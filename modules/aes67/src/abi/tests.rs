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
        ..ConfigV1::default()
    }
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
                            Body::Announce { grandmaster: GM },
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
    let audio = bind();
    audio
        .set_read_timeout(Some(Duration::from_millis(50)))
        .unwrap();
    let handle = Handle::new();
    let mut config = config_base();
    config.default_port = port(&audio);
    config.ptp_event_port = port(&event);
    config.ptp_general_port = port(&general);
    config.ptp_local_event_port = port(&local_event);
    config.ptp_local_general_port = port(&local_general);
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
    let size = unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) };
    assert!(size > 0);
    let mut sdp = vec![0_u8; size as usize];
    assert_eq!(
        unsafe { nsp_aes67_sdp(handle.0, 0, sdp.as_mut_ptr().cast(), sdp.len()) },
        size
    );
    assert!(
        CStr::from_bytes_with_nul(&sdp)
            .unwrap()
            .to_str()
            .unwrap()
            .contains("a=ts-refclk:ptp=IEEE1588-2008:12-12-12-12-12-12-12-12:19")
    );

    let releases = AtomicUsize::new(0);
    let token = ptr::from_ref(&releases).cast_mut().cast();
    let samples = [0.25_f32, -0.5].repeat(24 * FRAMES);
    assert_eq!(
        unsafe {
            nsp_aes67_submit_leased(
                handle.0.cast(),
                123,
                samples.as_ptr(),
                (24 * FRAMES) as u32,
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
    assert_eq!(
        unsafe { nsp_aes67_sdp(handle.0, 0, ptr::null_mut(), 0) },
        session::STATE
    );
    // RTP must still be running after the lock-loss notification, then cease
    // at the holdover deadline while the follower is trying to reacquire.
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
    let event = bind();
    let general = bind();
    let local_event = bind();
    let local_general = bind();
    let audio = bind();
    audio
        .set_read_timeout(Some(Duration::from_millis(100)))
        .unwrap();
    let handle = Handle::new();
    let mut config = config_base();
    config.default_port = port(&audio);
    config.ptp_event_port = port(&event);
    config.ptp_general_port = port(&general);
    config.ptp_local_event_port = port(&local_event);
    config.ptp_local_general_port = port(&local_general);
    config.lock_timeout_ms = loopback_timeout();
    drop((local_event, local_general));
    let _gm = Mock::spawn(
        event,
        general,
        config.ptp_local_event_port,
        config.ptp_local_general_port,
        0x12,
    );
    assert_eq!(unsafe { nsp_aes67_configure(handle.0, &config) }, 0);
    assert_eq!(unsafe { nsp_aes67_start(handle.0) }, 0);
    assert_eq!(handle.poll().unwrap().kind, 1);

    let mut buf = [0; 1500];
    let first = audio.recv(&mut buf).unwrap();
    assert_eq!(first, 12 + FRAMES * 2 * 3);
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
        irregular += u64::from(ts.wrapping_sub(previous_ts) != 48);
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
    assert!(handle.poll().is_none(), "unexpected lifecycle event");
    assert_eq!(unsafe { nsp_aes67_stop(handle.0, 1000) }, 0);
}

#[test]
fn hardware_partial_init_and_start_timeout_release_all_local_ports() {
    let handle = Handle::new();
    let first = bind();
    let held = bind();
    let sink = bind();
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
fn abi_layout_and_default_multiflow_routing() {
    assert_eq!(mem::size_of::<FlowV1>(), 24);
    assert_eq!(mem::size_of::<ConfigV1>(), 128);
    assert_eq!(mem::offset_of!(ConfigV1, flows), 88);
    assert_eq!(mem::size_of::<Counters>(), 80);
    assert_eq!(mem::size_of::<Event>(), 12);
    let mut raw = config_base();
    raw.channels = 10;
    raw.input_channels = 2;
    let map = [1, 0, 0, 1, 0, 1, 1, 0, 0, 0];
    raw.channel_map = map.as_ptr();
    raw.ssrc_base = u32::MAX;
    let config = unsafe { super::config(&raw) }.unwrap();
    assert_eq!(config.map, map.map(|x| x as usize));
    assert_eq!(
        (
            config.flows[0].offset,
            config.flows[0].channels,
            config.flows[0].ssrc
        ),
        (0, 8, u32::MAX)
    );
    assert_eq!(
        (
            config.flows[1].offset,
            config.flows[1].channels,
            config.flows[1].ssrc
        ),
        (8, 2, 0)
    );
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
