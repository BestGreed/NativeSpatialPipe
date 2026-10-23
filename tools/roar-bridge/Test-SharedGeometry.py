"""Four block contexts: exact PCM, shared geometry lifetime and independent histories."""
import ctypes as C
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'build/roar-shared-geometry-20261022'
spec=importlib.util.spec_from_file_location('startup',ROOT/'tools/endpoint-probe/Profile-RoarStartup.py')
startup=importlib.util.module_from_spec(spec);spec.loader.exec_module(startup)
spec=importlib.util.spec_from_file_location('audit',ROOT/'tools/endpoint-probe/Analyze-RoarKernel.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
packets, source=audit.packets();search=os.add_dll_directory(str(audit.LLVM))
diag=startup.load(OUT/'profile/startup.dll');reference=startup.load(OUT/'reference.dll')
diag.nsp_startup_geometry.argtypes=[C.c_void_p,C.c_void_p]
diag.nsp_startup_metrics.argtypes=[C.c_void_p]
info=(C.c_uint64*2)();metrics=(C.c_uint64*(len(startup.NAMES)*4))()
def geometry(h):
    diag.nsp_startup_geometry(h,info);return tuple(info)
handles=[];refs=[];pointers=[];checks=[]
try:
    for block in range(4):
        diag.nsp_startup_reset()
        h=diag.nsp_packet_create(480,48000,0,0,1,1.)
        r=reference.nsp_packet_create(480,48000,0,0,1,1.)
        assert h and r
        handles.append(h);refs.append(r)
        ptr,count=geometry(h);assert ptr and count==1
        diag.nsp_startup_metrics(metrics)
        assert metrics[startup.NAMES.index('gainGeometry')*4]==1
        pointers.append(ptr)
    assert len(set(pointers))==4
    for seq in range(5):
        for block,(h,r) in enumerate(zip(handles,refs)):
            if not h:continue
            packet=bytearray(packets[seq])
            dynamics=[i for i in range(140) if struct.unpack_from('<I',packet,i*32+8)[0]==1]
            if seq==1:
                for i in dynamics:
                    flags=struct.unpack_from('<I',packet,i*32+12)[0]
                    struct.pack_into('<I',packet,i*32+12,flags|4)
                    struct.pack_into('<3f',packet,i*32+16,.1*(block+1),.2,-.8)
            if seq==2:
                i=dynamics[0];flags=struct.unpack_from('<I',packet,i*32+12)[0]
                struct.pack_into('<I',packet,i*32+12,flags|5)
            output=[(C.c_float*(480*12))() for _ in range(2)]
            diag.nsp_startup_reset()
            for lib,engine,out in zip([diag,reference],[h,r],output):
                assert lib.nsp_packet_render(engine,seq,140,bytes(packet),len(packet),out,480*12)==0
            assert bytes(output[0])==bytes(output[1]),(seq,block)
            ptr,count=geometry(h);assert ptr==pointers[block] and count==257,(seq,block,count)
            diag.nsp_startup_metrics(metrics);assert metrics[startup.NAMES.index('gainGeometry')*4]==0
            checks.append(dict(block=block,sequence=seq,geometryReferences=count,geometryBuilds=0,pcmSha256=hashlib.sha256(bytes(output[0])).hexdigest()))
        if seq==2:
            diag.nsp_packet_destroy(handles[0]);reference.nsp_packet_destroy(refs[0])
            handles[0]=refs[0]=None
    assert len(checks)==18
finally:
    for lib,values in [(diag,handles),(reference,refs)]:
        for h in values:
            if h:lib.nsp_packet_destroy(h)
result=dict(passed=True,source=source,contexts=4,initialGeometryBuilds=4,checks=checks,
            note='Diagnostic-only geometry introspection; 257 refs = context +128 object renderers +128 channel interpreters. Object replacement reuses geometry; closing one context leaves other three rendering bit-identically. No new production diagnostics ABI.')
(OUT/'four-contexts.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print('Four contexts / four builds; 18 PCM/lifetime checks passed; no per-packet or replacement geometry rebuilds')
