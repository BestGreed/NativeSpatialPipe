//! Notebook integration and legacy audition controls.
use nsp_lsac_adapter::Stage;

/// Public renderer-independent post-processing of a virtual 7.1.4 bus.
/// Neither the ROAR Engine nor LIRA Spatial owns any notebook DSP state.
pub struct Post {stage:Stage,upstage:Stage,upmix:crate::stereo_upmix::Upmix,semantic:Option<crate::semantic::Semantic>,freesurround:Option<crate::freesurround::FreeSurround>,joint:Option<crate::joint::Joint>,joint_active:bool,switch_mute:usize,switch_gain:f32,switch_output:Vec<f32>,baseline:Vec<f32>,ab:crate::dsp_ab::Ab,pub interleaved:Vec<f32>,planar:Vec<f32>,n:usize}
impl Post {
    pub fn new(filters:&str,n:usize,bass:bool,amount:f64,highpass:f64,source_path:&str)->Result<Self,String>{
        let stage=Stage::new(filters,n,bass,amount,highpass)?;
        let ab=crate::dsp_ab::Ab::new(n,stage.alignment_delay());
        let upstage=Stage::new(filters,n,bass,amount,highpass)?;
        let semantic=crate::semantic::Semantic::configured(source_path,n)?;
        let freesurround=crate::freesurround::FreeSurround::configured(semantic.is_some(),n)?;
        let joint=crate::joint::Joint::configured(source_path,n)?;
        Ok(Self{joint,joint_active:false,switch_mute:0,switch_gain:1.,switch_output:vec![0.;2*n],stage,upstage,upmix:crate::stereo_upmix::Upmix::new(),semantic,freesurround,baseline:vec![0.;2*n],ab,interleaved:vec![0.;n*12],planar:vec![0.;n*12],n})
    }
    pub fn observe(&mut self,sequence:u64,identity:u32){if let Some(j)=&mut self.joint{if j.observe(sequence,identity){self.ab.invalidate();}}if let Some(s)=&mut self.semantic{if s.observe(sequence,identity){self.ab.invalidate();if let Some(fs)=&mut self.freesurround{fs.reset();}}}}
    pub fn has_semantic(&self)->bool{self.semantic.is_some()}
    pub fn process(&mut self)->&[f32]{
        let requested=self.ab.pipeline()&&self.joint.is_some();
        if requested!=self.joint_active&&self.switch_gain<=1e-4{self.joint_active=requested;self.switch_mute=7680;self.switch_gain=0.;if let Some(s)=&mut self.semantic{s.reset();}if let Some(fs)=&mut self.freesurround{fs.reset();}if let Some(j)=&mut self.joint{j.reset();}}
        if self.joint.is_some(){crate::joint::set_active(self.joint_active);}
        if self.joint_active{self.joint.as_mut().unwrap().process(&mut self.interleaved,self.n);}
        else if let Some(s)=&mut self.semantic{s.process(&mut self.interleaved,self.n);if let Some(fs)=&mut self.freesurround{fs.process(&mut self.interleaved,&s.vocal,self.n);}}
        for i in 0..self.n{for c in 0..12{self.planar[c*self.n+i]=self.interleaved[i*12+c];}}
        self.ab.prepare(&self.planar,self.n);
        self.baseline.copy_from_slice(self.stage.process(&mut self.planar));
        for i in 0..self.n{for c in 0..12{self.planar[c*self.n+i]=self.interleaved[i*12+c];}}
        let algorithm=self.ab.algorithm();let semantic=algorithm>0&&self.semantic.is_some();
        let stereo=self.planar[2*self.n..].iter().all(|x|x.abs()<1e-7);
        if semantic&&stereo{if self.joint_active{let j=self.joint.as_ref().unwrap();if algorithm==1&&j.available{self.planar.copy_from_slice(&j.output);}}else if let Some(fs)=&self.freesurround{if algorithm==1{self.planar.copy_from_slice(&fs.output);for i in 0..2*self.n{self.planar[i]+=fs.vocal[i];}}}else{let s=self.semantic.as_ref().unwrap();for i in 0..2*self.n{self.planar[i]-=s.vocal[i];}if algorithm==1{self.upmix.process(&mut self.planar,self.n);}for i in 0..2*self.n{self.planar[i]+=s.vocal[i];}}}
        else{self.upmix.process(&mut self.planar,self.n);}
        self.ab.calibration_valid(!semantic||if self.joint_active{self.joint.as_ref().unwrap().available}else{self.semantic.as_ref().unwrap().available});
        self.ab.prepare_upmix(&self.planar,self.n);
        let upwet=self.upstage.process(&mut self.planar);
        let output=self.ab.process_four(&self.baseline,upwet,self.n);
        for i in 0..self.n{if requested!=self.joint_active{self.switch_gain=(self.switch_gain-1./480.).max(0.);}else if self.switch_mute>0{self.switch_mute-=1;self.switch_gain=0.;}else{self.switch_gain=(self.switch_gain+1./2400.).min(1.);}for c in 0..2{self.switch_output[c*self.n+i]=output[c*self.n+i]*self.switch_gain;}}
        &self.switch_output
    }
    pub fn finish(&mut self,source:&[f32])->Vec<f32>{
        if self.semantic.is_some(){
            let source_frames=source.len()/12;let target=source_frames+self.semantic.as_ref().unwrap().delay_frames()+self.freesurround.as_ref().map_or(0,|fs|fs.delay_frames())+self.stage.tail_frames();let mut out=Vec::new();
            for chunk in source.chunks(self.n*12){self.interleaved.fill(0.);self.interleaved[..chunk.len()].copy_from_slice(chunk);let n=self.n;let pcm=self.process();for i in 0..n{out.push(pcm[i]);out.push(pcm[n+i]);}}
            while out.len()/2<target{self.interleaved.fill(0.);let n=self.n;let pcm=self.process();for i in 0..n{out.push(pcm[i]);out.push(pcm[n+i]);}}out.truncate(target*2);return out;
        }
        finish_stage(&mut self.stage, &mut self.planar, source)
    }
}

pub fn finish_stage(stage: &mut Stage, planar: &mut [f32], source: &[f32]) -> Vec<f32> {
    let n = planar.len() / 12;
    let target = source.len() / 12 + stage.tail_frames();
    let mut out = Vec::with_capacity(target * 2);
    for chunk in source.chunks(n * 12) {
        planar.fill(0.);
        for (frame, row) in chunk.chunks_exact(12).enumerate() {
            for (channel, sample) in row.iter().enumerate() {
                planar[channel * n + frame] = *sample;
            }
        }
        let pcm = stage.process(planar);
        for i in 0..n { out.extend([pcm[i], pcm[n+i]]); }
    }
    out.extend(stage.tail());
    out.truncate(target * 2);
    out
}
