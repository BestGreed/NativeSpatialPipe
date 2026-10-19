"""Isolated Zig migration tests: never use the user's audiodg or live ports."""
import json
import importlib.util
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
EXE = Path(os.environ.get('NSP_SCHEDULER_TEST_EXE', ROOT / 'build/zig-scheduler/nsp-scheduler.exe'))
FLAGS = {'creationflags': subprocess.CREATE_NO_WINDOW}


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(predicate, process=None, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise AssertionError(f'Process exited early: {process.returncode}')
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError):
            pass
        time.sleep(.03)
    raise AssertionError('Timed out')


def cli(home, *args, **kwargs):
    kwargs.setdefault('timeout', 15)
    return subprocess.run([str(EXE), *args, '--root', str(ROOT), '--home', str(home)],
                          capture_output=True, text=True, **FLAGS, **kwargs)


class SchedulerTests(unittest.TestCase):
    def test_explicit_start_recovers_retired_fault_only_after_live_stop_proof(self):
        target=subprocess.Popen([sys.executable,'-c','pass'],**FLAGS);target.wait(timeout=5)
        with tempfile.TemporaryDirectory(dir=ROOT/'build',prefix='zig-restart-fault-') as temp:
            home=Path(temp);output=home/'sessions/old';(output/'observed').mkdir(parents=True)
            (home/'session.json').write_text(json.dumps(dict(id='old',outputDirectory=str(output),state='faulted',ownerPid=123,endpoint='fixture',orchestratorPid=target.pid)))
            (output/'observed/hook-session.json').write_text(json.dumps(dict(hookGeneration=2,pid=target.pid,created=1)))
            host=subprocess.Popen([str(EXE),'serve','--root',str(ROOT),'--home',temp,'--port','0'],**FLAGS)
            try:
                wait_for(lambda:(home/'runtime.json').exists(),host)
                # Owner zero intentionally rejects the new start before any
                # audio work. The previous-session transition is still real.
                result=cli(home,'start','--owner-pid','0','--endpoint','fixture')
                self.assertEqual(result.returncode,20)
                self.assertEqual(json.loads(cli(home,'request').stdout)['sessionState'],'recovery-required')
                (output/'endpoint.json').write_text(json.dumps(dict(restored=True,hookCompleted=True,hookExitCode=20)))
                result=cli(home,'start','--owner-pid','0','--endpoint','fixture')
                self.assertEqual(result.returncode,20)
                self.assertEqual(json.loads(cli(home,'request').stdout)['sessionState'],'stopped')
            finally:
                (output/'endpoint.json').write_text(json.dumps(dict(restored=True,hookCompleted=True,hookExitCode=0)))
                cli(home,'request','--action','recover');cli(home,'request','--action','exit');host.wait(timeout=5)

    def test_shutdown_idle_manager_exits_through_native_control_adapter(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build',prefix='zig-idle-exit-') as temp:
            home=Path(temp)
            host=subprocess.Popen([str(EXE),'serve','--root',str(ROOT),'--home',temp,'--port','0'],**FLAGS)
            try:
                wait_for(lambda:(home/'runtime.json').exists(),host)
                result=cli(home,'shutdown')
                self.assertEqual(result.returncode,10,result.stdout+result.stderr)
                self.assertEqual(host.wait(timeout=5),0)
                self.assertFalse((home/'runtime.json').exists())
            finally:
                if host.poll() is None:
                    cli(home,'request','--action','exit');host.wait(timeout=5)

    def test_shutdown_fault_is_separate_from_live_hook_and_requires_teardown(self):
        # An owned process which has really exited supplies the target identity.
        # inspect returns targetExited without opening or loading any Hook DLL.
        target = subprocess.Popen([sys.executable, '-c', 'pass'], **FLAGS)
        target.wait(timeout=5)
        for reachable in (False, True):
            with self.subTest(backendReachable=reachable), tempfile.TemporaryDirectory(dir=ROOT/'build', prefix='zig-shutdown-') as temp:
                home=Path(temp); output=home/'sessions/fault'; (output/'observed').mkdir(parents=True)
                owner=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],**FLAGS)
                session=dict(id='fault',outputDirectory=str(output),state='faulted',ownerPid=123,
                             endpoint='fixture',orchestratorPid=owner.pid)
                (home/'session.json').write_text(json.dumps(session))
                (output/'observed/hook-session.json').write_text(json.dumps(dict(hookGeneration=2,pid=target.pid,created=1)))
                (output/'endpoint.json').write_text(json.dumps(dict(restored=True,hookCompleted=True,hookExitCode=20,error='Writer queue failed')))
                host=None
                try:
                    if reachable:
                        host=subprocess.Popen([str(EXE),'serve','--root',str(ROOT),'--home',temp,'--port','0'],**FLAGS)
                        wait_for(lambda:(home/'runtime.json').exists(),host)
                    # A live owner must not be mistaken for completed teardown.
                    self.assertEqual(cli(home,'shutdown').returncode,20)
                    owner.terminate();owner.wait(timeout=5)
                    (output/'endpoint.json').write_text(json.dumps(dict(restored=False,hookCompleted=True,hookExitCode=20)))
                    self.assertEqual(cli(home,'shutdown').returncode,20)
                    (output/'endpoint.json').write_text(json.dumps(dict(restored=True,hookCompleted=True,hookExitCode=20,error='Writer queue failed')))
                    result=cli(home,'shutdown')
                    self.assertEqual(result.returncode,10,result.stdout+result.stderr)
                    if host: self.assertEqual(host.wait(timeout=5),0)
                finally:
                    if owner.poll() is None: owner.terminate();owner.wait(timeout=5)
                    if host and host.poll() is None:
                        cli(home,'request','--action','recover')
                        cli(home,'request','--action','exit')
                        try: host.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            # Only this test's own fixture, never a live host.
                            host.terminate();host.wait(timeout=5)

    def test_repeated_stop_after_fault_stays_responsive_without_false_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-fault-stop-') as temp:
            home = Path(temp)
            output = home / 'sessions/failed'
            output.mkdir(parents=True)
            (home / 'session.json').write_text(json.dumps(dict(id='failed', outputDirectory=str(output),
                state='faulted', ownerPid=123, endpoint='test')))
            host = subprocess.Popen([str(EXE), 'serve', '--root', str(ROOT), '--home', temp, '--port', '0'], **FLAGS)
            try:
                wait_for(lambda: (home / 'runtime.json').exists(), host)
                for _ in range(3):
                    for action in ('stop', 'emergency-stop'):
                        result = cli(home, action)
                        self.assertEqual(result.returncode, 20)
                    result = cli(home, 'request')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['sessionState'], 'recovery-required')
                # Unproven stop must not be reported as successful merely to exit.
                self.assertNotEqual(cli(home, 'request', '--action', 'exit').returncode, 0)
                (output / 'endpoint.json').write_text(json.dumps(dict(restored=True, hookCompleted=True, hookExitCode=0)))
                self.assertEqual(cli(home, 'request', '--action', 'recover').returncode, 0)
            finally:
                cli(home, 'request', '--action', 'exit')
                host.wait(timeout=5)

    def test_idle_and_partial_http_connections_have_a_total_deadline(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-http-timeout-') as temp:
            home = Path(temp)
            host = subprocess.Popen([str(EXE), 'serve', '--root', str(ROOT), '--home', temp,
                                     '--port', '0', '--python', sys.executable], **FLAGS)
            try:
                runtime = wait_for(lambda: json.loads((home / 'runtime.json').read_text()), host)
                cases = [b'', b'GET / HTTP/1.1\r\nHost: localhost\r\n',
                         ('POST /api/backend/configure HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer '
                          + runtime['token'] + '\r\nContent-Length: 100\r\n\r\n{').encode()]
                for payload in cases:
                    with socket.create_connection(('127.0.0.1', runtime['port']), timeout=2) as idle:
                        if payload:
                            idle.sendall(payload)
                        time.sleep(.1)
                        started = time.monotonic()
                        with urlopen(runtime['url'] + '/api/backend/status', timeout=4) as reply:
                            self.assertEqual(json.load(reply)['sessionState'], 'idle')
                        self.assertLess(time.monotonic() - started, 3.5)
                        # Same manager serves the actual frontend and schema.
                        for asset in ('/', '/contracts/visualization/v0/schema.json'):
                            with urlopen(runtime['url']+asset,timeout=2) as reply:
                                self.assertGreater(len(reply.read()),0)
            finally:
                cli(home, 'request', '--action', 'exit')
                host.wait(timeout=5)

    def test_control_client_times_out_when_server_never_replies(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-client-timeout-') as temp:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                listener.listen(1)
                home = Path(temp)
                (home / 'runtime.json').write_text(json.dumps(dict(scheduler='zig', port=listener.getsockname()[1], token='test')))
                started = time.monotonic()
                reply = cli(home, 'request', timeout=5)
                self.assertEqual(reply.returncode, 20)
                self.assertIn('SocketTimeout', reply.stderr)
                self.assertLess(time.monotonic() - started, 3.5)

    def test_default_configuration_migration_preserves_rules_and_snapshots(self):
        import configparser
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-default-') as temp:
            ini = Path(temp) / 'fsa.ini'
            original = '[Manager]\nLanguage=zh\n[Rule:Keep]\nEnabled=1\nProfile=7.1\n[NSP]\nPython=' + sys.executable + '\nControlScript=legacy.py\n[Snapshot:Keep]\nData=untouched\n'
            ini.write_text(original, encoding='utf-16')
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                       str(ROOT / 'tools/fsa-integration/Configure-Scheduler.ps1'), '-Configuration', str(ini)]
            for mode in ('Zig', 'Zig', 'Python', 'Zig'):
                result = subprocess.run(command + ['-Scheduler', mode], capture_output=True, timeout=15, **FLAGS)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = configparser.ConfigParser(interpolation=None)
                config.read(ini, encoding='utf-16')
                self.assertEqual(config['NSP']['Scheduler'], mode)
                self.assertEqual(Path(config['NSP']['ControlExecutable']), ROOT / 'build/zig-scheduler/nsp-scheduler.exe')
                self.assertEqual(config['NSP']['Python'], sys.executable)
                self.assertEqual(config['Rule:Keep']['Enabled'], '1')
                self.assertEqual(config['Manager']['Language'], 'zh')
                self.assertEqual(config['Snapshot:Keep']['Data'], 'untouched')
            output = Path(temp) / 'must-not-start'
            result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                str(ROOT / 'tools/wwise-probe/Run-GameTelemetry.ps1'), '-OutputDirectory', str(output),
                '-SchedulerExecutable', str(Path(temp) / 'missing.exe')], capture_output=True, timeout=15, **FLAGS)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Python fallback is disabled', result.stderr)
            self.assertFalse(output.exists())

    def test_recovery_requires_clean_endpoint_report(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-recovery-') as temp:
            home = Path(temp)
            output = home / 'sessions/old'
            output.mkdir(parents=True)
            (home / 'session.json').write_text(json.dumps(dict(id='old', outputDirectory=str(output),
                                                               state='running', ownerPid=123, endpoint='test')))
            host = subprocess.Popen([str(EXE), 'serve', '--root', str(ROOT), '--home', temp, '--port', '0'], **FLAGS)
            try:
                wait_for(lambda: (home / 'runtime.json').exists(), host)
                result = cli(home, 'request')
                self.assertEqual(json.loads(result.stdout)['sessionState'], 'recovery-required')
                result = cli(home, 'request', '--action', 'recover')
                self.assertNotEqual(result.returncode, 0)
                result = cli(home, 'request', '--action', 'start-session', '--json', '{"ownerPid":123,"endpoint":"test"}')
                self.assertNotEqual(result.returncode, 0)
                (output / 'endpoint.json').write_text(json.dumps(dict(restored=True, hookCompleted=True, hookExitCode=0)))
                result = cli(home, 'request', '--action', 'recover')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['sessionState'], 'stopped')
            finally:
                cli(home, 'request', '--action', 'exit')
                host.wait(timeout=5)

    def test_fsa_native_adapter_and_correlated_marker(self):
        adapter = ROOT / 'build/fsa-integration/bin/nsp-ui-test.exe'
        self.assertTrue(adapter.exists(), 'Build FSA UI test first')
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-fsa-') as temp:
            ini = Path(temp) / 'fsa.ini'
            ini.write_text('[NSP]\nControlExecutable=' + str(EXE) + '\n', encoding='utf-16')
            env = dict(os.environ, LOCALAPPDATA=temp)
            result = subprocess.run([str(adapter), str(ini)], env=env, capture_output=True, text=True,
                                    timeout=15, **FLAGS)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            marker = Path(temp) / 'NativeSpatialPipe/hook-status.json'
            self.assertEqual(json.loads(marker.read_text())['scheduler'], 'zig')

    def test_control_http_persistence_auth_and_single_owner(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-control-') as temp:
            home = Path(temp)
            host = subprocess.Popen([str(EXE), 'serve', '--root', str(ROOT), '--home', temp,
                                     '--port', '0', '--python', sys.executable], **FLAGS)
            try:
                runtime = wait_for(lambda: json.loads((home / 'runtime.json').read_text()), host)
                url = runtime['url']
                with urlopen(url + '/') as response:
                    self.assertIn(b'NSp', response.read())
                result = cli(home, 'request')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['sessionState'], 'idle')
                duplicate = cli(home, 'serve', '--port', '0')
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertEqual(json.loads((home / 'runtime.json').read_text())['pid'], host.pid)
                for action in ('status', 'stop', 'emergency-stop'):
                    result = cli(home, action, '--request-id', 'isolated-test')
                    self.assertEqual(result.returncode, 10, result.stderr)
                    self.assertIn('RequestId=isolated-test', (home / 'hook-status.ini').read_text())
                for headers in ({}, {'Authorization': 'Bearer ' + runtime['token'], 'Origin': 'http://evil.invalid'}):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(url + '/api/backend/exit', data=b'{}', headers=headers))
                    self.assertEqual(error.exception.code, 403)
                result = cli(home, 'request', '--action', 'configure', '--json', '{"fullTiming":true}')
                self.assertEqual(result.returncode, 0, result.stderr)
                result = cli(home, 'request', '--action', 'exit')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(host.wait(timeout=5), 0)
                self.assertFalse((home / 'runtime.json').exists())
                self.assertTrue(json.loads((home / 'config.json').read_text())['fullTiming'])
            finally:
                if host.poll() is None:
                    cli(home, 'request', '--action', 'exit')
                    host.wait(timeout=5)

    def test_native_fixture_audio_telemetry_and_clean_stop(self):
        self.fixture(False)

    def test_full_timing_native_fixture(self):
        self.fixture(True)

    def test_telemetry_start_failure_prevents_hook_activation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-start-failure-') as temp:
            base = Path(temp)
            sample = base / 'sample.bin'
            sample.write_bytes(bytes(140 * (32 + 1920)))
            source = subprocess.Popen([str(ROOT / 'build/native-hook-managed-v2/nsp_fixture_host.exe'),
                                       str(ROOT / 'build/capture-fixture/nsp_fixture.dll'), str(sample), '1'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, **FLAGS)
            try:
                self.assertEqual(source.stdout.readline().strip(), 'ready')
                with socket.socket() as occupied:
                    occupied.bind(('127.0.0.1', 0))
                    occupied.listen()
                    output = base / 'observed'
                    result = subprocess.run([str(EXE), 'host', '--root', str(ROOT), '--out', str(output),
                                             '--pid', str(source.pid), '--owner-pid', str(os.getpid()),
                                             '--stop-file', str(base / 'stop'), '--fixture', '--python', sys.executable,
                                             '--telemetry-port', str(occupied.getsockname()[1])],
                                            capture_output=True, text=True, timeout=15, **FLAGS)
                self.assertEqual(result.returncode, 20)
                self.assertIn('TelemetryStart', result.stderr)
                self.assertFalse((output / 'hook-session.json').exists())
                self.assertFalse((output / 'ready.txt').exists())
            finally:
                source.stdin.write('quit\n')
                source.stdin.flush()
                source.wait(timeout=5)
                source.stdin.close()
                source.stdout.close()
                source.stderr.close()

    def fixture(self, full_timing):
        # This DLL and executable are our owned synthetic target, not audiodg.
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='zig-fixture-') as temp:
            base = Path(temp)
            sample = base / 'sample.bin'
            props = b''.join(struct.pack('<4I4f', i + 1, 480, 1 if i < 128 else 2 << (i - 128),
                                          4, 1. + i * .01, 0., -1., 1.) for i in range(140))
            sample.write_bytes(props + struct.pack('<67200f', *([.001] * 67200)))
            source = subprocess.Popen([str(ROOT / 'build/native-hook-managed-v2/nsp_fixture_host.exe'),
                                       str(ROOT / 'build/capture-fixture/nsp_fixture.dll'), str(sample), '250'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, **FLAGS)
            host = None
            try:
                self.assertEqual(source.stdout.readline().strip(), 'ready')
                output = base / 'observed'
                stop = base / 'session.stop'
                port = free_port()
                log = (base / 'host.log').open('wb')
                command = [str(EXE), 'host', '--root', str(ROOT), '--out', str(output),
                                         '--pid', str(source.pid), '--owner-pid', str(os.getpid()),
                                         '--stop-file', str(stop), '--fixture', '--python', sys.executable,
                                         '--telemetry-port', str(port)]
                if full_timing:
                    command.append('--full-timing')
                host = subprocess.Popen(command, stdout=log, stderr=log, **FLAGS)
                try:
                    try:
                        wait_for(lambda: (output / 'ready.txt').exists(), host)
                    except AssertionError as exc:
                        self.fail(f'{exc}: {(base / "host.log").read_text()}')
                    source.stdin.write('go\n')
                    source.stdin.flush()
                    url = f'http://127.0.0.1:{port}/v1/routes/native-route/snapshot'

                    def snapshot():
                        with urlopen(url, timeout=1) as response:
                            value = json.load(response)
                        return value if value['taps']['decoded-input']['quality'] == 'live' else None

                    value = wait_for(snapshot, host)
                    objects = value['taps']['decoded-input']['objects']
                    self.assertEqual(len(objects), 140)
                    self.assertEqual(sum(o['kind'] == 'dynamic' for o in objects), 128)
                    self.assertEqual(len(value['taps']['render-ready']['channels']), 12)
                    self.assertEqual(json.loads((output / 'telemetry-routing.json').read_text())['selectedPipeline'], 0)
                    source.stdout.readline()  # All 250 source blocks completed.
                    stop.write_text('stop')
                    self.assertEqual(host.wait(timeout=20), 0, (base / 'host.log').read_text())
                    report = json.loads((output / 'observer.json').read_text())
                    self.assertTrue(report['finalStats']['hookBytesRestored'])
                    self.assertTrue(report['finalStats']['captureLibraryReleased'])
                    self.assertTrue(report['finalStats']['transportLibraryReleased'])
                    self.assertEqual(report['finalStats']['copies'], 250)
                    self.assertEqual(report['nativeTransport']['received'], 250)
                    self.assertTrue(report['streamSummary']['passed'])
                    self.assertIsNone(report['telemetryBridgeError'])
                    self.assertIsNone(report['telemetryRecordError'])
                    self.assertEqual(report['bridgeSendErrors'], 0)
                    self.assertGreater((output / 'telemetry-snapshots.jsonl').stat().st_size, 0)
                    # Compare actual PCM with the old Python control shim using
                    # the same native renderer and identical packet bytes.
                    reference_dir = base / 'reference'
                    reference_dir.mkdir()
                    spec = importlib.util.spec_from_file_location('zig_reference', ROOT / 'tools/endpoint-probe/Native-Roar.py')
                    native = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(native)
                    reference = native.NativePipeline(reference_dir, ROOT / 'build/telemetry-objects/release/nsp_roar_bridge.dll')
                    for seq in range(8):
                        reference.submit(dict(block='reference', sequence=seq, count=140, frames=[480] * 140), sample.read_bytes())
                    self.assertTrue(reference.finish()['passed'])
                    expected = (reference_dir / 'roar-714.f32').read_bytes()
                    with (output / 'stream-0/roar-714.f32').open('rb') as actual:
                        self.assertEqual(actual.read(len(expected)), expected)
                    evidence = dict(passed=True, fullTiming=full_timing, inputObjects=140,
                                    dynamicObjects=128, outputChannels=12, packets=250,
                                    pcmReferenceBlocks=8, pcmByteIdentical=True,
                                    scope='Owned fixture only; no game, audiodg or endpoint changes',
                                    observer=report)
                    evidence_path = ROOT / 'build/zig-scheduler' / ('full-timing-validation.json' if full_timing else 'fixture-validation.json')
                    evidence_path.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
                finally:
                    if host.poll() is None:
                        stop.write_text('stop')
                        host.wait(timeout=30)
                    log.close()
            finally:
                # Only terminate the owned fixture after scheduler shutdown.
                if source.poll() is None:
                    source.stdin.write('quit\n')
                    source.stdin.flush()
                    source.wait(timeout=5)
                source.stdin.close()
                source.stdout.close()
                source.stderr.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
