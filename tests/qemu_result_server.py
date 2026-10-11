"""Local transfer endpoint between the QEMU host and disposable ARM64 guest."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("work", type=Path)
args = parser.parse_args()
root = args.work.resolve()
allowed = {"arm64-env.txt", "arm64.json", "arm64-test.log", "arm64-guest-progress.txt"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *pos, **kw):
        super().__init__(*pos, directory=str(root / "payload"), **kw)

    def do_PUT(self):
        name = self.path.removeprefix("/")
        if name not in allowed:
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size < 10_000_000:
            self.send_error(413)
            return
        temporary = root / (name + ".upload")
        temporary.write_bytes(self.rfile.read(size))
        temporary.replace(root / name)
        self.send_response(204)
        self.end_headers()


ThreadingHTTPServer(("127.0.0.1", 18787), Handler).serve_forever()
