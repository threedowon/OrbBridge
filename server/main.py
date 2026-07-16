import json
import os
import serial
import threading
import time
from pathlib import Path
from pythonosc import udp_client

from mapping_store import find_orb, load_mapping

from app_paths import data_dir, ensure_data_files

ensure_data_files()
SERVER_DIR = data_dir()
os.chdir(SERVER_DIR)

# ── 설정 로드 ──────────────────────────────────────────
with open(SERVER_DIR / "config.json")      as f: config      = json.load(f)
with open(SERVER_DIR / "orb_mapping.json") as f: orb_mapping = json.load(f)
with open(SERVER_DIR / "stage_data.json")  as f: stage_data  = json.load(f)

current_stage   = config["current_stage"]
stage           = next(s for s in stage_data["stages"] if s["stage"] == current_stage)
answer_sequence = stage["sequence"]
time_limit      = stage["time_limit"]
melody_id       = config.get("melody", "biplane")
_raw_input_mode = config.get("input_mode", "table")
# legacy: "mock" → "table" (웹 /table UI 입력)
input_mode      = "table" if _raw_input_mode == "mock" else _raw_input_mode

osc = udp_client.SimpleUDPClient(config["osc"]["host"], config["osc"]["port"])

game_active  = False
timer_thread = None

NOTE_MAP = {1: "C", 2: "D", 3: "E", 4: "F", 5: "G", 6: "A", 7: "B", 8: "C"}

# ── Admin 상태 (셀 점유, 이벤트 로그, 시리얼) ────────
_state_lock = threading.Lock()
_cells: dict[str, dict] = {}
_event_log: list[dict] = []
EVENT_LOG_LIMIT = 100
_serial_status = {"connected": False, "port": "", "error": ""}

# ── 슬레이브 생존 추적 (heartbeat 기반 online/offline) ──
# slave.ino 가 3초(HEARTBEAT_MS)마다 heartbeat 를 보낸다. 이 시간(주기의 ~2.5배)
# 안에 heartbeat 가 없으면 offline 으로 본다. 몇 개 놓쳐도 깜빡이지 않도록 여유.
SLAVE_ONLINE_TIMEOUT_S = 8.0
_slaves: dict[int, dict] = {}


def _cell_key(slave: int, cell: int) -> str:
    return f"{slave}:{cell}"


def _record_event(entry: dict) -> None:
    entry["ts"] = time.time() * 1000
    with _state_lock:
        _event_log.append(entry)
        if len(_event_log) > EVENT_LOG_LIMIT:
            del _event_log[: len(_event_log) - EVENT_LOG_LIMIT]


def _touch_slave(slave_id: int, *, heartbeat: bool) -> None:
    """슬레이브에서 온 메시지로 생존 상태 갱신. heartbeat 는 online 판정 기준."""
    now = time.time() * 1000
    with _state_lock:
        s = _slaves.get(slave_id)
        if s is None:
            s = {
                "slave": slave_id,
                "first_seen": now,
                "last_seen": now,
                "last_heartbeat": 0.0,
                "heartbeat_count": 0,
                "msg_count": 0,
            }
            _slaves[slave_id] = s
        s["last_seen"] = now
        if heartbeat:
            s["last_heartbeat"] = now
            s["heartbeat_count"] += 1
        else:
            s["msg_count"] += 1


def _slave_list_locked(now: float) -> list:
    """_state_lock 을 이미 잡은 상태에서 슬레이브 스냅샷을 만든다."""
    out = []
    for sid in sorted(_slaves):
        s = _slaves[sid]
        has_hb = s["last_heartbeat"] > 0
        hb_age = (now - s["last_heartbeat"]) if has_hb else None
        online = has_hb and hb_age <= SLAVE_ONLINE_TIMEOUT_S * 1000
        out.append({
            "slave": sid,
            "online": online,
            "has_heartbeat": has_hb,
            "hb_age_ms": hb_age,
            "last_seen_age_ms": now - s["last_seen"],
            "heartbeat_count": s["heartbeat_count"],
            "msg_count": s["msg_count"],
        })
    return out


def get_status() -> dict:
    now = time.time() * 1000
    with _state_lock:
        serial = dict(_serial_status)
        slaves = _slave_list_locked(now)
        return {
            "input_mode": input_mode,
            "game_active": game_active,
            "current_stage": current_stage,
            "melody": melody_id,
            "orb_count": len(orb_mapping),
            "serial": serial,
            "cell_count": len(_cells),
            "slaves": slaves,
            "slave_online_timeout_s": SLAVE_ONLINE_TIMEOUT_S,
        }


def clear_cells() -> None:
    with _state_lock:
        _cells.clear()


def get_cells() -> dict:
    with _state_lock:
        return dict(_cells)


def get_events() -> list:
    with _state_lock:
        return list(_event_log)


# ── 셀 파싱 ────────────────────────────────────────────
def parse_cell(cell: int):
    """cell 1–8 → Q1 도~도, cell 9–16 → Q2 도~도."""
    if cell <= 8:
        q, row = 1, cell
    else:
        q, row = 2, cell - 8
    note = NOTE_MAP.get(row, "invalid")
    return q, note

# ── 정답 판별 ──────────────────────────────────────────
def judge(slave: int, cell: int) -> str:
    q, note   = parse_cell(cell)
    q_actual  = q + (slave - 1) * 2
    answer    = answer_sequence.get(str(q_actual))
    if answer is None:
        return "invalid"
    return "correct" if note == answer else "wrong"

# ── ready / game lifecycle ─────────────────────────────
def set_ready(ready: bool) -> None:
    osc.send_message("/game/ready", [1 if ready else 0])
    print(f"[ready] {ready}")

def reload_stage_data() -> None:
    """Pick up Melody Editor saves (stage_data.json, config melody) without restarting."""
    global stage_data, stage, answer_sequence, time_limit, melody_id, config
    with open(SERVER_DIR / "config.json", encoding="utf-8") as f:
        config = json.load(f)
    melody_id = config.get("melody", "biplane")
    with open(SERVER_DIR / "stage_data.json", encoding="utf-8") as f:
        stage_data = json.load(f)
    stage = next(s for s in stage_data["stages"] if s["stage"] == current_stage)
    answer_sequence = stage["sequence"]
    time_limit = stage["time_limit"]
    print(f"[stage] 재로드 — melody={melody_id}, 정답 시퀀스: {answer_sequence}")


def start_game() -> bool:
    global game_active, timer_thread
    if game_active:
        print("[game] 이전 게임 정리 후 재시작")
        end_game("restart")
    reload_stage_data()
    clear_cells()
    game_active = True
    print(f"[game] 시작 — stage {current_stage}, melody={melody_id}")
    set_ready(True)
    osc.send_message("/game/start", [current_stage, 0])
    osc.send_message("/game/listen", [])
    return True

def end_game(reason: str = "complete") -> None:
    global game_active
    game_active = False
    clear_cells()
    set_ready(False)
    print(f"[game] 종료 — {reason}")
    osc.send_message("/game/end", [reason])

def request_listen() -> None:
    print("[game] 다시 듣기")
    osc.send_message("/game/listen", [])

def request_play() -> None:
    if not game_active:
        print("[game] 재생 무시 — 게임 비활성")
        return
    print("[game] 재생")
    osc.send_message("/game/play", [])


def request_dispensa() -> None:
    if not game_active:
        print("[game] 디페스타 무시 — 게임 비활성")
        return
    print("[game] 디페스타")
    osc.send_message("/game/dispensa", [])

def handle_serial_message(event: dict) -> None:
    """USB/serial 한 줄 JSON — orb 이벤트 또는 game 버튼."""
    t = event.get("type")

    # 슬레이브 생존 추적: slave 필드가 있는 모든 메시지로 last_seen 을 갱신하고,
    # heartbeat 는 online/offline 판정 기준으로 별도 기록. (admin /api/status 로 노출)
    sid = event.get("slave")
    if sid is not None:
        try:
            _touch_slave(int(sid), heartbeat=(t == "heartbeat"))
        except (TypeError, ValueError):
            pass  # 이상한 slave 값은 생존 추적만 건너뜀

    if t == "game":
        action = str(event.get("action", "")).lower()
        if action == "listen":
            request_listen()
        elif action == "play":
            request_play()
        else:
            print(f"[WARN] unknown game action: {action}")
        return
    if t == "heartbeat":
        return  # 생존 신호는 위에서 기록 완료 — 게임 로직과 무관하므로 여기서 종료
    handle_event(event)

# ── 이벤트 처리 ────────────────────────────────────────
def handle_event(event: dict):
    global orb_mapping
    orb_mapping = load_mapping()

    t      = event.get("type")
    slave  = event.get("slave")
    cell   = event.get("cell")
    orb_id = event.get("id")

    if t == "added":
        orb_key, orb = find_orb(orb_id, orb_mapping)
        if orb is None:
            print(f"[WARN] 알 수 없는 ID: {orb_id}")
            q, note = parse_cell(cell)
            with _state_lock:
                _cells[_cell_key(slave, cell)] = {
                    "id": orb_id, "voice": "unknown", "note": note, "result": "unknown",
                }
            _record_event({"type": "added", "slave": slave, "cell": cell,
                           "id": orb_id, "voice": "unknown", "note": note, "result": "unknown"})
            osc.send_message("/orb/added", [slave, cell, "unknown", "invalid", "wrong", orb_id or ""])
            return

        voice      = orb["voice"]
        q, note    = parse_cell(cell)
        q_actual   = q + (slave - 1) * 2
        result     = judge(slave, cell) if game_active else "inactive"

        with _state_lock:
            _cells[_cell_key(slave, cell)] = {
                "id": orb_key, "voice": voice, "note": note, "result": result,
            }
        _record_event({"type": "added", "slave": slave, "cell": cell,
                       "id": orb_key, "voice": voice, "note": note, "result": result})

        print(f"[added]   slave={slave} cell={cell} Q={q_actual} note={note} voice={voice} → {result}")
        osc.send_message("/orb/added", [slave, cell, voice, note, result, orb_key])

    elif t == "removed":
        orb_key, orb = find_orb(orb_id, orb_mapping)
        voice    = orb.get("voice", "unknown") if orb else "unknown"
        q, note  = parse_cell(cell)
        q_actual = q + (slave - 1) * 2

        with _state_lock:
            _cells.pop(_cell_key(slave, cell), None)
        _record_event({"type": "removed", "slave": slave, "cell": cell,
                       "id": orb_key or orb_id, "voice": voice, "note": note})

        print(f"[removed] slave={slave} cell={cell} Q={q_actual} note={note} voice={voice}")
        osc.send_message("/orb/removed", [slave, cell, voice, note, orb_key or orb_id or ""])

# ── 시리얼 수신 루프 ───────────────────────────────────
def serial_loop():
    port     = config["serial"]["port"]
    baudrate = config["serial"]["baudrate"]

    while True:
        with _state_lock:
            _serial_status.update({"connected": False, "port": port, "error": "연결 중…"})
        try:
            ser = serial.Serial(port, baudrate, timeout=1)
        except serial.SerialException as e:
            err = str(e)
            print(f"[serial] 포트 연결 실패 ({port}): {e}")
            with _state_lock:
                _serial_status.update({"connected": False, "port": port, "error": err})
            time.sleep(5)
            continue

        print(f"[serial] 연결됨: {port}")
        with _state_lock:
            _serial_status.update({"connected": True, "port": port, "error": ""})
        try:
            while True:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if not line.startswith("{"):
                    continue  # ESP 부트 로그, 디버그 println 등 — 무시
                try:
                    event = json.loads(line)
                    handle_serial_message(event)
                except json.JSONDecodeError:
                    print(f"[WARN] JSON 파싱 실패: {line[:120]}")
        except serial.SerialException as e:
            err = str(e)
            print(f"[serial] 연결 끊김 ({port}): {e}")
            with _state_lock:
                _serial_status.update({"connected": False, "port": port, "error": err})
        finally:
            ser.close()
        time.sleep(5)

# ── 진입점 ─────────────────────────────────────────────
def run_main() -> None:
    print(f"[stage {current_stage}] 정답 시퀀스: {answer_sequence}")
    print(f"[melody] {melody_id}")
    print(f"[input] {input_mode} mode")
    print("screen에서 시작을 누르면 게임이 시작됩니다.")
    print(f"[http] http://127.0.0.1:{config.get('mock', {}).get('port', 8080)}/screen")
    print(f"[http] http://127.0.0.1:{config.get('mock', {}).get('port', 8080)}/table")

    from mock_server import start_http_server

    http_cfg = config.get("mock", {"host": "127.0.0.1", "port": 8080})
    start_http_server(
        http_cfg,
        handle_event,
        orb_mapping,
        get_status=get_status,
        get_cells=get_cells,
        get_events=get_events,
        on_game_start=start_game,
        on_game_end=end_game,
        on_game_listen=request_listen,
        on_game_play=request_play,
        on_game_dispensa=request_dispensa,
    )

    if input_mode == "serial":
        threading.Thread(target=serial_loop, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("종료")


if __name__ == "__main__":
    from bridge_service import start_bridge_thread

    start_bridge_thread()
    time.sleep(0.4)
    run_main()
