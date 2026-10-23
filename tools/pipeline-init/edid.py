"""Transactional CTA audio override; explicit endpoint/monitor binding required."""
import hashlib,json,re,sys,uuid
from pathlib import Path
from windows import ROOT,atomic,run
sys.path.insert(0,str(ROOT/'tools/edid-probe'))
from edid_probe import parse_edid,display_snapshot
def patch_audio(data,profile='both'):
 if profile not in ('dtsx','atmos','both'):raise ValueError('Unknown EDID audio profile')
 b=bytearray(data)
 if len(b)!=128 or b[0]!=2 or b[1]<3 or sum(b)%256 or not 4<=b[2]<=127:raise ValueError('Unsupported CTA block')
 end=b[2];pos=4;audio=None
 while pos<end:
  n=b[pos]&31
  if pos+1+n>end:raise ValueError('Invalid block boundary')
  if b[pos]>>5==1:
   if audio is not None:raise ValueError('Multiple audio blocks require review')
   audio=(pos,n)
  pos+=n+1
 if audio:
  pos,n=audio
  if n%3:raise ValueError('Invalid SAD length')
  sads=[bytes(b[i:i+3]) for i in range(pos+1,pos+1+n,3)]
 else:pos=end;n=0;sads=[]
 new=[]
 for sad in sads:
  fmt=(sad[0]>>3)&15
  if fmt==1:sad=bytes([sad[0]|7,sad[1]|0x7f,sad[2]|7])
  elif fmt==11 and profile in ('dtsx','both'):sad=bytes([sad[0]|7,sad[1]|0x7e,sad[2]|3])
  elif fmt==12 and profile in ('atmos','both'):sad=bytes([sad[0]|7,sad[1]|0x7f,sad[2]|3])
  new.append(sad)
 additions=[(1,'0f7f07')]
 if profile in ('dtsx','both'):additions.extend(((7,'3d1fc0'),(11,'5f7e03')))
 if profile in ('atmos','both'):additions.append((12,'677f03'))
 for fmt,raw in additions:
  if not any((s[0]>>3)&15==fmt for s in new):new.append(bytes.fromhex(raw))
 payload=b''.join(new)
 if len(payload)>31:raise ValueError('Audio block full')
 removed=n+1 if audio else 0
 delta=len(payload)+1-removed
 if delta<0 or any(b[127-delta:127]):raise ValueError('No spare CTA padding; preserve display timings')
 result=bytearray(b[:pos]+bytes([0x20|len(payload)])+payload+b[pos+removed:127-delta])
 if len(result)!=127:raise ValueError('Rebuild length mismatch')
 result[2]=end+delta;result[3]|=0x40;result.append((-sum(result))&255)
 return bytes(result)
def plan(endpoint,monitor,output,profile='both'):
 import winreg as w
 if not any(m['active_in_desktop'] and m['registry_identity']==monitor.lower() for m in display_snapshot()):raise ValueError('Monitor is not an active desktop instance')
 probe=json.loads(run([ROOT/'work/OemEndpointProbe.exe']))
 target=next((e for e in probe['endpoints'] if e['id'].lower()==endpoint.lower() and e['state']==1),None)
 if not target or not any(n in target['name'].lower() for n in ('nvidia','amd','intel','radeon')):raise ValueError('Endpoint is not a verified active GPU output')
 sub='\\'.join(('SYSTEM','CurrentControlSet','Enum','DISPLAY',monitor,'Device Parameters'))
 with w.OpenKey(w.HKEY_LOCAL_MACHINE,sub) as key:
  original,kind=w.QueryValueEx(key,'EDID')
  if kind!=w.REG_BINARY:raise ValueError('Cached EDID type must be REG_BINARY')
 saved=[]
 try:
  with w.OpenKey(w.HKEY_LOCAL_MACHINE,'\\'.join((sub,'EDID_OVERRIDE'))) as key:
   for i in range(w.QueryInfoKey(key)[1]):
    name,value,kind=w.EnumValue(key,i);saved.append({'name':name,'kind':kind,'value':value.hex() if isinstance(value,bytes) else value})
 except FileNotFoundError:pass
 if any(v['name'].isdigit() and v['kind']!=w.REG_BINARY for v in saved):raise ValueError('EDID override type must be REG_BINARY')
 values={v['name']:bytes.fromhex(v['value']) for v in saved if v['kind']==w.REG_BINARY and v['name'].isdigit()}
 base=values.get('0',original[:128]);count=base[126]+1
 blocks=[values.get(str(i),original[i*128:i*128+128]) for i in range(count)]
 before=b''.join(blocks)
 if not parse_edid(before)['structure_ok']:raise ValueError('Existing effective EDID invalid')
 index=next((i for i in range(1,count) if blocks[i][0]==2 and blocks[i][1]>=3),None)
 if index is None:raise ValueError('No CTA block; explicit extension authoring required')
 changed=patch_audio(blocks[index],profile);old=blocks[index];blocks[index]=changed;after=b''.join(blocks)
 analysis=parse_edid(after)
 if not analysis['structure_ok']:raise ValueError('Patched EDID invalid')
 output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
 record={'version':1,'id':uuid.uuid4().hex,'endpoint':endpoint,'monitor':monitor,'subkey':sub,'profile':profile,'binding':'Explicit caller-selected endpoint/monitor pair',
  'cachedSha256':hashlib.sha256(original).hexdigest(),'savedOverrides':saved,'block':index,'oldBlock':old.hex(),'newBlock':changed.hex(),
  'oldBlockHadOverride':str(index) in values,'before':parse_edid(before),'after':analysis,'state':'planned'}
 atomic(output/'edid-plan.json',record);(output/'before.bin').write_bytes(before);(output/'after.bin').write_bytes(after)
 return record
def mutate(path,restore=False):
 import winreg as w
 path=Path(path).resolve();r=json.loads(path.read_text(encoding='utf8'))
 if not re.fullmatch(r'[^\\/]+\\[^\\/]+',r['monitor']):raise ValueError('Invalid monitor instance')
 wanted_sub='\\'.join(('SYSTEM','CurrentControlSet','Enum','DISPLAY',r['monitor'],'Device Parameters'))
 if r['subkey'].lower()!=wanted_sub.lower():raise ValueError('Plan registry path mismatch')
 if not isinstance(r['block'],int) or not 1<=r['block']<=255:raise ValueError('Invalid extension index')
 for field in ('oldBlock','newBlock'):
  block=bytes.fromhex(r[field])
  if len(block)!=128 or sum(block)%256:raise ValueError('Invalid planned EDID block')
 if r['version']!=1 or r['state'] not in (('applied','applying','restoring','restored') if restore else ('planned','applying','applied')):raise ValueError('Unexpected transaction state')
 with w.OpenKey(w.HKEY_LOCAL_MACHINE,r['subkey']) as key:
  cached,kind=w.QueryValueEx(key,'EDID')
  if kind!=w.REG_BINARY:raise ValueError('Cached EDID type must be REG_BINARY')
  if hashlib.sha256(cached).hexdigest()!=r['cachedSha256']:raise ValueError('Monitor EDID changed since plan')
 def read_override(key,name):
  try:value,kind=w.QueryValueEx(key,name)
  except FileNotFoundError:return None
  if kind!=w.REG_BINARY:raise ValueError('Override registry type must be REG_BINARY')
  return value
 with w.CreateKeyEx(w.HKEY_LOCAL_MACHINE,'\\'.join((r['subkey'],'EDID_OVERRIDE')),0,w.KEY_QUERY_VALUE|w.KEY_SET_VALUE) as key:
  name=str(r['block'])
  current=read_override(key,name)
  expected=bytes.fromhex(r['newBlock']) if restore else (bytes.fromhex(r['oldBlock']) if r['oldBlockHadOverride'] else None)
  original=bytes.fromhex(r['oldBlock']) if r['oldBlockHadOverride'] else None
  if restore and current==original:
   r['state']='restored';r['reloadRequired']=True;atomic(path,r);return r
  if not restore and current==bytes.fromhex(r['newBlock']):
   r['state']='applied';r['reloadRequired']=True;atomic(path,r);return r
  if r['state']==('restored' if restore else 'applied'):
   raise ValueError('Override changed after completed transaction')
  if current!=expected:raise ValueError('Override changed; refuse to overwrite another edit')
  r['state']='restoring' if restore else 'applying';atomic(path,r)
  # Durable intent includes old/new bytes before mutation. Recover can repeat
  # either direction after checking the exact current block.
  if restore and not r['oldBlockHadOverride']:w.DeleteValue(key,name)
  else:w.SetValueEx(key,name,0,w.REG_BINARY,bytes.fromhex(r['oldBlock'] if restore else r['newBlock']))
  w.FlushKey(key)
  actual=read_override(key,name)
  wanted=(bytes.fromhex(r['oldBlock']) if r['oldBlockHadOverride'] else None) if restore else bytes.fromhex(r['newBlock'])
  if actual!=wanted:raise RuntimeError('Override readback mismatch')
 r['state']='restored' if restore else 'applied';r['reloadRequired']=True;atomic(path,r);return r
