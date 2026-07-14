"""
OrbBridge 진단 도구 (Diagnostics)

마스터 COM 포트를 직접 열어 RS485 → USB 로 들어오는 JSON 을 모니터링하고,
웹 대시보드로 다음을 보여준다.

  - PC 에 잡힌 모든 COM 포트 목록 (ESP32-S3 로 추정되는 포트 표시)
  - 마스터 연결 상태 (어느 COM 포트, 열림/끊김, 마지막 수신 시각)
  - 슬레이브별 통신 상태 (SLAVE_ID, 마지막 신호, 메시지 수, 활성 셀)
  - 셀 점유 현황 / 원시 로그

구조 메모:
  [Slave] ──RS485 9600──► [Master] ──USB──► PC (이 도구가 여는 COM 포트)
  슬레이브는 PC 에 COM 으로 잡히지 않는다. 마스터를 통해 들어오는 JSON 의
  "slave" 필드(SLAVE_ID)로만 감지한다.
  펌웨어에 heartbeat 가 없어서, 오브를 올리거나 뺄 때만 신호가 온다.
  → 슬레이브 상태는 "마지막 신호 이후 경과 시간"으로 판단한다.

사용:
  python debug.py                 # config.json 의 serial.port 로 자동 연결
  python debug.py --port COM23    # 포트 지정
  python debug.py --no-open       # 포트 안 열고 대시보드만 (포트 목록 확인용)

  브라우저: http://127.0.0.1:8090/

주의: server/main.py 가 실행 중이면 같은 COM 포트를 동시에 못 연다.
      진단할 때는 main.py 를 잠깐 종료하고 이 도구를 실행한다.
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

import serial
import serial.tools.list_ports

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE.parent / "server" / "config.json"
INDEX_HTML = HERE / "index.html"

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8090

# 슬레이브를 "최근 활동"으로 볼 시간 창(초). 이 안에 신호가 왔으면 최근 활동.
RECENT_WINDOW_S = 10.0
# 하트비트 기반 online/offline 판정 창(초). 슬레이브 하트비트 주기(3초)의 2~3배.
# 이 시간 안에 하트비트가 없으면 offline 으로 표시.
ONLINE_TIMEOUT_S = 8.0
RAW_LOG_LIMIT = 300

# Espressif 등 ESP32-S3 로 흔히 잡히는 USB-Serial 칩 VID
ESP_LIKELY_VIDS = {
    0x303A,  # Espressif (S2/S3 native USB CDC)
    0x1A86,  # QinHeng CH340/CH9102
    0x10C4,  # Silicon Labs CP210x
    0x0403,  # FTDI
}


def now_ms() -> float:
    return time.time() * 1000.0


# ── 공유 상태 ────────────────────────────────────────────
_lock = threading.Lock()

_master = {
    "port": "",
    "baud": 0,
    "open": False,
    "error": "",
    "last_rx_ms": 0.0,     # 마지막으로 아무 줄이나 받은 시각
    "rx_lines": 0,         # 받은 전체 줄 수
    "json_lines": 0,       # JSON 으로 파싱된 줄 수
    "noise_lines": 0,      # JSON 이 아닌 줄(부트 로그 등)
    "started_ms": now_ms(),
}

# slave_id -> stats
_slaves: dict[int, dict] = {}
# "slave:cell" -> cell info
_cells: dict[str, dict] = {}
_raw_log: list[dict] = []


def _touch_slave(slave_id: int) -> dict:
    s = _slaves.get(slave_id)
    if s is None:
        s = {
            "slave": slave_id,
            "first_seen_ms": now_ms(),
            "last_seen_ms": now_ms(),
            "last_heartbeat_ms": 0.0,
            "heartbeat_count": 0,
            "msg_count": 0,
            "added_count": 0,
            "removed_count": 0,
            "active_cells": set(),
        }
        _slaves[slave_id] = s
    return s


def _record_raw(line: str, kind: str) -> None:
    _raw_log.append({"ts": now_ms(), "kind": kind, "line": line[:400]})
    if len(_raw_log) > RAW_LOG_LIMIT:
        del _raw_log[: len(_raw_log) - RAW_LOG_LIMIT]


def _handle_line(line: str) -> None:
    with _lock:
        _master["last_rx_ms"] = now_ms()
        _master["rx_lines"] += 1

        if not line.startswith("{"):
            _master["noise_lines"] += 1
            _record_raw(line, "noise")
            return

        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            _master["noise_lines"] += 1
            _record_raw(line, "badjson")
            return

        _master["json_lines"] += 1
        _record_raw(line, "json")

        t = evt.get("type")

        # 게임 버튼 이벤트(마스터가 직접 보냄) — slave 필드 없음
        if t == "game":
            return

        slave_id = evt.get("slave")
        cell = evt.get("cell")
        if slave_id is None:
            return

        slave_id = int(slave_id)
        s = _touch_slave(slave_id)
        s["last_seen_ms"] = now_ms()

        # 하트비트: 생존 신호 — 이벤트 카운터는 건드리지 않고 online 판정만 갱신
        if t == "heartbeat":
            s["last_heartbeat_ms"] = now_ms()
            s["heartbeat_count"] += 1
            return

        s["msg_count"] += 1

        key = f"{slave_id}:{cell}"
        if t == "added":
            s["added_count"] += 1
            if cell is not None:
                s["active_cells"].add(cell)
                _cells[key] = {
                    "slave": slave_id,
                    "cell": cell,
                    "id": evt.get("id", ""),
                    "since_ms": now_ms(),
                }
        elif t == "removed":
            s["removed_count"] += 1
            if cell is not None:
                s["active_cells"].discard(cell)
                _cells.pop(key, None)


def _snapshot() -> dict:
    with _lock:
        t = now_ms()
        slaves = []
        for sid in sorted(_slaves):
            s = _slaves[sid]
            age = t - s["last_seen_ms"]
            has_hb = s["last_heartbeat_ms"] > 0
            hb_age = (t - s["last_heartbeat_ms"]) if has_hb else None
            online = has_hb and hb_age <= ONLINE_TIMEOUT_S * 1000
            slaves.append({
                "slave": s["slave"],
                "msg_count": s["msg_count"],
                "added_count": s["added_count"],
                "removed_count": s["removed_count"],
                "active_cells": sorted(s["active_cells"]),
                "last_seen_ms": s["last_seen_ms"],
                "age_ms": age,
                "recent": age <= RECENT_WINDOW_S * 1000,
                "has_heartbeat": has_hb,
                "heartbeat_count": s["heartbeat_count"],
                "last_heartbeat_ms": s["last_heartbeat_ms"],
                "hb_age_ms": hb_age,
                "online": online,
            })

        master = dict(_master)
        master["last_rx_age_ms"] = (t - master["last_rx_ms"]) if master["last_rx_ms"] else None

        cells = sorted(_cells.values(), key=lambda c: (c["slave"], c["cell"]))
        raw = list(_raw_log[-120:])

        return {
            "now_ms": t,
            "recent_window_s": RECENT_WINDOW_S,
            "online_timeout_s": ONLINE_TIMEOUT_S,
            "master": master,
            "slaves": slaves,
            "cells": cells,
            "raw": raw,
        }


def list_serial_ports() -> list[dict]:
    ports = []
    for p in serial.tools.list_ports.comports():
        vid = p.vid
        likely_esp = vid in ESP_LIKELY_VIDS if vid is not None else False
        ports.append({
            "device": p.device,
            "description": p.description or "",
            "manufacturer": p.manufacturer or "",
            "hwid": p.hwid or "",
            "vid": f"{vid:04X}" if vid is not None else None,
            "pid": f"{p.pid:04X}" if p.pid is not None else None,
            "likely_esp": likely_esp,
            "is_master": p.device == _master["port"],
        })
    return ports


# ── 시리얼 수신 스레드 ───────────────────────────────────
_serial_stop = threading.Event()
_serial_thread: threading.Thread | None = None


def _serial_loop(port: str, baud: int) -> None:
    with _lock:
        _master.update({"port": port, "baud": baud, "open": False, "error": "연결 중…"})

    while not _serial_stop.is_set():
        try:
            ser = serial.Serial(port, baud, timeout=1)
        except serial.SerialException as e:
            with _lock:
                _master.update({"open": False, "error": str(e)})
            print(f"[serial] 열기 실패 ({port}): {e}")
            if _serial_stop.wait(3):
                return
            continue

        print(f"[serial] 연결됨: {port} @ {baud}")
        with _lock:
            _master.update({"open": True, "error": ""})

        try:
            while not _serial_stop.is_set():
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if line:
                    _handle_line(line)
        except serial.SerialException as e:
            with _lock:
                _master.update({"open": False, "error": str(e)})
            print(f"[serial] 끊김 ({port}): {e}")
        finally:
            try:
                ser.close()
            except Exception:
                pass
        if _serial_stop.wait(3):
            return


def start_serial(port: str, baud: int) -> None:
    global _serial_thread
    stop_serial()
    _serial_stop.clear()
    with _lock:
        # 포트 바꾸면 이전 슬레이브/셀 상태는 유지하되 마스터 카운터만 리셋
        _master.update({
            "port": port, "baud": baud, "open": False, "error": "연결 중…",
            "last_rx_ms": 0.0, "rx_lines": 0, "json_lines": 0, "noise_lines": 0,
            "started_ms": now_ms(),
        })
    _serial_thread = threading.Thread(target=_serial_loop, args=(port, baud), daemon=True)
    _serial_thread.start()


def stop_serial() -> None:
    global _serial_thread
    _serial_stop.set()
    if _serial_thread and _serial_thread.is_alive():
        _serial_thread.join(timeout=2)
    _serial_thread = None


# ── HTTP 서버 ────────────────────────────────────────────
class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 조용히

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            path = self.path.split("?", 1)[0]

            if path in ("/", "/index.html"):
                if INDEX_HTML.is_file():
                    self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(404, b"index.html not found", "text/plain")
                return

            if path == "/api/state":
                self._json(200, _snapshot())
                return

            if path == "/api/ports":
                self._json(200, {"ports": list_serial_ports()})
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

            if self.path == "/api/connect":
                port = data.get("port")
                baud = int(data.get("baud", _master["baud"] or 115200))
                if not port:
                    self._json(400, {"error": "port 필요"})
                    return
                start_serial(port, baud)
                self._json(200, {"ok": True, "port": port, "baud": baud})
                return

            if self.path == "/api/disconnect":
                stop_serial()
                with _lock:
                    _master.update({"open": False, "error": "연결 해제됨"})
                self._json(200, {"ok": True})
                return

            if self.path == "/api/reset":
                with _lock:
                    _slaves.clear()
                    _cells.clear()
                    _raw_log.clear()
                    _master.update({
                        "rx_lines": 0, "json_lines": 0, "noise_lines": 0,
                        "last_rx_ms": 0.0, "started_ms": now_ms(),
                    })
                self._json(200, {"ok": True})
                return

            self._send(404, b"Not found", "text/plain")

    return Handler


def load_default_serial() -> tuple[str, int]:
    port, baud = "", 115200
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        ser = cfg.get("serial", {})
        port = ser.get("port", "")
        baud = int(ser.get("baudrate", 115200))
    except Exception as e:
        print(f"[config] {CONFIG_PATH} 읽기 실패: {e}")
    return port, baud


def main() -> None:
    def_port, def_baud = load_default_serial()

    ap = argparse.ArgumentParser(description="OrbBridge 진단 도구")
    ap.add_argument("--port", default=def_port, help=f"마스터 COM 포트 (기본: {def_port or '없음'})")
    ap.add_argument("--baud", type=int, default=def_baud, help=f"보드레이트 (기본: {def_baud})")
    ap.add_argument("--http-port", type=int, default=HTTP_PORT, help=f"대시보드 포트 (기본: {HTTP_PORT})")
    ap.add_argument("--no-open", action="store_true", help="COM 포트를 열지 않고 대시보드만 (포트 목록 확인용)")
    args = ap.parse_args()

    print("=" * 56)
    print(" OrbBridge 진단 도구")
    print("=" * 56)
    print(" 감지된 COM 포트:")
    for p in list_serial_ports():
        tag = "  ← ESP32 추정" if p["likely_esp"] else ""
        print(f"   {p['device']:8}  {p['description']}{tag}")
    print("-" * 56)

    if not args.no_open and args.port:
        start_serial(args.port, args.baud)
    elif not args.port:
        print(" [알림] 열 COM 포트가 없습니다. 대시보드에서 선택하세요.")

    server = _ThreadingHTTPServer((HTTP_HOST, args.http_port), make_handler())
    url = f"http://{HTTP_HOST}:{args.http_port}/"
    print(f" 대시보드: {url}")
    print(" (Ctrl+C 로 종료)")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료 중…")
    finally:
        stop_serial()
        server.shutdown()


if __name__ == "__main__":
    main()
