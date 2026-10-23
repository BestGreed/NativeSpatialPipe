import {channelLabels,orders,getPath,setPath,prepare,numeric,finish,outputLayouts,layoutFor,migrateLayout} from './configuration-model.js';
const $=id=>document.getElementById(id);
let token='',id='playback',loaded=null,working=null,dirty=false,loading=false,rawDirty=false,routePreview=null;
const formats=['WindowsSonic','DTSXForHomeTheater','DolbyAtmosForHomeTheater','DolbyAtmosForHeadphones','DolbyAtmosForSpeakers','DTSHeadphoneX','DTSXUltra'];
function message(text,error=false){$('result').textContent=text;$('result').dataset.error=String(error);}
function markDirty(){dirty=true;$('dirtyState').textContent='有未保存修改';}
async function api(path,options={}){const response=await fetch('/api/web/'+path,{cache:'no-store',...options});const data=await response.json();if(!response.ok)throw Error(data.error??`HTTP ${response.status}`);return data;}
function field(container,path,label,type='number',options=null,note='',offset=0){
 const row=document.createElement('label');row.className='config-field';const title=document.createElement('span');title.textContent=label;row.append(title);
 const input=document.createElement(options?'select':'input');input.dataset.path=path;input.dataset.kind=type;
 if(options){for(const value of options){const option=document.createElement('option');option.value=typeof value==='object'?value.value:value;option.textContent=typeof value==='object'?value.label:value;if(value.disabled)option.disabled=true;input.append(option);}}
 else{input.type=type==='integer'?'number':type;if(type==='number')input.step='any';if(type==='integer')input.step='1';}
 const value=getPath(working,path);if(type==='checkbox')input.checked=value??false;else input.value=value==null?'':offset?value+offset:value;
 if(offset){input.dataset.offset=String(offset);input.min=String(offset);input.max='256';}
 if(path==='driver')input.setAttribute('list','asioDrivers');
 input.addEventListener('input',()=>{markDirty();if(path.startsWith('routing.'))updateRoutePreview();});input.addEventListener('change',()=>{if(path==='renderer'||path==='channelOrder'||path==='outputLayout'){try{const next=collect();working=path==='outputLayout'?migrateLayout(next,working.outputLayout,next.outputLayout):next;render();}catch(error){message(error.message,true);}}else if(path.startsWith('routing.'))updateRoutePreview();});
 row.append(input);if(note){const small=document.createElement('small');small.textContent=note;row.append(small);}container.append(row);
}
function group(title){const fieldset=document.createElement('fieldset');const legend=document.createElement('legend');legend.textContent=title;fieldset.append(legend);const body=document.createElement('div');body.className='config-fields';fieldset.append(body);$('fields').append(fieldset);return body;}
function render(){
 routePreview=null;$('fields').replaceChildren();if(!working)return;
 if(id==='backend'){
  const body=group('后端启动默认值');field(body,'version','配置版本','integer');field(body,'spatialFormat','默认空间格式','text',formats,'这是启动默认值，不会切换当前端点；FSA 启动时可能显式指定格式。');field(body,'fullTiming','完整时序统计','checkbox',null,'保存后需要重启后端服务才能加载。');return;
 }
 const channelLabels=layoutFor(working.outputLayout).channels;
 const recording=group('诊断录制（默认关闭）');field(recording,'recording.pcm','PCM 旁录','checkbox',null,'独立有界录音池，保存 DSP 前音频；磁盘跟不上会丢录音块，不阻塞播放。');field(recording,'recording.telemetrySnapshots','遥测快照存档','checkbox',null,'额外保存 JSONL，可能占用大量磁盘；关闭后实时遥测与可视化仍可使用。');
 let body=group('渲染器与输出设备');field(body,'renderer','渲染后端','text',['roar','lira'],'ROAR 当前使用 OLR 模式；LIRA 是独立渲染后端。');field(body,'driver','ASIO 驱动标识','text',null,'从本机注册驱动中选择，或输入 asio:{CLSID}。这里只读取注册表，不打开设备。');
 field(body,'outputLayout','扬声器布局','text',[...outputLayouts.map(p=>({value:p.id,label:p.id+' · '+p.channels.length+' 声道'})),...(working.renderer==='lira'?[]:[{value:'roar-custom-6dof',label:'6DoF 测量自定义（预留）',disabled:true}])],'切换布局按声道名称迁移校准和接线；新增声道使用默认值，保存后下次会话生效。');
 field(body,'sourceStream','源流索引（0–3）','integer');field(body,'sampleRate','设备采样率（Hz）');field(body,'bufferFrames','驱动缓冲（帧，0 为驱动默认）','integer');field(body,'clockSync','匹配设备时钟','checkbox');field(body,'prebufferFrames','预缓冲（源帧，0 为自动）','integer');field(body,'drainTimeoutMs','显式停止时排空超时（ms）','integer');
 body=group('公共 DSP · 渲染输出池内处理');field(body,'outputProcessing.bypass','跳过整个公共 DSP','checkbox',null,'开启后音量、trim、延迟和 limiter 均不处理；中性配置也会自动跳过。');field(body,'master','主音量（线性，1 为原值）');field(body,'outputProcessing.limiter.enabled','启用 limiter','checkbox');field(body,'outputProcessing.limiter.ceiling','峰值上限（0–1）');field(body,'outputProcessing.limiter.releaseMs','释放时间（ms）');field(body,'outputProcessing.limiter.lookaheadFrames','前视（0–4800 源帧）','integer','', '开启 limiter 时增加相应延迟；源采样率为 48 kHz。');
 body=group('声道校准');
 const wrap=document.createElement('div');wrap.className='config-channels';const table=document.createElement('table');const head=document.createElement('tr');for(const title of ['标准声道','Trim（线性）','延迟（源帧）']){const th=document.createElement('th');th.textContent=title;head.append(th);}table.append(head);
 for(let i=0;i<channelLabels.length;i++){const row=document.createElement('tr');const name=document.createElement('th');name.scope='row';name.textContent=channelLabels[i];row.append(name);for(const [key,kind] of [['trim','number'],['delayFrames','integer']]){const cell=document.createElement('td');field(cell,`outputProcessing.${key}.${i}`,`${channelLabels[i]} ${key}`,kind);cell.querySelector('span').hidden=true;row.append(cell);}table.append(row);}wrap.append(table);body.parentElement.append(wrap);
 const route=group('设备路由');field(route,'channelOrder','输出声道排列','text',[{value:'olr',label:'标准顺序'},{value:'dvs-speakers',label:'DVS 扬声器接线顺序（仅 7.1.4）',disabled:working.outputLayout!=='7.1.4'},{value:'custom',label:'自定义顺序'}],'切换后，下方每个输出槽的声道随之变化；设备输出通道号保留。');
 routePreview=document.createElement('p');routePreview.className='route-preview';routePreview.dataset.routePreview='true';routePreview.setAttribute('aria-live','polite');route.parentElement.append(routePreview);
 const standard=channelLabels.map((_,i)=>i);const source=working.channelOrder==='custom'?(working.routing.sourceChannelIndices??standard):working.channelOrder==='olr'?standard:orders[working.channelOrder];
 if(working.channelOrder==='custom')working.routing.sourceChannelIndices??=standard.slice();
 const routeWrap=document.createElement('div');routeWrap.className='config-channels';const routeTable=document.createElement('table');const routeHead=document.createElement('tr');for(const title of ['输出槽','声道','ASIO 设备输出通道（从 1 开始）']){const th=document.createElement('th');th.textContent=title;routeHead.append(th);}routeTable.append(routeHead);
 for(let i=0;i<channelLabels.length;i++){const row=document.createElement('tr');const slot=document.createElement('th');slot.scope='row';slot.textContent=String(i+1);row.append(slot);const speaker=document.createElement('td');
  if(working.channelOrder==='custom')field(speaker,`routing.sourceChannelIndices.${i}`,`输出槽 ${i+1} 的声道`,'integer',channelLabels.map((label,value)=>({value,label})));else speaker.textContent=channelLabels[source?.[i]]??'?';
  row.append(speaker);const device=document.createElement('td');field(device,`routing.physicalChannels.${i}`,`输出槽 ${i+1} 的设备输出通道`,'integer',null,'',1);device.querySelector('span').hidden=true;row.append(device);routeTable.append(row);
 }routeWrap.append(routeTable);route.parentElement.append(routeWrap);
 if(working.renderer!=='lira'){working.positionMapping??={mode:'scaled',reference:1};body=group('ROAR / OLR 坐标映射');field(body,'positionMapping.mode','半径映射','text',['scaled','direction']);field(body,'positionMapping.reference','半径参考值','number',null,'scaled 保留完整半径并除以参考值；direction 使用单位方向，参考值必须为 1。');}
 if(working.renderer!=='lira'){working.roarCustomLayout??={version:1,measurementFile:null};const slot=group('ROAR · 6DoF 自定义布局插槽（预留）');field(slot,'roarCustomLayout.measurementFile','测量配置路径（可留空）','text',null,'仅保存插槽信息；本轮不加载测量文件，也不启用自定义几何或实时追踪。');}
 body=group('模块路径');field(body,'bridgeDll','ROAR 桥接 DLL（绝对路径）','text');field(body,'asioDll','ASIO 输出 DLL（绝对路径）','text');field(body,'rendererDll','LIRA DLL（LIRA 时必填）','text');
 updateRoutePreview();
}
function updateRoutePreview(){if(!routePreview)return;try{const value=collect();const channelLabels=layoutFor(value.outputLayout).channels;const source=value.channelOrder==='custom'?value.routing.sourceChannelIndices:value.channelOrder==='olr'?channelLabels.map((_,i)=>i):orders[value.channelOrder];routePreview.textContent='声道排列：'+source.map(index=>channelLabels[index]??'?').join(' → ')+'\n设备输出：'+source.map((index,slot)=>`${channelLabels[index]??'?'} → 第 ${value.routing.physicalChannels[slot]+1} 路`).join(' ｜ ');}catch{routePreview.textContent='请补全有效配置，查看声道与设备输出的对应关系。';}}
function collect(){if(!working)throw Error('配置尚未加载');const value=structuredClone(working);for(const input of $('fields').querySelectorAll('[data-path]')){const kind=input.dataset.kind;setPath(value,input.dataset.path,kind==='checkbox'?input.checked:(kind==='integer'||kind==='number'?numeric(input.value,kind==='integer')-Number(input.dataset.offset??0):input.value));}return id==='playback'?finish(value):value;}
async function load(next=id){
 if(loading)return;if(dirty&&!confirm('放弃未保存的修改，重新读取配置？'))return;loading=true;$('save').disabled=true;
 try{const value=await api('config/'+next);id=next;loaded=value;dirty=false;working=null;
  if(value.config&&typeof value.config==='object'&&!Array.isArray(value.config)){try{working=prepare(value.config,id);}catch{working=null;}}
  $('configTitle').textContent=id==='playback'?'播放与公共 DSP':'后端默认配置';$('playbackTab').setAttribute('aria-pressed',String(id==='playback'));$('backendTab').setAttribute('aria-pressed',String(id==='backend'));
  $('applyInfo').textContent=id==='playback'?'保存后在下一次启动 NSp 会话时读取；不自动重启。':'保存后需要后端服务重启；FSA 重启不一定会重启已驻留的后端。';
  $('fileInfo').textContent=(value.exists?'文件：':'尚无文件；首次保存会创建并登记：')+value.path;$('revisionInfo').textContent='文件修订：'+value.revision+' · 当前会话是否已加载：未知（不假定保存即生效）';$('dirtyState').textContent='';
  render();rawDirty=false;$('rawConfig').value=value.raw??JSON.stringify(working??value.config,null,2);message(value.validationError?'当前配置校验：'+value.validationError+'。请修正后保存。':'已读取配置。',Boolean(value.validationError));
 }catch(error){message(error.message,true);}finally{loading=false;$('save').disabled=!loaded;}
}
$('configForm').addEventListener('submit',async event=>{event.preventDefault();$('save').disabled=true;try{if(rawDirty)throw Error('请先将修改的 JSON 应用到表单，再保存。');const config=collect();const saved=await api('config/'+id,{method:'PUT',headers:{'Content-Type':'application/json','X-NSP-Token':token},body:JSON.stringify({revision:loaded.revision,config})});loaded=saved;working=prepare(saved.config,id);dirty=false;$('dirtyState').textContent='已保存，未自动应用';$('fileInfo').textContent='文件：'+saved.path;$('revisionInfo').textContent='文件修订：'+saved.revision+' · 当前会话是否已加载：未知';$('rawConfig').value=JSON.stringify(saved.config,null,2);message('校验通过，已保存。'+(id==='playback'?'下次启动会话时读取。':'重启后端服务后加载。'));}catch(error){message(error.message==='RevisionConflict'?'文件已被其他入口修改，请重新读取后再编辑。':error.message,true);}finally{$('save').disabled=false;}});
$('playbackTab').onclick=()=>load('playback');$('backendTab').onclick=()=>load('backend');$('reload').onclick=()=>load();
$('toJson').onclick=()=>{try{$('rawConfig').value=JSON.stringify(collect(),null,2);rawDirty=false;message('表单已同步到 JSON，尚未保存。');}catch(error){message(error.message,true);}};
$('rawConfig').addEventListener('input',()=>{rawDirty=true;markDirty();});
$('fromJson').onclick=()=>{try{const value=JSON.parse($('rawConfig').value);if(!value||Array.isArray(value)||typeof value!=='object')throw Error('配置必须是 JSON 对象');working=prepare(value,id);render();rawDirty=false;markDirty();message('JSON 已应用到表单，尚未保存。');}catch(error){message(error.message,true);}};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
async function poll(){try{const state=await api('status');const fsa=state.fsa;const code=state.backend?.sessionState??'后端离线 / 未启动';$('runtime').textContent=(fsa.known?(fsa.stale?'FSA 状态已过期':'FSA 在线'):'FSA 状态未知')+' · '+code+(fsa.message?' · '+fsa.message:'');$('endpointInfo').textContent=fsa.endpoint?'端点：'+fsa.endpoint:'';$('runtimeConfig').textContent=state.backend?.config?'后端当前内存配置：'+JSON.stringify(state.backend.config):'读取状态不会启动 NSp。';}catch(error){$('runtime').textContent='状态读取失败：'+error.message;}finally{setTimeout(()=>{if(document.hidden)setTimeout(poll,2000);else poll();},2000);}}
try{const bootstrap=await api('bootstrap');token=bootstrap.token;const listing=await api('drivers');for(const driver of listing.drivers){const option=document.createElement('option');option.value=driver.driver;option.label=driver.name;$('asioDrivers').append(option);}await load();poll();}catch(error){message('无法连接 Web API Host：'+error.message+'。请通过 FSA 的 Web 地址打开此页。',true);}
