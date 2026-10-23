"""Separate service process: raw UDP ingress, metering, read-only HTTP/WebSocket."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import select
import socket
import struct
import threading
import time
from urllib.parse import urlparse, parse_qs
from engine import Engine, BLOCK_BYTES, VERSION, contract
from soundscape import SoundscapeEngine
from transport import Reassembler, MAX_BLOCK


def encode(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False).encode()


def run(connection, secret, allowed_origins, debug=False, http_port=8766, output_layout='7.1.4'):
    engine = SoundscapeEngine(output_layout=output_layout)
    reassembler=Reassembler()
    lock = threading.Lock()
    slots = threading.BoundedSemaphore(8)
    origins = set(allowed_origins)
    encoded_key=encoded_body=None

    def snapshot_bytes(snapshot):
        # Called under lock. Published snapshots are immutable: flush builds a
        # new tree before releasing the lock. Cache one encoding for all viewers.
        nonlocal encoded_key,encoded_body
        key=(snapshot['sessionId'],snapshot['streamEpoch'],snapshot['telemetrySeq'])
        if key!=encoded_key:
            encoded_body=encode(snapshot);encoded_key=key
        return encoded_body
    ingress = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ingress.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)
    ingress.bind(('127.0.0.1', 0))
    ingress.setblocking(False)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = False
        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
        def process_request(self, request, address):
            if not slots.acquire(False):
                request.close(); return
            try:
                super().process_request(request, address)
            except Exception:
                slots.release(); raise
        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                slots.release()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def handle(self):
            try: super().handle()
            except OSError: self.close_connection=True
        def finish(self):
            try: super().finish()
            except OSError: pass
        def setup(self):
            super().setup()
            self.connection.settimeout(2)
        def log_message(self, *args):
            pass
        def reply(self, value, code=200):
            body = encode(value)
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            if self.headers.get('Origin') in origins:
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            origin = self.headers.get('Origin')
            if origin and origin not in origins:
                self.reply({'error':'origin-denied'},403); return
            path = urlparse(self.path)
            if path.path == '/v1/telemetry' and self.headers.get('Upgrade','').lower() == 'websocket':
                if origin not in origins:
                    self.reply({'error':'websocket-origin-required'},403); return
                self.websocket(); return
            try:
                with lock:
                    if path.path == '/v1/capabilities': value = engine.capabilities()
                    elif path.path == '/v1/routes/native-route/layout': value = engine.layout()
                    elif path.path == '/v1/routes/native-route/history':
                        args = parse_qs(path.query)
                        if set(args)-{'window','band'} or any(len(v)!=1 for v in args.values()): raise ValueError()
                        value = engine.history(int(args.get('window',['600'])[0]),args.get('band',['all'])[0])
                    elif path.path == '/v1/routes/native-route/snapshot': value = engine.latest if engine.status()['status']=='live' else None
                    elif path.path == '/internal/status' and debug: value = engine.status()
                    else: value = None
                if value is None:
                    self.reply({'error':'not-found-or-not-ready'},404)
                else: self.reply(value)
            except (ValueError, OverflowError):
                self.reply({'error':'unsupported-query'},400)
        def read_frame(self):
            def exact(n):
                data = self.rfile.read(n)
                if len(data)!=n: raise ValueError('closed')
                return data
            a,b = exact(2)
            if a & 0x70 or not a & 0x80 or not b & 0x80: raise ValueError('invalid frame')
            n=b&127
            if n==126: n=struct.unpack('!H',exact(2))[0]
            elif n==127: n=struct.unpack('!Q',exact(8))[0]
            if n>4096 or a&15 >= 8 and n>125: raise ValueError('oversized frame')
            mask=exact(4); data=exact(n)
            return a&15, bytes(v^mask[i%4] for i,v in enumerate(data))
        def send_frame(self, opcode, body):
            head=bytes([0x80|opcode])
            head+=bytes([len(body)]) if len(body)<126 else b'\x7e'+struct.pack('!H',len(body)) if len(body)<65536 else b'\x7f'+struct.pack('!Q',len(body))
            self.connection.sendall(head+body)
        def websocket(self):
            key=self.headers.get('Sec-WebSocket-Key','')
            try:
                if self.headers.get('Sec-WebSocket-Version')!='13' or len(base64.b64decode(key,validate=True))!=16: raise ValueError()
            except Exception:
                self.reply({'error':'bad-websocket'},400); return
            self.send_response(101)
            self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade')
            self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode())
            self.end_headers()
            self.close_connection=True
            try:
                opcode,body=self.read_frame()
                if opcode!=1: raise ValueError('subscribe required')
                request=contract.loads(body.decode())
                contract.validate(request)
                if (request['schemaVersion'],request['messageType'],request['routeId'],request['mode'],request['publishRateHz'],request['bands']) not in [
                    (VERSION,'subscribe',engine.route,mode,rate,['all']) for rate in (1,10) for mode in ('sound-spectrum','sound-navigation','both')]: raise ValueError('unsupported subscription')
                response=dict(request,messageType='negotiated')
                self.send_frame(1,encode(response))
                self.connection.settimeout(.2)
                last=None; deadline=0
                while True:
                    if time.monotonic()>=deadline:
                        with lock:
                            live=engine.status()['status']=='live'
                            latest=engine.latest if live else None
                            body=None
                            if latest is not None:
                                ident=(latest['streamEpoch'],latest['telemetrySeq'])
                                if ident!=last:body=snapshot_bytes(latest)
                        if body is not None:
                            self.send_frame(1,body);last=ident
                        deadline=time.monotonic()+1/request['publishRateHz']
                    if select.select([self.connection],[],[],.02)[0]:
                        opcode,body=self.read_frame()
                        if opcode==8:
                            self.send_frame(8,body); break
                        if opcode==9: self.send_frame(10,body)
                        elif opcode!=10: raise ValueError('unexpected message')
            except (ValueError,UnicodeError,KeyError,TypeError):
                try: self.send_frame(8,struct.pack('!H',1008)+b'unsupported message')
                except OSError: pass
            except OSError:
                pass

    try:
        server=Server(('127.0.0.1',http_port),Handler)
    except OSError as exc:
        ingress.close()
        connection.send({'error':f'Telemetry cannot listen on 127.0.0.1:{http_port}: {exc}'})
        connection.close()
        return
    origins.add(f'http://127.0.0.1:{server.server_port}')
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    connection.send(dict(pid=os.getpid(),udp=ingress.getsockname(),url=f'http://127.0.0.1:{server.server_port}',meterBackend=engine.metering.name))
    try:
        running=True
        while running:
            if connection.poll():
                command=connection.recv()
                with lock:
                    if command=='stop': running=False; value=True
                    elif command=='status': value=dict(engine.status(),pid=os.getpid())
                    elif command=='snapshot': value=engine.latest
                    elif command in ('history600','history3600'): value=engine.history(int(command[7:]))
                    else: value={'error':'unsupported command'}
                connection.send(value)
            if select.select([ingress],[],[],.01)[0]:
                for _ in range(32):
                    try: data=ingress.recv(60000)
                    except BlockingIOError: break
                    if data[:32]==secret:
                        payload=data[32:]
                        if payload[:4]==b'NSPF':payload=reassembler.add(payload)
                        if payload:
                            with lock:
                                if payload[:4]==b'NSPO':engine.ingest_objects(payload)
                                elif payload[:4]==b'NSPT':engine.ingest(payload)
            reassembler.expire()
            with lock: engine.flush()
    except (EOFError,BrokenPipeError):
        pass
    finally:
        server.shutdown(); server.server_close(); ingress.close(); connection.close()
