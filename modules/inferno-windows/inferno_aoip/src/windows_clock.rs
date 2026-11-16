//! QPC/PTPv1 follower in the same process; never changes the Windows clock.
use inferno_windows_clock::{local::LocalClock, servo::Mapping};
use std::{
  path::PathBuf,
  sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
  },
  thread::JoinHandle,
};
use tokio::sync::watch;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ClockOverlay {
  pub freq_scale: f64,
  mapping: Mapping,
  master_uuid: [u8; 6],
}
impl ClockOverlay {
  pub fn freq_scale_including_hw(&self) -> f64 {
    self.freq_scale
  }
  pub fn now_ns(&self) -> Option<i64> {
    self.mapping.at(LocalClock::new().ok()?.now_ns())
  }
  /// Dante device identity for the directly selected PTPv1 source.
  /// This follower does not support boundary-clock/grandmaster reconstruction.
  pub fn master_clock_id(&self) -> Option<[u8; 8]> {
    self.now_ns()?;
    let mut id = [0; 8];
    id[..6].copy_from_slice(&self.master_uuid);
    Some(id)
  }
}

pub struct ClockReceiver {
  receiver: watch::Receiver<Option<ClockOverlay>>,
  stop: Arc<AtomicBool>,
  worker: Option<JoinHandle<()>>,
}
impl ClockReceiver {
  /// Windows CLOCK_PATH is an explicit "interface IPv4,master IPv4,local MAC".
  /// An empty master field discovers an active PTPv1 sender on that interface.
  pub fn start(path: Option<PathBuf>) -> Self {
    let (sender, receiver) = watch::channel(None);
    let stop = Arc::new(AtomicBool::new(false));
    let worker_stop = stop.clone();
    let worker = std::thread::spawn(move || {
      let result = (|| -> Result<(), Box<dyn std::error::Error>> {
        let config = path.ok_or("Windows requires CLOCK_PATH=interface,master,mac")?;
        let mut args: Vec<String> =
          config.to_str().ok_or("invalid clock configuration")?.split(',').map(str::to_owned).collect();
        if args.len() != 3 {
          return Err("CLOCK_PATH must contain interface,master,mac".into());
        }
        args.push("0".into());
        inferno_windows_clock::follower::follow_with_identity(&args, true, &worker_stop, |mapping| {
          let next =
            mapping.map(|(mapping, master)| ClockOverlay { freq_scale: mapping.rate_ppb as f64 / 1e9, mapping, master_uuid: master.uuid });
          sender.send_if_modified(|current| {
            if *current == next {
              false
            } else {
              *current = next;
              true
            }
          });
        })
      })();
      sender.send_replace(None);
      if let Err(error) = result {
        log::error!("Windows PTP follower stopped: {error}");
      }
    });
    Self { receiver, stop, worker: Some(worker) }
  }
  pub fn subscribe(&self) -> watch::Receiver<Option<ClockOverlay>> {
    self.receiver.clone()
  }
  pub async fn stop(mut self) -> Result<(), String> {
    self.stop.store(true, Ordering::Relaxed);
    if let Some(worker) = self.worker.take() {
      tokio::task::spawn_blocking(move || worker.join().map_err(|_| "clock worker panicked".to_owned()))
        .await
        .map_err(|e| e.to_string())??;
    }
    Ok(())
  }
}
impl Drop for ClockReceiver {
  fn drop(&mut self) {
    self.stop.store(true, Ordering::Relaxed);
  }
}

#[cfg(test)]
mod tests {
  use super::*;
  use crate::media_clock::{async_clock_receiver_to_realtime, MediaClock};

  fn overlay(valid: bool) -> ClockOverlay {
    let now = LocalClock::new().unwrap().now_ns();
    ClockOverlay {
      freq_scale: 0.0,
      master_uuid: [0, 14, 221, 250, 61, 38],
      mapping: Mapping {
        local_ns: now - 1_000_000,
        remote_ns: 1_000_000_000,
        rate_ppb: 0,
        valid_until_ns: if valid { now + 1_000_000_000 } else { now - 1 },
      },
    }
  }
  #[test]
  fn copied_overlay_expires_without_another_update() {
    let mut clock = MediaClock::new(false);
    clock.update_overlay(overlay(false));
    assert!(!clock.is_ready());
    assert!(clock.now_ns().is_none());
    assert!(clock.system_clock_duration_from_until(0, 48, 48000).is_none());
  }
  #[test]
  fn master_information_is_revoked_when_mapping_expires() {
    assert_eq!(overlay(true).master_clock_id(), Some([0, 14, 221, 250, 61, 38, 0, 0]));
    assert_eq!(overlay(false).master_clock_id(), None);
  }
  #[tokio::test]
  async fn loss_is_forwarded_to_realtime_reader() {
    let initial = overlay(true);
    let (tx, rx) = watch::channel(Some(initial));
    let mut realtime = async_clock_receiver_to_realtime(rx, Some(initial));
    tokio::task::yield_now().await;
    realtime.update();
    assert!(realtime.get().is_some());
    tx.send_replace(None);
    tokio::task::yield_now().await;
    realtime.update();
    assert!(realtime.get().is_none());
  }
}
