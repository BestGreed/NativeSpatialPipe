"""Load the real ASIO module without inheriting the compiler's PATH; no device open."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser=argparse.ArgumentParser()
parser.add_argument('--probe',required=True,type=Path)
parser.add_argument('--module',required=True,type=Path)
parser.add_argument('--runtime-dir',required=True,type=Path)
parser.add_argument('--report',required=True,type=Path)
args=parser.parse_args()
env=dict(os.environ)
env={key:value for key,value in env.items() if key.upper()!='PATH'}
env['PATH']=str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32')
with tempfile.TemporaryDirectory(prefix='nsp-loader-',dir=args.report.resolve().parent) as temp:
    directory=Path(temp);module=directory/'NSP.AsioOutput.dll'
    shutil.copy2(args.module,module)
    def probe(mode):
        return subprocess.run([str(args.probe.resolve()),mode,str(module.resolve())],env=env,
                              cwd=args.report.resolve().parent,capture_output=True,text=True,timeout=10)
    missing=probe('safe')
    assert missing.returncode!=0 and 'Win32=126' in missing.stderr,missing
    for name in ('libc++.dll','libunwind.dll'):
        shutil.copy2(args.runtime_dir/name,directory/name)
    legacy=probe('legacy');safe=probe('safe')
    assert legacy.returncode!=0,legacy
    assert safe.returncode==0 and 'ABI verified' in safe.stdout,safe
result={'passed':True,'missingRuntimeRejected':True,'legacyLookupFailedWithAdjacentRuntimes':True,
        'safeLookupVerifiedAbi':True,'compilerPathRemoved':True,'audioDevicesOpened':False}
args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
