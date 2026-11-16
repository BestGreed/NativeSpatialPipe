import {outputLayouts} from './layout-presets.js';
export {outputLayouts};
export const layoutFor=id=>outputLayouts.find(p=>p.id===id)??outputLayouts.find(p=>p.id==='7.1.4');
export const deviceChannels=value=>value.outputBackend==='wasapi'?['L','R']:layoutFor(value.outputLayout).channels;
// Pure form model: preserve existing optional/legacy fields and avoid coercing
// empty/invalid numeric entries into valid zero values.
export const channelLabels=['L','R','C','LFE','Ls','Rs','Lrs','Rrs','Ltf','Rtf','Ltr','Rtr'];
export const orders={olr:[0,1,2,3,4,5,6,7,8,9,10,11],'dvs-speakers':[0,2,1,5,4,7,6,3,10,8,9,11]};
export function getPath(object,path){return path.split('.').reduce((value,key)=>value?.[key],object);}
export function setPath(object,path,value){const keys=path.split('.');let target=object;for(const key of keys.slice(0,-1)){target[key]??={};target=target[key];}target[keys.at(-1)]=value;}
export function updateSourceSelection(value,backendChanged=false){
 const backend=value.outputBackend;
 if(backendChanged&&value.sourcePolicy!=='unified')value.sourcePolicy=backend==='aoip'?'active':'fixed';
 if((value.sourcePolicy==='unified'&&value.renderer==='lira')||(backend!=='aoip'&&value.sourcePolicy==='active'))value.sourcePolicy='fixed';
 if(value.sourcePolicy==='unified')value.sourceStream=0;
 else{
  delete value.unifiedSlotProfile;
  delete value.unifiedPrimaryPolicy;
 }
 if(value.pcmCapture?.routeToOutput&&(backend!=='wasapi'||value.sourcePolicy==='unified'))
  value.pcmCapture.routeToOutput=false;
 if(value.wasapiUpmix?.enabled&&(value.sourcePolicy!=='unified'||value.outputLayout!=='7.1.4'))
  value.wasapiUpmix.enabled=false;
 return value;
}
export function prepare(config,id){
 const value=structuredClone(config);
 if(id==='playback'){
  value.outputBackend??='asio';value.sourcePolicy??='fixed';value.aoip??={interfaceIp:'',masterIp:'',mac:'',name:'NSP Native AoIP',prebufferFrames:960,txLatencyUs:4000,framesPerPacket:32};value.aoip.name??='NSP Native AoIP';value.aoip.prebufferFrames??=960;value.aoip.txLatencyUs??=4000;value.aoip.framesPerPacket??=32;
  value.recording??={};value.recording.pcm??=false;value.recording.telemetrySnapshots??=false;value.recording.rawStreamStats??=false;
  if(value.notebookFilterFile&&!value.lsac){
   value.lsac={enabled:true,filterFile:value.notebookFilterFile,virtualBass:value.notebookVirtualBass,loudness:value.outputProcessing?.loudness};
   delete value.notebookFilterFile;delete value.notebookVirtualBass;
   if(value.outputProcessing)delete value.outputProcessing.loudness;
   if(value.outputLayout==='2.0')value.outputLayout='7.1.4';
  }
  if(value.lsac?.enabled){
   value.lsac.virtualBass??={};
   const bass=value.lsac.virtualBass;bass.enabled??=false;bass.amount??=0.18;bass.highpassHz??=130;
  }
  value.outputLayout??='7.1.4';const count=deviceChannels(value).length;
  if(value.renderer==='olr')value.renderer='roar';
  value.mode=value.renderer==='lira'?null:(value.mode??'olr');value.channelOrder??='olr';value.rendererDll??='';
  value.outputProcessing??={};value.outputProcessing.bypass??=false;
  value.outputProcessing.trim??=Array(count).fill(1);value.outputProcessing.delayFrames??=Array(count).fill(0);
  value.outputProcessing.limiter??={};
  for(const limiter of [value.outputProcessing.limiter,value.lsac?.limiter]){
   if(!limiter)continue;
   limiter.enabled??=false;limiter.ceiling??=1;limiter.releaseMs??=50;limiter.lookaheadFrames??=0;
  }
  value.routing??={};value.routing.physicalChannels??=Array.from({length:count},(_,i)=>i);
  value.sourceStream??=0;value.sampleRate??=48000;value.bufferFrames??=0;value.master??=1;value.clockSync??=true;value.prebufferFrames??=0;value.drainTimeoutMs??=5000;
 }
 if(value.roarCustomLayout?.measurementFile==='')value.roarCustomLayout.measurementFile=null;
 return value;
}
export function numeric(text,integer=false){if(text.trim()==='')throw Error('数值不能为空');const number=Number(text);if(!Number.isFinite(number)||(integer&&!Number.isInteger(number)))throw Error('请输入有效'+(integer?'整数':'数值'));return number;}
export function finish(value){
 if(value.sourcePolicy==='unified'&&value.pcmCapture)delete value.pcmCapture.inputEndpointId;
 if(value.outputBackend!=='aoip'&&!value.aoip?.interfaceIp&&!value.aoip?.masterIp&&!value.aoip?.mac)value.aoip=null;
 if(value.pcmCapture?.enabled===false){
  if(value.pcmCapture.routeToOutput)value.pcmCapture.routeToOutput=false;
  if(value.wasapiUpmix?.enabled)value.wasapiUpmix.enabled=false;
  if(value.browserIdentity?.enabled)value.browserIdentity.enabled=false;
  if(value.pcmCapture.browserIdentity?.enabled)value.pcmCapture.browserIdentity.enabled=false;
 }
 if(value.wasapiUpmix?.cpuFallbackDll==='')value.wasapiUpmix.cpuFallbackDll=null;
 if(value.renderer==='lira'){value.mode=null;delete value.positionMapping;delete value.roarCustomLayout;}else value.mode='olr';
 if(value.channelOrder!=='custom'&&value.routing)delete value.routing.sourceChannelIndices;
 if(value.roarCustomLayout?.measurementFile==='')value.roarCustomLayout.measurementFile=null;
 return value;
}

// Migrate only on an explicit layout choice; never silently reshape raw JSON.
export function migrateLayout(value,oldId,newId){
 const channels=id=>id==='2.0'?['L','R']:layoutFor(id).channels;
 const old=channels(oldId),next=channels(newId);
 const indices=value.channelOrder==='custom'?(value.routing?.sourceChannelIndices??old.map((_,i)=>i)):value.channelOrder==='dvs-speakers'?orders['dvs-speakers']:old.map((_,i)=>i);
 const devices=new Map(indices.map((source,slot)=>[old[source],value.routing?.physicalChannels?.[slot]??slot]));
 const used=new Set(next.filter(c=>devices.has(c)).map(c=>devices.get(c)));let available=0;
 const physical=next.map(c=>{if(devices.has(c))return devices.get(c);while(used.has(available))available++;used.add(available);return available++;});
 const trims=value.outputProcessing?.trim??old.map(()=>1),delays=value.outputProcessing?.delayFrames??old.map(()=>0);
 value.outputProcessing??={};value.outputProcessing.trim=next.map(c=>old.includes(c)?trims[old.indexOf(c)]:1);value.outputProcessing.delayFrames=next.map(c=>old.includes(c)?delays[old.indexOf(c)]:0);
 value.outputLayout=newId;value.channelOrder='olr';value.routing={physicalChannels:physical};return value;
}
