"""Local HTTP interface.

The UI is a web page rather than a native toolkit so that macOS and Windows get
byte-identical behaviour with no tcl/tk or Qt packaging to go wrong.

The server binds to loopback only and additionally requires a per-run token,
and rejects requests whose Host header is not loopback.  That stops any web
page you happen to have open from driving your microphone via DNS rebinding.
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as cfg_mod
from . import devices as devices_mod
from .engine import MONO_SOURCES, SUBTYPES, EngineError, RecorderEngine

WEB_ROOT = Path(__file__).parent / "web"
_HOST_OK = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", re.I)


class AppState:
    def __init__(self, engine: RecorderEngine, cfg: dict, token: str):
        self.engine = engine
        self.cfg = cfg
        self.token = token
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "tbone-recorder"
    protocol_version = "HTTP/1.1"
    state: AppState = None  # type: ignore[assignment]

    # ------------------------------------------------------------- plumbing

    def log_message(self, fmt, *args):  # noqa: ARG002
        pass  # the console belongs to the user, not to access logs

    def _authorised(self) -> bool:
        host = self.headers.get("Host", "")
        if not _HOST_OK.match(host):
            return False
        qs = urllib.parse.urlparse(self.path).query
        token = urllib.parse.parse_qs(qs).get("t", [None])[0]
        token = token or self.headers.get("X-Auth-Token")
        return bool(token) and secrets.compare_digest(token, self.state.token)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code: int = 200):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _error(self, message: str, code: int = 400):
        self._json({"ok": False, "error": message}, code)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    # ------------------------------------------------------------------ GET

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            if not self._authorised():
                self._send(403, b"Forbidden: open the URL printed in your terminal.", "text/plain")
                return
            self._serve_static("index.html")
            return

        if path in ("/app.js", "/style.css"):
            self._serve_static(path.lstrip("/"))
            return

        if not self._authorised():
            self._error("forbidden", 403)
            return

        if path == "/api/devices":
            self._api_devices()
        elif path == "/api/status":
            self._json({"ok": True, **self._status_payload()})
        elif path == "/api/stream":
            self._sse()
        elif path == "/api/recordings":
            self._json({"ok": True, "recordings": self._list_recordings()})
        elif path.startswith("/api/audio/"):
            self._serve_audio(urllib.parse.unquote(path[len("/api/audio/"):]))
        else:
            self._error("not found", 404)

    def _serve_static(self, name: str):
        target = (WEB_ROOT / name).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    # ----------------------------------------------------------------- POST

    def do_POST(self):  # noqa: N802
        if not self._authorised():
            self._error("forbidden", 403)
            return
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        engine = self.state.engine

        try:
            if path == "/api/open":
                self._api_open(body)
            elif path == "/api/close":
                engine.close()
                self._json({"ok": True, **self._status_payload()})
            elif path == "/api/record/start":
                self._api_record_start(body)
            elif path == "/api/record/stop":
                path_done = engine.stop_recording()
                self._json(
                    {
                        "ok": True,
                        "saved": path_done.name if path_done else None,
                        **self._status_payload(),
                    }
                )
            elif path == "/api/settings":
                self._api_settings(body)
            elif path == "/api/reveal":
                self._api_reveal(body)
            elif path == "/api/delete":
                self._api_delete(body)
            elif path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=self._shutdown, daemon=True).start()
            else:
                self._error("not found", 404)
        except EngineError as exc:
            self._error(str(exc))
        except Exception as exc:  # last-resort guard; UI shows the message
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _shutdown(self):
        time.sleep(0.3)
        self.state.engine.close()
        self.server.shutdown()

    # ------------------------------------------------------------- handlers

    def _api_devices(self):
        try:
            devs = devices_mod.list_input_devices()
        except RuntimeError as exc:
            self._error(str(exc), 500)
            return
        current = self.state.engine.device
        suggested = devices_mod.pick_default_device(devs)
        for dev in devs:
            dev["samplerates"] = devices_mod.supported_samplerates(dev["index"])
        self._json(
            {
                "ok": True,
                "devices": devs,
                "current": current,
                "suggested": suggested,
                "permission_hint": devices_mod.permission_hint(),
            }
        )

    def _api_open(self, body: dict):
        device = body.get("device")
        if device is None:
            self._error("no device given")
            return
        samplerate = int(body.get("samplerate") or 48000)
        self.state.engine.open(int(device), samplerate)
        info = devices_mod.describe_device(int(device))
        with self.state.lock:
            self.state.cfg["device_name"] = info["name"] if info else None
            self.state.cfg["device_host_api"] = info["host_api"] if info else None
            self.state.cfg["samplerate"] = samplerate
            cfg_mod.save(self.state.cfg)
        self._json({"ok": True, **self._status_payload()})

    def _api_record_start(self, body: dict):
        channels = int(body.get("channels") or self.state.cfg.get("channels") or 1)
        subtype = body.get("subtype") or self.state.cfg.get("subtype") or "PCM_24"
        mono_source = body.get("mono_source") or self.state.cfg.get("mono_source") or "left"
        name = body.get("name") or None
        path = self.state.engine.start_recording(
            channels=channels, subtype=subtype, mono_source=mono_source, name=name
        )
        with self.state.lock:
            self.state.cfg.update(
                {"channels": channels, "subtype": subtype, "mono_source": mono_source}
            )
            cfg_mod.save(self.state.cfg)
        self._json({"ok": True, "file": path.name, **self._status_payload()})

    def _api_settings(self, body: dict):
        with self.state.lock:
            for key in ("channels", "subtype", "mono_source", "samplerate"):
                if key in body:
                    self.state.cfg[key] = body[key]
            if body.get("outdir"):
                new_dir = Path(body["outdir"]).expanduser()
                try:
                    new_dir.mkdir(parents=True, exist_ok=True)
                except Exception as exc:
                    self._error(f"Cannot use that folder: {exc}")
                    return
                self.state.cfg["outdir"] = str(new_dir)
                self.state.engine.outdir = new_dir
            cfg_mod.save(self.state.cfg)
        self._json({"ok": True, **self._status_payload()})

    def _safe_recording(self, name: str) -> Path | None:
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        target = (self.state.engine.outdir / name).resolve()
        root = Path(self.state.engine.outdir).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            return None
        return target

    def _api_reveal(self, body: dict):
        name = body.get("name")
        target = self._safe_recording(name) if name else Path(self.state.engine.outdir)
        if target is None:
            self._error("no such recording", 404)
            return
        try:
            if sys.platform == "darwin":
                # -R reveals and selects the file; without a name we open the folder.
                cmd = ["open", "-R", str(target)] if name else ["open", str(target)]
                subprocess.Popen(cmd)
            elif sys.platform.startswith("win"):
                if name:
                    subprocess.Popen(["explorer", f"/select,{target}"])
                else:
                    subprocess.Popen(["explorer", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target.parent if name else target)])
        except Exception as exc:
            self._error(f"Could not open the file manager: {exc}")
            return
        self._json({"ok": True})

    def _api_delete(self, body: dict):
        target = self._safe_recording(body.get("name", ""))
        if target is None:
            self._error("no such recording", 404)
            return
        if self.state.engine.record_path and target == self.state.engine.record_path:
            self._error("that file is still recording")
            return
        try:
            target.unlink()
        except Exception as exc:
            self._error(str(exc))
            return
        self._json({"ok": True, "recordings": self._list_recordings()})

    # -------------------------------------------------------------- helpers

    def _status_payload(self) -> dict:
        st = self.state.engine.status()
        st["outdir"] = str(self.state.engine.outdir)
        st["subtypes"] = SUBTYPES
        st["mono_sources"] = list(MONO_SOURCES)
        return st

    def _list_recordings(self) -> list[dict]:
        out = []
        try:
            for f in sorted(
                Path(self.state.engine.outdir).glob("*.wav"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:100]:
                stat = f.stat()
                out.append(
                    {
                        "name": f.name,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "recording": bool(
                            self.state.engine.record_path
                            and f == self.state.engine.record_path
                        ),
                    }
                )
        except Exception:
            pass
        return out

    def _serve_audio(self, name: str):
        target = self._safe_recording(name)
        if target is None:
            self._send(404, b"not found", "text/plain")
            return
        data = target.read_bytes()
        total = len(data)
        rng = self.headers.get("Range")
        # Safari refuses to play <audio> without byte-range support.
        if rng and rng.startswith("bytes="):
            try:
                start_s, _, end_s = rng[6:].partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else total - 1
                start = max(0, min(start, total - 1))
                end = max(start, min(end, total - 1))
                chunk = data[start : end + 1]
                self._send(
                    206,
                    chunk,
                    "audio/wav",
                    {
                        "Content-Range": f"bytes {start}-{end}/{total}",
                        "Accept-Ranges": "bytes",
                    },
                )
                return
            except Exception:
                pass
        self._send(200, data, "audio/wav", {"Accept-Ranges": "bytes"})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(self._status_payload())
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.08)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def serve(engine: RecorderEngine, cfg: dict, host: str = "127.0.0.1", port: int = 0):
    token = secrets.token_urlsafe(16)
    handler = type("BoundHandler", (Handler,), {"state": AppState(engine, cfg, token)})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd, token
