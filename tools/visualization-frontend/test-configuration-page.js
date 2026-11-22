// DOM contract fixture only: does not drive a browser or operate the desktop.
import assert from 'node:assert/strict';
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.value='';this.checked=false;this.hidden=false;this.attrs={};}
 set value(value){this._value=String(value);}
 get value(){return this._value;}
 append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(key,value){this.attrs[key]=value;}
 addEventListener(key,fn){this.listeners[key]=fn;}
 querySelectorAll(selector){const result=[];for(const child of this.children){if(selector==='[data-path]'?child.dataset.path:child.tag===selector)result.push(child);result.push(...child.querySelectorAll(selector));}return result;}
 querySelector(selector){return this.querySelectorAll(selector)[0]??null;}
}
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const prior={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch,setTimeout:globalThis.setTimeout,confirm:globalThis.confirm};
globalThis.document={getElementById:element,createElement:tag=>new Element(tag),hidden:false};
// Mirror the real form hierarchy used by configuration.js.
element('configForm').append(element('fields'));
globalThis.window={addEventListener(){}};globalThis.confirm=()=>true;globalThis.setTimeout=()=>0;
let saves=0;
let playback={renderer:'roar',bridgeDll:'D:/bridge.dll',asioDll:'D:/asio.dll',rendererDll:'D:/lira.dll',driver:'asio:{fixture}',master:1};
globalThis.fetch=async(url,options={})=>{
 let body;
 if(url.endsWith('/bootstrap'))body={token:'fixture'};
 else if(url.endsWith('/drivers'))body={drivers:[{name:'Fixture driver',driver:'asio:{fixture}'}]};
 else if(url.endsWith('/network-status'))body={schema:'nsp-web-network-status/1',control:false,current:{backend:'aes67',source:'session',layout:'4.1.2',sessionState:'running'},configured:{known:true,path:'D:/fixture.json',outputBackend:'aes67',outputLayout:'4.1.2',networkIgnored:false},session:{known:true,state:'running',backend:'aes67',outputDirectory:'D:/fixture-session',layout:'4.1.2',networkIgnored:false},aes67:{status:'LIVE',source:'aes67-live.json',ageMs:1200,state:'playing',layout:'4.1.2',network:{destIp:'192.168.1.10',port:5004,ssrc:1314082870,channels:7},stats:{packetsSent:12345,silentPackets:10,bytesSent:22671360,ringDroppedFrames:0,sendErrors:0,lateTicks:0},rates:{packetsPerSecond:996.2,bitsPerSecond:13872000}},dante:{status:'NOT_AVAILABLE',detail:'placeholder'}};
 else if(url.endsWith('/status'))body={fsa:{known:true,stale:false,message:'fixture',endpoint:'fixture'},backend:null};
 else if(url.endsWith('/config/playback')){if(options.method==='PUT'){saves++;const request=JSON.parse(options.body);assert.equal(request.revision,'fixture-revision');playback=request.config;}body={id:'playback',exists:true,path:'D:/fixture.json',revision:'fixture-revision',config:structuredClone(playback)};}
 else if(url.endsWith('/config/backend'))body={id:'backend',exists:true,path:'D:/backend.json',revision:'fixture-revision',config:{version:1,spatialFormat:'WindowsSonic',fullTiming:false}};
 else throw Error('Unexpected API '+url);
 return {ok:true,json:async()=>body};
};
try{
 await import(new URL('./configuration.js',import.meta.url).href+'?fixture='+Date.now());
 const input=path=>element('fields').querySelectorAll('[data-path]').find(item=>item.dataset.path===path);
 assert.ok(input('outputProcessing.bypass'));
 assert.equal(input('recording.pcm').checked,false);assert.equal(input('recording.telemetrySnapshots').checked,false);
 input('recording.pcm').checked=true;
 assert.equal(element('asioDrivers').children.length,1);
 const preview=()=>element('fields').querySelectorAll('p').find(item=>item.dataset.routePreview==='true');
 assert.equal(input('routing.physicalChannels.0').value,'1');
 assert.ok(preview().textContent.startsWith('声道排列：L → R → C → LFE'));
 input('master').value='0.75';await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(saves,1);assert.equal(playback.master,.75);
 assert.equal(playback.recording.pcm,true);assert.equal(playback.recording.telemetrySnapshots,false);
 assert.equal(playback.outputProcessing.trim.length,12);
 assert.equal(playback.routing.physicalChannels[0],0);
 input('channelOrder').value='dvs-speakers';input('channelOrder').listeners.change();
 assert.ok(preview().textContent.startsWith('声道排列：L → C → R → Rs → Ls → Rrs → Lrs → LFE → Ltr → Ltf → Rtf → Rtr'));
 assert.ok(preview().textContent.includes('C → 第 2 路'));
 input('routing.physicalChannels.1').value='5';input('routing.physicalChannels.1').listeners.input();
 assert.ok(preview().textContent.includes('C → 第 5 路'));
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.channelOrder,'dvs-speakers');assert.equal(playback.routing.physicalChannels[1],4);
 assert.equal(playback.outputProcessing.trim[2],1);
 input('renderer').value='lira';input('renderer').listeners.change();
 assert.equal(input('positionMapping.reference'),undefined);
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.renderer,'lira');assert.equal(playback.mode,null);assert.equal(playback.positionMapping,undefined);
 input('channelOrder').value='custom';input('channelOrder').listeners.change();
 assert.ok(input('routing.sourceChannelIndices.11'));
 input('routing.sourceChannelIndices.0').value='2';input('routing.sourceChannelIndices.0').listeners.change();
 assert.ok(preview().textContent.startsWith('声道排列：C →'));
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.routing.sourceChannelIndices.length,12);
 assert.equal(playback.routing.sourceChannelIndices[0],2);
 element('rawConfig').value='{broken';element('rawConfig').listeners.input();
 const before=saves;await element('configForm').listeners.submit({preventDefault(){}});assert.equal(saves,before);
 element('rawConfig').value=JSON.stringify({...playback,master:.5});element('fromJson').onclick();
 await element('configForm').listeners.submit({preventDefault(){}});assert.equal(playback.master,.5);
 for(const [layout,count] of [['5.1.2',8],['5.1.4',10],['7.1.2',10],['7.1.4',12],['7.1.6',14],['9.1.4',14],['9.1.6',16]]){
  input('outputLayout').value=layout;input('outputLayout').listeners.change();
  assert.ok(input(`outputProcessing.trim.${count-1}`));
  assert.equal(input(`outputProcessing.trim.${count}`),undefined);
  assert.ok(input(`routing.physicalChannels.${count-1}`));
  await element('configForm').listeners.submit({preventDefault(){}});
  assert.equal(playback.outputLayout,layout);
  assert.equal(playback.outputProcessing.trim.length,count);
 }
 input('renderer').value='roar';input('renderer').listeners.change();
 assert.ok(input('roarCustomLayout.measurementFile'));
 assert.ok(input('outputLayout').children.some(o=>o.value==='roar-custom-6dof'&&o.disabled));
 await element('backendTab').onclick();assert.ok(input('fullTiming'));assert.equal(input('master'),undefined);
 // ---- output backend / AES67 / Dante placeholder / network status ----
 await element('playbackTab').onclick();
 const inputPath=path=>element('fields').querySelectorAll('[data-path]').find(item=>item.dataset.path===path);
 assert.equal(element('dirtyState').textContent,'');
 assert.ok(inputPath('outputBackend'));
 const danteOption=inputPath('outputBackend').children.find(o=>o.value==='dante');
 assert.ok(danteOption&&danteOption.disabled);
 inputPath('outputBackend').value='aes67';inputPath('outputBackend').listeners.change();
 for(const path of ['network.destIp','network.port','network.framesPerPacket','network.payloadType','network.ssrc','network.ttl','network.ringPackets','network.prebufferFrames','network.srcInterfaceIp','network.ptpGmid','network.ptpDomain','network.dll'])assert.ok(inputPath(path),path);
 assert.equal(inputPath('network.destIp').value,'239.1.1.1');
 inputPath('network.destIp').value='999.1.1.1';inputPath('network.destIp').listeners.input();
 const beforeInvalid=saves;await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(saves,beforeInvalid,'invalid IPv4 must not be saved');
 assert.equal(element('result').dataset.error,'true');
 inputPath('network.destIp').value='192.168.1.10';inputPath('network.port').value='5004';inputPath('network.ssrc').value='1314082870';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputBackend,'aes67');
 assert.equal(playback.network.destIp,'192.168.1.10');
 assert.equal(playback.network.framesPerPacket,48);
 assert.equal(playback.network.ringPackets,64);
 assert.equal(playback.network.ssrc,1314082870);
 // Switching to ASIO hides AES67 inputs but keeps the block in the saved JSON.
 inputPath('outputBackend').value='asio';inputPath('outputBackend').listeners.change();
 assert.equal(inputPath('network.destIp'),undefined);
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputBackend,'asio');
 assert.equal(playback.network.destIp,'192.168.1.10');
 // Status block consumes the read-only API and never fabricates numbers.
 for(let i=0;i<20;i++)await Promise.resolve();
 const statusText=element('networkStatus').children.map(row=>row.children.map(cell=>cell.textContent).join(' ')).join('\n');
 assert.ok(statusText.includes('AES67'),statusText);
 assert.ok(statusText.includes('LIVE'),statusText);
 assert.ok(statusText.includes('12345'),statusText);
 assert.ok(statusText.includes('192.168.1.10:5004'),statusText);
 console.log('PASS configuration page renders fields, submits shared JSON, switches renderer/routing, guards unsynced raw JSON and loads backend tab');
 console.log('PASS AES67 fields validate/save/preserve across backend switch and status block shows live sender numbers');
}finally{for(const [key,value]of Object.entries(prior))globalThis[key]=value;}