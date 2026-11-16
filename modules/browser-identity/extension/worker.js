// Identity/lifecycle only. No tabCapture, getUserMedia or audio processing.
let socket=null,instance='',seq=0,revision=0,timer=null;
const blocked=new Set();
async function snapshot(reason){
 const rev=++revision;
 try{
  const tabs=await chrome.tabs.query({});const targets=await chrome.debugger.getTargets();
  const targetByTab=new Map(targets.filter(t=>t.tabId!==undefined).map(t=>[t.tabId,t.id]));
  const windows=await chrome.windows.getAll();const bounds=new Map(windows.map(w=>[w.id,{left:w.left,top:w.top,width:w.width,height:w.height,state:w.state}]));
  const rows=await Promise.all(tabs.filter(t=>t.id!==undefined&&/^(https?|file):/.test(t.url||'')).map(async t=>{
   const frames=await chrome.webNavigation.getAllFrames({tabId:t.id}).catch(()=>[]);
   return {tabId:t.id,windowId:t.windowId,targetId:targetByTab.get(t.id),audible:!!t.audible,active:!!t.active,bounds:bounds.get(t.windowId),frames:(frames||[]).filter(f=>f.documentLifecycle==='active'&&f.processId>=0).map(f=>({frameId:f.frameId,parentFrameId:f.parentFrameId,documentId:f.documentId,processId:f.processId,lifecycle:f.documentLifecycle}))};
  }));
  if(rev!==revision||socket?.readyState!==WebSocket.OPEN)return;
  socket.send(JSON.stringify({version:1,instance,seq:++seq,reason,blockedTabs:[...blocked],tabs:rows}));
 }catch(e){console.debug('NSp identity snapshot',String(e));}
}
function invalidate(details){if(details.frameId===0){blocked.add(details.tabId);socket?.readyState===WebSocket.OPEN&&socket.send(JSON.stringify({version:1,instance,seq:++seq,invalidateTab:details.tabId}));}snapshot('navigation-start');}
function committed(details){if(details.frameId===0)blocked.delete(details.tabId);snapshot('committed');}
async function connect(){
 const stored=await chrome.storage.session.get('instance');instance=stored.instance||crypto.randomUUID();if(!stored.instance)await chrome.storage.session.set({instance});
 const settings=await chrome.storage.local.get('port');const port=Number(settings.port)||8784;
 socket=new WebSocket('ws://127.0.0.1:'+port+'/identity');socket.onopen=()=>snapshot('connected');socket.onclose=()=>{socket=null;setTimeout(connect,1000);};socket.onerror=()=>socket?.close();
}
chrome.webNavigation.onBeforeNavigate.addListener(invalidate);
chrome.webNavigation.onCommitted.addListener(committed);
chrome.webNavigation.onErrorOccurred.addListener(d=>{if(d.frameId===0)blocked.delete(d.tabId);snapshot('navigation-error');});
chrome.webNavigation.onCompleted.addListener(()=>snapshot('completed'));
chrome.webNavigation.onTabReplaced.addListener(()=>snapshot('replaced'));
chrome.tabs.onRemoved.addListener(id=>{blocked.delete(id);socket?.readyState===WebSocket.OPEN&&socket.send(JSON.stringify({version:1,instance,seq:++seq,invalidateTab:id}));snapshot('removed');});
chrome.tabs.onUpdated.addListener(()=>snapshot('updated'));
chrome.tabs.onAttached.addListener(()=>snapshot('attached'));chrome.tabs.onDetached.addListener(()=>snapshot('detached'));
chrome.windows.onBoundsChanged.addListener(()=>snapshot('bounds'));
timer=setInterval(()=>snapshot('heartbeat'),1000);connect();
