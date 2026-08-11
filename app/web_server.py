"""정적 UI(/screen, /admin)와 REST API를 제공하는 HTTP 서버이다."""

import json
import socket
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Callable

from app.app_paths import repo_root

ROOT = repo_root()
WEB_ROOT = ROOT / "web"
GAME_UI = WEB_ROOT / "screen"
ADMIN_UI = WEB_ROOT / "admin"
TABLE_UI = ADMIN_UI / "table"
ADMIN_CONTROL_UI = ADMIN_UI / "control"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
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
    *,
    get_status: Callable[[], dict] | None = None,
    get_game_state: Callable[[], dict] | None = None,
    get_melody: Callable[[], dict] | None = None,
    get_cells: Callable[[], dict] | None = None,
    get_events: Callable[[], list] | None = None,
    on_game_start=None,
    on_game_end=None,
    on_game_reset=None,
    on_game_listen=None,
    on_game_play=None,
    on_game_dfesta=None,
    on_admin_collect_orbs=None,
    on_admin_auto_complete=None,
    on_admin_rescan_devices=None,
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

        def _serve_asset(self, base: Path, subpath: str) -> None:
            """상위 폴더 이탈을 막고 UI 정적 파일을 전송한다."""
            parts = subpath.replace("\\", "/").lstrip("/").split("/")
            if not subpath or ".." in parts:
                self._send(404, b"Not found", "text/plain")
                return
            root = base.resolve()
            path = (root / Path(*parts)).resolve()
            if not path.is_relative_to(root):
                self._send(404, b"Not found", "text/plain")
                return
            self._serve_file(path)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?", 1)[0]

            if path in ("/", ""):
                self.send_response(302)
                self.send_header("Location", "/admin")
                self.end_headers()
                return

            if path == "/api/voices":
                from app.voice_catalog import voice_api_payload

                self._json(200, voice_api_payload())
                return

            if path == "/api/admin/midi/status":
                try:
                    from tools.midi_debug import status_payload

                    self._json(200, status_payload())
                except Exception as exc:
                    self._json(500, {"ok": False, "error": str(exc)})
                return

            if path == "/api/orbs":
                from app.data_store import load_mapping

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

            if path == "/api/melody" and get_melody:
                self._json(200, get_melody())
                return

            if path == "/api/cells" and get_cells:
                self._json(200, get_cells())
                return

            if path == "/api/events" and get_events:
                self._json(200, get_events())
                return

            if path in ("/admin", "/admin/"):
                self._serve_file(ADMIN_UI / "index.html")
                return

            if path in ("/table", "/table/"):
                self.send_response(302)
                self.send_header("Location", "/admin")
                self.end_headers()
                return

            if path in ("/screen", "/screen/"):
                self._serve_file(GAME_UI / "index.html")
                return

            if path in ("/admin/table", "/admin/table/"):
                self._serve_file(TABLE_UI / "index.html")
                return

            if path in ("/admin/control", "/admin/control/"):
                self._serve_file(ADMIN_CONTROL_UI / "index.html")
                return

            if path.startswith("/admin/"):
                rel = path[len("/admin/"):]
                self._serve_asset(ADMIN_UI, rel)
                return

            if path.startswith("/screen/"):
                rel = path[len("/screen/"):]
                self._serve_asset(GAME_UI, rel)
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

            if self.path == "/api/game/end":
                self._run_callback(on_game_end)
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

            if self.path == "/api/game/dfesta":
                self._run_callback(on_game_dfesta)
                return

            if self.path == "/api/admin/orbs/collect":
                self._run_callback(on_admin_collect_orbs)
                return

            if self.path == "/api/admin/game/auto-complete":
                self._run_callback(on_admin_auto_complete)
                return

            if self.path == "/api/admin/devices/rescan":
                self._run_callback(on_admin_rescan_devices)
                return

            if self.path == "/api/admin/midi/reconnect":
                from tools.midi_debug import reconnect

                self._run_callback(reconnect)
                return

            if self.path == "/api/admin/midi/test":
                from tools.midi_debug import start_test

                self._run_callback(start_test, data)
                return

            if self.path == "/api/admin/midi/stop":
                from tools.midi_debug import stop_test

                self._run_callback(stop_test)
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
        print(f"[http] http://{host}:{port}/admin")
        print(f"[http] http://{host}:{port}/screen")
        try:
            server.serve_forever()
        except Exception:
            print("[http] ERROR: HTTP 서버 중단")
            traceback.print_exc()

    threading.Thread(target=run, daemon=True).start()
