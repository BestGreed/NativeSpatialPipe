//! Hardware-volume-driven common stereo EQ. All state belongs to the output writer.
use std::sync::{Arc, atomic::{AtomicU64, Ordering::Relaxed}};

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Level {
    pub spl_db: f64,
    pub low_db: f64,
    pub high_db: f64,
}

#[derive(Clone, Copy)]
struct Band {
    b0: f64,
    a: [f64; 2],
    state: [[f64; 2]; 2],
}

impl Band {
    fn new(hz: f64, q: f64) -> Self {
        let w = std::f64::consts::TAU * hz / 48_000.0;
        let alpha = w.sin() / (2.0 * q);
        let den = 1.0 + alpha;
        Self { b0: alpha / den, a: [-2.0 * w.cos() / den, (1.0 - alpha) / den], state: [[0.; 2]; 2] }
    }

    fn process(&mut self, channel: usize, x: f64) -> f64 {
        let z = &mut self.state[channel];
        let y = self.b0 * x + z[0];
        z[0] = z[1] - self.a[0] * y;
        z[1] = -self.b0 * x - self.a[1] * y;
        y
    }
}

pub struct Loudness {
    endpoint_db: Arc<AtomicU64>,
    reference_spl: f64,
    points: Box<[Level]>,
    bands: [Band; 2],
    amounts: [f64; 2],
    initialized: bool,
}

impl Loudness {
    pub fn new(endpoint_db: Arc<AtomicU64>, reference_spl: f64, points: &[Level]) -> Result<Self, String> {
        if !(20.0..=100.0).contains(&reference_spl) || !(2..=16).contains(&points.len())
            || points.iter().any(|p| !(20.0..=reference_spl).contains(&p.spl_db)
                || !(0.0..=4.0).contains(&p.low_db) || !(0.0..=1.0).contains(&p.high_db))
            || points.windows(2).any(|w| w[0].spl_db >= w[1].spl_db)
            || points.last().is_none_or(|p| p.spl_db != reference_spl || p.low_db != 0.0 || p.high_db != 0.0)
        {
            return Err("Invalid equal-loudness profile".into());
        }
        Ok(Self { endpoint_db, reference_spl, points: points.into(),
            bands: [Band::new(315.0, 1.1), Band::new(8000.0, 0.7)], amounts: [0.; 2], initialized: false })
    }

    fn target_db(&self, attenuation: f64) -> [f64; 2] {
        if !attenuation.is_finite() || attenuation >= 0.0 { return [0.; 2]; }
        let spl = (self.reference_spl + attenuation).max(self.points[0].spl_db);
        let upper = self.points.partition_point(|p| p.spl_db < spl).max(1);
        let (a, b) = (self.points[upper - 1], self.points[upper]);
        let t = (spl - a.spl_db) / (b.spl_db - a.spl_db);
        [a.low_db + t * (b.low_db - a.low_db), a.high_db + t * (b.high_db - a.high_db)]
    }

    pub fn process(&mut self, samples: &mut [f32]) {
        let target = self.target_db(f64::from_bits(self.endpoint_db.load(Relaxed)))
            .map(|db| 10_f64.powf(db / 20.0) - 1.0);
        if !self.initialized {
            self.amounts = target;
            self.initialized = true;
        }
        // Smooth every sample so the response is independent of writer block size.
        const STEP: f64 = 1.0 / 4_800.0;
        for frame in samples.chunks_exact_mut(2) {
            for (amount, target) in self.amounts.iter_mut().zip(target) {
                *amount += (target - *amount) * STEP;
                if (target - *amount).abs() < 1e-9 { *amount = target; }
            }
            for (ch, sample) in frame.iter_mut().enumerate() {
                let x = f64::from(*sample);
                let low = self.bands[0].process(ch, x);
                let high = self.bands[1].process(ch, x);
                if self.amounts != [0.; 2] {
                    *sample = (x + self.amounts[0] * low + self.amounts[1] * high) as f32;
                }
            }
        }
    }

    pub fn gains_db(&self) -> [f64; 2] {
        self.amounts.map(|amount| 20.0 * amount.ln_1p() / std::f64::consts::LN_10)
    }
}
