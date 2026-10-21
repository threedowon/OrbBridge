import pytest
import importlib
from copy import deepcopy
import re
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

    def start(self, mode, melody, cells=None, *, hint_led=False):
        mode_value = getattr(mode, "value", str(mode))
        call = {
            "mode": mode_value,
            "bpm": melody.bpm,
            "cells": dict(cells or {}),
            "hint_led": hint_led,
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

    def display_snapshot(self):
        return self.snapshot()

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


def test_matrix_follows_accepted_requests_and_ignores_delayed_web_events(monkeypatch):
    import json
    from server.inout.matrix_display import MatrixDisplay

    main = _load_main_module()
    writes = []

    def write(data):
        writes.append(json.loads(data))
        return len(data)

    device = types.SimpleNamespace(write=write, close=lambda: None)
    matrix = MatrixDisplay(
        {"enabled": True},
        serial_module=types.SimpleNamespace(Serial=lambda *args, **kwargs: device),
        catalog={"voices": {}},
    )
    matrix.BOOT_S = 0
    main._matrix_display = matrix
    try:
        main._update_matrix_display()
        assert writes[-1]["text"] == "PLACE TRAY"
        assert main.request_play()["ignored"]
        main._update_matrix_display()
        assert writes[-1]["text"] == "PLACE TRAY"
        main.start_game()
        main._update_matrix_display()
        assert writes[-1]["text"] == "HINT"
        main.request_play()
        main._update_matrix_display()
        assert writes[-1]["text"] == "PLAYING"
        main._scheduler_publish_event({
            "type": "end", "run_id": 1, "mode": "listen", "cancelled": True,
        })
        main._update_matrix_display()
        assert writes[-1]["text"] == "PLAYING"
        assert main.request_dfesta()["ignored"]
        main._update_matrix_display()
        assert writes[-1]["text"] == "PLAYING"
        main.auto_complete_game()
        main.request_dfesta()
        main._update_matrix_display()
        assert writes[-1]["text"] == "COMPLETE"
        assert main.get_game_state()["state"] == "RESETTING"
        main.reset_game()
        main._update_matrix_display()
        # The runtime keeps the completion reason when resetting a completed game.
        assert writes[-1]["text"] == "COMPLETE"
    finally:
        matrix.close()
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("mode", ["sequence", "random", "fixed"])
def test_server_starts_without_legacy_melody_files(tmp_path, monkeypatch, mode):
    import json
    from server import app_paths, data_store
    config = data_store.load_config()
    config["melody"] = "biplane"
    config["melody_rotation"] = {"mode": mode, "playlist": ["dynamite", "Iam", "biplane"]}
    document = {"songId": "new_only", "bpm": 120,
                "steps": [{"step": 1, "note": "C4", "duration": 1, "velocity": 90}]}
    (tmp_path / "new_only.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(app_paths, "ensure_data_files", lambda: None)
    monkeypatch.setattr(app_paths, "melodies_dir", lambda: tmp_path)
    monkeypatch.setattr(data_store, "load_config", lambda: deepcopy(config))
    main = _load_main_module()
    try:
        assert main.get_active_melody()["songId"] == "new_only"
        assert main.start_game()["game"]["state"] == "RUNNING"
        assert main.get_active_melody()["songId"] == "new_only"
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def _load_main_module():
    serial_module = types.ModuleType("serial")
    serial_module.SerialException = type("SerialException", (Exception,), {})
    serial_module.Serial = lambda *args, **kwargs: None

    sys.modules["serial"] = serial_module
    sys.path.insert(0, str(PROJECT_ROOT))
    sys.modules.pop("server.main", None)
    sys.modules.pop("server.inout.serial_io", None)

    module = importlib.import_module("server.main")
    # Existing playback tests exercise the no-loading compatibility path.
    module.config.setdefault("game_rules", {})["initial_hint_delay_s"] = 0
    original_load_config = module.load_config
    def load_without_loading(*args, **kwargs):
        document = original_load_config(*args, **kwargs)
        document.setdefault("game_rules", {})["initial_hint_delay_s"] = 0
        return document
    module.load_config = load_without_loading
    module._playback_scheduler = FakeScheduler()
    module._websocket_service = FakeWebSocketService()
    return module


@pytest.fixture
def tray_main(monkeypatch):
    from server.game.game_engine import GameEngine
    from server.inout.matrix_display import MatrixDisplay
    import json

    main = _load_main_module()
    clock = [0.0]
    writes = []
    def write(data):
        writes.append(json.loads(data))
        return len(data)
    device = types.SimpleNamespace(write=write, close=lambda: None)
    matrix = MatrixDisplay(
        {"enabled": True}, clock=lambda: clock[0], catalog={"voices": {}},
        serial_module=types.SimpleNamespace(Serial=lambda *a, **k: device),
    )
    matrix.BOOT_S = 0
    main._matrix_display = matrix
    monkeypatch.setattr(main, "_tray_hardware", False)
    monkeypatch.setattr(main, "reload_melody_data", lambda: None)
    monkeypatch.setattr(main, "_new_game_engine", lambda: GameEngine(
        {"1": "C"}, time_over_s=60, idle_s=30, clock=lambda: clock[0]))
    try:
        yield main, clock, writes
    finally:
        matrix.close()
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_admin_status_exposes_matrix_connection(tray_main, monkeypatch):
    main, _, writes = tray_main
    assert main.get_status()["matrix"]["state"] == "waiting"
    assert writes == []
    main._update_matrix_display()
    assert main.get_status()["matrix"]["connected"] is True
    main._matrix_display.close()
    assert main.get_status()["matrix"]["state"] == "disconnected"
    monkeypatch.setattr(main, "_matrix_display", None)
    assert main.get_status()["matrix"] is None


@pytest.mark.parametrize("return_at", [0.5, 4.5, None])
def test_admin_tray_end_to_end_cleanup_and_next_player(tray_main, return_at):
    main, clock, writes = tray_main
    main._update_matrix_display()
    assert writes[-1]["text"] == "PLACE TRAY"
    main.set_tray({"present": True})
    assert main.get_game_state()["state"] == "RUNNING"
    starts = len(main._playback_scheduler.starts)
    main.set_tray({"present": True})
    assert len(main._playback_scheduler.starts) == starts
    runtime = main.get_game_runtime()
    runtime.add_orb(orb_id="a", slave=1, cell=1)
    main.end_game()
    runtime.remove_orb(slave=1, cell=1)
    main._update_matrix_display()
    assert main.get_game_state()["state"] == "RESETTING"
    assert writes[-1]["text"] == "PLEASE CLEAR TABLE"
    main.set_tray({"present": False})
    main._update_matrix_display()
    assert writes[-1]["text"] == "GOODBYE"
    goodbye_index = len(writes)
    clock[0] = return_at if return_at is not None else 4
    runtime.barrier()
    main._update_matrix_display()
    if clock[0] >= 4:
        assert writes[-1] == {"type": "clear"}
    if return_at is not None:
        main.set_tray({"present": True})
        main._update_matrix_display()
        assert main.get_game_state()["state"] == "RUNNING"
        assert writes[-1]["text"] == "HINT"
        assert not any(p.get("text") == "PLACE TRAY" for p in writes[goodbye_index:])
    else:
        clock[0] = 8
        runtime.barrier()
        main._update_matrix_display()
        assert writes[-1]["text"] == "PLACE TRAY"


def test_serial_tray_boot_heartbeat_partial_removal_and_input_source(tray_main):
    main, clock, writes = tray_main
    main._set_tray_source(True)
    main.handle_serial_message({"type": "tray", "present": True, "seated": True})
    assert main.get_game_state()["state"] == "READY"  # no start on boot already seated
    with pytest.raises(main.InvalidTransition):
        main.set_tray({"present": False})  # admin cannot overwrite live hardware
    for seated in [False, True]:
        main.handle_serial_message({"type": "tray", "present": seated, "seated": seated})
    assert main.get_game_state()["state"] == "RUNNING"
    starts = len(main._playback_scheduler.starts)
    clock[0] = 1
    main.handle_serial_message({"type": "tray", "present": True, "seated": True})
    assert len(main._playback_scheduler.starts) == starts
    main.end_game()
    main.handle_serial_message({"type": "tray", "present": True, "seated": False})
    assert main.get_game_state()["state"] == "RESETTING"
    clock[0] = 5
    main.get_game_runtime().barrier()
    assert main.get_game_state()["tray"]["present"] is None
    assert main.get_game_state()["state"] == "RESETTING"
    main.handle_serial_message({"type": "tray", "present": False, "seated": False})
    main._update_matrix_display()
    assert writes[-1]["text"] == "GOODBYE"


def test_placing_tray_during_cleanup_does_not_queue_a_later_start(tray_main):
    main, clock, _ = tray_main
    main.set_tray({"present": True})
    runtime = main.get_game_runtime()
    runtime.add_orb(orb_id="a", slave=1, cell=1)
    main.end_game()
    main.set_tray({"present": False})
    main.set_tray({"present": True})
    runtime.remove_orb(slave=1, cell=1)
    starts = len(main._playback_scheduler.starts)
    main.set_tray({"present": True})
    assert main.get_game_state()["state"] == "RESETTING"
    assert len(main._playback_scheduler.starts) == starts
    main.set_tray({"present": False})
    main.set_tray({"present": True})
    assert main.get_game_state()["state"] == "RUNNING"


@pytest.mark.parametrize("melody_id", ["dynamite", "Iam", "biplane"])
def test_completed_game_starts_harmony_plan_and_keeps_final_dfesta(monkeypatch, melody_id):
    from server.game.scheduler import build_plan

    main = _load_main_module()
    config = deepcopy(main.config)
    config["melody"] = melody_id
    config["melody_rotation"] = {"mode": "fixed", "playlist": [melody_id]}
    monkeypatch.setattr(main, "load_config", lambda: deepcopy(config))
    monkeypatch.setattr(main, "load_mapping", lambda: {
        f"ORB-{i}": {"voice": f"voice_{i}"} for i in range(12)
    })
    plans = []
    scheduler = main._playback_scheduler
    original_start = scheduler.start

    def start(mode, melody, cells=None, *, hint_led=False):
        plans.append(build_plan(mode, melody, cells))
        return original_start(mode, melody, cells, hint_led=hint_led)

    scheduler.start = start
    try:
        main.start_game()
        main.auto_complete_game()
        previous_cancels = scheduler.cancel_count
        result = main.request_dfesta()
        assert result["counted"] is True
        assert result["game"]["end_reason"] == "complete"
        assert scheduler.cancel_count == previous_cancels
        assert plans[-1].mode.value == "dfesta"
        for cue in plans[-1].cues:
            if cue.notes:
                assert len({note["pitch"] for note in cue.notes}) == 3
                assert len(cue.notes) == result["game"]["cell_count"]
        main.collect_all_orbs()
        assert main.get_game_state()["state"] == "READY"
        assert scheduler.cancel_count == previous_cancels
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_main_routes_web_event_through_runtime_snapshot_and_midi(monkeypatch):
    main = _load_main_module()
    config = deepcopy(main.config)
    config["melody_rotation"] = {"mode": "sequence", "playlist": ["dynamite", "Iam", "biplane"]}
    monkeypatch.setattr(main, "load_config", lambda: deepcopy(config))
    midi_calls = []
    matrix_calls = []
    main._matrix_display = types.SimpleNamespace(
        connection_status=lambda: {"enabled": True, "connected": True},
        clear_voice=lambda: None,
        show_voice=lambda voice, snapshot: matrix_calls.append(
            (voice, snapshot["state"])
        )
    )
    midi_module = types.ModuleType("server.inout.midi_out")
    midi_module.cell_to_pitch = lambda cell: (int(cell) - 1) % 8
    midi_module.trigger = lambda voice, pitch, **kwargs: midi_calls.append(
        (voice, pitch, kwargs)
    ) or {"ok": True}
    sys.modules["server.inout.midi_out"] = midi_module

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
        assert matrix_calls == [(voice, "RUNNING")]

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
        for name in ("server.main", "server.inout.serial_io", "serial", "server.inout.midi_out"):
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


@pytest.fixture
def simulation_main(monkeypatch):
    from server.inout import midi_out

    main = _load_main_module()
    monkeypatch.setattr(main, "load_mapping", lambda: {})
    monkeypatch.setattr(midi_out, "trigger", lambda *args, **kwargs: {"ok": True})
    try:
        yield main
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("registered_count", [0, 2, 12])
def test_simulation_auto_complete_without_full_registration(simulation_main, monkeypatch, registered_count):
    main = simulation_main
    registered = {f"ORB-{i:02d}": {"voice": "aespa_1"} for i in range(registered_count)}
    monkeypatch.setattr(main, "load_mapping", lambda: deepcopy(registered))
    main.start_game()
    result = main.auto_complete_game()
    assert result["ok"] and result["game"]["is_correct"]
    expected = sum(step.pitch is not None for step in main.playback_melody.steps)
    assert result["changed"] == expected
    ids = [cell["id"] for cell in main.get_cells().values()]
    assert len(set(ids)) == expected
    assert set(sorted(registered)[:expected]) <= set(ids)
    assert main.load_mapping() == registered
    assert main.collect_all_orbs()["changed"] == expected
    assert main.get_cells() == {}


def test_simulation_drag_move_remove_and_physical_id_isolation(simulation_main):
    main = simulation_main
    main.start_game()
    orb_id, orb = next(iter(main.virtual_orb_mapping().items()))
    event = {"type": "added", "slave": 1, "cell": 1, "id": orb_id}
    main.handle_simulation_event(event)
    assert main.get_cells()["1:1"]["voice"] == orb["voice"]
    assert main.handle_simulation_event(dict(event, type="removed"))["changed"]
    main.handle_simulation_event(dict(event, slave=2, cell=9))
    assert set(main.get_cells()) == {"2:9"}
    main.handle_simulation_event(dict(event, type="removed", slave=2, cell=9))
    assert main.get_cells() == {}
    main.handle_event(event)
    assert main.get_cells()["1:1"]["voice"] == "unknown"
    assert main.load_mapping() == {}


def test_admin_auto_complete_and_collect_use_runtime_state(monkeypatch):
    main = _load_main_module()
    config = deepcopy(main.config)
    config["melody_rotation"] = {"mode": "sequence", "playlist": ["dynamite", "Iam", "biplane"]}
    monkeypatch.setattr(main, "load_config", lambda: deepcopy(config))
    mapping = {
        f"ORB-{index:02d}": {"voice": f"aespa_{index}"}
        for index in range(1, 13)
    }
    main.load_mapping = lambda: mapping

    try:
        main.start_game()
        runtime = main.get_game_runtime()
        runtime.add_orb(
            orb_id="WRONG-ORB",
            slave=1,
            cell=2,
            voice="aespa_1",
            known=True,
        )

        completed = main.auto_complete_game()

        assert completed["ok"] is True
        assert completed["removed"] == 1
        assert completed["changed"] == 8
        assert completed["game"]["state"] == "RUNNING"
        assert completed["game"]["is_correct"] is True
        assert set(completed["game"]["cells"]) == {
            "1:1",
            "1:14",
            "2:5",
            "2:12",
            "3:3",
            "3:10",
            "4:1",
            "4:9",
        }
        assert all(
            cell["result"] == "correct"
            for cell in completed["game"]["cells"].values()
        )

        addresses = [
            address for address, _args in main._websocket_service.messages
        ]
        assert addresses.count("/orb/added") == 8
        assert addresses.count("/orb/removed") == 1

        collected = main.collect_all_orbs()

        assert collected["ok"] is True
        assert collected["changed"] == 8
        assert collected["game"]["state"] == "RUNNING"
        assert collected["game"]["cell_count"] == 0
        assert main.get_cells() == {}
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        # 정답 오브가 다 올라오지 않았으면 DFesta 는 재생하지 않는다.
        assert dfesta["ok"] is False
        assert dfesta["ignored"] is True
        assert dfesta["counted"] is False
        assert listen["ok"] is True
        assert [
            call["mode"] for call in main._playback_scheduler.starts
        ] == ["listen", "play", "listen"]
        assert [call["hint_led"] for call in main._playback_scheduler.starts] == [True, False, True]
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("page", ["software", "hardware"])
def test_admin_has_tray_toggle_and_reset_but_no_momentary_start(page):
    html = (ROOT / "admin" / page / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="btn-game-tray" aria-pressed="false"' in html
    assert 'data-game-action="start"' not in html
    assert 'btn-game-start' not in html
    assert 'data-game-action="reset"' in html
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_idle_transition_log_matches_runtime_until_reset_is_complete(monkeypatch):
    main = _load_main_module()
    main.reload_melody_data = lambda: None
    main.answer_sequence = {"1": "C"}
    main.time_over_s = None
    main.idle_s = 0.02
    original_factory = main._new_game_engine
    def without_intro_delay():
        engine = original_factory()
        engine.initial_timer_delay_s = 0
        return engine
    monkeypatch.setattr(main, "_new_game_engine", without_intro_delay)

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
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def _use_test_timers(monkeypatch):
    """운영 config의 진단용 시간 값과 무관하게 테스트 시작 설정을 고정한다."""
    from server import data_store

    config = data_store.load_config()
    config["game_rules"] = {"time_over_s": 120, "idle_s": 30}
    monkeypatch.setattr(data_store, "load_config", lambda: deepcopy(config))


def test_config_rules_expose_120_second_time_over_and_30_second_idle_timers(monkeypatch):
    _use_test_timers(monkeypatch)
    main = _load_main_module()

    try:
        started = main.start_game()
        timers = started["game"]["timers"]

        assert timers["time_over_limit_s"] == 120
        assert timers["idle_limit_s"] == 30
        assert 119 <= timers["remaining_time_over_s"] <= 120
        assert 29 <= timers["remaining_idle_s"] <= 30
        assert started["game"]["completion"]["dfesta_presses_required"] == 1
        main.reset_game()
        main.get_game_runtime().barrier()
        from copy import deepcopy
        updated = deepcopy(main.config)
        updated["game_rules"] = {"time_over_s": 180, "idle_s": 45}
        main.load_config = lambda: updated
        main.reload_melody_data()
        assert main.time_over_s == 120
        assert main.idle_s == 30
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        assert snapshot["melody"]["songId"] == main.get_active_melody()["songId"]
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        for name in ("server.main", "server.inout.serial_io", "serial"):
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
        assert messages[melody_index][1][0]["songId"] == main.get_active_melody()["songId"]
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_successive_games_follow_the_configured_melody_sequence(monkeypatch):
    main = _load_main_module()
    config = deepcopy(main.config)
    config["melody_rotation"] = {"mode": "sequence", "playlist": ["dynamite", "Iam", "biplane"]}
    monkeypatch.setattr(main, "load_config", lambda: deepcopy(config))
    # Keep this rotation fixture independent of songs added for venue testing.
    monkeypatch.setattr(main._melody_manager, "available_ids", lambda: ["dynamite", "Iam", "biplane"])

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
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_software_settings_persist_timers_without_hot_reload(tmp_path, monkeypatch):
    from server.data_store import load_config, save_config
    _use_test_timers(monkeypatch)
    main = _load_main_module()
    path = tmp_path / "config.json"
    original = main.config.copy()
    save_config(original, path)
    monkeypatch.setattr(main, "load_config", lambda: load_config(path))
    monkeypatch.setattr(main, "save_config", lambda value: save_config(value, path))
    try:
        settings = main.update_software_settings({"game_rules": {"time_over_s": 180, "idle_s": 45}})
        assert settings["restart_required"]
        assert settings["debug_mode"] is False
        assert settings["active_timers"] == {"time_over_s": 120, "idle_s": 30}
        assert load_config(path)["game_rules"] == {
            "time_over_s": 180, "idle_s": 45, "initial_hint_delay_s": 0,
        }
        assert load_config(path)["input_mode"] == original["input_mode"]
        main.reload_melody_data()
        assert (main.time_over_s, main.idle_s) == (120, 30)
        for value in (0, -1, True, "bad", float("inf"), None):
            with pytest.raises(ValueError):
                main.update_software_settings({"game_rules": {"time_over_s": value, "idle_s": 45}})
        assert load_config(path)["game_rules"]["time_over_s"] == 180
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_debug_start_preserves_orbs_and_allows_restart_from_resetting():
    main = _load_main_module()
    try:
        runtime = main.get_game_runtime()
        runtime.add_orb(orb_id="debug-orb", slave=1, cell=1, voice="aespa_1", known=True)
        with pytest.raises(main.InvalidTransition):
            main.start_game()
        main.update_software_settings({"debug_mode": True})
        main.start_game()
        assert main.get_game_state()["state"] == "RUNNING"
        assert main.get_game_state()["cell_count"] == 1
        with pytest.raises(main.InvalidTransition):
            main.start_game()
        main.end_game()
        main.start_game()
        assert main.get_game_state()["cell_count"] == 1
        main.end_game()
        main.update_software_settings({"debug_mode": False})
        with pytest.raises(main.InvalidTransition):
            main.start_game()
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_debug_melody_restart_and_999_requirement_are_temporary(monkeypatch, tmp_path):
    import json
    from server.game.melody_manager import MelodyManager
    main = _load_main_module()
    song = deepcopy(main.melody_document)
    song["game_rules"] = {"dfesta_presses_required": 3}
    for name in ["original", "added"]:
        (tmp_path / f"{name}.json").write_text(json.dumps(song), encoding="utf-8")
    config = deepcopy(main.config)
    config["melody"] = "original"
    config["melody_rotation"] = {"mode": "sequence", "playlist": ["original"]}
    monkeypatch.setattr(main, "load_config", lambda: deepcopy(config))
    monkeypatch.setattr(main, "_melody_manager", MelodyManager(tmp_path))
    try:
        main.start_game()
        runtime = main.get_game_runtime()
        runtime.add_orb(orb_id="kept", slave=1, cell=1, voice="aespa_1", known=True)
        with pytest.raises(ValueError):
            main.update_software_settings({"debug_melody": "added"})
        assert main.melody_id == "original"
        main.update_software_settings({"debug_mode": True})
        assert runtime.snapshot()["completion"]["dfesta_presses_required"] == 999
        before = main._melody_manager.snapshot()
        for bad in ["../config", "missing", None]:
            with pytest.raises(ValueError):
                main.update_software_settings({"debug_melody": bad})
        assert runtime.snapshot()["state"] == "RUNNING"
        settings = main.update_software_settings({"debug_melody": "added"})
        assert settings["current_melody"] == "added"
        assert settings["available_melodies"] == ["added", "original"]
        assert runtime.snapshot()["cell_count"] == 1
        assert runtime.snapshot()["state"] == "RUNNING"
        assert runtime.snapshot()["completion"]["dfesta_presses_required"] == 999
        assert main._melody_manager.snapshot() == before
        main.update_software_settings({"debug_mode": False})
        assert runtime.snapshot()["completion"]["dfesta_presses_required"] == 3
        assert json.loads((tmp_path / "added.json").read_text())["game_rules"]["dfesta_presses_required"] == 3
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("song_id", ["dynamite", "Iam", "biplane"])
def test_initial_listen_duration_plus_one_precedes_both_countdowns(monkeypatch, song_id):
    from server.game.game_engine import GameEngine
    main = _load_main_module()
    now = [100.0]
    def engine_with_clock(*args, **kwargs):
        return GameEngine(*args, **kwargs, clock=lambda: now[0])
    monkeypatch.setattr(main, "GameEngine", engine_with_clock)
    config = deepcopy(main.config)
    config["melody"] = song_id
    config["melody_rotation"] = {"mode": "fixed", "playlist": [song_id]}
    monkeypatch.setattr(main, "load_config", lambda: deepcopy(config))
    try:
        main.start_game()
        runtime = main.get_game_runtime()
        delay = sum(step.duration_beats for step in main.playback_melody.steps) * 60 / main.playback_melody.bpm + 1
        now[0] += delay - 0.1
        runtime.record_activity()
        timers = runtime.snapshot()["timers"]
        assert timers["remaining_time_over_s"] == main.time_over_s
        assert timers["remaining_idle_s"] == main.idle_s
        now[0] += 0.6
        runtime.record_activity()
        assert runtime.snapshot()["timers"]["remaining_time_over_s"] == pytest.approx(main.time_over_s - 0.5)
        assert runtime.snapshot()["timers"]["remaining_idle_s"] == main.idle_s
        main.request_listen()
        now[0] += 1
        runtime.record_activity()
        assert runtime.snapshot()["timers"]["remaining_time_over_s"] == pytest.approx(main.time_over_s - 1.5)
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)


def test_orb_inspection_stops_game_and_blocks_playback(tray_main, monkeypatch, tmp_path):
    import json
    from server.inout import midi_out
    main, clock, writes = tray_main
    path = tmp_path / "mapping.json"
    oid = "2D:01:02:03:04:05:06:07"
    path.write_text(json.dumps({oid: {"voice": "aespa_1"}}))
    main._orb_inspection.path = path
    sent, stopped = [], []
    monkeypatch.setattr(main.hardware_io, "send_line", lambda line: sent.append(line))
    monkeypatch.setattr(midi_out, "shutdown", lambda: stopped.append(True))
    main.start_game()
    main.orb_action({"action":"start"})
    assert stopped == [True]
    assert "H0,0,0,600" in sent and "D0" in sent
    assert main.get_game_state()["state"] != "RUNNING"
    for fn in (main.start_game, main.request_play, main.request_listen, main.request_dfesta):
        with pytest.raises(RuntimeError): fn()
    main.handle_serial_message({"type":"game", "action":"start"})
    main.handle_serial_message({"type":"tray", "present":True, "seated":True})
    main.handle_serial_message({"type":"heartbeat", "slave":1,"state":1})
    main.handle_serial_message({"type":"added", "slave":1,"cell":1,"id":oid})
    assert main._orb_inspection.snapshot()["seen"] == [oid]
    assert main.get_game_state()["cell_count"] == 0
    assert main._scheduler_send_notes([{"pitch":0}]) == []
    main.orb_action({"action":"pause"})
    with pytest.raises(RuntimeError): main.request_play()
    main.orb_action({"action":"resume"})
    main.orb_action({"action":"finish"})
    assert main.get_game_state()["state"] == "READY"
    assert main._orb_inspection.snapshot()["seen"] == [oid]


def test_orb_auto_start_enters_maintenance_and_registers(tray_main, monkeypatch, tmp_path):
    import json
    from server.inout import midi_out
    main, _, _ = tray_main
    mapping=tmp_path/"mapping.json"
    mapping.write_text("{}")
    main._orb_inspection.path=mapping
    monkeypatch.setattr(main.hardware_io,"send_line",lambda line:True)
    monkeypatch.setattr(midi_out,"shutdown",lambda:None)
    result=main.orb_action({"action":"auto_start"})
    assert result["auto_register"] and result["mode"]=="running"
    with pytest.raises(RuntimeError):main.start_game()
    main.handle_serial_message({"type":"added","slave":1,"cell":1,"id":"2D:01:02:03:04:05:06:07"})
    assert len(json.loads(mapping.read_text()))==1
    main.orb_action({"action":"auto_stop"})
    main.handle_serial_message({"type":"added","slave":1,"cell":2,"id":"2D:11:02:03:04:05:06:07"})
    assert len(json.loads(mapping.read_text()))==1
    main.orb_action({"action":"finish"})
    assert not main._orb_inspection.active


def test_orb_version_switch_blocks_game_and_loads_mapping(tray_main, monkeypatch, tmp_path):
    import json
    from server import data_store
    from server.inout import midi_out
    main, _, _ = tray_main
    path = tmp_path / "orb_mapping.json"
    oid = "2D:01:02:03:04:05:06:07"
    original = {oid: {"voice": "aespa_1"}}
    path.write_text(json.dumps(original))
    main._orb_inspection.path = path
    monkeypatch.setattr(main, "load_mapping", lambda: data_store.load_mapping(path))
    monkeypatch.setattr(main.hardware_io, "send_line", lambda line: True)
    monkeypatch.setattr(midi_out, "shutdown", lambda: None)
    result = main.orb_action({"action": "select_version", "version": "male"})
    assert result["mode"] == "running" and result["version"] == "male"
    assert main.orb_mapping == result["mapping"]
    assert main.orb_mapping[oid]["voice"].split("_")[0] not in ("aespa", "ive")
    with pytest.raises(RuntimeError):
        main.request_play()
    main.orb_action({"action": "select_version", "version": "original"})
    assert main.orb_mapping == original
    main.orb_action({"action": "finish"})
    assert not main._orb_inspection.active

