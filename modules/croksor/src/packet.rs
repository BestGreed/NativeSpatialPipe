//! Owned native packet consumption; the caller remains responsible for stream routing.
use aom_roar::*;
use crate::raw_tap as telemetry;
use crate::capture_input::{CaptureInput, Property, MAX_OBJECTS};
use crate::olr_input::{State, coordinates};
use std::{
    ffi::{CStr, c_char, c_void},
    panic::{AssertUnwindSafe, catch_unwind},
    slice,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicU64, AtomicUsize, Ordering},
        mpsc::{self, Receiver, SyncSender, TrySendError},
    },
    thread::{self, JoinHandle},
    time::Instant,
};

use crate::compact_storage::{CompactStorage, LaneKind, ObjectKey, PreparedLane};
const COMPACT_OBJECTS: usize = crate::compact_storage::OBJECTS;
const COMPACT_SLOTS: usize = COMPACT_OBJECTS + 12 * crate::unified_input::LANES;

fn compact_lane(lane: usize) -> (LaneKind, usize) {
    if lane < crate::unified_input::PCM {
        (LaneKind::Bed, lane)
    } else {
        (LaneKind::Pcm, lane - crate::unified_input::PCM)
    }
}

struct Expansion {
    requested: AtomicUsize,
    ready: Arc<AtomicUsize>,
    skipped: AtomicU64,
    object_budget_drops: Arc<AtomicU64>,
    request: Option<SyncSender<usize>>,
    worker: Option<JoinHandle<()>>,
}

impl Expansion {
    fn new(frames: usize) -> (Self, Receiver<PreparedLane>) {
        let initial = (1 << crate::compact_storage::INITIAL_LANES) - 1;
        let ready = Arc::new(AtomicUsize::new(initial | initial << crate::unified_input::PCM));
        let published = ready.clone();
        let (request, requests) = mpsc::sync_channel(crate::unified_input::LANES);
        let (prepared, prepared_rx) = mpsc::sync_channel(crate::unified_input::LANES);
        let worker = thread::spawn(move || {
            while let Ok(lane) = requests.recv() {
                let (kind, index) = compact_lane(lane);
                let buffer = PreparedLane::new(kind, index, frames).unwrap();
                if prepared.send(buffer).is_err() {
                    break;
                }
                published.fetch_or(1 << lane, Ordering::Release);
            }
        });
        (Self {
            requested: AtomicUsize::new(0),
            ready,
            skipped: AtomicU64::new(0),
            object_budget_drops: Arc::new(AtomicU64::new(0)),
            request: Some(request),
            worker: Some(worker),
        }, prepared_rx)
    }

    fn admits(&self, lane: usize) -> bool {
        let bit = 1 << lane;
        if self.ready.load(Ordering::Acquire) & bit != 0 {
            return true;
        }
        if self.requested.fetch_or(bit, Ordering::Relaxed) & bit == 0 {
            if self.request.as_ref().unwrap().try_send(lane).is_err() {
                self.requested.fetch_and(!bit, Ordering::Relaxed);
            }
        }
        self.skipped.fetch_add(1, Ordering::Relaxed);
        false
    }
}

impl Drop for Expansion {
    fn drop(&mut self) {
        self.request.take();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

const SLOTS: usize = MAX_OBJECTS;
const PCM_CHANNELS: usize = 12;
const SPATIAL_SLOTS: usize = SLOTS * crate::unified_input::PCM;
const RENDER_SLOTS: usize = SPATIAL_SLOTS + PCM_CHANNELS * crate::unified_input::PCM_LANES;

fn source_slot(lane: usize, local_slot: usize) -> usize {
    if lane < crate::unified_input::PCM {
        lane * SLOTS + local_slot
    } else {
        SPATIAL_SLOTS + (lane - crate::unified_input::PCM) * PCM_CHANNELS + local_slot
    }
}

fn slot_source(slot: usize) -> (usize, usize) {
    if slot < SPATIAL_SLOTS {
        (slot / SLOTS, slot % SLOTS)
    } else {
        let pcm_slot = slot - SPATIAL_SLOTS;
        (crate::unified_input::PCM + pcm_slot / PCM_CHANNELS, pcm_slot % PCM_CHANNELS)
    }
}
#[derive(Clone, Copy, Default)]
struct TapSlot {
    source: Option<usize>,
    source_generation: u64,
    generation: u64,
}

struct UnifiedTapIds {
    slots: [TapSlot; SLOTS],
    lookup: Vec<Option<usize>>,
}

impl UnifiedTapIds {
    fn assign(&mut self, occupied: &[usize], states: &[State]) {
        let mut present = [false; RENDER_SLOTS];
        for &source in occupied { present[source] = true; }
        for entry in &mut self.slots {
            if let Some(source) = entry.source.filter(|&source| !present[source]) {
                self.lookup[source] = None;
                entry.source = None;
            }
        }
        let mut free = [0; SLOTS];
        let mut free_count = 0;
        for (i, entry) in self.slots.iter().enumerate().rev() {
            if entry.source.is_none() {
                free[free_count] = i;
                free_count += 1;
            }
        }
        for &source in occupied {
            let slot = match self.lookup[source] {
                Some(slot) => slot,
                None => {
                    free_count -= 1;
                    let slot = free[free_count];
                    self.lookup[source] = Some(slot);
                    slot
                }
            };
            let entry = &mut self.slots[slot];
            if entry.source != Some(source) || entry.source_generation != states[source].generation {
                entry.generation += 1;
                entry.source = Some(source);
                entry.source_generation = states[source].generation;
            }
        }
    }
}
#[link(name = "avrt")]
unsafe extern "system" {
    fn AvSetMmThreadCharacteristicsW(name: *const u16, index: *mut u32) -> *mut c_void;
    fn AvSetMmThreadPriority(handle: *mut c_void, priority: i32) -> i32;
    fn AvRevertMmThreadCharacteristics(handle: *mut c_void) -> i32;
}
struct RenderTask { handle: *mut c_void }
impl Drop for RenderTask {
    fn drop(&mut self) { if !self.handle.is_null() { unsafe { AvRevertMmThreadCharacteristics(self.handle); } } }
}
#[link(name = "kernel32")]
unsafe extern "system" {
    fn QueryPerformanceCounter(value: *mut i64) -> i32;
    fn QueryPerformanceFrequency(value: *mut i64) -> i32;
    fn GetCurrentThreadId() -> u32;
    fn GetModuleHandleExW(flags: u32, address: *const c_void, module: *mut *mut c_void) -> i32;
    fn GetProcAddress(module: *mut c_void, name: *const c_char) -> *mut c_void;
}
type TraceMark = unsafe extern "C" fn(*mut c_void, u32, u64, u64, u64);
#[repr(u32)]
pub(crate) enum TraceEvent {
    PcmSubmit = 1, RenderBegin = 2, RenderEnd = 3, WriterBegin = 4, DspEnd = 5,
    IngressDrop = 9, RenderTick = 10, Scheduling = 11,
}
#[derive(Clone, Copy)]
pub(crate) struct PcmTrace { callback: TraceMark, context: usize }
impl PcmTrace {
    fn from_sink(publish: crate::output_pool::Publish, context: *mut c_void) -> Option<Self> {
        if context.is_null() { return None; }
        let mut module = std::ptr::null_mut();
        // FROM_ADDRESS | UNCHANGED_REFCOUNT. The host keeps the sink DLL loaded
        // until both pipeline workers have joined, as with the publish pointer.
        if unsafe { GetModuleHandleExW(0x6, publish as *const c_void, &mut module) } == 0 { return None; }
        let mark = unsafe { GetProcAddress(module, c"nsp_wasapi_trace_mark".as_ptr()) };
        if mark.is_null() { return None; }
        let callback = unsafe { std::mem::transmute::<*mut c_void, TraceMark>(mark) };
        Some(Self { callback, context: context as usize })
    }
    pub(crate) fn mark(self, event: TraceEvent, sequence: u64, a: u64, b: u64) {
        unsafe { (self.callback)(self.context as *mut c_void, event as u32, sequence, a, b) }
    }
}
fn qpc_ns() -> u64 {
    static HZ: OnceLock<u64> = OnceLock::new();
    let hz = *HZ.get_or_init(|| { let mut n = 0; assert_ne!(unsafe { QueryPerformanceFrequency(&mut n) }, 0); assert!(n > 0); n as u64 });
    let mut n = 0;
    assert_ne!(unsafe { QueryPerformanceCounter(&mut n) }, 0);
    ((n as u128 * 1_000_000_000) / hz as u128) as u64
}
const BED_ID: u32 = 10000;
// ROAR Layout714: FL, FR, C, LFE, SL, SR, BL, BR, TFL, TFR, TBL, TBR.
const BED_TYPES: [u32; 12] = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096];
#[derive(Default)]
struct Mapping {
    packets: u64,
    frames: u64,
    creates: u64,
    retires: u64,
    updates: u64,
    unknown: u64,
    max_dynamic: usize,
    max_static: usize,
}
/// ROAR's OLR mode and its dedicated adapter; not a generic ROAR engine.
pub struct Engine {
    layout: crate::layouts::Preset,
    rdr: RoarRenderer,
    group: GroupId,
    frames: Samples,
    n: usize,
    grouped: bool,
    apply_gain: bool,
    scaled: bool,
    reference: f64,
    states: Vec<State>,
    compact: Option<(CompactStorage, Receiver<PreparedLane>, Arc<AtomicU64>)>,
    source_leases: [u64; crate::unified_input::LANES],
    active: Vec<u64>,
    last: Option<u64>,
    bed_active: bool,
    signature: Option<Vec<(usize, u64)>>,
    pcm: Vec<f32>,
    bed: Vec<f32>,
    output: Vec<f32>,
    positions: ObjectPosition,
    one_position: ObjectPosition,
    mapping: Mapping,
    decode_ns: u64,
    metadata_ns: u64,
    dsp_ns: u64,
    unified_tap_ids: Option<Box<UnifiedTapIds>>,
    object_tap_truncated_records: u64,
    object_tap_truncated_blocks: u64,
}
fn err<T>(message: &str) -> Result<T, String> {
    Err(message.to_string())
}
fn oar<T>(result: Result<T, OarError>) -> Result<T, String> {
    result.map_err(|e| e.to_string())
}
impl Engine {
    fn new(
        n: u32,
        rate: u32,
        grouped: bool,
        apply_gain: bool,
        scaled: bool,
        reference: f64,
    ) -> Result<Self, String> {
        Self::new_layout(n,rate,grouped,apply_gain,scaled,reference,714)
    }
    fn new_layout(n:u32,rate:u32,grouped:bool,apply_gain:bool,scaled:bool,reference:f64,code:u32)->Result<Self,String>{
        Self::new_layout_with_storage(n, rate, grouped, apply_gain, scaled, reference, code, true)
    }
    fn new_layout_with_storage(
        n: u32, rate: u32, grouped: bool, apply_gain: bool, scaled: bool,
        reference: f64, code: u32, allocate_legacy_storage: bool,
    ) -> Result<Self, String> {
        let slots = if allocate_legacy_storage { SLOTS } else { COMPACT_SLOTS };
        let layout=crate::layouts::get(code)?;
        if !(1..=4096).contains(&n) || rate != 48000 || !reference.is_finite() || reference <= 0. {
            return err("Invalid native packet configuration");
        }
        let frames = oar(Samples::new(n))?;
        let mut rdr = oar(RoarRenderer::create(&oar(Config::new(
            layout.roar,
            frames,
            oar(SampleRate::new(rate))?,
        ))?))?;
        // Native capture carries one effective position per object per block,
        // without sample-offset position events. Keep ROAR's whole-block route
        // and avoid allocating sub-frame output scratch for every element.
        oar(rdr.set_object_position_metadata_unit_to_process(frames))?;
        // A receiver route binds this pipeline to one capture block. Prepare
        // once before capture; all object elements in that block share it.
        oar(rdr.prepare_olr_geometry())?;
        let group = oar(rdr.add_audio_group())?;
        Ok(Self {
            layout,
            rdr,
            group,
            frames,
            n: n as usize,
            grouped,
            apply_gain,
            scaled,
            reference,
            states: vec![State::default(); slots],
            compact: None,
            source_leases: [0; crate::unified_input::LANES],
            active: vec![0; slots],
            last: None,
            bed_active: false,
            signature: None,
            pcm: if allocate_legacy_storage { vec![0.; SLOTS * n as usize] } else { Vec::new() },
            bed: if allocate_legacy_storage { vec![0.; 12 * n as usize] } else { Vec::new() },
            output: vec![0.; layout.labels.len() * n as usize],
            positions: ObjectPosition::Cartesian(Vec::with_capacity(if allocate_legacy_storage { SLOTS } else { COMPACT_OBJECTS })),
            one_position: ObjectPosition::Cartesian(Vec::with_capacity(1)),
            mapping: Mapping::default(),
            decode_ns: 0,
            metadata_ns: 0,
            dsp_ns: 0,
            unified_tap_ids: None,
            object_tap_truncated_records: 0,
            object_tap_truncated_blocks: 0,
        })
    }
    fn process(&mut self, seq: u64, count: usize, data: &[u8]) -> Result<&[f32], String> {
        self.process_tapped(seq, count, data, None)
    }
    fn process_tapped(&mut self, seq: u64, count: usize, data: &[u8], object_tap: Option<&mut telemetry::Producer>) -> Result<&[f32], String> {
        self.process_sources(seq, &[(0, count, data, 0)], object_tap, false, None)
            .map(|pcm| &*pcm)
    }

    fn enable_unified(&mut self) {
        self.states.resize(RENDER_SLOTS, State::default());
        self.active.resize(RENDER_SLOTS, 0);
        self.pcm.resize(RENDER_SLOTS * self.n, 0.);
        self.unified_tap_ids = Some(Box::new(UnifiedTapIds {
            slots: [TapSlot::default(); SLOTS],
            lookup: vec![None; RENDER_SLOTS],
        }));
        if let ObjectPosition::Cartesian(positions) = &mut self.positions {
            positions.reserve(RENDER_SLOTS);
        }
    }

    fn enable_compact(&mut self, prepared: Receiver<PreparedLane>, object_budget_drops: Arc<AtomicU64>) -> Result<(), String> {
        // Prepare each independent renderer before the render worker starts.
        // Future source generations reset these instances in place.
        for slot in 0..COMPACT_OBJECTS {
            oar(self.rdr.add_element(self.group, slot as u32 + 1,
                &AudioElementConfig::ObjectBased(oar(ObjectBasedConfig::new(1, None))?)))?;
        }
        oar(self.rdr.add_element(self.group, BED_ID,
            &AudioElementConfig::ChannelBased(ChannelBasedConfig {
                layout: InputLayout::Layout714, downmix_info: None, rendering_config: None,
            })))?;
        self.bed_active = true;
        self.compact = Some((CompactStorage::new(self.n), prepared, object_budget_drops));
        self.unified_tap_ids = Some(Box::new(UnifiedTapIds {
            slots: [TapSlot::default(); SLOTS],
            lookup: vec![None; COMPACT_SLOTS],
        }));
        Ok(())
    }

    fn block<'a>(compact: Option<&'a CompactStorage>, pcm: &'a [f32], n: usize, slot: usize) -> &'a [f32] {
        if let Some(storage) = compact {
            if slot < COMPACT_OBJECTS {
                return storage.dynamic_pcm(slot);
            }
            let (lane, channel) = ((slot - COMPACT_OBJECTS) / 12, (slot - COMPACT_OBJECTS) % 12);
            let (kind, index) = compact_lane(lane);
            return storage.lane(kind, index).map_or(&[], |samples| {
                &samples[channel * n..(channel + 1) * n]
            });
        }
        &pcm[slot * n..(slot + 1) * n]
    }

    fn process_sources(
        &mut self,
        seq: u64,
        sources: &[(usize, usize, &[u8], u64)],
        object_tap: Option<&mut telemetry::Producer>,
        unified: bool,
        upmixed_bed: Option<&[f32]>,
    ) -> Result<&mut [f32], String> {
        if self.compact.is_some() {
            self.process_source_block::<COMPACT_SLOTS, { COMPACT_OBJECTS + 1 }>(seq, sources, object_tap, unified, upmixed_bed)
        } else {
            self.process_source_block::<RENDER_SLOTS, { RENDER_SLOTS + 1 }>(seq, sources, object_tap, unified, upmixed_bed)
        }
    }

    fn process_source_block<const SLOT_CAPACITY: usize, const RENDER_INPUTS: usize>(
        &mut self,
        seq: u64,
        sources: &[(usize, usize, &[u8], u64)],
        object_tap: Option<&mut telemetry::Producer>,
        unified: bool,
        upmixed_bed: Option<&[f32]>,
    ) -> Result<&mut [f32], String> {
        let begin = Instant::now();
        if self.last.is_some_and(|last| last.checked_add(1) != Some(seq)) {
            return err("Sequence discontinuity: rebuild native pipeline");
        }
        let mut compact_source = [[0u8; SLOTS]; crate::unified_input::PCM];
        if let Some((storage, prepared, budget_drops)) = &mut self.compact {
            for buffer in prepared.try_iter() {
                storage.install_lane(buffer).map_err(|_| "Invalid prepared slot")?;
            }
            const EMPTY: ObjectKey = ObjectKey { lane: 0, source_slot: 0, generation: 0 };
            let mut keys = [EMPTY; COMPACT_OBJECTS];
            let mut newcomers = [EMPTY; COMPACT_OBJECTS];
            let mut surviving = 0;
            let mut new_count = 0;
            let mut seen_lanes = 0u32;
            let mut assignments = [0; COMPACT_OBJECTS];
            for &(lane, objects, data, source_lease) in sources {
                if lane >= crate::unified_input::PCM || source_lease < self.source_leases[lane] {
                    continue;
                }
                seen_lanes |= 1 << lane;
                let input = CaptureInput::new(data, objects, self.n).map_err(str::to_string)?;
                for local_slot in 0..objects {
                    let prop = input.property(local_slot).map_err(str::to_string)?;
                    if prop.typ != 1 { continue; }
                    let key = ObjectKey { lane, source_slot: local_slot, generation: source_lease };
                    if storage.slot(key).is_some() {
                        keys[surviving] = key;
                        surviving += 1;
                    } else {
                        if new_count < COMPACT_OBJECTS {
                            newcomers[new_count] = key;
                        }
                        new_count += 1;
                    }
                }
            }
            let accepted = new_count.min(COMPACT_OBJECTS - surviving);
            if accepted != new_count {
                budget_drops.fetch_add((new_count - accepted) as u64, Ordering::Relaxed);
            }
            keys[surviving..surviving + accepted].copy_from_slice(&newcomers[..accepted]);
            let supplied = surviving + accepted;
            let mut count = supplied;
            let previous: [_; COMPACT_OBJECTS] = std::array::from_fn(|slot| storage.object(slot));
            // Preserve metadata for a lane missing this block when space permits.
            for key in previous.iter().flatten() {
                let slot = storage.slot(*key).unwrap();
                if seen_lanes & (1 << key.lane) == 0 && self.states[slot].present && count < COMPACT_OBJECTS {
                    keys[count] = *key;
                    count += 1;
                }
            }
            storage.reconcile_objects(&keys[..count], &mut assignments).map_err(|e| format!("{e:?}"))?;
            for (key, slot) in keys[..supplied].iter().zip(assignments) {
                compact_source[key.lane][key.source_slot] = (slot + 1) as u8;
            }
            for (slot, old) in previous.into_iter().enumerate() {
                if storage.object(slot) != old {
                    let state = &mut self.states[slot];
                    self.mapping.retires += u64::from(state.present);
                    state.present = false;
                    state.position = None;
                }
            }
        }
        let mut props = [Property::default(); SLOT_CAPACITY];
        let mut occupied = [0usize; SLOT_CAPACITY];
        let mut lane_counts = [None; crate::unified_input::LANES];
        let mut count = 0;
        for &(lane, objects, data, source_lease) in sources {
            if source_lease < self.source_leases[lane] {
                continue;
            }
            if source_lease > self.source_leases[lane] {
                let (start, slots) = if self.compact.is_some() {
                    (COMPACT_OBJECTS + 12 * lane, 12)
                } else {
                    (source_slot(lane, 0), if lane < crate::unified_input::PCM { SLOTS } else { PCM_CHANNELS })
                };
                for state in &mut self.states[start..start + slots] {
                    self.mapping.retires += u64::from(state.present);
                    state.present = false;
                    state.position = None;
                }
                self.source_leases[lane] = source_lease;
            }
            let input = CaptureInput::new(data, objects, self.n).map_err(str::to_string)?;
            if lane >= crate::unified_input::PCM && objects > PCM_CHANNELS {
                return err("PCM input exceeds its 12-channel budget");
            }
            lane_counts[lane] = Some(objects);
            for local_slot in 0..objects {
                let prop = input.property(local_slot).map_err(str::to_string)?;
                if prop.typ != 1 && !BED_TYPES.contains(&prop.typ) {
                    return err("Unsupported static type");
                }
                let slot = if self.compact.is_some() {
                    if prop.typ == 1 {
                        if lane >= crate::unified_input::PCM { continue; }
                        let Some(slot) = compact_source[lane][local_slot].checked_sub(1) else { continue; };
                        usize::from(slot)
                    } else {
                        let channel = BED_TYPES.iter().position(|&t| t == prop.typ).unwrap();
                        COMPACT_OBJECTS + 12 * lane + channel
                    }
                } else {
                    source_slot(lane, local_slot)
                };
                if props[slot].typ != 0 {
                    return err("Duplicate input slot");
                }
                props[slot] = prop;
                occupied[count] = slot;
                count += 1;
                let dest = if let Some((storage, _, _)) = &mut self.compact {
                    if slot < COMPACT_OBJECTS {
                        storage.dynamic_pcm_mut(slot)
                    } else {
                        let channel = (slot - COMPACT_OBJECTS) % 12;
                        let (kind, index) = compact_lane(lane);
                        let buffer = storage.lane_mut(kind, index).ok_or("Compact lane is not prepared")?;
                        &mut buffer[channel * self.n..(channel + 1) * self.n]
                    }
                } else {
                    &mut self.pcm[slot * self.n..(slot + 1) * self.n]
                };
                for (dst, bytes) in dest.iter_mut().zip(input.pcm_bytes(local_slot).chunks_exact(4))
                {
                    let sample = f32::from_le_bytes(bytes.try_into().unwrap());
                    *dst = if self.apply_gain { sample * prop.gain } else { sample };
                    if !dst.is_finite() { return err("Nonfinite PCM"); }
                }
            }
        }
        self.decode_ns = begin.elapsed().as_nanos() as u64;
        let meta_begin = Instant::now();
        let mut dynamic = [0usize; SLOT_CAPACITY];
        let mut desired = [false; SLOT_CAPACITY];
        let mut num_dynamic = 0;
        let mut seen_bed = [false; 12];
        let mut lane_bed = [[false; 12]; crate::unified_input::LANES];
        if let Some((storage, _, _)) = &mut self.compact {
            storage.bed_mix_mut().fill(0.);
        } else {
            self.bed.fill(0.);
        }
        if let ObjectPosition::Cartesian(p) = &mut self.positions { p.clear(); }
        for &slot in &occupied[..count] {
            let prop = props[slot];
            let Property { typ, .. } = prop;
            let state = &mut self.states[slot];
            let (created, retired, updated) = state.observe(prop);
            self.mapping.creates += u64::from(created);
            self.mapping.retires += u64::from(retired);
            self.mapping.updates += u64::from(updated);
            if typ == 1 {
                if let Some(position) = state.position {
                    let [x, y, z] = coordinates(position, self.scaled, self.reference);
                    if let ObjectPosition::Cartesian(p) = &mut self.positions {
                        p.push(oar(CartesianCoordinate::new(x, y, z))?);
                    }
                    dynamic[num_dynamic] = slot;
                    desired[slot] = true;
                    num_dynamic += 1;
                } else {
                    self.mapping.unknown += 1;
                    if !unified && self.pcm[slot * self.n..(slot + 1) * self.n].iter().any(|&v| v != 0.) {
                        return err("Unknown position with nonzero PCM");
                    }
                }
            } else {
                let lane = if self.compact.is_some() { (slot - COMPACT_OBJECTS) / 12 } else { slot_source(slot).0 };
                if lane == crate::unified_input::PCM && upmixed_bed.is_some() {
                    continue;
                }
                let index = BED_TYPES.iter().position(|&t| t == typ).unwrap();
                if lane_bed[lane][index] { return err("Duplicate static type"); }
                lane_bed[lane][index] = true;
                let range = index * self.n..(index + 1) * self.n;
                let (src, dst) = if let Some((storage, _, _)) = &mut self.compact {
                    let (kind, lane) = compact_lane(lane);
                    let (source, mix) = storage.lane_and_mix_mut(kind, lane).ok_or("Compact lane is not prepared")?;
                    (&source[range.clone()], &mut mix[range])
                } else {
                    (&self.pcm[slot * self.n..(slot + 1) * self.n], &mut self.bed[range])
                };
                if seen_bed[index] {
                    for (a, b) in dst.iter_mut().zip(src) { *a += b; }
                } else {
                    dst.copy_from_slice(src);
                    seen_bed[index] = true;
                }
            }
        }
        if let Some(bed) = upmixed_bed {
            // The native joint output is already planar in BED_TYPES order.
            // Sum it directly into the ROAR bed buffer, without making a packet.
            for (index, channel) in bed.chunks_exact(self.n).enumerate().take(8) {
                let bed = if let Some((storage, _, _)) = &mut self.compact { storage.bed_mix_mut() } else { &mut self.bed };
                let dest = &mut bed[index * self.n..(index + 1) * self.n];
                for (sample, value) in dest.iter_mut().zip(channel) { *sample += value; }
                seen_bed[index] = true;
            }
        }
        let bed_present = seen_bed.iter().any(|v| *v);
        if self.grouped {
            // Fast grouping is explicitly a stable-slot profile. Never silently reset
            // unchanged channels' interpolation when one slot changes its generation.
            if let Some(signature) = &self.signature {
                if signature.len() != num_dynamic
                    || signature.iter().zip(dynamic[..num_dynamic].iter()).any(
                        |(&(slot, generation), &now)| {
                            slot != now || generation != self.states[now].generation
                        },
                    )
                {
                    return err(
                        "Grouped profile requires stable slots/generations; use individual mode",
                    );
                }
            } else {
                if num_dynamic > 0 {
                    oar(self.rdr.add_element(
                        self.group,
                        1,
                        &AudioElementConfig::ObjectBased(oar(ObjectBasedConfig::new_nsp_olr_batch(num_dynamic))?),
                    ))?;
                }
                self.signature = Some(
                    dynamic[..num_dynamic]
                        .iter()
                        .map(|&i| (i, self.states[i].generation))
                        .collect(),
                );
            }
            if num_dynamic > 0 {
                oar(self
                    .rdr
                    .update_element_positions(1, &self.positions, self.frames))?;
            }
        } else {
            for slot in 0..self.active.len() {
                let state = self.states[slot];
                let missing_source = if let Some((storage, _, _)) = &self.compact {
                    slot < COMPACT_OBJECTS && storage.object(slot)
                        .is_some_and(|key| lane_counts[key.lane].is_none())
                } else {
                    lane_counts[slot_source(slot).0].is_none()
                };
                let retain = desired[slot] || (unified && missing_source && state.present);
                if self.active[slot] != 0
                    && (!retain || self.active[slot] != state.generation)
                {
                    if self.compact.is_some() {
                        oar(self.rdr.reset_olr_element(slot as u32 + 1))?;
                    } else {
                        oar(self.rdr.remove_element(slot as u32 + 1))?;
                    }
                    self.active[slot] = 0;
                }
            }
            for &slot in &dynamic[..num_dynamic] {
                if self.active[slot] == 0 {
                    if self.compact.is_none() {
                        oar(self.rdr.add_element(
                            self.group,
                            slot as u32 + 1,
                            &AudioElementConfig::ObjectBased(oar(ObjectBasedConfig::new(1, None))?),
                        ))?;
                    }
                    self.active[slot] = self.states[slot].generation;
                }
            }
            let coordinates = match &self.positions {
                ObjectPosition::Cartesian(p) => p,
                _ => unreachable!(),
            };
            for (index, &slot) in dynamic[..num_dynamic].iter().enumerate() {
                if let ObjectPosition::Cartesian(v) = &mut self.one_position {
                    v.clear();
                    v.push(coordinates[index]);
                }
                oar(self.rdr.update_element_positions(
                    slot as u32 + 1,
                    &self.one_position,
                    self.frames,
                ))?;
            }
        }
        if bed_present && !self.bed_active {
            oar(self.rdr.add_element(
                self.group,
                BED_ID,
                &AudioElementConfig::ChannelBased(ChannelBasedConfig {
                    layout: InputLayout::Layout714,
                    downmix_info: None,
                    rendering_config: None,
                }),
            ))?;
            self.bed_active = true;
        }
        if !bed_present && self.bed_active && self.compact.is_none() {
            oar(self.rdr.remove_element(BED_ID))?;
            self.bed_active = false;
        }
        self.metadata_ns = meta_begin.elapsed().as_nanos() as u64;
        let dsp_begin = Instant::now();
        self.output.fill(0.);
        if num_dynamic > 0 || bed_present {
            let compact = self.compact.as_ref().map(|(storage, _, _)| storage);
            let mono: [&[f32]; SLOT_CAPACITY] = std::array::from_fn(|i| {
                if i < self.states.len() { Self::block(compact, &self.pcm, self.n, i) } else { &[] }
            });
            let bed_samples = self.compact.as_ref().map_or(self.bed.as_slice(), |(storage, _, _)| storage.bed_mix());
            let bed: [&[f32]; 12] =
                std::array::from_fn(|i| &bed_samples[i * self.n..(i + 1) * self.n]);
            let mut grouped_channels = [&[][..]; RENDER_INPUTS];
            if self.grouped {
                for (i, &slot) in dynamic[..num_dynamic].iter().enumerate() {
                    grouped_channels[i] = mono[slot];
                }
            }
            let dummy = oar(PlanarBufferRef::new(&mono[0..1], 1, self.frames))?;
            let mut inputs = [(0, dummy); RENDER_INPUTS];
            let mut len = 0;
            if self.grouped && num_dynamic > 0 {
                inputs[len] = (
                    1,
                    oar(PlanarBufferRef::new(
                        &grouped_channels[..num_dynamic],
                        num_dynamic,
                        self.frames,
                    ))?,
                );
                len += 1;
            }
            if !self.grouped {
                for &slot in &dynamic[..num_dynamic] {
                    inputs[len] = (
                        slot as u32 + 1,
                        oar(PlanarBufferRef::new(&mono[slot..slot + 1], 1, self.frames))?,
                    );
                    len += 1;
                }
            }
            if bed_present {
                inputs[len] = (BED_ID, oar(PlanarBufferRef::new(&bed, 12, self.frames))?);
                len += 1;
            }
            let mut chunks = self.output.chunks_exact_mut(self.n);
            let channels=self.layout.labels.len();
            let mut out: [&mut [f32]; 16] = std::array::from_fn(|_| &mut [][..]);
            for slot in out.iter_mut().take(channels) { *slot=chunks.next().unwrap(); }
            let mut output = oar(PlanarBufferMut::new(&mut out[..channels], channels, self.frames))?;
            if unified && !self.grouped {
                oar(self.rdr.render_olr_sparse(&inputs[..len], &mut output))?;
            } else {
                oar(self.rdr.render(&inputs[..len], &mut output))?;
            }
        }
        self.dsp_ns = dsp_begin.elapsed().as_nanos() as u64;
        if !self.output.iter().all(|v| v.is_finite()) {
            return err("Nonfinite rendered output");
        }
        if let Some(tap) = object_tap.filter(|tap| tap.active() && count != 0) {
            if count > SLOTS {
                self.object_tap_truncated_blocks += 1;
                self.object_tap_truncated_records += (count - SLOTS) as u64;
            }
            let count = count.min(SLOTS);
            if let Some(ids) = &mut self.unified_tap_ids {
                ids.assign(&occupied[..count], &self.states);
            }
            tap.publish_custom(|epoch, wire| {
                telemetry::header(wire, b"NSPO", count as u16, epoch, seq);
                for (wire_slot, &slot) in occupied[..count].iter().enumerate() {
                    let s = self.states[slot]; let at = 32 + wire_slot * (48 + 480 * 4);
                    let meta = &mut wire[at..at+48];
                    let (id, generation) = self.unified_tap_ids.as_ref().map_or((slot, s.generation), |ids| {
                        let id = ids.lookup[slot].unwrap();
                        (id, ids.slots[id].generation)
                    });
                    for (index, value) in [id as u32, s.ident, s.typ, props[slot].flags].iter().enumerate() {
                        meta[index*4..index*4+4].copy_from_slice(&value.to_le_bytes());
                    }
                    meta[16..24].copy_from_slice(&generation.to_le_bytes());
                    for (i,value) in s.position.unwrap_or([0.;3]).iter().enumerate() {
                        meta[24+i*4..28+i*4].copy_from_slice(&(*value as f32).to_le_bytes());
                    }
                    meta[36..40].copy_from_slice(&(s.position.is_some() as u32).to_le_bytes());
                    meta[40..44].copy_from_slice(&props[slot].gain.to_le_bytes());
                    meta[44..48].copy_from_slice(&(self.apply_gain as u32).to_le_bytes());
                    let pcm = Self::block(self.compact.as_ref().map(|(storage, _, _)| storage), &self.pcm, self.n, slot);
                    let bytes = unsafe { slice::from_raw_parts(pcm.as_ptr() as *const u8, 480*4) };
                    wire[at+48..at+48+480*4].copy_from_slice(bytes);
                }
                32 + count * (48 + 480 * 4)
            });
        }
        for (slot, s) in self.states.iter_mut().enumerate() {
            let missing = if self.compact.is_some() {
                slot >= COMPACT_OBJECTS
                    && lane_counts[(slot - COMPACT_OBJECTS) / 12].is_some()
                    && props[slot].typ == 0
            } else {
                let (lane, local_slot) = slot_source(slot);
                lane_counts[lane].is_some_and(|n| local_slot >= n)
            };
            if s.present && (missing || props[slot].flags & 8 != 0) {
                s.present = false;
                s.position = None;
                self.mapping.retires += 1;
            }
        }
        self.last = Some(seq);
        self.mapping.packets += 1;
        self.mapping.frames += self.n as u64;
        self.mapping.max_dynamic = self.mapping.max_dynamic.max(num_dynamic);
        self.mapping.max_static = self
            .mapping
            .max_static
            .max(seen_bed.iter().filter(|v| **v).count());
        Ok(&mut self.output)
    }
}

fn status(f: impl FnOnce() -> Result<(), String>) -> i32 {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(Ok(())) => 0,
        Ok(Err(_)) => -22,
        Err(_) => -1000,
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn nsp_packet_create(
    n: u32,
    rate: u32,
    grouped: u32,
    gain: u32,
    scaled: u32,
    reference: f64,
) -> *mut c_void {
    catch_unwind(|| {
        Engine::new(n, rate, grouped != 0, gain != 0, scaled != 0, reference)
            .map(|e| Box::into_raw(Box::new(e)) as *mut c_void)
    })
    .ok()
    .and_then(Result::ok)
    .unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_packet_destroy(p: *mut c_void) {
    if !p.is_null() {
        drop(unsafe { Box::from_raw(p as *mut Engine) });
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_packet_render(
    p: *mut c_void,
    seq: u64,
    count: u32,
    data: *const u8,
    len: usize,
    out: *mut f32,
    out_len: usize,
) -> i32 {
    status(|| {
        if p.is_null() || data.is_null() || out.is_null() {
            return err("Null pointer");
        }
        let e = unsafe { &mut *(p as *mut Engine) };
        if out_len != e.n * e.layout.labels.len() {
            return err("Output length");
        }
        let result = e.process(seq, count as usize, unsafe {
            slice::from_raw_parts(data, len)
        })?;
        unsafe { slice::from_raw_parts_mut(out, out_len) }.copy_from_slice(result);
        Ok(())
    })
}

// Bounded native queues use preallocated packet/output buffer pools. Writer
// failure or pool exhaustion is fatal and is reflected in the final report.
pub(crate) struct Packet {
    pub(crate) lane: usize,
    pub(crate) seq: u64,
    pub(crate) count: usize,
    pub(crate) data: Vec<u8>,
    pub(crate) received: Instant,
    pub(crate) capture_ns: u64,
    pub(crate) source_lease: u64,
    pub(crate) upmix_ticket: Option<(u64, u32)>,
    pub(crate) upmix_eligible: bool,
}
impl Packet {
    pub(crate) fn recycle(mut self, pool: &SyncSender<Vec<u8>>) {
        self.data.clear();
        let _ = pool.try_send(self.data);
    }
}
struct OutputPacket { data: Box<crate::output_pool::Block>, sequence: u64, capture_ns: u64, render_ns: u64, queued: Instant }
struct Timing {
    hist: Vec<u64>,
    count: u64,
    max: u64,
    over5: u64,
    over10: u64,
}
impl Timing {
    fn new() -> Self {
        Self {
            hist: vec![0; 50001],
            count: 0,
            max: 0,
            over5: 0,
            over10: 0,
        }
    }
    fn add(&mut self, ns: u64) {
        self.hist[((ns / 1000) as usize).min(50000)] += 1;
        self.count += 1;
        self.max = self.max.max(ns);
        self.over5 += (ns >= 5_000_000) as u64;
        self.over10 += (ns >= 10_000_000) as u64;
    }
    fn percentile(&self, q: f64) -> f64 {
        let target = (self.count as f64 * q).ceil() as u64;
        let mut sum = 0;
        for (i, &v) in self.hist.iter().enumerate() {
            sum += v;
            if sum >= target {
                return i as f64 / 1000.;
            }
        }
        0.
    }
    fn json(&self) -> String {
        format!(
            "{{\"p50\":{},\"p95\":{},\"p99\":{},\"max\":{},\"count\":{},\"atLeast5ms\":{},\"atLeast10ms\":{}}}",
            self.percentile(0.5),
            self.percentile(0.95),
            self.percentile(0.99),
            self.max as f64 / 1e6, self.count, self.over5, self.over10
        )
    }
}
struct Stats {
    input_packets: u64,
    ingress_discarded: u64,
    spatial_boundary_timing: Option<[Timing; 4]>,
    processed: u64,
    written: u64,
    tail_packets: u64,
    tail_frames: u64,
    error: Option<String>,
    mapping: Mapping,
    work: Timing,
    decode: Timing,
    metadata: Timing,
    dsp: Timing,
    queue: Timing,
    capture_render: Timing,
    render_execution: Timing,
    telemetry_publish: Timing,
    capture_publish: Timing,
    writer: WriterStats,
    mmcss_requested: bool,
    mmcss_registered: bool,
    mmcss_priority: bool,
    mmcss_reverted: bool,
    thread_id: u32,
}
struct WriterStats { written: u64, frames: u64, queue: Timing, write: Timing, capture_write: Timing, flush_ns: u64, tails: Vec<String>, overflow: u64, thread_id: u32, mmcss_requested: bool, mmcss_registered: bool, mmcss_priority: bool, mmcss_reverted: bool }
impl WriterStats {
    fn new() -> Self { Self { written: 0, frames: 0, queue: Timing::new(), write: Timing::new(), capture_write: Timing::new(), flush_ns: 0, tails: Vec::with_capacity(256), overflow: 0, thread_id: 0, mmcss_requested: false, mmcss_registered: false, mmcss_priority: false, mmcss_reverted: false } }
}
impl Stats {
    fn new() -> Self {
        Self { input_packets: 0, ingress_discarded: 0, spatial_boundary_timing: None,
            processed: 0,
            written: 0,
            tail_packets: 0, tail_frames: 0,
            error: None,
            mapping: Mapping::default(),
            work: Timing::new(),
            decode: Timing::new(),
            metadata: Timing::new(),
            dsp: Timing::new(),
            queue: Timing::new(),
            capture_render: Timing::new(),
            render_execution: Timing::new(),
            telemetry_publish: Timing::new(),
            capture_publish: Timing::new(),
            writer: WriterStats::new(),
            mmcss_requested: false, mmcss_registered: false, mmcss_priority: false, mmcss_reverted: false, thread_id: 0,
        }
    }
}
// ROAR is the backend; this integration currently exposes only its OLR mode.
#[path = "render/backend.rs"]
mod backend;
use backend::{Backend, OutputLayout, RoarMode};

struct Pipeline {
    layout: OutputLayout,
    progress: Arc<AtomicU64>,
    recording: Arc<Mutex<String>>,
    lira: bool,
    notebook_post: bool,
    scaled: bool,
    reference: f64,
    sink: Arc<Mutex<Option<OutputSink>>>,
    pool_sink: Arc<Mutex<Option<crate::output_pool::Sink>>>,
    #[cfg(feature="aoip-output")]
    aoip: Option<Arc<crate::output_pool::aoip_source::Metrics>>,
    telemetry: Arc<telemetry::Control>,
    object_telemetry: Arc<telemetry::Control>,
    tx: Option<SyncSender<Packet>>,
    free: Mutex<Receiver<Vec<u8>>>,
    free_tx: SyncSender<Vec<u8>>,
    worker: Option<JoinHandle<Stats>>,
    enqueued: Arc<AtomicU64>,
    dropped: Arc<AtomicU64>,
    failed: Arc<AtomicU64>,
    capacity: usize,
    n: usize,
    report: String,
    unified: bool,
    inputs: [InputHandle; crate::unified_input::LANES],
    unified_stats: Arc<UnifiedStats>,
    expansion: Option<Expansion>,
    hardware_volume_db: Arc<AtomicU64>,
    trace: Arc<OnceLock<PcmTrace>>,
    render_wake: Option<Arc<crate::render_wake::RenderWake>>,
    primary_request: Arc<AtomicUsize>,
}
struct InputHandle { pipeline: usize, lane: usize }

#[derive(Default)]
struct UnifiedStats {
    source_blocks: [AtomicU64; crate::unified_input::LANES],
    mixed_blocks: AtomicU64,
    discarded: AtomicU64,
    upmix_loaded: bool,
    upmixed: AtomicU64,
    upmixed_with_spatial: AtomicU64,
    upmix_fallback: AtomicU64,
    spatial_windows: [AtomicU64; 5],
    spatial_incomplete: AtomicU64,
    spatial_late: AtomicU64,
    spatial_duplicates: AtomicU64,
    spatial_untimed: AtomicU64,
    spatial_window_closures: [AtomicU64; 4],
    primary_same: [AtomicU64; crate::unified_input::LANES],
    primary_previous: [AtomicU64; crate::unified_input::LANES],
    primary_dropped: [AtomicU64; crate::unified_input::LANES],
    spatial_over_with_late_input: AtomicU64,
    output_pool_acquire_max_ns: AtomicU64,
    object_tap_truncated_records: AtomicU64,
    object_tap_truncated_blocks: AtomicU64,
}
type SinkCallback = unsafe extern "C" fn(*mut c_void, u64, *const f32, u32, u32) -> i32;
#[derive(Clone, Copy)]
struct OutputSink { callback: SinkCallback, context: usize }
impl Pipeline {
    fn new(n: u32, capacity: usize, grouped: bool, gain: bool, path: &str) -> Result<Self, String> {
        Self::with_position_mapping(n, capacity, grouped, gain, false, 1., path)
    }
    fn with_position_mapping(n: u32, capacity: usize, grouped: bool, gain: bool, scaled: bool, reference: f64, path: &str) -> Result<Self, String> {
        let engine=Backend::Roar(RoarMode::Olr(Engine::new(n,48000,grouped,gain,scaled,reference)?));
        Self::with_backend(n,capacity,scaled,reference,path,engine)
    }
    fn with_backend(n:u32,capacity:usize,scaled:bool,reference:f64,path:&str,engine:Backend)->Result<Self,String>{
        Self::with_recording(n,capacity,scaled,reference,path,engine,true,true)
    }
    fn with_recording(n:u32,capacity:usize,scaled:bool,reference:f64,path:&str,engine:Backend,record:bool,reliable:bool)->Result<Self,String>{
        Self::with_ingress(n,capacity,scaled,reference,path,engine,record,reliable,false,None)
    }
    fn with_ingress(n:u32,capacity:usize,scaled:bool,reference:f64,path:&str,mut engine:Backend,record:bool,reliable:bool,unified:bool,joint:Option<crate::joint::Joint>)->Result<Self,String>{
        if !(1..=256).contains(&capacity) {return err("Queue capacity");}
        let lira=engine.is_lira();
        let notebook_post=matches!(&engine,Backend::Notebook(..)|Backend::Lsac(..));
        let layout=engine.layout();let channels=engine.channels();
        let _ = qpc_ns(); // Initialize frequency before either worker starts.
        let mut recording = if record {match crate::recording::Capture::file(path,n as usize*channels,channels,reliable){Ok(v)=>v,Err(e) if reliable=>return Err(e),Err(e)=>crate::recording::Capture::failed(e)}} else {crate::recording::Capture::disabled()};
        let recording_report=Arc::new(Mutex::new(recording.json()));let recording_result=recording_report.clone();
        let progress=Arc::new(AtomicU64::new(0));let worker_progress=progress.clone();
        let (tx, rx) = mpsc::sync_channel::<Packet>(capacity);
        let (free_tx, free_rx) = mpsc::sync_channel(capacity + 1);
        for _ in 0..capacity + 1 {
            free_tx
                .send(Vec::with_capacity(SLOTS * (32 + n as usize * 4)))
                .unwrap();
        }
        let (out_tx, out_rx) = mpsc::sync_channel::<OutputPacket>(capacity);
        let (out_free_tx, out_free_rx) = mpsc::sync_channel(capacity + 1);
        for _ in 0..capacity + 1 {
            out_free_tx.send(Box::new(crate::output_pool::Block { channels, data: vec![0f32; n as usize * channels], recycle: out_free_tx.clone() })).unwrap();
        }
        let out_free_rx = Arc::new(Mutex::new(out_free_rx));
        let writer_free_rx = Arc::clone(&out_free_rx);
        let enqueued = Arc::new(AtomicU64::new(0));
        let dropped = Arc::new(AtomicU64::new(0));
        let failed = Arc::new(AtomicU64::new(0));
        let worker_failed = Arc::clone(&failed);
        let worker_free = free_tx.clone();
        let sink = Arc::new(Mutex::new(None::<OutputSink>));
        let writer_sink = Arc::clone(&sink);
        let pool_sink = Arc::new(Mutex::new(None::<crate::output_pool::Sink>));
        let writer_pool_sink = Arc::clone(&pool_sink);
        let telemetry = telemetry::Control::output(n == 480, channels);
        let mut tap = telemetry.producer();
        let object_telemetry = telemetry::Control::objects(n == 480);
        let mut object_tap = object_telemetry.producer();
        if std::env::var("NSP_TELEMETRY").is_ok_and(|s| s == "1") { telemetry.enable(true); object_telemetry.enable(true); }
        let renderer_mmcss = unified||engine.has_semantic()||std::env::var("NSP_RENDER_MMCSS").map(|s| s == "1").unwrap_or(false);
        let unified_stats = Arc::new(UnifiedStats { upmix_loaded: joint.is_some(), ..UnifiedStats::default() });
        let worker_unified_stats = unified_stats.clone();
        let render_wake = unified.then(crate::render_wake::RenderWake::new).transpose()?.map(Arc::new);
        if let (Some(joint), Some(wake)) = (&joint, &render_wake) { joint.set_ready_event(wake); }
        let worker_wake = render_wake.clone();
        let primary_request = Arc::new(AtomicUsize::new(crate::unified_input::PRIMARY_NO_REQUEST));
        let worker_primary_request = primary_request.clone();
        let trace = Arc::new(OnceLock::<PcmTrace>::new());
        let writer_trace = trace.clone();
        let render_trace = trace.clone();
        let writer = thread::spawn(move || -> Result<WriterStats, String> {
            let mut stats = WriterStats::new();
            stats.thread_id = unsafe { GetCurrentThreadId() };
            let mut task = RenderTask { handle: std::ptr::null_mut() };
            let mut pool_sink = None::<crate::output_pool::Sink>;
            while let Ok(packet) = out_rx.recv() {
                let begin = Instant::now();
                if let Some(trace) = writer_trace.get() { trace.mark(TraceEvent::WriterBegin, packet.sequence, 0, 0); }
                if packet.capture_ns != 0 { stats.queue.add(packet.queued.elapsed().as_nanos() as u64); }
                let mut block = packet.data;
                let buf = &block.data;
                // Reliable output boundary, not a diagnostic tap. Legacy sinks
                // borrow synchronously; pool sinks transfer a releasable lease.
                let legacy_sink = *writer_sink.lock().unwrap();
                if pool_sink.is_none() {pool_sink=writer_pool_sink.lock().unwrap().take();}
                if legacy_sink.is_some() || pool_sink.is_some() {
                    // This writer is also the reliable realtime PCM handoff.
                    // Register on its own thread only for live output, not for
                    // file-only rendering. The same writer owns pool-side DSP.
                    if !stats.mmcss_requested {
                        stats.mmcss_requested = true;
                        let name: Vec<u16> = "Pro Audio\0".encode_utf16().collect();
                        let mut index = 0;
                        task.handle = unsafe { AvSetMmThreadCharacteristicsW(name.as_ptr(), &mut index) };
                        stats.mmcss_registered = !task.handle.is_null();
                        stats.mmcss_priority = stats.mmcss_registered && unsafe { AvSetMmThreadPriority(task.handle, 0) } != 0;
                        if let Some(trace) = writer_trace.get() {
                            trace.mark(TraceEvent::Scheduling, 2, stats.mmcss_registered.into(), stats.mmcss_priority.into());
                        }
                        if !stats.mmcss_priority { return Err("Output handoff MMCSS registration/priority failed".into()); }
                    }
                    if let Some(sink) = legacy_sink {
                        let rc = unsafe { (sink.callback)(sink.context as *mut c_void,
                            stats.frames, buf.as_ptr(), (buf.len()/channels) as u32, channels as u32) };
                        if rc != 0 { return Err(format!("Output sink rejected PCM: {rc}")); }
                    }
                }
                // Copy only when explicitly recording. Never retain playback leases
                // or wait for the recorder in a live session. Preserve pre-DSP PCM.
                recording.submit(buf);
                stats.written += 1;
                stats.frames += (buf.len()/channels) as u64;
                if packet.capture_ns != 0 {
                    let end = qpc_ns();
                    stats.write.add(begin.elapsed().as_nanos() as u64);
                    stats.capture_write.add(end.saturating_sub(packet.capture_ns));
                    if end.saturating_sub(packet.capture_ns) >= 5_000_000 {
                        if stats.tails.len() < 256 {
                            stats.tails.push(format!("{{\"sequence\":{},\"captureQpcNs\":{},\"renderQpcNs\":{},\"writeQpcNs\":{}}}", packet.sequence, packet.capture_ns, packet.render_ns, end));
                        } else { stats.overflow += 1; }
                    }
                }
                // Keep raw renderer recording/telemetry semantics unchanged.
                // DSP then mutates the exclusive pool block before publication.
                if let Some(sink) = pool_sink.as_mut() {
                    let frames = sink.process(&mut block, false)?;
                    if let Some(trace) = writer_trace.get() { trace.mark(TraceEvent::DspEnd, packet.sequence, frames as u64, 0); }
                    sink.publish(block, stats.frames - frames as u64, frames, false)?;
                } else {
                    let _ = out_free_tx.send(block);
                }
            }
            if pool_sink.is_none(){pool_sink=writer_pool_sink.lock().unwrap().take();}
            if let Some(sink) = pool_sink.as_mut() {
                let mut first = stats.frames;
                loop {
                    // EOF only: bounded pool, no new tail allocation. A failed
                    // output consumer returns outstanding leases before joining.
                    let mut block = writer_free_rx.lock().unwrap().recv().map_err(|_| "Output pool closed during DSP tail".to_string())?;
                    block.data.resize(n as usize * channels, 0.);
                    let frames = sink.process(&mut block, true)?;
                    if frames == 0 { let _ = out_free_tx.send(block); break; }
                    sink.publish(block, first, frames, true)?;
                    first += frames as u64;
                }
                sink.finish()?;
            }
            // Final disk flush is outside the realtime handoff lifetime.
            if !task.handle.is_null() {
                stats.mmcss_reverted = unsafe { AvRevertMmThreadCharacteristics(task.handle) } != 0;
                if !stats.mmcss_reverted { return Err("Output handoff MMCSS revert failed".into()); }
                task.handle = std::ptr::null_mut();
            }
            // Audio handoff and all DSP tails are complete before recorder drain.
            recording.finish();
            *recording_result.lock().unwrap()=recording.json();
            if reliable && recording.incomplete(){return Err("Offline recording incomplete; inspect pcmRecording".into());}
            Ok(stats)
        });
        let worker = thread::spawn(move || {
            let mut stats = Stats::new();
            stats.thread_id = unsafe { GetCurrentThreadId() };
            stats.mmcss_requested = renderer_mmcss;
            let mut task = RenderTask { handle: std::ptr::null_mut() };
            if renderer_mmcss {
                let name: Vec<u16> = "Pro Audio\0".encode_utf16().collect();
                let mut index = 0;
                task.handle = unsafe { AvSetMmThreadCharacteristicsW(name.as_ptr(), &mut index) };
                stats.mmcss_registered = !task.handle.is_null();
                stats.mmcss_priority = stats.mmcss_registered && unsafe { AvSetMmThreadPriority(task.handle, 0) } != 0;
                if !stats.mmcss_priority { stats.error = Some("Renderer MMCSS registration/priority failed".into()); worker_failed.store(1, Ordering::Release); }
            }
            let mut ingress = unified.then(|| crate::unified_input::Ingress::new(joint));
            stats.spatial_boundary_timing = unified.then(|| std::array::from_fn(|_| Timing::new()));
            let mut reported_scheduling = false;
            while stats.error.is_none() {
                let packets = if let Some(ingress) = &mut ingress {
                    let batch = match ingress.next_with_primary_control(&rx, &worker_free, worker_wake.as_ref().unwrap(), &render_trace, &worker_primary_request) {
                        Ok(Some(batch)) => batch,
                        Ok(None) => break,
                        Err(code) => {
                            stats.error = Some(format!("Render ingress failed with Win32 error {code}"));
                            worker_failed.store(1, Ordering::Release);
                            break;
                        }
                    };
                    batch
                } else {
                    let Ok(packet) = rx.recv() else { break };
                    let mut batch = std::array::from_fn(|_| None);
                    batch[0] = Some(packet);
                    batch
                };
                let first = ingress.as_ref().and_then(|i| i.primary_lane)
                    .and_then(|lane| packets[lane].as_ref())
                    .or_else(|| packets.iter().flatten().next()).unwrap();
                let sequence = if unified { stats.processed } else { first.seq };
                let trace = render_trace.get().copied();
                if let Some(trace) = trace {
                    if !reported_scheduling {
                        trace.mark(TraceEvent::Scheduling, 1, stats.mmcss_registered.into(), stats.mmcss_priority.into());
                        reported_scheduling = true;
                    }
                    let pcm = packets[crate::unified_input::PCM].as_ref().map_or(u64::MAX, |p| p.seq);
                    trace.mark(TraceEvent::RenderBegin, sequence, pcm, 0);
                }
                let capture_ns = if ingress.as_ref().is_some_and(|i| i.primary_lane.is_some()) {
                    first.capture_ns
                } else {
                    packets.iter().flatten().map(|p| p.capture_ns).filter(|&t| t != 0).min().unwrap_or(0)
                };
                let begin = Instant::now();
                stats.queue.add(first.received.elapsed().as_nanos() as u64);
                if let Some((first, sealed, gate)) = ingress.as_ref().and_then(|i| i.render_boundary) {
                    if let Some([to_seal, after_seal, gate_wait, after_gate]) = &mut stats.spatial_boundary_timing {
                        to_seal.add(sealed.saturating_duration_since(first).as_nanos() as u64);
                        after_seal.add(begin.saturating_duration_since(sealed).as_nanos() as u64);
                        gate_wait.add(gate.saturating_duration_since(sealed).as_nanos() as u64);
                        after_gate.add(begin.saturating_duration_since(gate.max(sealed)).as_nanos() as u64);
                    }
                }
                stats.input_packets += packets.iter().flatten().count() as u64;
                let result = (|| -> Result<(), String> {
                    let pool_begin = unified.then(Instant::now);
                    let mut out = out_free_rx.lock().unwrap()
                        // The independent writer may still be registering MMCSS
                        // at preroll. A transient empty pool is backpressure,
                        // not malformed PCM. Bound the wait on this renderer
                        // worker; the capture callback never waits here.
                        .recv_timeout(std::time::Duration::from_millis(1500))
                        .map_err(|_| format!("Writer buffer pool stalled for 1500ms (leases released {}, recycle failures {})",crate::output_pool::RELEASED.load(Ordering::Relaxed),crate::output_pool::RECYCLE_FAILED.load(Ordering::Relaxed)))?;
                    if let Some(begin) = pool_begin {
                        worker_unified_stats.output_pool_acquire_max_ns.fetch_max(
                            begin.elapsed().as_nanos().min(u64::MAX as u128) as u64,
                            Ordering::Relaxed,
                        );
                    }
                    out.data.resize(n as usize*channels,0.);
                    let mut upmixed_bed = None;
                    let planar = if let Some(ingress) = &mut ingress {
                        let pcm = packets[crate::unified_input::PCM].as_ref();
                        upmixed_bed = pcm.and_then(|packet| ingress.upmixed_bed(packet));
                        let result = engine.process_unified(sequence, &packets, &mut object_tap, &mut out.data, upmixed_bed)?;
                        if upmixed_bed.is_some() {
                            worker_unified_stats.upmixed.fetch_add(1, Ordering::Relaxed);
                            if packets[..crate::unified_input::PCM].iter().any(Option::is_some) {
                                worker_unified_stats.upmixed_with_spatial.fetch_add(1, Ordering::Relaxed);
                            }
                        } else if pcm.is_some_and(|packet| packet.upmix_eligible) {
                            worker_unified_stats.upmix_fallback.fetch_add(1, Ordering::Relaxed);
                        }
                        result
                    } else {
                        engine.process_into(sequence, first.count, &first.data, &mut object_tap, &mut out.data)?
                    };
                    stats.render_execution.add(begin.elapsed().as_nanos() as u64);
                    if let Some(trace) = trace { trace.mark(TraceEvent::RenderEnd, sequence, upmixed_bed.is_some().into(), 0); }
                    let render_ns = if capture_ns != 0 { qpc_ns() } else { 0 };
                    worker_progress.fetch_add(out.data.len() as u64*4,Ordering::Release);
                    if capture_ns != 0 { stats.capture_render.add(render_ns.saturating_sub(capture_ns)); }
                    // Telemetry is a separate bounded diagnostic copy. LIRA's
                    // reliable PCM already lives in the writer's output pool.
                    if tap.active() {
                        let publish_begin = Instant::now();
                        if let Some(pcm)=planar {tap.publish_with_upmix(sequence,pcm,upmixed_bed);} else {tap.publish_interleaved(sequence,&out.data);}
                        stats.telemetry_publish.add(publish_begin.elapsed().as_nanos() as u64);
                        if capture_ns != 0 { stats.capture_publish.add(qpc_ns().saturating_sub(capture_ns)); }
                    }
                    out_tx
                        .try_send(OutputPacket { data: out, sequence, capture_ns, render_ns, queued: Instant::now() })
                        .map_err(|_| "Writer queue full or failed".to_string())?;
                    Ok(())
                })();
                let (truncated_records, truncated_blocks) = engine.object_tap_truncation();
                worker_unified_stats.object_tap_truncated_records.store(truncated_records, Ordering::Relaxed);
                worker_unified_stats.object_tap_truncated_blocks.store(truncated_blocks, Ordering::Relaxed);
                if let Some(ingress) = &ingress {
                    if result.is_ok() {
                        for packet in packets.iter().flatten() {
                            worker_unified_stats.source_blocks[packet.lane].fetch_add(1, Ordering::Relaxed);
                        }
                        if packets[crate::unified_input::PCM..].iter().any(Option::is_some)
                            && packets[..crate::unified_input::PCM].iter().any(Option::is_some)
                        {
                            worker_unified_stats.mixed_blocks.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    worker_unified_stats.discarded.store(ingress.discarded, Ordering::Relaxed);
                    for (counter, count) in worker_unified_stats.spatial_windows.iter().zip(ingress.spatial_windows) {
                        counter.store(count, Ordering::Relaxed);
                    }
                    worker_unified_stats.spatial_incomplete.store(ingress.spatial_incomplete, Ordering::Relaxed);
                    for (counters, values) in [
                        (&worker_unified_stats.primary_same, ingress.primary_same),
                        (&worker_unified_stats.primary_previous, ingress.primary_previous),
                        (&worker_unified_stats.primary_dropped, ingress.primary_dropped),
                    ] {
                        for (counter, count) in counters.iter().zip(values) {
                            counter.store(count, Ordering::Relaxed);
                        }
                    }
                    worker_unified_stats.spatial_late.store(ingress.spatial_late, Ordering::Relaxed);
                    worker_unified_stats.spatial_duplicates.store(ingress.spatial_duplicates, Ordering::Relaxed);
                    worker_unified_stats.spatial_untimed.store(ingress.spatial_untimed, Ordering::Relaxed);
                    for (counter, count) in worker_unified_stats.spatial_window_closures.iter().zip(ingress.spatial_window_closures) {
                        counter.store(count, Ordering::Relaxed);
                    }
                    worker_unified_stats.spatial_over_with_late_input.store(ingress.spatial_over_with_late_input, Ordering::Relaxed);
                }
                for packet in packets.into_iter().flatten() { packet.recycle(&worker_free); }
                if let Err(error) = result {
                    stats.error = Some(error);
                    worker_failed.store(1, Ordering::Release);
                    break;
                }
                stats.processed += 1;
                stats.work.add(begin.elapsed().as_nanos() as u64);
                let (decode,metadata,dsp)=engine.timing();
                stats.decode.add(decode);stats.metadata.add(metadata);stats.dsp.add(dsp);
            }
            if stats.error.is_none() {
                let result=(||->Result<(),String>{
                    let tail=engine.finish()?;
                    for chunk in tail.chunks(n as usize*channels){
                        // EOS is non-realtime. Bound memory as before and let the
                        // writer drain; never truncate the renderer's 512-frame tail.
                        let mut out=out_free_rx.lock().unwrap().recv().map_err(|_|"Writer failed during tail".to_string())?;
                        out.data.resize(chunk.len(),0.);out.data.copy_from_slice(chunk);
                        let frames=(chunk.len()/channels) as u64;
                        out_tx.send(OutputPacket{data:out,sequence:0,capture_ns:0,render_ns:0,queued:Instant::now()}).map_err(|_|"Writer failed during tail".to_string())?;
                        stats.tail_packets+=1;stats.tail_frames+=frames;
                    }
                    Ok(())
                })();
                if let Err(e)=result{stats.error=Some(e);worker_failed.store(1,Ordering::Release);}
            }
            if !task.handle.is_null() {
                stats.mmcss_reverted = unsafe { AvRevertMmThreadCharacteristics(task.handle) } != 0;
                if stats.mmcss_reverted { task.handle = std::ptr::null_mut(); }
                else { stats.error = Some("Renderer MMCSS revert failed".into()); worker_failed.store(1, Ordering::Release); }
            }
            drop(out_tx);
            match writer.join() {
                Ok(Ok(w)) => { stats.written = w.written; stats.writer = w; },
                Ok(Err(e)) => stats.error = Some(e),
                Err(_) => stats.error = Some("Writer panic".to_string()),
            }
            drop(out_free_rx);
            if let Some(ingress) = &ingress {
                for (counter, count) in worker_unified_stats.primary_dropped.iter().zip(ingress.primary_dropped) {
                    counter.store(count, Ordering::Relaxed);
                }
                worker_unified_stats.discarded.store(ingress.discarded, Ordering::Relaxed);
            }
            stats.ingress_discarded = ingress.map_or(0, |i| i.discarded);
            stats.mapping = engine.mapping();
            stats
        });
        Ok(Self {
            progress,recording:recording_report,
            layout,
            lira,
            notebook_post,
            scaled,
            reference,
            sink,
            pool_sink,
            #[cfg(feature="aoip-output")]
            aoip:None,
            telemetry,
            object_telemetry,
            tx: Some(tx),
            free: Mutex::new(free_rx),
            free_tx,
            worker: Some(worker),
            enqueued,
            dropped,
            failed,
            capacity,
            n: n as usize,
            report: String::new(),
            unified,
            inputs: std::array::from_fn(|lane| InputHandle { pipeline: 0, lane }),
            unified_stats,
            expansion: None,
            hardware_volume_db: Arc::new(AtomicU64::new(f64::NAN.to_bits())),
            trace,
            render_wake,
            primary_request,
        })
    }
    fn finish(&mut self) {
        self.tx.take();
        if let Some(wake) = &self.render_wake { wake.notify(); }
        let stats = match self.worker.take().unwrap().join() {
            Ok(s) => s,
            Err(_) => {
                let mut s = Stats::new();
                s.error = Some("Renderer panic".into());
                s
            }
        };
        let enqueued = self.enqueued.load(Ordering::Acquire);
        let dropped = self.dropped.load(Ordering::Acquire);
        let passed = enqueued > 0
            && enqueued == stats.input_packets + stats.ingress_discarded
            && stats.processed + stats.tail_packets == stats.written
            && dropped == 0
            && stats.error.is_none();
        let error = stats
            .error
            .map(|s| {
                format!(
                    "\"{}\"",
                    s.replace('\\', "\\\\")
                        .replace('"', "\\\"")
                        .replace('\n', "\\n")
                        .replace('\r', "\\r")
                        .replace('\t', "\\t")
                )
            })
            .unwrap_or("null".into());
        let mapping_json=if self.lira {"null".into()} else {format!("{{\"creates\":{},\"retires\":{},\"positionUpdates\":{},\"unknownPositionBlocks\":{},\"maxDynamic\":{},\"maxStatic\":{}}}",stats.mapping.creates,stats.mapping.retires,stats.mapping.updates,stats.mapping.unknown,stats.mapping.max_dynamic,stats.mapping.max_static)};
        self.report = format!(
            "{{\"passed\":{passed},\"enqueued\":{enqueued},\"consumed\":{},\"written\":{},\"queueDropped\":{dropped},\"queueCapacity\":{},\"error\":{error},\"threadStopped\":true,\"processedFrames\":{},\"mapping\":{},\"workMs\":{},\"decodeMs\":{},\"metadataMs\":{},\"dspMs\":{},\"queueWaitMs\":{},\"scope\":\"Native packet worker; output serialization/enqueue included, live output handoff independent of optional best-effort recorder; histogram 1us, percentile ceiling 50ms; optional reliable output sink, device status reported by host\"}}",
            stats.processed,
            stats.written,
            self.capacity,
            stats.mapping.frames,
            mapping_json,
            stats.work.json(),
            if self.lira {"null".into()} else {stats.decode.json()},
            if self.lira {"null".into()} else {stats.metadata.json()},
            if self.lira {"null".into()} else {stats.dsp.json()},
            stats.queue.json()
        );
        self.report.pop();
        self.report.push_str(&format!(",\"pcmRecording\":{}",self.recording.lock().unwrap()));
        self.report.push_str(&format!(",\"renderStartToReadyMs\":{}", stats.render_execution.json()));
        if let Some([to_seal, after_seal, gate_wait, after_gate]) = &stats.spatial_boundary_timing {
            self.report.push_str(&format!(",\"spatialBoundaryMs\":{{\"firstIngressToSeal\":{},\"sealToRenderStart\":{},\"gateAfterSeal\":{},\"eligibleToRenderStart\":{}}}", to_seal.json(), after_seal.json(), gate_wait.json(), after_gate.json()));
        }
        if self.unified { self.report.push_str(&format!(",\"unifiedInput\":true,\"inputPacketsConsumed\":{},\"ingressDiscarded\":{}",stats.input_packets,stats.ingress_discarded)); }
        #[cfg(feature="aoip-output")]
        if let Some(m)=&self.aoip {
            self.report.push_str(&format!(",\"aoipOutput\":{{\"mode\":\"native-pool\",\"fault\":{},\"acceptedBlocks\":{},\"recycledBlocks\":{},\"sentPackets\":{},\"drained\":{},\"txLatencyNs\":4000000,\"clockMatching\":\"not-yet-implemented\"}}",
                m.fault.load(Ordering::Acquire),m.accepted.load(Ordering::Relaxed),m.recycled.load(Ordering::Relaxed),m.sent.load(Ordering::Relaxed),m.drained.load(Ordering::Acquire)));
        }
        self.report.push_str(&format!(",\"outputLayout\":\"{}\",\"outputChannels\":{}",self.layout.id,self.layout.channels));
        self.report.push_str(&format!(",\"renderer\":\"{}\",\"rendererMode\":{},\"rendererDelayFrames\":{},\"rendererTailFrames\":{},\"outputFrames\":{}",if self.lira {"lira"} else {"roar"},if self.lira {"null"} else {"\"olr\""},if self.lira {512} else {0},stats.tail_frames,stats.writer.frames));
        self.report.push_str(&format!(",\"rendererOutputLayout\":\"{}\",\"notebookPostProcessing\":{}",if self.notebook_post{"7.1.4"}else{self.layout.id},if self.notebook_post{"{\"placement\":\"lsac-adapter\",\"inputChannels\":12,\"outputChannels\":2}"}else{"null"}));
        if self.lira {
            self.report.push_str(",\"positionMapping\":{\"mode\":\"backend-room-projection\",\"negativeHeight\":\"floor\",\"outsideRoom\":\"uniform-extent-projection\"}");
        } else {
        self.report.push_str(&format!(",\"positionMapping\":{{\"mode\":\"{}\",\"reference\":{},\"units\":\"source-coordinate-units-per-OLR-unit\",\"physicalCalibration\":false}}", if self.scaled { "scaled" } else { "direction" }, self.reference));
        }
        self.report.push_str(&format!(",\"rendererScheduling\":{{\"threadId\":{},\"requested\":{},\"registered\":{},\"prioritySet\":{},\"reverted\":{}}}", stats.thread_id, stats.mmcss_requested, stats.mmcss_registered, stats.mmcss_priority, stats.mmcss_reverted));
        self.report.push_str(&format!(",\"outputHandoffScheduling\":{{\"threadId\":{},\"requested\":{},\"registered\":{},\"prioritySet\":{},\"reverted\":{}}}", stats.writer.thread_id, stats.writer.mmcss_requested, stats.writer.mmcss_registered, stats.writer.mmcss_priority, stats.writer.mmcss_reverted));
        self.report.push_str(&format!(",\"fullTiming\":{{\"captureToRenderMs\":{},\"writerQueueMs\":{},\"bufferedWriteMs\":{},\"captureToBufferedWriteMs\":{},\"finalFlushSyncMs\":{},\"tails\":[{}],\"tailOverflow\":{},\"scope\":\"QPC capture through render-ready and pre-DSP side-tap enqueue; legacy bufferedWriteMs names measure handoff work, not disk or device completion\"}}}}", stats.capture_render.json(), stats.writer.queue.json(), stats.writer.write.json(), stats.writer.capture_write.json(), stats.writer.flush_ns as f64 / 1e6, stats.writer.tails.join(","), stats.writer.overflow));
        self.report.pop();
        self.report.push_str(&format!(",\"telemetry\":{},\"objectTelemetry\":{},\"telemetryPublishMs\":{},\"captureThroughTelemetryMs\":{}}}", self.telemetry.json(), self.object_telemetry.json(), stats.telemetry_publish.json(), stats.capture_publish.json()));
    }
}

/// Control caller only, before any submit; immutable until pipeline destruction.
/// Callback PCM is borrowed only until return. Nonzero fails the output writer.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_set_output_sink(
    p: *mut c_void, callback: Option<SinkCallback>, context: *mut c_void,
) -> i32 {
    status(|| {
        if p.is_null() { return err("Null pipeline"); }
        let p = unsafe { &*(p as *const Pipeline) };
        if p.tx.is_none() || p.enqueued.load(Ordering::Acquire) != 0 { return err("Set sink before submitting PCM"); }
        let mut sink = p.sink.lock().unwrap();
        if sink.is_some() || callback.is_none() || p.pool_sink.lock().unwrap().is_some() { return err("Output sink already set or null"); }
        *sink = Some(OutputSink { callback: callback.unwrap(), context: context as usize });
        Ok(())
    })
}
/// Additive lease ABI. process runs on the writer's exclusively owned PCM.
/// publish success transfers one preallocated token; consumer calls release
/// exactly once after its synchronous device submit (or on abort).
/// process op 0 transforms capacity frames; op 1 drains up to capacity at EOF.
/// Rejected publish retains ownership here. Configure before any input submit.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_set_pool_output(
    p: *mut c_void, process: Option<crate::output_pool::Process>,
    publish: Option<crate::output_pool::Publish>, context: *mut c_void,
) -> i32 {
    status(|| {
        if p.is_null() { return err("Null pipeline"); }
        let p = unsafe { &*(p as *const Pipeline) };
        if p.tx.is_none() || p.enqueued.load(Ordering::Acquire) != 0 { return err("Set pool before submitting PCM"); }
        let legacy = p.sink.lock().unwrap();
        let mut pool = p.pool_sink.lock().unwrap();
        if legacy.is_some() || pool.is_some() || process.is_none() || publish.is_none() { return err("Pool output already set or null"); }
        *pool = Some(crate::output_pool::Sink { lsac_eq:None, process, native:None, destination:crate::output_pool::Destination::Device{publish:publish.unwrap(),context:context as usize}, context: context as usize });
        Ok(())
    })
}
/// Native DSP is constructed on the control caller, then moved once to the
/// Rust output writer. Only the device publication callback crosses the ABI.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_set_native_pool_output(
    p:*mut c_void, config:*const crate::output_processing::Config,
    publish:Option<crate::output_pool::Publish>, context:*mut c_void,
)->i32 {
    unsafe { nsp_pipeline_set_native_pool_output_v2(p, config, publish, context, 0., std::ptr::null(), 0) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_set_native_pool_output_v2(
    p: *mut c_void, config: *const crate::output_processing::Config,
    publish: Option<crate::output_pool::Publish>, context: *mut c_void,
    reference_spl: f64, levels: *const nsp_lsac_adapter::loudness::Level, level_count: usize,
) -> i32 {
    status(|| {
        if p.is_null() || config.is_null() || publish.is_none(){return err("Null native output configuration");}
        let p=unsafe{&*(p as *const Pipeline)};
        if p.tx.is_none() || p.enqueued.load(Ordering::Acquire)!=0 {return err("Configure native DSP before PCM");}
        let legacy=p.sink.lock().unwrap();let mut pool=p.pool_sink.lock().unwrap();
        if legacy.is_some() || pool.is_some(){return err("Output already configured");}
        let config = unsafe { *config };
        if config.channels as usize != p.layout.channels { return err("Native DSP/output channel mismatch"); }
        let mut processor=crate::output_processing::Processor::new(config)?;
        let mut lsac_eq = None;
        if level_count != 0 {
            if levels.is_null() || !(2..=16).contains(&level_count) || !p.notebook_post
            {
                return err("LSAC equal-loudness requires stereo");
            }
            let levels = unsafe { slice::from_raw_parts(levels, level_count) };
            lsac_eq = Some(nsp_lsac_adapter::loudness::Loudness::new(p.hardware_volume_db.clone(), reference_spl, levels)?);
        }
        processor.enable_live_control(p.unified);
        if p.unified {
            if let Some(trace) = PcmTrace::from_sink(publish.unwrap(), context) { let _ = p.trace.set(trace); }
        }
        *pool=Some(crate::output_pool::Sink{lsac_eq,process:None,native:Some(processor),destination:crate::output_pool::Destination::Device{publish:publish.unwrap(),context:context as usize},context:context as usize});
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_set_hardware_volume_db(p: *mut c_void, db: f64) {
    if let Some(p) = unsafe { p.cast::<Pipeline>().as_ref() } {
        p.hardware_volume_db.store(db.to_bits(), Ordering::Relaxed);
    }
}
/// Experimental native network destination; configure before any input submit.
/// Both configurations are copied during this call; no PCM crosses this ABI.
#[cfg(feature="aoip-output")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_set_aoip_output(
    p:*mut c_void, config:*const crate::output_processing::Config, network:*const crate::output_pool::aoip::NativeConfig,
)->i32 {
    status(|| {
        if p.is_null() || config.is_null() || network.is_null(){return err("Null AoIP configuration");}
        let p=unsafe{&mut *(p as *mut Pipeline)};
        if p.tx.is_none() || p.enqueued.load(Ordering::Acquire)!=0{return err("Configure AoIP before PCM");}
        let dsp=unsafe{*config};
        if p.notebook_post || dsp.channels as usize!=p.layout.channels{return err("AoIP requires a core output layout");}
        let legacy=p.sink.lock().unwrap();let mut pool=p.pool_sink.lock().unwrap();
        if legacy.is_some() || pool.is_some(){return err("Output already configured");}
        let network=crate::output_pool::aoip::Config::from_native(unsafe{*network})?;
        if network.prebuffer_frames>p.capacity.saturating_sub(1)*p.n {
            return err("AoIP prebuffer exceeds available native pool capacity");
        }
        let processor=crate::output_processing::Processor::new(dsp)?;
        let output=crate::output_pool::aoip::Output::start(network,dsp.channels as usize)?;
        p.aoip=Some(output.metrics());
        *pool=Some(crate::output_pool::Sink{lsac_eq:None,process:None,native:Some(processor),destination:crate::output_pool::Destination::Aoip(output),context:0});
        Ok(())
    })
}
#[cfg(feature="aoip-output")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_set_aoip_route(p:*mut c_void,config:*const crate::output_processing::Config,route:*const c_void,source:u32)->i32{
    status(||{
        if p.is_null() || config.is_null(){return err("Null output route configuration");}
        let p=unsafe{&mut *p.cast::<Pipeline>()};let dsp=unsafe{*config};
        if p.tx.is_none() || p.enqueued.load(Ordering::Acquire)!=0{return err("Bind route before PCM");}
        if p.notebook_post || dsp.channels as usize!=p.layout.channels{return err("AoIP route requires a core output layout");}
        let legacy=p.sink.lock().unwrap();let mut pool=p.pool_sink.lock().unwrap();
        if legacy.is_some() || pool.is_some(){return err("Output already configured");}
        let processor=crate::output_processing::Processor::new(dsp)?;
        let output=unsafe{crate::output_pool::aoip::RoutedOutput::attach(route,source as usize,dsp.channels as usize,p.capacity.saturating_sub(1)*p.n)}?;
        p.aoip=Some(output.metrics());
        *pool=Some(crate::output_pool::Sink{lsac_eq:None,process:None,native:Some(processor),destination:crate::output_pool::Destination::RoutedAoip(output),context:0});Ok(())
    })
}
#[cfg(feature="aoip-output")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_aoip_fault(p:*mut c_void)->u32 {
    if p.is_null(){return u32::MAX;}
    let p=unsafe{&*(p as *const Pipeline)};
    p.aoip.as_ref().map_or(0,|m|m.fault.load(Ordering::Acquire))
}
#[unsafe(no_mangle)]
/// Control caller only; do not race finish/destroy. Returns -1 if unsupported/failed.
pub unsafe extern "C" fn nsp_pipeline_telemetry_enable(p: *mut c_void, enabled: u32) -> i32 {
    if p.is_null() || enabled > 1 { return -1; }
    let p = unsafe { &*(p as *const Pipeline) };
    if p.worker.is_none() || !(p.telemetry.enable(enabled != 0) && p.object_telemetry.enable(enabled != 0)) { -1 } else { 0 }
}
#[unsafe(no_mangle)]
/// Control caller only. Returns required bytes including NUL; never truncates.
pub unsafe extern "C" fn nsp_pipeline_telemetry_read(p: *mut c_void, out: *mut u8, len: usize) -> usize {
    if p.is_null() { return 0; }
    catch_unwind(AssertUnwindSafe(|| {
        let p = unsafe { &*(p as *const Pipeline) };
        let json = p.telemetry.json();
        let needed = json.len() + 1;
        if !out.is_null() && len >= needed {
            let dest = unsafe { slice::from_raw_parts_mut(out, needed) };
            dest[..needed - 1].copy_from_slice(json.as_bytes()); dest[needed - 1] = 0;
        }
        needed
    })).unwrap_or(0)
}
#[unsafe(no_mangle)]
/// Non-RT caller only, serialized with finish/destroy. No consume on size query.
pub unsafe extern "C" fn nsp_pipeline_tap_read(p: *mut c_void, out: *mut u8, len: usize) -> usize {
    if p.is_null() { return 0; }
    catch_unwind(AssertUnwindSafe(|| {
        let p = unsafe { &*(p as *const Pipeline) };
        let dest = if out.is_null() { &mut [] } else { unsafe { slice::from_raw_parts_mut(out, len) } };
        p.telemetry.read(dest)
    })).unwrap_or(0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_object_tap_read(p: *mut c_void, out: *mut u8, len: usize) -> usize {
    if p.is_null() { return 0; }
    catch_unwind(AssertUnwindSafe(|| {
        let p = unsafe { &*(p as *const Pipeline) };
        let dest = if out.is_null() { &mut [] } else { unsafe { slice::from_raw_parts_mut(out, len) } };
        p.object_telemetry.read(dest)
    })).unwrap_or(0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create(
    n: u32,
    capacity: u32,
    grouped: u32,
    gain: u32,
    path: *const c_char,
) -> *mut c_void {
    if path.is_null() {
        return std::ptr::null_mut();
    }
    catch_unwind(|| {
        let path = unsafe { CStr::from_ptr(path) }
            .to_str()
            .map_err(|e| e.to_string())?;
        Pipeline::new(n, capacity as usize, grouped != 0, gain != 0, path)
            .map(|p| Box::into_raw(Box::new(p)) as *mut c_void)
    })
    .ok()
    .and_then(Result::ok)
    .unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
/// ROAR/OLR additive entry. scaled=1 preserves radius with a uniform reference divisor;
/// the legacy create entry retains direction semantics. No physical room model.
pub unsafe extern "C" fn nsp_pipeline_create_with_position_mapping(
    n: u32, capacity: u32, grouped: u32, gain: u32,
    scaled: u32, reference: f64, path: *const c_char,
) -> *mut c_void {
    if path.is_null() || scaled > 1 || (scaled == 0 && reference != 1.) { return std::ptr::null_mut(); }
    catch_unwind(|| {
        let path = unsafe { CStr::from_ptr(path) }.to_str().map_err(|e| e.to_string())?;
        Pipeline::with_position_mapping(n, capacity as usize, grouped != 0, gain != 0, scaled != 0, reference, path)
            .map(|p| Box::into_raw(Box::new(p)) as *mut c_void)
    }).ok().and_then(Result::ok).unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_spatial(n:u32,capacity:u32,dll:*const c_char,path:*const c_char)->*mut c_void{
    if dll.is_null()||path.is_null(){return std::ptr::null_mut();}
    catch_unwind(||{
        let dll=unsafe{CStr::from_ptr(dll)}.to_str().map_err(|e|e.to_string())?;
        let path=unsafe{CStr::from_ptr(path)}.to_str().map_err(|e|e.to_string())?;
        let engine=Backend::Lira(crate::spatial_input::Spatial::new(n,dll)?,Mapping::default());
        Pipeline::with_backend(n,capacity as usize,false,1.,path,engine).map(|p|Box::into_raw(Box::new(p)) as *mut c_void)
    }).ok().and_then(Result::ok).unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_submit(
    p: *mut c_void,
    seq: u64,
    count: u32,
    data: *const u8,
    len: usize,
) -> i32 {
    unsafe { submit_packet(p, 0, seq, count, data, len, 0, 0) }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_submit_timed(
    p: *mut c_void, seq: u64, count: u32, data: *const u8, len: usize, capture_ns: u64,
) -> i32 {
    if capture_ns == 0 || capture_ns > qpc_ns() { return -1; }
    unsafe { submit_packet(p, 0, seq, count, data, len, capture_ns, 0) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_input(p: *mut c_void, lane: u32) -> *mut c_void {
    if p.is_null() || lane as usize >= crate::unified_input::LANES {
        return std::ptr::null_mut();
    }
    let pipeline = unsafe { &*p.cast::<Pipeline>() };
    if !pipeline.unified {
        return std::ptr::null_mut();
    }
    std::ptr::from_ref(&pipeline.inputs[lane as usize]).cast_mut().cast()
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_submit_input(
    input: *mut c_void, seq: u64, count: u32, data: *const u8, len: usize,
) -> i32 {
    unsafe { nsp_pipeline_submit_input_timed(input, seq, count, data, len, 0) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_submit_input_timed(
    input: *mut c_void, seq: u64, count: u32, data: *const u8, len: usize, capture_ns: u64,
) -> i32 {
    unsafe { submit_input(input, seq, count, data, len, capture_ns, 0) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_submit_input_leased(
    input: *mut c_void, seq: u64, count: u32, data: *const u8, len: usize,
    capture_ns: u64, source_lease: u64,
) -> i32 {
    if source_lease == 0 { return -1; }
    unsafe { submit_input(input, seq, count, data, len, capture_ns, source_lease) }
}

unsafe fn submit_input(
    input: *mut c_void, seq: u64, count: u32, data: *const u8, len: usize,
    capture_ns: u64, source_lease: u64,
) -> i32 {
    if input.is_null() || (capture_ns != 0 && capture_ns > qpc_ns()) { return -1; }
    let input = unsafe { &*input.cast::<InputHandle>() };
    unsafe { submit_packet(input.pipeline as *mut c_void, input.lane, seq, count, data, len, capture_ns, source_lease) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_request_primary_lane(p: *mut c_void, lane: i32) -> i32 {
    let request = match lane {
        -1 => crate::unified_input::PRIMARY_DISABLE,
        lane if (0..crate::unified_input::PCM as i32).contains(&lane) => lane as usize,
        _ => return -1,
    };
    let Some(pipeline) = (unsafe { p.cast::<Pipeline>().as_ref() }) else { return -1 };
    let Some(wake) = &pipeline.render_wake else { return -1 };
    pipeline.primary_request.store(request, Ordering::Release);
    wake.notify();
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_unified_stats(p: *mut c_void, output: *mut u8, len: usize) -> usize {
    if p.is_null() || output.is_null() { return 0; }
    let pipeline = unsafe { &*p.cast::<Pipeline>() };
    if !pipeline.unified { return 0; }
    let stats = &pipeline.unified_stats;
    let spatial = std::array::from_fn::<_, { crate::unified_input::PCM }, _>(|i| stats.source_blocks[i].load(Ordering::Relaxed));
    let pcm_by_lane = std::array::from_fn::<_, { crate::unified_input::PCM_LANES }, _>(|i| stats.source_blocks[crate::unified_input::PCM + i].load(Ordering::Relaxed));
    let pcm: u64 = pcm_by_lane.iter().sum();
    let windows = stats.spatial_windows.each_ref().map(|count| count.load(Ordering::Relaxed));
    let incomplete = stats.spatial_incomplete.load(Ordering::Relaxed);
    let late = stats.spatial_late.load(Ordering::Relaxed);
    let duplicates = stats.spatial_duplicates.load(Ordering::Relaxed);
    let untimed = stats.spatial_untimed.load(Ordering::Relaxed);
    let closures = stats.spatial_window_closures.each_ref().map(|count| count.load(Ordering::Relaxed));
    let primary_same = stats.primary_same.each_ref().map(|count| count.load(Ordering::Relaxed));
    let primary_previous = stats.primary_previous.each_ref().map(|count| count.load(Ordering::Relaxed));
    let primary_dropped = stats.primary_dropped.each_ref().map(|count| count.load(Ordering::Relaxed));
    let late_input = stats.spatial_over_with_late_input.load(Ordering::Relaxed);
    let pool_max = stats.output_pool_acquire_max_ns.load(Ordering::Relaxed);
    let tap_truncated_records = stats.object_tap_truncated_records.load(Ordering::Relaxed);
    let tap_truncated_blocks = stats.object_tap_truncated_blocks.load(Ordering::Relaxed);
    let wake = pipeline.render_wake.as_ref().unwrap().stats();
    let wake_report = format!(
        "{{\"timerMode\":\"{}\",\"highResolutionCreateError\":{},\"standardCreateError\":{},\"timedWaits\":{},\"eventWakes\":{},\"timerWakes\":{},\"fallbackWaits\":{},\"fallbackTimeouts\":{},\"timerArmFailures\":{},\"waitFailures\":{},\"notifyFailures\":{},\"lastTimerArmError\":{},\"lastWaitError\":{},\"lastWaitResult\":{},\"lastNotifyError\":{},\"deadlineWakes\":{},\"deadlineOverrunNsTotal\":{},\"deadlineOverrunNsMax\":{}}}",
        wake.timer_mode, wake.high_resolution_create_error, wake.standard_create_error,
        wake.timed_waits, wake.event_wakes, wake.timer_wakes, wake.fallback_waits,
        wake.fallback_timeouts, wake.timer_arm_failures, wake.wait_failures, wake.notify_failures,
        wake.last_timer_arm_error, wake.last_wait_error, wake.last_wait_result, wake.last_notify_error,
        wake.deadline_wakes, wake.deadline_overrun_ns_total, wake.deadline_overrun_ns_max,
    );
    let (expansion_drops, prepared_space, prepared_pcm, object_budget_drops) = pipeline.expansion.as_ref().map_or((0, 8, 8, 0), |expansion| {
        let ready = expansion.ready.load(Ordering::Acquire);
        (expansion.skipped.load(Ordering::Relaxed), (ready & 0xff).count_ones(), (ready >> 8).count_ones(),
            expansion.object_budget_drops.load(Ordering::Relaxed))
    });
    let report = format!(
        "{{\"objectTapTruncatedRecords\":{tap_truncated_records},\"objectTapTruncatedBlocks\":{tap_truncated_blocks},\"expansionDrops\":{expansion_drops},\"objectBudgetDrops\":{object_budget_drops},\"preparedSpatialSlots\":{prepared_space},\"preparedPcmSlots\":{prepared_pcm},\"spatialBlocks\":{spatial:?},\"pcmBlocks\":{pcm},\"pcmBlocksByLane\":{pcm_by_lane:?},\"spatialSealBucketsUs200300400500Over\":{windows:?},\"spatialIncompleteBatches\":{incomplete},\"spatialLateBlocks\":{late},\"spatialDuplicateBlocks\":{duplicates},\"spatialUntimedBlocks\":{untimed},\"spatialWindowClosuresUs200300400500\":{closures:?},\"spatialOver500WithLateInput\":{late_input},\"outputPoolAcquireMaxNs\":{pool_max},\"renderWake\":{wake_report},\"primarySameByLane\":{primary_same:?},\"primaryPreviousByLane\":{primary_previous:?},\"primaryDroppedByLane\":{primary_dropped:?},\"mixedBlocks\":{},\"discardedBlocks\":{},\"queueRejected\":{},\"rendererFailed\":{},\"pcmUpmixLoaded\":{},\"upmixedPcmBlocks\":{},\"upmixedWithSpatialBlocks\":{},\"pcmUpmixFallbackBlocks\":{},\"pcmUpmixWorker\":{},\"limiter\":{}}}",
        stats.mixed_blocks.load(Ordering::Relaxed),
        stats.discarded.load(Ordering::Relaxed),
        pipeline.dropped.load(Ordering::Relaxed),
        pipeline.failed.load(Ordering::Relaxed) != 0,
        stats.upmix_loaded,
        stats.upmixed.load(Ordering::Relaxed),
        stats.upmixed_with_spatial.load(Ordering::Relaxed),
        stats.upmix_fallback.load(Ordering::Relaxed),
        if stats.upmix_loaded { crate::joint::status() } else { "null".into() },
        crate::limiter_control::status(),
    );
    if report.len() > len { return 0; }
    unsafe { std::ptr::copy_nonoverlapping(report.as_ptr(), output, report.len()) };
    report.len()
}

unsafe fn submit_packet(p: *mut c_void, lane: usize, seq: u64, count: u32, data: *const u8, len: usize, capture_ns: u64, source_lease: u64) -> i32 {
    status(|| {
        if p.is_null() || data.is_null() {
            return err("Null packet");
        }
        let p = unsafe { &*(p as *const Pipeline) };
        if count == 0
            || count as usize > if lane < crate::unified_input::PCM { SLOTS } else { PCM_CHANNELS }
            || len != count as usize * (32 + p.n * 4)
            || p.failed.load(Ordering::Acquire) != 0
        {
            return err("Invalid packet or worker failed");
        }
        if p.expansion.as_ref().is_some_and(|e| !e.admits(lane)) {
            return Ok(());
        }
        let mut owned = p.free.lock().unwrap().try_recv().map_err(|_| {
            p.dropped.fetch_add(1, Ordering::Relaxed);
            "Native input buffer pool exhausted".to_string()
        })?;
        owned.extend_from_slice(unsafe { slice::from_raw_parts(data, len) });
        let packet = Packet {
            lane,
            seq,
            count: count as usize,
            data: owned,
            received: Instant::now(),
            capture_ns,
            source_lease,
            upmix_ticket: None,
            upmix_eligible: false,
        };
        if lane == crate::unified_input::PCM {
            if let Some(trace) = p.trace.get() { trace.mark(TraceEvent::PcmSubmit, seq, capture_ns, 0); }
        }
        match p.tx.as_ref().ok_or("Pipeline finished")?.try_send(packet) {
            Ok(()) => {
                p.enqueued.fetch_add(1, Ordering::Relaxed);
                if let Some(wake) = &p.render_wake { wake.notify(); }
                Ok(())
            }
            Err(TrySendError::Full(mut pkt)) | Err(TrySendError::Disconnected(mut pkt)) => {
                pkt.data.clear();
                let _ = p.free_tx.try_send(pkt.data);
                p.dropped.fetch_add(1, Ordering::Relaxed);
                err("Native queue full or disconnected")
            }
        }
    })
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_finish(p: *mut c_void, out: *mut u8, len: usize) -> usize {
    if p.is_null() {
        return 0;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let p = unsafe { &mut *(p as *mut Pipeline) };
        if p.worker.is_some() {
            p.finish();
        }
        let needed = p.report.len() + 1;
        if !out.is_null() && len >= needed {
            let dest = unsafe { slice::from_raw_parts_mut(out, len) };
            dest[..needed - 1].copy_from_slice(p.report.as_bytes());
            dest[needed - 1] = 0;
        }
        needed
    }))
    .unwrap_or(0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_destroy(p: *mut c_void) {
    if !p.is_null() {
        let mut p = unsafe { Box::from_raw(p as *mut Pipeline) };
        if p.worker.is_some() {
            p.finish();
        }
    }
}

/// Additive layout-aware creation. Legacy exports retain exactly 7.1.4.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_layout(n:u32,capacity:u32,code:u32,scaled:u32,reference:f64,dll:*const c_char,path:*const c_char)->*mut c_void{
    if path.is_null() || scaled>1 || (scaled==0 && reference!=1.) {return std::ptr::null_mut();}
    catch_unwind(||->Result<*mut c_void,String>{
        let path=unsafe{CStr::from_ptr(path)}.to_str().map_err(|e|e.to_string())?;
        let engine=if dll.is_null(){Backend::Roar(RoarMode::Olr(Engine::new_layout(n,48000,false,false,scaled!=0,reference,code)?))}
            else {let dll=unsafe{CStr::from_ptr(dll)}.to_str().map_err(|e|e.to_string())?;Backend::Lira(crate::spatial_input::Spatial::new_layout(n,dll,code)?,Mapping::default())};
        Pipeline::with_backend(n,capacity as usize,scaled!=0,reference,path,engine).map(|p|Box::into_raw(Box::new(p)).cast())
    }).ok().and_then(Result::ok).unwrap_or(std::ptr::null_mut())
}

#[unsafe(no_mangle)]
/// Live host entry: no recording by default; explicit recording is best effort.
pub unsafe extern "C" fn nsp_pipeline_create_live(n:u32,capacity:u32,code:u32,scaled:u32,reference:f64,dll:*const c_char,path:*const c_char,record:u32)->*mut c_void{
    if path.is_null() || record>1 || scaled>1 || (scaled==0 && reference!=1.) {return std::ptr::null_mut();}
    catch_unwind(||->Result<*mut c_void,String>{
        let path=unsafe{CStr::from_ptr(path)}.to_str().map_err(|e|e.to_string())?;
        let engine=if dll.is_null(){Backend::Roar(RoarMode::Olr(Engine::new_layout(n,48000,false,false,scaled!=0,reference,code)?))}
            else{let dll=unsafe{CStr::from_ptr(dll)}.to_str().map_err(|e|e.to_string())?;Backend::Lira(crate::spatial_input::Spatial::new_layout(n,dll,code)?,Mapping::default())};
        Pipeline::with_recording(n,capacity as usize,scaled!=0,reference,path,engine,record!=0,false).map(|p|Box::into_raw(Box::new(p)).cast())
    }).ok().and_then(Result::ok).unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_progress(p:*mut c_void)->u64{
    if p.is_null(){return 0;}unsafe{&*(p as *const Pipeline)}.progress.load(Ordering::Acquire)
}

/// Experimental 7.1.4 virtual directions -> explicit measured-device FIR -> L/R.
/// Separate entry keeps every existing layout and ABI unchanged.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_notebook_live(n:u32,capacity:u32,scaled:u32,reference:f64,filters:*const c_char,path:*const c_char,record:u32)->*mut c_void {
    unsafe{nsp_pipeline_create_notebook_live_v2(n,capacity,scaled,reference,filters,path,record,0,0.,130.)}
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_notebook_live_v2(n:u32,capacity:u32,scaled:u32,reference:f64,filters:*const c_char,path:*const c_char,record:u32,bass:u32,amount:f64,highpass:f64)->*mut c_void {
    if filters.is_null() || path.is_null() || record>1 || bass>1 || scaled>1 || (scaled==0 && reference!=1.) {return std::ptr::null_mut();}
    catch_unwind(||->Result<*mut c_void,String>{
        let file=unsafe{CStr::from_ptr(filters)}.to_str().map_err(|e|e.to_string())?;
        let path=unsafe{CStr::from_ptr(path)}.to_str().map_err(|e|e.to_string())?;
        let engine=Engine::new_layout(n,48000,false,false,scaled!=0,reference,714)?;
        let backend=Backend::Notebook(Box::new(Backend::Roar(RoarMode::Olr(engine))),crate::notebook::Post::new(file,n as usize,bass!=0,amount,highpass,path)?);
        Pipeline::with_recording(n,capacity as usize,scaled!=0,reference,path,backend,record!=0,false).map(|p|Box::into_raw(Box::new(p)).cast())
    }).ok().and_then(|r|r.map_err(|e|eprintln!("Notebook pipeline initialization failed: {e}")).ok()).unwrap_or(std::ptr::null_mut())
}

fn lsac_backend(core: Backend, filters: &str, n: usize, bass: bool, amount: f64, highpass: f64) -> Result<Backend, String> {
    let stage = nsp_lsac_adapter::Stage::new(filters, n, bass, amount, highpass)?;
    Ok(Backend::Lsac(Box::new(core), stage, vec![0.; n * 12], vec![0.; n * 12]))
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_lsac_live(
    n: u32, capacity: u32, scaled: u32, reference: f64, renderer: *const c_char,
    filters: *const c_char, path: *const c_char, record: u32, bass: u32, amount: f64, highpass: f64,
) -> *mut c_void {
    if filters.is_null() || path.is_null() || record > 1 || bass > 1 || scaled > 1 ||
        (renderer.is_null() && scaled == 0 && reference != 1.) {
        return std::ptr::null_mut();
    }
    catch_unwind(|| -> Result<*mut c_void, String> {
        let text = |ptr| unsafe { CStr::from_ptr(ptr) }.to_str().map_err(|e| e.to_string());
        let core = if renderer.is_null() {
            Backend::Roar(RoarMode::Olr(Engine::new_layout(n, 48000, false, false, scaled != 0, reference, 714)?))
        } else {
            Backend::Lira(crate::spatial_input::Spatial::new_layout(n, text(renderer)?, 714)?, Mapping::default())
        };
        let backend = lsac_backend(core, text(filters)?, n as usize, bass != 0, amount, highpass)?;
        Pipeline::with_recording(n, capacity as usize, scaled != 0, reference, text(path)?, backend, record != 0, false)
            .map(|p| Box::into_raw(Box::new(p)).cast())
    }).ok().and_then(|r| r.map_err(|e| eprintln!("LSAC pipeline initialization failed: {e}")).ok())
        .unwrap_or(std::ptr::null_mut())
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_unified(
    capacity: u32, scaled: u32, reference: f64, filters: *const c_char,
    path: *const c_char, record: u32, bass: u32, amount: f64, highpass: f64,
) -> *mut c_void {
    unsafe { create_unified(capacity, scaled, reference, filters, path, record, bass, amount, highpass, 714, None, true, false) }
}

#[repr(C)]
pub struct UnifiedUpmix {
    dll: *const c_char,
    cpu_fallback: *const c_char,
    model: *const c_char,
    freesurround: *const c_char,
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_unified_v2(
    capacity: u32, scaled: u32, reference: f64, filters: *const c_char,
    path: *const c_char, record: u32, bass: u32, amount: f64, highpass: f64,
    code: u32, upmix: *const UnifiedUpmix,
) -> *mut c_void {
    unsafe { create_unified(capacity, scaled, reference, filters, path, record, bass, amount, highpass, code, upmix.as_ref(), false, false) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_unified_v3(
    capacity: u32, scaled: u32, reference: f64, filters: *const c_char,
    path: *const c_char, record: u32, bass: u32, amount: f64, highpass: f64,
    code: u32, upmix: *const UnifiedUpmix, spatial_profile: u32,
) -> *mut c_void {
    if spatial_profile > 1 {
        return std::ptr::null_mut();
    }
    unsafe { create_unified(capacity, scaled, reference, filters, path, record, bass, amount, highpass, code, upmix.as_ref(), false, spatial_profile == 1) }
}

unsafe fn create_unified(
    capacity: u32, scaled: u32, reference: f64, filters: *const c_char,
    path: *const c_char, record: u32, bass: u32, amount: f64, highpass: f64,
    code: u32, upmix: Option<&UnifiedUpmix>, legacy_upmix: bool, compact: bool,
) -> *mut c_void {
    if path.is_null() || record > 1 || bass > 1 || scaled > 1 || (scaled == 0 && reference != 1.) {
        return std::ptr::null_mut();
    }
    catch_unwind(|| -> Result<*mut c_void, String> {
        let text = |ptr| unsafe { CStr::from_ptr(ptr) }.to_str().map_err(|e| e.to_string());
        let path = text(path)?;
        if (!filters.is_null() || upmix.is_some()) && code != 714 {
            return err("LSAC adaptation and Joint upmix require a 7.1.4 bus");
        }
        let mut engine = Engine::new_layout_with_storage(480, 48000, false, false, scaled != 0, reference, code, !compact)?;
        let expansion = if compact {
            let (expansion, prepared) = Expansion::new(480);
            engine.enable_compact(prepared, expansion.object_budget_drops.clone())?;
            Some(expansion)
        } else {
            engine.enable_unified();
            None
        };
        let mut backend = Backend::Roar(RoarMode::Olr(engine));
        if !filters.is_null() {
            backend = lsac_backend(backend, text(filters)?, 480, bass != 0, amount, highpass)?;
        }
        let joint = if let Some(cfg) = upmix {
            if cfg.dll.is_null() || cfg.model.is_null() || cfg.freesurround.is_null() {
                return err("Incomplete Joint configuration");
            }
            let fallback = if cfg.cpu_fallback.is_null() { None } else { Some(text(cfg.cpu_fallback)?) };
            Some(crate::joint::Joint::open_unified(text(cfg.dll)?, fallback, text(cfg.model)?, text(cfg.freesurround)?)?)
        } else if legacy_upmix {
            crate::joint::Joint::configured_unified()?
        } else {
            None
        };
        let mut pipeline = Box::new(Pipeline::with_ingress(
            480, capacity as usize, scaled != 0, reference, path, backend, record != 0, false, true, joint,
        )?);
        pipeline.expansion = expansion;
        let address = std::ptr::from_ref(pipeline.as_ref()) as usize;
        for input in &mut pipeline.inputs { input.pipeline = address; }
        Ok(Box::into_raw(pipeline).cast())
    }).ok().and_then(|r| r.map_err(|e| eprintln!("Unified pipeline initialization failed: {e}")).ok())
        .unwrap_or(std::ptr::null_mut())
}

/// LIRA virtual 7.1.4 followed by the exact same notebook post-render stage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_create_notebook_lira_live(n:u32,capacity:u32,dll:*const c_char,filters:*const c_char,path:*const c_char,record:u32,bass:u32,amount:f64,highpass:f64)->*mut c_void {
    if dll.is_null()||filters.is_null()||path.is_null()||record>1||bass>1{return std::ptr::null_mut();}
    catch_unwind(||->Result<*mut c_void,String>{
        let text=|ptr|unsafe{CStr::from_ptr(ptr)}.to_str().map_err(|e|e.to_string());
        let core=Backend::Lira(crate::spatial_input::Spatial::new_layout(n,text(dll)?,714)?,Mapping::default());
        let backend=Backend::Notebook(Box::new(core),crate::notebook::Post::new(text(filters)?,n as usize,bass!=0,amount,highpass,text(path)?)?);
        Pipeline::with_recording(n,capacity as usize,false,1.,text(path)?,backend,record!=0,false).map(|p|Box::into_raw(Box::new(p)).cast())
    }).ok().and_then(Result::ok).unwrap_or(std::ptr::null_mut())
}

#[cfg(test)]
mod source_lease_tests {
    use super::*;

    fn object(flags: u32) -> Vec<u8> {
        let mut bytes = Vec::new();
        for field in [9, 480, 1, flags] {
            bytes.extend(field.to_le_bytes());
        }
        for field in [0.0f32, 0.0, -1.0, 1.0] {
            bytes.extend(field.to_le_bytes());
        }
        bytes.resize(32 + 480 * 4, 0);
        bytes
    }

    #[test]
    fn full_compact_input_reports_object_tap_truncation() {
        let mut engine = Engine::new_layout_with_storage(480, 48000, false, false, false, 1.0, 714, false).unwrap();
        let (_, prepared) = mpsc::channel();
        engine.enable_compact(prepared, Arc::new(AtomicU64::new(0))).unwrap();
        let storage = &mut engine.compact.as_mut().unwrap().0;
        for lane in 4..8 {
            for kind in [LaneKind::Bed, LaneKind::Pcm] {
                assert!(storage.install_lane(PreparedLane::new(kind, lane, 480).unwrap()).is_ok());
            }
        }
        let payloads: Vec<_> = (0..crate::unified_input::LANES).map(|lane| {
            let mut types = BED_TYPES.to_vec();
            if lane == 0 { types.extend([1; COMPACT_OBJECTS]); }
            let count = types.len();
            let mut bytes = Vec::new();
            for (ident, typ) in types.into_iter().enumerate() {
                for value in [ident as u32, 480, typ, 4] { bytes.extend(value.to_le_bytes()); }
                for value in [0.0f32, 0., -1., 1.] { bytes.extend(value.to_le_bytes()); }
            }
            bytes.resize(count * (32 + 480 * 4), 0);
            (count, bytes)
        }).collect();
        let sources: Vec<_> = payloads.iter().enumerate().map(|(lane, (count, data))|
            (lane, *count, data.as_slice(), 1)).collect();
        let control = telemetry::Control::objects(true);
        assert!(control.enable(true));
        let mut producer = control.producer();
        engine.process_sources(0, &sources, Some(&mut producer), true, None).unwrap();
        assert_eq!(engine.object_tap_truncated_records, 96);
        assert_eq!(engine.object_tap_truncated_blocks, 1);
        let mut wire = vec![0; telemetry::OBJECT_SIZE];
        assert_eq!(control.read(&mut wire), telemetry::OBJECT_SIZE);
        assert_eq!(u16::from_le_bytes(wire[6..8].try_into().unwrap()), 160);
    }

    #[test]
    fn compact_budget_rejects_a_new_object_before_displacing_live_objects() {
        let mut engine = Engine::new_layout_with_storage(480, 48000, false, false, false, 1.0, 714, false).unwrap();
        let (_expansion, prepared) = Expansion::new(480);
        let drops = Arc::new(AtomicU64::new(0));
        engine.enable_compact(prepared, drops.clone()).unwrap();
        let one = object(4);
        let mut full = Vec::new();
        for ident in 0..COMPACT_OBJECTS {
            full.extend_from_slice(&(ident as u32).to_le_bytes());
            full.extend_from_slice(&one[4..32]);
        }
        full.resize(COMPACT_OBJECTS * one.len(), 0);
        // Reproduce two concurrent sources crossing the old shared limit.
        let mut other = Vec::new();
        for ident in 0..15u32 {
            other.extend_from_slice(&ident.to_le_bytes());
            other.extend_from_slice(&one[4..32]);
        }
        other.resize(15 * one.len(), 0);
        let mut main = full[..32 * 32].to_vec();
        main.resize(32 * one.len(), 0);
        engine.process_sources(0, &[(0, 15, &other, 1), (1, 32, &main, 1)], None, true, None).unwrap();
        assert_eq!(engine.active[..COMPACT_OBJECTS].iter().filter(|&&generation| generation != 0).count(), 47);
        assert_eq!(drops.load(Ordering::Relaxed), 0);

        let mut engine = Engine::new_layout_with_storage(480, 48000, false, false, false, 1.0, 714, false).unwrap();
        let (_expansion, prepared) = Expansion::new(480);
        engine.enable_compact(prepared, drops.clone()).unwrap();
        engine.process_sources(1, &[(1, COMPACT_OBJECTS, &full, 1)], None, true, None).unwrap();
        engine.process_sources(2, &[(0, 1, &one, 1), (1, COMPACT_OBJECTS, &full, 1)], None, true, None).unwrap();
        assert!(engine.active[..COMPACT_OBJECTS].iter().all(|&generation| generation == 1));
        assert_eq!(drops.load(Ordering::Relaxed), 1);

        // Leave one capture cycle empty, then continue with no new position flag.
        engine.process_sources(3, &[], None, true, None).unwrap();
        assert!(engine.active[..COMPACT_OBJECTS].iter().all(|&generation| generation == 1));
        for prop in full[..32 * COMPACT_OBJECTS].chunks_exact_mut(32) {
            prop[12..16].copy_from_slice(&0u32.to_le_bytes());
        }
        engine.process_sources(4, &[(1, COMPACT_OBJECTS, &full, 1)], None, true, None).unwrap();
        assert!(engine.active[..COMPACT_OBJECTS].iter().all(|&generation| generation == 1));
    }

    #[test]
    fn source_replacement_clears_only_its_object_position_and_keeps_generations_monotonic() {
        let mut engine = Engine::new(480, 48000, false, false, false, 1.0).unwrap();
        engine.enable_unified();
        let positioned = object(4);
        let unpositioned = object(0);
        engine.process_sources(0, &[(0, 1, &positioned, 1), (1, 1, &positioned, 1)], None, true, None).unwrap();
        assert_eq!(engine.active[0], 1);
        assert_eq!(engine.active[SLOTS], 1);

        engine.process_sources(1, &[(0, 1, &unpositioned, 2), (1, 1, &unpositioned, 1)], None, true, None).unwrap();
        assert_eq!(engine.states[0].generation, 2);
        assert!(engine.states[0].position.is_none());
        assert_eq!(engine.active[0], 0);
        assert_eq!(engine.active[SLOTS], 1);
        assert_eq!(engine.states[SLOTS].position, Some([0., 0., -1.]));

        engine.process_sources(2, &[(0, 1, &positioned, 1), (0, 1, &positioned, 2), (1, 1, &unpositioned, 1)], None, true, None).unwrap();
        assert_eq!(engine.active[0], 2);
        assert_eq!(engine.active[SLOTS], 1);
    }
}
