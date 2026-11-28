//! Interface MAC discovery and the IEEE 1588 clock identity.
//!
//! IEEE 1588-2008 8.2.1: the clockIdentity of an
//! ordinary clock is the 8-octet EUI-64 of its interface, formed from the
//! 48-bit MAC as MAC[0..3] || FF FE || MAC[3..6]. Local corroboration: the
//! board's GMID AE-6F-0D-FF-FE-8E-86-FC is the EUI-64 form of the interface
//! MAC ae:6f:0d:8e:86:fc. GM mode never invents a random identity: when the
//! MAC cannot be read the runtime refuses to start as a grandmaster.
//!
//! Linux MAC discovery is NOT_IMPLEMENTED (honest gap, the sender target is
//! Windows; the receiver worktree owns the Linux path).
use super::codec::ClockId;
use std::net::Ipv4Addr;

/// Build the EUI-64 clock identity from a 48-bit MAC (IEEE 1588-2008 8.2.1).
pub fn eui64_from_mac(mac: [u8; 6]) -> ClockId {
    [mac[0], mac[1], mac[2], 0xff, 0xfe, mac[3], mac[4], mac[5]]
}

/// True when the clock identity has the FF FE insertion pattern of an EUI-64
/// derived from an EUI-48 MAC.
pub fn is_eui64_from_mac(identity: ClockId) -> bool {
    identity[3] == 0xff && identity[4] == 0xfe
}

/// MAC of the interface owning the given IPv4 address. An unspecified
/// address selects the first operational non-loopback unicast interface.
#[cfg(windows)]
pub fn interface_mac(interface: Ipv4Addr) -> Option<[u8; 6]> {
    windows::interface_mac(interface)
}

#[cfg(not(windows))]
pub fn interface_mac(_interface: Ipv4Addr) -> Option<[u8; 6]> {
    None
}

/// EUI-64 clock identity of the interface owning the given address.
pub fn interface_clock_identity(interface: Ipv4Addr) -> Option<ClockId> {
    interface_mac(interface).map(eui64_from_mac)
}

#[cfg(windows)]
mod windows {
    use std::{ffi::c_void, net::Ipv4Addr};

    #[repr(C)]
    struct SocketAddress {
        lp_sockaddr: *const u8,
        sockaddr_length: i32,
    }

    /// IP_ADAPTER_UNICAST_ADDRESS prefix: the union (Length, Flags) comes
    /// before the linked-list pointer (Windows SDK iptypes.h).
    #[repr(C)]
    struct UnicastAddress {
        length: u32,
        flags: u32,
        next: *const UnicastAddress,
        address: SocketAddress,
    }

    /// Leading fields of IP_ADAPTER_ADDRESSES (Windows SDK iphlpapi.h). The
    /// caller-provided buffer carries the remaining fields; only this prefix
    /// is read, and adapters are walked through the Length field.
    #[repr(C)]
    struct AdapterAddresses {
        length: u32,
        if_index: u32,
        next: *const AdapterAddresses,
        adapter_name: *const u8,
        first_unicast: *const UnicastAddress,
        first_anycast: *const u8,
        first_multicast: *const u8,
        first_dns_server: *const u8,
        dns_suffix: *const u16,
        description: *const u16,
        friendly_name: *const u16,
        physical_address: [u8; 8],
        physical_address_length: u32,
        if_type: u32,
        oper_status: u32,
    }

    #[link(name = "iphlpapi")]
    unsafe extern "system" {
        fn GetAdaptersAddresses(
            family: u32,
            flags: u32,
            reserved: *mut c_void,
            addresses: *mut AdapterAddresses,
            size: *mut u32,
        ) -> u32;
    }

    const AF_INET: u32 = 2;
    // GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER
    const FLAGS: u32 = 2 | 4 | 8;
    const ERROR_BUFFER_OVERFLOW: u32 = 111;
    const IF_OPER_STATUS_UP: u32 = 1;

    unsafe fn sockaddr_ipv4(sockaddr: *const u8) -> Option<Ipv4Addr> {
        if sockaddr.is_null() {
            return None;
        }
        let family = u16::from_ne_bytes([unsafe { *sockaddr }, unsafe { *sockaddr.add(1) }]);
        if family != AF_INET as u16 {
            return None;
        }
        Some(Ipv4Addr::new(
            unsafe { *sockaddr.add(4) },
            unsafe { *sockaddr.add(5) },
            unsafe { *sockaddr.add(6) },
            unsafe { *sockaddr.add(7) },
        ))
    }

    unsafe fn first_unicast_ip(mut list: *const UnicastAddress) -> Option<Ipv4Addr> {
        while !list.is_null() {
            let entry = unsafe { &*list };
            list = entry.next;
            if let Some(ip) = unsafe { sockaddr_ipv4(entry.address.lp_sockaddr) } {
                return Some(ip);
            }
        }
        None
    }

    unsafe fn adapter_has_unicast(mut list: *const UnicastAddress, interface: Ipv4Addr) -> bool {
        while !list.is_null() {
            let entry = unsafe { &*list };
            list = entry.next;
            if unsafe { sockaddr_ipv4(entry.address.lp_sockaddr) }.is_some_and(|ip| ip == interface)
            {
                return true;
            }
        }
        false
    }

    pub fn interface_mac(interface: Ipv4Addr) -> Option<[u8; 6]> {
        let mut size: u32 = 0;
        let probe = unsafe {
            GetAdaptersAddresses(
                AF_INET,
                FLAGS,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut size,
            )
        };
        if probe != ERROR_BUFFER_OVERFLOW || size == 0 {
            return None;
        }
        for _ in 0..2 {
            let words = (size as usize).div_ceil(8);
            let mut buffer = vec![0u64; words];
            let mut capacity = u32::try_from(words * 8).ok()?;
            let result = unsafe {
                GetAdaptersAddresses(
                    AF_INET,
                    FLAGS,
                    std::ptr::null_mut(),
                    buffer.as_mut_ptr().cast(),
                    &mut capacity,
                )
            };
            if result == ERROR_BUFFER_OVERFLOW {
                size = capacity;
                continue;
            }
            if result != 0 {
                return None;
            }
            let mut fallback = None;
            let mut adapter = buffer.as_ptr().cast::<AdapterAddresses>();
            while !adapter.is_null() {
                let entry = unsafe { &*adapter };
                adapter = entry.next;
                if entry.physical_address_length != 6 {
                    continue;
                }
                let mac: [u8; 6] = entry.physical_address[..6].try_into().unwrap();
                if mac == [0; 6] || mac == [0xff; 6] {
                    continue;
                }
                if interface.is_unspecified() {
                    // Prefer an operational, non-loopback unicast interface.
                    let first = unsafe { first_unicast_ip(entry.first_unicast) };
                    let routable = first.is_some_and(|ip| !ip.is_loopback() && !ip.is_link_local());
                    if routable && entry.oper_status == IF_OPER_STATUS_UP {
                        return Some(mac);
                    }
                    if fallback.is_none() && routable {
                        fallback = Some(mac);
                    }
                } else if unsafe { adapter_has_unicast(entry.first_unicast, interface) } {
                    return Some(mac);
                }
            }
            return fallback;
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn board_mac_maps_to_the_documented_gmid() {
        // Board corroboration: MAC ae:6f:0d:8e:86:fc is
        // advertised by the board GM as AE-6F-0D-FF-FE-8E-86-FC.
        let identity = eui64_from_mac([0xae, 0x6f, 0x0d, 0x8e, 0x86, 0xfc]);
        assert_eq!(identity, [0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc]);
        assert!(is_eui64_from_mac(identity));
        assert!(!is_eui64_from_mac([0; 8]));
    }

    #[test]
    fn interface_identity_uses_the_eui64_layout() {
        // The loopback adapter usually has no MAC; either way, a returned
        // identity must be the FF FE EUI-64 form.
        if let Some(identity) = interface_clock_identity(Ipv4Addr::LOCALHOST) {
            assert!(is_eui64_from_mac(identity), "{identity:?}");
        }
        // TEST-NET-3 is not assigned to any interface, so the identity must be
        // unreadable: this is the refusal path GM mode relies on.
        assert_eq!(
            interface_clock_identity(Ipv4Addr::new(203, 0, 113, 7)),
            None
        );
    }
}
