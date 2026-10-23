"""Seven output geometries, bed/object input, tap and lease dimensions; no devices."""
import ctypes as C
import json
import math
import os
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(os.environ.get('NSP_LAYOUT_TEST_OUT',str(ROOT/'build/layout-presets-20261023')))
RUN=OUT/'tests';RUN.mkdir(exist_ok=True)
search=os.add_dll_directory(str(ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'))
catalog=json.loads((ROOT/'contracts/output-layouts.json').read_text())['layouts']
bridge=C.CDLL(str(OUT/'rust/release/nsp_roar_bridge.dll'))
bridge.nsp_pipeline_create_layout.argtypes=[C.c_uint32]*4+[C.c_double,C.c_char_p,C.c_char_p]
bridge.nsp_pipeline_create_layout.restype=C.c_void_p
bridge.nsp_pipeline_submit.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t]
bridge.nsp_pipeline_finish.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];bridge.nsp_pipeline_finish.restype=C.c_size_t
bridge.nsp_pipeline_destroy.argtypes=[C.c_void_p]
bridge.nsp_pipeline_telemetry_enable.argtypes=[C.c_void_p,C.c_uint32]
bridge.nsp_pipeline_tap_read.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];bridge.nsp_pipeline_tap_read.restype=C.c_size_t
Process=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint32,C.POINTER(C.c_float),C.c_uint32,C.c_uint32,C.POINTER(C.c_uint32))
Release=C.CFUNCTYPE(None,C.c_void_p)
Publish=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint64,C.POINTER(C.c_float),C.c_uint32,C.c_uint32,C.c_uint32,C.c_void_p,Release)
bridge.nsp_pipeline_set_pool_output.argtypes=[C.c_void_p,Process,Publish,C.c_void_p]
checks=[]
for row in catalog:
 for renderer in ('roar','lira'):
  for bed in (False,True):
   channels=len(row['channels']);types=[2**(i+1) for i in range(12)] if bed else [1]
   name=f'{row["code"]}-{renderer}-{"bed" if bed else "object"}'
   path=RUN/(name+'.f32');path.unlink(missing_ok=True)
   handle=bridge.nsp_pipeline_create_layout(480,32,row['code'],0 if renderer=='lira' else 1,1.,str(OUT/'NSP.SpatialRenderer.dll').encode() if renderer=='lira' else None,str(path).encode())
   assert handle,name+' create failed'
   dimensions=[];released=[];processed=[]
   @Process
   def process(_,op,pcm,capacity,count,written):
    processed.append(count)
    written[0]=capacity if op==0 else 0
    return 0 if count==channels else -1
   @Publish
   def publish(_,first,pcm,frames,count,tail,token,release):
    dimensions.append((frames,count,first));release(token);released.append(1)
    return 0 if count==channels else -1
   try:
    assert bridge.nsp_pipeline_set_pool_output(handle,process,publish,None)==0
    assert bridge.nsp_pipeline_telemetry_enable(handle,1)==0
    for seq in range(8):
     props=b''.join(struct.pack('<4I4f',i+1,480,t,7,.3,.6,-.8,1.) for i,t in enumerate(types))
     raw=props+b''.join(struct.pack('<f',.01*math.sin((seq*480+f)*.03+c*.4)) for c in range(len(types)) for f in range(480))
     assert bridge.nsp_pipeline_submit(handle,seq,len(types),raw,len(raw))==0
    size=bridge.nsp_pipeline_finish(handle,None,0);assert size>0
    report=C.create_string_buffer(size);assert bridge.nsp_pipeline_finish(handle,report,size)==size
    result=json.loads(report.value);assert result['passed'],(name,result)
    assert result['outputLayout']==row['id'] and result['outputChannels']==channels
    pcm=path.read_bytes();expected=8*480+(512 if renderer=='lira' else 0)
    assert len(pcm)==expected*channels*4,(name,len(pcm),expected)
    values=struct.unpack('<'+'f'*(len(pcm)//4),pcm);assert all(map(math.isfinite,values)) and any(v!=0 for v in values)
    assert dimensions and all(d[1]==channels for d in dimensions) and len(released)==len(dimensions)
    assert all(c==channels for c in processed)
    tap=C.create_string_buffer(32+16*480*4);length=bridge.nsp_pipeline_tap_read(handle,tap,len(tap))
    assert length==32+channels*480*4,(name,length)
    assert struct.unpack_from('<H',tap.raw,6)[0]==channels
    checks.append(dict(layout=row['id'],renderer=renderer,bed=bed,channels=channels,outputFrames=expected,leaseBlocks=len(dimensions)))
   finally:bridge.nsp_pipeline_destroy(handle)
def legacy714(dll,renderer,bed,tag,lira):
 dll.nsp_pipeline_create_with_position_mapping.argtypes=[C.c_uint32]*5+[C.c_double,C.c_char_p]
 dll.nsp_pipeline_create_with_position_mapping.restype=C.c_void_p
 dll.nsp_pipeline_create_spatial.argtypes=[C.c_uint32,C.c_uint32,C.c_char_p,C.c_char_p]
 dll.nsp_pipeline_create_spatial.restype=C.c_void_p
 dll.nsp_pipeline_submit.argtypes=bridge.nsp_pipeline_submit.argtypes
 dll.nsp_pipeline_finish.argtypes=bridge.nsp_pipeline_finish.argtypes
 dll.nsp_pipeline_finish.restype=C.c_size_t
 dll.nsp_pipeline_destroy.argtypes=[C.c_void_p]
 path=RUN/(tag+'.f32');path.unlink(missing_ok=True)
 handle=(dll.nsp_pipeline_create_spatial(480,32,str(lira).encode(),str(path).encode()) if renderer=='lira'
         else dll.nsp_pipeline_create_with_position_mapping(480,32,0,0,1,1.,str(path).encode()))
 assert handle
 try:
  types=[2**(i+1) for i in range(12)] if bed else [1]
  for seq in range(8):
   props=b''.join(struct.pack('<4I4f',i+1,480,t,7,.3,.6,-.8,1.) for i,t in enumerate(types))
   raw=props+b''.join(struct.pack('<f',.01*math.sin((seq*480+f)*.03+c*.4)) for c in range(len(types)) for f in range(480))
   assert dll.nsp_pipeline_submit(handle,seq,len(types),raw,len(raw))==0
  size=dll.nsp_pipeline_finish(handle,None,0);assert size>0
  report=C.create_string_buffer(size);dll.nsp_pipeline_finish(handle,report,size)
  assert json.loads(report.value)['passed'],(tag,report.value)
 finally:dll.nsp_pipeline_destroy(handle)
 return path.read_bytes()

compat=[]
reference_bridge=OUT/'reference-roar.dll'
reference_lira=OUT/'reference-lira.dll'
if not reference_bridge.exists():reference_bridge=ROOT/'build/roar-native/release/nsp_roar_bridge.dll'
if not reference_lira.exists():reference_lira=ROOT/'build/spatial-renderer/NSP.SpatialRenderer.dll'
old=C.CDLL(str(reference_bridge))
for renderer in ('roar','lira'):
 for bed in (False,True):
  name=f'714-{renderer}-'+('bed' if bed else 'object')
  reference=legacy714(old,renderer,bed,name+'-before',reference_lira)
  current=legacy714(bridge,renderer,bed,name+'-legacy',OUT/'NSP.SpatialRenderer.dll')
  assert reference==current==(RUN/(name+'.f32')).read_bytes(),name+' compatibility changed'
  compat.append(name)
result=dict(passed=True,count=len(checks),checks=checks,bitExact714=compat,audioDevicesOpened=False)
(OUT/'renderer-tests.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(dict(passed=True,count=len(checks))))
