import threading
import time
from copy import deepcopy

from app.app_paths import ensure_data_files, melodies_dir
from app.data_store import find_orb, load_config, load_mapping
from app.device_manager import DeviceManager
from app.game_engine import (
    EndReason,
    GameEngine,
    GameState,
    InvalidTransition,
    PlacementResult,
)
from app.game_runtime import GameRuntime, RuntimeResult
from app.melody_manager import (
    MelodyManager,
    answer_sequence_from_melody,
    game_rules_from_melody,
)
from app.matrix_display import MatrixDisplay
from app.scheduler import PlaybackMode, PlaybackScheduler, parse_melody

ensure_data_files()

# ── 설정 로드 ──────────────────────────────────────────
config = load_config()
orb_mapping = load_mapping()

_melody_manager = MelodyManager(melodies_dir())

current_stage   = int(config.get("current_stage", 1))
melody_id       = _melody_manager.preview(config)
melody_path     = melodies_dir() / f"{melody_id}.json"
melody_document = _melody_manager.load(melody_id)
playback_melody = parse_melody(melody_document)
answer_sequence = answer_sequence_from_melody(melody_document)
game_rules      = game_rules_from_melody(melody_document)
time_over_s     = game_rules["time_over_s"]
idle_s          = game_rules["idle_s"]
dfesta_presses_required = game_rules["dfesta_presses_required"]
input_mode = str(config.get("input_mode", "table")).lower().strip()
if input_mode not in ("table", "serial"):
    raise ValueError("input_mode must be table or serial")

NOTE_MAP = {1: "C", 2: "D", 3: "E", 4: "F", 5: "G", 6: "A", 7: "B", 8: "C"}

# Serial JSON과 admin HTTP 제어가 함께 사용하는 이벤트 문자열이다.
GAME_ACTION_START = "start"
GAME_ACTION_END = "end"
GAME_ACTION_RESET = "reset"
GAME_ACTION_LISTEN = "listen"
GAME_ACTION_PLAY = "play"
GAME_ACTION_DFESTA = "dfesta"

# ── Admin 상태 (셀 점유, 이벤트 로그) ───────────────
_state_lock = threading.Lock()
_event_log: list[dict] = []
EVENT_LOG_LIMIT = 100

# ── USB 장치와 Dot Matrix 표시 상태 ──────────────────
_device_lock = threading.RLock()
_device_manager: DeviceManager | None = None
_matrix_display: MatrixDisplay | None = None

# ── 게임 런타임 (Serial/HTTP 입력의 단일 writer) ───────
_runtime_lock = threading.Lock()
_game_runtime: GameRuntime | None = None

# ── 재생 Scheduler (Listen·Play·DFesta의 단일 clock) ────
# GameRuntime은 게임 상태를, PlaybackScheduler는 박자와 MIDI 출력을 소유한다.
_scheduler_lock = threading.Lock()
_playback_scheduler: PlaybackScheduler | None = None

# ── 직접 WebSocket (브라우저 실시간 이벤트 + 재연결 snapshot) ──
# screen과 admin 내부 모니터 화면은 모두 이 서비스에서 직접 상태를 받는다.
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


def get_device_manager() -> DeviceManager:
    """Master·Dot Matrix 자동 식별 관리자를 한 번만 생성한다."""
    global _device_manager, _matrix_display
    with _device_lock:
        if _device_manager is None:
            hardware_cfg = config.get("hardware") or {}
            if not hardware_cfg:
                # 기존 설정에서는 고정 COM을 버리고 baudrate만 이어받는다.
                legacy_serial = config.get("serial") or {}
                hardware_cfg = {
                    "baudrate": legacy_serial.get("baudrate", 115200)
                }
            _device_manager = DeviceManager(
                hardware_cfg,
                _route_master_message,
            )
            matrix_cfg = config.get("dotmatrix") or {}
            _matrix_display = MatrixDisplay(
                _device_manager,
                brightness=int(matrix_cfg.get("brightness", 64)),
            )
            _device_manager.set_connected_callback(
                _matrix_display.handle_device_connected
            )
        return _device_manager


def get_matrix_display() -> MatrixDisplay:
    get_device_manager()
    assert _matrix_display is not None
    return _matrix_display


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


def _scheduler_send_notes(notes: list[dict]) -> list[dict]:
    """도달한 Scheduler beat 하나를 MIDI 묶음으로 보낸다."""
    if not notes:
        return []

    from app.midi_out import trigger_many

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
        _publish_web_event(
            "/game/playback/beat",
            [
                run_id,
                mode,
                int(event["step"]),
                int(event["duration_ms"]),
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
    _sync_matrix_display(snapshot)


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
    hardware = get_device_manager().status()
    matrix_display = get_matrix_display().status()
    with _state_lock:
        slaves = _slave_list_locked(now)
        return {
            "input_mode": input_mode,
            "game": game,
            "current_stage": current_stage,
            "melody": melody_id,
            "melody_rotation": _melody_manager.snapshot(),
            "playback": get_playback_scheduler().snapshot(),
            "orb_count": len(orb_mapping),
            "hardware": hardware,
            "matrix_display": matrix_display,
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
        "schema_version": 4,
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

        from app.websocket_service import WebSocketService

        ws_cfg = config.get("websocket") or {}
        host = str(
            ws_cfg.get(
                "host",
                config.get("web", {}).get("host", "127.0.0.1"),
            )
        )
        service = WebSocketService(
            get_web_snapshot,
            host=host,
            # 관객 화면과 Admin 내부 모니터가 같은 실시간 포트를 사용한다.
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
    """config를 다시 읽고 다음 게임용 멜로디와 규칙을 고정한다."""
    global answer_sequence, time_over_s, idle_s, dfesta_presses_required
    global melody_id, melody_path, melody_document, playback_melody, game_rules
    global config, current_stage, input_mode
    config = load_config()
    current_stage = int(config.get("current_stage", 1))
    input_mode = str(config.get("input_mode", "table")).lower().strip()
    if input_mode not in ("table", "serial"):
        raise ValueError("input_mode must be table or serial")
    selected_id = _melody_manager.reserve(config)
    selected_path = melodies_dir() / f"{selected_id}.json"
    selected_document = _melody_manager.load(selected_id)
    selected_playback = parse_melody(selected_document)
    selected_answers = answer_sequence_from_melody(selected_document)
    selected_rules = game_rules_from_melody(selected_document)

    melody_id = selected_id
    melody_path = selected_path
    melody_document = selected_document
    playback_melody = selected_playback
    answer_sequence = selected_answers
    game_rules = selected_rules
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

    current = runtime.snapshot()
    if current["state"] != GameState.READY.value:
        raise InvalidTransition(
            f"START is invalid from {current['state']}; RESET must complete first"
        )
    if current["cell_count"]:
        raise InvalidTransition("START requires an empty table")

    try:
        reload_melody_data()
        runtime.replace_engine(_new_game_engine())
        # 브라우저가 판정·재생과 같은 문서를 먼저 적용한 뒤 RUNNING을 받게 한다.
        _publish_web_event("/game/melody", [get_active_melody()])
        started = runtime.start_game()
    except Exception:
        _melody_manager.cancel()
        raise
    _melody_manager.commit()
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


def _remove_all_cells_for_admin() -> tuple[int, dict]:
    """현재 셀을 Runtime 경로로 제거하고 모든 웹 화면에 같은 순서로 알린다."""

    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    occupied = sorted(
        snapshot["cells"].values(),
        key=lambda entry: (int(entry["position"]), int(entry["cell"])),
    )
    changed = 0
    latest = snapshot

    for entry in occupied:
        slave = int(entry["slave"])
        cell = int(entry["cell"])
        orb_id = str(entry["orb_id"])
        voice = str(entry.get("voice") or "unknown")
        note = str(entry.get("note") or parse_cell(cell)[1])
        result = runtime.remove_orb(
            slave=slave,
            cell=cell,
            orb_id=orb_id,
        )
        if not result.value:
            continue

        changed += 1
        _record_event(
            {
                "type": "removed",
                "slave": slave,
                "cell": cell,
                "id": orb_id,
                "voice": voice,
                "note": note,
                "admin": True,
            }
        )
        _publish_web_event(
            "/orb/removed",
            [slave, cell, voice, note, orb_id],
        )
        latest = _publish_transition(result)

    return changed, latest


def collect_all_orbs() -> dict:
    """관리자 요청으로 현재 점유 셀 전체를 논리적으로 회수한다."""

    changed, snapshot = _remove_all_cells_for_admin()
    print(f"[admin] 모든 오브 회수 — {changed}개")
    return {"ok": True, "changed": changed, "game": snapshot}


def auto_complete_game() -> dict:
    """현재 멜로디의 정답 pitch에 등록 오브를 자동 배치한다."""

    global orb_mapping

    runtime = get_game_runtime()
    current = runtime.snapshot()
    if current["state"] != GameState.RUNNING.value:
        raise InvalidTransition("auto-complete requires RUNNING")

    orb_mapping = load_mapping()
    available_orbs = [
        (orb_id, str(entry.get("voice") or "").strip())
        for orb_id, entry in sorted(orb_mapping.items())
        if isinstance(entry, dict) and str(entry.get("voice") or "").strip()
    ]
    pitched_steps = [
        step for step in playback_melody.steps if step.pitch is not None
    ]
    if not pitched_steps:
        raise ValueError("active melody has no pitch steps")
    if any(step.index >= 12 for step in pitched_steps):
        raise ValueError("auto-complete supports up to 12 melody steps")
    if len(available_orbs) < len(pitched_steps):
        raise RuntimeError("not enough registered orbs for auto-complete")

    plan = []
    for step, (orb_id, voice) in zip(pitched_steps, available_orbs):
        position = step.index + 1
        slave = (position - 1) // 2 + 1
        q = (position - 1) % 2 + 1
        row = int(step.pitch) + 1
        cell = row + (q - 1) * 8
        plan.append((orb_id, voice, slave, cell))

    removed, _snapshot = _remove_all_cells_for_admin()
    changed = 0
    latest = runtime.snapshot()
    for orb_id, voice, slave, cell in plan:
        result = runtime.add_orb(
            orb_id=orb_id,
            slave=slave,
            cell=cell,
            voice=voice,
            known=True,
        )
        placement = result.value.value
        note = parse_cell(cell)[1]
        _record_event(
            {
                "type": "added",
                "slave": slave,
                "cell": cell,
                "id": orb_id,
                "voice": voice,
                "note": note,
                "result": placement,
                "admin": True,
            }
        )
        _publish_web_event(
            "/orb/added",
            [slave, cell, voice, note, placement, orb_id],
        )
        latest = _publish_transition(result)
        changed += 1

    if not latest.get("is_correct"):
        raise RuntimeError("auto-complete did not produce a correct solution")

    print(f"[admin] 자동완성 — 회수 {removed}개, 배치 {changed}개")
    return {
        "ok": True,
        "changed": changed,
        "removed": removed,
        "game": latest,
    }


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

def handle_master_message(event: dict) -> None:
    """Master가 전달한 orb 이벤트 또는 game 버튼을 처리한다."""
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

        # 실제 음향 출력은 Python MIDI 경로 하나만 사용한다.
        if runtime_result.value is not PlacementResult.INACTIVE:
            try:
                from app.midi_out import cell_to_pitch, trigger as midi_trigger

                midi_result = midi_trigger(
                    voice,
                    cell_to_pitch(int(cell)),
                    duration_ms=320,
                )
                if not midi_result.get("ok"):
                    print(f"[midi] trigger failed: {midi_result}")
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

# ── USB 장치와 Dot Matrix ─────────────────────────────
def _route_master_message(event: dict) -> None:
    """Table 모드에서도 장치는 감시하되 실제 입력은 serial 모드에서만 받는다."""
    if input_mode == "serial":
        handle_master_message(event)


def _sync_matrix_display(snapshot: dict | None = None) -> dict:
    """현재 게임 상태를 Dot Matrix의 최신 표시 상태로 반영한다."""
    try:
        return get_matrix_display().sync_game(
            snapshot or get_game_state(),
            stage=current_stage,
            melody=melody_id,
        )
    except Exception as exc:
        print(f"[dotmatrix] 표시 갱신 실패: {exc}")
        return {"ok": False, "error": str(exc)}


def start_devices() -> threading.Thread | None:
    """input_mode와 무관하게 Master·Dot Matrix 자동 검색을 시작한다."""
    thread = get_device_manager().start()
    _sync_matrix_display()
    return thread


def stop_devices() -> None:
    get_device_manager().stop()


def rescan_devices() -> dict:
    """Admin 요청으로 누락된 COM의 handshake 검색을 즉시 실행한다."""
    return {
        "ok": True,
        "hardware": get_device_manager().request_rescan(),
        "matrix_display": get_matrix_display().status(),
    }

# ── 진입점 ─────────────────────────────────────────────
def run_main() -> None:
    runtime = get_game_runtime()
    start_websocket_service()
    print(f"[stage {current_stage}] 정답 시퀀스: {answer_sequence}")
    print(f"[melody] {melody_id}")
    print(f"[melody rotation] {_melody_manager.snapshot()}")
    print(f"[input] {input_mode} mode")
    print("[audio] midi")
    print("admin에서 start를 누르면 게임이 시작됩니다.")
    web_config = config.get("web") or {"host": "127.0.0.1", "port": 8080}
    print(f"[http] http://127.0.0.1:{web_config.get('port', 8080)}/screen")
    print(f"[http] http://127.0.0.1:{web_config.get('port', 8080)}/admin")

    try:
        from app.midi_out import ensure_init

        ok, err = ensure_init()
        if ok:
            print("[midi] ready (loopMIDI → Ableton)")
        else:
            print(f"[midi] WARN: {err}")
    except Exception as exc:
        print(f"[midi] WARN: init failed: {exc}")

    from app.web_server import start_http_server

    start_http_server(
        web_config,
        handle_event,
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
        on_admin_collect_orbs=collect_all_orbs,
        on_admin_auto_complete=auto_complete_game,
        on_admin_rescan_devices=rescan_devices,
    )

    start_devices()

    try:
        while True:
            _sync_matrix_display()
            time.sleep(1)
    except KeyboardInterrupt:
        print("종료")
    finally:
        stop_devices()
        stop_websocket_service()
        runtime.close()
        with _scheduler_lock:
            scheduler = _playback_scheduler
        if scheduler is not None:
            scheduler.close()
        from app.midi_out import shutdown

        shutdown()


if __name__ == "__main__":
    run_main()
