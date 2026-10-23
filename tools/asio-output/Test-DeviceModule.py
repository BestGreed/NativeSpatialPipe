"""Build and run the extracted device with a fake driver; never open real ASIO."""
import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, default=ROOT / 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin/clang++.exe')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/dev16-asio-device-step1')
    parser.add_argument('--host-abi', action='store_true', help='Also compile/run the Zig 0.13 offline C ABI consumer')
    args = parser.parse_args()
    compiler = args.compiler.resolve(strict=True)
    output = args.output.resolve()
    temporary = output / 'temp'
    temporary.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEMP=str(temporary), TMP=str(temporary))
    env['PATH'] = str(compiler.parent) + os.pathsep + env.get('PATH', '')
    flags = ['-std=c++20', '-O2', '-DUNICODE', '-D_UNICODE', '-DNOMINMAX',
             '-DSPATIAL_AUDIO_ASIO_TESTING', '-DSPATIALASIO_VERSION="dev16-module-test"',
             '-Imodules/audio-backends/src', '-Imodules/audio-backends/third_party/asio/common']
    device = ['modules/audio-backends/src/asio_backend.cpp', 'modules/audio-backends/src/asio_format.cpp',
              'modules/audio-backends/src/diagnostics.cpp', 'modules/audio-backends/src/sinc_resampler.cpp']
    libraries = ['-lole32', '-ladvapi32', '-luser32', '-luuid', '-lavrt', '-lwinmm']
    for name, source, adapter in (
        ('asio-device', 'modules/audio-backends/tests/asio_device_tests.cpp', []),
        ('asio-legacy', 'modules/audio-backends/tests/asio_runtime_tests.cpp', ['modules/audio-backends/src/asio_layout_adapter.cpp']),
    ):
        executable = output / (name + '.exe')
        subprocess.run([str(compiler), *flags, source, *device, *adapter, *libraries,
                        '-o', str(executable)], cwd=ROOT, env=env, check=True)
        subprocess.run([str(executable)], cwd=ROOT, env=env, check=True, timeout=30)
    # Check extracted real-driver/ABI and legacy adapter compatibility without
    # linking or starting a capture host, renderer or device.
    production = [flag for flag in flags if flag != '-DSPATIAL_AUDIO_ASIO_TESTING']
    subprocess.run([str(compiler), *production, '-DNSP_ASIO_STATIC', '-fsyntax-only',
                    'modules/audio-backends/src/asio_backend.cpp', 'modules/audio-backends/src/asio_layout_adapter.cpp',
                    'modules/audio-backends/src/asio_output_c.cpp'], cwd=ROOT, env=env, check=True)
    c_object = output / 'asio-c-header.o'
    subprocess.run([str(compiler), '-x', 'c', '-std=c11', '-Werror', '-DNSP_ASIO_STATIC',
                    '-Imodules/audio-backends/src', '-c', 'modules/audio-backends/tests/asio_c_header_test.c',
                    '-o', str(c_object)], cwd=ROOT, env=env, check=True)
    executable = output / 'asio-c-api.exe'
    subprocess.run([str(compiler), *flags, '-DNSP_ASIO_STATIC',
                    'modules/audio-backends/tests/asio_c_api_tests.cpp', str(c_object),
                    'modules/audio-backends/src/asio_output_c.cpp', *device, *libraries,
                    '-o', str(executable)], cwd=ROOT, env=env, check=True)
    subprocess.run([str(executable)], cwd=ROOT, env=env, check=True, timeout=30)
    dll = output / 'NSP.AsioOutput.dll'
    subprocess.run([str(compiler), *production, '-DNSP_ASIO_BUILD', '-shared',
                    'modules/audio-backends/src/asio_output_c.cpp', *device, *libraries,
                    '-o', str(dll)], cwd=ROOT, env=env, check=True)
    # The module may be moved out of this build directory. Keep its runtime
    # dependencies together instead of depending on the test compiler PATH.
    for runtime in ('libc++.dll', 'libunwind.dll'):
        shutil.copy2(compiler.parent / runtime, output / runtime)
    # Real-driver DLL export smoke only. Invalid config returns before COM or
    # driver creation; this deliberately does NOT open/enumerate any device.
    with os.add_dll_directory(str(compiler.parent)):
        library = ctypes.CDLL(str(dll))
        library.nsp_asio_abi_version.restype = ctypes.c_uint32
        assert library.nsp_asio_abi_version() == 1
        for name in ('start', 'submit', 'prime', 'service', 'finish_input', 'hold',
                     'get_stats', 'stop', 'close', 'last_error'):
            getattr(library, 'nsp_asio_' + name)
        header = (ctypes.c_uint32 * 2)(8, 1)
        handle = ctypes.c_void_p()
        library.nsp_asio_open.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        library.nsp_asio_open.restype = ctypes.c_int32
        assert library.nsp_asio_open(header, ctypes.byref(handle)) == -22 and not handle.value
        print('Production DLL: C exports/version/invalid-config smoke passed; no driver opened.')
    if args.host_abi:
        zig = ROOT / 'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe'
        host = output / 'asio-host-abi.exe'
        zig_env = dict(env, ZIG_GLOBAL_CACHE_DIR=str(output / 'zig-global'),
                       ZIG_LOCAL_CACHE_DIR=str(output / 'zig-cache'))
        subprocess.run([str(zig), 'build-exe', str(ROOT / 'tools/asio-output/Test-HostAbi.zig'),
                        '-target', 'x86_64-windows-gnu', '-O', 'ReleaseSafe', '-lc',
                        '-I', str(ROOT / 'modules/audio-backends/src'), '-femit-bin=' + str(host)],
                       cwd=ROOT, env=zig_env, check=True)
        subprocess.run([str(host), str(dll)], cwd=ROOT, env=env, check=True, timeout=15)


if __name__ == '__main__':
    main()
