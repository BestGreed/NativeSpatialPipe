//! Control-thread JSON and lock-free block snapshots; no file IO on audio threads.
use std::sync::{Arc,Mutex,OnceLock,Weak,atomic::{AtomicU64,Ordering::Relaxed}};
static GAIN:AtomicU64=AtomicU64::new(0);
static LIVE:OnceLock<Mutex<Weak<Meter>>>=OnceLock::new();
#[derive(Default)]
pub struct Meter{pub gain:AtomicU64,pub pre:AtomicU64,pub post:AtomicU64,pub reduction:AtomicU64,pub max_reduction:AtomicU64,pub hold:AtomicU64,pub percent:AtomicU64,pub frames:AtomicU64,pub master:f64,pub ceiling:f64,pub ahead:u32,pub loudness_gain:[AtomicU64;2]}
fn value(x:&AtomicU64)->f64{f64::from_bits(x.load(Relaxed))}
pub fn set_gain(db:f64){if db.is_finite()&&(-12.0..=12.0).contains(&db){GAIN.store(db.to_bits(),Relaxed);}}
pub fn gain()->f64{f64::from_bits(GAIN.load(Relaxed))}
pub fn register(master:f64,ceiling:f64,ahead:u32)->Arc<Meter>{let meter=Arc::new(Meter{master,ceiling,ahead,..Meter::default()});*LIVE.get_or_init(||Mutex::new(Weak::new())).lock().unwrap()=Arc::downgrade(&meter);meter}
pub fn status()->String{let meter=LIVE.get().and_then(|m|m.lock().ok()?.upgrade());if let Some(m)=meter{
 let db=|x:f64|20.*x.max(1e-6).log10();
 format!("{{\"gainDb\":{},\"appliedGainDb\":{},\"masterDb\":{},\"ceilingDbfs\":{},\"lookaheadMs\":{},\"prePeakDbfs\":{},\"postPeakDbfs\":{},\"peakHoldDbfs\":{},\"reductionDb\":{},\"maxReductionDb\":{},\"limitedPercent\":{},\"frames\":{},\"loudnessGainDb\":[{},{}],\"detector\":\"linked-sample-peak\"}}",gain(),value(&m.gain),db(m.master),db(m.ceiling),m.ahead as f64/48.,db(value(&m.pre)),db(value(&m.post)),db(value(&m.hold)),value(&m.reduction),value(&m.max_reduction),value(&m.percent),m.frames.load(Relaxed),value(&m.loudness_gain[0]),value(&m.loudness_gain[1]))
 }else{"null".into()}}
