"""`guetteur serve`: the Lambda handler behind http.server, for local demos and for the judge fallback."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .handler import handle


class _H(BaseHTTPRequestHandler):
    def _go(self) -> None:
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(min(n, 30_000)).decode() if n else ""
        r = handle({"requestContext": {"http": {"method": self.command}}, "rawPath": self.path.split("?")[0], "body": body,
                     "headers": dict(self.headers.items())})
        data = r["body"].encode()
        self.send_response(r["statusCode"])
        for k, v in r["headers"].items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _go

    def log_message(self, fmt: str, *a: object) -> None:
        print(json.dumps({"msg": "request", "line": fmt % a}))


def serve(port: int = 8080) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), _H)
    print(f"http://127.0.0.1:{port}/  (synthetic scenes only; Ctrl-C to stop)")
    srv.serve_forever()
