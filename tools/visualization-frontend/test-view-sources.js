import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {scenarioObjects} from './navigation-scenarios.js';
import {mapPosition} from './visual-space.js';
import {namedView,projectViewPoint} from './view-camera.js';
const read=name=>JSON.parse(readFileSync(new URL('../../contracts/visualization/v0/fixtures/'+name,import.meta.url),'utf8'));
const layout=read('layout.json');
const front=layout.channels.find(c=>c.channelId==='C').direction;
assert(front[2]<0,'simulation layout forward is -Z');
assert(layout.channels.find(c=>c.channelId==='FL').direction[0]<0);
assert(layout.channels.find(c=>c.channelId==='FR').direction[0]>0);
const saved={yaw:-.72,tilt:.42,zoom:1,toiletHeight:.2};
const axes=[[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,-1],[0,0,1]];
for(const name of ['front','top','left','oblique']){
  const view=namedView(name,saved,[],1.5);
  for(const point of axes){
    const mapped=mapPosition(point,1),a=projectViewPoint(point,view,1.5),b=projectViewPoint(mapped,view,1.5);
    assert(Math.abs(a[0]*b[1]-a[1]*b[0])<1e-8,'radial mapping preserves screen bearing');
    assert(a[2]*b[2]>=-1e-10,'radial mapping preserves near/far side');
  }
}
for(const [name,axis,sign] of [['forward',2,1],['back',2,-1],['left',0,1],['right',0,-1]]){
  const a=scenarioObjects(name,0),b=scenarioObjects(name,1);
  assert(b.every((p,i)=>(p.p[axis]-a[i].p[axis])*sign>0),'simulated fixed scene moves opposite observer travel');
}
console.log('shared camera, simulation coordinate and relative travel tests passed');
