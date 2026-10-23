"""Small Win32 helpers shared by resident controller and CLI."""
import ctypes as C,json,subprocess,os
from ctypes import wintypes as W
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
k=C.WinDLL('kernel32',use_last_error=True)
k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
k.GetProcessTimes.argtypes=[W.HANDLE]+[C.POINTER(W.FILETIME)]*4
k.CloseHandle.argtypes=[W.HANDLE]
k.GetTickCount64.restype=C.c_ulonglong
k.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)];k.GetExitCodeProcess.restype=W.BOOL
def creation(pid):
 h=k.OpenProcess(0x1000,False,pid)
 if not h:return None
 try:
  code=W.DWORD()
  if not k.GetExitCodeProcess(h,C.byref(code)) or code.value!=259:return None
  ts=[W.FILETIME() for _ in range(4)]
  if not k.GetProcessTimes(h,*[C.byref(t) for t in ts]):return None
  return ts[0].dwLowDateTime|(ts[0].dwHighDateTime<<32)
 finally:k.CloseHandle(h)
def run(args,timeout=15):
 p=subprocess.run([str(a) for a in args],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=timeout,creationflags=0x08000000)
 if p.returncode:raise RuntimeError(f'{Path(str(args[0])).name} failed ({p.returncode}): {p.stderr[-500:]}')
 return p.stdout
def ps(script,*args,timeout=15):
 return run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',script,*args],timeout)
def default_endpoint():
 return json.loads(ps(ROOT/'tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1'))['multimedia']
def winrt(endpoint):return '\\\\?\\SWD#MMDEVAPI#'+endpoint+'#{e6327cad-dcec-4949-ae8a-991e976a79d2}'
def atomic(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(path)
def home():return Path(os.environ['LOCALAPPDATA'])/'NativeSpatialPipe/pipeline-init'
