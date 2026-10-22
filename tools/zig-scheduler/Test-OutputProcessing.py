"""Offline shared output PCM checks. No renderer, ASIO or capture process."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).absolute().parents[2]
OUT = ROOT / 'build/dev16-n06-output'
OUT.mkdir(parents=True, exist_ok=True)
(OUT/'checks.json').unlink(missing_ok=True)
env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(OUT/'global'), ZIG_LOCAL_CACHE_DIR=str(OUT/'cache'))
command = [ROOT/'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe','test',
           ROOT/'tools/zig-scheduler/output_processing_tests.zig','-target','x86_64-windows-gnu',
           '-O','ReleaseSafe','-femit-bin='+str(OUT/'output-tests.exe')]
result = subprocess.run(list(map(str,command)),cwd=ROOT,env=env,capture_output=True,text=True,
                        timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
(OUT/'test.log').write_text(result.stdout+result.stderr,encoding='utf-8')
print(result.stdout+result.stderr,end='')
result.check_returncode()
(OUT/'checks.json').write_text(json.dumps(dict(passed=True,tests=7,
    scope='Offline PCM only; default bypass, calibration, linked limiter/lookahead and EOS tail'),indent=2),encoding='utf-8')
