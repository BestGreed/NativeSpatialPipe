//! The subset of the IEEE 1588-2008 UDP message format used by the follower.
pub type ClockId = [u8; 8];
pub type PortId = [u8; 10];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Body {
    Sync,
    DelayReq(i128),
    FollowUp(i128),
    DelayResp {
        received_ns: i128,
        requester: PortId,
    },
    Announce {
        grandmaster: ClockId,
    },
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Message {
    pub domain: u8,
    pub source: PortId,
    pub seq: u16,
    pub flags: u16,
    pub correction_scaled_ns: i64,
    pub body: Body,
}
pub const TWO_STEP: u16 = 0x0200;
pub const UNICAST: u16 = 0x0400;
pub const PTP_TIMESCALE: u16 = 0x0008;

fn stamp(buf: &[u8]) -> Option<i128> {
    let seconds = u64::from_be_bytes([0, 0, buf[0], buf[1], buf[2], buf[3], buf[4], buf[5]]);
    let ns = u32::from_be_bytes(buf[6..10].try_into().ok()?);
    (ns < 1_000_000_000).then_some(i128::from(seconds) * 1_000_000_000 + i128::from(ns))
}

pub fn decode(buf: &[u8]) -> Option<Message> {
    if buf.len() < 34 || buf[0] >> 4 != 0 || buf[1] != 2 {
        return None;
    }
    let length = u16::from_be_bytes(buf[2..4].try_into().ok()?) as usize;
    let b = buf.get(..length)?;
    let body = match buf[0] & 0x0f {
        0 if length >= 44 => Body::Sync,
        1 if length >= 44 => Body::DelayReq(stamp(&b[34..44])?),
        8 if length >= 44 => Body::FollowUp(stamp(&b[34..44])?),
        9 if length >= 54 => Body::DelayResp {
            received_ns: stamp(&b[34..44])?,
            requester: b[44..54].try_into().ok()?,
        },
        11 if length >= 64 => Body::Announce {
            grandmaster: b[53..61].try_into().ok()?,
        },
        _ => return None,
    };
    Some(Message {
        domain: b[4],
        source: b[20..30].try_into().ok()?,
        seq: u16::from_be_bytes(b[30..32].try_into().ok()?),
        flags: u16::from_be_bytes(b[6..8].try_into().ok()?),
        correction_scaled_ns: i64::from_be_bytes(b[8..16].try_into().ok()?),
        body,
    })
}

fn put_stamp(buf: &mut [u8], ns: i128) {
    buf[..6].copy_from_slice(&((ns / 1_000_000_000) as u64).to_be_bytes()[2..]);
    buf[6..10].copy_from_slice(&((ns % 1_000_000_000) as u32).to_be_bytes());
}

pub fn encode(msg: Message, b: &mut [u8; 64]) -> &[u8] {
    b.fill(0);
    let (ty, control, length, loginterval) = match msg.body {
        Body::Sync => (0, 0, 44_u16, 0),
        Body::DelayReq(_) => (1, 1, 44, 0x7f),
        Body::FollowUp(_) => (8, 2, 44, 0),
        Body::DelayResp { .. } => (9, 3, 54, 0x7f),
        Body::Announce { .. } => (11, 5, 64, 1),
    };
    b[0] = ty;
    b[1] = 2;
    b[2..4].copy_from_slice(&length.to_be_bytes());
    b[4] = msg.domain;
    b[6..8].copy_from_slice(&msg.flags.to_be_bytes());
    b[8..16].copy_from_slice(&msg.correction_scaled_ns.to_be_bytes());
    b[20..30].copy_from_slice(&msg.source);
    b[30..32].copy_from_slice(&msg.seq.to_be_bytes());
    b[32] = control;
    b[33] = loginterval;
    match msg.body {
        Body::Sync => (),
        Body::DelayReq(ns) | Body::FollowUp(ns) => put_stamp(&mut b[34..44], ns),
        Body::DelayResp {
            received_ns,
            requester,
        } => {
            put_stamp(&mut b[34..44], received_ns);
            b[44..54].copy_from_slice(&requester);
        }
        Body::Announce { grandmaster } => b[53..61].copy_from_slice(&grandmaster),
    }
    &b[..length as usize]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn codec_preserves_wire_fields_and_bounds() {
        let received_ns = 1_900_000_000_123_456_789;
        for body in [
            Body::Sync,
            Body::FollowUp(received_ns),
            Body::DelayReq(0),
            Body::DelayResp {
                received_ns,
                requester: [9; 10],
            },
            Body::Announce {
                grandmaster: [8; 8],
            },
        ] {
            let msg = Message {
                domain: 42,
                source: [3; 10],
                seq: u16::MAX,
                flags: TWO_STEP | PTP_TIMESCALE,
                correction_scaled_ns: -65_535,
                body,
            };
            let mut bytes = [0; 64];
            let wire = encode(msg, &mut bytes);
            assert_eq!(decode(wire), Some(msg));
            assert_eq!(decode(&wire[..wire.len() - 1]), None);
            assert_eq!(&wire[8..16], &(-65_535_i64).to_be_bytes());
        }
    }
}
