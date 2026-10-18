// Frontend-only inference. A shared scene transform is NOT a tracked head pose.
import {robustRegister,directionalConsensus,groupRigidConsensus} from './navigation-core.js';
import {sparseMotion} from './navigation-sparse.js';
import {StationaryReferences} from './navigation-stationary.js';
export const rad = Math.PI / 180;
export const dot = (a,b)=>a.reduce((s,x,i)=>s+x*b[i],0);
export const sub = (a,b)=>a.map((x,i)=>x-b[i]);
export const norm = a=>Math.hypot(...a);
export const unit = a=>a.map(x=>x/(norm(a)||1));
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const identity=()=>[1,0,0,0,1,0,0,0,1];
export const apply=(r,p)=>[dot(r.slice(0,3),p),dot(r.slice(3,6),p),dot(r.slice(6,9),p)];
const multiply=(a,b)=>a.map((_,i)=>[0,1,2].reduce((s,k)=>s+a[Math.floor(i/3)*3+k]*b[k*3+i%3],0));
export function rotation(yaw,pitch=0){const c=Math.cos(yaw),s=Math.sin(yaw),x=Math.cos(pitch),t=Math.sin(pitch);return [c,s*t,s*x,0,x,-t,-s,c*t,c*x];}
export function direction(v,threshold=.015){if(norm(v)<threshold)return '静止';const x=v[0],z=v[2];if(Math.hypot(x,z)<threshold)return '上下';const h=Math.abs(x)>Math.abs(z)*.45?(x>0?'右':'左'):'';const f=Math.abs(z)>Math.abs(x)*.45?(z>0?'后':'前'):'';return f+h;}
const empty=reason=>({valid:false,confidence:0,reason,coordinateMode:'direction',sceneDelta:[0,0,0],observerCandidate:[0,0,0],observerDeltaRotation:identity(),commonFlow:[0,0,0],energyFlow:[0,0,0],energyAvailable:false,dominantFlow:[0,0,0],flowAvailable:false,cooperativeFlow:{valid:false,scene:[0,0,0],observer:[0,0,0],confidence:0,axes:[]},groupMotion:{valid:false,groups:0,confidence:0},navigationCandidate:{valid:false,rotation:null,travel:null,confidence:0,signals:[]},translation:null,visualFlow:null,translationUnit:null,residuals:new Map(),pairs:0,inlierRatio:0,medianResidual:null,degenerate:true,observerMotion:{status:'unobservable',rotation:null,translation:null,translationVelocity:null},sceneMotion:{status:'unobservable',rotation:null,translation:null}});
export class Navigation {
  constructor(){this.reset();}
  sparse(pairs,dt){
    this.good=0;this.flowHistory=[];this.cooperativeHistory=[[],[]];this.previousDelta=null;
    const fit=sparseMotion(pairs,dt),old=this.sparseState,result=empty('少对象参考确认中');result.coordinateMode='source';result.pairs=pairs.length;
    if(!fit){this.sparseState=null;result.reason='少对象运动不一致';return this.result=result;}
    const coherent=old&&old.keys===fit.keys&&norm(sub(old.angularVelocity,fit.angularVelocity))<1.5&&norm(sub(old.rawVelocity,fit.rawVelocity))<.6;
    this.sparseState={...fit,frames:coherent?old.frames+1:1};
    result.confidence=.52;result.valid=this.sparseState.frames>=2;result.degenerate=true;
    result.reason=result.valid?'少对象相对位姿 · 低置信度 / 连线轴旋转未约束':result.reason;
    if(!result.valid)return this.result=result;
    const {r,t}=fit,inverse=r.map((_,i)=>r[i%3*3+Math.floor(i/3)]),observer=apply(inverse,t).map(x=>-x);
    result.sceneDelta=[Math.atan2(r[2],r[8]),Math.asin(clamp(-r[5],-1,1)),Math.atan2(r[3],r[4])];
    result.observerDeltaRotation=inverse;result.observerCandidate=[Math.atan2(inverse[2],inverse[8]),Math.asin(clamp(-inverse[5],-1,1)),Math.atan2(inverse[3],inverse[4])];
    this.orientation=multiply(r,this.orientation);const yaw=Math.atan2(this.orientation[2],this.orientation[8]);
    this.yaw+=Math.atan2(Math.sin(yaw-this.yaw),Math.cos(yaw-this.yaw));this.pitch=Math.asin(clamp(-this.orientation[5],-1,1));
    result.translation=t.map(x=>x/dt);result.translationUnit='source-unverified/s';result.visualFlow=result.translation.map(x=>x/Math.max(fit.scale,1)*3);
    result.sceneMotion={status:'sparse-constrained',rotation:r,translation:t};
    result.observerMotion={status:'sparse-constrained',rotation:inverse,translation:observer,translationVelocity:observer.map(x=>x/dt)};
    result.navigationCandidate={valid:true,rotation:inverse,travel:result.observerMotion.translationVelocity,confidence:.52,signals:['sparse-relative-pose','baseline-twist-unobserved']};
    return this.result=result;
  }
  reset(){this.stationary=new StationaryReferences();this.sparseState=null;this.previous=null;this.scope='';this.orientation=identity();this.yaw=0;this.pitch=0;this.result=empty('等待连续参考');this.good=0;this.tracks=new Map();this.previousRate=null;this.previousDelta=null;this.flowHistory=[];this.cooperativeHistory=[[],[]];}
  cooperate(evidence,time){
    const scene=[0,0,0],axes=[],quality=[];
    for(let k=0;k<2;k++){
      const history=this.cooperativeHistory[k].filter(x=>time-x.time<=500000);
      if(evidence[k])history.push({...evidence[k],time});
      this.cooperativeHistory[k]=history.slice(-5);
      if(!history.length||time-history.at(-1).time>150000)continue;
      const sign=Math.sign(history.at(-1).value),same=history.filter(x=>Math.sign(x.value)===sign);
      if(same.length<3||same.at(-1).time-same.at(-3).time<180000)continue;
      const weight=same.reduce((s,x)=>s+x.confidence,0),velocity=same.reduce((s,x)=>s+x.value*x.confidence,0)/(weight||1);
      const axis=k?2:0;scene[axis]=velocity;axes.push(axis);quality.push(weight/same.length);
    }
    return {valid:axes.length>0,scene,observer:scene.map(x=>-x),confidence:quality.length?Math.min(...quality):0,axes};
  }
  ingest(m,{stableScale=false,sourceSpace=false}={}){
    const positional=stableScale||sourceSpace;
    const tap=m.taps['decoded-input'],scope=JSON.stringify([m.routeId,m.sessionId,m.streamEpoch,m.meterRevision]);
    if(scope!==this.scope){this.reset();this.scope=scope;}
    const objects=new Map(tap.objects.filter(o=>o.kind==='dynamic'&&o.spatial.positionValid&&norm(o.spatial.position)>1e-5).map(o=>[JSON.stringify([o.objectKey.sessionId,o.objectKey.streamEpoch,o.objectKey.slot,o.objectKey.generation]),{p:o.spatial.position,e:o.meter.meanSquare||0}]));
    const previous=this.previous,dt=previous?(m.sourceTimeUs-previous.time)/1e6:0;
    this.previous={objects,time:m.sourceTimeUs,end:m.sourceSequenceRange.endExclusive};
    if(!previous||dt<=0||dt>.25||tap.quality!=='live'||!tap.snapshotComplete||previous.end!==m.sourceSequenceRange.start){this.stationary.reset();this.sparseState=null;this.good=0;this.tracks.clear();this.previousRate=null;this.previousDelta=null;this.flowHistory=[];this.cooperativeHistory=[[],[]];this.previous=tap.quality==='live'&&tap.snapshotComplete?this.previous:null;return this.result=empty('参考中断 / 预热');}
    const pairs=[];for(const [key,o]of objects){const old=previous.objects.get(key);if(!old)continue;
      const a=positional?old.p:unit(old.p),b=positional?o.p:unit(o.p),track=this.tracks.get(key),velocity=sub(b,a).map(x=>x/dt);
      const age=Math.min(1,(track?.age||0)/4),coherence=track?.coherence??.65;
      const acceleration=track?.velocity?norm(sub(velocity,track.velocity))*dt:0;
      const prior=clamp((.5+.5*age)*coherence/(1+acceleration/(sourceSpace?Math.max(1,norm(a)*.08):stableScale?.2:.06)),.12,1);
      pairs.push({key,a,b,e:o.e,prior,velocity});}
    // Preserve range diversity in source-space fits; only collapse truly colocated voice layers.
    const unique=new Map();for(const p of pairs){const cell=(positional?[...p.a,...p.b].map(x=>Math.round(x*20)):[...unit(p.a),...unit(p.b)].map(x=>Math.round(x*100))).join(',');if(!unique.has(cell))unique.set(cell,p);}
    const fitPairs=[...unique.values()];
    const stationary=sourceSpace?this.stationary.update(pairs,m.sourceTimeUs,fitPairs.length<6):[];
    if(sourceSpace&&stationary.length>=2){
      this.sparseState=null;this.good=0;this.previousDelta=null;this.flowHistory=[];this.cooperativeHistory=[[],[]];
      const result=empty('远处静止参考锁定');result.coordinateMode='source';result.valid=true;result.confidence=.52;result.pairs=fitPairs.length;
      result.stationaryLock={active:true,keys:stationary};result.translation=[0,0,0];result.visualFlow=[0,0,0];
      result.navigationCandidate.signals=['stationary-reference-lock'];
      return this.result=result;
    }
    if(sourceSpace&&fitPairs.length>=2&&fitPairs.length<6)return this.sparse(fitPairs,dt);
    this.sparseState=null;
    if(pairs.length<6){this.good=0;return this.result=empty('有效对象不足');}
    const scale=fitPairs.length?fitPairs.map(p=>norm(p.a)).sort((a,b)=>a-b)[Math.floor(fitPairs.length/2)]:1;
    // The recorded raw coordinates retain a useful relative scale, though its physical unit is unverified.
    const gate=sourceSpace?clamp(scale*.05,.35,2):undefined;
    const fitResult=fitPairs.length>=6?robustRegister(fitPairs,{metric:positional,threshold:gate}):null,result=empty('共同运动不一致');result.pairs=fitPairs.length;result.coordinateMode=sourceSpace?'source':stableScale?'simulation':'direction';
    let energy=0;for(const p of pairs){energy+=p.e;const v=sub(unit(p.b),unit(p.a));for(let i=0;i<3;i++)result.energyFlow[i]+=p.e*v[i]/dt;}
    if(energy){result.energyFlow=result.energyFlow.map(x=>x/energy);result.energyAvailable=true;}
    const angle=fitResult?Math.acos(clamp((fitResult.r[0]+fitResult.r[4]+fitResult.r[8]-1)/2,-1,1)):Infinity;
    const plausible=sourceSpace&&fitResult&&!fitResult.degenerate&&fitResult.inlierRatio>=.45&&angle<Math.min(60*rad,dt*12);
    if(plausible&&(fitResult.confidence<.7||angle>3*rad))result.groupMotion=groupRigidConsensus(fitPairs.map(p=>({...p,prior:1})),scale,gate,fitResult.r);
    result.cooperativeFlow=this.cooperate(plausible?directionalConsensus(fitPairs,fitResult.r,scale,dt):[null,null],m.sourceTimeUs);
    if(result.cooperativeFlow.valid){result.translation=result.cooperativeFlow.scene;result.translationUnit='source-unverified/s';result.visualFlow=result.translation.map(x=>x/Math.max(scale,1)*3);result.reason='多源协同流 · 转向待确认';result.observerMotion={status:'flow-candidate',rotation:null,translation:null,translationVelocity:result.cooperativeFlow.observer};result.sceneMotion={status:'cooperative-flow',rotation:null,translation:result.cooperativeFlow.scene};result.navigationCandidate={valid:true,rotation:null,travel:result.cooperativeFlow.observer,confidence:result.cooperativeFlow.confidence,signals:['multi-direction-flow']};}
    if(!fitResult){this.good=0;return this.result=result;}
    result.inlierRatio=fitResult.inlierRatio;result.medianResidual=fitResult.error;result.degenerate=fitResult.degenerate;
    const effectiveConfidence=Math.max(fitResult.confidence,result.groupMotion.confidence);
    result.confidence=effectiveConfidence;
    for(let i=0;i<fitPairs.length;i++){const p=fitPairs[i],old=this.tracks.get(p.key),agreement=fitResult.errors[i]<=fitResult.threshold?1:0;
      this.tracks.set(p.key,{age:Math.min(12,(old?.age||0)+1),coherence:clamp((old?.coherence??.65)*.65+agreement*.35,.1,1),velocity:p.velocity});}
    for(const key of this.tracks.keys())if(!objects.has(key))this.tracks.delete(key);
    if(fitResult.degenerate){this.good=0;result.reason='参考几何退化';return this.result=result;}
    if(fitResult.inlierRatio<(sourceSpace&&result.groupMotion.valid ? .5 : sourceSpace ? .58 : .55)||effectiveConfidence<(sourceSpace ? .44 : .65)){this.good=0;this.previousDelta=null;return this.result=result;}
    const {r,t}=fitResult,confidence=effectiveConfidence;
    if(angle>Math.min((sourceSpace?60:70)*rad,dt*(sourceSpace?12:8))){this.good=0;result.reason='变化过快 / 参考跳变';return this.result=result;}
    const rate=angle/dt;if(!sourceSpace&&this.previousRate!==null&&Math.abs(rate-this.previousRate)>6&&fitResult.inlierRatio<.85){this.good=0;result.reason='运动时序突变';return this.result=result;}this.previousRate=rate;
    const deltaVector=[Math.atan2(r[2],r[8]),Math.asin(clamp(-r[5],-1,1)),Math.atan2(r[3],r[4])];
    if(this.previousDelta&&(sourceSpace?Math.abs(deltaVector[0])>8*rad&&Math.abs(this.previousDelta[0])>8*rad&&deltaVector[0]*this.previousDelta[0]<0&&effectiveConfidence<.7:norm(sub(deltaVector,this.previousDelta))>Math.max(.045,dt*.35)&&effectiveConfidence<.85)){this.good=0;this.previousDelta=deltaVector;result.reason='旋转方向突变';return this.result=result;}
    this.previousDelta=deltaVector;
    result.confidence=confidence;result.valid=++this.good>=(sourceSpace?2:3);result.reason=result.valid?result.groupMotion.valid?'多组共同场景运动 · 来源待确认':'共同场景运动 · 来源待确认':'一致性确认中';
    result.sceneDelta=[Math.atan2(r[2],r[8]),Math.asin(clamp(-r[5],-1,1)),Math.atan2(r[3],r[4])];
    const inverse=r.map((_,i)=>r[(i%3)*3+Math.floor(i/3)]);
    result.observerDeltaRotation=inverse;
    result.observerCandidate=[Math.atan2(inverse[2],inverse[8]),Math.asin(clamp(-inverse[5],-1,1)),Math.atan2(inverse[3],inverse[4])];
    if(result.valid){this.orientation=multiply(r,this.orientation);const yaw=Math.atan2(this.orientation[2],this.orientation[8]);const last=((this.yaw+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;this.yaw+=Math.atan2(Math.sin(yaw-last),Math.cos(yaw-last));this.pitch=Math.asin(clamp(-this.orientation[5],-1,1));}
    let flowWeight=0,movers=0;for(const p of pairs){const rotated=apply(r,p.a),residual=sub(sub(p.b,rotated),t),velocity=residual.map(x=>x/dt);result.residuals.set(p.key,velocity);
      const flow=sub(unit(rotated),unit(p.a));for(let i=0;i<3;i++)result.commonFlow[i]+=flow[i]/dt/pairs.length;
      const independent=norm(residual)>fitResult.threshold;if(independent)movers++;
      const weight=independent?(p.e||0)*clamp(norm(residual)/(fitResult.threshold*2),0,1):0;
      flowWeight+=weight;for(let i=0;i<3;i++)result.dominantFlow[i]+=velocity[i]*weight;}
    if(flowWeight>1e-8&&movers>=3&&result.valid){result.dominantFlow=result.dominantFlow.map(x=>x/flowWeight);result.flowAvailable=true;}
    if(positional&&result.valid){
      if(sourceSpace){
        this.flowHistory.push({time:m.sourceTimeUs,v:t.map(x=>x/dt),weight:confidence});
        this.flowHistory=this.flowHistory.filter(x=>m.sourceTimeUs-x.time<=500000).slice(-5);
        const weight=this.flowHistory.reduce((s,x)=>s+x.weight,0),sum=[0,0,0],length=this.flowHistory.reduce((s,x)=>s+norm(x.v)*x.weight,0);
        for(const x of this.flowHistory)for(let i=0;i<3;i++)sum[i]+=x.v[i]*x.weight;
        const mean=sum.map(x=>x/(weight||1));
        if(!result.cooperativeFlow.valid&&this.flowHistory.length>=3&&norm(sum)/(length||1)>.35&&norm(mean)>scale*.05){result.translation=mean;result.translationUnit='source-unverified/s';result.visualFlow=mean.map(x=>x/Math.max(scale,1)*3);}
      }else{result.translation=t.map(x=>x/dt);result.translationUnit='simulation-units/s';result.visualFlow=result.translation;}
    }
    result.sceneMotion={status:result.valid?'inferred-common':result.cooperativeFlow.valid?'cooperative-flow':'pending',rotation:result.valid?r:null,translation:result.valid&&positional?t:result.cooperativeFlow.valid?result.cooperativeFlow.scene:null};
    result.observerMotion={status:result.valid?'candidate-only':result.cooperativeFlow.valid?'flow-candidate':'pending',rotation:result.valid?inverse:null,translation:result.valid&&positional?apply(inverse,t).map(x=>-x):null,translationVelocity:result.cooperativeFlow.valid?result.cooperativeFlow.observer:null};
    result.navigationCandidate={valid:result.valid||result.cooperativeFlow.valid,rotation:result.valid?inverse:null,travel:result.cooperativeFlow.valid?result.cooperativeFlow.observer:null,confidence:Math.max(result.valid?confidence:0,result.cooperativeFlow.confidence),signals:[...(result.valid?['global-rigid']:[]),...(result.groupMotion.valid?['multi-group-rigid']:[]),...(result.cooperativeFlow.valid?['multi-direction-flow']:[])]};
    return this.result=result;
  }
}
export class VisualHeading {
  constructor({limit=40*rad,recenter=125*rad,hysteresis=20*rad}={}){Object.assign(this,{limit,recenter,hysteresis});this.reset();}
  reset(){this.yaw=0;this.pitch=0;this.anchorYaw=0;this.anchorPitch=0;this.fade=1;this.phase=0;this.cooldown=0;this.relocations=0;this.armed=true;this.flow=[0,0,0];this.trustAge=1;}
  step(nav,dt,{manual=false,reduced=false,fresh=true}={}){
    dt=clamp(dt,0,.1);const trusted=nav.result.prediction?.pose!=='predicted'&&nav.result.valid&&nav.result.confidence>=(nav.result.coordinateMode==='source' ? .5 : .7)&&fresh;
    this.trustAge=trusted?0:this.trustAge+dt;
    const offset=nav.yaw-this.anchorYaw,pitch=nav.pitch-this.anchorPitch;
    if(Math.abs(offset)<this.recenter-this.hysteresis)this.armed=true;
    this.cooldown=Math.max(0,this.cooldown-dt);
    if(trusted&&!manual&&!reduced&&this.armed&&!this.phase&&!this.cooldown&&Math.max(Math.abs(offset),Math.abs(pitch))>this.recenter){this.phase=1;this.armed=false;this.relocations++;}
    if(this.phase===1){this.fade=Math.max(0,this.fade-dt/.16);if(!this.fade){this.anchorYaw=nav.yaw;this.anchorPitch=nav.pitch;this.yaw=this.pitch=0;this.phase=2;}}
    else if(this.phase===2){this.fade=Math.min(1,this.fade+dt/.22);if(this.fade===1){this.phase=0;this.cooldown=1.2;}}
    const predictedPose=['predicted','reconciling','holding'].includes(nav.result.prediction?.pose)&&fresh;
    const gain=1-Math.exp(-dt/.24),enabled=(trusted||predictedPose||this.trustAge<.3)&&fresh&&!manual&&!reduced;
    const target=a=>{
      if(!enabled)return 0;
      const magnitude=Math.abs(a),linear=24*rad;
      return Math.sign(a)*(magnitude<=linear?magnitude:linear+(this.limit-linear)*(1-Math.exp(-(magnitude-linear)/(28*rad))));
    };
    this.yaw+=(target(nav.yaw-this.anchorYaw)-this.yaw)*gain;
    this.pitch+=(target(nav.pitch-this.anchorPitch)-this.pitch)*gain;
    const flowEnabled=(enabled||nav.result.cooperativeFlow?.valid&&fresh&&!manual&&!reduced||['predicted','reconciling'].includes(nav.result.prediction?.flow)&&fresh&&!manual&&!reduced);
    for(let i=0;i<3;i++)this.flow[i]+=((flowEnabled?(nav.result.visualFlow||nav.result.translation)?.[i]||0:0)-this.flow[i])*gain;
    return this;
  }
}
