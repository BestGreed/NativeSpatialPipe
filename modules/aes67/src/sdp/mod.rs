use crate::{net::multicast_interface, ptp::codec::ClockId, rtp::Output};
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

pub fn describe(
    name: &str,
    output: &Output,
    index: usize,
    labels: &[String],
    reference: Option<Reference>,
    session_id: u64,
    version: u64,
) -> String {
    let flow = &output.flow;
    let dest = flow.destination.ip();
    let pt = flow.payload_type;
    let mut s = format!(
        "v=0\r\no=- {session_id} {version} IN IP4 {}\r\ns={name} flow{index}\r\n",
        output.source
    );
    write!(s, "c=IN IP4 {dest}").unwrap();
    if dest.is_multicast() {
        write!(s, "/{}", flow.ttl).unwrap();
    }
    write!(
        s,
        "\r\nt=0 0\r\nm=audio {} RTP/AVP {pt}\r\na=rtpmap:{pt} L24/48000/{}\r\n",
        flow.destination.port(),
        flow.channels
    )
    .unwrap();
    s.push_str("a=ptime:1\r\na=sendonly\r\n");
    if dest.is_multicast() && flow.source_filter {
        write!(
            s,
            "a=source-filter: incl IN IP4 {dest} {}\r\n",
            output.source
        )
        .unwrap();
    }
    if let Some(r) = reference {
        s.push_str("a=ts-refclk:ptp=IEEE1588-2008:");
        for (i, octet) in r.gm.into_iter().enumerate() {
            if i != 0 {
                s.push('-');
            }
            write!(s, "{octet:02X}").unwrap();
        }
        write!(s, ":{}\r\na=mediaclk:direct=0\r\n", r.domain).unwrap();
    }
    let labels = &labels[flow.offset..flow.offset + flow.channels];
    if matches!(labels, [left,right] if (left == "L" && right == "R") || (left == "FL" && right == "FR"))
    {
        write!(s, "a=fmtp:{pt} channel-order=SMPTE2110.(ST)\r\n").unwrap();
    }
    write!(
        s,
        "a=comment:channel-labels={}\r\na=comment:flow={index}\r\na=comment:channel-offset={}\r\n",
        labels.join(","),
        flow.offset
    )
    .unwrap();
    s
}

/// Minimal SAP publisher, one announcement identity and socket per RTP flow.
pub struct Sap {
    socket: UdpSocket,
    hash: u16,
    source: Ipv4Addr,
    destination: Ipv4Addr,
}
impl Sap {
    pub fn open(output: &Output, hash: u16) -> io::Result<Self> {
        let socket = UdpSocket::bind((output.source, 0))?;
        multicast_interface(&socket, output.source)?;
        socket.set_multicast_ttl_v4(u32::from(output.flow.ttl))?;
        socket.set_nonblocking(true)?;
        let destination = if output.flow.destination.ip().octets()[0] == 239 {
            Ipv4Addr::new(239, 255, 255, 255)
        } else {
            Ipv4Addr::new(224, 2, 127, 254)
        };
        Ok(Self {
            socket,
            hash,
            source: output.source,
            destination,
        })
    }
    pub fn publish(&self, sdp: &str, delete: bool) -> io::Result<()> {
        let packet = sap_packet(self.hash, self.source, sdp, delete);
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
        // RFC 2974 SDP deletion contains only the original origin field.
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
    use crate::rtp::Flow;
    #[test]
    fn one_session_per_flow_with_honest_clock_and_labels() {
        let ip = Ipv4Addr::LOCALHOST;
        let sink = UdpSocket::bind((ip, 0)).unwrap();
        let output = Output::open(Flow {
            destination: std::net::SocketAddrV4::new(ip, sink.local_addr().unwrap().port()),
            interface: ip,
            ttl: 1,
            ssrc: 1,
            offset: 1,
            channels: 2,
            payload_type: 110,
            source_filter: false,
        })
        .unwrap();
        let labels = ["other".to_owned(), "FL".to_owned(), "FR".to_owned()];
        let unlocked = describe("test", &output, 0, &labels, None, 15, 1);
        assert!(!unlocked.contains("a=ts-refclk:"));
        let s = describe(
            "test",
            &output,
            0,
            &labels,
            Some(Reference {
                gm: [0xab; 8],
                domain: 42,
            }),
            15,
            1,
        );
        assert_eq!(s.matches("v=0\r\n").count(), 1);
        assert!(s.contains("a=rtpmap:110 L24/48000/2\r\n"));
        assert!(s.contains(
            "a=ts-refclk:ptp=IEEE1588-2008:AB-AB-AB-AB-AB-AB-AB-AB:42\r\na=mediaclk:direct=0\r\n"
        ));
        assert!(s.contains("a=comment:channel-labels=FL,FR\r\n"));
        assert!(s.contains("channel-order=SMPTE2110.(ST)"));
        assert_eq!(
            &sap_packet(0x1234, ip, &s, false)[..8],
            &[0x20, 0, 0x12, 0x34, 127, 0, 0, 1]
        );
        let stop = sap_packet(0x1234, ip, &s, true);
        assert_eq!(stop[0], 0x24);
        assert!(stop.ends_with(b"o=- 15 1 IN IP4 127.0.0.1\r\n"));
    }
}
