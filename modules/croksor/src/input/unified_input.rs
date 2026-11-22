//! Source-period spatial grouping and an independent PCM FIFO share one render clock.
use std::{
    collections::VecDeque,
    sync::{OnceLock, atomic::{AtomicUsize, Ordering}, mpsc::{Receiver, SyncSender, TryRecvError}},
    time::{Duration, Instant},
};

use crate::{capture_input::CaptureInput, joint::Joint, packet::{Packet, PcmTrace, TraceEvent}, render_wake::RenderWake};

pub(crate) const PCM: usize = 8;
pub(crate) const PCM_LANES: usize = 8;
pub(crate) const LANES: usize = PCM + PCM_LANES;
const PERIOD: Duration = Duration::from_millis(10);
const PERIOD_NS: i128 = 10_000_000;
const PREROLL: Duration = Duration::from_millis(20);
const UPMIX_PREROLL: Duration = Duration::from_millis(25);
const QUEUE_LIMIT: usize = 4;
// Windows PCM producers can publish several render periods in one burst.
// Keep a bounded PCM backlog without expanding spatial or primary queues.
const PCM_QUEUE_LIMIT: usize = 24;
const ACTIVE_LANE: Duration = PERIOD.saturating_mul(3);
const START_JOIN: Duration = Duration::from_micros(2500);
const SPATIAL_WINDOWS_US: [u64; 4] = [200, 300, 400, 500];
// Opt-in at pipeline construction, so an A/B run can restore the clocked path
// without rebuilding. This changes render submission pacing, not source slots.
const EARLY_SPATIAL_RENDER_ENV: &str = "NSP_EXPERIMENTAL_SPATIAL_EARLY_RENDER";
// Explicit fixture/session lane only: unified routing does not reserve lane 0
// for a dynamic stream. Unset/invalid values preserve the existing scheduler.
const PRIMARY_LANE_ENV: &str = "NSP_EXPERIMENTAL_PRIMARY_LANE";
const PRIMARY_MATCH_WINDOW: Duration = Duration::from_micros(200);
// The producer publishes one latest-wins request, then signals RenderWake.
// NO_REQUEST is distinct from an explicit request to restore legacy scheduling.
pub(crate) const PRIMARY_NO_REQUEST: usize = usize::MAX;
pub(crate) const PRIMARY_DISABLE: usize = usize::MAX - 1;

fn packet_due(deadline: Instant, arrival_due: Instant, previous: Option<Instant>) -> Instant {
    let offset = arrival_due.saturating_duration_since(deadline) + PERIOD / 2;
    let cycles = (offset.as_nanos() / PERIOD.as_nanos()) as u32;
    let target = deadline + PERIOD * cycles;
    previous.map_or(target, |last| (last + PERIOD).min(target).max(last))
}

fn instant_delta(at: Instant, origin: Instant) -> i128 {
    if at >= origin { at.duration_since(origin).as_nanos() as i128 }
    else { -(origin.duration_since(at).as_nanos() as i128) }
}

fn nearest_period(delta_ns: i128) -> Option<u64> {
    u64::try_from((delta_ns + PERIOD_NS / 2).div_euclid(PERIOD_NS)).ok()
}

struct SpatialClock {
    capture_origin: Option<i128>,
    received_origin: Instant,
}

impl SpatialClock {
    fn new(packet: &Packet) -> Self {
        Self {
            capture_origin: (packet.capture_ns != 0).then_some(packet.capture_ns as i128),
            received_origin: packet.received,
        }
    }

    fn slot(&mut self, packet: &Packet) -> Option<u64> {
        let arrival_delta = instant_delta(packet.received, self.received_origin);
        if packet.capture_ns == 0 {
            // Compatibility/diagnostic fallback only. Normal spatial ingress
            // carries source QPC time, independently of the full-timing report.
            return nearest_period(arrival_delta);
        }
        let origin = self.capture_origin.get_or_insert_with(|| {
            let slot = (arrival_delta + PERIOD_NS / 2).div_euclid(PERIOD_NS);
            packet.capture_ns as i128 - slot * PERIOD_NS
        });
        // QPC estimates the source period; it is not a hardware sample counter.
        // Signed rounding also accepts another stream's first packet slightly
        // before our first observed capture, without underflow or epoch rebasing.
        nearest_period(packet.capture_ns as i128 - *origin)
    }
}

struct SpatialFrame {
    slot: u64,
    first: Instant,
    expected: u16,
    packets: [Option<Packet>; PCM],
    checkpoint: usize,
    allow_early_seal: bool,
    sealed_at: Option<Instant>,
    sealed: Option<(Instant, usize, bool)>,
}

impl SpatialFrame {
    fn new(slot: u64, first: Instant, expected: u16, allow_early_seal: bool) -> Self {
        Self { slot, first, expected, packets: std::array::from_fn(|_| None), checkpoint: 0, allow_early_seal, sealed_at: None, sealed: None }
    }

    fn checkpoint_at(&self) -> Instant {
        self.first + Duration::from_micros(SPATIAL_WINDOWS_US[self.checkpoint])
    }

    fn advance(&mut self, now: Instant, finishing: bool) -> bool {
        if self.sealed.is_some() { return false; }
        // At most four checks, even after a late wake. Membership at each
        // checkpoint uses producer timestamps, not the worker's wake time.
        while self.checkpoint < SPATIAL_WINDOWS_US.len() {
            let at = self.checkpoint_at();
            if !finishing && now < at && !self.allow_early_seal { return false; }
            let available = self.packets.iter().enumerate().fold(0u16, |mask, (lane, packet)| {
                mask | if packet.as_ref().is_some_and(|p| p.received <= at) { 1 << lane } else { 0 }
            });
            let complete = available & self.expected == self.expected;
            if !finishing && now < at && !complete { return false; }
            if complete || self.checkpoint == SPATIAL_WINDOWS_US.len() - 1 {
                self.sealed = Some((at, self.checkpoint, complete));
                return true;
            }
            self.checkpoint += 1;
        }
        unreachable!("the final checkpoint seals a spatial frame")
    }
}

enum Poll {
    Batch([Option<Packet>; LANES]),
    Wait(Option<Instant>),
    Finished,
    Failed(u32),
}

struct PrimaryIngress {
    lane: usize,
    main: VecDeque<Packet>,
    auxiliary: [VecDeque<Packet>; LANES],
    clock: Option<SpatialClock>,
    first_sequence: Option<u64>,
    last_enqueued: Option<u64>,
    consumed: [Option<u64>; LANES],
    same: [u64; LANES],
    previous: [u64; LANES],
    dropped: [u64; LANES],
    failure: Option<u32>,
}

impl PrimaryIngress {
    fn new(lane: usize) -> Self {
        Self { lane, main: VecDeque::with_capacity(QUEUE_LIMIT),
            auxiliary: std::array::from_fn(|_| VecDeque::with_capacity(QUEUE_LIMIT)),
            clock: None, first_sequence: None, last_enqueued: None,
            consumed: [None; LANES], same: [0; LANES], previous: [0; LANES],
            dropped: [0; LANES], failure: None }
    }

    fn discard(&mut self, packet: Packet, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>, discarded: &mut u64) {
        self.dropped[packet.lane] += 1;
        if let Some(trace) = trace { trace.mark(TraceEvent::IngressDrop, packet.seq, packet.lane as u64, *discarded); }
        *discarded += 1;
        packet.recycle(recycle);
    }

    fn enqueue(&mut self, packet: Packet, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>, discarded: &mut u64) {
        let lane = packet.lane;
        if lane == self.lane {
            // A primary block is never dropped/relabelled by QPC rounding. Its
            // accepted sequence must remain contiguous for this pipeline's life.
            if self.failure.is_some() || self.last_enqueued.is_some_and(|last| last.checked_add(1) != Some(packet.seq)) {
                self.failure.get_or_insert(13); // ERROR_INVALID_DATA
                self.discard(packet, recycle, trace, discarded);
                return;
            }
            if self.main.len() == QUEUE_LIMIT {
                self.failure = Some(111); // ERROR_BUFFER_OVERFLOW, never silently evict primary audio.
                self.discard(packet, recycle, trace, discarded);
                return;
            }
            self.clock.get_or_insert_with(|| SpatialClock::new(&packet));
            self.first_sequence.get_or_insert(packet.seq);
            self.last_enqueued = Some(packet.seq);
            self.main.push_back(packet);
        } else {
            if self.consumed[lane].is_some_and(|last| packet.seq <= last)
                || self.auxiliary[lane].iter().any(|queued| queued.seq == packet.seq)
            {
                self.discard(packet, recycle, trace, discarded);
                return;
            }
            if self.auxiliary[lane].len() == QUEUE_LIMIT {
                let evicted = self.auxiliary[lane].pop_front().unwrap();
                self.discard(evicted, recycle, trace, discarded);
            }
            self.auxiliary[lane].push_back(packet);
        }
    }

    fn poll_at(&mut self, now: Instant, finishing: bool, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>, discarded: &mut u64) -> Poll {
        if let Some(code) = self.failure { return Poll::Failed(code); }
        let Some(main) = self.main.pop_front() else {
            if finishing {
                for lane in 0..LANES {
                    while let Some(packet) = self.auxiliary[lane].pop_front() { self.discard(packet, recycle, trace, discarded); }
                }
                return Poll::Finished;
            }
            // Configuring a primary owns the entire session, including pauses.
            // Auxiliary arrivals and upmix completions never create output ticks.
            return Poll::Wait(None);
        };
        let slot = main.seq - self.first_sequence.unwrap();
        let oldest = slot.saturating_sub(1);
        let cutoff = main.received + PRIMARY_MATCH_WINDOW;
        let mut batch: [Option<Packet>; LANES] = std::array::from_fn(|_| None);
        self.same[self.lane] += 1;
        self.consumed[self.lane] = Some(main.seq);
        batch[self.lane] = Some(main);
        for lane in 0..LANES {
            if lane == self.lane { continue; }
            let mut best: Option<(usize, u64, u64)> = None;
            let mut index = 0;
            // At most four candidates per auxiliary lane. This is one snapshot
            // selection, with no retry loop or timer waiting for missing input.
            while index < self.auxiliary[lane].len() {
                let packet = &self.auxiliary[lane][index];
                let auxiliary_slot = self.clock.as_mut().unwrap().slot(packet);
                if auxiliary_slot.is_none_or(|at| at < oldest)
                    || self.consumed[lane].is_some_and(|last| packet.seq <= last)
                {
                    let stale = self.auxiliary[lane].remove(index).unwrap();
                    self.discard(stale, recycle, trace, discarded);
                    continue;
                }
                let at = auxiliary_slot.unwrap();
                if at <= slot && packet.received <= now && (at < slot || packet.received <= cutoff)
                    && best.is_none_or(|(_, chosen, sequence)| (at, packet.seq) < (chosen, sequence))
                {
                    // Consume a still-unplayed previous-period block before a
                    // current block. The current block can then follow once.
                    best = Some((index, at, packet.seq));
                }
                index += 1;
            }
            if let Some((index, at, _)) = best {
                let packet = self.auxiliary[lane].remove(index).unwrap();
                self.consumed[lane] = Some(packet.seq);
                if at == slot { self.same[lane] += 1; } else { self.previous[lane] += 1; }
                batch[lane] = Some(packet);
            }
        }
        Poll::Batch(batch)
    }
}

pub(crate) struct Ingress {
    spatial: VecDeque<SpatialFrame>,
    spatial_clock: Option<SpatialClock>,
    closed_through: Option<u64>,
    source_leases: [u64; LANES],
    last_spatial_arrival: [Option<Instant>; PCM],
    pcm: [VecDeque<(Instant, Packet)>; PCM_LANES],
    pcm_deadline: [Option<Instant>; PCM_LANES],
    pcm_join: Option<Instant>,
    render_epoch: Option<Instant>,
    render_tick: u64,
    early_spatial_render: bool,
    primary: Option<PrimaryIngress>,
    pending_primary_lane: Option<Option<usize>>,
    pub(crate) primary_lane: Option<usize>,
    pub(crate) primary_same: [u64; LANES],
    pub(crate) primary_previous: [u64; LANES],
    pub(crate) primary_dropped: [u64; LANES],
    disconnected: bool,
    preroll: Duration,
    upmix: Option<PcmUpmix>,
    pub(crate) discarded: u64,
    pub(crate) spatial_windows: [u64; 5],
    pub(crate) spatial_window_closures: [u64; 4],
    pub(crate) render_boundary: Option<(Instant, Instant, Instant)>,
    pub(crate) spatial_incomplete: u64,
    pub(crate) spatial_over_with_late_input: u64,
    pub(crate) spatial_late: u64,
    pub(crate) spatial_duplicates: u64,
    pub(crate) spatial_untimed: u64,
}

impl Ingress {
    pub(crate) fn new(joint: Option<Joint>) -> Self {
        let early = std::env::var(EARLY_SPATIAL_RENDER_ENV).is_ok_and(|value| value == "1");
        let mut ingress = Self::with_early_spatial_render(joint, early);
        if let Some(lane) = std::env::var(PRIMARY_LANE_ENV).ok().and_then(|value| value.parse::<usize>().ok()).filter(|&lane| lane < PCM) {
            ingress.primary_lane = Some(lane);
            ingress.primary = Some(PrimaryIngress::new(lane));
        }
        ingress
    }

    fn with_early_spatial_render(joint: Option<Joint>, early_spatial_render: bool) -> Self {
        Self {
            spatial: VecDeque::with_capacity(QUEUE_LIMIT),
            spatial_clock: None,
            closed_through: None,
            source_leases: [0; LANES],
            last_spatial_arrival: [None; PCM],
            pcm: std::array::from_fn(|_| VecDeque::with_capacity(PCM_QUEUE_LIMIT)),
            pcm_deadline: [None; PCM_LANES],
            pcm_join: None,
            render_epoch: None,
            render_tick: 0,
            early_spatial_render,
            primary: None,
            pending_primary_lane: None,
            primary_lane: None,
            primary_same: [0; LANES],
            primary_previous: [0; LANES],
            primary_dropped: [0; LANES],
            disconnected: false,
            preroll: if joint.is_some() { UPMIX_PREROLL } else { PREROLL },
            upmix: joint.map(|joint| PcmUpmix { joint, last: None, has_surround: false }),
            discarded: 0,
            spatial_windows: [0; 5],
            spatial_window_closures: [0; 4],
            render_boundary: None,
            spatial_incomplete: 0,
            spatial_over_with_late_input: 0,
            spatial_late: 0,
            spatial_duplicates: 0,
            spatial_untimed: 0,
        }
    }

    fn discard(&mut self, packet: Packet, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>) {
        if let Some(trace) = trace {
            trace.mark(TraceEvent::IngressDrop, packet.seq, packet.lane as u64, self.discarded);
        }
        packet.recycle(recycle);
        self.discarded += 1;
    }

    /// Register a control request on the sole ingress/render worker. Applying
    /// it is deferred to a batch boundary; this never touches an in-flight ROAR
    /// call. None explicitly restores the non-primary scheduler.
    pub(crate) fn set_primary_lane(&mut self, lane: Option<usize>) -> Result<(), u32> {
        if lane.is_some_and(|lane| lane >= PCM) { return Err(87); } // ERROR_INVALID_PARAMETER
        self.pending_primary_lane = Some(lane);
        Ok(())
    }

    fn apply_primary_request(&mut self, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>) {
        let Some(requested) = self.pending_primary_lane else { return; };
        if requested == self.primary_lane {
            self.pending_primary_lane = None;
            return;
        }
        if self.primary.as_ref().is_some_and(|primary| primary.failure.is_some()) { return; }
        let mut first_sequence = None;
        if let Some(lane) = requested {
            first_sequence = if let Some(primary) = &mut self.primary {
                let last_slot = primary.consumed[primary.lane].map(|seq| seq - primary.first_sequence.unwrap());
                primary.auxiliary[lane].iter().filter_map(|packet| {
                    if primary.consumed[lane].is_some_and(|last| packet.seq <= last) { return None; }
                    let fresh = last_slot.is_none_or(|last| primary.clock.as_mut().unwrap().slot(packet).is_some_and(|slot| slot > last));
                    fresh.then_some(packet.seq)
                }).min()
            } else {
                self.spatial.iter().filter_map(|frame| frame.packets[lane].as_ref().map(|packet| packet.seq)).min()
            };
            // A pending election must not silence the current primary while
            // its successor has no fresh block. Auxiliary arrivals will wake us.
            if first_sequence.is_none() { return; }
        }

        // Control changes are rare and move only the packets already queued.
        // Preserve unconsumed packets across the handoff, never replay a packet
        // already used as either primary or auxiliary by the previous owner.
        let mut packets = Vec::with_capacity(PCM * QUEUE_LIMIT + PCM_LANES * PCM_QUEUE_LIMIT);
        let mut consumed = [None; LANES];
        if let Some(mut primary) = self.primary.take() {
            consumed = primary.consumed;
            self.primary_same = primary.same;
            self.primary_previous = primary.previous;
            self.primary_dropped = primary.dropped;
            packets.extend(primary.main.drain(..));
            for queue in &mut primary.auxiliary { packets.extend(queue.drain(..)); }
        } else {
            for frame in self.spatial.drain(..) { packets.extend(frame.packets.into_iter().flatten()); }
            for queue in &mut self.pcm { packets.extend(queue.drain(..).map(|(_, packet)| packet)); }
        }
        self.pending_primary_lane = None;
        self.primary_lane = requested;
        self.render_boundary = None;
        if let Some(lane) = requested {
            let mut primary = PrimaryIngress::new(lane);
            primary.consumed = consumed;
            primary.same = self.primary_same;
            primary.previous = self.primary_previous;
            primary.dropped = self.primary_dropped;
            // Establish the successor's clock from its first new primary block,
            // before transferring other lanes into their independent FIFOs.
            packets.sort_unstable_by_key(|packet| (packet.lane != lane, packet.seq, packet.received));
            for packet in packets {
                if packet.lane == lane && packet.seq < first_sequence.unwrap() {
                    primary.discard(packet, recycle, trace, &mut self.discarded);
                } else {
                    primary.enqueue(packet, recycle, trace, &mut self.discarded);
                }
            }
            self.primary_dropped = primary.dropped;
            self.primary = Some(primary);
        } else {
            // Legacy timing is explicitly re-established on disable. This is a
            // scheduling-mode handoff, not a promise of sample-accurate crossfade.
            self.spatial_clock = None;
            self.closed_through = None;
            self.last_spatial_arrival = [None; PCM];
            self.pcm_deadline = [None; PCM_LANES];
            self.pcm_join = None;
            self.render_epoch = None;
            self.render_tick = 0;
            packets.sort_unstable_by_key(|packet| (packet.received, packet.lane, packet.seq));
            // These PCM packets already own their upmix tickets. Re-enqueueing
            // them must not advance/reset the stateful upmixer a second time.
            let upmix = self.upmix.take();
            for packet in packets { self.enqueue(packet, recycle, trace); }
            self.upmix = upmix;
        }
    }

    fn replace_lane_source(&mut self, lane: usize, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>) {
        if let Some(primary) = &mut self.primary {
            primary.consumed[lane] = None;
            if lane == primary.lane {
                while let Some(packet) = primary.main.pop_front() {
                    primary.discard(packet, recycle, trace, &mut self.discarded);
                }
                primary.clock = None;
                primary.first_sequence = None;
                primary.last_enqueued = None;
            }
            while let Some(packet) = primary.auxiliary[lane].pop_front() {
                primary.discard(packet, recycle, trace, &mut self.discarded);
            }
            self.primary_dropped = primary.dropped;
        }

        if lane < PCM {
            self.last_spatial_arrival[lane] = None;
            for index in (0..self.spatial.len()).rev() {
                let frame = &mut self.spatial[index];
                frame.expected &= !(1 << lane);
                let packet = frame.packets[lane].take();
                if frame.packets.iter().all(Option::is_none) {
                    self.spatial.remove(index);
                }
                if let Some(packet) = packet {
                    self.discard(packet, recycle, trace);
                }
            }
        } else {
            let index = lane - PCM;
            while let Some((_, packet)) = self.pcm[index].pop_front() {
                self.discard(packet, recycle, trace);
            }
            self.pcm_deadline[index] = None;
            if index == 0 {
                if let Some(upmix) = &mut self.upmix {
                    upmix.joint.reset();
                    upmix.last = None;
                    upmix.has_surround = false;
                }
            }
        }
    }

    fn enqueue(&mut self, mut packet: Packet, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>) {
        let lane = packet.lane;
        match packet.source_lease.cmp(&self.source_leases[lane]) {
            std::cmp::Ordering::Less => {
                self.discard(packet, recycle, trace);
                return;
            }
            std::cmp::Ordering::Greater => {
                self.replace_lane_source(lane, recycle, trace);
                self.source_leases[lane] = packet.source_lease;
            }
            std::cmp::Ordering::Equal => {}
        }
        if let Some(primary) = &mut self.primary {
            if packet.lane == PCM {
                if let Some(upmix) = &mut self.upmix { upmix.submit(&mut packet); }
            } else if packet.lane < PCM {
                self.spatial_untimed += u64::from(packet.capture_ns == 0);
            }
            primary.enqueue(packet, recycle, trace, &mut self.discarded);
            self.primary_dropped = primary.dropped;
            return;
        }
        let epoch = *self.render_epoch.get_or_insert(packet.received);
        if self.spatial.is_empty() && self.pcm.iter().all(VecDeque::is_empty) {
            // Skip empty elapsed render slots in O(1), retaining the phase.
            let elapsed = packet.received.saturating_duration_since(epoch).as_nanos() / PERIOD.as_nanos();
            self.render_tick = self.render_tick.max(elapsed as u64);
        }
        if packet.lane >= PCM {
            let index = packet.lane - PCM;
            if index == 0 { if let Some(upmix) = &mut self.upmix { upmix.submit(&mut packet); } }
            let first_due = packet.received + self.preroll;
            let deadline = *self.pcm_deadline[index].get_or_insert(epoch + self.preroll);
            self.pcm_join.get_or_insert(packet.received + START_JOIN);
            let due = packet_due(deadline, first_due, self.pcm[index].back().map(|(last, _)| *last));
            if self.pcm[index].len() == PCM_QUEUE_LIMIT {
                let (_, evicted) = self.pcm[index].pop_front().unwrap();
                self.discard(evicted, recycle, trace);
            }
            self.pcm[index].push_back((due, packet));
            return;
        }

        self.spatial_untimed += u64::from(packet.capture_ns == 0);
        let clock = self.spatial_clock.get_or_insert_with(|| SpatialClock::new(&packet));
        let Some(slot) = clock.slot(&packet) else {
            self.spatial_late += 1;
            self.discard(packet, recycle, trace);
            return;
        };
        if self.closed_through.is_some_and(|closed| slot <= closed) {
            self.spatial_late += 1;
            self.discard(packet, recycle, trace);
            return;
        }
        let lane = packet.lane;
        let index = if let Some(index) = self.spatial.iter().position(|frame| frame.slot == slot) {
            index
        } else {
            // Make room before insertion: retain the original allocation and
            // at most four complete source periods, even under a producer burst.
            if self.spatial.len() == QUEUE_LIMIT {
                if slot < self.spatial.front().unwrap().slot {
                    self.closed_through = Some(slot);
                    self.discard(packet, recycle, trace);
                    return;
                }
                let evicted = self.spatial.pop_front().unwrap();
                self.closed_through = Some(evicted.slot);
                for packet in evicted.packets.into_iter().flatten() { self.discard(packet, recycle, trace); }
            }
            // Freeze the known active set when this frame first enters ingress.
            // Future-frame arrivals cannot retroactively extend its collection.
            let expected = self.last_spatial_arrival.iter().enumerate().fold(1 << lane, |mask, (i, last)| {
                mask | if last.is_some_and(|at| packet.received.saturating_duration_since(at) < ACTIVE_LANE) { 1 << i } else { 0 }
            });
            let index = self.spatial.iter().position(|frame| frame.slot > slot).unwrap_or(self.spatial.len());
            let active_before = self.last_spatial_arrival[lane].is_some_and(|at|
                packet.received.saturating_duration_since(at) < ACTIVE_LANE);
            self.spatial.insert(index, SpatialFrame::new(slot, packet.received, expected, active_before));
            index
        };
        let frame = &mut self.spatial[index];
        if frame.packets[lane].is_some() {
            self.spatial_duplicates += 1;
            self.discard(packet, recycle, trace);
            return;
        }
        frame.first = frame.first.min(packet.received);
        self.last_spatial_arrival[lane] = Some(packet.received);
        frame.packets[lane] = Some(packet);
    }

    fn seal_front(&mut self, now: Instant, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>) {
        let Some(frame) = self.spatial.front_mut() else { return };
        if !frame.advance(now, self.disconnected) { return; }
        frame.sealed_at = Some(now);
        let (cutoff, checkpoint, complete) = frame.sealed.unwrap();
        self.closed_through = Some(frame.slot);
        self.spatial_window_closures[checkpoint] += 1;
        if complete {
            let waited = now.saturating_duration_since(frame.first);
            let bucket = SPATIAL_WINDOWS_US.iter().position(|&us| waited <= Duration::from_micros(us))
                .unwrap_or(SPATIAL_WINDOWS_US.len());
            self.spatial_windows[bucket] += 1;
            if bucket == SPATIAL_WINDOWS_US.len() {
                let maximum = frame.first + Duration::from_micros(SPATIAL_WINDOWS_US[3]);
                self.spatial_over_with_late_input += u64::from(frame.packets.iter().flatten().any(|p| p.received > maximum));
            }
        } else {
            self.spatial_incomplete += 1;
        }
        let late: [Option<Packet>; PCM] = std::array::from_fn(|i| {
            if frame.packets[i].as_ref().is_some_and(|packet| packet.received > cutoff) { frame.packets[i].take() }
            else { None }
        });
        for packet in late.into_iter().flatten() {
            self.spatial_late += 1;
            self.discard(packet, recycle, trace);
        }
    }

    fn render_at(&self) -> Instant {
        self.render_epoch.expect("queued packets have a render epoch")
            + Duration::from_nanos((self.render_tick as u128 * PERIOD.as_nanos()).min(u64::MAX as u128) as u64)
    }

    fn poll_at(&mut self, now: Instant, recycle: &SyncSender<Vec<u8>>, trace: Option<PcmTrace>) -> Poll {
        self.apply_primary_request(recycle, trace);
        if let Some(primary) = &mut self.primary {
            let result = primary.poll_at(now, self.disconnected, recycle, trace, &mut self.discarded);
            self.primary_same = primary.same;
            self.primary_previous = primary.previous;
            self.primary_dropped = primary.dropped;
            if let Poll::Batch(batch) = &result {
                let first = batch[primary.lane].as_ref().unwrap().received;
                self.render_boundary = Some((first, now, now));
                self.render_tick += 1;
                if let Some(trace) = trace {
                    trace.mark(TraceEvent::RenderTick, batch[PCM].as_ref().map_or(u64::MAX, |p| p.seq), 0, primary.auxiliary[PCM].len() as u64);
                }
            }
            return result;
        }
        if self.spatial.is_empty() && self.pcm.iter().all(VecDeque::is_empty) {
            return if self.disconnected { Poll::Finished } else { Poll::Wait(None) };
        }
        self.seal_front(now, recycle, trace);
        let render_at = self.render_at();
        let spatial_ready = self.spatial.front().is_some_and(|frame| frame.sealed.is_some());
        let pcm_earliest = self.pcm.each_ref().map(|queue|
            queue.front().map(|(due, _)| due.checked_sub(self.preroll).unwrap_or(*due)));
        let pcm_available = pcm_earliest.map(|at| at.is_some_and(|at| now >= at || self.disconnected));
        let pcm_ready = self.pcm.each_ref().map(|queue| queue.front().is_some_and(|(due, packet)| {
            self.disconnected || now >= *due || !packet.upmix_eligible || packet.upmix_ticket
                .is_some_and(|ticket| self.upmix.as_ref().is_some_and(|upmix| upmix.joint.ready(ticket)))
        }));
        let pcm_alone = self.spatial.is_empty() && pcm_available.iter().zip(pcm_ready).any(|(&available, ready)| available && ready)
            && (self.disconnected || now >= self.pcm_join.unwrap_or(now));
        let spatial_on_seal = self.early_spatial_render && spatial_ready;
        if (self.disconnected || now >= render_at || spatial_on_seal) && (spatial_ready || pcm_alone) {
            let mut batch: [Option<Packet>; LANES] = std::array::from_fn(|_| None);
            self.render_boundary = None;
            if spatial_ready {
                let frame = self.spatial.pop_front().unwrap();
                // Report the effective gate; the nominal clock no longer holds
                // this batch in the opt-in experiment.
                let gate = if spatial_on_seal { render_at.min(frame.sealed_at.unwrap()) } else { render_at };
                self.render_boundary = Some((frame.first, frame.sealed_at.unwrap(), gate));
                for (dest, packet) in batch[..PCM].iter_mut().zip(frame.packets) { *dest = packet; }
            }
            for index in 0..PCM_LANES {
                if pcm_available[index] && pcm_ready[index] {
                    let (due, packet) = self.pcm[index].pop_front().unwrap();
                    self.pcm_deadline[index] = Some(due + PERIOD);
                    batch[PCM + index] = Some(packet);
                }
            }
            // Account for every output block even when the spatial experiment
            // bypasses its gate. PCM-only output still uses this clock, so early
            // spatial blocks cannot be followed by extra PCM catch-up blocks.
            // A spatial backlog can now be submitted in a burst: downstream
            // FIFO/backpressure and end-to-end latency need separate validation.
            self.render_tick += 1;
            if let Some(trace) = trace {
                let seq = batch[PCM].as_ref().map_or(u64::MAX, |p| p.seq);
                trace.mark(TraceEvent::RenderTick, seq, now.saturating_duration_since(render_at).as_nanos() as u64, self.pcm.iter().map(VecDeque::len).sum::<usize>() as u64);
            }
            return Poll::Batch(batch);
        }
        let until = if let Some(frame) = self.spatial.front() {
            if frame.sealed.is_some() { render_at }
            else { frame.checkpoint_at() }
        } else {
            let pcm_at = (0..PCM_LANES).filter_map(|index| {
                let (due, _) = self.pcm[index].front()?;
                Some(if pcm_ready[index] {
                    pcm_earliest[index].unwrap().max(self.pcm_join.unwrap_or(now))
                } else { *due })
            }).min().expect("PCM queues are nonempty");
            render_at.max(pcm_at)
        };
        Poll::Wait(Some(until))
    }

    pub(crate) fn upmixed_bed(&mut self, packet: &Packet) -> Option<&[f32]> {
        self.upmix.as_mut()?.joint.read_planar(packet.upmix_ticket?)
    }

    pub(crate) fn next(
        &mut self,
        input: &Receiver<Packet>,
        recycle: &SyncSender<Vec<u8>>,
        wake: &RenderWake,
        trace: &OnceLock<PcmTrace>,
    ) -> Result<Option<[Option<Packet>; LANES]>, u32> {
        self.next_inner(input, recycle, wake, trace, None)
    }

    /// The controller stores a lane or PRIMARY_DISABLE with Release ordering,
    /// then calls wake.notify(). Every loop observes the mailbox, including the
    /// iteration after an otherwise indefinite input wait is interrupted.
    pub(crate) fn next_with_primary_control(
        &mut self,
        input: &Receiver<Packet>,
        recycle: &SyncSender<Vec<u8>>,
        wake: &RenderWake,
        trace: &OnceLock<PcmTrace>,
        primary_request: &AtomicUsize,
    ) -> Result<Option<[Option<Packet>; LANES]>, u32> {
        self.next_inner(input, recycle, wake, trace, Some(primary_request))
    }

    fn next_inner(
        &mut self,
        input: &Receiver<Packet>,
        recycle: &SyncSender<Vec<u8>>,
        wake: &RenderWake,
        trace: &OnceLock<PcmTrace>,
        primary_request: Option<&AtomicUsize>,
    ) -> Result<Option<[Option<Packet>; LANES]>, u32> {
        loop {
            // Ordinary blocks do one read only. If a writer publishes after
            // this check, its subsequent SetEvent remains pending until wait;
            // we never reset the event between checking the mailbox and waiting.
            if let Some(control) = primary_request.filter(|control| control.load(Ordering::Acquire) != PRIMARY_NO_REQUEST) {
                match control.swap(PRIMARY_NO_REQUEST, Ordering::AcqRel) {
                    PRIMARY_NO_REQUEST => {}
                    PRIMARY_DISABLE => self.set_primary_lane(None)?,
                    lane => self.set_primary_lane(Some(lane))?,
                }
            }
            let mut drained = 0;
            while drained < LANES * QUEUE_LIMIT && !self.disconnected
                && !self.primary.as_ref().is_some_and(|primary| primary.main.len() == QUEUE_LIMIT || primary.failure.is_some())
            {
                match input.try_recv() {
                    Ok(packet) => self.enqueue(packet, recycle, trace.get().copied()),
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => { self.disconnected = true; break; }
                }
                drained += 1;
            }
            match self.poll_at(Instant::now(), recycle, trace.get().copied()) {
                Poll::Batch(batch) => return Ok(Some(batch)),
                Poll::Finished => return Ok(None),
                Poll::Failed(code) => return Err(code),
                Poll::Wait(until) => {
                    if drained < LANES * QUEUE_LIMIT {
                        wake.wait_until(until)?;
                    }
                }
            }
        }
    }
}

struct PcmUpmix {
    joint: Joint,
    last: Option<(u64, u32)>,
    has_surround: bool,
}

impl PcmUpmix {
    fn submit(&mut self, packet: &mut Packet) {
        let Ok(input) = CaptureInput::new(&packet.data, packet.count, 480) else { return };
        let Ok(first) = input.property(0) else { return };
        if self.last.is_some_and(|(seq, id)| seq.checked_add(1) != Some(packet.seq) || id != first.ident) {
            self.joint.reset();
            self.has_surround = false;
        }
        self.last = Some((packet.seq, first.ident));
        if self.has_surround { return; }
        let mut front = [None; 2];
        for slot in 0..packet.count {
            let Ok(prop) = input.property(slot) else { return };
            let bytes = input.pcm_bytes(slot);
            match prop.typ {
                2 => front[0] = Some(bytes),
                4 => front[1] = Some(bytes),
                // Keep a padded stereo endpoint multichannel once a surround
                // channel has carried signal, until its generation changes.
                _ if bytes.chunks_exact(4).any(|v| u32::from_le_bytes(v.try_into().unwrap()) & 0x7fff_ffff != 0) => {
                    self.has_surround = true;
                    return;
                }
                _ => {}
            }
        }
        if let [Some(left), Some(right)] = front {
            packet.upmix_eligible = true;
            packet.upmix_ticket = self.joint.submit_planar(left, right);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::sync_channel;

    fn packet(lane: usize, seq: u64, received: Instant, capture_ns: u64) -> Packet {
        Packet { lane, seq, count: 0, data: Vec::new(), received, capture_ns, source_lease: 0, upmix_ticket: None, upmix_eligible: false }
    }

    fn batch(ingress: &mut Ingress, at: Instant, recycle: &SyncSender<Vec<u8>>) -> [Option<Packet>; LANES] {
        match ingress.poll_at(at, recycle, None) {
            Poll::Batch(batch) => batch,
            _ => panic!("expected a render batch"),
        }
    }

    fn primary_ingress(lane: usize) -> Ingress {
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.primary_lane = Some(lane);
        ingress.primary = Some(PrimaryIngress::new(lane));
        ingress
    }

    fn leased_packet(lane: usize, lease: u64, seq: u64, received: Instant, capture_ns: u64) -> Packet {
        let mut packet = packet(lane, seq, received, capture_ns);
        packet.source_lease = lease;
        packet
    }

    #[test]
    fn source_lease_purges_sealed_and_pending_audio_preserving_other_lanes() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(leased_packet(0, 1, 10, start, 100_000_000), &recycle, None);
        ingress.enqueue(leased_packet(1, 1, 20, start, 100_000_000), &recycle, None);
        ingress.seal_front(start + Duration::from_micros(500), &recycle, None);
        assert!(ingress.spatial[0].sealed.is_some());

        let next = start + PERIOD;
        ingress.enqueue(leased_packet(0, 1, 11, next, 110_000_000), &recycle, None);
        ingress.enqueue(leased_packet(1, 1, 21, next, 110_000_000), &recycle, None);
        ingress.enqueue(leased_packet(0, 2, 12, next, 110_000_000), &recycle, None);
        ingress.enqueue(leased_packet(0, 1, 999, next, 110_000_000), &recycle, None);

        assert_eq!(ingress.discarded, 3);
        assert_eq!(ingress.render_epoch, Some(start));
        assert_eq!(ingress.closed_through, Some(0));
        assert_eq!(ingress.spatial.len(), 2);
        assert!(ingress.spatial[0].packets[0].is_none());
        assert_eq!(ingress.spatial[0].packets[1].as_ref().unwrap().seq, 20);
        assert_eq!(ingress.spatial[1].packets[0].as_ref().unwrap().source_lease, 2);
        assert_eq!(ingress.spatial[1].packets[1].as_ref().unwrap().seq, 21);
    }

    #[test]
    fn replacing_primary_rebases_sequence_and_keeps_matching_auxiliary_packets() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(leased_packet(0, 1, 10, start, 100_000_000), &recycle, None);
        let next = start + PERIOD;
        ingress.enqueue(leased_packet(1, 1, 20, next, 110_000_000), &recycle, None);
        ingress.enqueue(leased_packet(0, 2, 0, next, 110_000_000), &recycle, None);
        ingress.enqueue(leased_packet(0, 1, 11, next, 110_000_000), &recycle, None);
        let batch = batch(&mut ingress, next, &recycle);
        assert_eq!(batch[0].as_ref().unwrap().source_lease, 2);
        assert_eq!(batch[1].as_ref().unwrap().seq, 20);
        assert_eq!(ingress.discarded, 2);
    }

    #[test]
    fn primary_mode_never_emits_auxiliary_only_blocks_even_during_a_pause() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(3);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        ingress.enqueue(packet(PCM, 0, start, 100_000_000), &recycle, None);
        assert!(matches!(ingress.poll_at(start + PERIOD * 10, &recycle, None), Poll::Wait(None)));
        ingress.enqueue(packet(3, 0, start + PERIOD * 10, 200_000_000), &recycle, None);
        let packets = batch(&mut ingress, start + PERIOD * 10, &recycle);
        assert!(packets[3].is_some() && packets[0].is_none() && packets[PCM].is_none());
        ingress.enqueue(packet(PCM, 1, start + PERIOD * 11, 210_000_000), &recycle, None);
        assert!(matches!(ingress.poll_at(start + PERIOD * 20, &recycle, None), Poll::Wait(None)));
        ingress.disconnected = true;
        assert!(matches!(ingress.poll_at(start + PERIOD * 20, &recycle, None), Poll::Finished));
        assert_eq!(ingress.primary_same[3], 1);
        assert_eq!(ingress.primary_dropped[0], 1);
        assert_eq!(ingress.primary_dropped[PCM], 2);
    }

    #[test]
    fn primary_sequence_emits_immediately_even_when_capture_timestamps_share_a_bucket() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        for seq in 0..3 {
            let at = start + Duration::from_micros(seq * 50);
            ingress.enqueue(packet(0, seq + 40, at, 100_000_000 + seq), &recycle, None);
            let packets = batch(&mut ingress, at, &recycle);
            assert_eq!(packets[0].as_ref().unwrap().seq, seq + 40);
        }
        assert_eq!(ingress.primary_same[0], 3);
        assert_eq!(ingress.primary_dropped[0], 0);
    }

    #[test]
    fn primary_late_wake_does_not_extend_the_200us_same_period_budget() {
        let start = Instant::now() + PERIOD;
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        for lane in [1, PCM] { ingress.enqueue(packet(lane, 0, start - Duration::from_micros(100), 100_000_000), &recycle, None); }
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        let first = batch(&mut ingress, start, &recycle);
        assert!(first[1].is_some() && first[PCM].is_some());
        let second_at = start + PERIOD;
        ingress.enqueue(packet(0, 1, second_at, 110_000_000), &recycle, None);
        for lane in [1, PCM] { ingress.enqueue(packet(lane, 1, second_at + Duration::from_micros(201), 110_000_000), &recycle, None); }
        let second = batch(&mut ingress, second_at + Duration::from_micros(800), &recycle);
        assert!(second[1].is_none() && second[PCM].is_none());
        ingress.enqueue(packet(0, 2, start + PERIOD * 2, 120_000_000), &recycle, None);
        let third = batch(&mut ingress, start + PERIOD * 2, &recycle);
        for lane in [1, PCM] {
            assert_eq!(third[lane].as_ref().unwrap().seq, 1);
            assert_eq!(ingress.primary_same[lane], 1);
            assert_eq!(ingress.primary_previous[lane], 1);
            assert_eq!(ingress.primary_dropped[lane], 0);
        }
    }

    #[test]
    fn primary_auxiliary_lanes_choose_independently_and_consume_each_block_once() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start, &recycle);
        ingress.enqueue(packet(1, 0, start + PERIOD, 100_000_000), &recycle, None);
        ingress.enqueue(packet(PCM, 1, start + PERIOD, 110_000_000), &recycle, None);
        ingress.enqueue(packet(0, 1, start + PERIOD, 110_000_000), &recycle, None);
        let second = batch(&mut ingress, start + PERIOD, &recycle);
        assert_eq!(second[1].as_ref().unwrap().seq, 0);
        assert_eq!(second[PCM].as_ref().unwrap().seq, 1);
        assert_eq!(ingress.primary_previous[1], 1);
        assert_eq!(ingress.primary_same[PCM], 1);
        ingress.enqueue(packet(1, 0, start + PERIOD * 2, 100_000_000), &recycle, None);
        ingress.enqueue(packet(0, 2, start + PERIOD * 2, 120_000_000), &recycle, None);
        let third = batch(&mut ingress, start + PERIOD * 2, &recycle);
        assert!(third[1].is_none() && third[PCM].is_none());
        assert_eq!(ingress.primary_dropped[1], 1);
    }

    #[test]
    fn primary_chooses_an_unplayed_previous_block_before_the_current_block() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start, &recycle);
        ingress.enqueue(packet(1, 1, start + PERIOD, 110_000_000), &recycle, None);
        ingress.enqueue(packet(1, 0, start + PERIOD, 100_000_000), &recycle, None);
        ingress.enqueue(packet(0, 1, start + PERIOD, 110_000_000), &recycle, None);
        assert_eq!(batch(&mut ingress, start + PERIOD, &recycle)[1].as_ref().unwrap().seq, 0);
        ingress.enqueue(packet(0, 2, start + PERIOD * 2, 120_000_000), &recycle, None);
        assert_eq!(batch(&mut ingress, start + PERIOD * 2, &recycle)[1].as_ref().unwrap().seq, 1);
        assert_eq!(ingress.primary_previous[1], 2);
    }

    #[test]
    fn primary_drops_auxiliary_blocks_older_than_one_period() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        for seq in 0..2 {
            ingress.enqueue(packet(0, seq, start + PERIOD * seq as u32, 100_000_000 + seq * 10_000_000), &recycle, None);
            batch(&mut ingress, start + PERIOD * seq as u32, &recycle);
        }
        ingress.enqueue(packet(1, 0, start + PERIOD * 2, 100_000_000), &recycle, None);
        ingress.enqueue(packet(0, 2, start + PERIOD * 2, 120_000_000), &recycle, None);
        assert!(batch(&mut ingress, start + PERIOD * 2, &recycle)[1].is_none());
        assert_eq!(ingress.primary_dropped[1], 1);
    }

    #[test]
    fn primary_pcm_without_capture_time_uses_arrival_fallback_and_can_be_previous() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start, &recycle);
        ingress.enqueue(packet(PCM, 0, start + Duration::from_micros(300), 0), &recycle, None);
        ingress.enqueue(packet(0, 1, start + PERIOD, 110_000_000), &recycle, None);
        assert_eq!(batch(&mut ingress, start + PERIOD, &recycle)[PCM].as_ref().unwrap().seq, 0);
        assert_eq!(ingress.primary_previous[PCM], 1);
    }

    #[test]
    fn primary_sequence_gap_is_explicit_failure_not_silent_audio_loss() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start, &recycle);
        ingress.enqueue(packet(0, 2, start + PERIOD, 110_000_000), &recycle, None);
        assert!(matches!(ingress.poll_at(start + PERIOD, &recycle, None), Poll::Failed(13)));
        assert_eq!(ingress.primary_same[0], 1);
        assert_eq!(ingress.primary_dropped[0], 1);
    }

    #[test]
    fn primary_burst_draining_preserves_all_primary_blocks_with_a_bounded_queue() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let (sender, input) = sync_channel(16);
        let wake = RenderWake::new().unwrap();
        let mut ingress = primary_ingress(0);
        for seq in 0..10 { sender.send(packet(0, seq, start, 100_000_000)).unwrap(); }
        drop(sender);
        for seq in 0..10 {
            let packets = ingress.next(&input, &recycle, &wake, &OnceLock::new()).unwrap().unwrap();
            assert_eq!(packets[0].as_ref().unwrap().seq, seq);
            assert!(ingress.primary.as_ref().unwrap().main.len() <= QUEUE_LIMIT);
        }
        assert!(ingress.next(&input, &recycle, &wake, &OnceLock::new()).unwrap().is_none());
        assert_eq!(ingress.primary_same[0], 10);
        assert_eq!(ingress.primary_dropped[0], 0);
    }

    #[test]
    fn primary_control_waits_for_a_fresh_successor_and_preserves_unconsumed_inputs() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start, &recycle);
        ingress.set_primary_lane(Some(1)).unwrap();
        assert_eq!(ingress.primary_lane, Some(0));
        ingress.enqueue(packet(0, 1, start + PERIOD, 110_000_000), &recycle, None);
        assert_eq!(batch(&mut ingress, start + PERIOD, &recycle)[0].as_ref().unwrap().seq, 1);
        assert_eq!(ingress.primary_lane, Some(0));
        // A successor's old unplayed block cannot create a second output for an
        // already emitted source period, even though the control request exists.
        ingress.enqueue(packet(1, 0, start + PERIOD, 100_000_000), &recycle, None);
        assert!(matches!(ingress.poll_at(start + PERIOD, &recycle, None), Poll::Wait(None)));
        assert_eq!(ingress.primary_lane, Some(0));
        ingress.enqueue(packet(0, 2, start + PERIOD * 2, 120_000_000), &recycle, None);
        ingress.enqueue(packet(1, 1, start + PERIOD * 2, 120_000_000), &recycle, None);
        let switched = batch(&mut ingress, start + PERIOD * 2, &recycle);
        assert_eq!(ingress.primary_lane, Some(1));
        assert_eq!(switched[1].as_ref().unwrap().seq, 1);
        assert_eq!(switched[0].as_ref().unwrap().seq, 2);
        assert_eq!(ingress.primary_dropped[1], 1);
        ingress.enqueue(packet(1, 2, start + PERIOD * 3, 130_000_000), &recycle, None);
        assert_eq!(batch(&mut ingress, start + PERIOD * 3, &recycle)[1].as_ref().unwrap().seq, 2);
    }

    #[test]
    fn primary_control_reselecting_current_lane_does_not_reset_its_sequence() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(0, 40, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start, &recycle);
        ingress.set_primary_lane(Some(0)).unwrap();
        ingress.enqueue(packet(0, 41, start + PERIOD, 110_000_000), &recycle, None);
        assert_eq!(batch(&mut ingress, start + PERIOD, &recycle)[0].as_ref().unwrap().seq, 41);
        assert_eq!(ingress.primary.as_ref().unwrap().first_sequence, Some(40));
        assert_eq!(ingress.primary_same[0], 2);
        assert!(ingress.pending_primary_lane.is_none());
    }

    #[test]
    fn primary_control_can_enable_from_legacy_without_waiting_or_replaying() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(packet(PCM, 0, start, 100_000_000), &recycle, None);
        ingress.enqueue(packet(3, 20, start, 100_000_000), &recycle, None);
        ingress.set_primary_lane(Some(3)).unwrap();
        let packets = batch(&mut ingress, start, &recycle);
        assert_eq!(ingress.primary_lane, Some(3));
        assert_eq!(packets[3].as_ref().unwrap().seq, 20);
        assert_eq!(packets[PCM].as_ref().unwrap().seq, 0);
        assert!(matches!(ingress.poll_at(start + PERIOD, &recycle, None), Poll::Wait(None)));
    }

    #[test]
    fn primary_disable_preserves_queued_pcm_ticket_and_does_not_replay_primary() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start, &recycle);
        let mut pcm = packet(PCM, 42, start + PERIOD, 110_000_000);
        pcm.upmix_eligible = true;
        pcm.upmix_ticket = Some((7, 8));
        ingress.enqueue(pcm, &recycle, None);
        ingress.set_primary_lane(None).unwrap();
        let packets = batch(&mut ingress, start + PERIOD * 4, &recycle);
        assert_eq!(ingress.primary_lane, None);
        assert!(packets[0].is_none());
        assert_eq!(packets[PCM].as_ref().unwrap().seq, 42);
        assert_eq!(packets[PCM].as_ref().unwrap().upmix_ticket, Some((7, 8)));
    }

    #[test]
    fn primary_control_rejects_invalid_lanes_and_latest_request_replaces_pending() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = primary_ingress(0);
        assert_eq!(ingress.set_primary_lane(Some(PCM)), Err(87));
        assert!(ingress.pending_primary_lane.is_none());
        ingress.set_primary_lane(Some(1)).unwrap();
        ingress.set_primary_lane(Some(2)).unwrap();
        ingress.enqueue(packet(1, 0, start, 100_000_000), &recycle, None);
        ingress.enqueue(packet(2, 0, start, 100_000_000), &recycle, None);
        let packets = batch(&mut ingress, start, &recycle);
        assert_eq!(ingress.primary_lane, Some(2));
        assert!(packets[2].is_some() && packets[1].is_some());
    }

    #[test]
    fn primary_control_mailbox_is_observed_after_a_control_wake_without_new_input() {
        use std::{sync::Arc, thread};
        let (recycle, _pool) = sync_channel(64);
        let (sender, input) = sync_channel(4);
        let (started, ready) = sync_channel(1);
        let (finished, result) = sync_channel(1);
        let wake = Arc::new(RenderWake::new().unwrap());
        let control = Arc::new(AtomicUsize::new(PRIMARY_NO_REQUEST));
        let mut ingress = primary_ingress(0);
        ingress.enqueue(packet(PCM, 42, Instant::now() - PERIOD * 10, 0), &recycle, None);
        let worker_wake = wake.clone();
        let worker_control = control.clone();
        let worker = thread::spawn(move || {
            started.send(()).unwrap();
            let batch = ingress.next_with_primary_control(&input, &recycle, &worker_wake, &OnceLock::new(), &worker_control);
            finished.send((batch, ingress.primary_lane)).unwrap();
        });
        ready.recv().unwrap();
        control.store(PRIMARY_DISABLE, Ordering::Release);
        wake.notify();
        let answer = result.recv_timeout(Duration::from_secs(2));
        // Also release the worker on failure, so a failed wake regression cannot
        // strand the test process waiting on an open input channel.
        drop(sender);
        wake.notify();
        worker.join().unwrap();
        let (packets, lane) = answer.expect("control wake must release queued PCM without new audio input");
        assert_eq!(lane, None);
        assert_eq!(packets.unwrap().unwrap()[PCM].as_ref().unwrap().seq, 42);
        assert_eq!(control.load(Ordering::Acquire), PRIMARY_NO_REQUEST);
    }

    #[test]
    fn capture_period_accepts_an_earlier_stream_without_rebasing() {
        let start = Instant::now();
        let first = packet(0, 0, start, 100_000_000);
        let mut clock = SpatialClock::new(&first);
        assert_eq!(clock.slot(&first), Some(0));
        assert_eq!(clock.slot(&packet(1, 0, start + PERIOD, 99_900_000)), Some(0));
        assert_eq!(clock.slot(&packet(0, 1, start + PERIOD * 4, 109_990_000)), Some(1));
        assert_eq!(clock.slot(&packet(0, 0, start + PERIOD * 5, 89_990_000)), None);
        assert_eq!(clock.capture_origin, Some(100_000_000));
    }

    #[test]
    fn pcm_does_not_establish_the_spatial_epoch() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(packet(PCM, 0, start, 0), &recycle, None);
        assert!(ingress.spatial_clock.is_none());
        ingress.enqueue(packet(0, 0, start + PERIOD, 123_000_000), &recycle, None);
        assert_eq!(ingress.spatial.front().unwrap().slot, 0);
        assert_eq!(ingress.spatial_clock.as_ref().unwrap().capture_origin, Some(123_000_000));
        assert_eq!(ingress.spatial_untimed, 0);
    }

    #[test]
    fn a_missing_lane_extends_at_200_and_seals_as_soon_as_it_arrives() {
        let start = Instant::now() + PERIOD;
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.last_spatial_arrival[0] = Some(start - PERIOD);
        ingress.last_spatial_arrival[1] = Some(start - PERIOD);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        assert!(matches!(ingress.poll_at(start + Duration::from_micros(199), &recycle, None), Poll::Wait(_)));
        assert!(matches!(ingress.poll_at(start + Duration::from_micros(200), &recycle, None), Poll::Wait(_)));
        ingress.enqueue(packet(1, 0, start + Duration::from_micros(250), 100_010_000), &recycle, None);
        let packets = batch(&mut ingress, start + Duration::from_micros(250), &recycle);
        assert!(packets[0].is_some() && packets[1].is_some());
        assert_eq!(ingress.spatial_window_closures, [0, 1, 0, 0]);
    }

    #[test]
    fn a_late_wake_uses_arrival_times_at_each_checkpoint() {
        let start = Instant::now() + PERIOD;
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.last_spatial_arrival[1] = Some(start - PERIOD);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        ingress.enqueue(packet(1, 0, start + Duration::from_micros(400), 100_000_001), &recycle, None);
        let packets = batch(&mut ingress, start + Duration::from_micros(800), &recycle);
        assert!(packets[0].is_some() && packets[1].is_some());
        assert_eq!(ingress.spatial_window_closures, [0, 0, 1, 0]);
        assert_eq!(ingress.spatial_windows, [0, 0, 0, 0, 1]);
        assert_eq!(ingress.spatial_late, 0);
    }

    #[test]
    fn input_after_500_microseconds_cannot_join_or_become_the_next_frame() {
        let start = Instant::now() + PERIOD;
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.last_spatial_arrival[1] = Some(start - PERIOD);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        ingress.enqueue(packet(1, 0, start + Duration::from_micros(700), 100_000_010), &recycle, None);
        let packets = batch(&mut ingress, start + Duration::from_micros(800), &recycle);
        assert!(packets[0].is_some() && packets[1].is_none());
        assert_eq!(ingress.spatial_incomplete, 1);
        assert_eq!(ingress.spatial_late, 1);
        ingress.enqueue(packet(2, 0, start + PERIOD, 100_000_020), &recycle, None);
        assert_eq!(ingress.spatial_late, 2);
        assert!(ingress.spatial.is_empty());
        ingress.enqueue(packet(0, 1, start + PERIOD, 110_000_000), &recycle, None);
        assert_eq!(ingress.spatial.front().unwrap().slot, 1);
    }

    #[test]
    fn an_unknown_lane_after_the_first_complete_checkpoint_is_late() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        ingress.enqueue(packet(1, 0, start + Duration::from_micros(300), 100_000_001), &recycle, None);
        let packets = batch(&mut ingress, start + Duration::from_micros(800), &recycle);
        assert!(packets[0].is_some() && packets[1].is_none());
        assert_eq!(ingress.spatial_window_closures, [1, 0, 0, 0]);
        assert_eq!(ingress.spatial_late, 1);
    }

    #[test]
    fn spatial_sealing_cannot_bypass_the_shared_render_gate() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start + Duration::from_micros(200), &recycle);
        ingress.enqueue(packet(0, 1, start + Duration::from_micros(600), 110_000_000), &recycle, None);
        assert!(matches!(ingress.poll_at(start + Duration::from_micros(800), &recycle, None), Poll::Wait(Some(at)) if at == start + PERIOD));
        let packets = batch(&mut ingress, start + PERIOD, &recycle);
        assert_eq!(packets[0].as_ref().unwrap().seq, 1);
    }

    #[test]
    fn pcm_survives_a_spatial_close_and_keeps_its_own_due_time() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start + Duration::from_micros(200), &recycle);
        ingress.enqueue(packet(PCM, 42, start + Duration::from_micros(600), 0), &recycle, None);
        let due = ingress.pcm[0].front().unwrap().0;
        ingress.enqueue(packet(1, 0, start + Duration::from_micros(700), 100_000_000), &recycle, None);
        assert_eq!(ingress.pcm[0].front().unwrap().0, due);
        let packets = batch(&mut ingress, start + PERIOD, &recycle);
        assert_eq!(packets[PCM].as_ref().unwrap().seq, 42);
        assert!(packets[..PCM].iter().all(Option::is_none));
    }

    #[test]
    fn an_unfinished_pcm_upmix_cannot_extend_the_spatial_window() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        let mut pcm = packet(PCM, 42, start, 0);
        pcm.upmix_eligible = true;
        ingress.enqueue(pcm, &recycle, None);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        let packets = batch(&mut ingress, start + Duration::from_micros(200), &recycle);
        assert!(packets[0].is_some());
        assert!(packets[PCM].is_none());
        let (due, queued) = ingress.pcm[0].front().unwrap();
        assert_eq!(queued.seq, 42);
        let due = *due;
        assert_eq!(batch(&mut ingress, due + PERIOD, &recycle)[PCM].as_ref().unwrap().seq, 42);
        assert!(ingress.pcm[0].is_empty());
        assert_eq!(ingress.spatial_window_closures, [1, 0, 0, 0]);
    }

    #[test]
    fn capacity_and_duplicate_drops_never_retag_old_spatial_packets() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        let capacity = ingress.spatial.capacity();
        for slot in 0..10 {
            ingress.enqueue(packet(0, slot, start + PERIOD * slot as u32, 100_000_000 + slot * 10_000_000), &recycle, None);
            assert!(ingress.spatial.len() <= QUEUE_LIMIT);
            assert_eq!(ingress.spatial.capacity(), capacity);
        }
        assert_eq!(ingress.spatial.front().unwrap().slot, 6);
        assert_eq!(ingress.closed_through, Some(5));
        ingress.enqueue(packet(0, 99, start + PERIOD * 10, 190_000_000), &recycle, None);
        assert_eq!(ingress.spatial_duplicates, 1);
        ingress.enqueue(packet(1, 0, start + PERIOD * 10, 150_000_000), &recycle, None);
        assert_eq!(ingress.spatial_late, 1);
        assert_eq!(ingress.discarded, 8);
    }

    #[test]
    fn disconnect_preserves_spatial_periods_instead_of_zipping_lane_heads() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        ingress.enqueue(packet(1, 1, start + PERIOD, 110_000_000), &recycle, None);
        ingress.disconnected = true;
        let first = batch(&mut ingress, start + PERIOD, &recycle);
        assert!(first[0].is_some() && first[1].is_none());
        let second = batch(&mut ingress, start + PERIOD, &recycle);
        assert!(second[0].is_none() && second[1].is_some());
        assert!(matches!(ingress.poll_at(start + PERIOD, &recycle, None), Poll::Finished));
    }

    #[test]
    fn untimed_spatial_fallback_is_counted_and_pcm_does_not_count() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        ingress.enqueue(packet(PCM, 0, start, 0), &recycle, None);
        ingress.enqueue(packet(0, 0, start, 0), &recycle, None);
        ingress.enqueue(packet(0, 1, start + PERIOD, 0), &recycle, None);
        assert_eq!(ingress.spatial_untimed, 2);
        assert_eq!(ingress.spatial.back().unwrap().slot, 1);
    }

    #[test]
    fn early_spatial_experiment_bypasses_only_the_gate_not_source_periods() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, true);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start + Duration::from_micros(200), &recycle);
        ingress.enqueue(packet(0, 1, start + Duration::from_micros(600), 110_000_000), &recycle, None);
        let early = start + Duration::from_micros(800);
        assert!(early < ingress.render_at());
        let packets = batch(&mut ingress, early, &recycle);
        assert_eq!(packets[0].as_ref().unwrap().seq, 1);
        assert_eq!(ingress.closed_through, Some(1));
        assert_eq!(ingress.render_tick, 2);
        let (_, sealed, effective_gate) = ingress.render_boundary.unwrap();
        assert_eq!(sealed, early);
        assert!(effective_gate <= sealed);
        ingress.enqueue(packet(1, 0, start + PERIOD, 100_000_010), &recycle, None);
        assert!(ingress.spatial.is_empty());
        assert_eq!(ingress.spatial_late, 1);
    }

    #[test]
    fn early_spatial_experiment_still_waits_for_an_unsealed_frame() {
        let start = Instant::now() + PERIOD;
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, true);
        ingress.last_spatial_arrival[0] = Some(start - PERIOD);
        ingress.last_spatial_arrival[1] = Some(start - PERIOD);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        // The experiment cannot skip the outstanding lane's collection window.
        assert!(matches!(ingress.poll_at(start + Duration::from_micros(199), &recycle, None), Poll::Wait(_)));
        assert!(matches!(ingress.poll_at(start + Duration::from_micros(499), &recycle, None), Poll::Wait(_)));
        let packets = batch(&mut ingress, start + Duration::from_micros(500), &recycle);
        assert!(packets[0].is_some() && packets[1].is_none());
        assert_eq!(ingress.spatial_incomplete, 1);
        assert_eq!(ingress.spatial_window_closures, [0, 0, 0, 1]);
    }

    #[test]
    fn early_spatial_experiment_preserves_pcm_only_pacing() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, true);
        ingress.enqueue(packet(PCM, 0, start, 0), &recycle, None);
        ingress.enqueue(packet(PCM, 1, start + Duration::from_micros(100), 0), &recycle, None);
        assert!(matches!(ingress.poll_at(start + Duration::from_micros(200), &recycle, None), Poll::Wait(_)));
        assert_eq!(batch(&mut ingress, start + START_JOIN, &recycle)[PCM].as_ref().unwrap().seq, 0);
        assert!(matches!(ingress.poll_at(start + Duration::from_millis(3), &recycle, None), Poll::Wait(Some(at)) if at == start + PERIOD));
        assert_eq!(batch(&mut ingress, start + PERIOD, &recycle)[PCM].as_ref().unwrap().seq, 1);
        assert!(ingress.spatial_clock.is_none());
    }

    #[test]
    fn early_spatial_blocks_are_accounted_before_pcm_only_output_resumes() {
        let start = Instant::now();
        let (recycle, _pool) = sync_channel(64);
        let mut ingress = Ingress::with_early_spatial_render(None, true);
        ingress.enqueue(packet(0, 0, start, 100_000_000), &recycle, None);
        batch(&mut ingress, start + Duration::from_micros(200), &recycle);
        ingress.enqueue(packet(0, 1, start + Duration::from_micros(600), 110_000_000), &recycle, None);
        batch(&mut ingress, start + Duration::from_micros(800), &recycle);
        ingress.enqueue(packet(PCM, 42, start + Duration::from_micros(900), 0), &recycle, None);
        assert!(matches!(ingress.poll_at(start + Duration::from_millis(4), &recycle, None), Poll::Wait(Some(at)) if at == start + PERIOD * 2));
        let packets = batch(&mut ingress, start + PERIOD * 2, &recycle);
        assert!(packets[..PCM].iter().all(Option::is_none));
        assert_eq!(packets[PCM].as_ref().unwrap().seq, 42);
    }

    #[test]
    fn burst_pcm_keeps_order_and_renders_at_one_block_per_period() {
        let start = Instant::now();
        let mut ingress = Ingress::with_early_spatial_render(None, false);
        let (recycle, _rx) = sync_channel(PCM_QUEUE_LIMIT);
        let burst = 11_u32;
        for group in 0..3 {
            let arrival = start + PERIOD * (group * burst);
            for block in 0..burst {
                let seq = u64::from(group * burst + block);
                ingress.enqueue(packet(PCM, seq, arrival, 0), &recycle, None);
            }
            for block in 0..burst {
                let at = (arrival + PERIOD * block).max(start + START_JOIN);
                let output = batch(&mut ingress, at, &recycle);
                assert_eq!(output[PCM].as_ref().unwrap().seq, u64::from(group * burst + block));
                assert!(!matches!(ingress.poll_at(at, &recycle, None), Poll::Batch(_)));
            }
        }
        assert_eq!(ingress.discarded, 0);
    }

    #[test]
    fn a_faster_pcm_source_does_not_push_queued_packets_past_the_render_clock() {
        let start = Instant::now();
        let mut previous = None;
        let mut queue = VecDeque::new();
        let mut deadline = start + UPMIX_PREROLL;
        let mut rendered = 0;
        let mut dropped = 0;
        for seq in 0..10_000 {
            let arrived = start + Duration::from_micros(seq * 9_950);
            while deadline <= arrived {
                if queue.front().is_some_and(|&due| due <= deadline + PERIOD / 4) {
                    queue.pop_front();
                    rendered += 1;
                }
                deadline += PERIOD;
            }
            let due = packet_due(deadline, arrived + UPMIX_PREROLL, previous);
            assert!(due <= arrived + UPMIX_PREROLL + PERIOD / 2);
            if queue.len() == PCM_QUEUE_LIMIT { queue.pop_front(); dropped += 1; }
            queue.push_back(due);
            previous = Some(due);
        }
        assert!(rendered >= 9_940, "rendered {rendered}, discarded {dropped}");
    }
}
