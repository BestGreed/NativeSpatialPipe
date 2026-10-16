"""Owned-process contract tests. Python callback is a test spy, never production."""
import ctypes as C, importlib.util, pathlib, threading, time, json, types, unittest
import sys
MMCSS='--mmcss' in sys.argv
if MMCSS:sys.argv.remove('--mmcss')
DLL=None
if '--receiver-dll' in sys.argv:
    i=sys.argv.index('--receiver-dll');DLL=sys.argv.pop(i+1);sys.argv.pop(i)
HERE=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('receiver',HERE/'Native-Receiver.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
Submit=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint64,C.c_uint32,C.c_void_p,C.c_size_t)

class Tests(unittest.TestCase):
    def setUp(self):
        self.actual=[];self.enter=threading.Event();self.release=threading.Event();self.release.set()
        def submit(p,seq,count,data,size):
            self.enter.set();self.release.wait(2)
            self.actual.append((seq,count,C.string_at(data,size)));return 0
        self.spy=Submit(submit)
        pipeline=types.SimpleNamespace(handle=1,frames=480,lib=types.SimpleNamespace(dll=types.SimpleNamespace(nsp_pipeline_submit=self.spy)))
        self.rx=module.Receiver(pipeline,mmcss=MMCSS,receiver_dll_path=DLL)
        def cleanup_receiver():
            self.release.set()
            if self.rx.handle:self.rx.finish()
            else:self.rx.transport.finish()
        self.addCleanup(cleanup_receiver)
        self.rx.start()
        self.lib=self.rx.transport.lib
        self.lib.nsp_link_send.argtypes=[C.c_void_p,C.c_char_p,C.c_uint32,C.c_char_p,C.c_uint32];self.lib.nsp_link_send.restype=C.c_int
        self.writer=self.lib.nsp_link_open(self.rx.config['name'],self.rx.config['event'],0)
        self.assertTrue(self.writer)
    def tearDown(self):
        self.release.set();self.lib.nsp_link_close(self.writer);self.rx.finish()
    def send(self,seq,**change):
        now=time.perf_counter_ns()
        d=dict(block='0x123',sequence=seq,count=1,framesUniform=480,captureQpcNs=now,copyDoneQpcNs=now,sendQpcNs=now)
        d.update(change);header=json.dumps(d).encode();data=bytes([seq%251])*1952
        return self.lib.nsp_link_send(self.writer,header,len(header),data,len(data))
    def until(self,predicate):
        deadline=time.monotonic()+3
        while not predicate() and time.monotonic()<deadline:time.sleep(.001)
        self.assertTrue(predicate())
    def test_slot_held_until_submit_returns(self):
        self.release.clear();self.assertEqual(self.send(0),0);self.assertTrue(self.enter.wait(2))
        for seq in range(1,16):self.assertEqual(self.send(seq),0)
        self.assertEqual(self.send(16),-2)
        self.release.set();self.until(lambda:self.rx.received==16)
        for seq in range(16,48):
            self.assertEqual(self.send(seq),0);self.until(lambda:self.rx.received==seq+1)
        self.assertEqual(self.actual,[(s,1,bytes([s])*1952) for s in range(48)])
    def test_bad_length_fails_closed(self):
        self.assertEqual(self.send(0,count=2),0);self.until(lambda:bool(self.rx.errors))
        self.assertEqual(self.actual,[]);self.assertFalse(self.rx.finish()['passed'])
    def test_duplicate_sequence_fails_closed(self):
        self.assertEqual(self.send(0),0);self.until(lambda:self.rx.received==1)
        self.assertEqual(self.send(0),0);self.until(lambda:bool(self.rx.errors))
        self.assertEqual(len(self.actual),1)
    def test_second_stream_fails_closed(self):
        self.assertEqual(self.send(0),0);self.until(lambda:self.rx.received==1)
        self.assertEqual(self.send(1,block='0x456'),0);self.until(lambda:bool(self.rx.errors))
        self.assertEqual(len(self.actual),1)
    def test_idle_stop_and_idempotent_finish(self):
        result=self.rx.finish();self.assertTrue(result['passed'],result['errors']);self.assertTrue(result['threadStopped']);self.assertEqual(result['received'],0)
        self.assertEqual(self.rx.finish(),result)

class StartupTests(unittest.TestCase):
    def setUp(self):
        self.spy=Submit(lambda *args:0)
        self.pipeline=types.SimpleNamespace(handle=1,frames=480,lib=types.SimpleNamespace(dll=types.SimpleNamespace(nsp_pipeline_submit=self.spy)))
        self.rx=module.Receiver(self.pipeline,mmcss=MMCSS,receiver_dll_path=DLL)
        self.addCleanup(self.cleanup)
        if self.rx.start_error is None:self.skipTest('Legacy DLL has no startup diagnostic export')
    def cleanup(self):
        if self.rx.handle:self.rx.finish()
        else:self.rx.transport.finish()
    def test_invalid_arguments_and_success_reset(self):
        self.pipeline.frames=0
        with self.assertRaisesRegex(RuntimeError,'arguments .*stage=1, Win32=0'):self.rx.start()
        self.pipeline.frames=480;self.rx.start()
        self.assertEqual(self.rx.start_error(),0)
        self.assertTrue(self.rx.finish()['passed'])
    def test_missing_mapping(self):
        self.rx.config['name']+='-missing'
        with self.assertRaisesRegex(RuntimeError,'OpenFileMappingW .*stage=2, Win32=2'):self.rx.start()
    def test_invalid_ring_header(self):
        import mmap, uuid
        name='Local\\NSP-test-'+uuid.uuid4().hex
        with mmap.mmap(-1,42156240,tagname=name) as mapping:
            mapping[:16]=bytes(16);self.rx.config['name']=name
            with self.assertRaisesRegex(RuntimeError,'ring header .*stage=5, Win32=0'):self.rx.start()
    def test_view_too_small(self):
        import mmap, uuid
        name='Local\\NSP-test-'+uuid.uuid4().hex
        with mmap.mmap(-1,4096,tagname=name):
            self.rx.config['name']=name
            with self.assertRaisesRegex(RuntimeError,'MapViewOfFile .*stage=4, Win32=[1-9]'):self.rx.start()
    def test_missing_event_and_thread_local_error(self):
        self.rx.config['event']+='-missing'
        with self.assertRaisesRegex(RuntimeError,'OpenEventW .*stage=3, Win32=2'):self.rx.start()
        other=[]
        def check():other.append(self.rx.start_error())
        thread=threading.Thread(target=check);thread.start();thread.join()
        self.assertEqual(other,[0]);self.assertEqual(self.rx.start_error(),(3<<32)|2)

if __name__=='__main__':unittest.main(verbosity=2)
