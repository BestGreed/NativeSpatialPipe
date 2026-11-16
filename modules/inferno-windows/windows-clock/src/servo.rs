use std::collections::VecDeque;
use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::{cell::Cell, sync::Arc};

#[derive(Clone, Copy, Debug)]
pub struct Exchange {
    pub t1: i64, // master Sync transmit
    pub t2: i64, // local Sync receive
    pub t3: i64, // local Delay_Req transmit
    pub t4: i64, // master Delay_Req receive
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Mapping {
    pub local_ns: i64,
    pub remote_ns: i64,
    pub rate_ppb: i64,
    pub valid_until_ns: i64,
}

impl Mapping {
    pub fn at(self, local: i64) -> Option<i64> {
        if local < self.local_ns || local > self.valid_until_ns {
            return None;
        }
        let elapsed = local as i128 - self.local_ns as i128;
        let value =
            self.remote_ns as i128 + elapsed + elapsed * self.rate_ppb as i128 / 1_000_000_000;
        value.try_into().ok()
    }
    /// Exact affine inverse, for later TX deadline calculations.
    pub fn local_deadline(self, remote: i64) -> Option<i64> {
        let delta = remote as i128 - self.remote_ns as i128;
        let denominator = 1_000_000_000i128 + self.rate_ppb as i128;
        if denominator <= 0 || delta < 0 {
            return None;
        }
        let scaled = delta.checked_mul(1_000_000_000)?;
        let local = self.local_ns as i128 + (scaled + denominator - 1) / denominator;
        let local = i64::try_from(local).ok()?;
        (local <= self.valid_until_ns).then_some(local)
    }
}

/// Single writer, bounded-retry readers; no lock/allocation on a TX read.
#[derive(Default)]
struct Shared {
    sequence: AtomicU64,
    local: AtomicI64,
    remote: AtomicI64,
    rate: AtomicI64,
    expires: AtomicI64,
}

impl Shared {
    fn publish(&self, mapping: Option<Mapping>) {
        self.sequence.fetch_add(1, Ordering::SeqCst);
        let m = mapping.unwrap_or(Mapping {
            local_ns: 0,
            remote_ns: 0,
            rate_ppb: 0,
            valid_until_ns: -1,
        });
        self.local.store(m.local_ns, Ordering::SeqCst);
        self.remote.store(m.remote_ns, Ordering::SeqCst);
        self.rate.store(m.rate_ppb, Ordering::SeqCst);
        self.expires.store(m.valid_until_ns, Ordering::SeqCst);
        self.sequence.fetch_add(1, Ordering::SeqCst);
    }
    fn load(&self, now: i64) -> Option<Mapping> {
        for _ in 0..4 {
            let before = self.sequence.load(Ordering::SeqCst);
            if before == 0 || before & 1 != 0 {
                continue;
            }
            let m = Mapping {
                local_ns: self.local.load(Ordering::SeqCst),
                remote_ns: self.remote.load(Ordering::SeqCst),
                rate_ppb: self.rate.load(Ordering::SeqCst),
                valid_until_ns: self.expires.load(Ordering::SeqCst),
            };
            if before == self.sequence.load(Ordering::SeqCst) {
                return m.at(now).map(|_| m);
            }
        }
        None
    }
}

/// One non-cloneable writer; readers own only the shared atomic fields.
#[derive(Default)]
pub struct Snapshot {
    shared: Arc<Shared>,
}
impl Snapshot {
    pub fn publish(&mut self, mapping: Option<Mapping>) {
        self.shared.publish(mapping);
    }
    pub fn load(&self, now: i64) -> Option<Mapping> {
        self.shared.load(now)
    }
    pub fn reader(&self) -> ClockReader {
        ClockReader {
            shared: self.shared.clone(),
            last_remote: Cell::new(None),
        }
    }
}
/// Create one reader per consumer. Reject a backward correction instead of
/// exposing backward network time or silently claiming the old mapping is valid.
pub struct ClockReader {
    shared: Arc<Shared>,
    last_remote: Cell<Option<i64>>,
}
impl ClockReader {
    pub fn now_ns(&self, local: i64) -> Option<i64> {
        let value = self.shared.load(local)?.at(local)?;
        if self.last_remote.get().is_some_and(|last| value < last) {
            return None;
        }
        self.last_remote.set(Some(value));
        Some(value)
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Config {
    pub min_samples: usize,
    pub min_span_ns: i64,
    pub timeout_ns: i64,
    pub max_delay_ns: i64,
    pub delay_margin_ns: i64,
    pub max_residual_ns: i64,
    pub max_rate_ppb: i64,
    pub step_limit_ns: i64,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            min_samples: 8,
            min_span_ns: 2_000_000_000,
            timeout_ns: 2_000_000_000,
            max_delay_ns: 20_000_000,
            delay_margin_ns: 200_000,
            max_residual_ns: 100_000,
            max_rate_ppb: 500_000,
            step_limit_ns: 5_000_000,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Status {
    pub samples: usize,
    pub residual_ns: f64,
    pub delay_ns: i64,
    pub rate_ppb: i64,
    pub locked: bool,
}

pub struct Servo {
    config: Config,
    samples: VecDeque<(i64, i64, i64)>,
    last_t3: Option<i64>,
    mapping: Option<Mapping>,
    pub status: Status,
}

impl Servo {
    pub fn new(config: Config) -> Self {
        assert!(config.min_samples >= 2 && config.min_samples <= 32);
        Self {
            config,
            samples: VecDeque::with_capacity(32),
            last_t3: None,
            mapping: None,
            status: Status {
                samples: 0,
                residual_ns: 0.0,
                delay_ns: 0,
                rate_ppb: 0,
                locked: false,
            },
        }
    }
    pub fn mapping(&self, now: i64) -> Option<Mapping> {
        self.mapping.filter(|m| m.at(now).is_some())
    }
    pub fn reset(&mut self) {
        self.samples.clear();
        self.mapping = None;
        self.last_t3 = None;
        self.status = Status {
            samples: 0,
            residual_ns: 0.0,
            delay_ns: 0,
            rate_ppb: 0,
            locked: false,
        };
    }
    pub fn observe(&mut self, e: Exchange, now: i64) -> Result<Status, &'static str> {
        if e.t2 < 0
            || e.t3 < e.t2
            || now < e.t3
            || now - e.t2 > self.config.timeout_ns
            || e.t4 < e.t1
        {
            return Err("invalid or stale timestamp order");
        }
        if self.last_t3.is_some_and(|last| e.t3 <= last) {
            return Err("duplicate/out-of-order exchange");
        }
        if self
            .last_t3
            .is_some_and(|last| e.t3 - last > self.config.timeout_ns)
        {
            self.reset();
        }
        let local = ((e.t2 as i128 + e.t3 as i128) / 2) as i64;
        let remote = ((e.t1 as i128 + e.t4 as i128) / 2) as i64;
        let rate = 1.0 + self.status.rate_ppb as f64 / 1e9;
        let delay = ((e.t4 as i128 - e.t1 as i128) as f64 - (e.t3 - e.t2) as f64 * rate) / 2.0;
        if delay < 0.0 || delay > self.config.max_delay_ns as f64 {
            return Err("invalid/excessive path delay");
        }
        let delay = delay.round() as i64;
        if let Some(min_delay) = self.samples.iter().map(|p| p.2).min() {
            if delay > min_delay + self.config.delay_margin_ns {
                return Err("path delay outlier");
            }
        }
        if let Some(m) = self.mapping {
            let delta = local as i128 - m.local_ns as i128;
            let predicted =
                m.remote_ns as i128 + delta + delta * m.rate_ppb as i128 / 1_000_000_000;
            if (remote as i128 - predicted).abs() > self.config.step_limit_ns as i128 {
                self.reset();
                return Err("clock discontinuity: reacquire required");
            }
        }
        let evicted = if self.samples.len() == 32 {
            self.samples.pop_front()
        } else {
            None
        };
        self.samples.push_back((local, remote, delay));
        let &(x0, y0, _) = self.samples.front().unwrap();
        let n = self.samples.len() as f64;
        let mx = self.samples.iter().map(|p| (p.0 - x0) as f64).sum::<f64>() / n;
        let my = self
            .samples
            .iter()
            .map(|p| (p.1 as i128 - y0 as i128) as f64)
            .sum::<f64>()
            / n;
        let variance = self
            .samples
            .iter()
            .map(|p| ((p.0 - x0) as f64 - mx).powi(2))
            .sum::<f64>();
        let covariance = self
            .samples
            .iter()
            .map(|p| ((p.0 - x0) as f64 - mx) * ((p.1 as i128 - y0 as i128) as f64 - my))
            .sum::<f64>();
        let slope = if variance > 0.0 {
            covariance / variance
        } else {
            1.0
        };
        let intercept = my - slope * mx;
        let residual = (self
            .samples
            .iter()
            .map(|p| {
                ((p.1 as i128 - y0 as i128) as f64 - intercept - slope * (p.0 - x0) as f64).powi(2)
            })
            .sum::<f64>()
            / n)
            .sqrt();
        let ppb = ((slope - 1.0) * 1e9).round() as i64;
        let locked = self.samples.len() >= self.config.min_samples
            && local - x0 >= self.config.min_span_ns
            && residual <= self.config.max_residual_ns as f64
            && ppb.abs() <= self.config.max_rate_ppb;
        // A noisy candidate must neither destroy a still-valid clock nor renew
        // its lease. Keep the last validated fit and undo this candidate so an
        // isolated scheduling spike cannot poison the entire regression window.
        if !locked && self.mapping(now).is_some() {
            self.samples.pop_back();
            if let Some(sample) = evicted {
                self.samples.push_front(sample);
            }
            return Err("fit quality outlier; retaining unexpired mapping");
        }
        self.last_t3 = Some(e.t3);
        self.status = Status {
            samples: self.samples.len(),
            residual_ns: residual,
            delay_ns: delay,
            rate_ppb: ppb,
            locked,
        };
        self.mapping = if locked {
            let correction = intercept + slope * (e.t3 - x0) as f64;
            let remote_ns = i64::try_from(y0 as i128 + correction.round() as i128)
                .map_err(|_| "remote clock overflow")?;
            Some(Mapping {
                local_ns: e.t3,
                remote_ns,
                rate_ppb: ppb,
                valid_until_ns: e
                    .t3
                    .checked_add(self.config.timeout_ns)
                    .ok_or("expiry overflow")?,
            })
        } else {
            self.mapping.filter(|mapping| mapping.at(now).is_some())
        };
        Ok(self.status)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn exchange(i: i64, offset: i64, ppb: i64) -> Exchange {
        let local = 10_000_000_000 + i * 250_000_000;
        let map = |t: i64| t + offset + ((t as i128 * ppb as i128) / 1_000_000_000) as i64;
        Exchange {
            t1: map(local) - 50_000,
            t2: local,
            t3: local + 1_000_000,
            t4: map(local + 1_000_000) + 50_000,
        }
    }
    #[test]
    fn converges_positive_and_negative_frequency() {
        for ppb in [-125_000, 0, 80_000] {
            let mut s = Servo::new(Config::default());
            for i in 0..32 {
                let e = exchange(i, 1_700_000_000_000_000_000, ppb);
                s.observe(e, e.t3 + 10_000_000).unwrap();
            }
            assert!(s.status.locked);
            assert!((s.status.rate_ppb - ppb).abs() <= 1);
            let e = exchange(31, 1_700_000_000_000_000_000, ppb);
            assert!((s.mapping(e.t3).unwrap().at(e.t3).unwrap() - (e.t4 - 50_000)).abs() <= 2);
        }
    }
    #[test]
    fn rejects_outliers_duplicates_and_invalid_order() {
        let mut s = Servo::new(Config::default());
        let e = exchange(0, 100, 0);
        s.observe(e, e.t3).unwrap();
        assert!(s.observe(e, e.t3).is_err());
        let mut e = exchange(1, 100, 0);
        e.t4 += 2_000_000;
        assert!(s.observe(e, e.t3).is_err());
        e.t3 = e.t2 - 1;
        assert!(s.observe(e, e.t2).is_err());
    }
    #[test]
    fn stale_and_clock_step_revoke_lock() {
        let mut s = Servo::new(Config::default());
        for i in 0..16 {
            let e = exchange(i, 1_000_000_000, 0);
            s.observe(e, e.t3).unwrap();
        }
        let e = exchange(15, 1_000_000_000, 0);
        assert!(s.mapping(e.t3 + 2_000_000_001).is_none());
        let e = exchange(16, 2_000_000_000, 0);
        assert!(s.observe(e, e.t3).is_err());
        assert!(s.mapping(e.t3).is_none());
    }
    #[test]
    fn large_frequency_does_not_lock() {
        let mut s = Servo::new(Config::default());
        for i in 0..16 {
            let e = exchange(i, 100, 1_000_000);
            s.observe(e, e.t3).unwrap();
        }
        assert!(!s.status.locked);
    }
    #[test]
    fn noisy_fit_keeps_only_original_lease_and_recovers_on_clean_sample() {
        let mut s = Servo::new(Config::default());
        for i in 0..32 {
            let e = exchange(i, 1_000_000_000, 0);
            s.observe(e, e.t3).unwrap();
        }
        let last = exchange(31, 1_000_000_000, 0);
        let original = s.mapping(last.t3).unwrap();
        let mut noisy = exchange(32, 1_000_000_000, 0);
        // Equal shifts change the fitted offset without changing path delay.
        noisy.t1 += 1_000_000;
        noisy.t4 += 1_000_000;
        assert!(s
            .observe(noisy, noisy.t3)
            .unwrap_err()
            .contains("fit quality"));
        assert_eq!(s.mapping(noisy.t3), Some(original));
        assert!(s.mapping(original.valid_until_ns + 1).is_none());
        let clean = exchange(33, 1_000_000_000, 0);
        s.observe(clean, clean.t3).unwrap();
        let recovered = s.mapping(clean.t3).unwrap();
        assert!(recovered.valid_until_ns > original.valid_until_ns);
        assert_eq!(recovered.rate_ppb, 0);
    }
    #[test]
    fn snapshot_and_exact_inverse() {
        let mut snapshot = Snapshot::default();
        assert!(snapshot.load(0).is_none());
        let m = Mapping {
            local_ns: 100,
            remote_ns: 1000,
            rate_ppb: 100_000,
            valid_until_ns: 2_000_000_100,
        };
        snapshot.publish(Some(m));
        let t = 1_000_000_100;
        let remote = m.at(t).unwrap();
        assert_eq!(m.local_deadline(remote), Some(t));
        assert!(snapshot.load(m.valid_until_ns + 1).is_none());
        snapshot.publish(None);
        assert!(snapshot.load(100).is_none());
    }
    #[test]
    fn readers_reject_backward_updates_and_can_cross_threads() {
        let mut s = Snapshot::default();
        let reader = s.reader();
        s.publish(Some(Mapping {
            local_ns: 10,
            remote_ns: 1000,
            rate_ppb: 0,
            valid_until_ns: 1000,
        }));
        assert_eq!(reader.now_ns(10), Some(1000));
        s.publish(Some(Mapping {
            local_ns: 11,
            remote_ns: 900,
            rate_ppb: 0,
            valid_until_ns: 1000,
        }));
        assert_eq!(reader.now_ns(11), None);
        assert_eq!(reader.now_ns(111), Some(1000));
        let reader = s.reader();
        let thread = std::thread::spawn(move || reader.now_ns(12));
        assert_eq!(thread.join().unwrap(), Some(901));
    }
}
