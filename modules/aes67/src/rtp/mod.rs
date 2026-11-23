pub mod ring;

use crate::{
    net::{TOS_AF41, dscp, multicast_interface},
    time::{MAX_RTP_PAYLOAD, PacketTiming},
};
use std::{
    io,
    net::{Ipv4Addr, SocketAddr, SocketAddrV4, UdpSocket},
};

/// RTP fixed header, twelve bytes (RFC 3550 clause 5.1).
pub const RTP_HEADER: usize = 12;

/// Largest datagram this sender emits: header plus the AES67 1440-byte RTP
/// payload budget (AES67-2023 sec 6.3, draft line 1364-1366).
pub const PACKET_CAPACITY: usize = RTP_HEADER + MAX_RTP_PAYLOAD;

/// Hard per-flow channel ceiling. AES67 senders must offer 1-8 channels per
/// stream and may support more (AES67-2023 sec 7.3, line 1556-1558); 64 is the
/// RAVENNA LKM ceiling. The ABI additionally enforces the MTU-derived cap.
pub const MAX_FLOW_CHANNELS: usize = 64;

/// Linear PCM sample encoding, network byte order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Encoding {
    L16,
    L24,
}

impl Encoding {
    pub fn bytes(self) -> usize {
        match self {
            Self::L16 => 2,
            Self::L24 => 3,
        }
    }

    /// SDP encoding name: L16 per RFC 3551 clause 4.5.11, L24 per RFC 3190
    /// clause 4 (AES67-2023 sec 7.1, lines 1411-1414).
    pub fn codec(self) -> &'static str {
        match self {
            Self::L16 => "L16",
            Self::L24 => "L24",
        }
    }

    /// ABI encoding selector: 0 = L24 (default), 1 = L16.
    pub fn from_abi(raw: u8) -> Option<Self> {
        match raw {
            0 => Some(Self::L24),
            1 => Some(Self::L16),
            _ => None,
        }
    }

    pub fn abi(self) -> u8 {
        match self {
            Self::L24 => 0,
            Self::L16 => 1,
        }
    }

    /// Packs one float sample as two's-complement big-endian PCM. Values are
    /// rounded and clamped; NaN maps to digital silence.
    pub fn pack(self, sample: f32, out: &mut [u8]) {
        let sample = if sample.is_nan() { 0.0 } else { sample };
        match self {
            Self::L16 => {
                let value = (sample * 32_768.0)
                    .round()
                    .clamp(-32_768.0, 32_767.0) as i32;
                out[..2].copy_from_slice(&value.to_be_bytes()[2..4]);
            }
            Self::L24 => {
                let value = (sample * 8_388_608.0)
                    .round()
                    .clamp(-8_388_608.0, 8_388_607.0) as i32;
                out[..3].copy_from_slice(&value.to_be_bytes()[1..4]);
            }
        }
    }
}

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
    pub encoding: Encoding,
    pub source: Ipv4Addr,
    socket: UdpSocket,
    sequence: u16,
    #[cfg(test)]
    fail_next_send: bool,
}

impl Output {
    pub fn open(flow: Flow, encoding: Encoding) -> io::Result<Self> {
        if flow.channels == 0 || flow.channels > MAX_FLOW_CHANNELS {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        let multicast = flow.destination.ip().is_multicast();
        let socket = UdpSocket::bind((flow.interface, 0))?;
        if multicast {
            multicast_interface(&socket, flow.interface)?;
            socket.set_multicast_ttl_v4(u32::from(flow.ttl))?;
        }
        socket.set_nonblocking(true)?;
        // AES67-2023 sec 6.2 Table 1 (line 1300-1302): mark media AF41 (34).
        // Best effort: some Windows stacks reject IP_TOS on a bound socket.
        let _ = dscp(&socket, TOS_AF41);
        socket.connect(flow.destination)?;
        let SocketAddr::V4(address) = socket.local_addr()? else {
            return Err(io::ErrorKind::InvalidData.into());
        };
        let source = *address.ip();
        Ok(Self {
            flow,
            encoding,
            source,
            socket,
            sequence: 0,
            #[cfg(test)]
            fail_next_send: false,
        })
    }

    /// Drops pacing slots by jumping the sequence number. RTP timestamps are
    /// absolute media-clock positions, so they jump by the same slot count.
    pub fn drop_slots(&mut self, count: u64) {
        self.sequence = self.sequence.wrapping_add(count as u16);
    }

    /// Packetizes one interleaved audio interval. The marker bit is always
    /// zero: AES67 assigns it no meaning for these payloads, and the Merging
    /// LKM compares RTP byte 1 literally against the payload type, so an M=1
    /// packet would be dropped as a payload-type error (audit 2026-11-23,
    /// RFC 3550 clause 5.3 line 968-977 leaves the bit to the profile).
    pub fn send(&mut self, stamp: u32, frames: &[f32], channels: usize) -> io::Result<usize> {
        if channels != self.flow.channels
            || frames.is_empty()
            || !frames.len().is_multiple_of(channels)
        {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        let count = frames.len() / channels;
        let end = RTP_HEADER + count * channels * self.encoding.bytes();
        if end > PACKET_CAPACITY {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        let mut packet = [0_u8; PACKET_CAPACITY];
        packet[0] = 0x80;
        packet[1] = self.flow.payload_type;
        packet[2..4].copy_from_slice(&self.sequence.to_be_bytes());
        packet[4..8].copy_from_slice(&stamp.to_be_bytes());
        packet[8..12].copy_from_slice(&self.flow.ssrc.to_be_bytes());
        let width = self.encoding.bytes();
        let mut offset = RTP_HEADER;
        for frame in frames.chunks_exact(channels) {
            for &sample in &frame[self.flow.offset..self.flow.offset + self.flow.channels] {
                self.encoding.pack(sample, &mut packet[offset..offset + width]);
                offset += width;
            }
        }
        let sent = self.write(&packet[..end]).and_then(|size| {
            if size == end {
                Ok(size)
            } else {
                Err(io::ErrorKind::WriteZero.into())
            }
        });
        self.sequence = self.sequence.wrapping_add(1);
        sent
    }

    fn write(&mut self, packet: &[u8]) -> io::Result<usize> {
        #[cfg(test)]
        {
            if std::mem::take(&mut self.fail_next_send) {
                return Err(io::ErrorKind::Other.into());
            }
        }
        self.socket.send(packet)
    }
}
/// Absolute PTP packet grid. Slots are delimited by the exact rational media
/// packet period, so 44.1 kHz (1088.435 us) and 48 kHz (1 ms or 125 us) share
/// one implementation and never accumulate pacing drift.
#[derive(Clone, Copy)]
pub struct Grid {
    timing: PacketTiming,
    next_slot: Option<i128>,
}

pub struct Batch {
    pub first_slot: i128,
    pub slots: u32,
    pub dropped: u64,
    pub wake_late_ns: i128,
}

impl Batch {
    pub fn slot_range(&self) -> impl Iterator<Item = i128> + '_ {
        (0..i128::from(self.slots)).map(move |i| self.first_slot + i)
    }
}

impl Grid {
    pub fn new(timing: PacketTiming) -> Self {
        Self {
            timing,
            next_slot: None,
        }
    }

    /// Index of the last packet whose deadline is at or before ptp_ns. The
    /// division is exact: for a fractional 44.1 kHz period the floor of the
    /// rational deadline is accounted for instead of assuming 1 ms slots.
    fn last_due(&self, ptp_ns: i128) -> i128 {
        let numerator = i128::from(self.timing.samples_per_packet()) * crate::time::NS_PER_S;
        let rate = i128::from(self.timing.sample_rate());
        (((ptp_ns + 1) * rate) - 1).div_euclid(numerator)
    }

    /// First call arms the grid at the next packet boundary and returns None;
    /// later calls return every due slot, keeping the newest ones if the batch
    /// exceeds max_catch_up.
    pub fn due(&mut self, ptp_ns: i128, max_catch_up: u32) -> Option<Batch> {
        let current = self.last_due(ptp_ns);
        let next = match self.next_slot {
            Some(next) => next,
            None => {
                self.next_slot = Some(current + 1);
                return None;
            }
        };
        if current < next {
            return None;
        }
        self.next_slot = Some(current + 1);
        let total = current - next + 1;
        let slots = total.min(i128::from(max_catch_up) + 1);
        Some(Batch {
            first_slot: current - slots + 1,
            slots: slots as u32,
            dropped: (total - slots) as u64,
            wake_late_ns: ptp_ns - self.timing.deadline(next),
        })
    }

    pub fn next_ns(&self) -> i128 {
        self.timing
            .deadline(self.next_slot.expect("grid initialized by due"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time::PacketTiming;

    const HZ_48K: u32 = 48_000;

    fn local_output(
        port_sink: &UdpSocket,
        _timing: PacketTiming,
        encoding: Encoding,
        channels: usize,
    ) -> Output {
        let SocketAddr::V4(destination) = port_sink.local_addr().unwrap() else {
            unreachable!()
        };
        Output::open(
            Flow {
                destination,
                interface: Ipv4Addr::LOCALHOST,
                ttl: 1,
                ssrc: 0x4E53_5000,
                offset: 0,
                channels,
                payload_type: 96,
                source_filter: false,
            },
            encoding,
        )
        .unwrap()
    }

    fn sink() -> UdpSocket {
        let socket = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        socket
            .set_read_timeout(Some(std::time::Duration::from_millis(500)))
            .unwrap();
        socket
    }

    fn receive(socket: &UdpSocket) -> ([u8; PACKET_CAPACITY], usize) {
        let mut datagram = [0; PACKET_CAPACITY];
        let size = socket.recv(&mut datagram).unwrap();
        (datagram, size)
    }

    #[test]
    fn marker_bit_is_always_clear_for_aes67_payloads() {
        // RFC 3550 clause 5.3 leaves the marker bit to the profile; AES67 does
        // not use it here and the Merging LKM reads byte 1 literally as the PT.
        let timing = PacketTiming::new(HZ_48K, 1_000).unwrap();
        let sink = sink();
        let mut output = local_output(&sink, timing, Encoding::L24, 1);
        for index in 0..4_u32 {
            output
                .send(timing.timestamp(i128::from(index)), &[0.5; 48], 1)
                .unwrap();
            let (datagram, size) = receive(&sink);
            assert_eq!(size, RTP_HEADER + 48 * 3);
            assert_eq!(datagram[1], 96);
        }
    }

    #[test]
    fn dropped_slots_jump_sequence_and_timestamp_without_error() {
        let timing = PacketTiming::new(HZ_48K, 1_000).unwrap();
        let sink = sink();
        let mut output = local_output(&sink, timing, Encoding::L24, 1);
        output.send(timing.timestamp(1), &[0.5; 48], 1).unwrap();
        let (datagram, _) = receive(&sink);
        assert_eq!(u16::from_be_bytes(datagram[2..4].try_into().unwrap()), 0);

        output.drop_slots(7);
        output.send(timing.timestamp(8), &[0.5; 48], 1).unwrap();
        let (datagram, size) = receive(&sink);
        assert_eq!(size, RTP_HEADER + 48 * 3);
        assert_eq!(datagram[1], 96);
        assert_eq!(u16::from_be_bytes(datagram[2..4].try_into().unwrap()), 8);
        assert_eq!(u32::from_be_bytes(datagram[4..8].try_into().unwrap()), 8 * 48);
    }

    #[test]
    fn send_error_retry_keeps_the_header_sequence() {
        let timing = PacketTiming::new(HZ_48K, 1_000).unwrap();
        let sink = sink();
        let mut output = local_output(&sink, timing, Encoding::L24, 1);
        output.send(0, &[0.5; 48], 1).unwrap();
        receive(&sink);
        output.fail_next_send = true;
        assert!(output.send(48, &[0.5; 48], 1).is_err());
        assert_eq!(output.sequence, 2);
        output.send(96, &[0.5; 48], 1).unwrap();
        let (datagram, _) = receive(&sink);
        assert_eq!(datagram[1], 96);
        assert_eq!(u16::from_be_bytes(datagram[2..4].try_into().unwrap()), 2);
        assert_eq!(u32::from_be_bytes(datagram[4..8].try_into().unwrap()), 96);
    }

    #[test]
    fn packet_sizes_follow_rate_ptime_channels_and_encoding() {
        // (rate Hz, ptime us, channels, encoding, expected RTP datagram bytes)
        let cases = [
            (48_000, 1_000, 8, Encoding::L24, 12 + 48 * 8 * 3),
            (48_000, 1_000, 8, Encoding::L16, 12 + 48 * 8 * 2),
            (48_000, 125, 8, Encoding::L24, 12 + 6 * 8 * 3),
            (48_000, 125, 64, Encoding::L24, 12 + 6 * 64 * 3),
            (192_000, 125, 8, Encoding::L24, 12 + 24 * 8 * 3),
            (192_000, 125, 8, Encoding::L16, 12 + 24 * 8 * 2),
            (192_000, 125, 16, Encoding::L24, 12 + 24 * 16 * 3),
            (96_000, 1_000, 4, Encoding::L24, 12 + 96 * 4 * 3),
            (44_100, 1_000, 8, Encoding::L16, 12 + 48 * 8 * 2),
        ];
        for (rate, ptime, channels, encoding, expected) in cases {
            let timing = PacketTiming::new(rate, ptime).unwrap();
            assert_eq!(
                timing.samples_per_packet() as usize * channels * encoding.bytes() + RTP_HEADER,
                expected
            );
            let sink = sink();
            let mut output = local_output(&sink, timing, encoding, channels);
            let frames = vec![0.25_f32; timing.samples_per_packet() as usize * channels];
            output
                .send(timing.timestamp(1), &frames, channels)
                .unwrap();
            let (_, size) = receive(&sink);
            assert_eq!(size, expected, "{rate} Hz / {ptime} us / {channels}ch / {encoding:?}");
        }
    }

    #[test]
    fn interleaved_samples_are_packed_big_endian_per_encoding() {
        let timing = PacketTiming::new(HZ_48K, 1_000).unwrap();
        // One frame of two channels: -0.5 and 0.25.
        let receiver = sink();
        let mut l16 = local_output(&receiver, timing, Encoding::L16, 2);
        l16.send(0, &[-0.5, 0.25], 2).unwrap();
        let (datagram, size) = receive(&receiver);
        assert_eq!(size, RTP_HEADER + 4);
        assert_eq!(&datagram[12..14], &[0xC0, 0x00]);
        assert_eq!(&datagram[14..16], &[0x20, 0x00]);

        let sink2 = sink();
        let mut l24 = local_output(&sink2, timing, Encoding::L24, 2);
        l24.send(0, &[-0.5, 0.25], 2).unwrap();
        let (datagram, size) = receive(&sink2);
        assert_eq!(size, RTP_HEADER + 6);
        assert_eq!(&datagram[12..15], &[0xC0, 0x00, 0x00]);
        assert_eq!(&datagram[15..18], &[0x20, 0x00, 0x00]);
    }

    #[test]
    fn oversize_packets_are_rejected_before_the_socket() {
        // 48 kHz / 1 ms / L24 allows at most 1440 / 144 = 10 channels.
        let timing = PacketTiming::new(HZ_48K, 1_000).unwrap();
        assert_eq!(timing.max_channels_per_flow(3), 10);
        let sink = sink();
        let mut output = local_output(&sink, timing, Encoding::L24, 11);
        let frames = vec![0.0_f32; 48 * 11];
        assert!(matches!(
            output.send(0, &frames, 11),
            Err(error) if error.kind() == io::ErrorKind::InvalidInput
        ));
        // Nothing was transmitted and the sequence stayed untouched.
        assert_eq!(output.sequence, 0);
    }

    #[test]
    fn late_wake_sends_each_pcm_slice_with_continuous_headers() {
        let timing = PacketTiming::new(HZ_48K, 1_000).unwrap();
        let sink = sink();
        let mut output = local_output(&sink, timing, Encoding::L24, 1);
        output.sequence = u16::MAX - 1;
        let mut ring = ring::Ring::new(8 * 48, 1);
        let levels = [0.125, 0.25, 0.375, 0.5];
        for (index, level) in levels.into_iter().enumerate() {
            ring.copy((index * 48) as u64, &[level; 48], 1, &[0])
                .unwrap();
        }
        let mut grid = Grid::new(timing);
        assert!(grid.due(100_000_100, 8).is_none());
        let batch = grid.due(104_000_000, 8).unwrap();
        assert_eq!(batch.slots - 1, 3);
        assert_eq!(batch.dropped, 0);
        for (index, slot) in batch.slot_range().enumerate() {
            let mut frames = [0.0; 48];
            assert!(!ring.consume(&mut frames));
            output.send(timing.timestamp(slot), &frames, 1).unwrap();
            let (datagram, size) = receive(&sink);
            let seq = u16::from_be_bytes(datagram[2..4].try_into().unwrap());
            let ts = u32::from_be_bytes(datagram[4..8].try_into().unwrap());
            assert_eq!(seq, (u16::MAX - 1).wrapping_add(index as u16));
            assert_eq!(ts, timing.timestamp(slot));
            assert_eq!(size, RTP_HEADER + 48 * 3);
            for sample in datagram[12..size].chunks_exact(3) {
                assert_eq!(sample, [((index + 1) * 0x10) as u8, 0, 0]);
            }
        }
        let overflow = grid.due(125_000_000, 8).unwrap();
        assert_eq!(overflow.dropped, 12);
        output.drop_slots(overflow.dropped);
        output
            .send(timing.timestamp(overflow.first_slot), &[0.0; 48], 1)
            .unwrap();
        let (datagram, _) = receive(&sink);
        assert_eq!(datagram[1], 96);
        assert_eq!(u16::from_be_bytes(datagram[2..4].try_into().unwrap()), 14);
        assert_eq!(
            u32::from_be_bytes(datagram[4..8].try_into().unwrap()),
            timing.timestamp(117)
        );
    }

    #[test]
    fn grid_catches_up_in_order_and_bounds_the_burst_at_1ms() {
        let timing = PacketTiming::new(HZ_48K, 1_000).unwrap();
        let mut grid = Grid::new(timing);
        assert!(grid.due(150_000_100, 3).is_none());
        let first = grid.due(151_000_000, 3).unwrap();
        assert_eq!(first.slot_range().collect::<Vec<_>>(), [151]);
        assert_eq!(timing.timestamp(151), 151 * 48);
        assert!(grid.due(151_999_999, 3).is_none());
        let late = grid.due(155_000_100, 3).unwrap();
        assert_eq!(late.wake_late_ns, 3_000_100);
        assert_eq!(late.dropped, 0);
        assert_eq!(late.slot_range().collect::<Vec<_>>(), [152, 153, 154, 155]);
        let overflow = grid.due(163_000_000, 3).unwrap();
        assert_eq!(overflow.dropped, 4);
        assert_eq!(overflow.slot_range().collect::<Vec<_>>(), [160, 161, 162, 163]);
        assert!(grid.due(154_000_000, 3).is_none());
        assert_eq!(grid.next_ns(), 164_000_000);
    }

    #[test]
    fn grid_uses_the_exact_rational_period_at_44k1_and_125us() {
        let timing = PacketTiming::new(44_100, 1_000).unwrap();
        let mut grid = Grid::new(timing);
        assert!(grid.due(0, 8).is_none());
        // 918.75 packets/s: slot 918 starts at 918 * 48e9 / 44100 ns.
        let deadline = timing.deadline(918);
        assert_eq!(deadline, 918 * i128::from(48u32) * 1_000_000_000 / 44_100);
        let batch = grid.due(deadline, 2_000).unwrap();
        assert_eq!(batch.first_slot, 1);
        assert_eq!(batch.slots, 918);
        assert_eq!(batch.dropped, 0);
        let stamps: Vec<u32> = batch.slot_range().map(|slot| timing.timestamp(slot)).collect();
        assert_eq!(stamps[0], 48);
        assert_eq!(stamps[917], 918 * 48);
        // Timestamps advance by exactly 48 samples per packet and wrap at 2^32.
        let hour = 3600 * 44_100 / 48;
        assert_eq!(timing.timestamp(hour + 1).wrapping_sub(timing.timestamp(hour)), 48);
        // The 44.1 kHz RTP clock wraps about every 27.05 h; the step across
        // the wrap is still exactly 48 samples in wrapping arithmetic.
        let wrap_slot = 89_478_485_i128; // floor(2^32 / 48)
        assert_eq!(
            timing
                .timestamp(wrap_slot + 1)
                .wrapping_sub(timing.timestamp(wrap_slot)),
            48
        );

        let fast = PacketTiming::new(48_000, 125).unwrap();
        let mut grid = Grid::new(fast);
        assert!(grid.due(0, 8).is_none());
        let batch = grid.due(3 * 125_000, 8).unwrap();
        assert_eq!(batch.slot_range().collect::<Vec<_>>(), [1, 2, 3]);
        // The wake is two packet periods late relative to slot 1.
        assert_eq!(batch.wake_late_ns, 2 * 125_000);
        assert_eq!(grid.next_ns(), 4 * 125_000);
    }

    #[test]
    fn multicast_interface_and_ttl_can_be_set_without_transmitting() {
        let socket = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        multicast_interface(&socket, Ipv4Addr::LOCALHOST).unwrap();
        socket.set_multicast_ttl_v4(19).unwrap();
        assert_eq!(socket.multicast_ttl_v4().unwrap(), 19);
    }
}