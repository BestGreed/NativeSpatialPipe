//! Local scheduler measurement only; no network sockets.
#[cfg(windows)]
use inferno_windows_clock::local::{ClockPriorityGuard, LocalClock};
#[cfg(windows)]
use std::time::Duration;
#[cfg(windows)]
fn main() {
    let clock = LocalClock::new().unwrap();
    for managed in [false, true] {
        let _guard = managed.then(|| ClockPriorityGuard::enter().unwrap());
        let mut times = Vec::with_capacity(1000);
        for _ in 0..1000 {
            let start = clock.now_ns();
            std::thread::sleep(Duration::from_micros(1250));
            times.push(clock.now_ns()-start);
        }
        times.sort_unstable();
        println!("managed={managed} requested_ns=1250000 min={} p50={} p99={} max={}", times[0],times[500],times[990],times[999]);
    }
}
#[cfg(not(windows))]
fn main() {
    eprintln!("This diagnostic measures the Windows scheduler.");
}
