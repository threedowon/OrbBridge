import importlib
import os
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"


class FakeOscClient:
    """Record OSC output without opening UDP during integration tests."""

    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.messages = []
        self.__class__.instances.append(self)

    def send_message(self, address, args):
        self.messages.append((address, list(args)))


class FakeScheduler:
    """Expose the production Scheduler contract without starting worker threads."""

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
    """Record direct browser broadcasts without opening a TCP listener."""

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

    udp_client_module = types.ModuleType("pythonosc.udp_client")
    udp_client_module.SimpleUDPClient = FakeOscClient
    pythonosc_module = types.ModuleType("pythonosc")
    pythonosc_module.udp_client = udp_client_module

    sys.modules["serial"] = serial_module
    sys.modules["pythonosc"] = pythonosc_module
    sys.modules["pythonosc.udp_client"] = udp_client_module
    sys.path.insert(0, str(SERVER))
    sys.modules.pop("main", None)

    previous_cwd = Path.cwd()
    try:
        module = importlib.import_module("main")
        module._playback_scheduler = FakeScheduler()
        return module
    finally:
        os.chdir(previous_cwd)


def test_main_routes_web_event_through_runtime_snapshot_and_midi():
    FakeOscClient.instances.clear()
    main = _load_main_module()
    midi_calls = []
    midi_module = types.ModuleType("midi_out")
    midi_module.cell_to_pitch = lambda cell: (int(cell) - 1) % 8
    midi_module.trigger = lambda voice, pitch, **kwargs: midi_calls.append(
        (voice, pitch, kwargs)
    ) or {"ok": True}
    sys.modules["midi_out"] = midi_module

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
        assert started["game"]["phase"] == "RUNNING"
        assert main._playback_scheduler.starts[0]["mode"] == "listen"

        orb_id, slave, cell, voice = placements[0]
        placed = main.handle_event(
            {"type": "added", "slave": slave, "cell": cell, "id": orb_id}
        )

        assert placed["result"] == "correct"
        assert placed["game"]["phase"] == "RUNNING"
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
        assert status["game_active"] is True

        for orb_id, slave, cell, _voice in placements[1:]:
            completed = main.handle_event(
                {"type": "added", "slave": slave, "cell": cell, "id": orb_id}
            )

        assert completed["game"]["phase"] == "RUNNING"
        assert completed["game"]["solved"] is True
        assert completed["game"]["completion"]["dispensa_presses_remaining"] == 1
        assert "/game/end" not in [
            address for address, _args in main.osc.messages
        ]

        finished = main.request_dispensa()

        assert finished["counted"] is True
        assert finished["game"]["phase"] == "RESETTING"
        assert finished["game"]["finish_reason"] == "complete"
        assert main.get_game_state()["phase"] == "RESETTING"
        assert len(midi_calls) == 8

        addresses = [address for address, _args in main.osc.messages]
        final_added_index = max(
            i for i, value in enumerate(addresses) if value == "/orb/added"
        )
        game_end_index = max(
            i for i, value in enumerate(addresses) if value == "/game/end"
        )
        game_reset_index = max(
            i for i, value in enumerate(addresses) if value == "/game/reset"
        )
        dispensa_index = max(
            i for i, value in enumerate(addresses) if value == "/game/dispensa"
        )
        assert final_added_index < dispensa_index < game_end_index < game_reset_index

        for orb_id, slave, cell, _voice in placements:
            removed = main.handle_event(
                {"type": "removed", "slave": slave, "cell": cell, "id": orb_id}
            )
        assert removed["changed"] is True
        assert removed["game"]["phase"] == "READY"
        assert main.get_cells() == {}

        addresses = [address for address, _args in main.osc.messages]
        removed_index = max(i for i, value in enumerate(addresses) if value == "/orb/removed")
        reset_complete_index = max(
            i for i, value in enumerate(addresses) if value == "/game/reset_complete"
        )
        assert removed_index < reset_complete_index
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(SERVER)]
        for name in ("main", "serial", "pythonosc", "pythonosc.udp_client", "midi_out"):
            sys.modules.pop(name, None)


def test_manual_finish_stays_finished_until_reset_is_requested():
    FakeOscClient.instances.clear()
    main = _load_main_module()

    try:
        main.start_game()

        finished = main.end_game("manual")
        assert finished["game"]["phase"] == "FINISHED"
        assert finished["game"]["finish_reason"] == "manual"

        addresses = [address for address, _args in main.osc.messages]
        assert "/game/end" in addresses
        assert "/game/reset" not in addresses

        reset = main.reset_game()
        assert reset["game"]["phase"] == "READY"
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(SERVER)]
        for name in ("main", "serial", "pythonosc", "pythonosc.udp_client"):
            sys.modules.pop(name, None)



def test_listen_play_and_dispensa_use_python_scheduler():
    FakeOscClient.instances.clear()
    main = _load_main_module()

    try:
        main.start_game()
        play = main.request_play()
        dispensa = main.request_dispensa()
        listen = main.request_listen()

        assert play["ok"] is True
        assert dispensa["ok"] is True
        assert dispensa["counted"] is False
        assert listen["ok"] is True
        assert [
            call["mode"] for call in main._playback_scheduler.starts
        ] == ["listen", "play", "dispensa", "listen"]

        addresses = [address for address, _args in main.osc.messages]
        assert "/game/listen" in addresses
        assert "/game/play" in addresses
        assert "/game/dispensa" in addresses
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(SERVER)]
        for name in ("main", "serial", "pythonosc", "pythonosc.udp_client"):
            sys.modules.pop(name, None)


def test_stage_rules_expose_120_second_total_and_30_second_idle_timers():
    FakeOscClient.instances.clear()
    main = _load_main_module()

    try:
        started = main.start_game()
        timers = started["game"]["timers"]

        assert timers["total_limit_s"] == 120
        assert timers["idle_limit_s"] == 30
        assert 119 <= timers["remaining_total_s"] <= 120
        assert 29 <= timers["remaining_idle_s"] <= 30
        assert started["game"]["completion"]["dispensa_presses_required"] == 1
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(SERVER)]
        for name in ("main", "serial", "pythonosc", "pythonosc.udp_client"):
            sys.modules.pop(name, None)


def test_playback_requests_are_ignored_outside_active_game_phases():
    FakeOscClient.instances.clear()
    main = _load_main_module()

    try:
        assert main.get_game_state()["phase"] == "READY"

        assert main.request_listen()["ignored"] is True
        assert main.request_play()["ignored"] is True
        assert main.request_dispensa()["ignored"] is True
        assert main._playback_scheduler.starts == []
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(SERVER)]
        for name in ("main", "serial", "pythonosc", "pythonosc.udp_client"):
            sys.modules.pop(name, None)


def test_finish_phase_cancels_active_scheduler_without_blocking_runtime():
    FakeOscClient.instances.clear()
    main = _load_main_module()

    try:
        main.start_game()
        before = main._playback_scheduler.cancel_count

        finished = main.end_game("manual")

        assert finished["game"]["phase"] == "FINISHED"
        assert main._playback_scheduler.cancel_count == before + 1
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(SERVER)]
        for name in ("main", "serial", "pythonosc", "pythonosc.udp_client"):
            sys.modules.pop(name, None)


def test_main_fans_out_events_and_builds_complete_web_snapshot():
    """M4 keeps OSC output while giving browsers one direct recovery source."""

    FakeOscClient.instances.clear()
    main = _load_main_module()
    websocket = FakeWebSocketService()
    main._websocket_service = websocket

    try:
        main._osc_send("/game/example", [7, "value"])

        assert main.osc.messages[-1] == ("/game/example", [7, "value"])
        assert websocket.messages == [("/game/example", [7, "value"])]

        snapshot = main.get_web_snapshot()
        assert snapshot["schema_version"] == 1
        assert snapshot["game"]["phase"] == "READY"
        assert snapshot["status"]["game"] == snapshot["game"]
        assert snapshot["status"]["cell_count"] == snapshot["game"]["cell_count"]
        assert snapshot["cells"] == {}
        assert snapshot["events"] == []
    finally:
        main._websocket_service = None
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(SERVER)]
        for name in ("main", "serial", "pythonosc", "pythonosc.udp_client"):
            sys.modules.pop(name, None)
