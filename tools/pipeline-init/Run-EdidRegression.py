"""Explicit OG27QH audio-only EDID baseline/inject/reload/restore regression.

Default prints the reviewed preview. --execute requires elevation and restarts
only the exact caller-selected NVIDIA display adapter. Never touches H16 EDID.
"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import winreg as w
import edid
from windows import ROOT,atomic,ps,run

FORMAT='DTSXForHomeTheater'
FORMAT_GUID='{10201b4a-3322-4967-bf40-2caa9bafca44}'
ZERO='{00000000-0000-0000-0000-000000000000}'

def snapshot(subkey):
    with w.OpenKey(w.HKEY_LOCAL_MACHINE,subkey) as key:cached,kind=w.QueryValueEx(key,'EDID')
    if kind!=w.REG_BINARY:raise ValueError('Nonbinary cached EDID')
    values={}
    with w.OpenKey(w.HKEY_LOCAL_MACHINE,subkey+'\\EDID_OVERRIDE') as key:
        for i in range(w.QueryInfoKey(key)[1]):
            name,value,kind=w.EnumValue(key,i);values[name]=dict(kind=kind,value=value.hex() if isinstance(value,bytes) else value)
    return dict(cached=cached.hex(),cachedSha256=hashlib.sha256(cached).hexdigest(),overrides=values)

def execute(args,preview):
    if not ctypes.windll.shell32.IsUserAnAdmin():raise RuntimeError('Administrator required')
    if not args.adapter_instance.startswith('PCI\\VEN_10DE&'):raise ValueError('Exact NVIDIA PCI instance required')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    endpoint=preview['endpoint'];monitor=preview['monitor'];block=str(preview['changedBlock'])
    subkey='SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\'+monitor+'\\Device Parameters'
    state=dict(completed=False,restored=False,scope='OG27QH EDID declarations only; fresh-process WinRT queries after display driver reload; no PCM/audio quality test',stages=[])
    before=None;mode=None;defaults=None;changed=False;owned={preview['currentBlock'],preview['baselineBlock'],preview['candidateBlock']}
    def save():atomic(out/'result.json',state)
    def command(name,argv,timeout=60):
        p=subprocess.run([str(x) for x in argv],capture_output=True,timeout=timeout,creationflags=0x08000000)
        (out/(name+'.stdout.log')).write_bytes(p.stdout);(out/(name+'.stderr.log')).write_bytes(p.stderr)
        state['stages'].append(dict(name=name,exitCode=p.returncode));save()
        if p.returncode:raise RuntimeError(name+' failed; inspect logs')
        return p.stdout.decode('utf-8-sig').strip()
    def query():return json.loads(ps(ROOT/'tools/pipeline-init/Spatial-Format.ps1','-Action','query','-Endpoint',endpoint,'-Format',FORMAT))
    def reload(name,plan):
        # Production API invokes Reload-Display.ps1, which checks this exact
        # present physical PCI adapter; no blanket device or service restart.
        command(name,[sys.executable,Path(__file__).with_name('pipeline.py'),'edid-reload','--plan',plan,'--adapter-instance',args.adapter_instance],timeout=60)
    def wait_support(wanted):
        deadline=time.monotonic()+60;observations=[]
        while True:
            try:
                result=query();observations.append(result)
                if result['supported']==wanted:return result,observations
            except Exception as exc:observations.append(dict(error=str(exc)))
            if time.monotonic()>=deadline:raise RuntimeError('Fresh-process support query did not reach expected '+str(wanted))
            time.sleep(1)
    try:
        owners=json.loads(run(['powershell.exe','-NoProfile','-Command',"@(Get-Process FinalSpatialAudio,Wwise,nsp-scheduler,nsp-latency-host -ErrorAction SilentlyContinue | Select-Object Id,ProcessName) | ConvertTo-Json -Compress"]) or '[]')
        if owners:raise RuntimeError('Existing FSA/audio test owner; stop it before display reload')
        # Check prior EDID identity and live endpoints before any mutation.
        before=snapshot(subkey);atomic(out/'before.json',before)
        if before['overrides'][block]!=dict(kind=w.REG_BINARY,value=preview['currentBlock']):raise ValueError('OG27QH override changed since reviewed preview')
        mode=query();defaults=json.loads(ps(ROOT/'tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1'))
        state.update(endpoint=endpoint,monitor=monitor,adapter=args.adapter_instance,modeBefore=mode,defaultsBefore=defaults);save()
        if mode['active'].lower()!=ZERO or mode['selected'].lower()!=ZERO:raise ValueError('This reviewed regression requires OG27QH spatial mode initially off')
        from manager import status
        if status().get('live'):raise ValueError('Enumeration hook is live; cannot prove native EDID support')
        baseline=edid.plan(endpoint,monitor,out/'baseline',profile='dtsx')
        baseline['newBlock']=preview['baselineBlock']
        data=bytearray((out/'baseline/before.bin').read_bytes());index=int(block);data[index*128:(index+1)*128]=bytes.fromhex(baseline['newBlock'])
        baseline['after']=edid.parse_edid(data)
        if not baseline['after']['structure_ok']:raise ValueError('Historical negative-control EDID invalid')
        baseline_path=out/'baseline/edid-plan.json';atomic(baseline_path,baseline);(out/'baseline/after.bin').write_bytes(data)
        # Mark ownership before mutation so finally covers partial write errors.
        changed=True;edid.mutate(baseline_path);reload('baseline-reload',baseline_path)
        negative,seen=wait_support(False);state.update(negativeControl=negative,negativeQueries=seen);save()
        candidate=edid.plan(endpoint,monitor,out/'candidate',profile='dtsx')
        if candidate['newBlock']!=preview['candidateBlock']:raise ValueError('Candidate differs from reviewed preview')
        candidate_path=out/'candidate/edid-plan.json';edid.mutate(candidate_path);reload('injection-reload',candidate_path)
        positive,seen=wait_support(True);state.update(positiveControl=positive,positiveQueries=seen);save()
        selected=json.loads(ps(ROOT/'tools/pipeline-init/Spatial-Format.ps1','-Action','select','-Endpoint',endpoint,'-Format',FORMAT))
        fresh=query()
        if fresh['active'].lower()!=FORMAT_GUID or fresh['selected'].lower()!=FORMAT_GUID:raise RuntimeError('Fresh active/default identity mismatch')
        state.update(selection=selected,selectedReadback=fresh,completed=True);save()
    except Exception as exc:
        state['error']=str(exc);state['traceback']=traceback.format_exc();save()
    finally:
        if changed:
            def verify_final():
                if snapshot(subkey)['overrides']!=before['overrides']:raise RuntimeError('Pre-test override snapshot not restored exactly')
                restored=query();after_defaults=json.loads(ps(ROOT/'tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1'))
                if any(after_defaults[k]!=defaults[k] for k in ['console','multimedia','communications']):raise RuntimeError('Default render roles changed')
                if restored['supported']!=mode['supported'] or restored['active'].lower()!=mode['active'].lower() or restored['selected'].lower()!=mode['selected'].lower():raise RuntimeError('OG27QH spatial state not restored')
                state.update(restored=True,modeAfter=restored,defaultsAfter=after_defaults,overrideSnapshotRestored=True);atomic(out/'after.json',snapshot(subkey))
            try:
                command('spatial-off',[ROOT/'build/edid-regression-20261023/spatial-off.exe',endpoint])
                candidate_path=out/'candidate/edid-plan.json'
                if candidate_path.exists() and json.loads(candidate_path.read_text('utf-8'))['state'] in ('applying','applied','restoring'):
                    edid.mutate(candidate_path,True);reload('candidate-restore-reload',candidate_path)
                    negative,seen=wait_support(False);state.update(candidateRestoreVerified=True,restoredNegativeControl=negative,restoreQueries=seen);save()
                edid.mutate(out/'baseline/edid-plan.json',True);reload('baseline-restore-reload',out/'baseline/edid-plan.json')
                wait_support(mode['supported']);verify_final()
            except Exception as exc:
                state['restoreError']=str(exc);state['restoreTraceback']=traceback.format_exc();save()
            if not state['restored']:
              try:
                # Explicit pre-test snapshot restoration, not an old cached
                # device hash bypass in the production mutator. Restore only
                # the owned CTA value if its exact bytes are from this trial.
                current=snapshot(subkey)
                if current['overrides'][block]['kind']!=w.REG_BINARY or current['overrides'][block]['value'] not in owned:raise RuntimeError('Foreign CTA edit; manual recovery required')
                command('spatial-off',[ROOT/'build/edid-regression-20261023/spatial-off.exe',endpoint])
                journal=dict(state='restoring',target=subkey,block=block,original=before['overrides'][block],current=current['overrides'][block]);atomic(out/'restore-intent.json',journal)
                with w.OpenKey(w.HKEY_LOCAL_MACHINE,subkey+'\\EDID_OVERRIDE',0,w.KEY_QUERY_VALUE|w.KEY_SET_VALUE) as key:
                    w.SetValueEx(key,block,0,w.REG_BINARY,bytes.fromhex(before['overrides'][block]['value']));w.FlushKey(key)
                if snapshot(subkey)['overrides']!=before['overrides']:raise RuntimeError('Pre-test override snapshot not restored exactly')
                # Production reload accepts only a committed restored journal.
                atomic(out/'restore-plan.json',dict(state='restored'))
                reload('restoration-reload',out/'restore-plan.json')
                wait_support(mode['supported']);verify_final();state['emergencySnapshotRestore']=True
              except Exception as exc:state['emergencyRestoreError']=str(exc);state['emergencyRestoreTraceback']=traceback.format_exc()
        state['passed']=state['completed'] and state['restored'] and 'restoreError' not in state and 'error' not in state
        save()
    return 0 if state['passed'] else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preview',type=Path,default=ROOT/'build/edid-regression-20261023/live-preview.json');p.add_argument('--output',type=Path,required=True);p.add_argument('--adapter-instance',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    preview=json.loads(a.preview.read_text('utf-8'))
    if not a.execute:print(json.dumps(preview,indent=2));sys.exit(0)
    sys.exit(execute(a,preview))
