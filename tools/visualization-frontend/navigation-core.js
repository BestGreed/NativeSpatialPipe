// Robust weighted rigid registration. All arrays are row-major; no audio thread work.
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const norm=a=>Math.hypot(...a);
const sub=(a,b)=>a.map((x,i)=>x-b[i]);
const add=(a,b)=>a.map((x,i)=>x+b[i]);
const scale=(a,s)=>a.map(x=>x*s);
const unit=a=>scale(a,1/(norm(a)||1));
const apply=(r,p)=>[dot(r.slice(0,3),p),dot(r.slice(3,6),p),dot(r.slice(6,9),p)];
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const median=a=>{const v=[...a].sort((x,y)=>x-y);return v[Math.floor(v.length/2)]||0;};
function largestEigen4(matrix){
  const a=matrix.map(row=>[...row]),v=Array.from({length:4},(_,i)=>Array.from({length:4},(_,j)=>+(i===j)));
  for(let step=0;step<40;step++){
    let p=0,q=1;for(let i=0;i<4;i++)for(let j=i+1;j<4;j++)if(Math.abs(a[i][j])>Math.abs(a[p][q]))[p,q]=[i,j];
    if(Math.abs(a[p][q])<1e-12)break;
    const angle=.5*Math.atan2(2*a[p][q],a[q][q]-a[p][p]),c=Math.cos(angle),s=Math.sin(angle);
    for(let k=0;k<4;k++)if(k!==p&&k!==q){const x=a[k][p],y=a[k][q];a[k][p]=a[p][k]=c*x-s*y;a[k][q]=a[q][k]=s*x+c*y;}
    const pp=a[p][p],qq=a[q][q],pq=a[p][q];a[p][p]=c*c*pp+s*s*qq-2*c*s*pq;a[q][q]=s*s*pp+c*c*qq+2*c*s*pq;a[p][q]=a[q][p]=0;
    for(let k=0;k<4;k++){const x=v[k][p],y=v[k][q];v[k][p]=c*x-s*y;v[k][q]=s*x+c*y;}
  }
  let index=0;for(let i=1;i<4;i++)if(a[i][i]>a[index][index])index=i;
  const q=v.map(row=>row[index]),length=Math.hypot(...q);return q.map(x=>x/length);
}
function quatMatrix([w,x,y,z]){return [1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)];}
export function weightedProcrustes(pairs,weights,metric=false){
  let weight=0,ca=[0,0,0],cb=[0,0,0];for(let i=0;i<pairs.length;i++){const w=weights[i];if(w<=0)continue;weight+=w;ca=add(ca,scale(pairs[i].a,w));cb=add(cb,scale(pairs[i].b,w));}
  if(weight<=0)return null;ca=metric?scale(ca,1/weight):[0,0,0];cb=metric?scale(cb,1/weight):[0,0,0];
  const h=Array.from({length:3},()=>[0,0,0]);let variance=0;
  for(let i=0;i<pairs.length;i++){const w=weights[i];if(w<=0)continue;const a=sub(pairs[i].a,ca),b=sub(pairs[i].b,cb);variance+=w*dot(a,a);for(let j=0;j<3;j++)for(let k=0;k<3;k++)h[j][k]+=w*a[j]*b[k];}
  if(variance/weight<1e-9)return null;
  const [[xx,xy,xz],[yx,yy,yz],[zx,zy,zz]]=h;
  const n=[[xx+yy+zz,yz-zy,zx-xz,xy-yx],[yz-zy,xx-yy-zz,xy+yx,zx+xz],[zx-xz,xy+yx,-xx+yy-zz,yz+zy],[xy-yx,zx+xz,yz+zy,-xx-yy+zz]];
  const r=quatMatrix(largestEigen4(n)),t=metric?sub(cb,apply(r,ca)):[0,0,0];
  return {r,t};
}
function geometry(pairs,weights,metric){
  let total=0,center=[0,0,0];for(let i=0;i<pairs.length;i++){total+=weights[i];center=add(center,scale(pairs[i].a,weights[i]));}center=metric?scale(center,1/(total||1)):[0,0,0];
  const matrix=Array.from({length:3},()=>[0,0,0]);for(let i=0;i<pairs.length;i++){const a=sub(pairs[i].a,center),w=weights[i];for(let j=0;j<3;j++)for(let k=0;k<3;k++)matrix[j][k]+=w*a[j]*a[k]/(total||1);}
  const trace=matrix[0][0]+matrix[1][1]+matrix[2][2];if(trace<1e-6)return 0;
  // Directional constraint requires two well-separated axes; SE(3) also needs spatial spread.
  let v=unit([1,.37,.11]);for(let i=0;i<12;i++)v=unit(matrix.map(row=>dot(row,v)));
  const dominant=dot(v,matrix.map(row=>dot(row,v)));
  return clamp((trace-dominant)/(trace*.3),0,1);
}
const residual=(pair,fit)=>norm(sub(pair.b,add(apply(fit.r,pair.a),fit.t)));
export function robustRegister(pairs,{metric=false,iterations=64,threshold}={}){
  if(pairs.length<6)return null;
  const scaleValue=median(pairs.map(p=>norm(p.a)))||1,gate=threshold??(metric?Math.max(.012,scaleValue*.008):.025);
  let seed=0x9e3779b9,best=null;
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed%pairs.length;};
  for(let n=0;n<iterations;n++){
    const ids=new Set();while(ids.size<3)ids.add(random());
    const w=pairs.map((_,i)=>ids.has(i)?1:0),fit=weightedProcrustes(pairs,w,metric);if(!fit||geometry(pairs,w,metric)<.08)continue;
    const errors=pairs.map(p=>residual(p,fit));let support=0,score=0;
    for(let i=0;i<errors.length;i++)if(errors[i]<gate){support++;score+=(pairs[i].prior??1)*(1-errors[i]/gate);}
    if(!best||score>best.score)best={fit,score,support};
    if(support/pairs.length>.92&&score>pairs.length*.82)break;
  }
  if(!best||best.support<6)return null;
  let fit=best.fit,weights=[],errors=[];
  for(let step=0;step<6;step++){
    errors=pairs.map(p=>residual(p,fit));const med=median(errors),mad=median(errors.map(x=>Math.abs(x-med)));
    const robustScale=Math.max(gate*.25,1.4826*mad),huber=Math.min(gate,2.5*robustScale);
    weights=errors.map((e,i)=>e>gate*2?0:(pairs[i].prior??1)*Math.min(1,huber/Math.max(e,1e-9)));
    const next=weightedProcrustes(pairs,weights,metric);if(!next)break;fit=next;
  }
  errors=pairs.map(p=>residual(p,fit));const inliers=errors.filter(e=>e<=gate).length,ratio=inliers/pairs.length;
  const spread=geometry(pairs,weights,metric),error=median(errors),confidence=clamp(ratio*spread*(1-error/(gate*2)),0,1);
  return {...fit,errors,inlierRatio:ratio,geometry:spread,degenerate:spread<.35,error,threshold:gate,confidence,weights};
}

// Translation-direction evidence from many identities after compensating a plausible shared rotation.
// Coordinates may be source units; this never labels a measured observer displacement.
export function directionalConsensus(pairs,rotation,scale,dt){
  if(pairs.length<12||!rotation||dt<=0)return [null,null];
  const gate=clamp(scale*.035,.25,1.3),radius=scale,axes=[0,2];
  return axes.map(axis=>{
    const samples=pairs.map(p=>({position:p.a,delta:p.b[axis]-apply(rotation,p.a)[axis]}));
    const center=median(samples.map(x=>x.delta)),sign=Math.sign(center);
    if(!sign||Math.abs(center)<Math.max(.08,scale*.005))return null;
    const agreeing=samples.filter(x=>Math.abs(x.delta-center)<=gate),vote=samples.filter(x=>x.delta*sign>Math.max(.05,Math.abs(center)*.2));
    const sectors=new Set(agreeing.map(x=>`${x.position[0]>=0?'R':'L'}${x.position[2]>=0?'B':'F'}${norm(x.position)>=radius?'n':'f'}`));
    const support=agreeing.length/samples.length,agreement=vote.length/samples.length;
    const gain=median(samples.map(x=>Math.abs(x.delta)))-median(samples.map(x=>Math.abs(x.delta-center)));
    if(agreeing.length<12||support<.45||agreement<.58||sectors.size<5||gain<Math.max(.05,scale*.002))return null;
    return {axis,value:center/dt,confidence:clamp((support+agreement)*.5*(sectors.size/8)*clamp(gain/gate,0,1),0,1),support,agreement,sectors:sectors.size};
  });
}

// Cross-check a global transform against four spatial cohorts, each with its own rigid fit.
// Three agreeing cohorts necessarily cover both horizontal sides and both range bands.
export function groupRigidConsensus(pairs,scale,gate,globalRotation){
  if(pairs.length<24||!globalRotation)return {valid:false,groups:0,confidence:0};
  const globalYaw=Math.atan2(globalRotation[2],globalRotation[8]);
  const bins=[[],[],[],[]];
  for(const p of pairs)bins[(p.a[0]>=0?1:0)+2*(norm(p.a)>=scale?1:0)].push(p);
  const estimates=[];
  for(let index=0;index<4;index++){
    if(bins[index].length<12)continue;
    const fit=robustRegister(bins[index],{metric:true,threshold:gate,iterations:24});
    if(!fit||fit.inlierRatio<.5||fit.geometry<.12||fit.error>gate*1.5)continue;
    const yaw=Math.atan2(fit.r[2],fit.r[8]),pitch=Math.asin(clamp(-fit.r[5],-1,1));
    if(Math.abs(Math.atan2(Math.sin(yaw-globalYaw),Math.cos(yaw-globalYaw)))>Math.max(.06,Math.abs(globalYaw)*.18))continue;
    estimates.push({index,yaw,pitch,ratio:fit.inlierRatio,size:bins[index].length});
  }
  if(estimates.length<3)return {valid:false,groups:estimates.length,confidence:0};
  const yaw=median(estimates.map(x=>x.yaw)),pitch=median(estimates.map(x=>x.pitch));
  const spread=Math.max(...estimates.map(x=>Math.abs(x.yaw-yaw)));
  if(spread>Math.max(.055,Math.abs(yaw)*.16))return {valid:false,groups:estimates.length,confidence:0};
  const meanRatio=estimates.reduce((s,x)=>s+x.ratio,0)/estimates.length;
  return {valid:true,groups:estimates.length,confidence:clamp(meanRatio*.75+.2-spread,0,1),yaw,pitch,spread};
}
