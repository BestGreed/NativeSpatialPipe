"""Control adapter only; native executable loads/controls MinHook. No Frida import."""
import ctypes as C,json,pathlib,subprocess,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
class Config(C.Structure):
 _fields_=[('magic',C.c_uint32),('size',C.c_uint32),('fixture',C.c_uint32),('duration_ms',C.c_uint32),('budget',C.c_uint64),('capture',C.c_wchar*1024),('send',C.c_wchar*1024),('mapping',C.c_wchar*256),('event',C.c_wchar*256),('error',C.c_uint32),('win32',C.c_uint32),('output',C.c_char*10000)]
class Module(C.Structure):
 _fields_=[('dwSize',C.c_uint32),('th32ModuleID',C.c_uint32),('th32ProcessID',C.c_uint32),('GlblcntUsage',C.c_uint32),('ProccntUsage',C.c_uint32),('modBaseAddr',C.c_void_p),('modBaseSize',C.c_uint32),('hModule',C.c_void_p),('szModule',C.c_wchar*256),('szExePath',C.c_wchar*260)]
def modules(pid):
 k=C.WinDLL('kernel32',use_last_error=True);k.CreateToolhelp32Snapshot.argtypes=[C.c_uint32,C.c_uint32];k.CreateToolhelp32Snapshot.restype=C.c_void_p;k.CloseHandle.argtypes=[C.c_void_p]
 for n in ['Module32FirstW','Module32NextW']:getattr(k,n).argtypes=[C.c_void_p,C.POINTER(Module)]
 h=k.CreateToolhelp32Snapshot(0x18,pid)
 if h==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
 result=[];m=Module();m.dwSize=C.sizeof(m)
 try:
  more=k.Module32FirstW(h,C.byref(m))
  if not more:raise C.WinError(C.get_last_error())
  while more:result.append(m.szModule);more=k.Module32NextW(h,C.byref(m))
 finally:k.CloseHandle(h)
 return sorted(result,key=str.lower)
class Controller:
 def __init__(self,pid,transport,duration=120,fixture=False,capture_dll=None):
  self.pid=pid;self.result=None;self.started=False;self.callback=None;self.exports_sync=self
  self.config=Config();c=self.config;c.magic=0x4e534831;c.size=C.sizeof(c);c.fixture=fixture;c.duration_ms=int(duration*1000);c.budget=max(512*1024*1024,c.duration_ms*32000)
  c.capture=str(ROOT/'build/native-capture/nsp_native_capture.dll');c.send=transport['dll'];c.mapping=transport['name'];c.event=transport['event']
  if capture_dll is not None:c.capture=str(pathlib.Path(capture_dll).resolve(strict=True))
  self.before=modules(pid)
  if any(any(t in n.lower() for t in ['frida','gum-js','quickjs','v8']) for n in self.before):raise RuntimeError('Runtime module already present in target')
  f=tempfile.NamedTemporaryFile(prefix='nsp-hook-',suffix='.bin',delete=False);self.path=pathlib.Path(f.name);f.write(bytes(c));f.close()
 def _call(self,action):
  p=subprocess.run([str(ROOT/'build/native-hook/nsp_hook_control.exe'),str(self.pid),str(ROOT/'build/native-hook/nsp_native_hook.dll'),action,str(self.path)],capture_output=True,text=True,timeout=40,creationflags=subprocess.CREATE_NO_WINDOW)
  if p.returncode:raise RuntimeError(f'Native {action}: {p.stderr.strip()}')
  return json.loads(p.stdout)
 def on(self,event,callback):self.callback=callback
 def load(self):
  ready=self._call('start');self.started=True;self.during=modules(self.pid)
  if self.callback:self.callback({'type':'send','payload':ready},None)
 def stop(self):
  if self.result is not None:return self.result
  r=self._call('stop');r['errors']=[]
  for n in ['concurrentRejected','faults','sendErrors']:
   if r[n]:r['errors'].append(f'{n}: {r[n]}')
  r['nativeSendStats']={'backend':'native-shared-memory','sent':r['sent'],'rejected':r['sendErrors'],'libraryReleased':r['transportLibraryReleased']}
  if not r['captureLibraryReleased']:r['errors'].append('Capture DLL release failed')
  r.update(capturePack='pure-c',compactDescriptor=True,tailTrace=None,layouts={})
  r['moduleAudit']={'before':self.before,'during':self.during,'after':modules(self.pid),'scope':'Loader module inventory; not a scan of anonymous allocations'}
  self.result=r;return r
 def unload(self):
  if self.started and self.result is None:raise RuntimeError('Native hook must stop before control disconnect')
  self.path.unlink(missing_ok=True)
