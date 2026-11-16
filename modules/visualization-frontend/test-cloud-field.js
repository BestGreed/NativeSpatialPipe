import assert from 'node:assert/strict';
import {updateCloudEnvelope} from './cloud-field.js';

const run=(db,frames=14)=>{
  let previous=null,cloud;
  const states=[];
  for(let i=0;i<frames;i++){
    cloud=updateCloudEnvelope(previous,db,100,i>0,(i+1)*100,false);
    states.push(cloud);
    previous={cloud};
  }
  return states;
};
const strong=run(-18),medium=run(-32);
assert(strong.slice(0,3).every(x=>!x.visible&&x.mix===0),'a short sound has no cloud');
assert(medium.slice(0,3).every(x=>!x.visible&&x.mix===0),'medium sound also waits for continuity');
assert(strong[3].mix>medium[3].mix&&medium[3].mix>.35,'early strength controls starting brightness');
assert(strong[5].mix-strong[3].mix>medium[5].mix-medium[3].mix,'strong early sound brightens faster');
assert(medium[13].mix>medium[5].mix,'medium continuous sound brightens gradually');
assert(medium[13].mix<1,'medium cloud does not snap to full entrance brightness');
assert.equal(updateCloudEnvelope({cloud:strong[13]},-18,100,true,1500,true).mix,0,'a transient halo interrupts the cloud');
assert.equal(updateCloudEnvelope({cloud:strong[13]},null,100,true,0,false).visible,false,'silence removes the cloud');
assert.equal(updateCloudEnvelope({cloud:strong[13]},-18,100,false,100,false).visible,false,'identity or stream discontinuity resets qualification');
console.log('cloud envelope tests passed');
