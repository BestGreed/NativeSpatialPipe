//! Bounded, worker-owned PCM source roster and per-source input queues.
//!
//! This layer neither elects the primary nor assigns source clock periods. Its
//! owner supplies a mapped period and may later select same/previous-period
//! blocks at the render boundary. Producers must not auto-register old packets:
//! registration is a control action, and every submission uses its leased handle.
//! All storage is fixed-size. Rejected, expired and retired payloads are returned
//! to the caller for recycling; no queue operation allocates a Vec or grows a
//! container. Creating/upmixing a source remains a separate control-layer task.

use std::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SourceKey {
    pub(crate) stream: u64,
    pub(crate) generation: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SourceHandle {
    slot: usize,
    lease: u64,
    key: SourceKey,
}

impl SourceHandle {
    pub(crate) fn slot(self) -> usize { self.slot }
    pub(crate) fn key(self) -> SourceKey { self.key }
}

#[derive(Debug)]
pub(crate) struct PcmBlock<T> {
    pub(crate) sequence: u64,
    /// A monotonic period supplied by this source's clock mapper, not inferred
    /// here from arrival time. Different sources can have different mappings.
    pub(crate) period: u64,
    pub(crate) received: Instant,
    pub(crate) payload: T,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RegistrationKind { Created, Existing, NewGeneration }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RegisterError { Capacity, StaleGeneration, LeaseExhausted }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum AccessError { StaleHandle, WatermarkMovedBackwards }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PushReason {
    StaleHandle,
    Expired,
    DuplicateOrOutOfOrder,
    PeriodMovedBackwards,
    Full,
}

#[derive(Debug)]
pub(crate) struct RejectedBlock<T> {
    pub(crate) reason: PushReason,
    pub(crate) block: PcmBlock<T>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Accepted {
    /// Forward sequence gaps are observable but do not reset this source or any
    /// other source. A caller needing strict continuity can reject this upstream.
    pub(crate) skipped_sequences: u64,
}

/// Owned blocks in original FIFO order, returned without allocation.
#[derive(Debug)]
pub(crate) struct RetiredBlocks<T, const DEPTH: usize> {
    blocks: [Option<PcmBlock<T>>; DEPTH],
    len: usize,
}

impl<T, const DEPTH: usize> RetiredBlocks<T, DEPTH> {
    fn empty() -> Self { Self { blocks: std::array::from_fn(|_| None), len: 0 } }
    pub(crate) fn len(&self) -> usize { self.len }
    pub(crate) fn is_empty(&self) -> bool { self.len == 0 }
}

impl<T, const DEPTH: usize> IntoIterator for RetiredBlocks<T, DEPTH> {
    type Item = PcmBlock<T>;
    type IntoIter = std::iter::Flatten<std::array::IntoIter<Option<PcmBlock<T>>, DEPTH>>;
    fn into_iter(self) -> Self::IntoIter { self.blocks.into_iter().flatten() }
}

#[derive(Debug)]
pub(crate) struct Registration<T, const DEPTH: usize> {
    pub(crate) handle: SourceHandle,
    pub(crate) kind: RegistrationKind,
    pub(crate) previous: Option<SourceKey>,
    pub(crate) retired: RetiredBlocks<T, DEPTH>,
}

struct Queue<T, const DEPTH: usize> {
    blocks: [Option<PcmBlock<T>>; DEPTH],
    head: usize,
    len: usize,
}

impl<T, const DEPTH: usize> Queue<T, DEPTH> {
    fn new() -> Self { Self { blocks: std::array::from_fn(|_| None), head: 0, len: 0 } }
    fn front(&self) -> Option<&PcmBlock<T>> { self.blocks[self.head].as_ref() }
    fn push(&mut self, block: PcmBlock<T>) {
        let index = (self.head + self.len) % DEPTH;
        debug_assert!(self.len < DEPTH && self.blocks[index].is_none());
        self.blocks[index] = Some(block);
        self.len += 1;
    }
    fn pop(&mut self) -> Option<PcmBlock<T>> {
        if self.len == 0 { return None; }
        let block = self.blocks[self.head].take();
        self.head = (self.head + 1) % DEPTH;
        self.len -= 1;
        block
    }
    fn retire_before(&mut self, minimum: Option<u64>) -> RetiredBlocks<T, DEPTH> {
        let mut retired = RetiredBlocks::empty();
        while self.front().is_some_and(|block| minimum.is_none_or(|at| block.period < at)) {
            retired.blocks[retired.len] = self.pop();
            retired.len += 1;
        }
        retired
    }
}

struct Slot<T, const DEPTH: usize> {
    key: Option<SourceKey>,
    lease: u64,
    active: bool,
    minimum_period: u64,
    last_sequence: Option<u64>,
    last_period: Option<u64>,
    queue: Queue<T, DEPTH>,
}

impl<T, const DEPTH: usize> Slot<T, DEPTH> {
    fn new() -> Self {
        Self { key: None, lease: 0, active: false, minimum_period: 0,
            last_sequence: None, last_period: None, queue: Queue::new() }
    }
    fn matches(&self, handle: SourceHandle) -> bool {
        self.active && self.lease == handle.lease && self.key == Some(handle.key)
    }
}

pub(crate) struct PcmSources<T, const SOURCES: usize, const DEPTH: usize> {
    slots: [Slot<T, DEPTH>; SOURCES],
}

impl<T, const SOURCES: usize, const DEPTH: usize> PcmSources<T, SOURCES, DEPTH> {
    pub(crate) fn new() -> Self {
        assert!(SOURCES != 0 && DEPTH != 0, "PCM roster and queue capacities must be nonzero");
        Self { slots: std::array::from_fn(|_| Slot::new()) }
    }

    pub(crate) fn register(&mut self, key: SourceKey) -> Result<Registration<T, DEPTH>, RegisterError> {
        let known = self.slots.iter().position(|slot| slot.key.is_some_and(|old| old.stream == key.stream));
        if let Some(index) = known {
            let slot = &self.slots[index];
            let old = slot.key.unwrap();
            if key.generation < old.generation || (key.generation == old.generation && !slot.active) {
                return Err(RegisterError::StaleGeneration);
            }
            if key.generation == old.generation {
                return Ok(Registration {
                    handle: SourceHandle { slot: index, lease: slot.lease, key },
                    kind: RegistrationKind::Existing, previous: None, retired: RetiredBlocks::empty(),
                });
            }
        }
        let index = match known {
            Some(index) => index,
            None => self.slots.iter().position(|slot| !slot.active && slot.lease != u64::MAX)
                .ok_or_else(|| if self.slots.iter().any(|slot| !slot.active) { RegisterError::LeaseExhausted } else { RegisterError::Capacity })?,
        };
        let slot = &mut self.slots[index];
        let lease = slot.lease.checked_add(1).ok_or(RegisterError::LeaseExhausted)?;
        let retired = slot.queue.retire_before(None);
        let previous = slot.key;
        slot.key = Some(key);
        slot.lease = lease;
        slot.active = true;
        slot.minimum_period = 0;
        slot.last_sequence = None;
        slot.last_period = None;
        Ok(Registration { handle: SourceHandle { slot: index, lease, key },
            kind: if known.is_some() { RegistrationKind::NewGeneration } else { RegistrationKind::Created },
            previous, retired })
    }

    fn get(&self, handle: SourceHandle) -> Result<&Slot<T, DEPTH>, AccessError> {
        self.slots.get(handle.slot).filter(|slot| slot.matches(handle)).ok_or(AccessError::StaleHandle)
    }

    fn get_mut(&mut self, handle: SourceHandle) -> Result<&mut Slot<T, DEPTH>, AccessError> {
        self.slots.get_mut(handle.slot).filter(|slot| slot.matches(handle)).ok_or(AccessError::StaleHandle)
    }

    pub(crate) fn push(&mut self, handle: SourceHandle, block: PcmBlock<T>) -> Result<Accepted, RejectedBlock<T>> {
        let Ok(slot) = self.get_mut(handle) else { return Err(RejectedBlock { reason: PushReason::StaleHandle, block }); };
        let reason = if block.period < slot.minimum_period { Some(PushReason::Expired) }
            else if slot.last_sequence.is_some_and(|last| block.sequence <= last) { Some(PushReason::DuplicateOrOutOfOrder) }
            else if slot.last_period.is_some_and(|last| block.period < last) { Some(PushReason::PeriodMovedBackwards) }
            else if slot.queue.len == DEPTH { Some(PushReason::Full) }
            else { None };
        if let Some(reason) = reason { return Err(RejectedBlock { reason, block }); }
        let skipped_sequences = slot.last_sequence.map_or(0, |last| block.sequence - last - 1);
        slot.last_sequence = Some(block.sequence);
        slot.last_period = Some(block.period);
        slot.queue.push(block);
        Ok(Accepted { skipped_sequences })
    }

    pub(crate) fn peek(&self, handle: SourceHandle) -> Result<Option<&PcmBlock<T>>, AccessError> {
        Ok(self.get(handle)?.queue.front())
    }

    /// Move ownership at the caller's chosen commit point. This never replays a
    /// consumed block or resets the source's accepted-sequence watermark.
    pub(crate) fn pop(&mut self, handle: SourceHandle) -> Result<Option<PcmBlock<T>>, AccessError> {
        Ok(self.get_mut(handle)?.queue.pop())
    }

    pub(crate) fn queued(&self, handle: SourceHandle) -> Result<usize, AccessError> {
        Ok(self.get(handle)?.queue.len)
    }

    /// Set this source's expiration watermark and return every older queued
    /// block. The caller chooses a watermark from its actual mapped timeline;
    /// another source's progress cannot expire this queue accidentally.
    pub(crate) fn expire_before(&mut self, handle: SourceHandle, minimum_period: u64) -> Result<RetiredBlocks<T, DEPTH>, AccessError> {
        let slot = self.get_mut(handle)?;
        if minimum_period < slot.minimum_period { return Err(AccessError::WatermarkMovedBackwards); }
        slot.minimum_period = minimum_period;
        Ok(slot.queue.retire_before(Some(minimum_period)))
    }

    pub(crate) fn retire(&mut self, handle: SourceHandle) -> Result<RetiredBlocks<T, DEPTH>, AccessError> {
        let slot = self.get_mut(handle)?;
        slot.active = false;
        // Keep a tombstone until slot reuse so this stream cannot reopen its
        // retired generation. The lease also rejects old handles after reuse.
        Ok(slot.queue.retire_before(None))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(stream: u64, generation: u64) -> SourceKey { SourceKey { stream, generation } }
    fn block(sequence: u64, period: u64) -> PcmBlock<u64> {
        PcmBlock { sequence, period, received: Instant::now(), payload: sequence }
    }

    #[test]
    fn registration_is_bounded_idempotent_and_does_not_reset_existing_sources() {
        let mut sources = PcmSources::<u64, 2, 2>::new();
        let a = sources.register(key(10, 1)).unwrap();
        assert_eq!(a.kind, RegistrationKind::Created);
        sources.push(a.handle, block(4, 8)).unwrap();
        let again = sources.register(key(10, 1)).unwrap();
        assert_eq!(again.kind, RegistrationKind::Existing);
        assert_eq!(again.handle, a.handle);
        assert!(again.retired.is_empty());
        assert_eq!(sources.queued(a.handle), Ok(1));
        sources.register(key(20, 1)).unwrap();
        assert_eq!(sources.register(key(30, 1)).unwrap_err(), RegisterError::Capacity);
    }

    #[test]
    fn full_source_rejects_new_payload_without_advancing_sequence_or_other_queue() {
        let mut sources = PcmSources::<u64, 2, 2>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        let b = sources.register(key(2, 1)).unwrap().handle;
        sources.push(a, block(0, 0)).unwrap();
        sources.push(a, block(1, 1)).unwrap();
        let rejected = sources.push(a, block(2, 2)).unwrap_err();
        assert_eq!(rejected.reason, PushReason::Full);
        assert_eq!(rejected.block.payload, 2);
        sources.push(b, block(100, 30)).unwrap();
        assert_eq!(sources.pop(a).unwrap().unwrap().sequence, 0);
        sources.push(a, rejected.block).unwrap();
        assert_eq!(sources.pop(a).unwrap().unwrap().sequence, 1);
        assert_eq!(sources.pop(a).unwrap().unwrap().sequence, 2);
        assert_eq!(sources.pop(b).unwrap().unwrap().sequence, 100);
    }

    #[test]
    fn duplicates_and_backward_sequences_are_rejected_after_consumption() {
        let mut sources = PcmSources::<u64, 1, 2>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        sources.push(a, block(10, 10)).unwrap();
        sources.pop(a).unwrap();
        for seq in [10, 9, 0] {
            assert_eq!(sources.push(a, block(seq, 10)).unwrap_err().reason, PushReason::DuplicateOrOutOfOrder);
        }
        assert_eq!(sources.push(a, block(13, 13)).unwrap(), Accepted { skipped_sequences: 2 });
        assert_eq!(sources.pop(a).unwrap().unwrap().sequence, 13);
    }

    #[test]
    fn generation_change_returns_old_blocks_and_invalidates_old_handles_only() {
        let mut sources = PcmSources::<u64, 2, 3>::new();
        let a = sources.register(key(1, 4)).unwrap().handle;
        let b = sources.register(key(2, 7)).unwrap().handle;
        sources.push(a, block(90, 90)).unwrap();
        sources.push(a, block(91, 91)).unwrap();
        sources.push(b, block(50, 50)).unwrap();
        let changed = sources.register(key(1, 5)).unwrap();
        assert_eq!(changed.kind, RegistrationKind::NewGeneration);
        assert_eq!(changed.previous, Some(key(1, 4)));
        assert_eq!(changed.retired.len(), 2);
        assert_eq!(changed.retired.into_iter().map(|block| block.sequence).collect::<Vec<_>>(), [90, 91]);
        assert_eq!(sources.push(a, block(92, 92)).unwrap_err().reason, PushReason::StaleHandle);
        sources.push(changed.handle, block(0, 0)).unwrap();
        assert_eq!(sources.pop(changed.handle).unwrap().unwrap().sequence, 0);
        assert_eq!(sources.pop(b).unwrap().unwrap().sequence, 50);
        assert_eq!(sources.register(key(1, 4)).unwrap_err(), RegisterError::StaleGeneration);
    }

    #[test]
    fn retirement_and_slot_reuse_reject_stale_handles_including_aba() {
        let mut sources = PcmSources::<u64, 1, 2>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        sources.push(a, block(1, 1)).unwrap();
        assert_eq!(sources.retire(a).unwrap().len(), 1);
        assert_eq!(sources.queued(a), Err(AccessError::StaleHandle));
        assert_eq!(sources.register(key(1, 1)).unwrap_err(), RegisterError::StaleGeneration);
        let b = sources.register(key(2, 1)).unwrap().handle;
        assert_eq!(a.slot(), b.slot());
        sources.retire(b).unwrap();
        // Control may reuse an identity after its tombstone was replaced, but
        // an old producer's handle still cannot submit to the new lease.
        let reopened = sources.register(key(1, 1)).unwrap().handle;
        assert_eq!(reopened.key(), a.key());
        assert_ne!(reopened, a);
        assert_eq!(sources.push(a, block(2, 2)).unwrap_err().reason, PushReason::StaleHandle);
        sources.push(reopened, block(0, 0)).unwrap();
    }

    #[test]
    fn expiration_is_monotonic_and_local_to_one_source() {
        let mut sources = PcmSources::<u64, 2, 4>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        let b = sources.register(key(2, 1)).unwrap().handle;
        for period in 0..4 { sources.push(a, block(period, period)).unwrap(); }
        sources.push(b, block(0, 0)).unwrap();
        let retired = sources.expire_before(a, 2).unwrap();
        assert_eq!(retired.into_iter().map(|block| block.period).collect::<Vec<_>>(), [0, 1]);
        assert_eq!(sources.peek(a).unwrap().unwrap().period, 2);
        assert_eq!(sources.peek(b).unwrap().unwrap().period, 0);
        assert_eq!(sources.push(a, block(4, 1)).unwrap_err().reason, PushReason::Expired);
        assert_eq!(sources.expire_before(a, 1).unwrap_err(), AccessError::WatermarkMovedBackwards);
    }

    #[test]
    fn source_period_regression_requires_a_new_generation() {
        let mut sources = PcmSources::<u64, 1, 3>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        sources.push(a, block(10, 20)).unwrap();
        assert_eq!(sources.push(a, block(11, 19)).unwrap_err().reason, PushReason::PeriodMovedBackwards);
        sources.push(a, block(11, 20)).unwrap();
        let reset = sources.register(key(1, 2)).unwrap();
        sources.push(reset.handle, block(0, 0)).unwrap();
    }

    #[test]
    fn wrapped_ring_retirement_returns_fifo_order() {
        let mut sources = PcmSources::<u64, 1, 3>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        for seq in 0..3 { sources.push(a, block(seq, seq)).unwrap(); }
        assert_eq!(sources.pop(a).unwrap().unwrap().sequence, 0);
        assert_eq!(sources.pop(a).unwrap().unwrap().sequence, 1);
        sources.push(a, block(3, 3)).unwrap();
        sources.push(a, block(4, 4)).unwrap();
        assert_eq!(sources.retire(a).unwrap().into_iter().map(|block| block.sequence).collect::<Vec<_>>(), [2, 3, 4]);
    }

    #[test]
    fn maximum_sequence_does_not_wrap_into_a_new_source() {
        let mut sources = PcmSources::<u64, 1, 2>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        sources.push(a, block(u64::MAX - 1, 0)).unwrap();
        sources.push(a, block(u64::MAX, 1)).unwrap();
        sources.pop(a).unwrap();
        assert_eq!(sources.push(a, block(0, 2)).unwrap_err().reason, PushReason::DuplicateOrOutOfOrder);
        assert_eq!(sources.pop(a).unwrap().unwrap().sequence, u64::MAX);
    }

    #[test]
    fn received_time_and_payload_survive_peek_and_ownership_commit() {
        let mut sources = PcmSources::<u64, 1, 2>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        let received = Instant::now();
        sources.push(a, PcmBlock { sequence: 5, period: 3, received, payload: 987 }).unwrap();
        assert_eq!(sources.peek(a).unwrap().unwrap().received, received);
        let committed = sources.pop(a).unwrap().unwrap();
        assert_eq!((committed.sequence, committed.period, committed.payload), (5, 3, 987));
        assert_eq!(committed.received, received);
        assert!(sources.pop(a).unwrap().is_none());
        assert_eq!(sources.queued(a), Ok(0));
    }

    #[test]
    fn rejected_and_retired_payloads_remain_owned_by_the_caller() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        #[derive(Debug)]
        struct Payload<'a>(&'a AtomicUsize);
        impl Drop for Payload<'_> { fn drop(&mut self) { self.0.fetch_add(1, Ordering::Relaxed); } }
        let drops = AtomicUsize::new(0);
        let mut sources = PcmSources::<Payload<'_>, 1, 1>::new();
        let a = sources.register(key(1, 1)).unwrap().handle;
        let owned = |sequence| PcmBlock { sequence, period: sequence, received: Instant::now(), payload: Payload(&drops) };
        sources.push(a, owned(0)).unwrap();
        let rejected = sources.push(a, owned(1)).unwrap_err();
        let generation = sources.register(key(1, 2)).unwrap();
        assert_eq!(drops.load(Ordering::Relaxed), 0);
        drop(rejected.block);
        assert_eq!(drops.load(Ordering::Relaxed), 1);
        drop(generation.retired);
        assert_eq!(drops.load(Ordering::Relaxed), 2);
    }
}
