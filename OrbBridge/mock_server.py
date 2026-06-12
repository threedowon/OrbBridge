"""Mock hardware server — HTTP ingest + static UI for /table and /screen."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_UI = ROOT / "table-ui"
GAME_UI = ROOT / "game-ui"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


def start_mock_server(cfg: dict, on_event, orb_mapping: dict,
                      on_game_start=None, on_game_end=None,
                      on_game_listen=None, on_game_play=None) -> None:
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8080)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"[mock] {args[0]}")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _serve_file(self, path: Path) -> None:
            if not path.is_file():
                self._send(404, b"Not found", "text/plain")
                return
            body = path.read_bytes()
            self._send(200, body, MIME.get(path.suffix, "application/octet-stream"))

        def _serve_screen_asset(self, subpath: str) -> None:
            self._serve_file(GAME_UI / subpath)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?", 1)[0]

            if path == "/api/orbs":
                orbs = [{"id": oid, **info} for oid, info in orb_mapping.items()]
                self._json(200, orbs)
                return

            if path in ("/table", "/table/"):
                self._serve_file(TABLE_UI / "index.html")
                return

            if path in ("/screen", "/screen/"):
                self._serve_file(GAME_UI / "index.html")
                return

            if path.startswith("/table/"):
                rel = path[len("/table/"):]
                self._serve_file(TABLE_UI / rel)
                return

            if path.startswith("/screen/"):
                rel = path[len("/screen/"):]
                self._serve_screen_asset(rel)
                return

            self._send(404, b"Not found", "text/plain")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid JSON"})
                return

            if self.path == "/api/event":
                required = ("type", "slave", "cell", "id")
                if not all(k in data for k in required):
                    self._json(400, {"error": f"required fields: {required}"})
                    return
                if data["type"] not in ("added", "removed"):
                    self._json(400, {"error": "type must be added or removed"})
                    return
                on_event(data)
                self._json(200, {"ok": True})
                return

            if self.path == "/api/game/start":
                if on_game_start:
                    on_game_start()
                self._json(200, {"ok": True})
                return

            if self.path == "/api/game/end":
                reason = data.get("reason", "complete")
                if on_game_end:
                    on_game_end(reason)
                self._json(200, {"ok": True})
                return

            if self.path == "/api/game/listen":
                if on_game_listen:
                    on_game_listen()
                self._json(200, {"ok": True})
                return

            if self.path == "/api/game/play":
                if on_game_play:
                    on_game_play()
                self._json(200, {"ok": True})
                return

            self._send(404, b"Not found", "text/plain")

    def run():
        server = HTTPServer((host, port), Handler)
        print(f"[mock] http://{host}:{port}/table  (fake hardware)")
        print(f"[mock] http://{host}:{port}/screen (melody game)")
        server.serve_forever()

    threading.Thread(target=run, daemon=True).start()
