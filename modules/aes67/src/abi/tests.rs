use super::*;
use crate::ptp::codec::{self, Body, Message, PTP_TIMESCALE, TWO_STEP};
use std::{
    net::{SocketAddr, UdpSocket},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread::{self, JoinHandle},
    time::Instant,
};

const IP: Ipv4Addr = Ipv4Addr::LOCALHOST;
const GM: [u8; 8] = [0x12; 8];

/// Full Announce data set for the loopback mock GM (IEEE 1588-2008 13.3.1).
fn codec_announce(grandmaster: [u8; 8]) -> ptp::codec::Announce {
    ptp::codec::Announce {
        origin_ns: 0,
        current_utc_offset: 0,
        priority1: 128,
        clock_class: 248,
        clock_accuracy: 0xfe,
        offset_scaled_log_variance: 0xffff,
        priority2: 128,
        grandmaster,
        steps_removed: 0,
        time_source: 0xa0,
    }
}
const EPOCH: i128 = 1_900_000_000_000_000_000;

struct Handle(*mut Session);
impl Handle {
    fn new() -> Self {
        let mut handle = ptr::null_mut();
        assert_eq!(unsafe { nsp_aes67_create(&mut handle) }, 0);
        Self(handle)
    }
    fn poll(&self) -> Option<Event> {
        let mut event = Event::default();
        (unsafe { nsp_aes67_poll_lifecycle_event(self.0, &mut event) } == 1).then_some(event)
    }
    fn configure(&self, config: &ConfigV1) -> i32 {
        unsafe { nsp_aes67_configure(self.0, config) }
    }
    fn configure_v2(&self, config: &ConfigV2) -> i32 {
        unsafe { nsp_aes67_configure(self.0, config.cast()) }
    }
    fn configure_v3(&self, config: &ConfigV3) -> i32 {
        unsafe { nsp_aes67_configure(self.0, config.cast()) }
    }
    fn ptp_status(&self) -> PtpStatusV1 {
        let mut status = PtpStatusV1::default();
        assert_eq!(unsafe { nsp_aes67_ptp_status(self.0, &mut status) }, 0);
        status
    }
}
impl Drop for Handle {
    fn drop(&mut self) {
        assert_eq!(unsafe { nsp_aes67_close(self.0, 1500) }, 0);
    }
}

fn bind() -> UdpSocket {
    UdpSocket::bind((IP, 0)).unwrap()
}
fn port(socket: &UdpSocket) -> u16 {
    socket.local_addr().unwrap().port()
}
/// RTP media ports must be even (RFC 3551 clause 8); ephemeral binds are
/// retried until the OS hands out an even port.
fn bind_even() -> UdpSocket {
    loop {
        let socket = bind();
        if port(&socket) % 2 == 0 {
            return socket;
        }
    }
}
fn config_base() -> ConfigV1 {
    ConfigV1 {
        struct_size: mem::size_of::<ConfigV1>() as u32,
        channels: 2,
        sample_rate: 48_000,
        ptp_interface_ip: IP.octets(),
        ptp_master_ip: IP.octets(),
        ptp_domain: 19,
        ptp_gmid: GM,
        default_dest_ip: IP.octets(),
        default_src_interface_ip: IP.octets(),
        default_ttl: DEFAULT_TTL,
        ..ConfigV1::default()
    }
}
fn ptp_ports(
    config: &mut ConfigV1,
    event: u16,
    general: u16,
    local_event: u16,
    local_general: u16,
) {
    config.ptp_event_port = event;
    config.ptp_general_port = general;
    config.ptp_local_event_port = local_event;
    config.ptp_local_general_port = local_general;
}

struct Mock {
    stop: Arc<AtomicBool>,
    impair: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
}
impl Mock {
    fn spawn(
        event: UdpSocket,
        general: UdpSocket,
        dest_event: u16,
        dest_general: u16,
        version: u8,
    ) -> Self {
        event.set_nonblocking(true).unwrap();
        general.set_nonblocking(true).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let cancel = Arc::clone(&stop);
        let impair = Arc::new(AtomicBool::new(false));
        let inject = Arc::clone(&impair);
        let join = thread::spawn(move || {
            let start = Instant::now();
            let stamp = || EPOCH + start.elapsed().as_nanos() as i128;
            let mut sequence = 0_u16;
            let mut next = Instant::now();
            let mut delayed = None;
            let mut buf = [0_u8; 64];
            let send = |socket: &UdpSocket, target: u16, sequence, flags, body| {
                let mut bytes = [0_u8; 64];
                let msg = Message {
                    domain: 19,
                    log_interval: if matches!(body, Body::Announce { .. }) {
                        -1
                    } else {
                        0
                    },
                    source: [7; 10],
                    seq: sequence,
                    flags,
                    correction_scaled_ns: 0,
                    body,
                };
                let length = codec::encode(msg, &mut bytes).len();
                bytes[1] = version;
                let _ = socket.send_to(&bytes[..length], (IP, target));
            };
            while !cancel.load(Ordering::Acquire) {
                if Instant::now() >= next {
                    // Eleven Syncs per advertised Announce interval. Occasionally miss one Announce.
                    if sequence % 11 == 0 && !(inject.load(Ordering::Relaxed) && sequence % 44 == 0)
                    {
                        send(
                            &general,
                            dest_general,
                            sequence,
                            PTP_TIMESCALE,
                            Body::Announce(codec_announce(GM)),
                        );
                    }
                    let tx = stamp();
                    send(&event, dest_event, sequence, TWO_STEP, Body::Sync);
                    send(&general, dest_general, sequence, 0, Body::FollowUp(tx));
                    sequence = sequence.wrapping_add(1);
                    next += Duration::from_millis(45);
                }
                if let Ok((len, _)) = event.recv_from(&mut buf) {
                    let rx = stamp();
                    if let Ok(request) = codec::decode(&buf[..len])
                        && matches!(request.body, Body::DelayReq(_))
                    {
                        let body = Body::DelayResp {
                            received_ns: rx,
                            requester: request.source,
                        };
                        let impair = inject.load(Ordering::Relaxed);
                        if impair && request.seq % 12 == 0 {
                            // Interleaved Delay_Resp to a different follower must not satisfy our request.
                            send(
                                &general,
                                dest_general,
                                request.seq,
                                0,
                                Body::DelayResp {
                                    received_ns: rx,
                                    requester: [9; 10],
                                },
                            );
                        } else if impair && request.seq % 12 == 6 {
                            delayed = Some((
                                Instant::now() + Duration::from_millis(300),
                                request.seq,
                                body,
                            ));
                        } else {
                            send(&general, dest_general, request.seq, 0, body);
                        }
                    }
                }
                if delayed.is_some_and(|(deadline, _, _)| Instant::now() >= deadline) {
                    let (_, sequence, body) = delayed.take().unwrap();
                    send(&general, dest_general, sequence, 0, body);
                }
                thread::sleep(Duration::from_micros(70));
            }
        });
        Self {
            stop,
            impair,
            join: Some(join),
        }
    }
}
impl Drop for Mock {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.join.take().unwrap().join().unwrap();
    }
}

fn loopback_timeout() -> u32 {
    std::env::var("NSP_AES67_TEST_LOCK_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(3200)
}

unsafe extern "C" fn release(token: *mut c_void) {
    unsafe { &*token.cast::<AtomicUsize>() }.fetch_add(1, Ordering::Relaxed);
}

fn start_locked(handle: &Handle, config: &ConfigV1) -> Mock {
    let event = bind();
    let general = bind();
    let local_event = bind();
    let local_general = bind();
    let mut config = config.clone();
    ptp_ports(
        &mut config,
        port(&event),
        port(&general),
        port(&local_event),
        port(&local_general),
    );
    config.lock_timeout_ms = loopback_timeout();
    drop((local_event, local_general));
    let gm = Mock::spawn(
        event,
        general,
        config.ptp_local_event_port,
        config.ptp_local_general_port,
        0x12,
    );
    assert_eq!(handle.configure(&config), 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, 0);
    assert_eq!(handle.poll().unwrap().kind, 1);
    gm
}
#[test]
fn udp_mock_v20_gm_drives_native_start_rtp_lease_loss_and_stop() {
    check_udp_mock_gm(0x02);
}

#[test]
fn udp_mock_v21_gm_drives_native_start_rtp_lease_loss_and_stop() {
    check_udp_mock_gm(0x12);
}

fn check_udp_mock_gm(version: u8) {
    let event = bind();
    let general = bind();
    let local_event = bind();
    let local_general = bind();
    let audio = bind_even();
    audio
        .set_read_timeout(Some(Duration::from_millis(50)))
        .unwrap();
    let handle = Handle::new();
    let mut config = config_base();
    config.default_port = port(&audio);
    ptp_ports(
        &mut config,
        port(&event),
        port(&general),
        port(&local_event),
        port(&local_general),
    );
    config.lock_timeout_ms = loopback_timeout();
    config.loss_timeout_ms = 250;
    config.prebuffer_ms = 1;
    drop((local_event, local_general));
    let gm = Mock::spawn(
        event,
        general,
        config.ptp_local_event_port,
        config.ptp_local_general_port,
        version,
    );
    assert_eq!(unsafe { nsp_aes67_configure(handle.0, &config) }, 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, 0);
    assert_eq!(handle.poll().unwrap().kind, 1);
    let mut counters = Counters::default();
    assert_eq!(unsafe { nsp_aes67_counters(handle.0, &mut counters) }, 0);
    assert_eq!((counters.locked, counters.timestamping), (1, 1));
    let status = handle.ptp_status();
    assert_eq!(status.mode, ptp::PtpMode::Slave.as_abi());
    assert_eq!(status.role, ptp::Role::Slave.as_abi());
    assert_eq!(status.source, ptp::Source::ExternalGm.as_abi());
    assert_eq!(status.gm_identity, GM);
    assert_eq!(status.domain, 19);
    assert_eq!(
        status.time_traceable, 0,
        "mock announce has no TIME_TRACEABLE"
    );
    assert_eq!(status.ptp_timescale, 1, "mock announce sets PTP_TIMESCALE");
    let size = unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) };
    assert!(size > 0);
    let mut sdp = vec![0_u8; size as usize];
    assert_eq!(
        unsafe { nsp_aes67_sdp(handle.0, 0, sdp.as_mut_ptr().cast(), sdp.len()) },
        size
    );
    let text = CStr::from_bytes_with_nul(&sdp).unwrap().to_str().unwrap();
    assert!(text.contains("a=ts-refclk:ptp=IEEE1588-2008:12-12-12-12-12-12-12-12:19"));
    assert!(text.contains("a=ptime:1\r\n"));
    assert!(text.contains("a=framecount:48\r\n"));

    let releases = AtomicUsize::new(0);
    let token = ptr::from_ref(&releases).cast_mut().cast();
    let frames = 24 * 48;
    let samples = [0.25_f32, -0.5].repeat(frames);
    assert_eq!(
        unsafe {
            nsp_aes67_submit_leased(
                handle.0.cast(),
                123,
                samples.as_ptr(),
                frames as u32,
                2,
                0,
                token,
                Some(release),
            )
        },
        0
    );
    assert_eq!(releases.load(Ordering::Relaxed), 1);
    assert_eq!(
        unsafe {
            nsp_aes67_submit_leased(
                handle.0.cast(),
                123,
                samples.as_ptr(),
                48,
                1,
                0,
                token,
                Some(release),
            )
        },
        session::INVALID
    );
    assert_eq!(
        unsafe {
            nsp_aes67_submit_leased(
                handle.0.cast(),
                123,
                samples.as_ptr(),
                100_000,
                2,
                0,
                token,
                Some(release),
            )
        },
        session::QUEUE_FULL
    );
    assert_eq!(releases.load(Ordering::Relaxed), 1);
    let mut packet = [0_u8; 1500];
    let mut received_audio = false;
    let deadline = Instant::now() + Duration::from_millis(400);
    while Instant::now() < deadline {
        if let Ok((length, SocketAddr::V4(peer))) = audio.recv_from(&mut packet) {
            assert_eq!(*peer.ip(), IP);
            assert_eq!(length, 12 + 48 * 2 * 3);
            // The marker bit is never set (byte 1 is the literal PT).
            assert_eq!(packet[1], 96);
            let stamp = u32::from_be_bytes(packet[4..8].try_into().unwrap());
            assert!(stamp.wrapping_sub((EPOCH / 1_000_000 * 48) as u32) < 48_000 * 10);
            if packet[12..18] == [0x20, 0, 0, 0xc0, 0, 0] {
                received_audio = true;
                break;
            }
        }
    }
    assert!(received_audio);
    gm.impair.store(true, Ordering::Release);
    let deadline = Instant::now() + Duration::from_millis(3200);
    while Instant::now() < deadline {
        audio
            .recv_from(&mut packet)
            .expect("RTP paused under packet impairment");
        assert!(
            handle.poll().is_none(),
            "transient PTP impairment lost lock"
        );
    }
    gm.impair.store(false, Ordering::Release);

    drop(gm);
    let deadline = Instant::now() + Duration::from_secs(4);
    let loss = loop {
        if let Some(event) = handle.poll() {
            break event;
        }
        assert!(Instant::now() < deadline);
        thread::sleep(Duration::from_millis(1));
    };
    assert_eq!(loss.kind, 4);
    // SAP/SDP stay available while RTP is still flowing in holdover.
    assert!(unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) } > 0);
    let holdover_ms = std::env::var("NSP_AES67_HOLDOVER_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|ms| (1000..=3000).contains(ms))
        .unwrap_or(2000);
    let loss_seen = Instant::now();
    while loss_seen.elapsed() < Duration::from_millis(holdover_ms - 200) {
        audio
            .recv_from(&mut packet)
            .expect("RTP stopped before holdover expired");
    }
    while audio.recv_from(&mut packet).is_ok() {
        assert!(
            loss_seen.elapsed() < Duration::from_millis(holdover_ms + 500),
            "expired holdover still emits RTP"
        );
    }
    assert!(loss_seen.elapsed() >= Duration::from_millis(holdover_ms - 80));
    // Only after RTP stops is the SDP withdrawn.
    assert_eq!(
        unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) },
        session::STATE
    );
    let event = UdpSocket::bind((IP, config.ptp_event_port)).unwrap();
    let general = UdpSocket::bind((IP, config.ptp_general_port)).unwrap();
    let gm = Mock::spawn(
        event,
        general,
        config.ptp_local_event_port,
        config.ptp_local_general_port,
        version,
    );
    let deadline = Instant::now() + Duration::from_millis(u64::from(config.lock_timeout_ms));
    while unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) } <= 0 {
        assert!(Instant::now() < deadline, "PTP did not reacquire");
        thread::sleep(Duration::from_millis(1));
    }
    audio
        .recv_from(&mut packet)
        .expect("RTP did not resume after reacquisition");
    drop(gm);
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        if let Some(lost_again) = handle.poll() {
            assert_eq!(lost_again.kind, 4);
            break;
        }
        assert!(Instant::now() < deadline);
        thread::sleep(Duration::from_millis(1));
    }
    let deadline = Instant::now()
        + Duration::from_millis(u64::from(config.lock_timeout_ms) + holdover_ms + 1500);
    let exit = loop {
        if let Some(event) = handle.poll() {
            break event;
        }
        assert!(Instant::now() < deadline);
        thread::sleep(Duration::from_millis(1));
    };
    assert_eq!((exit.kind, exit.code), (2, session::LOCK_TIMEOUT));
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1000) }, 0);
    assert_eq!(handle.poll().unwrap().kind, 3);
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 0) }, 0);
    assert!(handle.poll().is_none());
    UdpSocket::bind((IP, config.ptp_local_event_port)).unwrap();
    UdpSocket::bind((IP, config.ptp_local_general_port)).unwrap();
}

#[test]
fn udp_pacing_30s_delivers_at_least_999_pps_without_timestamp_holes() {
    let audio = bind_even();
    audio
        .set_read_timeout(Some(Duration::from_millis(100)))
        .unwrap();
    let handle = Handle::new();
    let mut config = config_base();
    config.default_port = port(&audio);
    config.lock_timeout_ms = loopback_timeout();
    let _gm = start_locked(&handle, &config);
    let mut buf = [0; 1500];
    let first = audio.recv(&mut buf).unwrap();
    assert_eq!(first, 12 + 48 * 2 * 3);
    let start = Instant::now();
    let mut previous_seq = u16::from_be_bytes(buf[2..4].try_into().unwrap());
    let mut previous_ts = u32::from_be_bytes(buf[4..8].try_into().unwrap());
    let mut packets = 0;
    let mut irregular = 0;
    let mut missing = 0_u64;
    while start.elapsed() < Duration::from_secs(30) {
        let length = audio
            .recv(&mut buf)
            .expect("unexpected RTP pause during 30-second pacing test");
        assert_eq!(length, first);
        let seq = u16::from_be_bytes(buf[2..4].try_into().unwrap());
        let ts = u32::from_be_bytes(buf[4..8].try_into().unwrap());
        missing += u64::from(seq.wrapping_sub(previous_seq).wrapping_sub(1));
        let step = ts.wrapping_sub(previous_ts);
        if step != 48 {
            irregular += 1;
            println!("irregular step {step} seq {seq} ts {ts}");
        }
        previous_seq = seq;
        previous_ts = ts;
        packets += 1;
    }
    let elapsed = start.elapsed().as_secs_f64();
    let pps = f64::from(packets) / elapsed;
    println!(
        "{packets} RTP packets over {elapsed:.3}s = {pps:.3}pps, sequence gaps={missing}, abnormal timestamp steps={irregular}"
    );
    assert_eq!(missing, 0);
    assert_eq!(irregular, 0);
    assert!(pps >= 999.0, "{pps:.3}pps below minimum rate");
    if let Some(event) = handle.poll() {
        panic!(
            "unexpected lifecycle event kind={} code={}",
            event.kind, event.code
        );
    }
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1000) }, 0);
}

#[test]
fn loopback_125us_emits_six_sample_packets_with_six_sample_timestamp_steps() {
    let audio = bind_even();
    audio
        .set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    let handle = Handle::new();
    let mut config = config_base();
    config.default_port = port(&audio);
    let mut v2 = ConfigV2 {
        packet_time_us: 125,
        ..ConfigV2::default()
    };
    v2.base = config.clone();
    v2.base.struct_size = mem::size_of::<ConfigV2>() as u32;
    let _gm = start_locked_v2(&handle, &v2);
    let mut buf = [0; 1500];
    let mut previous_ts = None;
    let mut previous_seq = None;
    let mut checked = 0;
    while checked < 600 {
        let length = audio.recv(&mut buf).unwrap();
        assert_eq!(length, 12 + 6 * 2 * 3);
        assert_eq!(buf[1], 96);
        let seq = u16::from_be_bytes(buf[2..4].try_into().unwrap());
        let ts = u32::from_be_bytes(buf[4..8].try_into().unwrap());
        if let Some(previous) = previous_ts {
            assert_eq!(ts.wrapping_sub(previous), 6);
        }
        if let Some(previous) = previous_seq {
            assert_eq!(seq.wrapping_sub(previous), 1);
        }
        previous_ts = Some(ts);
        previous_seq = Some(seq);
        checked += 1;
    }
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1000) }, 0);
}

#[test]
fn loopback_44k1_keeps_48_sample_packets_and_918_75_packet_rate() {
    let audio = bind_even();
    audio
        .set_read_timeout(Some(Duration::from_millis(500)))
        .unwrap();
    let handle = Handle::new();
    let mut config = config_base();
    config.sample_rate = 44_100;
    config.default_port = port(&audio);
    let mut v2 = ConfigV2 {
        packet_time_us: 1_000,
        bit_depth: 0,
        ..ConfigV2::default()
    };
    v2.base = config.clone();
    v2.base.struct_size = mem::size_of::<ConfigV2>() as u32;
    let _gm = start_locked_v2(&handle, &v2);
    let mut buf = [0; 1500];
    let mut previous_ts = None;
    let first = Instant::now();
    let mut packets = 0_u64;
    while first.elapsed() < Duration::from_secs(3) {
        let length = audio.recv(&mut buf).unwrap();
        assert_eq!(length, 12 + 48 * 2 * 3);
        let ts = u32::from_be_bytes(buf[4..8].try_into().unwrap());
        if let Some(previous) = previous_ts {
            // AES67 Table 2 line 1532: 48 samples per packet at 44.1 kHz.
            assert_eq!(ts.wrapping_sub(previous), 48);
        }
        previous_ts = Some(ts);
        packets += 1;
    }
    let pps = packets as f64 / first.elapsed().as_secs_f64();
    println!("44.1 kHz 1 ms profile measured {pps:.2} packets/s");
    assert!((900.0..=935.0).contains(&pps), "{pps:.2} pps");
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1000) }, 0);
}

fn start_locked_v2(handle: &Handle, config: &ConfigV2) -> Mock {
    let event = bind();
    let general = bind();
    let local_event = bind();
    let local_general = bind();
    let mut config = config.clone();
    config.base.ptp_event_port = port(&event);
    config.base.ptp_general_port = port(&general);
    config.base.ptp_local_event_port = port(&local_event);
    config.base.ptp_local_general_port = port(&local_general);
    config.base.lock_timeout_ms = loopback_timeout();
    drop((local_event, local_general));
    let gm = Mock::spawn(
        event,
        general,
        config.base.ptp_local_event_port,
        config.base.ptp_local_general_port,
        0x12,
    );
    assert_eq!(handle.configure_v2(&config), 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, 0);
    assert_eq!(handle.poll().unwrap().kind, 1);
    gm
}
#[test]
fn hardware_partial_init_and_start_timeout_release_all_local_ports() {
    let handle = Handle::new();
    let first = bind();
    let held = bind();
    let sink = bind_even();
    let mut config = config_base();
    config.default_port = port(&sink);
    config.ptp_local_event_port = port(&first);
    config.ptp_local_general_port = port(&held);
    config.lock_timeout_ms = 100;
    drop(first);
    config.timestamping = TimestampMode::Hardware as u32;
    assert_eq!(unsafe { nsp_aes67_configure(handle.0, &config) }, 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, session::UNAVAILABLE);
    config.timestamping = TimestampMode::Software as u32;
    assert_eq!(unsafe { nsp_aes67_configure(handle.0, &config) }, 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, session::IO);
    UdpSocket::bind((IP, config.ptp_local_event_port)).unwrap();
    drop(held);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, session::LOCK_TIMEOUT);
    assert!(handle.poll().is_none());
    UdpSocket::bind((IP, config.ptp_local_event_port)).unwrap();
    UdpSocket::bind((IP, config.ptp_local_general_port)).unwrap();
}

#[test]
fn abi_layout_and_default_flow_plan() {
    assert_eq!(mem::size_of::<FlowV1>(), 24);
    assert_eq!(mem::size_of::<ConfigV1>(), 128);
    assert_eq!(mem::size_of::<ConfigV2>(), 152);
    assert_eq!(mem::offset_of!(ConfigV1, flows), 88);
    assert_eq!(mem::size_of::<Counters>(), 80);
    assert_eq!(mem::size_of::<Event>(), 12);
    let mut raw = config_base();
    raw.channels = 10;
    raw.input_channels = 2;
    let map = [1, 0, 0, 1, 0, 1, 1, 0, 0, 0];
    raw.channel_map = map.as_ptr();
    raw.ssrc_base = u32::MAX;
    // Derived multi-flow plans need a multicast base group to allocate one
    // unique destination per flow.
    raw.default_dest_ip = [239, 69, 0, 1];
    let config = unsafe { super::config(&raw) }.unwrap();
    assert_eq!(config.map, map.map(|x| x as usize));
    // AES67 media profile caps a flow at 8 channels, so 10 channels split as
    // two balanced flows (5 + 5) even though the payload budget would allow 10.
    assert_eq!(config.flows.len(), 2);
    assert_eq!(
        (
            config.flows[0].offset,
            config.flows[0].channels,
            config.flows[0].ssrc
        ),
        (0, 5, u32::MAX)
    );
    assert_eq!(
        (
            config.flows[1].offset,
            config.flows[1].channels,
            config.flows[1].ssrc
        ),
        (5, 5, 0)
    );
    assert_eq!(config.encoding, Encoding::L24);
    assert_eq!(config.timing.samples_per_packet(), 48);
}

#[test]
fn sample_rate_whitelist_is_enforced() {
    for rate in SUPPORTED_SAMPLE_RATES {
        let mut raw = config_base();
        raw.sample_rate = rate;
        raw.channels = 2;
        let config =
            unsafe { super::config(&raw) }.unwrap_or_else(|| panic!("rate {rate} rejected"));
        assert_eq!(config.timing.sample_rate(), rate);
    }
    for rate in [32_000, 352_800, 384_000, 22_050] {
        let mut raw = config_base();
        raw.sample_rate = rate;
        assert!(unsafe { super::config(&raw) }.is_none(), "rate {rate}");
    }
}

#[test]
fn packet_time_and_bit_depth_validation() {
    for ptime in SUPPORTED_PACKET_TIMES_US {
        let mut v2 = auto_config(2, 48_000, ptime, 0);
        v2.base.default_dest_ip = IP.octets();
        let config = unsafe { super::config((&v2).cast()) }.unwrap();
        assert_eq!(config.timing.packet_time_us(), ptime);
    }
    let mut v2 = auto_config(2, 48_000, 333, 0);
    v2.base.default_dest_ip = IP.octets();
    assert!(unsafe { super::config((&v2).cast()) }.is_some());
    // Explicit frames-per-packet must agree with the Table 2 sample count.
    let mut frames = auto_config(2, 48_000, 1_000, 0);
    frames.base.default_dest_ip = IP.octets();
    frames.frames_per_packet = 48;
    assert!(unsafe { super::config((&frames).cast()) }.is_some());
    frames.frames_per_packet = 44;
    assert!(unsafe { super::config((&frames).cast()) }.is_none());
    let mut third = auto_config(2, 48_000, 333, 0);
    third.base.default_dest_ip = IP.octets();
    third.frames_per_packet = 16;
    assert!(unsafe { super::config((&third).cast()) }.is_some());
    let mut unsupported = auto_config(2, 48_000, 300, 0);
    unsupported.base.default_dest_ip = IP.octets();
    assert!(unsafe { super::config((&unsupported).cast()) }.is_none());
    let mut odd = auto_config(2, 48_000, 1_000, 0);
    odd.base.default_port = 5005;
    odd.base.default_dest_ip = IP.octets();
    assert!(unsafe { super::config((&odd).cast()) }.is_none());
    let mut l16 = auto_config(2, 48_000, 1_000, 1);
    l16.base.default_dest_ip = IP.octets();
    let config = unsafe { super::config((&l16).cast()) }.unwrap();
    assert_eq!(config.encoding, Encoding::L16);
    let mut bad_depth = auto_config(2, 48_000, 1_000, 2);
    bad_depth.base.default_dest_ip = IP.octets();
    assert!(unsafe { super::config((&bad_depth).cast()) }.is_none());
    // The extended struct must not carry unknown reserved bits.
    let mut reserved = auto_config(2, 48_000, 1_000, 0);
    reserved.base.default_dest_ip = IP.octets();
    reserved.reserved = [1, 0, 0, 0, 0, 0];
    assert!(unsafe { super::config((&reserved).cast()) }.is_none());
}

fn auto_config(channels: u32, rate: u32, ptime: u32, depth: u8) -> ConfigV2 {
    let mut v2 = ConfigV2 {
        packet_time_us: ptime,
        bit_depth: depth,
        ..ConfigV2::default()
    };
    v2.base.struct_size = mem::size_of::<ConfigV2>() as u32;
    v2.base.channels = channels;
    v2.base.sample_rate = rate;
    v2.base.ptp_interface_ip = IP.octets();
    v2.base.ptp_master_ip = IP.octets();
    v2.base.ptp_domain = 19;
    v2.base.ptp_gmid = GM;
    v2.base.default_port = 5004;
    v2.base.default_ttl = 32;
    v2.base.default_dest_ip = [0; 4];
    v2.base.default_src_interface_ip = IP.octets();
    v2
}

#[test]
fn auto_mode_splits_by_payload_budget_and_rejects_unservable_times() {
    // 48 kHz / 1 ms / 16ch / L24: 2 x 8ch (1152 B each).
    let config = unsafe { super::config((&auto_config(16, 48_000, 1_000, 0)).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 2);
    assert_eq!(
        config.flows.iter().map(|f| f.channels).collect::<Vec<_>>(),
        [8, 8]
    );
    assert!(
        config
            .flows
            .iter()
            .all(|flow| flow.destination.port() == 5004)
    );
    assert_eq!(
        config
            .flows
            .iter()
            .map(|f| *f.destination.ip())
            .collect::<Vec<_>>(),
        [Ipv4Addr::new(239, 69, 0, 1), Ipv4Addr::new(239, 69, 0, 2)]
    );

    // 48 kHz / 1 ms / 16ch / L16: 16 x 48 x 2 = 1536 B, still 2 x 8ch (768 B).
    let config = unsafe { super::config((&auto_config(16, 48_000, 1_000, 1)).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 2);

    // 192 kHz / 125 us / 16ch / L24: payload would fit one 1152 B flow, but
    // AES67 sends at most 8 channels per flow, so 16ch = 2 x 8ch.
    let config = unsafe { super::config((&auto_config(16, 192_000, 125, 0)).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 2);
    assert!(config.flows.iter().all(|flow| flow.channels == 8));

    // 192 kHz / 500 us / 16ch / L24: 96 samples -> 4ch per flow, 4 flows.
    let config = unsafe { super::config((&auto_config(16, 192_000, 500, 0)).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 4);
    assert!(config.flows.iter().all(|flow| flow.channels == 4));

    // 192 kHz / 1 ms / 16ch / L24: 2ch per flow, 8 flows. This is an EXTENSION
    // (192 kHz is outside AES67) and the maximum flow count; no single stream
    // can carry it.
    let config = unsafe { super::config((&auto_config(16, 192_000, 1_000, 0)).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 8);
    assert!(config.flows.iter().all(|flow| flow.channels == 2));
    // 192 kHz / 2 ms / 16ch / L24 would need 16 flows (> 8); reject.
    assert!(unsafe { super::config((&auto_config(16, 192_000, 2_000, 0)).cast()) }.is_none());
    // 192 kHz / 1 ms / 8ch / L16: 3072 B -> 3 flows of 3/3/2 channels.
    let config = unsafe { super::config((&auto_config(8, 192_000, 1_000, 1)).cast()) }.unwrap();
    assert_eq!(
        config.flows.iter().map(|f| f.channels).collect::<Vec<_>>(),
        [3, 3, 2]
    );
}

#[test]
fn single_flow_mode_is_capped_to_the_eight_channel_profile() {
    let mut v2 = auto_config(8, 192_000, 125, 0);
    v2.base.flow_mode = 1;
    let config = unsafe { super::config((&v2).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 1);
    assert_eq!(config.flows[0].channels, 8);

    // No 16-channel single flow: split with flow_mode = auto (2 x 8ch).
    let mut v2 = auto_config(16, 192_000, 125, 0);
    v2.base.flow_mode = 1;
    assert!(unsafe { super::config((&v2).cast()) }.is_none());
    v2.base.flow_mode = 0;
    assert_eq!(
        unsafe { super::config((&v2).cast()) }.unwrap().flows.len(),
        2
    );
}

#[test]
fn sixty_four_channels_map_to_eight_eight_channel_flows() {
    let mut v2 = auto_config(64, 48_000, 1_000, 0);
    v2.base.default_src_interface_ip = IP.octets();
    let config = unsafe { super::config((&v2).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 8);
    assert!(config.flows.iter().all(|flow| flow.channels == 8));
    assert!(
        config
            .flows
            .iter()
            .all(|flow| flow.destination.port() == 5004)
    );
}

fn flow_row(dest: [u8; 4], port: u16, offset: u32) -> FlowV1 {
    FlowV1 {
        dest_ip: dest,
        src_interface_ip: IP.octets(),
        port,
        ttl: 32,
        channels: 4,
        channel_offset: offset,
        ..FlowV1::default()
    }
}

#[test]
fn strict_ports_share_5004_and_reject_divergence() {
    let rows = [
        flow_row([239, 1, 0, 1], 5004, 0),
        flow_row([239, 1, 0, 2], 5004, 4),
    ];
    let mut v2 = auto_config(8, 48_000, 1_000, 0);
    v2.base.flow_mode = 2;
    v2.base.flow_count = 2;
    v2.base.flows = rows.as_ptr();
    let config = unsafe { super::config((&v2).cast()) }.unwrap();
    assert_eq!(config.flows.len(), 2);
    assert!(
        config
            .flows
            .iter()
            .all(|flow| flow.destination.port() == 5004)
    );

    // Divergent per-flow port is a strict-mode error (legacy 5010/5012 plans
    // must migrate to 5004 + unique groups).
    let divergent = [
        flow_row([239, 1, 0, 1], 5004, 0),
        flow_row([239, 1, 0, 2], 5006, 4),
    ];
    v2.base.flows = divergent.as_ptr();
    assert!(unsafe { super::config((&v2).cast()) }.is_none());

    // Explicit/legacy policy accepts even per-flow ports for unicast labs.
    v2.port_policy = 1;
    let unicast = [
        flow_row([192, 0, 2, 20], 5004, 0),
        flow_row([192, 0, 2, 20], 5006, 4),
    ];
    v2.base.flows = unicast.as_ptr();
    assert!(unsafe { super::config((&v2).cast()) }.is_some());

    let odd = [
        flow_row([239, 1, 0, 1], 5005, 0),
        flow_row([239, 1, 0, 2], 5004, 4),
    ];
    v2.base.flows = odd.as_ptr();
    assert!(unsafe { super::config((&v2).cast()) }.is_none());
}

#[test]
fn redundant_flag_derives_the_secondary_port() {
    let mut v2 = auto_config(2, 48_000, 1_000, 0);
    v2.base.default_dest_ip = IP.octets();
    v2.base.redundant = 1;
    let config = unsafe { super::config((&v2).cast()) }.unwrap();
    assert!(config.redundant);
    assert_eq!(config.flows[0].destination.port(), 5004);
    v2.base.default_port = 65_534;
    assert!(unsafe { super::config((&v2).cast()) }.is_none());
}

#[test]
fn oversized_explicit_multi_flow_is_rejected() {
    // Multi mode caps each flow at 8 channels (AES67 media profile).
    let rows = [FlowV1 {
        dest_ip: [239, 1, 0, 1],
        src_interface_ip: IP.octets(),
        port: 5004,
        ttl: 32,
        channels: 9,
        channel_offset: 0,
        ..FlowV1::default()
    }];
    let mut v2 = auto_config(9, 48_000, 1_000, 0);
    v2.base.flow_mode = 2;
    v2.base.flow_count = 1;
    v2.base.flows = rows.as_ptr();
    assert!(unsafe { super::config((&v2).cast()) }.is_none());

    // Explicit 11ch L24 at 1 ms exceeds the 1440-byte payload budget even in
    // single mode (1440 / 144 = 10 channels maximum).
    let rows = [FlowV1 {
        dest_ip: [239, 1, 0, 1],
        src_interface_ip: IP.octets(),
        port: 5004,
        ttl: 32,
        channels: 11,
        channel_offset: 0,
        ..FlowV1::default()
    }];
    let mut v2 = auto_config(11, 48_000, 1_000, 0);
    v2.base.flow_mode = 1;
    v2.base.flow_count = 1;
    v2.base.flows = rows.as_ptr();
    assert!(unsafe { super::config((&v2).cast()) }.is_none());
}

#[cfg(windows)]
#[test]
fn windows_product_dll_exports_and_loads() {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn LoadLibraryW(path: *const u16) -> *mut c_void;
        fn GetProcAddress(module: *mut c_void, name: *const c_char) -> *mut c_void;
        fn FreeLibrary(module: *mut c_void) -> i32;
    }

    let Some(path) = std::env::var_os("NSP_AES67_TEST_DLL") else {
        eprintln!("Set NSP_AES67_TEST_DLL to run the product load smoke test");
        return;
    };
    let path = std::fs::canonicalize(path).unwrap();
    let path: Vec<_> = path.as_os_str().encode_wide().chain([0]).collect();
    unsafe {
        let module = LoadLibraryW(path.as_ptr());
        assert!(!module.is_null(), "{}", std::io::Error::last_os_error());
        let symbol = |name: &CStr| {
            let symbol = GetProcAddress(module, name.as_ptr());
            assert!(!symbol.is_null(), "missing {name:?}");
            symbol
        };
        let create: unsafe extern "C" fn(*mut *mut Session) -> i32 =
            mem::transmute(symbol(c"nsp_aes67_create"));
        let close: unsafe extern "C" fn(*mut Session, u32) -> i32 =
            mem::transmute(symbol(c"nsp_aes67_close"));
        for name in [
            c"nsp_aes67_configure",
            c"nsp_aes67_start",
            c"nsp_aes67_stop",
            c"nsp_aes67_poll_lifecycle_event",
            c"nsp_aes67_counters",
            c"nsp_aes67_sdp",
            c"nsp_aes67_submit_leased",
        ] {
            symbol(name);
        }
        let mut handle = ptr::null_mut();
        assert_eq!(create(&mut handle), 0);
        assert_eq!(close(handle, 1000), 0);
        assert_ne!(FreeLibrary(module), 0);
    }
}
fn topology_config(
    channels: u32,
    rate: u32,
    ptime: u32,
    depth: u8,
    mode: u8,
    gm_capable: u8,
) -> ConfigV3 {
    let mut v3 = ConfigV3 {
        ptp_mode: mode,
        ptp_gm_capable: gm_capable,
        ..ConfigV3::default()
    };
    v3.base = auto_config(channels, rate, ptime, depth);
    v3.base.base.struct_size = mem::size_of::<ConfigV3>() as u32;
    v3
}

#[test]
fn ptp_topology_fields_parse_from_the_appended_abi() {
    assert_eq!(mem::size_of::<ConfigV3>(), 168);
    let mut v3 = topology_config(2, 48_000, 1_000, 0, 2, 1);
    v3.ptp_priority1 = 10;
    v3.ptp_priority2 = 20;
    v3.ptp_announce_timeout_ms = 4_000;
    v3.ptp_gm_utc_offset = 37;
    let config = unsafe { super::config(v3.cast()) }.unwrap();
    assert_eq!(config.ptp.mode, ptp::PtpMode::Gm);
    assert_eq!(config.ptp.priority1, 10);
    assert_eq!(config.ptp.priority2, 20);
    assert!(config.ptp.gm_capable);
    assert_eq!(config.ptp.announce_timeout_ns, 4_000 * NS_PER_MS);
    assert_eq!(config.ptp.gm_utc_offset, Some(37));
    assert_eq!(config.ptp.gm_identity, None);

    // ARB: the -1 sentinel means "no UTC offset configured".
    let mut arb = topology_config(2, 48_000, 1_000, 0, 1, 0);
    arb.ptp_gm_utc_offset = -1;
    let config = unsafe { super::config(arb.cast()) }.unwrap();
    assert_eq!(config.ptp.mode, ptp::PtpMode::Auto);
    assert_eq!(config.ptp.gm_utc_offset, None);
    assert!(!config.ptp.gm_capable);
    // Zero priority selects the AES67 default 128 ("unchanged" semantics).
    assert_eq!(config.ptp.priority1, 128);
    // Announce timeout zero selects the 6000 ms profile default.
    let default_timeout = topology_config(2, 48_000, 1_000, 0, 1, 0);
    let config = unsafe { super::config(default_timeout.cast()) }.unwrap();
    assert_eq!(config.ptp.announce_timeout_ns, 6_000 * NS_PER_MS);

    // Invalid ptp_mode values must be rejected (fail closed).
    for mode in 4..=255u8 {
        let mut invalid = topology_config(2, 48_000, 1_000, 0, 1, 0);
        invalid.ptp_mode = mode;
        assert!(
            unsafe { super::config(invalid.cast()) }.is_none(),
            "mode {mode}"
        );
    }
    let mut bad_timeout = topology_config(2, 48_000, 1_000, 0, 1, 0);
    bad_timeout.ptp_announce_timeout_ms = 999;
    assert!(unsafe { super::config(bad_timeout.cast()) }.is_none());
    let mut bad_offset = topology_config(2, 48_000, 1_000, 0, 2, 1);
    bad_offset.ptp_gm_utc_offset = 128;
    assert!(unsafe { super::config(bad_offset.cast()) }.is_none());
    let mut bad_capable = topology_config(2, 48_000, 1_000, 0, 1, 0);
    bad_capable.ptp_gm_capable = 2;
    assert!(unsafe { super::config(bad_capable.cast()) }.is_none());
    let mut bad_reserved = topology_config(2, 48_000, 1_000, 0, 1, 0);
    bad_reserved.reserved = [1, 0, 0, 0, 0, 0];
    assert!(unsafe { super::config(bad_reserved.cast()) }.is_none());
}

#[test]
fn ptp_local_mode_starts_without_ptp_and_reports_local_sdp() {
    let handle = Handle::new();
    let v3 = topology_config(2, 48_000, 1_000, 0, 3, 0);
    assert_eq!(handle.configure_v3(&v3), 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, 0);
    assert_eq!(handle.poll().unwrap().kind, 1);
    let status = handle.ptp_status();
    assert_eq!(status.mode, ptp::PtpMode::Local.as_abi());
    assert_eq!(status.role, ptp::Role::Local.as_abi());
    assert_eq!(status.source, ptp::Source::Local.as_abi());
    assert_eq!(status.time_traceable, 0);
    assert_eq!(status.ptp_timescale, 0);
    assert_eq!(status.gm_identity, [0; 8]);
    let reason = CStr::from_bytes_until_nul(&status.reason)
        .unwrap()
        .to_str()
        .unwrap();
    assert_eq!(reason, "configured_local");
    let mut counters = Counters::default();
    assert_eq!(unsafe { nsp_aes67_counters(handle.0, &mut counters) }, 0);
    assert_eq!(counters.locked, 0, "local mode never claims a PTP lock");
    let size = unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) };
    assert!(size > 0, "local mode has an SDP immediately");
    let mut sdp = vec![0_u8; size as usize];
    assert_eq!(
        unsafe { nsp_aes67_sdp(handle.0, 0, sdp.as_mut_ptr().cast(), sdp.len()) },
        size
    );
    let text = CStr::from_bytes_with_nul(&sdp).unwrap().to_str().unwrap();
    assert!(text.contains("a=ts-refclk:local\r\na=mediaclk:direct=0\r\n"));
    assert!(!text.contains("ts-refclk:ptp"));
    assert!(!text.contains("localmac"));
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1500) }, 0);
}

#[test]
fn ptp_auto_without_a_gm_falls_back_to_local() {
    let handle = Handle::new();
    let mut v3 = topology_config(2, 48_000, 1_000, 0, 1, 0);
    v3.ptp_announce_timeout_ms = 1_000;
    assert_eq!(handle.configure_v3(&v3), 0);
    let started = Instant::now();
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, 0);
    assert!(started.elapsed() >= Duration::from_millis(900));
    let status = handle.ptp_status();
    assert_eq!(status.mode, ptp::PtpMode::Auto.as_abi());
    assert_eq!(status.role, ptp::Role::Local.as_abi());
    assert_eq!(status.source, ptp::Source::Local.as_abi());
    let reason = CStr::from_bytes_until_nul(&status.reason)
        .unwrap()
        .to_str()
        .unwrap();
    assert_eq!(reason, "announce_timeout_local");
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1500) }, 0);
}

#[test]
fn ptp_gm_mode_without_a_readable_identity_is_refused() {
    let handle = Handle::new();
    let v3 = topology_config(2, 48_000, 1_000, 0, 2, 1);
    assert_eq!(handle.configure_v3(&v3), 0);
    // The loopback adapter has no EUI-48 MAC; gm mode must refuse rather than
    // invent an identity (DESIGN-TOPO.md section 3).
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, session::PTP_IDENTITY);
    let mut status = PtpStatusV1::default();
    assert_eq!(
        unsafe { nsp_aes67_ptp_status(handle.0, &mut status) },
        session::STATE,
        "no status snapshot without a running worker"
    );
}

fn read_sdp(handle: &Handle) -> String {
    let size = unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) };
    assert!(size > 0, "SDP available");
    let mut sdp = vec![0_u8; size as usize];
    assert_eq!(
        unsafe { nsp_aes67_sdp(handle.0, 0, sdp.as_mut_ptr().cast(), sdp.len()) },
        size
    );
    CStr::from_bytes_with_nul(&sdp)
        .unwrap()
        .to_str()
        .unwrap()
        .to_owned()
}

/// SDP session version from the o= line (o=- <session-id> <version> IN IP4 <addr>).
fn sdp_version(sdp: &str) -> u64 {
    let origin = sdp.lines().find(|line| line.starts_with("o=")).unwrap();
    origin.split_whitespace().nth(2).unwrap().parse().unwrap()
}

#[test]
fn role_switch_from_local_to_slave_raises_the_sdp_version() {
    let audio = bind_even();
    audio
        .set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    let event = bind();
    let general = bind();
    let local_event = bind();
    let local_general = bind();
    let mut v3 = topology_config(2, 48_000, 1_000, 0, 1, 0);
    v3.base.base.default_port = port(&audio);
    v3.base.base.default_dest_ip = IP.octets();
    v3.base.base.ptp_event_port = port(&event);
    v3.base.base.ptp_general_port = port(&general);
    v3.base.base.ptp_local_event_port = port(&local_event);
    v3.base.base.ptp_local_general_port = port(&local_general);
    // Loopback integration with a real QPC servo: keep the lock window well
    // above the 8-exchange/750 ms fit gate so a loaded test host cannot turn
    // this into a spurious role oscillation.
    v3.base.base.lock_timeout_ms = 12_000;
    v3.ptp_announce_timeout_ms = 1_000;
    drop((local_event, local_general));
    let handle = Handle::new();
    assert_eq!(handle.configure_v3(&v3), 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, 0);
    assert_eq!(handle.poll().unwrap().kind, 1);
    // AUTO without a GM falls back to local first: SDP advertises ts-refclk:local.
    let local = read_sdp(&handle);
    assert!(local.contains("a=ts-refclk:local\r\n"));
    assert!(!local.contains("ts-refclk:ptp"));
    let local_version = sdp_version(&local);
    // An external GM now appears: the role machine needs two consecutive
    // Announces to leave local, then the follower needs a clock fit.
    let gm = Mock::spawn(
        event,
        general,
        v3.base.base.ptp_local_event_port,
        v3.base.base.ptp_local_general_port,
        0x12,
    );
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut ptp = String::new();
    while Instant::now() < deadline {
        // Loopback integration: real sockets and wall-clock servo. Allow SDP to
        // be briefly unavailable during re-render and keep polling instead of
        // failing on a single unlucky sample.
        let size = unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) };
        if size > 0 {
            let candidate = read_sdp(&handle);
            if candidate.contains("a=ts-refclk:ptp=IEEE1588-2008:12-12-12-12-12-12-12-12:19") {
                ptp = candidate;
                break;
            }
        }
        thread::sleep(Duration::from_millis(10));
    }
    assert!(
        !ptp.is_empty(),
        "role switch to slave re-advertises ts-refclk:ptp"
    );
    assert!(
        sdp_version(&ptp) > local_version,
        "role change bumps the SDP version"
    );
    assert_eq!(handle.ptp_status().role, ptp::Role::Slave.as_abi());
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1500) }, 0);
    drop(gm);
}
