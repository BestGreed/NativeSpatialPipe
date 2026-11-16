//! In-process follower shared by the diagnostic CLI and the Windows audio backend.
use crate::{
    envelope::EnvelopeServo as Servo,
    local::LocalClock,
    ptp::{self, Identity, Message, Pairer},
    servo::{Config, Snapshot},
};
use std::io::Write as _;
use std::{
    error::Error,
    net::{Ipv4Addr, SocketAddr, SocketAddrV4},
};

const PTP_MULTICAST: Ipv4Addr = Ipv4Addr::new(224, 0, 1, 129);
const ACQUISITION_NS: i64 = 6_000_000_000;
const RETRY_NS: i64 = 2_000_000_000;

#[derive(Default)]
struct AutoMaster {
    peer: Option<(Ipv4Addr, Identity)>,
    acquisition_deadline: Option<i64>,
    retry: Option<((Ipv4Addr, Identity), i64)>,
}

impl AutoMaster {
    fn accept(&mut self, ip: Ipv4Addr, identity: Identity, now: i64) -> bool {
        let peer = (ip, identity);
        if let Some(current) = self.peer {
            return peer == current;
        }
        if self.retry.is_some_and(|(failed, until)| peer == failed && now < until) {
            return false;
        }
        self.peer = Some(peer);
        self.acquisition_deadline = Some(now + ACQUISITION_NS);
        println!("auto_master_candidate={ip} identity={identity:?}");
        true
    }

    fn expire(&mut self, now: i64, locked: bool) -> bool {
        let Some(peer) = self.peer else { return false };
        if locked {
            self.acquisition_deadline = None;
            return false;
        }
        if self.acquisition_deadline.is_some_and(|deadline| now < deadline) {
            return false;
        }
        self.retry = Some((peer, now + RETRY_NS));
        self.peer = None;
        self.acquisition_deadline = None;
        println!("auto_master_lost={}", peer.0);
        true
    }
}

pub fn follow(
    args: &[String],
    multicast: bool,
    stop: &std::sync::atomic::AtomicBool,
    mut publish: impl FnMut(Option<crate::servo::Mapping>),
) -> Result<(), Box<dyn Error>> {
    follow_with_identity(args, multicast, stop, |state| publish(state.map(|(mapping, _)| mapping)))
}

/// Publish the mapping together with the identity of the accepted PTP source.
pub fn follow_with_identity(
    args: &[String],
    multicast: bool,
    stop: &std::sync::atomic::AtomicBool,
    mut publish: impl FnMut(Option<(crate::servo::Mapping, Identity)>),
) -> Result<(), Box<dyn Error>> {
    if args.len() != 4 {
        return Err(
            "usage: --follow-unicast INTERFACE_IPV4 MASTER_IPV4_OR_EMPTY LOCAL_MAC_12HEX SECONDS".into(),
        );
    }
    let interface: Ipv4Addr = args[0].parse()?;
    let pinned_master = if args[1].is_empty() {
        None
    } else {
        Some(args[1].parse::<Ipv4Addr>()?)
    };
    let mac = &args[2];
    if mac.len() != 12 || !mac.is_ascii() {
        return Err("MAC must be 12 hex ASCII characters".into());
    }
    let mut uuid = [0u8; 6];
    for i in 0..6 {
        uuid[i] = u8::from_str_radix(&mac[i * 2..i * 2 + 2], 16)?;
    }
    let seconds: u64 = args[3].parse()?;
    if seconds > 120 {
        return Err("duration must be 1..120 seconds".into());
    }
    let local = Identity { uuid, port: 1 };
    #[cfg(windows)]
    let _priority = crate::local::ClockPriorityGuard::enter()?;
    let clock = LocalClock::new()?;
    // Exclusive binds: never enable address reuse alongside DVS on these ports.
    let event = crate::net::bind_exclusive_v4(319)
        .map_err(|e| format!("PTP event bind failed (DVS may own port): {e}"))?;
    let general = crate::net::bind_exclusive_v4(320)?;
    // Bind TX to the selected local address, independently of the wildcard
    // multicast receive sockets. Dante peers also use nonstandard source ports.
    let transmit = std::net::UdpSocket::bind((interface, 0))?;
    crate::net::multicast_interface(&transmit, interface)?;
    println!("transmit_endpoint={}", transmit.local_addr()?);
    if pinned_master.is_none() {
        crate::net::restrict_receive_interface([&event, &general], interface)?;
    }
    for socket in [&event, &general] {
        socket.join_multicast_v4(&PTP_MULTICAST, &interface)?;
        socket.set_nonblocking(true)?;
    }
    let mut pairer = Pairer::new(local);
    let mut auto = AutoMaster::default();
    let mut parent_packet: Option<[u8; 124]> = None;
    let mut servo = Servo::new(Config::default());
    let mut snapshot = Snapshot::default();
    let end = clock.now_ns() + seconds as i64 * 1_000_000_000;
    let mut counts = [0u64; 5];
    let mut trace = std::env::var_os("INFERNO_CLOCK_TRACE")
        .map(std::fs::File::create)
        .transpose()?
        .map(std::io::BufWriter::new);
    if let Some(trace) = &mut trace {
        writeln!(
            trace,
            "received_ns,t1,t2,t3,t4,accepted,rate_ppb,residual_ns,reason"
        )?;
    }
    let mut buffer = [0u8; 2048];
    let mut last_report = 0;
    let master_mode = pinned_master.map_or_else(|| "auto".into(), |ip| ip.to_string());
    println!("experimental_follower=true multicast_delay={multicast} master={master_mode} no_master_election=true software_timestamps=true");
    while (seconds == 0 || clock.now_ns() < end) && !stop.load(std::sync::atomic::Ordering::Relaxed)
    {
        let now = clock.now_ns();
        if auto.expire(now, snapshot.load(now).is_some()) {
            pairer.clear_master();
            parent_packet = None;
            servo = Servo::new(Config::default());
            snapshot = Snapshot::default();
            publish(None);
        }
        for (is_event, socket) in [(true, &event), (false, &general)] {
            // Bound work per iteration so malformed traffic cannot starve expiry/exit.
            for _ in 0..64 {
                let (n, source) = match socket.recv_from(&mut buffer) {
                    Ok(v) => v,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(e) => return Err(e.into()),
                };
                let received = clock.now_ns();
                // Dante hardware may source PTP from 1031/1032. Validate the
                // destination socket, selected peer IP and PTP port identity;
                // the peer's UDP source port is not required to be 319/320.
                let SocketAddr::V4(source) = source else { continue };
                let ip = *source.ip();
                let master = pinned_master.or(auto.peer.map(|peer| peer.0));
                if master.is_some_and(|master| ip != master)
                    || (pinned_master.is_none() && ip == interface)
                {
                    continue;
                }
                counts[0] += 1;
                match ptp::decode(&buffer[..n], &ptp::DEFAULT_SUBDOMAIN) {
                    Ok(Message::Sync { source, sequence }) if is_event => {
                        if pinned_master.is_none()
                            && (source.uuid == local.uuid || !auto.accept(ip, source, received))
                        {
                            continue;
                        }
                        if pairer.sync(source, sequence, received).is_err() {
                            counts[4] += 1;
                        } else {
                            parent_packet = Some(buffer[..124].try_into().unwrap());
                        }
                    }
                    Ok(Message::FollowUp {
                        source,
                        associated_sequence,
                        origin_ns,
                    }) if !is_event => {
                        let t3 = clock.now_ns();
                        if let Ok(seq) =
                            pairer.follow_up(source, associated_sequence, origin_ns, t3)
                        {
                            let request = ptp::delay_request_with_parent(
                                local,
                                seq,
                                &ptp::DEFAULT_SUBDOMAIN,
                                parent_packet.as_ref().ok_or("missing parent dataset")?,
                                pairer
                                    .request_origin_estimate()
                                    .ok_or("missing request timestamp")?,
                            )?;
                            if counts[1] == 0 {
                                println!(
                                    "request_hex={}",
                                    request
                                        .iter()
                                        .map(|v| format!("{v:02x}"))
                                        .collect::<String>()
                                );
                            }
                            if transmit
                                .send_to(
                                    &request,
                                    SocketAddrV4::new(
                                        if multicast {
                                            PTP_MULTICAST
                                        } else {
                                            ip
                                        },
                                        319,
                                    ),
                                )
                                .is_err()
                            {
                                pairer.send_failed();
                                counts[4] += 1;
                            } else {
                                counts[1] += 1;
                            }
                        }
                    }
                    Ok(Message::DelayResponse {
                        source,
                        requester,
                        request_sequence,
                        receipt_ns,
                    }) => {
                        if requester == local {
                            println!("own_delay_response sequence={request_sequence} socket_event={is_event}");
                        }
                        match pairer.response(
                            source,
                            requester,
                            request_sequence,
                            receipt_ns,
                            received,
                        ) {
                            Ok(exchange) => {
                                let observed = servo.observe(exchange, received);
                                if let Some(trace) = &mut trace {
                                    writeln!(
                                        trace,
                                        "{received},{},{},{},{},{},{},{},{}",
                                        exchange.t1,
                                        exchange.t2,
                                        exchange.t3,
                                        exchange.t4,
                                        observed.is_ok(),
                                        servo.status.rate_ppb,
                                        servo.status.residual_ns,
                                        observed.err().unwrap_or("")
                                    )?;
                                }
                                match observed {
                                    Ok(_) => {
                                        counts[2] += 1;
                                        snapshot.publish(servo.mapping(received));
                                    }
                                    Err(reason) => {
                                        counts[3] += 1;
                                        snapshot.publish(servo.mapping(received));
                                        println!("observation_rejected={reason}");
                                    }
                                }
                            }
                            Err(_) => counts[4] += 1,
                        }
                    }
                    Err(_) => counts[4] += 1,
                    _ => {}
                }
            }
        }
        let now = clock.now_ns();
        publish(snapshot.load(now).zip(pairer.master()));
        if now - last_report >= 1_000_000_000 {
            println!("packets={} requests={} observations={} rejected={} unmatched={} locked={} rate_ppb={} residual_ns={:.1} interval_half_width_ns={:.1}",counts[0],counts[1],counts[2],counts[3],counts[4],snapshot.load(now).is_some(),servo.status.rate_ppb,servo.status.residual_ns,servo.status.interval_half_width_ns);
            last_report = now;
            if let Some(trace) = &mut trace {
                trace.flush()?;
            }
        }
        crate::net::wait_readable(&event, &general)?;
    }
    publish(None);
    if !stop.load(std::sync::atomic::Ordering::Relaxed) && snapshot.load(clock.now_ns()).is_none() {
        return Err("no currently valid clock lock; do not use for TX".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_follows_a_new_peer_after_expiry_and_retries_failed_acquisition() {
        let mut auto = AutoMaster::default();
        let first = Ipv4Addr::new(192, 168, 50, 2);
        let second = Ipv4Addr::new(192, 168, 50, 6);
        let identity = Identity { uuid: [1; 6], port: 1 };
        let other_identity = Identity { uuid: [2; 6], port: 1 };
        assert!(auto.accept(first, identity, 0));
        assert!(!auto.accept(first, other_identity, 1));
        assert!(!auto.accept(second, identity, 1));
        assert!(!auto.expire(ACQUISITION_NS - 1, false));
        assert!(auto.expire(ACQUISITION_NS, false));
        assert!(!auto.accept(first, identity, ACQUISITION_NS));
        assert!(auto.accept(second, other_identity, ACQUISITION_NS));
        assert!(!auto.expire(2 * ACQUISITION_NS, true));
        assert!(auto.expire(2 * ACQUISITION_NS + 1, false));
        assert!(auto.accept(first, identity, 2 * ACQUISITION_NS + 1));
    }
}
