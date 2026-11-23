import {outputLayouts} from './layout-presets.js';
export {outputLayouts};
export const layoutFor=id=>outputLayouts.find(p=>p.id===id)??outputLayouts.find(p=>p.id==='7.1.4');
// ASIO, native AoIP and AES67 are transport-only: the channel count and order
// always follow outputLayout. WASAPI is the only stereo device path.
export const deviceChannels=value=>value.outputBackend==='wasapi'?['L','R']:layoutFor(value.outputLayout).channels;
// AES67 RTP/L24 transport defaults, matching modules/zig-scheduler/aes67_config.zig.
// E16 flows-only: destinations live exclusively in aes67.flows; the legacy
// top-level destIp/port mirror is gone from the model and from saved files.
export const aes67Defaults={framesPerPacket:48,payloadType:96,ssrc:0,srcInterfaceIp:'',ptpGmid:'',ptpDomain:0,ringPackets:64,prebufferFrames:480,ttl:32};
// One AES67 RTP flow carries at most 8 L24 channels (48 frames x 8 ch x 3 B
// = 1152 B <= the 1440 B RFC 3550 budget). The backend accepts 1-8 explicit
// flows whose contiguous channel offsets sum to the layout channel count; the
// panel proposes the minimal ceil(channels/8) split. Destinations live only in
// playback.json (aes67.flows), addresses are never derived at runtime, and the
// removed top-level destIp/port mirror is rejected as a legacy format.
export const aes67MaxFlowChannels=8;
// Optional wire-order permutation example: the 9.1.6 renderer order mapped onto
// the 16ch container order. Transmitted channel k carries source channel
// channelMap[k]; without a map the renderer source order is used.
export const aes67StandardChannelMap916=[6,7,2,3,8,9,4,5,0,1,10,11,14,15,12,13];
// RTP layer default SSRC, matching aes67_config.zig default_ssrc (NSP6).
export const aes67DefaultSsrc=0x4E535036;
// Per-flow SSRC: a flow ssrc of 0/absent means "derived at runtime as
// base + flow index" (base = top-level ssrc, default 0x4E535036). The UI
// accepts 0 to preserve runtime derivation and previews the effective value.
export function effectiveAes67FlowSsrc(aes,index){
 const explicit=Number(aes?.flows?.[index]?.ssrc);
 if(Number.isInteger(explicit)&&explicit>0)return explicit;
 const base=Number.isInteger(Number(aes?.ssrc))&&Number(aes.ssrc)>0?Number(aes.ssrc):aes67DefaultSsrc;
 return (base+index)>>>0;
}
// SSRC is stored as a 32-bit integer (the flows[] JSON storage format) but
// every human-facing surface shows hexadecimal; input accepts both.
export function formatAes67Ssrc(value){return '0x'+(Number(value)>>>0).toString(16).toUpperCase();}
export function parseAes67Ssrc(text){
 const value=String(text??'').trim().toLowerCase();
 if(value==='')return null;
 if(/^0x[0-9a-f]+$/.test(value))return parseInt(value,16);
 if(/^-?[0-9]+$/.test(value))return Number(value);
 if(/^[0-9a-f]{1,8}$/.test(value))return parseInt(value,16)>>>0;
 return NaN;
}
// Pure form model: preserve existing optional/legacy fields and avoid coercing
// empty/invalid numeric entries into valid zero values.
export const channelLabels=['L','R','C','LFE','Ls','Rs','Lrs','Rrs','Ltf','Rtf','Ltr','Rtr'];
export const orders={olr:[0,1,2,3,4,5,6,7,8,9,10,11],'dvs-speakers':[0,2,1,5,4,7,6,3,10,8,9,11]};
export function getPath(object,path){return path.split('.').reduce((value,key)=>value?.[key],object);}
export function setPath(object,path,value){const keys=path.split('.');let target=object;for(const key of keys.slice(0,-1)){target[key]??={};target=target[key];}target[keys.at(-1)]=value;}
export const supportsActiveSource=backend=>backend==='aoip';
function normalizeSourcePolicy(value){
 value.sourcePolicy??=value.outputBackend==='aoip'?'active':'fixed';
 if(value.sourcePolicy==='active'&&!supportsActiveSource(value.outputBackend))value.sourcePolicy='fixed';
}
export function updateSourceSelection(value,previousBackend){
 const backend=value.outputBackend;
 // Native AoIP defaults to active when switching from a device backend.
 if(previousBackend&&previousBackend!==backend&&previousBackend!=='aes67'&&!supportsActiveSource(previousBackend)&&
    backend==='aoip'&&value.sourcePolicy==='fixed')value.sourcePolicy='active';
 normalizeSourcePolicy(value);
 if(value.sourcePolicy==='unified'&&value.renderer==='lira')value.sourcePolicy='fixed';
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
  value.outputBackend??='asio';normalizeSourcePolicy(value);value.aoip??={interfaceIp:'',masterIp:'',mac:'',name:'NSP Native AoIP',prebufferFrames:960,txLatencyUs:4000,framesPerPacket:32};value.aoip.name??='NSP Native AoIP';value.aoip.prebufferFrames??=960;value.aoip.txLatencyUs??=4000;value.aoip.framesPerPacket??=32;
  // The scheduler validates the AES67 block even while another backend is
  // selected, so normalise it in place instead of dropping or recreating it.
  if(value.aes67&&typeof value.aes67==='object')value.aes67={...aes67Defaults,...value.aes67};
  else if(value.outputBackend==='aes67')value.aes67={...aes67Defaults,flows:proposeAes67Flows(layoutFor(value.outputLayout).channels.length)};
  if(value.aes67?.ptp==null&&value.aes67)value.aes67.ptp={domain:value.aes67.ptpDomain,lockTimeoutMs:5000,timestamping:'auto'};
  else if(value.aes67?.ptp&&typeof value.aes67.ptp==='object'&&!Array.isArray(value.aes67.ptp))value.aes67.ptp={domain:0,lockTimeoutMs:5000,timestamping:'auto',...value.aes67.ptp};
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
 // Never wipe a saved AES67 block when switching backends: it is network
 // transport state for the next AES67 session. Drop only the editor-created
 // placeholder (empty flow-1 destination) the user never filled in.
 if(value.outputBackend!=='aes67'&&Array.isArray(value.aes67?.flows)&&value.aes67.flows.length>0&&
    !Object.hasOwn(value.aes67,'destIp')&&!Object.hasOwn(value.aes67,'port')&&
    value.aes67.flows.every(flow=>flow?.destIp===''))delete value.aes67;
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

// ---- AES67 transport validation (mirrors modules/zig-scheduler/aes67_config.zig) ----
function parseIpv4(text){
 const match=/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(text??'').trim());
 if(!match)return null;
 const parts=match.slice(1).map(Number);
 return parts.every(part=>part<=255)?parts:null;
}
const unicastIpv4=text=>{const parts=parseIpv4(text);return parts!==null&&parts[0]!==0&&parts[0]!==255&&parts[0]<224;};
export const isIpv4=text=>parseIpv4(text)!==null;
export const isPtpGmid=text=>{const value=String(text??'').trim();return value===''||/^[0-9a-fA-F]{2}(-[0-9a-fA-F]{2}){7}$/.test(value);};
// Destination rules mirror aes67_config.zig resolveDestination: class D
// multicast groups (224.0.0.0-239.255.255.255) and ordinary unicast hosts
// (first octet 1-223) are accepted; 0.x, 255.x and reserved 240-255.x are
// rejected by the scheduler before a socket exists.
const multicastIpv4=text=>{const parts=parseIpv4(text);return parts!==null&&parts[0]>=224&&parts[0]<=239;};
export const isAes67Destination=text=>{const parts=parseIpv4(text);return parts!==null&&parts[0]>=1&&parts[0]<=239;};
// Last-octet increment with carry into the higher octets; null when the
// address would leave the valid IPv4 range.
function incrementIpv4(parts,step){
 const octets=parts.slice();let carry=step;
 for(let index=3;index>=1&&carry>0;index--){const value=octets[index]+carry;octets[index]=value&255;carry=value>>8;}
 return carry>0?null:octets;
}
// A fresh form or explicit layout change proposes the minimum split.
export function proposeAes67Flows(channels,first={}){
 return Array.from({length:Math.ceil(channels/aes67MaxFlowChannels)},(_,index)=>({
  ...(index===0?first:{}),
  destIp:index===0?(first.destIp??''):'',
  channels:Math.min(aes67MaxFlowChannels,channels-index*aes67MaxFlowChannels),
  channelOffset:index*aes67MaxFlowChannels,
 }));
}

// Match web_host.zig: preserve explicit values, derive missing destinations
// from flow 1, default multicast ports to 5004 and increment unicast ports by 2.
export function materializeAes67Destinations(aes){
 if(!Array.isArray(aes?.flows)||aes.flows.length===0||aes.flows.length>8)return;
 if(Object.hasOwn(aes,'destIp')||Object.hasOwn(aes,'port'))return;
 const first=aes.flows[0];
 if(typeof first?.destIp!=='string'||!isAes67Destination(first.destIp))return;
 const base=parseIpv4(first.destIp),basePort=first.port??5004;
 if(!intRange(basePort,1,65535))return;
 const multicast=multicastIpv4(first.destIp);
 aes.flows.forEach((flow,index)=>{
  if(!flow||typeof flow!=='object')return;
  if(flow.destIp==null||flow.destIp===''){
   const next=multicast?incrementIpv4(base,index):base;
   if(next)flow.destIp=next.join('.');
  }
  const flowMulticast=multicastIpv4(flow.destIp);
  flow.port??=flowMulticast?5004:basePort+2*index;
  if(flowMulticast&&(flow.ttl==null||flow.ttl===0))flow.ttl=aes.ttl??32;
  if(!flow.srcInterfaceIp&&aes.srcInterfaceIp)flow.srcInterfaceIp=aes.srcInterfaceIp;
 });
}
export function deriveAes67Flows(aes,channels){
 const first=aes?.flows?.[0];
 if(!first||!isAes67Destination(first.destIp))return null;
 const proposal={...aes,flows:proposeAes67Flows(channels,first)};
 materializeAes67Destinations(proposal);
 return proposal.flows.every(flow=>isAes67Destination(flow.destIp)&&intRange(flow.port,1,65535))?proposal.flows:null;
}
const intRange=(value,min,max)=>Number.isInteger(value)&&value>=min&&value<=max;
export function aes67BlockProblems(aes,channels){
 const problems=[];
 // E16 flows-only: destinations are validated per flow; a block without
 // any flow row is simply incomplete.
 const flows=Array.isArray(aes?.flows)?aes.flows:[];
 for(const key of ['destIp','port'])if(Object.hasOwn(aes,key))problems.push({path:'aes67.'+key,message:'旧版 AES67 目标字段 '+key+' 已移除。请用高级 JSON 删除该字段并将目标写入 aes67.flows。'});
 if(flows.length>8)problems.push({path:'aes67.flows',message:'AES67 最多支持 8 条流'});
 if(!flows.length)problems.push({path:'aes67.flows.0.destIp',message:'AES67 后端需要完整的 aes67 网络配置'});
 if(aes?.framesPerPacket!==48)problems.push({path:'aes67.framesPerPacket',message:'AES67 每包帧数当前只支持 48（1 ms）'});
 if(!intRange(aes?.payloadType,96,127))problems.push({path:'aes67.payloadType',message:'AES67 payloadType 必须在 96–127'});
 if(!intRange(aes?.ssrc,0,4294967295))problems.push({path:'aes67.ssrc',message:'AES67 SSRC 必须在 0–4294967295'});
 if(String(aes?.srcInterfaceIp??'')!==''&&!unicastIpv4(aes?.srcInterfaceIp))problems.push({path:'aes67.srcInterfaceIp',message:'AES67 源网卡 IP 必须留空或为有效的单播 IPv4'});
 if(String(aes?.ptpGmid??'').trim()!=='')problems.push({path:'aes67.ptpGmid',message:'AES67 PTP GMID 必须留空，由模块从锁定的时钟取得'});
 if(!intRange(aes?.ptpDomain,0,127))problems.push({path:'aes67.ptpDomain',message:'AES67 PTP domain 必须在 0–127'});
 if(aes?.ptp!=null){
  const ptp=aes.ptp;
  if(typeof ptp!=='object'||Array.isArray(ptp))problems.push({path:'aes67.ptp',message:'AES67 PTP 配置必须是对象'});
  else{
   if(Object.hasOwn(ptp,'domain')&&!intRange(ptp.domain,0,127))problems.push({path:'aes67.ptp.domain',message:'AES67 PTP domain 必须在 0–127'});
   if(Object.hasOwn(ptp,'lockTimeoutMs')&&!intRange(ptp.lockTimeoutMs,100,120000))problems.push({path:'aes67.ptp.lockTimeoutMs',message:'AES67 PTP 锁定超时必须在 100–120000 ms'});
   if(Object.hasOwn(ptp,'timestamping')&&!['auto','software','hardware'].includes(ptp.timestamping))problems.push({path:'aes67.ptp.timestamping',message:'AES67 PTP 时间戳模式必须是 auto、software 或 hardware'});
  }
 }
 if(!intRange(aes?.ringPackets,8,1024))problems.push({path:'aes67.ringPackets',message:'AES67 环形缓冲包数必须在 8–1024'});
 if(!intRange(aes?.prebufferFrames,48,48000)||(intRange(aes?.ringPackets,8,1024)&&intRange(aes?.prebufferFrames,0,1e9)&&aes.prebufferFrames>aes.ringPackets*48))problems.push({path:'aes67.prebufferFrames',message:'AES67 预缓冲必须在 48–48000 帧且不超过 ringPackets × 48'});
 if(!intRange(aes?.ttl,1,255))problems.push({path:'aes67.ttl',message:'AES67 组播 TTL 必须在 1–255'});
 // Multi-flow rows are validated like the backend does: contiguous channel
 // coverage of the layout, per-flow destination/port ranges and no duplicate
 // (address,port) pair. A multicast destination requires srcInterfaceIp.
 if(flows.length&&Number.isInteger(channels)&&flows.reduce((total,flow)=>total+Number(flow?.channels??0),0)!==channels)problems.push({path:'aes67.flows',message:'AES67 多流声道总数必须等于布局声道数'});
 let offset=0;
 const seen=new Set();
 const seenGroups=new Set();
 const seenSsrc=new Map();
 flows.forEach((flow,index)=>{
  if(!isAes67Destination(flow?.destIp))problems.push({path:'aes67.flows.'+index+'.destIp',message:'流 '+(index+1)+' 目标 IP 必须是有效的单播 IPv4 或组播组'});
  if(!intRange(flow?.port,1,65535))problems.push({path:'aes67.flows.'+index+'.port',message:'流 '+(index+1)+' 端口必须在 1–65535'});
  if(!intRange(flow?.channels,1,aes67MaxFlowChannels))problems.push({path:'aes67.flows.'+index+'.channels',message:'流 '+(index+1)+' 声道必须在 1–8'});
  if(flow?.channelOffset!==offset)problems.push({path:'aes67.flows.'+index+'.channelOffset',message:'AES67 流声道偏移必须连续且从 0 开始'});
  offset+=Number(flow?.channels??0);
  if(!intRange(flow?.ssrc??0,0,4294967295))problems.push({path:'aes67.flows.'+index+'.ssrc',message:'流 '+(index+1)+' SSRC 必须在 0x0–0xFFFFFFFF'});
  const address=parseIpv4(flow?.destIp)?.join('.');
  if(multicastIpv4(address)){
   if(seenGroups.has(address))problems.push({path:'aes67.flows.'+index+'.destIp',message:'每条 AES67 组播流必须使用不同的目标组'});
   seenGroups.add(address);
  }
  const key=address+':'+Number(flow?.port??0);
  if(seen.has(key))problems.push({path:'aes67.flows.'+index+'.port',message:'流 '+(index+1)+' 目标地址与端口与前面的流重复'});
  seen.add(key);
  // Cross-flow SSRC uniqueness on the effective on-wire value, reported
  // in hexadecimal, the way audio people read SSRCs.
  const ssrcValue=effectiveAes67FlowSsrc(aes,index);
  const priorSsrc=seenSsrc.get(ssrcValue);
  if(priorSsrc!==undefined)problems.push({path:'aes67.flows.'+index+'.ssrc',message:'流 '+(priorSsrc+1)+' 与流 '+(index+1)+' SSRC 冲突，各流必须不同：'+formatAes67Ssrc(ssrcValue)});
  else seenSsrc.set(ssrcValue,index);
 });
 // Multicast egress is resolved per flow: a flow-level override or the
 // session-level value satisfies it; the sender fails closed otherwise.
 const sessionSource=String(aes?.srcInterfaceIp??'').trim();
 flows.forEach((flow,index)=>{
  const flowSource=String(flow?.srcInterfaceIp??'').trim();
  if(flowSource!==''&&!unicastIpv4(flowSource))problems.push({path:'aes67.flows.'+index+'.srcInterfaceIp',message:'AES67 源网卡 IP 必须留空或为有效的单播 IPv4'});
  if(multicastIpv4(flow?.destIp)&&flowSource===''&&sessionSource==='')problems.push({path:'aes67.srcInterfaceIp',message:'组播目标必须填写源网卡绑定 IP'});
 });
 return problems;
}
export function validateAes67(value){
 let problems=[];
 const channels=value?.outputBackend?layoutFor(value.outputLayout).channels.length:undefined;
 if(value?.aes67!=null)problems=problems.concat(aes67BlockProblems(value.aes67,channels));
 else if(value?.outputBackend==='aes67')problems.push({path:'aes67.flows.0.destIp',message:'AES67 后端需要完整的 aes67 网络配置'});
 if(value?.outputBackend==='aes67'){
  if(value.sampleRate!==48000)problems.push({path:'sampleRate',message:'AES67 需要设备采样率 48000 Hz'});
  if(!['fixed','unified'].includes(value.sourcePolicy))problems.push({path:'sourcePolicy',message:'AES67 选流方式必须是固定源流或统一输入'});
 }
 return problems;
}
export function problemText(problems,limit=4){
 const lines=problems.slice(0,limit).map(problem=>'· '+problem.message);
 if(problems.length>limit)lines.push('…另有 '+(problems.length-limit)+' 项问题');
 return lines.join('\n');
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
