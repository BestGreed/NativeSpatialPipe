"""Actual live ABI: recording cannot change post-DSP output or lease ownership."""
import argparse
import ctypes as C
import json
import math
import os
from pathlib import Path
import struct
import tempfile

parser=argparse.ArgumentParser()
parser.add_argument('--bridge',required=True,type=Path)
parser.add_argument('--lira',required=True,type=Path)
parser.add_argument('--report',required=True,type=Path)
args=parser.parse_args()
dll=C.CDLL(str(args.bridge.resolve()))
dll.nsp_pipeline_create_live.argtypes=[C.c_uint32]*4+[C.c_double,C.c_char_p,C.c_char_p,C.c_uint32]
dll.nsp_pipeline_create_live.restype=C.c_void_p
dll.nsp_pipeline_submit.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t]
dll.nsp_pipeline_progress.argtypes=[C.c_void_p];dll.nsp_pipeline_progress.restype=C.c_uint64
dll.nsp_pipeline_finish.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];dll.nsp_pipeline_finish.restype=C.c_size_t
dll.nsp_pipeline_destroy.argtypes=[C.c_void_p]
dll.nsp_pipeline_telemetry_enable.argtypes=[C.c_void_p,C.c_uint32]
dll.nsp_pipeline_tap_read.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];dll.nsp_pipeline_tap_read.restype=C.c_size_t
Process=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint32,C.POINTER(C.c_float),C.c_uint32,C.c_uint32,C.POINTER(C.c_uint32))
Release=C.CFUNCTYPE(None,C.c_void_p)
Publish=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint64,C.POINTER(C.c_float),C.c_uint32,C.c_uint32,C.c_uint32,C.c_void_p,Release)
dll.nsp_pipeline_set_pool_output.argtypes=[C.c_void_p,Process,Publish,C.c_void_p]
checks=[]
with tempfile.TemporaryDirectory(prefix='recording-',dir=args.report.resolve().parent) as temp:
 for renderer in ('roar','lira'):
  for code,count in ((714,12),(916,16)):
   baseline=None
   for mode in ('off','on','file-conflict'):
    path=Path(temp)/(f'{renderer}-{code}-{mode}.f32')
    if mode=='file-conflict':path.write_bytes(b'preserve-existing-data')
    handle=dll.nsp_pipeline_create_live(480,32,code,int(renderer=='roar'),1.,str(args.lira.resolve()).encode() if renderer=='lira' else None,str(path).encode(),int(mode!='off'))
    assert handle
    raw=[];played=[];returned=[];first=[]
    @Process
    def process(_,op,pcm,capacity,channels,written):
     assert channels==count
     if op==0:
      raw.append(C.string_at(pcm,capacity*channels*4))
      for i in range(capacity*channels):pcm[i]*=.5
      written[0]=capacity
     else:written[0]=0
     return 0
    @Publish
    def publish(_,frame,pcm,frames,channels,tail,token,release):
     assert channels==count
     first.append(frame);played.append(C.string_at(pcm,frames*channels*4));release(token);returned.append(1);return 0
    try:
     assert dll.nsp_pipeline_set_pool_output(handle,process,publish,None)==0
     assert dll.nsp_pipeline_telemetry_enable(handle,1)==0
     for seq in range(8):
      pcm=[.02*math.sin((seq*480+i)*.03) for i in range(480)]
      packet=struct.pack('<4I4f',1,480,1,7,.3,.6,-.8,1.)+struct.pack('<480f',*pcm)
      assert dll.nsp_pipeline_submit(handle,seq,1,packet,len(packet))==0
     size=dll.nsp_pipeline_finish(handle,None,0);assert size
     report=C.create_string_buffer(size);dll.nsp_pipeline_finish(handle,report,size)
     result=json.loads(report.value);assert result['passed'],result
     assert dll.nsp_pipeline_progress(handle)==8*480*count*4
     tap=C.create_string_buffer(32+16*480*4)
     assert dll.nsp_pipeline_tap_read(handle,tap,len(tap))==32+count*480*4
     assert len(returned)==len(played) and first[0]==0
     if baseline is None:baseline=b''.join(played)
     else:assert baseline==b''.join(played),'Recording changed playback'
     rec=result['pcmRecording']
     if mode=='off':assert not path.exists() and not rec['enabled'] and rec['capacityBlocks']==0
     elif mode=='on':
      assert path.read_bytes()==b''.join(raw),'Recording must remain pre-DSP'
      assert rec['droppedBlocks']==0 and rec['error'] is None
     else:assert path.read_bytes()==b'preserve-existing-data' and rec['error'] and rec['droppedBlocks']>0
     checks.append(dict(renderer=renderer,layout=code,mode=mode,playbackFrames=len(baseline)//(count*4),recording=rec))
    finally:dll.nsp_pipeline_destroy(handle)
result=dict(passed=True,count=len(checks),checks=checks,audioDevicesOpened=False)
args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(dict(passed=True,count=len(checks))))
