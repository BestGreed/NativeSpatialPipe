import {outputLayouts} from './layout-presets.js';
export {outputLayouts};
export const layoutFor=id=>outputLayouts.find(p=>p.id===id)??outputLayouts.find(p=>p.id==='7.1.4');
// Pure form model: preserve existing optional/legacy fields and avoid coercing
// empty/invalid numeric entries into valid zero values.
export const channelLabels=['L','R','C','LFE','Ls','Rs','Lrs','Rrs','Ltf','Rtf','Ltr','Rtr'];
export const orders={olr:[0,1,2,3,4,5,6,7,8,9,10,11],'dvs-speakers':[0,2,1,5,4,7,6,3,10,8,9,11]};
export function getPath(object,path){return path.split('.').reduce((value,key)=>value?.[key],object);}
export function setPath(object,path,value){const keys=path.split('.');let target=object;for(const key of keys.slice(0,-1)){target[key]??={};target=target[key];}target[keys.at(-1)]=value;}
export function prepare(config,id){
 const value=structuredClone(config);
 if(id==='playback'){
  value.recording??={};value.recording.pcm??=false;value.recording.telemetrySnapshots??=false;
  value.outputLayout??='7.1.4';const count=layoutFor(value.outputLayout).channels.length;
  if(value.renderer==='olr')value.renderer='roar';
  value.mode=value.renderer==='lira'?null:(value.mode??'olr');value.channelOrder??='olr';value.rendererDll??='';
  value.outputProcessing??={};value.outputProcessing.bypass??=false;
  value.outputProcessing.trim??=Array(count).fill(1);value.outputProcessing.delayFrames??=Array(count).fill(0);
  value.outputProcessing.limiter??={};
  const limiter=value.outputProcessing.limiter;limiter.enabled??=false;limiter.ceiling??=1;limiter.releaseMs??=50;limiter.lookaheadFrames??=0;
  value.routing??={};value.routing.physicalChannels??=Array.from({length:count},(_,i)=>i);
  value.sourceStream??=0;value.sampleRate??=48000;value.bufferFrames??=0;value.master??=1;value.clockSync??=true;value.prebufferFrames??=0;value.drainTimeoutMs??=5000;
 }
 if(value.roarCustomLayout?.measurementFile==='')value.roarCustomLayout.measurementFile=null;
 return value;
}
export function numeric(text,integer=false){if(text.trim()==='')throw Error('数值不能为空');const number=Number(text);if(!Number.isFinite(number)||(integer&&!Number.isInteger(number)))throw Error('请输入有效'+(integer?'整数':'数值'));return number;}
export function finish(value){
 if(value.renderer==='lira'){value.mode=null;delete value.positionMapping;delete value.roarCustomLayout;}else value.mode='olr';
 if(value.channelOrder!=='custom'&&value.routing)delete value.routing.sourceChannelIndices;
 if(value.roarCustomLayout?.measurementFile==='')value.roarCustomLayout.measurementFile=null;
 return value;
}

// Migrate only on an explicit layout choice; never silently reshape raw JSON.
export function migrateLayout(value,oldId,newId){
 const old=layoutFor(oldId).channels,next=layoutFor(newId).channels;
 const indices=value.channelOrder==='custom'?(value.routing?.sourceChannelIndices??old.map((_,i)=>i)):value.channelOrder==='dvs-speakers'?orders['dvs-speakers']:old.map((_,i)=>i);
 const devices=new Map(indices.map((source,slot)=>[old[source],value.routing?.physicalChannels?.[slot]??slot]));
 const used=new Set(next.filter(c=>devices.has(c)).map(c=>devices.get(c)));let available=0;
 const physical=next.map(c=>{if(devices.has(c))return devices.get(c);while(used.has(available))available++;used.add(available);return available++;});
 const trims=value.outputProcessing?.trim??old.map(()=>1),delays=value.outputProcessing?.delayFrames??old.map(()=>0);
 value.outputProcessing??={};value.outputProcessing.trim=next.map(c=>old.includes(c)?trims[old.indexOf(c)]:1);value.outputProcessing.delayFrames=next.map(c=>old.includes(c)?delays[old.indexOf(c)]:0);
 value.outputLayout=newId;value.channelOrder='olr';value.routing={physicalChannels:physical};return value;
}
