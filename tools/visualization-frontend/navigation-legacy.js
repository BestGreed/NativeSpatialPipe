// Frontend-only inference. A shared scene transform is NOT a tracked head pose.
export const rad = Math.PI / 180;
export const dot = (a,b)=>a.reduce((s,x,i)=>s+x*b[i],0);
export const sub = (a,b)=>a.map((x,i)=>x-b[i]);
export const norm = a=>Math.hypot(...a);
export const unit = a=>a.map(x=>x/(norm(a)||1));
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const identity=()=>[1,0,0,0,1,0,0,0,1];
export const apply=(r,p)=>[dot(r.slice(0,3),p),dot(r.slice(3,6),p),dot(r.slice(6,9),p)];
const multiply=(a,b)=>a.map((_,i)=>[0,1,2].reduce((s,k)=>s+a[Math.floor(i/3)*3+k]*b[k*3+i%3],0));
export function rotation(yaw,pitch=0){const c=Math.cos(yaw),s=Math.sin(yaw),x=Math.cos(pitch),t=Math.sin(pitch);return [c,s*t,s*x,0,x,-t,-s,c*t,c*x];}
const median=a=>a.sort((a,b)=>a-b)[Math.floor(a.length/2)]||0;
const medVector=ps=>[0,1,2].map(i=>median(ps.map(p=>p[i])));
function basis(a,b){const x=unit(a),z=unit(cross(a,b));if(norm(cross(x,unit(b)))<.12)return null;return [x,cross(z,x),z];}
function fitPair(a,b,c,d){const u=basis(a,b),v=basis(c,d);return u&&v?[0,1,2].flatMap(i=>[0,1,2].map(j=>v.reduce((s,e,k)=>s+e[i]*u[k][j],0))):null;}
function solve(a,b){const m=a.map((row,i)=>[...row,b[i]]);for(let i=0;i<3;i++){let p=i;for(let j=i+1;j<3;j++)if(Math.abs(m[j][i])>Math.abs(m[p][i]))p=j;[m[i],m[p]]=[m[p],m[i]];if(Math.abs(m[i][i])<1e-7)return null;const q=m[i][i];for(let k=i;k<4;k++)m[i][k]/=q;for(let j=0;j<3;j++)if(j!==i){const f=m[j][i];for(let k=i;k<4;k++)m[j][k]-=f*m[i][k];}}return m.map(row=>row[3]);}
function rodrigues(w){const angle=norm(w);if(angle<1e-12)return identity();const [x,y,z]=unit(w),c=Math.cos(angle),s=Math.sin(angle),d=1-c;return [c+x*x*d,x*y*d-z*s,x*z*d+y*s,y*x*d+z*s,c+y*y*d,y*z*d-x*s,z*x*d-y*s,z*y*d+x*s,c+z*z*d];}
// Deterministic RANSAC (bounded 64 proposals), followed by inlier least squares.
function fit(pairs,metric){
  const n=pairs.length,scale=median(pairs.map(p=>norm(p.a)))||1,threshold=metric?scale*.008:.018;
  let best=null,seed=7351;
  const random=()=>{seed=(1664525*seed+1013904223)>>>0;return seed%n;};
  for(let trial=0;trial<64;trial++){
    const i=random(),j=random(),k=random();if(i===j||metric&&(i===k||j===k))continue;
    const origin=metric?pairs[k]:{a:[0,0,0],b:[0,0,0]};
    const r=fitPair(sub(pairs[i].a,origin.a),sub(pairs[j].a,origin.a),sub(pairs[i].b,origin.b),sub(pairs[j].b,origin.b));if(!r)continue;
    const t=metric?medVector(pairs.map(p=>sub(p.b,apply(r,p.a)))):[0,0,0];
    const inliers=pairs.filter(p=>norm(sub(sub(p.b,apply(r,p.a)),t))<threshold);
    if(!best||inliers.length>best.inliers.length)best={r,t,inliers};
  }
  if(!best||best.inliers.length<6)return null;
  let {r,t,inliers}=best;
  for(let iteration=0;iteration<4;iteration++){
    const center=metric?[0,1,2].map(i=>inliers.reduce((s,p)=>s+p.a[i],0)/inliers.length):[0,0,0];
    const a=[[0,0,0],[0,0,0],[0,0,0]],b=[0,0,0];
    for(const p of inliers){const q=apply(r,sub(p.a,center)),e=sub(sub(p.b,apply(r,p.a)),t),v=cross(q,e),q2=dot(q,q);for(let i=0;i<3;i++){b[i]+=v[i];for(let j=0;j<3;j++)a[i][j]+=(i===j?q2:0)-q[i]*q[j];}}
    const w=solve(a,b);if(!w)return null;r=multiply(rodrigues(w),r);if(metric)t=medVector(inliers.map(p=>sub(p.b,apply(r,p.a))));
  }
  const residuals=pairs.map(p=>norm(sub(sub(p.b,apply(r,p.a)),t)));
  const support=residuals.filter(e=>e<threshold).length/n;
  // Spatial spread rejects coincident/near-collinear references.
  let spread=0;for(const p of inliers)spread+=norm(cross(unit(p.a),unit(inliers[0].a)))**2;spread/=inliers.length;
  return {r,t,residuals,support,confidence:support*clamp(spread/.3,0,1),error:median(residuals),threshold};
}
export function direction(v,threshold=.015){if(norm(v)<threshold)return '静止';const x=v[0],z=v[2];if(Math.hypot(x,z)<threshold)return '上下';const h=Math.abs(x)>Math.abs(z)*.45?(x>0?'右':'左'):'';const f=Math.abs(z)>Math.abs(x)*.45?(z>0?'后':'前'):'';return f+h;}
const empty=reason=>({valid:false,confidence:0,reason,sceneDelta:[0,0,0],observerCandidate:[0,0,0],observerDeltaRotation:identity(),commonFlow:[0,0,0],energyFlow:[0,0,0],energyAvailable:false,translation:null,residuals:new Map(),pairs:0});
export class Navigation {
  constructor(){this.reset();}
  reset(){this.previous=null;this.scope='';this.orientation=identity();this.yaw=0;this.pitch=0;this.result=empty('等待连续参考');this.good=0;}
  ingest(m,{stableScale=false}={}){
    const tap=m.taps['decoded-input'],scope=JSON.stringify([m.routeId,m.sessionId,m.streamEpoch,m.meterRevision]);
    if(scope!==this.scope){this.reset();this.scope=scope;}
    const objects=new Map(tap.objects.filter(o=>o.kind==='dynamic'&&o.spatial.positionValid&&norm(o.spatial.position)>1e-5).map(o=>[JSON.stringify([o.objectKey.sessionId,o.objectKey.streamEpoch,o.objectKey.slot,o.objectKey.generation]),{p:o.spatial.position,e:o.meter.meanSquare||0}]));
    const previous=this.previous,dt=previous?(m.sourceTimeUs-previous.time)/1e6:0;
    this.previous={objects,time:m.sourceTimeUs,end:m.sourceSequenceRange.endExclusive};
    if(!previous||dt<=0||dt>.25||tap.quality!=='live'||!tap.snapshotComplete||previous.end!==m.sourceSequenceRange.start){this.good=0;this.previous=tap.quality==='live'&&tap.snapshotComplete?this.previous:null;return this.result=empty('参考中断 / 预热');}
    const pairs=[];for(const [key,o]of objects){const old=previous.objects.get(key);if(old)pairs.push({key,a:stableScale?old.p:unit(old.p),b:stableScale?o.p:unit(o.p),e:o.e});}
    if(pairs.length<6){this.good=0;return this.result=empty('有效对象不足');}
    // Coincident voice layers must not count as independent spatial references.
    const unique=new Map();for(const p of pairs){const cell=[...unit(p.a),...unit(p.b)].map(x=>Math.round(x*100)).join(',');if(!unique.has(cell))unique.set(cell,p);}
    const fitPairs=[...unique.values()];
    const fitResult=fitPairs.length>=6?fit(fitPairs,stableScale):null,result=empty('共同运动不一致');result.pairs=fitPairs.length;
    let energy=0;for(const p of pairs){energy+=p.e;const v=sub(unit(p.b),unit(p.a));for(let i=0;i<3;i++)result.energyFlow[i]+=p.e*v[i]/dt;}
    if(energy){result.energyFlow=result.energyFlow.map(x=>x/energy);result.energyAvailable=true;}
    if(!fitResult||fitResult.support<.7||fitResult.confidence<.65){this.good=0;return this.result=result;}
    const {r,t,confidence}=fitResult,angle=Math.acos(clamp((r[0]+r[4]+r[8]-1)/2,-1,1));
    if(angle>Math.min(35*rad,dt*5)){this.good=0;result.reason='变化过快 / 参考跳变';return this.result=result;}
    result.confidence=confidence;result.valid=++this.good>=3;result.reason=result.valid?'共同场景旋转 · 非头部追踪':'一致性确认中';
    result.sceneDelta=[Math.atan2(r[2],r[8]),Math.asin(clamp(-r[5],-1,1)),Math.atan2(r[3],r[4])];
    const inverse=r.map((_,i)=>r[(i%3)*3+Math.floor(i/3)]);
    result.observerDeltaRotation=inverse;
    result.observerCandidate=[Math.atan2(inverse[2],inverse[8]),Math.asin(clamp(-inverse[5],-1,1)),Math.atan2(inverse[3],inverse[4])];
    if(result.valid){this.orientation=multiply(r,this.orientation);const yaw=Math.atan2(this.orientation[2],this.orientation[8]);const last=((this.yaw+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;this.yaw+=Math.atan2(Math.sin(yaw-last),Math.cos(yaw-last));this.pitch=Math.asin(clamp(-this.orientation[5],-1,1));}
    for(const p of pairs){const rotated=apply(r,p.a),residual=sub(sub(p.b,rotated),t);result.residuals.set(p.key,residual.map(x=>x/dt));const flow=sub(unit(rotated),unit(p.a));for(let i=0;i<3;i++)result.commonFlow[i]+=flow[i]/dt/pairs.length;}
    if(stableScale&&result.valid)result.translation=t.map(x=>x/dt); // scene translation, source units/s, simulation only
    return this.result=result;
  }
}
export class VisualHeading {
  constructor({limit=30*rad,recenter=125*rad,hysteresis=20*rad}={}){Object.assign(this,{limit,recenter,hysteresis});this.reset();}
  reset(){this.yaw=0;this.pitch=0;this.anchorYaw=0;this.anchorPitch=0;this.fade=1;this.phase=0;this.cooldown=0;this.relocations=0;this.armed=true;this.flow=[0,0,0];}
  step(nav,dt,{manual=false,reduced=false,fresh=true}={}){
    dt=clamp(dt,0,.1);const trusted=nav.result.valid&&nav.result.confidence>=.7&&fresh;
    const offset=nav.yaw-this.anchorYaw,pitch=nav.pitch-this.anchorPitch;
    if(Math.abs(offset)<this.recenter-this.hysteresis)this.armed=true;
    this.cooldown=Math.max(0,this.cooldown-dt);
    if(trusted&&!manual&&!reduced&&this.armed&&!this.phase&&!this.cooldown&&Math.max(Math.abs(offset),Math.abs(pitch))>this.recenter){this.phase=1;this.armed=false;this.relocations++;}
    if(this.phase===1){this.fade=Math.max(0,this.fade-dt/.16);if(!this.fade){this.anchorYaw=nav.yaw;this.anchorPitch=nav.pitch;this.yaw=this.pitch=0;this.phase=2;}}
    else if(this.phase===2){this.fade=Math.min(1,this.fade+dt/.22);if(this.fade===1){this.phase=0;this.cooldown=1.2;}}
    const gain=1-Math.exp(-dt/.24),enabled=trusted&&!manual&&!reduced;
    const target=a=>enabled?this.limit*Math.tanh(a/this.limit):0;
    this.yaw+=(target(nav.yaw-this.anchorYaw)-this.yaw)*gain;
    this.pitch+=(target(nav.pitch-this.anchorPitch)-this.pitch)*gain;
    for(let i=0;i<3;i++)this.flow[i]+=((enabled?nav.result.translation?.[i]||0:0)-this.flow[i])*gain;
    return this;
  }
}
