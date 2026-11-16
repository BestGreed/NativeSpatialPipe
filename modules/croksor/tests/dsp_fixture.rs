//! Offline differential probe using the production Rust DSP implementation.
#[path="../src/dsp/output_processing.rs"] mod output_processing;
#[path="../src/dsp/limiter_control.rs"] mod limiter_control;
use output_processing::{Config,Processor};
fn main()->Result<(),Box<dyn std::error::Error>> {
    let a:Vec<_>=std::env::args().collect();assert_eq!(a.len(),6);
    let config=std::fs::read(&a[1])?;assert_eq!(config.len(),std::mem::size_of::<Config>());
    let config=unsafe{std::ptr::read_unaligned(config.as_ptr().cast::<Config>())};
    let bytes=std::fs::read(&a[2])?;assert_eq!(bytes.len()%4,0);
    let mut samples:Vec<f32>=bytes.chunks_exact(4).map(|v|f32::from_le_bytes(v.try_into().unwrap())).collect();
    let mut dsp=Processor::new(config)?;let block:usize=a[4].parse()?;
    assert!(block>0);let channels=dsp.channels();
    assert_eq!(samples.len()%channels,0);
    for part in samples.chunks_mut(block*channels){dsp.process(part)?;}
    let mut out=Vec::new();for &s in &samples {out.extend_from_slice(&s.to_le_bytes());}
    dsp.finish();let mut tail=vec![0f32;block*channels];
    loop{let frames=dsp.drain(&mut tail)?;if frames==0{break;}
        for &s in &tail[..frames*channels]{out.extend_from_slice(&s.to_le_bytes());}}
    std::fs::write(&a[3],out)?;
    // a[5] is a probe identifier, recorded by the test driver.
    Ok(())
}
