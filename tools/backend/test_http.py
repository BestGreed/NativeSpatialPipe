import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend import ROOT, request


class HTTPTests(unittest.TestCase):
    def test_detached_host_persistence_auth_and_exit(self):
        base = ROOT / 'build/backend-tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            home = Path(temp)
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('backend.py')),
                                        'serve', '--home', str(home), '--port', '0'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(100):
                    if (home / 'runtime.json').exists():
                        break
                    if process.poll() is not None:
                        self.fail(process.stderr.read().decode(errors='replace'))
                    time.sleep(.05)
                runtime = json.loads((home / 'runtime.json').read_text())
                status = request(home, 'status')
                self.assertEqual(status['sessionState'], 'idle')
                with urlopen(runtime['url'] + '/') as response:
                    self.assertIn('NSp'.encode(), response.read())
                # Closing an HTTP client has no lifecycle side effects.
                self.assertEqual(request(home, 'status')['pid'], process.pid)
                duplicate = subprocess.run([sys.executable, str(Path(__file__).with_name('backend.py')),
                                            'serve', '--home', str(home), '--port', '0'],
                                           capture_output=True, timeout=5)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertEqual(json.loads((home / 'runtime.json').read_text())['pid'], process.pid)
                for action in ('status', 'stop', 'emergency-stop'):
                    adapter = subprocess.run([sys.executable, str(Path(__file__).with_name('fsa_control.py')),
                                              action, '--home', str(home), '--request-id', 'test-'+action],
                                             capture_output=True, timeout=5)
                    self.assertEqual(adapter.returncode, 10, adapter.stderr.decode(errors='replace'))
                    marker = (home / 'hook-status.ini').read_text()
                    self.assertIn('RequestId=test-'+action, marker)
                    self.assertIn('Code=10', marker)
                for headers in ({}, {'Authorization': 'Bearer ' + runtime['token'], 'Origin': 'http://evil.invalid'}):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(runtime['url'] + '/api/backend/exit', data=b'{}', headers=headers))
                    self.assertEqual(error.exception.code, 403)
                request(home, 'configure', {'fullTiming': True})
                self.assertTrue(json.loads((home / 'config.json').read_text())['fullTiming'])
                request(home, 'exit')
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertFalse((home / 'runtime.json').exists())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                process.stderr.close()


if __name__ == '__main__':
    unittest.main()
