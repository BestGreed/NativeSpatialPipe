pub mod ring;

use crate::{
    net::multicast_interface,
    time::{FRAMES_PER_MS, NS_PER_MS},
};
use std::{
    io,
    net::{Ipv4Addr, SocketAddr, SocketAddrV4, UdpSocket},
};

pub const FRAMES: usize = FRAMES_PER_MS as usize;
pub const RTP_HEADER: usize = 12;
const PACKET_CAPACITY: usize = RTP_HEADER + FRAMES * 8 * 3;

#[derive(Clone, Debug)]
pub struct Flow {
    pub destination: SocketAddrV4,
    pub interface: Ipv4Addr,
    pub ttl: u8,
    pub ssrc: u32,
    pub offset: usize,
    pub channels: usize,
    pub payload_type: u8,
    pub source_filter: bool,
}

pub struct Output {
    pub flow: Flow,
    pub source: Ipv4Addr,
    socket: UdpSocket,
    sequence: u16,
    marker: bool,
}

impl Output {
    pub fn open(flow: Flow) -> io::Result<Self> {
        if flow.channels == 0 || flow.channels > 8 {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        let multicast = flow.destination.ip().is_multicast();
        let socket = UdpSocket::bind((flow.interface, 0))?;
        if multicast {
            multicast_interface(&socket, flow.interface)?;
            socket.set_multicast_ttl_v4(u32::from(flow.ttl))?;
        }
        socket.set_nonblocking(true)?;
        socket.connect(flow.destination)?;
        let SocketAddr::V4(address) = socket.local_addr()? else {
            return Err(io::ErrorKind::InvalidData.into());
        };
        let source = *address.ip();
        Ok(Self {
            flow,
            source,
            socket,
            sequence: 0,
            marker: true,
        })
    }

    pub fn discontinuity(&mut self) {
        self.marker = true;
    }

    pub fn send(&mut self, stamp: u32, frames: &[f32], channels: usize) -> io::Result<usize> {
        let mut packet = [0_u8; PACKET_CAPACITY];
        packet[0] = 0x80;
        packet[1] = self.flow.payload_type | if self.marker { 0x80 } else { 0 };
        packet[2..4].copy_from_slice(&self.sequence.to_be_bytes());
        packet[4..8].copy_from_slice(&stamp.to_be_bytes());
        packet[8..12].copy_from_slice(&self.flow.ssrc.to_be_bytes());
        let mut end = RTP_HEADER;
        for frame in frames.chunks_exact(channels) {
            for &sample in &frame[self.flow.offset..self.flow.offset + self.flow.channels] {
                let value = if sample.is_nan() {
                    0
                } else {
                    (sample * 8_388_608.0)
                        .round()
                        .clamp(-8_388_608.0, 8_388_607.0) as i32
                };
                packet[end..end + 3].copy_from_slice(&value.to_be_bytes()[1..4]);
                end += 3;
            }
        }
        let size = self.socket.send(&packet[..end])?;
        if size != end {
            return Err(io::ErrorKind::WriteZero.into());
        }
        self.sequence = self.sequence.wrapping_add(1);
        self.marker = false;
        Ok(size)
    }
}

/// Emits at most one packet interval per wake. A missed grid interval is skipped
/// rather than introducing a burst or drifting the cadence from the PTP clock.
#[derive(Default)]
pub struct Grid {
    next_ms: Option<i128>,
}
impl Grid {
    pub fn due(&mut self, ptp_ns: i128) -> Option<(u32, u64)> {
        let ms = ptp_ns.div_euclid(NS_PER_MS);
        let next = *self.next_ms.get_or_insert(ms + 1);
        if ms < next {
            return None;
        }
        self.next_ms = Some(ms + 1);
        Some(((ms * FRAMES_PER_MS) as u32, (ms - next) as u64))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn local_unicast_flows_have_one_shared_timestamp_and_sequence_wraps() {
        let ip = Ipv4Addr::LOCALHOST;
        let rx = [
            UdpSocket::bind((ip, 0)).unwrap(),
            UdpSocket::bind((ip, 0)).unwrap(),
        ];
        let mut outputs = Vec::new();
        for (i, r) in rx.iter().enumerate() {
            r.set_read_timeout(Some(std::time::Duration::from_millis(500)))
                .unwrap();
            let SocketAddr::V4(destination) = r.local_addr().unwrap() else {
                unreachable!()
            };
            let mut out = Output::open(Flow {
                destination,
                interface: ip,
                ttl: 1,
                ssrc: 123 + i as u32,
                offset: i,
                channels: 1,
                payload_type: 96 + i as u8,
                source_filter: false,
            })
            .unwrap();
            out.sequence = u16::MAX;
            outputs.push(out);
        }
        let samples = [0.25, -0.5].repeat(FRAMES);
        for j in 0..3 {
            let stamp = (u32::MAX - 15).wrapping_add(j * 48);
            for (i, out) in outputs.iter_mut().enumerate() {
                out.send(stamp, &samples, 2).unwrap();
                let mut buf = [0; PACKET_CAPACITY];
                let n = rx[i].recv(&mut buf).unwrap();
                assert_eq!(n, 12 + FRAMES * 3);
                assert_eq!(
                    &buf[..2],
                    &[0x80, (96 + i as u8) | if j == 0 { 128 } else { 0 }]
                );
                assert_eq!(
                    u16::from_be_bytes(buf[2..4].try_into().unwrap()),
                    u16::MAX.wrapping_add(j as u16)
                );
                assert_eq!(u32::from_be_bytes(buf[4..8].try_into().unwrap()), stamp);
                assert_eq!(
                    u32::from_be_bytes(buf[8..12].try_into().unwrap()),
                    123 + i as u32
                );
                for sample in buf[12..n].chunks_exact(3) {
                    assert_eq!(sample, if i == 0 { &[0x20, 0, 0] } else { &[0xc0, 0, 0] });
                }
            }
        }
    }
    #[test]
    fn multicast_interface_and_ttl_can_be_set_without_transmitting() {
        let socket = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        multicast_interface(&socket, Ipv4Addr::LOCALHOST).unwrap();
        socket.set_multicast_ttl_v4(19).unwrap();
        assert_eq!(socket.multicast_ttl_v4().unwrap(), 19);
    }
    #[test]
    fn grid_is_absolute_and_skips_backlog() {
        let mut grid = Grid::default();
        assert_eq!(grid.due(150_000_100), None);
        assert_eq!(grid.due(151_000_000), Some((151 * 48, 0)));
        assert_eq!(grid.due(151_999_999), None);
        assert_eq!(grid.due(156_000_001), Some((156 * 48, 4)));
        assert_eq!(grid.due(154_000_000), None);
    }
}
