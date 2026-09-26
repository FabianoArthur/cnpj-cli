"""A scriptable local stand-in for the Receita Federal file share (tests and demo)."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


@dataclass
class FakeShare:
    """A scriptable stand-in for the Receita Federal file share.

    ``files`` maps a month to its archive bytes. ``script`` maps a month to a
    queue of one-shot behaviours consumed per GET, before normal serving:
    ``"503"``, ``"429"`` (with Retry-After), ``"cut:N"`` (announce the full
    length, send N bytes, drop the connection), ``"ignore-range"``,
    ``"html"``, ``"bad-range"`` (206 at the wrong offset) and ``"416"``.
    """

    files: dict[str, bytes] = field(default_factory=dict)
    script: dict[str, list[str]] = field(default_factory=dict)
    requests: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: int = 0
    max_active: int = 0
    delay: float = 0.0
    base_url: str = ""

    def etag(self, month: str) -> str:
        return '"' + hashlib.sha1(self.files[month]).hexdigest()[:16] + '"'


def _handler(share: FakeShare):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep test output clean
            pass

        def _month(self) -> str:
            path = parse_qs(urlparse(self.path).query).get("path", [""])[0]
            return path.rstrip("/").rsplit("/", 1)[-1]

        def _send(self, status: int, body: bytes = b"", headers: dict | None = None):
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            month = self._month()
            data = share.files.get(month)
            if data is None:
                self._send(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", share.etag(month))
            self.end_headers()

        def do_GET(self):
            month = self._month()
            with share.lock:
                share.requests.append({"month": month, "headers": dict(self.headers)})
                share.active += 1
                share.max_active = max(share.max_active, share.active)
                action = share.script.get(month, []).pop(0) if share.script.get(month) else ""
            try:
                self._serve(month, action)
            finally:
                with share.lock:
                    share.active -= 1

        def _serve(self, month: str, action: str):
            if share.delay:
                time.sleep(share.delay)
            data = share.files.get(month)
            if data is None:
                self._send(404, b"not found")
                return
            if action == "503":
                self._send(503, b"busy")
                return
            if action == "429":
                self._send(429, b"slow down", {"Retry-After": "7"})
                return
            if action == "html":
                self._send(200, b"<html>maintenance</html>", {"Content-Type": "text/html"})
                return
            etag = share.etag(month)
            start = 0
            rng = self.headers.get("Range")
            if_range = self.headers.get("If-Range")
            if rng and action != "ignore-range" and (if_range in (None, etag)):
                start = int(rng.removeprefix("bytes=").split("-")[0])
                if action == "416" or start >= len(data):
                    self._send(416, b"", {"Content-Range": f"bytes */{len(data)}"})
                    return
                if action == "bad-range":
                    start = max(0, start - 10)
                body = data[start:]
                headers = {
                    "Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}",
                    "ETag": etag,
                    "Content-Type": "application/octet-stream",
                }
                status = 206
            else:
                body = data
                headers = {"ETag": etag, "Content-Type": "application/octet-stream"}
                status = 200
            if action.startswith("cut:"):
                n = int(action.split(":")[1])
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body[:n])
                self.wfile.flush()
                self.close_connection = True
                return
            self._send(status, body, headers)

    return Handler


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # dropped connections are part of the script


def serve(share: FakeShare) -> ThreadingHTTPServer:
    """Start serving ``share`` on an ephemeral localhost port; sets ``share.base_url``."""
    server = _QuietServer(("127.0.0.1", 0), _handler(share))
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    share.base_url = f"http://127.0.0.1:{server.server_address[1]}/download?path=/%2F{{}}"
    return server
