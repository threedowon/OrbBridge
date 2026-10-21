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
    import server.inout.midi_out as midi_out

    midi_out.shutdown()
    module = importlib.reload(midi_out)
    return module, fake_mido


def test_event_absolute_notes_short_pulse_and_idempotent_gate(monkeypatch, tmp_path):
    midi, fake = _load_midi(monkeypatch, ["event"])
    mapping = _mapping_file(tmp_path, {"_event": {"port": "event", "channel": 1}})
    try:
        assert midi.ensure_init(mapping)[0]
        port = fake.ports["event"]
        assert [m.control for m in port.messages] == [64, 123, 120]
        port.messages.clear()
        for note in (60, 61, 62, 63, 65, 66):
            assert midi.event_note(note)["ok"]
        assert midi.event_note(64, duration_ms=None)["changed"]
        assert not midi.event_note(64, duration_ms=None)["changed"]
        time.sleep(0.08)
        assert [m.note for m in port.messages if m.type == "note_on"] == [60, 61, 62, 63, 65, 66, 64]
        assert sorted(m.note for m in port.messages if m.type == "note_off") == [60, 61, 62, 63, 65, 66]
        assert midi.event_note(64, duration_ms=None, active=False)["ok"]
        assert port.messages[-1].type == "note_off" and port.messages[-1].note == 64
        assert not midi.event_note(64, duration_ms=None, active=False)["changed"]
    finally:
        midi.shutdown()


def test_event_gate_recovers_after_failed_send_and_reconnect(monkeypatch, tmp_path):
    midi, fake = _load_midi(monkeypatch, ["event"])
    mapping = _mapping_file(tmp_path, {"_event": {"port": "event", "channel": 1}})
    monkeypatch.setattr(midi, "PORTMAPPING_PATH", mapping)
    try:
        midi.ensure_init()
        port = fake.ports["event"]
        send = port.send
        def fail(message):
            raise OSError("disconnected")
        port.send = fail
        assert not midi.event_note(64, duration_ms=None)["ok"]
        port.send = send
        assert midi.event_note(64, duration_ms=None)["ok"]
        assert port.messages[-1].type == "note_on"
        port.send = fail
        assert not midi.event_note(64, duration_ms=None, active=False)["ok"]
        port.send = send
        assert midi.event_note(64, duration_ms=None, active=False)["ok"]
        midi.event_note(64, duration_ms=None)
        midi.reinitialize()
        assert port.messages[-4].type == "note_off"
        assert [m.control for m in port.messages[-3:]] == [64, 123, 120]
        assert midi.event_note(64, duration_ms=None)["changed"]
        midi.shutdown()
        assert port.messages[-1].type == "note_off"
    finally:
        midi.shutdown()


def test_missing_event_port_does_not_block_voice_notes(monkeypatch, tmp_path):
    midi, fake = _load_midi(monkeypatch, ["voice"])
    mapping = _mapping_file(tmp_path, {"_event": {"port": "event", "channel": 1},
                                      "test_1": {"port": "voice", "channel": 1}})
    try:
        assert midi.ensure_init(mapping)[0]
        assert not midi.event_note(60)["ok"]
        assert midi.trigger("test_1")["ok"]
        assert fake.ports["voice"].messages[0].type == "note_on"
    finally:
        midi.shutdown()


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


def test_stop_playback_notes_preserves_retriggered_notes_and_event_gate(monkeypatch, tmp_path):
    midi, fake = _load_midi(monkeypatch, ['voice', 'event'])
    mapping = _mapping_file(tmp_path, {'test_1': {'port': 'voice', 'channel': 1},
                                      '_event': {'port': 'event', 'channel': 1}})
    try:
        assert midi.ensure_init(mapping)[0]
        old = midi.trigger_many([{'voice': 'test_1', 'pitch': 0, 'duration_ms': 60},
                                 {'voice': 'test_1', 'pitch': 2, 'duration_ms': 2000}])
        new = midi.trigger('test_1', 0, duration_ms=2000)
        unrelated = midi.trigger('test_1', 4, duration_ms=2000)
        midi.event_note(64, duration_ms=None)
        port = fake.ports['voice']
        before = len(port.messages)
        midi.stop_notes(old)
        assert [(m.type, m.note) for m in port.messages[before:]] == [('note_off', 38)]
        assert ('voice', 0, 36) in midi._active_notes
        assert ('voice', 0, 40) in midi._active_notes
        assert ('event', 0, 64) in midi._active_notes
        assert ('voice', 0, 38) not in midi._active_notes
        time.sleep(0.09)  # The cancelled note's old timer must not stop its replacement.
        assert len(port.messages) == before + 1
        midi.stop_notes(old)
        assert len(port.messages) == before + 1
        midi.stop_notes([new, unrelated])
        assert [(m.type, m.note) for m in port.messages[-2:]] == [('note_off', 36), ('note_off', 40)]
    finally:
        midi.shutdown()


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


def test_status_poll_does_not_enumerate_devices(monkeypatch, tmp_path):
    midi_out, fake = _load_midi(monkeypatch, ["alpha"])
    mapping = _mapping_file(tmp_path, {"alpha": {"port": "alpha"}})
    calls = []

    def enumerate_devices():
        calls.append(True)
        return ["alpha"]

    monkeypatch.setattr(fake, "get_output_names", enumerate_devices)
    try:
        assert midi_out.ensure_init(mapping)[0]
        count_after_init = len(calls)
        for _ in range(20):
            assert midi_out.runtime_status()["available_ports"] == ["alpha"]
        assert len(calls) == count_after_init
    finally:
        midi_out.shutdown()


def test_catalog_is_loaded_before_beats_and_refreshed_on_reconnect(monkeypatch, tmp_path):
    from server import voice_catalog

    midi_out, fake = _load_midi(monkeypatch, ["alpha"])
    mapping = _mapping_file(tmp_path, {
        "alpha": {"port": "alpha", "members": {"first": 1, "second": 2}},
    })
    monkeypatch.setattr(midi_out, "PORTMAPPING_PATH", mapping)
    labels = {"en": "second"}
    reads = []

    def catalog():
        reads.append(True)
        return {"voices": {"alpha_1": {"labels": dict(labels)}}}

    monkeypatch.setattr(voice_catalog, "load_voice_catalog", catalog)
    try:
        assert midi_out.ensure_init()[0]
        assert len(reads) == 1
        for _ in range(10):
            assert midi_out.trigger("alpha_1")["channel"] == 2
        assert len(reads) == 1
        labels["en"] = "first"
        assert midi_out.reinitialize()[0]
        assert midi_out.trigger("alpha_1")["channel"] == 1
        assert len(reads) == 2
    finally:
        midi_out.shutdown()


def test_multiport_beats_do_not_start_new_threads(monkeypatch, tmp_path):
    midi_out, fake = _load_midi(monkeypatch, ["alpha", "beta"])
    mapping = _mapping_file(tmp_path, {
        name: {"port": name} for name in ["alpha", "beta"]
    })
    started = []
    original_start = threading.Thread.start

    def start(thread):
        started.append(thread.name)
        return original_start(thread)

    try:
        assert midi_out.ensure_init(mapping)[0]
        monkeypatch.setattr(threading.Thread, "start", start)
        for pitch in range(3):
            results = midi_out.trigger_many([
                {"voice": "alpha_1", "pitch": pitch},
                {"voice": "beta_1", "pitch": pitch},
            ])
            assert all(result["ok"] for result in results)
        assert started == []
    finally:
        midi_out.shutdown()


def test_failed_init_does_not_rescan_on_every_note(monkeypatch, tmp_path):
    midi_out, fake = _load_midi(monkeypatch, [])
    mapping = _mapping_file(tmp_path, {"alpha": {"port": "alpha"}})
    monkeypatch.setattr(midi_out, "PORTMAPPING_PATH", mapping)
    scans = []

    def enumerate_devices():
        scans.append(True)
        return list(fake.names)

    monkeypatch.setattr(fake, "get_output_names", enumerate_devices)
    try:
        assert not midi_out.ensure_init()[0]
        for _ in range(5):
            assert not midi_out.trigger("alpha_1")["ok"]
        assert len(scans) == 1
        fake.names = ["alpha"]
        fake.ports["alpha"] = FakePort("alpha")
        assert midi_out.reinitialize()[0]
        assert midi_out.trigger("alpha_1")["ok"]
        assert len(scans) == 2
    finally:
        midi_out.shutdown()


def test_shutdown_waits_for_inflight_batch(monkeypatch, tmp_path):
    midi_out, fake = _load_midi(monkeypatch, ["alpha", "beta"])
    mapping = _mapping_file(tmp_path, {name: {"port": name} for name in fake.names})
    sending = threading.Event()
    release = threading.Event()
    closing = threading.Event()
    closed = threading.Event()
    port = fake.ports["alpha"]
    send = port.send
    failures = []

    def blocked_send(message):
        if message.type == "note_on":
            sending.set()
            release.wait(2)
        if port.closed:
            failures.append("send after close")
        send(message)

    def shutdown():
        closing.set()
        midi_out.shutdown()
        closed.set()

    monkeypatch.setattr(port, "send", blocked_send)
    sender = threading.Thread(target=lambda: midi_out.trigger_many([
        {"voice": "alpha_1"}, {"voice": "beta_1"},
    ]))
    closer = threading.Thread(target=shutdown)
    try:
        assert midi_out.ensure_init(mapping)[0]
        sender.start()
        assert sending.wait(1)
        closer.start()
        assert closing.wait(1)
        assert not closed.wait(0.05)
        release.set()
        sender.join(1)
        closer.join(1)
        assert not sender.is_alive() and not closer.is_alive()
        assert closed.is_set() and port.closed
        assert failures == []
    finally:
        release.set()
        if sender.ident is not None:
            sender.join(2)
        if closer.ident is not None:
            closer.join(2)
        midi_out.shutdown()
