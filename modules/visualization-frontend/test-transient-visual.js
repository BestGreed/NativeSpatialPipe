import assert from 'node:assert/strict';
import {meterOnset,selectVisibleOnsets} from './transient-visual.js';

const object=(db,peak=.08,kind='dynamic')=>({kind,objectKey:{generation:1,slot:12},
  spatial:{positionValid:true,position:[1,0,-2]},meter:{rmsDbfs:db,samplePeak:peak}});
const base=object(-57);
const burst=object(-34,.17);
const inferred=meterOnset(base,burst,100000,-Infinity,1000000);
assert.equal(inferred?.visualInference,'meter-rise');
assert.equal(inferred?.expiresTimeUs,1650000);
assert.equal(meterOnset(base,burst,100000,800000,1000000),null,'cooldown prevents repeated halos');
assert.equal(meterOnset(base,burst,300000,-Infinity,1000000),null,'a data gap is not a measured onset');
assert.equal(meterOnset(base,object(-46,.17),100000,-Infinity,1000000),null,'weak windows stay below threshold');
assert.equal(meterOnset(base,object(-34,.03),100000,-Infinity,1000000),null,'a low peak stays below threshold');
assert.equal(meterOnset(object(-39),burst,100000,-Infinity,1000000),null,'a loud steady source is not a new onset');
assert.equal(meterOnset(null,burst,100000,-Infinity,1000000),null,'the first observation has no rise reference');
assert.equal(meterOnset(base,object(-34,.17,'static'),100000,-Infinity,1000000),null,'the static bed is excluded');
const onset=(id,strength,position=[1,0,0])=>({eventId:id,kind:'onset',strength,
  spatial:{positionValid:true,position},sourceTimeUs:0,expiresTimeUs:650000});
const outer=Array.from({length:7},(_,i)=>onset(`outer-${i}`,1-i*.1));
const center=onset('center',.12,[0,0,0]);
const withReserve=selectVisibleOnsets([...outer,center],100000,1);
assert.equal(withReserve.length,7,'a center candidate receives one extra slot');
assert.equal(withReserve.at(-1),center,'the exact listener origin is eligible');
assert.equal(selectVisibleOnsets([onset('strong-center',1.1,[0,0,0]),...outer],100000,1).length,6,'an already-selected center does not get duplicated');
assert.equal(selectVisibleOnsets(outer,100000,1).length,6,'no center keeps the six-event limit');
assert.equal(selectVisibleOnsets([...outer,center],700000,1).length,0,'expired candidates are not retained');
console.log('PASS measured onset and one bounded listener-center reserve');
