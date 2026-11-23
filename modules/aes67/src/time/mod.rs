//! Software socket-boundary timestamps and an affine media clock.
use std::{
    collections::VecDeque,
    io,
    net::{SocketAddr, SocketAddrV4, UdpSocket},
    time::Instant,
};

pub const NS_PER_MS: i128 = 1_000_000;
pub const FRAMES_PER_MS: i128 = 48;

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
    /// Epoch arithmetic is integer; only the small local delta is converted to float.
    pub fn ptp_ns(self, local_ns: i128) -> i128 {
        let elapsed = local_ns - self.local_ns;
        self.remote_ns + elapsed + (elapsed as f64 * self.rate_ppm / 1_000_000.0).round() as i128
    }
    pub fn offset_ns(self, local_ns: i128) -> i128 {
        self.ptp_ns(local_ns) - local_ns
    }
    pub fn media_frame(self, local_ns: i128) -> i128 {
        (self.ptp_ns(local_ns) * 48_000).div_euclid(1_000_000_000)
    }
}

const MAX_SAMPLES: usize = 24;
const MIN_SAMPLES: usize = 8;
const MIN_FIT_SPAN_NS: i128 = 750_000_000;
const FIT_RMS_LIMIT_NS: f64 = 250_000.0;
const RATE_LIMIT_PPM: f64 = 500.0;

#[derive(Default)]
pub struct MediaClock {
    samples: VecDeque<(i128, i128)>,
    fit: Option<ClockFit>,
    bad_samples: u8,
}
impl MediaClock {
    pub fn clear(&mut self) {
        self.samples.clear();
        self.fit = None;
        self.bad_samples = 0;
    }
    pub fn fit(&self) -> Option<ClockFit> {
        self.fit
    }
    pub fn observe(&mut self, local_ns: i128, ptp_ns: i128) {
        if self
            .samples
            .back()
            .is_some_and(|&(local, _)| local_ns <= local)
        {
            return;
        }
        self.samples.push_back((local_ns, ptp_ns - local_ns));
        if self.samples.len() > MAX_SAMPLES {
            self.samples.pop_front();
        }
        let &(base_local, base_offset) = self.samples.front().unwrap();
        if self.samples.len() < MIN_SAMPLES || local_ns - base_local < MIN_FIT_SPAN_NS {
            return;
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
        if ppm.abs() > RATE_LIMIT_PPM || rms > FIT_RMS_LIMIT_NS {
            self.bad_samples = self.bad_samples.saturating_add(1);
            if self.bad_samples >= 3 {
                self.clear();
            }
            return;
        }
        self.bad_samples = 0;
        let offset = base_offset
            + (mean_y + slope * ((local_ns - base_local) as f64 - mean_x)).round() as i128;
        self.fit = Some(ClockFit {
            local_ns,
            remote_ns: local_ns + offset,
            rate_ppm: ppm,
            rms_ns: rms,
        });
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
    fn fit_80_ppm_with_epoch_precision_and_reset() {
        let mut clock = MediaClock::default();
        let epoch = 1_900_000_000_000_000_123_i128;
        for i in 0..24 {
            let x = i * 125_000_000;
            clock.observe(x, epoch + x + x * 80 / 1_000_000);
        }
        let fit = clock.fit().unwrap();
        assert!((fit.rate_ppm - 80.0).abs() < 0.001);
        assert!(fit.rms_ns < 0.01);
        assert_eq!(fit.ptp_ns(3_000_000_000), epoch + 3_000_240_000);
        assert_eq!(fit.media_frame(0), epoch * 48_000 / 1_000_000_000);
        clock.clear();
        assert!(clock.fit().is_none());
        for i in 0..24 {
            let x = i * 125_000_000;
            clock.observe(x, epoch + x - x / 25_000);
        }
        assert!((clock.fit().unwrap().rate_ppm + 40.0).abs() < 0.001);
    }
}
