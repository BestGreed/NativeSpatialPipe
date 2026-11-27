use crate::{
    ptp::{self, MediaSource, OpenError, PtpMode, PtpRuntime, Role},
    rtp::{self, Encoding, Flow, Grid, Output, ring::Ring},
    sdp::{self, Reference, Sap},
    time::{
        ClockFit, NS_PER_MS, PacketTiming, RATE_LIMIT_PPM, TimestampMode, TimestampSource,
        fit_rms_limit_ns, timestamp_source,
    },
};
use std::{
    collections::VecDeque,
    io::{self, Write},
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
/// EXTENSION: gm mode was requested but the interface MAC could not provide an
/// EUI-64 clock identity, so the sender refuses to fake a grandmaster identity.
pub const PTP_IDENTITY: i32 = -10;

const STARTED: u32 = 1;
const SENDER_EXITED: u32 = 2;
const STOP_COMPLETE: u32 = 3;
const PTP_LOST: u32 = 4;
const MAX_EVENTS: usize = 32;
/// DNS-SD/SAP: repeat a deletion three times for loss robustness, as the
/// daemon does (sap.hpp max_deletions = 3).
const SAP_DELETE_REPEATS: u32 = 3;

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
    pub sap_interval: Duration,
    pub sap_destination: std::net::Ipv4Addr,
    pub timing: PacketTiming,
    pub encoding: Encoding,
    pub redundant: bool,
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

/// One announced SDP and its RFC 2974 message identifier hash. The hash is a
/// CRC-16 of exactly the bytes announced, so any description change yields a
/// new identity and a deletion can reference the old one.
#[derive(Clone)]
struct Description {
    sdp: String,
    hash: u16,
}

struct Run {
    config: Arc<Config>,
    audio: Mutex<Audio>,
    cancel: AtomicBool,
    counters: Mutex<Counters>,
    /// Latest PTP status snapshot, refreshed every worker iteration for the
    /// nsp_aes67_ptp_status export (DESIGN-TOPO.md section 6).
    ptp_status: Mutex<Option<ptp::PtpStatus>>,
    descriptions: Mutex<Vec<Description>>,
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
            ptp_status: Mutex::new(None),
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

    /// Latest PTP mode/role/source snapshot (EXTENSION export; the counters
    /// struct is size-stable so the role is reported through this function and
    /// the diagnostics log line).
    pub fn ptp_status(&self) -> Option<ptp::PtpStatus> {
        lock(&self.audio_handle)
            .as_ref()
            .and_then(|run| *lock(&run.ptp_status))
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
        descriptions
            .get(index)
            .map_or(STATE, |sdp| use_sdp(&sdp.sdp))
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

mod pacer;

struct Network {
    ptp: PtpRuntime,
    outputs: Vec<Output>,
    /// ST 2022-7 duplicate legs on the secondary port (primary + 2). Empty when
    /// redundancy is disabled.
    secondary: Vec<Output>,
    sap: Vec<Sap>,
    id: u64,
}
impl Network {
    fn open(config: &Config, source: Box<dyn TimestampSource>) -> Result<Self, Failure> {
        let ptp = PtpRuntime::open(config.ptp.clone(), source).map_err(|error| match error {
            OpenError::Io(error) => Failure::from(error),
            OpenError::GmIdentityUnavailable => Failure::new(PTP_IDENTITY),
        })?;
        let outputs: Vec<_> = config
            .flows
            .iter()
            .cloned()
            .map(|flow| Output::open(flow, config.encoding))
            .collect::<io::Result<_>>()?;
        let secondary: Vec<Output> = if config.redundant {
            config
                .flows
                .iter()
                .cloned()
                .map(|flow| {
                    let mut flow = flow;
                    let secondary_port = flow.destination.port().checked_add(2).ok_or_else(|| {
                        io::Error::new(io::ErrorKind::InvalidInput, "redundant secondary port overflows")
                    })?;
                    flow.destination = std::net::SocketAddrV4::new(*flow.destination.ip(), secondary_port);
                    Output::open(flow, config.encoding)
                })
                .collect::<io::Result<_>>()?
        } else {
            Vec::new()
        };
        let id = stable_session_id(config);
        let sap = if config.sap {
            outputs
                .iter()
                .map(|output| Sap::open(output, config.sap_destination))
                .collect::<io::Result<_>>()?
        } else {
            Vec::new()
        };
        Ok(Self {
            ptp,
            outputs,
            secondary,
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
        let mut stream = Stream::new(
            run.config.channels,
            self.outputs.len() + self.secondary.len(),
            run.config.timing,
        );
        let holdover_ms = std::env::var("NSP_AES67_HOLDOVER_MS")
            .ok()
            .and_then(|value| value.parse::<u32>().ok())
            .filter(|ms| (1000..=3000).contains(ms))
            .unwrap_or(2000);
        let holdover_ns = i128::from(holdover_ms) * NS_PER_MS;
        let diagnostic = std::env::var("NSP_AES67_PTP_DIAGNOSTICS").as_deref() == Ok("1");
        let max_catch_up = std::env::var("NSP_AES67_CATCH_UP_SLOTS")
            .ok()
            .and_then(|value| value.parse::<u32>().ok())
            .filter(|slots| (1..=10).contains(slots))
            .unwrap_or(8);
        let pacer = pacer::Pacer::new();
        let spin_ns = (run.config.timing.slot_period_ns() / 8).clamp(1, 500_000);
        let mut next_log = 0;
        let mut deadline = Instant::now() + run.config.lock_timeout;
        let mut last_status = self.ptp.status();
        let mut last_role = last_status.role;
        let outcome = loop {
            if run.cancel.load(Ordering::Acquire) {
                break Ok(());
            }
            let now = self.ptp.now_ns();
            // Session-owned timing signals for the role state machine: a real
            // holdover expiry is a fallback trigger in auto mode, and the
            // startup lock deadline distinguishes degraded from failed startup.
            let lost = stream.holdover_until.is_some_and(|until| now >= until)
                && self.ptp.media_clock().is_none();
            self.ptp.note_loss_expired(lost);
            let lock_expired = self.ptp.media_clock().is_none() && Instant::now() >= deadline;
            self.ptp.note_lock_expired(lock_expired);
            if let Err(error) = self.ptp.tick() {
                break Err(error.into());
            }
            let now = self.ptp.now_ns();
            let status = self.ptp.status();
            *lock(&run.ptp_status) = Some(status);
            let transitioned = status != last_status;
            let planned = status.role != last_role;
            if transitioned {
                last_status = status;
                last_role = status.role;
            }
            let clock = self.ptp.media_clock();
            if clock.is_some() {
                // While a media clock exists the lock deadline only counts the
                // time since the last good clock; it gates startup and the
                // paused rediscovery window, not ordinary holdover.
                deadline = Instant::now() + run.config.lock_timeout;
            }
            let mut log_transition = transitioned;
            if stream.source.is_some()
                && stream.holdover_until.is_none()
                && clock.is_none()
                && !planned
            {
                // Keep RTP and SAP alive during holdover: the announcement is
                // only withdrawn when RTP actually pauses (audit 2026-11-23,
                // issue 3; RFC 2974 requires consistency with the media).
                stream.holdover_until = Some(now + holdover_ns);
                lock(&run.counters).locked = 0;
                event(events, PTP_LOST, Failure::new(0));
                log_transition = true;
            }
            if stream.holdover_until.is_some_and(|until| now >= until) && clock.is_none() {
                // No role fallback produced a media clock. Explicit slave mode
                // keeps the pre-existing behavior: pause, then exit LOCK_TIMEOUT
                // when the lock deadline passes.
                self.deactivate(run, &mut stream, true);
                deadline = Instant::now() + run.config.lock_timeout;
                log_transition = true;
            }
            if let Some((media, fit)) = clock {
                let reference = match (status.role, media) {
                    (Role::Slave | Role::Passive | Role::Gm, MediaSource::Ptp(gm)) => {
                        Reference::Ptp {
                            gm,
                            domain: run.config.ptp.domain,
                        }
                    }
                    _ => Reference::Local,
                };
                if stream.source != Some(media) || stream.reference != Some(reference) {
                    let preserve_grid = stream.source == Some(media)
                        && stream.last_fit.is_some_and(|old| {
                            (old.ptp_ns(now) - fit.ptp_ns(now)).abs() <= NS_PER_MS
                        });
                    self.activate(run, &mut stream, media, reference, now, preserve_grid);
                    stream.holdover_until = None;
                    log_transition = true;
                    self.update_clock(run, now, fit, media);
                    if let Some(tx) = startup.take() {
                        event(events, STARTED, Failure::new(0));
                        let _ = tx.send(Ok(()));
                    }
                }
                stream.last_fit = Some(fit);
                self.update_clock(run, now, fit, media);
            }
            // SAP follows the media: announce while there is a media clock and
            // while RTP is still flowing in holdover. A role change raises the
            // SDP version and changes the hash instead of delete+re-announce
            // flapping (DESIGN-TOPO.md section 4).
            if now >= stream.next_sap
                && !self.sap.is_empty()
                && !stream.descriptions.is_empty()
                && (clock.is_some() || stream.holdover_until.is_some())
            {
                self.announce(run, &stream.descriptions, false);
                stream.next_sap = now
                    + duration_ns(run.config.sap_interval)
                    + rfc2974_offset(run.config.sap_interval, self.id ^ now as u64);
            }
            let pacing_fit = clock
                .map(|(_, fit)| fit)
                .or_else(|| stream.holdover_until.and(stream.last_fit));
            if diagnostic && (log_transition || now >= next_log) {
                let state = if clock.is_some() {
                    "locked"
                } else if stream.holdover_until.is_some() {
                    "holdover"
                } else if startup.is_some() {
                    "acquiring"
                } else {
                    "paused"
                };
                let c = *lock(&run.counters);
                let mode = if c.timestamping == TimestampMode::Hardware as u32 {
                    TimestampMode::Hardware
                } else {
                    TimestampMode::Software
                };
                let unix_ns = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos();
                let pcm = stream.pcm.take();
                let ring_frames = lock(&run.audio).ring.frames();
                let metrics = &stream.pacing;
                let samples = run.config.timing.samples_per_packet();
                let _ = writeln!(
                    io::stderr(),
                    "nsp_aes67 pacing unix_ns={unix_ns} state={state} timer={} priority={} rate={} ptime_us={} samples_per_packet={} max_catch_up_slots={max_catch_up} packetsSent={} framesSent={} due_slots={} caught_up_slots={} dropped_slots={} max_wake_late_us={} p99_wake_late_us={} ring_dropped={} send_errors={} silent_packets={} ring_frames={} consumed_frames={} nonzero_frames={} peak_abs={:.3} nonzero_frames_total={}",
                    pacer.timer(),
                    pacer.priority(),
                    run.config.timing.sample_rate(),
                    run.config.timing.packet_time_us(),
                    samples,
                    c.packets,
                    c.packets.saturating_mul(u64::from(samples)),
                    metrics.due_slots,
                    metrics.caught_up_slots,
                    metrics.dropped_slots,
                    metrics.max_wake_late_us,
                    metrics.p99_wake_late_us(),
                    c.dropped,
                    metrics.send_errors,
                    c.silent,
                    ring_frames,
                    pcm.consumed_frames,
                    pcm.nonzero_frames,
                    pcm.peak_abs,
                    pcm.nonzero_frames_total,
                );
                let gm = status
                    .gm
                    .map(|gm| {
                        gm.iter()
                            .map(|octet| format!("{octet:02X}"))
                            .collect::<Vec<_>>()
                            .join("-")
                    })
                    .unwrap_or_else(|| "-".into());
                let _ = writeln!(
                    io::stderr(),
                    "nsp_aes67 ptp unix_ns={unix_ns} state={state} mode={} role={} source={} gm={} domain={} time_traceable={} ptp_timescale={} reason={} timestamping={mode:?} offset_ns={} rate_ppm={} fit_rms_ns={} rms_limit_ns={} rate_limit_ppm={RATE_LIMIT_PPM} holdover_ms={holdover_ms} sap_interval_ms={} transitions={} {:?} {:?}",
                    status.mode.as_str(),
                    status.role.as_str(),
                    status.source.as_str(),
                    gm,
                    status.domain,
                    status.time_traceable,
                    status.ptp_timescale,
                    status.reason,
                    c.offset_ns,
                    c.rate_ppm,
                    c.fit_rms_ns,
                    fit_rms_limit_ns(mode),
                    run.config.sap_interval.as_millis(),
                    status.transitions,
                    self.ptp.follower_diagnostics(),
                    self.ptp.gm_diagnostics(),
                );
                if self.ptp.identity_unavailable() {
                    let _ = writeln!(
                        io::stderr(),
                        "nsp_aes67 ptp warning=gm_identity_unavailable interface_mac_unreadable"
                    );
                }
                next_log = now + 1_000 * NS_PER_MS;
            }
            if let Some(fit) = pacing_fit {
                // Sample again after PTP processing and diagnostics. Deadline checks
                // before blocking work would undercount lateness and add idle time.
                let ptp_ns = fit.ptp_ns(self.ptp.now_ns());
                if let Some(batch) = stream.grid.due(ptp_ns, max_catch_up) {
                    stream.pacing.batch(&batch);
                    self.drop_backlog(run, &stream, batch.dropped);
                    for slot in batch.slot_range() {
                        self.send_interval(
                            run,
                            &mut stream,
                            stream_timestamp(run, slot),
                            diagnostic,
                        )?;
                    }
                }
                let deadline = stream.grid.next_ns();
                let remaining = deadline - fit.ptp_ns(self.ptp.now_ns());
                if remaining > spin_ns {
                    pacer.wait(Duration::from_nanos(
                        (remaining - spin_ns).min(NS_PER_MS) as u64
                    ));
                }
                // In case a new fit has moved the next grid slot noticeably,
                // return to PTP polling instead of spinning for the entire gap.
                while {
                    let remaining = deadline - fit.ptp_ns(self.ptp.now_ns());
                    (1..=spin_ns).contains(&remaining)
                } {
                    std::hint::spin_loop();
                }
            } else {
                // No media clock (listening without a lock, or a passive GM with
                // no winner fit). Explicit slave mode preserves the lock-timeout
                // exit; auto/gm modes leave the decision to the role machine and
                // keep running degraded instead of silently claiming a GM.
                if Instant::now() >= deadline && run.config.ptp.mode == PtpMode::Slave {
                    break Err(Failure::new(LOCK_TIMEOUT));
                }
                if Instant::now() >= deadline {
                    deadline = Instant::now() + run.config.lock_timeout;
                }
                pacer.wait(Duration::from_micros(100));
            }
        };
        self.deactivate(run, &mut stream, false);
        outcome
    }

    fn activate(
        &mut self,
        run: &Run,
        stream: &mut Stream,
        media: MediaSource,
        reference: Reference,
        now: i128,
        preserve_grid: bool,
    ) {
        stream.source = Some(media);
        stream.reference = Some(reference);
        if !preserve_grid {
            stream.grid = Grid::new(run.config.timing);
        }
        stream.version += 1;
        stream.next_sap = now;
        stream.descriptions = self
            .outputs
            .iter()
            .enumerate()
            .map(|(i, output)| {
                let secondary_port = if run.config.redundant {
                    Some(output.flow.destination.port() + 2)
                } else {
                    None
                };
                let sdp = sdp::describe(
                    &run.config.name,
                    output,
                    i,
                    &run.config.labels,
                    Some(reference),
                    self.id.wrapping_add(i as u64),
                    stream.version,
                    run.config.timing,
                    secondary_port,
                );
                Description {
                    hash: sdp::crc16(sdp.as_bytes()),
                    sdp,
                }
            })
            .collect();
        *lock(&run.descriptions) = stream.descriptions.clone();
        lock(&run.audio).discard = false;
        stream.prebuffered = false;
    }

    fn deactivate(&self, run: &Run, stream: &mut Stream, rediscover: bool) {
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
        if !stream.descriptions.is_empty() {
            self.announce(run, &stream.descriptions, true);
        }
        stream.descriptions.clear();
        lock(&run.descriptions).clear();
        stream.source = None;
        stream.reference = None;
        stream.holdover_until = None;
        stream.last_fit = None;
        stream.prebuffered = false;
    }

    fn update_clock(&self, run: &Run, now: i128, fit: ClockFit, media: MediaSource) {
        let mut counters = lock(&run.counters);
        // Counters.locked keeps its pre-existing meaning: the media clock is
        // the PTP epoch (slave lock, passive winner, or our own GM clock). In
        // local mode RTP flows but locked stays 0, so the host never reports a
        // PTP lock for a local reference.
        counters.locked = u32::from(matches!(media, MediaSource::Ptp(_)));
        counters.offset_ns = fit.offset_ns(now) as f64;
        counters.rate_ppm = fit.rate_ppm;
        counters.fit_rms_ns = fit.rms_ns;
    }

    fn announce(&self, run: &Run, descriptions: &[Description], delete: bool) {
        let repeats = if delete { SAP_DELETE_REPEATS } else { 1 };
        let mut failures = 0;
        for _ in 0..repeats {
            for (sap, description) in self.sap.iter().zip(descriptions) {
                if sap
                    .publish(description.hash, &description.sdp, delete)
                    .is_err()
                {
                    failures += 1;
                }
            }
        }
        if failures != 0 {
            lock(&run.counters).errors += failures as u64;
        }
    }

    fn drop_backlog(&mut self, run: &Run, stream: &Stream, dropped: u64) {
        if dropped == 0 {
            return;
        }
        let frames = dropped.saturating_mul(u64::from(run.config.timing.samples_per_packet()));
        let discarded = if stream.prebuffered {
            lock(&run.audio).ring.skip(frames)
        } else {
            0
        };
        let mut c = lock(&run.counters);
        c.late = c.late.saturating_add(frames);
        c.dropped = c.dropped.saturating_add(discarded);
        for output in self.outputs.iter_mut().chain(self.secondary.iter_mut()) {
            output.drop_slots(dropped);
        }
    }

    fn send_interval(
        &mut self,
        run: &Run,
        stream: &mut Stream,
        stamp: u32,
        diagnostic: bool,
    ) -> Result<(), Failure> {
        let samples = run.config.timing.samples_per_packet() as usize;
        let len = samples * run.config.channels;
        let silent = {
            let mut audio = lock(&run.audio);
            stream.prebuffered |= audio.ring.frames() >= run.config.prebuffer;
            if stream.prebuffered {
                audio.ring.consume(&mut stream.samples[..len])
            } else {
                stream.samples[..len].fill(0.0);
                true
            }
        };
        if diagnostic {
            stream
                .pcm
                .account(&stream.samples[..len], run.config.channels);
        }
        let mut packets = 0;
        let mut bytes = 0;
        let mut errors = 0;
        let mut fatal = None;
        for (channel, output) in self
            .outputs
            .iter_mut()
            .chain(self.secondary.iter_mut())
            .enumerate()
        {
            match output.send(stamp, &stream.samples[..len], run.config.channels) {
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
        stream.pacing.send_errors += errors;
        if silent {
            c.silent += packets;
        }
        fatal.map_or(Ok(()), Err)
    }
}

/// Stable per-configuration session id for the SDP origin line. Using a
/// launch timestamp makes the daemon's auto_sinks_update see a brand new
/// session on every restart; hashing the transport configuration keeps o=
/// stable while the stream shape is unchanged (audit 2026-11-23, item 2).
fn stable_session_id(config: &Config) -> u64 {
    let mut hash: u64 = 0xCBF2_9CE4_8422_2325;
    let mut feed = |bytes: &[u8]| {
        for &byte in bytes {
            hash ^= u64::from(byte);
            hash = hash.wrapping_mul(0x0000_0100_0000_01B3);
        }
    };
    feed(config.name.as_bytes());
    feed(&(config.channels as u32).to_be_bytes());
    feed(&config.timing.sample_rate().to_be_bytes());
    feed(&config.timing.packet_time_us().to_be_bytes());
    feed(&config.encoding.abi().to_be_bytes());
    for flow in &config.flows {
        feed(&flow.destination.ip().octets());
        feed(&flow.destination.port().to_be_bytes());
        feed(&(flow.channels as u32).to_be_bytes());
        feed(&(flow.offset as u32).to_be_bytes());
        feed(&flow.payload_type.to_be_bytes());
    }
    if hash == 0 { 1 } else { hash }
}

/// RFC 2974 clause 3.1 offset: rand(interval*2/3) - interval/3, keeping the
/// average announcement interval while avoiding lockstep with other senders.
fn rfc2974_offset(interval: Duration, seed: u64) -> i128 {
    let interval_ns = duration_ns(interval);
    if interval_ns <= 0 {
        return 0;
    }
    let random = ((seed
        .wrapping_mul(6_364_136_223_846_793_005)
        .wrapping_add(1_442_695_040_888_963_407))
        >> 33) as i128;
    random % interval_ns - interval_ns / 3
}

fn duration_ns(duration: Duration) -> i128 {
    duration.as_nanos() as i128
}

fn stream_timestamp(run: &Run, slot: i128) -> u32 {
    run.config.timing.timestamp(slot)
}
const WAKE_BUCKET_US: u64 = 10;
const WAKE_BUCKETS: usize = 1201; // 0-12 ms, with the last bucket including larger delays.

struct PacingMetrics {
    due_slots: u64,
    caught_up_slots: u64,
    dropped_slots: u64,
    send_errors: u64,
    max_wake_late_us: u64,
    wakes: u64,
    histogram: [u64; WAKE_BUCKETS],
}

impl PacingMetrics {
    fn new() -> Self {
        Self {
            due_slots: 0,
            caught_up_slots: 0,
            dropped_slots: 0,
            send_errors: 0,
            max_wake_late_us: 0,
            wakes: 0,
            histogram: [0; WAKE_BUCKETS],
        }
    }

    fn batch(&mut self, batch: &rtp::Batch) {
        self.due_slots += u64::from(batch.slots) + batch.dropped;
        self.caught_up_slots += u64::from(batch.slots - 1);
        self.dropped_slots += batch.dropped;
        let late_us = batch.wake_late_ns.div_euclid(1000) as u64;
        self.max_wake_late_us = self.max_wake_late_us.max(late_us);
        let bin = late_us
            .div_ceil(WAKE_BUCKET_US)
            .min((WAKE_BUCKETS - 1) as u64) as usize;
        self.histogram[bin] += 1;
        self.wakes += 1;
    }

    fn p99_wake_late_us(&self) -> u64 {
        if self.wakes == 0 {
            return 0;
        }
        let rank = self.wakes - self.wakes / 100;
        let mut seen = 0;
        for (i, count) in self.histogram.iter().enumerate() {
            seen += count;
            if seen >= rank {
                return if i == WAKE_BUCKETS - 1 {
                    self.max_wake_late_us
                } else {
                    (i as u64 * WAKE_BUCKET_US).min(self.max_wake_late_us)
                };
            }
        }
        unreachable!("histogram count tracks the number of wakes");
    }
}

/// Diagnostics-only view of the interleaved PCM the pacing loop consumes.
/// One packet block is accounted per send interval, and only while
/// NSP_AES67_PTP_DIAGNOSTICS is enabled, so the audio path stays unchanged.
#[derive(Default)]
struct PcmConsumption {
    consumed_frames: u64,
    nonzero_frames: u64,
    peak_abs: f32,
    nonzero_frames_total: u64,
}

struct PcmWindow {
    consumed_frames: u64,
    nonzero_frames: u64,
    peak_abs: f32,
    nonzero_frames_total: u64,
}

impl PcmConsumption {
    /// A frame counts as nonzero when any of its channel samples is nonzero.
    fn account(&mut self, samples: &[f32], channels: usize) {
        if channels == 0 {
            return;
        }
        self.consumed_frames += (samples.len() / channels) as u64;
        for frame in samples.chunks_exact(channels) {
            let peak = frame
                .iter()
                .fold(0.0_f32, |peak, sample| peak.max(sample.abs()));
            if peak > 0.0 {
                self.nonzero_frames += 1;
                self.nonzero_frames_total += 1;
            }
            self.peak_abs = self.peak_abs.max(peak);
        }
    }

    /// Snapshot the window since the previous diagnostics line and restart it.
    fn take(&mut self) -> PcmWindow {
        let window = PcmWindow {
            consumed_frames: self.consumed_frames,
            nonzero_frames: self.nonzero_frames,
            peak_abs: self.peak_abs,
            nonzero_frames_total: self.nonzero_frames_total,
        };
        self.consumed_frames = 0;
        self.nonzero_frames = 0;
        self.peak_abs = 0.0;
        window
    }
}

struct Stream {
    pacing: PacingMetrics,
    pcm: PcmConsumption,
    /// Media clock currently in use: the PTP epoch of a GM or the local
    /// reference epoch. None before the first media clock / after a pause.
    source: Option<MediaSource>,
    /// SDP clock reference currently announced (Ptp or Local).
    reference: Option<Reference>,
    holdover_until: Option<i128>,
    last_fit: Option<ClockFit>,
    grid: Grid,
    prebuffered: bool,
    samples: Vec<f32>,
    descriptions: Vec<Description>,
    version: u64,
    next_sap: i128,
    io_streak: Vec<u32>,
}
impl Stream {
    fn new(channels: usize, flows: usize, timing: PacketTiming) -> Self {
        Self {
            source: None,
            pacing: PacingMetrics::new(),
            pcm: PcmConsumption::default(),
            reference: None,
            holdover_until: None,
            last_fit: None,
            grid: Grid::new(timing),
            prebuffered: false,
            samples: vec![0.0; channels * timing.samples_per_packet() as usize],
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

    fn test_config() -> Config {
        Config {
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
                ..ptp::Config::default()
            },
            lock_timeout: Duration::from_secs(1),
            flows: vec![],
            sap: false,
            sap_interval: Duration::from_secs(30),
            sap_destination: std::net::Ipv4Addr::new(224, 2, 127, 254),
            timing: PacketTiming::new(48_000, 1_000).unwrap(),
            encoding: Encoding::L24,
            redundant: false,
        }
    }

    #[test]
    fn pcm_consumption_reports_nonzero_frames_and_peak() {
        let mut pcm = PcmConsumption::default();
        pcm.account(&[0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, -0.25], 2);
        let window = pcm.take();
        assert_eq!(window.consumed_frames, 4);
        assert_eq!(window.nonzero_frames, 2);
        assert_eq!(window.peak_abs, 0.5);
        assert_eq!(window.nonzero_frames_total, 2);
        pcm.account(&[0.0; 8], 2);
        let window = pcm.take();
        assert_eq!(window.consumed_frames, 4);
        assert_eq!(window.nonzero_frames, 0);
        assert_eq!(window.peak_abs, 0.0);
        assert_eq!(window.nonzero_frames_total, 2);
    }

    #[test]
    fn join_timeout_retains_handle_for_a_clean_retry() {
        let session = Session::default();
        let (finish, wait) = mpsc::channel::<()>();
        let join = thread::spawn(move || {
            wait.recv().unwrap();
        });
        let run = Arc::new(Run {
            config: Arc::new(test_config()),
            audio: Mutex::new(Audio {
                ring: Ring::new(48, 1),
                accept: true,
                discard: false,
            }),
            cancel: AtomicBool::new(false),
            counters: Mutex::new(Counters::default()),
            ptp_status: Mutex::new(None),
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
