//! Bounded raw output tap. No metering, JSON, network or consumer thread here.
use std::{cell::UnsafeCell, sync::{Arc, Mutex, OnceLock, atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering}}};
const CAP: usize = 16;
const SAMPLES: usize = 12 * 480;
pub(crate) const WIRE_SIZE: usize = 32 + SAMPLES * 4;
pub(crate) const OBJECT_SIZE: usize = 32 + 160 * (48 + 480 * 4);
struct Block { epoch: u64, len: usize, wire: Box<[u8]> }
struct Slot(UnsafeCell<Block>);
// Only Producer mutates FREE slots. Control serializes its single reader.
// Release/acquire cursors publish READY blocks and return READING slots to FREE.
unsafe impl Sync for Slot {}
struct Ring { slots: Box<[Slot]>, write: AtomicUsize, read: AtomicUsize }
impl Ring {
    fn new(bytes: usize) -> Self {
        Self { slots: (0..CAP).map(|_| Slot(UnsafeCell::new(Block { epoch: 0, len: 0, wire: vec![0; bytes].into_boxed_slice() }))).collect::<Vec<_>>().into_boxed_slice(), write: AtomicUsize::new(0), read: AtomicUsize::new(0) }
    }
}
pub(crate) struct Control {
    bytes: usize, supported: bool, state: AtomicU64, stopped: AtomicBool, ring: OnceLock<Ring>,
    reader: Mutex<usize>, published: AtomicU64, dropped: AtomicU64, consumed: AtomicU64,
}
pub(crate) struct Producer { control: Arc<Control>, cursor: usize }
impl Control {
    pub(crate) fn new(supported: bool) -> Arc<Self> {
        Self::sized(supported, WIRE_SIZE)
    }
    pub(crate) fn objects(supported: bool) -> Arc<Self> { Self::sized(supported, OBJECT_SIZE) }
    fn sized(supported: bool, bytes: usize) -> Arc<Self> {
        Arc::new(Self { bytes, supported, state: AtomicU64::new(0), stopped: AtomicBool::new(false), ring: OnceLock::new(), reader: Mutex::new(0), published: AtomicU64::new(0), dropped: AtomicU64::new(0), consumed: AtomicU64::new(0) })
    }
    pub(crate) fn producer(self: &Arc<Self>) -> Producer { Producer { control: self.clone(), cursor: 0 } }
    /// Control caller only. Allocate/touch the pool before enabling publication.
    pub(crate) fn enable(&self, enabled: bool) -> bool {
        if enabled && (!self.supported || self.stopped.load(Ordering::Acquire)) { return false; }
        if enabled { self.ring.get_or_init(|| Ring::new(self.bytes)); }
        self.state.fetch_update(Ordering::AcqRel, Ordering::Acquire, |old| {
            if (old & 1 != 0) == enabled { Some(old) } else { old.checked_add(1) }
        }).is_ok()
    }
    pub(crate) fn json(&self) -> String {
        let status = if !self.supported { "unsupported" } else if self.stopped.load(Ordering::Acquire) { "stopped" }
            else if self.state.load(Ordering::Acquire) & 1 == 0 { "disabled" } else { "enabled" };
        format!("{{\"schemaVersion\":\"nsp-output-tap/0.2\",\"status\":\"{status}\",\"poolAllocated\":{},\"capacity\":16,\"blockBytes\":{},\"publishedBlocks\":{},\"poolDroppedBlocks\":{},\"consumedBlocks\":{}}}", self.ring.get().is_some(), self.bytes, self.published.load(Ordering::Acquire), self.dropped.load(Ordering::Acquire), self.consumed.load(Ordering::Acquire))
    }
    /// Copy out at most one block on the non-RT bridge caller. An undersized
    /// buffer does not consume. Old epochs are discarded in at most 16 steps.
    pub(crate) fn read(&self, out: &mut [u8]) -> usize {
        let Some(ring) = self.ring.get() else { return 0; };
        let epoch = self.state.load(Ordering::Acquire);
        if epoch & 1 == 0 { return 0; }
        let mut cursor = self.reader.lock().unwrap_or_else(|p| p.into_inner());
        for _ in 0..CAP {
            if *cursor == ring.write.load(Ordering::Acquire) { return 0; }
            // SAFETY: acquire grants this reader the slot until release below.
            let block = unsafe { &*ring.slots[*cursor % CAP].0.get() };
            let current = block.epoch == epoch;
            if current && out.len() < block.len { return block.len; }
            let length = block.len;
            if current {
                out[..length].copy_from_slice(&block.wire[..length]);
                self.consumed.fetch_add(1, Ordering::Relaxed);
            }
            *cursor = cursor.wrapping_add(1);
            ring.read.store(*cursor, Ordering::Release);
            if current { return length; }
        }
        0
    }
}
impl Producer {
    pub(crate) fn active(&self) -> bool { self.control.state.load(Ordering::Acquire) & 1 != 0 }
    pub(crate) fn publish(&mut self, sequence: u64, pcm: &[f32]) -> bool {
        if pcm.len() != SAMPLES { return false; }
        self.publish_custom(|epoch, wire| {
            header(wire, b"NSPT", 12, epoch, sequence);
            let bytes = unsafe { std::slice::from_raw_parts(pcm.as_ptr() as *const u8, SAMPLES * 4) };
            wire[32..WIRE_SIZE].copy_from_slice(bytes); WIRE_SIZE
        })
    }
    /// Preserve the planar wire contract without a planar audio workspace.
    pub(crate) fn publish_interleaved(&mut self, sequence: u64, pcm: &[f32]) -> bool {
        if pcm.len() != SAMPLES { return false; }
        self.publish_custom(|epoch, wire| {
            header(wire, b"NSPT", 12, epoch, sequence);
            for channel in 0..12 {
                for frame in 0..480 {
                    let at = 32 + (channel*480+frame)*4;
                    wire[at..at+4].copy_from_slice(&pcm[frame*12+channel].to_le_bytes());
                }
            }
            WIRE_SIZE
        })
    }
    pub(crate) fn publish_custom(&mut self, fill: impl FnOnce(u64, &mut [u8]) -> usize) -> bool {
        let epoch = self.control.state.load(Ordering::Acquire);
        if epoch & 1 == 0 { return false; }
        let Some(ring) = self.control.ring.get() else { return false; };
        if self.cursor.wrapping_sub(ring.read.load(Ordering::Acquire)) == CAP {
            self.control.dropped.fetch_add(1, Ordering::Relaxed); return false;
        }
        // SAFETY: sole producer owns this FREE slot; reader returns it by release.
        let block = unsafe { &mut *ring.slots[self.cursor % CAP].0.get() };
        block.epoch = epoch; block.len = fill(epoch, &mut block.wire);
        self.cursor = self.cursor.wrapping_add(1);
        ring.write.store(self.cursor, Ordering::Release);
        self.control.published.fetch_add(1, Ordering::Relaxed);
        true
    }
}
pub(crate) fn header(out: &mut [u8], magic: &[u8;4], count: u16, epoch: u64, sequence: u64) {
    out[..4].copy_from_slice(magic); out[4..6].copy_from_slice(&1u16.to_le_bytes());
    out[6..8].copy_from_slice(&count.to_le_bytes()); out[8..12].copy_from_slice(&480u32.to_le_bytes());
    out[12..16].copy_from_slice(&48000u32.to_le_bytes()); out[16..24].copy_from_slice(&epoch.to_le_bytes());
    out[24..32].copy_from_slice(&sequence.to_le_bytes());
}
impl Drop for Producer { fn drop(&mut self) { self.control.stopped.store(true, Ordering::Release); } }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_off_allocates_nothing_and_full_pool_drops() {
        let c = Control::new(true); let mut p = c.producer();
        assert!(!p.publish(0, &[0.; SAMPLES])); assert!(c.ring.get().is_none());
        assert!(c.enable(true)); assert!(c.enable(true));
        for i in 0..16 { assert!(p.publish(i, &[0.25; SAMPLES])); }
        assert!(!p.publish(16, &[0.; SAMPLES]));
        assert_eq!(c.read(&mut []), WIRE_SIZE); // size query cannot consume
        let mut out = vec![0; WIRE_SIZE]; assert_eq!(c.read(&mut out), WIRE_SIZE);
        assert_eq!(&out[..4], b"NSPT"); assert_eq!(u64::from_le_bytes(out[24..32].try_into().unwrap()), 0);
        assert!(p.publish(17, &[0.5; SAMPLES]));
        assert_eq!(c.dropped.load(Ordering::Relaxed), 1);
    }
    #[test]
    fn disabled_epochs_do_not_leak_and_stopped_can_be_drained() {
        let c = Control::new(true); let mut p = c.producer(); c.enable(true);
        p.publish(0, &[0.; SAMPLES]); c.enable(false); c.enable(true);
        p.publish(1, &[0.5; SAMPLES]); drop(p);
        let mut out = vec![0; WIRE_SIZE]; assert_eq!(c.read(&mut out), WIRE_SIZE);
        assert_eq!(u64::from_le_bytes(out[16..24].try_into().unwrap()), 3);
        assert_eq!(u64::from_le_bytes(out[24..32].try_into().unwrap()), 1);
        assert_eq!(c.read(&mut out), 0); assert!(!c.enable(true));
        assert!(!Control::new(false).enable(true));
    }
    #[test]
    fn concurrent_handoff_preserves_all_samples() {
        let c = Control::new(true); c.enable(true); let mut p = c.producer(); let reader = c.clone();
        let thread = std::thread::spawn(move || {
            let mut out = vec![0; WIRE_SIZE];
            for seq in 0..20000u64 {
                while reader.read(&mut out) == 0 { std::thread::yield_now(); }
                assert_eq!(u64::from_le_bytes(out[24..32].try_into().unwrap()), seq);
                assert!(out[32..].chunks_exact(4).all(|b| f32::from_le_bytes(b.try_into().unwrap()) == seq as f32));
            }
        });
        for seq in 0..20000 { while !p.publish(seq, &[seq as f32; SAMPLES]) { std::thread::yield_now(); } }
        drop(p); thread.join().unwrap();
    }
}
