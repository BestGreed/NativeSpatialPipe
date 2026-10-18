"""FSA worker adapter. Exit codes: 0 verified Hook, 10 stopped, 11 busy, 20 unknown."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from backend import DEFAULT_HOME, ROOT, atomic_json, request
from hook_state import inspect, preflight


def ensure_backend(home):
    subprocess.run([sys.executable, str(Path(__file__).with_name('backend.py')), 'start', '--home', str(home)],
                   check=True, capture_output=True, timeout=15,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def observe(home):
    session_file = home / 'session.json'
    if not session_file.exists():
        return 10, {'state': 'idle', 'scope': 'No managed session recorded'}
    session = json.loads(session_file.read_text(encoding='utf-8'))
    marker = Path(session['outputDirectory']) / 'observed/hook-session.json'
    if not marker.exists():
        # A launch intent may have crashed before Hook activation. Keep locked.
        if session['state'] in ('starting', 'stopping'):
            try:
                state = request(home, 'status')['sessionState']
                if state in ('starting', 'stopping'):
                    return 11, {'state': state}
            except OSError:
                pass
        # A cancelled startup may have stopped cleanly before any Hook call.
        if Path(session['outputDirectory']).with_suffix('.user-stop').exists():
            try:
                state = request(home, 'status')['sessionState']
                if state in ('faulted', 'recovery-required'):
                    state = request(home, 'recover')['sessionState']
                if state == 'stopped':
                    return 10, {'state': 'stopped-before-hook'}
            except OSError:
                pass
        if session['state'] not in ('idle', 'stopped'):
            return 20, {'state': 'unknown', 'reason': 'Hook identity not yet available'}
        return 10, {'state': 'stopped'}
    actual = inspect(json.loads(marker.read_text(encoding='utf-8')))
    if actual.get('state') == 1 and actual.get('hookEnabled') is True:
        backend = request(home, 'status')
        if backend['sessionState'] != 'running':
            return (11 if backend['sessionState'] in ('starting', 'stopping') else 20), {'state': backend['sessionState'], 'hook': actual}
        return 0, {'state': 'running', 'hook': actual}
    if (actual.get('state') == 2 and (actual.get('hookBytesRestored') is True or actual.get('targetExited') is True)) or actual.get('inactiveVerified') is True:
        # Hook is off; downstream and endpoint restore may still be finishing.
        try:
            backend = request(home, 'status')
            if backend['sessionState'] not in ('idle', 'stopped'):
                if backend['sessionState'] in ('recovery-required', 'faulted') and Path(session['outputDirectory']).with_suffix('.user-stop').exists():
                    try:
                        backend = request(home, 'recover')
                        if backend['sessionState'] == 'stopped':
                            return 10, {'state': 'stopped', 'hook': actual}
                    except OSError:
                        pass
                return (11 if backend['sessionState'] in ('starting', 'stopping') else 20), {'state': backend['sessionState'], 'hook': actual}
        except OSError:
            return 20, {'state': 'hook-stopped-owner-unavailable', 'hook': actual}
        return 10, {'state': 'stopped', 'hook': actual}
    return 20, {'state': 'unknown', 'hook': actual}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop', 'emergency-stop', 'preflight'))
    parser.add_argument('--home', type=Path, default=DEFAULT_HOME)
    parser.add_argument('--owner-pid', type=int)
    parser.add_argument('--endpoint')
    parser.add_argument('--request-id', default='manual')
    args = parser.parse_args()
    home = args.home.resolve()
    code, value = 20, {'state': 'unknown'}
    try:
        if args.action == 'preflight':
            code, value = 12, {'state': 'eligible', 'target': preflight()}
        elif args.action == 'start':
            if not args.owner_pid or not args.endpoint:
                raise ValueError('FSA owner PID and endpoint required')
            ensure_backend(home)
            request(home, 'start-session', dict(ownerPid=args.owner_pid, endpoint=args.endpoint))
            code, value = 11, {'state': 'starting'}
        elif args.action in ('stop', 'emergency-stop'):
            session_file = home / 'session.json'
            session = json.loads(session_file.read_text(encoding='utf-8')) if session_file.exists() else None
            if session:
                output = Path(session['outputDirectory'])
                output.with_suffix('.user-stop').write_text('stop', encoding='ascii')
                output.with_suffix('.stop').write_text('stop', encoding='ascii')
            if args.action == 'emergency-stop' and session:
                path = output / 'observed/hook-session.json'
                if path.exists():
                    marker = json.loads(path.read_text(encoding='utf-8'))
                    actual = inspect(marker)
                    if actual.get('state') == 1:
                        inspect(marker, 'stop')
            if session:
                try:
                    request(home, 'stop-session')
                except OSError:
                    # Recreate only the control service; never restart audio.
                    # Recovery still requires a clean stop/restore report.
                    ensure_backend(home)
            code, value = observe(home)
        else:
            code, value = observe(home)
    except Exception as exc:
        value = {'state': 'unknown', 'error': str(exc)}
    value.update(code=code, checkedAt=time.time())
    value['action'] = args.action
    home.mkdir(parents=True, exist_ok=True)
    atomic_json(home / 'hook-status.json', value)
    if code == 20:
        atomic_json(home / 'last-error.json', value)
    # Small fixed-format companion for the native FSA disk detection module.
    marker = home / 'hook-status.ini'
    temp = marker.with_name(marker.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text('[NSP]\nCode=%d\nCheckedAt=%d\nRequestId=%s\n' % (code, time.time(), args.request_id), encoding='ascii')
    temp.replace(marker)
    print(json.dumps(value, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
