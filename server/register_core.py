"""DS2431 serial detect + orb_mapping.json registration logic."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable

import serial

from mapping_store import (
    MAPPING_PATH,
    list_groups,
    load_mapping,
    normalize_id,
    save_mapping,
    suggest_voice,
    validate_voice,
)

DETECT_TYPES = {"ds2431", "ds2431_detected", "detect", "orb_detect", "orb_id"}
LOG_LIMIT = 80


def friendly_serial_error(error: str) -> str:
    """Turn raw serial exceptions into short Korean messages."""
    if not error:
        return ""
    e = error.strip()
    low = e.lower()

    if e == "포트를 선택하세요":
        return e
    if e == "disconnected" or "연결이 끊" in e:
        return "시리얼 연결이 끊어졌습니다"

    if "could not open port" in low or "cannot open" in low:
        if "access is denied" in low or "permission" in low:
            return "포트에 접근할 수 없습니다. 다른 프로그램이 COM 포트를 사용 중인지 확인하세요."
        if "filenotfound" in low or "no such file" in low:
            return "포트를 찾을 수 없습니다. USB 연결과 포트 선택을 확인하세요."
        return "포트를 열 수 없습니다. 케이블·드라이버·포트 번호를 확인하세요."

    if "access is denied" in low or "permissionerror" in low:
        return "포트 접근이 거부되었습니다."

    if "device reports readiness" in low:
        return "장치가 준비되지 않았습니다. 잠시 후 다시 시도하세요."

    if "invalid id" in low or e.startswith("voice must"):
        return e

    # Windows pyserial often: "COM13 PermissionError(13, '액세스가 거부되었습니다.', ...)"
    if "permissionerror" in low or "액세스가 거부" in e:
        return "포트 접근이 거부되었습니다. 다른 프로그램을 종료한 뒤 다시 시도하세요."

    # Trim long Python repr
    if len(e) > 120:
        return e[:117] + "…"
    return e


class RegisterState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.serial_ok = False
        self.serial_port = ""
        self.serial_error = ""
        self.mapping_path = str(MAPPING_PATH)
        self.pending: dict | None = None
        self.history: list[dict] = []
        self.logs: list[dict] = []
        self._listeners: list[Callable[[dict], None]] = []
        self._log_listeners: list[Callable[[dict], None]] = []

    def add_listener(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    def add_log_listener(self, cb: Callable[[dict], None]) -> None:
        self._log_listeners.append(cb)

    def push_log(self, level: str, message: str) -> None:
        entry = {
            "level": level,
            "message": message,
            "at": time.time(),
        }
        with self.lock:
            self.logs = ([entry] + self.logs)[:LOG_LIMIT]
        for cb in self._log_listeners:
            try:
                cb(entry)
            except Exception:
                pass

    def _notify(self, entry: dict) -> None:
        for cb in self._listeners:
            try:
                cb(entry)
            except Exception as e:
                self.push_log("error", f"UI 갱신 오류: {friendly_serial_error(str(e))}")

    def set_serial(self, ok: bool, port: str = "", error: str = "") -> None:
        friendly = friendly_serial_error(error) if error else ""
        with self.lock:
            prev_ok = self.serial_ok
            prev_err = self.serial_error
            self.serial_ok = ok
            self.serial_port = port
            self.serial_error = friendly

        if ok and not prev_ok:
            self.push_log("ok", f"{port} 연결됨")
        elif not ok and friendly and friendly != prev_err:
            if friendly == "포트를 선택하세요":
                self.push_log("info", friendly)
            else:
                self.push_log("warn" if "끊" in friendly else "error", friendly)

    def push_detection(self, raw_id: str, source: str = "serial") -> dict:
        normalized = normalize_id(raw_id)
        mapping = load_mapping()
        known = normalized in mapping
        entry = {
            "id": normalized,
            "raw": raw_id,
            "known": known,
            "voice": mapping.get(normalized, {}).get("voice"),
            "source": source,
            "at": time.time(),
        }
        with self.lock:
            self.pending = entry
            self.history = ([entry] + self.history)[:30]
        print(
            f"[register] {'KNOWN' if known else 'NEW'} {normalized}"
            + (f" voice={entry['voice']}" if known else "")
        )
        self.push_log("info", f"{'등록됨' if known else '신규'} · {normalized}")
        self._notify(entry)
        return entry

    def clear_pending(self) -> None:
        with self.lock:
            self.pending = None

    def snapshot(self) -> dict:
        mapping = load_mapping()
        with self.lock:
            return {
                "serial_ok": self.serial_ok,
                "serial_port": self.serial_port,
                "serial_error": self.serial_error,
                "mapping_path": self.mapping_path,
                "mapping_count": len(mapping),
                "groups": list_groups(mapping),
                "pending": self.pending,
                "history": list(self.history),
                "mapping": [
                    {"id": oid, **info} for oid, info in sorted(mapping.items())
                ],
                "logs": list(self.logs),
            }


def parse_serial_line(line: str) -> str | None:
    line = line.strip()
    if not line:
        return None

    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        if _looks_like_id(line):
            return line
        return None

    if not isinstance(msg, dict):
        return None

    t = str(msg.get("type", "")).lower()
    if t in DETECT_TYPES or msg.get("id"):
        raw = msg.get("id") or msg.get("orb_id") or msg.get("uid")
        return str(raw) if raw else None
    return None


def _looks_like_id(s: str) -> bool:
    cleaned = s.replace(":", "")
    return 6 <= len(cleaned) <= 32 and all(c in "0123456789abcdefABCDEF:" for c in s)


def list_com_ports() -> list[dict]:
    """Return available serial ports with optional description."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []

    out = []
    for p in list_ports.comports():
        label = p.device
        if p.description and p.description != p.device:
            label = f"{p.device} — {p.description}"
        out.append({"device": p.device, "label": label})
    return out


class SerialManager:
    """Background serial reader with hot-swappable COM port."""

    def __init__(self, state: RegisterState, baudrate: int, port: str = "") -> None:
        self.state = state
        self.baudrate = baudrate
        self._port = port
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def get_port(self) -> str:
        with self._lock:
            return self._port

    def set_port(self, port: str) -> None:
        with self._lock:
            self._port = port or ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        active_port = ""
        ser = None

        while not self._stop.is_set():
            target = self.get_port()

            if not target:
                if ser:
                    ser.close()
                    ser = None
                active_port = ""
                self.state.set_serial(False, "", "포트를 선택하세요")
                time.sleep(0.4)
                continue

            if target != active_port:
                if ser:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                active_port = target
                try:
                    ser = serial.Serial(active_port, self.baudrate, timeout=1)
                    self.state.set_serial(True, active_port)
                except serial.SerialException as e:
                    ser = None
                    active_port = ""
                    self.state.set_serial(False, target, str(e))
                    time.sleep(2)
                    continue

            try:
                line = ser.readline().decode("utf-8", errors="ignore")
                raw_id = parse_serial_line(line)
                if raw_id:
                    try:
                        self.state.push_detection(raw_id, "serial")
                    except ValueError as e:
                        self.state.push_log("warn", friendly_serial_error(str(e)))
            except serial.SerialException as e:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                active_port = ""
                self.state.set_serial(False, target, "disconnected")
                time.sleep(1.5)


def serial_loop(state: RegisterState, port: str, baudrate: int) -> None:
    """Legacy helper — prefer SerialManager."""
    mgr = SerialManager(state, baudrate, port)
    mgr.start()
    while True:
        time.sleep(3600)


def add_orb(orb_id: str, voice: str, state: RegisterState | None = None) -> str:
    orb_id = normalize_id(orb_id)
    voice = validate_voice(voice)
    mapping = load_mapping()
    mapping[orb_id] = {"voice": voice}
    save_mapping(mapping)
    if state:
        state.clear_pending()
        state.push_log("ok", f"등록 · {orb_id} → {voice}")
    print(f"[register] ADD {orb_id} -> {voice}")
    return orb_id


def update_orb(orb_id: str, voice: str, state: RegisterState | None = None) -> str:
    orb_id = normalize_id(orb_id)
    voice = validate_voice(voice)
    mapping = load_mapping()
    if orb_id not in mapping:
        raise KeyError("not found")
    mapping[orb_id] = {"voice": voice}
    save_mapping(mapping)
    if state:
        state.push_log("ok", f"수정 · {orb_id} → {voice}")
    print(f"[register] UPDATE {orb_id} -> {voice}")
    return orb_id


def remove_orb(orb_id: str) -> None:
    orb_id = normalize_id(orb_id)
    mapping = load_mapping()
    if orb_id not in mapping:
        raise KeyError("not found")
    del mapping[orb_id]
    save_mapping(mapping)
    print(f"[register] REMOVE {orb_id}")
