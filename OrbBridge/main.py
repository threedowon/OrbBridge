import json
import serial
import threading
import time
from pythonosc import udp_client

# ── 설정 로드 ──────────────────────────────────────────
with open("config.json")      as f: config      = json.load(f)
with open("orb_mapping.json") as f: orb_mapping = json.load(f)
with open("stage_data.json")  as f: stage_data  = json.load(f)

current_stage   = config["current_stage"]
stage           = next(s for s in stage_data["stages"] if s["stage"] == current_stage)
answer_sequence = stage["sequence"]
time_limit      = stage["time_limit"]
melody_id       = config.get("melody", "biplane")

osc = udp_client.SimpleUDPClient(config["osc"]["host"], config["osc"]["port"])

game_active  = False
timer_thread = None

NOTE_MAP = {1: "C", 2: "D", 3: "E", 4: "F", 5: "G", 6: "A", 7: "B", 8: "C"}

# ── 셀 파싱 ────────────────────────────────────────────
def parse_cell(cell: int):
    q    = 1 if cell % 2 == 1 else 2
    row  = (cell + 1) // 2
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

def start_game() -> bool:
    global game_active, timer_thread
    if game_active:
        print("[game] 이전 게임 정리 후 재시작")
        end_game("restart")
    game_active = True
    print(f"[game] 시작 — stage {current_stage}, melody={melody_id}")
    set_ready(True)
    osc.send_message("/game/start", [current_stage, 0])
    return True

def end_game(reason: str = "complete") -> None:
    global game_active
    game_active = False
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

# ── 이벤트 처리 ────────────────────────────────────────
def handle_event(event: dict):
    t      = event.get("type")
    slave  = event.get("slave")
    cell   = event.get("cell")
    orb_id = event.get("id")

    if t == "added":
        orb = orb_mapping.get(orb_id)
        if orb is None:
            print(f"[WARN] 알 수 없는 ID: {orb_id}")
            osc.send_message("/orb/added", [slave, cell, "unknown", "invalid", "wrong"])
            return

        voice      = orb["voice"]
        q, note    = parse_cell(cell)
        q_actual   = q + (slave - 1) * 2
        result     = judge(slave, cell) if game_active else "inactive"

        print(f"[added]   slave={slave} cell={cell} Q={q_actual} note={note} voice={voice} → {result}")
        osc.send_message("/orb/added", [slave, cell, voice, note, result])

    elif t == "removed":
        orb      = orb_mapping.get(orb_id, {})
        voice    = orb.get("voice", "unknown")
        q, note  = parse_cell(cell)
        q_actual = q + (slave - 1) * 2

        print(f"[removed] slave={slave} cell={cell} Q={q_actual} note={note} voice={voice}")
        osc.send_message("/orb/removed", [slave, cell, voice, note])

# ── 시리얼 수신 루프 ───────────────────────────────────
def serial_loop():
    port     = config["serial"]["port"]
    baudrate = config["serial"]["baudrate"]

    while True:
        try:
            ser = serial.Serial(port, baudrate, timeout=1)
        except serial.SerialException as e:
            print(f"[serial] 포트 연결 실패 ({port}): {e}")
            time.sleep(5)
            continue

        print(f"[serial] 연결됨: {port}")
        try:
            while True:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    handle_event(event)
                except json.JSONDecodeError:
                    print(f"[WARN] JSON 파싱 실패: {line}")
        except serial.SerialException as e:
            print(f"[serial] 연결 끊김 ({port}): {e}")
        finally:
            ser.close()
        time.sleep(5)

# ── 진입점 ─────────────────────────────────────────────
if __name__ == "__main__":
    input_mode = config.get("input_mode", "serial")

    print(f"[stage {current_stage}] 정답 시퀀스: {answer_sequence}")
    print(f"[melody] {melody_id}")
    print(f"[input] {input_mode} mode")
    print("screen에서 시작을 누르면 게임이 시작됩니다.")

    if input_mode == "mock":
        from mock_server import start_mock_server
        start_mock_server(
            config.get("mock", {}),
            handle_event,
            orb_mapping,
            on_game_start=start_game,
            on_game_end=end_game,
            on_game_listen=request_listen,
            on_game_play=request_play,
        )
    else:
        threading.Thread(target=serial_loop, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("종료")
