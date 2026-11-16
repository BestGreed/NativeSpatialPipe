//! Real UDP/QPC integration, with a local mock master, never production ports.
//! Tolerances test socket plumbing, not network synchronization precision.
use inferno_windows_clock::{
    envelope::EnvelopeServo as Servo,
    local::LocalClock,
    ptp::{self, Identity, Message, Pairer},
    servo::Config,
};
use std::{net::UdpSocket, time::Duration};

const MASTER: Identity = Identity {
    uuid: [2, 0, 0, 0, 0, 1],
    port: 1,
};
const LOCAL: Identity = Identity {
    uuid: [2, 0, 0, 0, 0, 2],
    port: 1,
};
const EPOCH: i64 = 1_700_000_000_000_000_000;
fn stamp(p: &mut [u8], at: usize, ns: i64) {
    p[at..at + 4].copy_from_slice(&((ns / 1_000_000_000) as u32).to_be_bytes());
    p[at + 4..at + 8].copy_from_slice(&((ns % 1_000_000_000) as u32).to_be_bytes());
}

#[test]
fn qpc_udp_two_step_mock_master() {
    let client = UdpSocket::bind("127.0.0.1:0").unwrap();
    let master = UdpSocket::bind("127.0.0.1:0").unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    master
        .set_read_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    let peer = client.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let clock = LocalClock::new().unwrap();
        let mut buffer = [0u8; 2048];
        for seq in 0..20u16 {
            let mut sync = ptp::delay_request(MASTER, seq, &ptp::DEFAULT_SUBDOMAIN);
            sync[32] = 0;
            let t1 = clock.now_ns() + EPOCH;
            master.send_to(&sync, peer).unwrap();
            let mut fu = sync[..52].to_vec();
            fu[20] = 2;
            fu[32] = 2;
            fu[42..44].copy_from_slice(&seq.to_be_bytes());
            stamp(&mut fu, 44, t1);
            master.send_to(&fu, peer).unwrap();
            let (n, requester) = master.recv_from(&mut buffer).unwrap();
            let t4 = clock.now_ns() + EPOCH;
            assert_eq!(n, 124);
            assert_eq!(buffer[32], 1);
            let mut response = sync[..60].to_vec();
            response[20] = 2;
            response[32] = 3;
            stamp(&mut response, 40, t4);
            response[50..56].copy_from_slice(&buffer[22..28]);
            response[56..58].copy_from_slice(&buffer[28..30]);
            response[58..60].copy_from_slice(&buffer[30..32]);
            master.send_to(&response, requester).unwrap();
            std::thread::sleep(Duration::from_millis(125));
        }
    });
    let clock = LocalClock::new().unwrap();
    let mut pairer = Pairer::new(LOCAL);
    let config = Config {
        delay_margin_ns: 5_000_000,
        max_residual_ns: 1_000_000,
        max_rate_ppb: 5_000_000,
        ..Config::default()
    };
    let mut servo = Servo::new(config);
    let mut received = 0;
    let mut accepted = 0;
    let mut buffer = [0u8; 2048];
    while received < 20 {
        let (n, source) = client.recv_from(&mut buffer).unwrap();
        let now = clock.now_ns();
        match ptp::decode(&buffer[..n], &ptp::DEFAULT_SUBDOMAIN).unwrap() {
            Message::Sync { source, sequence } => pairer.sync(source, sequence, now).unwrap(),
            Message::FollowUp {
                source: identity,
                associated_sequence,
                origin_ns,
            } => {
                let seq = pairer
                    .follow_up(identity, associated_sequence, origin_ns, clock.now_ns())
                    .unwrap();
                client
                    .send_to(
                        &ptp::delay_request(LOCAL, seq, &ptp::DEFAULT_SUBDOMAIN),
                        source,
                    )
                    .unwrap();
            }
            Message::DelayResponse {
                source,
                requester,
                request_sequence,
                receipt_ns,
            } => {
                let e = pairer
                    .response(source, requester, request_sequence, receipt_ns, now)
                    .unwrap();
                if servo.observe(e, now).is_ok() {
                    accepted += 1;
                }
                received += 1;
            }
            _ => panic!("unexpected mock message"),
        }
    }
    server.join().unwrap();
    assert!(accepted >= 8);
    let now = clock.now_ns();
    let mapping = servo.mapping(now).expect("mock follower did not acquire");
    assert!((mapping.at(now).unwrap() - (now + EPOCH)).abs() < 20_000_000);
}
