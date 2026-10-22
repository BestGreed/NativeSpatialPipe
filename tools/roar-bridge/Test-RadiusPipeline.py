"""N09 reliable threaded sink/file output must match the synchronous OLR engine."""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
ROOT=Path(__file__).absolute().parents[2]
sys.path.insert(0,str(ROOT/'work/consumer-deps'))
import numpy as np
OUT=ROOT/'build/dev16-n09-radius'
RUN=OUT/'pipeline-runs'/str(time.time_ns());RUN.mkdir(parents=True)
subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/roar-bridge/Build.ps1'),
    '-TargetDirectory','build/dev16-n09-radius'],cwd=ROOT,check=True,timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
search=os.add_dll_directory(str(ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'))
os.environ['NSP_RENDER_MMCSS']='1'
lib=C.CDLL(str(OUT/'release/nsp_roar_bridge.dll'))
lib.nsp_packet_create.argtypes=[C.c_uint32]*5+[C.c_double];lib.nsp_packet_create.restype=C.c_void_p
lib.nsp_packet_render.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t,C.POINTER(C.c_float),C.c_size_t]
lib.nsp_packet_destroy.argtypes=[C.c_void_p]
lib.nsp_pipeline_create.argtypes=[C.c_uint32]*4+[C.c_char_p];lib.nsp_pipeline_create.restype=C.c_void_p
lib.nsp_pipeline_create_with_position_mapping.argtypes=[C.c_uint32]*5+[C.c_double,C.c_char_p]
lib.nsp_pipeline_create_with_position_mapping.restype=C.c_void_p
lib.nsp_pipeline_submit.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t]
lib.nsp_pipeline_finish.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];lib.nsp_pipeline_finish.restype=C.c_size_t
lib.nsp_pipeline_destroy.argtypes=[C.c_void_p]
CALLBACK=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint64,C.POINTER(C.c_float),C.c_uint32,C.c_uint32)
lib.nsp_pipeline_set_output_sink.argtypes=[C.c_void_p,CALLBACK,C.c_void_p]
results=[]
def run(name,positions,scaled,reference=1.,legacy=False):
    path=RUN/(name+'.f32');copies=[];errors=[]
    @CALLBACK
    def sink(context,first,pcm,frames,channels):
        if first!=len(copies)*480 or frames!=480 or channels!=12:
            errors.append('sink format/continuity');return -22
        copies.append(C.string_at(pcm,frames*channels*4));return 0
    pipe=lib.nsp_pipeline_create(480,64,0,0,str(path).encode()) if legacy else lib.nsp_pipeline_create_with_position_mapping(480,64,0,0,int(scaled),reference,str(path).encode())
    engine=lib.nsp_packet_create(480,48000,0,0,int(scaled),reference)
    assert pipe and engine,name
    expected=[]
    try:
        assert lib.nsp_pipeline_set_output_sink(pipe,sink,None)==0
        for seq,xyz in enumerate(positions):
            pcm=np.array([.01*math.sin((seq*480+i)*.03) for i in range(480)],dtype='<f4')
            packet=struct.pack('<4I4f',1,480,1,5 if seq==0 else 4,*xyz,.25)+pcm.tobytes()
            buf=(C.c_float*(480*12))()
            assert lib.nsp_packet_render(engine,seq,1,packet,len(packet),buf,len(buf))==0
            expected.append(np.ctypeslib.as_array(buf).reshape(12,480).T.copy().tobytes())
            assert lib.nsp_pipeline_submit(pipe,seq,1,packet,len(packet))==0,name
        size=lib.nsp_pipeline_finish(pipe,None,0);assert 0<size<1024*1024
        report=C.create_string_buffer(size);assert lib.nsp_pipeline_finish(pipe,report,size)==size
        summary=json.loads(report.value)
        (RUN/(name+'.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
        assert summary['passed'] and not errors,(name,summary)
        mapping=summary['positionMapping']
        assert mapping['mode']==('scaled' if scaled else 'direction') and mapping['reference']==reference
        assert summary['rendererScheduling']['registered'] and summary['rendererScheduling']['reverted']
        assert summary['outputHandoffScheduling']['registered'] and summary['outputHandoffScheduling']['reverted']
        data=b''.join(expected)
        assert b''.join(copies)==data and path.read_bytes()==data,name
        results.append(dict(name=name,frames=len(positions)*480,byteExact=True,
                            sha256=hashlib.sha256(data).hexdigest(),positionMapping=mapping))
        return data
    finally:
        lib.nsp_packet_destroy(engine);lib.nsp_pipeline_destroy(pipe)
radial=[(.6*r,0,-.8*r) for r in [.125,.25,.5,.9,1,4]*4]
old=run('legacy-direction',radial,False,legacy=True)
assert old==run('explicit-direction',radial,False)
assert old!=run('raw-radius',radial,True)
run('reference10',radial,True,10.)
run('negative-height',[(.6,-.5,-.8)]*24,True)
run('large-radius',[(76.8,0,-102.4)]*24,True)
run('origin',[(0,0,0)]*24,True)
for index,value in enumerate([0.,-1.,float('nan'),float('inf')]):
    path=RUN/f'invalid-{index}.f32'
    assert not lib.nsp_pipeline_create_with_position_mapping(480,64,0,0,1,value,str(path).encode())
    assert not path.exists(),'invalid config created output file'
for name,mode,reference in [('bad-mode',2,1.),('ignored-reference',0,10.)]:
    path=RUN/(name+'.f32')
    assert not lib.nsp_pipeline_create_with_position_mapping(480,64,0,0,mode,reference,str(path).encode())
    assert not path.exists()
zig=ROOT/'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe'
probe=OUT/'radius-host-probe.exe'
env=dict(os.environ,ZIG_GLOBAL_CACHE_DIR=str(OUT/'zig-global'),ZIG_LOCAL_CACHE_DIR=str(OUT/'zig-cache'))
compiled=subprocess.run([str(zig),'build-exe',str(ROOT/'tools/zig-scheduler/radius_host_probe.zig'),
    '-target','x86_64-windows-gnu','-O','ReleaseSafe','-lc','-lws2_32','-ladvapi32',
    '-I',str(ROOT/'modules/audio-backends/src'),'-I',str(ROOT/'tools/endpoint-probe'),'-femit-bin='+str(probe)],
    cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
(RUN/'zig-build.log').write_text(compiled.stdout+compiled.stderr,encoding='utf-8')
print(compiled.stdout+compiled.stderr,end='',flush=True)
compiled.check_returncode()
env['PATH']=str(ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin')+os.pathsep+env['PATH']
for mode,reference in [('direction',1),('scaled',1),('scaled',10)]:
    checked=subprocess.run([str(probe),str(OUT/'release/nsp_roar_bridge.dll'),str(RUN/f'zig-{mode}-{reference}.f32'),mode,str(reference)],
        cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    (RUN/f'zig-{mode}-{reference}.log').write_text(checked.stdout+checked.stderr,encoding='utf-8')
    print(checked.stdout+checked.stderr,end='',flush=True)
    checked.check_returncode()
report=dict(passed=True,scope='Actual threaded pipeline -> reliable borrowed sink/file, byte-exact synchronous reference; no device',
            cases=results,zigHostCases=3,invalidReferencesRejected=4,invalidPoliciesRejected=2,legacyCreateKeepsDirection=True,
            hostDefault='scaled/reference=1, software unit-preserving mapping')
(RUN/'checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(dict(passed=True,cases=len(results),evidence=str(RUN)),indent=2))
