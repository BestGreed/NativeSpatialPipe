import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import ROOT, atomic_json
from fsa_control import observe


class ObservationTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'build/backend-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.home = Path(self.temp.name)
        self.output = self.home / 'sessions/test'
        atomic_json(self.home / 'session.json', dict(state='running', outputDirectory=str(self.output)))
        atomic_json(self.output / 'observed/hook-session.json', dict(pid=123, created=456))

    def tearDown(self):
        self.temp.cleanup()

    @patch('fsa_control.request', return_value={'sessionState': 'running'})
    @patch('fsa_control.inspect', return_value={'state': 1, 'hookEnabled': True})
    def test_verified_running(self, *_):
        self.assertEqual(observe(self.home)[0], 0)

    @patch('fsa_control.request', return_value={'sessionState': 'stopping'})
    @patch('fsa_control.inspect', return_value={'state': 1, 'hookEnabled': True})
    def test_stopping_does_not_publish_running(self, *_):
        self.assertEqual(observe(self.home)[0], 11)

    @patch('fsa_control.request', return_value={'sessionState': 'stopped'})
    @patch('fsa_control.inspect', return_value={'state': 2, 'hookBytesRestored': True})
    def test_stopped_requires_restore_evidence(self, *_):
        self.assertEqual(observe(self.home)[0], 10)

    @patch('fsa_control.inspect', return_value={'state': 2, 'hookBytesRestored': False})
    def test_disabled_without_restoration_stays_unknown(self, *_):
        self.assertEqual(observe(self.home)[0], 20)

    @patch('fsa_control.request', return_value={'sessionState': 'faulted'})
    @patch('fsa_control.inspect', return_value={'state': 2, 'hookBytesRestored': True})
    def test_downstream_fault_stays_locked(self, *_):
        self.assertEqual(observe(self.home)[0], 20)

    def test_missing_identity_is_unknown(self):
        (self.output / 'observed/hook-session.json').unlink()
        self.assertEqual(observe(self.home)[0], 20)

    @patch('fsa_control.request', return_value={'sessionState': 'stopped'})
    @patch('fsa_control.inspect', return_value={'state': 2, 'targetExited': True, 'hookBytesRestored': None})
    def test_exited_target_is_separate_from_restored_bytes(self, *_):
        code, value = observe(self.home)
        self.assertEqual(code, 10)
        self.assertIsNone(value['hook']['hookBytesRestored'])

    @patch('fsa_control.request', side_effect=[{'sessionState': 'recovery-required'}, {'sessionState': 'stopped'}])
    @patch('fsa_control.inspect', return_value={'state': 2, 'hookBytesRestored': True})
    def test_user_stop_allows_confirmed_recovery(self, *_):
        self.output.with_suffix('.user-stop').write_text('stop')
        self.assertEqual(observe(self.home)[0], 10)

    @patch('fsa_control.request', return_value={'sessionState': 'recovery-required'})
    @patch('fsa_control.inspect', return_value={'state': 2, 'hookBytesRestored': True})
    def test_no_user_stop_keeps_recovery_locked(self, *_):
        self.assertEqual(observe(self.home)[0], 20)


if __name__ == '__main__':
    unittest.main()
