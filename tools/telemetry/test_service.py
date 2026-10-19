import base64
import ctypes as C
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from client import TelemetryService as _TelemetryService
from engine import contract
from test_engine import block
from test_soundscape import objects
from transport import fragments

ROOT=Path(__file__).resolve().parents[2]


def TelemetryService(**kwargs):
    # Test services must never take over the live frontend's telemetry port.
    kwargs.setdefault('http_port',0)
    return _TelemetryService(**kwargs)
spec=importlib.util.spec_from_file_location('native_control',ROOT/'tools/endpoint-probe/Native-Roar.py')
native=importlib.util.module_from_spec(spec); spec.loader.exec_module(native)
DLL=ROOT/'build/telemetry-objects/release/nsp_roar_bridge.dll'


def audio_packet(seq):
    props=b''.join(struct.pack('<4I4f',i+1,480,2<<i,4,0.,0.,-1.,1.) for i in range(12))
    return dict(block='test',sequence=seq,count=12,frames=[480]*12),props+struct.pack('<5760f',*([.5]*5760))


def websocket(svc, subscribe=True, receive_buffer=None):
    port=int(svc.info['url'].rsplit(':',1)[1])
    sock=socket.create_connection(('127.0.0.1',port),timeout=2)
    try:
        if receive_buffer: sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,receive_buffer)
        key=base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f'GET /v1/telemetry HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:8765\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n').encode())
        response=b''
        while not response.endswith(b'\r\n\r\n'):
            chunk=sock.recv(1)
            if not chunk: raise AssertionError('Upgrade closed')
            response+=chunk
        if not response.startswith(b'HTTP/1.1 101 '): raise AssertionError(response)
        if subscribe:
            body=json.dumps(dict(schemaVersion='0.2.0',messageType='subscribe',routeId='native-route',mode='both',publishRateHz=10,bands=['all'])).encode()
            mask=b'abcd'
            sock.sendall(b'\x81\xfe'+struct.pack('!H',len(body))+mask+bytes(v^mask[i%4] for i,v in enumerate(body)))
            if ws_message(sock)['messageType']!='negotiated': raise AssertionError('Negotiation failed')
        return sock
    except BaseException:
        sock.close(); raise


def ws_message(sock):
    def exact(n):
        data=b''
        while len(data)<n:
            chunk=sock.recv(n-len(data))
            if not chunk: raise AssertionError('WebSocket closed')
            data+=chunk
        return data
    opcode,size=exact(2)
    if opcode!=0x81: raise AssertionError(f'Unexpected opcode: {opcode}')
    size=size if size<126 else struct.unpack('!H',exact(2))[0] if size==126 else struct.unpack('!Q',exact(8))[0]
    return json.loads(exact(size))


class ServiceTests(unittest.TestCase):
    def test_abandoned_frontend_handshakes_release_all_connection_slots(self):
        # Browser preconnect, incomplete headers and upgrade-without-subscribe.
        # Fill the real eight-slot server, retain sockets, and verify timeout
        # reclamation rather than making recovery depend on browser cleanup.
        with TelemetryService() as svc:
            port=int(svc.info['url'].rsplit(':',1)[1])
            for kind in ('preconnect','headers','subscribe'):
                with self.subTest(kind=kind), ExitStack() as stack:
                    for _ in range(8):
                        sock=websocket(svc,False) if kind=='subscribe' else socket.create_connection(('127.0.0.1',port),timeout=2)
                        stack.enter_context(sock)
                        if kind=='headers': sock.sendall(b'GET /v1/capabilities HTTP/1.1\r\n')
                    svc.socket.sendto(svc.secret+block(seq={'preconnect':0,'headers':1,'subscribe':2}[kind]),svc.info['udp'])
                    deadline=time.monotonic()+1
                    before={'preconnect':1,'headers':2,'subscribe':3}[kind]
                    while svc.query('status')['receivedBlocks']<before and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(svc.query('status')['receivedBlocks'],before)
                    time.sleep(2.2)
                    with urlopen(svc.info['url']+'/v1/capabilities',timeout=2) as reply:
                        contract.validate(json.load(reply))
                    # A new subscription must negotiate even after every slot
                    # was occupied; stale audio need not publish a snapshot.
                    with websocket(svc): pass

    def test_frontend_slow_reader_refresh_and_reconnect_preserve_native_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as temp, TelemetryService() as svc, ExitStack() as stack:
            offdir=Path(temp)/'off'; offdir.mkdir()
            off=native.NativePipeline(offdir,DLL); on=native.NativePipeline(Path(temp),DLL)
            off.prepare(480); on.prepare(480)
            try:
                svc.attach(on)
                # One viewer never reads again, another leaves a partial frame.
                stack.enter_context(websocket(svc,receive_buffer=1024))
                partial=stack.enter_context(websocket(svc))
                partial.sendall(b'\x89\x85a')
                healthy=stack.enter_context(websocket(svc))
                latest=-1
                for seq in range(80):
                    off.submit(*audio_packet(seq)); on.submit(*audio_packet(seq))
                    time.sleep(.01); svc.pump(on)
                    if seq%8==7:
                        # Actual HTTP fetches and abrupt WebSocket page reloads.
                        with urlopen(svc.info['url']+'/v1/routes/native-route/layout',timeout=2) as reply:
                            contract.validate(json.load(reply))
                        with websocket(svc): pass
                        snapshot=ws_message(healthy); contract.validate(snapshot)
                        self.assertGreater(snapshot['telemetrySeq'],latest)
                        latest=snapshot['telemetrySeq']
                        self.assertGreater(svc.query('status')['receivedBlocks'],0)
                deadline=time.monotonic()+2
                while svc.query('status')['receivedBlocks']<80 and time.monotonic()<deadline:
                    svc.pump(on);time.sleep(.01)
                self.assertEqual(svc.query('status')['receivedBlocks'],80)
                self.assertEqual(svc.query('status')['objectBlocks'],80)
                self.assertTrue(off.finish()['passed']);self.assertTrue(on.finish(tap_drain=svc.pump)['passed'])
                self.assertEqual((offdir/'roar-714.f32').read_bytes(),(Path(temp)/'roar-714.f32').read_bytes())
            finally:
                off.finish(); on.finish()

    def test_fixed_port_and_conflict(self):
        with TelemetryService() as svc:
            port=int(svc.info['url'].rsplit(':',1)[1])
            self.assertGreater(port,0)
            with self.assertRaisesRegex(RuntimeError, f'127.0.0.1:{port}'):
                TelemetryService(http_port=port)
            self.assertIn('status', svc.query('status'))

    def test_finish_drains_after_renderer_join_before_handle_destroy(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as temp:
            p=native.NativePipeline(Path(temp),DLL);p.prepare(480)
            with TelemetryService() as svc:
                svc.attach(p)
                for seq in range(5):p.submit(*audio_packet(seq))
                result=p.finish(tap_drain=svc.pump)
                self.assertTrue(result['passed']);self.assertEqual(result['telemetryFinalDrain'],5)
                deadline=time.monotonic()+2
                while svc.query('status')['objectBlocks']<5 and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(svc.query('status')['objectBlocks'],5)
    def test_object_soundscape_actual_dll_140_slots(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as temp:
            p=native.NativePipeline(Path(temp),DLL,gain='apply');p.prepare(480)
            offdir=Path(temp)/'off';offdir.mkdir()
            off=native.NativePipeline(offdir,DLL,gain='apply');off.prepare(480)
            try:
                with TelemetryService() as svc:
                    svc.attach(p)
                    for seq in range(20):
                        types=[1]*128+[2<<i for i in range(12)]
                        props=b''.join(struct.pack('<4I4f',i+1,480,typ,4,2.+i*.1+seq*.01,3.,-4.,.5) for i,typ in enumerate(types))
                        record=dict(block='objects',sequence=seq,count=140,frames=[480]*140)
                        data=props+struct.pack('<'+str(140*480)+'f',*([.5]*(140*480)))
                        p.submit(record,data);off.submit(record,data)
                        time.sleep(.008);svc.pump(p)
                    deadline=time.monotonic()+3
                    while svc.query('status')['objectBlocks']<20 and time.monotonic()<deadline:
                        svc.pump(p);time.sleep(.01)
                    self.assertEqual(svc.query('status')['objectBlocks'],20)
                    time.sleep(.15)
                    snapshot=svc.query('snapshot');contract.validate(snapshot)
                    tap=snapshot['taps']['decoded-input']
                    self.assertEqual(tap['quality'],'live')
                    self.assertEqual(len(tap['objects']),140)
                    self.assertEqual(tap['gainMode'],'applied')
                    dynamic=tap['objects'][0]
                    self.assertEqual(dynamic['meter']['samplePeak'],.25)
                    self.assertAlmostEqual(dynamic['spatial']['position'][0],2.19,places=5)
                    self.assertEqual(sum(o['kind']=='dynamic' for o in tap['objects']),128)
            finally:
                self.assertTrue(p.finish()['passed']);self.assertTrue(off.finish()['passed'])
            self.assertEqual((Path(temp)/'roar-714.f32').read_bytes(),(offdir/'roar-714.f32').read_bytes())
    def test_native_eos_is_copied_before_cleanup_and_generation_changes(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as temp:
            p=native.NativePipeline(Path(temp),DLL,gain='apply');p.prepare(480)
            try:
                with TelemetryService() as svc:
                    svc.attach(p)
                    for seq,flags in enumerate((4,8,5)):
                        position=(999.,999.,999.) if seq==1 else (2.,3.,-4.)
                        data=struct.pack('<4I4f',99,480,1,flags,*position,.5)+struct.pack('<480f',*([.5]*480))
                        p.submit(dict(block='eos',sequence=seq,count=1,frames=[480]),data)
                        time.sleep(.01);svc.pump(p)
                    deadline=time.monotonic()+2
                    while svc.query('status')['objectBlocks']<3 and time.monotonic()<deadline:
                        svc.pump(p);time.sleep(.01)
                    time.sleep(.15)
                    snapshot=svc.query('snapshot');contract.validate(snapshot)
                    self.assertEqual(snapshot['taps']['decoded-input']['objects'][0]['objectKey']['generation'],2)
                    ended=[e for e in snapshot['events'] if e['kind']=='ended']
                    self.assertEqual(ended[0]['objectKey']['generation'],1)
                    self.assertEqual(ended[0]['spatial']['position'],[2.,3.,-4.])
            finally:self.assertTrue(p.finish()['passed'])
    def test_process_http_origin_debug_and_udp_auth(self):
        with TelemetryService() as svc:
            self.assertNotEqual(svc.info['pid'],os.getpid())
            caps=json.load(urlopen(svc.info['url']+'/v1/capabilities',timeout=2))
            contract.validate(caps)
            self.assertTrue(caps['routes'][0]['decodedInput'])
            for path in ('/internal/status','/v1/routes/wrong/layout'):
                with self.assertRaises(HTTPError) as caught:urlopen(svc.info['url']+path,timeout=2)
                self.assertEqual(caught.exception.code,404)
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(svc.info['url']+'/v1/capabilities',headers={'Origin':'https://untrusted.invalid'}),timeout=2)
            self.assertEqual(caught.exception.code,403)
            svc.socket.sendto(b'x'*32+block(),svc.info['udp'])
            time.sleep(.05)
            self.assertEqual(svc.query('status')['receivedBlocks'],0)
            svc.socket.sendto(svc.secret+block(),svc.info['udp'])
            time.sleep(.15)
            s=svc.query('snapshot'); contract.validate(s)
            self.assertEqual(s['taps']['render-ready']['channels'][0]['meter']['samplePeak'],.5)
            contract.validate(json.load(urlopen(svc.info['url']+'/v1/routes/native-route/history?window=600&band=all',timeout=2)))

    def test_websocket_negotiation_and_measured_snapshot(self):
        with TelemetryService() as svc:
            port=int(svc.info['url'].rsplit(':',1)[1])
            sock=socket.create_connection(('127.0.0.1',port),timeout=2)
            self.addCleanup(sock.close)
            key=base64.b64encode(os.urandom(16)).decode()
            sock.sendall((f'GET /v1/telemetry HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:8765\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n').encode())
            response=b''
            while not response.endswith(b'\r\n\r\n'):response+=sock.recv(1)
            self.assertIn(b'101',response)
            body=json.dumps(dict(schemaVersion='0.2.0',messageType='subscribe',routeId='native-route',mode='both',publishRateHz=10,bands=['all'])).encode()
            mask=b'abcd'
            head=bytes([0x81,0x80|len(body)]) if len(body)<126 else b'\x81\xfe'+struct.pack('!H',len(body))
            sock.sendall(head+mask+bytes(v^mask[i%4] for i,v in enumerate(body)))
            def exact(n):
                result=b''
                while len(result)<n:
                    chunk=sock.recv(n-len(result))
                    if not chunk:raise AssertionError('WS closed')
                    result+=chunk
                return result
            def message():
                a,b=exact(2); self.assertEqual(a,0x81)
                size=b if b<126 else struct.unpack('!H',exact(2))[0] if b==126 else struct.unpack('!Q',exact(8))[0]
                return json.loads(exact(size))
            self.assertEqual(message()['messageType'],'negotiated')
            svc.socket.sendto(svc.secret+block(),svc.info['udp'])
            for part in fragments(objects(count=140),1):svc.socket.sendto(svc.secret+part,svc.info['udp'])
            snapshot=message(); contract.validate(snapshot)
            self.assertEqual(len(snapshot['taps']['decoded-input']['objects']),140)
            self.assertEqual(snapshot['dataOrigin'],'measured')

    def test_real_dll_bridge_equivalence_and_service_crash_isolation(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as temp:
            offdir=Path(temp)/'off'; ondir=Path(temp)/'on'; offdir.mkdir(); ondir.mkdir()
            off=native.NativePipeline(offdir,DLL); on=native.NativePipeline(ondir,DLL)
            off.prepare(480); on.prepare(480)
            svc=TelemetryService()
            try:
                self.assertFalse(off.telemetry()['poolAllocated'])
                svc.attach(on)
                for seq in range(30):
                    off.submit(*audio_packet(seq)); on.submit(*audio_packet(seq))
                    time.sleep(.004); svc.pump(on)
                deadline=time.monotonic()+3
                while svc.query('status')['receivedBlocks']<30 and time.monotonic()<deadline:
                    svc.pump(on); time.sleep(.01)
                self.assertEqual(svc.query('status')['receivedBlocks'],30)
                time.sleep(.12)
                h=svc.query('history600')
                self.assertEqual(h['coverage']['validSamples'],30*480)
                self.assertEqual(h['channels'][0]['meanSquare'],.25)
                svc.process.terminate(); svc.process.join(2)
                for seq in range(30,70):
                    off.submit(*audio_packet(seq)); on.submit(*audio_packet(seq)); time.sleep(.002)
                self.assertEqual(svc.pump(on),0)
                self.assertGreater(on.telemetry()['poolDroppedBlocks'],0)
                with TelemetryService() as replacement:
                    replacement.attach(on)
                    for seq in range(70,75):
                        off.submit(*audio_packet(seq)); on.submit(*audio_packet(seq)); time.sleep(.004); replacement.pump(on)
                    deadline=time.monotonic()+2
                    while replacement.query('status')['receivedBlocks']<5 and time.monotonic()<deadline:
                        replacement.pump(on); time.sleep(.01)
                    h=replacement.query('history600')
                    self.assertEqual(h['streamEpoch'],3)
                    self.assertEqual(h['coverage']['validSamples'],5*480)
                self.assertTrue(off.finish()['passed']); report=on.finish(); self.assertTrue(report['passed'])
                self.assertGreater(report['telemetry']['poolDroppedBlocks'],0)
                self.assertEqual((offdir/'roar-714.f32').read_bytes(),(ondir/'roar-714.f32').read_bytes())
            finally:
                svc.close(); off.finish(); on.finish()

    def test_slow_http_clients_do_not_block_ingress(self):
        with TelemetryService(debug=True) as svc:
            port=int(svc.info['url'].rsplit(':',1)[1])
            clients=[]
            try:
                for _ in range(8):
                    sock=socket.create_connection(('127.0.0.1',port),timeout=2)
                    sock.sendall(b'GET /v1/capabilities HTTP/1.1\r\n')
                    clients.append(sock)
                svc.socket.sendto(svc.secret+block(),svc.info['udp'])
                deadline=time.monotonic()+1
                while svc.query('status')['receivedBlocks']<1 and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(svc.query('status')['receivedBlocks'],1)
            finally:
                for sock in clients:sock.close()


if __name__=='__main__':unittest.main()
