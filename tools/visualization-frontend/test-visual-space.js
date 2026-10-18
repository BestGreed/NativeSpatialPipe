import assert from 'node:assert/strict';
import {mapPosition,referenceScale,cloudDepthStyle,burstOffset} from './visual-space.js';
const length=p=>Math.hypot(...p);
const near=mapPosition([1,2,-3],4),far=mapPosition([4,8,-12],4);
assert(length(far)>length(near));
for(let i=0;i<3;i++)assert(Math.abs(near[i]/length(near)-far[i]/length(far))<1e-12);
assert.deepEqual(mapPosition([0,0,0],4),[0,0,0]);
assert.equal(mapPosition(null,4),null);
assert.equal(referenceScale([]),null);
const objects=[1,2,8].map(r=>({kind:'dynamic',spatial:{positionValid:true,position:[0,0,r]}}));
assert.equal(referenceScale(objects),2);
const innerCloud=cloudDepthStyle(.1),outerCloud=cloudDepthStyle(.85);
assert(innerCloud.opacity<outerCloud.opacity*.4&&innerCloud.size<outerCloud.size&&innerCloud.spread<outerCloud.spread,'near-centre clouds should be smaller and dimmer');
assert.deepEqual(outerCloud,{opacity:1,size:1,spread:1},'outer clouds retain their original profile');
for(let i=0;i<18;i++){
  assert.equal(length(burstOffset(i,0)),0);
  assert(length(burstOffset(i,.5))>length(burstOffset(i,.1)));
  assert(length(burstOffset(i,1))<.36);
}
console.log('PASS direction-preserving non-shell mapping, origin/unknown, fixed scale selection and local expanding burst');
