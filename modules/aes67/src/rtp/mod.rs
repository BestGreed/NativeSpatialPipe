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

    pub fn drop_slots(&mut self, count: u64) {
        self.sequence = self.sequence.wrapping_add(count as u16);
        self.discontinuity();
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
        let sent = self.socket.send(&packet[..end]).and_then(|size| {
            if size == end {
                Ok(size)
            } else {
                Err(io::ErrorKind::WriteZero.into())
            }
        });
        self.sequence = self.sequence.wrapping_add(1);
        self.marker = sent.is_err();
        sent
    }
}

/// Each slot remains anchored to the absolute PTP grid, including the slots
/// at the front of a delayed batch. Keep the newest slots if the batch is full.
#[derive(Default)]
pub struct Grid {
    next_ms: Option<i128>,
}

pub struct Batch {
    first_ms: i128,
    pub slots: u32,
    pub dropped: u64,
    pub wake_late_ns: i128,
}

impl Batch {
    pub fn stamps(&self) -> impl Iterator<Item = u32> + '_ {
        (0..self.slots).map(|i| ((self.first_ms + i128::from(i)) * FRAMES_PER_MS) as u32)
    }
}

impl Grid {
    pub fn due(&mut self, ptp_ns: i128, max_catch_up: u32) -> Option<Batch> {
        let ms = ptp_ns.div_euclid(NS_PER_MS);
        let next = *self.next_ms.get_or_insert(ms + 1);
        if ms < next {
            return None;
        }
        self.next_ms = Some(ms + 1);
        let total = ms - next + 1;
        let slots = total.min(i128::from(max_catch_up) + 1);
        Some(Batch {
            first_ms: ms - slots + 1,
            slots: slots as u32,
            dropped: (total - slots) as u64,
            wake_late_ns: ptp_ns - next * NS_PER_MS,
        })
    }

    pub fn next_ns(&self) -> i128 {
        self.next_ms.expect("grid initialized by due") * NS_PER_MS
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
    fn late_wake_sends_each_pcm_slice_with_continuous_rtp_headers() {
        let ip = Ipv4Addr::LOCALHOST;
        let sink = UdpSocket::bind((ip, 0)).unwrap();
        sink.set_read_timeout(Some(std::time::Duration::from_millis(500)))
            .unwrap();
        let mut output = Output::open(Flow {
            destination: SocketAddrV4::new(ip, sink.local_addr().unwrap().port()),
            interface: ip,
            ttl: 1,
            ssrc: 123,
            offset: 0,
            channels: 1,
            payload_type: 96,
            source_filter: false,
        })
        .unwrap();
        output.sequence = u16::MAX - 1;
        let mut ring = ring::Ring::new(8 * FRAMES, 1);
        let levels = [0.125, 0.25, 0.375, 0.5];
        for (index, level) in levels.into_iter().enumerate() {
            ring.copy((index * FRAMES) as u64, &[level; FRAMES], 1, &[0])
                .unwrap();
        }
        let mut grid = Grid::default();
        assert!(grid.due(100_000_100, 8).is_none());
        let batch = grid.due(104_000_000, 8).unwrap();
        assert_eq!(batch.slots - 1, 3);
        assert_eq!(batch.dropped, 0);
        for (index, stamp) in batch.stamps().enumerate() {
            let mut frames = [0.0; FRAMES];
            assert!(!ring.consume(&mut frames));
            output.send(stamp, &frames, 1).unwrap();
            let mut datagram = [0; PACKET_CAPACITY];
            let size = sink.recv(&mut datagram).unwrap();
            let seq = u16::from_be_bytes(datagram[2..4].try_into().unwrap());
            let ts = u32::from_be_bytes(datagram[4..8].try_into().unwrap());
            assert_eq!(seq, (u16::MAX - 1).wrapping_add(index as u16));
            assert_eq!(ts, (101 + index as u32) * 48);
            for sample in datagram[12..size].chunks_exact(3) {
                assert_eq!(sample, [((index + 1) * 0x10) as u8, 0, 0]);
            }
        }
        let overflow = grid.due(125_000_000, 8).unwrap();
        assert_eq!(overflow.dropped, 12);
        output.drop_slots(overflow.dropped);
        output
            .send(overflow.stamps().next().unwrap(), &[0.0; FRAMES], 1)
            .unwrap();
        let mut datagram = [0; PACKET_CAPACITY];
        sink.recv(&mut datagram).unwrap();
        assert_ne!(datagram[1] & 0x80, 0);
        assert_eq!(u16::from_be_bytes(datagram[2..4].try_into().unwrap()), 14);
        assert_eq!(
            u32::from_be_bytes(datagram[4..8].try_into().unwrap()),
            117 * 48
        );
    }

    #[test]
    fn multicast_interface_and_ttl_can_be_set_without_transmitting() {
        let socket = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        multicast_interface(&socket, Ipv4Addr::LOCALHOST).unwrap();
        socket.set_multicast_ttl_v4(19).unwrap();
        assert_eq!(socket.multicast_ttl_v4().unwrap(), 19);
    }
    #[test]
    fn grid_catches_up_in_order_and_bounds_the_burst() {
        let mut grid = Grid::default();
        assert!(grid.due(150_000_100, 3).is_none());
        assert_eq!(
            grid.due(151_000_000, 3)
                .unwrap()
                .stamps()
                .collect::<Vec<_>>(),
            [151 * 48]
        );
        assert!(grid.due(151_999_999, 3).is_none());
        let late = grid.due(155_000_100, 3).unwrap();
        assert_eq!(late.wake_late_ns, 3_000_100);
        assert_eq!(late.dropped, 0);
        assert_eq!(
            late.stamps().collect::<Vec<_>>(),
            [152 * 48, 153 * 48, 154 * 48, 155 * 48]
        );
        let overflow = grid.due(163_000_000, 3).unwrap();
        assert_eq!(overflow.dropped, 4);
        assert_eq!(
            overflow.stamps().collect::<Vec<_>>(),
            [160 * 48, 161 * 48, 162 * 48, 163 * 48]
        );
        assert!(grid.due(154_000_000, 3).is_none());
        assert_eq!(grid.next_ns(), 164_000_000);
    }
}
