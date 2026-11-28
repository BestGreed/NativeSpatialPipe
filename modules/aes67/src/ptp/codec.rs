//! Default-profile UDP messages with the common IEEE 1588-2008 (v2.0) and
//! IEEE 1588-2019 (v2.1) header and body offsets.
//!
//! IEEE 1588-2008 clause 13.3 fixes the message layouts used here:
//! 13.3.1 Announce (64 bytes, 34 byte header + 30 byte body), 13.3.2 Sync
//! (44 bytes), 13.3.3 Follow_Up (44), 13.3.4 Delay_Req (44) and 13.3.5
//! Delay_Resp (54). The local LinuxPTP mirror (`docs/source/linuxptp-msg.h`)
//! corroborates the offsets and field order used below.
pub type ClockId = [u8; 8];
pub type PortId = [u8; 10];

/// Announce data set as carried by an Announce message (IEEE 1588-2008
/// 13.3.1 table 26): the fields compared by the BMCA (9.3.2) plus the
/// timePropertiesDS subset (IEEE 1588-2008 8.2.3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Announce {
    pub origin_ns: i128,
    pub current_utc_offset: i16,
    pub priority1: u8,
    pub clock_class: u8,
    pub clock_accuracy: u8,
    pub offset_scaled_log_variance: u16,
    pub priority2: u8,
    pub grandmaster: ClockId,
    pub steps_removed: u16,
    pub time_source: u8,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Body {
    Sync,
    DelayReq(i128),
    FollowUp(i128),
    DelayResp {
        received_ns: i128,
        requester: PortId,
    },
    Announce(Announce),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Message {
    pub domain: u8,
    pub source: PortId,
    pub seq: u16,
    pub flags: u16,
    pub log_interval: i8,
    pub correction_scaled_ns: i64,
    pub body: Body,
}
pub const TWO_STEP: u16 = 0x0200;
pub const UNICAST: u16 = 0x0400;
pub const PTP_TIMESCALE: u16 = 0x0008;
/// IEEE 1588-2008 flagField bits as named by the local LinuxPTP mirror
/// (`docs/source/linuxptp-msg.h`): currentUtcOffsetValid 0x0004,
/// timeTraceable 0x0010 and frequencyTraceable 0x0020.
pub const CURRENT_UTC_OFFSET_VALID: u16 = 0x0004;
pub const TIME_TRACEABLE: u16 = 0x0010;
pub const FREQUENCY_TRACEABLE: u16 = 0x0020;

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
        11 => Body::Announce(Announce {
            origin_ns: stamp(&b[34..44])?,
            current_utc_offset: i16::from_be_bytes(b[44..46].try_into().unwrap()),
            priority1: b[47],
            clock_class: b[48],
            clock_accuracy: b[49],
            offset_scaled_log_variance: u16::from_be_bytes(b[50..52].try_into().unwrap()),
            priority2: b[52],
            grandmaster: b[53..61].try_into().unwrap(),
            steps_removed: u16::from_be_bytes(b[61..63].try_into().unwrap()),
            time_source: b[63],
        }),
        _ => unreachable!(),
    };
    Ok(Message {
        domain: b[4],
        source: b[20..30].try_into().unwrap(),
        seq: u16::from_be_bytes(b[30..32].try_into().unwrap()),
        flags: u16::from_be_bytes(b[6..8].try_into().unwrap()),
        log_interval: b[33] as i8,
        correction_scaled_ns: i64::from_be_bytes(b[8..16].try_into().unwrap()),
        body,
    })
}

fn put_stamp(buf: &mut [u8], ns: i128) {
    buf[..6].copy_from_slice(&((ns / 1_000_000_000) as u64).to_be_bytes()[2..]);
    buf[6..10].copy_from_slice(&((ns % 1_000_000_000) as u32).to_be_bytes());
}

/// Encode a message. The buffer must be at least 64 bytes (the largest
/// default-profile body, Announce).
pub fn encode(msg: Message, b: &mut [u8; 64]) -> &[u8] {
    b.fill(0);
    let (ty, control, length) = match msg.body {
        Body::Sync => (0, 0, 44_u16),
        Body::DelayReq(_) => (1, 1, 44),
        Body::FollowUp(_) => (8, 2, 44),
        Body::DelayResp { .. } => (9, 3, 54),
        Body::Announce(_) => (11, 5, 64),
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
    b[33] = msg.log_interval as u8;
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
        Body::Announce(announce) => {
            put_stamp(&mut b[34..44], announce.origin_ns);
            b[44..46].copy_from_slice(&announce.current_utc_offset.to_be_bytes());
            // b[46] is reserved (IEEE 1588-2008 13.3.1).
            b[47] = announce.priority1;
            b[48] = announce.clock_class;
            b[49] = announce.clock_accuracy;
            b[50..52].copy_from_slice(&announce.offset_scaled_log_variance.to_be_bytes());
            b[52] = announce.priority2;
            b[53..61].copy_from_slice(&announce.grandmaster);
            b[61..63].copy_from_slice(&announce.steps_removed.to_be_bytes());
            b[63] = announce.time_source;
        }
    }
    &b[..length as usize]
}

impl Announce {
    /// Validate a received Announce for use as a foreign master after the
    /// domain/socket tests (IEEE 1588-2008 9.3.2.5 and AES67 Annex A):
    /// grandmasterIdentity zero is never a real GM, stepsRemoved is bounded
    /// by 255 and the time source must not be 0 (reserved).
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.grandmaster == [0; 8] {
            return Err("zero_grandmaster_identity");
        }
        if self.steps_removed > 255 {
            return Err("steps_removed");
        }
        if self.time_source == 0 {
            return Err("time_source");
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn announce(grandmaster: ClockId) -> Announce {
        Announce {
            origin_ns: 1_234_567_890,
            current_utc_offset: 37,
            priority1: 128,
            clock_class: 248,
            clock_accuracy: 0xfe,
            offset_scaled_log_variance: 0xffff,
            priority2: 129,
            grandmaster,
            steps_removed: 0,
            time_source: 0xa0,
        }
    }

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
            Body::Announce(announce([8; 8])),
        ] {
            let msg = Message {
                log_interval: -2,
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
    fn announce_fields_round_trip_at_the_ieee_1588_offsets() {
        let announce = announce([0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc]);
        let msg = Message {
            log_interval: 1,
            domain: 0,
            source: [0x22; 10],
            seq: 7,
            flags: PTP_TIMESCALE,
            correction_scaled_ns: 0,
            body: Body::Announce(announce),
        };
        let mut bytes = [0; 64];
        let wire = encode(msg, &mut bytes);
        assert_eq!(wire.len(), 64);
        assert_eq!(wire[32], 5, "Announce controlField is 5 (13.3.1)");
        assert_eq!(u16::from_be_bytes([wire[2], wire[3]]), 64);
        // IEEE 1588-2008 table 26 offsets.
        assert_eq!(wire[47], 128, "priority1");
        assert_eq!(wire[48], 248, "clockClass");
        assert_eq!(wire[49], 0xfe, "clockAccuracy");
        assert_eq!(&wire[50..52], &[0xff, 0xff], "offsetScaledLogVariance");
        assert_eq!(wire[52], 129, "priority2");
        assert_eq!(&wire[53..61], &announce.grandmaster, "grandmasterIdentity");
        assert_eq!(&wire[61..63], &[0, 0], "stepsRemoved");
        assert_eq!(wire[63], 0xa0, "timeSource");
        assert_eq!(decode(wire).unwrap().body, Body::Announce(announce));
    }

    #[test]
    fn announce_validation_rejects_a_non_dataset_body() {
        let mut bad = announce([1; 8]);
        bad.grandmaster = [0; 8];
        assert_eq!(bad.validate(), Err("zero_grandmaster_identity"));
        bad = announce([1; 8]);
        bad.steps_removed = 256;
        assert_eq!(bad.validate(), Err("steps_removed"));
        bad = announce([1; 8]);
        bad.time_source = 0;
        assert_eq!(bad.validate(), Err("time_source"));
        assert!(announce([1; 8]).validate().is_ok());
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
            log_interval: -2,
            domain: 19,
            source: [7; 10],
            seq: 0,
            flags: PTP_TIMESCALE,
            correction_scaled_ns: 0,
            body: Body::Announce(announce([0x12; 8])),
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
