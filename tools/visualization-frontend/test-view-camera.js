import assert from 'node:assert/strict';
import {projectViewPoint,namedView,dragView} from './view-camera.js';

const start={yaw:.72,tilt:.38,zoom:1.1,toiletHeight:.27};
assert(dragView(start,50,0).yaw<start.yaw,'right drag reverses yaw');
assert(dragView(start,0,-50).tilt>start.tilt,'up drag reverses pitch');
assert.deepEqual(namedView('oblique',start,[],1.5),start,'saved diagonal view is the reset preset');
assert.equal(dragView({yaw:0,tilt:Math.PI/2},10,0).tilt,Math.PI/2,'horizontal drag from top does not change elevation');
const points=[[-1.5,-.7,-1.4],[1.4,.6,-1.1],[-1.3,.8,1.5],[1.4,-.5,1.2]];
for(const tilt of [-1.5,-.36,0,.36]){
  const saved={...start,tilt};
  assert.deepEqual(namedView('oblique',saved,points,1.5),saved,'saved orbit restored exactly, including elevation');
}
for(const [name,yaw,tilt] of [['front',0,0],['top',0,Math.PI/2],['left',Math.PI/2,0]]){
  const view=namedView(name,start,points,1.5);
  assert(Math.abs(view.yaw-yaw)<1e-9&&Math.abs(view.tilt-tilt)<1e-9,`${name} orientation`);
  assert.equal(view.toiletHeight,start.toiletHeight,'switching views keeps the prop height');
  for(const p of points){const q=projectViewPoint(p,view,1.5);
    assert(Math.abs(q[0])<=.861&&Math.abs(q[1])<=.761,`${name} fits all current objects`);}
}
const top=namedView('top',start,points,1.5);
const front=namedView('front',start,points,1.5),left=namedView('left',start,points,1.5);
assert(projectViewPoint([0,1,0],top,1.5)[2]<projectViewPoint([0,-1,0],top,1.5)[2],'top camera sees upper surfaces nearer, not underside');
assert(projectViewPoint([0,0,1],front,1.5)[2]<projectViewPoint([0,0,-1],front,1.5)[2],'rear camera sees model back nearer than model front');
// For every orbit, the camera's own right/up/away axes must form one
// consistent physical projection, including off-axis perspective and depth.
for(const yaw of [0,.28,-.28,Math.PI/2,Math.PI])for(const tilt of [0,.36,Math.PI/2]){
  const view={yaw,tilt,zoom:1},c=Math.cos(yaw),s=Math.sin(yaw),ct=Math.cos(tilt),st=Math.sin(tilt);
  const right=[c,0,s],up=[s*st,ct,-c*st],camera=[-s*ct,st,c*ct];
  const qr=projectViewPoint(right,view,1),qu=projectViewPoint(up,view,1),near=projectViewPoint(camera,view,1),far=projectViewPoint(camera.map(x=>-x),view,1);
  assert(qr[0]>0&&Math.abs(qr[1])<1e-8,'camera-right projects right');
  assert(qu[1]>0&&Math.abs(qu[0])<1e-8,'camera-up projects up');
  assert(near[2]<far[2],'point toward camera is nearer');
}
for(const radius of [.01,.2,1,2]){
  for(const z of [-radius,0,radius]){
    assert(projectViewPoint([radius,0,z],front,1.5)[0]>0,'front: listener-right stays right at all depths');
    assert(projectViewPoint([-radius,0,z],front,1.5)[0]<0,'front: listener-left stays left at all depths');
  }
  assert(projectViewPoint([0,radius,0],front,1.5)[1]>0,'front: up stays up');
  assert(projectViewPoint([0,0,-radius],left,1.5)[0]<0,'left side: forward is screen-left');
  assert(projectViewPoint([0,0,radius],left,1.5)[0]>0,'left side: back is screen-right');
  assert(projectViewPoint([0,radius,0],left,1.5)[1]>0,'left side: up stays up');
  assert(projectViewPoint([-radius,0,0],left,1.5)[2]<projectViewPoint([radius,0,0],left,1.5)[2],'left side: listener-left is nearer camera');
  assert(projectViewPoint([radius,0,0],top,1.5)[0]>0,'right stays right at every radius');
  assert(projectViewPoint([-radius,0,0],top,1.5)[0]<0,'left stays left at every radius');
  assert(projectViewPoint([0,0,-radius],top,1.5)[1]>0,'forward appears above listener');
  assert(projectViewPoint([0,0,radius],top,1.5)[1]<0,'back appears below listener');
}
const diagonal=namedView('oblique',{yaw:-.28,tilt:.36,zoom:1},points,1.5);
for(const yaw of [Math.PI+.28,-Math.PI+.28,Math.PI*3+.28]){
  const restored=namedView('oblique',{yaw,tilt:.36,zoom:1,toiletHeight:.2},points,1.5);
  assert.equal(restored.yaw,yaw,'saved camera must never be flipped automatically');
  assert.equal(restored.zoom,1);assert.equal(restored.toiletHeight,.2);
}
for(const radius of [.01,.2,1,2]){
  assert(projectViewPoint([radius,0,0],diagonal,1.5)[0]>0,'default diagonal: right axis points right');
  assert(projectViewPoint([0,0,-radius],diagonal,1.5)[0]>0,'default diagonal: model forward points screen-right');
  assert(projectViewPoint([-radius,0,0],diagonal,1.5)[0]<0,'default diagonal: left axis points left');
  assert(projectViewPoint([0,radius,0],diagonal,1.5)[1]>0,'default diagonal: up axis points up');
  assert(projectViewPoint([0,0,-radius],diagonal,1.5)[1]>0,'default diagonal: forward projects toward screen-top');
}
console.log('view camera tests passed');
