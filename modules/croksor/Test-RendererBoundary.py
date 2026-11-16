"""Focused hierarchy correction check; existing N09/N10 development artifacts only.
No endpoint, driver, or production deployment. Build the bridge first.
"""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build/dev16-renderer-boundary'
RUN = OUT / str(time.time_ns())
RUN.mkdir(parents=True)
old_path = ROOT / 'build/dev16-n09-radius/release/nsp_croksor.dll'
new_path = ROOT / 'build/dev16-n10-renderer/release/nsp_croksor.dll'
lira_path = ROOT / 'build/dev16-n10-renderer/standalone/NSP.SpatialRenderer.dll'
search = os.add_dll_directory(str(ROOT / 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'))

def load(path):
    lib = C.CDLL(str(path))
    lib.nsp_pipeline_create_with_position_mapping.argtypes = [C.c_uint32]*5 + [C.c_double,C.c_char_p]
    lib.nsp_pipeline_create_with_position_mapping.restype = C.c_void_p
    lib.nsp_pipeline_submit.argtypes = [C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t]
    lib.nsp_pipeline_finish.argtypes = [C.c_void_p,C.c_void_p,C.c_size_t]
    lib.nsp_pipeline_finish.restype = C.c_size_t
    lib.nsp_pipeline_destroy.argtypes = [C.c_void_p]
    return lib

old, new = load(old_path), load(new_path)
new.nsp_pipeline_create_spatial.argtypes = [C.c_uint32,C.c_uint32,C.c_char_p,C.c_char_p]
new.nsp_pipeline_create_spatial.restype = C.c_void_p
def packet(seq, xyz):
    return struct.pack('<4I4f',1,480,1,7,*xyz,.5) + b''.join(
        struct.pack('<f',.01*math.sin((seq*480+f)*.03)) for f in range(480))
packets = [packet(i,xyz) for i,xyz in enumerate([(.3,.6,-.8),(2,1,-4),(.3,-1,-.8),(0,0,0)]*6)]
def render(lib, name, scaled=1, lira=False):
    path = RUN / (name+'.f32')
    handle = lib.nsp_pipeline_create_spatial(480,64,str(lira_path).encode(),str(path).encode()) if lira else lib.nsp_pipeline_create_with_position_mapping(480,64,0,0,scaled,1.,str(path).encode())
    assert handle, name
    try:
        for seq,raw in enumerate(packets):
            assert lib.nsp_pipeline_submit(handle,seq,1,raw,len(raw))==0
        size=lib.nsp_pipeline_finish(handle,None,0)
        assert size>0
        buf=C.create_string_buffer(size)
        lib.nsp_pipeline_finish(handle,buf,size)
        report=json.loads(buf.value)
        assert report['passed'],report
        if lib is new:
            assert (report['renderer'],report['rendererMode']) == (('lira',None) if lira else ('roar','olr'))
        (RUN/(name+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
        return path.read_bytes()
    finally:
        lib.nsp_pipeline_destroy(handle)

cases=[]
for scaled in [0,1]:
    before=render(old,'old-roar-olr-'+str(scaled),scaled)
    after=render(new,'roar-olr-'+str(scaled),scaled)
    assert before==after
    cases.append(dict(backend='roar',mode='olr',scaled=scaled,byteExact=True,sha256=hashlib.sha256(after).hexdigest()))
after=render(new,'lira',lira=True)
baseline=ROOT/'build/dev16-n10-renderer/runs/1792585873929348200/motion-and-room.f32'
assert after==baseline.read_bytes()
cases.append(dict(backend='lira',mode=None,byteExact=True,sha256=hashlib.sha256(after).hexdigest()))
(RUN/'checks.json').write_text(json.dumps(dict(passed=True,cases=cases,scope='Hierarchy and unchanged PCM; no hardware',bridgeSha256=hashlib.sha256(new_path.read_bytes()).hexdigest()),indent=2),encoding='utf-8')
print(RUN/'checks.json')
