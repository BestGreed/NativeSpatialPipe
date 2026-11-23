use std::collections::VecDeque;

/// A bounded interleaved ring on the host frame timeline. Output channel
/// selection and duplication are done once while copying the producer's lease.
///
/// Two independent concerns share this buffer:
/// - `next_host` is the host frame cursor used to place producer blocks on a
///   continuous timeline. Only `copy` moves it.
/// - `samples` holds the buffered audio drained by `consume` (sent output
///   slots) or `skip` (slots the pacer dropped). Neither of those may move the
///   host cursor: the producer keeps advancing at real time, so tying the two
///   together lets a large skip push the cursor past every future block and
///   starve the ring permanently.
pub struct Ring {
    samples: VecDeque<f32>,
    capacity_frames: usize,
    channels: usize,
    next_host: Option<u64>,
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
            next_host: None,
        }
    }
    pub fn frames(&self) -> usize {
        self.samples.len() / self.channels
    }
    pub fn clear(&mut self) {
        self.samples.clear();
        self.next_host = None;
    }

    /// Copies one producer block onto the host frame timeline.
    ///
    /// The first accepted block anchors the timeline at `first`. A block that
    /// starts after the cursor inserts silence for the missing frames; a block
    /// that starts before it is trimmed as late. A rejection caused by capacity
    /// leaves the cursor untouched so the producer timeline is never corrupted.
    pub fn copy(
        &mut self,
        first: u64,
        input: &[f32],
        inputs: usize,
        map: &[usize],
    ) -> Option<Accepted> {
        let frames = (input.len() / inputs) as u64;
        let end = first.checked_add(frames)?;
        let cursor = self.next_host.unwrap_or(first);
        let late = cursor.saturating_sub(first).min(frames);
        let gap = first.saturating_sub(cursor);
        let accepted = frames - late;
        let needed = gap.checked_add(accepted)?;
        if needed > self.capacity_frames.saturating_sub(self.frames()) as u64 {
            return None;
        }
        self.next_host = Some(cursor.max(end));
        self.samples
            .extend(std::iter::repeat_n(0.0, gap as usize * self.channels));
        for frame in input[late as usize * inputs..].chunks_exact(inputs) {
            self.samples.extend(map.iter().map(|&source| frame[source]));
        }
        Some(Accepted { late })
    }

    /// Discards the oldest buffered frames for output slots the pacer dropped.
    /// The host frame cursor is deliberately left alone.
    pub fn skip(&mut self, frames: u64) -> u64 {
        let discard = (self.frames() as u64).min(frames) as usize;
        self.samples.drain(..discard * self.channels);
        discard as u64
    }

    /// Consumes one output interval and zero pads the portion currently missing.
    /// An under-run drains what is buffered without moving the host cursor.
    pub fn consume(&mut self, out: &mut [f32]) -> bool {
        let available = out.len().min(self.samples.len());
        for (dst, sample) in out[..available]
            .iter_mut()
            .zip(self.samples.drain(..available))
        {
            *dst = sample;
        }
        out[available..].fill(0.0);
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

    #[test]
    fn pacer_slot_drops_do_not_break_the_host_timeline() {
        const SLOT: usize = 48;
        const BLOCK: usize = 10 * SLOT;
        const LEVEL: f32 = 0.25;
        const PERIODS: u64 = 24_000; // four simulated minutes of 10 ms periods.

        let mut ring = Ring::new(100 * SLOT, 1); // 100 ms buffer.
        ring.copy(0, &[LEVEL; BLOCK], 1, &[0]).unwrap();
        let mut first = BLOCK as u64;
        let mut silent = 0u64;
        let mut audible = 0u64;
        let mut slots = 0u64;
        for period in 0..PERIODS {
            // Every 100 ms the worker wakes after an OS stall and drops 91
            // slots, one skip call per slot. That is far more audio than the
            // ring holds, which is exactly what used to desynchronize the
            // producer timeline.
            let stalled = period % 100 == 0;
            if stalled {
                for _ in 0..91 {
                    ring.skip(SLOT as u64);
                }
            }
            ring.copy(first, &[LEVEL; BLOCK], 1, &[0]).unwrap();
            first += BLOCK as u64;
            for _ in 0..if stalled { 9 } else { 10 } {
                let mut out = [0.0f32; SLOT];
                let short = ring.consume(&mut out);
                assert!(!short, "ring under-ran at period {period}");
                slots += 1;
                if out.iter().all(|sample| *sample == 0.0) {
                    silent += SLOT as u64;
                } else {
                    audible += SLOT as u64;
                    assert!(
                        out.iter().all(|sample| *sample == LEVEL),
                        "partial or corrupt block at period {period}"
                    );
                }
            }
        }
        assert_eq!(silent, 0);
        assert_eq!(audible, slots * SLOT as u64);
    }

    #[test]
    fn under_run_zero_pads_only_the_missing_part() {
        let mut ring = Ring::new(16, 1);
        ring.copy(0, &[0.5; 5], 1, &[0]).unwrap();
        let mut out = [9.0f32; 8];
        assert!(ring.consume(&mut out));
        assert_eq!(out, [0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.0, 0.0]);
        // The under-run must not move the host cursor: the block at the next
        // host frame is accepted whole rather than being treated as late.
        let accepted = ring.copy(5, &[0.25; 3], 1, &[0]).unwrap();
        assert_eq!(accepted.late, 0);
        let mut next = [9.0f32; 3];
        assert!(!ring.consume(&mut next));
        assert_eq!(next, [0.25; 3]);
    }

    #[test]
    fn skip_discards_oldest_samples_without_moving_the_host_cursor() {
        let mut ring = Ring::new(16, 1);
        ring.copy(0, &[1.0; 2], 1, &[0]).unwrap();
        assert_eq!(ring.skip(5), 2);
        let accepted = ring.copy(2, &[2.0; 3], 1, &[0]).unwrap();
        assert_eq!(accepted.late, 0);
        let mut out = [9.0f32; 3];
        assert!(!ring.consume(&mut out));
        assert_eq!(out, [2.0; 3]);
    }

    #[test]
    fn host_gap_inserts_silence_and_duplicate_blocks_are_trimmed() {
        let mut ring = Ring::new(32, 1);
        ring.copy(0, &[1.0; 2], 1, &[0]).unwrap();
        assert_eq!(ring.copy(4, &[2.0; 2], 1, &[0]).unwrap().late, 0);
        let mut out = [9.0f32; 6];
        assert!(!ring.consume(&mut out));
        assert_eq!(out, [1.0, 1.0, 0.0, 0.0, 2.0, 2.0]);
        // A duplicate block entirely behind the cursor is dropped, not replayed.
        assert_eq!(ring.copy(2, &[3.0; 4], 1, &[0]).unwrap().late, 4);
        let mut behind = [9.0f32; 4];
        assert!(ring.consume(&mut behind));
        assert_eq!(behind, [0.0; 4]);
        // A block overlapping the cursor keeps only its unseen tail.
        assert_eq!(ring.copy(4, &[4.0; 4], 1, &[0]).unwrap().late, 2);
        let mut tail = [9.0f32; 2];
        assert!(!ring.consume(&mut tail));
        assert_eq!(tail, [4.0, 4.0]);
    }

    #[test]
    fn capacity_rejection_keeps_the_host_cursor() {
        let mut ring = Ring::new(4, 1);
        ring.copy(0, &[1.0; 2], 1, &[0]).unwrap();
        assert!(ring.copy(10, &[2.0; 2], 1, &[0]).is_none());
        let accepted = ring.copy(2, &[2.0; 2], 1, &[0]).unwrap();
        assert_eq!(accepted.late, 0);
        let mut out = [9.0f32; 4];
        assert!(!ring.consume(&mut out));
        assert_eq!(out, [1.0, 1.0, 2.0, 2.0]);
    }

    #[test]
    fn clear_resets_the_host_cursor() {
        let mut ring = Ring::new(8, 1);
        ring.copy(0, &[1.0; 4], 1, &[0]).unwrap();
        ring.clear();
        assert_eq!(ring.copy(100, &[2.0; 4], 1, &[0]).unwrap().late, 0);
        let mut out = [9.0f32; 4];
        assert!(!ring.consume(&mut out));
        assert_eq!(out, [2.0; 4]);
    }
}
