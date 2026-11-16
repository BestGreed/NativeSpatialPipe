"""No devices: compare direct-pool LIRA against the pre-change bridge and tap wire."""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'build/lira-output-direct/runs'/str(time.time_ns())
RUN.mkdir(parents=True)
search=os.add_dll_directory(str(ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'))
LIRA=ROOT/'build/dev16-n10-renderer/standalone/NSP.SpatialRenderer.dll'
PATHS=[ROOT/'build/dev16-n12-closeout/nsp_croksor.dll',ROOT/'build/lira-output-direct/release/nsp_croksor.dll']

def load(path):
    lib=C.CDLL(str(path))
    lib.nsp_pipeline_create_spatial.argtypes=[C.c_uint32,C.c_uint32,C.c_char_p,C.c_char_p]
    lib.nsp_pipeline_create_spatial.restype=C.c_void_p
    lib.nsp_pipeline_create_with_position_mapping.argtypes=[C.c_uint32]*5+[C.c_double,C.c_char_p]
    lib.nsp_pipeline_create_with_position_mapping.restype=C.c_void_p
    lib.nsp_pipeline_submit.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t]
    lib.nsp_pipeline_finish.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t]
    lib.nsp_pipeline_finish.restype=C.c_size_t
    lib.nsp_pipeline_destroy.argtypes=[C.c_void_p]
    lib.nsp_pipeline_telemetry_enable.argtypes=[C.c_void_p,C.c_uint32]
    lib.nsp_pipeline_tap_read.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t]
    lib.nsp_pipeline_tap_read.restype=C.c_size_t
    return lib

libs=list(map(load,PATHS))
def packet(types,seq,n):
    props=b''.join(struct.pack('<4I4f',i+1,n,t,7,(-1)**i*.3,-.2 if seq%2 else .6,-.8,.5) for i,t in enumerate(types))
    return props+b''.join(struct.pack('<f',.01*math.sin((seq*n+f)*.03+c*.4)) for c in range(len(types)) for f in range(n))

def render(lib,index,name,types,n,lira,tap):
    path=RUN/f'{name}-{index}-{int(tap)}.f32'
    p=lib.nsp_pipeline_create_spatial(n,64,str(LIRA).encode(),str(path).encode()) if lira else lib.nsp_pipeline_create_with_position_mapping(n,64,0,0,1,1.,str(path).encode())
    assert p
    try:
        if tap:assert lib.nsp_pipeline_telemetry_enable(p,1)==0
        for seq in range(9):
            raw=packet(types,seq,n)
            assert lib.nsp_pipeline_submit(p,seq,len(types),raw,len(raw))==0
        size=lib.nsp_pipeline_finish(p,None,0)
        buf=C.create_string_buffer(size);lib.nsp_pipeline_finish(p,buf,size)
        report=json.loads(buf.value);assert report['passed'],report
        pcm=path.read_bytes();assert len(pcm)==(9*n+(512 if lira else 0))*12*4
        wires=[]
        if tap:
            for seq in range(9):
                wire=C.create_string_buffer(32+12*480*4)
                assert lib.nsp_pipeline_tap_read(p,wire,len(wire))==len(wire)
                assert struct.unpack_from('<Q',wire.raw,24)[0]==seq
                expected=b''.join(pcm[((seq*n+f)*12+c)*4:((seq*n+f)*12+c+1)*4] for c in range(12) for f in range(n))
                assert wire.raw[32:]==expected,(name,index,seq)
                wires.append(wire.raw)
            assert lib.nsp_pipeline_tap_read(p,wire,len(wire))==0
        return pcm,wires
    finally:lib.nsp_pipeline_destroy(p)

results=[]
for name,types,n,lira in [
    ('dynamic',[1,1],480,True),
    ('bed714',[2,4,8,16,32,64,128,256,512,1024,2048,4096],480,True),
    ('mixed',[1,2,4,8,16,32,64,128,256,512,1024,2048,4096],480,True),
    ('legacy-extra-types',[8192,16384,32768,65536,131072,262144,524288],480,True),
    ('short-block',[1],17,True),
    ('roar-regression',[1,2,4,8,16,32,64,128,256,512,1024,2048,4096],480,False),
]:
    for tap in ([False,True] if n==480 else [False]):
        before=render(libs[0],0,name,types,n,lira,tap)
        after=render(libs[1],1,name,types,n,lira,tap)
        assert before==after,(name,tap)
        results.append(dict(name=name,tap=tap,pcmAndTapByteExact=True,sha256=hashlib.sha256(after[0]).hexdigest()))
(RUN/'checks.json').write_text(json.dumps(dict(passed=True,cases=results,scope='LIRA pool output, tail, planar telemetry and ROAR regression; no ASIO or endpoints',bridgeHashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in PATHS]),indent=2),encoding='utf-8')
print(RUN/'checks.json')
