"""Query a verified target instance; never infer Hook state from file existence."""
import ctypes as C
import importlib.util
import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
from windows_privileges import enable_debug

ROOT = Path(__file__).resolve().parents[2]


def preflight():
    """Read-only check before FSA changes Sonic; same rights as the capture host."""
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                             'Get-Process audiodg -ErrorAction Stop | Select-Object -ExpandProperty Id'],
                            capture_output=True, text=True, check=True, timeout=5,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    pids = [int(value) for value in result.stdout.split()]
    if len(pids) != 1:
        raise RuntimeError('Exactly one audio target is required')
    pid = pids[0]
    created = identity(pid)
    if created is None:
        raise RuntimeError('Audio target exited during preflight')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
    kernel.OpenProcess.restype = C.c_void_p
    kernel.CloseHandle.argtypes = [C.c_void_p]
    kernel.GetProcessInformation.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32]
    handle = kernel.OpenProcess(0x43a, False, pid)
    if not handle:
        raise C.WinError(C.get_last_error())
    try:
        level = C.c_uint32()
        if not kernel.GetProcessInformation(handle, 7, C.byref(level), 4) or level.value != 0xfffffffe:
            raise RuntimeError('Protected or unverified audio target')
    finally:
        kernel.CloseHandle(handle)
    spec = importlib.util.spec_from_file_location('native_preflight', ROOT / 'tools/endpoint-probe/Native-Hook.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = [name.lower() for name in module.modules(pid)]
    if 'nsp_native_hook.dll' in names:
        from legacy_hook import verify_stopped
        verify_stopped(pid)
    for path in (module.HOOK_DLL, module.HOOK_DIR/'nsp_hook_control.exe',
                 ROOT/'build/native-receiver-routing/release/nsp_native_receiver.dll',
                 ROOT/'build/telemetry-objects/release/nsp_roar_bridge.dll',
                 ROOT/'build/capture-scheduler-mmcss/nsp_native_capture.dll'):
        if not path.is_file():
            raise RuntimeError('Missing runtime dependency: '+str(path))
    import os
    image = Path(os.environ['SystemRoot'])/'System32/audiodg.exe'
    if hashlib.sha256(image.read_bytes()).hexdigest() != '3dd24fe6faf0b56f99d5b00f24687b5f3b3e76e15b219e23f0963114a0dce9a5':
        raise RuntimeError('Audio target build mismatch')
    return dict(pid=pid, created=created)


def identity(pid):
    enable_debug()
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
    kernel.OpenProcess.restype = C.c_void_p
    kernel.GetProcessTimes.argtypes = [C.c_void_p] + [C.POINTER(C.c_uint64)] * 4
    kernel.CloseHandle.argtypes = [C.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        error = C.get_last_error()
        if error == 87:
            return None
        raise C.WinError(error)
    try:
        fields = [C.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[C.byref(v) for v in fields]):
            raise C.WinError(C.get_last_error())
        return fields[0].value
    finally:
        kernel.CloseHandle(handle)


def inspect(marker, action='status'):
    current = identity(marker['pid'])
    if current is None or current != marker['created']:
        return dict(state=2, hookEnabled=False, hookBytesRestored=None, targetExited=True)
    spec = importlib.util.spec_from_file_location('native_hook_config', ROOT / 'tools/endpoint-probe/Native-Hook.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generation = marker.get('hookGeneration', 1) if action != 'baseline' else 2
    config_type = module.Config
    if generation == 1:
        class V1Config(C.Structure):
            _fields_ = module.Config._fields_[:next(i for i, field in enumerate(module.Config._fields_) if field[0]=='owner_handle')]
        config_type = V1Config
    config = config_type()
    config.magic, config.size = 0x4e534831, C.sizeof(config)
    config.target_created = marker['created']
    directory = ROOT / 'build/hook-queries'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, suffix='.bin', delete=False) as stream:
        path = Path(stream.name)
        stream.write(bytes(config))
    try:
        folder = ROOT / ('build/native-hook-managed' if generation == 1 else 'build/native-hook-managed-v2')
        dll = 'nsp_managed_hook.dll' if generation == 1 else 'nsp_managed_hook_v2.dll'
        result = subprocess.run([str(folder / 'nsp_hook_control.exe'), str(marker['pid']),
                                 str(folder / dll), action, str(path)],
                                capture_output=True, text=True, timeout=40,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        value = json.loads(result.stdout)
        if action=='status' and value.get('state')==0:
            value['inactiveVerified'] = inspect(marker,'baseline').get('baselineVerified') is True
        return value
    finally:
        path.unlink(missing_ok=True)
