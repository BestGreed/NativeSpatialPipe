import {readFileSync,readdirSync} from 'node:fs';
import assert from 'node:assert/strict';
import {createValidator} from './data.js';
const root=new URL('../../contracts/visualization/v0/',import.meta.url);
const read=name=>JSON.parse(readFileSync(new URL(name,root),'utf8'));
const validate=createValidator(read('schema.json'));
let positives=0,negatives=0;
for(const name of readdirSync(new URL('fixtures/',root))){validate(read('fixtures/'+name));positives++;}
function reject(mutator){const m=read('fixtures/live-six-axis.json');mutator(m);assert.throws(()=>validate(m));negatives++;}
reject(m=>m.pcm=[0]);reject(m=>m.telemetrySeq=2**53);reject(m=>m.coverage['render-ready'].ratio=.5);
reject(m=>m.taps['render-ready'].channels[0].meter.rmsDbfs=0);
reject(m=>m.taps['render-ready'].channels[0].meter.sumSquares=Infinity);
reject(m=>m.taps['decoded-input'].objects[0].objectKey.streamEpoch=2);
reject(m=>m.taps['decoded-input'].objects[0].spatial.rendererPosition=[0,0,0]);
reject(m=>m.taps['render-ready'].channels[1].channelId='FL');
reject(m=>{m.taps['decoded-input'].quality='gap';m.taps['decoded-input'].snapshotComplete=true;});
const reordered=read('fixtures/gap.json');reordered.coverage['render-ready']={ratio:.8,validSamples:1920,expectedSamples:2400};validate(reordered);
const nativeCaps=read('fixtures/capabilities.json');nativeCaps.schemaVersion='0.2.0';nativeCaps.routes[0].authority='native';nativeCaps.routes[0].decodedInput=false;nativeCaps.routes[0].soundNavigation=false;validate(nativeCaps);
nativeCaps.schemaVersion='0.1.0';assert.throws(()=>validate(nativeCaps));
console.log(`PASS ${positives} contract fixtures; ${negatives} invalid messages rejected; field-order independence`);
