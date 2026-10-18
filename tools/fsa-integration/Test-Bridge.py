"""Exercise actual FSA native/adapter IPC in an isolated user directory; no audio."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
base = ROOT / 'build/fsa-bridge-tests'
base.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=base) as directory:
    home = Path(directory)
    ini = home / 'test.ini'
    ini.write_text('[NSP]\nPython=%s\nControlScript=%s\n' %
                   (sys.executable, ROOT / 'tools/backend/fsa_control.py'), encoding='utf-16')
    env = dict(os.environ, LOCALAPPDATA=str(home))
    backend_home = home / 'NativeSpatialPipe'
    try:
        subprocess.run([str(ROOT / 'build/fsa-integration/bin/nsp-ui-test.exe'), str(ini)],
                       check=True, env=env, timeout=30)
    finally:
        if (backend_home / 'runtime.json').exists():
            subprocess.run([sys.executable, str(ROOT / 'tools/backend/backend.py'), 'exit', '--home', str(backend_home)],
                           check=True, capture_output=True, timeout=10)
            # Exit acknowledges before serve loop closes the ownership handle.
            import time
            for _ in range(50):
                if not (backend_home / 'runtime.json').exists():
                    break
                time.sleep(.1)
