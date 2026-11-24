use crate::{
    net::multicast_interface,
    ptp::codec::ClockId,
    rtp::Output,
    time::PacketTiming,
};
use std::{
    fmt::Write,
    io,
    net::{Ipv4Addr, UdpSocket},
};

#[derive(Clone, Copy, Debug)]
pub struct Reference {
    pub gm: ClockId,
    pub domain: u8,
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
        crc = (crc << 8)
            ^ (u16::from(value) << 12)
            ^ (u16::from(value) << 5)
            ^ u16::from(value);
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

fn media_block(
    s: &mut String,
    output: &Output,
    timing: PacketTiming,
    reference: Option<Reference>,
    port: u16,
    mid: Option<u8>,
) {
    let flow = &output.flow;
    let dest = flow.destination.ip();
    let pt = flow.payload_type;
    let _ = write!(
        s,
        "m=audio {} RTP/AVP {pt}\r\na=rtpmap:{pt} {}/{}/{}\r\na=sendonly\r\na=ptime:{}\r\n",
        port,
        output.encoding.codec(),
        timing.sample_rate(),
        flow.channels,
        ptime_milliseconds(timing),
    );
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
    if let Some(r) = reference {
        let _ = write!(s, "a=clock-domain:PTPv2 {}\r\n", r.domain);
        s.push_str("a=ts-refclk:ptp=IEEE1588-2008:");
        for (i, octet) in r.gm.into_iter().enumerate() {
            if i != 0 {
                s.push('-');
            }
            let _ = write!(s, "{octet:02X}");
        }
        let _ = write!(s, ":{}\r\na=mediaclk:direct=0\r\n", r.domain);
    }
    if let Some(mid) = mid {
        let _ = write!(s, "a=mid:{mid}\r\n");
    }
}

/// Serializes one RTP flow as one v=0 session. With ST 2022-7 redundancy the
/// session carries two media sections (primary and secondary port) under
/// a=group:DUP, matching the daemon source layout.
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
    let pt = flow.payload_type;
    let mut s = format!(
        "v=0\r\no=- {session_id} {version} IN IP4 {}\r\ns={name} flow{index}\r\n",
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
    media_block(
        &mut s,
        output,
        timing,
        reference,
        flow.destination.port(),
        secondary_port.map(|_| 1),
    );
    if let Some(secondary) = secondary_port {
        media_block(&mut s, output, timing, reference, secondary, Some(2));
    }
    let labels = &labels[flow.offset..flow.offset + flow.channels];
    if matches!(labels, [left,right] if (left == "L" && right == "R") || (left == "FL" && right == "FR"))
    {
        let _ = write!(s, "a=fmtp:{pt} channel-order=SMPTE2110.(ST)\r\n");
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

    fn reference() -> Option<Reference> {
        Some(Reference {
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
        let s = describe("test", &output, 0, &labels, reference(), 15, 1, timing, None);
        assert_eq!(s.matches("v=0\r\n").count(), 1);
        assert!(s.contains("m=audio 5004 RTP/AVP 110\r\n"));
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
        assert!(stop.ends_with(b"o=- 15 1 IN IP4 127.0.0.1\r\n"));
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
            (48_000, 333, Encoding::L24, 8, "L24/48000/8", "0.333333", "16"),
            (44_100, 333, Encoding::L16, 8, "L16/44100/8", "0.36", "16"),
            (44_100, 1_000, Encoding::L16, 8, "L16/44100/8", "1.09", "48"),
            (44_100, 4_000, Encoding::L16, 8, "L16/44100/8", "4.35", "192"),
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
            let s = describe("test", &output, 0, &vec!["CH".into(); channels + 1], reference(), 1, 1, timing, None);
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
        let s = describe("test", &output, 0, &labels, reference(), 1, 1, timing, Some(5006));
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
        let s = describe("test", &output, 0, &["L".into(), "R".into()], reference(), 1, 1, timing, None);
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
}