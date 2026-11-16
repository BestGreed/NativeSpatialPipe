import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {Replay} from './replay.js';
import {createValidator} from './data.js';

const replayRoot=new URL('../../build/wwise-visualization-replay/',import.meta.url);
const read=file=>JSON.parse(readFileSync(new URL(file,replayRoot),'utf8'));
const manifest=read('manifest.json');
const schema=JSON.parse(readFileSync(new URL('../../contracts/visualization/v0/schema.json',import.meta.url),'utf8'));
const validate=createValidator(schema);
let now=0,frames=0,last=-1,events=0,status='';
const player=new Replay(manifest,async file=>read(file),(m,seek)=>{
  validate(m);if(!seek)assert.equal(m.telemetrySeq,last+1);last=m.telemetrySeq;frames++;events+=m.events.length;
},s=>status=s,()=>now);
await player.seek(0,true);
await player.tick(-50);assert.equal(player.position,0);
for(now=100;now<=60000;now+=100){await player.tick(now);assert(player.cache.size<=3);}
assert.equal(frames,600);assert.equal(last,599);assert.equal(status,'ended');assert.equal(player.playing,false);
assert.equal(events,manifest.statistics.onsetCandidates);assert.equal(manifest.statistics.eventOverflow,0);
const fullMinuteEvents=events;
await player.seek(20,false);assert.equal(last,199);assert.equal(player.playing,false);
const count=frames;await player.tick(now+5000);assert.equal(frames,count);
player.play();now+=100;await player.tick(now);assert.equal(last,200);
player.pause();const held=last;now+=5000;await player.tick(now);assert.equal(last,held);
await player.seek(0,true);assert.equal(last,0);player.dispose();assert.equal(player.cache.size,0);
assert.throws(()=>new Replay({...manifest,frameCount:0},async()=>[],()=>{}));
// A slow seek must not overwrite a later seek or emit frames after disposal.
let unblock;const delayed=new Promise(resolve=>unblock=resolve);let final=-1;
const slow=new Replay(manifest,async file=>{if(file==='chunk-000.json')await delayed;return read(file);},m=>final=m.telemetrySeq,()=>{},()=>0);
const earlier=slow.seek(0,false);await slow.seek(30,false);unblock();await earlier;assert.equal(final,299);
slow.dispose();
console.log(`PASS complete 60s replay: 600 ordered frames, ${fullMinuteEvents} events; bounded cache, pause, seek, restart, stale-load cancellation`);
