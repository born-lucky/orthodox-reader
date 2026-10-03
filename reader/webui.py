"""The Reader window: a local page in a chromeless Edge app window.

The app (tray, scheduler, break screens) serves this page on 127.0.0.1 only, on a
random port, and every API call must carry a random token, so no website can
talk to it. Edge's --app mode shows the page as a plain window with no browser
around it; its profile lives in %APPDATA%\\OrthodoxReader\\window.

  GET  /?t=TOKEN        the page (data/ui/index.html)
  GET  /fonts/<file>    bundled fonts
  GET  /icon            today's icon
  GET  /api/state       settings + status, polled by the page
  GET  /api/month?y=&m= a civil month of the old calendar: fasts, feasts, saints
  POST /api/set         {"key", "value"}
  POST /api/do          {"action"}: break | listen | today | stop | toggle | hear
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import settings

UI = settings.DATA / "ui"
FONTS = settings.DATA / "fonts"
PROFILE = settings.HOME / "window"
EDGE = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe"]


class Server:
    """state() -> dict and handle(kind, payload) are given by the app; handle runs
    on the server thread and must only queue work for the Tk thread."""

    def __init__(self, state, handle, icon_path, month) -> None:
        self.token = secrets.token_urlsafe(16)
        self.state, self.handle, self.icon_path, self.month = state, handle, icon_path, month
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _send(self, code: int, body: bytes, kind: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _ok(self) -> bool:
                url = urlparse(self.path)
                token = parse_qs(url.query).get("t", [""])[0] or self.headers.get("X-Token", "")
                return secrets.compare_digest(token, server.token)

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path.startswith("/fonts/"):
                    f = FONTS / Path(path).name
                    if f.is_file():
                        return self._send(200, f.read_bytes(), "font/ttf")
                    return self._send(404, b"", "text/plain")
                if not self._ok():
                    return self._send(403, b"", "text/plain")
                if path == "/":
                    return self._send(200, (UI / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/icon":
                    f = Path(server.icon_path())
                    return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "image/jpeg")
                if path == "/favicon.png":
                    from PIL import Image
                    import io

                    buf = io.BytesIO()
                    Image.open(server.icon_path()).convert("RGB").resize((64, 82)).save(buf, "PNG")
                    return self._send(200, buf.getvalue(), "image/png")
                if path == "/api/state":
                    return self._send(200, json.dumps(server.state()).encode(), "application/json")
                if path == "/api/month":
                    q = parse_qs(urlparse(self.path).query)
                    try:
                        year, month = int(q["y"][0]), int(q["m"][0])
                    except (KeyError, ValueError):
                        return self._send(400, b"", "text/plain")
                    return self._send(200, json.dumps(server.month(year, month)).encode(), "application/json")
                self._send(404, b"", "text/plain")

            def do_POST(self) -> None:
                if not self._ok():
                    return self._send(403, b"", "text/plain")
                size = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(size) or b"{}")
                except ValueError:
                    return self._send(400, b"", "text/plain")
                kind = urlparse(self.path).path.rsplit("/", 1)[-1]
                server.handle(kind, payload)
                self._send(200, b"{}", "application/json")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="reader-ui", daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def open_window(self) -> None:
        edge = next((str(p) for p in EDGE if p.is_file()), None) or shutil.which("msedge")
        if not edge:
            os.startfile(self.url)  # no Edge: the default browser
            return
        PROFILE.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([edge, f"--app={self.url}", f"--user-data-dir={PROFILE}", "--window-size=560,900",
                          "--no-first-run", "--disable-features=Translate", "--no-default-browser-check"],
                         creationflags=0x08000000)  # CREATE_NO_WINDOW

    def close(self) -> None:
        self.httpd.shutdown()
