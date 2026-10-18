"""Compare isolated idle FSA status calls; not an audio latency/CPU benchmark."""
import ctypes as C
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
kernel = C.WinDLL('kernel32', use_last_error=True)
kernel.GetProcessTimes.argtypes = [C.c_void_p] + [C.POINTER(C.c_uint64)] * 4


def sample(command):
    started = time.perf_counter()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=subprocess.CREATE_NO_WINDOW) as child:
        stdout, stderr = child.communicate(timeout=10)
        elapsed = time.perf_counter() - started
        if child.returncode != 10:
            raise RuntimeError((child.returncode, stderr.decode(errors='replace')))
        value = json.loads(stdout)
        if value['code'] != 10:
            raise RuntimeError('Expected confirmed idle status')
        fields = [C.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(int(child._handle), *[C.byref(v) for v in fields]):
            raise C.WinError(C.get_last_error())
        return elapsed * 1000, (fields[2].value + fields[3].value) / 10000


def main():
    report = dict(scope='Short-lived idle FSA status adapter only; no audio or live state',
                  iterations=20, results={})
    with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-control-ab-') as temp:
        for role, command in (
            ('python', [sys.executable, str(ROOT / 'tools/backend/fsa_control.py'), 'status']),
            ('zig', [str(ROOT / 'build/zig-scheduler/nsp-scheduler.exe'), 'status', '--root', str(ROOT)]),
        ):
            command += ['--home', str(Path(temp) / role), '--request-id', 'benchmark']
            for _ in range(3):
                sample(command)
            rows = [sample(command) for _ in range(report['iterations'])]
            wall = sorted(row[0] for row in rows)
            report['results'][role] = dict(wallMedianMs=statistics.median(wall), wallP95Ms=wall[18],
                                           cpuMeanMs=statistics.mean(row[1] for row in rows))
    output = ROOT / 'build/zig-scheduler/control-ab.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
