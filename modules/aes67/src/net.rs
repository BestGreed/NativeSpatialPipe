use std::{
    io,
    net::{Ipv4Addr, UdpSocket},
};

/// std exposes multicast TTL and membership, but omits IP_MULTICAST_IF.
pub fn multicast_interface(socket: &UdpSocket, address: Ipv4Addr) -> io::Result<()> {
    if address.is_unspecified() {
        return Ok(());
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawSocket;
        #[link(name = "ws2_32")]
        unsafe extern "system" {
            fn setsockopt(socket: usize, level: i32, name: i32, value: *const u8, len: i32) -> i32;
            fn WSAGetLastError() -> i32;
        }
        let result = unsafe {
            setsockopt(
                socket.as_raw_socket() as usize,
                0,
                9,
                address.octets().as_ptr(),
                4,
            )
        };
        if result != 0 {
            return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;
        unsafe extern "C" {
            fn setsockopt(socket: i32, level: i32, name: i32, value: *const u8, len: u32) -> i32;
        }
        let result = unsafe { setsockopt(socket.as_raw_fd(), 0, 32, address.octets().as_ptr(), 4) };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(not(any(windows, target_os = "linux")))]
    Err(io::ErrorKind::Unsupported.into())
}

/// DSCP traffic-class marking (AES67-2023 Table 1, draft line 1288-1308):
/// Clock/EF (46) for PTP, Media/AF41 (34) for RTP. TOS is DSCP shifted left 2.
pub const TOS_AF41: u8 = 34 << 2;
pub const TOS_EF: u8 = 46 << 2;

/// Best-effort IP_TOS marking. Setsockopt for IP_TOS predates the DSCP API and
/// carries the DSCP bits in the top six bits of the TOS octet (RFC 2474).
/// Mark once per process if IP_TOS cannot be set, so a silent QoS miss is at
/// least visible in the host stderr instead of being indistinguishable from OK.
static DSCP_WARNING_EMITTED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
pub fn mark_dscp(socket: &UdpSocket, tos: u8) {
    if let Err(error) = dscp(socket, tos) {
        if !DSCP_WARNING_EMITTED.swap(true, std::sync::atomic::Ordering::Relaxed) {
            eprintln!("nsp_aes67: IP_TOS/DSCP marking failed (QoS not applied): {error}");
        }
    }
}

pub fn dscp(socket: &UdpSocket, tos: u8) -> io::Result<()> {
    let level = 0;
    // IP_TOS takes an int on both Windows and Linux; a one-byte option length
    // fails with EINVAL on Linux and is not guaranteed on Windows.
    let value: i32 = i32::from(tos);
    let length = std::mem::size_of::<i32>();
    #[cfg(windows)]
    let name = 3; // IP_TOS
    #[cfg(target_os = "linux")]
    let name = 1; // IP_TOS
    #[cfg(not(any(windows, target_os = "linux")))]
    {
        let _ = (socket, tos, level, name);
        return Err(io::ErrorKind::Unsupported.into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawSocket;
        #[link(name = "ws2_32")]
        unsafe extern "system" {
            fn setsockopt(socket: usize, level: i32, name: i32, value: *const u8, len: i32) -> i32;
            fn WSAGetLastError() -> i32;
        }
        let result = unsafe {
            setsockopt(socket.as_raw_socket() as usize, level, name, &value as *const i32 as *const u8, length as i32)
        };
        if result != 0 {
            return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;
        unsafe extern "C" {
            fn setsockopt(socket: i32, level: i32, name: i32, value: *const u8, len: u32) -> i32;
        }
        let result = unsafe { setsockopt(socket.as_raw_fd(), level, name, &value as *const i32 as *const u8, length as u32) };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    /// Linux requires optlen >= sizeof(int) for IP_TOS; a one-byte option used
    /// to fail with EINVAL while the error was swallowed. Both supported
    /// platforms must accept the four-byte form.
    #[test]
    fn dscp_marking_sets_four_byte_tos() {
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").expect("bind");
        #[cfg(any(windows, target_os = "linux"))]
        assert!(super::dscp(&socket, super::TOS_AF41).is_ok());
        let _ = socket;
    }
}