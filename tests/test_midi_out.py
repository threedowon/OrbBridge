import importlib
import json
import sys
import threading
import time
import types

class FakeMessage:
    def __init__(self, message_type, **values):
        self.type = message_type
        self.__dict__.update(values)


class FakePort:
    def __init__(self, name, barrier=None):
        self.name = name
        self.barrier = barrier
        self.messages = []
        self.closed = False

    def send(self, message):
        if self.barrier is not None and message.type == "note_on":
            self.barrier.wait(timeout=1)
        self.messages.append(message)

    def close(self):
        self.closed = True


class FakeMido(types.ModuleType):
    def __init__(self, names, barrier=None):
        super().__init__("mido")
        self.names = list(names)
        self.ports = {name: FakePort(name, barrier) for name in names}

    def get_output_names(self):
        return list(self.names)

    def open_output(self, name):
        return self.ports[name]

    @staticmethod
    def Message(message_type, **values):
        return FakeMessage(message_type, **values)


def _mapping_file(tmp_path, groups):
    path = tmp_path / "portmapping.json"
    path.write_text(json.dumps(groups), encoding="utf-8")
    return path


def _load_midi(monkeypatch, names, barrier=None):
    fake_mido = FakeMido(names, barrier)
    monkeypatch.setitem(sys.modules, "mido", fake_mido)
    import app.midi_out as midi_out

    midi_out.shutdown()
    module = importlib.reload(midi_out)
    return module, fake_mido


def test_retrigger_invalidates_the_previous_note_off(monkeypatch, tmp_path):
    midi_out, fake = _load_midi(monkeypatch, ["aespa"])
    mapping = _mapping_file(
        tmp_path,
        {"aespa": {"port": "aespa", "members": {"member": 1}}},
    )

    try:
        assert midi_out.ensure_init(mapping)[0] is True
        assert midi_out.trigger("aespa_1", 0, duration_ms=60)["ok"] is True
        time.sleep(0.01)
        assert midi_out.trigger("aespa_1", 0, duration_ms=60)["ok"] is True
        time.sleep(0.09)

        assert [message.type for message in fake.ports["aespa"].messages] == [
            "note_on",
            "note_off",
            "note_on",
            "note_off",
        ]
    finally:
        midi_out.shutdown()


def test_trigger_many_sends_different_ports_concurrently(monkeypatch, tmp_path):
    barrier = threading.Barrier(2)
    midi_out, fake = _load_midi(monkeypatch, ["alpha", "beta"], barrier)
    mapping = _mapping_file(
        tmp_path,
        {
            "alpha": {"port": "alpha", "members": {"one": 1}},
            "beta": {"port": "beta", "members": {"one": 1}},
        },
    )

    try:
        assert midi_out.ensure_init(mapping)[0] is True
        results = midi_out.trigger_many(
            [
                {"voice": "alpha_1", "pitch": 2, "duration_ms": 40},
                {"voice": "beta_1", "pitch": 5, "duration_ms": 40},
            ]
        )
        assert all(result["ok"] for result in results)
        assert fake.ports["alpha"].messages[0].type == "note_on"
        assert fake.ports["beta"].messages[0].type == "note_on"
    finally:
        midi_out.shutdown()


def test_invalid_note_does_not_block_valid_notes(monkeypatch, tmp_path):
    midi_out, _fake = _load_midi(monkeypatch, ["aespa"])
    mapping = _mapping_file(
        tmp_path,
        {"aespa": {"port": "aespa", "members": {"member": 1}}},
    )

    try:
        assert midi_out.ensure_init(mapping)[0] is True
        results = midi_out.trigger_many(
            [
                {"voice": "missing_1", "pitch": 0},
                {"voice": "aespa_1", "pitch": 7},
            ]
        )
        assert results[0]["ok"] is False
        assert results[1]["ok"] is True
        assert results[1]["note"] == 43
    finally:
        midi_out.shutdown()
