"""Run the full four-reload orchestration with in-memory device/registry doubles."""
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import edid
import test_edid_transactions as transactions

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=transactions.Transactions(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;baseline=f.original[128:];current=edid.patch_audio(baseline,'both');candidate=edid.patch_audio(baseline,'dtsx')
        f.values['1']=(current,3)
        self.preview=dict(endpoint=f.endpoint,monitor=f.monitor,changedBlock=1,currentBlock=current.hex(),baselineBlock=baseline.hex(),candidateBlock=candidate.hex())
        spec=importlib.util.spec_from_file_location('edid_runner',Path(__file__).with_name('Run-EdidRegression.py'));self.runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.runner)
        self.active=self.runner.ZERO;self.commands=[];self.fail_reload=False
        patch('ctypes.windll.shell32.IsUserAnAdmin',return_value=True).start()
        patch.object(self.runner,'run',return_value='[]').start()
        patch.object(self.runner,'ps',side_effect=self.ps).start()
        patch.object(self.runner.subprocess,'run',side_effect=self.process).start()
        import manager
        patch.object(manager,'status',return_value=dict(live=False)).start()
    def ps(self,script,*args,**kwargs):
        if Path(script).name=='Get-DefaultRenderEndpoint.ps1':return json.dumps(dict(console='H16',multimedia='H16',communications='Steam'))
        if 'select' in args:self.active=self.runner.FORMAT_GUID
        supported=self.fixture.values['1'][0].hex()!=self.preview['baselineBlock']
        return json.dumps(dict(supported=supported,active=self.active,selected=self.active))
    def process(self,argv,**kwargs):
        self.commands.append([str(x) for x in argv])
        if str(argv[0]).endswith('spatial-off.exe'):self.active=self.runner.ZERO
        code=0
        if self.fail_reload and 'edid-reload' in argv:self.fail_reload=False;code=1
        return types.SimpleNamespace(returncode=code,stdout=b'',stderr=b'')
    def invoke(self):
        out=self.fixture.folder/'live'
        args=types.SimpleNamespace(adapter_instance=r'PCI\VEN_10DE&DEV_TEST\INSTANCE',output=out)
        code=self.runner.execute(args,self.preview)
        return code,json.loads((out/'result.json').read_text('utf-8'))
    def test_four_reload_roundtrip_and_fresh_support_checks(self):
        before=dict(self.fixture.values);code,r=self.invoke()
        self.assertEqual(code,0,r);self.assertTrue(r['candidateRestoreVerified']);self.assertTrue(r['restored'])
        self.assertEqual(before,self.fixture.values)
        reloads=[s['name'] for s in r['stages'] if 'reload' in s['name']]
        self.assertEqual(reloads,['baseline-reload','injection-reload','candidate-restore-reload','baseline-restore-reload'])
        self.assertFalse(r['negativeControl']['supported']);self.assertTrue(r['positiveControl']['supported'])
    def test_reload_failure_restores_original_without_claiming_success(self):
        self.fail_reload=True;before=dict(self.fixture.values);code,r=self.invoke()
        self.assertEqual(code,1);self.assertFalse(r['completed']);self.assertTrue(r['restored'],r)
        self.assertEqual(before,self.fixture.values);self.assertIn('baseline-reload failed',r['error'])

if __name__=='__main__':unittest.main()
