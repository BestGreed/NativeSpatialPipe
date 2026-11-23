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
