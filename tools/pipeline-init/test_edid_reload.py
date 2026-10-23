"""Reload orchestration contract; subprocess/driver calls are mocked."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pipeline
from windows import ROOT

class ReloadTests(unittest.TestCase):
    adapter=r'PCI\VEN_10DE&DEV_TEST\INSTANCE'
    def setUp(self):
        root=ROOT/'build/edid-regression-20261023/fixtures';root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'plan.json'
    def invoke(self,state='applied',error=None):
        self.path.write_text(json.dumps(dict(state=state,reloadRequired=True)),encoding='utf-8')
        out=io.StringIO()
        with patch.object(sys,'argv',['pipeline.py','edid-reload','--plan',str(self.path),'--adapter-instance',self.adapter]),patch.object(pipeline,'ps',return_value='driver-result',side_effect=error) as ps,contextlib.redirect_stdout(out):
            code=pipeline.main()
        return code,json.loads(out.getvalue()),ps
    def test_committed_apply_forwards_only_exact_selected_adapter(self):
        code,result,ps=self.invoke();self.assertEqual(code,0)
        args,kwargs=ps.call_args
        self.assertEqual(Path(args[0]).name,'Reload-Display.ps1');self.assertEqual(args[1:],('-InstanceId',self.adapter));self.assertEqual(kwargs,dict(timeout=45))
        self.assertTrue(result['reloaded']);self.assertTrue(result['verifyNativeSupportNext'])
    def test_restored_override_also_requires_reload(self):
        _,result,ps=self.invoke('restored');ps.assert_called_once();self.assertTrue(result['verifyNativeSupportNext'])
    def test_reload_failure_is_not_returned_as_success(self):
        with self.assertRaisesRegex(RuntimeError,'driver failed'):self.invoke(error=RuntimeError('driver failed'))
        self.assertTrue(json.loads(self.path.read_text('utf-8'))['reloadRequired'])
    def test_uncommitted_transactions_never_restart_driver(self):
        for state in ['planned','applying','restoring','faulted']:
            self.path.write_text(json.dumps(dict(state=state)),encoding='utf-8')
            with self.subTest(state=state),patch.object(sys,'argv',['pipeline.py','edid-reload','--plan',str(self.path),'--adapter-instance',self.adapter]),patch.object(pipeline,'ps') as ps:
                with self.assertRaisesRegex(ValueError,'not committed'):pipeline.main()
                ps.assert_not_called()

if __name__=='__main__':unittest.main()
