use super::{Engine, Mapping, Packet, err, telemetry};

pub(super) enum RoarMode { Olr(Engine) }
pub(super) enum Backend {
    Roar(RoarMode),
    Lira(crate::spatial_input::Spatial, Mapping),
    Notebook(Box<Backend>, crate::notebook::Post),
    Lsac(Box<Backend>, nsp_lsac_adapter::Stage, Vec<f32>, Vec<f32>),
}
impl Backend {
    pub(super) fn has_semantic(&self)->bool{matches!(self,Self::Notebook(_,post) if post.has_semantic())}
    pub(super) fn layout(&self) -> OutputLayout {
        let preset = match self {
            Self::Roar(RoarMode::Olr(e)) => e.layout,
            Self::Lira(e, _) => crate::layouts::get(e.layout_code).unwrap(),
            Self::Notebook(..) | Self::Lsac(..) => return OutputLayout { id: "2.0", channels: 2 },
        };
        OutputLayout { id: preset.id, channels: preset.labels.len() }
    }
    pub(super) fn is_lira(&self)->bool{match self{Self::Lira(_,_)=>true,Self::Notebook(core,_) | Self::Lsac(core,..)=>core.is_lira(),_=>false}}
    pub(super) fn channels(&self)->usize{self.layout().channels}
    pub(super) fn process_into<'a>(&'a mut self,seq:u64,count:usize,data:&[u8],object_tap:&mut telemetry::Producer,out:&mut [f32])->Result<Option<&'a [f32]>,String>{
        let channels=self.channels();
        match self {
            Self::Roar(RoarMode::Olr(e))=>{
                let pcm=e.process_tapped(seq,count,data,Some(object_tap))?;
                for i in 0..out.len()/channels { for c in 0..channels { out[i*channels+c]=pcm[c*(out.len()/channels)+i]; } }
                Ok(Some(pcm))
            },
            Self::Lira(e,m)=>{
                e.process_into(seq,count,data,out)?;
                m.packets+=1;m.frames+=(out.len()/channels) as u64;
                Ok(None)
            },
            Self::Lsac(core, stage, interleaved, planar) => {
                let n = out.len() / 2;
                let pcm = if let Self::Roar(RoarMode::Olr(engine)) = core.as_mut() {
                    let rendered = engine.process_sources(seq, &[(0, count, data, 0)], Some(object_tap), false, None)?;
                    stage.process(rendered)
                } else {
                    core.process_into(seq, count, data, object_tap, interleaved)?;
                    for (frame, row) in interleaved.chunks_exact(12).enumerate() {
                        for (channel, sample) in row.iter().enumerate() {
                            planar[channel * n + frame] = *sample;
                        }
                    }
                    stage.process(planar)
                };
                for (frame, dst) in out.chunks_exact_mut(2).enumerate() {
                    dst.copy_from_slice(&[pcm[frame], pcm[n + frame]]);
                }
                Ok(Some(pcm))
            }
            Self::Notebook(core,post)=>{
                let input=crate::capture_input::CaptureInput::new(data,count,out.len()/2).map_err(str::to_string)?;
                post.observe(seq,input.property(0).map_err(str::to_string)?.ident);
                core.process_into(seq,count,data,object_tap,&mut post.interleaved)?;
                let pcm=post.process();let n=out.len()/2;for i in 0..n{for c in 0..2{out[i*2+c]=pcm[c*n+i];}}Ok(Some(pcm))
            }
        }
    }
    pub(super) fn object_tap_truncation(&self) -> (u64, u64) {
        match self {
            Self::Roar(RoarMode::Olr(engine)) => (engine.object_tap_truncated_records, engine.object_tap_truncated_blocks),
            Self::Notebook(core, _) | Self::Lsac(core, ..) => core.object_tap_truncation(),
            _ => (0, 0),
        }
    }
    pub(super) fn timing(&self)->(u64,u64,u64){match self{Self::Roar(RoarMode::Olr(e))=>(e.decode_ns,e.metadata_ns,e.dsp_ns),Self::Notebook(core,_) | Self::Lsac(core,..)=>core.timing(),_ =>(0,0,0)}}
    pub(super) fn process_unified<'a>(
        &'a mut self,
        seq: u64,
        packets: &[Option<Packet>; crate::unified_input::LANES],
        tap: &mut telemetry::Producer,
        output: &mut [f32],
        upmixed_bed: Option<&[f32]>,
    ) -> Result<Option<&'a [f32]>, String> {
        let mut sources = [(0, 0, &[][..], 0); crate::unified_input::LANES];
        let mut len = 0;
        for packet in packets.iter().flatten() {
            if packet.lane == crate::unified_input::PCM && upmixed_bed.is_some() && !tap.active() { continue; }
            sources[len] = (packet.lane, packet.count, packet.data.as_slice(), packet.source_lease);
            len += 1;
        }
        match self {
            Self::Roar(RoarMode::Olr(engine)) => {
                let channels = engine.layout.labels.len();
                let frames = engine.n;
                let pcm = engine.process_sources(seq, &sources[..len], Some(tap), true, upmixed_bed)?;
                for (frame, dst) in output.chunks_exact_mut(channels).enumerate() {
                    for (channel, sample) in dst.iter_mut().enumerate() {
                        *sample = pcm[channel * frames + frame];
                    }
                }
                Ok(Some(pcm))
            }
            Self::Lsac(core, stage, ..) => {
                let Self::Roar(RoarMode::Olr(engine)) = core.as_mut() else {
                    return err("Unified notebook input requires ROAR OLR");
                };
                let rendered = engine.process_sources(seq, &sources[..len], Some(tap), true, upmixed_bed)?;
                let pcm = stage.process(rendered);
                let frames = output.len() / 2;
                for (frame, dst) in output.chunks_exact_mut(2).enumerate() {
                    dst.copy_from_slice(&[pcm[frame], pcm[frames + frame]]);
                }
                Ok(Some(pcm))
            }
            _ => err("Unified input requires ROAR OLR"),
        }
    }
    pub(super) fn finish(&mut self)->Result<Vec<f32>,String>{match self{Self::Roar(_)=>Ok(Vec::new()),Self::Lira(e,_)=>e.finish(),Self::Notebook(core,post)=>Ok(post.finish(&core.finish()?)),Self::Lsac(core, stage, _, planar)=>Ok(crate::notebook::finish_stage(stage, planar, &core.finish()?))}}
    pub(super) fn mapping(self)->Mapping{match self{Self::Roar(RoarMode::Olr(mut e))=>{e.mapping.retires+=e.states.iter().filter(|s|s.present).count() as u64;e.mapping},Self::Lira(_,m)=>m,Self::Notebook(core,_) | Self::Lsac(core,..)=>core.mapping()}}
}
pub(super) struct OutputLayout { pub(super) id: &'static str, pub(super) channels: usize }

