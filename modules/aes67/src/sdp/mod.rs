use crate::{net::multicast_interface, ptp::codec::ClockId, rtp::Output, time::PacketTiming};
use std::{
    fmt::Write,
    io,
    net::{Ipv4Addr, UdpSocket},
};

/// Clock reference signalled for a stream (DESIGN-TOPO.md section 5).
///
/// Ptp: a=clock-domain:PTPv2 <domain> + a=ts-refclk:ptp=IEEE1588-2008:<GMID>:<domain>
/// + a=mediaclk:direct=0. Local: a=ts-refclk:local + a=mediaclk:direct=0 (mode C
/// or an auto/gm fallback before PTP; RFC 7273 4.6/4.8, a local reference is
/// rate-only, no time-sync assurance). ts-refclk:localmac is never emitted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reference {
    Ptp { gm: ClockId, domain: u8 },
    Local,
}

/// 16-bit CRC used as the SAP message identifier hash. The arithmetic matches
/// the daemon crc16 (utils.cpp) so our announcements and a daemon browser are
/// keyed the same way; the value changes whenever the SDP changes, as RFC 2974
/// clause 3.2 requires (hash MUST change when the description is modified).
pub fn crc16(bytes: &[u8]) -> u16 {
    let mut crc: u16 = 0xFFFF;
    for &byte in bytes {
        let mut value = (crc >> 8) as u8 ^ byte;
        value ^= value >> 4;
        crc = (crc << 8) ^ (u16::from(value) << 12) ^ (u16::from(value) << 5) ^ u16::from(value);
    }
    crc
}

/// SDP ptime is in milliseconds (AES67-2023 sec 8.2 line 1695; RFC 8866).
/// 48 kHz-family values are exact (0.125, 1, 4); the 44.1 kHz family reports
/// the actual packet duration (48/44100 s = 1.088 ms) rounded to two decimals,
/// so a 1 ms-profile packet is a=ptime:1.09 (AES67-2023 Table 2 line 1532).
pub fn ptime_milliseconds(timing: PacketTiming) -> String {
    if timing.sample_rate() % 48_000 == 0 {
        let microseconds = timing.packet_time_us();
        if microseconds == 333 {
            // Table 2 line 1521-1527 names 333 1/3 us; 16/48000 s is a
            // repeating decimal, so print six fractional digits.
            return "0.333333".into();
        }
        if microseconds % 1_000 == 0 {
            return format!("{}", microseconds / 1_000);
        }
        return format!("{}", f64::from(microseconds) / 1_000.0);
    }
    let actual = f64::from(timing.samples_per_packet()) * 1_000.0 / f64::from(timing.sample_rate());
    let mut text = format!("{actual:.2}");
    if text.ends_with('0') {
        text.pop();
    }
    text
}

/// SDP o= username (RFC 8866 sec 5.2). The field is a single token: printable
/// ASCII is kept, while spaces, non-ASCII and control characters become '-'.
/// Capped at 64 characters and never empty, so a configured device name
/// cannot split or inject lines in the origin field.
fn origin_username(name: &str) -> String {
    let mut token: String = name
        .chars()
        .take(64)
        .map(|value| if value.is_ascii_graphic() { value } else { '-' })
        .collect();
    if token.is_empty() {
        token.push('-');
    }
    token
}

/// Media-level i= value (RFC 8866 sec 5.4). Spaces are legal here, but CR, LF
/// and every other control character are dropped so a configured name cannot
/// start a new SDP line; 200 characters keeps the line inside the RFC 8866
/// sec 5 recommendation.
fn media_information(name: &str) -> String {
    name.chars()
        .filter(|value| !value.is_control())
        .take(200)
        .collect()
}

/// ST 2110-30 channel-order groups for one flow's exact label sequence.
///
/// A mapping is accepted only when the labels start with the standard group's
/// exact sequence (longest group first); the remainder of 1..=8 channels is
/// described as a user-defined group. A sequence without a standard prefix is
/// described as one user-defined group when it has 1..=8 channels. Anything
/// else is omitted rather than guessed: a wrong channel-order makes a
/// receiver decode the wrong speakers, so silence is safer (DESIGN-TOPO.md).
fn channel_order(labels: &[String]) -> Option<String> {
    // Longest first: 7.1 starts with 5.1, which starts with the stereo pair.
    const PREFIXES: [(&str, &[&str]); 6] = [
        ("71", &["L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"]),
        ("51", &["L", "R", "C", "LFE", "Ls", "Rs"]),
        ("ST", &["L", "R"]),
        ("ST", &["FL", "FR"]),
        ("M", &["M"]),
        ("M", &["C"]),
    ];
    for (group, prefix) in PREFIXES {
        let prefix_matches = labels.len() >= prefix.len()
            && labels
                .iter()
                .zip(prefix)
                .all(|(label, expected)| label.as_str() == *expected);
        if !prefix_matches {
            continue;
        }
        let remaining = labels.len() - prefix.len();
        return match remaining {
            0 => Some(group.to_owned()),
            1..=8 => Some(format!("{group},U{remaining:02}")),
            _ => None,
        };
    }
    (1..=8)
        .contains(&labels.len())
        .then(|| format!("U{:02}", labels.len()))
}

#[allow(clippy::too_many_arguments)]
fn media_block(
    s: &mut String,
    name: &str,
    index: usize,
    output: &Output,
    timing: PacketTiming,
    reference: Option<Reference>,
    port: u16,
    mid: Option<u8>,
    channel_order: Option<&str>,
) {
    let flow = &output.flow;
    let dest = flow.destination.ip();
    let pt = flow.payload_type;
    // Media-level information line immediately after m= (RFC 8866 sec 5.4),
    // carrying the device name and which configured flow this is.
    let information = media_information(name);
    let _ = write!(
        s,
        "m=audio {port} RTP/AVP {pt}\r\ni={information} flow{index} IN/{}\r\na=rtpmap:{pt} {}/{}/{}\r\n",
        flow.channels,
        output.encoding.codec(),
        timing.sample_rate(),
        flow.channels,
    );
    if let Some(order) = channel_order {
        let _ = write!(s, "a=fmtp:{pt} channel-order=SMPTE2110.({order})\r\n");
    }
    let _ = write!(s, "a=sendonly\r\na=ptime:{}\r\n", ptime_milliseconds(timing));
    if dest.is_multicast() && flow.source_filter {
        let _ = write!(
            s,
            "a=source-filter: incl IN IP4 {dest} {}\r\n",
            output.source
        );
    }
    // RAVENNA/ST 2110 conventions: sync-time and framecount are not required
    // by AES67 (they are emitted for RAVENNA-style parsers); clock-domain is a
    // ST 2110 attribute, emitted here as an extension.
    s.push_str("a=sync-time:0\r\n");
    let _ = write!(s, "a=framecount:{}\r\n", timing.samples_per_packet());
    match reference {
        // AES67-2023 sec 8.2 line 1784-1786: when a specific GM is indicated,
        // both GMID and PTP domain are signalled. The epoch is shared, so the
        // media clock is direct=0 (AES67 sec 8.3 line 1807-1809).
        Some(Reference::Ptp { gm, domain }) => {
            let _ = write!(s, "a=clock-domain:PTPv2 {domain}\r\n");
            s.push_str("a=ts-refclk:ptp=IEEE1588-2008:");
            for (i, octet) in gm.into_iter().enumerate() {
                if i != 0 {
                    s.push('-');
                }
                let _ = write!(s, "{octet:02X}");
            }
            let _ = write!(s, ":{domain}\r\na=mediaclk:direct=0\r\n");
        }
        // RFC 7273 sec 4.6/4.8 (line 429-436) and sec 6 (line 1076-1078): a
        // local reference clock means rate synchronisation only. This is the
        // deliberate mode-C EXTENSION deviation from AES67 sec 4/5.
        Some(Reference::Local) => {
            s.push_str("a=ts-refclk:local\r\na=mediaclk:direct=0\r\n");
        }
        None => {}
    }
    if let Some(mid) = mid {
        let _ = write!(s, "a=mid:{mid}\r\n");
    }
}

/// Serializes one RTP flow as one v=0 session. With ST 2022-7 redundancy the
/// session carries two media sections (primary and secondary port) under
/// a=group:DUP, matching the daemon source layout. Every media section carries
/// i= with the device name and flow index, plus a=fmtp:<pt> channel-order
/// when the flow's exact label sequence identifies the ST 2110-30 group.
#[allow(clippy::too_many_arguments)]
pub fn describe(
    name: &str,
    output: &Output,
    index: usize,
    labels: &[String],
    reference: Option<Reference>,
    session_id: u64,
    version: u64,
    timing: PacketTiming,
    secondary_port: Option<u16>,
) -> String {
    let flow = &output.flow;
    let dest = flow.destination.ip();
    let mut s = format!(
        "v=0\r\no={} {session_id} {version} IN IP4 {}\r\ns={name} flow{index}\r\n",
        origin_username(name),
        output.source
    );
    let _ = write!(s, "c=IN IP4 {dest}");
    if dest.is_multicast() {
        let _ = write!(s, "/{}", flow.ttl);
    }
    s.push_str("\r\nt=0 0\r\n");
    if secondary_port.is_some() {
        s.push_str("a=group:DUP 1 2\r\n");
    }
    let labels = &labels[flow.offset..flow.offset + flow.channels];
    let order = channel_order(labels);
    media_block(
        &mut s,
        name,
        index,
        output,
        timing,
        reference,
        flow.destination.port(),
        secondary_port.map(|_| 1),
        order.as_deref(),
    );
    if let Some(secondary) = secondary_port {
        media_block(
            &mut s,
            name,
            index,
            output,
            timing,
            reference,
            secondary,
            Some(2),
            order.as_deref(),
        );
    }
    let _ = write!(
        s,
        "a=comment:channel-labels={}\r\na=comment:flow={index}\r\na=comment:channel-offset={}\r\n",
        labels.join(","),
        flow.offset
    );
    s
}

/// Minimal SAP publisher, one announcement identity and socket per RTP flow.
pub struct Sap {
    socket: UdpSocket,
    source: Ipv4Addr,
    destination: Ipv4Addr,
}
impl Sap {
    pub fn open(output: &Output, destination: Ipv4Addr) -> io::Result<Self> {
        let socket = UdpSocket::bind((output.source, 0))?;
        multicast_interface(&socket, output.source)?;
        // RFC 2974 clause 3.1: SAP announcements SHOULD use IP TTL 255 and
        // MUST use port 9875 (line 135-137); TTL scoping is discouraged.
        socket.set_multicast_ttl_v4(255)?;
        socket.set_nonblocking(true)?;
        Ok(Self {
            socket,
            source: output.source,
            destination,
        })
    }
    pub fn publish(&self, hash: u16, sdp: &str, delete: bool) -> io::Result<()> {
        let packet = sap_packet(hash, self.source, sdp, delete);
        if self.socket.send_to(&packet, (self.destination, 9875))? != packet.len() {
            return Err(io::ErrorKind::WriteZero.into());
        }
        Ok(())
    }
}

fn sap_packet(hash: u16, source: Ipv4Addr, sdp: &str, delete: bool) -> Vec<u8> {
    let mut packet = Vec::with_capacity(24 + sdp.len());
    packet.extend_from_slice(&[if delete { 0x24 } else { 0x20 }, 0]);
    packet.extend_from_slice(&hash.to_be_bytes());
    packet.extend_from_slice(&source.octets());
    packet.extend_from_slice(b"application/sdp\0");
    if delete {
        // RFC 2974 clause 4: the deletion payload is the origin line only.
        if let Some(origin) = sdp.lines().find(|line| line.starts_with("o=")) {
            packet.extend_from_slice(origin.as_bytes());
            packet.extend_from_slice(b"\r\n");
        }
    } else {
        packet.extend_from_slice(sdp.as_bytes());
    }
    packet
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::rtp::{Encoding, Flow};
    use crate::time::PacketTiming;
    use std::net::SocketAddrV4;

    fn output(port: u16, channels: usize, encoding: Encoding, source_filter: bool) -> Output {
        let ip = Ipv4Addr::LOCALHOST;
        Output::open(
            Flow {
                destination: SocketAddrV4::new(ip, port),
                interface: ip,
                ttl: 32,
                ssrc: 1,
                offset: 1,
                channels,
                payload_type: 110,
                source_filter,
            },
            encoding,
        )
        .unwrap()
    }

    /// Flow whose label window starts at `offset`: lets one config's labels be
    /// described as several flows, as the session does with flow.offset.
    fn output_at(offset: usize, channels: usize) -> Output {
        let ip = Ipv4Addr::LOCALHOST;
        Output::open(
            Flow {
                destination: SocketAddrV4::new(ip, 5004),
                interface: ip,
                ttl: 32,
                ssrc: 1,
                offset,
                channels,
                payload_type: 110,
                source_filter: false,
            },
            Encoding::L24,
        )
        .unwrap()
    }

    fn label_list(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    fn reference() -> Option<Reference> {
        Some(Reference::Ptp {
            gm: [0xab; 8],
            domain: 42,
        })
    }

    #[test]
    fn one_session_per_flow_with_honest_clock_and_labels() {
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let output = output(5004, 2, Encoding::L24, false);
        let labels = ["other".to_owned(), "FL".to_owned(), "FR".to_owned()];
        let unlocked = describe("test", &output, 0, &labels, None, 15, 1, timing, None);
        assert!(!unlocked.contains("a=ts-refclk:"));
        assert!(!unlocked.contains("a=clock-domain:"));
        let s = describe(
            "test",
            &output,
            0,
            &labels,
            reference(),
            15,
            1,
            timing,
            None,
        );
        assert_eq!(s.matches("v=0\r\n").count(), 1);
        assert!(s.contains("m=audio 5004 RTP/AVP 110\r\ni=test flow0 IN/2\r\n"));
        assert!(s.contains("a=rtpmap:110 L24/48000/2\r\n"));
        // AES67 requires ptime; the value is milliseconds (line 1695).
        assert!(s.contains("a=ptime:1\r\n"));
        assert!(s.contains("a=sync-time:0\r\n"));
        assert!(s.contains("a=framecount:48\r\n"));
        assert!(s.contains(
            "a=clock-domain:PTPv2 42\r\na=ts-refclk:ptp=IEEE1588-2008:AB-AB-AB-AB-AB-AB-AB-AB:42\r\na=mediaclk:direct=0\r\n"
        ));
        assert!(s.contains("a=comment:channel-labels=FL,FR\r\n"));
        assert!(s.contains("channel-order=SMPTE2110.(ST)"));
        let packet = sap_packet(0x1234, Ipv4Addr::LOCALHOST, &s, false);
        assert_eq!(&packet[..8], &[0x20, 0, 0x12, 0x34, 127, 0, 0, 1]);
        let stop = sap_packet(0x1234, Ipv4Addr::LOCALHOST, &s, true);
        assert_eq!(stop[0], 0x24);
        assert!(s.contains("o=test 15 1 IN IP4 127.0.0.1\r\n"));
        assert!(stop.ends_with(b"o=test 15 1 IN IP4 127.0.0.1\r\n"));
    }

    #[test]
    fn sdp_rtpmap_ptime_and_framecount_follow_rate_encoding_and_packet_time() {
        // (rate, ptime us, encoding, channels, expected rtpmap, expected ptime,
        //  expected framecount)
        let cases = [
            (48_000, 1_000, Encoding::L24, 8, "L24/48000/8", "1", "48"),
            (48_000, 1_000, Encoding::L16, 8, "L16/48000/8", "1", "48"),
            (48_000, 125, Encoding::L24, 8, "L24/48000/8", "0.125", "6"),
            (48_000, 250, Encoding::L24, 8, "L24/48000/8", "0.25", "12"),
            (48_000, 500, Encoding::L24, 8, "L24/48000/8", "0.5", "24"),
            (48_000, 2_000, Encoding::L24, 8, "L24/48000/8", "2", "96"),
            (48_000, 4_000, Encoding::L24, 8, "L24/48000/8", "4", "192"),
            (
                48_000,
                333,
                Encoding::L24,
                8,
                "L24/48000/8",
                "0.333333",
                "16",
            ),
            (44_100, 333, Encoding::L16, 8, "L16/44100/8", "0.36", "16"),
            (44_100, 1_000, Encoding::L16, 8, "L16/44100/8", "1.09", "48"),
            (
                44_100,
                4_000,
                Encoding::L16,
                8,
                "L16/44100/8",
                "4.35",
                "192",
            ),
            (96_000, 1_000, Encoding::L24, 8, "L24/96000/8", "1", "96"),
            (96_000, 125, Encoding::L24, 8, "L24/96000/8", "0.125", "12"),
            (192_000, 125, Encoding::L24, 16, "L24/192000/16", "0.125", "24"),
            // Extension mode single-flow 16ch at short packet times: rtpmap
            // carries the actual channel count and ptime the actual duration.
            (48_000, 125, Encoding::L24, 16, "L24/48000/16", "0.125", "6"),
            (48_000, 250, Encoding::L24, 16, "L24/48000/16", "0.25", "12"),
            (192_000, 1_000, Encoding::L16, 2, "L16/192000/2", "1", "192"),
        ];
        for (rate, ptime, encoding, channels, rtpmap, ptime_text, framecount) in cases {
            let timing = PacketTiming::new(rate, ptime).unwrap();
            let output = output(5004, channels, encoding, false);
            let s = describe(
                "test",
                &output,
                0,
                &vec!["CH".into(); channels + 1],
                reference(),
                1,
                1,
                timing,
                None,
            );
            assert!(
                s.contains(&format!("a=rtpmap:110 {rtpmap}\r\n")),
                "{rate}/{ptime}/{encoding:?}: {s}"
            );
            assert!(
                s.contains(&format!("a=ptime:{ptime_text}\r\n")),
                "{rate}/{ptime}: {s}"
            );
            assert!(
                s.contains(&format!("a=framecount:{framecount}\r\n")),
                "{rate}/{ptime}: {s}"
            );
        }
    }

    #[test]
    fn redundant_session_duplicates_media_under_group_dup() {
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let output = output(5004, 8, Encoding::L24, false);
        let labels = vec!["CH".to_owned(); 9];
        let s = describe(
            "test",
            &output,
            0,
            &labels,
            reference(),
            1,
            1,
            timing,
            Some(5006),
        );
        assert!(s.contains("a=group:DUP 1 2\r\n"));
        assert!(s.contains("m=audio 5004 RTP/AVP 110\r\n"));
        assert!(s.contains("m=audio 5006 RTP/AVP 110\r\n"));
        assert_eq!(s.matches("a=rtpmap:110 L24/48000/8\r\n").count(), 2);
        assert!(s.contains("a=mid:1\r\n"));
        assert!(s.contains("a=mid:2\r\n"));
    }

    #[test]
    fn source_filter_is_emitted_for_multicast_when_enabled() {
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let ip = Ipv4Addr::LOCALHOST;
        let output = Output::open(
            Flow {
                destination: SocketAddrV4::new(Ipv4Addr::new(239, 69, 0, 1), 5004),
                interface: ip,
                ttl: 32,
                ssrc: 1,
                offset: 0,
                channels: 2,
                payload_type: 96,
                source_filter: true,
            },
            Encoding::L24,
        )
        .unwrap();
        let s = describe(
            "test",
            &output,
            0,
            &["L".into(), "R".into()],
            reference(),
            1,
            1,
            timing,
            None,
        );
        assert!(s.contains("c=IN IP4 239.69.0.1/32\r\n"));
        assert!(s.contains("a=source-filter: incl IN IP4 239.69.0.1 127.0.0.1\r\n"));
    }

    #[test]
    fn sap_hash_is_a_crc16_of_the_current_description() {
        assert_eq!(crc16(b"123456789"), 0x29B1);
        assert_eq!(crc16(b"v=0\r\no=- 1 1 IN IP4 192.0.2.1\r\n"), 0xF2FF);
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let output = output(5004, 2, Encoding::L24, false);
        let labels = ["other".to_owned(), "L".to_owned(), "R".to_owned()];
        let first = describe("test", &output, 0, &labels, reference(), 1, 1, timing, None);
        let second = describe("test", &output, 0, &labels, reference(), 1, 2, timing, None);
        assert_ne!(crc16(first.as_bytes()), crc16(second.as_bytes()));
        assert_ne!(crc16(first.as_bytes()), 0);
    }

    #[test]
    fn clock_reference_lines_are_exact_per_role() {
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let output = output(5004, 2, Encoding::L24, false);
        let labels = ["other".to_owned(), "FL".to_owned(), "FR".to_owned()];
        // DESIGN-TOPO.md section 7.5: slave-locked / gm-active exact lines.
        let gm = [0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc];
        let ptp = describe(
            "test",
            &output,
            0,
            &labels,
            Some(Reference::Ptp { gm, domain: 19 }),
            1,
            1,
            timing,
            None,
        );
        assert!(ptp.contains(
            "a=clock-domain:PTPv2 19\r\na=ts-refclk:ptp=IEEE1588-2008:AE-6F-0D-FF-FE-8E-86-FC:19\r\na=mediaclk:direct=0\r\n"
        ));
        // Local mode: ts-refclk:local only; never ptp and never localmac.
        let local = describe(
            "test",
            &output,
            0,
            &labels,
            Some(Reference::Local),
            1,
            1,
            timing,
            None,
        );
        assert!(local.contains("a=ts-refclk:local\r\na=mediaclk:direct=0\r\n"));
        assert!(!local.contains("ts-refclk:ptp"));
        assert!(!local.contains("localmac"));
        assert!(!local.contains("a=clock-domain:"));
        // Unlocked cold start emits no clock reference at all.
        let none = describe("test", &output, 0, &labels, None, 1, 1, timing, None);
        assert!(!none.contains("a=ts-refclk:"));
        assert!(!none.contains("a=mediaclk:"));
    }

    #[test]
    fn origin_username_is_a_bounded_ascii_token() {
        assert_eq!(origin_username("test"), "test");
        assert_eq!(origin_username("Living Room"), "Living-Room");
        assert_eq!(origin_username("NSP 主输出"), "NSP----");
        assert_eq!(origin_username(""), "-");
        assert_eq!(origin_username("a\r\nb"), "a--b");
        assert_eq!(origin_username(&"x".repeat(100)), "x".repeat(64));
        let token = origin_username("NSP 主输出");
        assert!(!token.contains(' '));
        assert!(token.is_ascii());
    }

    #[test]
    fn channel_order_maps_standard_layouts_and_only_exact_prefixes() {
        assert_eq!(channel_order(&label_list(&["M"])).as_deref(), Some("M"));
        assert_eq!(channel_order(&label_list(&["C"])).as_deref(), Some("M"));
        assert_eq!(channel_order(&label_list(&["L", "R"])).as_deref(), Some("ST"));
        assert_eq!(channel_order(&label_list(&["FL", "FR"])).as_deref(), Some("ST"));
        assert_eq!(
            channel_order(&label_list(&["L", "R", "C", "LFE", "Ls", "Rs"])).as_deref(),
            Some("51")
        );
        assert_eq!(
            channel_order(&label_list(&[
                "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"
            ]))
            .as_deref(),
            Some("71")
        );
        // A standard prefix followed by user-defined channels: the count is
        // spelled out so the receiver does not have to guess the order.
        assert_eq!(
            channel_order(&label_list(&[
                "L", "R", "C", "LFE", "Ls", "Rs", "aux-a", "aux-b"
            ]))
            .as_deref(),
            Some("51,U02")
        );
        assert_eq!(
            channel_order(&label_list(&[
                "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "aux-a", "aux-b"
            ]))
            .as_deref(),
            Some("71,U02")
        );
        assert_eq!(
            channel_order(&label_list(&["L", "R", "aux"])).as_deref(),
            Some("ST,U01")
        );
        // No standard prefix: one user-defined group for 1..=8 channels.
        assert_eq!(
            channel_order(&label_list(&["CH", "CH", "CH", "CH"])).as_deref(),
            Some("U04")
        );
        assert_eq!(channel_order(&label_list(&["CH"; 8])).as_deref(), Some("U08"));
        assert_eq!(channel_order(&label_list(&["CH"])).as_deref(), Some("U01"));
        // Order matters: the same channels in a different order must not claim
        // 51. With an unknown head there is no standard prefix, so the whole
        // 6-channel segment is one user-defined group.
        assert_eq!(
            channel_order(&label_list(&["Ls", "Rs", "C", "LFE", "L", "R"])).as_deref(),
            Some("U06")
        );
        // A sequence that starts with the stereo pair is ST plus four
        // user-defined channels, never 51: the remaining order is not known.
        assert_eq!(
            channel_order(&label_list(&["L", "R", "C", "LFE", "Rs", "Ls"])).as_deref(),
            Some("ST,U04")
        );
        // More than eight unmatched channels cannot be described: emit nothing.
        assert_eq!(channel_order(&label_list(&["CH"; 9])), None);
        assert_eq!(channel_order(&label_list(&["CH"; 16])), None);
        assert_eq!(channel_order(&[]), None);
    }

    #[test]
    fn origin_and_information_lines_use_the_device_name_safely() {
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let output = output(5004, 2, Encoding::L24, false);
        let labels = ["other".to_owned(), "L".to_owned(), "R".to_owned()];
        let s = describe(
            "Living Room 主",
            &output,
            3,
            &labels,
            reference(),
            15,
            1,
            timing,
            None,
        );
        // o= is one ASCII token: the device name survives without spaces.
        assert!(
            s.contains("\r\no=Living-Room-- 15 1 IN IP4 127.0.0.1\r\n"),
            "{s}"
        );
        // i= follows m= and keeps spaces while the name has no control bytes.
        let lines: Vec<&str> = s.split("\r\n").collect();
        let media = lines
            .iter()
            .position(|line| line.starts_with("m=audio "))
            .unwrap();
        assert_eq!(lines[media + 1], "i=Living Room 主 flow3 IN/2");

        // CR/LF (and any other control character) is removed, not echoed: a
        // configured name cannot start a new SDP line.
        let injected = describe(
            "Stage\r\nIN IP4 10.0.0.1",
            &output,
            0,
            &labels,
            reference(),
            1,
            1,
            timing,
            None,
        );
        let lines: Vec<&str> = injected.split("\r\n").collect();
        let media = lines
            .iter()
            .position(|line| line.starts_with("m=audio "))
            .unwrap();
        assert_eq!(lines[media + 1], "i=StageIN IP4 10.0.0.1 flow0 IN/2");
        assert_eq!(lines.iter().filter(|line| line.starts_with("i=")).count(), 1);
    }

    #[test]
    fn channel_order_is_emitted_per_media_section_from_the_exact_label_sequence() {
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let cases: [(&[&str], &str); 7] = [
            (&["L", "R"], "ST"),
            (&["L", "R", "C", "LFE", "Ls", "Rs"], "51"),
            (&["L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"], "71"),
            (&["L", "R", "C", "LFE", "Ls", "Rs", "aux-a", "aux-b"], "51,U02"),
            (
                &[
                    "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "aux-a", "aux-b",
                ],
                "71,U02",
            ),
            (&["CH", "CH", "CH", "CH"], "U04"),
            (&["CH", "CH", "CH", "CH", "CH", "CH", "CH", "CH"], "U08"),
        ];
        for (names, group) in cases {
            let output = output_at(0, names.len());
            let s = describe(
                "test",
                &output,
                0,
                &label_list(names),
                reference(),
                1,
                1,
                timing,
                None,
            );
            assert!(
                s.contains(&format!("a=fmtp:110 channel-order=SMPTE2110.({group})\r\n")),
                "{names:?}: {s}"
            );
        }
        // The second flow of a multi-flow config is described by its labels.
        let labels = label_list(&["L", "R", "C", "LFE", "CH", "CH", "CH", "CH"]);
        let second = output_at(4, 4);
        let s = describe("test", &second, 1, &labels, reference(), 1, 1, timing, None);
        assert!(
            s.contains("a=fmtp:110 channel-order=SMPTE2110.(U04)\r\n"),
            "{s}"
        );
        // Unmatched and wider than eight channels: omit instead of guessing.
        let wide = output_at(0, 16);
        let s = describe(
            "test",
            &wide,
            0,
            &label_list(&["CH"; 16]),
            reference(),
            1,
            1,
            timing,
            None,
        );
        assert!(!s.contains("channel-order"), "{s}");
    }

    #[test]
    fn redundant_sections_repeat_information_and_channel_order() {
        let timing = PacketTiming::new(48_000, 1_000).unwrap();
        let output = output_at(0, 2);
        let labels = label_list(&["L", "R"]);
        let s = describe(
            "NSP Sender",
            &output,
            0,
            &labels,
            reference(),
            1,
            1,
            timing,
            Some(5006),
        );
        assert_eq!(
            s.matches("m=audio 5004 RTP/AVP 110\r\ni=NSP Sender flow0 IN/2\r\n")
                .count(),
            1
        );
        assert_eq!(
            s.matches("m=audio 5006 RTP/AVP 110\r\ni=NSP Sender flow0 IN/2\r\n")
                .count(),
            1
        );
        assert_eq!(
            s.matches("a=fmtp:110 channel-order=SMPTE2110.(ST)\r\n")
                .count(),
            2
        );
    }
}
