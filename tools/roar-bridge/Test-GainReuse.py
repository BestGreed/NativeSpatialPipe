"""Compile actual reuse/interpreter code; check bounds, opt-in, GC and output history."""
import os
import argparse
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'build/roar-candidates-20261022/reuse-tests';OUT.mkdir(parents=True,exist_ok=True)
parser=argparse.ArgumentParser()
parser.add_argument('--source-root',type=Path,default=ROOT/'work/roar')
args=parser.parse_args()
SOURCE=args.source_root/'src/renderer/olr'
for name in ['processing_block','gain_calculator','interpret_object_metadata']:
    s=(SOURCE/(name+'.rs')).read_text('utf-8');body=s.split('// ===== Tests =====')[0]
    if name=='processing_block':body+='\n'+s[s.index('// Exact zero only, checked'):]
    if name=='interpret_object_metadata':body+='\n'+s[s.index('const REUSE_CAPACITY'):]
    body=re.sub(r'crate::candidate_metrics::add\(\d+\);','',body)
    (OUT/(name+'.rs')).write_text(body,encoding='utf-8')
with (OUT/'interpret_object_metadata.rs').open('a',encoding='utf-8') as f:f.write('''
#[cfg(test)] mod focused {
use super::*;
use std::sync::atomic::{AtomicUsize,Ordering};
#[derive(Debug)] struct Fake {calls:AtomicUsize,safe:bool,offset:f32}
impl GainCalculator for Fake {
 fn calculate_gains(&self,az:crate::common::definitions::Degrees,el:crate::common::definitions::Degrees,d:crate::common::definitions::Distance,g:&mut[f32])->Result<(),OarError>{
  self.calls.fetch_add(1,Ordering::Relaxed);if d.value()<0. {return Err(OarError::InvalidParameter);}
  for(i,v)in g.iter_mut().enumerate(){*v=self.offset+az.0*0.01+el.0*0.02+d.value()*0.03+i as f32*0.125;}Ok(())
 }
 fn gains_count(&self)->usize{4} fn speaker_indices(&self)->&[usize]{&[0,1,2,3]} fn is_convex_hull(&self)->bool{true}
 fn reuse_safe(&self)->bool{self.safe}
}
fn render(safe:bool,coords:&[[f32;3]])->(Vec<u32>,usize){
 let g=Arc::new(Fake{calls:AtomicUsize::new(0),safe,offset:0.});let mut e=InterpretObjectMetadata::new(g.clone());let mut bits=Vec::new();
 for(i,c)in coords.iter().enumerate(){
  let p=PolarCoordinate::new(c[0],c[1],c[2]);let mut b=e.process(SampleRate(48000),&p,(i*32)as u64,Samples(32)).unwrap().unwrap();
  let input:Vec<f32>=(0..32).map(|j|if i%13<4 {0.}else{(j as f32-16.)*0.03125}).collect();let mut out=vec![0.;32*4];
  b.process((i*32)as u64,&input,32,1,&mut out).unwrap();bits.extend(out.into_iter().map(f32::to_bits));
  if let Some(p)=&e.reuse_pool{assert!(p.entries.iter().filter(|e|e.key.is_some()).count()<=4);assert!(p.requests<64);assert!(p.hits<=p.requests);}
 }
 (bits,g.calls.load(Ordering::Relaxed))
}
#[test] fn exact_reuse_preserves_pcm_and_history(){
 for mode in 0..4 {
  let coords:Vec<[f32;3]>=(0..1024).map(|i|match mode{0=>[1.,2.,3.],1=>[(i%4)as f32,2.,3.],2=>[i as f32*0.03125,2.,3.],_=>[if i%2==0{1.}else{f32::from_bits(1f32.to_bits()+1)},-0.,3.]}).collect();
  let(a,uncached)=render(false,&coords);let(b,cached)=render(true,&coords);assert_eq!(a,b);
  assert_eq!(uncached,1024);if mode==0{assert_eq!(cached,1);}if mode==2{assert_eq!(cached,1024);}if mode==3{assert_eq!(cached,2);}
 }
}
#[test] fn gc_has_fixed_capacity_and_single_low_hit_eviction(){
 let mut p=GainReusePool::new(4);for i in 0..4{p.insert([i,0,0],GainsBuffer::new(4));}
 for i in 0..64{assert!(p.lookup([1000+i,0,0]).is_none());}
 assert_eq!(p.entries.iter().filter(|e|e.key.is_some()).count(),3);assert_eq!(p.requests,0);assert_eq!(p.hits,0);
 for i in 0..4096{let key=[10000+i,0,0];assert!(p.lookup(key).is_none());p.insert(key,GainsBuffer::new(4));assert!(p.entries.iter().filter(|e|e.key.is_some()).count()<=4);}
 println!("reuse_pool_bytes={} capacity={} window={}",std::mem::size_of::<GainReusePool>(),REUSE_CAPACITY,REUSE_WINDOW);
}
#[test] fn failed_calculation_is_not_cached_and_new_instance_is_empty(){
 let g=Arc::new(Fake{calls:AtomicUsize::new(0),safe:true,offset:0.});let mut e=InterpretObjectMetadata::new(g.clone());
 for i in 0..2{assert!(e.process(SampleRate(48000),&PolarCoordinate::new(0.,0.,-1.),i*32,Samples(32)).is_err());}
 assert_eq!(g.calls.load(Ordering::Relaxed),2);assert_eq!(e.reuse_pool.as_ref().unwrap().entries.iter().filter(|e|e.key.is_some()).count(),0);
 let fresh=InterpretObjectMetadata::new(Arc::new(Fake{calls:AtomicUsize::new(0),safe:true,offset:1.}));assert!(fresh.reuse_pool.unwrap().entries.iter().all(|e|e.key.is_none()));
}
}
''')
source='''
mod common {pub mod definitions {
 #[derive(Debug,PartialEq,Copy,Clone)]pub enum OarError{InvalidParameter}
 pub const MAX_OUTPUT_CHANNEL_COUNT:usize=25;
 #[derive(Debug,Copy,Clone)]pub struct Degrees(pub f32);
 #[derive(Debug,Copy,Clone)]pub struct Distance(pub f32);impl Distance{pub fn value(self)->f32{self.0}}
 #[derive(Debug,Copy,Clone)]pub struct SampleRate(pub u32);
 #[derive(Debug,Copy,Clone)]pub struct Samples(pub u32);impl From<Samples>for u32{fn from(s:Samples)->u32{s.0}}
 #[derive(Debug,Copy,Clone)]pub struct PolarCoordinate{a:Degrees,e:Degrees,d:Distance}
 impl PolarCoordinate{pub fn new(a:f32,e:f32,d:f32)->Self{Self{a:Degrees(a),e:Degrees(e),d:Distance(d)}}pub fn azimuth(&self)->Degrees{self.a}pub fn elevation(&self)->Degrees{self.e}pub fn distance(&self)->Distance{self.d}}
}}
mod renderer{pub mod olr{
 #[path="BLOCK"]pub mod processing_block;
 #[path="GAIN"]pub mod gain_calculator;
 #[path="INTERP"]pub mod interpret_object_metadata;
}}
'''
for token,name in [('BLOCK','processing_block'),('GAIN','gain_calculator'),('INTERP','interpret_object_metadata')]:source=source.replace(token,(OUT/(name+'.rs')).as_posix())
(OUT/'reuse-test.rs').write_text(source,encoding='utf-8')
llvm=ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin';env=dict(os.environ,PATH=str(llvm)+os.pathsep+os.environ['PATH'])
rust=ROOT/'work/rust/rustup/toolchains/1.97.1-x86_64-pc-windows-gnullvm/bin/rustc.exe'
subprocess.run([str(rust),'--edition=2024','--test','-O',str(OUT/'reuse-test.rs'),'-o',str(OUT/'reuse-test.exe')],env=env,check=True)
subprocess.run([str(OUT/'reuse-test.exe'),'--nocapture'],env=env,check=True)
