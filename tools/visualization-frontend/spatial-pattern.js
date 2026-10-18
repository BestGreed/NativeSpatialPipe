// Detect a measured depth arrangement, never an inferred projectile or sound path.
const length=p=>Math.hypot(...p);
const key=o=>`${o.objectKey.sessionId}:${o.objectKey.streamEpoch}:${o.objectKey.slot}:${o.objectKey.generation}`;
const empty=()=>({valid:false,kind:null,anchors:[],span:0,frames:0,sourceTimeUs:null});
export class SpatialPattern{
  constructor(){this.reset();}
  reset(){this.previous=null;this.streaks=new Map();this.result=empty();}
  ingest(snapshot){
    const tap=snapshot.taps?.['decoded-input'],scope=`${snapshot.routeId}:${snapshot.sessionId}:${snapshot.streamEpoch}:${snapshot.meterRevision}`;
    const previous=this.previous,continuous=previous&&previous.scope===scope&&snapshot.sourceTimeUs>previous.time&&snapshot.sourceTimeUs-previous.time<=250000&&previous.end===snapshot.sourceSequenceRange.start;
    if(!continuous)this.streaks.clear();
    this.previous={scope,time:snapshot.sourceTimeUs,end:snapshot.sourceSequenceRange.endExclusive};
    if(!tap||tap.quality!=='live'||!tap.snapshotComplete){this.streaks.clear();return this.result=empty();}
    const positioned=tap.objects.filter(o=>o.kind==='dynamic'&&o.spatial?.positionValid&&o.spatial.position?.every(Number.isFinite));
    const radii=positioned.map(o=>length(o.spatial.position)).filter(r=>r>1e-5).sort((a,b)=>a-b),scale=radii[Math.floor(radii.length/2)]||0;
    if(!scale)return this.result=empty();
    const objects=positioned.filter(o=>o.meter.rmsDbfs!==null&&o.meter.rmsDbfs>-52&&length(o.spatial.position)>scale*.017&&Math.abs(o.spatial.position[0])<scale*.17&&Math.abs(o.spatial.position[1])<scale*.17)
      .map(o=>({key:key(o),sourceIdent:o.sourceIdent,p:o.spatial.position,db:o.meter.rmsDbfs})).sort((a,b)=>a.p[2]-b.p[2]);
    const next=new Map();let best=null;
    for(let i=0;i<objects.length;i++)for(let k=i+2;k<objects.length;k++){
      const a=objects[i],c=objects[k],span=c.p[2]-a.p[2];
      if(span<scale*.55||span>scale*1.4||length(c.p)>scale*.1)continue;
      for(let j=i+1;j<k;j++){
        const b=objects[j],first=b.p[2]-a.p[2],second=c.p[2]-b.p[2];
        if(first<scale*.16||second<scale*.16||first/second<.55||first/second>1.8||new Set([a.sourceIdent,b.sourceIdent,c.sourceIdent]).size<3)continue;
        const t=first/span,error=Math.hypot(b.p[0]-a.p[0]-(c.p[0]-a.p[0])*t,b.p[1]-a.p[1]-(c.p[1]-a.p[1])*t);
        if(error>scale*.08)continue;
        const signature=`${a.key}|${b.key}|${c.key}`,prior=continuous?this.streaks.get(signature):null,frames=prior?.frames||0;
        const changes=[b.db-a.db,c.db-b.db],gradientDirection=changes.every(x=>x>=3)?'far-to-near-rising':changes.every(x=>x<=-3)?'far-to-near-falling':null;
        const gradientFrames=gradientDirection?(prior?.gradientDirection===gradientDirection?prior.gradientFrames+1:1):0;
        const candidate={valid:frames+1>=3,kind:'depth-chain',anchors:[a,b,c].map(x=>({key:x.key,position:[...x.p],db:x.db})),span,frames:frames+1,sourceTimeUs:snapshot.sourceTimeUs,
          gradientDirection,gradientFrames,gradientValid:gradientFrames>=3,score:Math.min(frames+1,8)*.25+(a.db+b.db+c.db+156)/90-error/scale};
        next.set(signature,candidate);
        if(candidate.valid&&(!best||candidate.score>best.score))best=candidate;
      }
    }
    this.streaks=next;
    return this.result=best||empty();
  }
}
