import importlib
from pathlib import Path
import sys
import time
import types


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT


class FakeScheduler:
    """작업 스레드 없이 운영 Scheduler 계약을 재현한다."""

    def __init__(self):
        self.starts = []
        self.cancel_count = 0
        self.closed = False

    def start(self, mode, melody, cells=None):
        mode_value = getattr(mode, "value", str(mode))
        call = {
            "mode": mode_value,
            "bpm": melody.bpm,
            "cells": dict(cells or {}),
        }
        self.starts.append(call)
        return {
            "run_id": len(self.starts),
            "mode": mode_value,
            "step_count": len(melody.steps),
        }

    def snapshot(self):
        return {
            "active": bool(self.starts) and not self.closed,
            "run_id": len(self.starts),
            "mode": self.starts[-1]["mode"] if self.starts else None,
        }

    def cancel(self):
        self.cancel_count += 1

    def close(self, timeout=2.0):
        self.closed = True


class FakeWebSocketService:
    """TCP 리스너를 열지 않고 브라우저 전송 내용을 기록한다."""

    def __init__(self):
        self.messages = []
        self.running = True
        self.closed = False

    def broadcast(self, address, args):
        self.messages.append((address, list(args)))
        return True

    def close(self):
        self.running = False
        self.closed = True


def _load_main_module():
    serial_module = types.ModuleType("serial")
    serial_module.SerialException = type("SerialException", (Exception,), {})
    serial_module.Serial = lambda *args, **kwargs: None

    sys.modules["serial"] = serial_module
    sys.path.insert(0, str(PROJECT_ROOT))
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.serial_io", None)

    module = importlib.import_module("app.main")
    module._playback_scheduler = FakeScheduler()
    module._websocket_service = FakeWebSocketService()
    return module


def test_main_routes_web_event_through_runtime_snapshot_and_midi():
    main = _load_main_module()
    midi_calls = []
    midi_module = types.ModuleType("app.midi_out")
    midi_module.cell_to_pitch = lambda cell: (int(cell) - 1) % 8
    midi_module.trigger = lambda voice, pitch, **kwargs: midi_calls.append(
        (voice, pitch, kwargs)
    ) or {"ok": True}
    sys.modules["app.midi_out"] = midi_module

    placements = [
        ("AA:00:00:00:00:00:00:01", 1, 1, "aespa_1"),
        ("AA:00:00:00:00:00:00:02", 1, 14, "aespa_2"),
        ("AA:00:00:00:00:00:00:03", 2, 5, "aespa_3"),
        ("AA:00:00:00:00:00:00:04", 2, 12, "aespa_4"),
        ("AA:00:00:00:00:00:00:05", 3, 3, "aespa_5"),
        ("AA:00:00:00:00:00:00:06", 3, 10, "aespa_6"),
        ("AA:00:00:00:00:00:00:07", 4, 1, "aespa_7"),
        ("AA:00:00:00:00:00:00:08", 4, 9, "aespa_8"),
    ]
    mapping = {
        orb_id: {"voice": voice}
        for orb_id, _slave, _cell, voice in placements
    }
    main.load_mapping = lambda: mapping

    try:
        started = main.start_game()
        assert started["game"]["state"] == "RUNNING"
        assert main._playback_scheduler.starts[0]["mode"] == "listen"

        orb_id, slave, cell, voice = placements[0]
        placed = main.handle_event(
            {"type": "added", "slave": slave, "cell": cell, "id": orb_id}
        )

        assert placed["result"] == "correct"
        assert placed["game"]["state"] == "RUNNING"
        assert midi_calls == [(voice, 0, {"duration_ms": 320})]

        game = main.get_game_state()
        cells = main.get_cells()
        status = main.get_status()
        assert game["cells"]["1:1"]["orb_id"] == orb_id
        assert cells["1:1"] == {
            "id": orb_id,
            "voice": "aespa_1",
            "note": "C",
            "result": "correct",
        }
        assert status["game"] == game
        assert status["melody"] == "dynamite"
        assert status["melody_rotation"]["next_melody"] == "Iam"
        assert "audio_output" not in status

        for orb_id, slave, cell, _voice in placements[1:]:
            completed = main.handle_event(
                {"type": "added", "slave": slave, "cell": cell, "id": orb_id}
            )

        assert completed["game"]["state"] == "RUNNING"
        assert completed["game"]["is_correct"] is True
        assert completed["game"]["completion"]["dfesta_presses_remaining"] == 1
        state_events = [
            args[0]
            for address, args in main._websocket_service.messages
            if address == "/game/state"
        ]
        assert set(state_events) <= {"READY", "RUNNING", "RESETTING"}

        ended = main.request_dfesta()

        assert ended["counted"] is True
        assert ended["game"]["state"] == "RESETTING"
        assert ended["game"]["end_reason"] == "complete"
        assert main.get_game_state()["state"] == "RESETTING"
        assert len(midi_calls) == 8

        messages = main._websocket_service.messages
        addresses = [address for address, _args in messages]
        final_added_index = max(
            i for i, value in enumerate(addresses) if value == "/orb/added"
        )
        resetting_index = next(
            i
            for i, (address, args) in enumerate(messages)
            if address == "/game/state" and args[0] == "RESETTING"
        )
        assert final_added_index < resetting_index

        for orb_id, slave, cell, _voice in placements:
            removed = main.handle_event(
                {"type": "removed", "slave": slave, "cell": cell, "id": orb_id}
            )
        assert removed["changed"] is True
        assert removed["game"]["state"] == "READY"
        assert main.get_cells() == {}

        messages = main._websocket_service.messages
        addresses = [address for address, _args in messages]
        removed_index = max(i for i, value in enumerate(addresses) if value == "/orb/removed")
        ready_index = max(
            i
            for i, (address, args) in enumerate(messages)
            if address == "/game/state" and args[0] == "READY"
        )
        assert removed_index < ready_index
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial", "app.midi_out"):
            sys.modules.pop(name, None)


def test_manual_end_enters_resetting_directly():
    main = _load_main_module()

    try:
        main.start_game()

        ended = main.end_game()
        assert ended["game"]["state"] == "RESETTING"
        assert ended["game"]["end_reason"] == "manual"

        states = [
            args[0]
            for address, args in main._websocket_service.messages
            if address == "/game/state"
        ]
        assert states[-1] == "RESETTING"
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)



def test_listen_play_and_dfesta_use_python_scheduler():
    main = _load_main_module()

    try:
        main.start_game()
        runtime = main.get_game_runtime()
        original_record_activity = runtime.record_activity
        activity_calls = []

        def record_activity(*args, **kwargs):
            activity_calls.append(True)
            return original_record_activity(*args, **kwargs)

        runtime.record_activity = record_activity
        play = main.request_play()
        dfesta = main.request_dfesta()
        listen = main.request_listen()

        assert play["ok"] is True
        assert dfesta["ok"] is True
        assert dfesta["counted"] is False
        assert listen["ok"] is True
        assert [
            call["mode"] for call in main._playback_scheduler.starts
        ] == ["listen", "play", "dfesta", "listen"]
        assert activity_calls == [True, True]

        # 명령 전용 전송은 제거했다. 브라우저는 Scheduler 재생 이벤트와
        # 기준 게임 상태를 렌더링한다.
        addresses = [
            address for address, _args in main._websocket_service.messages
        ]
        assert "/game/listen" not in addresses
        assert "/game/play" not in addresses
        assert "/game/dfesta" not in addresses
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_serial_game_controls_use_the_canonical_action_literals():
    main = _load_main_module()
    calls = []
    main.start_game = lambda: calls.append("start")
    main.end_game = lambda: calls.append("end")
    main.reset_game = lambda: calls.append("reset")
    main.request_listen = lambda: calls.append("listen")
    main.request_play = lambda: calls.append("play")
    main.request_dfesta = lambda: calls.append("dfesta")

    try:
        main.handle_serial_message({"type": "game", "action": "start"})
        main.handle_serial_message({"type": "game", "action": "listen"})
        main.handle_serial_message({"type": "game", "action": "play"})
        main.handle_serial_message({"type": "game", "action": "dfesta"})
        main.handle_serial_message({"type": "game", "action": "end"})
        main.handle_serial_message({"type": "game", "action": "reset"})

        assert calls == [
            "start",
            "listen",
            "play",
            "dfesta",
            "end",
            "reset",
        ]
        assert (
            main.GAME_ACTION_START,
            main.GAME_ACTION_END,
            main.GAME_ACTION_RESET,
            main.GAME_ACTION_LISTEN,
            main.GAME_ACTION_PLAY,
            main.GAME_ACTION_DFESTA,
        ) == ("start", "end", "reset", "listen", "play", "dfesta")
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_heartbeat_uses_occupancy_mask_instead_of_game_state_field():
    main = _load_main_module()
    calls = []
    main.reconcile_occupancy_mask = lambda slave, mask: calls.append(
        (slave, mask)
    )

    try:
        main.handle_serial_message(
            {"type": "heartbeat", "slave": 2, "occupancy_mask": 5}
        )

        assert calls == [(2, 5)]
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_admin_keeps_start_and_reset_but_hides_manual_end():
    html = (ROOT / "web" / "admin" / "index.html").read_text(encoding="utf-8")

    for action in ("start", "reset"):
        assert f'data-game-action="{action}">{action}</button>' in html
    assert 'data-game-action="end"' not in html


def test_state_log_uses_authoritative_state_names_only():
    main = _load_main_module()

    try:
        main.start_game()
        main.end_game()
        events = main.get_events()

        states = [
            event["state"]
            for event in events
            if event["type"] == "game_state"
        ]
        assert states == ["RUNNING", "RESETTING"]
        assert all(state in {"READY", "RUNNING", "RESETTING"} for state in states)
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_idle_transition_log_matches_runtime_until_reset_is_complete():
    main = _load_main_module()
    main.reload_melody_data = lambda: None
    main.answer_sequence = {"1": "C"}
    main.time_over_s = None
    main.idle_s = 0.02

    try:
        main.start_game()
        runtime = main.get_game_runtime()
        runtime.add_orb(orb_id="orb-a", slave=1, cell=2)

        deadline = time.monotonic() + 1
        while (
            runtime.snapshot()["state"] != "RESETTING"
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert runtime.snapshot()["state"] == "RESETTING"

        removed = runtime.remove_orb(
            orb_id="orb-a",
            slave=1,
            cell=2,
        )
        runtime.publish_state_change(removed)

        transitions = [
            (
                event["previous_state"],
                event["state"],
                event["end_reason"],
            )
            for event in main.get_events()
            if event["type"] == "game_state"
        ]
        assert transitions == [
            ("READY", "RUNNING", None),
            ("RUNNING", "RESETTING", "idle"),
            ("RESETTING", "READY", None),
        ]
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_melody_rules_expose_120_second_time_over_and_30_second_idle_timers():
    main = _load_main_module()

    try:
        started = main.start_game()
        timers = started["game"]["timers"]

        assert timers["time_over_limit_s"] == 120
        assert timers["idle_limit_s"] == 30
        assert 119 <= timers["remaining_time_over_s"] <= 120
        assert 29 <= timers["remaining_idle_s"] <= 30
        assert started["game"]["completion"]["dfesta_presses_required"] == 1
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_playback_requests_are_ignored_outside_active_game_states():
    main = _load_main_module()

    try:
        assert main.get_game_state()["state"] == "READY"

        assert main.request_listen()["ignored"] is True
        assert main.request_play()["ignored"] is True
        assert main.request_dfesta()["ignored"] is True
        assert main._playback_scheduler.starts == []
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_resetting_state_cancels_active_scheduler_without_blocking_runtime():
    main = _load_main_module()

    try:
        main.start_game()
        before = main._playback_scheduler.cancel_count

        ended = main.end_game()

        assert ended["game"]["state"] == "RESETTING"
        assert main._playback_scheduler.cancel_count == before + 1
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_main_fans_out_events_and_builds_complete_web_snapshot():
    """브라우저가 단일 실시간 이벤트 흐름과 복구 snapshot을 받는다."""

    main = _load_main_module()
    websocket = FakeWebSocketService()
    main._websocket_service = websocket

    try:
        main._publish_web_event("/game/example", [7, "value"])

        assert websocket.messages == [("/game/example", [7, "value"])]

        snapshot = main.get_web_snapshot()
        assert snapshot["schema_version"] == 3
        assert snapshot["game"]["state"] == "READY"
        assert snapshot["melody"]["songId"] == "dynamite"
        assert snapshot["melody"]["steps"]
        assert snapshot["status"]["game"] == snapshot["game"]
        assert snapshot["status"]["cell_count"] == snapshot["game"]["cell_count"]
        assert snapshot["cells"] == {}
        assert snapshot["events"] == []
    finally:
        main._websocket_service = None
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_playback_beat_websocket_contains_display_data_only():
    main = _load_main_module()

    try:
        main._scheduler_publish_event(
            {
                "type": "beat",
                "run_id": 4,
                "mode": "play",
                "step": 2,
                "duration_ms": 320,
                "notes": [{"voice": "aespa_1", "pitch": 0}],
            }
        )
        assert main._websocket_service.messages[-1] == (
            "/game/playback/beat",
            [4, "play", 2, 320],
        )
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_start_publishes_the_active_melody_before_running_state():
    main = _load_main_module()

    try:
        main.start_game()
        messages = main._websocket_service.messages
        melody_index = next(
            i for i, (address, _args) in enumerate(messages)
            if address == "/game/melody"
        )
        running_index = next(
            i for i, (address, args) in enumerate(messages)
            if address == "/game/state" and args[0] == "RUNNING"
        )

        assert melody_index < running_index
        assert messages[melody_index][1][0]["songId"] == "dynamite"
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_successive_games_follow_the_configured_melody_sequence():
    main = _load_main_module()

    def wait_until_ready():
        deadline = time.monotonic() + 1
        while (
            main.get_game_state()["state"] != "READY"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert main.get_game_state()["state"] == "READY"

    try:
        selected = []
        for _ in range(4):
            main.start_game()
            selected.append(main.get_active_melody()["songId"])
            main.end_game()
            wait_until_ready()

        assert selected == ["dynamite", "Iam", "biplane", "dynamite"]
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("app.main", "app.serial_io", "serial"):
            sys.modules.pop(name, None)
