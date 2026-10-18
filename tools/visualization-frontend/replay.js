// Bounded, seekable replay. Chunk loading never creates an unbounded frame queue.
export class Replay {
  constructor(manifest,load,onFrame,onStatus=()=>{},now=()=>performance.now()){
    if(manifest.replayVersion!==1||manifest.frameRateHz!==10||!Number.isInteger(manifest.frameCount)||manifest.frameCount<1||manifest.frameCount>36000||manifest.framesPerChunk!==10||manifest.durationUs!==manifest.frameCount*100000||manifest.chunks.length!==Math.ceil(manifest.frameCount/10))throw Error('不支持的回放清单');
    if(!manifest.chunks.every((c,i)=>c.file===`chunk-${String(i).padStart(3,'0')}.json`&&c.firstFrame===i*10&&c.frames===Math.min(10,manifest.frameCount-i*10)&&c.bytes<4*1024*1024))throw Error('回放分块清单不一致');
    this.manifest=manifest;this.load=load;this.onFrame=onFrame;this.onStatus=onStatus;this.now=now;
    this.cache=new Map();this.pending=new Map();this.frame=-1;this.position=0;this.playing=false;this.version=0;this.busy=false;this.disposed=false;
  }
  async chunk(index){
    if(this.cache.has(index))return this.cache.get(index);
    if(this.pending.has(index))return this.pending.get(index);
    const pending=this.load(this.manifest.chunks[index].file).then(frames=>{
      if(!Array.isArray(frames)||frames.length!==this.manifest.chunks[index].frames||frames.some((f,i)=>f.telemetrySeq!==index*10+i))throw Error('回放分块缺帧或乱序');
      if(!this.disposed){this.cache.set(index,frames);this.trim(index);}return frames;
    }).finally(()=>this.pending.delete(index));
    this.pending.set(index,pending);return pending;
  }
  trim(index){while(this.cache.size>3){const farthest=[...this.cache.keys()].sort((a,b)=>Math.abs(b-index)-Math.abs(a-index))[0];this.cache.delete(farthest);}}
  prefetch(index){if(index+1<this.manifest.chunks.length&&!this.disposed)this.chunk(index+1).catch(()=>{});}
  async seek(seconds,resume=this.playing){
    const version=++this.version;this.playing=false;
    const position=Math.max(0,Math.min(this.manifest.durationUs/1e6,seconds));
    const index=Math.max(0,Math.min(this.manifest.frameCount-1,Math.ceil(position*10)-1)),chunkIndex=Math.floor(index/10);
    this.onStatus('buffering',position);
    const frames=await this.chunk(chunkIndex);
    const localIndex=index%10,previousFrames=frames.slice(Math.max(0,localIndex-2),localIndex);
    if(previousFrames.length<2&&chunkIndex>0){const earlier=await this.chunk(chunkIndex-1);previousFrames.unshift(...earlier.slice(-(2-previousFrames.length)));}
    if(version!==this.version||this.disposed)return;
    this.position=position;this.frame=index;this.onFrame(frames[localIndex],true,previousFrames);
    this.anchorPosition=position;this.anchorTime=this.now();this.playing=resume&&position<this.manifest.durationUs/1e6;
    this.onStatus(this.playing?'playing':'paused',position);this.prefetch(chunkIndex);
  }
  play(){if(this.disposed)return;if(this.position>=this.manifest.durationUs/1e6)return this.seek(0,true);this.anchorTime=this.now();this.anchorPosition=this.position;this.playing=true;this.onStatus('playing',this.position);}
  pause(){this.playing=false;this.version++;this.position=Math.max(0,(this.frame+1)/10);this.onStatus('paused',this.position);}
  async tick(now=this.now()){
    if(!this.playing||this.busy||this.disposed)return;
    this.busy=true;const version=this.version;
    try{
      const position=Math.max(0,Math.min(this.manifest.durationUs/1e6,this.anchorPosition+(now-this.anchorTime)/1000));
      const target=Math.max(0,Math.min(this.manifest.frameCount-1,Math.floor(position*10+1e-8)-1));
      for(let index=this.frame+1;index<=target;index++){
        const chunkIndex=Math.floor(index/10);
        if(!this.cache.has(chunkIndex))this.onStatus('buffering',this.position);
        const frames=await this.chunk(chunkIndex);
        if(version!==this.version||!this.playing||this.disposed)return;
        this.onFrame(frames[index%10],false);this.frame=index;this.prefetch(chunkIndex);
      }
      this.position=position;
      if(position>=this.manifest.durationUs/1e6){this.playing=false;this.onStatus('ended',position);}
      else this.onStatus('playing',position);
    }catch(e){if(version===this.version){this.playing=false;this.onStatus('error',this.position,e.message);}}
    finally{this.busy=false;}
  }
  dispose(){this.disposed=true;this.playing=false;this.version++;this.cache.clear();}
}
