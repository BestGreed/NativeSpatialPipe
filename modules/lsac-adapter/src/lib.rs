//! Experimental measured-device 12-to-2 FIR renderer; loaded before capture.
use rustfft::{Fft, FftPlanner, num_complex::Complex32 as C};
use std::sync::Arc;
/// Identical post-render stage shared by ROAR and LIRA virtual 7.1.4 output.
pub struct Stage { filters:Filters, bass:Option<crate::virtual_bass::VirtualBass>, n:usize }
impl Stage {
    pub fn alignment_delay(&self) -> usize { self.filters.alignment_delay }
    pub fn new(path:&str,n:usize,bass:bool,amount:f64,highpass:f64)->Result<Self,String>{
        Ok(Self{filters:Filters::load(path,n)?,bass:if bass{Some(crate::virtual_bass::VirtualBass::new(n,amount,highpass)?)}else{None},n})
    }
    pub fn process(&mut self,input:&mut[f32])->&[f32]{
        if let Some(bass)=&mut self.bass{bass.process(input,self.n);}
        self.filters.process(input)
    }
    pub fn tail_frames(&self)->usize{self.filters.taps-1+if self.bass.is_some(){10*self.n}else{0}}
    pub fn tail(&mut self)->Vec<f32>{
        let mut out=Vec::new();
        if self.bass.is_some(){let mut zero=vec![0.;12*self.n];for _ in 0..10{zero.fill(0.);let n=self.n;let pcm=self.process(&mut zero);for i in 0..n{out.push(pcm[i]);out.push(pcm[n+i]);}}}
        out.extend(self.filters.tail());out
    }
}
pub struct Filters {
    fft: Arc<dyn Fft<f32>>, ifft: Arc<dyn Fft<f32>>,
    h: Vec<Vec<C>>, history: Vec<Vec<f32>>, x: Vec<Vec<C>>,
    sum: Vec<C>, scratch: Vec<C>, output: Vec<f32>,
    n: usize, size: usize, taps: usize,alignment_delay:usize,
}
impl Filters {
    pub fn load(path: &str, n: usize) -> Result<Self,String> {
        let data=std::fs::read(path).map_err(|e|e.to_string())?;
        if data.len()<24 || &data[..8]!=b"NSPNBF01" {return Err("Notebook filter header".into());}
        let word=|at|u32::from_le_bytes(data[at..at+4].try_into().unwrap()) as usize;
        let taps=word(16);
        if word(8)!=48000 || word(12)!=12 || word(20)!=0 || !(32..=4096).contains(&taps) || data.len()!=24+24*taps*4 {return Err("Notebook filter dimensions".into());}
        let size=(n+taps-1).next_power_of_two();
        let mut planner=FftPlanner::new();let fft=planner.plan_fft_forward(size);let ifft=planner.plan_fft_inverse(size);
        let mut h=Vec::with_capacity(24);let mut direct_energy=vec![0f32;taps];
        for k in 0..24 {
            let mut v=vec![C::default();size];let mut l1=0.;
            for t in 0..taps {let at=24+(k*taps+t)*4;let f=f32::from_le_bytes(data[at..at+4].try_into().unwrap());if !f.is_finite(){return Err("Nonfinite notebook filter".into());}l1+=f.abs();v[t].re=f;if k==0||k==13{direct_energy[t]+=f*f;}}
            if l1>32. {return Err("Notebook filter gain exceeds bound".into());}
            fft.process(&mut v);h.push(v);
        }
        let scratch_len=fft.get_inplace_scratch_len().max(ifft.get_inplace_scratch_len());
        let alignment_delay=direct_energy.iter().enumerate().max_by(|a,b|a.1.total_cmp(b.1)).map(|(i,_)|i).unwrap_or(0);
        Ok(Self{fft,ifft,h,history:vec![vec![0.;taps-1];12],x:vec![vec![C::default();size];12],sum:vec![C::default();size],scratch:vec![C::default();scratch_len],output:vec![0.;2*n],n,size,taps,alignment_delay})
    }
    /// Planar input and output, zero allocations on the render worker.
    pub fn process(&mut self, input:&[f32])->&[f32] {
        let keep=self.taps-1;
        for c in 0..12 {
            let v=&mut self.x[c];v.fill(C::default());
            for t in 0..keep {v[t].re=self.history[c][t];}
            for t in 0..self.n {v[keep+t].re=input[c*self.n+t];}
            // Last keep samples of history concatenated with this block.
            if self.n>=keep {self.history[c].copy_from_slice(&input[c*self.n+self.n-keep..(c+1)*self.n]);}
            else {self.history[c].copy_within(self.n..,0);self.history[c][keep-self.n..].copy_from_slice(&input[c*self.n..(c+1)*self.n]);}
            self.fft.process_with_scratch(v,&mut self.scratch);
        }
        for ear in 0..2 {
            self.sum.fill(C::default());
            for c in 0..12 {for k in 0..self.size {self.sum[k]+=self.x[c][k]*self.h[ear*12+c][k];}}
            self.ifft.process_with_scratch(&mut self.sum,&mut self.scratch);
            for t in 0..self.n {self.output[ear*self.n+t]=self.sum[keep+t].re/self.size as f32;}
        }
        &self.output
    }
    pub fn tail(&mut self)->Vec<f32>{
        let zero=vec![0.;12*self.n];let mut result=Vec::new();let mut remain=self.taps-1;
        while remain>0 {let n=self.n;let pcm=self.process(&zero);let take=remain.min(n);for i in 0..take {result.push(pcm[i]);result.push(pcm[n+i]);}remain-=take;}
        result
    }
}

mod virtual_bass;
pub mod loudness;
