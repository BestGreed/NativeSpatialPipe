//! Never share PTP ports with an existing DVS/clock process.
use std::{io, net::{Ipv4Addr, UdpSocket}};

/// Wildcard PTP sockets must only discover peers on the configured send NIC.
#[cfg(windows)]
pub fn restrict_receive_interface(sockets: [&UdpSocket; 2], interface: Ipv4Addr) -> io::Result<()> {
    use std::os::windows::io::AsRawSocket;

    #[link(name = "iphlpapi")]
    extern "system" {
        fn GetIpAddrTable(table: *mut u32, bytes: *mut u32, sort: i32) -> u32;
    }
    #[link(name = "ws2_32")]
    extern "system" {
        fn setsockopt(socket: usize, level: i32, option: i32, value: *const u8, length: i32) -> i32;
        fn WSAGetLastError() -> i32;
    }
    const ERROR_INSUFFICIENT_BUFFER: u32 = 122;
    const IP_IFLIST: i32 = 28;
    const IP_ADD_IFLIST: i32 = 29;
    let mut bytes = 0;
    let result = unsafe { GetIpAddrTable(std::ptr::null_mut(), &mut bytes, 0) };
    if result != ERROR_INSUFFICIENT_BUFFER {
        return Err(io::Error::from_raw_os_error(result as i32));
    }
    let mut table = vec![0u32; (bytes as usize).div_ceil(4)];
    let result = unsafe { GetIpAddrTable(table.as_mut_ptr(), &mut bytes, 0) };
    if result != 0 {
        return Err(io::Error::from_raw_os_error(result as i32));
    }
    // MIB_IPADDRROW consists of five DWORDs and two USHORTs (six u32 words).
    let index = table[1..]
        .chunks_exact(6)
        .take(table[0] as usize)
        .find(|row| row[0].to_ne_bytes() == interface.octets())
        .map(|row| row[1])
        .ok_or_else(|| io::Error::new(io::ErrorKind::AddrNotAvailable, "PTP send interface not found"))?;
    for socket in sockets {
        for (option, value) in [(IP_IFLIST, 1), (IP_ADD_IFLIST, index)] {
            if unsafe { setsockopt(socket.as_raw_socket() as _, 0, option, (&value as *const u32).cast(), 4) } != 0 {
                return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
            }
        }
    }
    Ok(())
}

#[cfg(not(windows))]
pub fn restrict_receive_interface(_: [&UdpSocket; 2], _: Ipv4Addr) -> io::Result<()> {
    Err(io::Error::new(io::ErrorKind::Unsupported, "PTP receive interface is Windows-only"))
}

/// Wake on either PTP socket instead of adding a polling sleep to RX timestamps.
#[cfg(windows)]
pub fn wait_readable(event: &UdpSocket, general: &UdpSocket) -> io::Result<()> {
    use std::os::windows::io::AsRawSocket;
    #[repr(C)]
    struct PollFd {
        socket: usize,
        events: i16,
        returned: i16,
    }
    #[link(name = "ws2_32")]
    extern "system" {
        fn WSAPoll(fds: *mut PollFd, count: u32, timeout_ms: i32) -> i32;
        fn WSAGetLastError() -> i32;
    }
    let mut fds = [event, general].map(|s| PollFd {
        socket: s.as_raw_socket() as _,
        events: 0x0100,
        returned: 0,
    });
    if unsafe { WSAPoll(fds.as_mut_ptr(), 2, 10) } < 0 {
        return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
    }
    Ok(())
}
#[cfg(not(windows))]
pub fn wait_readable(_: &UdpSocket, _: &UdpSocket) -> io::Result<()> {
    std::thread::sleep(std::time::Duration::from_millis(1));
    Ok(())
}

#[cfg(windows)]
pub fn multicast_interface(socket: &UdpSocket, interface: std::net::Ipv4Addr) -> io::Result<()> {
    use std::os::windows::io::AsRawSocket;
    #[link(name = "ws2_32")]
    extern "system" {
        fn setsockopt(socket: usize, level: i32, option: i32, value: *const u8, length: i32)
            -> i32;
        fn WSAGetLastError() -> i32;
    }
    let address = interface.octets();
    if unsafe { setsockopt(socket.as_raw_socket() as _, 0, 9, address.as_ptr(), 4) } != 0 {
        return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
    }
    socket.set_multicast_ttl_v4(1)?;
    socket.set_multicast_loop_v4(true)
}
#[cfg(not(windows))]
pub fn multicast_interface(_: &UdpSocket, _: std::net::Ipv4Addr) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "explicit multicast interface is Windows-only in this prototype",
    ))
}

#[cfg(windows)]
pub fn bind_exclusive_v4(port: u16) -> io::Result<UdpSocket> {
    use std::os::windows::io::FromRawSocket;
    #[repr(C)]
    struct SockAddrIn {
        family: u16,
        port: u16,
        address: u32,
        zero: [u8; 8],
    }
    #[link(name = "ws2_32")]
    extern "system" {
        fn socket(af: i32, kind: i32, protocol: i32) -> usize;
        fn setsockopt(socket: usize, level: i32, option: i32, value: *const u8, length: i32)
            -> i32;
        fn bind(socket: usize, address: *const SockAddrIn, length: i32) -> i32;
        fn closesocket(socket: usize) -> i32;
        fn WSAGetLastError() -> i32;
    }
    // Let std initialize Winsock. No packets are sent by this bootstrap socket.
    drop(UdpSocket::bind("127.0.0.1:0")?);
    let raw = unsafe { socket(2, 2, 17) };
    if raw == usize::MAX {
        return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
    }
    let enabled = 1i32;
    let address = SockAddrIn {
        family: 2,
        port: port.to_be(),
        address: 0,
        zero: [0; 8],
    };
    let result = unsafe {
        // SOL_SOCKET, SO_EXCLUSIVEADDRUSE: set before binding.
        if setsockopt(raw, 0xffff, -5, (&enabled as *const i32).cast(), 4) != 0 {
            Err(WSAGetLastError())
        } else if bind(raw, &address, std::mem::size_of::<SockAddrIn>() as i32) != 0 {
            Err(WSAGetLastError())
        } else {
            Ok(())
        }
    };
    match result {
        Ok(()) => Ok(unsafe { UdpSocket::from_raw_socket(raw as _) }),
        Err(error) => {
            unsafe {
                closesocket(raw);
            };
            Err(io::Error::from_raw_os_error(error))
        }
    }
}

#[cfg(not(windows))]
pub fn bind_exclusive_v4(port: u16) -> io::Result<UdpSocket> {
    UdpSocket::bind((std::net::Ipv4Addr::UNSPECIFIED, port))
}

#[cfg(test)]
mod tests {
    #[test]
    fn conflicting_port_is_rejected() {
        let first = super::bind_exclusive_v4(0).unwrap();
        assert!(super::bind_exclusive_v4(first.local_addr().unwrap().port()).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn receive_interface_accepts_datagrams_from_the_selected_adapter() {
        use super::*;
        let event = UdpSocket::bind("0.0.0.0:0").unwrap();
        let general = UdpSocket::bind("0.0.0.0:0").unwrap();
        restrict_receive_interface([&event, &general], Ipv4Addr::LOCALHOST).unwrap();
        event.set_read_timeout(Some(std::time::Duration::from_secs(1))).unwrap();
        let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
        sender.send_to(b"ptp", (Ipv4Addr::LOCALHOST, event.local_addr().unwrap().port())).unwrap();
        let mut buffer = [0; 3];
        assert_eq!(event.recv_from(&mut buffer).unwrap().0, 3);
        assert_eq!(&buffer, b"ptp");
    }
}
