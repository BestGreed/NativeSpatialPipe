use crate::{
    ptp::{self, Follower, codec::ClockId},
    rtp::{self, Flow, Grid, Output, ring::Ring},
    sdp::{self, Reference, Sap},
    time::{ClockFit, NS_PER_MS, TimestampMode, timestamp_source},
};
use std::{
    collections::VecDeque,
    io,
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub const INVALID: i32 = -22;
pub const STATE: i32 = -2;
pub const IO: i32 = -3;
pub const UNAVAILABLE: i32 = -4;
pub const LOCK_TIMEOUT: i32 = -5;
pub const JOIN_TIMEOUT: i32 = -6;
pub const QUEUE_FULL: i32 = -7;
pub const WORKER_FAILURE: i32 = -8;
pub const BUFFER_TOO_SMALL: i32 = -9;

const STARTED: u32 = 1;
const SENDER_EXITED: u32 = 2;
const STOP_COMPLETE: u32 = 3;
const PTP_LOST: u32 = 4;
const MAX_EVENTS: usize = 32;
const SAP_INTERVAL_NS: i128 = 300_000_000_000;

#[repr(C)]
#[derive(Clone, Copy, Default, Debug)]
pub struct Event {
    pub kind: u32,
    pub code: i32,
    pub detail: i32,
}

#[repr(C)]
#[derive(Clone, Copy, Default, Debug)]
pub struct Counters {
    pub packets: u64,
    pub silent: u64,
    pub dropped: u64,
    pub late: u64,
    pub errors: u64,
    pub bytes: u64,
    pub locked: u32,
    pub timestamping: u32,
    pub offset_ns: f64,
    pub rate_ppm: f64,
    pub fit_rms_ns: f64,
}

pub struct Config {
    pub channels: usize,
    pub inputs: usize,
    pub map: Vec<usize>,
    pub labels: Vec<String>,
    pub name: String,
    pub capacity: usize,
    pub prebuffer: usize,
    pub ptp: ptp::Config,
    pub mode: TimestampMode,
    pub lock_timeout: Duration,
    pub flows: Vec<Flow>,
    pub sap: bool,
}

#[derive(Clone, Copy, Debug)]
struct Failure {
    code: i32,
    detail: i32,
}
impl Failure {
    fn new(code: i32) -> Self {
        Self { code, detail: 0 }
    }
}
impl From<io::Error> for Failure {
    fn from(err: io::Error) -> Self {
        Self {
            code: IO,
            detail: err.raw_os_error().unwrap_or(0),
        }
    }
}

type Events = Arc<Mutex<VecDeque<Event>>>;
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|err| err.into_inner())
}
fn event(events: &Events, kind: u32, failure: Failure) {
    let mut events = lock(events);
    if events.len() == MAX_EVENTS {
        events.pop_front();
    }
    events.push_back(Event {
        kind,
        code: failure.code,
        detail: failure.detail,
    });
}

struct Audio {
    ring: Ring,
    accept: bool,
    discard: bool,
}
struct Run {
    config: Arc<Config>,
    audio: Mutex<Audio>,
    cancel: AtomicBool,
    counters: Mutex<Counters>,
    descriptions: Mutex<Vec<String>>,
}

struct Worker {
    join: JoinHandle<()>,
    run: Arc<Run>,
}

#[derive(Default)]
pub struct Session {
    config: Mutex<Option<Arc<Config>>>,
    worker: Mutex<Option<Worker>>,
    audio_handle: Mutex<Option<Arc<Run>>>,
    events: Events,
    last: Mutex<Counters>,
}

impl Session {
    pub fn configure(&self, config: Config) -> i32 {
        if lock(&self.worker).is_some() {
            return STATE;
        }
        *lock(&self.config) = Some(Arc::new(config));
        0
    }

    pub fn start(&self) -> i32 {
        let mut worker = lock(&self.worker);
        if worker.is_some() {
            return STATE;
        }
        let Some(config) = lock(&self.config).clone() else {
            return STATE;
        };
        *lock(&self.last) = Counters::default();
        let (mode, source) = match timestamp_source(config.mode) {
            Ok(pair) => pair,
            Err(_) => return UNAVAILABLE,
        };
        let network = match Network::open(&config, source) {
            Ok(network) => network,
            Err(failure) => return failure.code,
        };
        let run = Arc::new(Run {
            audio: Mutex::new(Audio {
                ring: Ring::new(config.capacity, config.channels),
                accept: true,
                discard: false,
            }),
            config,
            cancel: AtomicBool::new(false),
            counters: Mutex::new(Counters {
                timestamping: mode as u32,
                ..Counters::default()
            }),
            descriptions: Mutex::new(Vec::new()),
        });
        let (start_tx, start_rx) = mpsc::sync_channel(1);
        let thread_run = Arc::clone(&run);
        let events = Arc::clone(&self.events);
        let join = match thread::Builder::new()
            .name("nsp-aes67".into())
            .spawn(move || {
                let mut startup = Some(start_tx);
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    network.run(&thread_run, &events, &mut startup)
                }))
                .unwrap_or_else(|_| Err(Failure::new(WORKER_FAILURE)));
                {
                    let mut audio = lock(&thread_run.audio);
                    audio.accept = false;
                    audio.ring.clear();
                }
                lock(&thread_run.counters).locked = 0;
                lock(&thread_run.descriptions).clear();
                match (startup, outcome) {
                    (Some(tx), Err(failure)) => {
                        let _ = tx.send(Err(failure));
                    }
                    (Some(tx), Ok(())) => {
                        let _ = tx.send(Err(Failure::new(STATE)));
                    }
                    (None, Err(failure)) => event(&events, SENDER_EXITED, failure),
                    (None, Ok(())) => (),
                }
            }) {
            Ok(join) => join,
            Err(_) => return IO,
        };
        *lock(&self.audio_handle) = Some(Arc::clone(&run));
        match start_rx.recv() {
            Ok(Ok(())) => {
                *worker = Some(Worker { join, run });
                0
            }
            response => {
                let _ = join.join();
                *lock(&self.audio_handle) = None;
                *lock(&self.last) = *lock(&run.counters);
                response
                    .ok()
                    .and_then(Result::err)
                    .map_or(WORKER_FAILURE, |failure| failure.code)
            }
        }
    }

    pub fn stop(&self, timeout_ms: u32) -> i32 {
        let mut worker = lock(&self.worker);
        let Some(current) = &*worker else {
            return 0;
        };
        current.run.cancel.store(true, Ordering::Release);
        *lock(&self.audio_handle) = None;
        let deadline = Instant::now() + Duration::from_millis(u64::from(timeout_ms));
        while !current.join.is_finished() {
            if Instant::now() >= deadline {
                return JOIN_TIMEOUT;
            }
            thread::sleep(Duration::from_micros(100));
        }
        let current = worker.take().unwrap();
        let joined = current.join.join();
        *lock(&self.last) = *lock(&current.run.counters);
        drop(current.run);
        if joined.is_err() {
            return WORKER_FAILURE;
        }
        event(&self.events, STOP_COMPLETE, Failure::new(0));
        0
    }

    pub fn poll(&self) -> Option<Event> {
        lock(&self.events).pop_front()
    }

    pub fn counters(&self) -> Counters {
        let worker = lock(&self.worker);
        worker
            .as_ref()
            .map_or_else(|| *lock(&self.last), |w| *lock(&w.run.counters))
    }

    pub fn with_sdp(&self, index: usize, use_sdp: impl FnOnce(&str) -> i32) -> i32 {
        let worker = lock(&self.worker);
        let Some(worker) = &*worker else {
            return STATE;
        };
        if index >= worker.run.config.flows.len() {
            return INVALID;
        }
        let descriptions = lock(&worker.run.descriptions);
        descriptions.get(index).map_or(STATE, |sdp| use_sdp(sdp))
    }

    /// The caller keeps the PCM readable until this function returns. Shared
    /// handle and ring locks are released before returning to the C release hook.
    pub unsafe fn submit(
        &self,
        first: u64,
        pcm: *const f32,
        frames: usize,
        channels: usize,
    ) -> i32 {
        // Keep the handle guard until the ring copy returns. Stop waits for any
        // in-progress copy before dropping its last references and acknowledging.
        let handle = lock(&self.audio_handle);
        let Some(run) = &*handle else {
            return STATE;
        };
        if channels != run.config.inputs {
            return INVALID;
        }
        let mut audio = lock(&run.audio);
        if !audio.accept {
            return STATE;
        }
        if audio.discard {
            lock(&run.counters).dropped += frames as u64;
            return 0;
        }
        if frames > run.config.capacity {
            lock(&run.counters).dropped += frames as u64;
            return QUEUE_FULL;
        }
        let input = unsafe { std::slice::from_raw_parts(pcm, frames * channels) };
        match audio.ring.copy(first, input, channels, &run.config.map) {
            Some(accepted) => {
                lock(&run.counters).late += accepted.late;
                0
            }
            None => {
                lock(&run.counters).dropped += frames as u64;
                QUEUE_FULL
            }
        }
    }
}

struct Network {
    ptp: Follower,
    outputs: Vec<Output>,
    sap: Vec<Sap>,
    id: u64,
}
impl Network {
    fn open(
        config: &Config,
        source: Box<dyn crate::time::TimestampSource>,
    ) -> Result<Self, Failure> {
        let ptp = Follower::open(config.ptp.clone(), source)?;
        let outputs: Vec<_> = config
            .flows
            .iter()
            .cloned()
            .map(Output::open)
            .collect::<io::Result<_>>()?;
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;
        let sap = if config.sap {
            outputs
                .iter()
                .enumerate()
                .map(|(i, flow)| Sap::open(flow, (id as u16).wrapping_add(i as u16)))
                .collect::<io::Result<_>>()?
        } else {
            Vec::new()
        };
        Ok(Self {
            ptp,
            outputs,
            sap,
            id,
        })
    }

    fn run(
        mut self,
        run: &Run,
        events: &Events,
        startup: &mut Option<mpsc::SyncSender<Result<(), Failure>>>,
    ) -> Result<(), Failure> {
        let mut stream = Stream::new(run.config.channels, self.outputs.len());
        let mut deadline = Instant::now() + run.config.lock_timeout;
        let outcome = loop {
            if run.cancel.load(Ordering::Acquire) {
                break Ok(());
            }
            if let Err(error) = self.ptp.tick() {
                break Err(error.into());
            }
            let now = self.ptp.now_ns();
            let clock = self.ptp.locked();
            if stream.gm.is_some() && stream.gm != clock.map(|(gm, _)| gm) {
                self.deactivate(run, &mut stream, true);
                event(events, PTP_LOST, Failure::new(0));
                deadline = Instant::now() + run.config.lock_timeout;
            }
            if let Some((gm, fit)) = clock {
                if stream.gm.is_none() {
                    self.activate(run, &mut stream, gm, now);
                    self.update_clock(run, now, fit);
                    if let Some(tx) = startup.take() {
                        event(events, STARTED, Failure::new(0));
                        let _ = tx.send(Ok(()));
                    }
                }
                self.update_clock(run, now, fit);
                if now >= stream.next_sap && !self.sap.is_empty() {
                    self.announce(run, &stream.descriptions, false);
                    stream.next_sap = now + SAP_INTERVAL_NS;
                }
                let ptp_ns = fit.ptp_ns(now);
                if let Some((stamp, skipped)) = stream.grid.due(ptp_ns) {
                    if let Err(error) = self.send_interval(run, &mut stream, stamp, skipped) {
                        break Err(error);
                    }
                }
                let remaining = NS_PER_MS - ptp_ns.rem_euclid(NS_PER_MS);
                if remaining > 250_000 {
                    thread::sleep(Duration::from_nanos(
                        (remaining - 180_000).min(300_000) as u64
                    ));
                } else {
                    let until = now + remaining.min(180_000);
                    while self.ptp.now_ns() < until {
                        std::hint::spin_loop();
                    }
                }
            } else {
                if Instant::now() >= deadline {
                    break Err(Failure::new(LOCK_TIMEOUT));
                }
                thread::sleep(Duration::from_micros(100));
            }
        };
        self.deactivate(run, &mut stream, false);
        outcome
    }

    fn activate(&mut self, run: &Run, stream: &mut Stream, gm: ClockId, now: i128) {
        stream.gm = Some(gm);
        stream.grid = Grid::default();
        stream.version += 1;
        stream.next_sap = now;
        stream.descriptions = self
            .outputs
            .iter()
            .enumerate()
            .map(|(i, output)| {
                sdp::describe(
                    &run.config.name,
                    output,
                    i,
                    &run.config.labels,
                    Some(Reference {
                        gm,
                        domain: run.config.ptp.domain,
                    }),
                    self.id.wrapping_add(i as u64),
                    stream.version,
                )
            })
            .collect();
        *lock(&run.descriptions) = stream.descriptions.clone();
        lock(&run.audio).discard = false;
        for output in &mut self.outputs {
            output.discontinuity();
        }
    }

    fn deactivate(&self, run: &Run, stream: &mut Stream, rediscover: bool) {
        lock(&run.descriptions).clear();
        {
            let mut audio = lock(&run.audio);
            let discarded = audio.ring.frames() as u64;
            audio.ring.clear();
            audio.accept = rediscover;
            audio.discard = rediscover;
            let mut counters = lock(&run.counters);
            counters.locked = 0;
            counters.dropped += discarded;
        }
        if stream.gm.take().is_some() {
            self.announce(run, &stream.descriptions, true);
        }
        stream.prebuffered = false;
    }

    fn update_clock(&self, run: &Run, now: i128, fit: ClockFit) {
        let mut c = lock(&run.counters);
        c.locked = 1;
        c.offset_ns = fit.offset_ns(now) as f64;
        c.rate_ppm = fit.rate_ppm;
        c.fit_rms_ns = fit.rms_ns;
    }

    fn announce(&self, run: &Run, descriptions: &[String], delete: bool) {
        let failures = self
            .sap
            .iter()
            .zip(descriptions)
            .filter(|(sap, sdp)| sap.publish(sdp, delete).is_err())
            .count();
        if failures != 0 {
            lock(&run.counters).errors += failures as u64;
        }
    }

    fn send_interval(
        &mut self,
        run: &Run,
        stream: &mut Stream,
        stamp: u32,
        skipped: u64,
    ) -> Result<(), Failure> {
        let silent = {
            let mut audio = lock(&run.audio);
            let mut counters = lock(&run.counters);
            let skipped_frames = skipped.saturating_mul(rtp::FRAMES as u64);
            counters.late = counters.late.saturating_add(skipped_frames);
            if stream.prebuffered {
                audio.ring.skip(skipped_frames);
            }
            stream.prebuffered |= audio.ring.frames() >= run.config.prebuffer;
            if stream.prebuffered {
                audio.ring.consume(&mut stream.samples)
            } else {
                stream.samples.fill(0.0);
                true
            }
        };
        let mut packets = 0;
        let mut bytes = 0;
        let mut errors = 0;
        let mut fatal = None;
        for (channel, output) in self.outputs.iter_mut().enumerate() {
            if skipped != 0 {
                output.discontinuity();
            }
            match output.send(stamp, &stream.samples, run.config.channels) {
                Ok(size) => {
                    packets += 1;
                    bytes += size as u64;
                    stream.io_streak[channel] = 0;
                }
                Err(err) => {
                    errors += 1;
                    if err.kind() != io::ErrorKind::WouldBlock {
                        stream.io_streak[channel] += 1;
                        if stream.io_streak[channel] >= 32 {
                            fatal = Some(Failure::from(err));
                        }
                    }
                }
            }
        }
        let mut c = lock(&run.counters);
        c.packets += packets;
        c.bytes += bytes;
        c.errors += errors;
        if silent {
            c.silent += packets;
        }
        fatal.map_or(Ok(()), Err)
    }
}

struct Stream {
    gm: Option<ClockId>,
    grid: Grid,
    prebuffered: bool,
    samples: Vec<f32>,
    descriptions: Vec<String>,
    version: u64,
    next_sap: i128,
    io_streak: Vec<u32>,
}
impl Stream {
    fn new(channels: usize, flows: usize) -> Self {
        Self {
            gm: None,
            grid: Grid::default(),
            prebuffered: false,
            samples: vec![0.0; channels * rtp::FRAMES],
            descriptions: Vec::new(),
            version: 0,
            next_sap: 0,
            io_streak: vec![0; flows],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv4Addr;
    #[test]
    fn join_timeout_retains_handle_for_a_clean_retry() {
        let session = Session::default();
        let (finish, wait) = mpsc::channel::<()>();
        let join = thread::spawn(move || {
            wait.recv().unwrap();
        });
        let run = Arc::new(Run {
            config: Arc::new(Config {
                channels: 1,
                inputs: 1,
                map: vec![0],
                labels: vec!["CH1".into()],
                name: "test".into(),
                capacity: 48,
                prebuffer: 48,
                mode: TimestampMode::Auto,
                ptp: ptp::Config {
                    domain: 0,
                    pinned_gm: None,
                    interface: Ipv4Addr::LOCALHOST,
                    multicast: Ipv4Addr::new(224, 0, 1, 129),
                    unicast_master: Some(Ipv4Addr::LOCALHOST),
                    event_port: 319,
                    general_port: 320,
                    local_event_port: 0,
                    local_general_port: 0,
                    loss_ns: 1_000_000_000,
                },
                lock_timeout: Duration::from_secs(1),
                flows: vec![],
                sap: false,
            }),
            audio: Mutex::new(Audio {
                ring: Ring::new(48, 1),
                accept: true,
                discard: false,
            }),
            cancel: AtomicBool::new(false),
            counters: Mutex::new(Counters::default()),
            descriptions: Mutex::new(Vec::new()),
        });
        *lock(&session.worker) = Some(Worker { join, run });
        assert_eq!(session.stop(0), JOIN_TIMEOUT);
        assert!(session.poll().is_none());
        finish.send(()).unwrap();
        assert_eq!(session.stop(1000), 0);
        assert_eq!(session.poll().unwrap().kind, STOP_COMPLETE);
        assert!(lock(&session.worker).is_none());
    }
}
