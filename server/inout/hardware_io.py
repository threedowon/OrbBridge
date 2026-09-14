"""메인 서버가 소유하는 master / slave USB 통신 및 진단 상태."""
from __future__ import annotations

import json
import re
import threading
import time

try:
    import serial
except ImportError:
    serial = None

RECENT_WINDOW_S = 10.0
ONLINE_TIMEOUT_S = 8.0
RAW_LOG_LIMIT = 300
ESP_LIKELY_VIDS = {0x303A, 0x1A86, 0x10C4, 0x0403}

def now_ms() -> float:
    return time.time() * 1000.0


# ── 공유 상태 ────────────────────────────────────────────
_lock = threading.Lock()
_connection_lock = threading.RLock()
_mode = "master"
_connections: dict[str, dict] = {}
_generation = 0

_master = {
    "port": "",
    "baud": 0,
    "open": False,
    "error": "",
    "last_rx_ms": 0.0,     # 마지막으로 아무 줄이나 받은 시각
    "last_heartbeat_ms": 0.0,
    "rx_lines": 0,         # 받은 전체 줄 수
    "json_lines": 0,       # JSON 으로 파싱된 줄 수
    "noise_lines": 0,      # JSON 이 아닌 줄(부트 로그 등)
    "started_ms": now_ms(),
}

# 슬레이브 ID별 통계를 보관한다.
_slaves: dict[int, dict] = {}
# "슬레이브:셀" 키별 셀 정보를 보관한다.
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


def _consume_line(line: str, port: str | None = None, generation: int | None = None) -> None:
    with _lock:
        if generation is not None and generation != _generation:
            return
        source = _connections.get(port) if port is not None else _master
        if source is None:
            return
        source["last_rx_ms"] = now_ms()
        source["rx_lines"] += 1
        if _mode == "master" and re.fullmatch(r"#master alive relay=\d+ bad=\d+", line):
            source["last_heartbeat_ms"] = now_ms()
            bad = int(line.rsplit("bad=", 1)[1])
            previous = source.get("crc_bad")
            source["crc_delta"] = max(0, bad - previous) if previous is not None else 0
            source["crc_bad"] = bad
            _record_raw(line, "heartbeat")
            return

        if not line.startswith("{"):
            identity = re.fullmatch(r"#slave id=(\d+)", line)
            if _mode == "direct" and identity:
                source["reported_slave_id"] = int(identity.group(1))
            source["noise_lines"] += 1
            _record_raw(f"[{port or 'master'}] {line}", "noise")
            return

        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            source["noise_lines"] += 1
            _record_raw(line, "badjson")
            return

        if not isinstance(evt, dict):
            return
        source["json_lines"] += 1
        _record_raw(f"[{port or 'master'}] {line}", "json")

        t = evt.get("type")

        # 게임 버튼 이벤트(마스터가 직접 보냄) — slave 필드 없음
        if t == "game":
            return evt if _mode == "master" else None

        slave_id = evt.get("slave")
        cell = evt.get("cell")
        if slave_id is None:
            return

        if type(slave_id) is not int or not 1 <= slave_id <= 99:
            return
        if t not in ("heartbeat", "added", "removed", "snapshot"):
            return
        if t != "heartbeat" and (type(cell) is not int or not 1 <= cell <= 16):
            return
        if t != "heartbeat" and not isinstance(evt.get("id", ""), str):
            return
        if _mode == "direct":
            if evt.get("transport") != "usb":
                source["error"] = "USB 직접 모드 미지원 — slave 포트와 펌웨어를 확인하세요"
                return
            if source.get("slave_id") not in (None, slave_id):
                source["error"] = "한 포트에서 서로 다른 slave ID 수신 — master 포트인지 확인"
                return
            source["slave_id"] = slave_id
            if any(c is not source and c.get("slave_id") == slave_id for c in _connections.values()):
                source["error"] = f"중복 slave ID {slave_id}: DIP 스위치 확인"
                return
            source["error"] = ""
        s = _touch_slave(slave_id)
        s["port"] = port or ""
        s["last_seen_ms"] = now_ms()

        # 하트비트로 연결 여부와 점유 상태를 갱신한다(이벤트 수는 유지).
        if t == "heartbeat":
            s["last_heartbeat_ms"] = now_ms()
            s["heartbeat_count"] += 1
            if _mode == "direct":
                source["last_heartbeat_ms"] = now_ms()
                source["diagnostics"] = {
                    key: evt[key] for key in ("scan_count", "rom_seen_mask", "crc_error_mask",
                                               "led_effect", "led_commands")
                    if type(evt.get(key)) is int
                }
            state = evt.get("occupancy_mask", evt.get("state"))
            if type(state) is int and 0 <= state <= 65535:
                for slot in range(1, 17):
                    key = f"{slave_id}:{slot}"
                    if state & (1 << (slot - 1)):
                        s["active_cells"].add(slot)
                        _cells.setdefault(key, {"slave": slave_id, "cell": slot,
                                               "id": "", "since_ms": now_ms()})
                    else:
                        s["active_cells"].discard(slot)
                        _cells.pop(key, None)
            return evt

        if t != "snapshot":
            s["msg_count"] += 1

        key = f"{slave_id}:{cell}"
        if t in ("added", "snapshot"):
            if t == "added":
                s["added_count"] += 1
            if cell is not None:
                s["active_cells"].add(cell)
                _cells[key] = {
                    "slave": slave_id,
                    "cell": cell,
                    "id": evt.get("id", ""),
                    "since_ms": _cells.get(key, {}).get("since_ms", now_ms()),
                }
        elif t == "removed":
            s["removed_count"] += 1
            if cell is not None:
                s["active_cells"].discard(cell)
                _cells.pop(key, None)

        return evt

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
            connection = _connections.get(s.get("port"))
            online = online and bool(connection and connection["open"] and not connection["error"])
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
                "port": s.get("port", ""),
            })

        master = {k: v for k, v in _master.items() if k not in ("device", "thread", "stop", "write_lock")}
        master["last_rx_age_ms"] = (t - master["last_rx_ms"]) if master["last_rx_ms"] else None
        master["health"] = _health(master, t) if _mode == "master" else "bypassed"
        connections = [{k: v for k, v in c.items() if k not in ("device", "thread", "stop", "write_lock")}
                       for c in _connections.values()]
        for c in connections:
            c["health"] = _health(c, t)

        cells = sorted(_cells.values(), key=lambda c: (c["slave"], c["cell"]))
        raw = list(_raw_log[-120:])

        return {
            "now_ms": t,
            "recent_window_s": RECENT_WINDOW_S,
            "online_timeout_s": ONLINE_TIMEOUT_S,
            "master": master,
            "mode": _mode,
            "generation": _generation,
            "connections": connections,
            "slaves": slaves,
            "cells": cells,
            "raw": raw,
        }


def list_serial_ports() -> list[dict]:
    ports = []
    with _lock:
        identities = {port: c.get("slave_id") or c.get("reported_slave_id")
                      for port, c in _connections.items() if c["open"]}
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return []
    for p in comports():
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
            "slave_id": identities.get(p.device),
            "is_master": _mode == "master" and p.device == _master["port"],
        })
    return ports


# Serial sessions are owned by one mode and stopped before switching routes.
def _health(c: dict, t: float) -> str:
    if not c["open"]:
        return "disconnected"
    hb = c.get("last_heartbeat_ms", 0)
    if hb and t - hb <= ONLINE_TIMEOUT_S * 1000:
        return "alive"
    if not hb and t - c["started_ms"] <= ONLINE_TIMEOUT_S * 1000:
        return "waiting"
    return "unresponsive"


def _write(c: dict, command: str) -> bool:
    with c["write_lock"]:
        device = c.get("device")
        if device is None:
            return False
        try:
            device.write((command + "\n").encode("ascii"))
            return True
        except (serial.SerialException, OSError) as exc:
            with _lock:
                detail = str(exc)
                c["error"] = (
                    f"{c['port']} 포트가 없습니다. 현재 USB 포트를 선택해 연결하세요."
                    if "FileNotFoundError" in detail else detail
                )
                _record_raw(f"[{c['port']}] {detail}", "error")
            return False


def send_line(command: str) -> bool:
    with _lock:
        targets = list(_connections.values())
        mode = _mode
    if mode == "direct" and not re.fullmatch(r"C[0-9]", command):
        return False  # Master-only D commands never reach slave USB ports.
    results = [_write(c, command) if not c.get("error") and
               (mode == "master" or c.get("ready", False)) else False
               for c in targets]
    return bool(results) and all(results)


def send_led(effect: int) -> bool:
    return send_line(f"C{effect}")


_dispatch_lock = threading.Lock()
on_message = lambda event: None
on_ready = lambda: None


def _handle_line(line: str, port=None, generation=None) -> None:
    # Multiple USB readers share the same ordered game event path.
    with _dispatch_lock:
        event = _consume_line(line, port, generation)
        if event is not None:
            if _mode == "direct":
                c = _connections.get(port)
                if c is not None and not c.get("ready"):
                    c["ready"] = True
                    _write(c, "C0")
            try:
                on_message(event)
            except Exception as exc:
                with _lock:
                    _record_raw(f"게임 입력 처리 실패: {exc}", "error")


def _serial_loop(c: dict, mode: str, generation: int) -> None:
    stop = c["stop"]
    if serial is None:
        c["error"] = "pyserial이 설치되지 않음"
        return
    while not stop.is_set():
        device = None
        try:
            device = serial.Serial(c["port"], c["baud"], timeout=0.2, write_timeout=0.5)
            with c["write_lock"]:
                c["device"] = device
            with _lock:
                c.update(open=True, error="", started_ms=now_ms(), last_heartbeat_ms=0.0,
                         ready=False, slave_id=None, reported_slave_id=None, crc_bad=None, crc_delta=0,
                         diagnostics={})
            if mode == "master":
                on_ready()
            next_lease = 0.0
            pending = bytearray()
            while not stop.is_set():
                if mode == "direct" and time.monotonic() >= next_lease:
                    if not _write(c, "U1"):
                        raise serial.SerialException("USB 직접 모드 요청 실패")
                    next_lease = time.monotonic() + 1
                chunk = device.read_until(b"\n", 4096)
                pending.extend(chunk)
                if len(pending) > 8192:
                    pending.clear()
                elif pending.endswith(b"\n"):
                    line = pending.decode("utf-8", errors="replace").strip()
                    pending.clear()
                    if line:
                        _handle_line(line, c["port"], generation)
        except (serial.SerialException, OSError) as exc:
            with _lock:
                c["error"] = str(exc)
        finally:
            if mode == "direct":
                _write(c, "U0")
            with c["write_lock"]:
                c["device"] = None
                if device is not None:
                    try:
                        device.close()
                    except (serial.SerialException, OSError):
                        pass
            with _lock:
                c["open"] = False
        if stop.wait(3):
            break


def stop_serial() -> None:
    with _connection_lock:
        sessions = list(_connections.values())
        for c in sessions:
            c["stop"].set()
        for c in sessions:
            c["thread"].join()
        with _lock:
            _connections.clear()
            _master.update(open=False)


def validate_connection(mode: str, ports: list[str], baud: int = 115200) -> list[str]:
    if mode not in ("master", "direct"):
        raise ValueError("mode는 master 또는 direct여야 합니다")
    if not isinstance(ports, list) or any(not isinstance(p, str) or not p.strip() for p in ports):
        raise ValueError("ports는 COM 포트 목록이어야 합니다")
    ports = list(dict.fromkeys(p.strip() for p in ports))
    if len(ports) > (1 if mode == "master" else 7):
        raise ValueError("master는 1개, slave 직접 연결은 최대 7개 포트를 선택하세요")
    if type(baud) is not int or not 1 <= baud <= 2000000:
        raise ValueError("유효하지 않은 보드레이트")
    return ports


def configure_connection(mode: str, ports: list[str], baud: int = 115200, *, before_start=None) -> None:
    global _mode, _generation
    ports = validate_connection(mode, ports, baud)
    with _connection_lock:
        stop_serial()
        if before_start is not None:
            before_start()
        with _lock:
            _mode = mode
            _generation += 1
            _slaves.clear()
            _cells.clear()
            _raw_log.clear()
            for port in ports:
                c = {"port": port, "baud": baud, "open": False, "error": "연결 중…",
                     "last_rx_ms": 0.0, "last_heartbeat_ms": 0.0,
                     "rx_lines": 0, "json_lines": 0, "noise_lines": 0,
                     "started_ms": now_ms(), "slave_id": None,
                     "stop": threading.Event(), "write_lock": threading.Lock(), "device": None}
                if mode == "master":
                    _master.clear()
                    _master.update(c)
                    c = _master
                c["thread"] = threading.Thread(target=_serial_loop,
                                               args=(c, mode, _generation), daemon=True)
                _connections[port] = c
        for c in _connections.values():
            c["thread"].start()


def start_serial(port: str, baud: int) -> None:
    configure_connection("master", [port], baud)


