from server.text_log import install_logging, write_log

if __name__ == "__main__":
    install_logging()

import json
import threading
import time
import math
from datetime import datetime
from copy import deepcopy

from server.app_paths import ensure_data_files, melodies_dir
from server.data_store import find_orb, load_config, load_mapping, save_config
from server.tray_input import hall_sensor_count, tray_state
from server.simulation_orbs import simulation_orb_mapping, virtual_orb_mapping
from server.game.game_engine import (
    EndReason,
    GameEngine,
    GameState,
    InvalidTransition,
    PlacementResult,
)
from server.game.game_runtime import GameRuntime, RuntimeResult
from server.inout.matrix_display import MatrixDisplay
from server.inout.game_events import GameEvents
from server.game.melody_manager import (
    MelodyManager,
    answer_sequence_from_melody,
    game_rules_from_melody,
    game_timers_from_config,
)
from server.game.scheduler import PlaybackMode, PlaybackScheduler, parse_melody, build_plan
from server.inout import hardware_io
from server.inout.hint_led import HintLedBridge
from server.orb_inspection import OrbInspection

_orb_inspection = OrbInspection()


ensure_data_files()

# ── 설정 로드 ──────────────────────────────────────────
config = load_config()
hall_sensor_count(config)  # Reject invalid hardware policy at startup.
_hint_led = HintLedBridge(lambda command: hardware_io.send_line(command), fade_ms=config.get("hint_led_fade_ms", 600), tray_easing=config.get("tray_led_easing", "none"))
orb_mapping = load_mapping()

_melody_manager = MelodyManager(melodies_dir())

current_stage   = int(config.get("current_stage", 1))
melody_id       = _melody_manager.preview(config)
melody_path     = melodies_dir() / f"{melody_id}.json"
melody_document = _melody_manager.load(melody_id)
playback_melody = parse_melody(melody_document)
answer_sequence = answer_sequence_from_melody(melody_document)
_startup_timers = game_timers_from_config(config)
_debug_mode = False
_settings_lock = threading.Lock()
game_rules      = {**game_rules_from_melody(melody_document), **_startup_timers}
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

# ── Admin 상태 (셀 점유, 이벤트 로그, 시리얼) ────────
_state_lock = threading.Lock()
_event_log: list[dict] = []
EVENT_LOG_LIMIT = 100
_serial_status = {"connected": False, "port": "", "error": ""}
_hardware_switching = threading.Event()
_matrix_display: MatrixDisplay | None = None
_game_events: GameEvents | None = None
_tray_lock = threading.RLock()
_tray_hardware = input_mode == "serial"

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
# slave.ino 가 2초(HEARTBEAT_MS)마다 heartbeat 를 보낸다. 이 시간(주기의 4배)
# 안에 heartbeat 가 없으면 offline 으로 본다. 몇 개 놓쳐도 깜빡이지 않도록 여유.
SLAVE_ONLINE_TIMEOUT_S = 8.0
_slaves: dict[int, dict] = {}

# 유효 slave ID 범위. 이 밖(예: 충돌로 깨진 slave:0)의 메시지는 폐기해
# admin 에 '유령 슬레이브'가 잡히지 않게 한다. 보드가 늘면 MAX 만 올리면 됨.
SLAVE_ID_MIN = 1
SLAVE_ID_MAX = 99

# slave가 300ms 연속 미검출을 확인한 removed는 서버에서 즉시 반영한다.

# 슬레이브가 유실 대비로 added·removed 를 여러 번 보낸다(slave.ino FRAME_REPEAT).
# 같은 칸에 같은 오브가 이 시간 안에 다시 들어오면 재전송으로 보고 버린다.
# 엔진은 중복 added 를 상태 변화로 치지 않지만 MIDI 는 매번 울리므로 여기서 막는다.
DUPLICATE_ADDED_IGNORE_MS = 1000.0
_last_added: dict[str, tuple[str, float]] = {}


def _cell_key(slave: int, cell: int) -> str:
    return f"{slave}:{cell}"


def _record_event(entry: dict) -> None:
    entry["ts"] = time.time() * 1000
    write_log(json.dumps(entry, ensure_ascii=False, default=str), "EVENT")
    with _state_lock:
        _event_log.append(entry)
        if len(_event_log) > EVENT_LOG_LIMIT:
            del _event_log[: len(_event_log) - EVENT_LOG_LIMIT]


def _is_duplicate_added(slave: int, cell: int, orb_id: str) -> bool:
    """슬레이브의 반복 전송이면 True. 기록은 매번 갱신한다."""
    key = _cell_key(slave, cell)
    ident = str(orb_id or "").strip().upper()
    now = time.time() * 1000
    with _state_lock:
        previous = _last_added.get(key)
        _last_added[key] = (ident, now)
    if previous is None:
        return False
    last_ident, last_at = previous
    return last_ident == ident and (now - last_at) < DUPLICATE_ADDED_IGNORE_MS


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
    with _orb_inspection.lock:
        return _send_game_notes(notes)


def _send_game_notes(notes: list[dict]) -> list[dict]:
    """도달한 Scheduler beat 하나를 MIDI 묶음으로 보낸다."""
    if _orb_inspection.active or not notes:
        return []

    from server.inout.midi_out import trigger_many

    results = trigger_many(notes)
    failures = [result for result in results if not result.get("ok")]
    if failures:
        print(f"[scheduler] MIDI WARN: {failures}")
    return results


def _scheduler_publish_event(event: dict) -> None:
    """Scheduler callback을 브라우저 WebSocket 이벤트로 변환한다."""
    if not _orb_inspection.active:
        _hint_led.handle(event)
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


def _start_playback(mode: PlaybackMode, *, hint_led: bool = False) -> dict:
    """게임 시작 때 고정한 멜로디와 현재 셀로 재생 plan을 만든다."""
    cells = get_game_state().get("cells") or {}
    return get_playback_scheduler().start(mode, playback_melody, cells, hint_led=hint_led)


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
    raw_delay = (config.get("game_rules") or {}).get("initial_hint_delay_s", 0)
    if isinstance(raw_delay, bool):
        raise ValueError("game_rules.initial_hint_delay_s must be a number")
    delay = float(raw_delay)
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("game_rules.initial_hint_delay_s must be finite and non-negative")
    return GameEngine(
        answer_sequence,
        time_over_s=time_over_s,
        idle_s=idle_s,
        dfesta_presses_required=999 if _debug_mode else dfesta_presses_required,
        initial_timer_delay_s=delay + build_plan(PlaybackMode.LISTEN, playback_melody).duration_s + 1.0,
        initial_hint_delay_s=delay,
        tray_present=None if _tray_hardware else False,
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
    # 타이머 만료처럼 tick 이 만든 전이도 표시등에 반영한다.
    _sync_dfesta_led(snapshot)
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
    _hint_led.tray_transition(previous, current, snapshot)

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
                on_snapshot=_on_game_snapshot,
            )
            _game_runtime.start()
        return _game_runtime


def _on_game_snapshot(previous: dict, current: dict, action: str) -> None:
    _hint_led.observe_snapshot(previous, current, action)
    if _game_events is not None:
        _game_events.observe(previous, current, action)


def _start_initial_hint_if_due() -> None:
    # Share the input guard so reset/start cannot interleave with a claimed hint.
    with _orb_inspection.lock:
        if _orb_inspection.active:
            return
        runtime = get_game_runtime()
        initial = runtime.snapshot().get("initial_hint") or {}
        if not initial.get("pending") or initial.get("remaining_s", 0) > 0:
            return
        result = runtime.claim_initial_hint()
        if result.value:
            try:
                request_listen(initial_hint=True)
            except InvalidTransition:
                pass  # A concurrent manual end/reset can win after the claim.


def get_game_state() -> dict:
    return get_game_runtime().snapshot()


def get_active_melody() -> dict:
    """현재 게임이 사용하는 멜로디 문서의 복사본을 반환한다."""
    return deepcopy(melody_document)


def get_status() -> dict:
    game = get_game_state()
    now = time.time() * 1000
    hardware = hardware_io._snapshot()
    connections = hardware["connections"]
    matrix = _matrix_display.connection_status() if _matrix_display is not None else None
    with _state_lock:
        serial = {"connected": any(c["open"] for c in connections),
                  "port": ", ".join(c["port"] for c in connections),
                  "error": "; ".join(c["error"] for c in connections if c["error"])}
        slaves = _slave_list_locked(now)
        observed = {s["slave"]: s for s in hardware["slaves"]}
        for slave in slaves:
            if slave["slave"] in observed:
                slave["online"] = observed[slave["slave"]]["online"]
        return {
            "debug_mode": _debug_mode,
            "input_mode": input_mode,
            "tray_simulation": not _tray_hardware,
            "game": game,
            "current_stage": current_stage,
            "melody": melody_id,
            "melody_rotation": _melody_manager.snapshot(),
            "playback": get_playback_scheduler().snapshot(),
            "orb_count": len(orb_mapping),
            "serial": serial,
            "hardware": hardware,
            "matrix": matrix,
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

        from server.web.websocket_service import WebSocketService

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
def reload_melody_data(melody_override: str | None = None) -> None:
    """config를 다시 읽고 다음 게임용 멜로디와 규칙을 고정한다."""
    global answer_sequence, time_over_s, idle_s, dfesta_presses_required
    global melody_id, melody_path, melody_document, playback_melody, game_rules
    global config, current_stage, input_mode
    config = load_config()
    current_stage = int(config.get("current_stage", 1))
    input_mode = str(config.get("input_mode", "table")).lower().strip()
    if input_mode not in ("table", "serial"):
        raise ValueError("input_mode must be table or serial")
    selected_id = melody_override or _melody_manager.reserve(config)
    selected_path = melodies_dir() / f"{selected_id}.json"
    selected_document = _melody_manager.load(selected_id)
    selected_playback = parse_melody(selected_document)
    selected_answers = answer_sequence_from_melody(selected_document)
    selected_rules = {**game_rules_from_melody(selected_document), **_startup_timers}

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


# 마스터 GPIO39 디페스타 표시등. 평소 LOW, 디페스타 버튼이 유효한 동안만 HIGH.
_serial_writer = hardware_io
_dfesta_led_lock = threading.Lock()
_dfesta_led_state: bool | None = None   # None = 미전송(연결 직후 강제 재전송)
_led_normal_restored = False            # 이번 실행에서 일반 모드를 되돌렸는지


def _sync_dfesta_led(snapshot: dict) -> None:
    """디페스타 버튼이 유효한 동안에만 표시등을 켠다.

    조건은 snapshot의 `waiting_for_dfesta`(RUNNING이면서 정답 상태)이다.
    버튼을 눌러 완료되면 RESETTING으로 넘어가 조건이 풀리므로 자동으로 꺼진다.
    값이 바뀔 때만 보내고, 전송이 실패하면 기록을 남기지 않아 다음 기회에 재시도한다.
    """

    global _dfesta_led_state
    completion = snapshot.get("completion") or {}
    desired = bool(completion.get("waiting_for_dfesta"))
    with _dfesta_led_lock:
        if desired == _dfesta_led_state:
            return
        if _serial_writer.send_line("D1" if desired else "D0"):
            _dfesta_led_state = desired


def _publish_transition(result: RuntimeResult) -> dict:
    """RuntimeResult의 상태 전이를 발행하고 snapshot을 반환한다."""

    get_game_runtime().publish_state_change(result)
    _sync_dfesta_led(result.snapshot)
    return result.snapshot


def get_software_settings() -> dict:
    with _settings_lock:
        saved = game_timers_from_config(load_config())
        available = _melody_manager.available_ids()
        melody_titles = {
            song_id: _melody_manager.load(song_id).get("title") or song_id
            for song_id in available
        }
        return {"debug_mode": _debug_mode, "saved_timers": saved,
                "available_melodies": available,
                "melody_titles": melody_titles,
                "current_melody": melody_id,
                "active_timers": dict(_startup_timers),
                "restart_required": saved != _startup_timers}


def update_software_settings(data: dict) -> dict:
    global _debug_mode
    if not isinstance(data, dict):
        raise ValueError("settings must be an object")
    if "debug_mode" in data and not isinstance(data["debug_mode"], bool):
        raise ValueError("debug_mode must be a boolean")
    with _settings_lock:
        selected = data.get("debug_melody")
        if "debug_melody" in data:
            if _hardware_switching.is_set():
                raise RuntimeError("하드웨어 연결을 변경 중입니다")
            if not data.get("debug_mode", _debug_mode):
                raise ValueError("멜로디 변경은 디버그 모드에서만 가능합니다")
            if not isinstance(selected, str) or selected not in _melody_manager.available_ids():
                raise ValueError("멜로디 파일을 찾을 수 없습니다")
            # Validate before interrupting the current game.
            candidate = _melody_manager.load(selected)
            parse_melody(candidate)
            answer_sequence_from_melody(candidate)
            game_rules_from_melody(candidate)
        if "game_rules" in data:
            rules = data["game_rules"]
            if not isinstance(rules, dict) or any(
                rules.get(key) is None for key in ("time_over_s", "idle_s")
            ):
                raise ValueError("게임 시간과 idle 시간을 모두 입력하세요")
            timers = game_timers_from_config({"game_rules": rules})
            document = load_config()
            document.setdefault("game_rules", {}).update(timers)
            save_config(document)
        if "debug_mode" in data:
            _debug_mode = data["debug_mode"]
            _publish_transition(get_game_runtime().set_dfesta_requirement(
                999 if _debug_mode else dfesta_presses_required))
        if selected is not None:
            _cancel_playback()
            reset_game()
            start_game(melody_override=selected)
    return {"ok": True, **get_software_settings()}


@_orb_inspection.guard
def set_tray(data: dict, *, hardware: bool = False) -> dict:
    """Physical reports and the admin toggle share one ordered tray path."""
    present, seated = tray_state(data, hardware=hardware, config=config)
    if _hardware_switching.is_set():
        raise RuntimeError("하드웨어 연결을 변경 중입니다")
    with _tray_lock:
        if hardware != _tray_hardware:
            raise InvalidTransition("실제 Master 연결 중에는 tray 시뮬레이션을 사용할 수 없습니다")
        runtime = get_game_runtime()
        before = runtime.snapshot().get("tray")
        result = runtime.set_tray(present, seated, ttl_s=3.0 if hardware else None)
        snapshot = _publish_transition(result)
        if before != snapshot["tray"]:
            _publish_web_event("/game/tray", [snapshot["tray"]])
        # Heartbeats refresh the lease only. They never retrigger a game, nor
        # does the first report after boot/reconnect synthesize a placement.
        if result.value and snapshot["state"] == "READY" and snapshot["cell_count"] == 0:
            return start_game()
        return {"ok": True, "game": snapshot}


def _set_tray_source(hardware: bool) -> None:
    global _tray_hardware
    with _tray_lock:
        _tray_hardware = hardware
        state = None if hardware else False
        _publish_transition(get_game_runtime().set_tray(state, state))


@_orb_inspection.guard
def start_game(melody_override: str | None = None) -> dict:
    if _hardware_switching.is_set():
        raise RuntimeError("하드웨어 연결을 변경 중입니다")
    runtime = get_game_runtime()
    _cancel_playback()

    debug_mode = _debug_mode
    current = runtime.snapshot()
    if current["state"] not in (("READY", "RESETTING") if debug_mode else ("READY",)):
        raise InvalidTransition(
            f"START is invalid from {current['state']}; RESET must complete first"
        )
    if current["cell_count"] and not debug_mode:
        raise InvalidTransition("START requires an empty table")

    try:
        if melody_override is None:
            reload_melody_data()
        else:
            reload_melody_data(melody_override)
        runtime.replace_engine(_new_game_engine(), allow_occupied=debug_mode)
        if _matrix_display is not None:
            _matrix_display.clear_voice()
        # 브라우저가 판정·재생과 같은 문서를 먼저 적용한 뒤 RUNNING을 받게 한다.
        _publish_web_event("/game/melody", [get_active_melody()])
        started = runtime.start_game(allow_occupied=debug_mode)
    except Exception:
        _melody_manager.cancel()
        raise
    _melody_manager.commit()
    _publish_transition(started)

    print(f"[game] 시작 — stage {current_stage}, melody={melody_id}")
    _start_initial_hint_if_due()
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


@_orb_inspection.guard
def auto_complete_game() -> dict:
    """현재 멜로디의 정답 pitch에 오브를 배치하며 부족분은 가상 오브로 채운다."""

    global orb_mapping

    runtime = get_game_runtime()
    current = runtime.snapshot()
    if current["state"] != GameState.RUNNING.value:
        raise InvalidTransition("auto-complete requires RUNNING")

    orb_mapping = load_mapping()
    available_orbs = [
        (orb_id, entry["voice"])
        for orb_id, entry in simulation_orb_mapping(orb_mapping).items()
    ]
    pitched_steps = [
        step for step in playback_melody.steps if step.pitch is not None
    ]
    if not pitched_steps:
        raise ValueError("active melody has no pitch steps")
    if any(step.index >= 12 for step in pitched_steps):
        raise ValueError("auto-complete supports up to 12 melody steps")
    if len(available_orbs) < len(pitched_steps):
        raise RuntimeError("자동완성에 사용할 오브가 부족합니다. 음색 카탈로그를 확인하세요.")

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


@_orb_inspection.guard
def request_listen(*, initial_hint: bool = False) -> dict:
    """RUNNING에서 Python 기준 목표 멜로디 미리듣기를 시작한다."""
    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    state = snapshot["state"]
    if (snapshot.get("initial_hint") or {}).get("pending"):
        return {"ok": False, "ignored": True, "reason": "loading", "game": snapshot}
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
    # Both the automatic first hint and manual replay illuminate answer rings.
    playback = _start_playback(PlaybackMode.LISTEN, hint_led=True)
    return {"ok": True, "playback": playback, "game": snapshot}


@_orb_inspection.guard
def request_play() -> dict:
    """배치된 각 오브를 현재 열과 실제 pitch로 재생한다."""
    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    if (snapshot.get("initial_hint") or {}).get("pending"):
        return {"ok": False, "ignored": True, "reason": "loading", "game": snapshot}
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


@_orb_inspection.guard
def request_dfesta() -> dict:
    """모든 voice를 재생하고 DFestaPress를 완료 조건에 반영한다."""
    runtime = get_game_runtime()
    snapshot = runtime.snapshot()
    if (snapshot.get("initial_hint") or {}).get("pending"):
        return {"ok": False, "ignored": True, "reason": "loading", "game": snapshot}
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

    # press_dfesta 는 정답이 완성된 경우에만 True 를 준다. 버튼 입력 자체는
    # 위에서 이미 엔진에 전달돼 Idle 타이머와 연속 입력 횟수에 반영됐으므로,
    # 여기서는 재생만 막는다. (admin UI 의 isDFestaActive 와 같은 조건)
    counted = bool(runtime_result.value)
    if not counted:
        snapshot = _publish_transition(runtime_result)
        print("[game] 디페스타 재생 무시 — 정답 오브가 다 올라오지 않음")
        return {
            "ok": False,
            "ignored": True,
            "counted": False,
            "game": snapshot,
        }

    print("[game] 디페스타")
    try:
        if _game_events is not None:
            _game_events.flush()  # Deliver the gate-off before DFESTA playback.
        playback = _start_playback(PlaybackMode.DFESTA)
    finally:
        snapshot = _publish_transition(runtime_result)
    return {
        "ok": True,
        "counted": counted,
        "playback": playback,
        "game": snapshot,
    }

LED_EFFECT_MIN = 0
LED_EFFECT_MAX = 9
LED_EFFECT_NORMAL = 0    # 일반 모드 — 오브를 놓으면 슬레이브가 링을 켠다
LED_EFFECT_ALL_OFF = 2   # 전체 소등 — 이펙트 모드라 오브를 놓아도 켜지지 않는다


@_orb_inspection.guard
def send_led_effect(effect) -> dict:
    """마스터에 'C<n>' 을 보내 슬레이브 링 LED 이펙트를 브로드캐스트한다.

    마스터가 RS485 로 5회 반복 전송하므로 서버는 한 번만 보낸다.
    """

    try:
        value = int(effect)
    except (TypeError, ValueError) as exc:
        raise ValueError("effect must be an integer") from exc
    if not (LED_EFFECT_MIN <= value <= LED_EFFECT_MAX):
        raise ValueError(
            f"effect must be {LED_EFFECT_MIN}-{LED_EFFECT_MAX}, got {value}"
        )

    sent_at = datetime.now().isoformat(sep=" ", timespec="milliseconds")
    if not _serial_writer.send_line(f"C{value}"):
        raise RuntimeError("일부 또는 전체 포트 전송 실패 — 하드웨어 연결 상태를 확인하세요")
    route = "master" if hardware_io._snapshot()["mode"] == "master" else "slave USB"
    print(f"[led] [{route}] [송신] {sent_at} 이펙트={value} (PC 전송 요청 시각)")
    return {"ok": True, "effect": value}


def blackout_leds() -> None:
    """서버가 내려갈 때 슬레이브 링과 디페스타 표시등을 모두 끈다.

    테이블에 오브가 남아 있어도 불이 켜진 채로 방치되지 않게 한다.
    C2로 먼저 소등한 뒤 stop_serial_input에서 서버 연결 신호를 중단하고
    L0/U0를 보낸다. 비정상 종료 시에는 보드의 5초 연결 만료로 소등한다.
    """

    if not _serial_writer.send_line(f"C{LED_EFFECT_ALL_OFF}"):
        print("[led] 소등 생략 — 마스터 미연결")
        return
    _serial_writer.send_line("D0")
    print("[led] 전체 소등")


def handle_serial_message(event: dict) -> None:
    with _orb_inspection.lock:
        _handle_serial_message(event)


def _handle_serial_message(event: dict) -> None:
    """USB/serial 한 줄 JSON — orb 이벤트 또는 game 버튼."""
    if _orb_inspection.active:
        _orb_inspection.ingest(event)
        return
    t = event.get("type")
    if t == "tray":
        set_tray(event, hardware=True)
        return

    if t == "led_ack":
        received_at = datetime.now().isoformat(sep=" ", timespec="milliseconds")
        sid, effect = event.get("slave"), event.get("effect")
        rx, applied = event.get("received_ms"), event.get("applied_ms")
        if (type(sid) is int and SLAVE_ID_MIN <= sid <= SLAVE_ID_MAX and
                type(effect) is int and 0 <= effect <= 9 and
                all(type(v) is int and 0 <= v <= 0xffffffff for v in (rx, applied))):
            duration = (applied - rx) & 0xffffffff
            waiting = event.get("ack_wait_ms")
            wait_text = (f"응답송신대기={waiting}ms " if type(waiting) is int and waiting >= 0
                         else "응답송신대기=미측정(이전 펌웨어) ")
            if event.get("timing_capped"):
                wait_text += "[경고: 측정값 상한 65535ms 도달] "
            print(f"[led] [slave {sid}] [수신] {received_at} 적용 확인 이펙트={effect} "
                  f"보드수신={rx}ms 보드출력완료={applied}ms 처리={duration}ms "
                  f"{wait_text}"
                  "(PC 확인 응답 수신 시각)")
        return

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
        mask = event.get("occupancy_mask", event.get("state"))
        if sid is not None and type(mask) is int and 0 <= mask <= 65535:
            try:
                reconcile_occupancy_mask(sid, mask)
            except (TypeError, ValueError):
                pass  # 잘못된 점유 마스크는 자가복구만 건너뛴다.
        return

    if t == "snapshot":
        # USB periodically refreshes UIDs. Identical snapshots must not retrigger MIDI
        # or refresh the game's idle timer.
        cell = event.get("cell")
        orb_id = event.get("id")
        if sid is None or type(cell) is not int or not 1 <= cell <= 16 or not isinstance(orb_id, str):
            return
        current = get_cells().get(_cell_key(sid, cell))
        if current and current["id"] == orb_id:
            return
        event = {**event, "type": "added"}
        _is_duplicate_added(sid, cell, orb_id)  # Update repeat tracking; live state wins.
        handle_event(event)
        return

    # added 재전송만 중복 제거한다. 제거는 slave에서 이미 안정화했다.
    try:
        cell = int(event.get("cell"))
    except (TypeError, ValueError):
        cell = None
    if sid is not None and cell is not None:
        if t == "added":
            if _is_duplicate_added(sid, cell, event.get("id")):
                return   # 슬레이브 반복 전송 — 첫 프레임만 처리한다

    handle_event(event)

# ── 이벤트 처리 ────────────────────────────────────────
@_orb_inspection.guard
def handle_event(event: dict) -> dict:
    global orb_mapping
    orb_mapping = load_mapping()
    return _handle_orb_event(event, orb_mapping)


@_orb_inspection.guard
def handle_simulation_event(event: dict) -> dict:
    # Resolve even previously placed virtual IDs after physical registration grows.
    mapping = virtual_orb_mapping()
    mapping.update(load_mapping())
    return _handle_orb_event(event, mapping)


def _handle_orb_event(event: dict, mapping: dict) -> dict:
    if _hardware_switching.is_set():
        raise RuntimeError("하드웨어 연결을 변경 중입니다")

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
    orb_key, orb = find_orb(orb_id, mapping)
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
            if _matrix_display is not None:
                _matrix_display.show_voice(voice, runtime_result.snapshot)
            try:
                from server.inout.midi_out import cell_to_pitch, trigger as midi_trigger

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
    with _state_lock:
        _last_added.pop(_cell_key(slave, cell), None)

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
# 센서 순간 오검출 방지: 해당 슬롯의 점유 비트가 연속 2회 같을 때 반영한다.

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
    with _state_lock:
        _last_added.pop(_cell_key(slave_id, cell), None)
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
        occupied_mem = {
            int(k.split(":", 1)[1])
            for k in get_cells()
            if k.startswith(f"{slave_id}:")
        }

    if prev is None:
        return

    for cell in range(1, 17):
        # 다른 슬롯의 변화 때문에 이 슬롯의 제거 복구가 계속 지연되지 않게 한다.
        if (prev ^ occupancy_mask) & (1 << (cell - 1)):
            continue
        mask_occ = bool((occupancy_mask >> (cell - 1)) & 1)
        mem_occ  = cell in occupied_mem
        if mask_occ and not mem_occ:
            _synth_added_unknown(slave_id, cell)
        elif not mask_occ and mem_occ:
            _synth_removed(slave_id, cell)

# ── 시리얼 하드웨어 입력 ───────────────────────────────
def _update_serial_status(connected: bool, port: str, error: str) -> None:
    """SerialReader 상태를 Admin snapshot에 반영한다."""
    global _dfesta_led_state
    with _state_lock:
        _serial_status.update(
            {"connected": connected, "port": port, "error": error}
        )
    if _tray_hardware:
        _publish_transition(get_game_runtime().set_tray(None, None))
    if not connected:
        return
    # ESP 가 재부팅하면 표시등이 LOW 로 돌아가므로 현재 상태를 다시 보낸다.
    with _dfesta_led_lock:
        _dfesta_led_state = None
    try:
        # 지난 종료 때 소등해 두었으므로 이번 실행의 첫 연결에서 일반 모드로 되돌린다.
        # 재연결마다 보내면 admin 에서 지정한 이펙트를 덮어쓰므로 한 번만 보낸다.
        global _led_normal_restored
        if not _led_normal_restored:
            if _serial_writer.send_line(f"C{LED_EFFECT_NORMAL}"):
                _led_normal_restored = True
        _sync_dfesta_led(get_game_state())
    except Exception as exc:  # 표시등 실패가 입력 스레드를 죽이면 안 된다.
        print(f"[WARN] 디페스타 표시등 동기화 실패: {exc}")


_hardware_control_lock = threading.Lock()


def _clear_hardware_game() -> None:
    global _led_normal_restored
    reset_game()
    _remove_all_cells_for_admin()
    with _state_lock:
        _slaves.clear()
        _last_added.clear()
    _led_normal_restored = False


def configure_hardware(data: dict) -> dict:
    """Stop old readers, reset the game, then accept only the selected route."""
    global input_mode
    if not isinstance(data, dict):
        raise ValueError("JSON object 필요")
    mode = data.get("mode", "master")
    ports = data.get("ports", [])
    baud = data.get("baud", 115200)
    hardware_io.validate_connection(mode, ports, baud)
    with _hardware_control_lock:
        hardware_io.on_message = handle_serial_message
        hardware_io.on_ready = lambda: _update_serial_status(True, "", "")
        _hardware_switching.set()
        try:
            def prepare():
                _clear_hardware_game()
                _set_tray_source(mode == "master")
            hardware_io.configure_connection(mode, ports, baud, before_start=prepare)
            input_mode = "serial"
        finally:
            _hardware_switching.clear()
    return {"ok": True, "hardware": hardware_io._snapshot()}


def disconnect_hardware() -> dict:
    with _hardware_control_lock:
        _hardware_switching.set()
        try:
            hardware_io.stop_serial()
            _clear_hardware_game()
            _set_tray_source(False)
        finally:
            _hardware_switching.clear()
    return {"ok": True}


def start_serial_input():
    """Always boot in master mode; table mode leaves hardware disconnected."""
    if input_mode != "serial":
        return None
    hardware_io.on_message = handle_serial_message
    hardware_io.on_ready = lambda: _update_serial_status(True, "", "")
    cfg = config.get("serial") or {}
    port = cfg.get("port", "")
    hardware_io.configure_connection("master", [port] if port else [], int(cfg.get("baudrate", 115200)))


def stop_serial_input() -> None:
    hardware_io.stop_serial()

# ── 진입점 ─────────────────────────────────────────────
def _update_matrix_display() -> None:
    _start_initial_hint_if_due()
    # Do not expose the brief READY + tray-on snapshot during automatic start.
    with _tray_lock:
        snapshot = get_game_state()
        snapshot["orb_inspection"] = _orb_inspection.active
        with _scheduler_lock:
            scheduler = _playback_scheduler
        playback = scheduler.display_snapshot() if scheduler is not None else {}
    _hint_led.sync_ready(snapshot, playback, _orb_inspection.active)
    if _matrix_display is not None:
        _matrix_display.update(snapshot, playback)


def orb_action(data: dict) -> dict:
    global orb_mapping
    with _orb_inspection.lock:
        action = data.get("action")
        if action in ("select_version", "reshuffle_version"):
            version = data.get("version")
            if version not in ("original", "male"):
                raise ValueError("올바른 오브 버전을 선택하세요")
            if not _orb_inspection.active:
                orb_action({"action": "start"})
            result = _orb_inspection.select_version(
                version, reshuffle=action == "reshuffle_version")
            orb_mapping = load_mapping()
            return {**result, **_orb_inspection.snapshot()}
        if action == "auto_start":
            if not _orb_inspection.active:
                orb_action({"action": "start"})
            _orb_inspection.begin_registration()
            return {"ok": True, **_orb_inspection.snapshot()}
        if action == "auto_stop":
            _orb_inspection.auto_register = False
            return {"ok": True, **_orb_inspection.snapshot()}
        if action == "start":
            if _orb_inspection.active:
                raise RuntimeError("기존 점검을 종료한 뒤 새 점검을 시작하세요")
            _orb_inspection.start()
            _cancel_playback()
            reset_game()
            _remove_all_cells_for_admin()
            from server.inout import midi_out
            midi_out.shutdown()
            hardware_io.send_line("H0,0,0")
            hardware_io.send_line("C0")
            hardware_io.send_line("D0")
        elif action == "pause" and _orb_inspection.mode == "running":
            _orb_inspection.mode = "paused"
        elif action == "resume" and _orb_inspection.mode == "paused":
            _orb_inspection.mode = "running"
            _orb_inspection.snapshot()
            _orb_inspection.seen.update(_orb_inspection.cells.values())
        elif action == "finish" and _orb_inspection.active:
            # A new engine starts READY; physical reports must establish tray state again.
            get_game_runtime().replace_engine(_new_game_engine(), allow_occupied=True)
            _publish_transition(get_game_runtime().set_tray(None, None))
            _orb_inspection.mode = "finished"
            _orb_inspection.auto_register = False
            hardware_io.send_line("H0,0,0")
        elif action == "register":
            result = _orb_inspection.register(data)
            orb_mapping = load_mapping()
            return result
        else:
            raise ValueError("현재 상태에서 실행할 수 없는 작업입니다")
        return {"ok": True, **_orb_inspection.snapshot()}


def run_main() -> None:
    global _matrix_display, _game_events
    install_logging()
    _game_events = GameEvents(config.get("event_midi") or {})
    runtime = get_game_runtime()
    _matrix_display = MatrixDisplay(config.get("matrix_display") or {},
                                    on_screen_change=_game_events.screen_changed)
    start_websocket_service()
    print(f"[stage {current_stage}] 정답 시퀀스: {answer_sequence}")
    print(f"[melody] {melody_id}")
    print(f"[melody rotation] {_melody_manager.snapshot()}")
    print(f"[input] {input_mode} mode")
    print("[audio] midi")
    print("admin에서 start를 누르면 게임이 시작됩니다.")
    web_config = config.get("web") or {"host": "127.0.0.1", "port": 8080}
    print(f"[http] http://127.0.0.1:{web_config.get('port', 8080)}/admin")
    print(f"[http] http://127.0.0.1:{web_config.get('port', 8080)}/admin/software")

    try:
        from server.inout.midi_out import ensure_init

        ok, err = ensure_init()
        if ok:
            print("[midi] ready (loopMIDI → Ableton)")
        else:
            print(f"[midi] WARN: {err}")
    except Exception as exc:
        print(f"[midi] WARN: init failed: {exc}")

    from server.web.web_server import start_http_server

    start_http_server(
        web_config,
        handle_event,
        on_simulation_event=handle_simulation_event,
        get_status=get_status,
        get_game_state=get_game_state,
        get_melody=get_active_melody,
        get_cells=get_cells,
        get_events=get_events,
        on_game_start=start_game,
        on_game_tray=set_tray,
        get_software_settings=get_software_settings,
        on_software_settings=update_software_settings,
        on_game_end=end_game,
        on_game_reset=reset_game,
        on_game_listen=request_listen,
        on_game_play=request_play,
        on_game_dfesta=request_dfesta,
        on_admin_led_effect=send_led_effect,
        get_orb_inspection=_orb_inspection.snapshot,
        on_orb_action=orb_action,
        get_hardware_ports=hardware_io.list_serial_ports,
        on_hardware_connect=configure_hardware,
        on_hardware_disconnect=disconnect_hardware,
        on_admin_collect_orbs=collect_all_orbs,
        on_admin_auto_complete=auto_complete_game,
    )

    start_serial_input()

    try:
        while True:
            # Current snapshots avoid delayed playback callbacks restoring an old
            # message. Serial I/O stays off the MIDI and game runtime threads.
            _update_matrix_display()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("종료")
    finally:
        blackout_leds()   # 시리얼 스레드가 포트를 닫기 전에 보내야 한다
        stop_serial_input()
        _matrix_display.close()
        stop_websocket_service()
        runtime.close()
        _game_events.close()
        with _scheduler_lock:
            scheduler = _playback_scheduler
        if scheduler is not None:
            scheduler.close()
        from server.inout.midi_out import shutdown

        shutdown()


if __name__ == "__main__":
    run_main()

