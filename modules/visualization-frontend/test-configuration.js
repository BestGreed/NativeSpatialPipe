import assert from 'node:assert/strict';
import {prepare,numeric,finish,setPath,orders,updateSourceSelection,aes67Defaults,validateAes67,deviceChannels,isIpv4,isPtpGmid,deriveAes67Flows,aes67StandardChannelMap916,effectiveAes67FlowSsrc,proposeAes67Flows,materializeAes67Destinations,parseAes67Ssrc,formatAes67Ssrc,aes67MaxChannelsPerFlow,aes67SamplesPerPacket} from './configuration-model.js';
const original={renderer:'roar',bridgeDll:'C:/bridge.dll',asioDll:'C:/asio.dll',driver:'asio:{test}',startupTimeoutMs:120000};
const prepared=prepare(original,'playback');
assert.equal(original.outputProcessing,undefined);
assert.equal(prepared.outputProcessing.trim.length,12);
assert.equal(prepared.outputProcessing.limiter.enabled,false);
assert.equal(prepared.startupTimeoutMs,120000);
assert.equal(prepare({...original,renderer:'olr'},'playback').renderer,'roar');
setPath(prepared,'outputProcessing.trim.2',.7);
assert.equal(prepared.outputProcessing.trim[2],.7);
prepared.renderer='lira';prepared.positionMapping={mode:'scaled',reference:1};
assert.equal(finish(prepared).positionMapping,undefined);
assert.equal(prepared.mode,null);
prepared.channelOrder='custom';prepared.routing.sourceChannelIndices=orders['dvs-speakers'].slice();
assert.equal(finish(prepared).routing.sourceChannelIndices.length,12);
prepared.channelOrder='olr';assert.equal(finish(prepared).routing.sourceChannelIndices,undefined);
assert.equal(numeric('0',true),0);assert.equal(numeric('0.5'),.5);
for(const value of ['',' ','NaN','Infinity'])assert.throws(()=>numeric(value));
assert.throws(()=>numeric('1.5',true));
assert.equal(JSON.stringify(prepare({version:1,fullTiming:false},'backend')),JSON.stringify({version:1,fullTiming:false}));
console.log('PASS configuration form defaults, optional fields, renderer/routing conversion and numeric validation');

const {outputLayouts,migrateLayout,layoutFor}=await import('./configuration-model.js');
for(const layout of outputLayouts){
 const value=prepare({...original,outputLayout:layout.id},'playback');
 assert.equal(value.outputProcessing.trim.length,layout.channels.length);
 assert.equal(value.routing.physicalChannels.length,layout.channels.length);
}
let routed=prepare(original,'playback');
routed.outputProcessing.trim[0]=.7;routed.outputProcessing.delayFrames[2]=123;
routed.channelOrder='dvs-speakers';routed.routing.physicalChannels=Array.from({length:12},(_,i)=>i+20);
const wide=migrateLayout(routed,'7.1.4','9.1.6');
assert.equal(wide.outputProcessing.trim[6],.7);
assert.equal(wide.outputProcessing.delayFrames[2],123);
assert.equal(wide.routing.physicalChannels[6],20);
assert.equal(wide.routing.physicalChannels[2],21);
assert.equal(new Set(wide.routing.physicalChannels).size,16);
assert.equal(wide.outputProcessing.trim[0],1);
assert.equal(wide.channelOrder,'olr');
const back=migrateLayout(wide,'9.1.6','7.1.4');
assert.equal(back.outputProcessing.trim[0],.7);
assert.equal(back.routing.physicalChannels[2],21);
assert.equal(layoutFor('9.1.6').channels.length,16);
console.log('PASS seven layouts, speaker-identity calibration migration and collision-free device routing');

const pcm={enabled:true,captureDll:'C:/capture.dll',ingressDll:'C:/ingress.dll',routeToOutput:true};
const mixed={outputBackend:'wasapi',outputLayout:'7.1.4',renderer:'roar',sourcePolicy:'unified',sourceStream:2,
 pcmCapture:{...pcm},wasapiUpmix:{enabled:true,modelPath:'C:/model.onnx'}};
updateSourceSelection(mixed);
assert.equal(mixed.sourceStream,0);
assert.equal(mixed.pcmCapture.routeToOutput,false);
assert.equal(mixed.wasapiUpmix.enabled,true);
mixed.outputBackend='aoip';
updateSourceSelection(mixed,'wasapi');
assert.equal(mixed.sourcePolicy,'unified');
assert.equal(mixed.wasapiUpmix.enabled,true);
assert.equal(mixed.wasapiUpmix.modelPath,'C:/model.onnx');
mixed.outputBackend='asio';
assert.equal(updateSourceSelection(mixed,'aoip').sourcePolicy,'unified');
for(const backend of ['aes67','aoip','asio','wasapi']){
 const network=backend==='aoip';
 assert.equal(prepare({...original,outputBackend:backend},'playback').sourcePolicy,backend==='aoip'?'active':'fixed');
 for(const policy of ['active','fixed','unified']){
  const expected=policy==='active'&&!network?'fixed':policy;
  assert.equal(prepare({...original,outputBackend:backend,sourcePolicy:policy},'playback').sourcePolicy,expected);
  assert.equal(updateSourceSelection({...original,outputBackend:backend,sourcePolicy:policy},backend==='aoip'?'aes67':'aoip').sourcePolicy,expected);
 }
}
assert.equal(updateSourceSelection({...original,outputBackend:'aoip',sourcePolicy:'fixed'},'asio').sourcePolicy,'active');
mixed.outputBackend='wasapi';
mixed.sourcePolicy='unified';
mixed.unifiedSlotProfile='dtsxHtCompact';
mixed.unifiedPrimaryPolicy='objectCount';
mixed.renderer='lira';
assert.equal(updateSourceSelection(mixed).sourcePolicy,'fixed');
assert.equal(mixed.unifiedSlotProfile,undefined);
assert.equal(mixed.unifiedPrimaryPolicy,undefined);
const disabled=finish({renderer:'roar',pcmCapture:{...pcm,enabled:false},
 browserIdentity:{enabled:true,identityFile:'C:/identity.json'},wasapiUpmix:{enabled:true}});
assert.equal(disabled.pcmCapture.captureDll,pcm.captureDll);
assert.equal(disabled.pcmCapture.routeToOutput,false);
assert.equal(disabled.browserIdentity.enabled,false);
assert.equal(disabled.browserIdentity.identityFile,'C:/identity.json');
assert.equal(disabled.wasapiUpmix.enabled,false);
const followsSession=finish({renderer:'roar',sourcePolicy:'unified',pcmCapture:{...pcm,enabled:true}});
assert.equal(followsSession.pcmCapture.inputEndpointId,undefined);
const independent=finish({renderer:'roar',sourcePolicy:'fixed',pcmCapture:{...pcm,enabled:true}});
assert.equal(independent.pcmCapture.inputEndpointId,pcm.inputEndpointId);
console.log('PASS backend source selection, PCM disable, session endpoint following and dependent settings');


// ---- AES67 fourth output backend ----
// E16 flows-only: destinations live in aes67.flows; the top-level destIp/port
// mirror is gone from the model and from saved files.
const aes67={...aes67Defaults,payloadType:96,ssrc:1314082870,srcInterfaceIp:'',ptpDomain:0,ringPackets:512,prebufferFrames:480,ttl:32,flows:[{destIp:'192.0.2.222',port:5004,channels:8,channelOffset:0,payloadType:96,ssrc:0,ttl:0}]};
const asioBase={renderer:'roar',bridgeDll:'C:/bridge.dll',asioDll:'C:/asio.dll',driver:'asio:{test}',outputBackend:'asio',outputLayout:'5.1.2'};
const aesPrepared=prepare({...asioBase,outputBackend:'aes67'},'playback');
assert.equal(aesPrepared.aes67.destIp,undefined);
assert.equal(aesPrepared.aes67.port,undefined);
assert.equal(aesPrepared.aes67.flows.length,1,'a fresh block seeds one empty flow-1 row');
assert.equal(aesPrepared.aes67.flows[0].destIp,'');
assert.equal(aesPrepared.aes67.flows[0].port,undefined);
for(const [key,value] of Object.entries(aes67Defaults))assert.equal(aesPrepared.aes67[key],value,'scalar default '+key);
assert.equal(deviceChannels({outputBackend:'aes67',outputLayout:'5.1.2'}).length,8);
assert.equal(deviceChannels({outputBackend:'aes67',outputLayout:'7.1.4'}).length,12);
// Legacy mirror keys are preserved for explicit repair and rejected on save.
const legacy={...aes67Defaults,destIp:'192.0.2.20',port:5006,ringPackets:256};
const kept=prepare({...asioBase,aes67:legacy},'playback');
const expectedLegacy={...legacy,ptp:{domain:0,lockTimeoutMs:5000,timestamping:'auto',mode:'slave',priority1:128,priority2:128,announceTimeoutMs:6000,gmCapable:false,gmUtcOffset:null}};
assert.deepEqual(kept.aes67,expectedLegacy);
assert.deepEqual(finish(kept).aes67,expectedLegacy);
assert.ok(validateAes67(kept).some(problem=>problem.path==='aes67.destIp'));
assert.ok(validateAes67(kept).some(problem=>problem.path==='aes67.port'));
// Switching back to ASIO keeps a real AES67 block; only the empty flow-1
// placeholder the user never filled in is dropped.
assert.equal(finish({...asioBase,aes67:{...aes67}}).aes67.flows[0].destIp,'192.0.2.222');
assert.equal(finish({...asioBase,aes67:{...aes67Defaults,flows:[{destIp:'',port:5004,channels:8,channelOffset:0,payloadType:96,ssrc:0,ttl:0}]}}).aes67,undefined);
// A valid AES67 backend configuration passes client validation.
assert.deepEqual(validateAes67({...aesPrepared,aes67,outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}),[]);
assert.deepEqual(validateAes67({...aesPrepared,aes67,outputBackend:'aes67',sourcePolicy:'unified',sampleRate:48000}),[]);
assert.ok(validateAes67({...aesPrepared,aes67,outputBackend:'aes67',sourcePolicy:'active',sampleRate:48000}).some(problem=>problem.path==='sourcePolicy'));
// Field-level problems mirror aes67_config.zig. Flow destinations validate
// per flow; scalar transport fields validate at the top of the block.
const invalidFlow=modifier=>validateAes67({...aesPrepared,aes67:{...aes67,flows:[{...aes67.flows[0],...modifier}]},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.flows.0.'+Object.keys(modifier)[0]);
assert.ok(!invalidFlow({destIp:'239.1.1.1'}));
assert.ok(invalidFlow({destIp:'240.1.1.1'}));
assert.ok(invalidFlow({destIp:'255.1.1.1'}));
assert.ok(invalidFlow({destIp:'0.0.0.0'}));
assert.ok(invalidFlow({destIp:'192.0.2'}));
assert.ok(!invalidFlow({destIp:'127.0.0.1'}));
assert.ok(invalidFlow({port:0}));
assert.ok(invalidFlow({port:70000}));
assert.ok(invalidFlow({channels:0}));
assert.ok(invalidFlow({channels:9}));
const invalidScalar=modifier=>validateAes67({...aesPrepared,aes67:{...aes67,...modifier},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.'+Object.keys(modifier)[0]);
assert.ok(invalidScalar({framesPerPacket:96}));
assert.ok(invalidScalar({payloadType:95}));
assert.ok(invalidScalar({payloadType:128}));
assert.ok(invalidScalar({ssrc:-1}));
assert.ok(invalidScalar({srcInterfaceIp:'224.0.0.1'}));
assert.ok(!invalidScalar({srcInterfaceIp:'192.0.2.10'}));
assert.ok(invalidScalar({ptpGmid:'00-11-22'}));
assert.ok(invalidScalar({ptpGmid:'00-11-22-FF-FE-33-44-55'}),'Phase 1 rejects any configured PTP GMID');
assert.ok(invalidScalar({ptpDomain:128}));
assert.ok(invalidScalar({ringPackets:7}));
assert.ok(invalidScalar({ringPackets:1025}));
assert.ok(invalidScalar({prebufferFrames:0}));
assert.ok(invalidScalar({prebufferFrames:48001}));
assert.ok(invalidScalar({prebufferFrames:512*48+1}));
assert.ok(!invalidScalar({prebufferFrames:512*48}));
assert.ok(invalidScalar({ttl:0}));
// A block without any flow row is incomplete.
assert.ok(validateAes67({...aesPrepared,aes67:{...aes67,flows:[]},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.flows.0.destIp'));
assert.ok(validateAes67({...asioBase,outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.flows.0.destIp'));
const backendRule=validateAes67({...aesPrepared,aes67,outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:44100}).map(problem=>problem.path);
assert.deepEqual(backendRule,['sampleRate']);
assert.ok(validateAes67({...aesPrepared,aes67,sourcePolicy:'unknown'}).some(problem=>problem.path==='sourcePolicy'));
assert.ok(validateAes67({...asioBase,aes67,sourcePolicy:'fixed',sampleRate:48000}).length===0);
assert.equal(isIpv4('192.0.2.10'),true);
assert.equal(isIpv4('192.0.2.256'),false);
assert.equal(isIpv4('1.2.3'),false);
assert.equal(isPtpGmid(''),true);
assert.equal(isPtpGmid('00-11-22-FF-FE-33-44-55'),true);
assert.equal(isPtpGmid('00-11-22-FF-FE-33-44'),false);
// Saved incomplete flow plans stay intact and fail validation.
const first={destIp:'239.1.0.1',port:5010,channels:8,channelOffset:0,payloadType:96,ssrc:0,ttl:0};
const aes916={...aes67,srcInterfaceIp:'192.0.2.63',flows:[first]};
const prepared916=prepare({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',aes67:aes916},'playback');
assert.deepEqual(prepared916.aes67.flows,[first]);
assert.ok(validateAes67(prepared916).some(problem=>problem.path==='aes67.flows'));
const derived=(destIp,port)=>deriveAes67Flows({...aes916,flows:[{...first,destIp,port}]},16);
assert.equal(derived('192.0.2.222',5004)[1].port,5006);
assert.equal(derived('192.0.2.222',5004)[1].destIp,'192.0.2.222');
assert.equal(derived('239.1.0.255',5010)[1].destIp,'239.1.1.0');
assert.equal(derived('239.1.0.255',5010)[1].port,5004);
assert.equal(derived('239.255.255.255',5010),null);
assert.equal(derived('192.0.2',5010),null);
// Explicitly saved flow rows survive a reload verbatim (no silent rewrite).
const explicitFlows=[{destIp:'239.1.0.1',port:5010,channels:8,channelOffset:0,payloadType:96,ssrc:1314082870,ttl:0},{destIp:'239.1.0.3',port:5100,channels:8,channelOffset:8,payloadType:96,ssrc:1314082871,ttl:0}];
const explicit916=prepare({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',aes67:{...aes916,flows:structuredClone(explicitFlows)}},'playback');
assert.deepEqual(explicit916.aes67.flows,explicitFlows);
// Flow-level validation mirrors aes67_config.zig: bad address, duplicate
// (address,port) and multicast without a bound source interface.
const flowCase=(flows,portPolicy)=>validateAes67({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',sourcePolicy:'fixed',sampleRate:48000,aes67:{...aes916,portPolicy:portPolicy??'strict',flows}}).map(problem=>problem.path);
assert.ok(flowCase([{destIp:'239.1.0.1',port:5010,channels:8,channelOffset:0,payloadType:96,ssrc:0,ttl:32},{destIp:'240.1.1.1',port:5012,channels:8,channelOffset:8,payloadType:96,ssrc:0,ttl:32}]).includes('aes67.flows.1.destIp'));
assert.ok(flowCase([{destIp:'239.1.0.1',port:5010,channels:8,channelOffset:0,payloadType:96,ssrc:0,ttl:32},{destIp:'239.1.0.1',port:5010,channels:8,channelOffset:8,payloadType:96,ssrc:0,ttl:32}]).includes('aes67.flows.1.port'));
// Port policy mirrors the scheduler: explicit allows lab ports, strict only
// accepts the base port, and ports are always 1024-65534 even.
const twoFlows=(firstPort,secondPort)=>[{destIp:'239.1.0.1',port:firstPort,channels:8,channelOffset:0,payloadType:96,ssrc:0,ttl:32},{destIp:'239.1.0.2',port:secondPort,channels:8,channelOffset:8,payloadType:96,ssrc:0,ttl:32}];
assert.ok(!flowCase(twoFlows(5004,5004)).includes('aes67.flows.1.port'),'strict accepts the base port for every multicast flow');
assert.ok(flowCase(twoFlows(5004,5006)).includes('aes67.flows.1.port'),'strict rejects a non-base port');
assert.ok(flowCase(twoFlows(5010,5012),'explicit').length===0,'explicit allows per-flow ports');
assert.ok(!flowCase(twoFlows(5004,5004),'explicit').includes('aes67.flows.1.port'));
assert.ok(flowCase(twoFlows(5004,5003)).some(path=>path==='aes67.flows.1.port'),'odd ports are rejected');
assert.ok(flowCase(twoFlows(5004,1022)).some(path=>path==='aes67.flows.1.port'),'ports below 1024 are rejected');
assert.ok(validateAes67({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',sourcePolicy:'fixed',sampleRate:48000,aes67:{...aes916,srcInterfaceIp:''}}).map(problem=>problem.path).includes('aes67.srcInterfaceIp'));
assert.ok(validateAes67({...asioBase,outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000,aes67:{...aes67,ttl:0}}).map(problem=>problem.path).includes('aes67.ttl'));
// Per-flow SSRC: hex display/input, base-derivation default, and the
// cross-flow uniqueness rule reported in hexadecimal.
assert.equal(formatAes67Ssrc(1314082870),'0x4E535036');
assert.equal(formatAes67Ssrc(0),'0x0');
assert.equal(parseAes67Ssrc('0x4E535036'),1314082870);
assert.equal(parseAes67Ssrc('4E535036'),1314082870);
assert.equal(parseAes67Ssrc('1314082870'),1314082870);
assert.ok(Number.isNaN(parseAes67Ssrc('0xZZ')));
const defaultBased=prepare({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',aes67:{...aes67,ssrc:0,srcInterfaceIp:'192.0.2.63'}},'playback');
assert.equal(effectiveAes67FlowSsrc(defaultBased.aes67,0),0x4E535036);
assert.equal(effectiveAes67FlowSsrc(defaultBased.aes67,1),0x4E535037);
assert.equal(defaultBased.aes67.flows[0].ssrc,0);
const ssrcConflict=validateAes67({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',sourcePolicy:'fixed',sampleRate:48000,aes67:{...aes916,flows:[{destIp:'239.1.0.1',port:5010,channels:8,channelOffset:0,payloadType:96,ssrc:0x12345678,ttl:0},{destIp:'239.1.0.2',port:5012,channels:8,channelOffset:8,payloadType:96,ssrc:0x12345678,ttl:0}]}});
assert.ok(ssrcConflict.some(problem=>problem.path==='aes67.flows.1.ssrc'&&problem.message.includes('0x12345678')&&problem.message.includes('流 1 与流 2')));
assert.ok(validateAes67({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',sourcePolicy:'fixed',sampleRate:48000,aes67:{...aes916,flows:[{destIp:'239.1.0.1',port:5010,channels:8,channelOffset:0,payloadType:96,ssrc:-1,ttl:0},{destIp:'239.1.0.2',port:5012,channels:8,channelOffset:8,payloadType:96,ssrc:0,ttl:0}]}}).some(problem=>problem.path==='aes67.flows.0.ssrc'));
// Proposed minimum splits are separate from saved, user-defined packing.
assert.deepEqual(proposeAes67Flows(8).map(flow=>flow.channels),[8]);
assert.deepEqual(proposeAes67Flows(10).map(flow=>flow.channels),[8,2]);
const custom={...aes916,portPolicy:'explicit',flows:[{...first,channels:6},{...first,destIp:'239.1.0.2',channels:6,channelOffset:6},{...first,destIp:'239.1.0.3',channels:4,channelOffset:12}],channelMap:aes67StandardChannelMap916};
const saved=prepare({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',aes67:custom},'playback');
assert.deepEqual(saved.aes67,{...custom,ptp:expectedLegacy.ptp});
assert.deepEqual(validateAes67(saved),[]);
const draft={...aes916,flows:proposeAes67Flows(16,first)};
materializeAes67Destinations(draft);
assert.equal(draft.flows[1].destIp,'239.1.0.2');
assert.equal(draft.flows[1].port,5004);
// Multicast default ports follow the session basePort, not a hard-coded 5004
// (same rule as web_host.zig and aes67_config.zig).
const basePortDraft={...aes916,basePort:6004,flows:proposeAes67Flows(16,{...first,port:undefined})};
materializeAes67Destinations(basePortDraft);
assert.equal(basePortDraft.flows[0].port,6004);
assert.equal(basePortDraft.flows[1].port,6004);
assert.equal(draft.flows[1].srcInterfaceIp,'192.0.2.63');
assert.ok(flowCase([{...first},{...first,port:5008,channelOffset:8}]).includes('aes67.flows.1.destIp'));
assert.ok(flowCase([{...first},{...first,destIp:'239.1.0.2',channelOffset:7}]).includes('aes67.flows.1.channelOffset'));
assert.ok(parseAes67Ssrc('0x100000000')>0xFFFFFFFF);
// Device name: optional printable ASCII with spaces, <=63 characters. It is
// not part of aes67Defaults, so an absent name never gets injected on save,
// while a configured name survives the save round trip.
assert.equal(Object.hasOwn(aes67Defaults,'name'),false,'a default device name is never injected');
assert.equal('name' in aesPrepared.aes67,false,'a fresh AES67 block keeps the name unset');
assert.ok(!invalidScalar({name:'NSP Living Room'}),'spaces are allowed in a device name');
assert.ok(!invalidScalar({name:'a'.repeat(63)}),'63 printable characters are accepted');
assert.ok(!invalidScalar({name:'   '}),'a blank name is equivalent to omitted');
assert.ok(invalidScalar({name:'a'.repeat(64)}),'64 characters are rejected');
assert.ok(invalidScalar({name:'NSP\tRoom'}),'control characters are rejected');
assert.ok(invalidScalar({name:'NSP-音响'}),'non-ASCII characters are rejected');
const named={...aes67,name:'NSP Living Room'};
const namedSaved=prepare({...asioBase,outputBackend:'aes67',outputLayout:'5.1.2',aes67:named},'playback');
assert.equal(namedSaved.aes67.name,'NSP Living Room');
assert.deepEqual(namedSaved.aes67,{...named,ptp:expectedLegacy.ptp});
assert.equal(finish(namedSaved).aes67.name,'NSP Living Room','a saved device name survives finish()');
assert.equal(JSON.parse(JSON.stringify(finish(namedSaved))).aes67.name,'NSP Living Room','a saved device name survives a JSON round trip');
assert.deepEqual(validateAes67({...aesPrepared,aes67:named,outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).filter(problem=>problem.path==='aes67.name'),[]);
// ptime drives packetization: frames, per-flow packing and legacy-key guidance.
assert.equal(aes67SamplesPerPacket(48000,1000),48);
assert.equal(aes67SamplesPerPacket(48000,125),6);
assert.equal(aes67SamplesPerPacket(96000,1000),96);
assert.equal(aes67SamplesPerPacket(44100,1000),48);
assert.equal(aes67SamplesPerPacket(48000,123),null);
assert.equal(aes67MaxChannelsPerFlow({flowMode:'standard',ptimeUs:1000}),8);
assert.equal(aes67MaxChannelsPerFlow({flowMode:'extension',ptimeUs:1000,sampleRate:48000}),10);
assert.equal(aes67MaxChannelsPerFlow({flowMode:'extension',ptimeUs:125,sampleRate:48000}),16);
assert.deepEqual(proposeAes67Flows(16,first,10).map(flow=>flow.channels),[10,6]);
assert.ok(validateAes67({...aesPrepared,aes67:{...aes67,ptimeUs:123},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.ptimeUs'));
assert.ok(validateAes67({...aesPrepared,aes67:{...aes67,framesPerPacket:6},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.framesPerPacket'),'the legacy frames key must agree with ptimeUs');
const extension={...aes67,srcInterfaceIp:'192.0.2.63',flowMode:'extension',portPolicy:'explicit',flows:proposeAes67Flows(16,first,10)};materializeAes67Destinations(extension);
assert.deepEqual(validateAes67({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',sourcePolicy:'fixed',sampleRate:48000,aes67:extension}),[]);
assert.ok(validateAes67({...asioBase,outputBackend:'aes67',outputLayout:'9.1.6',sourcePolicy:'fixed',sampleRate:48000,aes67:{...extension,flowMode:'standard'}}).some(problem=>problem.path.startsWith('aes67.flows.')&&problem.message.includes('当前打包模式')));
assert.ok(validateAes67({...aesPrepared,aes67:{...aes67,flowMode:'packed'},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.flowMode'));
assert.ok(validateAes67({...aesPrepared,aes67:{...aes67,portPolicy:'loose'},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.portPolicy'));
assert.ok(validateAes67({...aesPrepared,aes67:{...aes67,sapAddress:'192.0.2.1'},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.sapAddress'));
assert.ok(validateAes67({...aesPrepared,aes67:{...aes67,sapAddress:'239.255.255.255',sapIntervalMs:500},outputBackend:'aes67',sourcePolicy:'fixed',sampleRate:48000}).some(problem=>problem.path==='aes67.sapIntervalMs'));
// finish() materializes the single sample-rate control into the AES67 block.
assert.equal(finish(prepare({...asioBase,outputBackend:'aes67',outputLayout:'5.1.2',aes67},'playback')).aes67.sampleRate,48000);
assert.equal(prepare({...asioBase,outputBackend:'aes67',outputLayout:'5.1.2',aes67},'playback').aes67.ptimeUs,1000,'ptimeUs defaults to 1 ms');
console.log('PASS AES67 backend defaults, multicast destinations, flows-only universal per-flow rows for every layout, hex SSRC, config preservation, channel count and contract validation');
