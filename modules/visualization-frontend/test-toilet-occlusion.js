import assert from 'node:assert/strict';
import {prepareToiletOccluders,coveredToiletLine} from './toilet-occlusion.js';
import {projectViewPoint} from './view-camera.js';
import {toiletLines,toiletFaces} from './assets/toilet-wire.js';
import {ToiletFollow,toiletBoundsCenter,toiletTransform} from './toilet-follow.js';

const view={yaw:0,tilt:0,zoom:1},project=p=>projectViewPoint(p,view,1);
const face=[[-.5,-.5,.3],[.5,-.5,.3],[0,.5,.3]];
const front=prepareToiletOccluders([face],p=>p,project);
assert(coveredToiletLine([-.1,0,0],[.1,0,0],front,project),'near triangle darkens an edge behind it');
assert(!coveredToiletLine([-.1,0,.5],[.1,0,.5],front,project),'foreground edge stays bright');
const pose=toiletTransform(toiletBoundsCenter(toiletLines),new ToiletFollow(),.2);
for(const yaw of [0,Math.PI,-Math.PI/2]){
  view.yaw=yaw;
  const occluders=prepareToiletOccluders(toiletFaces,pose,project);
  const hidden=toiletLines.filter(([a,b])=>coveredToiletLine(pose(a),pose(b),occluders,project)).length;
  assert(hidden>10&&hidden<toiletLines.length-10,'the real mesh retains both bright and dark edges');
}
console.log('toilet occlusion tests passed');
