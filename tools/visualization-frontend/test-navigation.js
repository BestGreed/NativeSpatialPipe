import assert from 'node:assert/strict';
import {Navigation,VisualHeading,rad,rotation,apply,norm} from './navigation.js';
import {scenarioObjects,metricScenario} from './navigation-scenarios.js';
export function snapshot(name,time,seq){return {routeId:'test',sessionId:'test',streamEpoch:0,meterRevision:'test',sourceTimeUs:Math.round(time*1e6),sourceSequenceRange:{start:seq*10,endExclusive:(seq+1)*10},taps:{'decoded-input':{quality:'live',snapshotComplete:true,objects:scenarioObjects(name,time).map(o=>({objectKey:{sessionId:'test',streamEpoch:0,slot:o.slot,generation:o.generation},kind:'dynamic',spatial:{positionValid:true,position:o.p},meter:{meanSquare:o.peak**2}}))}}};}
const results=[];
function run(name,seconds=3){const nav=new Navigation(),view=new VisualHeading();let valid=0,max=0;for(let i=0;i<=seconds*10;i++){nav.ingest(snapshot(name,i/10,i),{stableScale:metricScenario(name)});for(let k=0;k<2;k++)view.step(nav,.05);valid+=nav.result.valid?1:0;max=Math.max(max,Math.abs(view.yaw),Math.abs(view.pitch));}results.push({name,valid,maxDegrees:max/rad,relocations:view.relocations});return {nav,view};}
const still=run('still');assert(still.nav.result.valid);assert(norm(still.nav.result.sceneDelta)<1e-8);assert(Math.abs(still.view.yaw)<1e-8);
const turn=run('turn');assert(turn.nav.result.valid);assert(Math.abs(turn.nav.yaw+.35*Math.sin(3*.55))<.045);assert(Math.abs(turn.nav.pitch-.12*Math.sin(3*.4))<.02);
for(const [name,axis,sign] of [['forward',2,1],['back',2,-1],['left',0,1],['right',0,-1],['diagonal',2,1]]){const {nav}=run(name);assert(nav.result.valid);assert(nav.result.translation[axis]*sign>.1);assert(Math.abs(nav.yaw)<.001);}
const mixed=run('mixed');assert(mixed.nav.result.valid);assert(Math.abs(mixed.nav.yaw+.35*Math.sin(3*.55))<.045);assert(mixed.nav.result.confidence>.7);
const large=run('large',15);assert(large.view.relocations===1);assert(Math.abs(large.nav.yaw)>170*rad);assert(Math.abs(large.view.yaw)<=40*rad);
assert(!run('sparse').nav.result.valid);assert(!run('reuse').nav.result.valid);
const nav=new Navigation();for(let i=0;i<10;i++)nav.ingest(snapshot('turn',i/10,i));let m=snapshot('turn',1,10);m.taps['decoded-input'].quality='gap';nav.ingest(m);assert(!nav.result.valid);nav.ingest(snapshot('turn',1.1,11));assert(!nav.result.valid);
nav.ingest(snapshot('turn',3,30));assert(!nav.result.valid);nav.ingest(snapshot('turn',2,20));assert(!nav.result.valid);
const raw=run('forward').nav;raw.reset();for(let i=0;i<10;i++)raw.ingest(snapshot('forward',i/10,i));assert.equal(raw.result.translation,null);
const degenerate=new Navigation();for(let i=0;i<5;i++){m=snapshot('still',i/10,i);for(const o of m.taps['decoded-input'].objects)o.spatial.position=[0,0,-2];degenerate.ingest(m);}assert(!degenerate.result.valid);
const noise=new Navigation();let falseMotion=0;for(let i=0;i<100;i++){m=snapshot('still',i/10,i);for(const o of m.taps['decoded-input'].objects)o.spatial.position=o.spatial.position.map((x,j)=>x+.0001*Math.sin(i*1.9+o.objectKey.slot+j));noise.ingest(m);if(norm(noise.result.sceneDelta)>rad)falseMotion++;}assert.equal(falseMotion,0);
const view=new VisualHeading();view.step(large.nav,.05,{manual:true});assert.equal(view.relocations,0);for(let i=0;i<100;i++)view.step(large.nav,.05,{fresh:false});assert(Math.abs(view.yaw)<.001);
const independent=new Navigation();for(let i=0;i<20;i++){m=snapshot('still',i/10,i);for(const o of m.taps['decoded-input'].objects)o.spatial.position=apply(rotation(Math.sin(i*.6+o.objectKey.slot)*.5),o.spatial.position);independent.ingest(m);}assert(!independent.result.valid);
// A single 90-degree discontinuity is not integrated as plausible motion.
const jump=new Navigation();for(let i=0;i<5;i++)jump.ingest(snapshot('still',i/10,i));m=snapshot('still',.5,5);for(const o of m.taps['decoded-input'].objects)o.spatial.position=apply(rotation(Math.PI/2),o.spatial.position);jump.ingest(m);assert(!jump.result.valid);
const before=large.nav.yaw,stableView=new VisualHeading();for(let i=0;i<100;i++){large.nav.yaw=(126+(i%2?.2:-.2))*rad;stableView.step(large.nav,.05);}assert.equal(stableView.relocations,1);large.nav.yaw=before;
const epoch=new Navigation();for(let i=0;i<5;i++)epoch.ingest(snapshot('turn',i/10,i));m=snapshot('turn',.5,5);m.streamEpoch=1;epoch.ingest(m);assert(!epoch.result.valid);assert.equal(epoch.yaw,0);
const clones=new Navigation();for(let i=0;i<6;i++){m=snapshot('still',i/10,i);for(const o of m.taps['decoded-input'].objects)o.spatial.position=m.taps['decoded-input'].objects[0].spatial.position;clones.ingest(m);}assert(!clones.result.valid);
const reordered=new Navigation();for(let i=0;i<8;i++){m=snapshot('turn',i/10,i);if(i%2)for(const o of m.taps['decoded-input'].objects)o.objectKey=Object.fromEntries(Object.entries(o.objectKey).reverse());reordered.ingest(m);}assert(reordered.result.valid);
const p=[.2,.3,-.8];
// Exact inverse is carried as a matrix, not a negated Euler triplet.
const inverse=reordered.result.observerDeltaRotation,forward=inverse.map((_,i)=>inverse[(i%3)*3+Math.floor(i/3)]);
assert(norm(apply(inverse,apply(forward,p)).map((x,i)=>x-p[i]))<1e-8);
console.log(JSON.stringify({passed:true,scenarios:results,staticFalseMotion: falseMotion},null,2));
