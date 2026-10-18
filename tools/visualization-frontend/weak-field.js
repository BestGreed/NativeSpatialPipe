// Activity phenotype only: source coordinates are uncalibrated and do not imply distance in metres.
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));

export function updateWeakField(previous,object,intervalMs,continuous,scale,transient=false){
  const p=object.spatial.position,db=object.meter.rmsDbfs;
  const radius=object.spatial.positionValid&&p?Math.hypot(...p):0;
  const outer=scale>0?clamp((radius/scale-.62)/.68):0;
  // A transient halo temporarily owns this source's visual presentation.
  if(transient)return {dwell:0,active:false,mix:0,outer};
  const prior=continuous?previous?.weakField:{dwell:0,mix:0,active:false};
  const crest=Number.isFinite(object.meter.peakDbfs)&&Number.isFinite(db)?object.meter.peakDbfs-db:null;
  const audible=Number.isFinite(db)&&db>=-60&&db<=-37;
  const retained=Number.isFinite(db)&&db>=-64&&db<=-33;
  const eligible=radius>0&&(crest===null||crest<=19)&&(prior?.active?retained:audible);
  const dwell=eligible?Math.min(2000,(prior?.dwell||0)+intervalMs):0;
  const active=eligible&&dwell>=650;
  const target=active?(.35+.65*outer):0;
  const mix=(prior?.mix||0)+(target-(prior?.mix||0))*(1-Math.exp(-intervalMs/(target>0?230:420)));
  return {dwell,active,mix,outer};
}

// Pick a few seeds; local edges are chosen separately from the seed geometry.
export function selectWeakAnchors(candidates,maxAnchors=20){
  const anchors=[];
  for(const c of [...candidates].sort((a,b)=>b.mix-a.mix)){
    if(anchors.length>=maxAnchors)break;
    if(anchors.some(a=>Math.hypot(...a.p.map((x,i)=>x-c.p[i]))<.18))continue;
    anchors.push(c);
  }
  return anchors;
}

// Nearby, similarly sustained seeds may form a local network. Distance orders
// the proposals, while degree limits prevent a dense constellation graph.
export function buildWeakNetwork(anchors,maxEdges=20){
  const proposals=[],degree=new Array(anchors.length).fill(0),edges=[];
  for(let i=0;i<anchors.length;i++)for(let j=i+1;j<anchors.length;j++){
    const a=anchors[i],b=anchors[j],distance=length(a.p.map((x,k)=>x-b.p[k]));
    if(distance<.16||distance>.86||Math.min(a.mix,b.mix)<.18)continue;
    const activity=clamp(1-Math.abs((a.dwell||0)-(b.dwell||0))/1800);
    if(activity<.45)continue;
    proposals.push({i,j,distance,score:distance/(.7+.3*activity)});
  }
  proposals.sort((a,b)=>a.score-b.score);
  for(const p of proposals){
    if(edges.length>=maxEdges)break;
    if(degree[p.i]>=3||degree[p.j]>=3)continue;
    edges.push([p.i,p.j]);degree[p.i]++;degree[p.j]++;
  }
  return edges;
}

// Group neighboring edges by graph distance so small patches illuminate in
// sequence instead of every segment blinking independently.
export function weakNetworkBlocks(anchors,edges){
  const adjacency=anchors.map(()=>[]),depth=new Array(anchors.length).fill(-1),component=new Array(anchors.length).fill(-1),visited=new Set();
  for(const [i,j] of edges){adjacency[i].push(j);adjacency[j].push(i);}
  for(let root=0;root<anchors.length;root++){
    if(visited.has(root)||!adjacency[root].length)continue;
    const group=[root];visited.add(root);
    for(let head=0;head<group.length;head++)for(const next of adjacency[group[head]])if(!visited.has(next)){visited.add(next);group.push(next);}
    const start=group.reduce((best,i)=>(anchors[i].slot||0)<(anchors[best].slot||0)?i:best,group[0]);
    const queue=[start];depth[start]=0;component[start]=anchors[start].slot||0;
    for(let head=0;head<queue.length;head++)for(const next of adjacency[queue[head]]){
      if(depth[next]>=0)continue;
      depth[next]=depth[queue[head]]+1;component[next]=component[start];queue.push(next);
    }
  }
  return edges.map(([i,j])=>({i,j,block:Math.floor(Math.min(depth[i],depth[j])/2),component:component[i]}));
}

export function weakBlockFlash(block,component,time){
  const wave=Math.sin(time*1.65-block*.78-(component%7)*.91);
  return Math.pow(Math.max(0,wave),8);
}

// The resting bridge is lit at its two source ends. A narrow travelling pulse
// may briefly illuminate the otherwise barely visible middle.
export function weakConnectionBrightness(t,flash=0,pulseAt=null){
  t=clamp(t);flash=clamp(flash);
  const ends=Math.pow(Math.abs(2*t-1),1.5);
  const resting=.065+.40*ends+.22*flash*ends;
  const distance=pulseAt===null?Infinity:Math.abs(t-clamp(pulseAt));
  const pulse=.56*Math.exp(-Math.pow(distance/.065,2));
  return clamp(resting+pulse);
}

// Every free filament segment fades by distance from its measured source,
// including laterals that start partway along a trunk.
export function weakFilamentBrightness(distance,{outward=false,branch=false}={}){
  const start=outward?.28:branch?.24:.42;
  return start*Math.exp(-Math.max(0,distance)*(outward?6:3.8));
}

// Six visual response channels: translation in display space and angular rate.
// Missing or untrusted axes are supplied as zero; this is not a measured 6DoF pose.
export class WeakInertia {
  constructor(){this.reset();}
  reset(){this.translation=[0,0,0];this.rotation=[0,0,0];}
  step(flow,angular,dt,{enabled=true}={}){
    dt=clamp(dt,0,.1);
    const gain=1-Math.exp(-dt/.19);
    for(let i=0;i<3;i++){
      const f=enabled&&Number.isFinite(flow?.[i])?clamp(flow[i],-2,2):0;
      const r=enabled&&Number.isFinite(angular?.[i])?clamp(angular[i],-1,1):0;
      this.translation[i]+=(f*.015-this.translation[i])*gain;
      this.rotation[i]+=(r*.026-this.rotation[i])*gain;
    }
    return this;
  }
}

const length=v=>Math.hypot(...v);
const unit=v=>v.map(x=>x/(length(v)||1));
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const hash=x=>{const v=Math.sin(x*127.1+78.233)*43758.5453;return v-Math.floor(v);};

// Three long filaments bend inward; mature peripheral seeds add one or two
// shorter outward feelers. Endpoints are visual gestures, not source positions.
export function growWeakFilaments(origin,seed,reach,phase=0){
  if(!origin?.every(Number.isFinite)||length(origin)<1e-6||reach<=0)return [];
  reach=clamp(reach,0,1.18);
  const radial=unit(origin),reference=Math.abs(radial[1])<.88?[0,1,0]:[1,0,0];
  const u=unit(cross(radial,reference)),v=cross(radial,u),paths=[];
  const room=clamp((length(origin)-.18)/.55,.08,1),trunks=3;
  for(let trunk=0;trunk<trunks;trunk++){
    const angle=seed*2.39996+trunk*(1.68+.67*hash(seed*13+trunk*7));
    const tangent=u.map((x,i)=>x*Math.cos(angle)+v[i]*Math.sin(angle));
    const heading=unit(tangent.map((x,i)=>x-radial[i]*(.38+.12*hash(seed*19+trunk))));
    const side=unit(cross(radial,heading)),lengthScale=(.36+.24*hash(seed*11+trunk*17))*room;
    const point=t=>origin.map((x,i)=>x+heading[i]*lengthScale*t+
      side[i]*(.055*Math.sin(Math.PI*t)+.025*Math.sin(t*9+seed*.7+phase*.35)*t)-
      radial[i]*room*(.11*t*t+.014*Math.sin(t*7+trunk+phase*.25)*t));
    const count=Math.max(1,Math.ceil(16*reach)),points=[];
    for(let step=0;step<=count;step++)points.push(point(reach*step/count));
    paths.push({points,branch:false,trunk});
    for(let branch=0;branch<(hash(seed*31+trunk*13)>.35?1:0);branch++){
      const sprout=.43+branch*.18;
      if(reach<=sprout)continue;
      const branchReach=clamp((reach-sprout)/(.57-branch*.1)),start=point(sprout);
      const branchDirection=unit(heading.map((x,i)=>x*.3+side[i]*(branch?-.75:.75)-radial[i]*.2));
      const branchCount=Math.max(1,Math.ceil(8*branchReach)),twigs=[];
      for(let step=0;step<=branchCount;step++){
        const t=branchReach*step/branchCount;
        twigs.push(start.map((x,i)=>x+branchDirection[i]*lengthScale*.4*t-
          radial[i]*room*.025*t*t+side[i]*.018*Math.sin(t*6+phase*.3+trunk)*t));
      }
      paths.push({points:twigs,branch:true,trunk});
    }
  }
  if(room>.4&&reach>.3){
    const feelers=1+(hash(seed*47+9)>.55?1:0);
    for(let feeler=0;feeler<feelers;feeler++){
      const outReach=clamp((reach-.3-feeler*.14)/(.7-feeler*.14));
      if(outReach<=0)continue;
      const angle=seed*1.817+hash(seed*23)*1.4+feeler*2.35;
      const tangent=u.map((x,i)=>x*Math.cos(angle)+v[i]*Math.sin(angle));
      const heading=unit(radial.map((x,i)=>x*.77+tangent[i]*.63));
      const side=unit(cross(radial,heading)),span=(.19+.08*hash(seed*29+feeler*11+3))*room;
      const count=Math.max(1,Math.ceil(11*outReach)),points=[];
      for(let step=0;step<=count;step++){
        const t=outReach*step/count;
        points.push(origin.map((x,i)=>x+heading[i]*span*t+
          side[i]*(.032*Math.sin(Math.PI*t)+.014*Math.sin(t*7+seed+phase*.27)*t)-radial[i]*.018*t*t));
      }
      paths.push({points,branch:false,outward:true,trunk:trunks+feeler});
    }
  }
  return paths;
}

// A connection advances from both seeds along one stable, gently curved local
// route and only becomes a continuous bridge when the two fronts meet.
export function exploreWeakConnection(a,b,seed,reach,phase=0){
  if(!a?.every(Number.isFinite)||!b?.every(Number.isFinite)||reach<=0)return {left:[],right:[],connected:false,points:[]};
  const delta=b.map((x,i)=>x-a[i]),distance=length(delta);
  if(distance<1e-6)return {left:[],right:[],connected:false,points:[]};
  const forward=unit(delta),radial=unit(a.map((x,i)=>x+b[i]));
  const reference=length(cross(forward,radial))>.12?radial:Math.abs(forward[1])<.85?[0,1,0]:[1,0,0];
  const side=unit(cross(forward,reference)),up=unit(cross(forward,side));
  const bow=Math.min(.19,distance*(.24+.08*hash(seed))),steps=18,points=[];
  for(let i=0;i<=steps;i++){
    const t=i/steps,curve=Math.sin(Math.PI*t);
    points.push(a.map((x,k)=>x+delta[k]*t+side[k]*bow*.65*curve-
      radial[k]*bow*.55*curve+up[k]*bow*.32*Math.sin(2*Math.PI*t)+side[k]*.012*Math.sin(phase*.28+seed)*curve));
  }
  const grown=Math.min(steps/2,Math.max(1,Math.ceil(steps*.5*clamp(reach))));
  return {left:points.slice(0,grown+1),right:points.slice(steps-grown),connected:grown*2>=steps,points};
}

// Deform only the visual network, never the measured object coordinates. The
// field is shared in world space so neighboring strands swim together; its
// influence falls to zero at every source endpoint.
export function swimWeakPoint(point,roots,time,outer,maturity=1,inertia=null){
  if(!point?.every(Number.isFinite)||!roots?.length||!Number.isFinite(time))return point;
  const peripheral=clamp((outer-.45)/.4),ready=clamp(maturity);
  if(peripheral<=0||ready<=0)return point;
  let nearest=Infinity,origin=null;
  for(const root of roots){
    const distance=Math.hypot(point[0]-root[0],point[1]-root[1],point[2]-root[2]);
    if(distance<nearest){nearest=distance;origin=root;}
  }
  const amplitude=.052*peripheral*ready*clamp(nearest/.22);
  if(amplitude<=1e-8)return point;
  const [x,y,z]=point,phase=time*.52;
  const flow=[Math.sin(phase+y*1.8+z*1.1),
    Math.sin(phase*.76+z*1.6-x*1.2+1.4),
    Math.sin(phase*.87+x*1.5+y*1.3+2.1)];
  if(!inertia)return [x+flow[0]*amplitude,y+flow[1]*amplitude,z+flow[2]*amplitude];
  const weight=peripheral*ready*clamp(nearest/.22),[rx,ry,rz]=inertia.rotation;
  const dx=x-origin[0],dy=y-origin[1],dz=z-origin[2];
  return [x+flow[0]*amplitude+weight*(inertia.translation[0]+ry*dz-rz*dy),
    y+flow[1]*amplitude+weight*(inertia.translation[1]+rz*dx-rx*dz),
    z+flow[2]*amplitude+weight*(inertia.translation[2]+rx*dy-ry*dx)];
}

export function createWeakEcho({p,from,intervalMs,weakField,slot,startUs}){
  if(!weakField?.active||weakField.outer<.5||!p?.every(Number.isFinite)||length(p)<1e-6)return null;
  const motion=from?.every(Number.isFinite)&&intervalMs>0?p.map((x,i)=>(x-from[i])*.5/(intervalMs/1000)):[0,0,0];
  const cap=Math.min(1,.32/(length(motion)||1));
  return {p:[...p],motion:motion.map(x=>x*cap),mix:weakField.mix,outer:weakField.outer,slot,startUs};
}

export function sampleWeakEcho(echo,nowUs){
  const age=(nowUs-echo.startUs)/1e6;
  if(age<0||age>=1)return null;
  const lead=clamp(age/.5),collapse=clamp((age-.5)/.5);
  return {p:echo.p.map((x,i)=>x+echo.motion[i]*lead),mix:echo.mix*(1-collapse)*(1-collapse),
    outer:echo.outer,dwell:2000,slot:echo.slot,reach:1+.16*lead-(1.16-.12)*collapse,echo:true};
}

// A short, tapered patch follows two connected routes from the same measured
// node. Reject wide or folded wedges instead of filling arbitrary edge pairs.
export function weakMembraneBetween(pathA,pathB,outer,{
  extent=.55,steps=6,minCos=.4,maxCos=.96,maxWidth=.32,maxDepth=.16,sag=.018,opacityScale=1
}={}){
  if(outer<.5||pathA?.length<8||pathB?.length<8)return [];
  const anchor=pathA[0];
  if(length(anchor.map((x,k)=>x-pathB[0][k]))>.015)return [];
  const strength=clamp((outer-.5)/.45),triangles=[];
  const edge=(path,i)=>path[Math.round(i*(path.length-1)*extent/steps)];
  const sample=Math.round(steps*.75),va=edge(pathA,sample).map((x,k)=>x-anchor[k]),vb=edge(pathB,sample).map((x,k)=>x-anchor[k]);
  const cosine=va.reduce((s,x,k)=>s+x*vb[k],0)/Math.max(1e-9,length(va)*length(vb));
  if(cosine<minCos||cosine>maxCos||Math.abs(length(edge(pathA,steps))-length(edge(pathB,steps)))>maxDepth)return [];
  let normal=null;
  for(let i=1;i<=steps;i++){
    const a=edge(pathA,i).map((x,k)=>x-anchor[k]),b=edge(pathB,i).map((x,k)=>x-anchor[k]);
    if(length(a.map((x,k)=>x-b[k]))>maxWidth)return [];
    const next=unit(cross(a,b));
    if(length(next)<.5||normal&&normal.reduce((s,x,k)=>s+x*next[k],0)<.35)return [];
    normal=next;
  }
  const radial=unit(anchor);
  const middle=(a,b,t)=>a.map((x,k)=>(x+b[k])*.5-radial[k]*sag*strength*Math.sin(Math.PI*t));
  for(let i=0;i<steps;i++){
    const t0=i/steps,t1=(i+1)/steps,a0=edge(pathA,i),a1=edge(pathA,i+1),b0=edge(pathB,i),b1=edge(pathB,i+1);
    const width0=length(a0.map((x,k)=>x-b0[k])),width1=length(a1.map((x,k)=>x-b1[k]));
    const m0=middle(a0,b0,t0),m1=middle(a1,b1,t1);
    const opacity=(t,width)=>opacityScale*(.025+.055*strength)*Math.sin(Math.PI*t)*clamp((maxWidth-width)/(maxWidth*.375));
    const fade0=opacity(t0,width0),fade1=opacity(t1,width1);
    const faces=[[[a0,fade0*.45],[m0,fade0],[a1,fade1*.45]],
      [[a1,fade1*.45],[m0,fade0],[m1,fade1]],
      [[m0,fade0],[b0,fade0*.45],[m1,fade1]],
      [[m1,fade1],[b0,fade0*.45],[b1,fade1*.45]]];
    for(const face of faces){
      const u=face[1][0].map((x,k)=>x-face[0][0][k]),v=face[2][0].map((x,k)=>x-face[0][0][k]);
      if(length(cross(u,v))>1e-6)triangles.push(face.map(([p,alpha])=>({p,alpha})));
    }
  }
  return triangles;
}

// A mature source may grow a compact fan from its measured core between two
// of its own main filaments, regardless of surrounding network size.
export function weakMembraneFromFilaments(paths,outer,growth=1){
  if(outer<.5||!paths?.length||growth<=0)return [];
  growth=clamp(growth);
  const trunks=paths.filter(path=>!path.branch&&!path.outward),triangles=[];
  for(let i=0;i<trunks.length;i++)for(let j=i+1;j<trunks.length;j++){
    const extent=.12+.20*growth;
    const steps=Math.min(5,Math.max(2,Math.floor((Math.min(trunks[i].points.length,trunks[j].points.length)-1)*extent)));
    const sheet=weakMembraneBetween(trunks[i].points,trunks[j].points,outer,{
      extent,steps,minCos:-.55,maxCos:.7,maxWidth:.24,maxDepth:.1,sag:.008,opacityScale:.7*growth
    });
    if(!sheet.length)continue;
    triangles.push(...sheet);
    return triangles;
  }
  return triangles;
}
