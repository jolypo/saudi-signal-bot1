from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_state = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "last_scan_started": None,
    "last_scan_finished": None,
    "last_scan_ok": None,
    "last_scan_error": None,
    "last_signal": None,
    "last_trade_monitor": None,
}
_lock = threading.Lock()
_server = None


def update_state(**kwargs):
    with _lock:
        _state.update(kwargs)


def get_state():
    with _lock:
        return dict(_state)


class HealthHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/health", "/healthz"):
            self._json(200, {"status": "ok", "service": "saudi-signal-bot", **get_state()})
            return
        if self.path == "/status":
            self._json(200, get_state())
            return
        self._json(404, {"status": "not_found"})

    def log_message(self, fmt, *args):
        return


def start_health_server():
    global _server
    port = int(os.getenv("PORT", "10000"))
    _server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=_server.serve_forever, name="health-server", daemon=True)
    thread.start()
    return _server
