//! PTPv1 wire fields, independently implemented from published packet layouts.
//! This prototype deliberately accepts two-step Sync/Follow_Up only.
use crate::servo::Exchange;

pub const DEFAULT_SUBDOMAIN: [u8; 16] = *b"_DFLT\0\0\0\0\0\0\0\0\0\0\0";
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Identity {
    pub uuid: [u8; 6],
    pub port: u16,
}
#[derive(Clone, Copy, Debug)]
pub enum Message {
    Sync {
        source: Identity,
        sequence: u16,
    },
    FollowUp {
        source: Identity,
        associated_sequence: u16,
        origin_ns: i64,
    },
    DelayResponse {
        source: Identity,
        requester: Identity,
        request_sequence: u16,
        receipt_ns: i64,
    },
    Other,
}
fn u16_at(data: &[u8], offset: usize) -> u16 {
    u16::from_be_bytes(data[offset..offset + 2].try_into().unwrap())
}
fn timestamp(data: &[u8], offset: usize) -> Result<i64, &'static str> {
    let seconds = u32::from_be_bytes(data[offset..offset + 4].try_into().unwrap()) as i64;
    let nanos = u32::from_be_bytes(data[offset + 4..offset + 8].try_into().unwrap());
    if nanos >= 1_000_000_000 {
        return Err("invalid timestamp nanoseconds");
    }
    Ok(seconds * 1_000_000_000 + nanos as i64)
}
pub fn decode(data: &[u8], domain: &[u8; 16]) -> Result<Message, &'static str> {
    if data.len() < 40 {
        return Err("short header");
    }
    if u16_at(data, 0) != 1 || u16_at(data, 2) != 1 {
        return Err("not Ethernet/UDP PTPv1");
    }
    if &data[4..20] != domain {
        return Err("wrong PTP subdomain");
    }
    let source = Identity {
        uuid: data[22..28].try_into().unwrap(),
        port: u16_at(data, 28),
    };
    match data[32] {
        0 => {
            if data[20] != 1 || data.len() < 124 {
                return Err("short/invalid Sync");
            }
            // Epoch rollover support is intentionally not implemented.
            if u16_at(data, 48) != 0 {
                return Err("nonzero PTPv1 epoch unsupported");
            }
            Ok(Message::Sync {
                source,
                sequence: u16_at(data, 30),
            })
        }
        2 => {
            if data[20] != 2 || data.len() < 52 {
                return Err("short/invalid Follow_Up");
            }
            Ok(Message::FollowUp {
                source,
                associated_sequence: u16_at(data, 42),
                origin_ns: timestamp(data, 44)?,
            })
        }
        3 => {
            if data[20] != 2 || data.len() < 60 {
                return Err("short/invalid Delay_Resp");
            }
            Ok(Message::DelayResponse {
                source,
                requester: Identity {
                    uuid: data[50..56].try_into().unwrap(),
                    port: u16_at(data, 56),
                },
                request_sequence: u16_at(data, 58),
                receipt_ns: timestamp(data, 40)?,
            })
        }
        _ => Ok(Message::Other),
    }
}
pub fn delay_request(local: Identity, sequence: u16, domain: &[u8; 16]) -> [u8; 124] {
    let mut data = [0u8; 124];
    data[0..2].copy_from_slice(&1u16.to_be_bytes());
    data[2..4].copy_from_slice(&1u16.to_be_bytes());
    data[4..20].copy_from_slice(domain);
    data[20] = 1;
    data[21] = 1;
    data[22..28].copy_from_slice(&local.uuid);
    data[28..30].copy_from_slice(&local.port.to_be_bytes());
    data[30..32].copy_from_slice(&sequence.to_be_bytes());
    data[32] = 1;
    data[67] = 255;
    data[95] = 255;
    data
}

/// Advertise the selected parent's clock dataset instead of an all-zero one.
/// The origin is a coarse pre-lock estimate for the request body, never a
/// published synchronized clock. The peer measures t4 independently.
pub fn delay_request_with_parent(
    local: Identity,
    sequence: u16,
    domain: &[u8; 16],
    parent: &[u8],
    origin: i64,
) -> Result<[u8; 124], &'static str> {
    if !matches!(decode(parent, domain)?, Message::Sync { .. })
        || origin < 0
        || origin / 1_000_000_000 > u32::MAX as i64
    {
        return Err("invalid parent dataset/origin");
    }
    let mut data = delay_request(local, sequence, domain);
    data[34..36].copy_from_slice(&parent[34..36]);
    data[48..124].copy_from_slice(&parent[48..124]);
    // Sync carries the leader's local dataset. A Delay_Req must describe the
    // follower and its selected parent, not repeat leader-local state/padding.
    for at in [72, 80, 84, 88] {
        data[at..at + 2].fill(0);
    }
    data[86..88].copy_from_slice(&0x7060u16.to_be_bytes());
    let steps = u16_at(parent, 90).saturating_add(1);
    data[90..92].copy_from_slice(&steps.to_be_bytes());
    data[101] = parent[21];
    data[102..108].copy_from_slice(&parent[22..28]);
    data[110..112].copy_from_slice(&parent[28..30]);
    data[114..118].fill(0);
    data[40..44].copy_from_slice(&((origin / 1_000_000_000) as u32).to_be_bytes());
    data[44..48].copy_from_slice(&((origin % 1_000_000_000) as u32).to_be_bytes());
    data[95] = 255;
    data[96..100].copy_from_slice(b"DFLT");
    Ok(data)
}

/// Bounded outstanding state, pinned master identity, no master election.
pub struct Pairer {
    local: Identity,
    master: Option<Identity>,
    sync: Option<(u16, i64)>,
    pending: Option<(u16, Exchange)>,
    next_sequence: u16,
    timeout_ns: i64,
}
impl Pairer {
    pub(crate) fn clear_master(&mut self) {
        self.master = None;
        self.sync = None;
        self.pending = None;
    }

    pub fn master(&self) -> Option<Identity> {
        self.master
    }
    pub fn new(local: Identity) -> Self {
        Self {
            local,
            master: None,
            sync: None,
            pending: None,
            next_sequence: 0,
            timeout_ns: 2_000_000_000,
        }
    }
    pub fn sync(
        &mut self,
        source: Identity,
        sequence: u16,
        receive_ns: i64,
    ) -> Result<(), &'static str> {
        if self.master.is_some_and(|master| master != source) {
            return Err("master identity changed; restart explicitly");
        }
        self.master = Some(source);
        if self.sync.is_some_and(|(seq, _)| seq == sequence) {
            return Err("duplicate Sync");
        }
        self.sync = Some((sequence, receive_ns));
        Ok(())
    }
    /// Caller supplies a fresh local timestamp immediately before send().
    pub fn follow_up(
        &mut self,
        source: Identity,
        sequence: u16,
        t1: i64,
        t3: i64,
    ) -> Result<u16, &'static str> {
        if self.master != Some(source) {
            return Err("wrong master");
        }
        let (seq, t2) = self.sync.ok_or("no pending Sync")?;
        if seq != sequence || t3 < t2 || t3 - t2 > self.timeout_ns {
            return Err("unmatched/expired Follow_Up");
        }
        self.sync = None;
        // Keep an outstanding request rather than overwriting it on every Sync.
        if self
            .pending
            .is_some_and(|(_, e)| t3 - e.t3 < self.timeout_ns)
        {
            return Err("Delay_Req still outstanding");
        }
        let seq = self.next_sequence;
        self.next_sequence = self.next_sequence.wrapping_add(1);
        self.pending = Some((seq, Exchange { t1, t2, t3, t4: 0 }));
        Ok(seq)
    }
    pub fn send_failed(&mut self) {
        self.pending = None;
    }
    pub fn request_origin_estimate(&self) -> Option<i64> {
        let (_, e) = self.pending?;
        e.t1.checked_add(e.t3 - e.t2)
    }
    pub fn response(
        &mut self,
        source: Identity,
        requester: Identity,
        sequence: u16,
        t4: i64,
        now: i64,
    ) -> Result<Exchange, &'static str> {
        if self.master != Some(source) || requester != self.local {
            return Err("wrong master/requester");
        }
        let (seq, mut e) = self.pending.ok_or("no pending Delay_Req")?;
        if seq != sequence || now < e.t3 || now - e.t2 > self.timeout_ns {
            return Err("unmatched/expired Delay_Resp");
        }
        self.pending = None;
        e.t4 = t4;
        Ok(e)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const LOCAL: Identity = Identity {
        uuid: [1, 2, 3, 4, 5, 6],
        port: 1,
    };
    const MASTER: Identity = Identity {
        uuid: [6, 5, 4, 3, 2, 1],
        port: 2,
    };
    #[test]
    fn request_parent_dataset_preserves_own_identity_and_local_role() {
        let mut parent = delay_request(MASTER, 42, &DEFAULT_SUBDOMAIN);
        parent[32] = 0;
        parent[90..92].copy_from_slice(&2u16.to_be_bytes());
        let request =
            delay_request_with_parent(LOCAL, 9, &DEFAULT_SUBDOMAIN, &parent, 42_123_456_789)
                .unwrap();
        assert_eq!(&request[22..28], &LOCAL.uuid);
        assert_eq!(u16_at(&request, 30), 9);
        assert_eq!(request[32], 1);
        assert_eq!(timestamp(&request, 40).unwrap(), 42_123_456_789);
        assert_eq!(&request[102..108], &MASTER.uuid);
        assert_eq!(u16_at(&request, 110), MASTER.port);
        assert_eq!(u16_at(&request, 90), 3);
        assert_eq!(request[95], 255);
        assert!(delay_request_with_parent(LOCAL, 1, &DEFAULT_SUBDOMAIN, &[0; 40], 0).is_err());
    }
    #[test]
    fn wire_follow_up_uses_associated_sequence_not_header_sequence() {
        let mut p = delay_request(MASTER, 99, &DEFAULT_SUBDOMAIN).to_vec();
        p.truncate(52);
        p[20] = 2;
        p[32] = 2;
        p[42..44].copy_from_slice(&7u16.to_be_bytes());
        p[44..48].copy_from_slice(&42u32.to_be_bytes());
        match decode(&p, &DEFAULT_SUBDOMAIN).unwrap() {
            Message::FollowUp {
                associated_sequence,
                origin_ns,
                ..
            } => {
                assert_eq!(associated_sequence, 7);
                assert_eq!(origin_ns, 42_000_000_000);
            }
            _ => panic!(),
        }
        p[48..52].copy_from_slice(&1_000_000_000u32.to_be_bytes());
        assert!(decode(&p, &DEFAULT_SUBDOMAIN).is_err());
    }
    #[test]
    fn matches_four_timestamps_and_rejects_wrong_identity() {
        let mut p = Pairer::new(LOCAL);
        p.sync(MASTER, 9, 1000).unwrap();
        let seq = p.follow_up(MASTER, 9, 100, 1100).unwrap();
        assert!(p.response(MASTER, MASTER, seq, 250, 1200).is_err());
        let e = p.response(MASTER, LOCAL, seq, 250, 1200).unwrap();
        assert_eq!((e.t1, e.t2, e.t3, e.t4), (100, 1000, 1100, 250));
        assert!(p.response(MASTER, LOCAL, seq, 250, 1200).is_err());
        assert!(p.sync(LOCAL, 10, 1300).is_err());
    }
    #[test]
    fn incomplete_expired_and_wrong_sequence_never_produce_exchange() {
        let mut p = Pairer::new(LOCAL);
        assert!(p.follow_up(MASTER, 0, 0, 0).is_err());
        p.sync(MASTER, 65535, 1).unwrap();
        assert!(p.follow_up(MASTER, 0, 1, 10).is_err());
        assert!(p.follow_up(MASTER, 65535, 1, 3_000_000_000).is_err());
        p.sync(MASTER, 0, 3_000_000_001).unwrap();
        let seq = p.follow_up(MASTER, 0, 1, 3_000_000_010).unwrap();
        assert!(p.response(MASTER, LOCAL, seq, 2, 6_000_000_000).is_err());
    }
    #[test]
    fn truncated_and_foreign_domains_are_rejected() {
        for n in 0..40 {
            assert!(decode(&vec![0; n], &DEFAULT_SUBDOMAIN).is_err());
        }
        let p = delay_request(LOCAL, 0, &DEFAULT_SUBDOMAIN);
        assert!(decode(&p, &[0; 16]).is_err());
        assert!(matches!(decode(&p, &DEFAULT_SUBDOMAIN), Ok(Message::Other)));
    }
    #[test]
    fn wire_roundtrip_drives_servo_only_after_complete_exchanges() {
        use crate::servo::{Config, Servo};
        fn stamp(packet: &mut [u8], offset: usize, ns: i64) {
            packet[offset..offset + 4]
                .copy_from_slice(&((ns / 1_000_000_000) as u32).to_be_bytes());
            packet[offset + 4..offset + 8]
                .copy_from_slice(&((ns % 1_000_000_000) as u32).to_be_bytes());
        }
        let mut pairer = Pairer::new(LOCAL);
        let mut servo = Servo::new(Config::default());
        for seq in 0..16u16 {
            let t2 = 10_000_000_000 + seq as i64 * 250_000_000;
            let t3 = t2 + 1_000_000;
            let t1 = 1_700_000_000_000_000_000 + t2 - 50_000;
            let t4 = 1_700_000_000_000_000_000 + t3 + 50_000;
            let mut sync = delay_request(MASTER, seq, &DEFAULT_SUBDOMAIN);
            sync[32] = 0;
            if let Message::Sync { source, sequence } = decode(&sync, &DEFAULT_SUBDOMAIN).unwrap() {
                pairer.sync(source, sequence, t2).unwrap();
            } else {
                panic!();
            }
            let mut fu = sync[..52].to_vec();
            fu[20] = 2;
            fu[32] = 2;
            fu[42..44].copy_from_slice(&seq.to_be_bytes());
            stamp(&mut fu, 44, t1);
            let request = if let Message::FollowUp {
                source,
                associated_sequence,
                origin_ns,
            } = decode(&fu, &DEFAULT_SUBDOMAIN).unwrap()
            {
                pairer
                    .follow_up(source, associated_sequence, origin_ns, t3)
                    .unwrap()
            } else {
                panic!()
            };
            let mut resp = sync[..60].to_vec();
            resp[20] = 2;
            resp[32] = 3;
            stamp(&mut resp, 40, t4);
            resp[50..56].copy_from_slice(&LOCAL.uuid);
            resp[56..58].copy_from_slice(&LOCAL.port.to_be_bytes());
            resp[58..60].copy_from_slice(&request.to_be_bytes());
            if let Message::DelayResponse {
                source,
                requester,
                request_sequence,
                receipt_ns,
            } = decode(&resp, &DEFAULT_SUBDOMAIN).unwrap()
            {
                let e = pairer
                    .response(
                        source,
                        requester,
                        request_sequence,
                        receipt_ns,
                        t3 + 1_000_000,
                    )
                    .unwrap();
                servo.observe(e, t3 + 1_000_000).unwrap();
            } else {
                panic!()
            }
        }
        assert!(servo.status.locked);
        assert_eq!(servo.status.delay_ns, 50_000);
    }
}
