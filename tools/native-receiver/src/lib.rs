//! Windows x64, one reader, NSP v1 ring. No Python calls on the worker.
//! Control caller must stop producer before finish and keep the pipeline/DLL alive
//! until finish succeeds. Failures retain an unconsumed slot and fail closed.
use serde::{Deserialize, Serialize};
use std::{ffi::c_void, sync::{Arc, atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering}}, thread, time::{Duration, Instant}};
const CAP: usize = 16;
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
enum Submission { Plain(Submit), Timed(SubmitTimed) }
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
struct Trace { sequence: u64, capture_qpc_ns: u64, send_qpc_ns: u64, acquired_qpc_ns: u64, parsed_qpc_ns: u64, submitted_qpc_ns: u64 }
struct Statistics { hist: [Histogram; 6], tails: Vec<Trace>, tails_overflow: u64, error: Option<String>, block: Option<u64>, first: Option<u64>, last: Option<u64>, thread_id: u32 }
impl Statistics {
    fn new() -> Self { Self { hist: std::array::from_fn(|_| Histogram::new()), tails: Vec::with_capacity(256), tails_overflow: 0, error: None, block: None, first: None, last: None, thread_id: 0 } }
}
struct Control { map: Arc<Mapping>, stopping: Arc<AtomicBool>, received: Arc<AtomicU64>, failed: Arc<AtomicBool>, worker: Option<thread::JoinHandle<Statistics>>, report: Option<Vec<u8>> }
fn run(map: Arc<Mapping>, stopping: Arc<AtomicBool>, received: Arc<AtomicU64>, failed: Arc<AtomicBool>, pipeline: usize, submit: Submission, frames: u32, hz: u64, timing: bool, mmcss: bool, mut stats: Statistics) -> Statistics {
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
            let block = validate(&d, nd, frames, stats.block, stats.last)?;
            let parsed = if timing { now(hz) } else { 0 };
            if timing && d.send_qpc_ns > acquired { return Err("QPC clocks not aligned".into()); }
            // Existing renderer copies synchronously to its preallocated pool.
            // Never release the slot while it can still read the borrowed pointer.
            let rc = unsafe { match submit {
                Submission::Plain(f) => f(pipeline as *mut _, d.sequence, d.count, ptr.add(12+HEADER), nd),
                Submission::Timed(f) => f(pipeline as *mut _, d.sequence, d.count, ptr.add(12+HEADER), nd, d.capture_qpc_ns),
            }};
            if rc != 0 { return Err(format!("Native submit failed: {rc}")); }
            let end = if timing { now(hz) } else { 0 };
            stats.block = Some(block); stats.first.get_or_insert(d.sequence); stats.last = Some(d.sequence);
            if timing {
                for (h, ns) in stats.hist.iter_mut().zip([d.send_qpc_ns-d.copy_done_qpc_ns, acquired-d.send_qpc_ns, parsed-acquired, end-parsed, end-d.capture_qpc_ns, d.copy_done_qpc_ns-d.capture_qpc_ns]) { h.add(ns); }
                if end-d.capture_qpc_ns >= 5_000_000 {
                    if stats.tails.len() < stats.tails.capacity() { stats.tails.push(Trace { sequence: d.sequence, capture_qpc_ns: d.capture_qpc_ns, send_qpc_ns: d.send_qpc_ns, acquired_qpc_ns: acquired, parsed_qpc_ns: parsed, submitted_qpc_ns: end }); }
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
    unsafe { start_impl(name, event, pipeline, submit.map(Submission::Plain), frames, timing) }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_start_full(name: *const u16, event: *const u16, pipeline: *mut c_void, submit: Option<SubmitTimed>, frames: u32, timing: u32) -> *mut c_void {
    unsafe { start_impl(name, event, pipeline, submit.map(Submission::Timed), frames, timing | 1) }
}
unsafe fn start_impl(name: *const u16, event: *const u16, pipeline: *mut c_void, submit: Option<Submission>, frames: u32, timing: u32) -> *mut c_void {
    START_ERROR.set(0);
    if name.is_null() || event.is_null() || pipeline.is_null() || submit.is_none() || !(1..=4096).contains(&frames) { start_error(1, 0); return std::ptr::null_mut(); }
    std::panic::catch_unwind(|| {
        let map = Arc::new(unsafe { Mapping::open(name, event) }?);
        let stopping = Arc::new(AtomicBool::new(false)); let received = Arc::new(AtomicU64::new(0)); let failed = Arc::new(AtomicBool::new(false));
        let mut hz = 0; if unsafe { QueryPerformanceFrequency(&mut hz) } == 0 || hz <= 0 { start_error(6, unsafe { GetLastError() }); return None; }
        let stats = Statistics::new(); // Allocate/touch statistics on control thread.
        let (m,s,r,f) = (map.clone(),stopping.clone(),received.clone(),failed.clone());
        let pipeline = pipeline as usize;
        let worker = thread::Builder::new().name("nsp-rust-receiver".into()).spawn(move || run(m,s,r,f,pipeline,submit.unwrap(),frames,hz as u64,timing & 1 != 0,timing & 2 != 0,stats)).map_err(|e| start_error(7, e.raw_os_error().unwrap_or(0) as u32)).ok()?;
        Some(Box::into_raw(Box::new(Control { map, stopping, received, failed, worker: Some(worker), report: None })) as *mut c_void)
    }).map_err(|_| start_error(8, 0)).ok().flatten().unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_receiver_received(p: *const c_void) -> u64 { if p.is_null() { return 0; } unsafe { &*(p as *const Control) }.received.load(Ordering::Acquire) }
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
            c.report = Some(serde_json::to_vec(&serde_json::json!({"backend":"rust-shared-memory-borrow","received":c.received.load(Ordering::Acquire),"passed":stats.error.is_none(),"errors":stats.error.iter().collect::<Vec<_>>(),"threadStopped":true,"threadId":stats.thread_id,"block":stats.block.map(|b|format!("0x{b:x}")),"firstSequence":stats.first,"lastSequence":stats.last,"timing":{"units":"ms","stages":stages},"tails":stats.tails,"tailOverflow":stats.tails_overflow,"receiverPcmCopies":0})).unwrap());
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
}
