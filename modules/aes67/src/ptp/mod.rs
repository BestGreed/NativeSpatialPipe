pub mod codec;

use crate::{
    net::{TOS_EF, dscp, multicast_interface},
    time::{ClockFit, FitResult, MediaClock, TimestampMode, TimestampSource},
};
use codec::{Body, ClockId, Message, PTP_TIMESCALE, PortId, TWO_STEP, UNICAST};
use std::{
    collections::VecDeque,
    hash::{BuildHasher, RandomState},
    io,
    net::{Ipv4Addr, SocketAddr, SocketAddrV4, UdpSocket},
};

const REQUEST_INTERVAL: i128 = 125_000_000;
const EXCHANGE_MAX_AGE: i128 = 2_000_000_000;
const MAX_ROUND_TRIP: i128 = 40_000_000;
const SYNC_HISTORY: usize = 16;
const ANNOUNCE_MISSES: i128 = 5;
const DELAY_RESPONSE_TIMEOUT: i128 = 1_000_000_000;

fn announce_interval(log: i8) -> Option<i128> {
    // 0x7f denotes an unspecified interval, which cannot establish cadence.
    match log {
        -20..=-1 => Some(1_000_000_000 >> (-log as u32)),
        0..=20 => Some(1_000_000_000 << log as u32),
        _ => None,
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
    invalid_packets: u64,
    last_decode_error: Option<codec::DecodeError>,
    last_delay_reject: Option<&'static str>,
    last_fit_reject: Option<FitResult>,
    last_lost_reason: Option<&'static str>,
    announce_interval_ns: i128,
}

#[derive(Clone)]
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
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Master {
    gm: ClockId,
    port: PortId,
    ip: Ipv4Addr,
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

struct State {
    domain: u8,
    pin: Option<ClockId>,
    mode: TimestampMode,
    diagnostics: Diagnostics,
    port: PortId,
    master: Option<Master>,
    announce_at: i128,
    last_good: Option<i128>,
    sync_pairs: VecDeque<(u16, SyncPair)>,
    latest_sync: Option<SyncSample>,
    pending: Option<Delay>,
    next_seq: u16,
    next_req: i128,
    clock: MediaClock,
}

impl State {
    fn new(config: &Config, port: PortId, mode: TimestampMode) -> Self {
        Self {
            domain: config.domain,
            pin: config.pinned_gm,
            mode,
            diagnostics: Diagnostics::default(),
            port,
            master: None,
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

    fn expire(&mut self, now: i128) {
        let budget = self.diagnostics.announce_interval_ns * ANNOUNCE_MISSES;
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
            .is_some_and(|d| now - d.issued_ns >= DELAY_RESPONSE_TIMEOUT)
        {
            self.diagnostics.delay_resp_timeout += 1;
            self.diagnostics.last_delay_reject = Some("response_timeout");
            self.pending = None;
        }
    }

    fn locked(&self) -> Option<(ClockId, ClockFit)> {
        Some((self.master?.gm, self.clock.fit()?))
    }

    fn receive(&mut self, msg: Message, ip: Ipv4Addr, now: i128, event: bool) {
        let result = self.accept(msg, ip, now, event);
        let counts = match msg.body {
            Body::Announce { .. } => &mut self.diagnostics.announce,
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
        if let Body::Announce { grandmaster } = msg.body {
            if event
                || grandmaster == [0; 8]
                || self.pin.is_some_and(|pin| pin != grandmaster)
                || msg.flags & PTP_TIMESCALE == 0
            {
                return Err("invalid_announce");
            }
            let cadence = announce_interval(msg.log_interval).ok_or("invalid_announce_interval")?;
            let master = Master {
                gm: grandmaster,
                port: msg.source,
                ip,
            };
            if let Some(existing) = self.master {
                if existing.port != master.port || existing.ip != master.ip {
                    return Err("wrong_master");
                }
                if existing != master {
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
            .is_some_and(|m| m.port == msg.source && m.ip == ip)
        {
            return Err("wrong_master");
        }
        match msg.body {
            Body::Sync if event && msg.flags & TWO_STEP != 0 => {
                self.sync(msg, now, Some(now), None)
            }
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
            .retain(|(_, p)| now - p.arrival <= EXCHANGE_MAX_AGE);
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
            if self.latest_sync.is_none_or(|s| local_ns > s.local_ns) {
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
            .filter(|s| now - s.local_ns <= EXCHANGE_MAX_AGE)?;
        if now < self.next_req {
            return None;
        }
        if self.pending.is_some() {
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
        let expected =
            elapsed + (elapsed as f64 * self.clock.fit().map_or(0.0, |f| f.rate_ppm) / 1e6) as i128;
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

pub struct Follower {
    config: Config,
    state: State,
    event: UdpSocket,
    general: UdpSocket,
    source: Box<dyn TimestampSource>,
}

impl Follower {
    pub fn open(
        config: Config,
        source: Box<dyn TimestampSource>,
        mode: TimestampMode,
    ) -> io::Result<Self> {
        let open = |configured, remote| -> io::Result<UdpSocket> {
            let (addr, port) = if config.unicast_master.is_some() {
                (config.interface, configured)
            } else {
                (
                    Ipv4Addr::UNSPECIFIED,
                    if configured != 0 { configured } else { remote },
                )
            };
            let socket = UdpSocket::bind((addr, port))?;
            socket.set_nonblocking(true)?;
            if config.unicast_master.is_none() {
                socket.join_multicast_v4(&config.multicast, &config.interface)?;
                multicast_interface(&socket, config.interface)?;
                socket.set_multicast_ttl_v4(1)?;
                socket.set_multicast_loop_v4(false)?;
            }
            Ok(socket)
        };
        let event = open(config.local_event_port, config.event_port)?;
        let general = open(config.local_general_port, config.general_port)?;
        // AES67-2023 sec 6.2 Table 1 (line 1295-1299): clock traffic EF (46).
        let _ = dscp(&event, TOS_EF);
        let _ = dscp(&general, TOS_EF);
        // Use a random locally administered identity for this follower session.
        let mut clock_id = RandomState::new()
            .hash_one(event.local_addr()?)
            .to_be_bytes();
        clock_id[0] = (clock_id[0] | 2) & !1;
        let mut port = [0; 10];
        port[..8].copy_from_slice(&clock_id);
        port[9] = 1;
        let state = State::new(&config, port, mode);
        Ok(Self {
            config,
            state,
            event,
            general,
            source,
        })
    }

    pub fn now_ns(&self) -> i128 {
        self.source.now_ns()
    }
    pub fn locked(&self) -> Option<(ClockId, ClockFit)> {
        self.state.locked()
    }

    pub fn diagnostics(&self) -> &Diagnostics {
        &self.state.diagnostics
    }

    pub fn tick(&mut self) -> io::Result<()> {
        let mut buf = [0; 1536];
        for (socket, event) in [(&self.event, true), (&self.general, false)] {
            // Bound the work per socket so a flood cannot indefinitely postpone RTP.
            for _ in 0..16 {
                match self.source.recv(socket, &mut buf) {
                    Ok((n, SocketAddr::V4(peer), now)) => {
                        let ip = *peer.ip();
                        if self.config.unicast_master.is_none_or(|master| ip == master) {
                            match codec::decode(&buf[..n]) {
                                Ok(message) => self.state.receive(message, ip, now, event),
                                Err(error) => {
                                    self.state.diagnostics.invalid_packets += 1;
                                    self.state.diagnostics.last_decode_error = Some(error);
                                }
                            }
                        }
                    }
                    Ok(_) => (),
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    // Winsock reports an ICMP port unreachable on a subsequent
                    // UDP receive. The qualified Announce/fit timers own loss.
                    Err(error) if cfg!(windows) && error.raw_os_error() == Some(10054) => {
                        self.state.diagnostics.delay_req_icmp_unreachable += 1;
                        continue;
                    }
                    Err(error) => return Err(error),
                }
            }
        }
        if let Some(delay) = self
            .state
            .pending
            .as_mut()
            .filter(|delay| delay.sent_ns.is_none())
        {
            delay.sent_ns = self.source.tx_timestamp()?;
        }
        let now = self.source.now_ns();
        self.state.finish_exchange(now);
        self.state.expire(now);
        if let Some(msg) = self
            .state
            .request(now, self.config.unicast_master.is_some())
        {
            let destination = SocketAddrV4::new(
                self.config.unicast_master.unwrap_or(self.config.multicast),
                self.config.event_port,
            );
            let mut bytes = [0; 64];
            match self
                .source
                .send(&self.event, codec::encode(msg, &mut bytes), destination)
            {
                Ok(sent) => {
                    self.state.diagnostics.delay_req_sent += 1;
                    self.state.pending.as_mut().unwrap().sent_ns = sent;
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    self.state.pending = None
                }
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn two_step_pin_domain_corrections_loss_and_recovery() {
        let ip = Ipv4Addr::LOCALHOST;
        let gm = [7; 8];
        let master: PortId = [2; 10];
        let port = [1; 10];
        let config = Config {
            domain: 42,
            pinned_gm: Some(gm),
            interface: ip,
            multicast: Ipv4Addr::new(224, 0, 1, 129),
            unicast_master: Some(ip),
            event_port: 319,
            general_port: 320,
            local_event_port: 0,
            local_general_port: 0,
        };
        let mut s = State::new(&config, port, TimestampMode::Software);
        let msg = |body, seq, flags, correction_scaled_ns| Message {
            log_interval: -3,
            domain: 42,
            source: master,
            seq,
            flags,
            correction_scaled_ns,
            body,
        };
        let announce = |id| msg(Body::Announce { grandmaster: id }, 0, PTP_TIMESCALE, 0);
        s.receive(announce([8; 8]), ip, 0, false);
        s.receive(
            Message {
                domain: 0,
                ..announce(gm)
            },
            ip,
            0,
            false,
        );
        assert!(s.master.is_none());
        let epoch = 1_881_000_000_000_000_000_i128;
        for cycle in [0, 4_000_000_000] {
            for seq in 0..12 {
                let x = cycle + i128::from(seq) * 130_000_000;
                let t1 = epoch + x + x * 80 / 1_000_000;
                let tx = x + 100_000;
                s.receive(announce(gm), ip, x, false);
                // FollowUp can be polled before Sync; they travel over separate sockets.
                s.receive(
                    msg(Body::FollowUp(t1), seq, 0, 25 << 16),
                    ip,
                    x + 50_000,
                    false,
                );
                s.receive(
                    msg(Body::Sync, seq, TWO_STEP, 75 << 16),
                    ip,
                    x + 50_000,
                    true,
                );
                let req = s.request(tx, true).unwrap();
                s.pending.as_mut().unwrap().sent_ns = Some(tx);
                let received_ns = t1 + 149_908;
                s.receive(
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
                assert!(s.pending.as_ref().unwrap().received_scaled_ns.is_none());
                assert_eq!(
                    s.diagnostics.last_delay_reject,
                    Some("wrong_requesting_port_identity")
                );
                s.receive(
                    msg(
                        Body::DelayResp {
                            received_ns,
                            requester: port,
                        },
                        req.seq.wrapping_add(1),
                        0,
                        0,
                    ),
                    ip,
                    x + 200_000,
                    false,
                );
                assert!(s.pending.as_ref().unwrap().received_scaled_ns.is_none());
                assert_eq!(s.diagnostics.last_delay_reject, Some("wrong_sequence_id"));
                s.receive(
                    msg(
                        Body::DelayResp {
                            received_ns,
                            requester: port,
                        },
                        req.seq,
                        0,
                        0,
                    ),
                    ip,
                    x + 200_000,
                    false,
                );
                s.finish_exchange(x + 200_000);
            }
            assert_eq!(s.locked().unwrap().0, gm);
            let fit = s.locked().unwrap().1;
            assert!((fit.rate_ppm - 80.0).abs() < 0.01);
            assert!((fit.ptp_ns(cycle) - (epoch + cycle + cycle * 80 / 1_000_000)).abs() <= 3);
            let announce_deadline = cycle + 11 * 130_000_000 + 5 * 125_000_000;
            s.expire(announce_deadline - 1);
            assert!(s.locked().is_some());
            s.expire(announce_deadline);
            assert_eq!(s.diagnostics.last_lost_reason, Some("announce_timeout"));
            assert!(s.locked().is_none());
            assert!(s.master.is_none());
        }
    }
}
