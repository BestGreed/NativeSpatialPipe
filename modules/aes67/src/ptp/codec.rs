//! Default-profile UDP messages with the common IEEE 1588-2008 (v2.0) and
//! IEEE 1588-2019 (v2.1) header and body offsets.
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecodeError {
    TruncatedHeader,
    UnsupportedMajorVersion(u8),
    UnsupportedMinorVersion(u8),
    UnsupportedSdo { major: u8, minor: u8 },
    UnsupportedMessageType(u8),
    InvalidMessageLength,
    InvalidTimestamp,
    MalformedTlv,
}

fn stamp(buf: &[u8]) -> Result<i128, DecodeError> {
    let seconds = u64::from_be_bytes([0, 0, buf[0], buf[1], buf[2], buf[3], buf[4], buf[5]]);
    let ns = u32::from_be_bytes(buf[6..10].try_into().unwrap());
    (ns < 1_000_000_000)
        .then_some(i128::from(seconds) * 1_000_000_000 + i128::from(ns))
        .ok_or(DecodeError::InvalidTimestamp)
}

pub fn decode(buf: &[u8]) -> Result<Message, DecodeError> {
    if buf.len() < 34 {
        return Err(DecodeError::TruncatedHeader);
    }
    let major = buf[1] & 0x0f;
    let minor = (buf[1] >> 4) & 0x0f;
    if major != 2 {
        return Err(DecodeError::UnsupportedMajorVersion(major));
    }
    if !matches!(minor, 0 | 1) {
        return Err(DecodeError::UnsupportedMinorVersion(minor));
    }
    // The v2.0 transportSpecific nibble becomes majorSdoId in v2.1.
    // Byte 5 is reserved in v2.0 and carries minorSdoId in v2.1.
    let major_sdo = buf[0] >> 4;
    let minor_sdo = if minor == 1 { buf[5] } else { 0 };
    if major_sdo != 0 || minor_sdo != 0 {
        return Err(DecodeError::UnsupportedSdo {
            major: major_sdo,
            minor: minor_sdo,
        });
    }
    let kind = buf[0] & 0x0f;
    let body_end = match kind {
        0 | 1 | 8 => 44,
        9 => 54,
        11 => 64,
        _ => return Err(DecodeError::UnsupportedMessageType(kind)),
    };
    let length = usize::from(u16::from_be_bytes([buf[2], buf[3]]));
    if length < body_end || length > buf.len() {
        return Err(DecodeError::InvalidMessageLength);
    }
    let b = &buf[..length];
    // All supported messages have fixed base bodies. Skip well-formed trailing
    // TLVs without assigning profile-specific meaning to their contents.
    let mut tlvs = &b[body_end..];
    while !tlvs.is_empty() {
        if tlvs.len() < 4 {
            return Err(DecodeError::MalformedTlv);
        }
        let value_len = usize::from(u16::from_be_bytes([tlvs[2], tlvs[3]]));
        if value_len % 2 != 0 || value_len > tlvs.len() - 4 {
            return Err(DecodeError::MalformedTlv);
        }
        tlvs = &tlvs[4 + value_len..];
    }
    let body = match kind {
        0 => Body::Sync,
        1 => Body::DelayReq(stamp(&b[34..44])?),
        8 => Body::FollowUp(stamp(&b[34..44])?),
        9 => Body::DelayResp {
            received_ns: stamp(&b[34..44])?,
            requester: b[44..54].try_into().unwrap(),
        },
        11 => Body::Announce {
            grandmaster: b[53..61].try_into().unwrap(),
        },
        _ => unreachable!(),
    };
    Ok(Message {
        domain: b[4],
        source: b[20..30].try_into().unwrap(),
        seq: u16::from_be_bytes(b[30..32].try_into().unwrap()),
        flags: u16::from_be_bytes(b[6..8].try_into().unwrap()),
        correction_scaled_ns: i64::from_be_bytes(b[8..16].try_into().unwrap()),
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
            assert_eq!(wire[1], 0x02);
            assert_eq!(&wire[8..16], &(-65_535_i64).to_be_bytes());
            let mut wire = wire.to_vec();
            for version in [0x02, 0x12] {
                wire[1] = version;
                assert_eq!(decode(&wire), Ok(msg));
                assert_eq!(
                    decode(&wire[..wire.len() - 1]),
                    Err(DecodeError::InvalidMessageLength)
                );
            }
        }
    }

    #[test]
    fn codec_reports_unsupported_major_and_minor_versions() {
        let mut wire = [0; 44];
        wire[2..4].copy_from_slice(&44_u16.to_be_bytes());
        for (version, err) in [
            (0x01, DecodeError::UnsupportedMajorVersion(1)),
            (0x13, DecodeError::UnsupportedMajorVersion(3)),
            (0x22, DecodeError::UnsupportedMinorVersion(2)),
        ] {
            wire[1] = version;
            assert_eq!(decode(&wire), Err(err));
        }
    }

    #[test]
    fn v21_sdo_and_tlv_handling_preserves_announce_body() {
        let msg = Message {
            domain: 19,
            source: [7; 10],
            seq: 0,
            flags: PTP_TIMESCALE,
            correction_scaled_ns: 0,
            body: Body::Announce {
                grandmaster: [0x12; 8],
            },
        };
        let mut buf = [0; 64];
        let mut wire = encode(msg, &mut buf).to_vec();
        wire[1] = 0x12;
        // A Path Trace TLV carries one clock identity here.
        wire.extend([0, 8, 0, 8]);
        wire.extend([9; 8]);
        wire[2..4].copy_from_slice(&76_u16.to_be_bytes());
        assert_eq!(decode(&wire), Ok(msg));
        wire[5] = 1;
        assert_eq!(
            decode(&wire),
            Err(DecodeError::UnsupportedSdo { major: 0, minor: 1 })
        );
        wire[5] = 0;
        wire[67] = 10;
        assert_eq!(decode(&wire), Err(DecodeError::MalformedTlv));
    }
}
