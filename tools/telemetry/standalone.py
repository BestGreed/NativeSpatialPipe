"""Existing telemetry service hosted for the native scheduler over loopback IPC.

This adapter only translates its control channel; audio orchestration and tap
forwarding belong to Zig. EOF from the owning scheduler closes the service.
"""
import argparse
import json
import select
import socket

from service import run


class Connection:
    def __init__(self, stream):
        self.stream = stream

    def send(self, value):
        self.stream.sendall(json.dumps(value, allow_nan=False).encode('utf-8') + b'\n')

    def recv(self):
        data = bytearray()
        while len(data) < 4096:
            byte = self.stream.recv(1)
            if not byte:
                raise EOFError()
            if byte == b'\n':
                return json.loads(data)
            data.extend(byte)
        raise ValueError('Control message too large')

    def poll(self):
        return bool(select.select([self.stream], [], [], 0)[0])

    def close(self):
        self.stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control-port', type=int, required=True)
    parser.add_argument('--secret', required=True)
    parser.add_argument('--http-port', type=int, default=8766)
    parser.add_argument('--origin', action='append', default=[])
    args = parser.parse_args()
    secret = bytes.fromhex(args.secret)
    if len(secret) != 32:
        raise ValueError('32-byte secret required')
    stream = socket.create_connection(('127.0.0.1', args.control_port), timeout=10)
    stream.settimeout(None)
    connection = Connection(stream)
    # Authenticate before delivering service data to the scheduler.
    connection.send({'secret': args.secret})
    origins = args.origin or ['http://127.0.0.1:8765', 'http://localhost:8765']
    origins = set(origins)
    for origin in tuple(origins):
        if origin.startswith('http://127.0.0.1:'):
            origins.add(origin.replace('127.0.0.1', 'localhost', 1))
        elif origin.startswith('http://localhost:'):
            origins.add(origin.replace('localhost', '127.0.0.1', 1))
    run(connection, secret, tuple(origins), http_port=args.http_port)


if __name__ == '__main__':
    main()
