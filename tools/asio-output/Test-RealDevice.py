"""N08 opt-in real ASIO probe/playback. Never edits endpoint or Dante routing."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).absolute().parents[2]
OUT = ROOT / 'build/dev16-n08-device'
LLVM = ROOT / 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
ZIG = ROOT / 'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe'
parser = argparse.ArgumentParser()
parser.add_argument('--driver', required=True)
parser.add_argument('--play-silence', action='store_true')
parser.add_argument('--build-only', action='store_true')
parser.add_argument('--seconds', type=int, default=3, choices=range(3,121), metavar='3..120')
parser.add_argument('--case', choices=['both','normal','rebuffer'], default='both')
parser.add_argument('--network-return', action='store_true', help='Test-only duplex capture, outputs 1..12 to inputs 25..36; low-level tones')
parser.add_argument('--output-order', choices=['olr','dvs-speakers'], default=None)
args = parser.parse_args()
OUT.mkdir(parents=True, exist_ok=True)
run_dir = OUT / 'runs' / str(time.time_ns())
run_dir.mkdir(parents=True)
env = dict(os.environ, PATH=str(LLVM) + os.pathsep + os.environ['PATH'],
           ZIG_GLOBAL_CACHE_DIR=str(OUT/'global'), ZIG_LOCAL_CACHE_DIR=str(OUT/'cache'))

def run(command, name, timeout=90):
    result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (run_dir / (name + '.stdout.txt')).write_text(result.stdout, encoding='utf-8')
    (run_dir / (name + '.stderr.txt')).write_text(result.stderr, encoding='utf-8')
    print(result.stdout + result.stderr, end='', flush=True)
    result.check_returncode()
    return result.stdout

probe = OUT/'probe-driver.exe'
run([LLVM/'clang++.exe', '-std=c++20', '-O2', '-DUNICODE', '-D_UNICODE', '-DNOMINMAX',
     '-DSPATIALASIO_VERSION="n08-probe"', '-Imodules/audio-backends/src',
     '-Imodules/audio-backends/third_party/asio/common', ROOT/'tools/asio-output/Probe-Driver.cpp',
     *[ROOT/'modules/audio-backends/src'/p for p in ['asio_backend.cpp','asio_format.cpp',
                                        'diagnostics.cpp','sinc_resampler.cpp']],
     '-lole32','-ladvapi32','-luser32','-luuid','-municode','-o',probe], 'build-probe')
dll = OUT/('NSP.AsioOutput.NetworkTest.dll' if args.network_return else 'NSP.AsioOutput.dll')
player = OUT/'real-device-probe.exe'
if args.play_silence or args.network_return or args.build_only:
    run([LLVM/'clang++.exe','-std=c++20','-O2','-DUNICODE','-D_UNICODE','-DNOMINMAX',
         '-DSPATIALASIO_VERSION="n08-device"','-DNSP_ASIO_BUILD','-shared',
         *(['-DNSP_ASIO_NETWORK_RETURN_TEST'] if args.network_return else []),
         '-Imodules/audio-backends/src','-Imodules/audio-backends/third_party/asio/common',
         *[ROOT/'modules/audio-backends/src'/p for p in ['asio_output_c.cpp','asio_backend.cpp',
                                            'asio_format.cpp','diagnostics.cpp','sinc_resampler.cpp']],
         *([ROOT/'modules/audio-backends/tests/asio_network_return.cpp'] if args.network_return else []),
         '-lole32','-ladvapi32','-luser32','-luuid','-lavrt','-lwinmm','-o',dll], 'build-device')
    run([ZIG,'build-exe',ROOT/'tools/zig-scheduler/real_device_probe.zig',
         '-target','x86_64-windows-gnu','-O','ReleaseSafe','-lc','-lws2_32','-ladvapi32','-lavrt',
         '-I',ROOT/'modules/audio-backends/src','-I',ROOT/'tools/endpoint-probe',
         '-femit-bin='+str(player)], 'build-player')
if not args.build_only:
    try:
        capabilities = json.loads(run([probe, args.driver], 'capabilities'))
        (run_dir/'capabilities.json').write_text(json.dumps(capabilities, indent=2), encoding='utf-8')
        if args.play_silence or args.network_return:
            if capabilities['outputChannels'] < 12:
                raise RuntimeError('Selected driver has fewer than 12 outputs')
            if args.network_return and (capabilities['inputChannels'] < 36 or capabilities['sampleRate'] != 48000):
                raise RuntimeError('Network test needs inputs 25..36 and current rate 48000; configuration is not changed')
            for case in (['network-return'] if args.network_return else ['normal','rebuffer'] if args.case == 'both' else [args.case]):
                directory = run_dir/case
                directory.mkdir()
                run([player,ROOT/'build/dev16-n07-roar/release/nsp_roar_bridge.dll',
                     dll,args.driver,directory,str(capabilities['sampleRate']),case,str(9 if args.network_return else args.seconds),
                     args.output_order or ('dvs-speakers' if args.network_return else 'olr')],
                    case, timeout=args.seconds + 45)
                if args.network_return:
                    run([os.sys.executable, ROOT/'tools/asio-output/Analyze-NetworkReturn.py', directory], 'analysis')
        (run_dir/'result.json').write_text(json.dumps(dict(passed=True,
            scope='Real network return' if args.network_return else 'Real silent playback' if args.play_silence else 'Capabilities only',
            driver=args.driver),indent=2),encoding='utf-8')
    except Exception as error:
        (run_dir/'result.json').write_text(json.dumps(dict(passed=False,
            driver=args.driver,error=str(error)),indent=2),encoding='utf-8')
        print('Evidence: '+str(run_dir),flush=True)
        raise
print('Evidence: ' + str(run_dir), flush=True)
