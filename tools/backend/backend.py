"""Persistent local control host. Audio starts only on an explicit command."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOME = Path(os.environ.get('LOCALAPPDATA', str(ROOT / 'build'))) / 'NativeSpatialPipe'
FRONTEND_FORMATS = ('WindowsSonic','DTSXForHomeTheater','DolbyAtmosForHomeTheater','DolbyAtmosForHeadphones','DolbyAtmosForSpeakers','DTSHeadphoneX','DTSXUltra')
DEFAULT_CONFIG = {'version': 1, 'spatialFormat': 'WindowsSonic', 'fullTiming': False}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_config(path):
    value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(DEFAULT_CONFIG)
    if (not isinstance(value, dict) or set(value) != set(DEFAULT_CONFIG)
            or type(value['version']) is not int or value['version'] != 1
            or value['spatialFormat'] not in FRONTEND_FORMATS
            or type(value['fullTiming']) is not bool):
        raise ValueError('Invalid backend config; original file preserved')
    return value


class Controller:
    def __init__(self, home, launcher=None):
        self.home = home
        self.config = load_config(home / 'config.json')
        self.lock = threading.RLock()
        self.process = None
        self.session = None
        self.state = 'idle'
        self.error = None
        self.launcher = launcher or self.launch
        self.stop_deadline = None
        self.started = time.time()
        previous = home / 'session.json'
        if previous.exists():
            self.session = json.loads(previous.read_text(encoding='utf-8'))
            if self.session.get('state') not in ('stopped', 'idle'):
                self.state = 'recovery-required'
                self.error = 'Previous session was not confirmed stopped; inspect endpoint.json before recovery'
        atomic_json(home / 'config.json', self.config)

    @staticmethod
    def launch(output, config):
        args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                str(ROOT / 'tools/wwise-probe/Run-GameTelemetry.ps1'), '-LegacyPythonScheduler',
                '-OutputDirectory', str(output), '-SpatialFormat', config['spatialFormat']]
        if config['fullTiming']:
            args.append('-FullTiming')
        if config.get('ownerPid'):
            args.extend(['-Managed', '-OwnerPid', str(config['ownerPid']), '-Endpoint', config['endpoint']])
        log = (output.parent / (output.name + '.log')).open('ab')
        try:
            return subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        finally:
            log.close()

    def save_session(self):
        if self.session:
            self.session['state'] = self.state
            self.session['error'] = self.error
            atomic_json(self.home / 'session.json', self.session)

    def tick(self):
        with self.lock:
            if self.process is None:
                return
            output = Path(self.session['outputDirectory'])
            code = self.process.poll()
            if code is not None:
                try:
                    report = json.loads((output / 'endpoint.json').read_text(encoding='utf-8-sig'))
                    clean = (code == 0 and report.get('restored') is True
                             and report.get('hookCompleted') is True
                             and report.get('hookExitCode') == 0 and not report.get('error'))
                except (OSError, ValueError):
                    clean = False
                self.state = 'stopped' if clean else 'faulted'
                self.error = None if clean else 'Session ended without confirmed clean stop and endpoint restore'
                self.session['exitCode'] = code
                self.process = None
                self.save_session()
            elif self.state == 'starting' and (output / 'observed/ready.txt').exists():
                self.state = 'running'
                self.save_session()
            elif self.state == 'stopping' and time.monotonic() > self.stop_deadline:
                self.state = 'faulted'
                self.error = 'Stop timed out; resources retained, no forced termination or restart'
                self.save_session()

    def status(self):
        self.tick()
        with self.lock:
            data = 'disconnected'
            if self.state == 'running':
                try:
                    with urlopen('http://127.0.0.1:8766/v1/routes/native-route/snapshot', timeout=.3) as response:
                        json.load(response)
                    data = 'live'
                except (OSError, ValueError):
                    data = 'waiting-or-stale'
            return dict(backend='ready', pid=os.getpid(), startedAt=self.started,
                        sessionState=self.state, dataState=data, error=self.error,
                        session=self.session, config=dict(self.config))

    def command(self, action, value):
        with self.lock:
            self.tick()
            if action == 'configure':
                if self.process or self.state in ('recovery-required', 'faulted'):
                    raise ValueError('Stop and resolve session before changing audio configuration')
                candidate = dict(self.config)
                if set(value) - {'spatialFormat', 'fullTiming'}:
                    raise ValueError('Unsupported configuration field')
                candidate.update(value)
                if (candidate['spatialFormat'] not in FRONTEND_FORMATS
                        or type(candidate['fullTiming']) is not bool):
                    raise ValueError('Invalid configuration')
                atomic_json(self.home / 'config.json', candidate)
                self.config = candidate
            elif action == 'start-session':
                if set(value)-{'ownerPid','endpoint','spatialFormat'}:
                    raise ValueError('Unsupported session field')
                if 'ownerPid' in value and (type(value['ownerPid']) is not int or value['ownerPid'] <= 0 or not isinstance(value.get('endpoint'), str) or not value['endpoint']):
                    raise ValueError('Managed session requires ownerPid and endpoint')
                if self.state in ('starting', 'running'):
                    if value and (value.get('ownerPid')!=self.session.get('ownerPid') or value.get('endpoint')!=self.session.get('endpoint') or value.get('spatialFormat','WindowsSonic')!=self.session['config']['spatialFormat']):
                        raise ValueError('Another owner or endpoint already holds the session')
                    return self.status()
                if self.process or self.state not in ('idle', 'stopped'):
                    raise ValueError('Session requires stop or explicit recovery first')
                ident = uuid.uuid4().hex
                output = self.home / 'sessions' / ident
                output.parent.mkdir(parents=True, exist_ok=True)
                self.state, self.error = 'starting', None
                self.session = dict(id=ident, outputDirectory=str(output), config=dict(self.config))
                self.save_session()  # Intent is durable before any system side effect.
                try:
                    runtime_config = dict(self.config)
                    if 'ownerPid' in value:
                        if type(value['ownerPid']) is not int or value['ownerPid'] <= 0 or not isinstance(value.get('endpoint'), str) or not value['endpoint']:
                            raise ValueError('Managed session requires ownerPid and endpoint')
                        runtime_config.update(ownerPid=value['ownerPid'], endpoint=value['endpoint'], spatialFormat=value.get('spatialFormat','WindowsSonic'))
                        if runtime_config['spatialFormat'] not in FRONTEND_FORMATS:raise ValueError('Unknown spatialFormat')
                        self.session['ownerPid'] = value['ownerPid']
                        self.session['endpoint'] = value['endpoint']
                        self.session['config']['spatialFormat']=runtime_config['spatialFormat']
                        self.save_session()
                    self.process = self.launcher(output, runtime_config)
                    self.session['orchestratorPid'] = self.process.pid
                except Exception as exc:
                    self.state, self.error = 'faulted', str(exc)
                    self.save_session()
                    raise
                self.save_session()
            elif action == 'stop-session':
                if self.process:
                    if self.state != 'stopping':
                        output = Path(self.session['outputDirectory'])
                        output.with_suffix('.stop').write_text('stop', encoding='ascii')
                        self.state = 'stopping'
                        self.stop_deadline = time.monotonic() + 45
                        self.save_session()
                elif self.state not in ('idle', 'stopped'):
                    raise ValueError('Cannot confirm ownership; inspect previous session and use recover')
            elif action == 'recover':
                if self.process:
                    raise ValueError('Session process is still alive')
                if self.state not in ('recovery-required', 'faulted'):
                    return self.status()
                report = json.loads((Path(self.session['outputDirectory']) / 'endpoint.json').read_text(encoding='utf-8-sig'))
                inactive = report.get('hookExitCode') == 0
                if not inactive and report.get('restored') is True and report.get('hookCompleted') is True:
                    from hook_state import inspect
                    marker = json.loads((Path(self.session['outputDirectory'])/'observed/hook-session.json').read_text(encoding='utf-8'))
                    proof = inspect(marker)
                    inactive = proof.get('inactiveVerified') is True or proof.get('targetExited') is True or (proof.get('state')==2 and proof.get('hookBytesRestored') is True)
                if not (report.get('restored') is True and report.get('hookCompleted') is True and inactive):
                    raise ValueError('Previous session has no confirmed stop and restore report')
                self.state, self.error = 'stopped', None
                self.save_session()
            else:
                raise ValueError('Unsupported command')
            return self.status()


def serve(home, port):
    home.mkdir(parents=True, exist_ok=True)
    # One controller per user state directory, independent of the chosen port.
    with (home / 'owner.lock').open('a+b') as owner:
        owner.seek(0)
        if owner.read(1) == b'':
            owner.write(b'0')
            owner.flush()
        owner.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _serve(home, port)


def _serve(home, port):
    # Exclusive socket is the single-owner lock, acquired before reading/writing state.
    spec = importlib.util.spec_from_file_location('frontend_serve', ROOT / 'tools/visualization-frontend/serve.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = secrets.token_hex(32)
    shutdown = threading.Event()
    exit_requested = threading.Event()
    controller = None

    class Handler(module.Handler):
        def log_message(self, *args):
            pass

        def reply(self, value, code=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/api/backend/status':
                self.reply(controller.status())
            elif self.path.startswith('/api/'):
                self.reply({'error': 'not-found'}, 404)
            else:
                super().do_GET()

        def do_POST(self):
            if (self.headers.get('Authorization') != 'Bearer ' + token
                    or self.headers.get('Origin') is not None):
                self.reply({'error': 'unauthorized'}, 403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    raise ValueError('Invalid request size')
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError('Object required')
                action = self.path.removeprefix('/api/backend/')
                if action == 'exit':
                    controller.command('stop-session', {})
                    exit_requested.set()
                    if controller.state not in ('idle', 'stopped'):
                        self.reply({'error': 'Session stopping; retry exit after confirmed stop', **controller.status()}, 409)
                        return
                    shutdown.set()
                    self.reply({'backend': 'stopping'})
                else:
                    self.reply(controller.command(action, value))
            except (ValueError, OSError, KeyError) as exc:
                self.reply({'error': str(exc)}, 409)

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    server = Server(('127.0.0.1', port), Handler)
    try:
        controller = Controller(home)
        atomic_json(home / 'runtime.json', dict(pid=os.getpid(), url=f'http://127.0.0.1:{server.server_port}', token=token))
        server.timeout = .2
        def stop_signal(*_):
            # Signal requests graceful stop; never abandon a running owned session.
            try:
                controller.command('stop-session', {})
                exit_requested.set()
                if controller.state in ('idle', 'stopped'):
                    shutdown.set()
            except ValueError:
                pass
        signal.signal(signal.SIGINT, stop_signal)
        signal.signal(signal.SIGTERM, stop_signal)
        while not shutdown.is_set():
            server.handle_request()
            controller.tick()
            if exit_requested.is_set() and controller.state in ('idle', 'stopped'):
                shutdown.set()
    finally:
        server.server_close()
        (home / 'runtime.json').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('serve', 'start', 'status', 'configure', 'start-session', 'stop-session', 'recover', 'exit'))
    parser.add_argument('--home', type=Path, default=DEFAULT_HOME)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--spatial-format', choices=FRONTEND_FORMATS)
    parser.add_argument('--full-timing', choices=('on', 'off'))
    args = parser.parse_args()
    home = args.home.resolve()
    if args.action == 'serve':
        serve(home, args.port)
        return
    if args.action == 'start':
        try:
            print(json.dumps(request(home, 'status'), ensure_ascii=False))
            return
        except (OSError, ValueError):
            pass
        home.mkdir(parents=True, exist_ok=True)
        with (home / 'backend.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'serve', '--home', str(home), '--port', str(args.port)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError('Backend startup failed; inspect backend.log (port may be occupied)')
            try:
                result = request(home, 'status')
                if result['pid'] == child.pid:
                    print(json.dumps(result, ensure_ascii=False))
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        raise RuntimeError('Backend startup timed out; inspect backend.log')
    value = {}
    if args.spatial_format:
        value['spatialFormat'] = args.spatial_format
    if args.full_timing:
        value['fullTiming'] = args.full_timing == 'on'
    print(json.dumps(request(home, args.action, value), ensure_ascii=False, indent=2))


def request(home, action, value=None):
    runtime = json.loads((home / 'runtime.json').read_text(encoding='utf-8'))
    req = Request(runtime['url'] + '/api/backend/' + action,
                  data=None if action == 'status' else json.dumps(value or {}).encode(),
                  headers={'Authorization': 'Bearer ' + runtime['token'], 'Content-Type': 'application/json'})
    with urlopen(req, timeout=5) as response:
        return json.load(response)


if __name__ == '__main__':
    main()
