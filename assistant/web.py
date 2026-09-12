"""Local web chat for the mobility assistant (standard library only).

  python assistant/web.py [--port 8000] [--offline]    then open http://localhost:8000
"""
import argparse
import json
import sys
import threading
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import MobilityAssistant  # noqa: E402

PAGE = (Path(__file__).resolve().parent / "chat.html").read_text()


def to_json(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return str(v)


class Handler(BaseHTTPRequestHandler):
    bot: MobilityAssistant = None
    lock = threading.Lock()          # one DuckDB connection + one conversation -> serialise requests

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE.replace("__MODE__", self.bot.mode), "text/html")
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path not in ("/api/ask", "/api/reset"):
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 10_000:
                return self._send(413, json.dumps({"error": "question too long"}))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, json.dumps({"error": "malformed request"}))
        with self.lock:
            if self.path == "/api/reset":
                self.bot.reset()
                return self._send(200, json.dumps({"ok": True}))
            question = str(payload.get("question", ""))[:2000]
            r = self.bot.ask(question)
        self._send(200, json.dumps({"text": r.text, "kind": r.kind, "sql": r.sql, "columns": r.columns,
                                    "rows": r.rows[:50], "total_rows": len(r.rows), "mode": self.bot.mode}, default=to_json))

    def log_message(self, fmt, *args):   # keep the terminal quiet
        pass


def make_server(port=8000, offline=False):
    Handler.bot = MobilityAssistant("offline" if offline else "auto")
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    srv = make_server(a.port, a.offline)
    print(f"Mobility Assistant ({Handler.bot.mode}) on http://localhost:{a.port}  - Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
