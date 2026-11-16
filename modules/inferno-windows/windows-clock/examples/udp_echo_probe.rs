//! Bounded, peer-restricted UDP echo probe; no Dante, audio, PTP or clock changes.
use inferno_windows_clock::local::LocalClock;
use std::{error::Error, fs::File, io::{self, Write}, net::{IpAddr, SocketAddr, UdpSocket}, sync::Arc, thread, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};
const MAGIC: &[u8; 8] = b"INFPRB01";
const SIZE: usize = 369; // Match the current 60-frame, two-slot, 24-bit audio payload size.
const PPS: u64 = 800;
fn get(packet: &[u8], offset: usize) -> u64 { u64::from_be_bytes(packet[offset..offset+8].try_into().unwrap()) }
fn put(packet: &mut [u8], offset: usize, value: u64) { packet[offset..offset+8].copy_from_slice(&value.to_be_bytes()); }
fn transient(error: &io::Error) -> bool { matches!(error.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) }
fn duration(text: &str) -> Result<u64, Box<dyn Error>> {
    let seconds=text.parse()?;
    if !(1..=600).contains(&seconds) { return Err("duration must be 1..600 seconds".into()); }
    Ok(seconds)
}
fn echo(bind: SocketAddr, peer: IpAddr, seconds: u64) -> Result<(), Box<dyn Error>> {
    let socket=UdpSocket::bind(bind)?;
    socket.set_read_timeout(Some(Duration::from_millis(100)))?;
    let clock=LocalClock::new()?;
    #[cfg(windows)]
    let _priority=inferno_windows_clock::local::ClockPriorityGuard::enter().ok();
    let end=Instant::now()+Duration::from_secs(seconds);
    let mut packet=[0u8; SIZE+1];let mut replies=0u64;
    println!("ECHO_READY bind={} allowed_peer={peer} seconds={seconds}",socket.local_addr()?);
    while Instant::now()<end {
        match socket.recv_from(&mut packet) {
            Ok((SIZE, source)) if source.ip()==peer && &packet[..8]==MAGIC => {
                let received=clock.now_ns() as u64;
                put(&mut packet,32,received);
                let sent=clock.now_ns() as u64;put(&mut packet,40,sent);
                socket.send_to(&packet[..SIZE],source)?;replies+=1;
            }
            Ok(_) => {},
            Err(error) if transient(&error) => {},
            Err(error) => return Err(error.into()),
        }
    }
    println!("ECHO_STOPPED replies={replies}");Ok(())
}
fn quantiles(name: &str, mut values: Vec<u64>) {
    values.sort_unstable();
    if values.is_empty() { println!("{name}: no samples");return; }
    let pick=|p:f64| values[(p*(values.len()-1) as f64) as usize] as f64/1e6;
    println!("{name}_ms min={:.4} p50={:.4} p95={:.4} p99={:.4} max={:.4}",pick(0.0),pick(0.5),pick(0.95),pick(0.99),pick(1.0));
}
fn client(bind: SocketAddr, peer: SocketAddr, seconds: u64, csv: &str) -> Result<(), Box<dyn Error>> {
    let socket=UdpSocket::bind(bind)?;socket.connect(peer)?;
    socket.set_read_timeout(Some(Duration::from_millis(100)))?;
    let sender=socket.try_clone()?;let clock=Arc::new(LocalClock::new()?);let tx_clock=clock.clone();
    let cookie=SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos() as u64;
    let start=Instant::now();let end=start+Duration::from_secs(seconds);
    println!("CLIENT_READY bind={} peer={peer} payload={SIZE} target_pps={PPS} seconds={seconds}",socket.local_addr()?);
    let tx=thread::spawn(move || -> io::Result<(u64,u64,Vec<u64>)> {
        #[cfg(windows)]
        let _priority=inferno_windows_clock::local::ClockPriorityGuard::enter().ok();
        let mut packet=[0u8;SIZE];packet[..8].copy_from_slice(MAGIC);put(&mut packet,8,cookie);
        let interval=Duration::from_nanos(1_000_000_000/PPS);let mut deadline=start;
        let mut sequence=0u64;let mut last=None;let mut gaps=Vec::new();let mut resets=0;
        while Instant::now()<end {
            while let Some(remaining)=deadline.checked_duration_since(Instant::now()) {
                if remaining>Duration::from_micros(200) { thread::sleep(remaining-Duration::from_micros(100)); }
                else { std::hint::spin_loop(); }
            }
            if Instant::now()>=end {break;}
            put(&mut packet,16,sequence);let sent=tx_clock.now_ns() as u64;put(&mut packet,24,sent);
            if let Some(old)=last {gaps.push(sent-old);}last=Some(sent);
            sender.send(&packet)?;sequence+=1;deadline+=interval;
            if Instant::now().saturating_duration_since(deadline)>Duration::from_millis(5) {deadline=Instant::now()+interval;resets+=1;}
        }
        Ok((sequence,resets,gaps))
    });
    #[cfg(windows)]
    let _priority=inferno_windows_clock::local::ClockPriorityGuard::enter().ok();
    let mut output=io::BufWriter::new(File::create(csv)?);
    writeln!(output,"sequence,local_send_ns,local_receive_ns,remote_processing_ns,rtt_ns")?;
    let mut packet=[0u8;SIZE+1];let mut received=vec![false;(seconds*PPS+1) as usize];
    let mut rtts=Vec::new();let mut processing=Vec::new();let mut duplicates=0;let mut reordered=0;let mut high=None;
    while Instant::now()<end+Duration::from_secs(1) {
        match socket.recv(&mut packet) {
            Ok(SIZE) if &packet[..8]==MAGIC && get(&packet,8)==cookie => {
                let now=clock.now_ns() as u64;let sequence=get(&packet,16) as usize;
                if sequence>=received.len() {continue;}
                if received[sequence] {duplicates+=1;continue;}
                received[sequence]=true;
                if high.is_some_and(|old|sequence<old) {reordered+=1;}high=Some(high.map_or(sequence,|old:usize|old.max(sequence)));
                let sent=get(&packet,24);let remote=get(&packet,40).saturating_sub(get(&packet,32));
                let rtt=now.saturating_sub(sent);rtts.push(rtt);processing.push(remote);
                writeln!(output,"{sequence},{sent},{now},{remote},{rtt}")?;
            }
            Ok(_) => {},
            Err(error) if transient(&error) => {},
            Err(error) => return Err(error.into()),
        }
    }
    let (sent,resets,gaps)=tx.join().map_err(|_|"sender panicked")??;
    println!("RESULT sent={sent} received={} missing={} duplicate={duplicates} reordered={reordered} pacing_resets={resets}",rtts.len(),sent.saturating_sub(rtts.len() as u64));
    quantiles("rtt",rtts);quantiles("remote_processing",processing);quantiles("actual_send_gap",gaps);
    println!("RTT combines both directions and host scheduling; timestamps are not synchronized one-way measurements.");
    output.flush()?;Ok(())
}
fn main() -> Result<(), Box<dyn Error>> {
    let args:Vec<String>=std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("--echo") if args.len()==4 => echo(args[1].parse()?,args[2].parse()?,duration(&args[3])?),
        Some("--client") if args.len()==5 => client(args[1].parse()?,args[2].parse()?,duration(&args[3])?,&args[4]),
        _ => Err("usage: udp_echo_probe --echo BIND_IP:PORT ALLOWED_PEER_IP SECONDS | --client BIND_IP:PORT PEER_IP:PORT SECONDS OUTPUT.csv".into()),
    }
}
