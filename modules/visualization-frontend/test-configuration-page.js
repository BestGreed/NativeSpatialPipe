// DOM contract fixture only: does not drive a browser or operate the desktop.
import assert from 'node:assert/strict';
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
 await element('backendTab').onclick();assert.ok(input('fullTiming'));assert.equal(input('master'),undefined);
 console.log('PASS configuration page renders fields, submits shared JSON, switches renderer/routing, guards unsynced raw JSON and loads backend tab');
}finally{for(const [key,value]of Object.entries(prior))globalThis[key]=value;}
