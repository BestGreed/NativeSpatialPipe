import assert from 'node:assert/strict';
import {ToiletFollow,toiletBoundsCenter,toiletTransform} from './toilet-follow.js';
import {toiletLines} from './assets/toilet-wire.js';

const center=toiletBoundsCenter(toiletLines),still=new ToiletFollow();
assert(center.every(Number.isFinite),'model bounds have a valid center');
const atRest=toiletTransform(center,still,.2);
assert.deepEqual(atRest(center),[center[0],center[1]+.2,center[2]],'height does not move the pivot sideways');
assert(Math.hypot(...atRest(toiletLines[0][0]).map((v,i)=>v-toiletLines[0][0][i]-(i===1?.2:0)))<1e-9,'rest pose retains the original shape');

const right=new ToiletFollow(),left=new ToiletFollow();
for(let i=0;i<12;i++){right.step([1,1,-1],1.5,.05);left.step([-1,0,1],-1.5,.05);}
assert(right.roll<0&&left.roll>0,'right and left travel lean in opposite directions');
assert(right.pitch>0&&left.pitch<0,'up/forward and backward travel lean differently');
assert(right.yaw>0&&left.yaw<0,'opposite turns rotate in opposite directions');
for(const pose of [right,left]){
  assert(Math.abs(pose.roll)<=4*Math.PI/180&&Math.abs(pose.pitch)<=3.5*Math.PI/180&&Math.abs(pose.yaw)<=3*Math.PI/180,'all follow angles stay small');
  const moved=toiletTransform(center,pose,.2)(center);
  assert(Math.hypot(moved[0]-center[0],moved[1]-center[1]-.2,moved[2]-center[2])<1e-9,'motion rotates about the model bounds center');
}
for(let i=0;i<25;i++)right.step([100,100,100],100,.05,{enabled:false});
assert(Math.hypot(right.roll,right.pitch,right.yaw)<.002,'untrusted or manual state returns to neutral');
console.log('toilet follow tests passed');
