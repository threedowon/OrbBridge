import json
import os
import re
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

# ── master 로 명령 전송 (디페스타 LED 등) ──────────────
# serial_loop 가 연 시리얼 핸들을 공유해 PC→master 로 짧은 명령("D1"/"D0")을 쓴다.
_master_ser = None
_master_ser_lock = threading.Lock()
_dispensa_out = False   # 현재 디페스타 LED(GPIO39) 출력 상태 캐시 (변할 때만 전송)

# ── 슬레이브 생존 추적 (heartbeat 기반 online/offline) ──
# slave.ino 가 3초(HEARTBEAT_MS)마다 heartbeat 를 보낸다. 이 시간(주기의 ~2.5배)
# 안에 heartbeat 가 없으면 offline 으로 본다. 몇 개 놓쳐도 깜빡이지 않도록 여유.
SLAVE_ONLINE_TIMEOUT_S = 8.0
_slaves: dict[int, dict] = {}

# 유효 slave ID 범위. 이 밖(예: 충돌로 깨진 slave:0)의 메시지는 폐기해
# admin 에 '유령 슬레이브'가 잡히지 않게 한다. 보드가 늘면 MAX 만 올리면 됨.
SLAVE_ID_MIN = 1
SLAVE_ID_MAX = 99


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
                "prev_state_mask": None,  # 직전 heartbeat 의 점유 마스크 (debounce용)
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

# ── 디페스타 LED (정답 완성 표시등, master GPIO39) ─────
def send_master_command(cmd: str) -> None:
    """PC→master USB 로 짧은 명령 한 줄 전송 (예: 'D1'/'D0')."""
    with _master_ser_lock:
        ser = _master_ser
    if ser is None:
        return
    try:
        ser.write((cmd + "\n").encode("ascii"))
    except Exception as e:
        print(f"[master] 명령 전송 실패({cmd}): {e}")


def _dispensa_ready_locked() -> bool:
    """디페스타 활성(정답 완성) 여부. _state_lock 을 이미 잡은 상태에서 호출.

    조건: 게임 활성 + 점유된 칸이 모두 correct + 모든 정답 위치가 채워짐.
    오답/미상 칸이 하나라도 있으면 비활성.
    """
    if not game_active:
        return False
    if not answer_sequence:
        return False
    satisfied = set()
    for key, c in _cells.items():
        if c.get("result") != "correct":
            return False  # 오답·미상 칸 존재 → 비활성
        slave_s, cell_s = key.split(":")
        q, _note = parse_cell(int(cell_s))
        satisfied.add(str(q + (int(slave_s) - 1) * 2))
    return all(q_actual in satisfied for q_actual in answer_sequence)


def update_dispensa_output() -> None:
    """정답 완성 상태가 바뀌면 master 로 D1/D0 전송해 GPIO39 를 갱신."""
    global _dispensa_out
    with _state_lock:
        ready = _dispensa_ready_locked()
    if ready != _dispensa_out:
        _dispensa_out = ready
        send_master_command("D1" if ready else "D0")
        print(f"[dispensa] LED {'ON(정답 완성)' if ready else 'OFF'}")


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
    update_dispensa_output()   # 새 게임 시작 → 디페스타 LED 초기화(OFF)
    osc.send_message("/game/start", [current_stage, 0])
    osc.send_message("/game/listen", [])
    return True

def end_game(reason: str = "complete") -> None:
    global game_active
    game_active = False
    clear_cells()
    set_ready(False)
    update_dispensa_output()   # 게임 종료 → 디페스타 LED 끄기
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
    # 유효 범위 밖 slave(예: 시리얼 손상으로 깨진 slave:0)는 메시지 자체를 폐기 →
    # admin 에 '유령 슬레이브'가 잡히지 않는다.
    sid = event.get("slave")
    if sid is not None:
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            return  # slave 필드가 손상된 메시지 → 폐기
        if not (SLAVE_ID_MIN <= sid <= SLAVE_ID_MAX):
            print(f"[WARN] 유효 범위 밖 slave id 무시: {sid} (손상 메시지 또는 오설정 보드)")
            return
        _touch_slave(sid, heartbeat=(t == "heartbeat"))

    if t == "game":
        action = str(event.get("action", "")).lower()
        if action == "start":
            start_game()
        elif action == "dispensa":
            request_dispensa()
        elif action == "listen":
            request_listen()
        elif action == "play":
            request_play()
        else:
            print(f"[WARN] unknown game action: {action}")
        return
    if t == "heartbeat":
        # 생존 기록은 위에서 완료. state 마스크가 있으면 유실 이벤트 자가복구.
        # (sid 는 위에서 유효 범위 검증·int 변환 완료된 값)
        if sid is not None and "state" in event:
            try:
                reconcile_slave_state(sid, int(event["state"]))
                update_dispensa_output()  # 자가복구로 칸이 바뀌었을 수 있음
            except (TypeError, ValueError):
                pass  # 이상한 state 값은 reconcile 만 건너뜀
        return
    handle_event(event)
    update_dispensa_output()  # added/removed 로 정답 완성 상태가 바뀌면 LED 갱신

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

# ── 자가복구 (heartbeat state mask 기반 reconcile) ────
# slave heartbeat 의 16비트 점유 마스크로 유실된 added/removed 를 스스로 메꾼다.
#  - mask=점유 / memory=빔  → added 유실. UID 를 모르므로 "미상"으로 admin 에만 표시
#                             (/orb/recovered — screen 은 이 주소를 처리 안 함).
#                             관람자가 스스로 다시 올리면 진짜 added 로 정확히 복구됨.
#  - mask=빔  / memory=점유 → removed 유실. UID 불필요 → 정식 /orb/removed 로 화면도 정리.
# 센서 순간 오검출(flicker) 방지: "직전 heartbeat 와 같은 마스크"일 때만 반영(연속 2회).

def _synth_added_unknown(slave_id: int, cell: int) -> None:
    """유실된 added 추정 — 정체 미상. admin 에만 표시하고 screen 에는 보내지 않는다."""
    _q, note = parse_cell(cell)
    with _state_lock:
        _cells[_cell_key(slave_id, cell)] = {
            "id": "", "voice": "미상", "note": note,
            "result": "unknown", "synthetic": True,
        }
    _record_event({"type": "recovered", "slave": slave_id, "cell": cell,
                   "note": note, "voice": "미상", "synthetic": True})
    print(f"[recover] slave={slave_id} cell={cell} note={note} → added 유실 추정(미상, admin만)")
    osc.send_message("/orb/recovered", [slave_id, cell, note])


def _synth_removed(slave_id: int, cell: int) -> None:
    """유실된 removed 추정 — 정식 removed 로 처리해 screen·admin 모두 칸을 비운다."""
    with _state_lock:
        cell_data = _cells.pop(_cell_key(slave_id, cell), None)
    voice   = (cell_data or {}).get("voice", "unknown")
    orb_key = (cell_data or {}).get("id", "") or ""
    _q, note = parse_cell(cell)
    _record_event({"type": "removed", "slave": slave_id, "cell": cell,
                   "id": orb_key, "voice": voice, "note": note, "synthetic": True})
    print(f"[recover] slave={slave_id} cell={cell} → removed 유실 복구(screen+admin)")
    osc.send_message("/orb/removed", [slave_id, cell, voice, note, orb_key])


def reconcile_slave_state(slave_id: int, state: int) -> None:
    """heartbeat 점유 마스크(state)로 메모리와의 불일치를 자가복구. flicker debounce 포함."""
    with _state_lock:
        s = _slaves.get(slave_id)
        prev = s.get("prev_state_mask") if s else None
        if s is not None:
            s["prev_state_mask"] = state
        # 직전 heartbeat 와 마스크가 동일해야(=~3초 안정) 반영. 순간 오검출 1회는 무시.
        stable = prev is not None and prev == state
        occupied_mem = {
            int(k.split(":", 1)[1])
            for k in _cells
            if k.startswith(f"{slave_id}:")
        }

    if not stable:
        return

    for cell in range(1, 17):
        mask_occ = bool((state >> (cell - 1)) & 1)
        mem_occ  = cell in occupied_mem
        if mask_occ and not mem_occ:
            _synth_added_unknown(slave_id, cell)
        elif not mask_occ and mem_occ:
            _synth_removed(slave_id, cell)

# ── 시리얼 수신 루프 ───────────────────────────────────
def serial_loop():
    port     = config["serial"]["port"]
    baudrate = config["serial"]["baudrate"]
    _last_master_bad = [0]   # 마지막으로 본 누적 bad 카운트

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
        global _master_ser, _dispensa_out
        with _master_ser_lock:
            _master_ser = ser        # PC→master 명령 전송용 핸들 공유
        _dispensa_out = False        # 재연결 시 상태 캐시 초기화 → 다음 갱신에서 재전송
        try:
            while True:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("#"):
                    # master 진단(alive/relay) — 평소엔 조용히.
                    # 깨진 프레임(bad)이 '새로 늘어난' 순간에만 한 줄 노출.
                    if "alive" in line:
                        m = re.search(r"bad=(\d+)", line)
                        bad = int(m.group(1)) if m else 0
                        if bad < _last_master_bad[0]:
                            _last_master_bad[0] = 0   # 마스터 재부팅 → 카운터 리셋
                        if bad > _last_master_bad[0]:
                            print(f"[master] 깨진 프레임 감지 (누적 bad={bad})")
                            _last_master_bad[0] = bad
                        continue
                    print(f"[master] {line}")
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
            with _master_ser_lock:
                _master_ser = None   # 끊긴 핸들로 쓰지 않도록 해제
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
