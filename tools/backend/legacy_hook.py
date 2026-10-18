"""Read/stop the locally retained v1 controller; never start a legacy Hook."""
import ctypes as C
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def query(pid, stop=False):
    spec = importlib.util.spec_from_file_location('legacy_config_fields', ROOT/'tools/endpoint-probe/Native-Hook.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Config(C.Structure):
        _fields_ = module.Config._fields_[:next(i for i, field in enumerate(module.Config._fields_) if field[0]=='owner_pid')]
    config = Config()
    config.magic, config.size = 0x4e534831, C.sizeof(config)
    directory = ROOT/'build/hook-queries'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, suffix='.bin', delete=False) as stream:
        path = Path(stream.name)
        stream.write(bytes(config))
    try:
        result = subprocess.run([str(ROOT/'build/native-hook/nsp_hook_control.exe'), str(pid),
                                 str(ROOT/'build/native-hook/nsp_native_hook.dll'),
                                 'stop' if stop else 'status', str(path)], capture_output=True,
                                text=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout)
    finally:
        path.unlink(missing_ok=True)


def verify_stopped(pid):
    status = query(pid)
    if status.get('state') != 2 or status.get('activeOriginalCalls') != 0:
        raise RuntimeError('Legacy Hook has not completed stopping')
    report = query(pid, True)
    if not (report.get('hookBytesRestored') is True and report.get('captureLibraryReleased') is True
            and report.get('transportLibraryReleased') is True):
        raise RuntimeError('Legacy Hook has no confirmed restore/release evidence')
    return report


if __name__ == '__main__':
    import sys
    print(json.dumps(query(int(sys.argv[1]), '--stop' in sys.argv), indent=2))
