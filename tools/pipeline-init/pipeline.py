"""Parameter-driven front-pipeline initialization API used by FSA and scripts."""
import argparse,json,sys
from pathlib import Path
from model import FORMATS,NAMES
from windows import ROOT,run,ps,default_endpoint,home
def prepare(endpoint,format,persist,groups,owner_pid=None):
 from manager import status
 if endpoint.lower()!=default_endpoint().lower():raise ValueError('Preferred endpoint changed')
 state=status()
 if groups:
  # start is idempotent for the same resident configuration; no restart per NSp.
  lifetime=['--persist'] if persist else ['--owner-pid',str(owner_pid)] if owner_pid else None
  if lifetime is None:raise ValueError('Nonpersistent groups require --owner-pid')
  run([sys.executable,Path(__file__).with_name('manager.py'),'start','--endpoint',endpoint,'--groups',*groups,*lifetime],timeout=20)
  state=status()
 query=json.loads(ps(Path(__file__).with_name('Spatial-Format.ps1'),'-Action','query','-Endpoint',endpoint,'-Format',format))
 if not query['supported']:
  if not (state.get('live') and state.get('endpoint','').lower()==endpoint.lower() and format in state.get('formats',[])):
   raise ValueError('Unsupported format without a live matching enumeration hook')
 result=json.loads(ps(Path(__file__).with_name('Spatial-Format.ps1'),'-Action','select','-Endpoint',endpoint,'-Format',format))
 if result['active'].strip('{}').lower()!=FORMATS[format]:raise RuntimeError('Active identity mismatch')
 return {'prepared':True,'endpoint':endpoint,'format':format,'selection':result,'resident':state}
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
 for action in ('prepare','query','ensure','verify-hook'):
  q=sub.add_parser(action);q.add_argument('--endpoint',required=True);q.add_argument('--format',choices=NAMES,default='WindowsSonic');q.add_argument('--persist',action='store_true');q.add_argument('--owner-pid',type=int);q.add_argument('--groups',nargs='*',choices=('headphones','speakers'),default=[])
 q=sub.add_parser('edid-plan');q.add_argument('--endpoint',required=True);q.add_argument('--monitor-instance',required=True);q.add_argument('--output',required=True);q.add_argument('--profile',choices=('dtsx','atmos','both'),default='both')
 for action in ('edid-apply','edid-restore'):
  q=sub.add_parser(action);q.add_argument('--plan',required=True)
 q=sub.add_parser('edid-reload');q.add_argument('--plan',required=True);q.add_argument('--adapter-instance',required=True)
 q=sub.add_parser('edid-enable');q.add_argument('--endpoint',required=True);q.add_argument('--output',required=True)
 for action in ('status','recover','stop'):sub.add_parser(action)
 q=sub.add_parser('inspect');q.add_argument('--endpoint',required=True)
 a=p.parse_args()
 if a.action=='inspect':
  sys.path.insert(0,str(ROOT/'tools/edid-probe'));from edid_probe import display_snapshot
  probe=json.loads(run([ROOT/'work/OemEndpointProbe.exe']))
  e=next((e for e in probe['endpoints'] if e['id'].lower()==a.endpoint.lower()),None)
  if not e:raise ValueError('Endpoint not found')
  gpu=any(t in e['name'].lower() for t in ('nvidia','amd','intel','radeon'))
  modes={n:json.loads(ps(Path(__file__).with_name('Spatial-Format.ps1'),'-Action','query','-Endpoint',a.endpoint,'-Format',n))['supported'] for n in ('WindowsSonic','DTSXForHomeTheater','DolbyAtmosForHomeTheater')}
  r={'endpoint':e,'gpuOutput':gpu,'nativeSupport':modes,'recommendEdidInitialization':gpu and not (modes['DTSXForHomeTheater'] or modes['DolbyAtmosForHomeTheater']),
     'monitorCandidates':[m for m in display_snapshot() if m['active_in_desktop']],'monitorBindingRequiresExplicitSelection':True}
 elif a.action=='verify-hook':
  from manager import status
  s=status();valid=s.get('live') and s['endpoint'].lower()==a.endpoint.lower() and a.format in s.get('formats',[])
  print(json.dumps({'verified':bool(valid),'resident':s}));return 0 if valid else 1
 elif a.action=='ensure':
  if not a.groups or (not a.persist and not a.owner_pid):raise ValueError('Ensure requires groups and --persist or --owner-pid')
  lifetime=['--persist'] if a.persist else ['--owner-pid',str(a.owner_pid)]
  r=json.loads(run([sys.executable,Path(__file__).with_name('manager.py'),'start','--endpoint',a.endpoint,'--groups',*a.groups,*lifetime],timeout=20))
 elif a.action=='prepare':r=prepare(a.endpoint,a.format,a.persist,a.groups,a.owner_pid)
 elif a.action=='query':r=json.loads(ps(Path(__file__).with_name('Spatial-Format.ps1'),'-Action','query','-Endpoint',a.endpoint,'-Format',a.format))
 elif a.action=='edid-enable':
  from edid_menu import enable
  r=enable(a.endpoint,a.output)
  print(json.dumps(r,ensure_ascii=False));return 0 if r.get('verified') else 1
 elif a.action=='edid-plan':
  from edid import plan;r=plan(a.endpoint,a.monitor_instance,a.output,a.profile)
 elif a.action=='edid-reload':
  r=json.loads(Path(a.plan).read_text(encoding='utf8'))
  if r.get('state') not in ('applied','restored'):raise ValueError('Plan is not committed')
  text=ps(Path(__file__).with_name('Reload-Display.ps1'),'-InstanceId',a.adapter_instance,timeout=45)
  r={'reloaded':True,'adapter':a.adapter_instance,'result':text,'verifyNativeSupportNext':True}
 elif a.action.startswith('edid-'):
  from edid import mutate;r=mutate(a.plan,a.action=='edid-restore')
 else:
  from manager import status,stop;r=status() if a.action=='status' else stop()
 print(json.dumps(r,ensure_ascii=False))
 return 1 if r.get('stopped') is False else 0
if __name__=='__main__':
 try:sys.exit(main())
 except Exception as e:print(json.dumps({'error':str(e)}));sys.exit(1)
