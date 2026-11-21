"""Isolated HTTP contract tests; never starts a capture/renderer/ASIO session."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    checks = []
    with tempfile.TemporaryDirectory(prefix='web-', dir=root / 'build') as directory:
        fixture = Path(directory)
        home = fixture / 'backend'
        ini = fixture / 'fsa.ini'
        ini.write_text('[NSP]\nScheduler=Zig\n', encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        command = [str(Path(args.host).resolve()), 'web-host', '--root', str(root),
                   '--home', str(home), '--fsa-config', str(ini), '--port', str(port)]

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            result = (response.status, data, dict(response.getheaders()))
            connection.close()
            return result

        def get(path):
            code, body, _ = request('GET', path)
            assert code == 200, (code, body)
            return json.loads(body)

        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as host:
            try:
                for _ in range(100):
                    if host.poll() is not None:
                        raise RuntimeError(host.stderr.read().decode(errors='replace'))
                    try:
                        bootstrap = get('/api/web/bootstrap')
                        break
                    except OSError:
                        time.sleep(.05)
                else:
                    raise RuntimeError('Web host did not start')
                token = bootstrap['token']
                assert bootstrap['control'] is False
                headers = {'Content-Type': 'application/json', 'X-NSP-Token': token,
                           'Origin': f'http://127.0.0.1:{port}'}
                status = get('/api/web/status')
                assert status['backend'] is None and status['control'] is False
                network = get('/api/web/network-status')
                assert network['schema'] == 'nsp-web-network-status/1' and network['control'] is False
                assert network['current']['backend'] == 'none'
                assert network['aes67']['status'] == 'NOT_RUN' and network['aes67']['stats'] is None
                assert network['dante']['status'] == 'NOT_AVAILABLE'
                assert network['session']['known'] is False
                assert not home.exists(), 'State reads must not start/create backend'
                checks.append('status-read-does-not-start-backend')
                page_code, page, _ = request('GET', '/')
                assert page_code == 200 and b'configuration.html' in page
                assert request('GET', '/configuration.html')[0] == 200
                for path in ['/../fsa.ini', '/%2e%2e/fsa.ini', '/api/backend/status',
                             '/api/web/config/anything', '/api/web/config/playback?path=x']:
                    assert request('GET', path)[0] >= 400, path
                checks.append('static-page-and-path-allowlist')
                for path in ['/api/backend/start-session', '/api/backend/stop-session',
                             '/api/web/start', '/api/web/configure', '/api/web/edid']:
                    assert request('POST', path, '{}', headers)[0] == 405, path
                checks.append('runtime-control-is-not-exposed')
                assert request('GET', '/api/web/network-status', headers={'Host': 'evil.example'})[0] == 403
                assert request('GET', '/api/web/network-status', headers={'Origin': 'https://evil.example'})[0] == 403
                assert request('GET', '/api/web/bootstrap', headers={'Host': 'evil.example'})[0] == 403
                assert request('GET', '/api/web/bootstrap', headers={'Origin': 'https://evil.example'})[0] == 403
                assert request('GET', '/api/web/bootstrap', headers={'Sec-Fetch-Site': 'cross-site'})[0] == 403
                checks.append('host-origin-and-cross-site-protection')
                initial = get('/api/web/config/playback')
                assert not initial['exists'] and not (home / 'playback.json').exists()
                config = initial['config']
                config['driver'] = 'asio:{00000000-0000-0000-0000-000000000000}'
                payload = json.dumps({'revision': initial['revision'], 'config': config})
                assert request('PUT', '/api/web/config/playback', payload, {'Content-Type': 'application/json'})[0] == 403
                assert request('PUT', '/api/web/config/playback', payload, {**headers, 'Origin': 'null'})[0] == 403
                assert not (home / 'playback.json').exists()
                checks.append('writes-require-nonce-and-same-origin')
                code, body, _ = request('PUT', '/api/web/config/playback', payload, headers)
                assert code == 200, (code, body)
                saved = json.loads(body)
                saved_path = Path(saved['path'])
                assert saved_path == home / 'playback.json' and saved_path.is_file()
                assert 'PlaybackConfig=' not in ini.read_text(encoding='utf-8')
                assert saved['revision'] == hashlib.sha256(saved_path.read_bytes()).hexdigest()
                assert not (home/'runtime.json').exists(), 'Config save must not start backend'
                checks.append('atomic-user-playback-save-without-installation-ini-write-or-start')
                assert request('PUT', '/api/web/config/playback', payload, headers)[0] == 409
                assert json.loads(saved_path.read_text()) == config
                checks.append('stale-revision-cannot-overwrite')
                before = saved_path.read_bytes()
                for change in [{'bufferFrames': -1}, {'extraUnknown': True}, {'outputProcessing': {'trim': [1]}},
                               {'renderer': 'lira', 'positionMapping': {'mode': 'scaled', 'reference': 1}},
                               {'recording': {'pcm': 'true'}}, {'recording': {'unknown': True}}]:
                    invalid = {**config, **change}
                    body = json.dumps({'revision': saved['revision'], 'config': invalid})
                    assert request('PUT', '/api/web/config/playback', body, headers)[0] == 400, change
                    assert saved_path.read_bytes() == before
                assert request('PUT', '/api/web/config/playback', '{bad', headers)[0] == 400
                assert request('PUT', '/api/web/config/playback', 'x' * 65537, headers)[0] == 413
                checks.append('shared-validation-and-bounded-body-preserve-file')
                config['outputProcessing'] = {'bypass': True, 'limiter': {'enabled': True, 'lookaheadFrames': 96}}
                config['channelOrder'] = 'dvs-speakers'
                config['routing'] = {'physicalChannels': list(range(12))}
                body = json.dumps({'revision': saved['revision'], 'config': config})
                code, body, _ = request('PUT', '/api/web/config/playback', body, headers)
                assert code == 200, body
                assert json.loads(body)['config']['outputProcessing']['bypass'] is True
                checks.append('existing-dsp-routing-contract-roundtrip')
                for pcm,archive in ((False,False),(True,False),(False,True),(True,True)):
                    current=get('/api/web/config/playback')
                    config['recording']={'pcm':pcm,'telemetrySnapshots':archive}
                    code,body,_=request('PUT','/api/web/config/playback',
                        json.dumps({'revision':current['revision'],'config':config}),headers)
                    assert code==200,body
                    assert get('/api/web/config/playback')['config']['recording']==config['recording']
                    assert not (home/'runtime.json').exists()
                checks.append('independent-recording-flags-save-without-audio-start')
                for preset in json.loads((root/'contracts/output-layouts.json').read_text())['layouts']:
                    count=len(preset['channels'])
                    selected={**config,'outputLayout':preset['id'],'channelOrder':'olr',
                              'outputProcessing':{'trim':[1]*count,'delayFrames':[0]*count},
                              'routing':{'physicalChannels':list(range(count))}}
                    current=get('/api/web/config/playback')
                    code, body, _=request('PUT','/api/web/config/playback',
                        json.dumps({'revision':current['revision'],'config':selected}),headers)
                    assert code==200,(preset['id'],body)
                    assert json.loads(body)['config']['outputLayout']==preset['id']
                current=get('/api/web/config/playback')
                selected['outputLayout']='roar-custom-6dof'
                assert request('PUT','/api/web/config/playback',
                    json.dumps({'revision':current['revision'],'config':selected}),headers)[0]==400
                checks.append('seven-output-layouts-roundtrip-and-reserved-6dof-rejection')
                # ---- Output backend switch + AES67 network settings ----
                aes67_network = {'destIp': '192.168.1.10', 'port': 5004, 'framesPerPacket': 48, 'ttl': 32,
                                 'payloadType': 96, 'ssrc': 1314082870, 'srcInterfaceIp': '',
                                 'ptpGmid': '00-11-22-FF-FE-33-44-55', 'ptpDomain': 0, 'ringPackets': 512,
                                 'prebufferFrames': 96, 'dll': ''}
                current = get('/api/web/config/playback')
                config = current['config']
                config['outputBackend'] = 'aes67'
                config['network'] = dict(aes67_network)
                code, body, _ = request('PUT', '/api/web/config/playback',
                    json.dumps({'revision': current['revision'], 'config': config}), headers)
                assert code == 200, (code, body)
                saved = json.loads(body)
                assert saved['config']['outputBackend'] == 'aes67'
                assert saved['config']['network'] == aes67_network
                assert saved['config']['network'] == get('/api/web/config/playback')['config']['network']
                status = get('/api/web/status')
                assert status['backend'] is None and not (home / 'runtime.json').exists()
                network = get('/api/web/network-status')
                assert network['configured']['outputBackend'] == 'aes67'
                assert network['configured']['network']['destIp'] == '192.168.1.10'
                assert network['configured']['network']['ringPackets'] == 512
                assert network['configured']['networkIgnored'] is False
                assert network['current']['backend'] == 'aes67'
                assert network['aes67']['status'] == 'NOT_RUN' and network['aes67']['stats'] is None
                checks.append('aes67-network-save-readback-and-not-run-status-without-session')
                # Switching back to ASIO keeps the AES67 block for the next switch.
                current = get('/api/web/config/playback')
                preserved = current['config']
                preserved['outputBackend'] = 'asio'
                code, body, _ = request('PUT', '/api/web/config/playback',
                    json.dumps({'revision': current['revision'], 'config': preserved}), headers)
                assert code == 200, (code, body)
                assert json.loads(body)['config']['network'] == aes67_network
                network = get('/api/web/network-status')
                assert network['configured']['outputBackend'] == 'asio'
                assert network['configured']['networkIgnored'] is True
                assert network['configured']['network']['destIp'] == '192.168.1.10'
                # Invalid preserved network still rejects instead of being silently ignored.
                current = get('/api/web/config/playback')
                for change, error, field in [
                        ({'destIp': '192.168.1.256'}, 'InvalidDestinationIp', 'network.destIp'),
                        ({'port': 0}, 'InvalidNetworkPort', 'network.port'),
                        ({'framesPerPacket': 50}, 'InvalidFramesPerPacket', 'network.framesPerPacket'),
                        ({'ttl': 0}, 'InvalidNetworkTtl', 'network.ttl'),
                        ({'payloadType': 200}, 'InvalidPayloadType', 'network.payloadType'),
                        ({'ptpDomain': 128}, 'InvalidPtpDomain', 'network.ptpDomain'),
                        ({'ringPackets': 0}, 'InvalidRingPackets', 'network.ringPackets'),
                        ({'prebufferFrames': 48001}, 'InvalidNetworkPrebuffer', 'network.prebufferFrames'),
                        ({'srcInterfaceIp': '192.168.1.999'}, 'InvalidSourceInterfaceIp', 'network.srcInterfaceIp'),
                        ({'ptpGmid': '00-11-22'}, 'InvalidPtpGmid', 'network.ptpGmid'),
                        ({'dll': 'relative.dll'}, 'InvalidDesktopPlaybackPath', 'network.dll')]:
                    invalid = dict(current['config'])
                    invalid['network'] = {**aes67_network, **change}
                    before = (home / 'playback.json').read_bytes()
                    code, body, _ = request('PUT', '/api/web/config/playback',
                        json.dumps({'revision': current['revision'], 'config': invalid}), headers)
                    assert code == 400, (change, code, body)
                    assert json.loads(body)['error'] == error, (change, body)
                    assert json.loads(body)['field'] == field, (change, body)
                    assert (home / 'playback.json').read_bytes() == before
                    current = get('/api/web/config/playback')
                invalid = dict(current['config'])
                invalid['outputBackend'] = 'dante'
                assert request('PUT', '/api/web/config/playback',
                    json.dumps({'revision': current['revision'], 'config': invalid}), headers)[0] == 400
                checks.append('aes67-field-validation-and-dante-placeholder-rejection')
                backend_config = get('/api/web/config/backend')
                body = json.dumps({'revision': backend_config['revision'], 'config':
                                   {'version': 1, 'spatialFormat': 'DTSXForHomeTheater', 'fullTiming': True}})
                code, body, _ = request('PUT', '/api/web/config/backend', body, headers)
                assert code == 200 and json.loads(body)['apply'] == 'backend-restart', body
                assert not (home / 'runtime.json').exists()
                checks.append('backend-default-save-without-controller')
                listing = get('/api/web/drivers')
                assert isinstance(listing['drivers'], list)
                checks.append('registry-only-driver-enumeration')
                saved_path.write_text('{broken', encoding='utf-8')
                broken = get('/api/web/config/playback')
                assert broken['raw'] == '{broken' and broken['config'] is None
                assert saved_path.read_text() == '{broken'
                checks.append('invalid-existing-json-is-readable-for-repair')
                # ---- Live AES67 sender status from a stub backend session ----
                session_dir = home / 'sessions' / 'fixture-aes67'
                observed_dir = session_dir / 'observed'
                observed_dir.mkdir(parents=True)
                live = {'schema': 'nsp-aes67-live/1', 'scheduler': 'zig', 'outputBackend': 'aes67',
                        'outputLayout': '4.1.2', 'observedMs': int(time.time() * 1000), 'state': 'playing',
                        'network': {'destIp': '192.168.1.10', 'port': 5004, 'framesPerPacket': 48, 'ttl': 32,
                                    'payloadType': 96, 'ssrc': 1314082870, 'srcInterfaceIp': '', 'ptpDomain': 0,
                                    'ringPackets': 512, 'prebufferFrames': 96, 'channels': 7, 'sdp': 'aes67.sdp'},
                        'stats': {'packetsSent': 12345, 'silentPackets': 10, 'framesSent': 592560,
                                  'framesSubmitted': 600000, 'bytesSent': 22671360, 'ringDroppedFrames': 0,
                                  'sendErrors': 0, 'lateTicks': 0, 'lastSeq': 12344, 'lastTimestamp': 592560},
                        'rates': {'packetsPerSecond': 996.2, 'bytesPerSecond': 1736064.0, 'bitsPerSecond': 13888512.0}}
                live_path = observed_dir / 'aes67-live.json'
                live_path.write_text(json.dumps(live), encoding='utf-8')
                final_path = observed_dir / 'aes67-output.json'
                payload = {'sessionState': 'running',
                           'session': {'state': 'running', 'outputDirectory': str(session_dir),
                                       'playback': {'outputBackend': 'aes67', 'outputLayout': '4.1.2',
                                                    'network': aes67_network}}}
                class StubBackend(BaseHTTPRequestHandler):
                    protocol_version = 'HTTP/1.1'
                    def do_POST(self):
                        length = int(self.headers.get('Content-Length') or 0)
                        if length:
                            self.rfile.read(length)
                        if self.path != '/api/backend/status':
                            self.send_response(404)
                            self.send_header('Content-Length', '0')
                            self.send_header('Connection', 'close')
                            self.end_headers()
                            self.close_connection = True
                            return
                        data = json.dumps(self.server.payload).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.wfile.write(data)
                        self.close_connection = True
                    def log_message(self, *args):
                        pass
                stub = ThreadingHTTPServer(('127.0.0.1', 0), StubBackend)
                stub.payload = payload
                thread = threading.Thread(target=stub.serve_forever, daemon=True)
                thread.start()
                try:
                    (home / 'runtime.json').write_text(json.dumps(
                        {'pid': 424242, 'scheduler': 'zig', 'port': stub.server_address[1], 'token': 'fixture'}),
                        encoding='utf-8')
                    network = get('/api/web/network-status')
                    assert network['session']['known'] is True and network['session']['state'] == 'running'
                    assert network['current']['backend'] == 'aes67'
                    assert network['aes67']['status'] == 'LIVE', network['aes67']
                    assert network['aes67']['source'] == 'aes67-live.json'
                    assert network['aes67']['stats']['packetsSent'] == 12345
                    assert network['aes67']['stats']['silentPackets'] == 10
                    assert network['aes67']['stats']['bytesSent'] == 22671360
                    assert network['aes67']['rates']['packetsPerSecond'] == 996.2
                    assert network['aes67']['network']['ssrc'] == 1314082870
                    assert network['aes67']['layout'] == '4.1.2'
                    checks.append('live-aes67-status-through-stub-session')
                    # Stale live file falls back to the shutdown report, labelled FINAL.
                    os_utime = time.time() - 60
                    import os as os_module
                    os_module.utime(live_path, (os_utime, os_utime))
                    final_path.write_text(json.dumps(
                        {'scheduler': 'zig', 'outputBackend': 'aes67', 'outputLayout': '4.1.2', 'state': 'done',
                         'network': dict(aes67_network), 'stats': {'packetsSent': 20000, 'silentPackets': 12,
                         'framesSent': 960000, 'framesSubmitted': 960000, 'bytesSent': 36736000,
                         'ringDroppedFrames': 3, 'sendErrors': 0, 'lateTicks': 0}}), encoding='utf-8')
                    network = get('/api/web/network-status')
                    assert network['aes67']['status'] == 'FINAL'
                    assert network['aes67']['stats']['packetsSent'] == 20000
                    assert network['aes67']['rates'] is None
                    assert network['aes67']['stats']['ringDroppedFrames'] == 3
                    checks.append('stale-live-falls-back-to-final-report')
                    # No live report and no final report: NOT_RUN, no fabricated numbers.
                    for item in (live_path, final_path):
                        item.unlink()
                    network = get('/api/web/network-status')
                    assert network['aes67']['status'] == 'NOT_RUN'
                    assert network['aes67']['stats'] is None and network['aes67']['reason']
                    # ASIO session with the preserved AES67 block reports it as ignored.
                    payload['session']['playback'] = {'outputBackend': 'asio', 'outputLayout': '4.1.2',
                                                      'network': aes67_network}
                    stub.payload = payload
                    network = get('/api/web/network-status')
                    assert network['current']['backend'] == 'asio'
                    assert network['session']['networkIgnored'] is True
                    assert network['aes67']['status'] == 'NOT_RUN'
                    checks.append('asio-session-marks-preserved-network-ignored')
                finally:
                    (home / 'runtime.json').unlink(missing_ok=True)
                    stub.shutdown()
                    stub.server_close()
            finally:
                host.terminate()
                host.wait(timeout=5)
        # No persistent orphan when its FSA owner exits (uses a harmless child).
        owner = subprocess.Popen(['python', '-c', 'import time; time.sleep(30)'])
        try:
            with subprocess.Popen(command + ['--owner-pid', str(owner.pid)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE) as host:
                try:
                    for _ in range(60):
                        try:
                            get('/api/web/bootstrap')
                            break
                        except OSError:
                            time.sleep(.05)
                    owner.terminate()
                    owner.wait(timeout=5)
                    assert host.wait(timeout=5) == 0, host.stderr.read()
                    checks.append('host-exits-with-owner')
                finally:
                    if host.poll() is None:
                        host.terminate()
                        host.wait(timeout=5)
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=5)
    result = {'passed': True, 'count': len(checks), 'checks': checks, 'audioDevicesOpened': False}
    Path(args.report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()