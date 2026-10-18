import {assert, createValidator} from "./data.js";
import {mapPosition,referenceScale,cloudDepthStyle,burstOffset} from './visual-space.js';
import {updateCloudEnvelope} from './cloud-field.js';
import {gridFlowOpacity,gridJunctionOpacity} from './ground-grid.js';
import {Replay} from "./replay.js";
import {Navigation,VisualHeading,rotation,apply,unit,norm,direction,rad} from './navigation.js';
import {NavigationForecast} from './navigation-forecast.js';
import {SpatialPattern} from './spatial-pattern.js';
import {updateWeakField,selectWeakAnchors,buildWeakNetwork,weakNetworkBlocks,weakBlockFlash,weakConnectionBrightness,weakFilamentBrightness,WeakInertia,growWeakFilaments,exploreWeakConnection,swimWeakPoint,createWeakEcho,sampleWeakEcho,weakMembraneBetween,weakMembraneFromFilaments} from './weak-field.js';
import {isVoiceCategory,visualLayer,voiceArcVisible,voiceArcRadius,voiceArcSegments} from './voice-visual.js';
import {createVoiceOverlay} from './voice-labels.js';
import {toiletLines,toiletFaces} from './assets/toilet-wire.js';
import {ToiletFollow,toiletBoundsCenter,toiletTransform} from './toilet-follow.js';
import {prepareToiletOccluders,coveredToiletLine} from './toilet-occlusion.js';
import {projectViewPoint,namedView,dragView} from './view-camera.js';
import {Navigation as LegacyNavigation} from './navigation-legacy.js';
import {scenarios,scenarioObjects,metricScenario} from './navigation-scenarios.js';
const baseline=new URLSearchParams(location.search).get('navigation')==='off';
const legacyMode=new URLSearchParams(location.search).get('navigation')==='legacy';
const navigation=legacyMode?new LegacyNavigation():new Navigation(),heading=new VisualHeading(),forecast=new NavigationForecast(),pattern=new SpatialPattern(),weakInertia=new WeakInertia(),toiletFollow=new ToiletFollow(),toiletCenter=toiletBoundsCenter(toiletLines);
let demoTick=0,spaceScale=null;
let scenario='turn',manualUntil=0,lastNavigation=0,gridOffset=[0,0,0],lastHeadingYaw=0,lastHeadingPitch=0,lastRollSeq=-1;
let toiletHidden=[],lastToiletOcclusion=-Infinity;
const stats={estimate:[],cpu:[],gpu:[]},record=(name,value)=>{stats[name].push(value);if(stats[name].length>600)stats[name].shift();};
const $ = id => document.getElementById(id);
const channelIds = ['FL','FR','C','LFE','SL','SR','BL','BR','TFL','TFR','TBL','TBR'];
const fixtureRoot = '/contracts/visualization/v0/';
const labels = {live:'连续数据',gap:'有缺块',stale:'数据过期',warmup:'预热中',disconnected:'已断开'};
const keyOf = k => JSON.stringify([k.sessionId,k.streamEpoch,k.slot,k.generation]);
const clamp = (x,a=0,b=1) => Math.max(a,Math.min(b,x));
const dbText = x => x == null ? '—' : x.toFixed(1);
const savedToiletHeight=()=>{try{const value=Number(localStorage.getItem('nsp.toiletHeight'));return Number.isFinite(value)&&localStorage.getItem('nsp.toiletHeight')!==null?clamp(value,-.2,.55):.2;}catch{return .2;}};
const readDefaultView=()=>{try{const value=JSON.parse(localStorage.getItem('nsp.defaultView'));
  if(value&&Number.isFinite(value.yaw)&&Number.isFinite(value.tilt)&&Number.isFinite(value.zoom)&&Number.isFinite(value.toiletHeight))
    return {yaw:value.yaw,tilt:clamp(value.tilt,-Math.PI/2,Math.PI/2),zoom:clamp(value.zoom,.05,1.8),toiletHeight:clamp(value.toiletHeight,-.2,.55)};
}catch{}return {yaw:-.28,tilt:.36,zoom:1,toiletHeight:savedToiletHeight()};};
let defaultView=namedView('oblique',readDefaultView(),[],1);
const state = {mode:'navigation', source:'demo', paused:false, reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,
  snapshot:null, layout:null, selected:null, window:0, history:null, received:0, events:new Map(), objects:new Map(), weakEchoes:new Map(), transientUntil:new Map(), scope:'', ...defaultView, error:''};
let schema, validate, seed, demoStart=performance.now(), lastDemo=0, lastDraw=0, lastStatus=0, socket=null, socketGeneration=0, historyGeneration=0, negotiated=false, selectorSignature='';
let replay=null,replayGeneration=0,replayManifest=null,hiddenReplayPlaying=false;
let replayScrubbing=false,replayResumeAfterScrub=false;
const notice = text => { $('notice').textContent=text; };
const identityText = k => `槽位 ${k.slot} / 代次 ${k.generation}`;
function meter(peak,n=4800){const mean=peak*peak/2;return {sampleCount:n,sumSquares:mean*n,meanSquare:mean,samplePeak:peak,rmsDbfs:peak?10*Math.log10(mean):null,peakDbfs:peak?20*Math.log10(peak):null,silent:peak===0,overFullScale:peak>1};}
function spatial(p){const r=Math.hypot(...p)||1;return {position:p,positionValid:true,coordinateSystem:'listener-relative-x-right-y-up-z-back',provenance:'raw',units:'source-unverified',rendererPosition:[p[0]/r,-p[2]/r,p[1]/r],rendererTransform:'normalize-x-negz-y'};}
function simulate(now){
  const step=demoTick++,t=step/10,m=structuredClone(seed);
  if(state.snapshot&&state.snapshot.telemetrySeq>=step)return;
  m.telemetrySeq=step;m.sourceSequenceRange={start:step*10,endExclusive:(step+1)*10};m.audioSampleRange={start:step*4800,endExclusive:(step+1)*4800};
  m.sourceTimeUs=(step+1)*100000;m.publishedTimeUs=m.sourceTimeUs+1000;m.meterRevision='linear-v1-demo-ignored';
  for(const name of Object.keys(m.taps)){const tap=m.taps[name];tap.coverage={expectedSamples:4800,validSamples:4800,ratio:1};m.coverage[name]={...tap.coverage};}
  m.taps['decoded-input'].objects=[];
  for(const o of scenarioObjects(scenario,t)){
    m.taps['decoded-input'].objects.push({objectKey:{sessionId:m.sessionId,streamEpoch:0,slot:o.slot,generation:o.generation},sourceIdent:100+o.slot,kind:'dynamic',staticType:null,
      spatial:spatial(o.p),meter:meter(o.peak*(.8+.2*Math.sin(t+o.slot))),activeDurationUs:Math.floor(t*1e6),applicationName:null,sourceCategory:null});
  }
  for(let i=0;i<12;i++){
    const peak=10**((-38+24*(.5+.5*Math.sin(t*.8+i*.7)))/20);
    m.taps['render-ready'].channels[i].meter=meter(peak);m.taps['render-ready'].channels[i].validDurationUs=100000;
  }
  if(step%9===0){const o=m.taps['decoded-input'].objects[Math.floor(step/9)%m.taps['decoded-input'].objects.length];m.events=[{eventId:`demo-${step}`,objectKey:o.objectKey,kind:'onset',sourceTimeUs:m.sourceTimeUs,
    expiresTimeUs:m.sourceTimeUs+600000,spatial:o.spatial,strength:.8,risePerSecond:20,endReason:null,exactEosSample:null}];}
  if(scenario==='gap'&&step%40>20)return;
  accept(m);
}
function visibleOnsets(sourceNow){
  return [...state.events.values()].filter(e=>e.kind==='onset'&&e.spatial.positionValid&&e.spatial.position&&sourceNow>=e.sourceTimeUs&&sourceNow<e.expiresTimeUs)
    .sort((a,b)=>b.strength-a.strength).slice(0,6);
}
function accept(m){
  validate(m);
  if(m.messageType==='layout'){state.layout=m;updateUI();return;}
  if(m.messageType==='history'){state.history=m;updateUI();return;}
  if(m.messageType==='negotiated'){negotiated=true;$('frameLabel').textContent=`${m.publishRateHz} Hz 数据 / 30 fps 画面`;return;}
  if(m.messageType!=='snapshot')return;
  if(state.source==='socket')assert(negotiated,'服务需先返回 negotiated 再发送快照');
  const scope=JSON.stringify([m.routeId,m.sessionId,m.streamEpoch]);
  if(state.scope===scope&&state.snapshot){assert(m.telemetrySeq>state.snapshot.telemetrySeq&&m.sourceSequenceRange.start>=state.snapshot.sourceSequenceRange.endExclusive,'已忽略重复或倒序快照');}
  if(state.scope!==scope){state.events.clear();state.objects.clear();state.weakEchoes.clear();state.transientUntil.clear();state.selected=null;state.scope=scope;spaceScale=null;navigation.reset();heading.reset();weakInertia.reset();toiletFollow.reset();lastHeadingYaw=lastHeadingPitch=0;lastRollSeq=-1;forecast.reset();pattern.reset();gridOffset=[0,0,0];}
  if(state.snapshot&&state.snapshot.meterRevision!==m.meterRevision){heading.reset();weakInertia.reset();toiletFollow.reset();lastHeadingYaw=lastHeadingPitch=0;lastRollSeq=-1;forecast.reset();pattern.reset();gridOffset=[0,0,0];}
  if(state.snapshot&&(m.sourceTimeUs<=state.snapshot.sourceTimeUs||m.sourceTimeUs-state.snapshot.sourceTimeUs>550000))forecast.reset();
  const estimateStart=performance.now();if(!baseline){navigation.ingest(m,{stableScale:state.source==='demo'&&metricScenario(scenario),sourceSpace:state.source==='replay'||state.source==='socket'&&m.dataOrigin==='measured'});pattern.ingest(m);}record('estimate',performance.now()-estimateStart);
  const sourceContinuous=state.snapshot&&m.sourceTimeUs>state.snapshot.sourceTimeUs&&m.sourceTimeUs-state.snapshot.sourceTimeUs<=250000&&state.snapshot.sourceSequenceRange.endExclusive===m.sourceSequenceRange.start;
  const interval=state.snapshot?Math.min(250,Math.max(10,(m.sourceTimeUs-state.snapshot.sourceTimeUs)/1000)):100;
  state.snapshot=m;state.received=performance.now();state.error='';
  if(!baseline&&!legacyMode)forecast.observe(navigation,state.received);
  // Classify the onset halos that the UI actually displays. The telemetry
  // contains many onset candidates that never receive a halo.
  for(const e of m.events)if(!state.events.has(e.eventId))state.events.set(e.eventId,e);
  for(const [id,e]of state.events)if(e.expiresTimeUs<=m.sourceTimeUs)state.events.delete(id);
  while(state.events.size>256)state.events.delete(state.events.keys().next().value);
  for(const e of visibleOnsets(m.sourceTimeUs))if(e.objectKey){
    const k=keyOf(e.objectKey);
    state.transientUntil.set(k,Math.max(state.transientUntil.get(k)||0,e.expiresTimeUs+200000));
    state.weakEchoes.delete(k);
  }
  for(const [k,until] of state.transientUntil)if(until<=m.sourceTimeUs)state.transientUntil.delete(k);
  const tap=m.taps['decoded-input'],seen=new Set();
  const windowMs=1000*(m.audioSampleRange.endExclusive-m.audioSampleRange.start)/m.sampleRate;
  if(spaceScale===null)spaceScale=referenceScale(tap.objects);
  for(const o of tap.objects){const k=keyOf(o.objectKey);seen.add(k);
    for(const [old,echo] of state.weakEchoes)if(echo.slot===o.objectKey.slot&&old!==k)state.weakEchoes.delete(old);
    for(const [old,value]of state.objects)if(value.object.objectKey.slot===o.objectKey.slot&&old!==k){state.objects.delete(old);state.transientUntil.delete(old);}
    const previous=state.objects.get(k),trail=previous?.trail||[];
    if(o.spatial.positionValid&&o.spatial.position&&Math.hypot(...o.spatial.position)){const r=Math.hypot(...o.spatial.position);trail.push(o.spatial.position.map(x=>x/r));if(trail.length>10)trail.shift();}
    const continuous=previous&&sourceContinuous&&tap.quality==='live'&&tap.snapshotComplete;
    const activityInterval=continuous?interval:Math.min(interval,windowMs);
    const sustained=o.meter.rmsDbfs!==null&&o.meter.rmsDbfs>-42?(continuous?(previous.sustained||0)+activityInterval:activityInterval):0;
    const targetEnergy=clamp(((o.meter.rmsDbfs??-60)+60)/60),priorEnergy=continuous?(previous.visualEnergy||0):0;
    const visualEnergy=priorEnergy+(targetEnergy-priorEnergy)*(1-Math.exp(-interval/(targetEnergy>priorEnergy?80:450)));
    const transient=(state.transientUntil.get(k)||0)>m.sourceTimeUs;
    const cloud=updateCloudEnvelope(previous,o.meter.rmsDbfs,activityInterval,continuous,sustained,transient);
    const weakField=updateWeakField(previous,o,interval,continuous,spaceScale,transient);
    if(weakField.active)state.weakEchoes.delete(k);
    else if(!transient&&continuous&&previous?.weakField?.active&&(o.meter.rmsDbfs===null||o.meter.rmsDbfs<-64||!o.spatial.positionValid)){
      const p=mapPosition(previous.object.spatial.position,spaceScale),from=previous.from?mapPosition(previous.from,spaceScale):null;
      const echo=createWeakEcho({p,from,intervalMs:previous.interval,weakField:previous.weakField,slot:o.objectKey.slot,startUs:m.sourceTimeUs});
      if(echo)state.weakEchoes.set(k,echo);
    }
    state.objects.set(k,{object:o,seen:state.received,trail,from:continuous?previous.object.spatial.position:o.spatial.position,interval,sustained,visualEnergy,weakField,cloud,cloudFrom:continuous?previous.cloud?.mix||0:0});}
  if(tap.snapshotComplete&&tap.quality==='live')for(const [k,v] of state.objects)if(!seen.has(k)){
    const p=v.object.spatial.positionValid?mapPosition(v.object.spatial.position,spaceScale):null;
    const from=v.from?mapPosition(v.from,spaceScale):null;
    const echo=state.received-v.seen<=300&&(state.transientUntil.get(k)||0)<=m.sourceTimeUs?createWeakEcho({p,from,intervalMs:v.interval,weakField:v.weakField,slot:v.object.objectKey.slot,startUs:m.sourceTimeUs}):null;
    if(echo)state.weakEchoes.set(k,echo);
    state.objects.delete(k);
  }
  while(state.weakEchoes.size>32)state.weakEchoes.delete(state.weakEchoes.keys().next().value);
  for(const [k,v]of state.objects)if(state.received-v.seen>2000)state.objects.delete(k);
  if(!state.paused)updateUI();
}
function quality(name){if(!state.snapshot)return 'disconnected';if(state.source==='socket'&&(!socket||socket.readyState!==WebSocket.OPEN))return 'disconnected';if(state.source==='socket'&&performance.now()-state.received>1500)return 'stale';return state.snapshot.taps[name].quality;}
const displayedChannels=()=>state.window&&state.history?state.history.channels.map(c=>({...c,band:'all',meter:{meanSquare:c.meanSquare,rmsDbfs:c.meanSquare?10*Math.log10(c.meanSquare):null,samplePeak:c.samplePeak,silent:c.validSamples>0&&c.sumSquares===0,sampleCount:c.validSamples}})):state.snapshot?.taps['render-ready'].channels||[];
function rows(target,items){target.replaceChildren();for(const [a,b]of items){const row=document.createElement('div');row.className='status-row';const span=document.createElement('span'),strong=document.createElement('b');span.textContent=a;strong.textContent=b;row.append(span,strong);target.append(row);}}
function updateUI(){
  const m=state.snapshot||state.history;if(!m)return;
  const simulated=m.dataOrigin==='simulated';$('sourceLabel').textContent=state.source==='replay'?'录制数据回放':simulated?(state.source==='import'?'导入 · 模拟数据':'模拟演示'):(state.source==='import'?'导入 · 测量记录':'测量遥测');document.querySelector('.source-tag').classList.toggle('measured',!simulated);
  const q=quality(state.mode==='navigation'?'decoded-input':'render-ready');$('quality').textContent=`${simulated?'模拟 · ':''}${labels[q]}`;
  $('inputQuality').textContent=labels[quality('decoded-input')];$('outputQuality').textContent=labels[quality('render-ready')];
  $('objectCount').textContent=m.taps?.['decoded-input'].objects.length??'—';
  const c=state.window&&state.history?state.history.coverage:m.taps[state.mode==='navigation'?'decoded-input':'render-ready'].coverage;
  $('coverage').textContent=`${(c.ratio*100).toFixed(c.ratio===1?0:1)}%`;
  $('drops').textContent=m.droppedBlocks?`${m.droppedBlocks['decoded-input']+m.droppedBlocks['render-ready']} / ${m.eventOverflow}`:'—';
  const sameLayout=state.layout?.layoutRevision===m.layoutRevision&&state.layout?.routeId===m.routeId;
  $('layoutLabel').textContent=sameLayout?'7.1.4 · 理想方向':'布局未知 · 声道表';
  $('footerState').textContent=`${m.routeId} · 48 kHz / 480 帧 · 全带`;
  if(!state.error)notice(state.source==='replay'?(state.window?'整段 60 秒录制统计 · 覆盖率按所选窗口计算':'真实录制源数据回放 · 输出为 ROAR 离线重渲染'):state.source==='socket'?(simulated?'服务提供模拟数据 · 未接通现场音频':'仅订阅遥测 · 页面不控制音频'):state.source==='import'?'离线记录 · 导入后不推进时间':state.window?'模拟历史样例 · 独立固定记录，不是动画累计结果':'模拟数据 · 未接通现场音频');
  const sig=[...state.objects.keys()].join('|');if(sig!==selectorSignature){selectorSignature=sig;const empty=document.createElement('option');empty.value='';empty.textContent='选择对象（含未定位对象）';$('objectSelect').replaceChildren(empty);for(const [k,{object:o}]of state.objects){const option=document.createElement('option');option.value=k;option.textContent=identityText(o.objectKey)+(o.kind==='static'?' · 静态声床':!o.spatial.positionValid?' · 未定位':'');$('objectSelect').append(option);}}
  $('objectSelect').value=state.objects.has(state.selected)?state.selected:'';
  const channels=displayedChannels();
  for(const c of channels){const node=document.querySelector(`[data-channel="${c.channelId}"]`);if(!node)continue;const meter=c.meter;
    node.querySelector('.meter-fill').style.height=`${meter.rmsDbfs==null?0:clamp((meter.rmsDbfs+60)/60)*100}%`;
    node.querySelector('.value').textContent=meter.sampleCount===0?'无数据':meter.silent?'静音':`${dbText(meter.rmsDbfs)} dB`;
    node.classList.toggle('selected',state.selected===c.channelId);
  }
  $('historyNote').textContent=state.window?(state.history?`窗口覆盖 ${(state.history.coverage.ratio*100).toFixed(2)}% · 线性累计 / 边界精度 1 秒`:'正在读取历史…'):'RMS · 幅值 1 为满刻度，满幅正弦约 −3.01 dBFS';
  updateInspector();
}
function updateInspector(){
  const target=$('inspector');target.classList.toggle('empty',!state.selected);
  if(!state.selected){target.textContent='选择场景中的光点或下方声道，查看位置与信号详情';return;}
  if(channelIds.includes(state.selected)){
    const c=displayedChannels().find(c=>c.channelId===state.selected);if(!c)return;
    $('selectionTitle').textContent=`声道 ${c.channelId}`;
    rows(target,[['RMS',`${dbText(c.meter.rmsDbfs)} dBFS`],['Sample peak',c.meter.samplePeak==null?'未知':`${dbText(c.meter.samplePeak?20*Math.log10(c.meter.samplePeak):null)} dBFS`],['空间定位',c.channelId==='LFE'?'不定向':'理想化方向'],['测量位置','render-ready']]);return;
  }
  const o=state.objects.get(state.selected)?.object;
  if(!o){$('selectionTitle').textContent='对象已结束';target.textContent='此生命周期已离开当前完整快照。';return;}
  $('selectionTitle').textContent=identityText(o.objectKey);
  rows(target,[['类型',o.kind==='static'?'静态声床':'动态对象'],['RMS',`${dbText(o.meter.rmsDbfs)} dBFS`],['原始坐标',o.spatial.position?.map(v=>v.toFixed(2)).join(', ')||'未知'],['声源类别',o.sourceCategory==='speech-candidate'?'人声候选 · 离线音轨':isVoiceCategory(o.sourceCategory)?'人声 · 来源已标注':o.sourceCategory||'未识别'],['应用归属',o.applicationName||'未知'],['gain',state.snapshot.taps['decoded-input'].gainMode],['位置单位','源单位 · 未校准']]);
}
for(const name of channelIds){const b=document.createElement('button');b.className='channel'+(name==='LFE'?' lfe':'');b.dataset.channel=name;b.title=`选择 ${name} 声道`;
  const head=document.createElement('div');head.className='channel-name';head.textContent=name;const label=document.createElement('span');label.textContent=name==='LFE'?'LOW':name.startsWith('T')?'TOP':'BED';head.append(label);
  const track=document.createElement('div');track.className='meter-track';const fill=document.createElement('div');fill.className='meter-fill';track.append(fill);const value=document.createElement('div');value.className='value';value.textContent='—';b.append(head,track,value);b.onclick=()=>{state.selected=name;updateUI();};$('channels').append(b);}

// Small WebGL2 renderer: shared buffers, one line pass and one point pass.
const canvas=$('scene'),gl=canvas.getContext('webgl2',{alpha:true,antialias:true});
let program,buffer,pointMode,cloudTime,vertexCount=0,picks=[];
const vertices=new Float32Array(20000*7);
const gpuTimer=gl?.getExtension('EXT_disjoint_timer_query_webgl2'),gpuPending=[];
if(gl){
  function shader(type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);assert(gl.getShaderParameter(s,gl.COMPILE_STATUS),gl.getShaderInfoLog(s));return s;}
  program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,`#version 300 es
  in vec2 p;in vec4 color;in float size;out vec4 c;void main(){gl_Position=vec4(p,0.,1.);gl_PointSize=size;c=color;}`));
  gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`#version 300 es
  precision highp float;in vec4 c;uniform int points;uniform float clock;out vec4 outColor;
  float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
  float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}
  void main(){float a=1.;if(points>0){vec2 p=(gl_PointCoord-.5)*2.;float r=length(p);if(r>1.)discard;
    if(points==2){vec2 uv=p*3.+vec2(clock*.055,-clock*.035);float n=.55*noise(uv)+.3*noise(uv*2.1)+.15*noise(uv*4.3);a=exp(-r*r*3.6)*(1.-smoothstep(.65,1.,r))*(.35+.9*n);}else a=pow(1.-r,1.3);}
    outColor=vec4(c.rgb,c.a*a);}`));
  gl.linkProgram(program);assert(gl.getProgramParameter(program,gl.LINK_STATUS),gl.getProgramInfoLog(program));gl.useProgram(program);buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,vertices.byteLength,gl.DYNAMIC_DRAW);
  for(const [name,size,offset]of [['p',2,0],['color',4,2],['size',1,6]]){const loc=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,size,gl.FLOAT,false,28,offset*4);}
  pointMode=gl.getUniformLocation(program,'points');cloudTime=gl.getUniformLocation(program,'clock');gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
}else $('fallback').hidden=false;
function project(p){return projectViewPoint(p,state,canvas.width/canvas.height);}
function vertex(p,color,size=1){const q=project(p),i=vertexCount*7;if(i+7>vertices.length)return;vertices.set([q[0],q[1],...color,size],i);vertexCount++;return q;}
function line(a,b,color){vertex(a,color);vertex(b,color);}
function flush(points){gl.uniform1i(pointMode,points==='cloud'?2:points===true?1:0);gl.bufferSubData(gl.ARRAY_BUFFER,0,vertices.subarray(0,vertexCount*7));gl.drawArrays(points==='triangles'?gl.TRIANGLES:points?gl.POINTS:gl.LINES,0,vertexCount);vertexCount=0;}
function tint(db){const t=clamp((db+60)/60),a=t<.6?[.08,.22,.29]:[.16,.64,.60],b=t<.6?[.16,.64,.60]:[.94,.82,.52],f=t<.6?t/.6:(t-.6)/.4;return a.map((v,i)=>v+(b[i]-v)*f);}
function positionAt(v,now){const p=v.object.spatial.position;if(!p)return null;const f=clamp((now-v.seen)/v.interval);return mapPosition(v.from?v.from.map((x,i)=>x+(p[i]-x)*f):p,spaceScale);}
function ring(p,r,alpha,color=[.32,.83,.72]){const n=unit(p),u=unit(Math.abs(n[1])<.9?[n[2],0,-n[0]]:[1,0,0]),v=[n[1]*u[2]-n[2]*u[1],n[2]*u[0]-n[0]*u[2],n[0]*u[1]-n[1]*u[0]];const point=a=>p.map((x,i)=>x+r*(Math.cos(a)*u[i]+Math.sin(a)*v[i]));for(let i=0;i<24;i++)line(point(i*Math.PI/12),point((i+1)*Math.PI/12),[...color,alpha]);}
function drawWeakField(now,dpr,alpha,haloKeys){
  const candidates=[];
  for(const v of state.objects.values()){
    if(v.object.kind!=='dynamic'||visualLayer(v.object)==='voice'||(v.weakField?.mix||0)<.12||!v.object.spatial.positionValid||state.weakEchoes.has(keyOf(v.object.objectKey))||haloKeys.has(keyOf(v.object.objectKey))||(state.transientUntil.get(keyOf(v.object.objectKey))||0)>state.snapshot.sourceTimeUs)continue;
    const p=positionAt(v,now);if(p)candidates.push({p,mix:v.weakField.mix,dwell:v.weakField.dwell,outer:v.weakField.outer,slot:v.object.objectKey.slot});
  }
  const snapshotUs=state.snapshot?.sourceTimeUs||0;
  const advancing=!state.paused&&(state.source!=='replay'||!!replay?.playing);
  const visualUs=snapshotUs+(advancing?Math.min(state.source==='replay'?100:1200,Math.max(0,now-state.received))*1000:0);
  for(const [key,echo] of state.weakEchoes){
    const candidate=sampleWeakEcho(echo,visualUs);
    if(candidate?.mix>=.12)candidates.push(candidate);
    else if(!candidate)state.weakEchoes.delete(key);
  }
  const anchors=selectWeakAnchors(candidates);
  const time=state.reduced?0:now/1000;
  const flicker=slot=>state.reduced?1:.84+.10*Math.sin(time*.54+slot*1.7)+.06*Math.sin(time*1.1+slot*.31);
  const strands=[],strokes=[],membranes=[],incident=new Map();
  const trace=(a,b,color)=>strokes.push({a,b,color});
  for(const a of anchors){
    const growth=a.echo?a.reach:clamp((a.dwell-650)/1150),breath=a.echo||state.reduced?1:.90+.10*Math.sin(time*.42+a.slot*.63);
    const maturity=state.reduced?0:clamp((a.dwell-950)/850),roots=[a.p];
    const peripheralGain=1-.30*clamp((a.outer-.4)/.6);
    const glow=a.mix*flicker(a.slot)*alpha*peripheralGain,paths=growWeakFilaments(a.p,a.slot,growth*breath,time)
      .map(path=>({...path,points:path.points.map(p=>swimWeakPoint(p,roots,time,a.outer,maturity,weakInertia))}));
    for(const path of paths){
      const points=path.points;
      for(let i=0;i<points.length-1;i++){
        const midpoint=points[i].map((x,j)=>(x+points[i+1][j])*.5);
        const distance=Math.hypot(...midpoint.map((x,j)=>x-a.p[j]));
        trace(points[i],points[i+1],[.30,.73,.77,weakFilamentBrightness(distance,path)*glow]);
      }
      if(!path.branch&&!path.outward)strands.push({points,glow});
    }
    const membraneGrowth=clamp((a.dwell-1700)/300);
    if(membraneGrowth>0)for(const triangle of weakMembraneFromFilaments(paths,a.outer,membraneGrowth))
      membranes.push(triangle.map(({p,alpha:opacity})=>({p,opacity:opacity*glow})));
  }
  for(const {i:ia,j:ib,block,component} of weakNetworkBlocks(anchors,buildWeakNetwork(anchors))){
    const a=anchors[ia],b=anchors[ib],reach=clamp(Math.min(a.reach??1,b.reach??1,(Math.min(a.dwell,b.dwell)-650)/1050));
    const flash=state.reduced?0:weakBlockFlash(block,component,time);
    const peripheralGain=1-.30*clamp((Math.min(a.outer,b.outer)-.4)/.6);
    const weight=Math.min(a.mix,b.mix)*Math.min(flicker(a.slot),flicker(b.slot))*alpha*peripheralGain;
    const raw=exploreWeakConnection(a.p,b.p,a.slot*37+b.slot*19,reach,time);
    const maturity=state.reduced?0:clamp((Math.min(a.dwell,b.dwell)-950)/850),roots=[a.p,b.p];
    const outer=Math.min(a.outer,b.outer);
    const points=raw.points.map(p=>swimWeakPoint(p,roots,time,outer,maturity,weakInertia));
    const bridge={connected:raw.connected,points,left:points.slice(0,raw.left.length),right:points.slice(points.length-raw.right.length)};
    const pulseAt=bridge.connected&&!state.reduced?(time*.18+(a.slot*3+b.slot)*.071)%1:null;
    for(const [side,path] of [bridge.left,bridge.right].entries())for(let i=0;i<path.length-1;i++){
      const segment=side===0?i:points.length-path.length+i;
      const t=(segment+.5)/(points.length-1);
      const brightness=weakConnectionBrightness(t,flash,pulseAt);
      trace(path[i],path[i+1],[.39+.12*flash,.84+.10*flash,.83+.08*flash,brightness*weight]);
    }
    if(bridge.connected){
      strands.push({points:bridge.points,glow:weight*.30,bridge:true});
      if(Math.min(a.outer,b.outer)>=.5){
        for(const [index,points] of [[ia,bridge.points],[ib,[...bridge.points].reverse()]]){
          if(!incident.has(index))incident.set(index,[]);
          incident.get(index).push({points,outer:Math.min(a.outer,b.outer),weight,flash});
        }
      }
    }
  }
  for(const paths of incident.values())for(let i=0;i<paths.length;i++)for(let j=i+1;j<paths.length;j++){
    const a=paths[i],b=paths[j],weight=Math.min(a.weight,b.weight)*(.72+.28*Math.max(a.flash,b.flash));
    for(const triangle of weakMembraneBetween(a.points,b.points,Math.min(a.outer,b.outer)))
      membranes.push(triangle.map(({p,alpha:opacity})=>({p,opacity:opacity*weight})));
  }
  for(const triangle of membranes)for(const {p,opacity} of triangle)vertex(p,[.19,.60,.63,opacity]);
  flush('triangles');
  for(const stroke of strokes)line(stroke.a,stroke.b,stroke.color);
  flush(false);
  gl.uniform1f(cloudTime,time%1000);
  for(const strand of strands)for(const fraction of [.24,.5]){
    const point=strand.points[Math.min(strand.points.length-1,Math.floor(fraction*(strand.points.length-1)))];
    const gradient=strand.bridge?(.18+.82*Math.abs(2*fraction-1)):(1-fraction)*(1-fraction);
    vertex(point,[.12,.43,.49,.06*strand.glow*gradient],(34-8*fraction)*dpr);
  }
  flush('cloud');
  for(const a of anchors)vertex(a.p,[.32,.72,.66,.21*a.mix*flicker(a.slot)*alpha],(4+5*a.mix)*dpr);
  flush(true);
}
function drawCloud(now,dpr,alpha,haloKeys){
  const time=state.reduced?0:now/1000,anchors=[],puffs=[];
  for(const v of state.objects.values()){
    if(v.object.kind!=='dynamic'||visualLayer(v.object)==='voice'||!v.cloud?.visible||!v.object.spatial.positionValid||haloKeys.has(keyOf(v.object.objectKey))||(state.transientUntil.get(keyOf(v.object.objectKey))||0)>state.snapshot.sourceTimeUs)continue;
    const p=positionAt(v,now),energy=(v.visualEnergy||0)*(1-.9*(v.weakField?.mix||0));
    if(!p||energy<.08)continue;
    const f=clamp((now-v.seen)/v.interval),envelope=v.cloudFrom+(v.cloud.mix-v.cloudFrom)*f;
    if(envelope<.02)continue;
    anchors.push({p,energy,envelope,slot:v.object.objectKey.slot,sustain:clamp(v.sustained/700),depth:cloudDepthStyle(Math.hypot(...p))});
  }
  anchors.sort((a,b)=>b.energy*b.envelope-a.energy*a.envelope);anchors.length=Math.min(48,anchors.length);
  for(const a of anchors)for(let i=0;i<4;i++){
    const phase=a.slot*2.39996+i*1.7,spread=(.08+i*.065)*a.depth.spread;
    const p=a.p.map((x,j)=>x+Math.sin(phase+j*2.1+time*.16)*spread);
    puffs.push({p,energy:a.energy*(.55+.45*a.sustain),size:(115+70*a.energy+15*i)*a.depth.size*(.82+.18*a.envelope),opacity:.36*a.depth.opacity*a.envelope,color:i%2?[.25,.52,.66]:[.25,.66,.55]});
  }
  puffs.sort((a,b)=>project(b.p)[2]-project(a.p)[2]);
  gl.uniform1f(cloudTime,time%1000);
  for(const p of puffs){const depth=project(p.p)[2],perspective=clamp(3.9/Math.max(.5,3.9+depth),.45,1.7);vertex(p.p,[...p.color,p.opacity*p.energy*alpha],Math.min(240,p.size*perspective)*dpr);}
  flush('cloud');
}
function drawVoiceArcs(now,alpha){
  const voices=[];
  for(const v of state.objects.values()){
    if(!voiceArcVisible(v.object,v.visualEnergy)||!v.object.spatial.positionValid)continue;
    const p=positionAt(v,now);if(p)voices.push({p,object:v.object,energy:v.visualEnergy||0});
  }
  voices.sort((a,b)=>b.energy-a.energy);
  const normal=[-Math.sin(state.yaw)*Math.cos(state.tilt),Math.sin(state.tilt),Math.cos(state.yaw)*Math.cos(state.tilt)];
  for(const v of voices.slice(0,8)){
    const phase=state.reduced?0:now/700+v.object.objectKey.slot*.9;
    const opacity=(.22+.24*v.energy)*alpha;
    for(const s of voiceArcSegments(v.p,voiceArcRadius(-60+60*v.energy),phase,normal))
      line(s.a,s.b,[.71,.81,.97,opacity*s.opacity]);
  }
  flush(false);
}
function navigationStep(now){
  const dt=Math.min(.1,(now-lastNavigation)/1000||.05);lastNavigation=now;
  const advancing=state.source!=='replay'||replay?.playing;
  const displayNav=!baseline&&!legacyMode?forecast.step(navigation,now,{allow:advancing}):navigation;
  const prediction=displayNav.result.prediction;
  const fresh=(now-state.received<350||prediction?.active)&&advancing;
  const manual=!!drag||now<manualUntil;
  heading.step(displayNav,dt,{manual,reduced:state.reduced,fresh});
  const rollFresh=state.snapshot?.telemetrySeq!==lastRollSeq;
  if(rollFresh)lastRollSeq=state.snapshot?.telemetrySeq;
  const motionEnabled=state.mode==='navigation'&&!baseline&&!legacyMode&&!manual&&!state.reduced&&fresh&&!heading.phase&&
    (displayNav.result.navigationCandidate?.valid||prediction?.active);
  // Return-to-center and forecast reconciliation are camera presentation,
  // not new measured angular motion. Never feed them back into prop inertia.
  const observationDt=Math.max(.05,(state.snapshot?.sourceSequenceRange.endExclusive-state.snapshot?.sourceSequenceRange.start)*.01||.1);
  const angular=[
    rollFresh&&navigation.result.valid?navigation.result.sceneDelta[1]/observationDt:displayNav.result.visualAngularVelocity?.[0]||0,
    rollFresh&&navigation.result.valid?navigation.result.sceneDelta[0]/observationDt:displayNav.result.visualAngularVelocity?.[1]||0,
    rollFresh&&navigation.result.valid&&navigation.result.confidence>=.7?navigation.result.sceneDelta[2]/observationDt:0
  ];
  weakInertia.step(heading.flow,angular,dt,{enabled:motionEnabled});
  toiletFollow.step(heading.flow.map(value=>-value),-angular[1],dt,{enabled:motionEnabled});
  lastHeadingYaw=heading.yaw;lastHeadingPitch=heading.pitch;
  for(let i=0;i<3;i++)gridOffset[i]=(gridOffset[i]+heading.flow[i]*dt)% .5;
  const r=navigation.result,translation=r.translation,turn=Math.abs(r.sceneDelta[0])>.0003?(r.sceneDelta[0]>0?'场景左转':'场景右转'):'旋转稳定';
  $('headingStatus').textContent=baseline?'性能对照 · 航向与分层关闭':!fresh?'参考暂停 · 回稳':r.stationaryLock?.active?'远处静止参考锁定 · 低参考位姿暂停':prediction?.pose==='holding'?'低置信度姿态保持 · 等待观测':prediction?.pose==='predicted'?'姿态短时前推 · 等待观测':prediction?.pose==='reconciling'?'姿态回归观测':r.valid?`${turn} · 一致度 ${Math.round(r.confidence*100)}%${legacyMode?' · 旧算法':''}`:r.reason;
  if(legacyMode&&!baseline)$('headingStatus').textContent=`旧算法对照 · ${$('headingStatus').textContent}`;
  $('flowStatus').textContent=prediction?.flow==='predicted'?`流向短时前推：${direction(displayNav.result.visualFlow||[0,0,0],.04)}`:prediction?.flow==='reconciling'?`流向回归观测：${direction(displayNav.result.visualFlow||[0,0,0],.04)}`:r.cooperativeFlow?.valid?`多源${direction(r.cooperativeFlow.scene,.04)}移 → ${direction(r.cooperativeFlow.observer,.04)}移候选`:translation?state.source==='replay'?`共同空间流：${direction(translation,.04)} · 源单位`:`模拟场景流：${direction(translation)}移`:'共同流动：参考不足';
  $('flowStatus').dataset.flow=JSON.stringify({cooperative:r.cooperativeFlow?.valid||false,scene:r.cooperativeFlow?.scene||translation||null,observer:r.cooperativeFlow?.observer||null,confidence:r.cooperativeFlow?.confidence||0,prediction:prediction?.flow||'none'});
  $('energyStatus').textContent=`全声场能量方位流：${r.energyAvailable?direction(r.energyFlow):'参考不足'}`;
  $('energyStatus').title=r.flowAvailable?`其中独立声源残差：${direction(r.dominantFlow)}；已从此项扣除共同运动`:'能量方位流包含所有连续对象，不扣除共同运动；独立声源残差参考不足';
  const shape=pattern.result,shapeVisible=!baseline&&shape.valid&&(state.source!=='socket'||now-state.received<350);
  $('patternStatus').hidden=!shapeVisible;
  if(shapeVisible){const db=shape.anchors.map(a=>Math.round(a.db)),gradient=shape.gradientValid?` · 稳定强度梯度（${shape.gradientDirection==='far-to-near-rising'?'近端':'远端'}更强）`:'';$('patternStatus').textContent=`纵深点列 · 远/中/近 ${db.join(' / ')} dBFS${gradient}`;}
  $('patternStatus').dataset.pattern=JSON.stringify({valid:shapeVisible,kind:shape.kind,slots:shape.anchors.map(a=>Number(a.key.split(':').at(-2))),db:shape.anchors.map(a=>a.db),span:shape.span,frames:shape.frames,gradient:shape.gradientValid?shape.gradientDirection:null});
  const idle=state.mode==='navigation'&&!r.valid&&!['predicted','reconciling'].includes(prediction?.pose)&&!state.reduced&&!state.paused;
  $('headingNote').textContent=heading.phase?'参考网格重定位':now<manualUntil?'手动观察 · 自动参考暂缓':idle?(r.cooperativeFlow?.valid?'流向候选 · 中心微幅浮动':'参考不足 · 中心微幅浮动'):prediction?.pose==='predicted'?'短时前推 · 等待新观测':prediction?.pose==='reconciling'?'前推校正 · 平滑回归':`共同旋转候选 · 视觉偏转 ${(heading.yaw/rad).toFixed(0)}° / ${(heading.pitch/rad).toFixed(0)}°`;
  $('headingStatus').dataset.relocations=heading.relocations;
  $('headingStatus').dataset.estimate=JSON.stringify({algorithm:legacyMode?'legacy':'weighted',yaw:navigation.yaw/rad,pitch:navigation.pitch/rad,confidence:r.confidence,valid:r.valid,inlierRatio:r.inlierRatio??null,medianResidual:r.medianResidual??null,degenerate:r.degenerate??null,groups:r.groupMotion?.groups??0,signals:r.navigationCandidate?.signals??[],observerStatus:r.observerMotion?.status??'legacy-unknown',prediction:prediction||null});
}
function draw(now){
  if(!gl)return;const start=performance.now();let query=null;
  if(gpuTimer){while(gpuPending.length&&gl.getQueryParameter(gpuPending[0],gl.QUERY_RESULT_AVAILABLE)){const q=gpuPending.shift();if(!gl.getParameter(gpuTimer.GPU_DISJOINT_EXT))record('gpu',gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q);}if(gpuPending.length<4){query=gl.createQuery();gl.beginQuery(gpuTimer.TIME_ELAPSED_EXT,query);}}
  const dpr=Math.min(devicePixelRatio,1.5),w=Math.round(canvas.clientWidth*dpr),h=Math.round(canvas.clientHeight*dpr);
  if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;gl.viewport(0,0,w,h);}gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);picks=[];
  const active=state.mode==='navigation'&&!baseline,grid=[.27,.52,.53,.21*(active?heading.fade:1)],ref=rotation(active?heading.yaw:0,active?heading.pitch:0);
  const gridLine=(a,b)=>line(apply(ref,a),apply(ref,b),grid),gridNodes=[];
  if(active){
    const offsetX=gridOffset[0],offsetZ=gridOffset[2],time=state.reduced?0:now/1000;
    const flowLine=(axis,lane,offset)=>{
      let previous=null;
      for(let step=0;step<=48;step++){
        const coordinate=-1.5+step/16;
        const source=axis==='x'?[coordinate,-.8,lane]:[lane,-.8,coordinate];
        const current={p:apply(ref,source),opacity:gridFlowOpacity(coordinate,offset,source[0],source[2],time)*heading.fade};
        if(previous){vertex(previous.p,[.27,.52,.53,previous.opacity]);vertex(current.p,[.27,.52,.53,current.opacity]);}
        previous=current;
      }
    };
    for(let i=-3;i<=3;i++){
      flowLine('x',i*.5+offsetZ,offsetX);
      flowLine('z',i*.5+offsetX,offsetZ);
    }
    for(let x=-3;x<=3;x++)for(let z=-3;z<=3;z++){
      const px=x*.5+offsetX,pz=z*.5+offsetZ;
      if(Math.abs(px)<=1.5&&Math.abs(pz)<=1.5)gridNodes.push({p:apply(ref,[px,-.8,pz]),opacity:gridJunctionOpacity(px,pz,time)*heading.fade});
    }
  }else for(let i=-3;i<=3;i++){const p=i*.5;gridLine([-1.5,-.8,p],[1.5,-.8,p]);gridLine([p,-.8,-1.5],[p,-.8,1.5]);}
  flush(false);
  if(gridNodes.length){
    gl.uniform1f(cloudTime,(state.reduced?0:now/1000)%1000);
    for(const node of gridNodes)vertex(node.p,[.27,.52,.53,node.opacity*.22],15*dpr);
    flush('cloud');
    for(const node of gridNodes)vertex(node.p,[.36,.67,.67,node.opacity],4*dpr);
    flush(true);
  }
  if(state.mode==='navigation'){
    // Pose about the model's own bounding-box center, then position it below
    // the fixed listener/head origin. The camera projection remains shared.
    const pose=toiletTransform(toiletCenter,toiletFollow,state.toiletHeight);
    const posed=toiletLines.map(([a,b])=>[pose(a),pose(b)]);
    if(now-lastToiletOcclusion>=50||toiletHidden.length!==posed.length){
      const occluders=prepareToiletOccluders(toiletFaces,pose,project);
      toiletHidden=posed.map(([a,b])=>coveredToiletLine(a,b,occluders,project));
      lastToiletOcclusion=now;
    }
    for(let i=0;i<posed.length;i++)line(posed[i][0],posed[i][1],[.65,.91,.84,toiletHidden[i] ? .075 : .46]);
  }
  if(state.mode==='spectrum')for(let ring=0;ring<3;ring++)for(let i=0;i<96;i++){const a=i*Math.PI/48,b=(i+1)*Math.PI/48;
    const p=t=>ring===0?[Math.cos(t),0,Math.sin(t)]:ring===1?[Math.cos(t),Math.sin(t),0]:[0,Math.sin(t),Math.cos(t)];line(p(a),p(b),[.3,.5,.53,.21]);}
  if(state.mode==='spectrum'){line([-1.4,0,0],[1.4,0,0],[.42,.68,.65,.26]);line([0,0,-1.4],[0,0,1.4],[.42,.68,.65,.26]);}
  flush(false);
  const m=state.snapshot||state.history;if(!m){if(query){gl.endQuery(gpuTimer.TIME_ELAPSED_EXT);gpuPending.push(query);}return;}
  const age=state.source==='socket'?now-state.received:0,alpha=age>1500?.15:1;
  const layoutOk=state.layout?.layoutRevision===m.layoutRevision&&state.layout?.routeId===m.routeId;
  if(state.mode==='spectrum'&&layoutOk){
    const channels=displayedChannels(),directions=state.layout.channels.filter(c=>c.direction&&c.channelId!=='LFE');
    for(let i=0;i<1300;i++){const y=1-2*(i+.5)/1300,r=Math.sqrt(1-y*y),a=i*2.39996,p=[Math.sin(a)*r,y,Math.cos(a)*r];let sum=0,weight=0;
      for(const c of directions){const v=channels.find(x=>x.channelId===c.channelId)?.meter.meanSquare;if(v==null)continue;const dot=p.reduce((s,x,j)=>s+x*c.direction[j],0),k=Math.exp(10*(dot-1));sum+=k*v;weight+=k;}
      const db=sum>0?10*Math.log10(sum/Math.max(weight,1e-9)):-60,col=tint(db);vertex(p,[...col,.12+clamp((db+60)/60)*.65],12*dpr);}
    for(const c of directions){const q=vertex(c.direction,[.79,.94,.83,.95],(state.selected===c.channelId?23:16)*dpr);picks.push({q,key:c.channelId});}
  }
  if(state.mode==='navigation'&&state.snapshot){
    const advancing=state.source==='demo'||state.source==='socket'||state.source==='replay'&&replay?.playing;
    const sourceNow=m.sourceTimeUs+(advancing?Math.min(state.source==='socket'?Infinity:100,Math.max(0,now-state.received))*1000:0);
    const haloEvents=visibleOnsets(sourceNow),haloKeys=new Set(haloEvents.map(e=>keyOf(e.objectKey)));
    if(!baseline){drawCloud(now,dpr,alpha,haloKeys);drawWeakField(now,dpr,alpha,haloKeys);drawVoiceArcs(now,alpha);}
    if(!baseline&&pattern.result.valid&&(state.source!=='socket'||now-state.received<350)){
      const anchors=pattern.result.anchors.map(a=>({p:mapPosition(a.position,spaceScale),strength:clamp((a.db+60)/40,.12,1)}));
      for(const a of anchors)ring(a.p,.035+.035*a.strength,.12+.20*a.strength,[.57,.87,.93]);
      flush(false);
    }
    for(const [key,value]of state.objects){const {object:o,seen}=value;let p=positionAt(value,now);
      if(o.kind==='static'&&layoutOk)p=state.layout.channels.find(c=>c.staticType===o.staticType)?.direction;
      if((o.kind==='dynamic'&&!o.spatial.positionValid)||!p)continue;
      const strength=clamp(((o.meter.rmsDbfs??-60)+60)/60),fade=now-seen>1500&&state.source==='socket'?.15:alpha;
      const weak=baseline?0:value.weakField?.mix||0,voice=!baseline&&visualLayer(o)==='voice',transient=haloKeys.has(key)||(state.transientUntil.get(key)||0)>m.sourceTimeUs;
      const nodePriority=baseline||voice||transient?0:clamp((1.2-Math.hypot(...p))/.8)*clamp((.35-weak)/.35)*clamp((strength-.03)/.2);
      if(!transient)vertex(p,[.35,.78,.73,(.18*strength*(1-.8*weak)*(voice?.28:1)+.05*nodePriority)*fade],(22+strength*(!baseline&&value.sustained>350?55:27))*(1-.35*weak)*(voice?.65:1)*dpr);
      if(!baseline&&!state.reduced&&strength>0&&strength<.35)for(let i=0;i<3;i++){const a=now/700+i*2.1+o.objectKey.slot;vertex(p.map((x,j)=>x+(j===0?Math.cos(a):j===1?Math.sin(a):0)*.015),[.38,.68,.73,strength*.4],2*dpr);}
      const q=vertex(p,voice?[.82,.87,1,(.12+strength*.6)*fade]:[.65,.98,.85,Math.min(1,(.05+strength*.95)*(1-.55*weak)+.16*nodePriority)*fade],(2+strength*12+1.5*nodePriority+(state.selected===key?7:0))*dpr);picks.push({q,key});
    }
    if(!baseline&&!state.reduced){
      const events=haloEvents;
      flush(true);
      for(const e of events){const t=(sourceNow-e.sourceTimeUs)/(e.expiresTimeUs-e.sourceTimeUs),p=mapPosition(e.spatial.position,spaceScale);if(!norm(p))continue;
        ring(p,.025+t*.28,(1-t)*(1-t)*.35,[.96,.79,.48]);
      }flush(false);
      for(const e of events){const t=(sourceNow-e.sourceTimeUs)/(e.expiresTimeUs-e.sourceTimeUs),p=mapPosition(e.spatial.position,spaceScale);vertex(p,[1,.84,.52,Math.exp(-t*10)],(8+40*Math.exp(-t*12))*dpr);for(let i=0;i<18;i++){const offset=burstOffset(i,t,e.objectKey.slot);vertex(p.map((x,j)=>x+offset[j]),[.96,.79,.48,(1-t)*(1-t)*.8],(2+4*(1-t))*dpr);}}
    }
  }
  flush(true);
  if(query){gl.endQuery(gpuTimer.TIME_ELAPSED_EXT);gpuPending.push(query);}record('cpu',performance.now()-start);
  if(stats.cpu.length%30===0){const report={};for(const [name,values]of Object.entries(stats)){const sorted=[...values].sort((a,b)=>a-b);report[name]={n:sorted.length,p50:sorted[Math.floor(sorted.length*.5)]??null,p95:sorted[Math.floor(sorted.length*.95)]??null};}canvas.dataset.performance=JSON.stringify(report);}
}
let drag=null;
function markView(name){document.querySelectorAll('[data-view]').forEach(button=>{
  const active=button.dataset.view===name;button.classList.toggle('active',active);button.setAttribute('aria-pressed',active);
});}
function viewFitPoints(now){
  const points=[[0,0,0]];
  for(const value of state.objects.values()){
    const object=value.object;
    const p=object.kind==='static'&&state.layout?.channels
      ?state.layout.channels.find(channel=>channel.staticType===object.staticType)?.direction
      :object.spatial.positionValid?positionAt(value,now):null;
    if(p?.every(Number.isFinite))points.push(p);
  }
  const pose=toiletTransform(toiletCenter,toiletFollow,state.toiletHeight);
  for(const [a,b] of toiletLines){points.push(pose(a),pose(b));}
  return points;
}
function setNamedView(name){
  const aspect=canvas.clientWidth/Math.max(1,canvas.clientHeight);
  Object.assign(state,namedView(name,defaultView,viewFitPoints(performance.now()),aspect));
  markView(name);manualUntil=performance.now()+2500;lastToiletOcclusion=-Infinity;
  if(state.paused)draw(performance.now());
}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>setNamedView(button.dataset.view));
const scenePanel=document.querySelector('.scene-panel');
const fullscreenButton=$('sceneFullscreen');
function syncSceneFullscreen(){
  const active=document.fullscreenElement===scenePanel;
  fullscreenButton.textContent=active?'退出全屏':'⛶ 全屏';
  fullscreenButton.setAttribute('aria-label',active?'退出三维视图全屏':'三维视图全屏');
  fullscreenButton.setAttribute('aria-pressed',active);
}
fullscreenButton.onclick=async()=>{
  try{
    if(document.fullscreenElement===scenePanel)await document.exitFullscreen();
    else await scenePanel.requestFullscreen();
  }catch{notice('当前浏览器无法将三维视图切换为全屏');}
};
document.addEventListener('fullscreenchange',syncSceneFullscreen);
syncSceneFullscreen();
canvas.onpointerdown=e=>{manualUntil=performance.now()+2500;drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY};canvas.setPointerCapture(e.pointerId);};
canvas.onpointermove=e=>{if(!drag)return;manualUntil=performance.now()+2500;
  Object.assign(state,dragView(state,e.clientX-drag.x,e.clientY-drag.y));
  markView(null);drag.x=e.clientX;drag.y=e.clientY;};
canvas.onpointerup=e=>{manualUntil=performance.now()+2500;if(drag&&Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)<5){const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*2-1,y=1-(e.clientY-r.top)/r.height*2;let best=null,dist=.06;
  for(const pick of picks){const d=Math.hypot((pick.q[0]-x)*r.width/r.height,pick.q[1]-y);if(d<dist){best=pick.key;dist=d;}}if(best){state.selected=best;updateUI();}}drag=null;};
canvas.onpointercancel=()=>drag=null;
canvas.ondblclick=()=>{$('reset').click();};
canvas.addEventListener('wheel',e=>{e.preventDefault();manualUntil=performance.now()+2500;state.zoom=clamp(state.zoom*Math.exp(-e.deltaY*.001),.05,1.8);markView(null);},{passive:false});
function setMode(mode){state.mode=mode;$('viewSwitch').hidden=mode!=='navigation';document.querySelectorAll('[data-mode]').forEach(b=>{const selected=b.dataset.mode===mode;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',selected);});
  $('title').replaceChildren(document.createTextNode(mode==='navigation'?'感受声场的流动':'观察声道的分布'));const tag=document.createElement('span');tag.textContent=mode==='navigation'?'声航':'声谱';$('title').append(tag);
  $('subtitle').textContent=mode==='navigation'?'开放空间中的声音向量、能量云雾与局部瞬态。':'理想扬声器布局上的声道能量示意，固定色标，支持时间窗口。';
  $('headingPanel').hidden=mode!=='navigation';$('navigationControls').hidden=mode!=='navigation'||state.source!=='demo';$('sceneContext').textContent=mode==='navigation'?'监听者相对方位':'声道能量示意';$('meaning').textContent=mode==='navigation'?'开放声场 · 原始向量压缩映射；云雾为能量意象，不代表真实距离或声压。':'热区是声道能量示意，不代表真实声压、房间反射或测得声束。';updateUI();}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
$('reset').onclick=()=>setNamedView('oblique');
$('saveView').onclick=()=>{defaultView={yaw:state.yaw,tilt:state.tilt,zoom:state.zoom,toiletHeight:state.toiletHeight};
  try{localStorage.setItem('nsp.defaultView',JSON.stringify(defaultView));}catch{}
  markView('oblique');$('saveView').textContent='已保存斜侧';setTimeout(()=>$('saveView').textContent='保存斜侧视角',1800);};
$('pause').onclick=()=>{state.paused=!state.paused;$('pause').textContent=state.paused?'▶ 继续画面':'Ⅱ 暂停画面';$('pause').setAttribute('aria-pressed',state.paused);$('pausedBadge').hidden=!state.paused;if(!state.paused)updateUI();};
$('motion').checked=state.reduced;$('motion').onchange=e=>state.reduced=e.target.checked;
$('clearSelection').onclick=()=>{state.selected=null;$('selectionTitle').textContent='选中对象';updateUI();};
$('objectSelect').onchange=e=>{state.selected=e.target.value||null;updateUI();};
$('settings').onclick=()=>{$('connection').hidden=!$('connection').hidden;$('settings').setAttribute('aria-expanded',!$('connection').hidden);};
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','BUTTON'].includes(e.target.tagName))return;if(e.code==='Space'){e.preventDefault();$('pause').click();}if(e.key.toLowerCase()==='r')$('reset').click();if(e.key==='Escape')$('clearSelection').click();});
function disconnect(){socketGeneration++;if(socket){socket.close();socket=null;}negotiated=false;$('connect').textContent='连接';}
function resetSource(source){disconnect();replayGeneration++;replay?.dispose();replay=null;replayManifest=null;$('replayBar').hidden=true;historyGeneration++;state.source=source;navigation.reset();heading.reset();weakInertia.reset();toiletFollow.reset();lastHeadingYaw=lastHeadingPitch=0;lastRollSeq=-1;forecast.reset();pattern.reset();gridOffset=[0,0,0];$('navigationControls').hidden=source!=='demo'||state.mode!=='navigation';state.scope='';state.snapshot=null;state.history=null;state.window=0;state.events.clear();state.objects.clear();state.weakEchoes.clear();state.transientUntil.clear();state.selected=null;state.error='';selectorSignature='reset';document.querySelectorAll('[data-window]').forEach(b=>b.classList.toggle('active',b.dataset.window==='0'));}
for(const [value,label]of Object.entries(scenarios)){const option=document.createElement('option');option.value=value;option.textContent=label;$('scenario').append(option);}
$('scenario').onchange=()=>{scenario=$('scenario').value;$('demo').click();};
$('demo').onclick=async()=>{resetSource('demo');state.layout=await fetchJSON('fixtures/layout.json');demoStart=performance.now();demoTick=0;lastDemo=0;simulate(demoStart);};
async function fetchJSON(path){const r=await fetch(fixtureRoot+path);assert(r.ok,`读取失败 ${r.status}`);return r.json();}
async function getHistory(seconds){const request=++historyGeneration;
  if(!seconds){state.window=0;state.history=null;updateUI();return;}
  try{let data;
    if(state.source==='demo')data=await fetchJSON(seconds===600?'fixtures/history-partial-600.json':'fixtures/history-silent-3600.json');
    else if(state.source==='replay'){const r=await fetch(`/replay/history-${seconds}.json`);assert(r.ok,'录制历史读取失败');data=await r.json();}
    else if(state.source==='socket'){const url=new URL($('endpoint').value);url.protocol=url.protocol==='wss:'?'https:':'http:';url.pathname=`/v1/routes/${encodeURIComponent(state.snapshot.routeId)}/history`;url.search=`?window=${seconds}&band=all`;const r=await fetch(url,{signal:AbortSignal.timeout(5000)});assert(r.ok,`历史请求失败 ${r.status}`);data=await r.json();}
    else throw Error('请先导入对应窗口的 history JSON');
    validate(data);assert(data.messageType==='history'&&data.requestedWindowSeconds===seconds,'历史窗口不匹配');if(state.source==='socket')assert(['routeId','sessionId','streamEpoch','layoutRevision','meterRevision'].every(k=>data[k]===state.snapshot[k]),'历史与当前配置不同，请重新读取');if(request!==historyGeneration)return;state.window=seconds;state.history=data;
    document.querySelectorAll('[data-window]').forEach(b=>b.classList.toggle('active',Number(b.dataset.window)===seconds));updateUI();if(state.source==='demo')notice('模拟历史样例 · 独立固定记录，不是演示动画的累计结果');
  }catch(e){state.error=e.message;notice(e.message);}
}
document.querySelectorAll('[data-window]').forEach(b=>b.onclick=()=>getHistory(Number(b.dataset.window)));
function replayStatus(status,position,error){
  const time=seconds=>`${String(Math.floor(seconds/60)).padStart(2,'0')}:${(seconds%60).toFixed(1).padStart(4,'0')}`;
  $('replayTime').textContent=`${time(position)} / ${time((replayManifest?.durationUs||60000000)/1e6)}`;
  if(!replayScrubbing)$('replaySeek').value=position;
  $('replayStatus').textContent=({playing:'回放中',paused:'已暂停',ended:'回放结束',buffering:'缓冲中',error:'读取失败'})[status];
  $('replayPlay').textContent=status==='playing'?'暂停回放':status==='ended'?'重新回放':'播放回放';
  if(error){state.error=error;notice(error);}
}
async function loadReplay(){
  resetSource('replay');const generation=replayGeneration;
  state.paused=false;$('pause').textContent='Ⅱ 暂停画面';$('pause').setAttribute('aria-pressed','false');$('pausedBadge').hidden=true;
  notice('正在加载 Wwise 一分钟录制记录…');
  try{
    const response=await fetch('/replay/manifest.json');assert(response.ok,'尚未生成回放数据，请先运行 prepare-replay.py');const manifest=await response.json();if(generation!==replayGeneration)return;
    assert(manifest.dataOrigin==='measured'&&manifest.playbackKind==='recorded-source-offline-rerender','回放来源不符合预期');
    const responseLayout=await fetch('/replay/layout.json');assert(responseLayout.ok,'缺少回放布局');const loadedLayout=validate(await responseLayout.json());if(generation!==replayGeneration)return;
    const voiceProbe=new URLSearchParams(location.search).get('voice')==='probe';
    let overlay=frame=>frame;
    if(voiceProbe){
      const labelsResponse=await fetch('/replay/voice-candidates.json');
      assert(labelsResponse.ok,'缺少离线人声候选标签');
      overlay=createVoiceOverlay(await labelsResponse.json(),manifest);
      if(generation!==replayGeneration)return;
      notice('已加载原始音轨的人声活动候选标签；结果尚未经过人工核验');
    }
    replayManifest=manifest;state.layout=loadedLayout;
    const player=new Replay(manifest,async file=>{const r=await fetch(`/replay/${file}`);assert(r.ok,'回放分块读取失败');const frames=await r.json();for(const frame of frames)validate(overlay(frame));return frames;},
      (frame,seek,previousFrames=[])=>{if(generation!==replayGeneration)return;if(seek){state.scope='';state.snapshot=null;state.objects.clear();state.weakEchoes.clear();state.transientUntil.clear();state.events.clear();state.selected=null;}accept(frame);
        if(seek&&!baseline){pattern.reset();for(const previous of previousFrames)pattern.ingest(previous);pattern.ingest(frame);}
      },
      (status,position,error)=>{if(generation===replayGeneration)replayStatus(status,position,error);});
    replay=player;$('replayBar').hidden=false;$('replaySeek').max=manifest.durationUs/1e6;$('frameLabel').textContent='录制回放 / 10 Hz / 600 快照';
    await player.seek(0,true);
  }catch(e){if(generation===replayGeneration){state.error=`回放加载失败：${e.message}`;notice(state.error);}}
}
$('loadReplay').onclick=loadReplay;
$('replayPlay').onclick=()=>{if(!replay)return;if(replay.playing)replay.pause();else replay.play();};
$('replayRestart').onclick=()=>replay?.seek(0,true).catch(e=>notice(e.message));
$('replaySeek').oninput=()=>{if(!replayScrubbing){replayResumeAfterScrub=replay?.playing||false;replayScrubbing=true;replay?.pause();}};
$('replaySeek').onchange=e=>{const resume=replayScrubbing?replayResumeAfterScrub:replay?.playing;replayScrubbing=false;replay?.seek(Number(e.target.value),resume).catch(e=>notice(e.message));};
document.addEventListener('visibilitychange',()=>{if(!replay)return;if(document.hidden){hiddenReplayPlaying=replay.playing;replay.pause();}else if(hiddenReplayPlaying){hiddenReplayPlaying=false;replay.play();}});
$('connect').onclick=async()=>{
  if(socket){disconnect();state.error='连接已断开';notice(state.error);updateUI();return;}
  try{const url=new URL($('endpoint').value);assert(['ws:','wss:'].includes(url.protocol),'请输入 ws:// 或 wss:// 地址');resetSource('socket');const generation=socketGeneration;
    const http=new URL(url);http.protocol=url.protocol==='wss:'?'https:':'http:';http.pathname='/v1/capabilities';http.search='';
    notice('正在读取服务能力…');const response=await fetch(http,{signal:AbortSignal.timeout(5000)});assert(response.ok,'无法读取 capabilities；请检查服务和跨域配置');const caps=validate(await response.json());assert(caps.messageType==='capabilities'&&caps.routes.length,'服务未提供路由');if(generation!==socketGeneration)return;
    const route=caps.routes[0];http.pathname=`/v1/routes/${encodeURIComponent(route.routeId)}/layout`;const lr=await fetch(http,{signal:AbortSignal.timeout(5000)});assert(lr.ok,'布局读取失败');state.layout=validate(await lr.json());if(generation!==socketGeneration)return;
    socket=new WebSocket(url);$('connect').textContent='断开';
    socket.onopen=()=>{if(generation!==socketGeneration)return;socket.send(JSON.stringify({schemaVersion:caps.schemaVersion,messageType:'subscribe',routeId:route.routeId,mode:route.soundNavigation&&route.soundSpectrum?'both':route.soundNavigation?'sound-navigation':'sound-spectrum',publishRateHz:caps.publishRatesHz.includes(10)?10:caps.publishRatesHz[0],bands:['all']}));notice('已连接 · 等待协商与快照');};
    socket.onmessage=e=>{if(generation!==socketGeneration)return;try{assert(typeof e.data==='string'&&e.data.length<2*1024*1024,'消息格式错误或超过 2 MiB');accept(JSON.parse(e.data));}catch(err){state.error=`数据被拒绝：${err.message}`;notice(state.error);}};
    socket.onclose=()=>{if(generation!==socketGeneration)return;socket=null;state.error='服务已断开 · 可手动重新连接';$('connect').textContent='连接';notice(state.error);updateUI();};socket.onerror=()=>notice('连接失败，请检查地址与服务状态');
  }catch(e){state.error=e.message;notice(e.message);}
};
$('file').onchange=async e=>{try{
  const messages=[];for(const file of e.target.files){assert(file.size<=2*1024*1024,'文件超过 2 MiB');messages.push(validate(JSON.parse(await file.text())));}
  assert(messages.every(m=>['layout','snapshot','history'].includes(m.messageType)),'仅支持 layout、snapshot 或 history 文件');
    if(messages.some(m=>m.messageType!=='layout'))resetSource('import');for(const type of ['layout','snapshot','history'])for(const m of messages.filter(x=>x.messageType===type)){if(type==='history'){state.window=m.requestedWindowSeconds;state.history=m;}else accept(m);}
  document.querySelectorAll('[data-window]').forEach(b=>b.classList.toggle('active',Number(b.dataset.window)===state.window));updateUI();
}catch(err){notice(`导入失败：${err.message}`);}finally{e.target.value='';}};
async function init(){try{schema=await fetchJSON('schema.json');validate=createValidator(schema);seed=await fetchJSON('fixtures/silence.json');state.layout=validate(await fetchJSON('fixtures/layout.json'));simulate(performance.now());}catch(e){notice(`启动失败：${e.message}`);return;}
  if(new URLSearchParams(location.search).get('replay')==='wwise-minute')await loadReplay();
  function loop(now){requestAnimationFrame(loop);if(document.hidden)return;if(state.source==='demo'&&now-lastDemo>=100){lastDemo=now;simulate(now);}if(state.source==='replay')replay?.tick(now);if(!state.paused&&now-lastNavigation>=50)navigationStep(now);if(!state.paused&&now-lastDraw>=1000/30){lastDraw=now;draw(now);}if(state.source==='socket'&&now-state.received>1500&&!state.paused&&now-lastStatus>500){lastStatus=now;updateUI();}}requestAnimationFrame(loop);
}
// Only subscribes; no route-control or Hook lifecycle requests exist in this page.
init();
