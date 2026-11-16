//! Windows x64, one reader, NSP v1 ring. No Python calls on the worker.
//! Control caller must stop producer before finish and keep the pipeline/DLL alive
//! until finish succeeds. Failures retain an unconsumed slot and fail closed.
use serde::{Deserialize, Serialize};
use std::{ffi::c_void, sync::{Arc, atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering}}, thread, time::{Duration, Instant}};
const CAP: usize = if cfg!(feature = "phase-probe") { 32 } else { 16 };
const HEADER: usize = 8192;
const PAYLOAD: usize = 160 * (32 + 4096 * 4);
const SLOT: usize = 12 + HEADER + PAYLOAD;
const RING: usize = 16 + CAP * SLOT;
// Control-thread diagnostic: high 32 bits = stage, low 32 = OS error.
// 1 arguments, 2 mapping open, 3 event open, 4 view, 5 header,
// 6 QPC frequency, 7 worker spawn, 8 panic. Zero means start succeeded.
thread_local! { static START_ERROR: std::cell::Cell<u64> = const { std::cell::Cell::new(0) }; }
fn start_error(stage: u32, code: u32) {
    START_ERROR.set(((stage as u64) << 32) | code as u64);
}
#[unsafe(no_mangle)]
pub extern "C" fn nsp_receiver_start_error() -> u64 { START_ERROR.get() }
type Submit = unsafe extern "C" fn(*mut c_void, u64, u32, *const u8, usize) -> i32;
type SubmitTimed = unsafe extern "C" fn(*mut c_void, u64, u32, *const u8, usize, u64) -> i32;
type SubmitLeased = unsafe extern "C" fn(*mut c_void, u64, u32, *const u8, usize, u64, u64) -> i32;
#[cfg(feature = "phase-probe")]
#[unsafe(no_mangle)]
pub extern "C" fn nsp_receiver_phase_sink(_: *mut c_void, _: u64, _: u32, _: *const u8, _: usize, _: u64) -> i32 { 0 }
#[derive(Clone, Copy)]
enum Submission { Plain(Submit), Timed(SubmitTimed), Leased(SubmitLeased) }

#[derive(Clone, Copy, Default)]
struct Route {
    block: Option<u64>, last: Option<u64>, first: Option<u64>, last_ns: u64, lease: u64,
    dynamic_objects: u32, ever_dynamic: bool, submitted: u64, filtered: u64, unsupported: u64,
}
// One coherent per-route observation, reusing the existing property validation
// pass. Microsecond QPC time leaves room for all 160 dynamic objects and a seen
// bit; even u64::MAX nanoseconds fits without timestamp wraparound.
const ROUTE_SEEN: u64 = 1 << 63;
fn pack_route_snapshot(dynamic_objects: u32, capture_qpc_ns: u64) -> u64 {
    ROUTE_SEEN | ((capture_qpc_ns / 1000) << 8) | dynamic_objects as u64
}
fn unpack_route_snapshot(packed: u64) -> Option<(u32, u64)> {
    (packed & ROUTE_SEEN != 0).then(|| (
        (packed & 0xff) as u32,
        ((packed & !ROUTE_SEEN) >> 8) * 1000,
    ))
}
// Published only when registration, reuse or overflow changes. The control
// thread serializes these independent diagnostic counters outside audio work.
struct LiveRouteStatus {
    capacity_drops: AtomicU64,
    recycled_routes: AtomicU64,
    stale_source_drops: AtomicU64,
    leases: [AtomicU64; 16],
    packet_shapes: Option<[AtomicU64; 16]>,
}
impl LiveRouteStatus {
    fn new() -> Self {
        Self {
            capacity_drops: AtomicU64::new(0),
            recycled_routes: AtomicU64::new(0),
            stale_source_drops: AtomicU64::new(0),
            leases: std::array::from_fn(|_| AtomicU64::new(0)),
            packet_shapes: None,
        }
    }
    fn snapshot(&self, count: usize) -> serde_json::Value {
        let leases: Vec<_> = self.leases[..count].iter()
            .map(|lease| lease.load(Ordering::Acquire)).collect();
        let mut status = serde_json::json!({
            "capacityDrops": self.capacity_drops.load(Ordering::Acquire),
            "recycledRoutes": self.recycled_routes.load(Ordering::Acquire),
            "staleSourceDrops": self.stale_source_drops.load(Ordering::Acquire),
            "registeredSlots": leases.iter().filter(|&&lease| lease != 0).count(),
            "slotLimit": count,
            "leases": leases,
        });
        if let Some(shapes) = &self.packet_shapes {
            let packet_shapes: Vec<_> = shapes[..count].iter().map(|shape| {
            let shape = shape.load(Ordering::Acquire);
            (shape != 0).then(|| serde_json::json!({
                "captureQpcUs": shape >> 16,
                "dynamicObjects": (shape >> 8) & 0xff,
                "bedChannels": shape & 0xff,
            }))
            }).collect();
            status["packetShapes"] = serde_json::json!(packet_shapes);
        }
        status
    }
}
struct PriorityRoutes {
    pipelines: [usize; 16], count: usize, routes: [Route; 16], timeout_ns: u64,
    submit_all: bool,
    publish_route_snapshots: bool,
    allow_recycling: bool,
    recycled_routes: u64,
    capacity_drops: u64,
    live: Arc<LiveRouteStatus>,
    latest_capture_ns: u64,
    retired: [Option<(u64, u64)>; 32],
    next_retired: usize,
}
impl PriorityRoutes {
    fn new(pipelines: [usize; 16], count: usize, timeout_ns: u64) -> Self {
        Self { pipelines, count, routes: [Route::default(); 16], timeout_ns, submit_all: false, publish_route_snapshots: false, allow_recycling: false, recycled_routes: 0, capacity_drops: 0, live: Arc::new(LiveRouteStatus::new()), latest_capture_ns: 0, retired: [None; 32], next_retired: 0 }
    }
    // Slot zero is reserved for the first dynamic stream (the primary telemetry
    // route). Static-only streams use the other prewarmed pipelines.
    fn select(&mut self, d: &Descriptor, payload: &[u8], frames: u32) -> Result<Option<(usize, bool)>, String> {
        let block=validate(d,payload.len(),frames,None,None)?;
        let mut dynamic_objects=0;
        let mut supported=true;
        for property in payload[..d.count as usize*32].chunks_exact(32) {
            if u32::from_le_bytes(property[4..8].try_into().unwrap())!=frames {
                return Err("Property frame mismatch".into());
            }
            let typ=u32::from_le_bytes(property[8..12].try_into().unwrap());
            dynamic_objects += u32::from(typ==1);
            supported &= typ==1 || (typ.is_power_of_two() && (2..=4096).contains(&typ));
        }
        let dynamic=dynamic_objects!=0;
        let existing=self.routes[..self.count].iter().position(|r|r.block==Some(block));
        if self.allow_recycling {
            let old_record = self.latest_capture_ns.saturating_sub(d.capture_qpc_ns) > self.timeout_ns
                || self.retired.iter().flatten().any(|&(retired, until)| retired == block && d.capture_qpc_ns <= until)
                || existing.is_some_and(|slot| {
                    let route = self.routes[slot];
                    d.capture_qpc_ns < route.last_ns
                        || route.last.is_some_and(|last| d.sequence <= last &&
                            !(d.sequence == 0 && d.capture_qpc_ns > route.last_ns))
                });
            if old_record {
                self.live.stale_source_drops.fetch_add(1, Ordering::Relaxed);
                return Ok(None);
            }
            self.latest_capture_ns = self.latest_capture_ns.max(d.capture_qpc_ns);
        }
        let eligible = usize::from(!dynamic && !self.submit_all)..self.count;
        let vacant = || eligible.clone().find(|&i| self.routes[i].block.is_none());
        let expired = || eligible.clone().filter(|&i| {
            d.capture_qpc_ns.saturating_sub(self.routes[i].last_ns) > self.timeout_ns
        }).min_by_key(|&i| self.routes[i].last_ns);
        let slot = existing.or_else(vacant).or_else(|| {
            if self.allow_recycling && supported { expired() } else { None }
        });
        let Some(slot) = slot else {
            if !self.submit_all {
                return Err("Priority route capacity exhausted".into());
            }
            self.capacity_drops = self.capacity_drops.saturating_add(1);
            self.live.capacity_drops.store(self.capacity_drops, Ordering::Release);
            return Ok(None);
        };
        let old = self.routes[slot];
        let source_restarted = self.allow_recycling
            && existing.is_some()
            && d.capture_qpc_ns > old.last_ns
            && old.last.is_some_and(|last| last.checked_add(1) != Some(d.sequence));
        if existing.is_none() || source_restarted {
            if let Some(previous) = old.block {
                // Keep bounded evidence for delayed packets after lane reuse.
                // A new same-address source with an indistinguishable continuous
                // sequence still requires identity evidence from the producer.
                self.retired[self.next_retired] = Some((previous, if source_restarted {
                    old.last_ns
                } else {
                    self.latest_capture_ns
                }));
                self.next_retired = (self.next_retired + 1) % self.retired.len();
            }
            // Keep lane submission order while changing source identity.
            let lease = old.lease.checked_add(1).ok_or("Route lease exhausted")?;
            self.routes[slot] = Route { lease, submitted: old.submitted, ..Route::default() };
            self.recycled_routes += u64::from(old.block.is_some());
            if let Some(shapes) = &self.live.packet_shapes {
                shapes[slot].store(0, Ordering::Release);
            }
            self.live.leases[slot].store(lease, Ordering::Release);
            self.live.recycled_routes.store(self.recycled_routes, Ordering::Release);
        }
        let old=self.routes[slot];
        if old.last.is_some_and(|last|last.checked_add(1)!=Some(d.sequence)) {
            return Err(format!("Source sequence discontinuity in block 0x{block:x}"));
        }
        if old.block.is_some() && d.capture_qpc_ns<old.last_ns { return Err("Source clock moved backwards".into()); }
        let other_dynamic=self.routes[..self.count].iter().enumerate().any(|(i,r)|
            i!=slot && r.ever_dynamic && r.block.is_some() &&
            d.capture_qpc_ns.saturating_sub(r.last_ns)<=self.timeout_ns);
        let route=&mut self.routes[slot];
        route.block=Some(block); route.first.get_or_insert(d.sequence); route.last=Some(d.sequence);
        route.last_ns=d.capture_qpc_ns; route.dynamic_objects=dynamic_objects; route.ever_dynamic |= dynamic;
        if !self.submit_all && !route.ever_dynamic && other_dynamic { route.filtered+=1; return Ok(Some((slot,false))); }
        // Do not feed unsupported static layouts into the fixed 7.1.4 renderer,
        // even before a dynamic companion is discovered. Report separately.
        if !supported { route.unsupported+=1; return Ok(Some((slot,false))); }
        Ok(Some((slot,true)))
    }
}
#[link(name = "avrt")]
unsafe extern "system" {
    fn AvSetMmThreadCharacteristicsW(name: *const u16, index: *mut u32) -> *mut c_void;
    fn AvSetMmThreadPriority(handle: *mut c_void, priority: i32) -> i32;
    fn AvRevertMmThreadCharacteristics(handle: *mut c_void) -> i32;
}
#[link(name = "kernel32")]
unsafe extern "system" {
    fn OpenFileMappingW(access: u32, inherit: i32, name: *const u16) -> *mut c_void;
    fn OpenEventW(access: u32, inherit: i32, name: *const u16) -> *mut c_void;
    fn MapViewOfFile(mapping: *mut c_void, access: u32, hi: u32, lo: u32, bytes: usize) -> *mut c_void;
    fn UnmapViewOfFile(ptr: *const c_void) -> i32;
    fn CloseHandle(handle: *mut c_void) -> i32;
    fn WaitForSingleObject(handle: *mut c_void, ms: u32) -> u32;
    fn SetEvent(handle: *mut c_void) -> i32;
    fn QueryPerformanceCounter(value: *mut i64) -> i32;
    fn QueryPerformanceFrequency(value: *mut i64) -> i32;
    fn GetCurrentThreadId() -> u32;
    fn GetLastError() -> u32;
}
struct Mapping { handle: usize, event: usize, view: usize }
impl Drop for Mapping {
    fn drop(&mut self) { unsafe {
        if self.view != 0 { UnmapViewOfFile(self.view as *const _); }
        if self.event != 0 { CloseHandle(self.event as *mut _); }
        if self.handle != 0 { CloseHandle(self.handle as *mut _); }
    }}
}
impl Mapping {
    unsafe fn open(name: *const u16, event: *const u16) -> Option<Self> {
        let mut m = Self { handle: 0, event: 0, view: 0 };
        m.handle = unsafe { OpenFileMappingW(6, 0, name) } as usize;
        if m.handle == 0 { start_error(2, unsafe { GetLastError() }); return None; }
        m.event = unsafe { OpenEventW(0x100002, 0, event) } as usize;
        if m.event == 0 { start_error(3, unsafe { GetLastError() }); return None; }
        m.view = unsafe { MapViewOfFile(m.handle as *mut _, 6, 0, 0, RING) } as usize;
        if m.view == 0 { start_error(4, unsafe { GetLastError() }); return None; }
        let header = unsafe { std::slice::from_raw_parts(m.view as *const u32, 4) };
        if header != [0x3150534e, 1, CAP as u32, SLOT as u32] { start_error(5, 0); return None; }
        Some(m)
    }
}
#[derive(Debug)]
struct Frames { values: [u32; 160], len: usize }
impl<'de> Deserialize<'de> for Frames {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Visitor;
        impl<'de> serde::de::Visitor<'de> for Visitor {
            type Value = Frames;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result { f.write_str("at most 160 frame counts") }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(self, mut seq: A) -> Result<Frames, A::Error> {
                let mut frames = Frames { values: [0; 160], len: 0 };
                while let Some(v) = seq.next_element::<u32>()? {
                    if frames.len == 160 { return Err(serde::de::Error::custom("Too many frames")); }
                    frames.values[frames.len] = v; frames.len += 1;
                }
                Ok(frames)
            }
        }
        d.deserialize_seq(Visitor)
    }
}
#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
struct Descriptor<'a> {
    block: &'a str, sequence: u64, count: u32,
    frames_uniform: Option<u32>, frames: Option<Frames>,
    capture_qpc_ns: u64, copy_done_qpc_ns: u64, send_qpc_ns: u64,
}
fn validate(d: &Descriptor, bytes: usize, frames: u32, block: Option<u64>, last: Option<u64>) -> Result<u64, &'static str> {
    let id = d.block.strip_prefix("0x").and_then(|v| u64::from_str_radix(v, 16).ok()).ok_or("Invalid block")?;
    if !(1..=160).contains(&d.count) || bytes != d.count as usize * (32 + frames as usize * 4) { return Err("Invalid payload length/count"); }
    match (&d.frames_uniform, &d.frames) {
        (Some(n), None) if *n == frames => (),
        (None, Some(ns)) if ns.len == d.count as usize && ns.values[..ns.len].iter().all(|n| *n == frames) => (),
        _ => return Err("Frame descriptor mismatch"),
    }
    if block.is_some_and(|b| b != id) { return Err("Multiple streams are not supported by this receiver"); }
    if last.is_some_and(|s| s.checked_add(1) != Some(d.sequence)) { return Err("Sequence discontinuity"); }
    if d.capture_qpc_ns > d.copy_done_qpc_ns || d.copy_done_qpc_ns > d.send_qpc_ns { return Err("Invalid source timestamps"); }
    Ok(id)
}
fn now(hz: u64) -> u64 { let mut q = 0; unsafe { QueryPerformanceCounter(&mut q); } ((q as u128 * 1_000_000_000) / hz as u128) as u64 }
struct Histogram { bins: Vec<u64>, count: u64, sum: u128, max: u64 }
impl Histogram {
    fn new() -> Self { Self { bins: vec![0; 50001], count: 0, sum: 0, max: 0 } }
    fn add(&mut self, ns: u64) { self.bins[(ns/1000).min(50000) as usize] += 1; self.count += 1; self.sum += ns as u128; self.max = self.max.max(ns); }
    fn json(&self) -> serde_json::Value {
        let percentile = |percent: u64| { let mut sum = 0; for (i, n) in self.bins.iter().enumerate() { sum += n; if sum*100 >= self.count*percent { return i as f64 / 1000.; }} 50. };
        serde_json::json!({"count":self.count,"mean":if self.count==0 {0.} else {self.sum as f64 / self.count as f64 / 1e6},"p50":percentile(50),"p95":percentile(95),"p99":percentile(99),"max":self.max as f64/1e6,"overflow":self.bins[50000]})
    }
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Trace { #[cfg(feature = "phase-probe")] source_copy_done_qpc_ns: u64, #[cfg(feature = "phase-probe")] shared_copy_start_qpc_ns: u64, #[cfg(feature = "phase-probe")] shared_copy_end_qpc_ns: u64, block: u64, sequence: u64, capture_qpc_ns: u64, send_qpc_ns: u64, acquired_qpc_ns: u64, parsed_qpc_ns: u64, submitted_qpc_ns: u64 }
struct Statistics { hist: [Histogram; 6], tails: Vec<Trace>, tails_overflow: u64, error: Option<String>, block: Option<u64>, first: Option<u64>, last: Option<u64>, thread_id: u32, decision: Histogram, routes: Option<PriorityRoutes> }
impl Statistics {
    fn new() -> Self { Self { hist: std::array::from_fn(|_| Histogram::new()), tails: Vec::with_capacity(if cfg!(feature = "phase-probe") { 4096 } else { 256 }), tails_overflow: 0, error: None, block: None, first: None, last: None, thread_id: 0, decision: Histogram::new(), routes: None } }
}
struct Control { primary: Arc<AtomicI32>, route_snapshots: Arc<[AtomicU64; 16]>, route_count: usize, live_routes: Option<Arc<LiveRouteStatus>>, map: Arc<Mapping>, stopping: Arc<AtomicBool>, received: Arc<AtomicU64>, failed: Arc<AtomicBool>, worker: Option<thread::JoinHandle<Statistics>>, report: Option<Vec<u8>> }
fn run(primary: Arc<AtomicI32>, route_snapshots: Arc<[AtomicU64; 16]>, map: Arc<Mapping>, stopping: Arc<AtomicBool>, received: Arc<AtomicU64>, failed: Arc<AtomicBool>, pipeline: usize, submit: Submission, frames: u32, hz: u64, timing: bool, mmcss: bool, mut stats: Statistics) -> Statistics {
    stats.thread_id = unsafe { GetCurrentThreadId() };
    let mut task_index=0;
    let task_name: Vec<u16>="Pro Audio\0".encode_utf16().collect();
    let task=if mmcss { unsafe { AvSetMmThreadCharacteristicsW(task_name.as_ptr(), &mut task_index) } } else { std::ptr::null_mut() };
    if mmcss && (task.is_null() || unsafe { AvSetMmThreadPriority(task, 0) } == 0) {
        if !task.is_null() { unsafe { AvRevertMmThreadCharacteristics(task); } }
        stats.error=Some("MMCSS registration/priority failed".into());failed.store(true,Ordering::Release);return stats;
    }
    let mut next = 0;
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result<(), String> {
        loop {
            let ptr = (map.view + 16 + next*SLOT) as *const u8;
            let ready = unsafe { &*(ptr as *const AtomicI32) };
            // Windows x64 interlocked publisher + acquire/release consumer. Borrow
            // only this slot's published bytes, never form a reference to the ring.
            if ready.load(Ordering::Acquire) != 1 {
                if stopping.load(Ordering::Acquire) { break; }
                let wait = unsafe { WaitForSingleObject(map.event as *mut _, 100) };
                if wait != 0 && wait != 258 { return Err("Event wait failed".into()); }
                continue;
            }
            let acquired = if timing { now(hz) } else { 0 };
            let nh = unsafe { (ptr.add(4) as *const u32).read() } as usize;
            let nd = unsafe { (ptr.add(8) as *const u32).read() } as usize;
            if nh == 0 || nh > HEADER || nd == 0 || nd > PAYLOAD { return Err("Invalid slot bounds".into()); }
            let header = unsafe { std::slice::from_raw_parts(ptr.add(12), nh) };
            let d: Descriptor = serde_json::from_slice(header).map_err(|e| e.to_string())?;
            let payload=unsafe { std::slice::from_raw_parts(ptr.add(12+HEADER),nd) };
            let (block, target, submit_sequence, should_submit, route_slot, lease)=if let Some(routes)=stats.routes.as_mut() {
                let Some((slot,keep)) = routes.select(&d,payload,frames)? else {
                    ready.store(0, Ordering::Release);
                    next = (next + 1) % CAP;
                    received.fetch_add(1, Ordering::Release);
                    continue;
                };
                let route = &routes.routes[slot];
                (route.block.unwrap(), routes.pipelines[slot], route.submitted, keep, Some(slot), route.lease)
            } else { (validate(&d,nd,frames,stats.block,stats.last)?,pipeline,d.sequence,true,None,0) };
            let parsed = if timing { now(hz) } else { 0 };
            if timing && d.send_qpc_ns > acquired { return Err("QPC clocks not aligned".into()); }
            // Existing renderer copies synchronously to its preallocated pool.
            // Never release the slot while it can still read the borrowed pointer.
            let rc = if should_submit { unsafe { match submit {
                Submission::Plain(f) => f(target as *mut _, submit_sequence, d.count, ptr.add(12+HEADER), nd),
                Submission::Timed(f) => f(target as *mut _, submit_sequence, d.count, ptr.add(12+HEADER), nd, d.capture_qpc_ns),
                Submission::Leased(f) => f(target as *mut _, submit_sequence, d.count, ptr.add(12+HEADER), nd, d.capture_qpc_ns, lease),
            }}} else {0};
            if rc != 0 { return Err(format!("Native submit failed: {rc}")); }
            if should_submit && let Some(slot)=route_slot {
                let routes=stats.routes.as_mut().unwrap();
                routes.routes[slot].submitted+=1;
                if let Some(shapes) = &routes.live.packet_shapes {
                    let dynamic = u64::from(routes.routes[slot].dynamic_objects);
                    let bed = u64::from(d.count) - dynamic;
                    let capture_us = d.capture_qpc_ns / 1000;
                    shapes[slot].store(
                        (capture_us << 16) | (dynamic << 8) | bed, Ordering::Release,
                    );
                }
                if routes.publish_route_snapshots {
                    let route=&routes.routes[slot];
                    route_snapshots[slot].store(pack_route_snapshot(route.dynamic_objects, route.last_ns), Ordering::Release);
                }
                let old=primary.load(Ordering::Relaxed);
                if old<0 && routes.routes[slot].ever_dynamic {
                    primary.store(slot as i32,Ordering::Release);
                }
            }
            let end = if timing { now(hz) } else { 0 };
            stats.block = Some(block); stats.first.get_or_insert(d.sequence); stats.last = Some(d.sequence);
            if timing && route_slot.is_some() { stats.decision.add(parsed-acquired); }
            if timing && should_submit {
                for (h, ns) in stats.hist.iter_mut().zip([d.send_qpc_ns-d.copy_done_qpc_ns, acquired-d.send_qpc_ns, parsed-acquired, end-parsed, end-d.capture_qpc_ns, d.copy_done_qpc_ns-d.capture_qpc_ns]) { h.add(ns); }
                if cfg!(feature = "phase-probe") || end-d.capture_qpc_ns >= 5_000_000 {
                    if stats.tails.len() < stats.tails.capacity() { stats.tails.push(Trace {
                        #[cfg(feature = "phase-probe")]
                        source_copy_done_qpc_ns: d.copy_done_qpc_ns,
                        #[cfg(feature = "phase-probe")]
                        shared_copy_start_qpc_ns: ((unsafe { ptr.add(12 + HEADER - 16).cast::<u64>().read_unaligned() } as u128 * 1_000_000_000) / hz as u128) as u64,
                        #[cfg(feature = "phase-probe")]
                        shared_copy_end_qpc_ns: ((unsafe { ptr.add(12 + HEADER - 8).cast::<u64>().read_unaligned() } as u128 * 1_000_000_000) / hz as u128) as u64,
                        block, sequence: d.sequence, capture_qpc_ns: d.capture_qpc_ns, send_qpc_ns: d.send_qpc_ns, acquired_qpc_ns: acquired, parsed_qpc_ns: parsed, submitted_qpc_ns: end }); }
                    else { stats.tails_overflow += 1; }
                }
            }
            ready.store(0, Ordering::Release);
            next = (next+1)%CAP; received.fetch_add(1, Ordering::Release);
        }
        Ok(())
    }));
    stats.error = match outcome { Ok(Ok(())) => None, Ok(Err(e)) => Some(e), Err(_) => Some("Receiver panic".into()) };
    if !task.is_null() && unsafe { AvRevertMmThreadCharacteristics(task) } == 0 { stats.error=Some("MMCSS revert failed".into()); }
    failed.store(stats.error.is_some(), Ordering::Release);
    stats
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_start(name: *const u16, event: *const u16, pipeline: *mut c_void, submit: Option<Submit>, frames: u32, timing: u32) -> *mut c_void {
    unsafe { start_impl(name, event, pipeline, submit.map(Submission::Plain), frames, timing, None) }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_start_full(name: *const u16, event: *const u16, pipeline: *mut c_void, submit: Option<SubmitTimed>, frames: u32, timing: u32) -> *mut c_void {
    unsafe { start_impl(name, event, pipeline, submit.map(Submission::Timed), frames, timing | 1, None) }
}
unsafe fn start_impl(name: *const u16, event: *const u16, pipeline: *mut c_void, submit: Option<Submission>, frames: u32, timing: u32, routes: Option<PriorityRoutes>) -> *mut c_void {
    START_ERROR.set(0);
    if name.is_null() || event.is_null() || pipeline.is_null() || submit.is_none() || !(1..=4096).contains(&frames) { start_error(1, 0); return std::ptr::null_mut(); }
    std::panic::catch_unwind(move || {
        let map = Arc::new(unsafe { Mapping::open(name, event) }?);
        let stopping = Arc::new(AtomicBool::new(false)); let received = Arc::new(AtomicU64::new(0)); let failed = Arc::new(AtomicBool::new(false));
        let mut hz = 0; if unsafe { QueryPerformanceFrequency(&mut hz) } == 0 || hz <= 0 { start_error(6, unsafe { GetLastError() }); return None; }
        let mut stats = Statistics::new(); // Allocate/touch statistics on control thread.
        stats.routes=routes;
        let route_count=stats.routes.as_ref().map_or(1,|routes|routes.count);
        let live_routes=stats.routes.as_ref().map(|routes| routes.live.clone());
        let route_snapshots=Arc::new(std::array::from_fn(|_|AtomicU64::new(0)));
        let worker_snapshots=route_snapshots.clone();
        let primary=Arc::new(AtomicI32::new(-1)); let primary_worker=primary.clone();
        let (m,s,r,f) = (map.clone(),stopping.clone(),received.clone(),failed.clone());
        let pipeline = pipeline as usize;
        let worker = thread::Builder::new().name("nsp-rust-receiver".into()).spawn(move || run(primary_worker,worker_snapshots,m,s,r,f,pipeline,submit.unwrap(),frames,hz as u64,timing & 1 != 0,timing & 2 != 0,stats)).map_err(|e| start_error(7, e.raw_os_error().unwrap_or(0) as u32)).ok()?;
        Some(Box::into_raw(Box::new(Control { primary, route_snapshots, route_count, live_routes, map, stopping, received, failed, worker: Some(worker), report: None })) as *mut c_void)
    }).map_err(|_| start_error(8, 0)).ok().flatten().unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_start_priority(name: *const u16, event: *const u16, pipelines: *const *mut c_void, count: u32, submit: Option<Submit>, frames: u32, timing: u32, timeout_ms: u32) -> *mut c_void {
    unsafe {start_priority_impl(name,event,pipelines,count,submit.map(Submission::Plain),frames,timing,timeout_ms)}
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_start_priority_full(name: *const u16, event: *const u16, pipelines: *const *mut c_void, count: u32, submit: Option<SubmitTimed>, frames: u32, timing: u32, timeout_ms: u32) -> *mut c_void {
    unsafe {start_priority_impl(name,event,pipelines,count,submit.map(Submission::Timed),frames,timing | 1,timeout_ms)}
}
/// Starts Unified reception with the lease carried inline on every submission.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_start_priority_leased(
    name: *const u16,
    event: *const u16,
    pipelines: *const *mut c_void,
    count: u32,
    submit: Option<SubmitLeased>,
    frames: u32,
    timing: u32,
    timeout_ms: u32,
) -> *mut c_void {
    unsafe { start_priority_impl(name, event, pipelines, count, submit.map(Submission::Leased), frames, timing | 5, timeout_ms) }
}

unsafe fn start_priority_impl(name: *const u16, event: *const u16, pipelines: *const *mut c_void, count: u32, submit: Option<Submission>, frames: u32, timing: u32, timeout_ms: u32) -> *mut c_void {
    if pipelines.is_null() || !(2..=16).contains(&count) || !(10..=5000).contains(&timeout_ms) { start_error(1,0); return std::ptr::null_mut(); }
    let handles=unsafe {std::slice::from_raw_parts(pipelines,count as usize)};
    if handles.iter().any(|p|p.is_null()) || handles.iter().enumerate().any(|(i,p)|handles[..i].contains(p)) {start_error(1,0);return std::ptr::null_mut();}
    let mut pool=[0usize;16];for (i,p) in handles.iter().enumerate(){pool[i]=*p as usize;}
    let mut routes = PriorityRoutes::new(pool, count as usize, timeout_ms as u64 * 1_000_000);
    routes.submit_all = timing & 4 != 0;
    if routes.submit_all && timing & 16 != 0 {
        Arc::get_mut(&mut routes.live).unwrap().packet_shapes =
            Some(std::array::from_fn(|_| AtomicU64::new(0)));
    }
    routes.publish_route_snapshots = routes.submit_all && timing & 8 != 0;
    // Only the leased callback can carry a source change to the renderer.
    routes.allow_recycling = matches!(submit, Some(Submission::Leased(_)));
    unsafe { start_impl(name, event, handles[0], submit, frames, timing, Some(routes)) }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_primary_pipeline(p: *const c_void) -> i32 { if p.is_null() { return -1; } unsafe { &*(p as *const Control) }.primary.load(Ordering::Acquire) }
/// Unified-all-streams route observation; lane is the receiver pipeline index.
/// Returns 1 for a successful submitted packet, 0 before any observation (also
/// for non-unified receivers), or -1 for invalid arguments. Both outputs are
/// cleared on 0. Timestamp uses the source QPC-nanosecond domain, truncated to
/// 1 us. The count describes the latest packet, not the sticky everDynamic flag.
/// One atomic load provides a coherent pair without locks or retry loops.
/// The caller must retain the receiver and writable outputs for this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_route_snapshot(p: *const c_void, lane: u32, dynamic_objects: *mut u32, capture_qpc_ns: *mut u64) -> i32 {
    if p.is_null() || dynamic_objects.is_null() || capture_qpc_ns.is_null() { return -1; }
    let control=unsafe { &*(p as *const Control) };
    if lane as usize>=control.route_count { return -1; }
    let snapshot=unpack_route_snapshot(control.route_snapshots[lane as usize].load(Ordering::Acquire));
    let (count,time)=snapshot.unwrap_or((0,0));
    unsafe { dynamic_objects.write(count); capture_qpc_ns.write(time); }
    i32::from(snapshot.is_some())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_received(p: *const c_void) -> u64 { if p.is_null() { return 0; } unsafe { &*(p as *const Control) }.received.load(Ordering::Acquire) }
/// Copies live route diagnostics as UTF-8 JSON without a trailing NUL.
/// Call from the control thread while retaining the receiver handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_route_status(p: *const c_void, out: *mut u8, len: usize) -> usize {
    if p.is_null() || out.is_null() { return 0; }
    std::panic::catch_unwind(|| {
        let control = unsafe { &*(p as *const Control) };
        let Some(live) = &control.live_routes else { return 0; };
        let bytes = serde_json::to_vec(&live.snapshot(control.route_count)).unwrap();
        if bytes.len() > len { return 0; }
        unsafe { std::ptr::copy_nonoverlapping(bytes.as_ptr(), out, bytes.len()) };
        bytes.len()
    }).unwrap_or(0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_failed(p: *const c_void) -> i32 { if p.is_null() { return 1; } unsafe { &*(p as *const Control) }.failed.load(Ordering::Acquire) as i32 }
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_finish(p: *mut c_void, out: *mut u8, len: usize) -> usize {
    if p.is_null() { return 0; }
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let c = unsafe { &mut *(p as *mut Control) };
        if c.report.is_none() {
            c.stopping.store(true, Ordering::Release); unsafe { SetEvent(c.map.event as *mut _); }
            let deadline = Instant::now()+Duration::from_secs(5);
            while !c.worker.as_ref().unwrap().is_finished() {
                if Instant::now() >= deadline { return 0; } // retain all resources
                thread::sleep(Duration::from_millis(1));
            }
            let Ok(stats) = c.worker.take().unwrap().join() else { return 0; };
            let mut stages = serde_json::Map::new();
            for (name,h) in ["ownedCopyToSend","sendToAcquire","descriptorParse","nativeSubmit","captureToSubmit","captureToCopy"].iter().zip(&stats.hist) { stages.insert((*name).into(),h.json()); }
            if stats.routes.is_some() {stages.insert("priorityDecision".into(),stats.decision.json());}
            let routes=stats.routes.as_ref().map(|pool|pool.routes[..pool.count].iter().enumerate().filter(|(_,r)|r.block.is_some()).map(|(i,r)|serde_json::json!({"pipelineIndex":i,"block":r.block.map(|b|format!("0x{b:x}")),"firstSourceSequence":r.first,"lastSourceSequence":r.last,"everDynamic":r.ever_dynamic,"submittedBlocks":r.submitted,"filteredBlocks":r.filtered,"unsupportedBlocks":r.unsupported})).collect::<Vec<_>>());
            c.report = Some(serde_json::to_vec(&serde_json::json!({"backend":"rust-shared-memory-borrow","received":c.received.load(Ordering::Acquire),"passed":stats.error.is_none(),"errors":stats.error.iter().collect::<Vec<_>>(),"threadStopped":true,"threadId":stats.thread_id,"block":stats.block.map(|b|format!("0x{b:x}")),"firstSequence":stats.first,"lastSequence":stats.last,"timing":{"units":"ms","stages":stages},"tails":stats.tails,"tailOverflow":stats.tails_overflow,"receiverPcmCopies":0,"streams":routes,"recycledRoutes":stats.routes.as_ref().map_or(0, |r| r.recycled_routes),"capacityDrops":stats.routes.as_ref().map_or(0, |r| r.capacity_drops),"staleSourceDrops":stats.routes.as_ref().map_or(0, |r| r.live.stale_source_drops.load(Ordering::Acquire)),"policy":match stats.routes.as_ref() { Some(r) if r.submit_all => "unified-all-streams", Some(_) => "prefer-observed-dynamic", None => "strict-single-stream" }})).unwrap());
        }
        let bytes = c.report.as_ref().unwrap();
        if !out.is_null() && len > bytes.len() { unsafe { std::ptr::copy_nonoverlapping(bytes.as_ptr(),out,bytes.len()); *out.add(bytes.len())=0; } }
        bytes.len()+1
    })).unwrap_or(0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_destroy(p: *mut c_void) -> i32 {
    if p.is_null() { return 0; }
    if unsafe { &*(p as *const Control) }.worker.is_some() { return -1; }
    unsafe { drop(Box::from_raw(p as *mut Control)); } 0
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn route_snapshot_preserves_count_and_source_time_without_a_torn_pair() {
        assert_eq!(unpack_route_snapshot(0),None);
        for count in [0,1,128,160] {
            for stamp in [0,999,1000,123_456_789,u64::MAX] {
                let atomic=AtomicU64::new(pack_route_snapshot(count,stamp));
                assert_eq!(unpack_route_snapshot(atomic.load(Ordering::Acquire)),Some((count,stamp/1000*1000)));
            }
        }
    }
    #[test] fn current_dynamic_count_does_not_inherit_sticky_route_history() {
        let mut routes=PriorityRoutes::new([1;16],4,1_000_000);
        routes.submit_all=true;
        for (sequence,types) in [[1u32,1,2],[2,4,8]].iter().enumerate() {
            let text=format!(r#"{{"block":"0x123","sequence":{sequence},"count":3,"captureQpcNs":1,"copyDoneQpcNs":2,"sendQpcNs":3,"framesUniform":480}}"#);
            let descriptor:Descriptor=serde_json::from_str(&text).unwrap();
            let mut payload=vec![0u8;3*(32+480*4)];
            for (index,typ) in types.iter().enumerate() {
                payload[index*32+4..index*32+8].copy_from_slice(&480u32.to_le_bytes());
                payload[index*32+8..index*32+12].copy_from_slice(&typ.to_le_bytes());
            }
            let (slot,keep)=routes.select(&descriptor,&payload,480).unwrap().unwrap();
            assert_eq!(slot,0);assert!(keep);
            assert_eq!(routes.routes[slot].dynamic_objects,if sequence==0 {2} else {0});
            assert!(routes.routes[slot].ever_dynamic);
        }
    }
    fn route_packet(routes: &mut PriorityRoutes, block: u64, sequence: u64, time: u64) -> Option<(usize, bool)> {
        let wire = format!(r#"{{"block":"0x{block:x}","sequence":{sequence},"count":1,"captureQpcNs":{time},"copyDoneQpcNs":{time},"sendQpcNs":{time},"framesUniform":480}}"#);
        let descriptor = serde_json::from_str(&wire).unwrap();
        let mut payload = [0; 32 + 480 * 4];
        payload[4..8].copy_from_slice(&480u32.to_le_bytes());
        payload[8..12].copy_from_slice(&1u32.to_le_bytes());
        routes.select(&descriptor, &payload, 480).unwrap()
    }

    #[test]
    fn unified_overflow_keeps_active_routes_and_receiver_running() {
        let mut routes = PriorityRoutes::new([1;16], 8, 100);
        routes.submit_all = true;
        for index in 0..8 {
            assert_eq!(route_packet(&mut routes, 0x100 + index, 0, 100), Some((index as usize, true)));
        }
        assert_eq!(route_packet(&mut routes, 0x108, 0, 101), None);
        assert_eq!(route_packet(&mut routes, 0x108, 1, 500), None);
        assert_eq!(route_packet(&mut routes, 0x100, 1, 501), Some((0, true)));
        assert_eq!(routes.capacity_drops, 2);
        assert_eq!(routes.recycled_routes, 0);
        let live = routes.live.snapshot(8);
        assert_eq!(live["capacityDrops"], 2);
        assert_eq!(live["registeredSlots"], 8);
        assert_eq!(live["leases"], serde_json::json!(vec![1; 8]));
    }

    #[test]
    fn ninth_source_reuses_an_idle_lane_after_eight_sources_close() {
        let mut routes = PriorityRoutes::new([1;16], 8, 100);
        routes.submit_all = true;
        routes.allow_recycling = true;
        for index in 0..8 {
            assert_eq!(route_packet(&mut routes, 0x100 + index, 0, 100 + index), Some((index as usize, true)));
            routes.routes[index as usize].submitted = 1;
        }
        assert_eq!(route_packet(&mut routes, 0x108, 0, 211), Some((0, true)));
        assert_eq!(route_packet(&mut routes, 0x108, 1, 212), Some((0, true)));
        assert_eq!(routes.routes[0].submitted, 1);
        assert_eq!(routes.capacity_drops, 0);
        assert_eq!(routes.recycled_routes, 1);
        let live = routes.live.snapshot(8);
        assert_eq!(live["recycledRoutes"], 1);
        assert_eq!(live["leases"][0], 2);
        assert_eq!(live["leases"][1], 1);
    }

    #[test]
    fn opt_in_recycling_reuses_oldest_route_and_keeps_transport_sequence() {
        let mut routes = PriorityRoutes::new([1;16], 2, 100);
        routes.submit_all = true;
        routes.allow_recycling = true;
        assert_eq!(route_packet(&mut routes, 0x100, 0, 100), Some((0, true)));
        routes.routes[0].submitted = 7;
        assert_eq!(route_packet(&mut routes, 0x101, 0, 180), Some((1, true)));
        assert_eq!(route_packet(&mut routes, 0x102, 5, 200), None);
        assert_eq!(route_packet(&mut routes, 0x102, 6, 201), Some((0, true)));
        assert_eq!(route_packet(&mut routes, 0x101, 1, 202), Some((1, true)));
        assert_eq!(routes.routes[0].submitted, 7);
        assert_eq!(routes.routes[0].first, Some(6));
        assert_eq!(routes.routes[0].lease, 2);
        assert_eq!(routes.routes[1].lease, 1);
        assert_eq!(routes.capacity_drops, 1);
        assert_eq!(routes.recycled_routes, 1);
    }

    #[test]
    fn leased_route_restarts_same_address_without_waiting_for_idle_timeout() {
        let mut routes = PriorityRoutes::new([1;16], 2, 500);
        routes.submit_all = true;
        routes.allow_recycling = true;
        assert_eq!(route_packet(&mut routes, 0x100, 40, 100), Some((0, true)));
        routes.routes[0].submitted = 7;
        assert_eq!(route_packet(&mut routes, 0x101, 0, 101), Some((1, true)));
        assert_eq!(route_packet(&mut routes, 0x100, 0, 110), Some((0, true)));
        assert_eq!(routes.routes[0].lease, 2);
        assert_eq!(routes.routes[0].submitted, 7);
        assert_eq!(route_packet(&mut routes, 0x100, 41, 105), None);
        assert_eq!(route_packet(&mut routes, 0x100, 0, 110), None);
        assert_eq!(route_packet(&mut routes, 0x100, 1, 120), Some((0, true)));
        assert_eq!(route_packet(&mut routes, 0x101, 1, 121), Some((1, true)));
        assert_eq!(routes.live.snapshot(2)["staleSourceDrops"], 2);
        assert_eq!(routes.routes[1].lease, 1);
    }

    #[test]
    fn retired_source_packets_cannot_reclaim_a_slot() {
        let mut routes = PriorityRoutes::new([1;16], 1, 100);
        routes.submit_all = true;
        routes.allow_recycling = true;
        assert_eq!(route_packet(&mut routes, 0x100, 0, 100), Some((0, true)));
        assert_eq!(route_packet(&mut routes, 0x101, 0, 201), Some((0, true)));
        assert_eq!(route_packet(&mut routes, 0x100, 1, 200), None);
        assert_eq!(route_packet(&mut routes, 0x999, 0, 50), None);
        assert_eq!(route_packet(&mut routes, 0x101, 1, 202), Some((0, true)));
        assert_eq!(routes.routes[0].lease, 2);
        assert_eq!(routes.live.snapshot(1)["staleSourceDrops"], 2);
    }

    fn packet(extra: &str) -> String { format!(r#"{{"block":"0x123","sequence":2,"count":2,"captureQpcNs":1,"copyDoneQpcNs":2,"sendQpcNs":3,{extra}}}"#) }
    #[test] fn descriptor_contract() {
        for frames in [r#""framesUniform":480"#, r#""frames":[480,480]"#] {
            let text=packet(frames); let d: Descriptor=serde_json::from_str(&text).unwrap();
            assert_eq!(validate(&d,3904,480,Some(0x123),Some(1)),Ok(0x123));
            assert!(validate(&d,3903,480,None,None).is_err());
            assert!(validate(&d,3904,480,Some(0x456),None).is_err());
            assert!(validate(&d,3904,480,None,Some(2)).is_err());
        }
    }
    #[test] fn reject_ambiguous_and_variable_frames() {
        for frames in [r#""framesUniform":480,"frames":[480,480]"#,r#""frames":[480,479]"#,r#""frames":[]"#] {
            let text=packet(frames);let d:Descriptor=serde_json::from_str(&text).unwrap();assert!(validate(&d,3904,480,None,None).is_err());
        }
        let text=packet(r#""framesUniform":480,"count":2"#);assert!(serde_json::from_str::<Descriptor>(&text).is_err());
    }
    #[test] fn fixed_frame_bound() { let text=format!("[{}]",vec!["480";161].join(","));assert!(serde_json::from_str::<Frames>(&text).is_err()); }
    #[test] fn wire_layout() { assert_eq!(SLOT,2634764);assert_eq!(RING,42156240);assert_eq!(SLOT%4,0); }
    #[test] fn priority_route_accepts_714_and_rejects_whole_mixed_packet() {
        fn select(types: &[u32]) -> (bool, u64) {
            let text=format!(r#"{{"block":"0x123","sequence":0,"count":{},"captureQpcNs":1,"copyDoneQpcNs":2,"sendQpcNs":3,"framesUniform":480}}"#,types.len());
            let d:Descriptor=serde_json::from_str(&text).unwrap();
            let mut payload=vec![0u8;types.len()*(32+480*4)];
            for (i,typ) in types.iter().enumerate(){
                payload[i*32+4..i*32+8].copy_from_slice(&480u32.to_le_bytes());
                payload[i*32+8..i*32+12].copy_from_slice(&typ.to_le_bytes());
            }
            let mut routes=PriorityRoutes::new([1;16],4,1_000_000);
            let (slot,accepted)=routes.select(&d,&payload,480).unwrap().unwrap();
            (accepted,routes.routes[slot].unsupported)
        }
        let standard=[1,2,4,8,16,32,64,128,256,512,1024,2048,4096];
        assert_eq!(select(&standard),(true,0));
        for typ in standard {assert_eq!(select(&[typ]),(true,0));}
        for typ in [0,3,8192,16384,32768,65536,131072,262144,524288] {
            assert_eq!(select(&[1,2,typ]),(false,1));
        }
    }
}
