"""HTTP server — static UI (/table, /screen, /admin) + REST API."""

import json
import socket
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Callable

from app_paths import melodies_dir, repo_root, sounds_dir

ROOT = repo_root()
SERVER_DIR = Path(__file__).resolve().parent
SOUNDS_DIR = sounds_dir()
TABLE_UI = ROOT / "table"
GAME_UI = ROOT / "screen"
ADMIN_UI = ROOT / "admin"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".webm": "video/webm",
}


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def start_http_server(
    cfg: dict,
    on_event,
    orb_mapping: dict,
    *,
    get_status: Callable[[], dict] | None = None,
    get_game_state: Callable[[], dict] | None = None,
    get_cells: Callable[[], dict] | None = None,
    get_events: Callable[[], list] | None = None,
    on_game_start=None,
    on_game_start_complete=None,
    on_game_end=None,
    on_game_reset=None,
    on_game_listen=None,
    on_game_play=None,
    on_game_dispensa=None,
) -> None:
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8080)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"[http] {args[0]}")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            # UI(HTML)·실시간 API(JSON)는 캐시 금지 — 코드 수정 후 새로고침이 바로 반영되게
            if content_type.startswith("text/html") or content_type.startswith("application/json"):
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _run_callback(self, callback, *args) -> None:
            if callback is None:
                self._json(501, {"ok": False, "error": "action unavailable"})
                return
            try:
                result = callback(*args)
            except (ValueError, RuntimeError) as exc:
                self._json(409, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                traceback.print_exc()
                self._json(500, {"ok": False, "error": str(exc)})
                return
            self._json(200, result if isinstance(result, dict) else {"ok": True})

        def _serve_file(self, path: Path) -> None:
            if not path.is_file():
                self._send(404, b"Not found", "text/plain")
                return
            body = path.read_bytes()
            self._send(200, body, MIME.get(path.suffix, "application/octet-stream"))

        def _serve_screen_asset(self, subpath: str) -> None:
            if subpath.startswith("melodies/"):
                name = subpath[len("melodies/") :]
                user = melodies_dir() / name
                if user.is_file():
                    self._serve_file(user)
                    return
                bundled = repo_root() / "screen" / "melodies" / name
                self._serve_file(bundled)
                return
            self._serve_file(GAME_UI / subpath)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def _serve_sound(self, rel: str) -> None:
            rel = rel.replace("\\", "/").lstrip("/")
            if not rel or ".." in rel.split("/"):
                self._send(404, b"Not found", "text/plain")
                return
            base = SOUNDS_DIR.resolve()
            path = (base / rel).resolve()
            if not str(path).startswith(str(base)):
                self._send(404, b"Not found", "text/plain")
                return
            self._serve_file(path)

        def do_GET(self):
            path = self.path.split("?", 1)[0]

            if path in ("/", ""):
                self.send_response(302)
                self.send_header("Location", "/admin")
                self.end_headers()
                return

            if path == "/api/voices":
                from voice_store import voice_api_payload

                self._json(200, voice_api_payload())
                return

            if path == "/api/orbs":
                from mapping_store import load_mapping

                mapping = load_mapping()
                orbs = [{"id": oid, **info} for oid, info in mapping.items()]
                self._json(200, orbs)
                return

            if path == "/api/status" and get_status:
                self._json(200, get_status())
                return

            if path == "/api/game/state" and get_game_state:
                self._json(200, get_game_state())
                return

            if path == "/api/cells" and get_cells:
                self._json(200, get_cells())
                return

            if path == "/api/events" and get_events:
                self._json(200, get_events())
                return

            if path.startswith("/sounds/"):
                self._serve_sound(path[len("/sounds/"):])
                return

            if path in ("/admin", "/admin/"):
                self._serve_file(ADMIN_UI / "index.html")
                return

            if path in ("/table", "/table/"):
                self._serve_file(TABLE_UI / "index.html")
                return

            if path in ("/screen", "/screen/"):
                self._serve_file(GAME_UI / "index.html")
                return

            if path.startswith("/admin/"):
                rel = path[len("/admin/"):]
                self._serve_file(ADMIN_UI / rel)
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
                try:
                    result = on_event(data)
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:
                    traceback.print_exc()
                    self._json(500, {"ok": False, "error": str(exc)})
                    return
                self._json(200, result if isinstance(result, dict) else {"ok": True})
                return

            if self.path == "/api/game/start":
                self._run_callback(on_game_start)
                return

            if self.path == "/api/game/start-complete":
                self._run_callback(on_game_start_complete)
                return

            if self.path == "/api/game/end":
                reason = data.get("reason", "complete")
                self._run_callback(on_game_end, reason)
                return

            if self.path == "/api/game/reset":
                self._run_callback(on_game_reset)
                return

            if self.path == "/api/game/listen":
                self._run_callback(on_game_listen)
                return

            if self.path == "/api/game/play":
                self._run_callback(on_game_play)
                return

            if self.path == "/api/game/dispensa":
                self._run_callback(on_game_dispensa)
                return

            if self.path == "/api/midi/note":
                try:
                    from midi_out import LISTEN_VOICE, note_name_to_pitch, trigger as midi_trigger

                    voice = str(data.get("voice") or LISTEN_VOICE)
                    pitch = data.get("pitch")
                    if pitch is None and data.get("note"):
                        pitch = note_name_to_pitch(str(data["note"]))
                    if pitch is None:
                        pitch = 0
                    duration_ms = float(data.get("duration_ms", 320))
                    velocity = int(data.get("velocity", 127))
                    result = midi_trigger(
                        voice,
                        int(pitch),
                        velocity=velocity,
                        duration_ms=duration_ms,
                    )
                    self._json(200 if result.get("ok") else 400, result)
                except Exception as exc:
                    self._json(500, {"ok": False, "error": str(exc)})
                return

            if self.path == "/api/midi/notes":
                try:
                    from midi_out import trigger_many

                    raw = data.get("notes")
                    if not isinstance(raw, list) or not raw:
                        self._json(400, {"ok": False, "error": "notes[] required"})
                        return
                    results = trigger_many(raw)
                    ok = all(r.get("ok") for r in results)
                    self._json(200 if ok else 207, {"ok": ok, "results": results})
                except Exception as exc:
                    self._json(500, {"ok": False, "error": str(exc)})
                return

            self._send(404, b"Not found", "text/plain")

    def run():
        if _port_in_use(host, port):
            print(f"[http] ERROR: 포트 {port}이(가) 이미 사용 중입니다.")
            print(f"  기존 main.py를 종료하세요: netstat -ano | findstr :{port}")
            print(f"  그다음: taskkill /PID <pid> /F")
            return
        try:
            server = _ThreadingHTTPServer((host, port), Handler)
        except OSError as e:
            print(f"[http] ERROR: HTTP 서버 시작 실패 ({host}:{port}): {e}")
            traceback.print_exc()
            return
        print(f"[http] http://{host}:{port}/admin  (debug)")
        print(f"[http] http://{host}:{port}/table  (virtual table)")
        print(f"[http] http://{host}:{port}/screen (melody game)")
        try:
            server.serve_forever()
        except Exception:
            print("[http] ERROR: HTTP 서버 중단")
            traceback.print_exc()

    threading.Thread(target=run, daemon=True).start()


# backward-compatible alias
start_mock_server = start_http_server
