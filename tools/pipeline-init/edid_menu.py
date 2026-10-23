"""Explicit EDID menu operation. No resident checks or automatic entry points.

Bind by the endpoint/monitor ContainerId, never by a friendly name. Only an
unambiguous active monitor with an exact present GPU parent may be written.
"""
import ctypes
import json
import re
import time
import uuid
import winreg as w
from pathlib import Path
import sys
from windows import ROOT,atomic,ps
import edid

HT=('DolbyAtmosForHomeTheater','DTSXForHomeTheater')
PROPS=r'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
FORM='{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},0'
ENUMERATOR='{a45c254e-df1c-4efd-8020-67d146a850e0},24'
INSTANCE='{b3f8fa53-0004-438e-9003-51a46e139bfc},2'
CONTAINER='{8c7ed206-3f8a-4827-b3ab-ae9e1faefc6c},2'

def eligible(form,enumerator,instance):
 return form==9 and enumerator.upper()=='HDAUDIO' and instance.upper().startswith('{1}.HDAUDIO\\')

def endpoint_binding(endpoint):
 if not re.fullmatch(r'\{0\.0\.0\.00000000\}\.\{[0-9a-fA-F-]{36}\}',endpoint):raise ValueError('Invalid render endpoint identity')
 guid=endpoint.split('.')[-1]
 with w.OpenKey(w.HKEY_LOCAL_MACHINE,PROPS+'\\'+guid+'\\Properties') as key:
  form=w.QueryValueEx(key,FORM)[0];bus=w.QueryValueEx(key,ENUMERATOR)[0];instance=w.QueryValueEx(key,INSTANCE)[0]
  raw,kind=w.QueryValueEx(key,CONTAINER)
 if not eligible(form,bus,instance):raise ValueError('EDID is unavailable for this endpoint transport')
 if kind!=w.REG_BINARY or not isinstance(raw,bytes) or len(raw)!=24 or raw[:8]!=bytes.fromhex('4800000001000000'):raise ValueError('Missing endpoint ContainerId evidence')
 container=uuid.UUID(bytes_le=raw[8:])
 if container.int in (0,(1<<64)-1):raise ValueError('Nonunique endpoint ContainerId')
 sys.path.insert(0,str(ROOT/'tools/edid-probe'))
 from edid_probe import display_snapshot
 matches=[]
 for monitor in display_snapshot():
  identity=monitor.get('registry_identity')
  if not monitor.get('active_in_desktop') or not identity:continue
  try:
   with w.OpenKey(w.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Enum\DISPLAY'+'\\'+identity) as key:
    value=w.QueryValueEx(key,'ContainerID')[0]
   if uuid.UUID(value)==container:matches.append(identity)
  except FileNotFoundError:continue
 matches=sorted(set(matches))
 if len(matches)!=1:raise ValueError('Endpoint does not identify exactly one active monitor; no EDID written')
 monitor=matches[0]
 resolved=json.loads(ps(Path(__file__).with_name('Resolve-EdidAdapter.ps1'),'-MonitorInstance',monitor,timeout=30))
 if resolved['monitor'].lower()!=monitor.lower() or not resolved['adapter'].upper().startswith('PCI\\'):raise ValueError('Invalid monitor/GPU binding')
 return dict(endpoint=endpoint,container=str(container),monitor=monitor,adapter=resolved['adapter'])

def support(endpoint):
 return {name:bool(json.loads(ps(Path(__file__).with_name('Spatial-Format.ps1'),'-Action','query','-Endpoint',endpoint,'-Format',name))['supported']) for name in HT}

def check_default(endpoint):
 current=json.loads(ps(ROOT/'tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1'))['console']
 if current.lower()!=endpoint.lower():raise ValueError('Default render endpoint changed; no further EDID operation')

def enable(endpoint,output):
 output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
 result=dict(endpoint=endpoint,verified=False,overrideApplied=False,reloaded=False)
 try:
  if not ctypes.windll.shell32.IsUserAnAdmin():raise PermissionError('Administrator required for EDID initialization')
  check_default(endpoint)
  before=support(endpoint);result['beforeSupport']=before
  if any(before.values()):
   result.update(verified=True,alreadySupported=True);return result
  binding=endpoint_binding(endpoint);result['binding']=binding
  transaction=output/'transaction'
  planned=edid.plan(endpoint,binding['monitor'],transaction,profile='both')
  result['plan']=str(transaction/'edid-plan.json')
  if planned['oldBlock']==planned['newBlock']:raise ValueError('EDID already declares the requested formats; no useful override can be added')
  check_default(endpoint)
  if endpoint_binding(endpoint)!=binding:raise ValueError('Device binding changed before apply')
  edid.mutate(result['plan']);result['overrideApplied']=True;atomic(output/'result.json',result)
  ps(Path(__file__).with_name('Reload-Display.ps1'),'-InstanceId',binding['adapter'],timeout=60)
  result['reloaded']=True;atomic(output/'result.json',result)
  deadline=time.monotonic()+45
  while True:
   try:
    after=support(endpoint);result['afterSupport']=after
    if any(after.values()):result['verified']=True;break
   except Exception as exc:result['lastQueryError']=str(exc)
   if time.monotonic()>=deadline:raise RuntimeError('EDID was written but HT capability did not become available; see retained transaction')
   time.sleep(1)
  return result
 except Exception as exc:
  result['error']=str(exc);return result
 finally:
  atomic(output/'result.json',result)
