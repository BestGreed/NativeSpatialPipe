"""Owned bounded native shared-memory receiver; Frida only carries control messages."""
import ctypes as C,json,pathlib,threading,uuid
ROOT=pathlib.Path(__file__).resolve().parents[2]
HEADER=8192;PAYLOAD=160*(32+4096*4)
class Receiver:
 def __init__(self,target_pid=None):
  self.dll_path=ROOT/'build/native-send/nsp_native_send.dll';self.lib=C.CDLL(str(self.dll_path))
  signatures={'nsp_link_open':([C.c_wchar_p,C.c_wchar_p,C.c_int],C.c_void_p),'nsp_link_close':([C.c_void_p],None),'nsp_link_read':([C.c_void_p,C.c_void_p,C.c_uint32,C.c_void_p,C.c_uint32,C.c_void_p],C.c_int),'nsp_link_wait':([C.c_void_p,C.c_uint32],C.c_int),'nsp_link_wake':([C.c_void_p],C.c_int)}
  for n,(args,r) in signatures.items():f=getattr(self.lib,n);f.argtypes=args;f.restype=r
  prefix='Local\\'
  if target_pid is not None:
   import os
   k=C.WinDLL('kernel32',use_last_error=True);k.ProcessIdToSessionId.argtypes=[C.c_uint32,C.POINTER(C.c_uint32)];k.ProcessIdToSessionId.restype=C.c_int
   sessions=[]
   for pid in (os.getpid(),target_pid):
    sid=C.c_uint32()
    if not k.ProcessIdToSessionId(pid,C.byref(sid)):raise C.WinError(C.get_last_error())
    sessions.append(sid.value)
   if sessions[0]!=sessions[1]:prefix='Global\\'
  token=uuid.uuid4().hex;self.config={'dll':str(self.dll_path),'name':prefix+'NSP-'+token,'event':prefix+'NSP-event-'+token}
  self.handle=self.lib.nsp_link_open(self.config['name'],self.config['event'],1)
  if not self.handle:raise OSError('Native receiver create failed: Win32 '+str(self.lib.nsp_link_open_error())+'; cross-session Global mapping requires elevated creation')
  self.stop=threading.Event();self.thread=None;self.received=0;self.errors=[];self.summary=None;self.thread_id=None
 def start(self,callback):
  if self.thread:raise RuntimeError('Receiver already started')
  def loop():
   self.thread_id=threading.get_native_id();header=C.create_string_buffer(HEADER);data=C.create_string_buffer(PAYLOAD);sizes=(C.c_uint32*2)()
   try:
    while True:
     result=self.lib.nsp_link_read(self.handle,header,HEADER,data,PAYLOAD,sizes)
     if result<0:raise RuntimeError('Native read: '+str(result))
     if result:
      record=json.loads(C.string_at(header,sizes[0]));raw=C.string_at(data,sizes[1]);callback({'type':'send','payload':{'kind':'snapshot','record':record}},raw);self.received+=1
     elif self.stop.is_set():break
     elif self.lib.nsp_link_wait(self.handle,100)<0:raise RuntimeError('Native wait failed')
   except Exception as exc:self.errors.append(repr(exc))
  self.thread=threading.Thread(target=loop,name='native-send-receiver',daemon=True);self.thread.start()
 def finish(self):
  if self.summary is not None:return self.summary
  self.stop.set();self.lib.nsp_link_wake(self.handle)
  if self.thread:self.thread.join(5)
  if self.thread and self.thread.is_alive():raise RuntimeError('Receiver did not stop; mapping retained')
  self.lib.nsp_link_close(self.handle);self.handle=None
  self.summary={'received':self.received,'errors':self.errors,'threadId':self.thread_id,'threadStopped':True,'passed':not self.errors};return self.summary
