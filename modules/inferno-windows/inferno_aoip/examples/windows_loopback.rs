//! Two-channel native Dante test host. Routing is performed by the operator.
use inferno_aoip::device_server::{
  AtomicSample, DeviceServer, ExternalBufferParameters, MediaClock, Settings,
};
use std::{
  collections::BTreeMap,
  sync::{
    atomic::{AtomicUsize, Ordering},
    Arc, RwLock,
  },
  time::Duration,
};

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
  env_logger::init_from_env(env_logger::Env::default().default_filter_or("info"));
  let args: Vec<String> = std::env::args().skip(1).collect();
  if args.len() != 4 {
    return Err("usage: windows_loopback INTERFACE_IP MASTER_IP MAC_12HEX SECONDS (1..1800)".into());
  }
  let seconds: u64 = args[3].parse()?;
  if !(1..=1800).contains(&seconds) {
    return Err("duration must be 1..1800 seconds".into());
  }
  let mut config = BTreeMap::new();
  config.insert("CLOCK_PATH".into(), args[..3].join(","));
  config.insert("NAME".into(), "Inferno Windows Loop Test".into());
  // Coexist with Dante's conmon service, which retains wildcard 8700/8800.
  config.insert("ALT_PORT".into(), "18000".into());
  config.insert("PROCESS_ID".into(), "1".into());
  config.insert("TX_LATENCY_NS".into(), "4000000".into());
  config.insert("RX_LATENCY_NS".into(), "250000".into());
  let mut settings = Settings::new("Inferno Windows", "InfWinTest", Some(args[0].parse()?), &config);
  settings.make_rx_channels(2);
  settings.make_tx_channels(2);
  let mut server = DeviceServer::start(settings).await;
  let mut receiver = server.get_realtime_clock_receiver();
  let mut clock = MediaClock::new(false);
  let start = tokio::time::Instant::now();
  let timestamp = loop {
    receiver.update();
    if let Some(overlay) = receiver.get() {
      clock.update_overlay(*overlay);
    }
    if let Some(now) = clock.now_in_timebase(48000) {
      break now as usize;
    }
    if start.elapsed() > Duration::from_secs(120) {
      server.shutdown().await;
      return Err("PTP did not lock within 120 seconds; audio not started".into());
    }
    tokio::time::sleep(Duration::from_millis(10)).await;
  };
  // A periodic source requires no independent sample clock or rate converter.
  // At 48 kHz these give distinct 375 Hz / 562.5 Hz tones at -30 dBFS.
  let storage: Vec<Vec<AtomicSample>> = [512., 768.]
    .into_iter()
    .map(|cycles| {
      (0..65536)
        .map(|i| {
          AtomicSample::new(
            ((i as f64 * cycles * std::f64::consts::TAU / 65536.).sin() * i32::MAX as f64 * 0.0316227766)
              as i32,
          )
        })
        .collect()
    })
    .collect();
  let valid = Arc::new(RwLock::new(true));
  let buffers = storage
    .iter()
    .map(|channel| {
      // Storage lives until server shutdown; all samples are atomic and initialized.
      unsafe { ExternalBufferParameters::new(channel.as_ptr(), channel.len(), 1, valid.clone(), None) }
    })
    .collect();
  let (tx, rx) = tokio::sync::oneshot::channel();
  tx.send(timestamp).unwrap();
  server.transmit_from_external_buffer(buffers, rx, Arc::new(AtomicUsize::new(usize::MAX)), None).await;
  let received = Arc::new(AtomicUsize::new(0));
  let nonzero = Arc::new(AtomicUsize::new(0));
  let r = received.clone();
  let n = nonzero.clone();
  server
    .receive_with_callback(Box::new(move |count, channels| {
      r.fetch_add(count, Ordering::Relaxed);
      let active = channels.iter().flat_map(|c| c.iter().take(count)).filter(|v| **v != 0).count();
      n.fetch_add(active, Ordering::Relaxed);
    }))
    .await;
  println!(
    "READY: 2 TX / 2 RX, 48 kHz. Route TX through the other computer back to RX in Dante Controller."
  );
  let deadline = tokio::time::sleep(Duration::from_secs(seconds));
  tokio::pin!(deadline);
  loop {
    tokio::select! {
        _ = &mut deadline => break,
        _ = tokio::signal::ctrl_c() => break,
        _ = tokio::time::sleep(Duration::from_secs(1)) => {
            receiver.update();
            let locked = receiver.get().is_some_and(|o| { let mut c = MediaClock::new(false); c.update_overlay(o); c.is_ready() });
            println!("clock_valid={locked} callback_frames={} nonzero_channel_samples={}", received.load(Ordering::Relaxed), nonzero.load(Ordering::Relaxed));
        }
    }
  }
  server.shutdown().await;
  *valid.write().unwrap() = false;
  println!("STOPPED: nonzero samples alone do not prove loop integrity; channel/frequency/continuity validation is still required.");
  Ok(())
}
