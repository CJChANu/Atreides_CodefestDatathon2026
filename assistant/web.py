"""Local web chat for the mobility assistant (standard library only).

  python assistant/web.py [--port 8000] [--host 127.0.0.1] [--offline]   then open http://localhost:8000

Each visitor gets their own conversation, keyed by a session cookie, so follow-up questions never mix
between users. Bind to 127.0.0.1 and put a reverse proxy in front when hosting.
"""
import argparse
import json
import secrets
import sys
import threading
import time
from collections import deque
from datetime import date, datetime
from decimal import Decimal
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import MobilityAssistant  # noqa: E402

PAGE = (Path(__file__).resolve().parent / "chat.html").read_text()
SESSION_TTL = 60 * 60          # forget an idle conversation after an hour
MAX_SESSIONS = 200
RATE_LIMIT = (20, 60)          # at most 20 questions per 60 s per session


def to_json(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return str(v)


class Sessions:
    """Per-visitor conversations. The warehouse connection is shared; only the message history is per session."""

    def __init__(self, offline=False):
        self.offline = offline
        self.shared = MobilityAssistant("offline" if offline else "auto")   # owns the DuckDB connection
        self.mode = self.shared.mode
        self.lock = threading.Lock()
        self.by_id = {}            # sid -> {"messages": [...], "seen": ts, "hits": deque}

    def _sweep(self, now):
        for sid in [s for s, v in self.by_id.items() if now - v["seen"] > SESSION_TTL]:
            del self.by_id[sid]
        while len(self.by_id) > MAX_SESSIONS:                     # oldest first
            del self.by_id[min(self.by_id, key=lambda s: self.by_id[s]["seen"])]

    def ask(self, sid, question):
        now = time.time()
        with self.lock:
            self._sweep(now)
            s = self.by_id.setdefault(sid, {"messages": [], "seen": now, "hits": deque()})
            s["seen"] = now
            while s["hits"] and now - s["hits"][0] > RATE_LIMIT[1]:
                s["hits"].popleft()
            if len(s["hits"]) >= RATE_LIMIT[0]:
                return None
            s["hits"].append(now)
            self.shared.messages = s["messages"]                  # swap this visitor's history in
            try:
                return self.shared.ask(question)
            finally:
                s["messages"] = self.shared.messages
                self.shared.messages = []

    def reset(self, sid):
        with self.lock:
            self.by_id.pop(sid, None)


class Handler(BaseHTTPRequestHandler):
    sessions: Sessions = None
    protocol_version = "HTTP/1.1"

    def _sid(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        sid = cookie["ufa_sid"].value if "ufa_sid" in cookie else ""
        if sid.isalnum() and 16 <= len(sid) <= 64:
            return sid, False
        return secrets.token_hex(16), True

    def _send(self, code, body, ctype="application/json", set_sid=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if set_sid:
            self.send_header("Set-Cookie", f"ufa_sid={set_sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        sid, fresh = self._sid()
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE.replace("__MODE__", self.sessions.mode), "text/html", set_sid=sid if fresh else None)
        if self.path == "/healthz":
            return self._send(200, json.dumps({"ok": True, "mode": self.sessions.mode}))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path not in ("/api/ask", "/api/reset"):
            return self._send(404, json.dumps({"error": "not found"}))
        sid, fresh = self._sid()
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 10_000:
                return self._send(413, json.dumps({"error": "question too long"}))
            payload = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            return self._send(400, json.dumps({"error": "malformed request"}))

        if self.path == "/api/reset":
            self.sessions.reset(sid)
            return self._send(200, json.dumps({"ok": True}), set_sid=sid if fresh else None)

        r = self.sessions.ask(sid, str(payload.get("question", ""))[:2000])
        if r is None:
            return self._send(429, json.dumps({"error": "Too many questions in a short time - please wait a moment."}))
        self._send(200, json.dumps({"text": r.text, "kind": r.kind, "sql": r.sql, "columns": r.columns,
                                    "rows": r.rows[:50], "total_rows": len(r.rows), "mode": self.sessions.mode},
                                   default=to_json), set_sid=sid if fresh else None)

    def log_message(self, fmt, *args):   # keep the terminal quiet
        pass


def make_server(port=8000, offline=False, host="127.0.0.1"):
    Handler.sessions = Sessions(offline)
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="keep this on loopback and use a reverse proxy when hosting")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    srv = make_server(a.port, a.offline, a.host)
    print(f"Mobility Assistant ({Handler.sessions.mode}) on http://{a.host}:{a.port}  - Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
