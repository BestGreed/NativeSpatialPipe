"""N07: actual OLR -> public output -> owner worker -> fake callback recording."""
import json
import os
from pathlib import Path
import subprocess
import time
import argparse

ROOT=Path(__file__).absolute().parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('--bridge',type=Path,help='Reuse an already built ROAR bridge; no renderer rebuild')
parser.add_argument('--output',type=Path,default=ROOT/'build/dev16-n07-playback')
args=parser.parse_args()
OUT=args.output.resolve()
LLVM=ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
ZIG=ROOT/'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe'
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'checks.json').unlink(missing_ok=True)
env=dict(os.environ,PATH=str(LLVM)+os.pathsep+os.environ['PATH'],
         ZIG_GLOBAL_CACHE_DIR=str(OUT/'global'),ZIG_LOCAL_CACHE_DIR=str(OUT/'cache'))
def run(args):
    r=subprocess.run(list(map(str,args)),cwd=ROOT,env=env,capture_output=True,text=True,
                     creationflags=subprocess.CREATE_NO_WINDOW,timeout=90)
    print(r.stdout+r.stderr,end='',flush=True)
    with (OUT/'test.log').open('a',encoding='utf-8') as log:log.write(r.stdout+r.stderr)
    r.check_returncode()
bridge=args.bridge.resolve() if args.bridge else ROOT/'build/dev16-n07-roar/release/nsp_roar_bridge.dll'
if not args.bridge:
    run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',ROOT/'tools/roar-bridge/Build.ps1',
         '-TargetDirectory','build/dev16-n07-roar'])
if not bridge.is_file():raise FileNotFoundError(bridge)
dll=OUT/'NSP.AsioOutput.Fake.dll'
run([LLVM/'clang++.exe','-std=c++20','-O2','-DUNICODE','-D_UNICODE','-DNOMINMAX',
     '-DSPATIAL_AUDIO_ASIO_TESTING','-DSPATIALASIO_VERSION="n07-playback-test"','-DNSP_ASIO_BUILD','-shared',
     '-Imodules/audio-backends/src','-Imodules/audio-backends/tests','-Imodules/audio-backends/third_party/asio/common',
     *[ROOT/'modules/audio-backends'/p for p in ['tests/asio_playback_fixture.cpp','src/asio_output_c.cpp',
         'src/asio_backend.cpp','src/asio_format.cpp','src/diagnostics.cpp','src/sinc_resampler.cpp']],
     '-lole32','-ladvapi32','-luser32','-luuid','-lavrt','-lwinmm','-o',dll])
flags=['-target','x86_64-windows-gnu','-O','ReleaseSafe','-lc','-lws2_32','-ladvapi32',
       '-I',ROOT/'modules/audio-backends/src','-I',ROOT/'tools/endpoint-probe']
run([ZIG,'test',ROOT/'tools/zig-scheduler/output_stream_tests.zig','-target','x86_64-windows-gnu',
     '-O','ReleaseSafe','-femit-bin='+str(OUT/'stream-tests.exe')])
probe=OUT/'playback-probe.exe'
run([ZIG,'build-exe',ROOT/'tools/zig-scheduler/playback_probe.zig',*flags,'-femit-bin='+str(probe)])
run([ZIG,'build-exe',ROOT/'tools/zig-scheduler/main.zig',*flags,'-femit-bin='+str(OUT/'nsp-scheduler.exe')])
results=[]
for case in ['normal','tail','rebuffer','repeat','idle-gap','idle-start','device-fault','clock-sync','rebuffer-sync','idle-start-sync','idle-gap-sync','idle-stop','drain-stall']:
    directory=OUT/'runs'/str(time.time_ns())/case
    run([probe,bridge,dll,directory,case])
    results.append(json.loads((directory/'playback.json').read_text()))
(OUT/'checks.json').write_text(json.dumps(dict(passed=True,scope='Owned software fixtures only; no DVS or physical driver',cases=results),indent=2),encoding='utf-8')
print(f'N07: {len(results)} complete software playback cases passed; evidence: {OUT / "checks.json"}')
