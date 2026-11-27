// DOM contract fixture only: does not drive a browser or operate the desktop.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.value='';this.checked=false;this.hidden=false;this.attrs={};}
 set value(value){this._value=String(value);}
 get value(){return this._value;}
 append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(key,value){this.attrs[key]=value;}
 addEventListener(key,fn){const previous=this.listeners[key];this.listeners[key]=previous?(...args)=>{previous(...args);return fn(...args);}:fn;}
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
const formDefaults={pcmCapture:{captureDll:'D:/capture.dll',ingressDll:'D:/ingress.dll',inputEndpointId:'{0.0.0.00000000}.{aae198db-ab16-4130-aa7d-095c64105833}'},
 wasapiUpmix:{dllPath:'D:/joint.dll',modelPath:'D:/vocals.onnx',freeSurroundDll:'D:/fs.dll',cpuFallbackDll:'D:/cpu.dll'}};
globalThis.fetch=async(url,options={})=>{
 let body;
 if(url.endsWith('/bootstrap'))body={token:'fixture'};
 else if(url.endsWith('/drivers'))body={drivers:[{name:'Fixture driver',driver:'asio:{fixture}'}]};
 else if(url.endsWith('/status'))body={fsa:{known:true,stale:false,message:'fixture',endpoint:'fixture'},backend:null};
 else if(url.endsWith('/config/playback')){if(options.method==='PUT'){saves++;const request=JSON.parse(options.body);assert.equal(request.revision,'fixture-revision');playback=request.config;}body={id:'playback',exists:true,path:'D:/fixture.json',revision:'fixture-revision',config:structuredClone(playback),formDefaults};}
 else if(url.endsWith('/config/backend'))body={id:'backend',exists:true,path:'D:/backend.json',revision:'fixture-revision',config:{version:1,spatialFormat:'WindowsSonic',fullTiming:false}};
 else throw Error('Unexpected API '+url);
 return {ok:true,json:async()=>body};
};
try{
 // The stylesheet is a first-class contract too: a tool-truncated line breaks
 // CSS nesting and silently drops every rule after it.
 const css=readFileSync(new URL('./configuration.css',import.meta.url),'utf8');
 assert.ok(!css.includes('line truncated'),'configuration.css carries no truncation marker');
 assert.equal((css.match(/{/g)||[]).length,(css.match(/}/g)||[]).length,'configuration.css braces balance');
 assert.match(css,/\.config-advanced \.aes67-flows/,'advanced flow rows span the whole panel');
 await import(new URL('./configuration.js',import.meta.url).href+'?fixture='+Date.now());
 const input=path=>element('fields').querySelectorAll('[data-path]').find(item=>item.dataset.path===path);
 assert.ok(input('outputProcessing.bypass'));
 assert.equal(input('pcmCapture.captureDll').value,formDefaults.pcmCapture.captureDll);
 assert.equal(input('pcmCapture.captureDll').disabled,true);
 assert.equal(input('wasapiUpmix.enabled').disabled,true);
 assert.equal(input('recording.pcm').checked,false);assert.equal(input('recording.telemetrySnapshots').checked,false);
 input('recording.pcm').checked=true;
 assert.equal(element('asioDrivers').children.length,1);
 const preview=()=>element('fields').querySelectorAll('p').find(item=>item.dataset.routePreview==='true');
 assert.equal(input('routing.physicalChannels.0').value,'1');
 assert.ok(preview().textContent.startsWith('声道排列：L → R → C → LFE'));
 input('master').value='0.75';await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(saves,1);assert.equal(playback.master,.75);
 assert.equal(playback.pcmCapture,undefined);
 assert.equal(playback.wasapiUpmix,undefined);
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
 const asioBefore=structuredClone(playback);
 input('outputBackend').value='aoip';input('outputBackend').listeners.change();
 assert.equal(input('sourcePolicy').value,'active');
 assert.equal(input('driver'),undefined);assert.equal(input('routing.physicalChannels.0'),undefined);
 assert.equal(input('aoip.txLatencyUs').value,'4000');assert.equal(input('aoip.framesPerPacket').value,'32');
 input('aoip.interfaceIp').value='192.168.50.1';input('aoip.masterIp').value='192.168.50.2';input('aoip.mac').value='f0d7afd04b1d';
 input('aoip.txLatencyUs').value='1200';input('aoip.framesPerPacket').value='0';input('sourcePolicy').value='active';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputBackend,'aoip');assert.equal(playback.aoip.txLatencyUs,1200);assert.equal(playback.aoip.framesPerPacket,0);
 assert.equal(playback.driver,asioBefore.driver);assert.equal(JSON.stringify(playback.routing),JSON.stringify(asioBefore.routing));
 input('outputBackend').value='asio';input('outputBackend').listeners.change();
 assert.equal(input('aoip.interfaceIp'),undefined);assert.equal(input('driver').value,asioBefore.driver);
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputBackend,'asio');assert.equal(playback.aoip.txLatencyUs,1200);assert.equal(playback.sourcePolicy,'fixed');
 input('sourcePolicy').value='unified';input('sourcePolicy').listeners.change();
 assert.equal(input('sourceStream'),undefined);
 assert.equal(input('unifiedSlotProfile').value,'standard');
 input('unifiedSlotProfile').value='dtsxHtCompact';
 assert.equal(input('unifiedPrimaryPolicy').value,'legacy');
 input('unifiedPrimaryPolicy').value='objectCount';
 input('outputBackend').value='aoip';input('outputBackend').listeners.change();
 assert.equal(input('sourcePolicy').value,'unified');
 assert.equal(input('sourceStream'),undefined);
 input('outputLayout').value='7.1.4';input('outputLayout').listeners.change();
 input('pcmCapture.enabled').checked=true;input('pcmCapture.enabled').listeners.change();
 assert.equal(input('pcmCapture.inputEndpointId').value,'');
 assert.equal(input('pcmCapture.inputEndpointId').disabled,true);
 input('pcmCapture.captureDll').value='D:/custom-capture.dll';
 input('wasapiUpmix.enabled').checked=true;input('wasapiUpmix.enabled').listeners.change();
 assert.equal(input('wasapiUpmix.modelPath').value,formDefaults.wasapiUpmix.modelPath);
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputBackend,'aoip');assert.equal(playback.sourcePolicy,'unified');assert.equal(playback.sourceStream,0);
 assert.equal(playback.unifiedSlotProfile,'dtsxHtCompact');
 assert.equal(playback.unifiedPrimaryPolicy,'objectCount');
 assert.equal(playback.pcmCapture.inputEndpointId,undefined);
 assert.equal(playback.pcmCapture.captureDll,'D:/custom-capture.dll');
 assert.equal(playback.pcmCapture.ingressDll,formDefaults.pcmCapture.ingressDll);
 assert.equal(playback.wasapiUpmix.modelPath,formDefaults.wasapiUpmix.modelPath);
 input('outputBackend').value='wasapi';input('outputBackend').listeners.change();
 assert.equal(input('driver'),undefined);assert.equal(input('asioDll'),undefined);
 assert.equal(input('outputLayout').value,'7.1.4');
 assert.ok(input('wasapi.endpointId'));assert.ok(input('wasapi.dllPath'));
 input('wasapi.endpointId').value='{0.0.0.00000000}.{77b2c5f3-cf42-4d93-8770-8528e102135e}';
 input('wasapi.dllPath').value='D:/NSP.WasapiOutput.dll';
 input('lsac.filterFile').value='D:/filters/notebook.nbf';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputBackend,'wasapi');assert.equal(playback.sourcePolicy,'unified');
 assert.equal(playback.outputProcessing.trim.length,2);assert.equal(playback.sampleRate,48000);
 element('rawConfig').value=JSON.stringify({...playback,outputLayout:'2.0',lsac:null,notebookFilterFile:'D:/filters/notebook.nbf'});
 element('rawConfig').listeners.input();await element('fromJson').onclick();
 assert.ok(input('lsac.virtualBass.enabled'));assert.equal(input('lsac.virtualBass.amount').value,'0.18');
 input('lsac.virtualBass.enabled').checked=true;input('lsac.virtualBass.amount').value='0.2';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.lsac.virtualBass.enabled,true);assert.equal(playback.lsac.virtualBass.amount,0.2);assert.equal(playback.lsac.virtualBass.highpassHz,130);
 // ---- AES67 output backend panel ----
 assert.equal(input('outputBackend').children.some(o=>o.value==='aes67'),true);
 input('outputBackend').value='aes67';input('outputBackend').listeners.change();
 assert.ok(input('aes67.flows.0.destIp'));assert.ok(input('aes67.flows.0.port'));assert.equal(input('aes67.framesPerPacket'),undefined,'the legacy frames key is not a form field');assert.equal(input('aes67.ptpGmid'),undefined,'the PTP GMID is never editable');
  // Basic fields stay visible; protocol parameters and clock tuning fold away.
  const insideDetails=node=>{let item=node;while(item){if(item.tag==='details')return true;item=item.parentElement;}return false;};
  assert.equal(element('fields').querySelectorAll('details').length,1,'AES67 builds exactly one advanced-settings disclosure');
  assert.match(element('fields').querySelectorAll('summary')[0].textContent,/高级设置/,'the disclosure is labelled as advanced settings');
  assert.equal(insideDetails(input('aes67.name')),false,'device name is a basic field');
  assert.equal(insideDetails(input('aes67.srcInterfaceIp')),false,'source interface is a basic field');
  assert.equal(insideDetails(input('aes67.ptp.domain')),true,'PTP domain lives under advanced settings');
  assert.equal(insideDetails(input('aes67.ptimeUs')),true,'packet time lives under advanced settings');
  assert.equal(insideDetails(input('aes67.flowMode')),true,'flow packing lives under advanced settings');
  assert.equal(insideDetails(input('sampleRate')),true,'sample rate lives under advanced settings');
  assert.equal(insideDetails(input('aes67.flows.0.destIp')),false,'the start group is a basic field');
  assert.equal(insideDetails(input('aes67.flows.0.port')),false,'the flow-1 port is a basic field');
 assert.equal(input('aes67.ptimeUs').value,'1000');
 assert.ok(input('aes67.ptimeUs').children.some(option=>option.value==='125'),'125 us is offered');
 assert.equal(input('sampleRate').value,'48000');
 assert.ok(input('sampleRate').children.filter(option=>!option.disabled).length===1,'only 48 kHz is currently selectable');
 assert.ok(input('sampleRate').children.some(option=>option.value==='96000'&&option.disabled),'96 kHz is visible but disabled');
 assert.equal(input('aes67.flowMode').value,'standard');assert.equal(input('aes67.portPolicy').value,'strict');
 assert.equal(input('aes67.payloadType').value,'96');
 assert.equal(input('aes67.ringPackets').value,'64');assert.equal(input('aes67.prebufferFrames').value,'480');
 const hiddenAncestor=node=>{let item=node;while(item){if(item.hidden)return true;item=item.parentElement;}return false;};
 assert.equal(hiddenAncestor(input('aes67.ptp.priority1')),true,'GM-only fields stay hidden in slave mode');
 input('aes67.ptp.mode').value='gm';input('aes67.ptp.mode').listeners.change();
 assert.equal(hiddenAncestor(input('aes67.ptp.priority1')),false,'GM fields appear in gm mode');
 input('aes67.ptp.mode').value='slave';input('aes67.ptp.mode').listeners.change();
 assert.equal(input('sourcePolicy').value,'unified');
 assert.ok(!input('sourcePolicy').children.some(option=>option.value==='active'));
 assert.equal(input('aes67.ptp.domain').value,'0');
 assert.equal(input('aes67.ptp.lockTimeoutMs').value,'5000');
 assert.equal(input('aes67.ptp.timestamping').value,'auto');
 assert.equal(input('aes67.ptp.mode').value,'slave');
 assert.equal(input('aes67.ptp.announceTimeoutMs').value,'6000');
 assert.equal(input('aes67.ptp.priority1').value,'128');
 assert.equal(input('aes67.ptp.priority2').value,'128');
 assert.equal(input('aes67.ptp.gmUtcOffset').value,'');
 const blocked=saves;await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(saves,blocked,'invalid empty AES67 destination must not be saved');
 assert.match(element('result').textContent,/(校验未通过|validation failed)/i);
 input('aes67.flows.0.destIp').value='192.0.2.222';input('aes67.ssrc').value='1314082870';
 // Unicast multi-flow derives port 5006 for flow 2, so the default strict policy
 // cannot apply; the panel must expose that through the explicit policy and the
 // client-side check below.
 input('aes67.portPolicy').value='explicit';
 input('aes67.ringPackets').value='512';
 input('aes67.ptp.gmUtcOffset').value='5';
 input('sourcePolicy').value='fixed';input('sourcePolicy').listeners.change();
 assert.ok(input('sourceStream'));
 assert.equal(input('unifiedSlotProfile'),undefined);
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(saves,blocked+1);
 assert.equal(playback.outputBackend,'aes67');
 assert.equal(playback.sourcePolicy,'fixed');
 assert.equal(playback.aes67.ptp.lockTimeoutMs,5000);
 assert.equal(playback.aes67.ptp.gmUtcOffset,5,'a typed UTC offset is saved as a number');
 input('aes67.ptp.gmUtcOffset').value='';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.ptp.gmUtcOffset,undefined,'clearing the UTC offset removes it (ARB)');
 const strictBlocked=saves;
 input('aes67.portPolicy').value='strict';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(saves,strictBlocked,'strict policy with a non-base port must be rejected in the client');
 assert.match(element('result').textContent,/strict|基准端口/);
 input('aes67.portPolicy').value='explicit';
 await element('reload').onclick();
 assert.equal(input('sourcePolicy').value,'fixed');
 assert.ok(input('sourceStream'));
 assert.equal(playback.aes67.destIp,undefined,'E16: no top-level mirror in saved files');
 assert.equal(playback.aes67.port,undefined);
 assert.equal(playback.aes67.ssrc,1314082870);
 assert.equal(playback.aes67.ringPackets,512);
 assert.equal(playback.sampleRate,48000);
 assert.equal(playback.aes67.flows.length,2,'a 12ch save persists the universal 8+4 packing');
 assert.equal(playback.aes67.flows[1].destIp,'192.0.2.222');
 assert.equal(playback.aes67.flows[1].port,5006);
 assert.equal(playback.aes67.flows[1].channels,4);
 assert.ok(playback.aes67.flows[1].ssrc===undefined||playback.aes67.flows[1].ssrc===0,'an empty per-flow SSRC keeps runtime derivation');
 // Destinations are always shown per flow: every layout binds the explicit
 // aes67.flows rows (E16 flows-only).
 assert.ok(element('fields').querySelectorAll('th').some(item=>item.textContent==='流 1'),'every layout renders the flow-1 row');
  assert.match(element('fields').querySelectorAll('p').find(item=>item.className==='aes67-flow-note'&&/2 条流/.test(item.textContent)).textContent,/2 条流/,'a 12ch layout announces the universal 2-flow packing');
 // Multi-flow destinations become explicit on a >8ch layout: the panel
 // derives ceil(channels/8) rows, stores them, and allows per-flow overrides.
 input('outputLayout').value='9.1.6';input('outputLayout').listeners.change();
 assert.ok(input('aes67.flows.0.destIp'));assert.ok(input('aes67.flows.1.destIp'));
 assert.equal(input('aes67.flows.0.destIp').value,'192.0.2.222');
 assert.equal(input('aes67.flows.0.port').value,'5004');
 assert.equal(input('aes67.flows.1.destIp').value,'192.0.2.222');
 assert.equal(input('aes67.flows.1.port').value,'5006');
 const readonlyCells=element('fields').querySelectorAll('span').filter(item=>item.className==='aes67-flow-readonly').map(item=>item.textContent);
 assert.equal(readonlyCells[0],'192.0.2.222','flow 1 shows its current destination in the per-flow editor');
 assert.equal(readonlyCells[1],'5004','flow 1 shows its current port');
 assert.equal(input('aes67.flows.0.ssrc').title,'0x4E535036','effective SSRC is shown in hexadecimal');
 assert.equal(input('aes67.flows.1.ssrc').title,'0x4E535037');
 const flowNote=()=>element('fields').querySelectorAll('p').find(item=>item.className==='aes67-flow-note');
 assert.ok(element('fields').querySelectorAll('p').some(item=>item.className==='aes67-flow-note'&&/自动分配/.test(item.textContent)),'multi-flow note explains the auto allocation rule');
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.flows.length,2);
 assert.equal(playback.aes67.flows[1].port,5006);
 assert.equal(playback.aes67.channelMap,undefined);
 // A per-flow override beats the auto rule and is saved verbatim (SSRC
 // input accepts 0x hexadecimal and stores the integer).
 input('aes67.flows.1.destIp').value='192.0.2.223';
 input('aes67.flows.1.ssrc').value='0x1234ABCD';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.flows[1].destIp,'192.0.2.223');
 assert.equal(playback.aes67.flows[0].destIp,'192.0.2.222');
 assert.equal(playback.aes67.flows[1].ssrc,0x1234ABCD);
 // Multicast auto allocation increments the group and defaults blank ports to 5004.
 input('aes67.flows.0.destIp').value='239.1.0.1';input('aes67.flows.0.port').value='5010';
 input('aes67.srcInterfaceIp').value='192.0.2.63';
 element('fields').querySelectorAll('button').find(item=>item.textContent==='重新自动分配流地址').listeners.click();
 assert.equal(input('aes67.flows.0.destIp').value,'239.1.0.1');
 assert.equal(input('aes67.flows.0.port').value,'5010');
 assert.equal(input('aes67.flows.1.destIp').value,'239.1.0.2');
 assert.equal(input('aes67.flows.1.port').value,'5004');
 assert.equal(input('aes67.flows.0.ssrc').title,'0x4E535036','effective SSRC preview is preserved');
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.flows[1].destIp,'239.1.0.2');
 assert.equal(playback.aes67.flows[1].port,5004);
 assert.equal(playback.aes67.destIp,undefined,'E16: saved files carry flows only');
 assert.equal(playback.aes67.port,undefined);
 assert.equal(playback.aes67.ttl,32);
 // Leaving the 16ch layout reshapes the stale split down to its base row
 // while the channel map that no longer fits is dropped.
 input('outputLayout').value='5.1.2';input('outputLayout').listeners.change();
 assert.equal(input('aes67.flows.0.destIp').value,'239.1.0.1','the base row carries over');
 assert.equal(input('aes67.flows.1.destIp'),undefined);
 assert.match(element('fields').querySelectorAll('p').find(item=>item.className==='aes67-flow-note'&&/条流/.test(item.textContent)).textContent,/1 条流/,'a <=8ch layout announces one flow');
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.flows.length,1);assert.equal(playback.aes67.channelMap,undefined);
 assert.equal(playback.aes67.flows[0].destIp,'239.1.0.1','multicast base destination survives the layout change');
 assert.equal(playback.aes67.flows[0].channels,8);
 // Universal packing on other layouts: 8ch stays one flow row, 10ch derives 8+2.
 input('outputLayout').value='5.1.2';input('outputLayout').listeners.change();
 assert.ok(input('aes67.flows.0.destIp'));
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputLayout,'5.1.2');assert.equal(playback.aes67.flows.length,1);
 assert.equal(playback.aes67.flows[0].channels,8);
 input('outputLayout').value='5.1.4';input('outputLayout').listeners.change();
 assert.ok(input('aes67.flows.0.destIp'));assert.ok(input('aes67.flows.1.destIp'));
 assert.equal(input('aes67.flows.1.destIp').value,'239.1.0.2');
 assert.equal(input('aes67.flows.1.port').value,'5004');
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.flows.length,2);
 assert.equal(playback.aes67.flows[1].channels,2,'the 10ch layout splits 8+2');
 assert.equal(playback.outputLayout,'5.1.4');
 assert.equal(playback.sourcePolicy,'fixed','layout changes preserve AES67 fixed source');
 // Advanced protocol controls: ptime drives packetization, extension packing
 // re-plans flows, and a failed save opens the advanced section automatically.
 input('outputLayout').value='9.1.6';input('outputLayout').listeners.change();
 input('aes67.flowMode').value='extension';input('aes67.flowMode').listeners.change();
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.flowMode,'extension');
 assert.deepEqual(playback.aes67.flows.map(flow=>flow.channels),[10,6],'extension packs 16ch as 10+6 at 1 ms');
 input('aes67.ptimeUs').value='125';input('aes67.ptimeUs').listeners.change();
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.ptimeUs,125);
 assert.deepEqual(playback.aes67.flows.map(flow=>flow.channels),[16],'125 us fits all 16 channels in one flow');
 assert.equal(playback.aes67.framesPerPacket,undefined,'the legacy frames key is not saved');
 input('aes67.flowMode').value='standard';input('aes67.flowMode').listeners.change();
 input('aes67.ptimeUs').value='1000';input('aes67.ptimeUs').listeners.change();
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.deepEqual(playback.aes67.flows.map(flow=>flow.channels),[8,8]);
 const details=element('fields').querySelectorAll('details')[0];
 details.open=false;
 input('aes67.ringPackets').value='7';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(details.open,true,'a failed save opens the advanced section');
 assert.equal(input('aes67.ringPackets').attrs['aria-invalid'],'true');
 input('aes67.ringPackets').value='64';
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.aes67.ringPackets,64);
 input('outputBackend').value='asio';input('outputBackend').listeners.change();
 assert.equal(input('aes67.flows.0.destIp'),undefined);
 assert.equal(input('sourcePolicy').value,'fixed');
 assert.ok(!input('sourcePolicy').children.some(option=>option.value==='active'));
 await element('configForm').listeners.submit({preventDefault(){}});
 assert.equal(playback.outputBackend,'asio');
 assert.equal(playback.sourcePolicy,'fixed');
 assert.equal(playback.aes67.flows[0].destIp,'239.1.0.1','AES67 settings survive a switch back to ASIO');
 input('outputBackend').value='aes67';input('outputBackend').listeners.change();
 assert.equal(input('sourcePolicy').value,'fixed');
 assert.ok(input('sourceStream'));
 input('outputBackend').value='wasapi';input('outputBackend').listeners.change();
 assert.equal(input('sourcePolicy').value,'fixed');
 assert.ok(!input('sourcePolicy').children.some(option=>option.value==='active'));
 await element('backendTab').onclick();assert.ok(input('fullTiming'));assert.equal(input('master'),undefined);
 console.log('PASS configuration page renders fields, submits shared JSON, switches renderer/routing, guards unsynced raw JSON, edits AES67, shows per-flow rows on every layout, derives and overrides multi-flow destinations, preserves AES67 settings across backend switches and loads backend tab');
}finally{for(const [key,value]of Object.entries(prior))globalThis[key]=value;}
