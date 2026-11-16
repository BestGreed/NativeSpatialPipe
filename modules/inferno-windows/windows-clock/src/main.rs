use inferno_windows_clock::{
    local::LocalClock,
    ptp,
    servo::{Config, Exchange, Servo, Snapshot},
};
use std::{
    error::Error,
    net::Ipv4Addr,
    time::Duration,
};

fn simulate() -> Result<(), Box<dyn Error>> {
    let clock = LocalClock::new()?;
    let start = clock.now_ns();
    let mut servo = Servo::new(Config::default());
    let mut snapshot = Snapshot::default();
    let remote_base = 1_790_000_000_000_000_000i64;
    let ppb = 80_000i64;
    let remote = |local: i64| {
        remote_base
            + (local - start)
            + (((local - start) as i128 * ppb as i128) / 1_000_000_000) as i64
    };
    for i in 0..48 {
        let t2 = start + i * 250_000_000;
        let t3 = t2 + 1_000_000;
        let e = Exchange {
            t1: remote(t2) - 50_000,
            t2,
            t3,
            t4: remote(t3) + 50_000,
        };
        let status = servo.observe(e, t3 + 1_000_000)?;
        snapshot.publish(servo.mapping(t3));
        if i % 8 == 0 || i == 47 {
            println!(
                "synthetic sample={i} locked={} rate_ppb={} residual_ns={:.1} delay_ns={}",
                status.locked, status.rate_ppb, status.residual_ns, status.delay_ns
            );
        }
    }
    let now = start + 47 * 250_000_000 + 1_000_000;
    let mapping = snapshot.load(now).ok_or("synthetic servo failed to lock")?;
    let error = mapping.at(now).unwrap() - remote(now);
    println!(
        "synthetic_only=true mapping_error_ns={error} stale_revoked={}",
        snapshot.load(now + 2_000_000_001).is_none()
    );
    if error.abs() > 10 {
        return Err("synthetic mapping error exceeds 10 ns".into());
    }
    Ok(())
}

fn probe() -> Result<(), Box<dyn Error>> {
    let clock = LocalClock::new()?;
    let mut last = clock.now_ns();
    let mut min_delta = i64::MAX;
    let start = last;
    for _ in 0..100_000 {
        let now = clock.now_ns();
        if now < last {
            return Err("local clock moved backward".into());
        }
        if now > last {
            min_delta = min_delta.min(now - last);
        }
        last = now;
    }
    println!(
        "qpc_reads=100000 elapsed_ns={} minimum_positive_delta_ns={min_delta} monotonic=true",
        last - start
    );
    Ok(())
}

fn discover(args: &[String]) -> Result<(), Box<dyn Error>> {
    if args.len() != 2 {
        return Err("usage: --discover INTERFACE_IPV4 SECONDS".into());
    }
    let interface: Ipv4Addr = args[0].parse()?;
    let seconds: u64 = args[1].parse()?;
    if !(1..=120).contains(&seconds) {
        return Err("duration must be 1..120 seconds".into());
    }
    let event = inferno_windows_clock::net::bind_exclusive_v4(319)?;
    let general = inferno_windows_clock::net::bind_exclusive_v4(320)?;
    for socket in [&event, &general] {
        socket.join_multicast_v4(&Ipv4Addr::new(224, 0, 1, 129), &interface)?;
        socket.set_nonblocking(true)?;
    }
    let clock = LocalClock::new()?;
    let end = clock.now_ns() + seconds as i64 * 1_000_000_000;
    let mut counts = std::collections::BTreeMap::<String, u64>::new();
    let mut buffer = [0u8; 2048];
    let mut total = 0;
    println!("passive_discovery=true interface={interface} duration_seconds={seconds} no_ptp_messages_sent=true");
    while clock.now_ns() < end {
        for (port, socket) in [(319, &event), (320, &general)] {
            for _ in 0..64 {
                let (size, source) = match socket.recv_from(&mut buffer) {
                    Ok(value) => value,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(error) => return Err(error.into()),
                };
                let message = format!(
                    "{:?}",
                    ptp::decode(&buffer[..size], &ptp::DEFAULT_SUBDOMAIN)
                );
                let key = format!("source={source} port={port} {message}");
                let count = counts.entry(key.clone()).or_default();
                *count += 1;
                total += 1;
                if *count == 1 && total <= 32 {
                    let prefix: String = buffer[..size.min(64)]
                        .iter()
                        .map(|v| format!("{v:02x}"))
                        .collect();
                    println!("{key} length={size} prefix={prefix}");
                }
            }
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    println!(
        "discovery_packets={total} distinct_messages={}",
        counts.len()
    );
    if total == 0 {
        return Err("no PTP packets received; master not identified".into());
    }
    Ok(())
}

fn follow(args: &[String], multicast: bool) -> Result<(), Box<dyn Error>> {
    inferno_windows_clock::follower::follow(
        args,
        multicast,
        &std::sync::atomic::AtomicBool::new(false),
        |_| {},
    )
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("--discover") => discover(&args[1..]),
        Some("--simulate")=>simulate(),Some("--probe")=>probe(),Some("--follow-unicast")=>follow(&args[1..],false),
        Some("--follow-multicast")=>follow(&args[1..],true),
        _=>Err("choose --simulate, --probe, or --follow-unicast INTERFACE_IPV4 MASTER_IPV4 LOCAL_MAC_12HEX SECONDS".into()),
    }
}
