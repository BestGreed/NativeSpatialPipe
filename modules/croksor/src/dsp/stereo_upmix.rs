//! Temporary synthetic 7.1.4 audition. Stereo only; no source separation claim.
//! Correlated/balanced content steers toward C; a high-passed side residual
//! feeds quiet decorrelated surround/height channels. LFE stays zero.
struct Allpass { data:Vec<f32>,at:usize,g:f32 }
impl Allpass {
 fn new(delay:usize,g:f32)->Self{Self{data:vec![0.;delay],at:0,g}}
 fn run(&mut self,x:f32)->f32{let d=self.data[self.at];let y=d-self.g*x;self.data[self.at]=x+self.g*y;self.at=(self.at+1)%self.data.len();y}
}
pub struct Upmix {ll:f64,rr:f64,lr:f64,fast:f64,slow:f64,hp_x:f32,hp_y:f32,ap:Vec<Allpass>}
impl Upmix {
 pub fn new()->Self{Self{ll:0.,rr:0.,lr:0.,fast:0.,slow:0.,hp_x:0.,hp_y:0.,ap:[149,211,307,401,503,601,701,809].into_iter().enumerate().map(|(i,d)|Allpass::new(d,if i%2==0{0.55}else{-0.55})).collect()}}
 pub fn process(&mut self,bus:&mut[f32],n:usize)->bool{
  if bus[2*n..].iter().any(|v|v.abs()>1e-7){return false;}
  let covariance=1.-(-1f64/(0.040*48000.)).exp();let fast=1.-(-1f64/(0.005*48000.)).exp();let slow=1.-(-1f64/(0.080*48000.)).exp();let hp=(-std::f32::consts::TAU*180./48000.).exp();
  for i in 0..n{
   let l=bus[i];let r=bus[n+i];let ld=l as f64;let rd=r as f64;
   self.ll+=covariance*(ld*ld-self.ll);self.rr+=covariance*(rd*rd-self.rr);self.lr+=covariance*(ld*rd-self.lr);
   let product=(self.ll*self.rr).sqrt();let correlation=(self.lr/(product+1e-15)).clamp(0.,1.);let balance=(2.*product/(self.ll+self.rr+1e-15)).clamp(0.,1.);
   let c=(0.55*correlation*balance) as f32*(l+r)*std::f32::consts::FRAC_1_SQRT_2;
   bus[i]=l-c*std::f32::consts::FRAC_1_SQRT_2;bus[n+i]=r-c*std::f32::consts::FRAC_1_SQRT_2;bus[2*n+i]=c;
   let side=(l-r)*0.5;let high=hp*(self.hp_y+side-self.hp_x);self.hp_x=side;self.hp_y=high;
   let energy=0.5*(ld*ld+rd*rd);self.fast+=fast*(energy-self.fast);self.slow+=slow*(energy-self.slow);let transient=(self.slow/(self.fast+1e-15)).clamp(0.,1.).sqrt();
   let ambient=high*(balance*transient*(1.-0.7*correlation)) as f32;
   for (k,scale) in [0.26,0.26,0.16,0.16,0.08,0.08,0.05,0.05].into_iter().enumerate(){bus[(k+4)*n+i]=self.ap[k].run(if k%2==0{ambient}else{-ambient})*scale;}
   bus[3*n+i]=0.;
  }true
 }
}
#[cfg(test)]mod tests{use super::*;
 #[test]fn mono_does_not_create_surround_and_front_reconstructs(){let n=480;let mut u=Upmix::new();for _ in 0..20{let mut bus=vec![0.;12*n];for i in 0..n{let x=(i as f32*0.05).sin()*0.1;bus[i]=x;bus[n+i]=x;}let original=bus[..n].to_vec();assert!(u.process(&mut bus,n));assert!(bus[3*n..].iter().all(|x|*x==0.));for i in 0..n{assert!((bus[i]+bus[2*n+i]*std::f32::consts::FRAC_1_SQRT_2-original[i]).abs()<1e-7);}}}
 #[test]fn single_sided_source_stays_front_and_multichannel_is_untouched(){let n=480;let mut u=Upmix::new();let mut bus=vec![0.;12*n];bus[..n].fill(0.1);let original=bus.clone();u.process(&mut bus,n);assert_eq!(bus,original);bus[4*n]=0.2;let original=bus.clone();assert!(!u.process(&mut bus,n));assert_eq!(bus,original);}
 #[test]fn stereo_creates_finite_synthetic_surround_without_lfe(){let n=480;let mut u=Upmix::new();let mut energy=0.;for block in 0..40{let mut bus=vec![0.;12*n];for i in 0..n{let t=(block*n+i) as f32;bus[i]=(t*0.11).sin()*0.1;bus[n+i]=(t*0.17).sin()*0.1;}u.process(&mut bus,n);assert!(bus.iter().all(|x|x.is_finite()));assert!(bus[3*n..4*n].iter().all(|x|*x==0.));energy+=bus[4*n..].iter().map(|x|x*x).sum::<f32>();}assert!(energy>0.01);}
}
