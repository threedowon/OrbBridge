import json
import os
from pathlib import Path
import serial
import threading
import time
from pythonosc import udp_client

from game_engine import (
    FinishReason,
    GameEngine,
    GamePhase,
    InvalidTransition,
    PlacementResult,
)
from game_runtime import GameRuntime, RuntimeResult
from scheduler import PlaybackMode, PlaybackScheduler, load_melody
from mapping_store import find_orb, load_mapping

from app_paths import data_dir, ensure_data_files, melodies_dir, repo_root

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
idle_time_limit = stage.get("idle_time_limit")
melody_id       = config.get("melody", "biplane")
_raw_input_mode = config.get("input_mode", "table")
# legacy: "mock" → "table" (웹 /table UI 입력)
input_mode      = "table" if _raw_input_mode == "mock" else _raw_input_mode

osc = udp_client.SimpleUDPClient(config["osc"]["host"], config["osc"]["port"])
_osc_lock = threading.Lock()

NOTE_MAP = {1: "C", 2: "D", 3: "E", 4: "F", 5: "G", 6: "A", 7: "B", 8: "C"}

# ── Admin 상태 (셀 점유, 이벤트 로그, 시리얼) ────────
_state_lock = threading.Lock()
_event_log: list[dict] = []
EVENT_LOG_LIMIT = 100
_serial_status = {"connected": False, "port": "", "error": ""}

# ── M2 게임 런타임 (Serial/HTTP 입력의 단일 writer) ───
_runtime_lock = threading.Lock()
_game_runtime: GameRuntime | None = None

# ── M3 재생 Scheduler (Listen·Play·Dispensa의 단일 clock) ──
# GameRuntime은 게임 상태를, PlaybackScheduler는 박자와 MIDI 출력을 소유한다.
_scheduler_lock = threading.Lock()
_playback_scheduler: PlaybackScheduler | None = None

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


def audio_output_mode() -> str:
    """Return the validated output backend selected in config.json."""
    mode = str(config.get("audio_output", "web")).lower().strip()
    return mode if mode in ("midi", "web") else "web"


def _scheduler_send_notes(notes: list[dict]) -> list[dict]:
    """Send one due Scheduler beat as a single locked MIDI burst.

    Web mode receives the same notes through the playback beat OSC payload, so
    Python intentionally does not open MIDI ports in that development fallback.
    """
    if audio_output_mode() != "midi" or not notes:
        return []

    from midi_out import trigger_many

    results = trigger_many(notes)
    failures = [result for result in results if not result.get("ok")]
    if failures:
        print(f"[scheduler] MIDI WARN: {failures}")
    return results


def _scheduler_publish_event(event: dict) -> None:
    """Translate Scheduler callbacks to the OSC/bridge/WebSocket protocol."""
    event_type = event["type"]
    run_id = int(event["run_id"])
    mode = str(event["mode"])

    if event_type == "start":
        _osc_send(
            "/game/playback/start",
            [run_id, mode, int(event["step_count"])],
        )
    elif event_type == "beat":
        # OSC transports the optional web-audio note batch as compact JSON.
        # MIDI mode ignores this field and uses Python's trigger_many directly.
        encoded_notes = json.dumps(
            event.get("notes") or [],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _osc_send(
            "/game/playback/beat",
            [
                run_id,
                mode,
                int(event["step"]),
                int(event["duration_ms"]),
                encoded_notes,
            ],
        )
    elif event_type == "end":
        _osc_send(
            "/game/playback/end",
            [run_id, mode, 1 if event.get("cancelled") else 0],
        )


def get_playback_scheduler() -> PlaybackScheduler:
    """Create the process-wide Scheduler lazily for tests and CLI tools."""
    global _playback_scheduler
    with _scheduler_lock:
        if _playback_scheduler is None:
            _playback_scheduler = PlaybackScheduler(
                send_notes=_scheduler_send_notes,
                publish_event=_scheduler_publish_event,
            )
        return _playback_scheduler


def _cancel_playback() -> None:
    """Cancel without joining so GameRuntime phase callbacks never block."""
    with _scheduler_lock:
        scheduler = _playback_scheduler
    if scheduler is not None:
        scheduler.cancel()


def _melody_path() -> Path:
    """Resolve editable user data first, then the bundled screen fallback."""
    filename = f"{melody_id}.json"
    user_path = melodies_dir() / filename
    if user_path.is_file():
        return user_path
    bundled_path = repo_root() / "screen" / "melodies" / filename
    if bundled_path.is_file():
        return bundled_path
    raise FileNotFoundError(f"melody not found: {filename}")


def _start_playback(mode: PlaybackMode) -> dict:
    """Snapshot melody and cells before handing an immutable plan to M3."""
    melody = load_melody(_melody_path())
    cells = get_game_state().get("cells") or {}
    return get_playback_scheduler().start(mode, melody, cells)


def _osc_send(address: str, args) -> None:
    """Serialize UDP writes made by HTTP, serial, and runtime threads."""
    with _osc_lock:
        osc.send_message(address, args)


def _new_game_engine() -> GameEngine:
    return GameEngine(
        answer_sequence,
        total_timeout_s=time_limit,
        idle_timeout_s=idle_time_limit,
    )


def _phase_payload(snapshot: dict) -> list:
    timers = snapshot.get("timers") or {}
    return [
        snapshot["phase"],
        snapshot.get("finish_reason") or "",
        snapshot.get("revision", 0),
        timers.get("remaining_total_s")
        if timers.get("remaining_total_s") is not None
        else -1,
        timers.get("remaining_idle_s")
        if timers.get("remaining_idle_s") is not None
        else -1,
    ]


def _on_phase_change(previous: str, current: str, snapshot: dict) -> None:
    """Translate Python phase transitions to OSC and stop invalid playback."""
    if current not in (GamePhase.STARTING.value, GamePhase.RUNNING.value):
        _cancel_playback()
    set_ready(current == GamePhase.RUNNING.value)

    if current == GamePhase.FINISHED.value:
        _osc_send("/game/end", [snapshot.get("finish_reason") or "manual"])
    elif current == GamePhase.RESETTING.value:
        _osc_send("/game/reset", [])
    elif (
        current == GamePhase.READY.value
        and previous == GamePhase.RESETTING.value
    ):
        _osc_send("/game/reset_complete", [])

    _osc_send("/game/state", _phase_payload(snapshot))


def get_game_runtime() -> GameRuntime:
    global _game_runtime
    with _runtime_lock:
        if _game_runtime is None:
            _game_runtime = GameRuntime(
                _new_game_engine(),
                tick_interval_s=0.05,
                on_phase_change=_on_phase_change,
            )
            _game_runtime.start()
        return _game_runtime


def get_game_state() -> dict:
    return get_game_runtime().snapshot()


def get_status() -> dict:
    game = get_game_state()
    now = time.time() * 1000
    with _state_lock:
        serial = dict(_serial_status)
        slaves = _slave_list_locked(now)
        return {
            "input_mode": input_mode,
            "game_active": game["phase"] == GamePhase.RUNNING.value,
            "game": game,
            "current_stage": current_stage,
            "melody": melody_id,
            "audio_output": audio_output_mode(),
            "playback": get_playback_scheduler().snapshot(),
            "orb_count": len(orb_mapping),
            "serial": serial,
            "cell_count": game["cell_count"],
            "slaves": slaves,
            "slave_online_timeout_s": SLAVE_ONLINE_TIMEOUT_S,
        }


def get_cells() -> dict:
    cells = {}
    for key, data in get_game_state()["cells"].items():
        orb_id = data["orb_id"]
        synthetic = orb_id.startswith("__recovered__:")
        cells[key] = {
            "id": "" if synthetic else orb_id,
            "voice": data["voice"] or ("미상" if synthetic else "unknown"),
            "note": data["note"],
            "result": data["result"],
        }
        if synthetic:
            cells[key]["synthetic"] = True
    return cells


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

# ── ready / game lifecycle ─────────────────────────────
def set_ready(ready: bool) -> None:
    _osc_send("/game/ready", [1 if ready else 0])
    print(f"[ready] {ready}")


def reload_stage_data() -> None:
    """Pick up Melody Editor saves (stage_data.json, config melody) without restarting."""
    global stage_data, stage, answer_sequence, time_limit, idle_time_limit
    global melody_id, config, current_stage, input_mode
    with open(SERVER_DIR / "config.json", encoding="utf-8") as f:
        config = json.load(f)
    current_stage = config["current_stage"]
    melody_id = config.get("melody", "biplane")
    raw_mode = config.get("input_mode", "table")
    input_mode = "table" if raw_mode == "mock" else raw_mode
    with open(SERVER_DIR / "stage_data.json", encoding="utf-8") as f:
        stage_data = json.load(f)
    stage = next(s for s in stage_data["stages"] if s["stage"] == current_stage)
    answer_sequence = stage["sequence"]
    time_limit = stage["time_limit"]
    idle_time_limit = stage.get("idle_time_limit")
    print(f"[stage] 재로드 — melody={melody_id}, 정답 시퀀스: {answer_sequence}")


def _publish_transition(result: RuntimeResult) -> dict:
    runtime = get_game_runtime()
    runtime.publish_phase_change(result)
    snapshot = result.snapshot

    # A solved game must immediately release the table for orb recovery.
    # Keep FINISHED as an observable transition so screen success effects run,
    # then let the Python source of truth enter RESETTING without a browser call.
    if (
        result.phase_changed
        and result.phase == GamePhase.FINISHED.value
        and snapshot.get("finish_reason") == FinishReason.COMPLETE.value
    ):
        reset_result = runtime.reset_game()
        runtime.publish_phase_change(reset_result)
        snapshot = reset_result.snapshot

    return snapshot


def start_game() -> dict:
    runtime = get_game_runtime()
    _cancel_playback()
    reload_stage_data()

    current = runtime.snapshot()
    if current["phase"] != GamePhase.READY.value:
        raise InvalidTransition(
            f"START is invalid from {current['phase']}; RESET must finish first"
        )
    if current["cell_count"]:
        raise InvalidTransition("START requires an empty table")

    runtime.replace_engine(_new_game_engine())
    started = runtime.start_game()
    _publish_transition(started)

    print(f"[game] 시작 — stage {current_stage}, melody={melody_id}")
    _osc_send("/game/start", [current_stage, 0])
    request_listen()

    if bool(config.get("start_auto_complete", True)):
        snapshot = complete_game_start()["game"]
    else:
        snapshot = started.snapshot
    return {"ok": True, "game": snapshot}


def complete_game_start() -> dict:
    result = get_game_runtime().complete_start()
    snapshot = _publish_transition(result)
    print("[game] STARTING → RUNNING")
    return {"ok": True, "game": snapshot}


def end_game(reason: str = "complete") -> dict:
    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    phase = snapshot["phase"]

    if phase == GamePhase.RUNNING.value:
        finish_reason = (
            FinishReason.COMPLETE
            if str(reason).lower() == FinishReason.COMPLETE.value
            else FinishReason.MANUAL
        )
        result = runtime.finish_game(finish_reason)
        snapshot = _publish_transition(result)
    elif phase == GamePhase.STARTING.value:
        result = runtime.reset_game()
        snapshot = _publish_transition(result)

    print(f"[game] 종료 요청 — {reason} ({snapshot['phase']})")
    return {"ok": True, "game": snapshot}


def reset_game() -> dict:
    result = get_game_runtime().reset_game()
    snapshot = _publish_transition(result)
    print(f"[game] 리셋 — {snapshot['phase']}")
    return {"ok": True, "game": snapshot}


def request_listen() -> dict:
    """Start Python-owned target-melody preview from STARTING or RUNNING."""
    snapshot = get_game_state()
    phase = snapshot["phase"]
    if phase not in (GamePhase.STARTING.value, GamePhase.RUNNING.value):
        print(f"[game] 다시 듣기 무시 — {phase}")
        return {"ok": False, "ignored": True, "game": snapshot}

    print("[game] 다시 듣기")
    _osc_send("/game/listen", [])
    playback = _start_playback(PlaybackMode.LISTEN)
    return {"ok": True, "playback": playback, "game": snapshot}


def request_play() -> dict:
    """Play each placed orb at its current column and physical pitch."""
    snapshot = get_game_state()
    if snapshot["phase"] != GamePhase.RUNNING.value:
        print("[game] 재생 무시 — RUNNING 아님")
        return {"ok": False, "ignored": True, "game": snapshot}

    print("[game] 재생")
    _osc_send("/game/play", [])
    playback = _start_playback(PlaybackMode.PLAY)
    return {"ok": True, "playback": playback, "game": snapshot}


def request_dispensa() -> dict:
    """Play the answer melody with every unique voice currently on the table."""
    snapshot = get_game_state()
    if snapshot["phase"] != GamePhase.RUNNING.value:
        print("[game] 디페스타 무시 — RUNNING 아님")
        return {"ok": False, "ignored": True, "game": snapshot}

    print("[game] 디페스타")
    _osc_send("/game/dispensa", [])
    playback = _start_playback(PlaybackMode.DISPENSA)
    return {"ok": True, "playback": playback, "game": snapshot}

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
        try:
            if action == "start":
                start_game()
            elif action in ("start_complete", "start-complete"):
                complete_game_start()
            elif action == "listen":
                request_listen()
            elif action == "play":
                request_play()
            elif action == "dispensa":
                request_dispensa()
            elif action == "end":
                end_game(str(event.get("reason", "manual")))
            elif action == "reset":
                reset_game()
            else:
                print(f"[WARN] unknown game action: {action}")
        except (InvalidTransition, ValueError, RuntimeError) as exc:
            print(f"[WARN] game action {action} rejected: {exc}")
        return
    if t == "heartbeat":
        # 생존 기록은 위에서 완료. state 마스크가 있으면 유실 이벤트 자가복구.
        # (sid 는 위에서 유효 범위 검증·int 변환 완료된 값)
        if sid is not None and "state" in event:
            try:
                reconcile_slave_state(sid, int(event["state"]))
            except (TypeError, ValueError):
                pass  # 이상한 state 값은 reconcile 만 건너뜀
        return
    handle_event(event)

# ── 이벤트 처리 ────────────────────────────────────────
def handle_event(event: dict) -> dict:
    global orb_mapping
    orb_mapping = load_mapping()

    t = str(event.get("type", "")).lower()
    if t not in ("added", "removed"):
        raise ValueError("type must be added or removed")
    try:
        slave = int(event.get("slave"))
        cell = int(event.get("cell"))
    except (TypeError, ValueError) as exc:
        raise ValueError("slave and cell must be integers") from exc
    orb_id = str(event.get("id") or "").strip()
    if not orb_id:
        raise ValueError("id must not be empty")

    runtime = get_game_runtime()
    orb_key, orb = find_orb(orb_id, orb_mapping)
    q, note = parse_cell(cell)
    q_actual = q + (slave - 1) * 2

    if t == "added":
        if orb is None:
            print(f"[WARN] 알 수 없는 ID: {orb_id}")
            runtime_result = runtime.add_orb(
                orb_id=orb_id,
                slave=slave,
                cell=cell,
                voice="unknown",
                known=False,
            )
            result = runtime_result.value.value
            _record_event({"type": "added", "slave": slave, "cell": cell,
                           "id": orb_id, "voice": "unknown", "note": note, "result": result})
            _osc_send("/orb/added", [slave, cell, "unknown", note, result, orb_id])
            snapshot = _publish_transition(runtime_result)
            return {
                "ok": True,
                "changed": True,
                "result": result,
                "game": snapshot,
            }

        voice = orb["voice"]
        runtime_result = runtime.add_orb(
            orb_id=orb_key,
            slave=slave,
            cell=cell,
            voice=voice,
            known=True,
        )
        result = runtime_result.value.value
        _record_event({"type": "added", "slave": slave, "cell": cell,
                       "id": orb_key, "voice": voice, "note": note, "result": result})

        print(f"[added]   slave={slave} cell={cell} Q={q_actual} note={note} voice={voice} → {result}")
        _osc_send("/orb/added", [slave, cell, voice, note, result, orb_key])

        # MIDI mode: sound immediately (screen skips placeExternal tone to avoid double)
        if (
            audio_output_mode() == "midi"
            and runtime_result.value is not PlacementResult.INACTIVE
        ):
            try:
                from midi_out import cell_to_pitch, trigger as midi_trigger

                midi_trigger(voice, cell_to_pitch(int(cell)), duration_ms=320)
            except Exception as exc:
                print(f"[midi] trigger failed: {exc}")
        snapshot = _publish_transition(runtime_result)
        return {
            "ok": True,
            "changed": True,
            "result": result,
            "game": snapshot,
        }

    voice = orb.get("voice", "unknown") if orb else "unknown"
    runtime_result = runtime.remove_orb(
        slave=slave,
        cell=cell,
        orb_id=orb_key or orb_id,
    )
    if not runtime_result.value:
        return {
            "ok": True,
            "changed": False,
            "game": runtime_result.snapshot,
        }

    _record_event({"type": "removed", "slave": slave, "cell": cell,
                   "id": orb_key or orb_id, "voice": voice, "note": note})

    print(f"[removed] slave={slave} cell={cell} Q={q_actual} note={note} voice={voice}")
    _osc_send("/orb/removed", [slave, cell, voice, note, orb_key or orb_id])
    snapshot = _publish_transition(runtime_result)
    return {
        "ok": True,
        "changed": True,
        "game": snapshot,
    }

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
    runtime_result = get_game_runtime().add_orb(
        orb_id=f"__recovered__:{slave_id}:{cell}",
        slave=slave_id,
        cell=cell,
        voice="미상",
        known=False,
    )
    _record_event({"type": "recovered", "slave": slave_id, "cell": cell,
                   "note": note, "voice": "미상", "synthetic": True})
    print(f"[recover] slave={slave_id} cell={cell} note={note} → added 유실 추정(미상, admin만)")
    _osc_send("/orb/recovered", [slave_id, cell, note])
    _publish_transition(runtime_result)


def _synth_removed(slave_id: int, cell: int) -> None:
    """유실된 removed 추정 — 정식 removed 로 처리해 screen·admin 모두 칸을 비운다."""
    cell_data = get_cells().get(_cell_key(slave_id, cell))
    runtime_result = get_game_runtime().remove_orb(
        slave=slave_id,
        cell=cell,
    )
    if not runtime_result.value:
        return
    voice   = (cell_data or {}).get("voice", "unknown")
    orb_key = (cell_data or {}).get("id", "") or ""
    _q, note = parse_cell(cell)
    _record_event({"type": "removed", "slave": slave_id, "cell": cell,
                   "id": orb_key, "voice": voice, "note": note, "synthetic": True})
    print(f"[recover] slave={slave_id} cell={cell} → removed 유실 복구(screen+admin)")
    _osc_send("/orb/removed", [slave_id, cell, voice, note, orb_key])
    _publish_transition(runtime_result)


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
            for k in get_cells()
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
                if line.startswith("#"):
                    print(f"[master] {line}")   # master 진단 출력 (폴링 생존/relay 카운트)
                    continue
                if not line.startswith("{"):
                    continue  # ESP 부트 로그, 디버그 println 등 — 무시
                try:
                    event = json.loads(line)
                    handle_serial_message(event)
                except json.JSONDecodeError:
                    print(f"[WARN] JSON 파싱 실패: {line[:120]}")
                except (ValueError, RuntimeError) as exc:
                    print(f"[WARN] 이벤트 처리 실패: {exc}")
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
    runtime = get_game_runtime()
    print(f"[stage {current_stage}] 정답 시퀀스: {answer_sequence}")
    print(f"[melody] {melody_id}")
    print(f"[input] {input_mode} mode")
    print(f"[audio] {audio_output_mode()}")
    print("screen에서 시작을 누르면 게임이 시작됩니다.")
    print(f"[http] http://127.0.0.1:{config.get('mock', {}).get('port', 8080)}/screen")
    print(f"[http] http://127.0.0.1:{config.get('mock', {}).get('port', 8080)}/table")

    if audio_output_mode() == "midi":
        try:
            from midi_out import ensure_init

            ok, err = ensure_init()
            if ok:
                print("[midi] ready (loopMIDI → Ableton)")
            else:
                print(f"[midi] WARN: {err}")
        except Exception as exc:
            print(f"[midi] WARN: init failed: {exc}")

    from mock_server import start_http_server

    http_cfg = config.get("mock", {"host": "127.0.0.1", "port": 8080})
    start_http_server(
        http_cfg,
        handle_event,
        orb_mapping,
        get_status=get_status,
        get_game_state=get_game_state,
        get_cells=get_cells,
        get_events=get_events,
        on_game_start=start_game,
        on_game_start_complete=complete_game_start,
        on_game_end=end_game,
        on_game_reset=reset_game,
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
    finally:
        runtime.close()
        with _scheduler_lock:
            scheduler = _playback_scheduler
        if scheduler is not None:
            scheduler.close()


if __name__ == "__main__":
    from bridge_service import start_bridge_thread

    start_bridge_thread()
    time.sleep(0.4)
    run_main()
