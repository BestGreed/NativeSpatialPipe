"""Compare actual old/new ObjectRenderer with queued metadata and 1/2-object focus."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'build/roar-direct-output-20261022/object-tests';OUT.mkdir(parents=True,exist_ok=True)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference-deps',type=Path,default=ROOT/'build/roar-direct-output-20261022/reference-target/release/deps')
parser.add_argument('--candidate-deps',type=Path,default=ROOT/'build/roar-native/release/deps')
args=parser.parse_args()
llvm=ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
env=dict(os.environ,PATH=str(llvm)+os.pathsep+os.environ['PATH'])
source=r'''
use aom_roar::*;
use std::io::Write;
fn main(){
 let mut file=std::fs::File::create(std::env::args().nth(1).unwrap()).unwrap();
 for n in [32usize,480,960] {for count in [1usize,2,3,128] {
  for separate in [false,true] {if separate && count!=2 {continue;}
   let rate=SampleRate::new(48000).unwrap();let size=Samples::new(n as u32).unwrap();
   let begin=std::time::Instant::now();let mut rdr=RoarRenderer::create(&Config::new(Layout::Layout714,size,rate).unwrap()).unwrap();
   let group=rdr.add_audio_group().unwrap();
   let cfg=|objects|AudioElementConfig::ObjectBased(ObjectBasedConfig{num_objects:objects,rendering_config:None});
   rdr.add_element(group,1,&cfg(if separate{1}else{count as u32})).unwrap();
   if separate {rdr.add_element(group,2,&cfg(1)).unwrap();}
   let create_ns=begin.elapsed().as_nanos();
   let mut render_ns=0;
   for block in 0..12 {
    // Multiple metadata blocks inside one render exercise isolated object sums.
    for part in 0..3 {
     let duration=if part<2 {n/4}else{n-2*(n/4)};
     let positions:Vec<_>=(0..count).map(|i|CartesianCoordinate{x:((block*7+i*13+part*11)%30) as f32*0.05-0.75,y:0.25+part as f32*0.25,z:(i%4) as f32*0.15-0.15}).collect();
     if separate {
      rdr.update_element_positions(1,&ObjectPosition::Cartesian(vec![positions[0]]),Samples::new(duration as u32).unwrap()).unwrap();
      rdr.update_element_positions(2,&ObjectPosition::Cartesian(vec![positions[1]]),Samples::new(duration as u32).unwrap()).unwrap();
     }else{rdr.update_element_positions(1,&ObjectPosition::Cartesian(positions),Samples::new(duration as u32).unwrap()).unwrap();}
    }
    let pcm:Vec<Vec<f32>>=(0..count).map(|c|(0..n).map(|j|if (3..6).contains(&block){0.}else{match (j+c)%11 {0=>-0.,1=>f32::from_bits(1),_=>(((j*3+c*7+block)%31) as f32-15.)*0.03125}}).collect()).collect();
    let input:Vec<&[f32]>=pcm.iter().map(Vec::as_slice).collect();
    // Independent output allocations prove the planar path does not assume contiguity.
    let mut output=vec![vec![123.0f32;n];12];let mut slices:Vec<_>=output.iter_mut().map(Vec::as_mut_slice).collect();
    let inputs=if separate {vec![(1,PlanarBufferRef::new(&input[..1],1,size).unwrap()),(2,PlanarBufferRef::new(&input[1..],1,size).unwrap())]}else{vec![(1,PlanarBufferRef::new(&input,count,size).unwrap())]};let mut out=PlanarBufferMut::new(&mut slices,12,size).unwrap();
    let begin=std::time::Instant::now();rdr.render(&inputs,&mut out).unwrap();render_ns+=begin.elapsed().as_nanos();
    for c in &output {for value in c {file.write_all(&(*value).to_le_bytes()).unwrap();}}
   }
   println!("{{\"frames\":{n},\"objects\":{count},\"separateElements\":{separate},\"createNs\":{create_ns},\"renderNs\":{render_ns}}}");
  }
 }}
}
'''
(OUT/'object-probe.rs').write_text(source,encoding='utf-8')
rust=ROOT/'work/rust/rustup/toolchains/1.97.1-x86_64-pc-windows-gnullvm/bin/rustc.exe'
records={};hashes={};library_hashes={}
for name,target in [('reference',args.reference_deps),('candidate',args.candidate_deps)]:
    lib=max(target.glob('libaom_roar-*.rlib'),key=lambda p:p.stat().st_mtime_ns)
    library_hashes[name]=hashlib.sha256(lib.read_bytes()).hexdigest()
    exe=OUT/(name+'.exe');pcm=OUT/(name+'.f32')
    subprocess.run([str(rust),'--edition=2024','-O',str(OUT/'object-probe.rs'),'--extern','aom_roar='+str(lib),'-L','dependency='+str(target),'-o',str(exe)],env=env,cwd=ROOT,check=True)
    p=subprocess.run([str(exe),str(pcm)],env=env,cwd=ROOT,capture_output=True,text=True,check=True)
    records[name]=[json.loads(line) for line in p.stdout.splitlines()]
    assert len(records[name])==15
    hashes[name]=hashlib.sha256(pcm.read_bytes()).hexdigest()
assert hashes['reference']==hashes['candidate']
result=dict(passed=True,librarySha256=library_hashes,harnessSha256=hashlib.sha256(source.encode('utf-8')).hexdigest(),cases=15,blocksPerCase=12,pcmSha256=hashes,records=records,
            scope='Actual old/new public ROAR pipeline including ObjectRenderer, 1/2 focus plus 3/128 fallback; one element and two separate elements, disjoint outputs, 3 queued metadata intervals per block, silence and recovery. Unpressured diagnostic timings, not a performance benchmark.')
(OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print('15 actual ROAR object pipeline cases /180 blocks passed; one/two objects, queue overlap, disjoint output, larger fallback all bit-identical')
