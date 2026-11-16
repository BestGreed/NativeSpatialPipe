"""N10: original source vs extracted backend, shared N03 pipeline, fake ASIO."""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import time

BASELINE='b6d5858b682a88cdceb05b35a640722ea2bc02f3'
ROOT=Path(__file__).absolute().parents[2]
OUT=ROOT/'build/dev16-n10-renderer'
RUN=OUT/'runs'/str(time.time_ns());RUN.mkdir(parents=True)
LLVM=ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
ZIG=ROOT/'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe'
env=dict(os.environ,PATH=str(LLVM)+os.pathsep+os.environ['PATH'],ZIG_GLOBAL_CACHE_DIR=str(OUT/'global'),ZIG_LOCAL_CACHE_DIR=str(OUT/'cache'))
def run(args):
    r=subprocess.run(list(map(str,args)),cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,creationflags=subprocess.CREATE_NO_WINDOW)
    with (RUN/'test.log').open('a',encoding='utf-8') as log:log.write(r.stdout+r.stderr)
    print(r.stdout+r.stderr,end='',flush=True);r.check_returncode()
run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',ROOT/'modules/croksor/Build.ps1','-TargetDirectory','build/dev16-n10-renderer'])
flags=['-std=c++20','-O2','-DUNICODE','-D_UNICODE','-DNOMINMAX','-DNSP_SPATIAL_BUILD','-shared','-Imodules/audio-backends/src']
core=['native_scene.cpp','metadata_renderer.cpp','point_geometry.cpp','speaker_identity.cpp']
dll=OUT/'NSP.SpatialRenderer.dll'
run([LLVM/'clang++.exe',*flags,ROOT/'modules/audio-backends/src/spatial_renderer_c.cpp',*[ROOT/'modules/audio-backends/src'/s for s in core],'-o',dll])
# Compile pre-extraction scene/layout sources separately, never call extracted
# scene as the reference. Same compiler/FP flags and explicit speaker geometry.
legacy=OUT/'original';legacy.mkdir(exist_ok=True)
names=subprocess.check_output(['git','ls-tree','-r','--name-only',BASELINE,'--','SpatialASIO/src'],cwd=ROOT,text=True).splitlines()
for path in names:
    if path.endswith('.h') or Path(path).name in ['native_spatial.cpp','speaker_layout.cpp','metadata_renderer.cpp','point_geometry.cpp']:
        data=subprocess.check_output(['git','show',BASELINE+':'+path],cwd=ROOT)
        (legacy/Path(path).name).write_bytes(data)
baseline=OUT/'Original.SpatialRenderer.dll'
run([LLVM/'clang++.exe',*flags,'-I'+str(legacy),ROOT/'modules/audio-backends/src/spatial_renderer_c.cpp',
     *[legacy/s for s in ['native_spatial.cpp','speaker_layout.cpp','metadata_renderer.cpp','point_geometry.cpp']],
     ROOT/'modules/audio-backends/src/sinc_resampler.cpp','-o',baseline])
search=os.add_dll_directory(str(LLVM));os.environ['NSP_RENDER_MMCSS']='1'
def load(path):
    lib=C.CDLL(str(path));lib.nsp_spatial_create.restype=C.c_void_p
    lib.nsp_spatial_process.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_uint32,C.c_void_p,C.c_void_p,C.c_void_p]
    lib.nsp_spatial_flush.argtypes=[C.c_void_p,C.c_void_p,C.c_uint32]
    lib.nsp_spatial_destroy.argtypes=[C.c_void_p]
    return lib
original=load(baseline);extracted=load(dll)
bridge=C.CDLL(str(OUT/'release/nsp_croksor.dll'))
bridge.nsp_pipeline_create_spatial.argtypes=[C.c_uint32,C.c_uint32,C.c_char_p,C.c_char_p];bridge.nsp_pipeline_create_spatial.restype=C.c_void_p
bridge.nsp_pipeline_submit.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t]
bridge.nsp_pipeline_finish.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];bridge.nsp_pipeline_finish.restype=C.c_size_t
bridge.nsp_pipeline_destroy.argtypes=[C.c_void_p]
CALLBACK=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint64,C.POINTER(C.c_float),C.c_uint32,C.c_uint32)
bridge.nsp_pipeline_set_output_sink.argtypes=[C.c_void_p,CALLBACK,C.c_void_p]
def prop(ident,typ=1,flags=7,xyz=(.3,.6,-.8),gain=.5,n=480):return struct.pack('<4I4f',ident,n,typ,flags,*xyz,gain)
def packet(props,seq,n=480,silent=False):
    pcm=b''.join(struct.pack('<f',0. if silent else .01*math.sin((seq*n+f)*.03+c*.4)) for c in range(len(props)) for f in range(n))
    return b''.join(props)+pcm
cases={}
cases['motion-and-room']=[packet([prop(1,xyz=xyz)],i) for i,xyz in enumerate([(.3,.6,-.8),(2,1,-4),(.3,-1,-.8),(0,0,0)]*6)]
types=[2,4,8,16,32,64,128,256,512,1024,2048,4096,8192,16384,32768,65536,131072,262144,524288]
cases['beds-and-aliases']=[packet([prop(k+1,t) for k,t in enumerate(types)],i) for i in range(5)]
cases['identity-reorder']=[packet([prop(1,flags=7 if i==0 else 6),prop(2,flags=7 if i==0 else 6,xyz=(-.5,.1,.9))][::1 if i%2==0 else -1],i) for i in range(8)]
cases['parked-restart']=[packet([prop(1)],0),packet([prop(1,flags=6,xyz=(0,0,0),gain=0)],1,silent=True),packet([prop(1,flags=6,xyz=(-.8,.5,.4))],2),packet([prop(1,flags=8)],3)]
cases['collision-complete']=[packet([prop(1),prop(1,xyz=(-.8,.5,.4))],i) for i in range(5)]
cases['collision-unknown']=[packet([prop(1,flags=0),prop(1,flags=0)],i) for i in range(4)]
cases['unknown-state']=[packet([prop(1,flags=0)],i) for i in range(3)]
cases['delta-gain']=[packet([prop(1,flags=7 if i==0 else 2,gain=.1+i*.1)],i) for i in range(8)]
cases['short-input']=[packet([prop(1,n=17)],i,n=17) for i in range(9)]
results=[]
for name,packets in cases.items():
    n=17 if name=='short-input' else 480
    handles=[lib.nsp_spatial_create() for lib in [original,extracted]];assert all(handles)
    expected=[]
    try:
        for seq,data in enumerate(packets):
            count=len(data)//(32+4*n);values=[]
            for lib,h in zip([original,extracted],handles):
                output=C.create_string_buffer(n*12*4)
                assert lib.nsp_spatial_process(h,seq,count,n,C.c_char_p(data[:count*32]),C.c_char_p(data[count*32:]),output)==0
                values.append(output.raw)
            assert values[0]==values[1],(name,seq);expected.append(values[0])
        tails=[]
        for lib,h in zip([original,extracted],handles):
            output=C.create_string_buffer(512*12*4);assert lib.nsp_spatial_flush(h,output,512)==512;tails.append(output.raw)
            assert lib.nsp_spatial_flush(h,output,512)==-1
        assert tails[0]==tails[1];expected.append(tails[0])
    finally:
        for lib,h in zip([original,extracted],handles):lib.nsp_spatial_destroy(h)
    data=b''.join(expected);path=RUN/(name+'.f32');copied=[];first=0
    @CALLBACK
    def sink(ctx,at,pcm,frames,channels):
        global first
        if at!=first or not 0<frames<=n or channels!=12:return -22
        copied.append(C.string_at(pcm,frames*channels*4));first+=frames;return 0
    pipe=bridge.nsp_pipeline_create_spatial(n,64,str(dll).encode(),str(path).encode());assert pipe
    try:
        assert bridge.nsp_pipeline_set_output_sink(pipe,sink,None)==0
        for seq,raw in enumerate(packets):assert bridge.nsp_pipeline_submit(pipe,seq,len(raw)//(32+n*4),raw,len(raw))==0
        size=bridge.nsp_pipeline_finish(pipe,None,0);assert size>0
        summary=C.create_string_buffer(size);bridge.nsp_pipeline_finish(pipe,summary,size);report=json.loads(summary.value)
        assert report['passed'],report
        assert report['renderer']=='lira'
        assert report['mapping'] is None and report['decodeMs'] is None
        assert report['positionMapping']['mode']=='backend-room-projection'
        for task in ['rendererScheduling','outputHandoffScheduling']:
            assert report[task]['registered'] and report[task]['prioritySet'] and report[task]['reverted']
        assert report['rendererDelayFrames']==512 and report['rendererTailFrames']==512
        assert report['outputFrames']==len(packets)*n+512
        assert path.read_bytes()==data==b''.join(copied),name
        (RUN/(name+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    finally:bridge.nsp_pipeline_destroy(pipe)
    results.append(dict(name=name,frames=first,byteExact=True,sha256=hashlib.sha256(data).hexdigest()))
empty=extracted.nsp_spatial_create();out=C.create_string_buffer(512*12*4)
assert extracted.nsp_spatial_flush(empty,out,512)==0
extracted.nsp_spatial_destroy(empty)
# Strict sequence/invalid input failures stay terminal; never enable legacy recovery.
for kind in ['gap','nonfinite','negative-gain']:
    h=extracted.nsp_spatial_create();out=C.create_string_buffer(480*12*4)
    raw=packet([prop(1)],0);assert extracted.nsp_spatial_process(h,0,1,480,C.c_char_p(raw[:32]),C.c_char_p(raw[32:]),out)==0
    bad=packet([prop(1,gain=-1 if kind=='negative-gain' else .5)],1)
    if kind=='nonfinite':bad=bad[:32]+struct.pack('<f',float('nan'))+bad[36:]
    assert extracted.nsp_spatial_process(h,2 if kind=='gap' else 1,1,480,C.c_char_p(bad[:32]),C.c_char_p(bad[32:]),out)==-1
    assert extracted.nsp_spatial_flush(h,out,512)==-1
    extracted.nsp_spatial_destroy(h)
# Missing DLL fails before creating output.
missing=RUN/'missing.f32';assert not bridge.nsp_pipeline_create_spatial(480,8,str(OUT/'absent.dll').encode(),str(missing).encode());assert not missing.exists()
# The complete original engine/capture/GUI is archived outside this workspace.
# Only the extracted backend and pinned historical renderer reference are tested.
fake=OUT/'NSP.AsioOutput.Fake.dll'
run([LLVM/'clang++.exe','-std=c++20','-O2','-DUNICODE','-D_UNICODE','-DNOMINMAX','-DSPATIAL_AUDIO_ASIO_TESTING','-DSPATIALASIO_VERSION="n10-test"','-DNSP_ASIO_BUILD','-shared',
     '-Imodules/audio-backends/src','-Imodules/audio-backends/tests','-Imodules/audio-backends/third_party/asio/common',
     *[ROOT/'modules/audio-backends'/p for p in ['tests/asio_playback_fixture.cpp','src/asio_output_c.cpp','src/asio_backend.cpp','src/asio_format.cpp','src/diagnostics.cpp','src/sinc_resampler.cpp']],
     '-lole32','-ladvapi32','-luser32','-luuid','-lavrt','-lwinmm','-o',fake])
zflags=['-target','x86_64-windows-gnu','-O','ReleaseSafe','-lc','-lws2_32','-ladvapi32','-I',ROOT/'modules/audio-backends/src','-I',ROOT/'modules/native-capture']
run([ZIG,'test',ROOT/'modules/zig-scheduler/output_stream_tests.zig',*zflags,'-femit-bin='+str(OUT/'stream-tests.exe')])
probe=OUT/'playback-probe.exe';run([ZIG,'build-exe',ROOT/'modules/zig-scheduler/playback_probe.zig',*zflags,'-femit-bin='+str(probe)])
run([ZIG,'build-exe',ROOT/'modules/zig-scheduler/main.zig',*zflags,'-femit-bin='+str(OUT/'nsp-scheduler.exe')])
for case in ['normal','tail','rebuffer','clock-sync']:
    run([probe,OUT/'release/nsp_croksor.dll',fake,RUN/case,case,dll])
# Existing OLR playback with the changed tail-capable shared handoff.
run([probe,OUT/'release/nsp_croksor.dll',fake,RUN/'olr-regression','normal'])
(RUN/'checks.json').write_text(json.dumps(dict(passed=True,baseline=BASELINE,cases=results,invalidCases=3,softwarePlaybackCases=4,olrRegression=True,originalEngineBuild=False,existingNativeSuites=0,legacyChecksExcluded="Complete downstream engine archived; pinned renderer reference retained",scope='No hardware playback or GUI deployment'),indent=2),encoding='utf-8')
print('N10 evidence:',RUN/'checks.json',flush=True)
