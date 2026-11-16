//! Clock estimation from separate forward/reverse delay bounds.
//! For nonnegative path delays, t1 <= remote(t2) and remote(t3) <= t4.
//! Intersecting these bounds across observations avoids treating variable
//! receive/transmit queue asymmetry as a change in clock frequency or phase.
use crate::servo::{Config, Exchange, Mapping};
use std::collections::VecDeque;

const HISTORY: usize = 128;
const BILLION: f64 = 1_000_000_000.0;
// This is a directional path-uncertainty budget, not midpoint-fit RMS or a
// claim of calibrated absolute synchronization. Keep the metrics separate.
const MAX_INTERVAL_HALF_WIDTH_NS: f64 = 200_000.0;

#[derive(Clone, Copy, Debug, Default)]
pub struct Status {
    pub rate_ppb: i64,
    pub residual_ns: f64,
    pub interval_half_width_ns: f64,
    pub locked: bool,
}

pub struct EnvelopeServo {
    config: Config,
    history: VecDeque<Exchange>,
    mapping: Option<Mapping>,
    last_t3: Option<i64>,
    pub status: Status,
}

fn floor_div(a: i128, b: i128) -> i128 {
    let q = a / b;
    if a % b != 0 && (a < 0) != (b < 0) {
        q - 1
    } else {
        q
    }
}

impl EnvelopeServo {
    pub fn new(config: Config) -> Self {
        assert!((2..=HISTORY).contains(&config.min_samples));
        Self {
            config,
            history: VecDeque::with_capacity(HISTORY),
            mapping: None,
            last_t3: None,
            status: Status::default(),
        }
    }
    pub fn mapping(&self, now: i64) -> Option<Mapping> {
        self.mapping.filter(|m| m.at(now).is_some())
    }
    fn reset(&mut self) {
        self.history.clear();
        self.mapping = None;
        self.last_t3 = None;
        self.status = Status::default();
    }
    fn bounds(&self, reference: i64, rate: i64) -> (f64, f64) {
        let mut lower = f64::NEG_INFINITY;
        let mut upper = f64::INFINITY;
        for e in &self.history {
            lower = lower.max(
                (e.t1 as i128 - e.t2 as i128) as f64
                    - (e.t2 - reference) as f64 * rate as f64 / BILLION,
            );
            upper = upper.min(
                (e.t4 as i128 - e.t3 as i128) as f64
                    - (e.t3 - reference) as f64 * rate as f64 / BILLION,
            );
        }
        (lower, upper)
    }
    fn feasible_rates(&self) -> Option<(i64, i64)> {
        let mut lower = -(self.config.max_rate_ppb as i128);
        let mut upper = self.config.max_rate_ppb as i128;
        for a in &self.history {
            for b in &self.history {
                let c =
                    ((a.t1 as i128 - a.t2 as i128) - (b.t4 as i128 - b.t3 as i128)) * 1_000_000_000;
                let d = a.t2 as i128 - b.t3 as i128;
                if d > 0 {
                    lower = lower.max(-floor_div(-c, d));
                } else if d < 0 {
                    upper = upper.min(floor_div(c, d));
                } else if c > 0 {
                    return None;
                }
                if lower > upper {
                    return None;
                }
            }
        }
        Some((lower.try_into().ok()?, upper.try_into().ok()?))
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
        if self.last_t3.is_some_and(|t| e.t3 <= t) {
            return Err("duplicate/out-of-order exchange");
        }
        if self
            .last_t3
            .is_some_and(|t| e.t3 - t > self.config.timeout_ns)
        {
            self.reset();
        }
        let raw_delay =
            ((e.t4 as i128 - e.t1 as i128) - (e.t3 as i128 - e.t2 as i128)) as f64 / 2.0;
        if raw_delay < -(self.config.max_rate_ppb as f64 / BILLION * (e.t3 - e.t2) as f64)
            || raw_delay > self.config.max_delay_ns as f64
        {
            return Err("invalid/excessive path delay");
        }
        if let Some(m) = self.mapping {
            let predict = |local: i64| {
                m.remote_ns as i128
                    + (local - m.local_ns) as i128
                    + (local - m.local_ns) as i128 * m.rate_ppb as i128 / 1_000_000_000
            };
            if e.t1 as i128 - predict(e.t2) > self.config.step_limit_ns as i128
                || predict(e.t3) - e.t4 as i128 > self.config.step_limit_ns as i128
            {
                self.reset();
                return Err("clock discontinuity: reacquire required");
            }
        }
        let evicted = if self.history.len() == HISTORY {
            self.history.pop_front()
        } else {
            None
        };
        self.history.push_back(e);
        // Midpoint regression proposes a frequency; directional bounds constrain it.
        let first = self.history.front().unwrap();
        let x0 = (first.t2 as i128 + first.t3 as i128) / 2;
        let y0 = (first.t1 as i128 + first.t4 as i128 - first.t2 as i128 - first.t3 as i128) / 2;
        let n = self.history.len() as f64;
        let mut storage = [(0.0, 0.0); HISTORY];
        for (point, v) in storage.iter_mut().zip(&self.history) {
            *point = (
                ((v.t2 as i128 + v.t3 as i128) / 2 - x0) as f64,
                ((v.t1 as i128 + v.t4 as i128 - v.t2 as i128 - v.t3 as i128) / 2 - y0) as f64,
            );
        }
        let points = &storage[..self.history.len()];
        let mx = points.iter().map(|p| p.0).sum::<f64>() / n;
        let my = points.iter().map(|p| p.1).sum::<f64>() / n;
        let variance = points.iter().map(|p| (p.0 - mx).powi(2)).sum::<f64>();
        let slope = if variance > 0.0 {
            points.iter().map(|p| (p.0 - mx) * (p.1 - my)).sum::<f64>() / variance
        } else {
            0.0
        };
        let estimate = slope * BILLION;
        let mut rate = if let Some(previous) = self.mapping(now) {
            ((previous.rate_ppb as f64 * 7.0 + estimate) / 8.0).round() as i64
        } else {
            estimate.round() as i64
        };
        rate = rate.clamp(-self.config.max_rate_ppb, self.config.max_rate_ppb);
        let (mut lower, mut upper) = self.bounds(e.t3, rate);
        if lower > upper {
            let Some((rmin, rmax)) = self.feasible_rates() else {
                self.history.pop_back();
                if let Some(old) = evicted {
                    self.history.push_front(old);
                }
                return Err("inconsistent directional clock bounds");
            };
            rate = rate.clamp(rmin, rmax);
            (lower, upper) = self.bounds(e.t3, rate);
        }
        self.last_t3 = Some(e.t3);
        let half_width = ((upper - lower) / 2.0).max(0.0);
        let residual = (points
            .iter()
            .map(|p| (p.1 - my - slope * (p.0 - mx)).powi(2))
            .sum::<f64>()
            / n)
            .sqrt();
        let qualified = self.history.len() >= self.config.min_samples
            && e.t3 - self.history.front().unwrap().t2 >= self.config.min_span_ns
            && (half_width <= MAX_INTERVAL_HALF_WIDTH_NS
                || residual <= self.config.max_residual_ns as f64);
        if qualified {
            let phase = self
                .mapping(now)
                .and_then(|m| m.at(e.t3))
                .map(|remote| (remote as i128 - e.t3 as i128) as f64)
                .unwrap_or((lower + upper) / 2.0)
                .clamp(lower.min(upper), upper.max(lower));
            let remote_ns = i64::try_from(e.t3 as i128 + phase.round() as i128)
                .map_err(|_| "remote clock overflow")?;
            self.mapping = Some(Mapping {
                local_ns: e.t3,
                remote_ns,
                rate_ppb: rate,
                valid_until_ns: e
                    .t3
                    .checked_add(self.config.timeout_ns)
                    .ok_or("expiry overflow")?,
            });
        }
        self.status = Status {
            rate_ppb: rate,
            residual_ns: residual,
            interval_half_width_ns: half_width,
            locked: self.mapping(now).is_some(),
        };
        Ok(self.status)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn exchange(i: i64, offset: i64, ppb: i64, asymmetric: bool) -> Exchange {
        let t2 = 10_000_000_000 + i * 250_000_000;
        let t3 = t2 + 2_000_000;
        let remote = |t: i64| t + offset + (t as i128 * ppb as i128 / 1_000_000_000) as i64;
        let (forward, reverse) = if asymmetric {
            if i % 2 == 0 {
                (950_000, 50_000)
            } else {
                (50_000, 950_000)
            }
        } else {
            (50_000, 50_000)
        };
        Exchange {
            t1: remote(t2) - forward,
            t2,
            t3,
            t4: remote(t3) + reverse,
        }
    }
    #[test]
    fn directional_bounds_tolerate_alternating_queue_asymmetry() {
        for ppb in [-80_000, 0, 80_000] {
            let mut s = EnvelopeServo::new(Config::default());
            for i in 0..160 {
                let e = exchange(i, 1_000_000_000, ppb, true);
                s.observe(e, e.t3 + 1_000_000).unwrap();
            }
            let e = exchange(159, 1_000_000_000, ppb, true);
            let remote = e.t3 + 1_000_000_000 + (e.t3 as i128 * ppb as i128 / 1_000_000_000) as i64;
            let m = s
                .mapping(e.t3)
                .expect("alternating queues should not prevent acquisition");
            assert!((m.at(e.t3).unwrap() - remote).abs() <= 100_000);
        }
    }
    #[test]
    fn expiration_and_real_clock_step_revoke_mapping() {
        let mut s = EnvelopeServo::new(Config::default());
        for i in 0..32 {
            let e = exchange(i, 1_000_000_000, 0, false);
            s.observe(e, e.t3).unwrap();
        }
        let e = exchange(31, 1_000_000_000, 0, false);
        assert!(s.mapping(e.t3 + 2_000_000_001).is_none());
        let e = exchange(32, 2_000_000_000, 0, false);
        assert!(s.observe(e, e.t3).is_err());
        assert!(s.mapping(e.t3).is_none());
    }
    #[test]
    fn duplicate_and_invalid_exchanges_cannot_refresh_lease() {
        let mut s = EnvelopeServo::new(Config::default());
        let e = exchange(0, 1_000_000_000, 0, false);
        s.observe(e, e.t3).unwrap();
        assert!(s.observe(e, e.t3).is_err());
        let mut bad = exchange(1, 1_000_000_000, 0, false);
        bad.t3 = bad.t2 - 1;
        assert!(s.observe(bad, bad.t2).is_err());
    }
    #[test]
    fn excessive_rate_does_not_acquire() {
        let mut s = EnvelopeServo::new(Config::default());
        for i in 0..64 {
            let e = exchange(i, 1_000_000_000, 1_000_000, false);
            let _ = s.observe(e, e.t3);
            assert!(s.mapping(e.t3).is_none());
        }
    }
    #[test]
    fn stable_fixed_queue_can_acquire_without_claiming_absolute_accuracy() {
        let mut s = EnvelopeServo::new(Config::default());
        for i in 0..64 {
            let mut e = exchange(i, 1_000_000_000, 0, false);
            e.t1 -= 600_000;
            s.observe(e, e.t3).unwrap();
        }
        let e = exchange(63, 1_000_000_000, 0, false);
        assert!(s.mapping(e.t3).is_some());
        assert!(s.status.interval_half_width_ns > MAX_INTERVAL_HALF_WIDTH_NS);
        assert!(s.status.residual_ns < 1.0);
    }
}
