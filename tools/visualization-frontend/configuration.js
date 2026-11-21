import {channelLabels,orders,getPath,setPath,prepare,numeric,finish,outputLayouts,layoutFor,migrateLayout,outputBackends,aes67Defaults,validateAes67,problemText,diffConfig,diffText} from './configuration-model.js';
const $=id=>document.getElementById(id);
let token='',id='playback',loaded=null,working=null,baseline=null,dirty=false,loading=false,rawDirty=false,routePreview=null,lastNetworkState=null;
const formats=['WindowsSonic','DTSXForHomeTheater','DolbyAtmosForHomeTheater','DolbyAtmosForHeadphones','DolbyAtmosForSpeakers','DTSHeadphoneX','DTSXUltra'];
const backendLabels={asio:'本机 ASIO',aes67:'AES67 网络输出',dante:'Dante 网络输出（第三方实验性 · 未接入）'};
const backendLabel=value=>backendLabels[value]??(value?String(value):'未知');
function message(text,error=false){$('result').textContent=text;$('result').dataset.error=String(error);}
function markDirty(){dirty=true;$('dirtyState').textContent='有未保存修改';updateDiff();}
async function api(path,options={}){const response=await fetch('/api/web/'+path,{cache:'no-store',...options});const data=await response.json();if(!response.ok)throw Error(data.error??`HTTP ${response.status}`);return data;}
function field(container,path,label,type='number',options=null,note='',offset=0){
 const row=document.createElement('label');row.className='config-field';const title=document.createElement('span');title.textContent=label;row.append(title);
 const input=document.createElement(options?'select':'input');input.dataset.path=path;input.dataset.kind=type;
 if(options){for(const value of options){const option=document.createElement('option');option.value=typeof value==='object'?value.value:value;option.textContent=typeof value==='object'?value.label:value;if(value.disabled)option.disabled=true;input.append(option);}}
 else{input.type=type==='integer'?'number':type;if(type==='number')input.step='any';if(type==='integer')input.step='1';}
 const value=getPath(working,path);if(type==='checkbox')input.checked=value??false;else input.value=value==null?'':offset?value+offset:value;
 if(offset){input.dataset.offset=String(offset);input.min=String(offset);input.max='256';}
 if(path==='driver')input.setAttribute('list','asioDrivers');
 input.addEventListener('input',()=>{input.removeAttribute?.('aria-invalid');markDirty();if(path.startsWith('routing.'))updateRoutePreview();});
 input.addEventListener('change',()=>{
  if(path==='renderer'||path==='channelOrder'||path==='outputLayout'||path==='outputBackend'){
   try{
    const next=collect();
    if(path==='outputBackend'&&next.outputBackend==='aes67'&&!next.network)next.network=structuredClone(aes67Defaults);
    working=path==='outputLayout'?migrateLayout(next,working.outputLayout,next.outputLayout):next;
    render();markDirty();
   }catch(error){message(error.message,true);}
  }else if(path.startsWith('routing.'))updateRoutePreview();
 });
 row.append(input);if(note){const small=document.createElement('small');small.textContent=note;row.append(small);}container.append(row);
}
function group(title){const fieldset=document.createElement('fieldset');const legend=document.createElement('legend');legend.textContent=title;fieldset.append(legend);const body=document.createElement('div');body.className='config-fields';fieldset.append(body);$('fields').append(fieldset);return body;}
function render(){
 routePreview=null;$('fields').replaceChildren();if(!working)return;
 if(id==='backend'){
  const body=group('后端启动默认值');field(body,'version','配置版本','integer');field(body,'spatialFormat','默认空间格式','text',formats,'这是启动默认值，不会切换当前端点；FSA 启动时可能显式指定格式。');field(body,'fullTiming','完整时序统计','checkbox',null,'保存后需要重启后端服务才能加载。');return;
 }
 const labelChannels=layoutFor(working.outputLayout).channels;
 const recording=group('诊断录制（默认关闭）');field(recording,'recording.pcm','PCM 旁录','checkbox',null,'独立有界录音池，保存 DSP 前音频；磁盘跟不上会丢录音块，不阻塞播放。');field(recording,'recording.telemetrySnapshots','遥测快照存档','checkbox',null,'额外保存 JSONL，可能占用大量磁盘；关闭后实时遥测与可视化仍可使用。');
 const aes67=working.outputBackend==='aes67';
 let body=group('渲染器与输出设备');
 field(body,'outputBackend','输出后端','text',outputBackends,'ASIO 走本机驱动；AES67 走 RTP/L24 网络输出。Dante 为第三方实验性后端，未接入（无 Dante 时钟主设备），选项禁用。切换只改配置文件，下次会话启动生效。');
 field(body,'renderer','渲染后端','text',['roar','lira'],'ROAR 当前使用 OLR 模式；LIRA 是独立渲染后端。');
 field(body,'driver','ASIO 驱动标识','text',null,aes67?'AES67 后端下忽略（设置保留，切回 ASIO 时使用）。':'从本机注册驱动中选择，或输入 asio:{CLSID}。这里只读取注册表，不打开设备。');
 field(body,'outputLayout','扬声器布局','text',[...outputLayouts.map(p=>({value:p.id,label:p.id+' · '+p.channels.length+' 声道'})),...(working.renderer==='lira'?[]:[{value:'roar-custom-6dof',label:'6DoF 测量自定义（预留）',disabled:true}])],'切换布局按声道名称迁移校准和接线；新增声道使用默认值，保存后下次会话生效。');
 field(body,'sourceStream','源流索引（0–3）','integer');
 field(body,'sampleRate','设备采样率（Hz）');
 field(body,'bufferFrames','驱动缓冲（帧，0 为驱动默认）','integer');
 field(body,'clockSync','匹配设备时钟','checkbox');
 field(body,'prebufferFrames','预缓冲（源帧，0 为自动）','integer',null,aes67?'ASIO 路径使用；AES67 预缓冲见下方 network.prebufferFrames。':'');
 field(body,'drainTimeoutMs','显式停止时排空超时（ms）','integer');
 if(aes67){
  body=group('AES67 网络输出（RTP / L24，48 kHz）');
  field(body,'network.destIp','目标 IP（dst）','text',null,'接收端 IPv4：单播填接收端地址（如 192.168.1.10），组播填组播组（如 239.69.1.1）。');
  field(body,'network.port','目标端口','integer',null,'RTP 端口，默认 5004。');
  field(body,'network.framesPerPacket','每包帧数（48 kHz）','integer',[{value:48,label:'48 帧 · 1 ms（推荐）'},{value:96,label:'96 帧 · 2 ms'},{value:192,label:'192 帧 · 4 ms'},{value:480,label:'480 帧 · 10 ms（兼容）'}]);
  field(body,'network.ttl','TTL（跳数）','integer',null,'组播时控制跨网段范围；单播同样需要 > 0。');
  field(body,'network.payloadType','RTP payloadType','integer',null,'动态负载类型，AES67 常用 96；范围 0–127。');
  field(body,'network.ssrc','SSRC（0 = 自动）','integer',null,'0 时由发送端决定（默认 0x4E535036），SDP 与 RTP 一致。');
  field(body,'network.prebufferFrames','网络预缓冲（源帧）','integer',null,'只控制发送端状态机/指标，不 gate RTP 发送；默认 480 帧 = 10 ms。');
  field(body,'network.ringPackets','环形缓冲包数','integer',null,'1–4096；过小会丢帧（ringDroppedFrames 上升）。');
  field(body,'network.srcInterfaceIp','源网卡绑定 IP（可空）','text',null,'留空按系统路由选择；多网卡时填发送网卡的 IPv4。');
  field(body,'network.ptpGmid','PTP GMID（可空）','text',null,'格式 XX-XX-XX-XX-XX-XX-XX-XX；写入 SDP ts-refclk。');
  field(body,'network.ptpDomain','PTP domain（0–127）','integer');
  field(body,'network.dll','AES67 DLL 路径（可空）','text',null,'留空使用 <安装目录>/build/aes67-output/nsp_aes67.dll。');
 }
 body=group('公共 DSP · 渲染输出池内处理');field(body,'outputProcessing.bypass','跳过整个公共 DSP','checkbox',null,'开启后音量、trim、延迟和 limiter 均不处理；中性配置也会自动跳过。');field(body,'master','主音量（线性，1 为原值）');field(body,'outputProcessing.limiter.enabled','启用 limiter','checkbox');field(body,'outputProcessing.limiter.ceiling','峰值上限（0–1）');field(body,'outputProcessing.limiter.releaseMs','释放时间（ms）');field(body,'outputProcessing.limiter.lookaheadFrames','前视（0–4800 源帧）','integer','', '开启 limiter 时增加相应延迟；源采样率为 48 kHz。');
 body=group('声道校准');
 const wrap=document.createElement('div');wrap.className='config-channels';const table=document.createElement('table');const head=document.createElement('tr');for(const title of ['标准声道','Trim（线性）','延迟（源帧）']){const th=document.createElement('th');th.textContent=title;head.append(th);}table.append(head);
 for(let i=0;i<labelChannels.length;i++){const row=document.createElement('tr');const name=document.createElement('th');name.scope='row';name.textContent=labelChannels[i];row.append(name);for(const [key,kind] of [['trim','number'],['delayFrames','integer']]){const cell=document.createElement('td');field(cell,`outputProcessing.${key}.${i}`,`${labelChannels[i]} ${key}`,kind);cell.querySelector('span').hidden=true;row.append(cell);}table.append(row);}wrap.append(table);body.parentElement.append(wrap);
 const route=group('设备路由');field(route,'channelOrder','输出声道排列','text',[{value:'olr',label:'标准顺序'},{value:'dvs-speakers',label:'DVS 扬声器接线顺序（仅 7.1.4）',disabled:working.outputLayout!=='7.1.4'},{value:'custom',label:'自定义顺序'}],'切换后，下方每个输出槽的声道随之变化；设备输出通道号保留。');
 routePreview=document.createElement('p');routePreview.className='route-preview';routePreview.dataset.routePreview='true';routePreview.setAttribute('aria-live','polite');route.parentElement.append(routePreview);
 const standard=labelChannels.map((_,i)=>i);const source=working.channelOrder==='custom'?(working.routing.sourceChannelIndices??standard):working.channelOrder==='olr'?standard:orders[working.channelOrder];
 if(working.channelOrder==='custom')working.routing.sourceChannelIndices??=standard.slice();
 const routeWrap=document.createElement('div');routeWrap.className='config-channels';const routeTable=document.createElement('table');const routeHead=document.createElement('tr');for(const title of ['输出槽','声道','ASIO 设备输出通道（从 1 开始）']){const th=document.createElement('th');th.textContent=title;routeHead.append(th);}routeTable.append(routeHead);
 for(let i=0;i<labelChannels.length;i++){const row=document.createElement('tr');const slot=document.createElement('th');slot.scope='row';slot.textContent=String(i+1);row.append(slot);const speaker=document.createElement('td');
  if(working.channelOrder==='custom')field(speaker,`routing.sourceChannelIndices.${i}`,`输出槽 ${i+1} 的声道`,'integer',labelChannels.map((label,value)=>({value,label})));else speaker.textContent=labelChannels[source?.[i]]??'?';
  row.append(speaker);const device=document.createElement('td');field(device,`routing.physicalChannels.${i}`,`输出槽 ${i+1} 的设备输出通道`,'integer',null,'',1);device.querySelector('span').hidden=true;row.append(device);routeTable.append(row);
 }routeWrap.append(routeTable);route.parentElement.append(routeWrap);
 if(working.renderer!=='lira'){working.positionMapping??={mode:'scaled',reference:1};body=group('ROAR / OLR 坐标映射');field(body,'positionMapping.mode','半径映射','text',['scaled','direction']);field(body,'positionMapping.reference','半径参考值','number',null,'scaled 保留完整半径并除以参考值；direction 使用单位方向，参考值必须为 1。');}
 if(working.renderer!=='lira'){working.roarCustomLayout??={version:1,measurementFile:null};const slot=group('ROAR · 6DoF 自定义布局插槽（预留）');field(slot,'roarCustomLayout.measurementFile','测量配置路径（可留空）','text',null,'仅保存插槽信息；本轮不加载测量文件，也不启用自定义几何或实时追踪。');}
 body=group('模块路径');field(body,'bridgeDll','ROAR 桥接 DLL（绝对路径）','text');field(body,'asioDll','ASIO 输出 DLL（绝对路径）','text',null,aes67?'AES67 后端下忽略（设置保留）。':'');field(body,'rendererDll','LIRA DLL（LIRA 时必填）','text');
 updateRoutePreview();
}
function updateRoutePreview(){if(!routePreview)return;try{const value=collect();const labels=layoutFor(value.outputLayout).channels;const source=value.channelOrder==='custom'?value.routing.sourceChannelIndices:value.channelOrder==='olr'?labels.map((_,i)=>i):orders[value.channelOrder];routePreview.textContent='声道排列：'+source.map(index=>labels[index]??'?').join(' → ')+'\n设备输出：'+source.map((index,slot)=>`${labels[index]??'?'} → 第 ${value.routing.physicalChannels[slot]+1} 路`).join(' ｜ ');}catch{routePreview.textContent='请补全有效配置，查看声道与设备输出的对应关系。';}}
function collect(){if(!working)throw Error('配置尚未加载');const value=structuredClone(working);for(const input of $('fields').querySelectorAll('[data-path]')){const kind=input.dataset.kind;setPath(value,input.dataset.path,kind==='checkbox'?input.checked:(kind==='integer'||kind==='number'?numeric(input.value,kind==='integer')-Number(input.dataset.offset??0):input.value));}return id==='playback'?finish(value):value;}
function focusField(path){const input=$('fields').querySelectorAll('[data-path]').find(item=>item.dataset.path===path);if(!input)return;input.setAttribute('aria-invalid','true');if(input.focus)input.focus();}
function updateDiff(){if(!baseline||!$('diffDetails'))return;let changes;try{changes=diffConfig(baseline,collect());}catch{return;}const details=$('diffDetails');details.hidden=!changes.length;$('diffPreview').textContent=changes.length?diffText(changes):'';if($('diffCount'))$('diffCount').textContent=changes.length?`（${changes.length} 处改动）`:'';}
function rowValue(text){return text==null?'NOT_RUN':String(text);}
function statusRow(container,label,value,tone=''){const row=document.createElement('div');row.className='network-status-row'+(tone?' '+tone:'');const key=document.createElement('span');key.className='k';key.textContent=label;const val=document.createElement('span');val.className='v';val.textContent=value;row.append(key,val);container.append(row);return row;}
const reasonText=reason=>({
 'active output backend is not aes67':'当前输出后端不是 AES67',
 'no active session; saved settings apply on the next session start':'没有活动会话；保存的配置在下次启动会话时生效',
 'session is running but no AES67 sender statistics have been published yet':'会话正在运行，但发送端统计尚未发布',
 'session output directory is unknown':'会话输出目录未知',
}[reason]??reason);
const bytesText=value=>value==null?'—':value>=1073741824?(value/1073741824).toFixed(2)+' GiB':value>=1048576?(value/1048576).toFixed(1)+' MiB':value>=1024?(value/1024).toFixed(1)+' KiB':value+' B';
const bitrateText=value=>value==null?'—':(value/1e6).toFixed(2)+' Mbps';
const ssrcText=value=>value==null?'—':'0x'+(value>>>0).toString(16).toUpperCase().padStart(8,'0');
function renderNetworkStatus(state){
 const box=$('networkStatus');if(!box)return;box.replaceChildren();lastNetworkState=state;
 const current=state.current??{};
 const session=state.session??{};
 const configured=state.configured??{};
 statusRow(box,'当前输出后端',backendLabel(current.backend)+(current.source==='session'?'（会话已加载）':current.source==='configured'?'（仅配置文件，未启动会话）':''),'headline');
 if(session.known)statusRow(box,'会话状态',session.state??'未知');
 if(current.pendingBackendChange)statusRow(box,'待生效','配置文件已改为 '+backendLabel(configured.outputBackend)+'，当前会话仍在 '+backendLabel(current.backend)+'；下次启动会话生效。','warn');
 if(configured.networkIgnored||session.networkIgnored)statusRow(box,'AES67 网络段','已保留但当前后端忽略（仅 AES67 后端生效）。','muted');
 const aes=state.aes67??{};
 if(aes.status==='LIVE'||aes.status==='FINAL'){
  const network=aes.network??{};const stats=aes.stats??{};const rates=aes.rates??{};
  statusRow(box,'AES67 状态',(aes.status==='LIVE'?'LIVE · 正在发送':'FINAL · 上次会话结束报告')+(aes.ageMs!=null?`（${(aes.ageMs/1000).toFixed(1)} s 前）`:''),aes.status==='LIVE'?'ok':'warn');
  statusRow(box,'目标',(network.destIp??'?')+':'+(network.port??'?'));
  statusRow(box,'布局 / 声道',(aes.layout??'?')+(network.channels!=null?' / '+network.channels+' ch':''));
  statusRow(box,'SSRC',ssrcText(network.ssrc));
  statusRow(box,'发包统计',`packetsSent ${stats.packetsSent??'—'} · silent ${stats.silentPackets??'—'} · bytes ${bytesText(stats.bytesSent)}`); 
  statusRow(box,'丢帧 / 错误',`ringDropped ${stats.ringDroppedFrames??'—'} · sendErrors ${stats.sendErrors??'—'} · lateTicks ${stats.lateTicks??'—'}`); 
  if(rates.packetsPerSecond!=null)statusRow(box,'最近速率',`${rates.packetsPerSecond.toFixed(1)} 包/s · ${bitrateText(rates.bitsPerSecond)}`); 
  else statusRow(box,'最近速率','—（最终报告不含实时速率）','muted');
  if(aes.source)statusRow(box,'数据源',aes.source,'muted');
 }else{
  statusRow(box,'AES67 状态','NOT_RUN'+(aes.reason?' — '+reasonText(aes.reason):''),'muted');
 }
 const dante=state.dante??{};
 if(current.backend==='dante')statusRow(box,'Dante 状态',dante.status==='NOT_AVAILABLE'?'NOT_AVAILABLE — '+(dante.detail??'第三方实现未接入'):String(dante.status),'muted');
 else statusRow(box,'Dante 状态','NOT_AVAILABLE — 第三方 Inferno 后端未接入；当前环境无 Dante 时钟主设备，无法验证','muted');
 if(state.error)statusRow(box,'状态读取','失败：'+state.error,'error');
}
async function poll(){
 try{
  const [state,network]=await Promise.all([api('status'),api('network-status').catch(error=>({error:error.message}))]);
  const fsa=state.fsa;const code=state.backend?.sessionState??'后端离线 / 未启动';
  $('runtime').textContent=(fsa.known?(fsa.stale?'FSA 状态已过期':'FSA 在线'):'FSA 状态未知')+' · '+code+(fsa.message?' · '+fsa.message:'');
  $('endpointInfo').textContent=fsa.endpoint?'端点：'+fsa.endpoint:'';
  $('runtimeConfig').textContent=state.backend?.config?'后端当前内存配置：'+JSON.stringify(state.backend.config):'读取状态不会启动 NSp。';
  if(network.error)renderNetworkStatus({error:'当前 web-host 不支持 /api/web/network-status（需要部署新版 nsp-scheduler.exe）：'+network.error});
  else renderNetworkStatus(network);
 }catch(error){$('runtime').textContent='状态读取失败：'+error.message;}
 finally{setTimeout(()=>{if(document.hidden)setTimeout(poll,2000);else poll();},2000);}
}
async function load(next=id){
 if(loading)return;if(dirty&&!confirm('放弃未保存的修改，重新读取配置？'))return;loading=true;$('save').disabled=true;
 try{const value=await api('config/'+next);id=next;loaded=value;dirty=false;working=null;baseline=null;
  if(value.config&&typeof value.config==='object'&&!Array.isArray(value.config)){try{working=prepare(value.config,id);}catch{working=null;}}
  $('configTitle').textContent=id==='playback'?'播放与公共 DSP':'后端默认配置';$('playbackTab').setAttribute('aria-pressed',String(id==='playback'));$('backendTab').setAttribute('aria-pressed',String(id==='backend'));
  $('applyInfo').textContent=id==='playback'?'保存只写入配置文件，下一次启动 NSp 会话时读取；不会自动重启当前会话。':'保存后需要后端服务重启；FSA 重启不一定会重启已驻留的后端。';
  $('fileInfo').textContent=(value.exists?'文件：':'尚无文件；首次保存会创建并登记：')+value.path;
  $('revisionInfo').textContent='文件修订：'+value.revision+' · 回读确认在保存后执行';
  $('dirtyState').textContent='';$('diffDetails').hidden=true;$('diffPreview').textContent='';
  render();
  // Baseline after render(), because render() normalises optional blocks; only
  // real user edits should appear in the save-preview diff.
  if(working)baseline=structuredClone(working);
  rawDirty=false;$('rawConfig').value=value.raw??JSON.stringify(working??value.config,null,2);
  message(value.validationError?'当前配置校验：'+value.validationError+(value.validationField?'（字段 '+value.validationField+'）':'')+'。请修正后保存。':'已读取配置。',Boolean(value.validationError));
 }catch(error){message(error.message,true);}finally{loading=false;$('save').disabled=!loaded;}
}
$('configForm').addEventListener('submit',async event=>{
 event.preventDefault();
 if(rawDirty){message('请先将修改的 JSON 应用到表单，再保存。',true);return;}
 $('save').disabled=true;
 try{
  const config=collect();
  const problems=id==='playback'?validateAes67(config):[];
  if(problems.length){message('校验未通过：\n'+problemText(problems),true);focusField(problems[0].path);return;}
  const changes=diffConfig(baseline??loaded?.config??{},config);
  if(!changes.length){message('没有需要保存的修改。');return;}
  if(!confirm('即将保存以下修改：\n\n'+diffText(changes)+'\n\n保存后在下一次启动 NSp 会话时生效，不会重启当前会话。继续？'))return;
  const saved=await api('config/'+id,{method:'PUT',headers:{'Content-Type':'application/json','X-NSP-Token':token},body:JSON.stringify({revision:loaded.revision,config})});
  const readback=await api('config/'+id);
  const same=JSON.stringify(readback.config)===JSON.stringify(saved.config)&&readback.revision===saved.revision;
  loaded=readback;working=prepare(readback.config,id);baseline=structuredClone(working);dirty=false;rawDirty=false;
  $('dirtyState').textContent='已保存，未自动应用';$('diffDetails').hidden=true;$('diffPreview').textContent='';
  $('fileInfo').textContent='文件：'+readback.path;
  $('revisionInfo').textContent='文件修订：'+readback.revision+' · 回读校验：'+(same?'一致':'不一致');
  $('rawConfig').value=JSON.stringify(readback.config,null,2);
  message(same?'已保存，回读确认一致（revision '+readback.revision.slice(0,12)+'…）。下次启动 NSp 会话时生效。':'保存返回成功，但回读内容不一致，请重新读取后核对。',!same);
 }catch(error){message(error.message==='RevisionConflict'?'文件已被其他入口修改，请重新读取后再编辑。':error.message,true);}
 finally{$('save').disabled=false;}
});
$('playbackTab').onclick=()=>load('playback');$('backendTab').onclick=()=>load('backend');$('reload').onclick=()=>load();
$('toJson').onclick=()=>{try{$('rawConfig').value=JSON.stringify(collect(),null,2);rawDirty=false;message('表单已同步到 JSON，尚未保存。');}catch(error){message(error.message,true);}};
$('rawConfig').addEventListener('input',()=>{rawDirty=true;markDirty();});
$('fromJson').onclick=()=>{try{const value=JSON.parse($('rawConfig').value);if(!value||Array.isArray(value)||typeof value!=='object')throw Error('配置必须是 JSON 对象');working=prepare(value,id);render();rawDirty=false;markDirty();message('JSON 已应用到表单，尚未保存。');}catch(error){message(error.message,true);}};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
try{const bootstrap=await api('bootstrap');token=bootstrap.token;const listing=await api('drivers');for(const driver of listing.drivers){const option=document.createElement('option');option.value=driver.driver;option.label=driver.name;$('asioDrivers').append(option);}await load();poll();}catch(error){message('无法连接 Web API Host：'+error.message+'。请通过 FSA 的 Web 地址打开此页。',true);}
