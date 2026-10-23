"""Independent spatial enumeration hook supervisor. Never owns NSp capture."""
import argparse,ctypes as C,hashlib,json,mmap,os,re,subprocess,sys,time,uuid
from pathlib import Path
from model import *
from windows import ROOT,k,creation,run,ps,atomic,home,default_endpoint
EXPECTED='487e51035a76323032acd5996511c221c449cd244051d3b0e057eec70538ca97'
def mapping(create=False):
 # Opening through Win32 prevents a reader accidentally creating a dead mapping.
 if not create:
  k.OpenFileMappingW.argtypes=[C.c_uint32,C.c_int,C.c_wchar_p];k.OpenFileMappingW.restype=C.c_void_p
  h=k.OpenFileMappingW(4,False,MAP_NAME)
  if not h:return None
  k.CloseHandle(h)
 return mmap.mmap(-1,MAP_SIZE,tagname=MAP_NAME,access=mmap.ACCESS_WRITE if create else mmap.ACCESS_READ)
def status():
 m=mapping()
 if m is None:return {'live':False,'reason':'No resident memory tag'}
 try:
  one=m[:];tag=decode_tag(one)
  if not tag or m[:4]!=one[:4]:return {'live':False,'reason':'Concurrent tag update'}
  current=creation(tag['pid'])
  tag['live']=eligible(tag,tag['endpoint'],k.GetTickCount64(),current)
  if tag['live']:
   try:tag['live']=default_endpoint().lower()==tag['endpoint'].lower()
   except Exception:tag['live']=False
  tag['formats']=[n for i,n in enumerate(NAMES) if tag['mask']&(1<<i)]
  return tag
 finally:m.close()
def publish(m,seq,state,flags,mask,endpoint,service=0):
 odd=seq+1
 m[:4]=struct.pack('<I',odd)
 buf=bytearray(MAP_SIZE)
 HEADER.pack_into(buf,0,odd,MAGIC,1,state,os.getpid(),flags,k.GetTickCount64(),creation(os.getpid()) or 0,service,mask)
 ep=endpoint.encode('utf-16-le')
 if len(ep)>1022:raise ValueError('Endpoint too long')
 buf[48:48+len(ep)]=ep;m[4:]=buf[4:];m[:4]=struct.pack('<I',odd+1)
 return odd+1
def refresh(names,agent=None):
 command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/pipeline-init/Spatial-Format.ps1'),'-Action','notify','-Formats',','.join(names)]
 p=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
 try:
  deadline=time.monotonic()+45
  while p.poll() is None:
   if agent and not agent.exports_sync.renew():raise RuntimeError('Hook lease lost during refresh')
   if time.monotonic()>deadline:raise RuntimeError('Configuration notification timeout')
   time.sleep(.25)
  if p.returncode:raise RuntimeError('Configuration notification failed')
 finally:
  if p.poll() is None:p.terminate()
  try:p.wait(timeout=3)
  except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
def serve(args):
 # Cross-session singleton is not required: Local namespace and per-user state.
 k.CreateMutexW.argtypes=[C.c_void_p,C.c_int,C.c_wchar_p];k.CreateMutexW.restype=C.c_void_p
 mutex=k.CreateMutexW(None,True,r'Local\NSp-PipelineInit-Owner-v1')
 if not mutex or C.get_last_error()==183:raise RuntimeError('Resident already exists')
 m=mapping(True);seq=0;names=selected_formats(args.groups);mask=mask_for(names)
 identity=args.endpoint;owner_created=creation(args.owner_pid) if args.owner_pid else None
 if not args.persist and not owner_created:raise ValueError('Nonpersistent hook requires a live --owner-pid')
 if default_endpoint().lower()!=identity.lower():raise ValueError('Target must be preferred multimedia endpoint')
 folder=home();folder.mkdir(parents=True,exist_ok=True);token=uuid.uuid4().hex
 stop_path=folder/('stop-'+token);record={'token':token,'endpoint':identity,'groups':args.groups,'persist':args.persist,
  'pid':os.getpid(),'created':creation(os.getpid()),'state':'starting','error':None,'cleanup':None}
 atomic(folder/'resident.json',record)
 sys.path.insert(0,str(ROOT/'work/oem-hook-deps/work/frida/package'));import frida
 session=script=None;service_created=None;retry_at=0;attempts=0
 def detach():
  nonlocal session,script
  if script:
   checks=script.exports_sync.cleanup();record['cleanup']=checks
   if not all(v['restored'] for v in checks):raise RuntimeError('Hook byte restoration mismatch')
   script.unload();script=None
  if session:session.detach();session=None
 try:
  while not stop_path.exists():
   if script is None:seq=publish(m,seq,1,2 if args.persist else 0,0,identity)
   if default_endpoint().lower()!=identity.lower():record['reason']='Preferred endpoint changed';break
   if not args.persist and creation(args.owner_pid)!=owner_created:record['reason']='Owner exited';break
   if script:
    try:
     if creation(record['servicePid'])!=service_created:raise RuntimeError('Service instance exited')
     if not script.exports_sync.renew():raise RuntimeError('Hook lease or bytes invalid')
    except Exception:
     # Same-instance health failure must restore before retry. An exited target
     # has no remaining memory to restore; never replay snapshots into a new PID.
     if creation(record['servicePid'])==service_created:detach()
     else:
      record['cleanup']={'targetExited':True};script=None
      if session:
       try:session.detach()
       except Exception:pass
      session=None
     retry_at=time.monotonic()+2
   if script is None and time.monotonic()>=retry_at:
    try:
     image=Path(os.environ['SystemRoot'])/'System32/AudioSrv.dll'
     if hashlib.sha256(image.read_bytes()).hexdigest()!=EXPECTED:raise RuntimeError('Unsupported AudioSrv build')
     service=run(['sc.exe','queryex','Audiosrv'])
     pid=int(re.search(r'PID\s+:\s*(\d+)',service).group(1));service_created=creation(pid)
     if not service_created:raise RuntimeError('Service identity unavailable')
     session=frida.attach(pid)
     cfg={'endpoint':identity,'guids':[FORMATS[n] for n in names],'indices':[LICENSE_INDEX[n] for n in names]}
     source=(Path(__file__).with_name('agent.js')).read_text().replace('CONFIG_PLACEHOLDER',json.dumps(cfg))
     script=session.create_script(source);script.load()
     if not script.exports_sync.renew():raise RuntimeError('Hook not healthy')
     record.update(servicePid=pid,serviceCreated=service_created,imageSha256=EXPECTED,snapshot=script.exports_sync.snapshot())
     atomic(folder/'resident.json',record) # rollback evidence before publishing ready
     refresh(names,script);attempts=0;record['error']=None
    except Exception as e:
     record['error']=repr(e);detach();attempts+=1;retry_at=time.monotonic()+min(30,2**min(attempts,5))
     if 'Unsupported AudioSrv' in str(e):raise
   if script:
    record['state']='ready';seq=publish(m,seq,2,1|(2 if args.persist else 0),mask,identity,record['servicePid'])
   else:record['state']='retrying'
   atomic(folder/'resident.json',record)
   # Frequent lease renewal; no endpoint or FSA initialization is needed to stop.
   time.sleep(.5)
 except Exception as e:record['error']=repr(e);record['state']='faulted'
 finally:
  seq=publish(m,seq,3,0,0,identity)
  try:detach();record['state']='stopped'
  except Exception as e:record.update(state='recovery-required',cleanupError=repr(e))
  try:refresh(names)
  except Exception as e:record['refreshError']=repr(e)
  atomic(folder/'resident.json',record);publish(m,seq,0,0,0,identity);m.close();k.CloseHandle(mutex)
def stop():
 path=home()/'resident.json'
 if not path.exists():return {'stopped':True,'reason':'No resident recorded'}
 r=json.loads(path.read_text(encoding='utf8'))
 if creation(r['pid'])!=r['created']:
  # Frida lease independently cleans up after host death. Never trust a disk
  # "ready" flag; recovery audits original bytes on the same service instance.
  if isinstance(r.get('cleanup'),list) and all(c['restored'] for c in r['cleanup']):return {'stopped':True,'targetExited':True}
  return audit_orphan(r)
 (home()/('stop-'+r['token'])).touch()
 deadline=time.monotonic()+12
 while time.monotonic()<deadline:
  r=json.loads(path.read_text(encoding='utf8'))
  if r['state']=='stopped':return {'stopped':True,'cleanup':r.get('cleanup')}
  if r['state']=='recovery-required':break
  time.sleep(.1)
 return {'stopped':False,'reason':'Cleanup not verified','record':r}
def audit_orphan(r):
 if creation(r.get('servicePid',0))!=r.get('serviceCreated'):
  proof={'stopped':True,'targetExited':True}
 else:
  # A second session reads only; it does not copy old code into a live process.
  sys.path.insert(0,str(ROOT/'work/oem-hook-deps/work/frida/package'));import frida
  time.sleep(5.1)
  with frida.attach(r['servicePid']) as s:
   entries=r.get('snapshot',{}).get('entries',[])
   code='const es='+json.dumps(entries)+';rpc.exports={audit(){const m=Process.getModuleByName("AudioSrv.dll");return es.map(e=>({rva:e.rva,restored:JSON.stringify(Array.from(new Uint8Array(m.base.add(e.rva).readByteArray(32))))===JSON.stringify(e.before)}));}};'
   js=s.create_script(code);js.load();checks=js.exports_sync.audit();js.unload()
  proof={'stopped':bool(checks) and all(c['restored'] for c in checks),'cleanup':checks}
 r.update(state='stopped' if proof['stopped'] else 'recovery-required',recovery=proof)
 atomic(home()/'resident.json',r);return proof
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
 for action in ('start','serve'):
  q=sub.add_parser(action);q.add_argument('--endpoint',required=True);q.add_argument('--groups',nargs='+',choices=GROUPS,required=True);q.add_argument('--persist',action='store_true');q.add_argument('--owner-pid',type=int)
 sub.add_parser('status');sub.add_parser('recover');sub.add_parser('stop')
 args=p.parse_args()
 if args.action=='status':print(json.dumps(status()));return
 if args.action in ('stop','recover'):
  r=stop();print(json.dumps(r));return 0 if r['stopped'] else 1
 if args.action=='serve':return serve(args)
 live=status()
 if live.get('live'):
  if live['endpoint'].lower()==args.endpoint.lower() and set(live['formats'])==set(selected_formats(args.groups)) and bool(live['flags']&2)==args.persist:print(json.dumps(live));return
  raise RuntimeError('Different hook configuration already active; stop it explicitly')
 command=[sys.executable,str(Path(__file__).resolve()),'serve',*sys.argv[2:]]
 subprocess.Popen(command,creationflags=0x08000000|0x00000200,close_fds=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=ROOT)
 deadline=time.monotonic()+12
 while time.monotonic()<deadline:
  live=status()
  if live.get('live'):print(json.dumps(live));return
  time.sleep(.2)
 raise RuntimeError('Resident not ready; inspect resident.json; resident may be retrying')
if __name__=='__main__':
 try:sys.exit(main() or 0)
 except Exception as e:print(json.dumps({'error':str(e)}));sys.exit(1)
