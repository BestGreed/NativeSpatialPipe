use std::collections::VecDeque;

/// A bounded interleaved ring on the input frame timeline. Output channel
/// selection and duplication are done once while copying the producer's lease.
pub struct Ring {
    samples: VecDeque<f32>,
    capacity_frames: usize,
    channels: usize,
    head: Option<u64>,
}

pub struct Accepted {
    pub late: u64,
}

impl Ring {
    pub fn new(capacity_frames: usize, channels: usize) -> Self {
        Self {
            samples: VecDeque::with_capacity(capacity_frames * channels),
            capacity_frames,
            channels,
            head: None,
        }
    }
    pub fn frames(&self) -> usize {
        self.samples.len() / self.channels
    }
    pub fn clear(&mut self) {
        self.samples.clear();
        self.head = None;
    }

    pub fn copy(
        &mut self,
        first: u64,
        input: &[f32],
        inputs: usize,
        map: &[usize],
    ) -> Option<Accepted> {
        let end = first.checked_add((input.len() / inputs) as u64)?;
        let head = self.head.unwrap_or(first);
        let buffered_end = head.saturating_add(self.frames() as u64);
        let late = (input.len() / inputs) as u64 - end.saturating_sub(buffered_end.max(first));
        let write = end.saturating_sub(buffered_end.max(first));
        if write == 0 {
            return Some(Accepted { late });
        }
        let gap = first.saturating_sub(buffered_end);
        if write.saturating_add(gap) > (self.capacity_frames - self.frames()) as u64 {
            return None;
        }
        self.head = Some(head);
        self.samples
            .extend(std::iter::repeat_n(0.0, gap as usize * self.channels));
        for frame in input[late as usize * inputs..].chunks_exact(inputs) {
            self.samples.extend(map.iter().map(|&source| frame[source]));
        }
        Some(Accepted { late })
    }

    pub fn skip(&mut self, frames: u64) {
        let discard = (self.frames() as u64).min(frames) as usize;
        self.samples.drain(..discard * self.channels);
        if let Some(head) = &mut self.head {
            *head = head.saturating_add(frames);
        }
    }

    /// Consumes one output interval and zero pads the portion currently missing.
    pub fn consume(&mut self, out: &mut [f32]) -> bool {
        let available = out.len().min(self.samples.len());
        for (dst, sample) in out[..available]
            .iter_mut()
            .zip(self.samples.drain(..available))
        {
            *dst = sample;
        }
        out[available..].fill(0.0);
        if let Some(head) = &mut self.head {
            *head = head.saturating_add((out.len() / self.channels) as u64);
        }
        available != out.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn routing_and_gaps_use_a_bounded_frame_timeline() {
        let mut ring = Ring::new(4, 2);
        assert!(ring.copy(100, &[1.0, 2.0, 3.0], 3, &[2, 0]).is_some());
        assert!(ring.copy(105, &[4.0, 5.0, 6.0], 3, &[1, 1]).is_none());
        assert!(ring.copy(102, &[4.0, 5.0, 6.0], 3, &[1, 1]).is_some());
        let mut out = [0.0; 8];
        assert!(ring.consume(&mut out));
        assert_eq!(out, [3.0, 1.0, 0.0, 0.0, 5.0, 5.0, 0.0, 0.0]);
        assert_eq!(
            ring.copy(102, &[4.0, 5.0, 6.0], 3, &[1, 1]).unwrap().late,
            1
        );
    }
}
