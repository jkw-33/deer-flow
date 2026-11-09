"""Run on a dedicated Docker host: python backend/ci_trace_runner.py."""

import argparse
import hmac
import json
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.gateway.ci_verification import MAX_SCRIPT_BYTES, bash_check, node_check


def create_server(host, port, token, image):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(3)
            super().setup()

        def reply(self, status, result):
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            authorization = self.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(authorization, ("Bearer " + token).encode()):
                return self.reply(401, {})
            if self.path != "/check":
                return self.reply(404, {})
            length = self.headers.get("Content-Length", "")
            if self.headers.get("Transfer-Encoding") or not length.isascii() or not length.isdecimal() or len(length) > 5 or not 0 < int(length) <= MAX_SCRIPT_BYTES:
                return self.reply(413, {})
            mode = self.headers.get("X-CI-Check")
            if mode not in {"bash_syntax", "node_lookup"}:
                return self.reply(400, {})
            source = self.rfile.read(int(length))
            if len(source) != int(length):
                return self.reply(400, {})
            try:
                code, symptom = bash_check(image, source) if mode == "bash_syntax" else node_check(image, source, self.headers.get("X-CI-Module"))
            except ValueError:
                return self.reply(422, {})
            except subprocess.TimeoutExpired:
                return self.reply(504, {})
            except (OSError, subprocess.SubprocessError):
                return self.reply(503, {})
            return self.reply(200, {"code": code, "symptom": symptom})

    return HTTPServer((host, port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    secret = args.token_file.read_text(encoding="utf-8").strip()
    if len(secret) < 32 or not secret.isascii() or not secret.isalnum():
        parser.error("token must be at least 32 ASCII letters or digits")
    with create_server(args.host, args.port, secret, args.image) as server:
        server.serve_forever()
