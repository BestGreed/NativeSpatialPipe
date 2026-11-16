// Minimum-twist relative motion from a sparse set of persistent landmarks.
// Rotation about the pair baseline is deliberately left unobserved.
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
const norm=v=>Math.hypot(...v);
const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const apply=(r,p)=>[0,3,6].map(i=>dot(r.slice(i,i+3),p));
export function sparseMotion(pairs,dt){
  if(pairs.length<2||pairs.length>5||dt<=0||dt>.25)return null;
  let first,second,length=0;
  for(let i=0;i<pairs.length;i++)for(let j=i+1;j<pairs.length;j++){
    const distance=norm(sub(pairs[i].a,pairs[j].a));
    if(distance>length){length=distance;first=pairs[i];second=pairs[j];}
  }
  if(length<.05)return null;
  const target=sub(second.b,first.b),nextLength=norm(target);
  if(Math.abs(nextLength-length)>length*.025)return null;
  const a=sub(second.a,first.a).map(v=>v/length),b=target.map(v=>v/nextLength);
  const axis=cross(a,b),sine=norm(axis),cosine=Math.max(-1,Math.min(1,dot(a,b))),angle=Math.atan2(sine,cosine);
  if(angle>Math.min(.35,dt*3))return null;
  const makeRotation=weight=>{
    if(sine<1e-8)return [1,0,0,0,1,0,0,0,1];
    const [x,y,z]=axis.map(v=>v/sine),c=Math.cos(angle*weight),s=Math.sin(angle*weight),k=1-c;
    return [c+x*x*k,x*y*k-z*s,x*z*k+y*s,y*x*k+z*s,c+y*y*k,y*z*k-x*s,z*x*k-y*s,z*y*k+x*s,c+z*z*k];
  };
  const raw=makeRotation(1),center=side=>[0,1,2].map(i=>pairs.reduce((sum,p)=>sum+p[side][i],0)/pairs.length);
  const ca=center('a'),cb=center('b'),t=sub(cb,apply(raw,ca));
  if(norm(t)>length*dt*2)return null;
  if(pairs.some(p=>norm(sub(sub(p.b,apply(raw,p.a)),t))>Math.max(.002,length*.015)))return null;
  const weight=.35,r=makeRotation(weight);
  return {r,t:t.map(v=>v*weight),rawVelocity:t.map(v=>v/dt/length),angularVelocity:axis.map(v=>sine>1e-8?v/sine*angle/dt:0),scale:length,keys:pairs.map(p=>p.key).sort().join('|')};
}
