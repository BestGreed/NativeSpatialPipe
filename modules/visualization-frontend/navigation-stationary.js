const distance=(a,b)=>Math.hypot(...a.map((x,i)=>x-b[i]));
export class StationaryReferences{
  constructor(){this.reset();}
  reset(){this.tracks=new Map();this.locked=[];}
  update(pairs,timeUs,allowAcquire=true){
    // Latch the identities and anchors which established this lock. Changes
    // elsewhere in the scene cannot replace or invalidate those references.
    if(this.locked.length){
      const current=new Map(pairs.map(p=>[p.key,p]));
      const unchanged=this.locked.every(key=>{
        const p=current.get(key),track=this.tracks.get(key);
        return p&&track&&distance(track.anchor,p.b)<=Math.max(.001,Math.hypot(...track.anchor)*.0001);
      });
      if(unchanged)return [...this.locked];
      this.reset();
      // A changed lock reference must not be replaced by another pair in
      // this same observation. New references require a fresh stable window.
    }
    const next=new Map(),stable=[];
    for(const p of pairs){
      const radius=Math.hypot(...p.b),tolerance=Math.max(.001,radius*.0001),old=this.tracks.get(p.key);
      const retained=old&&distance(old.anchor,p.b)<=tolerance&&distance(p.a,p.b)<=tolerance;
      const track=retained?old:{anchor:[...p.b],since:timeUs};next.set(p.key,track);
      if(timeUs-track.since>=300000)stable.push(p);
    }
    this.tracks=next;
    if(!allowAcquire||pairs.length<2)return [];
    const radii=pairs.map(p=>Math.hypot(...p.b)),outer=Math.max(...radii),inner=Math.min(...radii);
    const threshold=outer*(outer>inner*2?.5:.75);
    const far=stable.filter(p=>Math.hypot(...p.b)>=threshold),distinct=[];
    for(const p of far)if(distinct.every(q=>distance(p.b,q.b)>Math.max(.05,outer*.02)))distinct.push(p);
    this.locked=distinct.length>=2?distinct.map(p=>p.key):[];
    return [...this.locked];
  }
}
