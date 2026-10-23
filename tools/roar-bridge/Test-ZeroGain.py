"""Compare actual original/patched gain blocks and full 140-slot ROAR outputs."""
import ctypes as C
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',type=Path,default=ROOT/'work/roar')
parser.add_argument('--candidate',type=Path,default=ROOT/'build/roar-native/release/nsp_roar_bridge.dll')
parser.add_argument('--reference',type=Path,default=ROOT/'build/roar-kernel-audit-20261022/current.dll')
parser.add_argument('--output',type=Path,default=ROOT/'build/roar-zero-gain-20261022')
args=parser.parse_args();OUT=args.output;OUT.mkdir(parents=True,exist_ok=True)
LLVM=ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
RUST=ROOT/'work/rust/rustup/toolchains/1.97.1-x86_64-pc-windows-gnullvm/bin/rustc.exe'
env=dict(os.environ,PATH=str(LLVM)+os.pathsep+os.environ['PATH'])
original=subprocess.check_output(['git','-C','work/roar','show','HEAD:src/renderer/olr/processing_block.rs'],cwd=ROOT).decode('utf-8')
patched=(args.source_root/'src/renderer/olr/processing_block.rs').read_text('utf-8')
for name,text in [('original',original),('patched',patched)]:
    body=text.split('// ===== Tests =====')[0]
    if '// Exact zero only, checked' in text:body+='\n'+text[text.index('// Exact zero only, checked'):]
    (OUT/(name+'.rs')).write_text(body,encoding='utf-8')
harness='''
mod common {pub mod definitions {#[derive(Debug,PartialEq)] pub enum OarError{InvalidParameter}
pub const MAX_OUTPUT_CHANNEL_COUNT:usize=32;}}
#[path="original.rs"] mod original;
#[path="patched.rs"] mod patched;
fn same(a:&[f32],b:&[f32]) {
 for (i,(x,y)) in a.iter().zip(b).enumerate(){
  // Arbitrary pre-seeded -0 accumulators may retain their sign after a skipped add.
  // Numeric zero equality is sufficient here; full NSP outputs are checked bitwise below.
  assert!(x.to_bits()==y.to_bits() || (*x==0. && *y==0.) || (x.is_nan() && y.is_nan()),"index {i}: {x:?} != {y:?}");
 }
}
#[test] fn finite_gain_blocks_match_original(){
 for n in [1usize,7,32,480,512,960] {for channels in [1usize,2,3] {for silent in [false,true] {
  let pcm:Vec<f32>=(0..n*channels).map(|i|if silent {0.} else {match i%9{0=>0.,1=>-0.,2=>f32::from_bits(1),3=>-f32::MIN_POSITIVE,_=>((i%31) as f32-15.)*0.03125}}).collect();
  for count in [1usize,4,12,16] {for start in [0u64,3,17] {for duration in [0u64,1,8,480,1024] {for offset in [0u64,4,32,1200] {
   let gains:Vec<f32>=(0..count).map(|i|match i%6 {0=>0.,1=>-0.,2=>f32::from_bits(1),3=>-0.25,4=>1.,_=>0.125}).collect();
   let to:Vec<f32>=gains.iter().rev().copied().collect();
   for seed in [0f32,-0.,0.125,-0.125] {
    let mut a=vec![seed;n*count];let mut b=a.clone();
    let mut x=original::FixedGainsBlock::new(start,start+duration,&gains);
    let mut y=patched::FixedGainsBlock::new(start,start+duration,&gains);
    assert_eq!(x.process(offset,&pcm,n,channels,&mut a),y.process(offset,&pcm,n,channels,&mut b));same(&a,&b);
    for from in [None,Some(&gains[..])] {for to in [None,Some(&to[..])] {
     let mut a=vec![seed;n*count];let mut b=a.clone();
     let mut x=original::ProcessingBlockVariant::Interp(original::InterpGainsBlock::new(start,start+duration,from,to,count));
     let mut y=patched::ProcessingBlockVariant::Interp(patched::InterpGainsBlock::new(start,start+duration,from,to,count));
     assert_eq!(x.process(offset,&pcm,n,channels,&mut a),y.process(offset,&pcm,n,channels,&mut b));same(&a,&b);
    }}
   }
  }}}}
 }}}
}
#[test] fn invalid_output_size_still_errors(){
 let input=[1.;8];let mut a=[0.;1];let mut b=a;
 let mut x=original::FixedGainsBlock::new(0,8,&[0.,0.]);let mut y=patched::FixedGainsBlock::new(0,8,&[0.,0.]);
 assert_eq!(x.process(0,&input,8,1,&mut a),y.process(0,&input,8,1,&mut b));
 let mut x=original::ProcessingBlockVariant::Interp(original::InterpGainsBlock::new(0,8,Some(&[0.,0.]),Some(&[0.,0.]),2));
 let mut y=patched::ProcessingBlockVariant::Interp(patched::InterpGainsBlock::new(0,8,Some(&[0.,0.]),Some(&[0.,0.]),2));
 assert_eq!(x.process(0,&input,8,1,&mut a),y.process(0,&input,8,1,&mut b));
}
'''
if 'trait GainOutput' in patched:
    harness+='''
struct Split {data:Vec<Vec<f32>>,n:usize}
impl patched::GainOutput for Split {
 fn has_shape(&self,c:usize,n:usize)->bool{self.data.len()>=c && self.n==n}
 fn channel_slice(&mut self,c:usize,_n:usize)->&mut[f32]{&mut self.data[c][1..self.n+1]}
}
#[test] fn disjoint_planar_output_matches_flat(){
 for n in [1usize,7,32,480,960] {for channels in [1usize,2,3] {for count in [1usize,4,12] {
  let pcm:Vec<f32>=(0..n*channels).map(|i|((i%17) as f32-8.)*0.03125).collect();
  let g:Vec<f32>=(0..count).map(|i|if i%3==0{0.}else{0.125*(i as f32-2.)}).collect();
  let to:Vec<f32>=g.iter().rev().copied().collect();
  for offset in [0u64,3,19,1200] {for duration in [0u64,1,8,480,1024] {
   let mut flat=vec![0.;n*count];let mut split=Split{data:vec![vec![123.;n+2];count],n};
   for c in &mut split.data {c[1..n+1].fill(0.);}
   for (start,end,from,target) in [(0,duration,Some(&g[..]),Some(&to[..])),(3,3+duration,None,Some(&g[..]))] {
    let mut a=patched::ProcessingBlockVariant::Interp(patched::InterpGainsBlock::new(start,end,from,target,count));let mut b=a.clone();
    assert_eq!(a.process(offset,&pcm,n,channels,&mut flat),b.process_output(offset,&pcm,n,channels,&mut split));
   }
   let joined:Vec<f32>=split.data.iter().flat_map(|c|c[1..n+1].iter().copied()).collect();
   assert_eq!(flat.iter().map(|v|v.to_bits()).collect::<Vec<_>>(),joined.iter().map(|v|v.to_bits()).collect::<Vec<_>>());
   assert!(split.data.iter().all(|c|c[0]==123. && c[n+1]==123.));
  }}
 }}}
}
'''
(OUT/'blocks-test.rs').write_text(harness,encoding='utf-8')
subprocess.run([str(RUST),'--edition=2024','--test','-O',str(OUT/'blocks-test.rs'),'-o',str(OUT/'blocks-test.exe')],env=env,check=True)
subprocess.run([str(OUT/'blocks-test.exe')],env=env,check=True)
spec=importlib.util.spec_from_file_location('audit',ROOT/'tools/endpoint-probe/Analyze-RoarKernel.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
packets,source=audit.packets();search=os.add_dll_directory(str(LLVM))
paths=[args.reference,args.candidate]
libs=[]
for path in paths:
    lib=C.CDLL(str(path));lib.nsp_packet_create.argtypes=[C.c_uint32]*5+[C.c_double];lib.nsp_packet_create.restype=C.c_void_p
    lib.nsp_packet_render.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t,C.c_void_p,C.c_size_t]
    lib.nsp_packet_destroy.argtypes=[C.c_void_p];libs.append(lib)
checks=[]
for scaled in [0,1]:
    handles=[lib.nsp_packet_create(480,48000,0,0,scaled,1.) for lib in libs];assert all(handles)
    output=[(C.c_float*(480*12))() for _ in libs];digest=hashlib.sha256()
    try:
        for seq,packet in enumerate(packets):
            for lib,h,out in zip(libs,handles,output):assert lib.nsp_packet_render(h,seq,140,packet,len(packet),out,480*12)==0
            assert bytes(output[0])==bytes(output[1]),(scaled,seq)
            digest.update(bytes(output[1]))
    finally:
        for lib,h in zip(libs,handles):lib.nsp_packet_destroy(h)
    checks.append(dict(scaled=scaled,packets=len(packets),byteIdentical=True,outputSha256=digest.hexdigest()))
report=dict(passed=True,source=source,blockTests=f"{3 if 'trait GainOutput' in patched else 2} passed; finite PCM, signed zero, subnormal nonzero gains, overlap boundaries, dimensions, missing endpoints; disjoint planar output when supported",
            note='Block comparisons permit +/-0 equality for arbitrary pre-seeded accumulators; actual full NSP outputs must match bitwise. Nonfinite PCM is rejected by NSP before this kernel.',
            dlls={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},checks=checks)
(OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,indent=2))
