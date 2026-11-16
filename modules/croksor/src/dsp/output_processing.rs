//! Shared in-place output DSP, owned and executed by the Rust pool writer.
//! Port of the validated N06 algorithm; no device timing or SRC here.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Config {
    pub size: u32, pub version: u32, pub channels: u32, pub sample_rate: u32,
    pub bypass: u32, pub limiter: u32, pub lookahead_frames: u32, pub reserved: u32,
    pub master: f64, pub ceiling: f64, pub release_ms: f64,
    pub trim: [f64;32], pub delay_frames: [u32;32],
}

#[cfg(test)]
mod tests {
    use super::*;
    use nsp_lsac_adapter::loudness::{Level, Loudness};
    use std::sync::{Arc, atomic::{AtomicU64, Ordering::Relaxed}};

    #[test]
    fn equal_loudness_follows_hardware_and_feeds_the_limiter() {
        let endpoint_db = Arc::new(AtomicU64::new(0.0_f64.to_bits()));
        let points = [
            Level { spl_db: 55.0, low_db: 4.0, high_db: 0.5 },
            Level { spl_db: 75.0, low_db: 0.0, high_db: 0.0 },
        ];
        let cfg = Config { channels: 2, limiter: 1, ceiling: 0.8, lookahead_frames: 240, ..Config::default() };
        let mut processor = Processor::new(cfg).unwrap();
        let mut loudness = Loudness::new(endpoint_db.clone(), 75.0, &points).unwrap();
        let mut plain = Processor::new(cfg).unwrap();
        let tone = |amplitude: f64| -> Vec<f32> {
            (0..48000).flat_map(|i| {
                let v = (amplitude * (std::f64::consts::TAU * 315.0 * i as f64 / 48000.0).sin()) as f32;
                [v, v]
            }).collect()
        };
        let peak = |samples: &[f32]| samples.iter().fold(0.0_f32, |p, v| p.max(v.abs()));
        let mut full = tone(0.1);
        let mut baseline = full.clone();
        loudness.process(&mut full); processor.process(&mut full).unwrap();
        plain.process(&mut baseline).unwrap();
        assert_eq!(full, baseline);
        endpoint_db.store((-10.0_f64).to_bits(), Relaxed);
        let mut low = tone(0.1);
        loudness.process(&mut low); processor.process(&mut low).unwrap();
        let db = 20.0 * f64::from(peak(&low[48000..]) / peak(&baseline[48000..])).log10();
        assert!((db - 2.0).abs() < 0.03, "{db}");
        assert!(low.chunks_exact(2).all(|frame| frame[0] == frame[1]));
        let mut hot = tone(0.72);
        loudness.process(&mut hot); processor.process(&mut hot).unwrap();
        assert!((peak(&hot) - 0.8).abs() < 0.001);
        endpoint_db.store(f64::NAN.to_bits(), Relaxed);
        let mut unavailable = tone(0.1);
        loudness.process(&mut unavailable); processor.process(&mut unavailable).unwrap();
        assert!((peak(&unavailable[80_000..]) - 0.1).abs() < 0.001);
    }
    #[test]
    fn invalid_block_does_not_mutate_pcm_or_state() {
        let mut p=Processor::new(Config{channels:2,master:0.5,..Config::default()}).unwrap();
        let mut bad=[1.,f32::NAN];let bits=bad.map(f32::to_bits);
        assert!(p.process(&mut bad).is_err());assert_eq!(bad.map(f32::to_bits),bits);
        assert_eq!(p.frame,0);assert!(!p.has_input);
    }
    #[test]
    fn bypass_preserves_bits_and_has_no_history_or_tail() {
        let mut c=Config{channels:2,bypass:1,limiter:1,lookahead_frames:4800,..Config::default()};c.delay_frames[0]=48000;
        let mut p=Processor::new(c).unwrap();let mut pcm=[-0.,f32::from_bits(0x7fc00001),2.,-2.];
        let bits=pcm.map(f32::to_bits);p.process(&mut pcm).unwrap();assert_eq!(pcm.map(f32::to_bits),bits);
        assert!(p.delay.is_empty() && p.pending.is_empty() && p.peaks.is_empty());
        p.finish();assert_eq!(p.drain(&mut[0.;4]).unwrap(),0);assert!(p.process(&mut pcm).is_err());
    }
    #[test]
    fn configuration_rejected_before_large_allocations() {
        assert!(Processor::new(Config{version:2,..Config::default()}).is_err());
        assert!(Processor::new(Config{channels:33,..Config::default()}).is_err());
        assert!(Processor::new(Config{lookahead_frames:4801,..Config::default()}).is_err());
        let mut c=Config::default();c.delay_frames[0]=48001;assert!(Processor::new(c).is_err());
    }
    #[test]
    fn lookahead_limits_transients_preserves_stereo_and_drains_exact_tail(){
        let config=Config{channels:2,master:4.,limiter:1,ceiling:0.8,lookahead_frames:240,..Config::default()};
        let mut p=Processor::new(config).unwrap();let mut pcm=vec![0.;2000*2];
        for i in 0..2000{let x=if i==600{8.}else{0.1};pcm[2*i]=x;pcm[2*i+1]=x*0.25;}
        p.process(&mut pcm).unwrap();assert!(pcm[..480].iter().all(|x|*x==0.));
        assert!(pcm.iter().all(|x|x.is_finite()&&x.abs()<=0.800001));
        for frame in pcm.chunks_exact(2){assert!((frame[1]-frame[0]*0.25).abs()<1e-7);}
        // Gain begins declining before the delayed transient reaches the output.
        assert!(pcm[2*800]<pcm[2*600]);
        p.finish();let mut tail=vec![0.;960];assert_eq!(p.drain(&mut tail).unwrap(),240);assert_eq!(p.drain(&mut tail).unwrap(),0);
    }
    #[test]
    fn lookahead_is_independent_of_process_block_boundaries(){
        let config=Config{channels:2,master:3.,limiter:1,ceiling:0.8,lookahead_frames:240,..Config::default()};
        let source:Vec<f32>=(0..12000).map(|i|((i as f32*0.17).sin())*if i%997==0{4.}else{0.3}).collect();
        let mut a=source.clone();let mut b=source;let mut p=Processor::new(config).unwrap();let mut q=Processor::new(config).unwrap();
        p.process(&mut a).unwrap();for chunk in b.chunks_mut(74){q.process(chunk).unwrap();}assert_eq!(a,b);
    }
    #[test]
    fn live_gain_and_telemetry_report_actual_pre_limiter_signal(){
        crate::limiter_control::set_gain(6.);let mut p=Processor::new(Config{channels:2,limiter:1,ceiling:0.8,lookahead_frames:240,..Config::default()}).unwrap();
        let meter=crate::limiter_control::register(1.,0.8,240);p.meter=Some(meter.clone());let mut pcm=vec![0.5;48000*2];p.process(&mut pcm).unwrap();
        use std::sync::atomic::Ordering::Relaxed;let v=|a:&std::sync::atomic::AtomicU64|f64::from_bits(a.load(Relaxed));
        assert!((v(&meter.gain)-6.).abs()<1e-9);assert!((v(&meter.pre)-0.5*10f64.powf(6./20.)).abs()<1e-6);
        assert!(v(&meter.post)<=0.800001);assert!(v(&meter.reduction)>1.8);assert!(v(&meter.percent)>99.);
        crate::limiter_control::set_gain(0.);let mut low=vec![0.01;48000*2];p.process(&mut low).unwrap();assert!(p.boost_db.abs()<1e-9);assert!(v(&meter.reduction)<0.1);
    }
}
impl Default for Config {
    fn default()->Self {Self {size:std::mem::size_of::<Self>() as u32,version:1,channels:12,
        sample_rate:48000,bypass:0,limiter:0,lookahead_frames:0,reserved:0,
        master:1.,ceiling:1.,release_ms:50.,trim:[1.;32],delay_frames:[0;32]}}
}
impl Config {
    pub fn validate(&self)->Result<(),String> {
        if self.size as usize != std::mem::size_of::<Self>() || self.version!=1 ||
            self.reserved!=0 || !(1..=32).contains(&self.channels) || self.sample_rate!=48000 ||
            self.bypass>1 || self.limiter>1 || !self.master.is_finite() || !(0.0..=16.0).contains(&self.master) ||
            !self.ceiling.is_finite() || self.ceiling<=0. || self.ceiling>1. ||
            !self.release_ms.is_finite() || !(0.0..=10000.0).contains(&self.release_ms) ||
            self.lookahead_frames>4800 {return Err("Invalid output DSP config".into());}
        for ch in 0..self.channels as usize {
            if !self.trim[ch].is_finite() || !(0.0..=16.0).contains(&self.trim[ch]) || self.delay_frames[ch]>48000 {
                return Err("Invalid channel DSP config".into());
            }
        } Ok(())
    }
    pub fn neutral(&self)->bool {self.master==1. && self.limiter==0 &&
        (0..self.channels as usize).all(|c|self.trim[c]==1. && self.delay_frames[c]==0)}
}
#[derive(Clone,Copy,Default)]
struct Peak { index:u64, value:f64 }
pub struct Processor {
    meter:Option<std::sync::Arc<crate::limiter_control::Meter>>,boost_db:f64,attack:f64,pre_peak:f64,post_peak:f64,peak_hold:f64,max_reduction:f64,window_frames:u64,limited_frames:u64,window_reduction:f64,
    config:Config, delay:Vec<f32>, pending:Vec<f32>, peaks:Vec<Peak>,
    max_delay:u32, lookahead:u32, frame:u64, peak_head:usize, peak_count:usize,
    envelope:f64, release:f64, finishing:bool, tail_left:u32, has_input:bool,
    master_is_ceiling: bool,
}
impl Processor {
    pub fn new(config:Config)->Result<Self,String> {
        config.validate()?;
        let channels=config.channels as usize;
        let max_delay=if config.bypass!=0 {0}else{*config.delay_frames[..channels].iter().max().unwrap()};
        let lookahead=if config.limiter!=0 && config.bypass==0 {config.lookahead_frames}else{0};
        Ok(Self {meter:None,boost_db:0.,attack:(-1f64/(0.001*48000.)).exp(),pre_peak:0.,post_peak:0.,peak_hold:0.,max_reduction:0.,window_frames:0,limited_frames:0,window_reduction:0.,config,delay:vec![0.;if max_delay==0 {0}else{(max_delay as usize+1)*channels}],
            pending:vec![0.;if lookahead==0 {0}else{(lookahead as usize+1)*channels}],
            peaks:vec![Peak::default();if config.limiter!=0 && config.bypass==0 {lookahead as usize+1}else{0}],
            max_delay,lookahead,frame:0,peak_head:0,peak_count:0,envelope:1.,
            release:if config.release_ms==0. {0.}else{(-1.0/(config.release_ms*0.001*48000.)).exp()},
            finishing:false,tail_left:0,has_input:false,master_is_ceiling:false})
    }
    pub fn enable_live_control(&mut self, master_is_ceiling: bool){
        self.master_is_ceiling = master_is_ceiling;
        if self.config.limiter==0||self.config.bypass!=0{return}
        let enabled=std::env::var_os("LOCALAPPDATA").is_some_and(|p|std::path::PathBuf::from(p).join("NativeSpatialPipe/dsp-ab/enabled").is_file());
        if enabled || master_is_ceiling {self.meter=Some(crate::limiter_control::register(self.config.master,self.config.ceiling,self.lookahead));}
    }
    pub fn channels(&self)->usize {self.config.channels as usize}
    pub fn process(&mut self,samples:&mut[f32])->Result<(),String> {
        if self.finishing {return Err("Input finished".into());}
        let channels=self.channels();
        if samples.len()%channels!=0 {return Err("Invalid output block".into());}
        let frames=samples.len()/channels;
        if self.frame.checked_add(frames as u64).is_none(){return Err("Frame overflow".into());}
        if self.config.bypass!=0 || self.config.neutral() {
            self.frame+=frames as u64;self.has_input|=frames>0;return Ok(());
        }
        for (i,&s) in samples.iter().enumerate() {
            let value=(s as f64)*self.config.master*self.config.trim[i%channels];
            if !s.is_finite() || !value.is_finite() || value.abs()>f32::MAX as f64 {
                return Err("Invalid output sample".into());
            }

        }
        self.has_input|=frames>0;self.process_unchecked(samples);Ok(())
    }
    pub(crate) fn report_adapter_gain(&self, gains_db: [f64; 2]) {
        if let Some(meter) = &self.meter {
            for (field, gain) in meter.loudness_gain.iter().zip(gains_db) {
                field.store(gain.to_bits(), std::sync::atomic::Ordering::Relaxed);
            }
        }
    }
    fn process_unchecked(&mut self,samples:&mut[f32]) {
        let channels=self.channels();
        let requested = if self.meter.is_some(){crate::limiter_control::gain()}else{0.};
        let target_boost = if self.master_is_ceiling { requested.min(0.) } else { requested };
        for output in samples.chunks_exact_mut(channels) {
            self.boost_db+=(target_boost-self.boost_db).clamp(-12./2400.,12./2400.);
            let boost=10f64.powf(self.boost_db/20.);
            let mut aligned=[0f32;32];let mut peak=0f64;
            for ch in 0..channels {
                let gain=self.config.master*self.config.trim[ch]*boost;
                let value=if gain==1. {output[ch]}else{(output[ch] as f64*gain) as f32};
                if self.max_delay==0 {aligned[ch]=value;}else{
                    let length=self.max_delay as u64+1;
                    self.delay[(self.frame%length) as usize*channels+ch]=value;
                    let read=(self.frame%length+length-self.config.delay_frames[ch] as u64)%length;
                    aligned[ch]=self.delay[read as usize*channels+ch];
                }
                peak=peak.max((aligned[ch] as f64).abs());
            }
            if self.config.limiter==0 {output.copy_from_slice(&aligned[..channels]);}else{
                let oldest=self.frame.saturating_sub(self.lookahead as u64);
                while self.peak_count>0 && self.peaks[self.peak_head].index<oldest {
                    self.peak_head=(self.peak_head+1)%self.peaks.len();self.peak_count-=1;
                }
                while self.peak_count>0 {
                    let back=(self.peak_head+self.peak_count-1)%self.peaks.len();
                    if self.peaks[back].value>peak {break;}self.peak_count-=1;
                }
                let next=(self.peak_head+self.peak_count)%self.peaks.len();
                self.peaks[next]=Peak{index:self.frame,value:peak};self.peak_count+=1;
                let maximum=self.peaks[self.peak_head].value;
                let target=if maximum>self.config.ceiling {self.config.ceiling/maximum}else{1.};
                self.envelope=if self.lookahead>0&&target<self.envelope{target+self.attack*(self.envelope-target)}else{target.min(self.release*self.envelope+(1.-self.release))};
                if self.lookahead>0 {
                    let length=self.lookahead as u64+1;let write=(self.frame%length) as usize*channels;
                    self.pending[write..write+channels].copy_from_slice(&aligned[..channels]);
                    if self.frame<self.lookahead as u64 {output.fill(0.);}else{
                        let read=((self.frame-self.lookahead as u64)%length) as usize*channels;
                        let delayed_peak=self.pending[read..read+channels].iter().map(|x|(*x as f64).abs()).fold(0f64,f64::max);
                        self.envelope=self.envelope.min(self.config.ceiling/delayed_peak.max(self.config.ceiling));
                        for ch in 0..channels {output[ch]=(self.pending[read+ch] as f64*self.envelope) as f32;}
                    }
                }else{for ch in 0..channels {output[ch]=(aligned[ch] as f64*self.envelope) as f32;}}
            }
            if self.meter.is_some(){
                self.pre_peak=self.pre_peak.max(peak);self.peak_hold=self.peak_hold.max(peak);
                self.post_peak=self.post_peak.max(output.iter().map(|x|(*x as f64).abs()).fold(0f64,f64::max));
                let reduction=-20.*self.envelope.max(1e-12).log10();self.window_reduction=self.window_reduction.max(reduction);self.max_reduction=self.max_reduction.max(reduction);
                self.window_frames+=1;if reduction>0.1{self.limited_frames+=1;}
                if self.window_frames>=4800{let m=self.meter.as_ref().unwrap();use std::sync::atomic::Ordering::Relaxed;
                    for (dst,v) in [(&m.gain,self.boost_db),(&m.pre,self.pre_peak),(&m.post,self.post_peak),(&m.hold,self.peak_hold),(&m.reduction,self.window_reduction),(&m.max_reduction,self.max_reduction),(&m.percent,100.*self.limited_frames as f64/self.window_frames as f64)]{dst.store(v.to_bits(),Relaxed);}m.frames.store(self.frame+1,Relaxed);
                    self.window_frames=0;self.limited_frames=0;self.pre_peak=0.;self.post_peak=0.;self.window_reduction=0.;
                }
            }
            self.frame+=1;
        }
    }
    pub fn finish(&mut self) {if !self.finishing {self.finishing=true;
        self.tail_left=if self.has_input {self.max_delay+self.lookahead}else{0};}}
    pub fn drain(&mut self,output:&mut[f32])->Result<usize,String> {
        if !self.finishing {return Err("Input not finished".into());}
        let channels=self.channels();
        if output.is_empty() || output.len()%channels!=0 {return Err("Invalid output block".into());}
        let frames=(output.len()/channels).min(self.tail_left as usize);
        if self.frame.checked_add(frames as u64).is_none(){return Err("Frame overflow".into());}
        let samples=&mut output[..frames*channels];samples.fill(0.);self.process_unchecked(samples);
        self.tail_left-=frames as u32;Ok(frames)
    }
}

// Diagnostic/control API uses the same Rust implementation as the pool writer.
// Normal playback constructs Processor internally and does not call these per block.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_output_dsp_create(config:*const Config)->*mut std::ffi::c_void {
    std::panic::catch_unwind(|| {
        if config.is_null(){return std::ptr::null_mut();}
        Processor::new(unsafe{*config}).map(|p|Box::into_raw(Box::new(p)).cast())
            .unwrap_or(std::ptr::null_mut())
    }).unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_output_dsp_process(handle:*mut std::ffi::c_void,op:u32,
    pcm:*mut f32,frames:u32,channels:u32,written:*mut u32)->i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if handle.is_null() || pcm.is_null() || written.is_null() || frames==0 || op>1 {return -22;}
        let p=unsafe{&mut *handle.cast::<Processor>()};
        if channels as usize!=p.channels(){return -22;}
        let Some(len)=(frames as usize).checked_mul(channels as usize) else{return -22;};
        if len>isize::MAX as usize/std::mem::size_of::<f32>() {return -22;}
        let samples=unsafe{std::slice::from_raw_parts_mut(pcm,len)};
        let result=if op==0 {p.process(samples).map(|_|frames as usize)}else{p.finish();p.drain(samples)};
        match result {Ok(n)=>{unsafe{*written=n as u32};0},Err(_)=>-1}
    })).unwrap_or(-1000)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_output_dsp_destroy(handle:*mut std::ffi::c_void) {
    if !handle.is_null(){drop(unsafe{Box::from_raw(handle.cast::<Processor>())});}
}
