//! Conservative subband missing-fundamental prototype, BEFORE spatial FIR.
//! Not a low-shelf boost. Separate subbands reduce (do not eliminate) IMD.
#[derive(Clone,Copy,Default)]
struct Biquad {b:[f64;3],a:[f64;2],z:[f64;2]}
impl Biquad {
    fn new(hz:f64,high:bool)->Self {
        let w=2.*std::f64::consts::PI*hz/48000.;let c=w.cos();let a=w.sin()/2f64.sqrt();let den=1.+a;
        let b=if high {[(1.+c)/2.,-(1.+c),(1.+c)/2.]}else{[(1.-c)/2.,1.-c,(1.-c)/2.]};
        Self{b:b.map(|v|v/den),a:[-2.*c/den,(1.-a)/den],z:[0.;2]}
    }
    fn run(&mut self,x:f64)->f64 {let y=self.b[0]*x+self.z[0];self.z[0]=self.b[1]*x-self.a[0]*y+self.z[1];self.z[1]=self.b[2]*x-self.a[1]*y;y}
}
struct Band {hp:Biquad,lp:Biquad,envelope:f64,dc:Biquad}
impl Band {
    fn new(lo:f64,hi:f64)->Self {Self{hp:Biquad::new(lo,true),lp:Biquad::new(hi,false),envelope:0.,dc:Biquad::new(210.,true)}}
    fn harmonic(&mut self,x:f64)->f64 {
        let x=self.lp.run(self.hp.run(x));
        self.envelope=x.abs().max(self.envelope*(-1f64/(0.080*48000.)).exp());
        if self.envelope<1e-7 {return self.dc.run(0.);}
        let u=(x/self.envelope.max(1e-5)).clamp(-1.,1.);
        let second=2.*u*u-1.;let third=4.*u*u*u-3.*u;let fourth=8.*u.powi(4)-8.*u*u+1.;
        self.dc.run(self.envelope*(0.50*second+0.32*third+0.18*fourth))
    }
}
struct Channel {bands:[Band;3],hp:[Biquad;2],shape:Biquad}
pub struct VirtualBass {channels:Vec<Channel>,amount:f64,protect:bool,lfe_harmonics:Vec<f32>}
impl VirtualBass {
    pub fn new(n:usize,amount:f64,highpass:f64)->Result<Self,String> {
        if !amount.is_finite() || !(0.0..=0.5).contains(&amount) || !highpass.is_finite() || (highpass!=0. && !(80.0..=180.0).contains(&highpass)) {return Err("Invalid virtual bass parameters".into());}
        Ok(Self{amount,protect:highpass!=0.,channels:(0..12).map(|_|Channel{bands:[Band::new(80.,115.),Band::new(115.,165.),Band::new(165.,230.)],hp:[Biquad::new(highpass.max(80.),true);2],shape:Biquad::new(900.,false)}).collect(),lfe_harmonics:vec![0.;n]})
    }
    pub fn process(&mut self,pcm:&mut[f32],n:usize) {
        self.lfe_harmonics.fill(0.);
        for c in 0..12 {
            let channel=&mut self.channels[c];
            for i in 0..n {
                let x=pcm[c*n+i] as f64;
                let h=if self.amount==0. {0.}else{channel.shape.run(channel.bands.iter_mut().map(|b|b.harmonic(x)).sum::<f64>())*self.amount};
                let dry=if self.protect {let protected=channel.hp[0].run(x);channel.hp[1].run(protected)}else{x};
                if c==3 {self.lfe_harmonics[i]=h as f32;pcm[c*n+i]=dry as f32;}
                else {pcm[c*n+i]=if self.amount==0.{dry as f32}else{(dry+h) as f32};}
            }
        }
        // LFE FIR is lowpassed. Route only its synthesized upper harmonics to
        // the virtual CENTER before spatial convolution, not after it.
        if self.amount!=0. {for i in 0..n {pcm[2*n+i]+=self.lfe_harmonics[i];}}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn protection_bypass_is_independent_and_bit_exact_when_harmonics_zero() {
        let mut vb=VirtualBass::new(480,0.,0.).unwrap();
        let mut x:Vec<f32>=(0..5760).map(|i|if i%7==0{-0.0}else{((i as f32)*0.17).sin()*0.1}).collect();
        let bits:Vec<_>=x.iter().map(|v|v.to_bits()).collect();vb.process(&mut x,480);
        assert_eq!(x.iter().map(|v|v.to_bits()).collect::<Vec<_>>(),bits);
        let mut on=VirtualBass::new(480,0.26,0.).unwrap();on.process(&mut x,480);assert!(x.iter().all(|x|x.is_finite()));
    }
    fn component(x:&[f32],f:f64)->f64 {let n=x.len();let (mut re,mut im)=(0.,0.);for(i,&v)in x.iter().enumerate(){let w=2.*std::f64::consts::PI*f*i as f64/48000.;re+=v as f64*w.cos();im+=v as f64*w.sin();}2.*re.hypot(im)/n as f64}
    #[test]
    fn harmonic_band_and_lfe_route_preserve_direction() {
        for source in [0,3,7] {
            let mut vb=VirtualBass::new(480,0.18,130.).unwrap();let mut result=Vec::new();
            for block in 0..200 {let mut x=vec![0.;12*480];for i in 0..480{x[source*480+i]=(0.1*(2.*std::f64::consts::PI*100.*(block*480+i) as f64/48000.).sin()) as f32;}vb.process(&mut x,480);if block>=100 {result.extend_from_slice(&x[(if source==3{2}else{source})*480..(if source==3{3}else{source+1})*480]);}for c in 0..12 {if c!=source && !(source==3 && c==2){assert!(x[c*480..(c+1)*480].iter().all(|&v|v==0.));}}}
            assert!(component(&result,300.)>1e-4);
            assert!(component(&result,400.)>1e-5);
            assert!(component(&result,0.)<1e-5);
            assert!(result.iter().all(|x|x.is_finite()&&x.abs()<0.2));
        }
    }
    #[test]
    fn bounds_and_silence() {
        assert!(VirtualBass::new(480,f64::NAN,130.).is_err());assert!(VirtualBass::new(480,0.6,130.).is_err());assert!(VirtualBass::new(480,0.1,50.).is_err());
        let mut vb=VirtualBass::new(480,0.18,130.).unwrap();let mut x=vec![0.;5760];vb.process(&mut x,480);assert!(x.iter().all(|&v|v==0.));
    }
}
