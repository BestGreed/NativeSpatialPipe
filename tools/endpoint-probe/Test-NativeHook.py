"""Native-only owned target tests. This test does not import Frida."""
import importlib.util,json,pathlib,struct,subprocess,sys,time,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2];HERE=pathlib.Path(__file__).parent
def load(name,file):
 s=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
hook=load('hook','Native-Hook.py');transport=load('send','Native-Send.py')
SAMPLE=ROOT/'build/native-hook/test-fixture.bin'
props=bytearray(140*32)
for i in range(140):struct.pack_into('<II',props,i*32,i,480)
SAMPLE.write_bytes(props+bytes(140*1920))
class Tests(unittest.TestCase):
 def run_source(self,fixture,packets,mode='normal'):
  child=subprocess.Popen([str(ROOT/'build/native-hook/nsp_fixture_host.exe'),str(ROOT/'build'/fixture/'nsp_fixture.dll'),str(SAMPLE),str(packets)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
  receiver=control=None
  try:
   self.assertEqual(child.stdout.readline().strip(),'ready');received=[]
   receiver=transport.Receiver();receiver.start(lambda msg,data:received.append(data))
   control=hook.Controller(child.pid,receiver.config,duration=.1 if mode=='watchdog' else 10,fixture=True);control.load()
   self.assertNotIn('frida',sys.modules)
   self.assertFalse(any(any(x in n.lower() for x in ['frida','python','quickjs','v8']) for n in control.during))
   child.stdin.write('go\n');child.stdin.flush()
   if mode=='pending':
    deadline=time.monotonic()+2
    while not control._call('status')['activeOriginalCalls'] and time.monotonic()<deadline:time.sleep(.001)
    r=control.stop();self.assertGreater(r['activeOriginalCallsAtStop'],0);self.assertEqual(r['copies'],0)
   result=json.loads(child.stdout.readline());self.assertGreaterEqual(result['instrumentedCallTotalUs'],0)
   r=control.stop();self.assertEqual(r,control.stop());control.unload()
   self.assertIsNone(child.poll());self.assertTrue(r['hookBytesRestored']);self.assertTrue(r['captureLibraryReleased']);self.assertTrue(r['transportLibraryReleased']);self.assertFalse(r['errors']);self.assertEqual(r['queueDropped'],0)
   after=[n.lower() for n in r['moduleAudit']['after']];self.assertIn('nsp_native_hook.dll',after);self.assertNotIn('nsp_native_capture.dll',after);self.assertNotIn('nsp_native_send.dll',after)
   end=time.monotonic()+2
   while len(received)<r['copies'] and time.monotonic()<end:time.sleep(.001)
   self.assertEqual(len(received),r['copies']);self.assertTrue(all(data==SAMPLE.read_bytes() for data in received))
   if mode=='watchdog':self.assertGreater(r['copies'],0);self.assertLess(r['copies'],packets)
   elif mode=='normal':self.assertEqual(r['copies'],packets)
   if mode=='restart':
    control=hook.Controller(child.pid,receiver.config,duration=10,fixture=True);control.load();child.stdin.write('go\n');child.stdin.flush();self.assertGreaterEqual(json.loads(child.stdout.readline())['instrumentedCallTotalUs'],0)
    again=control.stop();control.unload();self.assertEqual(again['copies'],packets);self.assertFalse(again['errors']);self.assertTrue(again['hookBytesRestored'])
  finally:
   if control and control.started and control.result is None:control.stop();control.unload()
   if receiver:receiver.finish()
   child.terminate();child.wait(5);child.stdin.close();child.stdout.close();child.stderr.close()
 def test_three_arguments_return_and_last_error(self):self.run_source('capture-fixture-abi',30)
 def test_pending_original_can_return_after_stop(self):self.run_source('capture-fixture-delay',1,'pending')
 def test_watchdog_stops_without_host_request(self):self.run_source('capture-fixture',40,'watchdog')
 def test_restart_reuses_resident_trampoline(self):self.run_source('capture-fixture',20,'restart')
if __name__=='__main__':unittest.main()
