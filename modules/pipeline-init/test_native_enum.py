"""Real MinHook and native controller tests; no AudioSrv/FSA/device changes."""
import ctypes as C
import json
import subprocess
import sys
import time
import unittest
from native_enum import Config,Controller,DIRECTORY
from model import GROUPS

def config(mask=0x3c):
 c=Config();c.magic=0x454e5331;c.size=C.sizeof(c);c.fixture=1;c.mask=mask;c.endpoint='target-endpoint'
 k=C.WinDLL('kernel32',use_last_error=True)
 k.OpenProcess.argtypes=[C.c_uint32,C.c_int,C.c_uint32];k.OpenProcess.restype=C.c_void_p
 import os
 c.host_handle=k.OpenProcess(0x100000,False,os.getpid());return c

class InProcessTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.fixture=C.WinDLL(str(DIRECTORY/'nsp_enum_fixture.dll'),use_last_error=True)
  cls.hook=C.WinDLL(str(DIRECTORY/'nsp_enum_hook.dll'))
  for name in ('start','stop','status'):
   f=getattr(cls.hook,'nsp_hook_'+name);f.argtypes=[C.POINTER(Config)];f.restype=C.c_uint32
  for name in ('license_status','subtype','extended'):
   f=getattr(cls.fixture,name);f.argtypes=[C.c_void_p,C.c_wchar_p,C.c_wchar_p if name=='license_status' else C.c_uint32,C.c_void_p];f.restype=C.c_int32
 def tearDown(self):
  c=Config();c.magic=0x454e5331;c.size=C.sizeof(c)
  self.assertEqual(self.hook.nsp_hook_stop(C.byref(c)),0)
 def start(self,mask=0x3c):
  c=config(mask);self.assertEqual(self.hook.nsp_hook_start(C.byref(c)),0);return c
 def call(self,name,ep='target-endpoint',third=2,failed=False):
  b=(C.c_ubyte*(7*0x130))()
  r=getattr(self.fixture,name)(1 if failed else None,ep,third,b)
  self.assertEqual(r,-2147467259 if failed else 0)
  self.assertEqual(C.get_last_error(),1234);return bytes(b)
 def test_subtype_scope_failures_and_exact_fields(self):
  self.start(1<<2)
  for ep,index,failed,expected in [('TARGET-ENDPOINT',2,False,True),('other',2,False,False),('target-endpoint',3,False,False),('target-endpoint',2,True,False),('target-endpoint',31,False,False)]:
   b=self.call('subtype',ep,index,failed)
   e=bytearray([0xa5]*32)
   if expected:e[0:2]=b'\1\1';e[4:12]=bytes(8);e[16:24]=(0x24c85a5ed1c03fff).to_bytes(8,'little')
   self.assertEqual(b[:32],e)
 def test_guid_scope(self):
  self.start(1<<3)
  for ep,guid,modified in [('target-endpoint','{4C81E564-C8EF-4AD9-9F2C-9EF995533790}',True),('other','4c81e564-c8ef-4ad9-9f2c-9ef995533790',False),('target-endpoint','1459ac38-3875-49bf-bb59-0fe80f4d395d',False)]:
   # Status ABI uses GUID first, endpoint second.
   b=(C.c_ubyte*32)();self.assertEqual(self.fixture.license_status(None,guid,ep,b),0)
   e=bytearray([0xa5]*32)
   if modified:e[:4]=(1).to_bytes(4,'little');e[4:8]=(1).to_bytes(4,'little');e[8:16]=(0x24c85a5ed1c03fff).to_bytes(8,'little')
   self.assertEqual(bytes(b),e)
 def test_extended_untouched_indices_and_count(self):
  self.start((1<<2)|(1<<4))
  for count,ep,failed in [(7,'target-endpoint',False),(6,'target-endpoint',False),(7,'other',False),(7,'target-endpoint',True)]:
   b=self.call('extended',ep,count,failed)
   e=bytearray([0xa5]*(count*0x130))
   if count>=7 and ep=='target-endpoint' and not failed:
    for i in (2,4):
     p=i*0x130;e[p+0x18:p+0x1c]=(1).to_bytes(4,'little');e[p+0x20:p+0x28]=(0x24c85a5ed1c03fff).to_bytes(8,'little');e[p+0x28:p+0x2c]=bytes(4)
   self.assertEqual(b[:len(e)],e)
 def test_repeated_start_stop_and_original_bytes(self):
  for mask in (0x14,0x28,0x3c):
   self.start(mask);c=Config();c.magic=0x454e5331;c.size=C.sizeof(c)
   self.assertEqual(self.hook.nsp_hook_stop(C.byref(c)),0)
   r=json.loads(c.output);self.assertEqual(len(r['entries']),3);self.assertTrue(all(e['restored'] for e in r['entries']))
   self.assertEqual(self.call('subtype')[:32],bytes([0xa5]*32))
 def test_bad_mask_refused(self):
  c=config(1<<6);self.assertNotEqual(self.hook.nsp_hook_start(C.byref(c)),0)

class RemoteTests(unittest.TestCase):
 def setUp(self):
  self.child=subprocess.Popen([str(DIRECTORY/'nsp_enum_fixture_host.exe'),str(DIRECTORY/'nsp_enum_fixture.dll')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,creationflags=0x08000000)
  self.assertEqual(self.child.stdout.readline().strip(),'ready')
 def tearDown(self):
  self.child.stdin.write('q\n');self.child.stdin.flush();self.child.wait(timeout=5);self.child.stdin.close();self.child.stdout.close()
 def test_controller_and_expired_lease(self):
  c=Controller(self.child.pid,'target-endpoint',GROUPS['speakers'],fixture=True)
  try:
   c.load();self.assertTrue(c.renew());self.assertEqual(c.snapshot()['backend'],'minhook')
   time.sleep(5.6)
   self.assertFalse(c.renew());self.assertTrue(all(e['restored'] for e in c.cleanup()))
  finally:c.unload()
 def test_supervisor_exit_restores(self):
  code='import sys,time;from native_enum import Controller;from model import GROUPS;c=Controller(int(sys.argv[1]),"target-endpoint",GROUPS["headphones"],True);c.load();print("ready",flush=True);time.sleep(30)'
  p=subprocess.Popen([sys.executable,'-c',code,str(self.child.pid)],cwd=__import__('pathlib').Path(__file__).parent,stdout=subprocess.PIPE,text=True,creationflags=0x08000000)
  try:
   self.assertEqual(p.stdout.readline().strip(),'ready');p.terminate();p.wait(timeout=5);time.sleep(.7)
   c=Controller(self.child.pid,'target-endpoint',GROUPS['headphones'],fixture=True)
   try:
    r=c._call('status');self.assertEqual(r['state'],2);self.assertTrue(all(e['restored'] for e in r['entries']))
    c.load();self.assertTrue(c.renew());c.cleanup()
   finally:c.unload()
  finally:
   if p.poll() is None:p.kill();p.wait(timeout=5)
   p.stdout.close()

if __name__=='__main__':unittest.main()
