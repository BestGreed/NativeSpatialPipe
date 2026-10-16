import ctypes as C,importlib.util,pathlib,unittest
s=importlib.util.spec_from_file_location('transport',pathlib.Path(__file__).with_name('Native-Send.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_bounds_saturation_wrap_and_ownership(self):
  r=m.Receiver();lib=r.lib;lib.nsp_link_send.argtypes=[C.c_void_p,C.c_void_p,C.c_uint32,C.c_void_p,C.c_uint32];lib.nsp_link_send.restype=C.c_int
  producer=lib.nsp_link_open(r.config['name'],r.config['event'],0);self.assertTrue(producer,str(lib.nsp_link_open_error()))
  header=C.create_string_buffer(m.HEADER);data=C.create_string_buffer(m.PAYLOAD);sizes=(C.c_uint32*2)()
  try:
   self.assertEqual(lib.nsp_link_send(producer,b'{}',m.HEADER+1,b'x',1),-1)
   self.assertEqual(lib.nsp_link_send(producer,b'{}',2,b'x',m.PAYLOAD+1),-1)
   for turn in range(3):
    for i in range(16):
     raw=bytes([turn*16+i])*17;self.assertEqual(lib.nsp_link_send(producer,b'{}',2,raw,len(raw)),0)
    self.assertEqual(lib.nsp_link_send(producer,b'{}',2,b'x',1),-2)
    for i in range(16):
     self.assertEqual(lib.nsp_link_read(r.handle,header,m.HEADER,data,m.PAYLOAD,sizes),1)
     self.assertEqual(C.string_at(data,sizes[1]),bytes([turn*16+i])*17)
    self.assertEqual(lib.nsp_link_read(r.handle,header,m.HEADER,data,m.PAYLOAD,sizes),0)
   raw=bytes(range(256))*(m.PAYLOAD//256)+bytes(m.PAYLOAD%256)
   self.assertEqual(lib.nsp_link_send(producer,b'{}',2,raw,len(raw)),0)
   self.assertEqual(lib.nsp_link_read(r.handle,header,m.HEADER,data,m.PAYLOAD,sizes),1);self.assertEqual(C.string_at(data,sizes[1]),raw)
  finally:lib.nsp_link_close(producer);self.assertTrue(r.finish()['threadStopped'])
 def test_idle_stop(self):
  r=m.Receiver();r.start(lambda *_:self.fail('No packets expected'));self.assertTrue(r.finish()['passed'])
if __name__=='__main__':unittest.main()
