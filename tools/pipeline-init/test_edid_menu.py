"""Manual EDID orchestration regression. Registry/device/driver calls mocked."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
import uuid
import edid_menu as m

class MenuTests(unittest.TestCase):
 endpoint='{0.0.0.00000000}.{441f7c79-46cd-4823-9304-e2ac70e409f5}'
 binding=dict(endpoint=endpoint,container='container',monitor='TEST\\instance',adapter='PCI\\TEST\\GPU')
 def setUp(self):
  root=m.ROOT/'build/edid-regression-20261023/fixtures';root.mkdir(parents=True,exist_ok=True)
  temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(temp.cleanup)
  self.output=Path(temp.name)/'operation'
 def execute(self,**changes):
  values=dict(check_default=Mock(),support=Mock(side_effect=[dict(atmos=False,dtsx=False),dict(atmos=True,dtsx=True)]),endpoint_binding=Mock(return_value=self.binding),ps=Mock(return_value='reloaded'))
  values.update(changes)
  with contextlib.ExitStack() as stack:
   stack.enter_context(patch.object(m.ctypes.windll.shell32,'IsUserAnAdmin',return_value=1))
   for key,value in values.items():stack.enter_context(patch.object(m,key,value))
   plan=stack.enter_context(patch.object(m.edid,'plan',return_value=dict(oldBlock='a',newBlock='b')))
   mutate=stack.enter_context(patch.object(m.edid,'mutate'))
   result=m.enable(self.endpoint,self.output)
  self.assertEqual(json.loads((self.output/'result.json').read_text(encoding='utf8')),result)
  return result,values,plan,mutate
 def test_enable_writes_then_reloads_exact_gpu_then_verifies(self):
  result,values,plan,mutate=self.execute()
  self.assertTrue(result['verified'] and result['overrideApplied'] and result['reloaded'])
  plan.assert_called_once_with(self.endpoint,self.binding['monitor'],self.output/'transaction',profile='both')
  mutate.assert_called_once();self.assertEqual(values['check_default'].call_count,2)
  self.assertEqual(values['endpoint_binding'].call_count,2)
  self.assertEqual(values['ps'].call_args.args[1:],('-InstanceId',self.binding['adapter']))
 def test_already_supported_is_noop(self):
  result,values,plan,mutate=self.execute(support=Mock(return_value=dict(atmos=True,dtsx=False)))
  self.assertTrue(result['verified'] and result['alreadySupported'])
  self.assertFalse(result['overrideApplied']);plan.assert_not_called();mutate.assert_not_called()
  values['ps'].assert_not_called();values['endpoint_binding'].assert_not_called()
 def test_changed_default_never_writes(self):
  result,values,plan,mutate=self.execute(check_default=Mock(side_effect=ValueError('changed')))
  self.assertFalse(result['verified']);plan.assert_not_called();mutate.assert_not_called();values['ps'].assert_not_called()
 def test_binding_failure_never_writes(self):
  result,values,plan,mutate=self.execute(endpoint_binding=Mock(side_effect=ValueError('ambiguous')))
  self.assertFalse(result['verified']);plan.assert_not_called();mutate.assert_not_called();values['ps'].assert_not_called()
 def test_binding_changed_after_plan_never_writes(self):
  result,values,plan,mutate=self.execute(endpoint_binding=Mock(side_effect=[self.binding,dict(self.binding,monitor='other')]))
  self.assertFalse(result['verified']);mutate.assert_not_called();values['ps'].assert_not_called()
 def test_reload_failure_retains_transaction_without_claiming_success(self):
  result,values,plan,mutate=self.execute(ps=Mock(side_effect=RuntimeError('reload failed')))
  self.assertFalse(result['verified']);self.assertTrue(result['overrideApplied']);self.assertFalse(result['reloaded'])
  self.assertIn('plan',result)
 def test_unchanged_edid_is_not_reloaded(self):
  with patch.object(m.ctypes.windll.shell32,'IsUserAnAdmin',return_value=1),patch.object(m,'check_default'),patch.object(m,'support',return_value=dict(a=False)),patch.object(m,'endpoint_binding',return_value=self.binding),patch.object(m.edid,'plan',return_value=dict(oldBlock='a',newBlock='a')),patch.object(m.edid,'mutate') as mutate,patch.object(m,'ps') as reload:
   result=m.enable(self.endpoint,self.output)
  self.assertFalse(result['verified']);mutate.assert_not_called();reload.assert_not_called()
 def test_transport_filter(self):
  self.assertTrue(m.eligible(9,'HDAUDIO',r'{1}.HDAUDIO\device'))
  for bus in ('USB','ROOT','SWD'):self.assertFalse(m.eligible(9,bus,r'{1}.HDAUDIO\device'))
  self.assertFalse(m.eligible(1,'HDAUDIO',r'{1}.HDAUDIO\device'))
 def test_verification_timeout_is_failure_even_after_write(self):
  with patch.object(m.time,'monotonic',side_effect=[0,46]):
   result,values,plan,mutate=self.execute(support=Mock(return_value=dict(atmos=False,dtsx=False)))
  self.assertFalse(result['verified']);self.assertTrue(result['overrideApplied'] and result['reloaded'])
 def test_container_binding_requires_unique_active_monitor(self):
  sys.path.insert(0,str(m.ROOT/'tools/edid-probe'));import edid_probe
  container=uuid.UUID('09390aef-9b55-5bf2-bff3-a83a03104203')
  props={m.FORM:(9,m.w.REG_DWORD),m.ENUMERATOR:('HDAUDIO',m.w.REG_SZ),m.INSTANCE:(r'{1}.HDAUDIO\device',m.w.REG_SZ),m.CONTAINER:(bytes.fromhex('4800000001000000')+container.bytes_le,m.w.REG_BINARY)}
  @contextlib.contextmanager
  def key(root,path):yield props if path.endswith('Properties') else {'ContainerID':(str(container),m.w.REG_SZ)}
  monitors=[dict(active_in_desktop=True,registry_identity='TEST\\one')]
  with patch.object(m.w,'OpenKey',side_effect=key),patch.object(m.w,'QueryValueEx',side_effect=lambda k,n:k[n]),patch.object(edid_probe,'display_snapshot',return_value=monitors),patch.object(m,'ps',return_value=json.dumps(dict(monitor='TEST\\one',adapter='PCI\\GPU'))) as query:
   self.assertEqual(m.endpoint_binding(self.endpoint)['monitor'],'TEST\\one')
   monitors.append(dict(active_in_desktop=True,registry_identity='TEST\\two'))
   with self.assertRaisesRegex(ValueError,'exactly one'):m.endpoint_binding(self.endpoint)
   self.assertEqual(query.call_count,1)

if __name__=='__main__':unittest.main()
