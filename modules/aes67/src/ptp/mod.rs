//! PTP engine for the sender: the slave follower state machine (E2E delay
//! request-response, clock fit, holdover support) plus the topology-mode
//! runtime that owns the role state machine and the grandmaster engine.
//!
//! Spec citations: AES67-2023 Annex A Table A.1 (domain 0..127, logAnnounce
//! 1, logSync -3, logMinDelayReq 0, announceReceiptTimeout 3), A.2.3 (E2E
//! default, BMCA = IEEE 1588-2008 9.3.2), Annex B Table B.1 (224.0.1.129,
//! UDP 319/320), section 6.2 Table 1 (clock traffic DSCP EF 46). The full
//! IEEE 1588-2008 text is not in the workspace; message layouts and flag
//! bits are cross-checked against the local linuxptp msg.h/msg.c mirror and
//! live board captures. The BMCA
//! comparison order is IEEE 1588-2008 9.3.2, which is UNVERIFIED-LOCAL.
pub mod codec;
pub mod gm;
mod identity;
pub mod role;

use crate::{
    net::{TOS_EF, mark_dscp, multicast_interface},
    time::{ClockFit, FitResult, MediaClock, NS_PER_S, TimestampMode, TimestampSource},
};
use codec::{Body, ClockId, DecodeError, Message, PortId, UNICAST};
use gm::Gm;
pub use role::{PtpMode, Role, Source};
use std::{
    collections::VecDeque,
    hash::{BuildHasher, RandomState},
    io,
    net::{Ipv4Addr, SocketAddr, SocketAddrV4, UdpSocket},
    time::{SystemTime, UNIX_EPOCH},
};

pub use identity::{eui64_from_mac, interface_clock_identity, interface_mac, is_eui64_from_mac};

const REQUEST_INTERVAL: i128 = 125_000_000;
const EXCHANGE_MAX_AGE: i128 = 2_000_000_000;
const MAX_ROUND_TRIP: i128 = 40_000_000;
const SYNC_HISTORY: usize = 16;
const ANNOUNCE_MISSES: i128 = 5;
/// AES67 Annex A fixes the announce interval at 2 s (logMessageInterval 1) and
/// IEEE 1588 deployments stay within -3..=3. A malformed log interval (up to
/// 2^20 s) must not stretch the foreign-master timeout into days, so the
/// accepted cadence is clamped instead of rejected: Sync/Follow_Up following
/// still works for an unusual but reachable master.
const MIN_ANNOUNCE_INTERVAL_NS: i128 = 125_000_000;
const MAX_ANNOUNCE_INTERVAL_NS: i128 = 8_000_000_000;
const DELAY_RESPONSE_TIMEOUT: i128 = 1_000_000_000;

/// PTP multicast TTL. TTL is an IEEE 1588-2008 Annex D/E profile choice and a
/// deployment decision, not an RFC rule: RFC 1112 sec 6.1 only says an
/// unspecified multicast TTL should default to 1, and RFC 5771 sec 5 places
/// 224.0.1.129 in the Internetwork Control Block (224.0.1.0/24), not the
/// Local Network Control Block. 16 matches the validated board GM profile
/// (tspi-ptp-rx rt-stack/ptp/etc/ptp4l-master-udp.conf.example udp_ttl 16;
/// 2026-11-22 capture DSCP46/TTL16), so a sender and that GM look identical
/// on the wire. EXTENSION / deployment choice.
pub const PTP_MULTICAST_TTL: u32 = 16;

fn announce_interval(log: i8) -> Option<i128> {
    // 0x7f denotes an unspecified interval, which cannot establish cadence.
    match log {
        -20..=-1 => Some(1_000_000_000 >> (-log as u32)),
        0..=20 => Some(1_000_000_000 << log as u32),
        _ => None,
    }
}

/// Traceable clock classes of IEEE 1588-2008 Table 5: primary-reference and
/// previously-synchronized classes. time_traceable is
/// the TIME_TRACEABLE flag (or a traceable clockClass), never ptpTimescale;
/// the board GM proves the two differ (ptpTimescale=1, timeTraceable=0).
fn time_traceable(message: &Message) -> bool {
    if message.flags & codec::TIME_TRACEABLE != 0 {
        return true;
    }
    match message.body {
        Body::Announce(announce) => matches!(announce.clock_class, 6 | 7 | 166 | 187),
        _ => false,
    }
}

fn unix_now_ns() -> i128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as i128
}

#[derive(Clone, Debug)]
pub struct Config {
    pub domain: u8,
    pub pinned_gm: Option<ClockId>,
    pub interface: Ipv4Addr,
    pub multicast: Ipv4Addr,
    pub unicast_master: Option<Ipv4Addr>,
    pub event_port: u16,
    pub general_port: u16,
    pub local_event_port: u16,
    pub local_general_port: u16,
    /// Topology mode.
    pub mode: PtpMode,
    pub priority1: u8,
    pub priority2: u8,
    /// Announce a GM in auto mode when no external GM appears.
    pub gm_capable: bool,
    /// AUTO listening window and foreign-master timeout (AES67 Annex A
    /// Table A.1 announceReceiptTimeout default 3 x 2 s = 6 s).
    pub announce_timeout_ns: i128,
    /// IEEE 1588-2008 8.2.3 timePropertiesDS currentUtcOffset in seconds; when
    /// set the GM announces ptpTimescale=1 and currentUtcOffsetValid=1
    /// (field text absent locally; dataset semantics follow IEEE 1588-2008 8.2.3).
    pub gm_utc_offset: Option<i16>,
    /// Test/embedding explicit clock identity. Production GM mode leaves this
    /// None and derives the EUI-64 identity from the interface MAC; it never
    /// invents a random identity.
    pub gm_identity: Option<ClockId>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            domain: 0,
            pinned_gm: None,
            interface: Ipv4Addr::UNSPECIFIED,
            multicast: Ipv4Addr::new(224, 0, 1, 129),
            unicast_master: None,
            event_port: 319,
            general_port: 320,
            local_event_port: 0,
            local_general_port: 0,
            mode: PtpMode::Slave,
            priority1: gm::DEFAULT_PRIORITY,
            priority2: gm::DEFAULT_PRIORITY,
            gm_capable: false,
            announce_timeout_ns: 6 * NS_PER_S,
            gm_utc_offset: None,
            gm_identity: None,
        }
    }
}

#[derive(Debug, Default)]
struct PacketCounts {
    received: u64,
    matched: u64,
    rejected: u64,
}

#[derive(Debug, Default)]
pub struct Diagnostics {
    announce: PacketCounts,
    sync: PacketCounts,
    follow_up: PacketCounts,
    delay_req: PacketCounts,
    delay_resp: PacketCounts,
    delay_req_sent: u64,
    delay_req_icmp_unreachable: u64,
    delay_resp_timeout: u64,
    last_delay_reject: Option<&'static str>,
    last_fit_reject: Option<FitResult>,
    last_lost_reason: Option<&'static str>,
    announce_interval_ns: i128,
}

/// IEEE 1588-2008 9.3.4 dataset comparison key. These senders are ordinary
/// clocks (no boundary clock between them), so stepsRemoved is always zero and
/// the order is priority1, clockClass, clockAccuracy,
/// offsetScaledLogVariance, priority2, grandmasterIdentity. A lower value is a
/// better master.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Dataset {
    priority1: u8,
    clock_class: u8,
    clock_accuracy: u8,
    variance: u16,
    priority2: u8,
    gm: ClockId,
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Master {
    gm: ClockId,
    port: PortId,
    ip: Ipv4Addr,
    /// TIME_TRACEABLE flag / traceable clockClass of the accepted announce.
    time_traceable: bool,
    /// PTP_TIMESCALE flag of the accepted announce: PTP (TAI) timescale in use.
    ptp_timescale: bool,
    dataset: Dataset,
}

struct SyncPair {
    local_ns: Option<i128>,
    remote_ns: Option<i128>,
    correction_scaled: i128,
    arrival: i128,
}
#[derive(Clone, Copy)]
struct SyncSample {
    local_ns: i128,
    remote_scaled_ns: i128,
}
struct Delay {
    seq: u16,
    issued_ns: i128,
    sent_ns: Option<i128>,
    received_scaled_ns: Option<i128>,
    sync: Option<SyncSample>,
}

/// The slave follower state machine. Socket I/O lives in [PtpRuntime], so the
/// follower can share one socket pair with the grandmaster engine.
pub(crate) struct FollowerState {
    domain: u8,
    pin: Option<ClockId>,
    mode: TimestampMode,
    diagnostics: Diagnostics,
    port: PortId,
    master: Option<Master>,
    /// Foreign-master timeout budget from the configured announce timeout;
    /// expire() takes max(this, 5 x clamped cadence).
    announce_timeout_ns: i128,
    announce_at: i128,
    last_good: Option<i128>,
    sync_pairs: VecDeque<(u16, SyncPair)>,
    latest_sync: Option<SyncSample>,
    pending: Option<Delay>,
    next_seq: u16,
    next_req: i128,
    clock: MediaClock,
}

impl FollowerState {
    fn new(config: &Config, port: PortId, mode: TimestampMode) -> Self {
        Self {
            domain: config.domain,
            pin: config.pinned_gm,
            mode,
            diagnostics: Diagnostics::default(),
            port,
            master: None,
            announce_timeout_ns: config.announce_timeout_ns,
            announce_at: 0,
            last_good: None,
            sync_pairs: VecDeque::with_capacity(SYNC_HISTORY),
            latest_sync: None,
            pending: None,
            next_seq: 0,
            next_req: 0,
            clock: MediaClock::default(),
        }
    }

    fn clear(&mut self) {
        self.master = None;
        self.last_good = None;
        self.sync_pairs.clear();
        self.latest_sync = None;
        self.pending = None;
        self.clock.clear();
        self.next_req = 0;
    }

    /// Pin the follower to one GM identity (None accepts any eligible source).
    /// Used when a sender in gm/passive mode follows the winning foreign GM.
    fn set_pin(&mut self, pin: Option<ClockId>) {
        if self.pin != pin {
            self.pin = pin;
            if self
                .master
                .is_some_and(|master| pin.is_some_and(|pin| pin != master.gm))
            {
                self.clear();
            }
        }
    }

    fn expire(&mut self, now: i128) {
        // The configured announce timeout is the floor: a sender advertising a
        // slow cadence may stretch it, but never to the days a hostile
        // logMessageInterval of 20 would imply (clamped at accept time).
        let budget = self
            .announce_timeout_ns
            .max(self.diagnostics.announce_interval_ns * ANNOUNCE_MISSES);
        if self.master.is_some() && now - self.announce_at >= budget {
            if self.clock.fit().is_some() {
                self.diagnostics.last_lost_reason = Some("announce_timeout");
            }
            self.clear();
        } else if self.last_good.is_some_and(|last| now - last >= budget) {
            self.diagnostics.last_lost_reason = Some("clock_fit_stale");
            self.last_good = None;
            self.clock.clear();
        }
        if self
            .pending
            .as_ref()
            .is_some_and(|delay| now - delay.issued_ns >= DELAY_RESPONSE_TIMEOUT)
        {
            self.diagnostics.delay_resp_timeout += 1;
            self.diagnostics.last_delay_reject = Some("response_timeout");
            self.pending = None;
        }
    }

    fn master_identity(&self) -> Option<ClockId> {
        self.master.map(|master| master.gm)
    }

    /// Only used by the follower unit tests: the reported status takes
    /// traceability from the accepted Announce via the role machine.
    #[cfg(test)]
    fn time_traceable(&self) -> bool {
        self.master.is_some_and(|master| master.time_traceable)
    }

    fn ptp_timescale(&self) -> bool {
        self.master.is_some_and(|master| master.ptp_timescale)
    }

    fn locked(&self) -> Option<(ClockId, ClockFit)> {
        Some((self.master?.gm, self.clock.fit()?))
    }

    fn receive(&mut self, msg: Message, ip: Ipv4Addr, now: i128, event: bool) {
        let result = self.accept(msg, ip, now, event);
        let counts = match msg.body {
            Body::Announce(_) => &mut self.diagnostics.announce,
            Body::Sync => &mut self.diagnostics.sync,
            Body::FollowUp(_) => &mut self.diagnostics.follow_up,
            Body::DelayReq(_) => &mut self.diagnostics.delay_req,
            Body::DelayResp { .. } => &mut self.diagnostics.delay_resp,
        };
        counts.received += 1;
        match result {
            Ok(()) => counts.matched += 1,
            Err(reason) => {
                counts.rejected += 1;
                if matches!(msg.body, Body::DelayResp { .. }) {
                    self.diagnostics.last_delay_reject = Some(reason);
                }
            }
        }
    }

    fn accept(
        &mut self,
        msg: Message,
        ip: Ipv4Addr,
        now: i128,
        event: bool,
    ) -> Result<(), &'static str> {
        if msg.domain != self.domain || msg.source == self.port {
            return Err("wrong_domain_or_self");
        }
        if let Body::Announce(announce) = msg.body {
            // EXTENSION: ptpTimescale=0 (ARB) is
            // accepted for frequency-only locking; the traceability flag is
            // tracked separately. Malformed bodies are still rejected.
            if event || announce.validate().is_err() || announce.clock_class == 255 {
                return Err("invalid_announce");
            }
            if self.pin.is_some_and(|pin| pin != announce.grandmaster) {
                return Err("wrong_grandmaster");
            }
            // Unusual but reachable cadences are clamped, not rejected: the
            // master would otherwise get a timeout measured in days.
            let cadence = announce_interval(msg.log_interval)
                .ok_or("invalid_announce_interval")?
                .clamp(MIN_ANNOUNCE_INTERVAL_NS, MAX_ANNOUNCE_INTERVAL_NS);
            let master = Master {
                gm: announce.grandmaster,
                port: msg.source,
                ip,
                time_traceable: time_traceable(&msg),
                ptp_timescale: msg.flags & codec::PTP_TIMESCALE != 0,
                dataset: Dataset {
                    priority1: announce.priority1,
                    clock_class: announce.clock_class,
                    clock_accuracy: announce.clock_accuracy,
                    variance: announce.offset_scaled_log_variance,
                    priority2: announce.priority2,
                    gm: announce.grandmaster,
                },
            };
            if let Some(existing) = self.master {
                if existing.port != master.port || existing.ip != master.ip {
                    // BMCA-lite: the first accepted announce no longer pins the
                    // follower forever. A strictly better foreign dataset takes
                    // over; anything else is rejected as before.
                    if master.dataset >= existing.dataset {
                        return Err("wrong_master");
                    }
                    if self.clock.fit().is_some() {
                        self.diagnostics.last_lost_reason = Some("better_master");
                    }
                    self.clear();
                } else if existing != master {
                    if self.clock.fit().is_some() {
                        self.diagnostics.last_lost_reason = Some("grandmaster_changed");
                    }
                    self.clear();
                }
            }
            self.master = Some(master);
            self.announce_at = now;
            self.diagnostics.announce_interval_ns = cadence;
            return Ok(());
        }
        if !self
            .master
            .is_some_and(|master| master.port == msg.source && master.ip == ip)
        {
            return Err("wrong_master");
        }
        match msg.body {
            // AES67 media-clock recovery needs the two-step originTimestamp.
            // Keep this reason distinct from unrelated unsupported traffic.
            Body::Sync if event && msg.flags & codec::TWO_STEP == 0 => {
                Err("one_step_sync_unsupported")
            }
            Body::Sync if event => self.sync(msg, now, Some(now), None),
            Body::FollowUp(stamp) if !event => self.sync(msg, now, None, Some(stamp)),
            Body::DelayResp {
                received_ns,
                requester,
            } if !event => {
                if requester != self.port {
                    return Err("wrong_requesting_port_identity");
                }
                let delay = self.pending.as_mut().ok_or("no_pending_delay_req")?;
                if delay.seq != msg.seq {
                    return Err("wrong_sequence_id");
                }
                if now - delay.issued_ns >= DELAY_RESPONSE_TIMEOUT {
                    return Err("late_response");
                }
                if delay.received_scaled_ns.is_some() {
                    return Err("duplicate_response");
                }
                delay.received_scaled_ns =
                    Some((received_ns << 16) - i128::from(msg.correction_scaled_ns));
                Ok(())
            }
            _ => Err("unsupported_message_or_socket"),
        }
    }

    fn sync(
        &mut self,
        msg: Message,
        now: i128,
        local: Option<i128>,
        remote: Option<i128>,
    ) -> Result<(), &'static str> {
        self.sync_pairs
            .retain(|(_, pair)| now - pair.arrival <= EXCHANGE_MAX_AGE);
        let idx = match self.sync_pairs.iter().position(|(seq, _)| *seq == msg.seq) {
            Some(idx) => idx,
            None => {
                if self.sync_pairs.len() == SYNC_HISTORY {
                    self.sync_pairs.pop_front();
                }
                self.sync_pairs.push_back((
                    msg.seq,
                    SyncPair {
                        local_ns: None,
                        remote_ns: None,
                        correction_scaled: 0,
                        arrival: now,
                    },
                ));
                self.sync_pairs.len() - 1
            }
        };
        let (_, pair) = &mut self.sync_pairs[idx];
        if (local.is_some() && pair.local_ns.is_some())
            || (remote.is_some() && pair.remote_ns.is_some())
        {
            return Err("duplicate_sync_component");
        }
        pair.local_ns = pair.local_ns.or(local);
        pair.remote_ns = pair.remote_ns.or(remote);
        pair.correction_scaled += i128::from(msg.correction_scaled_ns);
        if let (Some(local_ns), Some(remote_ns)) = (pair.local_ns, pair.remote_ns) {
            if self.latest_sync.is_none_or(|sync| local_ns > sync.local_ns) {
                self.latest_sync = Some(SyncSample {
                    local_ns,
                    remote_scaled_ns: (remote_ns << 16) + pair.correction_scaled,
                });
            }
            self.sync_pairs.remove(idx);
        }
        Ok(())
    }

    fn request(&mut self, now: i128, unicast: bool) -> Option<Message> {
        self.master?;
        let sync = self
            .latest_sync
            .filter(|sync| now - sync.local_ns <= EXCHANGE_MAX_AGE)?;
        if now < self.next_req || self.pending.is_some() {
            return None;
        }
        self.next_req = now + REQUEST_INTERVAL;
        let seq = self.next_seq;
        self.next_seq = seq.wrapping_add(1);
        self.pending = Some(Delay {
            seq,
            issued_ns: now,
            sent_ns: None,
            received_scaled_ns: None,
            sync: Some(sync),
        });
        self.latest_sync = None;
        Some(Message {
            domain: self.domain,
            source: self.port,
            seq,
            flags: if unicast { UNICAST } else { 0 },
            correction_scaled_ns: 0,
            log_interval: 0x7f,
            body: Body::DelayReq(0),
        })
    }

    fn finish_exchange(&mut self, now: i128) {
        let Some(delay) = &self.pending else {
            return;
        };
        let (Some(sent), Some(received), Some(sync)) =
            (delay.sent_ns, delay.received_scaled_ns, delay.sync)
        else {
            return;
        };
        self.pending = None;
        // Allow for rate error between the forward and reverse measurements. Once
        // locked, use the previous fit to reject implausible path delays more tightly.
        let elapsed = sent - sync.local_ns;
        let inferred = received - sync.remote_scaled_ns;
        let expected = elapsed
            + (elapsed as f64 * self.clock.fit().map_or(0.0, |fit| fit.rate_ppm) / 1e6) as i128;
        let round_trip = (inferred >> 16) - expected;
        if !(-1_000_000..=MAX_ROUND_TRIP).contains(&round_trip) {
            self.diagnostics.last_delay_reject = Some("invalid_round_trip");
            return;
        }
        let local = (sent + sync.local_ns) / 2;
        let remote = (received + sync.remote_scaled_ns) >> 17;
        match self.clock.observe(local, remote, self.mode) {
            FitResult::Accepted => self.last_good = Some(now),
            FitResult::WarmingUp => (),
            result => self.diagnostics.last_fit_reject = Some(result),
        }
    }
}

/// Where the media clock comes from: the PTP epoch of a GM (our own or an
/// external one) or the sender-local reference epoch of mode local.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MediaSource {
    Ptp(ClockId),
    Local,
}

/// Reported role/source snapshot. The counters ABI
/// struct is size-stable, so this is surfaced through the extra
/// nsp_aes67_ptp_status export and the diagnostics log line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PtpStatus {
    pub mode: PtpMode,
    pub role: Role,
    pub source: Source,
    pub gm: Option<ClockId>,
    pub domain: u8,
    pub time_traceable: bool,
    pub ptp_timescale: bool,
    pub reason: &'static str,
    pub transitions: u64,
}

#[derive(Debug)]
pub enum OpenError {
    Io(io::Error),
    /// GM mode was requested but the interface MAC could not be read, so no
    /// EUI-64 clock identity exists. Refuse instead of inventing one.
    GmIdentityUnavailable,
}
impl From<io::Error> for OpenError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

/// One UDP socket pair shared by the follower and the GM engine. The event
/// socket (319) carries Sync and Delay_Req, the general socket (320) carries
/// Announce, Follow_Up and Delay_Resp (IEEE 1588-2008 Annex D/E; AES67
/// Annex A line 2029 and Annex B Table B.1).
struct Sockets {
    event: UdpSocket,
    general: UdpSocket,
}

impl Sockets {
    fn open(config: &Config) -> io::Result<Self> {
        let open = |configured: u16, remote: u16| -> io::Result<UdpSocket> {
            let (address, port) = if config.unicast_master.is_some() {
                (config.interface, configured)
            } else {
                (
                    Ipv4Addr::UNSPECIFIED,
                    if configured != 0 { configured } else { remote },
                )
            };
            let socket = UdpSocket::bind((address, port))?;
            socket.set_nonblocking(true)?;
            if config.unicast_master.is_none() {
                socket.join_multicast_v4(&config.multicast, &config.interface)?;
                multicast_interface(&socket, config.interface)?;
                socket.set_multicast_loop_v4(false)?;
            }
            // AES67-2023 sec 6.2 Table 1: clock traffic EF (46). The TTL is a
            // deployment choice, not an RFC rule; see PTP_MULTICAST_TTL.
            socket.set_multicast_ttl_v4(PTP_MULTICAST_TTL)?;
            mark_dscp(&socket, TOS_EF);
            Ok(socket)
        };
        Ok(Self {
            event: open(config.local_event_port, config.event_port)?,
            general: open(config.local_general_port, config.general_port)?,
        })
    }
}

/// The PTP runtime: sockets, follower state, role state machine and (when
/// applicable) the GM engine. All timing is read from the injected
/// [TimestampSource], so tests use a deterministic virtual clock.
pub struct PtpRuntime {
    config: Config,
    source: Box<dyn TimestampSource>,
    sockets: Option<Sockets>,
    follower: FollowerState,
    gm: Option<Gm>,
    role: role::RoleMachine,
    identity: Option<ClockId>,
    mode: PtpMode,
    /// Anchor (monotonic now, media time) while we are the GM. With
    /// ptp_gm_utc_offset the media base is SystemTime(UTC)+offset at
    /// activation; without it GM time is an ARB epoch anchored at activation.
    gm_base: Option<(i128, i128)>,
    /// Anchor (monotonic now, media time) while the role is local.
    local_base: Option<(i128, i128)>,
    media: Option<(MediaSource, ClockFit)>,
    previous_role: Role,
    pinned_winner: Option<ClockId>,
    loss_expired: bool,
    lock_expired: bool,
    invalid_packets: u64,
    last_decode_error: Option<DecodeError>,
}

impl PtpRuntime {
    pub fn open(config: Config, source: Box<dyn TimestampSource>) -> Result<Self, OpenError> {
        // Only modes that can announce a GM need the clock identity; reading
        // the MAC is a side effect, so pure follower/local modes skip it.
        let identity = if config.mode == PtpMode::Gm || config.gm_capable {
            config
                .gm_identity
                .or_else(|| interface_clock_identity(config.interface))
        } else {
            None
        };
        let identity_unavailable = config.mode == PtpMode::Gm && identity.is_none();
        if identity_unavailable {
            return Err(OpenError::GmIdentityUnavailable);
        }
        let sockets = match config.mode {
            PtpMode::Local => None,
            _ => Some(Sockets::open(&config)?),
        };
        let follower_port = sockets
            .as_ref()
            .and_then(|sockets| sockets.general.local_addr().ok())
            .map_or([0x02, 0, 0, 0, 0, 0, 0, 0, 0, 1], |address| {
                let mut clock_id = RandomState::new().hash_one(address).to_be_bytes();
                clock_id[0] = (clock_id[0] | 2) & !1;
                let mut port = [0; 10];
                port[..8].copy_from_slice(&clock_id);
                port[9] = 1;
                port
            });
        // The only reachable timestamp source on this Windows sender is QPC
        // software timestamping (hardware_source() always reports unavailable),
        // so the follower's fit gate mirrors the actual source. When a hardware
        // path lands, thread the negotiated mode through Config instead.
        let set = TimestampMode::Software;
        let mode = config.mode;
        let gm_capable = match mode {
            PtpMode::Gm => true,
            _ => config.gm_capable && identity.is_some(),
        };
        let role = role::RoleMachine::new(
            role::Config {
                mode,
                gm_capable,
                identity,
                // Our GM does not set the TIME_TRACEABLE flag (its own time is
                // a Windows software clock, not a disciplined reference), so a
                // slave following us reports time_traceable=false. Honest gap.
                gm_time_traceable: false,
                announce_timeout_ns: config.announce_timeout_ns,
                lock_timeout_ns: 0,
            },
            source.now_ns(),
        );
        let mut runtime = Self {
            follower: FollowerState::new(&config, follower_port, set),
            config,
            source,
            sockets,
            gm: None,
            role,
            identity,
            mode,
            gm_base: None,
            local_base: None,
            media: None,
            previous_role: Role::Init,
            pinned_winner: None,
            loss_expired: false,
            lock_expired: false,
            invalid_packets: 0,
            last_decode_error: None,
        };
        let now = runtime.source.now_ns();
        runtime.apply_role(now);
        Ok(runtime)
    }

    pub fn now_ns(&self) -> i128 {
        self.source.now_ns()
    }

    pub fn mode(&self) -> PtpMode {
        self.mode
    }

    pub fn status(&self) -> PtpStatus {
        let status = self.role.status();
        // Report the master the follower is actually locked to, not merely the
        // last Announce that arrived (the role machine tracks the winning
        // foreign GM for Passive). Falls back to the role snapshot while the fit
        // is still being acquired.
        let gm = match status.role {
            Role::Slave | Role::Passive => self.follower.master_identity().or(status.gm),
            _ => status.gm,
        };
        PtpStatus {
            mode: self.mode,
            role: status.role,
            source: status.source,
            gm,
            domain: self.config.domain,
            time_traceable: status.time_traceable,
            ptp_timescale: self.ptp_timescale(),
            reason: status.reason,
            transitions: self.role.transitions(),
        }
    }

    /// Media-clock mapping for the pacing loop: Some when RTP may flow.
    pub fn media_clock(&self) -> Option<(MediaSource, ClockFit)> {
        self.media
    }

    pub fn follower_diagnostics(&self) -> &Diagnostics {
        &self.follower.diagnostics
    }

    pub fn gm_diagnostics(&self) -> Option<gm::Diagnostics> {
        self.gm.as_ref().map(|gm| *gm.diagnostics())
    }

    /// True when the configuration asked for a GM but the interface MAC could
    /// not provide an EUI-64 identity (auto downgrades to local; gm refuses at
    /// open).
    /// PTP (TAI) timescale currently in use: PTP_TIMESCALE of the accepted
    /// announce while following, gm_utc_offset configured while we are the GM.
    /// Reported separately from time_traceable.
    pub fn ptp_timescale(&self) -> bool {
        match self.role.role() {
            Role::Slave | Role::Passive => self.follower.ptp_timescale(),
            Role::Gm => self.config.gm_utc_offset.is_some(),
            _ => false,
        }
    }

    pub fn identity_unavailable(&self) -> bool {
        self.identity.is_none()
    }

    pub fn invalid_packets(&self) -> u64 {
        self.invalid_packets
    }

    pub fn last_decode_error(&self) -> Option<DecodeError> {
        self.last_decode_error
    }

    /// Session feedback: the holdover deadline elapsed with no fit. In auto
    /// this drives the external->self / external->local fallback.
    pub fn note_loss_expired(&mut self, expired: bool) {
        self.loss_expired = expired;
    }

    /// Session feedback: the startup lock deadline elapsed with no fit.
    pub fn note_lock_expired(&mut self, expired: bool) {
        self.lock_expired = expired;
    }

    /// Local bound ports, for loopback tests and diagnostics.
    pub fn local_event_port(&self) -> Option<u16> {
        self.sockets
            .as_ref()
            .and_then(|sockets| sockets.event.local_addr().ok())
            .map(|address| address.port())
    }

    pub fn local_general_port(&self) -> Option<u16> {
        self.sockets
            .as_ref()
            .and_then(|sockets| sockets.general.local_addr().ok())
            .map(|address| address.port())
    }

    pub fn tick(&mut self) -> io::Result<()> {
        let mut announce: Option<role::AnnounceSeen> = None;
        if let Some(sockets) = self.sockets.take() {
            let result = self
                .receive_batch(&sockets, &mut announce)
                .and_then(|()| self.pump_gm(&sockets))
                .and_then(|()| self.pump_follower(&sockets));
            self.sockets = Some(sockets);
            result?;
        } else {
            self.follower.expire(self.source.now_ns());
        }
        self.step_role(announce);
        Ok(())
    }

    /// Drain both sockets (bounded per socket so a flood cannot postpone RTP)
    /// and decode what arrived.
    fn receive_batch(
        &mut self,
        sockets: &Sockets,
        announce: &mut Option<role::AnnounceSeen>,
    ) -> io::Result<()> {
        let mut buf = [0u8; 1536];
        for event in [true, false] {
            let socket = if event {
                &sockets.event
            } else {
                &sockets.general
            };
            for _ in 0..16 {
                match self.source.recv(socket, &mut buf) {
                    Ok((size, SocketAddr::V4(peer), now)) => {
                        let ip = *peer.ip();
                        if self
                            .config
                            .unicast_master
                            .is_some_and(|master| ip != master)
                        {
                            continue;
                        }
                        match codec::decode(&buf[..size]) {
                            Ok(message) => {
                                self.on_message(sockets, message, ip, now, event, announce)?
                            }
                            Err(error) => {
                                self.invalid_packets += 1;
                                self.last_decode_error = Some(error);
                            }
                        }
                    }
                    Ok(_) => (),
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    // Winsock reports an ICMP port unreachable on a subsequent
                    // UDP receive; the qualified timers own loss.
                    Err(error) if cfg!(windows) && error.raw_os_error() == Some(10054) => {
                        self.follower.diagnostics.delay_req_icmp_unreachable += 1;
                        continue;
                    }
                    Err(error) => return Err(error),
                }
            }
        }
        Ok(())
    }

    /// Pump the GM engine when it is our turn to announce.
    fn pump_gm(&mut self, sockets: &Sockets) -> io::Result<()> {
        if !matches!(self.role.role(), Role::Gm | Role::Passive) {
            return Ok(());
        }
        let now = self.source.now_ns();
        let gm_now = self.gm_now(now).unwrap_or(0);
        let mut outgoing = Vec::new();
        if let Some(gm) = self.gm.as_mut() {
            outgoing = gm.tick(now, gm_now);
        }
        self.send_packets(sockets, outgoing)
    }

    /// Follower housekeeping: finish timed exchanges, expire timers, and send
    /// Delay_Req when we are following a master (never while we are the GM).
    fn pump_follower(&mut self, sockets: &Sockets) -> io::Result<()> {
        let now = self.source.now_ns();
        let role = self.role.role();
        let following = matches!(role, Role::Slave | Role::Passive);
        if following
            && self
                .follower
                .pending
                .as_ref()
                .is_some_and(|delay| delay.sent_ns.is_none())
        {
            self.follower.pending.as_mut().unwrap().sent_ns = self.source.tx_timestamp()?;
        }
        if following {
            self.follower.finish_exchange(now);
        }
        self.follower.expire(now);
        if !following {
            return Ok(());
        }
        let Some(message) = self
            .follower
            .request(now, self.config.unicast_master.is_some())
        else {
            return Ok(());
        };
        let destination = SocketAddrV4::new(
            self.config.unicast_master.unwrap_or(self.config.multicast),
            self.config.event_port,
        );
        let mut bytes = [0u8; 64];
        let wire = codec::encode(message, &mut bytes);
        match self.source.send(&sockets.event, wire, destination) {
            Ok(sent) => {
                self.follower.diagnostics.delay_req_sent += 1;
                if let Some(pending) = self.follower.pending.as_mut() {
                    pending.sent_ns = sent;
                }
                Ok(())
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                self.follower.pending = None;
                Ok(())
            }
            Err(error) => Err(error),
        }
    }

    fn on_message(
        &mut self,
        sockets: &Sockets,
        message: Message,
        ip: Ipv4Addr,
        now: i128,
        event: bool,
        announce: &mut Option<role::AnnounceSeen>,
    ) -> io::Result<()> {
        if let Body::Announce(ref body) = message.body {
            if message.domain == self.config.domain
                && body.validate().is_ok()
                && body.clock_class != 255
                && Some(body.grandmaster) != self.identity
            {
                *announce = Some(role::AnnounceSeen {
                    gm: body.grandmaster,
                    dataset: gm::Dataset::from_announce(body),
                    time_traceable: time_traceable(&message),
                });
            }
        }
        match self.role.role() {
            Role::Gm | Role::Passive => {
                let gm_now = self.gm_now(now).unwrap_or(0);
                let mut outgoing = Vec::new();
                if let Some(mut gm) = self.gm.take() {
                    outgoing = gm.receive(&message, ip, now, gm_now);
                    self.gm = Some(gm);
                }
                self.send_packets(sockets, outgoing)?;
                if self.role.role() == Role::Passive {
                    self.follower.receive(message, ip, now, event);
                }
                Ok(())
            }
            _ => {
                self.follower.receive(message, ip, now, event);
                Ok(())
            }
        }
    }

    fn send_packets(&mut self, sockets: &Sockets, packets: Vec<gm::Packet>) -> io::Result<()> {
        let mut bytes = [0u8; 64];
        for packet in packets {
            let wire = codec::encode(packet.message, &mut bytes);
            let socket = match packet.socket {
                gm::Socket::Event => &sockets.event,
                gm::Socket::General => &sockets.general,
            };
            match self.source.send(socket, wire, packet.destination) {
                Ok(_) => (),
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => (),
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }

    fn step_role(&mut self, announce: Option<role::AnnounceSeen>) {
        let now = self.source.now_ns();
        let events = self
            .gm
            .as_mut()
            .map(|gm| gm.take_events())
            .unwrap_or_default();
        let foreign_gm = self.gm.as_ref().and_then(|gm| gm.foreign_identity());
        let inputs = role::Inputs {
            now,
            announce,
            follower_master: self.follower.master_identity(),
            follower_locked: self.follower.locked().is_some(),
            gm_stepped_aside: events
                .stepped_aside
                .map(|reason| (reason, foreign_gm.unwrap_or([0; 8]))),
            gm_reclaimed: events.reclaimed,
            loss_holdover_expired: std::mem::take(&mut self.loss_expired),
            lock_timed_out: std::mem::take(&mut self.lock_expired),
        };
        self.role.step(inputs);
        self.apply_role(now);
    }

    fn apply_role(&mut self, now: i128) {
        let role = self.role.role();
        if role != self.previous_role {
            match role {
                Role::Gm => self.start_gm(now),
                Role::Slave => {
                    self.pinned_winner = if matches!(self.previous_role, Role::Gm | Role::Passive) {
                        self.role.status().gm
                    } else {
                        None
                    };
                }
                _ => self.pinned_winner = None,
            }
            if !matches!(role, Role::Gm | Role::Passive) {
                // Stop announcing immediately when leaving the GM role.
                self.gm = None;
                self.gm_base = None;
            }
            self.previous_role = role;
        }
        if role == Role::Gm {
            if self.gm_base.is_none() {
                let media = match self.config.gm_utc_offset {
                    Some(offset) if self.gm.is_some() => {
                        unix_now_ns() + i128::from(offset) * NS_PER_S
                    }
                    _ => self.continuity_media(now),
                };
                self.gm_base = Some((now, media));
            }
        } else {
            self.gm_base = None;
        }
        if role == Role::Local {
            if self.local_base.is_none() {
                let media = self.continuity_media(now);
                self.local_base = Some((now, media));
            }
        } else {
            self.local_base = None;
        }
        let pin = match role {
            Role::Passive => self.role.status().gm,
            Role::Slave => self.pinned_winner.or(self.config.pinned_gm),
            _ => self.config.pinned_gm,
        };
        self.follower.set_pin(pin);
        self.media = match role {
            Role::Slave | Role::Passive => self
                .follower
                .locked()
                .map(|(gm, fit)| (MediaSource::Ptp(gm), fit)),
            Role::Gm => match (self.identity, self.gm_now(now)) {
                (Some(identity), Some(gm_now)) => {
                    Some((MediaSource::Ptp(identity), ClockFit::anchored(now, gm_now)))
                }
                _ => None,
            },
            Role::Local => self.local_base.map(|(mono, media)| {
                (
                    MediaSource::Local,
                    ClockFit::anchored(now, media + (now - mono)),
                )
            }),
            _ => None,
        };
    }

    fn start_gm(&mut self, now: i128) {
        if self.gm.is_some() {
            return;
        }
        let Some(identity) = self.identity else {
            return;
        };
        let mut config = gm::default_config(identity, self.config.domain);
        config.priority1 = self.config.priority1;
        config.priority2 = self.config.priority2;
        config.utc_offset = self.config.gm_utc_offset;
        config.announce_timeout_ns = self.config.announce_timeout_ns;
        config.multicast = self.config.multicast;
        config.unicast = self.config.unicast_master;
        config.event_port = self.config.event_port;
        config.general_port = self.config.general_port;
        self.gm = Some(Gm::new(config, now));
    }

    fn gm_now(&self, now: i128) -> Option<i128> {
        let (mono, media) = self.gm_base?;
        Some(media + (now - mono))
    }

    /// Media time just before a role change, so a planned switch keeps the RTP
    /// timestamp grid continuous instead of jumping.
    fn continuity_media(&self, now: i128) -> i128 {
        self.media.map(|(_, fit)| fit.ptp_ns(now)).unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time::NS_PER_MS;

    const GM: ClockId = [7; 8];
    const MASTER: PortId = [2; 10];
    const PORT: PortId = [1; 10];

    fn follower_config(ip: Ipv4Addr) -> Config {
        Config {
            domain: 42,
            pinned_gm: Some(GM),
            interface: ip,
            unicast_master: Some(ip),
            event_port: 319,
            general_port: 320,
            local_event_port: 0,
            local_general_port: 0,
            ..Config::default()
        }
    }

    fn announce(grandmaster: ClockId) -> Body {
        Body::Announce(codec::Announce {
            origin_ns: 0,
            current_utc_offset: 0,
            priority1: gm::DEFAULT_PRIORITY,
            clock_class: gm::DEFAULT_CLOCK_CLASS,
            clock_accuracy: gm::DEFAULT_CLOCK_ACCURACY,
            offset_scaled_log_variance: gm::DEFAULT_OFFSET_SCALED_LOG_VARIANCE,
            priority2: gm::DEFAULT_PRIORITY,
            grandmaster,
            steps_removed: 0,
            time_source: gm::TIME_SOURCE_INTERNAL_OSCILLATOR,
        })
    }

    fn announce_dataset(priority1: u8, gm: ClockId) -> Body {
        match announce(gm) {
            Body::Announce(mut body) => {
                body.priority1 = priority1;
                Body::Announce(body)
            }
            _ => unreachable!(),
        }
    }

    fn message(source: PortId, body: Body, seq: u16, flags: u16, log_interval: i8) -> Message {
        Message {
            domain: 42,
            source,
            seq,
            flags,
            log_interval,
            correction_scaled_ns: 0,
            body,
        }
    }

    #[test]
    fn one_step_sync_is_rejected_with_a_stable_reason() {
        // AES67 media-clock recovery needs two-step Sync/Follow_Up; a bare
        // one-step Sync must be distinguishable from malformed traffic.
        let ip = Ipv4Addr::LOCALHOST;
        let config = follower_config(ip);
        let mut state = FollowerState::new(&config, PORT, TimestampMode::Software);
        state.receive(message(MASTER, announce(GM), 0, 0, 1), ip, 0, false);
        let error = state
            .accept(message(MASTER, Body::Sync, 1, 0, 0), ip, 10, true)
            .unwrap_err();
        assert_eq!(error, "one_step_sync_unsupported");
    }

    #[test]
    fn announce_timeout_uses_the_config_and_clamps_a_hostile_cadence() {
        let ip = Ipv4Addr::LOCALHOST;
        let config = Config {
            domain: 42,
            interface: ip,
            unicast_master: Some(ip),
            announce_timeout_ns: 2 * NS_PER_S,
            ..Config::default()
        };
        let mut state = FollowerState::new(&config, PORT, TimestampMode::Software);
        // logMessageInterval 20 is about twelve days per announce. The cadence
        // is clamped to 8 s and the budget is max(configured 2 s, 5 x 8 s).
        state.receive(message(MASTER, announce(GM), 0, 0, 20), ip, 0, false);
        assert!(state.master.is_some());
        assert_eq!(state.diagnostics.announce_interval_ns, 8 * NS_PER_S);
        state.expire(39 * NS_PER_S);
        assert!(state.master.is_some(), "39 s is inside the 40 s budget");
        state.expire(41 * NS_PER_S);
        assert!(state.master.is_none(), "the master must never live for days");
    }

    #[test]
    fn a_better_foreign_announce_replaces_the_first_master() {
        let ip = Ipv4Addr::LOCALHOST;
        let config = Config {
            domain: 42,
            interface: ip,
            unicast_master: Some(ip),
            ..Config::default()
        };
        let mut state = FollowerState::new(&config, PORT, TimestampMode::Software);
        let better_source = [9; 10];
        let other_ip = Ipv4Addr::new(127, 0, 0, 2);
        state.receive(message(MASTER, announce_dataset(128, GM), 0, 0, 1), ip, 0, false);
        assert_eq!(state.master.unwrap().gm, GM);
        // A worse foreign dataset is still rejected.
        state.receive(
            message(better_source, announce_dataset(200, [8; 8]), 0, 0, 1),
            other_ip,
            0,
            false,
        );
        assert_eq!(state.master.unwrap().gm, GM);
        // A strictly better one takes over, even from a different source.
        state.receive(
            message(better_source, announce_dataset(10, [9; 8]), 0, 0, 1),
            other_ip,
            0,
            false,
        );
        assert_eq!(state.master.unwrap().gm, [9; 8]);
    }

    #[test]
    fn two_step_pin_domain_corrections_loss_and_recovery() {
        let ip = Ipv4Addr::LOCALHOST;
        let mut config = follower_config(ip);
        // Keep the configured floor below 5 x the 125 ms cadence this test
        // advertises, so the expiry deadline stays at 625 ms as before.
        config.announce_timeout_ns = 125_000_000;
        let mut state = FollowerState::new(&config, PORT, TimestampMode::Software);
        let announce_body = || announce(GM);
        let msg = |body, seq, flags, correction_scaled_ns| Message {
            log_interval: -3,
            domain: 42,
            source: MASTER,
            seq,
            flags,
            correction_scaled_ns,
            body,
        };
        // Wrong grandmaster is rejected by the pin; wrong domain is rejected.
        state.receive(
            message(MASTER, announce([8; 8]), 0, codec::PTP_TIMESCALE, -3),
            ip,
            0,
            false,
        );
        state.receive(
            Message {
                domain: 0,
                ..message(MASTER, announce_body(), 0, 0, 1)
            },
            ip,
            0,
            false,
        );
        assert!(state.master.is_none());
        let epoch = 1_881_000_000_000_000_000_i128;
        for cycle in [0, 4_000_000_000] {
            for seq in 0..12 {
                let x = cycle + i128::from(seq) * 130_000_000;
                let t1 = epoch + x + x * 80 / 1_000_000;
                let tx = x + 100_000;
                state.receive(
                    message(MASTER, announce_body(), 0, codec::PTP_TIMESCALE, -3),
                    ip,
                    x,
                    false,
                );
                // FollowUp can be polled before Sync; they travel over separate sockets.
                state.receive(
                    msg(Body::FollowUp(t1), seq, 0, 25 << 16),
                    ip,
                    x + 50_000,
                    false,
                );
                state.receive(
                    msg(Body::Sync, seq, codec::TWO_STEP, 75 << 16),
                    ip,
                    x + 50_000,
                    true,
                );
                let req = state.request(tx, true).unwrap();
                state.pending.as_mut().unwrap().sent_ns = Some(tx);
                let received_ns = t1 + 149_908;
                state.receive(
                    msg(
                        Body::DelayResp {
                            received_ns,
                            requester: [9; 10],
                        },
                        req.seq,
                        0,
                        0,
                    ),
                    ip,
                    x + 200_000,
                    false,
                );
                assert!(state.pending.as_ref().unwrap().received_scaled_ns.is_none());
                assert_eq!(
                    state.diagnostics.last_delay_reject,
                    Some("wrong_requesting_port_identity")
                );
                state.receive(
                    msg(
                        Body::DelayResp {
                            received_ns,
                            requester: PORT,
                        },
                        req.seq.wrapping_add(1),
                        0,
                        0,
                    ),
                    ip,
                    x + 200_000,
                    false,
                );
                assert!(state.pending.as_ref().unwrap().received_scaled_ns.is_none());
                assert_eq!(
                    state.diagnostics.last_delay_reject,
                    Some("wrong_sequence_id")
                );
                state.receive(
                    msg(
                        Body::DelayResp {
                            received_ns,
                            requester: PORT,
                        },
                        req.seq,
                        0,
                        0,
                    ),
                    ip,
                    x + 200_000,
                    false,
                );
                state.finish_exchange(x + 200_000);
            }
            assert_eq!(state.locked().unwrap().0, GM);
            let fit = state.locked().unwrap().1;
            assert!((fit.rate_ppm - 80.0).abs() < 0.01);
            assert!((fit.ptp_ns(cycle) - (epoch + cycle + cycle * 80 / 1_000_000)).abs() <= 3);
            let announce_deadline = cycle + 11 * 130_000_000 + 5 * 125_000_000;
            state.expire(announce_deadline - 1);
            assert!(state.locked().is_some());
            state.expire(announce_deadline);
            assert_eq!(state.diagnostics.last_lost_reason, Some("announce_timeout"));
            assert!(state.locked().is_none());
            assert!(state.master.is_none());
        }
    }

    #[test]
    fn arb_announce_locks_frequency_only_and_reports_untraceable() {
        // EXTENSION / DEVIATION: ptpTimescale=0 (ARB) is accepted for frequency lock, but the
        // media clock epoch is then an arbitrary anchor, so time_traceable
        // must stay false. AES67 section 5's shared TAI epoch is not met.
        let ip = Ipv4Addr::LOCALHOST;
        let config = follower_config(ip);
        let mut state = FollowerState::new(&config, PORT, TimestampMode::Software);
        let epoch = 1_881_000_000_000_000_000_i128;
        for seq in 0..12 {
            let x = i128::from(seq) * 130_000_000;
            let t1 = epoch + x;
            let tx = x + 100_000;
            state.receive(message(MASTER, announce(GM), 0, 0, 1), ip, x, false);
            state.receive(
                message(MASTER, Body::FollowUp(t1), seq, 0, 0),
                ip,
                x + 50_000,
                false,
            );
            state.receive(
                message(MASTER, Body::Sync, seq, codec::TWO_STEP, 0),
                ip,
                x + 50_000,
                true,
            );
            let req = state.request(tx, true).unwrap();
            state.pending.as_mut().unwrap().sent_ns = Some(tx);
            state.receive(
                message(
                    MASTER,
                    Body::DelayResp {
                        received_ns: t1 + 149_908,
                        requester: PORT,
                    },
                    req.seq,
                    0,
                    0,
                ),
                ip,
                x + 200_000,
                false,
            );
            state.finish_exchange(x + 200_000);
        }
        assert_eq!(state.locked().unwrap().0, GM, "ARB announce still locks");
        assert!(
            !state.time_traceable(),
            "ARB is frequency-only, no time sync"
        );
        assert!(!state.ptp_timescale(), "ptpTimescale=0");
    }

    #[test]
    fn traceability_comes_from_the_time_traceable_flag_not_ptp_timescale() {
        // ptpTimescale=1 and timeTraceable=0 is the
        // real board dataset; they are different flags. linuxptp msg.h:66
        // names TIME_TRACEABLE as bit 4 of flagField[1] (mask 0x0010 in the
        // big-endian flags value), and UTC_OFF_VALID as bit 2 (mask 0x0004).
        assert_eq!(codec::TIME_TRACEABLE, 0x0010);
        assert_eq!(codec::CURRENT_UTC_OFFSET_VALID, 0x0004);
        let ip = Ipv4Addr::LOCALHOST;
        let config = follower_config(ip);
        let mut untraceable = FollowerState::new(&config, PORT, TimestampMode::Software);
        let mut traceable = FollowerState::new(&config, PORT, TimestampMode::Software);
        untraceable.receive(
            message(
                MASTER,
                announce(GM),
                0,
                codec::PTP_TIMESCALE | codec::CURRENT_UTC_OFFSET_VALID,
                1,
            ),
            ip,
            0,
            false,
        );
        assert!(untraceable.ptp_timescale());
        assert!(!untraceable.time_traceable());
        traceable.receive(
            message(
                MASTER,
                announce(GM),
                0,
                codec::PTP_TIMESCALE | codec::TIME_TRACEABLE,
                1,
            ),
            ip,
            1,
            false,
        );
        assert!(traceable.ptp_timescale());
        assert!(traceable.time_traceable());
    }

    #[test]
    fn multicast_ttl_matches_the_validated_board_profile() {
        // TTL is a deployment/profile choice: the board
        // GM profile and the 2026-11-22 capture use TTL 16, not TTL 1.
        let mut config = Config {
            interface: Ipv4Addr::LOCALHOST,
            unicast_master: Some(Ipv4Addr::LOCALHOST),
            ..Config::default()
        };
        config.local_event_port = 0;
        config.local_general_port = 0;
        let sockets = Sockets::open(&config).unwrap();
        assert_eq!(sockets.event.multicast_ttl_v4().unwrap(), PTP_MULTICAST_TTL);
        assert_eq!(
            sockets.general.multicast_ttl_v4().unwrap(),
            PTP_MULTICAST_TTL
        );
    }

    fn probe_ports() -> (u16, u16) {
        let event = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let general = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        (
            event.local_addr().unwrap().port(),
            general.local_addr().unwrap().port(),
        )
    }

    fn gm_runtime() -> (PtpRuntime, UdpSocket, UdpSocket) {
        let view_event = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let view_general = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        view_event
            .set_read_timeout(Some(std::time::Duration::from_millis(100)))
            .unwrap();
        view_general
            .set_read_timeout(Some(std::time::Duration::from_millis(100)))
            .unwrap();
        let config = Config {
            mode: PtpMode::Gm,
            domain: 19,
            interface: Ipv4Addr::LOCALHOST,
            unicast_master: Some(Ipv4Addr::LOCALHOST),
            gm_identity: Some([0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc]),
            // gm mode listens for this long before announcing; the test cannot
            // wait the 6 s production default.
            announce_timeout_ns: 2 * NS_PER_MS,
            event_port: view_event.local_addr().unwrap().port(),
            general_port: view_general.local_addr().unwrap().port(),
            local_event_port: 0,
            local_general_port: 0,
            ..Config::default()
        };
        let (_, source) = crate::time::timestamp_source(TimestampMode::Software).unwrap();
        let runtime = PtpRuntime::open(config, source).unwrap();
        (runtime, view_event, view_general)
    }

    fn recv_message(socket: &UdpSocket) -> Option<Message> {
        let mut buf = [0u8; 1536];
        let (size, _) = socket.recv_from(&mut buf).ok()?;
        codec::decode(&buf[..size]).ok()
    }

    #[test]
    fn gm_engine_drives_real_udp_announce_sync_follow_up_and_delay_resp() {
        let (mut runtime, view_event, view_general) = gm_runtime();
        assert_eq!(runtime.status().role, Role::Listening);
        std::thread::sleep(std::time::Duration::from_millis(5));
        runtime.tick().unwrap();
        assert_eq!(runtime.status().role, Role::Gm);
        let mut announce = None;
        let mut sync = None;
        let mut follow_up = None;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while std::time::Instant::now() < deadline
            && (announce.is_none() || sync.is_none() || follow_up.is_none())
        {
            runtime.tick().unwrap();
            if announce.is_none() {
                announce =
                    recv_message(&view_general).filter(|m| matches!(m.body, Body::Announce(_)));
            }
            if sync.is_none() {
                sync = recv_message(&view_event).filter(|m| matches!(m.body, Body::Sync));
            }
            if follow_up.is_none() {
                follow_up =
                    recv_message(&view_general).filter(|m| matches!(m.body, Body::FollowUp(_)));
            }
        }
        let announce = announce.expect("Announce over real UDP");
        let sync = sync.expect("Sync over real UDP");
        let follow_up = follow_up.expect("Follow_Up over real UDP");
        assert_eq!(announce.domain, 19);
        assert_eq!(sync.domain, 19);
        assert_eq!(
            announce.source,
            gm::port_identity([0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc])
        );
        assert_eq!(sync.flags & codec::TWO_STEP, codec::TWO_STEP);
        assert_eq!(sync.seq, follow_up.seq, "Follow_Up echoes the Sync seqId");
        let Body::Announce(body) = announce.body else {
            unreachable!()
        };
        assert_eq!(body.steps_removed, 0);
        assert_eq!(
            body.grandmaster,
            [0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc]
        );
        assert_eq!(body.clock_class, gm::DEFAULT_CLOCK_CLASS);

        // Delay_Req from a separate requester: the GM answers on its general
        // socket to the requester's IP and its own general port.
        let requester = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let requester_id: PortId = [0x33; 10];
        let request = Message {
            domain: 19,
            source: requester_id,
            seq: 0x1234,
            flags: 0,
            log_interval: 0,
            correction_scaled_ns: 0,
            body: Body::DelayReq(0),
        };
        let mut bytes = [0u8; 64];
        let wire = codec::encode(request, &mut bytes);
        let event_port = runtime.local_event_port().unwrap();
        requester
            .send_to(wire, (Ipv4Addr::LOCALHOST, event_port))
            .unwrap();
        let mut response = None;
        for _ in 0..20 {
            runtime.tick().unwrap();
            if response.is_none() {
                response = recv_message(&view_general)
                    .filter(|m| matches!(m.body, Body::DelayResp { .. }));
            }
            if response.is_some() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        let response = response.expect("Delay_Resp over real UDP");
        assert_eq!(response.seq, 0x1234);
        let Body::DelayResp { requester, .. } = response.body else {
            unreachable!()
        };
        assert_eq!(requester, requester_id);
        assert!(runtime.gm_diagnostics().unwrap().delay_responses_sent == 1);
    }

    #[test]
    fn auto_listens_then_becomes_gm_after_the_announce_timeout() {
        let (event_port, general_port) = probe_ports();
        let config = Config {
            mode: PtpMode::Auto,
            gm_capable: true,
            domain: 19,
            interface: Ipv4Addr::LOCALHOST,
            unicast_master: Some(Ipv4Addr::LOCALHOST),
            gm_identity: Some([0x11; 8]),
            event_port,
            general_port,
            announce_timeout_ns: 40 * NS_PER_MS,
            ..Config::default()
        };
        let (_, source) = crate::time::timestamp_source(TimestampMode::Software).unwrap();
        let mut runtime = PtpRuntime::open(config, source).unwrap();
        assert_eq!(runtime.status().role, Role::Listening);
        assert!(runtime.media_clock().is_none());
        std::thread::sleep(std::time::Duration::from_millis(60));
        runtime.tick().unwrap();
        assert_eq!(runtime.status().role, Role::Gm);
        assert_eq!(runtime.status().source, Source::SelfGm);
        let (source, fit) = runtime.media_clock().expect("GM has a media clock");
        assert_eq!(source, MediaSource::Ptp([0x11; 8]));
        assert_eq!(fit.rate_ppm, 0.0);
        assert!(runtime.status().transitions >= 1);
    }

    #[test]
    fn gm_mode_refuses_to_start_without_a_readable_clock_identity() {
        let config = Config {
            mode: PtpMode::Gm,
            interface: Ipv4Addr::new(203, 0, 113, 7),
            gm_identity: None,
            ..Config::default()
        };
        let (_, source) = crate::time::timestamp_source(TimestampMode::Software).unwrap();
        assert!(matches!(
            PtpRuntime::open(config, source),
            Err(OpenError::GmIdentityUnavailable)
        ));
    }

    #[test]
    fn local_mode_opens_no_ptp_sockets_and_uses_a_local_media_clock() {
        let config = Config {
            mode: PtpMode::Local,
            domain: 19,
            ..Config::default()
        };
        let (_, source) = crate::time::timestamp_source(TimestampMode::Software).unwrap();
        let mut runtime = PtpRuntime::open(config, source).unwrap();
        assert_eq!(runtime.status().role, Role::Local);
        assert_eq!(runtime.local_event_port(), None);
        runtime.tick().unwrap();
        let (source, fit) = runtime.media_clock().expect("local media clock");
        assert_eq!(source, MediaSource::Local);
        assert_eq!(fit.rate_ppm, 0.0);
    }
}
