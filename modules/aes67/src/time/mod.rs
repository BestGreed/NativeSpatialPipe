//! Software socket-boundary timestamps, an affine media clock, and the
//! AES67 packet-time grid.
//!
//! The grid is anchored to the PTP epoch in *media samples*, not in wall-clock
//! milliseconds: a "1 millisecond" packet carries 48 samples at both 48 kHz and
//! 44.1 kHz (AES67-2023 sec 7.2.1, draft line 1473-1476), so the 44.1 kHz packet
//! interval is 48/44100 s (about 1088.4 us, 918.75 packets/s). RTP timestamps
//! therefore advance by exactly samples-per-packet on every packet, while the
//! pacing deadline is computed with exact rational arithmetic and never drifts.
use std::{
    collections::VecDeque,
    io,
    net::{SocketAddr, SocketAddrV4, UdpSocket},
    time::Instant,
};

pub const NS_PER_MS: i128 = 1_000_000;
pub const NS_PER_S: i128 = 1_000_000_000;

/// AES67-2023 sec 6.3 (draft line 1364-1366): the maximum allowed RTP payload
/// is 1440 bytes on the standard 1500-byte Ethernet MTU.
pub const MAX_RTP_PAYLOAD: usize = 1440;

/// Sampling rates this sender implements. 48 kHz is mandatory (AES67-2023
/// sec 7.1 line 1416), 44.1 and 96 kHz are recommended (line 1416), and the
/// 88.2/176.4/192 kHz rows are RAVENNA-family extensions beyond AES67.
pub const SUPPORTED_SAMPLE_RATES: [u32; 6] = [44_100, 48_000, 88_200, 96_000, 176_400, 192_000];

/// Packet times offered by this sender, in microseconds. The AES67-2023
/// Table 2 (lines 1486-1550) names 125 us, 250 us, 1 ms and 4 ms; 500 us and
/// 2 ms are "additional packet times" permitted by sec 7.2.2 (line 1484).
pub const SUPPORTED_PACKET_TIMES_US: [u32; 7] = [125, 250, 333, 500, 1_000, 2_000, 4_000];

/// Samples per packet at the 1FS rate of a sampling-rate family. AES67-2023
/// Table 2 (lines 1486-1550) names 6, 12, 16, 48 and 192 samples; 24 (500 us)
/// and 96 (2 ms) are additional packet times permitted by sec 7.2.2 line 1484.
fn base_samples(packet_time_us: u32) -> Option<u32> {
    match packet_time_us {
        125 => Some(6),
        250 => Some(12),
        333 => Some(16),
        500 => Some(24),
        1_000 => Some(48),
        2_000 => Some(96),
        4_000 => Some(192),
        _ => None,
    }
}

/// Required "1 millisecond" packet: 48 samples at 48 kHz or 44.1 kHz and
/// 96 samples at 96 kHz (AES67-2023 sec 7.2.1, lines 1473-1476).
pub const AES67_1MS_SAMPLES: u32 = 48;

/// The sampling-rate family a rate belongs to. AES67 defines the 1 ms packet
/// as 48 samples at the base rate of the family (48 kHz or 44.1 kHz), and the
/// higher rates scale that count by the family multiplier.
fn family_base(rate: u32) -> Option<u32> {
    if rate % 44_100 == 0 {
        Some(44_100)
    } else if rate % 48_000 == 0 {
        Some(48_000)
    } else {
        None
    }
}

/// Exact rational packet grid for one stream mode.
///
/// samples_per_packet is the sample count named by AES67 Table 2 for this
/// (rate, packet-time) pair; slot_at/deadline convert between PTP epoch
/// nanoseconds and the packet index using i128 rational arithmetic, so a
/// 44.1 kHz stream's 1088.435 us interval never accumulates rounding error.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PacketTiming {
    sample_rate: u32,
    packet_time_us: u32,
    samples_per_packet: u32,
}

impl PacketTiming {
    /// Validates a (sampling rate, packet time) pair and returns its exact
    /// sample count from AES67-2023 Table 2: 48 at 48 kHz / 1 ms and 48 at
    /// 44.1 kHz / 1 ms (line 1532), 6 at 125 us (lines 1492-1498), 16 at
    /// 333 us (lines 1521-1527), 192 at 4 ms (lines 1538-1545). Higher family
    /// rates scale the 1FS count (96 kHz 1 ms = 96 samples, line 1474-1476).
    pub fn new(sample_rate: u32, packet_time_us: u32) -> Option<Self> {
        if !SUPPORTED_SAMPLE_RATES.contains(&sample_rate)
            || !SUPPORTED_PACKET_TIMES_US.contains(&packet_time_us)
        {
            return None;
        }
        let base = family_base(sample_rate)?;
        // Sample count scales from the 1FS count of the same family: 16 at
        // 48 kHz / 333 us (Table 2 line 1521-1525) is 64 at 192 kHz.
        let samples_per_packet = base_samples(packet_time_us)? * (sample_rate / base);
        if samples_per_packet == 0 || samples_per_packet as usize * 3 > MAX_RTP_PAYLOAD {
            return None;
        }
        Some(Self {
            sample_rate,
            packet_time_us,
            samples_per_packet,
        })
    }

    pub fn sample_rate(self) -> u32 {
        self.sample_rate
    }

    pub fn packet_time_us(self) -> u32 {
        self.packet_time_us
    }

    pub fn samples_per_packet(self) -> u32 {
        self.samples_per_packet
    }

    /// Largest channel count whose payload fits AES67's 1440-byte RTP payload
    /// budget at this packet time for a given sample width (2 or 3 bytes).
    pub fn max_channels_per_flow(self, bytes_per_sample: usize) -> usize {
        MAX_RTP_PAYLOAD / (self.samples_per_packet as usize * bytes_per_sample)
    }

    /// Packet index containing PTP epoch time ns, counting the PTP epoch as
    /// sample zero (mediaclk:direct=0 semantics, AES67-2023 sec 8.3).
    pub fn slot_at(self, ns: i128) -> i128 {
        (ns * i128::from(self.sample_rate))
            .div_euclid(i128::from(self.samples_per_packet) * NS_PER_S)
    }

    /// PTP epoch time of the start of packet slot, rounded down to ns.
    pub fn deadline(self, slot: i128) -> i128 {
        (slot * i128::from(self.samples_per_packet) * NS_PER_S)
            .div_euclid(i128::from(self.sample_rate))
    }

    /// RTP timestamp of packet slot: the absolute media-clock sample position
    /// of its first sample, truncated to 32 bits (AES67-2023 sec 5, line 1181).
    pub fn timestamp(self, slot: i128) -> u32 {
        (slot * i128::from(self.samples_per_packet)) as u32
    }

    /// Nominal packet period in ns, used only for the spin budget and reports.
    pub fn slot_period_ns(self) -> i128 {
        i128::from(self.samples_per_packet) * NS_PER_S / i128::from(self.sample_rate)
    }

    /// Actual packet interval in microseconds (44.1 kHz 1 ms is ~1088.4 us).
    pub fn actual_packet_time_us(self) -> f64 {
        f64::from(self.samples_per_packet) * 1_000_000.0 / f64::from(self.sample_rate)
    }
}
#[repr(u32)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimestampMode {
    Auto = 0,
    Software = 1,
    Hardware = 2,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Unavailable;

/// Both socket timestamps and now_ns must share a monotonic timebase. Hardware
/// implementations must correlate NIC RX/TX stamps to that timebase or use a PHC
/// reading for now_ns. An asynchronous TX driver may return None until completion.
pub trait TimestampSource: Send {
    fn now_ns(&self) -> i128;
    fn recv(&mut self, socket: &UdpSocket, buf: &mut [u8])
    -> io::Result<(usize, SocketAddr, i128)>;
    fn send(
        &mut self,
        socket: &UdpSocket,
        buf: &[u8],
        dest: SocketAddrV4,
    ) -> io::Result<Option<i128>>;
    fn tx_timestamp(&mut self) -> io::Result<Option<i128>> {
        Ok(None)
    }
}

struct Software(Instant);
impl TimestampSource for Software {
    fn now_ns(&self) -> i128 {
        self.0.elapsed().as_nanos() as i128
    }
    fn recv(
        &mut self,
        socket: &UdpSocket,
        buf: &mut [u8],
    ) -> io::Result<(usize, SocketAddr, i128)> {
        let (size, addr) = socket.recv_from(buf)?;
        Ok((size, addr, self.now_ns()))
    }
    fn send(
        &mut self,
        socket: &UdpSocket,
        buf: &[u8],
        dest: SocketAddrV4,
    ) -> io::Result<Option<i128>> {
        let stamp = self.now_ns();
        if socket.send_to(buf, dest)? != buf.len() {
            return Err(io::ErrorKind::WriteZero.into());
        }
        Ok(Some(stamp))
    }
}

fn hardware_source() -> Result<Box<dyn TimestampSource>, Unavailable> {
    Err(Unavailable)
}

pub fn timestamp_source(
    mode: TimestampMode,
) -> Result<(TimestampMode, Box<dyn TimestampSource>), Unavailable> {
    // std::time::Instant uses QueryPerformanceCounter on Windows.
    match mode {
        TimestampMode::Hardware => Ok((TimestampMode::Hardware, hardware_source()?)),
        TimestampMode::Auto => match hardware_source() {
            Ok(source) => Ok((TimestampMode::Hardware, source)),
            Err(Unavailable) => Ok((TimestampMode::Software, Box::new(Software(Instant::now())))),
        },
        TimestampMode::Software => {
            Ok((TimestampMode::Software, Box::new(Software(Instant::now()))))
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct ClockFit {
    local_ns: i128,
    remote_ns: i128,
    pub rate_ppm: f64,
    pub rms_ns: f64,
}
impl ClockFit {
    /// A synthetic mapping for clocks we own rather than follow: a GM or local
    /// epoch whose time equals the injected monotonic clock plus a constant.
    /// rate_ppm 0 and rms 0 because the same oscillator drives both sides.
    pub fn anchored(local_ns: i128, remote_ns: i128) -> Self {
        Self {
            local_ns,
            remote_ns,
            rate_ppm: 0.0,
            rms_ns: 0.0,
        }
    }

    /// Epoch arithmetic is integer; only the small local delta is converted to float.
    pub fn ptp_ns(self, local_ns: i128) -> i128 {
        let elapsed = local_ns - self.local_ns;
        self.remote_ns + elapsed + (elapsed as f64 * self.rate_ppm / 1_000_000.0).round() as i128
    }
    pub fn offset_ns(self, local_ns: i128) -> i128 {
        self.ptp_ns(local_ns) - local_ns
    }
    /// Absolute media-clock sample position for a local instant at rate.
    pub fn media_frame(self, local_ns: i128, rate: u32) -> i128 {
        (self.ptp_ns(local_ns) * i128::from(rate)).div_euclid(NS_PER_S)
    }
}

const MAX_SAMPLES: usize = 24;
const MIN_SAMPLES: usize = 8;
const MIN_FIT_SPAN_NS: i128 = 750_000_000;
pub const RATE_LIMIT_PPM: f64 = 500.0;

pub fn fit_rms_limit_ns(mode: TimestampMode) -> f64 {
    match mode {
        TimestampMode::Hardware => 250_000.0,
        TimestampMode::Auto | TimestampMode::Software => 2_000_000.0,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FitResult {
    WarmingUp,
    Accepted,
    OutOfOrder,
    RateRejected,
    RmsRejected,
}

#[derive(Default)]
pub struct MediaClock {
    samples: VecDeque<(i128, i128)>,
    fit: Option<ClockFit>,
}
impl MediaClock {
    pub fn clear(&mut self) {
        self.samples.clear();
        self.fit = None;
    }
    pub fn fit(&self) -> Option<ClockFit> {
        self.fit
    }
    pub fn observe(&mut self, local_ns: i128, ptp_ns: i128, mode: TimestampMode) -> FitResult {
        if self
            .samples
            .back()
            .is_some_and(|&(local, _)| local_ns <= local)
        {
            return FitResult::OutOfOrder;
        }
        self.samples.push_back((local_ns, ptp_ns - local_ns));
        if self.samples.len() > MAX_SAMPLES {
            self.samples.pop_front();
        }
        let &(base_local, base_offset) = self.samples.front().unwrap();
        if self.samples.len() < MIN_SAMPLES || local_ns - base_local < MIN_FIT_SPAN_NS {
            return FitResult::WarmingUp;
        }

        let points = || {
            self.samples.iter().map(|&(local, offset)| {
                ((local - base_local) as f64, (offset - base_offset) as f64)
            })
        };
        let count = self.samples.len() as f64;
        let (sum_x, sum_y) = points().fold((0.0, 0.0), |(sx, sy), (x, y)| (sx + x, sy + y));
        let (mean_x, mean_y) = (sum_x / count, sum_y / count);
        let (cov, var) = points().fold((0.0, 0.0), |(cov, var), (x, y)| {
            let dx = x - mean_x;
            (cov + dx * (y - mean_y), var + dx * dx)
        });
        let slope = cov / var;
        let rms = (points()
            .map(|(x, y)| (y - mean_y - slope * (x - mean_x)).powi(2))
            .sum::<f64>()
            / count)
            .sqrt();
        let ppm = slope * 1_000_000.0;
        if ppm.abs() > RATE_LIMIT_PPM {
            return FitResult::RateRejected;
        }
        if rms > fit_rms_limit_ns(mode) {
            return FitResult::RmsRejected;
        }
        let offset = base_offset
            + (mean_y + slope * ((local_ns - base_local) as f64 - mean_x)).round() as i128;
        self.fit = Some(ClockFit {
            local_ns,
            remote_ns: local_ns + offset,
            rate_ppm: ppm,
            rms_ns: rms,
        });
        FitResult::Accepted
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mode_selection_never_fakes_hardware() {
        assert_eq!(
            timestamp_source(TimestampMode::Auto).unwrap().0,
            TimestampMode::Software
        );
        assert!(matches!(
            timestamp_source(TimestampMode::Hardware),
            Err(Unavailable)
        ));
    }
    #[test]
    fn phase_noise_is_gated_by_timestamp_mode() {
        let mut software = MediaClock::default();
        let mut hardware = MediaClock::default();
        let mut software_result = FitResult::WarmingUp;
        let mut hardware_result = FitResult::WarmingUp;
        // Each four-point group has zero mean and zero covariance with time.
        for i in 0..16 {
            let x = i * 125_000_000;
            let jitter = [850_000, -850_000, -850_000, 850_000][i as usize % 4];
            let remote = x + jitter + x * 80 / 1_000_000;
            software_result = software.observe(x, remote, TimestampMode::Software);
            hardware_result = hardware.observe(x, remote, TimestampMode::Hardware);
        }
        assert_eq!(software_result, FitResult::Accepted);
        assert_eq!(hardware_result, FitResult::RmsRejected);
        assert!((software.fit().unwrap().rate_ppm - 80.0).abs() < 0.01);
        assert!(hardware.fit().is_none());
    }

    #[test]
    fn fit_80_ppm_with_epoch_precision_and_reset() {
        let mut clock = MediaClock::default();
        let epoch = 1_900_000_000_000_000_123_i128;
        for i in 0..24 {
            let x = i * 125_000_000;
            clock.observe(x, epoch + x + x * 80 / 1_000_000, TimestampMode::Software);
        }
        let fit = clock.fit().unwrap();
        assert!((fit.rate_ppm - 80.0).abs() < 0.001);
        assert!(fit.rms_ns < 0.01);
        assert_eq!(fit.ptp_ns(3_000_000_000), epoch + 3_000_240_000);
        assert_eq!(fit.media_frame(0, 48_000), epoch * 48_000 / 1_000_000_000);
        clock.clear();
        assert!(clock.fit().is_none());
        for i in 0..24 {
            let x = i * 125_000_000;
            clock.observe(x, epoch + x - x / 25_000, TimestampMode::Software);
        }
        assert!((clock.fit().unwrap().rate_ppm + 40.0).abs() < 0.001);
    }

    #[test]
    fn packet_timing_matches_aes67_table_2() {
        // AES67-2023 Table 2 lines 1492-1544.
        let cases = [
            (48_000, 125, 6),
            (48_000, 333, 16),
            (44_100, 333, 16),
            (96_000, 333, 32),
            (192_000, 333, 64),
            (48_000, 250, 12),
            (48_000, 500, 24),
            (48_000, 1_000, 48),
            (48_000, 2_000, 96),
            (48_000, 4_000, 192),
            (44_100, 125, 6),
            (44_100, 250, 12),
            (44_100, 1_000, 48),
            (44_100, 4_000, 192),
            (96_000, 125, 12),
            (96_000, 1_000, 96),
            (192_000, 125, 24),
            (192_000, 1_000, 192),
            (88_200, 1_000, 96),
            (176_400, 1_000, 192),
        ];
        for (rate, ptime, samples) in cases {
            let timing = PacketTiming::new(rate, ptime).unwrap();
            assert_eq!(
                timing.samples_per_packet(),
                samples,
                "{rate} Hz / {ptime} us"
            );
        }
        // The 44.1 kHz 1 ms packet is 48/44100 s, not 1.000 ms.
        let timing = PacketTiming::new(44_100, 1_000).unwrap();
        assert!((timing.actual_packet_time_us() - 1088.435_374).abs() < 0.001);
        assert!((1_000_000.0 / timing.actual_packet_time_us() - 918.75).abs() < 0.001);
        // Unsupported rates are rejected (352.8/384 are outside this sender).
        assert!(PacketTiming::new(352_800, 1_000).is_none());
        assert!(PacketTiming::new(48_000, 300).is_none());
        let third = PacketTiming::new(48_000, 333).unwrap();
        assert!((third.actual_packet_time_us() - 333.333_333).abs() < 0.001);
    }

    #[test]
    fn rational_grid_never_drifts_and_timestamps_step_by_samples() {
        // 44.1 kHz / 1 ms: 48 samples per packet, 918.75 packets/s.
        let timing = PacketTiming::new(44_100, 1_000).unwrap();
        let mut timestamp = timing.timestamp(0);
        for slot in 0..918_750 {
            assert_eq!(timing.timestamp(slot), timestamp);
            timestamp = timestamp.wrapping_add(48);
        }
        // One hour contains exactly 3600 * 44100 samples: the deadline of the
        // last slot stays within one ns of the exact rational value.
        let slots = 3600 * 44_100 / 48;
        let exact = slots * i128::from(timing.samples_per_packet()) * NS_PER_S
            / i128::from(timing.sample_rate());
        assert!((timing.deadline(slots) - exact).abs() < 1);

        // 48 kHz / 125 us: 6 samples every 125000 ns exactly.
        let fast = PacketTiming::new(48_000, 125).unwrap();
        for slot in [0_i128, 1, 7, 8, 9, 12345] {
            assert_eq!(fast.deadline(slot), slot * 125_000);
            assert_eq!(fast.timestamp(slot), (slot * 6) as u32);
            assert_eq!(fast.slot_at(slot * 125_000), slot);
        }
    }
}
