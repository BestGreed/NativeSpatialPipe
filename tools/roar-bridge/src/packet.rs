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
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, SyncSender, TrySendError},
    },
    thread::{self, JoinHandle},
    time::Instant,
};

const SLOTS: usize = MAX_OBJECTS;
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
    states: [State; SLOTS],
    active: [u64; SLOTS],
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
        oar(rdr.set_object_position_metadata_unit_to_process(0))?;
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
            states: [State::default(); SLOTS],
            active: [0; SLOTS],
            last: None,
            bed_active: false,
            signature: None,
            pcm: vec![0.; SLOTS * n as usize],
            bed: vec![0.; 12 * n as usize],
            output: vec![0.; layout.labels.len() * n as usize],
            positions: ObjectPosition::Cartesian(Vec::with_capacity(SLOTS)),
            one_position: ObjectPosition::Cartesian(Vec::with_capacity(1)),
            mapping: Mapping::default(),
            decode_ns: 0,
            metadata_ns: 0,
            dsp_ns: 0,
        })
    }
    fn process(&mut self, seq: u64, count: usize, data: &[u8]) -> Result<&[f32], String> {
        self.process_tapped(seq, count, data, None)
    }
    fn process_tapped(&mut self, seq: u64, count: usize, data: &[u8], object_tap: Option<&mut telemetry::Producer>) -> Result<&[f32], String> {
        let begin = Instant::now();
        let input = CaptureInput::new(data, count, self.n).map_err(str::to_string)?;
        if self
            .last
            .is_some_and(|last| last.checked_add(1) != Some(seq))
        {
            return err("Sequence discontinuity: rebuild native pipeline");
        }
        // Decode/validate all bytes before changing any metadata or renderer state.
        let mut props = [Property::default(); SLOTS];
        for (slot, prop) in props.iter_mut().enumerate().take(count) {
            *prop = input.property(slot).map_err(str::to_string)?;
            if prop.typ != 1 && !BED_TYPES.contains(&prop.typ) {
                return err("Unsupported static type");
            }
            for (dst, b) in self.pcm[slot * self.n..(slot + 1) * self.n]
                .iter_mut()
                .zip(input.pcm_bytes(slot).chunks_exact(4))
            {
                let v = f32::from_le_bytes(b.try_into().unwrap());
                if !v.is_finite() {
                    return err("Nonfinite PCM");
                }
                *dst = if self.apply_gain { v * prop.gain } else { v };
                if !dst.is_finite() {
                    return err("Nonfinite gain-adjusted PCM");
                }
            }
        }
        self.decode_ns = begin.elapsed().as_nanos() as u64;
        let meta_begin = Instant::now();
        let mut dynamic = [0usize; SLOTS];
        let mut desired = [false; SLOTS];
        let mut num_dynamic = 0;
        let mut seen_bed = [false; 12];
        let mut retire = [false; SLOTS];
        self.bed.fill(0.);
        if let ObjectPosition::Cartesian(p) = &mut self.positions {
            p.clear();
        }
        // Effective state and source->OLR mapping below remain backend policy.
        for (slot, &prop) in props.iter().enumerate().take(count) {
            let Property { typ, flags, .. } = prop;
            let s = &mut self.states[slot];
            let (created, retired, updated) = s.observe(prop);
            self.mapping.creates += u64::from(created);
            self.mapping.retires += u64::from(retired);
            self.mapping.updates += u64::from(updated);
            retire[slot] = flags & 8 != 0;
            if typ == 1 {
                if let Some(position) = s.position {
                    let [x, y, z] = coordinates(position, self.scaled, self.reference);
                    if let ObjectPosition::Cartesian(p) = &mut self.positions {
                        p.push(CartesianCoordinate { x, y, z });
                    }
                    dynamic[num_dynamic] = slot;
                    desired[slot] = true;
                    num_dynamic += 1;
                } else {
                    self.mapping.unknown += 1;
                    if self.pcm[slot * self.n..(slot + 1) * self.n]
                        .iter()
                        .any(|&v| v != 0.)
                    {
                        return err("Unknown position with nonzero PCM");
                    }
                }
            } else {
                let index = BED_TYPES.iter().position(|&t| t == typ).unwrap();
                if seen_bed[index] {
                    return err("Duplicate static type");
                }
                seen_bed[index] = true;
                self.bed[index * self.n..(index + 1) * self.n]
                    .copy_from_slice(&self.pcm[slot * self.n..(slot + 1) * self.n]);
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
                        &AudioElementConfig::ObjectBased(ObjectBasedConfig {
                            num_objects: num_dynamic as u32,
                            rendering_config: None,
                        }),
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
            for slot in 0..SLOTS {
                if self.active[slot] != 0
                    && (!desired[slot] || self.active[slot] != self.states[slot].generation)
                {
                    oar(self.rdr.remove_element(slot as u32 + 1))?;
                    self.active[slot] = 0;
                }
            }
            for &slot in &dynamic[..num_dynamic] {
                if self.active[slot] == 0 {
                    oar(self.rdr.add_element(
                        self.group,
                        slot as u32 + 1,
                        &AudioElementConfig::ObjectBased(ObjectBasedConfig {
                            num_objects: 1,
                            rendering_config: None,
                        }),
                    ))?;
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
                    layout: Layout::Layout714,
                    downmix_info: None,
                    rendering_config: None,
                }),
            ))?;
            self.bed_active = true;
        }
        if !bed_present && self.bed_active {
            oar(self.rdr.remove_element(BED_ID))?;
            self.bed_active = false;
        }
        self.metadata_ns = meta_begin.elapsed().as_nanos() as u64;
        let dsp_begin = Instant::now();
        self.output.fill(0.);
        if num_dynamic > 0 || bed_present {
            let mono: [&[f32]; SLOTS] =
                std::array::from_fn(|i| &self.pcm[i * self.n..(i + 1) * self.n]);
            let bed: [&[f32]; 12] =
                std::array::from_fn(|i| &self.bed[i * self.n..(i + 1) * self.n]);
            let mut grouped_channels = [&[][..]; SLOTS];
            for (i, &slot) in dynamic[..num_dynamic].iter().enumerate() {
                grouped_channels[i] = mono[slot];
            }
            let dummy = oar(PlanarBufferRef::new(&mono[0..1], 1, self.frames))?;
            let mut inputs = [(0, dummy); SLOTS + 1];
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
            oar(self.rdr.render(
                &inputs[..len],
                &mut oar(PlanarBufferMut::new(&mut out[..channels], channels, self.frames))?,
            ))?;
        }
        self.dsp_ns = dsp_begin.elapsed().as_nanos() as u64;
        if !self.output.iter().all(|v| v.is_finite()) {
            return err("Nonfinite rendered output");
        }
        if let Some(tap) = object_tap.filter(|tap| tap.active()) {
            tap.publish_custom(|epoch, wire| {
                telemetry::header(wire, b"NSPO", count as u16, epoch, seq);
                for slot in 0..count {
                    let s = self.states[slot]; let at = 32 + slot * (48 + 480 * 4);
                    let meta = &mut wire[at..at+48];
                    for (index, value) in [slot as u32, s.ident, s.typ, props[slot].flags].iter().enumerate() {
                        meta[index*4..index*4+4].copy_from_slice(&value.to_le_bytes());
                    }
                    meta[16..24].copy_from_slice(&s.generation.to_le_bytes());
                    for (i,value) in s.position.unwrap_or([0.;3]).iter().enumerate() {
                        meta[24+i*4..28+i*4].copy_from_slice(&(*value as f32).to_le_bytes());
                    }
                    meta[36..40].copy_from_slice(&(s.position.is_some() as u32).to_le_bytes());
                    meta[40..44].copy_from_slice(&props[slot].gain.to_le_bytes());
                    meta[44..48].copy_from_slice(&(self.apply_gain as u32).to_le_bytes());
                    let pcm = &self.pcm[slot*480..(slot+1)*480];
                    let bytes = unsafe { slice::from_raw_parts(pcm.as_ptr() as *const u8, 480*4) };
                    wire[at+48..at+48+480*4].copy_from_slice(bytes);
                }
                32 + count * (48 + 480 * 4)
            });
        }
        for (slot, s) in self.states.iter_mut().enumerate() {
            if s.present && (slot >= count || retire[slot]) {
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
        Ok(&self.output)
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
struct Packet {
    seq: u64,
    count: usize,
    data: Vec<u8>,
    received: Instant,
    capture_ns: u64,
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
        Self {
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
            telemetry_publish: Timing::new(),
            capture_publish: Timing::new(),
            writer: WriterStats::new(),
            mmcss_requested: false, mmcss_registered: false, mmcss_priority: false, mmcss_reverted: false, thread_id: 0,
        }
    }
}
// ROAR is the backend; this integration currently exposes only its OLR mode.
enum RoarMode { Olr(Engine) }
enum Backend { Roar(RoarMode), Lira(crate::spatial_input::Spatial, Mapping) }
impl Backend {
    fn layout(&self)->crate::layouts::Preset {match self{Self::Roar(RoarMode::Olr(e))=>e.layout,Self::Lira(e,_)=>crate::layouts::get(e.layout_code).unwrap()}}
    fn channels(&self)->usize{self.layout().labels.len()}
    fn process_into<'a>(&'a mut self,seq:u64,count:usize,data:&[u8],object_tap:&mut telemetry::Producer,out:&mut [f32])->Result<Option<&'a [f32]>,String>{
        let channels=self.channels();
        match self {
            Self::Roar(RoarMode::Olr(e))=>{
                let pcm=e.process_tapped(seq,count,data,Some(object_tap))?;
                for i in 0..out.len()/channels { for c in 0..channels { out[i*channels+c]=pcm[c*(out.len()/channels)+i]; } }
                Ok(Some(pcm))
            },
            Self::Lira(e,m)=>{
                e.process_into(seq,count,data,out)?;
                m.packets+=1;m.frames+=(out.len()/channels) as u64;
                Ok(None)
            }
        }
    }
    fn timing(&self)->(u64,u64,u64){match self{Self::Roar(RoarMode::Olr(e))=>(e.decode_ns,e.metadata_ns,e.dsp_ns),_ =>(0,0,0)}}
    fn finish(&mut self)->Result<Vec<f32>,String>{match self{Self::Roar(RoarMode::Olr(_))=>Ok(Vec::new()),Self::Lira(e,_)=>e.finish()}}
    fn mapping(self)->Mapping{match self{Self::Roar(RoarMode::Olr(mut e))=>{e.mapping.retires+=e.states.iter().filter(|s|s.present).count() as u64;e.mapping},Self::Lira(_,m)=>m}}
}
struct Pipeline {
    layout: crate::layouts::Preset,
    progress: Arc<AtomicU64>,
    recording: Arc<Mutex<String>>,
    lira: bool,
    scaled: bool,
    reference: f64,
    sink: Arc<Mutex<Option<OutputSink>>>,
    pool_sink: Arc<Mutex<Option<crate::output_pool::Sink>>>,
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
    fn with_recording(n:u32,capacity:usize,scaled:bool,reference:f64,path:&str,mut engine:Backend,record:bool,reliable:bool)->Result<Self,String>{
        if !(1..=256).contains(&capacity) {return err("Queue capacity");}
        let lira=matches!(engine,Backend::Lira(..));
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
        let renderer_mmcss = std::env::var("NSP_RENDER_MMCSS").map(|s| s == "1").unwrap_or(false);
        let writer = thread::spawn(move || -> Result<WriterStats, String> {
            let mut stats = WriterStats::new();
            stats.thread_id = unsafe { GetCurrentThreadId() };
            let mut task = RenderTask { handle: std::ptr::null_mut() };
            while let Ok(packet) = out_rx.recv() {
                let begin = Instant::now();
                if packet.capture_ns != 0 { stats.queue.add(packet.queued.elapsed().as_nanos() as u64); }
                let mut block = packet.data;
                let buf = &block.data;
                // Reliable output boundary, not a diagnostic tap. Legacy sinks
                // borrow synchronously; pool sinks transfer a releasable lease.
                let legacy_sink = *writer_sink.lock().unwrap();
                let pool_sink = *writer_pool_sink.lock().unwrap();
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
                if let Some(sink) = pool_sink {
                    let frames = sink.process(&mut block, false)?;
                    sink.publish(block, stats.frames - frames as u64, frames, false)?;
                } else {
                    let _ = out_free_tx.send(block);
                }
            }
            if let Some(sink) = *writer_pool_sink.lock().unwrap() {
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
            while stats.error.is_none() {
                let Ok(mut packet) = rx.recv() else { break; };
                let begin = Instant::now();
                stats.queue.add(packet.received.elapsed().as_nanos() as u64);
                let result = (|| -> Result<(), String> {
                    let mut out = out_free_rx.lock().unwrap()
                        .try_recv()
                        .map_err(|_| "Writer buffer pool exhausted".to_string())?;
                    out.data.resize(n as usize*channels,0.);
                    let planar = engine.process_into(packet.seq, packet.count, &packet.data, &mut object_tap, &mut out.data)?;
                    let render_ns = if packet.capture_ns != 0 { qpc_ns() } else { 0 };
                    worker_progress.fetch_add(out.data.len() as u64*4,Ordering::Release);
                    if packet.capture_ns != 0 { stats.capture_render.add(render_ns.saturating_sub(packet.capture_ns)); }
                    // Telemetry is a separate bounded diagnostic copy. LIRA's
                    // reliable PCM already lives in the writer's output pool.
                    if tap.active() {
                        let publish_begin = Instant::now();
                        if let Some(pcm)=planar {tap.publish(packet.seq,pcm);} else {tap.publish_interleaved(packet.seq,&out.data);}
                        stats.telemetry_publish.add(publish_begin.elapsed().as_nanos() as u64);
                        if packet.capture_ns != 0 { stats.capture_publish.add(qpc_ns().saturating_sub(packet.capture_ns)); }
                    }
                    out_tx
                        .try_send(OutputPacket { data: out, sequence: packet.seq, capture_ns: packet.capture_ns, render_ns, queued: Instant::now() })
                        .map_err(|_| "Writer queue full or failed".to_string())?;
                    Ok(())
                })();
                packet.data.clear();
                let _ = worker_free.try_send(packet.data);
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
            stats.mapping = engine.mapping();
            stats
        });
        Ok(Self {
            progress,recording:recording_report,
            layout,
            lira,
            scaled,
            reference,
            sink,
            pool_sink,
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
        })
    }
    fn finish(&mut self) {
        self.tx.take();
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
            && enqueued == stats.processed
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
        self.report.push_str(&format!(",\"outputLayout\":\"{}\",\"outputChannels\":{}",self.layout.id,self.layout.labels.len()));
        self.report.push_str(&format!(",\"renderer\":\"{}\",\"rendererMode\":{},\"rendererDelayFrames\":{},\"rendererTailFrames\":{},\"outputFrames\":{}",if self.lira {"lira"} else {"roar"},if self.lira {"null"} else {"\"olr\""},if self.lira {512} else {0},stats.tail_frames,stats.writer.frames));
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
        *pool = Some(crate::output_pool::Sink { process: process.unwrap(), publish: publish.unwrap(), context: context as usize });
        Ok(())
    })
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
    unsafe { submit_packet(p, seq, count, data, len, 0) }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_pipeline_submit_timed(
    p: *mut c_void, seq: u64, count: u32, data: *const u8, len: usize, capture_ns: u64,
) -> i32 {
    if capture_ns == 0 || capture_ns > qpc_ns() { return -1; }
    unsafe { submit_packet(p, seq, count, data, len, capture_ns) }
}
unsafe fn submit_packet(p: *mut c_void, seq: u64, count: u32, data: *const u8, len: usize, capture_ns: u64) -> i32 {
    status(|| {
        if p.is_null() || data.is_null() {
            return err("Null packet");
        }
        let p = unsafe { &*(p as *const Pipeline) };
        if count == 0
            || count as usize > SLOTS
            || len != count as usize * (32 + p.n * 4)
            || p.failed.load(Ordering::Acquire) != 0
        {
            return err("Invalid packet or worker failed");
        }
        let mut owned = p.free.lock().unwrap().try_recv().map_err(|_| {
            p.dropped.fetch_add(1, Ordering::Relaxed);
            "Native input buffer pool exhausted".to_string()
        })?;
        owned.extend_from_slice(unsafe { slice::from_raw_parts(data, len) });
        let packet = Packet {
            seq,
            count: count as usize,
            data: owned,
            received: Instant::now(),
            capture_ns,
        };
        match p.tx.as_ref().ok_or("Pipeline finished")?.try_send(packet) {
            Ok(()) => {
                p.enqueued.fetch_add(1, Ordering::Relaxed);
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
