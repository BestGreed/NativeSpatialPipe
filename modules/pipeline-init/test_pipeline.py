import unittest,struct,subprocess,sys
from model import *
from edid import patch_audio
def cta(audio=b'\x09\x07\x07',space=True):
 b=bytearray(128);b[:4]=bytes([2,3,5+len(audio),64]);b[4]=32|len(audio);b[5:5+len(audio)]=audio
 # Preserve an actual timing-like opaque payload in the DTD area.
 b[b[2]:b[2]+18]=bytes(range(1,19))
 if not space:b[126]=1
 b[127]=(-sum(b[:127]))&255;return bytes(b)
class PipelineTests(unittest.TestCase):
 def test_dead_owner_with_retained_process_handle_is_not_live(self):
  from windows import creation
  p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=0x08000000)
  try:
   self.assertIsNotNone(creation(p.pid))
   p.terminate();p.wait(timeout=5)
   self.assertIsNone(creation(p.pid))
  finally:
   if p.poll() is None:p.kill();p.wait(timeout=5)
 def test_tag_rejects_stale_wrong_endpoint_pid_and_nonpersistent(self):
  t={'state':2,'flags':3,'created':123,'endpoint':'device','heartbeat':1000}
  self.assertTrue(eligible(t,'DEVICE',2000,123,True))
  self.assertFalse(eligible(t,'other',2000,123))
  self.assertFalse(eligible(t,'device',5001,123))
  self.assertFalse(eligible(t,'device',900,123))
  self.assertFalse(eligible(t,'device',2000,124))
  self.assertFalse(eligible({**t,'flags':1},'device',2000,123,True))
  self.assertFalse(eligible({**t,'state':1},'device',2000,123))
 def test_torn_or_wrong_version_tag_is_not_live(self):
  b=bytearray(MAP_SIZE);HEADER.pack_into(b,0,2,MAGIC,1,2,5,3,1000,123,6,mask_for(['DTSXUltra']))
  b[48:60]='device'.encode('utf-16-le')
  self.assertEqual(decode_tag(b)['endpoint'],'device')
  struct.pack_into('<I',b,0,3);self.assertIsNone(decode_tag(b))
  struct.pack_into('<I',b,0,4);struct.pack_into('<I',b,8,9);self.assertIsNone(decode_tag(b))
 def test_edid_preserves_non_audio_payload_and_checksum(self):
  original=cta();after=patch_audio(original)
  self.assertEqual(sum(after)%256,0)
  self.assertEqual(after[after[2]:after[2]+18],original[original[2]:original[2]+18])
  self.assertIn(bytes.fromhex('5f7e03'),after)
  self.assertIn(bytes.fromhex('3d1fc0'),after)
  self.assertIn(bytes.fromhex('677f03'),after)
  self.assertEqual(patch_audio(after),after)
 def test_edid_profiles_are_scoped(self):
  dts=patch_audio(cta(),'dtsx');atmos=patch_audio(cta(),'atmos')
  self.assertIn(bytes.fromhex('5f7e03'),dts);self.assertNotIn(bytes.fromhex('677f03'),dts)
  self.assertIn(bytes.fromhex('677f03'),atmos);self.assertNotIn(bytes.fromhex('5f7e03'),atmos)
 def test_edid_refuses_overflow_bad_checksum_and_unknown_layout(self):
  with self.assertRaises(ValueError):patch_audio(cta(space=False))
  b=bytearray(cta());b[127]^=1
  with self.assertRaises(ValueError):patch_audio(b)
  b=bytearray(cta());b[0]=0x70
  with self.assertRaises(ValueError):patch_audio(b)
 def test_requested_groups_do_not_include_other_modes(self):
  hp=selected_formats(['headphones']);self.assertEqual(set(hp),{'DolbyAtmosForHeadphones','DTSHeadphoneX'})
  self.assertFalse(mask_for(hp)&mask_for(GROUPS['speakers']))
if __name__=='__main__':unittest.main()
