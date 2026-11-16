"""Local preview. Serves this frontend and read-only contract assets only."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPLAY = ROOT / 'build/wwise-visualization-replay'


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        name = unquote(urlsplit(path).path)
        if name.startswith('/replay/'):
            base, relative = REPLAY, name.removeprefix('/replay/')
        elif name.startswith('/contracts/'):
            base, relative = ROOT / 'contracts', name.removeprefix('/contracts/')
        else:
            base, relative = HERE, name.lstrip('/')
        target = (base / relative).resolve()
        if not target.is_relative_to(base.resolve()):
            return str(HERE / '__forbidden__')
        return str(target)

    def list_directory(self, path):
        self.send_error(403, 'Directory listing disabled')

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'NSp frontend: http://127.0.0.1:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
