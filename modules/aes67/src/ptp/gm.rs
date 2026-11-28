//! Grandmaster engine: pure, offline-testable message scheduling and
//! BMCA-lite decisions with an injected clock, plus the wire messages it
//! produces. The transport (UDP sockets, multicast join, DSCP marking) lives
//! in [super::PtpRuntime]; this module never touches a socket.
//!
//! Spec citations: AES67-2023 Annex A Table A.1 fixes logAnnounceInterval=1
//! (2 s), logSyncInterval=-3 (125 ms) and end-to-end delay request-response
//! (A.2.3); IEEE 1588-2008 clause 13.3 defines the message layouts, 9.3.2 the
//! data-set comparison order and 8.2.3 the timeProperties data set.
use super::codec::{
    Announce, Body, CURRENT_UTC_OFFSET_VALID, ClockId, Message, PTP_TIMESCALE, PortId, TWO_STEP,
};
use crate::time::{NS_PER_MS, NS_PER_S};
use std::{
    cmp::Ordering,
    net::{Ipv4Addr, SocketAddrV4},
};

/// AES67-2023 Annex A Table A.1 line 2074-2076: logAnnounceInterval 1 = 2 s.
pub const ANNOUNCE_INTERVAL_NS: i128 = 2 * NS_PER_S;
/// AES67-2023 Annex A Table A.1 line 2077-2079: logSyncInterval -3 = 125 ms.
pub const SYNC_INTERVAL_NS: i128 = 125 * NS_PER_MS;
/// AES67-2023 Annex A Table A.2 line 2241-2244: the media profile's default
/// clockClass 248 ("Default ... unspecified tolerance").
pub const DEFAULT_CLOCK_CLASS: u8 = 248;
/// clockAccuracy unknown (IEEE 1588-2008 7.6.2.2, 0xFE). IMPLEMENTATION
/// CHOICE matching the board profile, not an AES67 numeric requirement.
pub const DEFAULT_CLOCK_ACCURACY: u8 = 0xFE;
/// offsetScaledLogVariance 0xFFFF (unknown). EXTENSION choice consistent with
/// the board's linuxptp profile.
pub const DEFAULT_OFFSET_SCALED_LOG_VARIANCE: u16 = 0xFFFF;
/// AES67-2023 Annex A Table A.1 line 2092-2099: default priority1/priority2 128.
pub const DEFAULT_PRIORITY: u8 = 128;
/// timeSource INTERNAL_OSCILLATOR (IEEE 1588-2008 7.6.2.3); ARB GM, no PHC.
pub const TIME_SOURCE_INTERNAL_OSCILLATOR: u8 = 0xA0;

/// Port number of the single port of this ordinary clock (IEEE 1588-2008
/// 7.5.2); the clockIdentity is the interface EUI-64.
pub const PORT_NUMBER: u16 = 1;

pub fn port_identity(clock_identity: ClockId) -> PortId {
    let mut port = [0; 10];
    port[..8].copy_from_slice(&clock_identity);
    port[8..10].copy_from_slice(&PORT_NUMBER.to_be_bytes());
    port
}

/// The six data-set fields BMCA compares (IEEE 1588-2008 9.3.2 / 9.3.4),
/// taken from the Announce message's grandmaster fields.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Dataset {
    pub priority1: u8,
    pub clock_class: u8,
    pub clock_accuracy: u8,
    pub offset_scaled_log_variance: u16,
    pub priority2: u8,
    pub clock_identity: ClockId,
}

impl Dataset {
    pub fn from_announce(announce: &Announce) -> Self {
        Self {
            priority1: announce.priority1,
            clock_class: announce.clock_class,
            clock_accuracy: announce.clock_accuracy,
            offset_scaled_log_variance: announce.offset_scaled_log_variance,
            priority2: announce.priority2,
            clock_identity: announce.grandmaster,
        }
    }

    /// IEEE 1588-2008 9.3.2 data-set comparison: lower priority1 wins, then
    /// lower clockClass, then lower clockAccuracy, then lower
    /// offsetScaledLogVariance, then lower priority2, then the numerically
    /// lower clockIdentity (compared from MSB) as the final tie-break.
    /// Returns Less when self is the better (winning) data set.
    pub fn compare(&self, other: &Dataset) -> Ordering {
        self.priority1
            .cmp(&other.priority1)
            .then(self.clock_class.cmp(&other.clock_class))
            .then(self.clock_accuracy.cmp(&other.clock_accuracy))
            .then(
                self.offset_scaled_log_variance
                    .cmp(&other.offset_scaled_log_variance),
            )
            .then(self.priority2.cmp(&other.priority2))
            .then(self.clock_identity.cmp(&other.clock_identity))
    }

    pub fn is_better_than(&self, other: &Dataset) -> bool {
        self.compare(other) == Ordering::Less
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Config {
    /// EUI-64 clock identity derived from the interface MAC (never random).
    pub identity: ClockId,
    pub domain: u8,
    pub priority1: u8,
    pub priority2: u8,
    pub clock_class: u8,
    pub clock_accuracy: u8,
    pub offset_scaled_log_variance: u16,
    /// Some(offset) => ptpTimescale=1, currentUtcOffsetValid=1, time traceable
    /// claim; None => ARB (ptpTimescale=0).
    pub utc_offset: Option<i16>,
    pub announce_interval_ns: i128,
    pub sync_interval_ns: i128,
    /// A foreign master is gone after this long without an Announce
    /// (AES67 Annex A Table A.1: announceReceiptTimeout 3 x 2 s = 6 s).
    pub announce_timeout_ns: i128,
    /// Multicast group / unicast peer the GM messages are addressed to.
    pub multicast: Ipv4Addr,
    pub unicast: Option<Ipv4Addr>,
    pub event_port: u16,
    pub general_port: u16,
}

impl Config {
    pub fn dataset(&self) -> Dataset {
        Dataset {
            priority1: self.priority1,
            clock_class: self.clock_class,
            clock_accuracy: self.clock_accuracy,
            offset_scaled_log_variance: self.offset_scaled_log_variance,
            priority2: self.priority2,
            clock_identity: self.identity,
        }
    }
    pub fn time_traceable(&self) -> bool {
        self.utc_offset.is_some()
    }
    pub fn flags(&self) -> u16 {
        let mut flags = TWO_STEP;
        if self.utc_offset.is_some() {
            flags |= PTP_TIMESCALE | CURRENT_UTC_OFFSET_VALID;
        }
        flags
    }
    fn destination(&self, event: bool) -> SocketAddrV4 {
        let ip = self.unicast.unwrap_or(self.multicast);
        SocketAddrV4::new(
            ip,
            if event {
                self.event_port
            } else {
                self.general_port
            },
        )
    }
}

/// Which UDP socket a message leaves from: the event socket carries Sync and
/// Delay_Req (UDP 319), the general socket carries Announce, Follow_Up and
/// Delay_Resp (UDP 320); IEEE 1588-2008 Annex D/E and AES67 Annex A line 2029.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Socket {
    Event,
    General,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Packet {
    pub socket: Socket,
    pub destination: SocketAddrV4,
    pub message: Message,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GmState {
    /// Announcing; the only state that may answer Delay_Req.
    Active,
    /// Stepped aside for a foreign GM; silent, still listening to reclaim.
    Passive,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StepReason {
    /// A foreign Announce carried a better data set (BMCA-lite).
    BetterForeignGm,
    /// A worse foreign GM kept announcing for two announce intervals: dual-GM
    /// watchdog, yield rather than run two GMs.
    DualGm,
}

impl StepReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::BetterForeignGm => "better_foreign_gm",
            Self::DualGm => "dual_gm_detected",
        }
    }
}

/// Reports what changed inside a [Gm] call. Events accumulate until drained
/// with [Gm::take_events], so a step-aside decided in receive() is still
/// visible to the caller after the next tick.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Events {
    pub stepped_aside: Option<StepReason>,
    pub reclaimed: bool,
    pub foreign_worse: bool,
    pub dual_gm: bool,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct Diagnostics {
    pub announces_sent: u64,
    pub syncs_sent: u64,
    pub follow_ups_sent: u64,
    pub delay_responses_sent: u64,
    pub foreign_announces: u64,
    pub invalid_announces: u64,
    pub foreign_worse: u64,
    pub dual_gm: u64,
    pub stepped_aside: u64,
    pub reclaimed: u64,
    pub wrong_domain: u64,
}

#[derive(Clone, Copy, Debug)]
struct ForeignMaster {
    dataset: Dataset,
    identity: ClockId,
    last_seen: i128,
}

pub struct Gm {
    config: Config,
    state: GmState,
    next_announce: i128,
    next_sync: i128,
    announce_seq: u16,
    sync_seq: u16,
    /// Best foreign master seen while passive (or while active for reclaim
    /// bookkeeping); only one is tracked (BMCA-lite, no foreign-master table).
    foreign: Option<ForeignMaster>,
    /// First time a worse foreign GM was heard while we announce.
    worse_since: Option<i128>,
    diagnostics: Diagnostics,
    events: Events,
}

impl Gm {
    pub fn new(config: Config, now: i128) -> Self {
        Self {
            config,
            state: GmState::Active,
            next_announce: now,
            next_sync: now,
            announce_seq: 0,
            sync_seq: 0,
            foreign: None,
            worse_since: None,
            diagnostics: Diagnostics::default(),
            events: Events::default(),
        }
    }

    pub fn config(&self) -> &Config {
        &self.config
    }
    pub fn state(&self) -> GmState {
        self.state
    }
    pub fn identity(&self) -> ClockId {
        self.config.identity
    }
    pub fn dataset(&self) -> Dataset {
        self.config.dataset()
    }
    pub fn diagnostics(&self) -> &Diagnostics {
        &self.diagnostics
    }

    /// Identity of the best foreign grandmaster currently tracked, if any.
    pub fn foreign_identity(&self) -> Option<ClockId> {
        self.foreign.map(|foreign| foreign.identity)
    }
    pub fn events(&self) -> Events {
        self.events
    }
    pub fn take_events(&mut self) -> Events {
        std::mem::take(&mut self.events)
    }

    /// A better foreign Announce (BMCA-lite) or the dual-GM watchdog makes us
    /// stop announcing immediately; tick() emits nothing afterwards.
    fn step_aside(&mut self, reason: StepReason) {
        if self.state == GmState::Active {
            self.state = GmState::Passive;
            self.worse_since = None;
            self.diagnostics.stepped_aside += 1;
            self.events.stepped_aside = Some(reason);
            if reason == StepReason::DualGm {
                self.diagnostics.dual_gm += 1;
                self.events.dual_gm = true;
            }
        }
    }

    fn reclaim(&mut self, now: i128) {
        if self.state == GmState::Passive {
            self.state = GmState::Active;
            self.next_announce = now;
            self.next_sync = now;
            self.diagnostics.reclaimed += 1;
            self.events.reclaimed = true;
        }
    }

    fn observe_foreign(&mut self, announce: &Announce, now: i128) {
        self.foreign = Some(ForeignMaster {
            dataset: Dataset::from_announce(announce),
            identity: announce.grandmaster,
            last_seen: now,
        });
        self.diagnostics.foreign_announces += 1;
    }

    /// Emit everything due at now. gm_now is the current GM clock value used
    /// for Announce originTimestamp and Follow_Up preciseOriginTimestamp.
    pub fn tick(&mut self, now: i128, gm_now: i128) -> Vec<Packet> {
        let mut packets = Vec::new();
        // A foreign master that stops announcing is forgotten after the
        // announce receipt timeout (AES67 Annex A Table A.1).
        if self
            .foreign
            .is_some_and(|foreign| now - foreign.last_seen >= self.config.announce_timeout_ns)
        {
            self.foreign = None;
            self.worse_since = None;
        }
        match self.state {
            GmState::Passive => {
                if self.foreign.is_none() {
                    self.reclaim(now);
                }
            }
            GmState::Active => {
                // Dual-GM watchdog: a worse foreign GM that keeps announcing
                // for two announce intervals while we also announce is a
                // configuration error; yield instead of running two GMs.
                if self
                    .worse_since
                    .is_some_and(|since| now - since >= 2 * self.config.announce_interval_ns)
                {
                    self.diagnostics.foreign_worse += 1;
                    self.events.foreign_worse = true;
                    self.step_aside(StepReason::DualGm);
                }
            }
        }
        if self.state != GmState::Active {
            return packets;
        }
        if now >= self.next_announce {
            packets.push(Packet {
                socket: Socket::General,
                destination: self.config.destination(false),
                message: self.announce(gm_now),
            });
            self.diagnostics.announces_sent += 1;
            self.announce_seq = self.announce_seq.wrapping_add(1);
            self.next_announce = advance(self.next_announce, now, self.config.announce_interval_ns);
        }
        if now >= self.next_sync {
            // Two-step master: IMPLEMENTATION CHOICE matching the validated
            // board GM profile (AES67-2023 has no two-step requirement): Sync carries
            // originTimestamp=0 and the twoStepFlag; Follow_Up immediately
            // follows with the precise origin timestamp (IEEE 1588-2008 11.3).
            let seq = self.sync_seq;
            packets.push(Packet {
                socket: Socket::Event,
                destination: self.config.destination(true),
                message: Message {
                    domain: self.config.domain,
                    source: port_identity(self.config.identity),
                    seq,
                    flags: TWO_STEP,
                    correction_scaled_ns: 0,
                    log_interval: -3,
                    body: Body::Sync,
                },
            });
            packets.push(Packet {
                socket: Socket::General,
                destination: self.config.destination(false),
                message: Message {
                    domain: self.config.domain,
                    source: port_identity(self.config.identity),
                    seq,
                    flags: self.config.flags(),
                    correction_scaled_ns: 0,
                    log_interval: -3,
                    body: Body::FollowUp(gm_now),
                },
            });
            self.diagnostics.syncs_sent += 1;
            self.diagnostics.follow_ups_sent += 1;
            self.sync_seq = self.sync_seq.wrapping_add(1);
            self.next_sync = advance(self.next_sync, now, self.config.sync_interval_ns);
        }
        packets
    }

    /// Handle one received message. Returns the outgoing packets (a Delay_Resp
    /// for an in-domain Delay_Req while active) and updates the BMCA state.
    pub fn receive(
        &mut self,
        message: &Message,
        ip: Ipv4Addr,
        now: i128,
        gm_now: i128,
    ) -> Vec<Packet> {
        if message.domain != self.config.domain {
            self.diagnostics.wrong_domain += 1;
            return Vec::new();
        }
        if message.source == port_identity(self.config.identity) {
            // Our own multicast loop-back or a duplicate of our own announce.
            return Vec::new();
        }
        match message.body {
            Body::Announce(announce) => {
                if announce.validate().is_err() || announce.clock_class == 255 {
                    self.diagnostics.invalid_announces += 1;
                    return Vec::new();
                }
                if announce.grandmaster == self.config.identity {
                    return Vec::new();
                }
                match self.state {
                    GmState::Active => {
                        let theirs = Dataset::from_announce(&announce);
                        match theirs.compare(&self.config.dataset()) {
                            Ordering::Less => {
                                // Better foreign data set: BMCA says the
                                // foreign GM wins; stop announcing now.
                                self.observe_foreign(&announce, now);
                                self.step_aside(StepReason::BetterForeignGm);
                            }
                            Ordering::Greater => {
                                // We win: keep GM, but arm the dual-GM watchdog.
                                self.observe_foreign(&announce, now);
                                self.diagnostics.foreign_worse += 1;
                                self.events.foreign_worse = true;
                                self.worse_since = Some(*self.worse_since.get_or_insert(now));
                            }
                            Ordering::Equal => {}
                        }
                    }
                    GmState::Passive => {
                        // Track the best (only) foreign master to time the
                        // reclaim after it goes away.
                        let better = self.foreign.is_none_or(|held| {
                            Dataset::from_announce(&announce).compare(&held.dataset)
                                == Ordering::Less
                        });
                        if better {
                            self.observe_foreign(&announce, now);
                        } else {
                            self.foreign.as_mut().unwrap().last_seen = now;
                            self.diagnostics.foreign_announces += 1;
                        }
                    }
                }
                Vec::new()
            }
            Body::DelayReq(_) => {
                if self.state != GmState::Active {
                    // A passive GM must not serve delay requests; it is not
                    // part of the active spanning tree.
                    return Vec::new();
                }
                self.diagnostics.delay_responses_sent += 1;
                vec![Packet {
                    socket: Socket::General,
                    destination: SocketAddrV4::new(ip, self.config.general_port),
                    message: Message {
                        domain: self.config.domain,
                        source: port_identity(self.config.identity),
                        seq: message.seq,
                        flags: 0,
                        correction_scaled_ns: 0,
                        log_interval: 0x7f,
                        body: Body::DelayResp {
                            received_ns: gm_now,
                            requester: message.source,
                        },
                    },
                }]
            }
            Body::Sync | Body::FollowUp(_) | Body::DelayResp { .. } => Vec::new(),
        }
    }

    fn announce(&self, gm_now: i128) -> Message {
        Message {
            domain: self.config.domain,
            source: port_identity(self.config.identity),
            seq: self.announce_seq,
            flags: self.config.flags(),
            correction_scaled_ns: 0,
            log_interval: 1,
            body: Body::Announce(Announce {
                origin_ns: gm_now,
                current_utc_offset: self.config.utc_offset.unwrap_or(0),
                priority1: self.config.priority1,
                clock_class: self.config.clock_class,
                clock_accuracy: self.config.clock_accuracy,
                offset_scaled_log_variance: self.config.offset_scaled_log_variance,
                priority2: self.config.priority2,
                grandmaster: self.config.identity,
                steps_removed: 0,
                time_source: TIME_SOURCE_INTERNAL_OSCILLATOR,
            }),
        }
    }
}

/// Advance a periodic deadline by whole intervals without bursting after a
/// scheduling stall: catch up at most one interval and re-anchor to now.
fn advance(deadline: i128, now: i128, interval: i128) -> i128 {
    let next = deadline + interval;
    if next <= now { now + interval } else { next }
}

/// Default GM configuration for the given identity/domain. Values follow
/// AES67-2023 Annex A Table A.1 (fixed media-profile PTP parameters).
pub fn default_config(identity: ClockId, domain: u8) -> Config {
    Config {
        identity,
        domain,
        priority1: DEFAULT_PRIORITY,
        priority2: DEFAULT_PRIORITY,
        clock_class: DEFAULT_CLOCK_CLASS,
        clock_accuracy: DEFAULT_CLOCK_ACCURACY,
        offset_scaled_log_variance: DEFAULT_OFFSET_SCALED_LOG_VARIANCE,
        utc_offset: None,
        announce_interval_ns: ANNOUNCE_INTERVAL_NS,
        sync_interval_ns: SYNC_INTERVAL_NS,
        announce_timeout_ns: 6 * NS_PER_S,
        multicast: Ipv4Addr::new(224, 0, 1, 129),
        unicast: None,
        event_port: 319,
        general_port: 320,
    }
}

/// Announce interval for a logMessageInterval value, or None for the
/// "unspecified" 0x7f encoding. Used to qualify foreign masters.
pub fn announce_interval_ns(log: i8) -> Option<i128> {
    match log {
        -20..=-1 => Some(1_000_000_000 >> (-log as u32)),
        0..=20 => Some(1_000_000_000 << log as u32),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time::NS_PER_MS;

    const OUR_ID: ClockId = [0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc];
    const BETTER_ID: ClockId = [0x02, 0, 0, 0, 0, 0, 0, 1];
    const WORSE_ID: ClockId = [0x02, 0, 0, 0, 0, 0, 1, 0];

    fn config() -> Config {
        default_config(OUR_ID, 19)
    }

    fn foreign(gm: ClockId, mut tweak: impl FnMut(&mut Announce)) -> Message {
        let mut announce = Announce {
            origin_ns: 0,
            current_utc_offset: 0,
            priority1: DEFAULT_PRIORITY,
            clock_class: DEFAULT_CLOCK_CLASS,
            clock_accuracy: DEFAULT_CLOCK_ACCURACY,
            offset_scaled_log_variance: DEFAULT_OFFSET_SCALED_LOG_VARIANCE,
            priority2: DEFAULT_PRIORITY,
            grandmaster: gm,
            steps_removed: 0,
            time_source: TIME_SOURCE_INTERNAL_OSCILLATOR,
        };
        tweak(&mut announce);
        Message {
            domain: 19,
            source: port_identity(gm),
            seq: 0,
            flags: 0,
            correction_scaled_ns: 0,
            log_interval: 1,
            body: Body::Announce(announce),
        }
    }

    #[test]
    fn gm_cadence_flags_sequence_and_two_step_body() {
        // Virtual clock: 10 s at a 1 ms polling step, no sleeping.
        let mut gm = Gm::new(config(), 0);
        let mut announces = 0;
        let mut syncs = 0;
        let mut follow_ups = 0;
        let mut last_announce_seq = None;
        let mut last_sync_seq = None;
        let mut previous_sync: Option<Packet> = None;
        for step in 0..=10_000_i128 {
            let now = step * NS_PER_MS;
            let packets = gm.tick(now, now / 2);
            let mut saw_sync = false;
            for packet in &packets {
                match packet.message.body {
                    Body::Announce(announce) => {
                        announces += 1;
                        assert!(packet.socket == Socket::General);
                        assert_eq!(announce.steps_removed, 0);
                        assert_eq!(announce.grandmaster, OUR_ID);
                        assert_eq!(announce.priority1, DEFAULT_PRIORITY);
                        assert_eq!(announce.priority2, DEFAULT_PRIORITY);
                        assert_eq!(announce.clock_class, DEFAULT_CLOCK_CLASS);
                        assert_eq!(announce.clock_accuracy, DEFAULT_CLOCK_ACCURACY);
                        assert_eq!(announce.time_source, TIME_SOURCE_INTERNAL_OSCILLATOR);
                        assert_eq!(announce.current_utc_offset, 0);
                        assert!(packet.message.flags & PTP_TIMESCALE == 0);
                        assert_eq!(packet.message.log_interval, 1);
                        if let Some(previous) = last_announce_seq {
                            assert_eq!(packet.message.seq, previous + 1);
                        }
                        last_announce_seq = Some(packet.message.seq);
                    }
                    Body::Sync => {
                        syncs += 1;
                        saw_sync = true;
                        assert!(packet.socket == Socket::Event);
                        assert_eq!(packet.message.flags & TWO_STEP, TWO_STEP);
                        assert_eq!(packet.message.log_interval, -3);
                        if let Some(previous) = last_sync_seq {
                            assert_eq!(packet.message.seq, previous + 1);
                        }
                        last_sync_seq = Some(packet.message.seq);
                        previous_sync = Some(*packet);
                    }
                    Body::FollowUp(precise) => {
                        follow_ups += 1;
                        assert!(packet.socket == Socket::General);
                        // Follow_Up immediately (same tick) after its Sync.
                        assert!(saw_sync);
                        assert_eq!(packet.message.seq, previous_sync.unwrap().message.seq);
                        assert_eq!(precise, now / 2);
                    }
                    _ => panic!("unexpected GM message"),
                }
            }
        }
        // 0..=10 s inclusive: 6 announces (0,2,4,6,8,10 s) and 81 Syncs
        // (125 ms cadence at 0,125,...,10000 ms).
        assert_eq!(announces, 6, "2 s Announce cadence");
        assert_eq!(syncs, 81, "125 ms Sync cadence");
        assert_eq!(follow_ups, syncs, "one Follow_Up per Sync");
        assert_eq!(last_sync_seq.unwrap(), 80);
        assert_eq!(last_announce_seq.unwrap(), 5);
        assert_eq!(gm.diagnostics().announces_sent, 6);
        assert_eq!(gm.diagnostics().syncs_sent, 81);
        assert_eq!(gm.diagnostics().follow_ups_sent, 81);
        assert_eq!(gm.state(), GmState::Active);
    }

    #[test]
    fn gm_time_properties_arb_and_configured_utc() {
        let mut arb = Gm::new(config(), 0);
        let ephemeral = arb.tick(0, 0);
        let Body::Announce(announce) = ephemeral[0].message.body else {
            panic!()
        };
        assert!(ephemeral[0].message.flags & PTP_TIMESCALE == 0);
        assert_eq!(announce.current_utc_offset, 0);

        let mut utc = config();
        utc.utc_offset = Some(37);
        let mut gm = Gm::new(utc, 0);
        let packets = gm.tick(0, 1_900_000_000_000_000_000);
        let Body::Announce(announce) = packets[0].message.body else {
            panic!()
        };
        // ptpTimescale=1 + currentUtcOffsetValid=1 when configured; ARB
        // otherwise (IEEE 1588-2008 8.2.3).
        assert_eq!(
            packets[0].message.flags & (PTP_TIMESCALE | CURRENT_UTC_OFFSET_VALID),
            PTP_TIMESCALE | CURRENT_UTC_OFFSET_VALID
        );
        assert_eq!(announce.current_utc_offset, 37);
        let follow_up = packets
            .iter()
            .find(|packet| matches!(packet.message.body, Body::FollowUp(_)))
            .unwrap();
        assert_eq!(follow_up.message.flags & PTP_TIMESCALE, PTP_TIMESCALE);
    }

    #[test]
    fn domain_filter_and_delay_resp_echo() {
        let mut gm = Gm::new(config(), 0);
        let request = Message {
            domain: 19,
            source: [7; 10],
            seq: 0x1234,
            flags: 0,
            correction_scaled_ns: 0,
            log_interval: 0,
            body: Body::DelayReq(0),
        };
        let response = gm.receive(&request, Ipv4Addr::new(192, 0, 2, 9), 10, 500);
        assert_eq!(response.len(), 1);
        assert_eq!(response[0].socket, Socket::General);
        assert_eq!(
            response[0].destination,
            SocketAddrV4::new(Ipv4Addr::new(192, 0, 2, 9), 320)
        );
        assert_eq!(response[0].message.seq, 0x1234);
        assert_eq!(response[0].message.domain, 19);
        assert_eq!(
            response[0].message.body,
            Body::DelayResp {
                received_ns: 500,
                requester: [7; 10],
            }
        );
        // AES67 end-to-end delay request-response (Annex A.2.3).
        assert_eq!(gm.diagnostics().delay_responses_sent, 1);

        // Wrong domain is dropped and counted, and never answered.
        let wrong = Message {
            domain: 18,
            ..request
        };
        assert!(gm.receive(&wrong, Ipv4Addr::LOCALHOST, 10, 0).is_empty());
        assert_eq!(gm.diagnostics().wrong_domain, 1);
        // A malformed Announce (zero grandmaster) is rejected.
        assert!(
            gm.receive(&foreign([0; 8], |_| {}), Ipv4Addr::LOCALHOST, 10, 0)
                .is_empty()
        );
        assert_eq!(gm.diagnostics().invalid_announces, 1);
    }

    #[test]
    fn bmca_better_foreign_steps_aside_and_stops_announcing() {
        let mut gm = Gm::new(config(), 0);
        // A lower priority1 wins the data-set comparison.
        let better = foreign(BETTER_ID, |announce| announce.priority1 = 1);
        gm.receive(&better, Ipv4Addr::LOCALHOST, 1, 0);
        assert_eq!(gm.events().stepped_aside, Some(StepReason::BetterForeignGm));
        assert_eq!(gm.state(), GmState::Passive);
        // Stop announcing immediately (next tick is silent).
        assert!(gm.tick(1 + SYNC_INTERVAL_NS, 0).is_empty());
        assert!(gm.tick(ANNOUNCE_INTERVAL_NS, 0).is_empty());
        // ... and no Delay_Resp while passive.
        let request = Message {
            domain: 19,
            source: [7; 10],
            seq: 1,
            flags: 0,
            correction_scaled_ns: 0,
            log_interval: 0,
            body: Body::DelayReq(0),
        };
        assert!(gm.receive(&request, Ipv4Addr::LOCALHOST, 2, 0).is_empty());
    }

    #[test]
    fn bmca_equal_dataset_ties_break_by_clock_identity() {
        let mut gm = Gm::new(config(), 0);
        // Identical priority1/class/accuracy/variance/priority2: only the
        // clockIdentity differs. 0x02..01 < 0xae..fc, so it wins.
        let tie = foreign(BETTER_ID, |_| {});
        gm.receive(&tie, Ipv4Addr::LOCALHOST, 1, 0);
        assert_eq!(gm.state(), GmState::Passive);
        assert_eq!(gm.events().stepped_aside, Some(StepReason::BetterForeignGm));

        // The opposite tie (a numerically larger identity) loses.
        let mut gm = Gm::new(config(), 0);
        let larger = foreign([0xff; 8], |_| {});
        gm.receive(&larger, Ipv4Addr::LOCALHOST, 1, 0);
        assert_eq!(gm.state(), GmState::Active);
        assert!(gm.events().foreign_worse);
    }

    #[test]
    fn dual_gm_watchdog_yields_after_two_announce_intervals() {
        let mut gm = Gm::new(config(), 0);
        let worse = foreign(WORSE_ID, |announce| announce.priority1 = 250);
        gm.receive(&worse, Ipv4Addr::LOCALHOST, 0, 0);
        assert!(gm.events().foreign_worse);
        assert_eq!(
            gm.state(),
            GmState::Active,
            "we win the data set comparison"
        );
        // Still announcing while the worse GM keeps announcing: after two
        // announce intervals the watchdog yields (never two GMs).
        gm.receive(&worse, Ipv4Addr::LOCALHOST, ANNOUNCE_INTERVAL_NS, 0);
        assert_eq!(gm.state(), GmState::Active);
        let _ = gm.tick(2 * ANNOUNCE_INTERVAL_NS, 0);
        assert_eq!(gm.events().stepped_aside, Some(StepReason::DualGm));
        assert!(gm.events().dual_gm);
        assert_eq!(gm.diagnostics().dual_gm, 1);
        assert!(
            gm.tick(2 * ANNOUNCE_INTERVAL_NS + SYNC_INTERVAL_NS, 0)
                .is_empty()
        );
        assert_eq!(gm.state(), GmState::Passive);
    }

    #[test]
    fn passive_gm_reclaims_after_the_foreign_master_times_out() {
        let mut gm = Gm::new(config(), 0);
        let better = foreign(BETTER_ID, |announce| announce.priority1 = 1);
        gm.receive(&better, Ipv4Addr::LOCALHOST, 0, 0);
        assert_eq!(gm.state(), GmState::Passive);
        // The foreign GM stops announcing; after announceReceiptTimeout we
        // reclaim and start announcing again from scratch.
        let reclaim_at = 6 * crate::time::NS_PER_S + 1;
        assert!(gm.tick(reclaim_at - 2, 0).is_empty());
        let packets = gm.tick(reclaim_at, 0);
        assert!(gm.events().reclaimed);
        assert_eq!(gm.state(), GmState::Active);
        assert!(
            packets
                .iter()
                .any(|packet| matches!(packet.message.body, Body::Announce(_)))
        );
        assert!(
            packets
                .iter()
                .any(|packet| matches!(packet.message.body, Body::Sync))
        );
    }

    #[test]
    fn periodic_deadlines_do_not_burst_after_a_stall() {
        let mut gm = Gm::new(config(), 0);
        // A 30 s stall: one announce and one sync pair, not a burst of 15.
        let packets = gm.tick(30 * crate::time::NS_PER_S, 0);
        assert_eq!(packets.len(), 3);
        assert!(matches!(packets[0].message.body, Body::Announce(_)));
        assert!(matches!(packets[1].message.body, Body::Sync));
        assert!(matches!(packets[2].message.body, Body::FollowUp(_)));
        assert_eq!(gm.tick(30 * crate::time::NS_PER_S + NS_PER_MS, 0).len(), 0);
    }

    #[test]
    fn dataset_order_matches_ieee_1588_bmca() {
        let base = Dataset {
            priority1: 128,
            clock_class: 248,
            clock_accuracy: 0xfe,
            offset_scaled_log_variance: 0xffff,
            priority2: 128,
            clock_identity: [0x80; 8],
        };
        assert_eq!(base.compare(&base), Ordering::Equal);
        assert!(
            Dataset {
                priority1: 127,
                ..base
            }
            .is_better_than(&base)
        );
        assert!(
            Dataset {
                clock_class: 6,
                ..base
            }
            .is_better_than(&base)
        );
        assert!(
            Dataset {
                clock_accuracy: 0x20,
                ..base
            }
            .is_better_than(&base)
        );
        assert!(
            Dataset {
                offset_scaled_log_variance: 0,
                ..base
            }
            .is_better_than(&base)
        );
        assert!(
            Dataset {
                priority2: 127,
                ..base
            }
            .is_better_than(&base)
        );
        assert!(
            Dataset {
                clock_identity: [0x7f; 8],
                ..base
            }
            .is_better_than(&base)
        );
    }
}
