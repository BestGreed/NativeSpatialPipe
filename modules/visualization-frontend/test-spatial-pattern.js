import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {SpatialPattern} from './spatial-pattern.js';

const object=(slot,position,db)=>({kind:'dynamic',objectKey:{sessionId:'test',streamEpoch:0,slot,generation:1},sourceIdent:slot,spatial:{position,positionValid:true},meter:{rmsDbfs:db}});
const background=Array.from({length:12},(_,i)=>object(i+20,[18*Math.cos(i),1,18*Math.sin(i)],-100));
const frame=(seq,db=[-36,-45,-21],middle=[0,0,-6])=>({routeId:'test',sessionId:'test',streamEpoch:0,meterRevision:'a',sourceTimeUs:(seq+1)*100000,sourceSequenceRange:{start:seq*10,endExclusive:(seq+1)*10},taps:{'decoded-input':{quality:'live',snapshotComplete:true,objects:[...background,object(1,[0,-1,-12],db[0]),object(2,middle,db[1]),object(3,[0,0,.5],db[2])]}}});

let detector=new SpatialPattern();
assert.equal(detector.ingest(frame(0)).valid,false);
assert.equal(detector.ingest(frame(1)).valid,false);
let shape=detector.ingest(frame(2));
assert.equal(shape.valid,true);
assert.equal(shape.kind,'depth-chain');
assert.equal(shape.anchors.length,3);
assert.equal(shape.gradientValid,false,'强弱层差不是自动的单向衰减');
assert.equal(detector.ingest(frame(3,[-30,-40,-50])).gradientValid,false,'单帧梯度不足以标注传播');
detector.reset();
for(let i=0;i<3;i++)shape=detector.ingest(frame(i,[-30,-40,-50]));
assert.equal(shape.gradientValid,true,'连续单调的测得强度可单独标注');
assert.equal(detector.ingest(frame(3,[-30,-40,-50],[4,0,-6])).valid,false,'不共线时不得延续提示');
detector.reset();
assert.equal(detector.ingest(frame(8)).valid,false,'跳转和来源复位不得带入旧点列');

const replayRoot=new URL('../../build/wwise-visualization-replay/',import.meta.url),manifest=JSON.parse(readFileSync(new URL('manifest.json',replayRoot),'utf8'));
detector=new SpatialPattern();const active=[];for(const chunk of manifest.chunks){const frames=JSON.parse(readFileSync(new URL(chunk.file,replayRoot),'utf8'));for(const snapshot of frames){const candidate=detector.ingest(snapshot);if(candidate.valid)active.push({time:snapshot.sourceTimeUs/1e6,candidate});}}
const last=active.at(-1);
assert.equal(last.time,60);
assert.deepEqual(last.candidate.anchors.map(a=>Number(a.key.split(':').at(-2))),[31,88,78]);
assert.equal(last.candidate.gradientValid,false,'尾帧不是持续单向强度衰减');
assert(active.length>10&&active.length<150,'形态提示应是选择性事件，不可覆盖整段');
console.log(JSON.stringify({passed:true,activeFrames:active.length,lastSlots:last.candidate.anchors.map(a=>Number(a.key.split(':').at(-2))),lastDb:last.candidate.anchors.map(a=>+a.db.toFixed(1)),tailGradient:last.candidate.gradientValid}));
