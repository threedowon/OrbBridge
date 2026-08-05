import json
import os
import serial
import threading
import time
from copy import deepcopy

from game_engine import (
    EndReason,
    GameEngine,
    GameState,
    InvalidTransition,
    PlacementResult,
)
from game_runtime import GameRuntime, RuntimeResult
from melody_store import (
    answer_sequence_from_melody,
    game_rules_from_melody,
    load_melody_document,
)
from scheduler import PlaybackMode, PlaybackScheduler, parse_melody
from mapping_store import find_orb, load_mapping

from app_paths import data_dir, ensure_data_files, melodies_dir

ensure_data_files()
SERVER_DIR = data_dir()
os.chdir(SERVER_DIR)

# ── 설정 로드 ──────────────────────────────────────────
with open(SERVER_DIR / "config.json")      as f: config      = json.load(f)
with open(SERVER_DIR / "orb_mapping.json") as f: orb_mapping = json.load(f)

current_stage   = int(config.get("current_stage", 1))
melody_id       = config.get("melody", "biplane")
melody_path     = melodies_dir() / f"{melody_id}.json"
melody_document = load_melody_document(melody_path)
playback_melody = parse_melody(melody_document)
answer_sequence = answer_sequence_from_melody(melody_document)
game_rules      = game_rules_from_melody(melody_document)
time_over_s     = game_rules["time_over_s"]
idle_s          = game_rules["idle_s"]
dfesta_presses_required = game_rules["dfesta_presses_required"]
_raw_input_mode = config.get("input_mode", "table")
# 이전 설정값인 "mock"은 웹 /table 입력을 뜻하는 "table"로 바꾼다.
input_mode      = "table" if _raw_input_mode == "mock" else _raw_input_mode

NOTE_MAP = {1: "C", 2: "D", 3: "E", 4: "F", 5: "G", 6: "A", 7: "B", 8: "C"}

# Serial JSON과 admin HTTP 제어가 함께 사용하는 이벤트 문자열이다.
GAME_ACTION_START = "start"
GAME_ACTION_END = "end"
GAME_ACTION_RESET = "reset"
GAME_ACTION_LISTEN = "listen"
GAME_ACTION_PLAY = "play"
GAME_ACTION_DFESTA = "dfesta"

# ── Admin 상태 (셀 점유, 이벤트 로그, 시리얼) ────────
_state_lock = threading.Lock()
_event_log: list[dict] = []
EVENT_LOG_LIMIT = 100
_serial_status = {"connected": False, "port": "", "error": ""}

# ── M2 게임 런타임 (Serial/HTTP 입력의 단일 writer) ───
_runtime_lock = threading.Lock()
_game_runtime: GameRuntime | None = None

# ── M3 재생 Scheduler (Listen·Play·DFesta의 단일 clock) ──
# GameRuntime은 게임 상태를, PlaybackScheduler는 박자와 MIDI 출력을 소유한다.
_scheduler_lock = threading.Lock()
_playback_scheduler: PlaybackScheduler | None = None

# ── 직접 WebSocket (브라우저 실시간 이벤트 + 재연결 snapshot) ──
# screen/table/admin은 모두 이 서비스에서 직접 상태를 받는다.
_web_event_lock = threading.Lock()
_websocket_lock = threading.Lock()
_websocket_service = None

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
                "prev_occupancy_mask": None,  # 직전 heartbeat 점유 마스크다.
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
    """config.json에서 검증된 오디오 출력 방식을 반환한다."""
    mode = str(config.get("audio_output", "web")).lower().strip()
    return mode if mode in ("midi", "web") else "web"


def _scheduler_send_notes(notes: list[dict]) -> list[dict]:
    """도달한 Scheduler beat 하나를 잠긴 MIDI 묶음으로 보낸다.

    web 모드는 같은 음표를 WebSocket beat 이벤트로 받으므로 Python에서
    MIDI 포트를 열지 않는다.
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
    """Scheduler callback을 브라우저 WebSocket 이벤트로 변환한다."""
    event_type = event["type"]
    run_id = int(event["run_id"])
    mode = str(event["mode"])

    if event_type == "start":
        _publish_web_event(
            "/game/playback/start",
            [run_id, mode, int(event["step_count"])],
        )
    elif event_type == "beat":
        # Web Audio용 음표 묶음을 압축된 JSON으로 전송한다.
        # MIDI 모드는 이 필드를 무시하고 Python의 trigger_many를 사용한다.
        encoded_notes = json.dumps(
            event.get("notes") or [],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _publish_web_event(
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
        _publish_web_event(
            "/game/playback/end",
            [run_id, mode, 1 if event.get("cancelled") else 0],
        )


def get_playback_scheduler() -> PlaybackScheduler:
    """테스트와 CLI에서도 쓸 수 있게 Scheduler를 지연 생성한다."""
    global _playback_scheduler
    with _scheduler_lock:
        if _playback_scheduler is None:
            _playback_scheduler = PlaybackScheduler(
                send_notes=_scheduler_send_notes,
                publish_event=_scheduler_publish_event,
            )
        return _playback_scheduler


def _cancel_playback() -> None:
    """GameRuntime 상태 callback을 막지 않도록 join 없이 취소한다."""
    with _scheduler_lock:
        scheduler = _playback_scheduler
    if scheduler is not None:
        scheduler.cancel()


def _start_playback(mode: PlaybackMode) -> dict:
    """게임 시작 때 고정한 멜로디와 현재 셀로 재생 plan을 만든다."""
    cells = get_game_state().get("cells") or {}
    return get_playback_scheduler().start(mode, playback_melody, cells)


def _publish_web_event(address: str, args) -> None:
    """연결된 모든 브라우저에 순서가 보장된 이벤트 하나를 보낸다."""

    # Runtime과 Scheduler가 서로 다른 스레드에서 실행되므로 하나의 lock으로
    # WebSocketService에 전달되는 순서를 보장한다.
    with _web_event_lock:
        with _websocket_lock:
            service = _websocket_service
        if service is not None:
            service.broadcast(address, args)


def _new_game_engine() -> GameEngine:
    return GameEngine(
        answer_sequence,
        time_over_s=time_over_s,
        idle_s=idle_s,
        dfesta_presses_required=dfesta_presses_required,
    )


def _state_payload(snapshot: dict, previous_state: str | None = None) -> list:
    timers = snapshot.get("timers") or {}
    return [
        snapshot["state"],
        snapshot.get("end_reason") or "",
        snapshot.get("revision", 0),
        timers.get("remaining_time_over_s")
        if timers.get("remaining_time_over_s") is not None
        else -1,
        timers.get("remaining_idle_s")
        if timers.get("remaining_idle_s") is not None
        else -1,
        previous_state or "",
    ]


def _on_state_change(previous: str, current: str, snapshot: dict) -> None:
    """기준 상태 전이 하나를 발행하고 유효하지 않은 재생을 중단한다."""
    with _scheduler_lock:
        scheduler = _playback_scheduler
    playback = scheduler.snapshot() if scheduler is not None else {}
    keep_final_dfesta = (
        playback.get("active")
        and playback.get("mode") == PlaybackMode.DFESTA.value
        and (
            snapshot.get("end_reason") == EndReason.COMPLETE.value
            or (
                previous == GameState.RESETTING.value
                and current == GameState.READY.value
            )
        )
    )
    if current != GameState.RUNNING.value and not keep_final_dfesta:
        _cancel_playback()

    # admin 실시간 로그와 저장 로그가 같은 상태 전이만 사용하게 한다.
    _record_event(
        {
            "type": "game_state",
            "previous_state": previous,
            "state": current,
            "end_reason": snapshot.get("end_reason"),
            "revision": snapshot.get("revision", 0),
        }
    )
    reason = snapshot.get("end_reason") or ""
    print(f"[state] {previous} → {current}{f' ({reason})' if reason else ''}")
    _publish_web_event("/game/state", _state_payload(snapshot, previous))


def get_game_runtime() -> GameRuntime:
    global _game_runtime
    with _runtime_lock:
        if _game_runtime is None:
            _game_runtime = GameRuntime(
                _new_game_engine(),
                tick_interval_s=0.05,
                on_state_change=_on_state_change,
            )
            _game_runtime.start()
        return _game_runtime


def get_game_state() -> dict:
    return get_game_runtime().snapshot()


def get_active_melody() -> dict:
    """현재 게임이 사용하는 멜로디 문서의 복사본을 반환한다."""
    return deepcopy(melody_document)


def get_status() -> dict:
    game = get_game_state()
    now = time.time() * 1000
    with _state_lock:
        serial = dict(_serial_status)
        slaves = _slave_list_locked(now)
        return {
            "input_mode": input_mode,
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


def get_cells(game_snapshot: dict | None = None) -> dict:
    """고정된 게임 revision 하나로 UI 셀 payload를 만든다."""

    cells = {}
    source = game_snapshot if game_snapshot is not None else get_game_state()
    for key, data in source["cells"].items():
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


def get_web_snapshot() -> dict:
    """WebSocket 연결마다 보내는 전체 복구 payload를 반환한다."""

    game = get_game_state()
    status = get_status()
    # get_status가 cache를 다시 읽으므로 game과 cells를 같은 revision으로 고정한다.
    status["game"] = game
    status["cell_count"] = game["cell_count"]
    return {
        "schema_version": 3,
        "game": game,
        "melody": get_active_melody(),
        "cells": get_cells(game),
        "status": status,
        "events": get_events(),
    }


def start_websocket_service():
    """프로세스 공용 WebSocket 발행기를 한 번만 시작한다."""

    global _websocket_service
    with _websocket_lock:
        if _websocket_service is not None and _websocket_service.running:
            return _websocket_service

        from websocket_service import WebSocketService

        ws_cfg = config.get("websocket") or {}
        host = str(
            ws_cfg.get(
                "host",
                config.get("mock", {}).get("host", "127.0.0.1"),
            )
        )
        service = WebSocketService(
            get_web_snapshot,
            host=host,
            # 세 브라우저 인터페이스가 같은 실시간 포트를 사용한다.
            port=8765,
        )
        service.start()
        _websocket_service = service
        return service


def stop_websocket_service() -> None:
    """정상 종료 과정에서 브라우저 직접 전송을 중단한다."""

    global _websocket_service
    with _websocket_lock:
        service = _websocket_service
        _websocket_service = None
    if service is not None:
        service.close()


# ── 셀 파싱 ────────────────────────────────────────────
def parse_cell(cell: int):
    """cell 1–8 → Q1 도~도, cell 9–16 → Q2 도~도."""
    if cell <= 8:
        q, row = 1, cell
    else:
        q, row = 2, cell - 8
    note = NOTE_MAP.get(row, "invalid")
    return q, note

# ── 게임 상태 전이 ──────────────────────────────────────
def reload_melody_data() -> None:
    """config와 활성 멜로디 단일 원본을 다시 읽어 게임 입력을 고정한다."""
    global answer_sequence, time_over_s, idle_s, dfesta_presses_required
    global melody_id, melody_path, melody_document, playback_melody, game_rules
    global config, current_stage, input_mode
    with open(SERVER_DIR / "config.json", encoding="utf-8") as f:
        config = json.load(f)
    current_stage = int(config.get("current_stage", 1))
    melody_id = config.get("melody", "biplane")
    raw_mode = config.get("input_mode", "table")
    input_mode = "table" if raw_mode == "mock" else raw_mode
    melody_path = melodies_dir() / f"{melody_id}.json"
    melody_document = load_melody_document(melody_path)
    playback_melody = parse_melody(melody_document)
    answer_sequence = answer_sequence_from_melody(melody_document)
    game_rules = game_rules_from_melody(melody_document)
    time_over_s = game_rules["time_over_s"]
    idle_s = game_rules["idle_s"]
    dfesta_presses_required = game_rules["dfesta_presses_required"]
    print(f"[melody] 재로드 — {melody_id}, 정답 시퀀스: {answer_sequence}")


def _publish_transition(result: RuntimeResult) -> dict:
    """RuntimeResult의 상태 전이를 발행하고 snapshot을 반환한다."""

    get_game_runtime().publish_state_change(result)
    return result.snapshot


def start_game() -> dict:
    runtime = get_game_runtime()
    _cancel_playback()
    reload_melody_data()

    current = runtime.snapshot()
    if current["state"] != GameState.READY.value:
        raise InvalidTransition(
            f"START is invalid from {current['state']}; RESET must complete first"
        )
    if current["cell_count"]:
        raise InvalidTransition("START requires an empty table")

    runtime.replace_engine(_new_game_engine())
    # 브라우저가 판정·재생과 같은 문서를 먼저 적용한 뒤 RUNNING을 받게 한다.
    _publish_web_event("/game/melody", [get_active_melody()])
    started = runtime.start_game()
    _publish_transition(started)

    print(f"[game] 시작 — stage {current_stage}, melody={melody_id}")
    request_listen()
    return {"ok": True, "game": get_game_state()}


def end_game() -> dict:
    runtime = get_game_runtime()
    result = runtime.end_game(EndReason.MANUAL)
    snapshot = _publish_transition(result)

    print(f"[game] 종료 요청 — {snapshot['state']}")
    return {"ok": True, "game": snapshot}


def reset_game() -> dict:
    result = get_game_runtime().reset_game()
    snapshot = _publish_transition(result)
    print(f"[game] 리셋 — {snapshot['state']}")
    return {"ok": True, "game": snapshot}


def request_listen() -> dict:
    """RUNNING에서 Python 기준 목표 멜로디 미리듣기를 시작한다."""
    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    state = snapshot["state"]
    if state != GameState.RUNNING.value:
        print(f"[game] 다시 듣기 무시 — {state}")
        return {"ok": False, "ignored": True, "game": snapshot}

    # Listen은 셀을 바꾸지 않지만 실제 참여자 입력이므로 Idle을 초기화한다.
    activity = runtime.record_activity()
    snapshot = _publish_transition(activity)
    if snapshot["state"] != GameState.RUNNING.value:
        print("[game] 다시 듣기 무시 — 타이머 만료")
        return {"ok": False, "ignored": True, "game": snapshot}

    print("[game] 다시 듣기")
    playback = _start_playback(PlaybackMode.LISTEN)
    return {"ok": True, "playback": playback, "game": snapshot}


def request_play() -> dict:
    """배치된 각 오브를 현재 열과 실제 pitch로 재생한다."""
    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    if snapshot["state"] != GameState.RUNNING.value:
        print("[game] 재생 무시 — RUNNING 아님")
        return {"ok": False, "ignored": True, "game": snapshot}

    # Play도 Listen과 같은 Idle 활동 규칙을 사용한다.
    # 제한 시각과 입력이 같으면 Queue의 엔진 판정이 제한 시각을 우선한다.
    activity = runtime.record_activity()
    snapshot = _publish_transition(activity)
    if snapshot["state"] != GameState.RUNNING.value:
        print("[game] 재생 무시 — 타이머 만료")
        return {"ok": False, "ignored": True, "game": snapshot}

    print("[game] 재생")
    playback = _start_playback(PlaybackMode.PLAY)
    return {"ok": True, "playback": playback, "game": snapshot}


def request_dfesta() -> dict:
    """모든 voice를 재생하고 DFestaPress를 완료 조건에 반영한다."""
    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    if snapshot["state"] != GameState.RUNNING.value:
        print("[game] 디페스타 무시 — RUNNING 아님")
        return {"ok": False, "ignored": True, "game": snapshot}

    # 제한 시각과 버튼 입력이 같으면 제한 시각이 우선하도록 버튼부터 Queue에 넣는다.
    # 마지막 DFesta 재생이 자기 상태 전이에 취소되지 않도록 재생 시작 뒤에 발행한다.
    runtime_result = runtime.press_dfesta()
    if (
        runtime_result.state == GameState.RESETTING.value
        and runtime_result.snapshot.get("end_reason")
        != EndReason.COMPLETE.value
    ):
        snapshot = _publish_transition(runtime_result)
        print("[game] 디페스타 무시 — 타이머 만료")
        return {"ok": False, "ignored": True, "game": snapshot}

    print("[game] 디페스타")
    try:
        playback = _start_playback(PlaybackMode.DFESTA)
    finally:
        snapshot = _publish_transition(runtime_result)
    return {
        "ok": True,
        "counted": bool(runtime_result.value),
        "playback": playback,
        "game": snapshot,
    }

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
            if action == GAME_ACTION_START:
                start_game()
            elif action == GAME_ACTION_LISTEN:
                request_listen()
            elif action == GAME_ACTION_PLAY:
                request_play()
            elif action == GAME_ACTION_DFESTA:
                request_dfesta()
            elif action == GAME_ACTION_END:
                end_game()
            elif action == GAME_ACTION_RESET:
                reset_game()
            else:
                print(f"[WARN] unknown game action: {action}")
        except (InvalidTransition, ValueError, RuntimeError) as exc:
            print(f"[WARN] game action {action} rejected: {exc}")
        return
    if t == "heartbeat":
        # 생존 기록은 위에서 완료했다. 점유 마스크가 있으면 유실 이벤트를 복구한다.
        # (sid 는 위에서 유효 범위 검증·int 변환 완료된 값)
        if sid is not None and "occupancy_mask" in event:
            try:
                reconcile_occupancy_mask(sid, int(event["occupancy_mask"]))
            except (TypeError, ValueError):
                pass  # 잘못된 점유 마스크는 자가복구만 건너뛴다.
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
            _publish_web_event(
                "/orb/added",
                [slave, cell, "unknown", note, result, orb_id],
            )
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
        _publish_web_event(
            "/orb/added",
            [slave, cell, voice, note, result, orb_key],
        )

        # MIDI 모드는 즉시 소리를 내며 screen은 중복 재생을 건너뛴다.
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
    _publish_web_event(
        "/orb/removed",
        [slave, cell, voice, note, orb_key or orb_id],
    )
    snapshot = _publish_transition(runtime_result)
    return {
        "ok": True,
        "changed": True,
        "game": snapshot,
    }

# ── 자가복구 (heartbeat 점유 마스크 기반) ─────────────
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
    _publish_web_event("/orb/recovered", [slave_id, cell, note])
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
    _publish_web_event(
        "/orb/removed",
        [slave_id, cell, voice, note, orb_key],
    )
    _publish_transition(runtime_result)


def reconcile_occupancy_mask(slave_id: int, occupancy_mask: int) -> None:
    """heartbeat 점유 마스크로 메모리 불일치를 안정화한 뒤 자가복구한다."""
    with _state_lock:
        s = _slaves.get(slave_id)
        prev = s.get("prev_occupancy_mask") if s else None
        if s is not None:
            s["prev_occupancy_mask"] = occupancy_mask
        # 직전 heartbeat 와 마스크가 동일해야(=~3초 안정) 반영. 순간 오검출 1회는 무시.
        stable = prev is not None and prev == occupancy_mask
        occupied_mem = {
            int(k.split(":", 1)[1])
            for k in get_cells()
            if k.startswith(f"{slave_id}:")
        }

    if not stable:
        return

    for cell in range(1, 17):
        mask_occ = bool((occupancy_mask >> (cell - 1)) & 1)
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
    start_websocket_service()
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
        get_melody=get_active_melody,
        get_cells=get_cells,
        get_events=get_events,
        on_game_start=start_game,
        on_game_end=end_game,
        on_game_reset=reset_game,
        on_game_listen=request_listen,
        on_game_play=request_play,
        on_game_dfesta=request_dfesta,
    )

    if input_mode == "serial":
        threading.Thread(target=serial_loop, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("종료")
    finally:
        stop_websocket_service()
        runtime.close()
        with _scheduler_lock:
            scheduler = _playback_scheduler
        if scheduler is not None:
            scheduler.close()


if __name__ == "__main__":
    run_main()
