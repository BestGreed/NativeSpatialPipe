import {channelLabels,orders,getPath,setPath,prepare,numeric,finish,outputLayouts,layoutFor,deviceChannels,migrateLayout,supportsActiveSource,updateSourceSelection,validateAes67,problemText,deriveAes67Flows,proposeAes67Flows,materializeAes67Destinations,effectiveAes67FlowSsrc,aes67MaxFlowChannels,parseAes67Ssrc,formatAes67Ssrc} from './configuration-model.js';
import {translate,uiReady} from './i18n.js';
await uiReady;
const $=id=>document.getElementById(id);
let token='',id='playback',loaded=null,working=null,dirty=false,loading=false,rawDirty=false,routePreview=null;
const formats=['WindowsSonic','DTSXForHomeTheater','DolbyAtmosForHomeTheater','DolbyAtmosForHeadphones','DolbyAtmosForSpeakers','DTSHeadphoneX','DTSXUltra'];
function message(text,error=false){$('result').textContent=text.split(/(?<=。)/u).filter(Boolean).map(part=>translate(part)).join(' ');$('result').dataset.error=String(error);}
function markDirty(){dirty=true;$('dirtyState').textContent='有未保存修改';}
async function api(path,options={}){const response=await fetch('/api/web/'+path,{cache:'no-store',...options});const data=await response.json();if(!response.ok)throw Error(data.error??`HTTP ${response.status}`);return data;}
function field(container,path,label,type='number',options=null,note='',offset=0){
 const row=document.createElement('label');row.className='config-field';const title=document.createElement('span');title.textContent=label;row.append(title);
 const input=document.createElement(options?'select':'input');input.dataset.path=path;input.dataset.kind=type;
 if(options){for(const value of options){const option=document.createElement('option');option.value=typeof value==='object'?value.value:value;option.textContent=typeof value==='object'?value.label:value;if(value.disabled)option.disabled=true;input.append(option);}}
 else{input.type=type==='integer'?'number':type;if(type==='number')input.step='any';if(type==='integer')input.step='1';}
 const configured=getPath(working,path);
 const suggestion=getPath(loaded?.formDefaults,path);
 const value=(configured===undefined||(configured===null&&path!=='wasapiUpmix.cpuFallbackDll')||configured==='')?suggestion??configured:configured;
 if(type==='checkbox')input.checked=value??false;else input.value=value==null?'':offset?value+offset:value;
 if(offset){input.dataset.offset=String(offset);input.min=String(offset);input.max='256';}
 if(path==='driver')input.setAttribute('list','asioDrivers');
 input.addEventListener('input',()=>{input.removeAttribute?.('aria-invalid');markDirty();if(path.startsWith('routing.'))updateRoutePreview();});
 input.addEventListener('change',()=>{
  if(!['outputBackend','renderer','sourcePolicy','channelOrder','outputLayout'].includes(path)){
   if(path.startsWith('routing.'))updateRoutePreview();
   return;
  }
  try{
   let next=collect();
   const backendChanged=path==='outputBackend'&&next.outputBackend!==working.outputBackend;
   if(backendChanged&&next.outputBackend==='wasapi'){
    next=migrateLayout(next,working.outputLayout,'2.0');
    next.outputLayout='7.1.4';next.lsac??={filterFile:''};next.lsac.enabled=true;
    next.renderer='roar';next.mode='olr';next.sampleRate=48000;next.channelOrder='olr';next.routing=null;
    next.wasapi??={dllPath:'',endpointId:''};
   }else if(backendChanged&&working.outputBackend==='wasapi'){
    next=migrateLayout(next,'2.0',next.outputLayout);
    if(next.lsac)next.lsac.enabled=false;
   }else if(backendChanged&&next.outputBackend==='aes67'){
    // Seed a complete transport block; the empty destination is caught by
    // client validation before saving, and an existing block is preserved.
    next.sampleRate=48000;
   }
   if(path==='outputLayout'){
     next=migrateLayout(next,working.outputLayout,next.outputLayout);
     if(Array.isArray(next.aes67?.flows)&&next.aes67.flows.length){
      const count=layoutFor(next.outputLayout).channels.length;
      if(next.aes67.flows.reduce((sum,flow)=>sum+(flow?.channels??0),0)!==count){
       next.aes67.flows=proposeAes67Flows(count,next.aes67.flows[0]??{});
       materializeAes67Destinations(next.aes67);
       if(next.aes67.channelMap?.length!==count)delete next.aes67.channelMap;
      }
     }
    }
   if(backendChanged||path==='renderer'||path==='sourcePolicy'||path==='outputLayout')updateSourceSelection(next,backendChanged?working.outputBackend:undefined);
   working=prepare(next,id);render();
  }catch(error){message(error.message,true);}
 });
 row.append(input);if(note){const small=document.createElement('small');small.textContent=note;row.append(small);}container.append(row);
 return input;
}
function group(title){const fieldset=document.createElement('fieldset');const legend=document.createElement('legend');legend.textContent=title;fieldset.append(legend);const body=document.createElement('div');body.className='config-fields';fieldset.append(body);$('fields').append(fieldset);return body;}
function render(){
 routePreview=null;$('fields').replaceChildren();if(!working)return;
 if(id==='backend'){
  const body=group('后端启动默认值');field(body,'version','配置版本','integer');field(body,'spatialFormat','默认空间格式','text',formats,'这是启动默认值，不会切换当前端点；FSA 启动时可能显式指定格式。');field(body,'fullTiming','完整时序统计','checkbox',null,'保存后需要重启后端服务才能加载。');return;
 }
 const channelLabels=deviceChannels(working);
 const recording=group('诊断录制（默认关闭）');field(recording,'recording.rawStreamStats','原始空间流统计','checkbox',null,'下次启动时启用每路对象和 Bed 声道统计。');field(recording,'recording.pcm','PCM 旁录','checkbox',null,'独立有界录音池，保存 DSP 前音频；磁盘跟不上会丢录音块，不阻塞播放。');field(recording,'recording.telemetrySnapshots','遥测快照存档','checkbox',null,'额外保存 JSONL，可能占用大量磁盘；关闭后实时遥测与可视化仍可使用。');
 let body=group('渲染器与输出设备');field(body,'renderer','渲染后端','text',['roar','lira'],'ROAR 当前使用 OLR 模式；LIRA 是独立渲染后端。');field(body,'outputBackend','输出方式','text',[{value:'aoip',label:'原生 AoIP 网络输出'},{value:'asio',label:'外挂 ASIO 输出'},{value:'wasapi',label:'WASAPI 系统播放端点'},{value:'aes67',label:'AES67 网络输出（RTP / L24）'}],'保存后下次 NSp 会话生效，不自动中断当前播放。');if(working.outputBackend==='asio')field(body,'driver','ASIO 驱动标识','text',null,'从本机注册驱动中选择，或输入 asio:{CLSID}。这里只读取注册表，不打开设备。');if(working.outputBackend==='wasapi')field(body,'wasapi.endpointId','WASAPI 输出端点 ID','text',null,'共享模式，当前支持 48 kHz 双声道；保留端点 APO 处理。不会改变默认输入端点。');
 field(body,'outputLayout','核心渲染布局','text',[...outputLayouts.map(p=>({value:p.id,label:p.id+' · '+p.channels.length+' 声道',disabled:working.outputBackend==='wasapi'&&p.id!=='7.1.4'})),...(working.renderer==='lira'?[]:[{value:'roar-custom-6dof',label:'6DoF 测量自定义（预留）',disabled:true}])],working.outputBackend==='wasapi'?'LSAC 将 7.1.4 下混至 WASAPI 设备的 2.0 输出。':'切换布局会按声道名称迁移校准和接线。');
 if(working.outputBackend==='wasapi'){
  const wasapiMode=field(body,'wasapi.mode','WASAPI 输出模式','text',[{value:'shared',label:'共享'},{value:'exclusive',label:'独占'}]);
  if(!wasapiMode.value)wasapiMode.value='shared';
  const wasapiPeriod=field(body,'wasapi.periodFrames','WASAPI 独占周期（帧）','integer',null,'范围 144–1920 帧，默认 480。');
  if(wasapiPeriod.value==='')wasapiPeriod.value='480';
  const updateWasapiPeriod=()=>{wasapiPeriod.disabled=wasapiMode.value!=='exclusive';};
  wasapiMode.addEventListener('change',updateWasapiPeriod);updateWasapiPeriod();
  field(body,'lsac.filterFile','LSAC 实测 FIR 路径','text');
 }
 if(working.outputBackend==='aes67'){
  body=group('AES67 网络输出（RTP / L24，48 kHz）');
  field(body,'aes67.name','设备名（留空自动）','text',null,'留空时使用 NSP-<计算机名>；该名字会出现在 SDP 与接收端设备列表。');
  field(body,'aes67.framesPerPacket','每包帧数','integer',[{value:48,label:'48 帧 · 1 ms（当前仅支持）'}],'传输层当前固定 1 ms（48 帧）。');
  field(body,'aes67.payloadType','RTP payloadType','integer',null,'动态负载类型范围 96–127，AES67 默认 96。');
  const baseSsrc=field(body,'aes67.ssrc','SSRC（0x0 = 默认）','ssrc',null,'0x0 使用固定默认 0x4E535036（NSP6）；非 0 时按填写值发送并在 SDP 通告。多流时每流 SSRC 在下表逐流显示与保存（自动分配 = 基准 + 流序号）。');baseSsrc.value=formatAes67Ssrc(working.aes67?.ssrc??0);
  field(body,'aes67.srcInterfaceIp','源网卡绑定 IP（可空）','text',null,'留空由系统路由选择发送网卡；填写时必须是本机单播 IPv4。组播目标必填。');
  field(body,'aes67.ptp.domain','PTP domain（0–127）','integer');
  field(body,'aes67.ptp.lockTimeoutMs','PTP 锁定超时（ms）','integer');
  field(body,'aes67.ptp.timestamping','PTP 时间戳模式','text',['auto','software','hardware'].map(value=>({value,label:value})));
  field(body,'aes67.ptp.mode','PTP 拓扑模式','text',[{value:'slave',label:'slave（跟随外部 GM，默认）'},{value:'auto',label:'先监听再回退'},{value:'gm',label:'本机作为 GM'},{value:'local',label:'无 PTP 本地时钟'}],'slave 保持原有行为；gm 需要可读取网卡 MAC 作为 EUI-64 时钟标识。');
  field(body,'aes67.ptp.announceTimeoutMs','Announce 超时（ms）','integer',null,'auto 模式的监听窗口与外部 GM 超时，1000–60000，默认 6000。');
  field(body,'aes67.ptp.priority1','GM priority1（0–255）','integer',null,'gm 模式广播的 BMCA priority1，默认 128。');
  field(body,'aes67.ptp.priority2','GM priority2（0–255）','integer',null,'gm 模式广播的 BMCA priority2，默认 128。');
  field(body,'aes67.ptp.gmCapable','auto 可成为 GM','text',[{value:'false',label:'false'},{value:'true',label:'true'}],'仅 auto 模式使用；gm 模式隐含 true。');
  field(body,'aes67.ptp.gmUtcOffset','GM UTC 偏移（秒，可空）','text',null,'留空为 ARB（ptpTimescale=0）；0–127 时广播 ptpTimescale=1 与 currentUtcOffsetValid=1。');
  field(body,'aes67.ttl','组播 TTL（1–255）','integer',null,'仅组播目标生效，默认 32；单播忽略。逐流 TTL 为 0 时继承此值。');
  field(body,'aes67.ringPackets','环形缓冲包数','integer',null,'范围 8–1024，每包 1 ms；溢出会丢最旧音频并计入 ringDroppedFrames。');
  field(body,'aes67.prebufferFrames','发送前预缓冲（源帧）','integer',null,'48–48000 帧且不超过 ringPackets × 48；只影响发送状态机。');
  // Render every explicitly stored row. The default proposal is the minimum split.
  const flows=Array.isArray(working.aes67?.flows)?working.aes67.flows:[];
  const layoutChannels=deviceChannels(working).length;
  const flowNeeded=Math.ceil(layoutChannels/aes67MaxFlowChannels);
  const wrap=document.createElement('div');wrap.className='config-channels aes67-flows';
  const table=document.createElement('table');
  const head=document.createElement('tr');for(const title of ['流','目标地址','端口','SSRC','声道']){const th=document.createElement('th');th.textContent=title;head.append(th);}table.append(head);
  flows.forEach((flow,index)=>{
   const row=document.createElement('tr');
   const name=document.createElement('th');name.scope='row';name.textContent='流 '+(index+1);row.append(name);
   const destCell=document.createElement('td');field(destCell,'aes67.flows.'+index+'.destIp','流 '+(index+1)+' 目标 IP','text');destCell.querySelector('span').hidden=true;row.append(destCell);
   const portCell=document.createElement('td');field(portCell,'aes67.flows.'+index+'.port','流 '+(index+1)+' 端口','integer');portCell.querySelector('span').hidden=true;row.append(portCell);
   // SSRC is per flow: hexadecimal display, mixed-radix input, stored per
   // flow as the integer the JSON machine format keeps.
   const ssrcCell=document.createElement('td');const ssrcInput=field(ssrcCell,'aes67.flows.'+index+'.ssrc','流 '+(index+1)+' SSRC','ssrc');ssrcInput.value=formatAes67Ssrc(flow?.ssrc??0);ssrcInput.title=formatAes67Ssrc(effectiveAes67FlowSsrc(working.aes67,index));ssrcCell.querySelector('span').hidden=true;row.append(ssrcCell);
   const channelsCell=document.createElement('td');channelsCell.textContent=(flow?.channels??'?')+' ch · 偏移 '+(flow?.channelOffset??'?');row.append(channelsCell);
   table.append(row);
  });
  wrap.append(table);body.parentElement.append(wrap);
  const packingNote=document.createElement('p');packingNote.className='aes67-flow-note';
  packingNote.textContent='打包规则：每流最多 8 声道（RTP/L24 预算 1440B）；默认至少需要 ⌈声道数/8⌉ 条流，最多支持 8 条。当前布局 '+layoutChannels+' 声道 → '+flowNeeded+' 条流。目标地址支持单播 IPv4（1–223 开头）或组播组（224.0.0.0–239.255.255.255，组播必填源网卡绑定 IP）；0.x、240–255.x 会被后端拒绝。';
  body.parentElement.append(packingNote);
 if(flowNeeded>1){
   const derived=deriveAes67Flows(working.aes67,layoutChannels);
   const isAuto=flows.length>0&&derived!==null&&derived.length===flows.length&&derived.every((flow,index)=>flow.destIp===String(flows[index]?.destIp??'').trim()&&flow.port===Number(flows[index]?.port));
   // Port guidance states specification facts only (AES67 6.3): default
   // 5004/5005, streams may share a port when distinguished by multicast
   // group, and non-default ports should prefer 49152-65535. The UI never
   // references any particular receiver's state, capabilities or plans.
   const portNote=document.createElement('p');portNote.className='aes67-flow-note';
   portNote.textContent='端口：AES67 规范默认 5004（RTP）/ 5005（RTCP）。多条流可共用同一端口，由各自组播组区分，接收端按目的组解复用。如需非默认端口，规范建议使用 49152–65535。';
   body.parentElement.append(portNote);
   const note=document.createElement('p');note.className='aes67-flow-note';
   note.textContent='保存时自动分配空白目标。单播沿用流 1 地址，端口按流 1 端口 + 2×（流序号−1）。组播组地址递增，空白端口默认为 5004。SSRC 必须逐流唯一，0x0 表示自动使用基准 SSRC + 流序号。'+(isAuto?'当前地址符合自动分配规则。':'');
   body.parentElement.append(note);
   const auto=document.createElement('button');auto.type='button';auto.textContent='重新自动分配';
   auto.addEventListener('click',()=>{try{
    const next=collect();
    const fresh=deriveAes67Flows(next.aes67,deviceChannels(next).length);
    if(!fresh)throw Error('请先填写有效的流 1 目标地址与端口，再自动分配多流目的地');
    next.aes67.flows=fresh;working=prepare(next,id);markDirty();render();
   }catch(error){message(error.message,true);}});
   body.parentElement.append(auto);
  }else{
   const note=document.createElement('p');note.className='aes67-flow-note';
   note.textContent='本条流目的地已显式保存于配置（aes67.flows）。';
   body.parentElement.append(note);
  }

  if(Array.isArray(working.aes67.channelMap)&&working.aes67.channelMap.length===layoutChannels){
   const mapNote=document.createElement('p');mapNote.className='aes67-flow-note';
   mapNote.textContent='声道映射：发送声道 k ← 源声道 channelMap[k]（9.1.6 渲染顺序 → 16ch 容器顺序），完整值见高级 JSON。';
   body.parentElement.append(mapNote);
  }
 }
 field(body,'mediaFoundation.enabled','启用 Media Foundation 播放适配','checkbox');
 const sourcePolicies=[{value:'fixed',label:'固定源流'},{value:'unified',label:'空间流与普通 PCM 统一输入',disabled:working.renderer==='lira'}];
 if(supportsActiveSource(working.outputBackend))sourcePolicies.unshift({value:'active',label:'自动跟随活动流'});
 field(body,'sourcePolicy','选流方式','text',sourcePolicies,working.outputBackend==='aes67'?'AES67 默认使用固定源流。':'');
 if(working.sourcePolicy==='unified'){
  const slotProfile=field(body,'unifiedSlotProfile','输入槽位配置','text',[
   {value:'standard',label:'标准'},
   {value:'dtsxHtCompact',label:'DTS:X HT 紧凑（测试）'}
  ],'紧凑配置仅供 DTS:X 家庭影院端点使用。Bed 和 PCM 各预热 4 路，最多各 8 路。');
  if(!slotProfile.value)slotProfile.value='standard';
  const primary=field(body,'unifiedPrimaryPolicy','空间主流选举','text',[
   {value:'legacy',label:'沿用当前默认策略'},
   {value:'objectCount',label:'按活跃对象数异步选举（测试）'}
  ],'保存后在下一次会话启动时生效。');
  if(!primary.value)primary.value='legacy';
 }
 if(working.sourcePolicy==='fixed'||(working.outputBackend==='asio'&&working.sourcePolicy!=='unified'))field(body,'sourceStream','固定源流索引（0–3）','integer');
 field(body,'sampleRate','设备采样率（Hz）','number',null,working.outputBackend==='aes67'?'AES67 固定 48000 Hz；其他值会被后端拒绝。':'');if(working.outputBackend==='asio'){field(body,'bufferFrames','驱动缓冲（帧，0 为驱动默认）','integer');field(body,'clockSync','匹配设备时钟','checkbox');field(body,'prebufferFrames','预缓冲（源帧，0 为自动）','integer');}field(body,'drainTimeoutMs','显式停止时排空超时（ms）','integer');
 body=group('普通 PCM 捕获');
 const pcmEnabled=field(body,'pcmCapture.enabled','启用普通 WASAPI PCM 捕获','checkbox',null,
  '选流方式设为「空间流与普通 PCM 统一输入」时，与空间流合流。');
 const pcmEndpoint=field(body,'pcmCapture.inputEndpointId','PCM 输入端点 ID','text',null,'统一输入在启动时跟随 FSA 会话端点。此字段用于独立 PCM 捕获。');
 const pcmPid=field(body,'pcmCapture.sourcePid','来源进程 PID','integer',null,'0 表示自动选择唯一符合条件的活动流。');
 if(pcmPid.value==='')pcmPid.value='0';
 const pcmCaptureDll=field(body,'pcmCapture.captureDll','PCM 采集 DLL（绝对路径）','text');
 const pcmIngressDll=field(body,'pcmCapture.ingressDll','PCM 入口 DLL（绝对路径）','text');
 function updatePcmFields(){
  const followsSession=working.sourcePolicy==='unified';
  pcmEndpoint.disabled=!pcmEnabled.checked||followsSession;
  if(followsSession){pcmEndpoint.value='';pcmEndpoint.placeholder='跟随 FSA 会话端点';}
  for(const input of [pcmPid,pcmCaptureDll,pcmIngressDll])input.disabled=!pcmEnabled.checked;
 }
 pcmEnabled.addEventListener('change',updatePcmFields);updatePcmFields();
 if(working.outputLayout==='7.1.4'&&working.renderer==='roar'){
  body=group('普通 PCM 上混');
  const upmixEnabled=field(body,'wasapiUpmix.enabled','启用 Joint 上混','checkbox',null,'先选择「空间流与普通 PCM 统一输入」并启用普通 PCM 捕获。');
  const upmixPaths=[
   field(body,'wasapiUpmix.dllPath','Joint DLL（绝对路径）','text',null,'安装位置未检测到文件时，可填自定义路径。'),
   field(body,'wasapiUpmix.modelPath','模型文件（绝对路径）','text'),
   field(body,'wasapiUpmix.freeSurroundDll','FreeSurround DLL（绝对路径）','text'),
   field(body,'wasapiUpmix.cpuFallbackDll','CPU 回退 DLL（可留空）','text')
  ];
  function updateUpmixFields(){
   upmixEnabled.disabled=working.sourcePolicy!=='unified'||!pcmEnabled.checked;
   if(upmixEnabled.disabled)upmixEnabled.checked=false;
   for(const input of upmixPaths)input.disabled=!upmixEnabled.checked;
  }
  pcmEnabled.addEventListener('change',updateUpmixFields);
  upmixEnabled.addEventListener('change',updateUpmixFields);updateUpmixFields();
 }
 if(working.outputBackend==='aoip'){body=group('AoIP 网络模块');field(body,'aoip.interfaceIp','本机发送网卡 IPv4','text');field(body,'aoip.masterIp','主时钟 IPv4','text',null,'留空时自动跟随发送网卡上的 PTPv1 主时钟；填写地址时固定跟随。');field(body,'aoip.mac','设备 MAC（12 位十六进制，无分隔符）','text');field(body,'aoip.name','网络设备名称（ASCII）','text');field(body,'aoip.txLatencyUs','TX 延迟设置（微秒）','integer',null,'默认 4000，即 4 ms；不是发包周期。');field(body,'aoip.framesPerPacket','每包帧数','integer',null,'默认 32；0 按接收端请求，覆盖值需接收端兼容。');field(body,'aoip.prebufferFrames','PCM 预缓冲（帧）','integer',null,'范围 32–4800 帧；默认 960（20 ms），64 帧约 1.33 ms。');}
 body=group('公共 DSP · 渲染输出池内处理');const lsacGain=working.lsac?.enabled&&working.lsac.gain!=null;const limiterPath=working.lsac?.enabled&&working.lsac.limiter?'lsac.limiter':'outputProcessing.limiter';field(body,'outputProcessing.bypass','跳过整个公共 DSP','checkbox',null,'开启后音量、trim、延迟和 limiter 均不处理；中性配置也会自动跳过。');field(body,lsacGain?'lsac.gain':'master','主音量（线性，1 为原值）');field(body,limiterPath+'.enabled','启用 limiter','checkbox');field(body,limiterPath+'.ceiling','峰值上限（0–1）');field(body,limiterPath+'.releaseMs','释放时间（ms）');field(body,limiterPath+'.lookaheadFrames','前视（0–4800 源帧）','integer','', '开启 limiter 时增加相应延迟；源采样率为 48 kHz。');
 if(working.lsac?.enabled){body=group('LSAC 虚拟低音');field(body,'lsac.virtualBass.enabled','启用虚拟低音','checkbox',null,'生成可重放的较高谐波，保留原有空间方向；保存后下次会话生效。');field(body,'lsac.virtualBass.amount','增强强度（0–0.5）','number',null,'首版建议 0.18；0 仅保留低频保护。');field(body,'lsac.virtualBass.highpassHz','低频保护高通（Hz）','number',null,'0 为关闭保护高通；启用范围 80–180Hz。虚拟低音可继续工作。');}
 body=group('声道校准');
 const wrap=document.createElement('div');wrap.className='config-channels';const table=document.createElement('table');const head=document.createElement('tr');for(const title of ['标准声道','Trim（线性）','延迟（源帧）']){const th=document.createElement('th');th.textContent=title;head.append(th);}table.append(head);
 for(let i=0;i<channelLabels.length;i++){const row=document.createElement('tr');const name=document.createElement('th');name.scope='row';name.textContent=channelLabels[i];row.append(name);for(const [key,kind] of [['trim','number'],['delayFrames','integer']]){const cell=document.createElement('td');field(cell,`outputProcessing.${key}.${i}`,`${channelLabels[i]} ${key}`,kind);cell.querySelector('span').hidden=true;row.append(cell);}table.append(row);}wrap.append(table);body.parentElement.append(wrap);
 if(working.outputBackend==='asio'){const route=group('设备路由');field(route,'channelOrder','输出声道排列','text',[{value:'olr',label:'标准顺序'},{value:'dvs-speakers',label:'DVS 扬声器接线顺序（仅 7.1.4）',disabled:working.outputLayout!=='7.1.4'},{value:'custom',label:'自定义顺序'}],'切换后，下方每个输出槽的声道随之变化；设备输出通道号保留。');
 routePreview=document.createElement('p');routePreview.className='route-preview';routePreview.dataset.routePreview='true';routePreview.setAttribute('aria-live','polite');route.parentElement.append(routePreview);
 const standard=channelLabels.map((_,i)=>i);const source=working.channelOrder==='custom'?(working.routing.sourceChannelIndices??standard):working.channelOrder==='olr'?standard:orders[working.channelOrder];
 if(working.channelOrder==='custom')working.routing.sourceChannelIndices??=standard.slice();
 const routeWrap=document.createElement('div');routeWrap.className='config-channels';const routeTable=document.createElement('table');const routeHead=document.createElement('tr');for(const title of ['输出槽','声道','ASIO 设备输出通道（从 1 开始）']){const th=document.createElement('th');th.textContent=title;routeHead.append(th);}routeTable.append(routeHead);
 for(let i=0;i<channelLabels.length;i++){const row=document.createElement('tr');const slot=document.createElement('th');slot.scope='row';slot.textContent=String(i+1);row.append(slot);const speaker=document.createElement('td');
  if(working.channelOrder==='custom')field(speaker,`routing.sourceChannelIndices.${i}`,`输出槽 ${i+1} 的声道`,'integer',channelLabels.map((label,value)=>({value,label})));else speaker.textContent=channelLabels[source?.[i]]??'?';
  row.append(speaker);const device=document.createElement('td');field(device,`routing.physicalChannels.${i}`,`输出槽 ${i+1} 的设备输出通道`,'integer',null,'',1);device.querySelector('span').hidden=true;row.append(device);routeTable.append(row);
 }routeWrap.append(routeTable);route.parentElement.append(routeWrap);}
 if(working.renderer!=='lira'){working.positionMapping??={mode:'scaled',reference:1};body=group('ROAR / OLR 坐标映射');field(body,'positionMapping.mode','半径映射','text',['scaled','direction']);field(body,'positionMapping.reference','半径参考值','number',null,'scaled 保留完整半径并除以参考值；direction 使用单位方向，参考值必须为 1。');}
 if(working.renderer!=='lira'){working.roarCustomLayout??={version:1,measurementFile:null};const slot=group('ROAR · 6DoF 自定义布局插槽（预留）');field(slot,'roarCustomLayout.measurementFile','测量配置路径（可留空）','text',null,'仅保存插槽信息；本轮不加载测量文件，也不启用自定义几何或实时追踪。');}
 body=group('模块路径');field(body,'bridgeDll','ROAR 桥接 DLL（绝对路径）','text');if(working.outputBackend==='asio')field(body,'asioDll','ASIO 输出 DLL（绝对路径）','text');if(working.outputBackend==='wasapi')field(body,'wasapi.dllPath','WASAPI 输出 DLL（绝对路径）','text');field(body,'rendererDll','LIRA DLL（LIRA 时必填）','text');
 updateRoutePreview();
}
function updateRoutePreview(){if(!routePreview)return;try{const value=collect();const channelLabels=layoutFor(value.outputLayout).channels;const source=value.channelOrder==='custom'?value.routing.sourceChannelIndices:value.channelOrder==='olr'?channelLabels.map((_,i)=>i):orders[value.channelOrder];routePreview.textContent='声道排列：'+source.map(index=>channelLabels[index]??'?').join(' → ')+'\n设备输出：'+source.map((index,slot)=>`${channelLabels[index]??'?'} → 第 ${value.routing.physicalChannels[slot]+1} 路`).join(' ｜ ');}catch{routePreview.textContent='请补全有效配置，查看声道与设备输出的对应关系。';}}
function aes67SsrcInput(text){const value=parseAes67Ssrc(text);if(!Number.isInteger(value)||value<0||value>4294967295)throw Error('SSRC 必须是 0x 十六进制或十进制 32 位值');return value;}
function collect(){if(!working)throw Error('配置尚未加载');const value=structuredClone(working);for(const input of $('fields').querySelectorAll('[data-path]')){if(input.disabled)continue;const path=input.dataset.path;const kind=input.dataset.kind;if(path==='pcmCapture.enabled'&&!input.checked&&!value.pcmCapture)continue;if(/^aes67\.flows\.\d+\.port$/.test(path)&&String(input.value).trim()===''){delete getPath(value,path.slice(0,-5)).port;continue;}if(path==='aes67.ptp.gmCapable'){setPath(value,path,input.value==='true');continue;}if(path==='aes67.ptp.gmUtcOffset'&&String(input.value).trim()===''){const ptp=getPath(value,'aes67.ptp');if(ptp&&typeof ptp==='object')delete ptp.gmUtcOffset;continue;}if(path==='aes67.name'&&String(input.value).trim()===''){const aes=getPath(value,'aes67');if(aes&&typeof aes==='object')delete aes.name;continue;}setPath(value,path,kind==='checkbox'?input.checked:kind==='ssrc'?aes67SsrcInput(input.value):(kind==='integer'||kind==='number'?numeric(input.value,kind==='integer')-Number(input.dataset.offset??0):input.value));}return id==='playback'?finish(value):value;}
async function load(next=id){
 if(loading)return;if(dirty&&!confirm(translate('放弃未保存的修改，重新读取配置？')))return;loading=true;$('save').disabled=true;
 try{const value=await api('config/'+next);id=next;loaded=value;dirty=false;working=null;
  if(value.config&&typeof value.config==='object'&&!Array.isArray(value.config)){try{working=prepare(value.config,id);}catch{working=null;}}
  $('configTitle').textContent=id==='playback'?'播放与公共 DSP':'后端默认配置';$('playbackTab').setAttribute('aria-pressed',String(id==='playback'));$('backendTab').setAttribute('aria-pressed',String(id==='backend'));
  $('applyInfo').textContent=id==='playback'?'输出方式（含 AES67）或 AoIP 参数修改后重启 FSA 生效；其他播放配置下次会话读取。不自动重启。':'保存后需要后端服务重启；FSA 重启不一定会重启已驻留的后端。';
  $('fileInfo').textContent=translate(value.exists?'文件：':'尚无文件；首次保存会创建并登记：')+' '+value.path;$('revisionInfo').textContent=translate('文件修订：')+' '+value.revision+' · '+translate(' · 当前会话是否已加载：未知（不假定保存即生效）');$('dirtyState').textContent='';
  render();rawDirty=false;$('rawConfig').value=value.raw??JSON.stringify(working??value.config,null,2);message(value.validationError?'当前配置校验：'+value.validationError+'。请修正后保存。':'已读取配置。',Boolean(value.validationError));
 }catch(error){message(error.message,true);}finally{loading=false;$('save').disabled=!loaded;}
}
$('configForm').addEventListener('submit',async event=>{event.preventDefault();$('save').disabled=true;try{if(rawDirty)throw Error('请先将修改的 JSON 应用到表单，再保存。');const config=collect();
  if(id==='playback')materializeAes67Destinations(config.aes67);
  const problems=id==='playback'?validateAes67(config):[];
  if(problems.length){const first=problems[0];const input=Array.from($('fields').querySelectorAll('[data-path]')).find(item=>item.dataset.path===first.path);if(input){input.setAttribute('aria-invalid','true');input.focus?.();}throw Error('配置校验未通过：\n'+problemText(problems));}
  const saved=await api('config/'+id,{method:'PUT',headers:{'Content-Type':'application/json','X-NSP-Token':token},body:JSON.stringify({revision:loaded.revision,config})});loaded=saved;working=prepare(saved.config,id);render();dirty=false;$('dirtyState').textContent='已保存，未自动应用';$('fileInfo').textContent=translate('文件：')+' '+saved.path;$('revisionInfo').textContent=translate('文件修订：')+' '+saved.revision+' · '+translate(' · 当前会话是否已加载：未知');$('rawConfig').value=JSON.stringify(saved.config,null,2);message('校验通过，已保存。'+(id==='playback'?'输出方式（含 AES67）或 AoIP 参数修改后重启 FSA；其他配置下次会话读取。':'重启后端服务后加载。'));}catch(error){message(error.message==='RevisionConflict'?'文件已被其他入口修改，请重新读取后再编辑。':error.message,true);}finally{$('save').disabled=false;}});
$('playbackTab').onclick=()=>load('playback');$('backendTab').onclick=()=>load('backend');$('reload').onclick=()=>load();
$('toJson').onclick=()=>{try{$('rawConfig').value=JSON.stringify(collect(),null,2);rawDirty=false;message('表单已同步到 JSON，尚未保存。');}catch(error){message(error.message,true);}};
$('rawConfig').addEventListener('input',()=>{rawDirty=true;markDirty();});
$('fromJson').onclick=()=>{try{const value=JSON.parse($('rawConfig').value);if(!value||Array.isArray(value)||typeof value!=='object')throw Error('配置必须是 JSON 对象');working=prepare(value,id);render();rawDirty=false;markDirty();message('JSON 已应用到表单，尚未保存。');}catch(error){message(error.message,true);}};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
async function poll(){try{const state=await api('status');const fsa=state.fsa;const code=state.backend?.sessionState??'后端离线 / 未启动';$('runtime').textContent=translate(fsa.known?(fsa.stale?'FSA 状态已过期':'FSA 在线'):'FSA 状态未知')+' · '+translate(code)+(fsa.message?' · '+fsa.message:'');$('endpointInfo').textContent=fsa.endpoint?translate('端点：')+' '+fsa.endpoint:'';$('runtimeConfig').textContent=state.backend?.config?translate('后端当前内存配置：')+' '+JSON.stringify(state.backend.config):translate('读取状态不会启动 NSp。');}catch(error){$('runtime').textContent='状态读取失败：'+error.message;}finally{setTimeout(()=>{if(document.hidden)setTimeout(poll,2000);else poll();},2000);}}
try{const bootstrap=await api('bootstrap');token=bootstrap.token;const listing=await api('drivers');for(const driver of listing.drivers){const option=document.createElement('option');option.value=driver.driver;option.label=driver.name;option.dataset.i18nPreserve='';$('asioDrivers').append(option);}await load();poll();}catch(error){message('无法连接 Web API Host：'+error.message+'。请通过 FSA 的 Web 地址打开此页。',true);}
