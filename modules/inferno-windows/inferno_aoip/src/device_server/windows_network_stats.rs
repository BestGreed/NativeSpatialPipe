//! Once-per-heartbeat adapter counters; never queried from audio workers.
use std::{io, net::Ipv4Addr, time::Instant};
use windows_sys::Win32::NetworkManagement::IpHelper::{GetIfEntry2, MIB_IF_ROW2};

#[derive(Clone, Copy)]
struct Counters { tx: u64, rx: u64, tx_errors: u64, rx_errors: u64 }
#[derive(Debug)]
pub(super) struct Sample { pub tx_bytes_per_second: u32, pub rx_bytes_per_second: u32, pub tx_errors: u32, pub rx_errors: u32 }
pub(super) struct NetworkStatistics {
  index: u32,
  mac: [u8; 6],
  previous: Option<(Instant, Counters)>,
}
fn rate(current: u64, previous: u64, elapsed_ns: u128) -> Option<u32> {
  let delta=current.checked_sub(previous)?;
  if elapsed_ns==0 { return None; }
  Some(((delta as u128 * 1_000_000_000 / elapsed_ns).min(u32::MAX as u128)) as u32)
}
impl NetworkStatistics {
  pub fn new(address: Ipv4Addr, mac: [u8; 6]) -> Option<Self> {
    let index=netdev::get_interfaces().into_iter().find(|iface|iface.ipv4.iter().any(|ip|ip.addr()==address))?.index;
    Some(Self { index, mac, previous: None })
  }
  pub fn sample(&mut self) -> io::Result<Option<Sample>> {
    let mut row: MIB_IF_ROW2=unsafe { std::mem::zeroed() };
    row.InterfaceIndex=self.index;
    let error=unsafe { GetIfEntry2(&mut row) };
    if error!=0 { self.previous=None; return Err(io::Error::from_raw_os_error(error as i32)); }
    if row.PhysicalAddressLength!=6 || row.PhysicalAddress[..6]!=self.mac {
      self.previous=None;
      return Err(io::Error::new(io::ErrorKind::NotFound,"Selected adapter identity changed"));
    }
    let now=Instant::now();
    let current=Counters { tx: row.OutOctets, rx: row.InOctets, tx_errors: row.OutErrors, rx_errors: row.InErrors };
    let previous=self.previous.replace((now,current));
    let Some((before,old))=previous else { return Ok(None); };
    // A counter reset establishes a fresh baseline instead of reporting a spike.
    let elapsed=(now-before).as_nanos();
    let Some(tx)=rate(current.tx,old.tx,elapsed) else { return Ok(None); };
    let Some(rx)=rate(current.rx,old.rx,elapsed) else { return Ok(None); };
    if current.tx_errors<old.tx_errors || current.rx_errors<old.rx_errors { return Ok(None); }
    Ok(Some(Sample { tx_bytes_per_second: tx, rx_bytes_per_second: rx,
      tx_errors: current.tx_errors.min(u32::MAX as u64) as u32,
      rx_errors: current.rx_errors.min(u32::MAX as u64) as u32 }))
  }
}
#[cfg(test)]
mod tests {
  use super::*;
  #[test]
  fn bandwidth_uses_elapsed_time_not_nominal_timer_period() {
    assert_eq!(rate(1_400_000,1_000_000,2_000_000_000),Some(200_000));
    assert_eq!(rate(4_300_400_000,4_300_000_000,1_000_000_000),Some(400_000));
  }
  #[test]
  fn resets_and_zero_intervals_do_not_emit_false_bandwidth() {
    assert_eq!(rate(1,100,1_000_000_000),None);
    assert_eq!(rate(100,1,0),None);
    assert_eq!(rate(u64::MAX,0,1),Some(u32::MAX));
  }
}
