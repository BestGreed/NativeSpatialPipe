"""Control-plane adapter for the native MinHook enumeration DLL. No JS runtime."""
import ctypes as C
import json
import os
import subprocess
import tempfile
from pathlib import Path
from model import LICENSE_INDEX
from windows import ROOT,creation,k

DIRECTORY=ROOT/'build/enum-hook'
class Config(C.Structure):
 _fields_=[('magic',C.c_uint32),('size',C.c_uint32),('fixture',C.c_uint32),('duration_ms',C.c_uint32),
  ('owner_pid',C.c_uint32),('host_pid',C.c_uint32),('target_created',C.c_uint64),('legacy_stopped',C.c_uint32),
  ('owner_handle',C.c_uint64),('host_handle',C.c_uint64),('mask',C.c_uint32),('error',C.c_uint32),('win32',C.c_uint32),
  ('endpoint',C.c_wchar*512),('output',C.c_char*10000)]

class Controller:
 def __init__(self,pid,endpoint,names,fixture=False):
  if not endpoint or len(endpoint.encode('utf-16-le'))>1022:raise ValueError('Invalid endpoint')
  self.pid=pid;self.config=Config();c=self.config
  c.magic=0x454e5331;c.size=C.sizeof(c);c.fixture=fixture
  c.host_pid=os.getpid();c.target_created=creation(pid) or 0
  if not c.target_created:raise RuntimeError('Service identity unavailable')
  c.endpoint=endpoint;c.mask=sum(1<<LICENSE_INDEX[n] for n in set(names))
  f=tempfile.NamedTemporaryFile(prefix='nsp-enum-',suffix='.bin',delete=False)
  self.path=Path(f.name)
  try:f.write(bytes(c))
  finally:f.close()
  self.started=False;self.initial=None
 def _call(self,action):
  p=subprocess.run([str(DIRECTORY/'nsp_enum_control.exe'),str(self.pid),str(DIRECTORY/'nsp_enum_hook.dll'),action,str(self.path)],
   capture_output=True,text=True,timeout=40,creationflags=0x08000000)
  if p.returncode:raise RuntimeError(f'Native enumeration {action}: {p.stderr.strip()}')
  return json.loads(p.stdout)
 def load(self):
  self.initial=self._call('start');self.started=True
 def renew(self):
  r=self._call('status');return r['state']==1 and r['hookEnabled'] and not r['stopError']
 def snapshot(self):return self.initial
 def cleanup(self):
  r=self._call('stop');self.started=False;return r['entries']
 def unload(self):
  if self.started:raise RuntimeError('Enumeration hook must be stopped first')
  self.path.unlink(missing_ok=True)

def audit_bytes(pid,entries):
 """Read-only orphan recovery. Never inject code or write saved bytes back."""
 if len(entries)!=3 or {e['rva'] for e in entries}!={0x57558,0x57248,0x8e304}:raise ValueError('Incomplete recovery evidence')
 class Module(C.Structure):
  _fields_=[('dwSize',C.c_uint32),('th32ModuleID',C.c_uint32),('th32ProcessID',C.c_uint32),('GlblcntUsage',C.c_uint32),('ProccntUsage',C.c_uint32),('modBaseAddr',C.c_void_p),('modBaseSize',C.c_uint32),('hModule',C.c_void_p),('szModule',C.c_wchar*256),('szExePath',C.c_wchar*260)]
 k.CreateToolhelp32Snapshot.argtypes=[C.c_uint32,C.c_uint32];k.CreateToolhelp32Snapshot.restype=C.c_void_p
 k.Module32FirstW.argtypes=[C.c_void_p,C.POINTER(Module)];k.Module32NextW.argtypes=[C.c_void_p,C.POINTER(Module)]
 h=k.CreateToolhelp32Snapshot(0x18,pid)
 if h==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
 base=0;m=Module();m.dwSize=C.sizeof(m)
 try:
  more=k.Module32FirstW(h,C.byref(m))
  while more:
   if m.szModule.lower()=='audiosrv.dll':base=m.modBaseAddr;break
   more=k.Module32NextW(h,C.byref(m))
 finally:k.CloseHandle(h)
 if not base:raise RuntimeError('AudioSrv module unavailable')
 p=k.OpenProcess(0x1010,False,pid)
 if not p:raise C.WinError(C.get_last_error())
 k.ReadProcessMemory.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
 checks=[]
 try:
  for entry in entries:
   if entry['rva'] not in (0x57558,0x57248,0x8e304) or len(entry['before'])!=32:raise ValueError('Invalid recovery evidence')
   data=(C.c_ubyte*32)();got=C.c_size_t()
   if not k.ReadProcessMemory(p,base+entry['rva'],data,32,C.byref(got)) or got.value!=32:raise C.WinError(C.get_last_error())
   checks.append({'rva':entry['rva'],'restored':list(data)==entry['before']})
 finally:k.CloseHandle(p)
 return checks
