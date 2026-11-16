//! Implementation of [usrvclock](https://gitlab.com/lumifaza/usrvclock) protocol

// Copyright 2024 Teodor Wozniak
//
// Licensed under the Apache License, Version 2.0, <LICENSE-APACHE or
// http://apache.org/licenses/LICENSE-2.0> or the MIT license <LICENSE-MIT or
// http://opensource.org/licenses/MIT>, at your option. This file may not be
// copied, modified, or distributed except according to those terms.

use std::{os::unix::net::{SocketAddr, UnixDatagram}, path::PathBuf, sync::Mutex, time::Duration};
use custom_error::custom_error;
use nix::{fcntl::OFlag, libc::{O_RDONLY, S_IFCHR, S_IFMT}, sys::stat::Mode, time::ClockId, libc};


const OVERLAY_SIZE_BYTES: usize = 40;

/// Clock overlay.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ClockOverlay {
    pub clock_id: i64,
    pub last_sync: i64,
    pub shift: i64,
    pub freq_scale: f64,
}

custom_error! { pub OverlayReceiveError
    PacketTooShort = "packet too short",
    UnexpectedData = "unexpected data in packet",
    UnsupportedMajorVersion = "unsupported major version",
    InvalidFlags = "invalid flags (client too old?)",
}

const PROTOCOL_MAJOR_VERSION: u16 = 1;
const PROTOCOL_MINOR_VERSION: u16 = 0;

#[cfg(all(target_os = "linux", target_env = "gnu"))]
pub const EMPTY_TIMEX: libc::timex = libc::timex {
    modes: 0,
    offset: 0,
    freq: 0,
    maxerror: 0,
    esterror: 0,
    status: 0,
    constant: 0,
    precision: 0,
    tolerance: 0,
    time: libc::timeval {
        tv_sec: 0,
        tv_usec: 0,
    },
    tick: 0,
    ppsfreq: 0,
    jitter: 0,
    shift: 0,
    stabil: 0,
    jitcnt: 0,
    calcnt: 0,
    errcnt: 0,
    stbcnt: 0,
    tai: 0,
    __unused1: 0,
    __unused2: 0,
    __unused3: 0,
    __unused4: 0,
    __unused5: 0,
    __unused6: 0,
    __unused7: 0,
    __unused8: 0,
    __unused9: 0,
    __unused10: 0,
    __unused11: 0,
};

#[cfg(all(target_os = "linux", target_env = "musl"))]
pub const EMPTY_TIMEX: libc::timex = libc::timex {
    modes: 0,
    offset: 0,
    freq: 0,
    maxerror: 0,
    esterror: 0,
    status: 0,
    constant: 0,
    precision: 0,
    tolerance: 0,
    time: libc::timeval {
        tv_sec: 0,
        tv_usec: 0,
    },
    tick: 0,
    ppsfreq: 0,
    jitter: 0,
    shift: 0,
    stabil: 0,
    jitcnt: 0,
    calcnt: 0,
    errcnt: 0,
    stbcnt: 0,
    tai: 0,
    __padding: [0; 11],
};

fn errno() -> libc::c_int {
    #[cfg(target_os = "linux")]
    unsafe {
        *libc::__errno_location()
    }

    #[cfg(not(target_os = "linux"))]
    unsafe {
        *libc::__error()
    }
}

#[inline(always)]
fn clock_now_ns(clock: nix::time::ClockId) -> i64 {
    let timespec = clock.now().unwrap();
    (timespec.tv_sec() as i64).wrapping_mul(1_000_000_000i64).wrapping_add(timespec.tv_nsec() as i64)
}

impl ClockOverlay {
    fn decode(buff: &[u8]) -> Result<Self, OverlayReceiveError> {
        if buff.len() < OVERLAY_SIZE_BYTES {
            return Err(OverlayReceiveError::PacketTooShort);
        }
        if buff[0]!=b'V' || buff[1]!=b'C' {
            return Err(OverlayReceiveError::UnexpectedData);
        }
        let major_version = u16::from_ne_bytes(buff[2..4].try_into().unwrap());
        if major_version != PROTOCOL_MAJOR_VERSION {
            return Err(OverlayReceiveError::UnsupportedMajorVersion);
        }
        let flags = u16::from_ne_bytes(buff[6..8].try_into().unwrap());
        if (flags & 1) == 0 {
            return Err(OverlayReceiveError::InvalidFlags);
        }
        Ok(Self {
            clock_id: i64::from_ne_bytes(buff[8..16].try_into().unwrap()),
            last_sync: i64::from_ne_bytes(buff[16..24].try_into().unwrap()),
            shift: i64::from_ne_bytes(buff[24..32].try_into().unwrap()),
            freq_scale: f64::from_ne_bytes(buff[32..40].try_into().unwrap()),
        })
    }

    /// Calculates timestamp in overlay clock's timescale given underlying clock's timestamp.
    /// All timestamps are in nanoseconds and may wrap.
    pub fn underlying_to_overlay_ns(&self, timestamp: i64) -> i64 {
        let elapsed = timestamp.wrapping_sub(self.last_sync);
        let correction = ((elapsed as f64) * self.freq_scale).round() as i64;
        timestamp.wrapping_add(self.shift).wrapping_add(correction)
    }

    /// Returns current underlying clock's timestamp in nanoseconds. May wrap.
    #[cfg(feature="unix")]
    pub fn now_underlying_ns(&self) -> i64 {
        clock_now_ns(nix::time::ClockId::from_raw(self.clock_id as nix::libc::clockid_t))
    }

    /// Returns current timestamp (in nanoseconds) obtained from underlying clock, then shifted and scaled to overlay clock's timescale. May wrap.
    #[cfg(feature="unix")]
    pub fn now_ns(&self) -> i64 {
        self.underlying_to_overlay_ns(self.now_underlying_ns())
    }

    /// Returns actual frequency correction factor, adjusted for hardware clock's scale
    #[cfg(target_os="linux")]
    pub fn freq_scale_including_hw(&self) -> f64 {
        let mut timex = libc::timex {
            ..EMPTY_TIMEX
        };
        // safety: clock_id is believed to be open
        // TODO: handle errors
        let _r = unsafe { libc::clock_adjtime(self.clock_id as _, &mut timex) };
        //println!("raw freq offset {} result {r} errno {}", timex.freq, errno());
        (timex.freq as f64 / 65_536_000_000.0) + self.freq_scale
    }
}

#[cfg(feature="unix")]
/// Guarded clock.
/// 
/// Workaround for situations where the underlying clock isn't guaranteed to be monotonic.
/// It may happen because software-timestamped network sockets can only use CLOCK_REALTIME which is settable via NTP.
/// Then, before PTP daemon notices that clock jumped and updates shift, we may have discontinuity.
/// It will hopefully get back to normal values after a while.
/// If it does not within a given timeout, SafeClock won't wait anymore and return input timestamp.
pub struct SafeClock {
    guard_clock: nix::time::ClockId,
    last_update: Option<(i64, i64)>,
    tolerance: f64,
    timeout_ns: i64,
    skewed: bool,
}

/// Timestamp returned by [`SafeClock::now`]
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct SafeTimestamp {
    /// Timestamp in nanoseconds. May wrap.
    pub nanos: i64,
    /// Whether timestamp was estimated because time from overlay clock looked suspicious.
    pub estimated: bool,
}

impl SafeTimestamp {
    /// Returns `Some(nanoseconds)` if timestamp wasn't estimated (should be precise), `None` otherwise.
    pub fn precise_ns(&self) -> Option<i64> {
        if !self.estimated {
            Some(self.nanos)
        } else {
            None
        }
    }
}

#[cfg(feature="unix")]
impl SafeClock {
    /// Creates new `SafeClock` with specified tolerance (in fractional seconds per second) and a default guard clock.
    /// 
    /// To prevent false detection of clock jumps, tolerance should be larger than maximum expected clock drift that can accumulate between
    /// subsequent calls to [`SafeClock::now`].
    pub fn new(tolerance: f64, timeout_ns: i64) -> Self {
        Self::new_with_guard(tolerance, timeout_ns, nix::time::ClockId::CLOCK_MONOTONIC)
    }

    /// Creates new `SafeClock` with specified tolerance (in fractional seconds per second) and specified guard clock.
    /// 
    /// Guard clock should be monotonic to serve its function ([`nix::time::ClockId::CLOCK_MONOTONIC`] or its variant `_RAW`, `_COARSE`, `_FAST`, `_PRECISE`).
    /// 
    /// To prevent false detection of clock jumps, tolerance should be larger than maximum expected clock drift that can accumulate between
    /// subsequent calls to [`SafeClock::now`].
    pub fn new_with_guard(tolerance: f64, timeout_ns: i64, guard_clock: nix::time::ClockId) -> Self {
        Self {
            guard_clock,
            last_update: None,
            tolerance,
            timeout_ns,
            skewed: false,
        }
    }

    /// Returns current timestamp. Checks whether overlay's underlying clock has jumped, with tolerance specified in [`SafeClock::new`].
    pub fn now(&mut self, overlay: &ClockOverlay) -> SafeTimestamp {
        let now_main = overlay.now_ns();
        if (overlay.clock_id as nix::libc::clockid_t) == self.guard_clock.as_raw() {
            return SafeTimestamp{ nanos: now_main, estimated: false };
        }

        let now_guard = clock_now_ns(self.guard_clock);
        let mut update_last = true;
        let now = if let &Some((last_main, last_guard)) = &self.last_update {
            let elapsed_main = now_main.wrapping_sub(last_main);
            let elapsed_guard = now_guard.wrapping_sub(last_guard);
            //debug_assert!(elapsed_guard >= 0);
            let tolerance = ((self.tolerance * (elapsed_guard as f64)) as i64).max(1_500_000); // XXX TODO
            if ((elapsed_main < 0) || (elapsed_main.wrapping_sub(elapsed_guard).abs() > tolerance)) &&
                ((!self.skewed) || (elapsed_guard <= self.timeout_ns)) {
                update_last = false;
                SafeTimestamp{ nanos: last_main.wrapping_add(elapsed_guard), estimated: true }
            } else {
                SafeTimestamp{ nanos: now_main, estimated: false }
            }
        } else {
            SafeTimestamp{ nanos: now_main, estimated: false }
        };
        if update_last {
            self.last_update = Some((now_main, now_guard));
        }
        self.skewed = now.estimated;
        now
    }
}


/// Default path to usrvclock datagram Unix-domain socket
pub const DEFAULT_SERVER_SOCKET_PATH: &str = "/tmp/ptp-usrvclock";

/// usrvclock protocol server - transmitter of clock overlay updates
pub struct Server {
    socket_path: PathBuf,
    socket: UnixDatagram,
    clients: Vec<SocketAddr>,
}

impl Server {
    /// Creates new server
    pub fn new(path: PathBuf) -> std::io::Result<Self> {
        let _ = std::fs::remove_file(&path);
        let socket = UnixDatagram::bind(&path)?;
        socket.set_nonblocking(true)?;
        if let Ok(metadata) = std::fs::metadata(&path) {
            let mut permissions = metadata.permissions();
            permissions.set_readonly(false);
            let _ = std::fs::set_permissions(&path, permissions);
        }

        Ok(Self {
            socket_path: path,
            socket,
            clients: vec![],
        })
    }

    /// Sends overlay update to connected clients.
    /// 
    /// This function is non-blocking.
    /// 
    /// It is recommended to call it at least once per second. Otherwise newly connected clients will wait too long for the first update.
    /// This recommendation may be removed in future versions.
    pub fn send(&mut self, overlay: ClockOverlay) {
        let flags: u16 = 1;
        let mut buff = [0u8; OVERLAY_SIZE_BYTES];
        buff[0] = b'V';
        buff[1] = b'C';
        buff[2..4].copy_from_slice(&PROTOCOL_MAJOR_VERSION.to_ne_bytes());
        buff[4..6].copy_from_slice(&PROTOCOL_MINOR_VERSION.to_ne_bytes());
        buff[6..8].copy_from_slice(&flags.to_ne_bytes());
        buff[8..16].copy_from_slice(&overlay.clock_id.to_ne_bytes());
        buff[16..24].copy_from_slice(&overlay.last_sync.to_ne_bytes());
        buff[24..32].copy_from_slice(&overlay.shift.to_ne_bytes());
        buff[32..40].copy_from_slice(&overlay.freq_scale.to_ne_bytes());

        // handle new client(s)
        while let Ok((_size, client)) = self.socket.recv_from(&mut buff) {
            self.clients.push(client);
        }
        
        // send update to all clients while removing disconnected ones
        self.clients.retain(|client| {
            match self.socket.send_to_addr(&buff, &client) {
                Ok(_) => true,
                Err(e) => {
                    match e.kind() {
                        std::io::ErrorKind::WouldBlock => true,
                        _ => false
                    }
                }
            }
        });
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.socket_path);
    }
}

fn client_socket_path(counter: i32) -> PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!("usrvclock-client.{}.{}", std::process::id(), counter));
    path
}

/// Blocking client of usrvclock protocol - receiver of clock overlay updates.
/// It is very simple and doesn't allow unblocking (needed for graceful exit).
/// It is recommended to use the async version ([`AsyncClient`]) if possible because it doesn't have this shortcoming.
pub struct BlockingClient {
    socket_path: PathBuf,
    local_path: Option<PathBuf>,
    socket: Option<UnixDatagram>,
}

impl BlockingClient {
    /// Creates new client
    pub fn new(path: PathBuf) -> Self {
        Self {
            socket_path: path,
            local_path: None,
            socket: None,
        }
    }

    /// Receives a single overlay update. If the server stops (is restarted, crashes etc.), it will try to connect again.
    /// 
    /// This function is blocking.
    pub fn recv(&mut self) -> Result<ClockOverlay, OverlayReceiveError> {
        let mut counter = 0;
        loop {
            if self.socket.is_none() {
                let local_path = client_socket_path(counter);
                counter += 1;
                let socket = if let Ok(socket) = UnixDatagram::bind(&local_path) {
                    socket
                } else {
                    std::thread::sleep(Duration::from_millis(250));
                    continue;
                };
                if socket.connect(&self.socket_path).is_err() || socket.send(&[]).is_err() {
                    drop(socket);
                    let _ = std::fs::remove_file(&local_path);
                    std::thread::sleep(Duration::from_millis(250));
                    continue;
                }
                self.socket = Some(socket);
                self.local_path = Some(local_path);
            }
            if let Some(socket) = &self.socket {
                let mut buff = [0u8; 40];
                match socket.recv(&mut buff) {
                    Ok(size) => {
                        return ClockOverlay::decode(&buff[0..size]);
                    },
                    _ => {
                        let _ = std::fs::remove_file(self.local_path.as_ref().unwrap());
                        self.socket = None;
                    }
                };
            }
        }
    }
}

/// Async client utilizing the [`tokio`] library - receiver of clock overlay updates.
/// 
/// Overlay updates are sent to [`tokio::sync::broadcast`] channel which can be subscribed using [`AsyncClient::subscribe`].
/// Errors are passed to `error_handler` callback specified when starting the client.
#[cfg(feature="tokio")]
pub struct AsyncClient {
    shutdown: tokio::sync::oneshot::Sender<()>,
    sender: tokio::sync::watch::Sender<Option<ClockOverlay>>,
    join_handle: tokio::task::JoinHandle<()>,
}

#[cfg(feature="tokio")]
impl AsyncClient {
    /// Starts the client coroutine using [`tokio::spawn`]. Returns [`AsyncClient`] instance.
    /// 
    /// `path` is server socket path. It must be configurable by user and default to [`DEFAULT_SERVER_SOCKET_PATH`],
    /// unless your application has a good reason to use a different convention (e.g. it supports multiple PTP clock domains)
    /// 
    /// `error_handler` is a callback which should print warning messages to the user
    pub fn start(path: PathBuf, error_handler: Box<dyn FnMut(OverlayReceiveError) + Send>) -> Self {
        let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel();
        let tx = tokio::sync::watch::Sender::new(None);
        let sender = tx.clone();

        let path = path.to_owned();
        //println!("path: {path:?}");
        let join_handle = if nix::sys::stat::stat(&path).map(|stat|(stat.st_mode & S_IFMT)==S_IFCHR).unwrap_or(false) {
            //println!("opening device");
            let fd = nix::fcntl::open(&path, OFlag::O_RDWR, Mode::empty()).unwrap_or_else(|_| {
                // retry without writing, however, freq_scale_including_hw will return invalid values
                //println!("unable to open clock for writing, clock_adjtime will not work so freq_scale_including_hw will be inaccurate!");
                nix::fcntl::open(&path, OFlag::O_RDONLY, Mode::empty()).unwrap()
            });
            let clock_id = ((!(fd as libc::clockid_t)) << 3) | 3;
            tx.send_replace(Some(ClockOverlay {
                clock_id: clock_id.into(),
                last_sync: 0,
                shift: 0,
                freq_scale: 0.0
            }));
            tokio::spawn(async move {
                shutdown_rx.await;
                nix::unistd::close(fd); // TODO FIXME ClockOverlay.now() may still use this clock!!!
            })
        } else {
            tokio::spawn(async move {
                async fn recv_loop(
                    path: PathBuf,
                    tx: tokio::sync::watch::Sender<Option<ClockOverlay>>,
                    mut error_handler: Box<dyn FnMut(OverlayReceiveError) + Send>,
                    file_to_delete: &Mutex<Option<PathBuf>>
                ) {
                    let mut socket_opt: Option<(tokio::net::UnixDatagram, PathBuf)> = None;
                    let mut counter: i32 = 0;
                    loop {
                        if socket_opt.is_none() {
                            let local_path = client_socket_path(counter);
                            counter += 1;
                            let result = tokio::net::UnixDatagram::bind(&local_path);
                            match result {
                                Ok(socket) => {
                                    if socket.connect(&path).is_err() || socket.send(&[]).await.is_err() {
                                        drop(socket);
                                        let _ = std::fs::remove_file(&local_path);
                                        tokio::time::sleep(Duration::from_millis(250)).await;
                                    } else {
                                        *file_to_delete.lock().unwrap() = Some(local_path.clone());
                                        socket_opt = Some((socket, local_path));
                                    }
                                },
                                Err(_e) => {
                                    tokio::time::sleep(Duration::from_millis(250)).await;
                                }
                            }
                        }
                        if let Some((socket, local_path)) = &socket_opt {
                            let mut buff = [0u8; OVERLAY_SIZE_BYTES];
                            match socket.recv(&mut buff).await {
                                Ok(size) => {
                                    match ClockOverlay::decode(&buff[0..size]) {
                                        Ok(v) => {
                                            let _ = tx.send(Some(v));
                                        },
                                        Err(e) => {
                                            (error_handler)(e);
                                        }
                                    }
                                },
                                _ => {
                                    let _ = std::fs::remove_file(&local_path);
                                    *file_to_delete.lock().unwrap() = None;
                                    socket_opt = None;
                                }
                            }
                        }
                    }
                }

                let file_to_delete = Mutex::new(None);

                tokio::select! {
                    _ = recv_loop(path, tx, error_handler, &file_to_delete) => {},
                    _ = shutdown_rx => {}
                };

                let file_to_delete = file_to_delete.into_inner().unwrap();
                if let Some(todelete) = file_to_delete {
                    let _ = std::fs::remove_file(&todelete);
                }
            })
        };
        Self {
            sender,
            shutdown: shutdown_tx,
            join_handle
        }
    }

    /// Stops running client coroutine
    pub async fn stop(self) -> Result<(), tokio::task::JoinError> {
        let _ = self.shutdown.send(());
        self.join_handle.await
    }

    /// Subscribes to clock overlay changes. Returns [`tokio::sync::watch::Receiver`].
    pub fn subscribe(&self) -> tokio::sync::watch::Receiver<Option<ClockOverlay>> {
        self.sender.subscribe()
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocking_send_recv() {
        let test_overlay = ClockOverlay {
            clock_id: 33,
            last_sync: 13000000000,
            shift: -14000000000,
            freq_scale: -0.0002,
        };
        let test_overlay2 = test_overlay;
        let client_thr = std::thread::spawn(move || {
            let mut client = BlockingClient::new("/tmp/usrvclock-test-blocking".into());
            let overlay = client.recv().unwrap();
            assert_eq!(overlay, test_overlay2);
        });
        let server_thr = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(600));
            let mut server = Server::new("/tmp/usrvclock-test-blocking".into()).unwrap();
            for _ in 0..4 {
                std::thread::sleep(Duration::from_millis(500));
                server.send(test_overlay);
            }
        });
        client_thr.join().unwrap();
        server_thr.join().unwrap();
    }

    #[cfg(feature="tokio")]
    #[tokio::test]
    async fn tokio_send_recv() {
        let test_overlay = ClockOverlay {
            clock_id: 33,
            last_sync: 13000000000,
            shift: -14000000000,
            freq_scale: -0.0002,
        };
        let server_thr = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(600));
            let mut server = Server::new("/tmp/usrvclock-test-tokio".into()).unwrap();
            for _ in 0..2 {
                std::thread::sleep(Duration::from_millis(500));
                server.send(test_overlay);
            }
        });
        let client = AsyncClient::start("/tmp/usrvclock-test-tokio".into(), Box::new(|e| panic!("{e:?}")));
        let mut receiver1 = client.subscribe();
        let mut receiver2 = client.subscribe();
        for _ in 0..2 {
            receiver1.changed().await;
            let overlay = receiver1.borrow().unwrap();
            assert_eq!(overlay, test_overlay);
            receiver2.changed().await;
            let overlay = receiver2.borrow().unwrap();
            assert_eq!(overlay, test_overlay);
        }
        client.stop().await.unwrap();
        server_thr.join().unwrap();
    }

    #[test]
    fn timestamp_computations() {
        let clock = nix::time::ClockId::CLOCK_MONOTONIC;
        let shift = -2_500_000_000;
        let shifting_overlay = ClockOverlay {
            clock_id: clock.as_raw() as i64,
            last_sync: 0,
            shift: shift,
            freq_scale: 0.0
        };
        let underlying_ts = 12_312_312_333_333_343i64;
        assert_eq!(shifting_overlay.underlying_to_overlay_ns(underlying_ts), underlying_ts + shift);

        let sync_age = 2_400_000_000;
        let shifting_and_slow_overlay = ClockOverlay {
            clock_id: clock.as_raw() as i64,
            last_sync: underlying_ts - sync_age,
            shift: shift,
            freq_scale: -0.25
        };
        assert_eq!(shifting_and_slow_overlay.underlying_to_overlay_ns(underlying_ts), underlying_ts + shift - sync_age/4);

        let shifting_and_fast_overlay = ClockOverlay {
            clock_id: clock.as_raw() as i64,
            last_sync: underlying_ts - sync_age,
            shift: shift,
            freq_scale: 0.5
        };
        assert_eq!(shifting_and_fast_overlay.underlying_to_overlay_ns(underlying_ts), underlying_ts + shift + sync_age/2);
    }
    
    #[cfg(feature="unix")]
    #[test]
    fn now_computations() {
        let epsilon_ns = 5_000_000; // 5 ms
        let identity_overlay = ClockOverlay {
            clock_id: nix::time::ClockId::CLOCK_MONOTONIC.as_raw() as i64,
            last_sync: 0,
            shift: 0,
            freq_scale: 0.0
        };
        let start = identity_overlay.now_underlying_ns();
        let slow_overlay = ClockOverlay {
            last_sync: start,
            freq_scale: -0.5,
            ..identity_overlay
        };
        let shifting_overlay = ClockOverlay {
            shift: -start,
            ..identity_overlay
        };
        let shifting_fast_overlay = ClockOverlay {
            last_sync: start + 100_000_000,
            shift: -start,
            freq_scale: 1.0,
            ..identity_overlay
        };
        
        let diff = slow_overlay.now_ns().wrapping_sub(start);
        assert!(diff >= 0);
        assert!(diff < epsilon_ns);

        let overlay_ts = shifting_overlay.now_ns();
        assert!(overlay_ts >= 0);
        assert!(overlay_ts < epsilon_ns);

        std::thread::sleep(Duration::from_millis(200));
        
        let diff = slow_overlay.now_ns().wrapping_sub(start);
        assert!((diff - 100_000_000).abs() < epsilon_ns);

        let overlay_ts = shifting_overlay.now_ns();
        assert!((overlay_ts - 200_000_000).abs() < epsilon_ns);

        let overlay_ts = shifting_fast_overlay.now_ns();
        assert!((overlay_ts - 300_000_000).abs() < epsilon_ns);

    }

    const NS_IN_MS: i64 = 1_000_000;

    #[cfg(feature="unix")]
    #[test]
    fn safe_clock() {
        let epsilon_ns = 5 * NS_IN_MS;
        let tolerance = epsilon_ns as f64 / (150f64 * NS_IN_MS as f64);
        let mut overlay = ClockOverlay {
            clock_id: nix::time::ClockId::CLOCK_MONOTONIC_RAW.as_raw() as i64,
            last_sync: 0,
            shift: 0,
            freq_scale: 0.0
        };
        let mut safe = SafeClock::new(tolerance, 205 * NS_IN_MS);

        let ts1 = safe.now(&overlay);
        assert!(!ts1.estimated);

        std::thread::sleep(Duration::from_millis(200));

        let ts2 = safe.now(&overlay);
        assert!(!ts2.estimated);
        let diff = ts2.nanos.wrapping_sub(ts1.nanos);
        assert!((diff - 200_000_000).abs() < epsilon_ns);

        overlay.shift += 20_000_000;
        let ts3 = safe.now(&overlay);
        assert!(ts3.estimated);
        assert!(ts3.nanos.wrapping_sub(ts2.nanos).abs() < epsilon_ns);

        std::thread::sleep(Duration::from_millis(150));

        overlay.shift -= 21_000_000;
        let ts4 = safe.now(&overlay);
        assert!(!ts4.estimated);
        let diff = ts4.nanos.wrapping_sub(ts2.nanos);
        assert!((diff - 149_000_000).abs() < epsilon_ns);

        overlay.shift += 21_000_000;
        let ts5 = safe.now(&overlay);
        assert!(ts5.estimated);
        let diff = ts5.nanos.wrapping_sub(ts2.nanos);
        assert!((diff - 149_000_000).abs() < epsilon_ns);

        // exceed the timeout, now result must not be estimated:
        std::thread::sleep(Duration::from_millis(215));
        let ts6 = safe.now(&overlay);
        assert!(!ts6.estimated);
    }
}
