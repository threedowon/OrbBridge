import importlib
import os
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"


class FakeOscClient:
    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.messages = []
        self.__class__.instances.append(self)

    def send_message(self, address, args):
        self.messages.append((address, list(args)))


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
        return importlib.import_module("main")
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

        assert completed["game"]["phase"] == "FINISHED"
        assert completed["game"]["finish_reason"] == "complete"
        assert len(midi_calls) == 8

        addresses = [address for address, _args in main.osc.messages]
        final_added_index = max(
            i for i, value in enumerate(addresses) if value == "/orb/added"
        )
        game_end_index = max(
            i for i, value in enumerate(addresses) if value == "/game/end"
        )
        assert final_added_index < game_end_index

        resetting = main.reset_game()
        assert resetting["game"]["phase"] == "RESETTING"

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
