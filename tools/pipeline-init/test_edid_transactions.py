"""Exercise production EDID transactions against an isolated registry double.

No device registry writes, display reloads, endpoint changes or Frida attach.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import edid


def checksum(b):
    b=bytearray(b);b[127]=(-sum(b[:127]))&255;return bytes(b)


def fixture():
    base=bytearray(128);base[:8]=bytes.fromhex('00ffffffffffff00');base[126]=1
    cta=bytearray(128)
    cta[:4]=bytes([2,3,12,64]);cta[4:12]=bytes.fromhex('2309070743010203')
    cta[12:30]=bytes(range(1,19))
    return checksum(base)+checksum(cta)


class Key:
    def __init__(self,registry,path):self.registry,self.path=registry,path
    def __enter__(self):return self
    def __exit__(self,*args):pass


class Registry:
    HKEY_LOCAL_MACHINE='HKLM'
    REG_BINARY=3
    REG_NONE=0
    REG_SZ=1
    KEY_QUERY_VALUE=1
    KEY_SET_VALUE=2
    def __init__(self,subkey,data,overrides):
        self.values={subkey:{'EDID':(data,self.REG_BINARY)},subkey+'\\EDID_OVERRIDE':copy.deepcopy(overrides)}
        self.events=[];self.failure=None;self.bad_read=False
    def OpenKey(self,root,path,*args):
        if isinstance(root,Key):path=root.path+'\\'+path
        if path not in self.values:raise FileNotFoundError(path)
        return Key(self,path)
    def CreateKeyEx(self,root,path,*args):
        self.events.append(('create',path));self.values.setdefault(path,{})
        return Key(self,path)
    def QueryInfoKey(self,key):return 0,len(self.values[key.path]),0
    def QueryValueEx(self,key,name):
        if name not in self.values[key.path]:raise FileNotFoundError(name)
        if self.bad_read and any(e[0]=='set' for e in self.events):
            self.bad_read=False;return b'bad-readback',self.REG_BINARY
        return copy.deepcopy(self.values[key.path][name])
    def EnumValue(self,key,index):
        name,(value,kind)=list(self.values[key.path].items())[index]
        return name,copy.deepcopy(value),kind
    def fail(self,operation):
        if self.failure==operation:self.failure=None;raise OSError('Injected '+operation+' failure')
    def SetValueEx(self,key,name,reserved,kind,value):
        self.fail('set');self.events.append(('set',name));self.values[key.path][name]=(value,kind)
    def DeleteValue(self,key,name):
        self.fail('delete');self.events.append(('delete',name));del self.values[key.path][name]
    def FlushKey(self,key):self.fail('flush');self.events.append(('flush',key.path))


class Transactions(unittest.TestCase):
    monitor=r'TEST0001\instance';endpoint='test-gpu-endpoint'
    def setUp(self):
        test_root=edid.ROOT/'build/edid-regression-20261023/fixtures'
        test_root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=test_root);self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.subkey='SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\'+self.monitor+'\\Device Parameters'
        self.original=fixture();self.overrides={'CRU_Name':(b'original-name',3),'7':(bytes(range(128)),3),'note':('preserve',1)}
        self.reg=Registry(self.subkey,self.original,self.overrides)
        self.addCleanup(patch.stopall)
        patch.dict('sys.modules',winreg=self.reg).start()
        patch.object(edid,'display_snapshot',return_value=[dict(active_in_desktop=True,registry_identity=self.monitor.lower())]).start()
        patch.object(edid,'run',return_value=json.dumps(dict(endpoints=[dict(id=self.endpoint,state=1,name='NVIDIA fixture')]))).start()
    @property
    def values(self):return self.reg.values[self.subkey+'\\EDID_OVERRIDE']
    def plan(self):
        self.path=self.folder/'plan'/'edid-plan.json'
        return edid.plan(self.endpoint,self.monitor,self.path.parent)
    def state(self):return json.loads(self.path.read_text('utf-8'))['state']
    def assert_unrelated(self):
        for name,value in self.overrides.items():self.assertEqual(self.values[name],value)
    def test_plan_is_read_only_and_preserves_timing(self):
        before=copy.deepcopy(self.reg.values);r=self.plan()
        self.assertEqual(before,self.reg.values);self.assertEqual(self.reg.events,[])
        self.assertEqual((self.path.parent/'before.bin').read_bytes(),self.original)
        after=(self.path.parent/'after.bin').read_bytes();self.assertTrue(edid.parse_edid(after)['structure_ok'])
        self.assertEqual(after[:128],self.original[:128]);self.assertEqual(after[128+after[130]:128+after[130]+18],self.original[140:158])
        self.assertFalse(r['oldBlockHadOverride'])
    def test_apply_and_restore_without_previous_override(self):
        r=self.plan();edid.mutate(self.path);self.assertEqual(self.values['1'],(bytes.fromhex(r['newBlock']),3))
        edid.mutate(self.path,True);self.assertNotIn('1',self.values);self.assertEqual(self.state(),'restored');self.assert_unrelated()
    def test_apply_restore_preserves_existing_override_exactly(self):
        self.values['1']=(self.original[128:],3);before=copy.deepcopy(self.reg.values)
        self.plan();edid.mutate(self.path);edid.mutate(self.path,True);self.assertEqual(self.reg.values,before)
    def test_no_op_plan_does_not_write_existing_profile(self):
        self.values['1']=(edid.patch_audio(self.original[128:]),3);before=copy.deepcopy(self.reg.values)
        self.plan();edid.mutate(self.path);edid.mutate(self.path,True)
        self.assertFalse(any(e[0] in ('set','delete') for e in self.reg.events));self.assertEqual(before,self.reg.values)
    def test_apply_can_resume_after_interrupted_set(self):
        self.plan();self.reg.failure='set'
        with self.assertRaises(OSError):edid.mutate(self.path)
        self.assertEqual(self.state(),'applying');edid.mutate(self.path);edid.mutate(self.path,True);self.assert_unrelated()
    def test_apply_can_resume_after_flush_failure(self):
        self.plan();self.reg.failure='flush'
        with self.assertRaises(OSError):edid.mutate(self.path)
        self.assertEqual(self.state(),'applying');edid.mutate(self.path);self.assertEqual(self.state(),'applied')
        edid.mutate(self.path,True);self.assertNotIn('1',self.values)
    def test_restore_can_recover_apply_intent_before_any_registry_write(self):
        self.plan();self.reg.failure='set'
        with self.assertRaises(OSError):edid.mutate(self.path)
        edid.mutate(self.path,True);self.assertEqual(self.state(),'restored');self.assertNotIn('1',self.values)
    def test_restore_resumes_after_delete_failure(self):
        self.plan();edid.mutate(self.path);self.reg.failure='delete'
        with self.assertRaises(OSError):edid.mutate(self.path,True)
        self.assertEqual(self.state(),'restoring');edid.mutate(self.path,True);self.assertNotIn('1',self.values)
    def test_restore_resumes_after_flush_failure(self):
        self.plan();edid.mutate(self.path);self.reg.failure='flush'
        with self.assertRaises(OSError):edid.mutate(self.path,True)
        self.assertEqual(self.state(),'restoring');edid.mutate(self.path,True);self.assertEqual(self.state(),'restored')
    def test_write_readback_mismatch_retains_recoverable_intent(self):
        self.plan();self.reg.bad_read=True
        with self.assertRaisesRegex(RuntimeError,'readback'):edid.mutate(self.path)
        self.assertEqual(self.state(),'applying');edid.mutate(self.path,True);self.assertNotIn('1',self.values)
    def test_journal_failure_before_write_does_not_mutate(self):
        self.plan();before=copy.deepcopy(self.reg.values)
        with patch.object(edid,'atomic',side_effect=OSError('journal')):
            with self.assertRaises(OSError):edid.mutate(self.path)
        self.assertEqual(before,self.reg.values);self.assertEqual(self.state(),'planned')
    def test_final_journal_failure_resumes_without_second_write(self):
        self.plan();atomic=edid.atomic;calls=[]
        def fail_final(path,value):
            calls.append(value['state'])
            if value['state']=='applied':raise OSError('final journal')
            atomic(path,value)
        with patch.object(edid,'atomic',side_effect=fail_final):
            with self.assertRaises(OSError):edid.mutate(self.path)
        writes=len([e for e in self.reg.events if e[0]=='set']);edid.mutate(self.path)
        self.assertEqual(writes,len([e for e in self.reg.events if e[0]=='set']));self.assertEqual(self.state(),'applied')
    def test_external_edit_before_apply_is_preserved(self):
        self.plan();self.values['1']=(b'external edit',3);before=copy.deepcopy(self.reg.values)
        with self.assertRaisesRegex(ValueError,'Override changed'):edid.mutate(self.path)
        self.assertEqual(before,self.reg.values)
    def test_external_edit_before_restore_is_preserved(self):
        self.plan();edid.mutate(self.path);self.values['1']=(b'external edit',3);before=copy.deepcopy(self.reg.values)
        with self.assertRaisesRegex(ValueError,'Override changed'):edid.mutate(self.path,True)
        self.assertEqual(before,self.reg.values)
    def test_changed_cached_device_edid_is_rejected(self):
        self.plan();self.reg.values[self.subkey]['EDID']=(bytes(256),3);before=copy.deepcopy(self.reg.values)
        with self.assertRaisesRegex(ValueError,'Monitor EDID changed'):edid.mutate(self.path)
        self.assertEqual(before,self.reg.values);self.assertEqual(self.reg.events,[])
    def test_tampered_registry_path_rejected(self):
        self.plan();r=json.loads(self.path.read_text());r['subkey']='SYSTEM\\other';edid.atomic(self.path,r)
        with self.assertRaisesRegex(ValueError,'registry path'):edid.mutate(self.path)
        self.assertEqual(self.reg.events,[])
    def test_unrelated_edits_during_transaction_are_preserved(self):
        self.plan();edid.mutate(self.path);self.values['note']=('later metadata',1);edid.mutate(self.path,True)
        self.assertEqual(self.values['note'],('later metadata',1));self.assertNotIn('1',self.values)
    def test_restore_is_idempotent(self):
        self.plan();edid.mutate(self.path);edid.mutate(self.path,True);before=copy.deepcopy(self.reg.values)
        edid.mutate(self.path,True);self.assertEqual(before,self.reg.values)
    def test_apply_is_idempotent(self):
        self.plan();edid.mutate(self.path);before=copy.deepcopy(self.reg.values);writes=list(self.reg.events)
        edid.mutate(self.path);self.assertEqual(before,self.reg.values)
        self.assertEqual(len([e for e in writes if e[0]=='set']),len([e for e in self.reg.events if e[0]=='set']))
    def test_completed_restore_cannot_consume_a_new_matching_override(self):
        r=self.plan();edid.mutate(self.path);edid.mutate(self.path,True)
        self.values['1']=(bytes.fromhex(r['newBlock']),3);before=copy.deepcopy(self.reg.values)
        with self.assertRaisesRegex(ValueError,'completed transaction'):edid.mutate(self.path,True)
        self.assertEqual(before,self.reg.values)
    def test_committed_apply_does_not_reapply_after_external_removal(self):
        self.plan();edid.mutate(self.path);del self.values['1'];before=copy.deepcopy(self.reg.values)
        with self.assertRaisesRegex(ValueError,'completed transaction'):edid.mutate(self.path)
        self.assertEqual(before,self.reg.values)
    def test_wrong_registry_type_is_rejected_during_plan(self):
        self.values['1']=(self.original[128:],0)
        with self.assertRaisesRegex(ValueError,'type'):self.plan()
        self.assertFalse((self.folder/'plan').exists())
    def test_cached_registry_type_is_checked_even_with_same_bytes(self):
        self.plan();self.reg.values[self.subkey]['EDID']=(self.original,0)
        with self.assertRaisesRegex(ValueError,'type'):edid.mutate(self.path)
        self.assertEqual(self.reg.events,[])
    def test_applied_bytes_with_wrong_registry_type_not_treated_as_committed(self):
        r=self.plan();self.values['1']=(bytes.fromhex(r['newBlock']),0)
        with self.assertRaisesRegex(ValueError,'type'):edid.mutate(self.path)
        self.assertEqual(self.state(),'planned')


if __name__=='__main__':unittest.main()
