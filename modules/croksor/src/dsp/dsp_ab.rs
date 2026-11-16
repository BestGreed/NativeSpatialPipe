//! Temporary, opt-in notebook A/B. File IO stays on the control thread.
use std::sync::{Arc,OnceLock,Mutex,atomic::{AtomicBool,AtomicU64,Ordering::Relaxed}};
use std::time::Duration;

#[derive(Default)]
pub struct Control { mode:AtomicU64, pipeline:AtomicU64, algorithm:AtomicU64, revision:AtomicU64, frames:AtomicU64, ready:AtomicU64, dry_gain:AtomicU64, wet_gain:AtomicU64, updry_gain:AtomicU64, upwet_gain:AtomicU64, active:AtomicU64, stereo:AtomicU64,users:AtomicU64,stop:AtomicBool,worker:Mutex<Option<std::thread::JoinHandle<()>>> }
#[cfg(not(test))]
fn semantic_status()->String{if crate::joint::active(){return crate::joint::status();}let mut s=crate::semantic::status();if s!="null"{s.pop();s.push_str(&format!(",\"pipelineId\":0,\"candidateAvailable\":{} }}",crate::joint::status()!="null"));s.pop();s.push_str(&format!(",\"residualBackend\":\"{}\",\"upmixDelayMs\":{}}}",if crate::freesurround::enabled(){"FreeSurround 7.1"}else{"prototype"},if crate::freesurround::enabled(){32}else{0}));}s}
#[cfg(test)]
fn semantic_status()->String{"null".into()}
#[cfg(not(test))]fn limiter_gain(db:f64){crate::limiter_control::set_gain(db);}
#[cfg(test)]fn limiter_gain(_:f64){}
#[cfg(not(test))]fn limiter_status()->String{crate::limiter_control::status()}
#[cfg(test)]fn limiter_status()->String{"null".into()}
static CONTROL:OnceLock<Arc<Control>>=OnceLock::new();
fn control()->Arc<Control>{let result=CONTROL.get_or_init(||{
 let state=Arc::new(Control::default());state.dry_gain.store(1f64.to_bits(),Relaxed);state.wet_gain.store(1f64.to_bits(),Relaxed);state.updry_gain.store(1f64.to_bits(),Relaxed);state.upwet_gain.store(1f64.to_bits(),Relaxed);
 state
}).clone();let mut worker=result.worker.lock().unwrap();result.users.fetch_add(1,Relaxed);
 if let Some(local)=std::env::var_os("LOCALAPPDATA") {let directory=std::path::PathBuf::from(local).join("NativeSpatialPipe/dsp-ab");
  // Opt in before process creation. No extra thread for normal sessions.
  if directory.join("enabled").is_file()&&worker.is_none(){let state=result.clone();state.stop.store(false,Relaxed);*worker=Some(std::thread::spawn(move||while !state.stop.load(Relaxed){
   let file=directory.join("control.txt");let fresh=std::fs::metadata(&file).and_then(|m|m.modified()).ok().and_then(|t|t.elapsed().ok()).is_some_and(|age|age<Duration::from_secs(3));
   if fresh {if let Ok(text)=std::fs::read_to_string(file){let words:Vec<_>=text.split_whitespace().collect();if words.len()==2||words.len()==3||words.len()==4 {if let (Ok(mode),Ok(rev))=(words[0].parse::<u64>(),words[1].parse::<u64>()){if mode<=3 {if rev!=state.revision.load(Relaxed){state.ready.store(0,Relaxed);state.frames.store(0,Relaxed);}state.mode.store(mode,Relaxed);state.pipeline.store(words.get(3).and_then(|v|v.parse::<u64>().ok()).unwrap_or(0).min(1),Relaxed);state.algorithm.store(words.get(2).and_then(|v|v.parse::<u64>().ok()).unwrap_or(0).min(2),Relaxed);state.revision.store(rev,Relaxed);}}}}}else{state.mode.store(1,Relaxed);state.revision.store(0,Relaxed);}
   let gain_file=directory.join("limiter-gain.txt");let gain_fresh=std::fs::metadata(&gain_file).and_then(|m|m.modified()).ok().and_then(|t|t.elapsed().ok()).is_some_and(|age|age<Duration::from_secs(3));
   let db=if gain_fresh{std::fs::read_to_string(gain_file).ok().and_then(|s|s.trim().parse::<f64>().ok()).filter(|x|x.is_finite()&&(-12.0..=12.0).contains(x)).unwrap_or(0.)}else{0.};
   limiter_gain(db);
   let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis();
   let mut json=format!("{{\"pid\":{},\"atMs\":{},\"mode\":{},\"revision\":{},\"calibrationFrames\":{},\"ready\":{},\"dryGainDb\":{},\"wetGainDb\":{},\"active\":{},\"stereoOnly\":{}}}",std::process::id(),now,state.mode.load(Relaxed),state.revision.load(Relaxed),state.frames.load(Relaxed),state.ready.load(Relaxed)!=0,20.*f64::from_bits(state.dry_gain.load(Relaxed)).max(1e-12).log10(),20.*f64::from_bits(state.wet_gain.load(Relaxed)).max(1e-12).log10(),state.active.load(Relaxed),state.stereo.load(Relaxed)!=0);
   json.pop();json.push_str(&format!(",\"upmixDryGainDb\":{},\"upmixWetGainDb\":{},\"syntheticUpmix\":true}}",20.*f64::from_bits(state.updry_gain.load(Relaxed)).max(1e-12).log10(),20.*f64::from_bits(state.upwet_gain.load(Relaxed)).max(1e-12).log10()));
   json.pop();json.push_str(&format!(",\"algorithm\":{},\"semantic\":{}}}",state.algorithm.load(Relaxed),semantic_status()));
   json.pop();json.push_str(&format!(",\"limiter\":{}}}",limiter_status()));
   let temporary=directory.join("status.tmp");if std::fs::write(&temporary,json).is_ok(){let _=std::fs::rename(temporary,directory.join("status.json"));}
   std::thread::sleep(Duration::from_millis(100));
  }));}
 }drop(worker);result}

#[derive(Clone,Copy,Default)]
struct Biquad {x:[f64;2],y:[f64;2]}
impl Biquad {fn sample(&mut self,x:f64,b:[f64;3],a:[f64;2])->f64{let y=b[0]*x+b[1]*self.x[0]+b[2]*self.x[1]-a[0]*self.y[0]-a[1]*self.y[1];self.x=[x,self.x[0]];self.y=[y,self.y[0]];y}}
#[derive(Clone,Copy,Default)]
struct Weight {s:Biquad,h:Biquad}
impl Weight {fn sample(&mut self,x:f32)->f64{let y=self.s.sample(x as f64,[1.53512485958697,-2.69169618940638,1.19839281085285],[-1.69065929318241,0.73248077421585]);self.h.sample(y,[1.,-2.,1.],[-1.99004745483398,0.99007225036621])}}
pub struct Ab {
 control:Arc<Control>,delay:Vec<[f32;2]>,updelay:Vec<[f32;2]>,at:usize,upat:usize,
 calibration_input_valid:bool,used_algorithm:u64,revision:u64,frames:u64,energy:[f64;4],weights:[Weight;8],gain:[f64;4],applied:[f64;4],
 mix:[f64;4],step:[f64;4],remaining:usize,target:usize,output:Vec<f32>,updry:Vec<f32>,ready:bool,stereo:bool,
}
impl Ab {
 pub fn new(n:usize,delay:usize)->Self{Self::with_control(n,delay,control())}
 fn with_control(n:usize,delay:usize,control:Arc<Control>)->Self{Self{control,delay:vec![[0.;2];delay],updelay:vec![[0.;2];delay],at:0,upat:0,calibration_input_valid:true,used_algorithm:0,revision:0,frames:0,energy:[0.;4],weights:[Weight::default();8],gain:[1.;4],applied:[1.;4],mix:[0.,1.,0.,0.],step:[0.;4],remaining:0,target:1,output:vec![0.;2*n],updry:vec![0.;2*n],ready:false,stereo:false}}
 pub fn pipeline(&self)->bool{self.control.pipeline.load(Relaxed)==1}
 pub fn algorithm(&self)->u64{self.control.algorithm.load(Relaxed)}
 pub fn calibration_valid(&mut self,valid:bool){self.calibration_input_valid=valid;}
 pub fn invalidate(&mut self){self.frames=0;self.energy=[0.;4];self.weights=[Weight::default();8];self.ready=false;self.control.ready.store(0,Relaxed);}
 pub fn prepare(&mut self,input:&[f32],n:usize){
  self.stereo=input[2*n..].iter().all(|x|x.abs()<1e-7);
  for i in 0..n{let mut dry=[input[i],input[n+i]];if !self.delay.is_empty(){std::mem::swap(&mut dry,&mut self.delay[self.at]);self.at=(self.at+1)%self.delay.len();}self.output[i]=dry[0];self.output[n+i]=dry[1];}
  self.updry.copy_from_slice(&self.output);
 }
 pub fn prepare_upmix(&mut self,bus:&[f32],n:usize){
  for i in 0..n{let c=bus[2*n+i]*std::f32::consts::FRAC_1_SQRT_2;let mut dry=[bus[i]+c+0.5*(bus[4*n+i]+bus[6*n+i]+bus[8*n+i]+bus[10*n+i]),bus[n+i]+c+0.5*(bus[5*n+i]+bus[7*n+i]+bus[9*n+i]+bus[11*n+i])];if !self.updelay.is_empty(){std::mem::swap(&mut dry,&mut self.updelay[self.upat]);self.upat=(self.upat+1)%self.updelay.len();}self.updry[i]=dry[0];self.updry[n+i]=dry[1];}
 }
 pub fn process(&mut self,wet:&[f32],n:usize)->&[f32]{self.process_four(wet,wet,n)}
 pub fn process_four(&mut self,wet:&[f32],upwet:&[f32],n:usize)->&[f32]{
  let revision=self.control.revision.load(Relaxed);let algorithm=self.algorithm();if revision!=self.revision||algorithm!=self.used_algorithm{self.used_algorithm=algorithm;self.revision=revision;self.frames=0;self.energy=[0.;4];self.weights=[Weight::default();8];self.ready=false;}
  if revision>0&&!self.ready&&self.stereo&&self.calibration_input_valid{let mut energy=[0.;4];for i in 0..n{for c in 0..2{let values=[self.output[c*n+i],wet[c*n+i],self.updry[c*n+i],upwet[c*n+i]];for b in 0..4{let x=self.weights[2*b+c].sample(values[b]);energy[b]+=x*x;}}}if energy.iter().all(|e|*e>1e-9){for b in 0..4{self.energy[b]+=energy[b];}self.frames+=n as u64;if self.frames>=480000{self.gain=matched_gains(self.energy);self.ready=true;}}}
  let requested=self.control.mode.load(Relaxed) as usize;let target=if self.ready&&self.stereo{requested.min(3)}else{1};
  if target!=self.target{self.target=target;self.remaining=2400;for b in 0..4{self.step[b]=((if b==target{1.}else{0.})-self.mix[b])/2400.;}}
  for i in 0..n{if self.remaining>0{for b in 0..4{self.mix[b]+=self.step[b];}self.remaining-=1;}for b in 0..4{let g=if self.ready{self.gain[b]}else{1.};self.applied[b]+=(g-self.applied[b]).clamp(-1./2400.,1./2400.);}for c in 0..2{let at=c*n+i;let values=[self.output[at],wet[at],self.updry[at],upwet[at]];self.output[at]=(0..4).map(|b|values[b] as f64*self.applied[b]*self.mix[b]).sum::<f64>() as f32;}}
  self.control.frames.store(self.frames,Relaxed);self.control.ready.store(self.ready as u64,Relaxed);self.control.dry_gain.store(self.gain[0].to_bits(),Relaxed);self.control.wet_gain.store(self.gain[1].to_bits(),Relaxed);self.control.updry_gain.store(self.gain[2].to_bits(),Relaxed);self.control.upwet_gain.store(self.gain[3].to_bits(),Relaxed);self.control.stereo.store(self.stereo as u64,Relaxed);self.control.active.fetch_add(1,Relaxed);&self.output
 }
}
fn matched_gains<const N:usize>(energy:[f64;N])->[f64;N]{let target=energy.iter().copied().fold(f64::INFINITY,f64::min);energy.map(|e|(target/e).sqrt())}
impl Drop for Ab{fn drop(&mut self){let mut worker=self.control.worker.lock().unwrap();if self.control.users.fetch_sub(1,Relaxed)==1{self.control.stop.store(true,Relaxed);if let Some(thread)=worker.take(){let _=thread.join();}}}}
#[cfg(test)]mod tests{use super::*;
 fn isolated(n:usize,delay:usize)->Ab{let control=Arc::new(Control::default());control.users.store(1,Relaxed);control.mode.store(1,Relaxed);Ab::with_control(n,delay,control)}
 #[test]fn matching_only_attenuates_and_equalizes(){for energy in [[1.,16.,2.,9.],[2.;4]]{let g=matched_gains(energy);assert!(g.iter().all(|x|*x<=1.));for b in 1..4{assert!((energy[0]*g[0]*g[0]-energy[b]*g[b]*g[b]).abs()<1e-10);}}}
 #[test]fn dry_delay_and_multichannel_guard(){let mut ab=isolated(4,2);let mut input=vec![0.;48];input[..4].copy_from_slice(&[1.,2.,3.,4.]);ab.prepare(&input,4);assert_eq!(&ab.output[..4],&[0.,0.,1.,2.]);assert!(ab.stereo);input[8]=1.;ab.prepare(&input,4);assert!(!ab.stereo);}
 #[test]fn four_combinations_calibrate_freeze_and_switch_without_gain_jump(){let n=480;let mut ab=isolated(n,0);ab.control.revision.store(1,Relaxed);let mut input=vec![0.;12*n];let mut wet=vec![0.;2*n];for i in 0..n{let x=(i as f32*std::f32::consts::TAU/48.).sin()*0.1;input[i]=x;input[n+i]=x;wet[i]=x*0.25;wet[n+i]=x*0.25;}let upwet:Vec<_>=wet.iter().map(|x|x*2.).collect();for _ in 0..1001{ab.prepare(&input,n);ab.process_four(&wet,&upwet,n);}assert!(ab.ready);assert!((ab.gain[0]-0.25).abs()<1e-6);for mode in [0,2,3,1]{ab.control.mode.store(mode,Relaxed);for _ in 0..6{ab.prepare(&input,n);ab.process_four(&wet,&upwet,n);}assert!(ab.output.iter().zip(&wet).all(|(a,b)|(a-b).abs()<1e-6));assert!((ab.mix.iter().sum::<f64>()-1.).abs()<1e-10);}let g=ab.gain;ab.prepare(&input,n);ab.process_four(&vec![0.;2*n],&vec![0.;2*n],n);assert_eq!(g,ab.gain);}
}