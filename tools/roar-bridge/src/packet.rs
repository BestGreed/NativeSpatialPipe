//! Owned native packet consumption; the caller remains responsible for stream routing.
use aom_roar::*;
use std::{
    ffi::{CStr, c_char, c_void},
    fs::File,
    io::{BufWriter, Write},
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

const SLOTS: usize = 160;
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
#[derive(Clone, Copy, Default)]
struct State {
    present: bool,
    ident: u32,
    typ: u32,
    generation: u64,
    position: Option<[f64; 3]>,
}
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
pub struct Engine {
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
        if !(1..=4096).contains(&n) || rate != 48000 || !reference.is_finite() || reference <= 0. {
            return err("Invalid native packet configuration");
        }
        let frames = oar(Samples::new(n))?;
        let mut rdr = oar(RoarRenderer::create(&oar(Config::new(
            Layout::Layout714,
            frames,
            oar(SampleRate::new(rate))?,
        ))?))?;
        let group = oar(rdr.add_audio_group())?;
        Ok(Self {
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
            output: vec![0.; 12 * n as usize],
            positions: ObjectPosition::Cartesian(Vec::with_capacity(SLOTS)),
            one_position: ObjectPosition::Cartesian(Vec::with_capacity(1)),
            mapping: Mapping::default(),
            decode_ns: 0,
            metadata_ns: 0,
            dsp_ns: 0,
        })
    }
    fn process(&mut self, seq: u64, count: usize, data: &[u8]) -> Result<&[f32], String> {
        let begin = Instant::now();
        if count == 0 || count > SLOTS || data.len() != count * (32 + self.n * 4) {
            return err("Invalid packet length/count");
        }
        if self
            .last
            .is_some_and(|last| last.checked_add(1) != Some(seq))
        {
            return err("Sequence discontinuity: rebuild native pipeline");
        }
        // Decode/validate all bytes before changing any metadata or renderer state.
        let mut props = [(0u32, 0u32, 0u32, [0f64; 3], 0f32); SLOTS];
        for (slot, prop) in props.iter_mut().enumerate().take(count) {
            let p = &data[slot * 32..slot * 32 + 32];
            let u = |i| u32::from_le_bytes(p[i..i + 4].try_into().unwrap());
            let f = |i| f32::from_le_bytes(p[i..i + 4].try_into().unwrap());
            let typ = u(8);
            let xyz = [f(16) as f64, f(20) as f64, f(24) as f64];
            let gain = f(28);
            if u(4) != self.n as u32 || !xyz.iter().all(|v| v.is_finite()) || !gain.is_finite() {
                return err("Invalid native property");
            }
            if typ != 1 && !BED_TYPES.contains(&typ) {
                return err("Unsupported static type");
            }
            *prop = (u(0), typ, u(12), xyz, gain);
            let at = count * 32 + slot * self.n * 4;
            for (dst, b) in self.pcm[slot * self.n..(slot + 1) * self.n]
                .iter_mut()
                .zip(data[at..at + self.n * 4].chunks_exact(4))
            {
                let v = f32::from_le_bytes(b.try_into().unwrap());
                if !v.is_finite() {
                    return err("Nonfinite PCM");
                }
                *dst = if self.apply_gain { v * gain } else { v };
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
        for (slot, &(ident, typ, flags, xyz, _gain)) in props.iter().enumerate().take(count) {
            let s = &mut self.states[slot];
            if !s.present || s.ident != ident || s.typ != typ || flags & 1 != 0 {
                if s.present {
                    self.mapping.retires += 1;
                }
                *s = State {
                    present: true,
                    ident,
                    typ,
                    generation: s.generation + 1,
                    position: None,
                };
                self.mapping.creates += 1;
            }
            if typ == 1 && flags & 4 != 0 {
                s.position = Some(xyz);
                self.mapping.updates += 1;
            }
            retire[slot] = flags & 8 != 0;
            if typ == 1 {
                if let Some([x, y, z]) = s.position {
                    let radius = (x * x + y * y + z * z).sqrt();
                    let scale = if self.scaled {
                        1. / self.reference
                    } else if radius > 0. {
                        1. / radius
                    } else {
                        1.
                    };
                    if let ObjectPosition::Cartesian(p) = &mut self.positions {
                        p.push(CartesianCoordinate {
                            x: (x * scale) as f32,
                            y: (-z * scale) as f32,
                            z: (y * scale) as f32,
                        });
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
            let mut out: [&mut [f32]; 12] = std::array::from_fn(|_| chunks.next().unwrap());
            oar(self.rdr.render(
                &inputs[..len],
                &mut oar(PlanarBufferMut::new(&mut out, 12, self.frames))?,
            ))?;
        }
        self.dsp_ns = dsp_begin.elapsed().as_nanos() as u64;
        if !self.output.iter().all(|v| v.is_finite()) {
            return err("Nonfinite rendered output");
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
        if out_len != e.n * 12 {
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
struct OutputPacket { data: Vec<f32>, sequence: u64, capture_ns: u64, render_ns: u64, queued: Instant }
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
    error: Option<String>,
    mapping: Mapping,
    work: Timing,
    decode: Timing,
    metadata: Timing,
    dsp: Timing,
    queue: Timing,
    capture_render: Timing,
    writer: WriterStats,
    mmcss_requested: bool,
    mmcss_registered: bool,
    mmcss_priority: bool,
    mmcss_reverted: bool,
    thread_id: u32,
}
struct WriterStats { written: u64, queue: Timing, write: Timing, capture_write: Timing, flush_ns: u64, tails: Vec<String>, overflow: u64 }
impl WriterStats {
    fn new() -> Self { Self { written: 0, queue: Timing::new(), write: Timing::new(), capture_write: Timing::new(), flush_ns: 0, tails: Vec::with_capacity(256), overflow: 0 } }
}
impl Stats {
    fn new() -> Self {
        Self {
            processed: 0,
            written: 0,
            error: None,
            mapping: Mapping::default(),
            work: Timing::new(),
            decode: Timing::new(),
            metadata: Timing::new(),
            dsp: Timing::new(),
            queue: Timing::new(),
            capture_render: Timing::new(),
            writer: WriterStats::new(),
            mmcss_requested: false, mmcss_registered: false, mmcss_priority: false, mmcss_reverted: false, thread_id: 0,
        }
    }
}
struct Pipeline {
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
impl Pipeline {
    fn new(n: u32, capacity: usize, grouped: bool, gain: bool, path: &str) -> Result<Self, String> {
        if !(1..=256).contains(&capacity) {
            return err("Queue capacity");
        }
        let mut engine = Engine::new(n, 48000, grouped, gain, false, 1.)?;
        let _ = qpc_ns(); // Initialize frequency before either worker starts.
        let output = File::create_new(path).map_err(|e| e.to_string())?;
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
            out_free_tx.send(vec![0f32; n as usize * 12]).unwrap();
        }
        let enqueued = Arc::new(AtomicU64::new(0));
        let dropped = Arc::new(AtomicU64::new(0));
        let failed = Arc::new(AtomicU64::new(0));
        let worker_failed = Arc::clone(&failed);
        let worker_free = free_tx.clone();
        let renderer_mmcss = std::env::var("NSP_RENDER_MMCSS").map(|s| s == "1").unwrap_or(false);
        let writer = thread::spawn(move || -> Result<WriterStats, String> {
            let mut output = BufWriter::with_capacity(1024 * 1024, output);
            let mut stats = WriterStats::new();
            while let Ok(packet) = out_rx.recv() {
                let begin = Instant::now();
                if packet.capture_ns != 0 { stats.queue.add(packet.queued.elapsed().as_nanos() as u64); }
                let buf = packet.data;
                // All supported targets for this Windows bridge are little endian.
                let bytes =
                    unsafe { slice::from_raw_parts(buf.as_ptr() as *const u8, buf.len() * 4) };
                output.write_all(bytes).map_err(|e| e.to_string())?;
                stats.written += 1;
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
                let _ = out_free_tx.send(buf);
            }
            let flush = Instant::now();
            output.flush().map_err(|e| e.to_string())?;
            output.get_ref().sync_all().map_err(|e| e.to_string())?;
            stats.flush_ns = flush.elapsed().as_nanos() as u64;
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
                    let mut out = out_free_rx
                        .try_recv()
                        .map_err(|_| "Writer buffer pool exhausted".to_string())?;
                    let pcm = engine.process(packet.seq, packet.count, &packet.data)?;
                    for i in 0..n as usize {
                        for c in 0..12 {
                            out[i * 12 + c] = pcm[c * n as usize + i];
                        }
                    }
                    let render_ns = if packet.capture_ns != 0 { qpc_ns() } else { 0 };
                    if packet.capture_ns != 0 { stats.capture_render.add(render_ns.saturating_sub(packet.capture_ns)); }
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
                stats.decode.add(engine.decode_ns);
                stats.metadata.add(engine.metadata_ns);
                stats.dsp.add(engine.dsp_ns);
            }
            if !task.handle.is_null() {
                stats.mmcss_reverted = unsafe { AvRevertMmThreadCharacteristics(task.handle) } != 0;
                if stats.mmcss_reverted { task.handle = std::ptr::null_mut(); }
                else { stats.error = Some("Renderer MMCSS revert failed".into()); worker_failed.store(1, Ordering::Release); }
            }
            drop(out_tx);
            drop(out_free_rx);
            match writer.join() {
                Ok(Ok(w)) => { stats.written = w.written; stats.writer = w; },
                Ok(Err(e)) => stats.error = Some(e),
                Err(_) => stats.error = Some("Writer panic".to_string()),
            }
            engine.mapping.retires += engine.states.iter().filter(|s| s.present).count() as u64;
            stats.mapping = engine.mapping;
            stats
        });
        Ok(Self {
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
            && stats.processed == stats.written
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
        self.report = format!(
            "{{\"passed\":{passed},\"enqueued\":{enqueued},\"consumed\":{},\"written\":{},\"queueDropped\":{dropped},\"queueCapacity\":{},\"error\":{error},\"threadStopped\":true,\"processedFrames\":{},\"mapping\":{{\"creates\":{},\"retires\":{},\"positionUpdates\":{},\"unknownPositionBlocks\":{},\"maxDynamic\":{},\"maxStatic\":{}}},\"workMs\":{},\"decodeMs\":{},\"metadataMs\":{},\"dspMs\":{},\"queueWaitMs\":{},\"scope\":\"Native packet worker; output serialization/enqueue included, disk write on independent bounded writer; histogram 1us, percentile ceiling 50ms; no device playback\"}}",
            stats.processed,
            stats.written,
            self.capacity,
            stats.mapping.frames,
            stats.mapping.creates,
            stats.mapping.retires,
            stats.mapping.updates,
            stats.mapping.unknown,
            stats.mapping.max_dynamic,
            stats.mapping.max_static,
            stats.work.json(),
            stats.decode.json(),
            stats.metadata.json(),
            stats.dsp.json(),
            stats.queue.json()
        );
        self.report.pop();
        self.report.push_str(&format!(",\"rendererScheduling\":{{\"threadId\":{},\"requested\":{},\"registered\":{},\"prioritySet\":{},\"reverted\":{}}}", stats.thread_id, stats.mmcss_requested, stats.mmcss_registered, stats.mmcss_priority, stats.mmcss_reverted));
        self.report.push_str(&format!(",\"fullTiming\":{{\"captureToRenderMs\":{},\"writerQueueMs\":{},\"bufferedWriteMs\":{},\"captureToBufferedWriteMs\":{},\"finalFlushSyncMs\":{},\"tails\":[{}],\"tailOverflow\":{},\"scope\":\"QPC capture through render-ready and buffered file write return; not per-packet durable disk completion or device playback\"}}}}", stats.capture_render.json(), stats.writer.queue.json(), stats.writer.write.json(), stats.writer.capture_write.json(), stats.writer.flush_ns as f64 / 1e6, stats.writer.tails.join(","), stats.writer.overflow));
    }
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
