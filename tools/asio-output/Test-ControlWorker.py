"""N05 only: fake driver DLL + actual Zig owner-thread adapter; no real ASIO."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).absolute().parents[2]
OUT = ROOT / 'build/dev16-n05-control'
LLVM = ROOT / 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
ZIG = ROOT / 'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe'
OUT.mkdir(parents=True, exist_ok=True)
(OUT/'checks.json').unlink(missing_ok=True)
(OUT/'test.log').write_text('',encoding='utf-8')
env = dict(os.environ, PATH=str(LLVM)+os.pathsep+os.environ['PATH'],
           ZIG_GLOBAL_CACHE_DIR=str(OUT/'global'), ZIG_LOCAL_CACHE_DIR=str(OUT/'cache'))
fixture = OUT/'fake-driver.cpp'
fixture.write_text('#include "asio_test_driver.h"\n', encoding='utf-8')
dll = OUT/'NSP.AsioOutput.Fake.dll'
def run(args):
    result = subprocess.run(list(map(str,args)),cwd=ROOT,env=env,capture_output=True,text=True,timeout=90,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    print(result.stdout + result.stderr, end='', flush=True)
    with (OUT/'test.log').open('a',encoding='utf-8') as log:
        log.write(result.stdout + result.stderr)
    result.check_returncode()
    return result
run([LLVM/'clang++.exe','-std=c++20','-O2','-DUNICODE','-D_UNICODE','-DNOMINMAX',
     '-DSPATIAL_AUDIO_ASIO_TESTING','-DSPATIALASIO_VERSION="n05-control-test"',
     '-DNSP_ASIO_BUILD','-shared','-Imodules/audio-backends/src','-Imodules/audio-backends/tests',
     '-Imodules/audio-backends/third_party/asio/common',fixture,
     *[ROOT/'modules/audio-backends/src'/name for name in ('asio_output_c.cpp','asio_backend.cpp','asio_format.cpp','diagnostics.cpp','sinc_resampler.cpp')],
     '-lole32','-ladvapi32','-luser32','-luuid','-lavrt','-lwinmm','-o',dll])
env['NSP_ASIO_CONTROL_TEST_DLL']=str(dll)
run([ZIG,'test',ROOT/'tools/zig-scheduler/output_control_tests.zig','-target','x86_64-windows-gnu',
     '-O','ReleaseSafe','-lc','-I',ROOT/'modules/audio-backends/src','-femit-bin='+str(OUT/'control-tests.exe')])
(OUT/'checks.json').write_text(json.dumps(dict(passed=True,tests=7,
    scope='Owned mock backend and fake ASIO DLL only; no capture, endpoint, FSA or real driver',
    implemented='Dedicated lifecycle worker and actual C ABI adapter; PCM/service scheduling remains N07'),indent=2),encoding='utf-8')
