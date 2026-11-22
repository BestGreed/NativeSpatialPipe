import {outputLayouts} from './layout-presets.js';
export {outputLayouts};
export const layoutFor=id=>outputLayouts.find(p=>p.id===id)??outputLayouts.find(p=>p.id==='7.1.4');
// Pure form model: preserve existing optional/legacy fields and avoid coercing
// empty/invalid numeric entries into valid zero values.
export const channelLabels=['L','R','C','LFE','Ls','Rs','Lrs','Rrs','Ltf','Rtf','Ltr','Rtr'];
export const orders={olr:[0,1,2,3,4,5,6,7,8,9,10,11],'dvs-speakers':[0,2,1,5,4,7,6,3,10,8,9,11]};
// Output backend is explicit; Dante stays a third-party placeholder until the
// Inferno backend is actually integrated (the parser still rejects it).
export const outputBackends=[
 {value:'asio',label:'本机 ASIO'},
 {value:'aes67',label:'AES67 网络输出'},
 {value:'dante',label:'Dante 网络输出（第三方实验性 · 未接入）',disabled:true},
];
export const aes67Defaults={destIp:'239.1.1.1',port:5004,framesPerPacket:48,ttl:32,payloadType:96,ssrc:0,srcInterfaceIp:'',ptpGmid:'',ptpDomain:0,ringPackets:64,prebufferFrames:480,dll:''};
export function getPath(object,path){return path.split('.').reduce((value,key)=>value?.[key],object);}
export function setPath(object,path,value){const keys=path.split('.');let target=object;for(const key of keys.slice(0,-1)){target[key]??={};target=target[key];}target[keys.at(-1)]=value;}
export function prepare(config,id){
 const value=structuredClone(config);
 if(id==='playback'){
  value.recording??={};value.recording.pcm??=false;value.recording.telemetrySnapshots??=false;
  value.outputLayout??='7.1.4';const count=layoutFor(value.outputLayout).channels.length;
  if(value.renderer==='olr')value.renderer='roar';
  value.mode=value.renderer==='lira'?null:(value.mode??'olr');value.channelOrder??='olr';value.rendererDll??='';
  // Saved configs without outputBackend keep the historical ASIO path.
  value.outputBackend??='asio';
  if(value.outputBackend==='aes67'&&!value.network)value.network=structuredClone(aes67Defaults);
  if(value.network)value.network={...aes67Defaults,...value.network};
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
 // The network block is intentionally kept for non-AES67 backends: parse
 // accepts it, the runtime ignores it, and the UI never loses the settings.
 return value;
}

// ---- Output backend / AES67 validation (mirrors tools/zig-scheduler/desktop_playback.zig) ----
export function isIpv4(text){
 const match=/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(text??'').trim());
 if(!match)return false;
 return match.slice(1).every(part=>{const number=Number(part);return number<=255&&String(number)===part;});
}
export const isPtpGmid=text=>{const value=String(text??'').trim();return value===''||/^[0-9a-fA-F]{2}(-[0-9a-fA-F]{2}){7}$/.test(value);};
const absolutePath=text=>text===''||/^[A-Za-z]:[\\/]/.test(text)||text.startsWith('/');
const range=(value,min,max)=>Number.isInteger(value)&&value>=min&&value<=max;
export function validateAes67(value){
 const problems=[];const net=value?.network;
 if(value?.outputBackend==='dante')problems.push({path:'outputBackend',message:'Dante 是第三方实验性后端，当前版本未接入，不能保存为运行后端'});
 if(value?.outputBackend!=='aes67')return problems;
 if(!net)return [{path:'network',message:'AES67 网络段缺失'}];
 if(!isIpv4(net.destIp))problems.push({path:'network.destIp',message:'目标 IP 必须是有效 IPv4（例如 192.168.1.10 或 239.1.1.1）'});
 else if(net.destIp==='0.0.0.0'||net.destIp==='255.255.255.255')problems.push({path:'network.destIp',message:'0.0.0.0 / 255.255.255.255 不能作为发送目标'});
 if(!range(net.port,1,65535))problems.push({path:'network.port',message:'端口必须在 1–65535'});
 if(![48,96,192,480].includes(net.framesPerPacket))problems.push({path:'network.framesPerPacket',message:'每包帧数只允许 48 / 96 / 192 / 480'});
 if(!range(net.ttl,1,255))problems.push({path:'network.ttl',message:'TTL 必须在 1–255'});
 if(!range(net.payloadType,0,127))problems.push({path:'network.payloadType',message:'payloadType 必须在 0–127（动态 RTP 负载类型）'});
 if(!range(net.ssrc,0,4294967295))problems.push({path:'network.ssrc',message:'SSRC 必须在 0–4294967295（0 表示自动）'});
 if(net.srcInterfaceIp&&(!isIpv4(net.srcInterfaceIp)||net.srcInterfaceIp==='0.0.0.0'))problems.push({path:'network.srcInterfaceIp',message:'源网卡 IP 必须留空或填有效 IPv4'});
 if(!isPtpGmid(net.ptpGmid))problems.push({path:'network.ptpGmid',message:'PTP GMID 必须留空或填 8 组十六进制：XX-XX-XX-XX-XX-XX-XX-XX'});
 if(!range(net.ptpDomain,0,127))problems.push({path:'network.ptpDomain',message:'PTP domain 必须在 0–127'});
 if(!range(net.ringPackets,1,4096))problems.push({path:'network.ringPackets',message:'环形缓冲包数必须在 1–4096'});
 if(!range(net.prebufferFrames,0,48000))problems.push({path:'network.prebufferFrames',message:'网络预缓冲必须在 0–48000 源帧'});
 if(!absolutePath(String(net.dll??'')))problems.push({path:'network.dll',message:'DLL 路径必须留空或为绝对路径'});
 return problems;
}
export function problemText(problems,limit=4){
 const list=problems.slice(0,limit).map(item=>'· '+item.message);
 if(problems.length>limit)list.push('…另有 '+(problems.length-limit)+' 项');
 return list.join('\n');
}

// ---- Save preview diff (before PUT) ----
function flatten(value,prefix='',out=[]){
 if(value===null||typeof value!=='object'){out.push([prefix,value]);return out;}
 if(Array.isArray(value)){out.push([prefix,value]);return out;}
 for(const key of Object.keys(value).sort())flatten(value[key],prefix?prefix+'.'+key:key,out);
 return out;
}
const show=value=>value===undefined?'(未设置)':typeof value==='string'?JSON.stringify(value):typeof value==='object'?JSON.stringify(value):String(value);
export function diffConfig(before,after){
 const left=new Map(flatten(before??{}));const right=new Map(flatten(after??{}));const paths=[...new Set([...left.keys(),...right.keys()])].sort();
 const changes=[];
 for(const path of paths){
  const from=left.get(path),to=right.get(path);
  if(JSON.stringify(from)!==JSON.stringify(to))changes.push({path,from,to});
 }
 return changes;
}
export function diffText(changes,limit=20){
 const lines=changes.slice(0,limit).map(change=>change.path+': '+show(change.from)+' → '+show(change.to));
 if(changes.length>limit)lines.push('…另有 '+(changes.length-limit)+' 处');
 return lines.join('\n');
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
