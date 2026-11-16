"""Explicit elevated live smoke: enumerate, verify and restore; never select formats."""
import argparse
import ctypes as C
import json
import os
import sys
from pathlib import Path
from windows import atomic,default_endpoint,ps,run,home
from manager import status
from model import GROUPS
from native_enum import audit_bytes

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if not C.windll.shell32.IsUserAnAdmin():raise RuntimeError('Administrator required')
 if status().get('live'):raise RuntimeError('Existing enumeration resident: do not interrupt it')
 endpoint=default_endpoint();manager=Path(__file__).with_name('manager.py')
 fmt=Path(__file__).with_name('Spatial-Format.ps1')
 names=GROUPS['headphones']+GROUPS['speakers']
 def query(name):return json.loads(ps(fmt,'-Action','query','-Endpoint',endpoint,'-Format',name))
 report={'endpoint':endpoint,'before':{n:query(n) for n in names},'errors':[]}
 def save():atomic(a.output,report)
 save()
 try:
  report['ready']=json.loads(run([sys.executable,manager,'start','--endpoint',endpoint,'--groups','headphones','speakers','--owner-pid',os.getpid()],timeout=25))
  report['during']={n:query(n) for n in names}
  report['resident']=json.loads((home()/'resident.json').read_text(encoding='utf8'))
  report['renewed']=status()
  if not report['renewed'].get('live'):raise AssertionError('Resident no longer live')
  if report['resident']['snapshot']['backend']!='minhook':raise AssertionError('Wrong backend')
  if not all(v['supported'] for v in report['during'].values()):raise AssertionError('Some formats remain unsupported')
 except Exception as e:report['errors'].append(repr(e))
 finally:
  try:
   report['recovery']=json.loads(run([sys.executable,manager,'recover'],timeout=25))
   report['after']={n:query(n) for n in names}
   if not report['recovery'].get('stopped'):raise AssertionError('Cleanup not verified')
   if len(report['recovery'].get('cleanup',[]))!=3 or not all(e['restored'] for e in report['recovery']['cleanup']):raise AssertionError('Bytes not restored')
   resident=report.get('resident',{})
   if resident.get('snapshot'):
    report['readOnlyAudit']=audit_bytes(resident['servicePid'],resident['snapshot']['entries'])
    if not all(e['restored'] for e in report['readOnlyAudit']):raise AssertionError('Read-only audit failed')
   for n in names:
    for key in ('supported','active','selected'):
     if report['before'][n][key]!=report['after'][n][key]:raise AssertionError(f'{n} {key} changed')
  except Exception as e:report['errors'].append(repr(e))
  save()
 print(json.dumps({'passed':not report['errors'],'output':str(a.output),'errors':report['errors']}))
 return 1 if report['errors'] else 0
if __name__=='__main__':sys.exit(main())
