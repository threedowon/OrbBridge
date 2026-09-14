"""
슬레이브 단독 점검 (firmware/slave_test/slave_test.ino 전용)

master 없이 slave 보드 한 장을 USB 로 직접 열어 회로·DS2431 접점·LED 를
확인한다. 보드가 1초마다 보내는 status JSON 과 added/removed/bounce/memtest
이벤트를 누적해 대시보드(/slave)에 보여주고, 텍스트 명령을 그대로 전달한다.

tools.debug 의 master 진단과 독립된 시리얼 연결·상태를 가진다. 한 프로세스가
master 와 slave_test 보드를 각각 다른 COM 포트로 동시에 열 수 있다.
"""

import json
import threading
import time

import serial

CELL_COUNT = 16
RAW_LOG_LIMIT = 300
EVENT_LIMIT = 200
# 이 시간 안에 status 가 없으면 보드가 멈췄거나 다른 펌웨어다.
STATUS_TIMEOUT_S = 3.0
# 접촉 튐(bounce)이 이 값 이상이면 셀 접점을 의심한다.
BOUNCE_WARN = 3
CRC_WARN = 1

# 보드가 받는 명령의 첫 글자. 그 외는 보내지 않는다.
ALLOWED_CMD_OPS = set("?SCLWABMRZ")


def now_ms() -> float:
    return time.time() * 1000.0


_lock = threading.Lock()

_board = {
    "port": "",
    "baud": 0,
    "open": False,
    "error": "",
    "last_rx_ms": 0.0,
    "last_status_ms": 0.0,
    "rx_lines": 0,
    "json_lines": 0,
    "noise_lines": 0,
    "hello": None,        # 마지막 hello JSON (핀 배치 등)
    "status": None,       # 마지막 status JSON (원본)
}

# 셀 번호(1~16) → 이번 세션 누적 정보. status 의 카운터는 보드 쪽 값이고,
# 여기서는 이벤트에서 얻는 마지막 UID·memtest 결과를 덧붙인다.
_cells: dict[int, dict] = {}
_events: list[dict] = []
_raw_log: list[dict] = []
_walk_slot: int | None = None

_ser: "serial.Serial | None" = None
_serial_stop = threading.Event()
_serial_thread: threading.Thread | None = None


def _cell(n: int) -> dict:
    c = _cells.get(n)
    if c is None:
        c = {
            "cell": n,
            "last_id": "",
            "last_added_ms": 0.0,
            "last_removed_ms": 0.0,
            "last_bounce_ms": 0.0,
            "memtest": None,
        }
        _cells[n] = c
    return c


def _record_raw(line: str, kind: str) -> None:
    _raw_log.append({"ts": now_ms(), "kind": kind, "line": line[:600]})
    if len(_raw_log) > RAW_LOG_LIMIT:
        del _raw_log[: len(_raw_log) - RAW_LOG_LIMIT]


def _record_event(evt: dict) -> None:
    item = dict(evt)
    item["ts"] = now_ms()
    _events.append(item)
    if len(_events) > EVENT_LIMIT:
        del _events[: len(_events) - EVENT_LIMIT]


def reset_session() -> None:
    """이벤트·로그·셀 메모를 비운다. 보드 쪽 카운터는 Z 명령으로 따로 지운다."""
    with _lock:
        _cells.clear()
        _events.clear()
        _raw_log.clear()
        _board.update({
            "rx_lines": 0, "json_lines": 0, "noise_lines": 0,
        })


def handle_line(line: str) -> None:
    """보드에서 온 한 줄을 반영한다. 테스트에서 직접 호출할 수 있게 공개한다."""
    global _walk_slot
    with _lock:
        _board["last_rx_ms"] = now_ms()
        _board["rx_lines"] += 1

        if not line.startswith("{"):
            _board["noise_lines"] += 1
            _record_raw(line, "noise")
            return
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            _board["noise_lines"] += 1
            _record_raw(line, "badjson")
            return

        _board["json_lines"] += 1
        t = evt.get("type")

        if t == "status":
            _board["status"] = evt
            _board["last_status_ms"] = now_ms()
            # status 는 1초마다 오므로 원시 로그에는 남기지 않는다(다른 줄이 묻힌다).
            return

        _record_raw(line, "json")

        if t == "hello":
            _board["hello"] = evt
            _record_event(evt)
            return

        cell = evt.get("cell")
        if t == "added" and cell is not None:
            c = _cell(int(cell))
            c["last_id"] = evt.get("id", "")
            c["last_added_ms"] = now_ms()
            _record_event(evt)
        elif t == "removed" and cell is not None:
            c = _cell(int(cell))
            c["last_removed_ms"] = now_ms()
            _record_event(evt)
        elif t == "bounce" and cell is not None:
            c = _cell(int(cell))
            c["last_bounce_ms"] = now_ms()
            _record_event(evt)
        elif t == "memtest" and cell is not None:
            c = _cell(int(cell))
            c["memtest"] = {
                "ok": int(evt.get("ok", 0)),
                "tries": int(evt.get("tries", 0)),
                "present": int(evt.get("present", 0)),
                "data": evt.get("data", ""),
                "ms": evt.get("ms", 0),
                "ts": now_ms(),
            }
            _record_event(evt)
        elif t == "walk":
            _walk_slot = evt.get("slot")
        elif t in ("ack", "rs485"):
            _record_event(evt)


def _cell_rows(status: dict | None) -> list[dict]:
    rows = []
    fw_cells = {}
    if status:
        for c in status.get("cells", []):
            try:
                fw_cells[int(c.get("c"))] = c
            except (TypeError, ValueError):
                continue
    for n in range(1, CELL_COUNT + 1):
        fw = fw_cells.get(n, {})
        memo = _cells.get(n, {})
        bounces = int(fw.get("bnc", 0))
        crc = int(fw.get("crc", 0))
        stuck = bool(fw.get("low", 0))
        present = bool(fw.get("p", 0))
        seen = bool(fw.get("seen", 0))
        if stuck:
            verdict, msg = "bad", "DQ 가 LOW 로 고정 — 풀업 없음 또는 단락"
        elif crc >= CRC_WARN or bounces >= BOUNCE_WARN:
            verdict, msg = "warn", "접촉 불안정 — 접점·납땜 확인"
        elif present:
            verdict, msg = "ok", "오브 인식 중"
        elif seen:
            verdict, msg = "ok", "확인됨 (현재 비어 있음)"
        else:
            verdict, msg = "idle", "아직 오브를 올리지 않음"
        rows.append({
            "cell": n,
            "gpio": fw.get("g"),
            "present": present,
            "raw": bool(fw.get("r", 0)),
            "id": fw.get("id") or memo.get("last_id", ""),
            "held_ms": fw.get("held", 0),
            "crc": crc,
            "bad_family": int(fw.get("fam", 0)),
            "flips": int(fw.get("flip", 0)),
            "bounces": bounces,
            "adds": int(fw.get("add", 0)),
            "removes": int(fw.get("rm", 0)),
            "stuck_low": stuck,
            "seen": seen,
            "memtest": memo.get("memtest"),
            "verdict": verdict,
            "message": msg,
        })
    return rows


def _checklist(board: dict, status: dict | None, rows: list[dict]) -> list[dict]:
    """자동으로 판정할 수 있는 항목만 모은다. LED 점등은 눈으로 봐야 하므로
    웹 페이지가 수동 체크박스로 다룬다."""
    items = []
    fw_ok = bool(status) and status.get("fw") == "slave_test"
    items.append({
        "key": "firmware",
        "label": "slave_test 펌웨어 응답",
        "state": "ok" if fw_ok else ("bad" if board["open"] else "idle"),
        "detail": "status 수신 중" if fw_ok else "보드 연결 후 status 를 기다리는 중",
    })
    sid = int(status.get("id", 0)) if status else 0
    items.append({
        "key": "dip",
        "label": "DIP 스위치로 보드 ID 설정",
        "state": "ok" if sid else ("bad" if status else "idle"),
        "detail": f"ID = {sid}" if sid else "ID 0 — DIP 를 1~7 로 맞춘다",
    })
    stuck = [r["cell"] for r in rows if r["stuck_low"]]
    items.append({
        "key": "pullup",
        "label": "16개 DQ 선 유휴 HIGH (풀업·단락)",
        "state": "bad" if stuck else ("ok" if status else "idle"),
        "detail": ("LOW 고정: cell " + ", ".join(map(str, stuck))) if stuck else "모든 버스 정상",
    })
    seen = [r["cell"] for r in rows if r["seen"]]
    unseen = [r["cell"] for r in rows if not r["seen"]]
    items.append({
        "key": "contact",
        "label": "16셀 모두 DS2431 인식",
        "state": "ok" if len(seen) == CELL_COUNT else ("warn" if seen else "idle"),
        "detail": f"{len(seen)}/{CELL_COUNT} 확인" + (f" — 남은 셀: {', '.join(map(str, unseen))}" if unseen else ""),
    })
    flaky = [r["cell"] for r in rows if r["crc"] >= CRC_WARN or r["bounces"] >= BOUNCE_WARN]
    items.append({
        "key": "quality",
        "label": "접촉 안정 (CRC 오류·튐 없음)",
        "state": "warn" if flaky else ("ok" if seen else "idle"),
        "detail": ("의심 셀: " + ", ".join(map(str, flaky))) if flaky else "이상 없음",
    })
    mt_fail = [r["cell"] for r in rows if r["memtest"] and r["memtest"]["ok"] < r["memtest"]["tries"]]
    mt_done = [r["cell"] for r in rows if r["memtest"] and r["memtest"]["present"]]
    items.append({
        "key": "memtest",
        "label": "DS2431 통신 테스트 (Read ROM·Memory)",
        "state": "bad" if mt_fail else ("ok" if mt_done else "idle"),
        "detail": ("실패 셀: " + ", ".join(map(str, mt_fail))) if mt_fail
                  else (f"{len(mt_done)}셀 통과" if mt_done else "오브를 올리고 M 테스트 실행"),
    })
    return items


def snapshot() -> dict:
    with _lock:
        t = now_ms()
        board = dict(_board)
        status = board.get("status")
        board["last_rx_age_ms"] = (t - board["last_rx_ms"]) if board["last_rx_ms"] else None
        st_age = (t - board["last_status_ms"]) if board["last_status_ms"] else None
        board["status_age_ms"] = st_age
        board["alive"] = bool(board["open"] and st_age is not None and st_age <= STATUS_TIMEOUT_S * 1000)
        rows = _cell_rows(status)
        return {
            "now_ms": t,
            "board": board,
            "cells": rows,
            "checklist": _checklist(board, status, rows),
            "events": list(_events[-80:]),
            "raw": list(_raw_log[-120:]),
            "walk_slot": _walk_slot,
        }


def current_port() -> str:
    with _lock:
        return _board["port"] if _board["open"] else ""


def send_command(cmd: str) -> tuple[bool, str]:
    cmd = (cmd or "").strip()
    if not cmd or cmd[0] not in ALLOWED_CMD_OPS or len(cmd) > 8:
        return False, "알 수 없는 명령"
    with _lock:
        s = _ser
    if s is None:
        return False, "slave 보드 미연결(포트 열림 확인)"
    try:
        s.write(f"{cmd}\n".encode())
        s.flush()
        return True, ""
    except Exception as e:  # noqa: BLE001 — 시리얼 예외를 그대로 보여준다
        return False, str(e)


def _serial_loop(port: str, baud: int) -> None:
    global _ser
    with _lock:
        _board.update({"port": port, "baud": baud, "open": False, "error": "연결 중…"})

    while not _serial_stop.is_set():
        try:
            ser = serial.Serial(port, baud, timeout=1)
        except serial.SerialException as e:
            with _lock:
                _board.update({"open": False, "error": str(e)})
            print(f"[slave] 열기 실패 ({port}): {e}")
            if _serial_stop.wait(3):
                return
            continue

        print(f"[slave] 연결됨: {port} @ {baud}")
        with _lock:
            _board.update({"open": True, "error": ""})
            _ser = ser
        try:
            # 부팅 hello 를 놓쳤을 수 있으므로 연결 직후 한 번 요청한다.
            ser.write(b"?\n")
            ser.flush()
            while not _serial_stop.is_set():
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if line:
                    handle_line(line)
        except serial.SerialException as e:
            with _lock:
                _board.update({"open": False, "error": str(e)})
            print(f"[slave] 끊김 ({port}): {e}")
        finally:
            with _lock:
                _ser = None
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass
        if _serial_stop.wait(3):
            return


def start_serial(port: str, baud: int) -> None:
    global _serial_thread
    stop_serial()
    _serial_stop.clear()
    with _lock:
        _board.update({
            "port": port, "baud": baud, "open": False, "error": "연결 중…",
            "last_rx_ms": 0.0, "last_status_ms": 0.0,
            "rx_lines": 0, "json_lines": 0, "noise_lines": 0,
            "hello": None, "status": None,
        })
    _serial_thread = threading.Thread(target=_serial_loop, args=(port, baud), daemon=True)
    _serial_thread.start()


def stop_serial() -> None:
    global _serial_thread
    _serial_stop.set()
    if _serial_thread and _serial_thread.is_alive():
        _serial_thread.join(timeout=2)
    _serial_thread = None
    with _lock:
        _board.update({"open": False, "error": "연결 해제됨"})
