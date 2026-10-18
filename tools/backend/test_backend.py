import json
from pathlib import Path
import tempfile
import unittest

from backend import Controller, atomic_json


class Process:
    pid = 12345
    code = None

    def poll(self):
        return self.code


class BackendTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[2] / 'build/backend-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.home = Path(self.temp.name)
        self.process = Process()
        self.calls = 0

    def tearDown(self):
        self.temp.cleanup()

    def launch(self, output, config):
        self.calls += 1
        return self.process

    def controller(self):
        return Controller(self.home, self.launch)

    def report(self, controller, **fields):
        atomic_json(Path(controller.session['outputDirectory']) / 'endpoint.json',
                    dict(restored=True, hookCompleted=True, hookExitCode=0, **fields))

    def test_config_survives_but_running_does_not(self):
        c = self.controller()
        c.command('configure', {'fullTiming': True})
        c.command('start-session', {})
        restarted = self.controller()
        self.assertTrue(restarted.config['fullTiming'])
        self.assertEqual(restarted.state, 'recovery-required')
        with self.assertRaises(ValueError):
            restarted.command('start-session', {})
        self.assertEqual(self.calls, 1)

    def test_idempotent_start_and_cancel_before_directory_exists(self):
        c = self.controller()
        c.command('start-session', {})
        c.command('start-session', {})
        self.assertEqual(self.calls, 1)
        output = Path(c.session['outputDirectory'])
        c.command('stop-session', {})
        c.command('stop-session', {})
        self.assertTrue(output.with_suffix('.stop').exists())
        self.assertFalse(output.exists())
        self.report(c)
        self.process.code = 0
        c.tick()
        self.assertEqual(c.state, 'stopped')

    def test_fault_blocks_restart_and_requires_restore_evidence(self):
        c = self.controller()
        c.command('start-session', {})
        self.process.code = 1
        c.tick()
        self.assertEqual(c.state, 'faulted')
        with self.assertRaises(ValueError):
            c.command('start-session', {})
        with self.assertRaises(OSError):
            c.command('recover', {})
        self.report(c)
        c.command('recover', {})
        self.assertEqual(c.state, 'stopped')

    def test_timeout_retains_process_and_lock(self):
        c = self.controller()
        c.command('start-session', {})
        c.command('stop-session', {})
        c.stop_deadline = 0
        c.tick()
        self.assertEqual(c.state, 'faulted')
        self.assertIs(c.process, self.process)
        with self.assertRaises(ValueError):
            c.command('recover', {})
        with self.assertRaises(ValueError):
            c.command('configure', {'fullTiming': True})

    def test_invalid_config_preserved(self):
        path = self.home / 'config.json'
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.controller()
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_active_config_is_immutable(self):
        c = self.controller()
        c.command('start-session', {})
        with self.assertRaises(ValueError):
            c.command('configure', {'spatialFormat': 'DTSXForHomeTheater'})
        self.assertEqual(json.loads((self.home / 'config.json').read_text())['spatialFormat'], 'WindowsSonic')

    def test_managed_owner_and_invalid_request(self):
        c = self.controller()
        with self.assertRaises(ValueError):
            c.command('start-session', {'ownerPid': 0})
        self.assertEqual(c.state, 'idle')
        c.command('start-session', {'ownerPid': 123, 'endpoint': 'test-endpoint'})
        with self.assertRaises(ValueError):
            c.command('start-session', {'ownerPid': 456, 'endpoint': 'test-endpoint'})
        self.assertEqual(self.calls, 1)


if __name__ == '__main__':
    unittest.main()
