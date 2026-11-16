"""Offline comparison of the original bridge and Croksor; no device or capture."""
import ctypes as C
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).absolute().parents[2]
OUT = ROOT / 'build/dev16-input-adapter'
LLVM = ROOT / 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
RUST = ROOT / 'work/rust/rustup/toolchains/1.97.1-x86_64-pc-windows-gnullvm/bin'
env = dict(os.environ, CARGO_HOME=str(ROOT / 'work/rust/cargo'),
           RUSTUP_HOME=str(ROOT / 'work/rust/rustup'), PATH=str(LLVM)+os.pathsep+os.environ['PATH'])
FLAGS = dict(check=True, timeout=90, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
OUT.mkdir(parents=True, exist_ok=True)
test_root=OUT/'input-tests.rs'
inputs = {'capture_input': 'input/capture_input.rs', 'olr_input': 'render/olr_input.rs'}
test_root.write_text(''.join(f'#[path = "{(ROOT / "modules/croksor/src" / path).as_posix()}"]\nmod {name};\n'
                            for name, path in inputs.items()),encoding='utf-8')
subprocess.run([str(RUST/'rustc.exe'), '--edition=2024', '--test',
                str(test_root), '-o', str(OUT/'input-tests.exe')], **FLAGS)
subprocess.run([str(OUT/'input-tests.exe')], **FLAGS)

# Build actual pre-change source with the same dependency tree/compiler/profile.
baseline = OUT/'baseline-source'
(baseline/'src').mkdir(parents=True, exist_ok=True)
for name in ('src/lib.rs','src/packet.rs','src/raw_tap.rs','Cargo.toml','Cargo.lock'):
    data = subprocess.check_output(['git','show','1dac6f5:tools/roar-bridge/'+name], creationflags=subprocess.CREATE_NO_WINDOW)
    if name=='Cargo.toml':
        data=data.replace(b'../../work/roar', (ROOT/'work/roar').as_posix().encode())
    (baseline/name).write_bytes(data)
baseline_target=OUT/'baseline-build'
subprocess.run([str(ROOT/'work/rust/cargo/bin/cargo.exe'), '+1.97.1-x86_64-pc-windows-gnullvm',
                'build','--release','--locked','--offline','--manifest-path',str(baseline/'Cargo.toml'),
                '--target-dir',str(baseline_target)], **FLAGS)
directories=[os.add_dll_directory(str(LLVM))]
paths=[baseline_target/'release/nsp_roar_bridge.dll', OUT/'release/nsp_croksor.dll']
libs=[]
for path in paths:
    lib=C.CDLL(str(path))
    lib.nsp_packet_create.argtypes=[C.c_uint32]*5+[C.c_double];lib.nsp_packet_create.restype=C.c_void_p
    lib.nsp_packet_render.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t,C.POINTER(C.c_float),C.c_size_t]
    lib.nsp_packet_destroy.argtypes=[C.c_void_p]
    libs.append(lib)
bed=[2,4,8,16,32,64,128,256,512,1024,2048,4096]

def packet(seq, count=14, flags=None, silent=False):
    types=[1]*(count-12)+bed if count>=12 else [1]*count
    flag=flags if flags is not None else (5 if seq==0 else 4 if seq%3==0 else 0)
    props=b''.join(struct.pack('<4I4f',i+1,480,t,flag,3+seq*.01,-4.,12.,.25) for i,t in enumerate(types))
    pcm=b''.join(struct.pack('<480f',*[0. if silent else math.sin(j*.03+i)*.001 for j in range(480)]) for i in range(count))
    return props+pcm

checks=[]
def compare(name, packets, mode=0, gain=0, scaled=0, expect_error=False):
    handles=[lib.nsp_packet_create(480,48000,mode,gain,scaled,2.) for lib in libs]
    assert all(handles)
    digest=hashlib.sha256();last=0
    try:
        for seq,count,data in packets:
            outputs=[(C.c_float*(12*480))() for _ in libs]
            codes=[lib.nsp_packet_render(h,seq,count,data,len(data),out,len(out)) for lib,h,out in zip(libs,handles,outputs)]
            assert codes[0]==codes[1],(name,codes)
            last=codes[0]
            if last:
                assert expect_error,name
                break
            assert bytes(outputs[0])==bytes(outputs[1]),name
            digest.update(bytes(outputs[0]))
        assert bool(last)==expect_error,name
        checks.append(dict(name=name,byteIdentical=True,status=last,sha256=digest.hexdigest()))
    finally:
        for lib,h in zip(libs,handles):lib.nsp_packet_destroy(h)

for mode,gain,scaled in itertools.product((0,1),repeat=3):
    compare(f'policy-{mode}-{gain}-{scaled}',[(i,14,packet(i)) for i in range(12)],mode,gain,scaled)
compare('160-slots',[(0,160,packet(0,160))])
compare('unknown-silent',[(0,1,packet(0,1,flags=0,silent=True))])
compare('unknown-audible',[(0,1,packet(0,1,flags=0))],expect_error=True)
compare('sequence-gap',[(0,14,packet(0)),(2,14,packet(2))],expect_error=True)
compare('retire-and-recreate',[(i,14,packet(i,flags=12 if i==3 else 5 if i==4 else 4)) for i in range(8)])
changed=packet(1,1,flags=0)
changed=struct.pack('<I',99)+changed[4:]
compare('identity-reset-unknown',[(0,1,packet(0,1)),(1,1,changed)],expect_error=True)
data=packet(0,1)
for name,bad in [('length',data[:-1]),('pcm-nan',data[:32]+struct.pack('<f',float('nan'))+data[36:]),
                 ('unsupported-type',data[:8]+struct.pack('<I',0x80000000)+data[12:])]:
    compare(name,[(0,1,bad)],expect_error=True)
report=dict(passed=True,baseline='HEAD',baselineDllSha256=hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            workingDllSha256=hashlib.sha256(paths[1].read_bytes()).hexdigest(),checks=checks,
            scope='Offline raw decoder tests and same-source OLR comparison; no ASIO or capture')
(OUT/'input-checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(f'Passed {len(checks)} OLR before/after comparisons; evidence: {OUT / "input-checks.json"}')
