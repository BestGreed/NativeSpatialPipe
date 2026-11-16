import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Navigation,rad} from './navigation.js';
import {NavigationForecast} from './navigation-forecast.js';

const replayRoot=new URL('../../build/wwise-visualization-replay/',import.meta.url);
const read=name=>JSON.parse(readFileSync(new URL(name,replayRoot),'utf8'));
const manifest=read('manifest.json');
assert.equal(manifest.frameCount,600);
const navigation=new Navigation(),forecast=new NavigationForecast(),samples=[];
for(const chunk of manifest.chunks)for(const frame of read(chunk.file)){
  const result=navigation.ingest(frame,{sourceSpace:true});
  const time=frame.sourceTimeUs/1000;forecast.observe(navigation,time);const projected=forecast.step(navigation,time+50).result.prediction;
  samples.push({frame:frame.telemetrySeq,valid:result.valid,yaw:result.sceneDelta[0]/rad,flow:result.translation,unit:result.translationUnit,confidence:result.confidence,cooperative:result.cooperativeFlow.valid,observer:result.cooperativeFlow.observer,groups:result.groupMotion.groups,predicted:projected.active});
}
assert.equal(samples.length,600);
const count=(start,end)=>samples.slice(start,end).filter(s=>s.valid).length;
assert(count(50,100)>=30,'5–10 秒应保留连续成组运动');
assert(count(450,550)>=75,'45–55 秒应保留连续成组运动');
const earlyForward=samples.slice(20,31).filter(s=>s.cooperative&&s.observer[2]<-.1).length;
assert(earlyForward>=6,'开头 2–3 秒的多源后移应产生前进候选');
assert(samples.filter(s=>s.groups>=3).length>=150,'不同方位与距离层应有成组刚体互证');
assert(samples.some(s=>s.valid&&s.yaw>10)&&samples.some(s=>s.valid&&s.yaw< -10),'应恢复双向转向片段');
assert(samples.filter(s=>s.flow&&s.flow[2]>.1).length>30,'应恢复向后共同流');
assert(samples.filter(s=>s.flow&&s.flow[2]<-.1).length>30,'应恢复向前共同流');
assert(samples.filter(s=>s.flow&&Math.abs(s.flow[0])>.1).length>100,'应恢复横向共同流');
assert(samples.every(s=>!s.flow||s.unit==='source-unverified/s'),'源坐标不能标为米制');
const missing=samples.filter(s=>!s.valid&&!s.cooperative),bridged=missing.filter(s=>s.predicted).length;
assert(bridged>=15,'短时缺口应由有限时长前推衔接');
console.log(JSON.stringify({passed:true,valid:samples.filter(s=>s.valid).length,missing:missing.length,bridged,turn5to10:count(50,100),motion45to55:count(450,550),earlyForward,groupVerified:samples.filter(s=>s.groups>=3).length,front:samples.filter(s=>s.flow&&s.flow[2]<-.1).length,back:samples.filter(s=>s.flow&&s.flow[2]>.1).length}));
