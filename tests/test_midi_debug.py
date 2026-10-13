import importlib
import time

import pytest


class FakeMidiOut:
    def __init__(self):
        self.calls = []

    def ensure_init(self):
        return True, ""

    def runtime_status(self):
        return {
            "ready": True,
            "error": "",
            "required_ports": ["aespa"],
            "available_ports": ["aespa"],
            "opened_ports": ["aespa"],
        }

    def reinitialize(self):
        return True, ""

    def shutdown(self):
        return None

    def trigger(self, voice, pitch, *, duration_ms):
        result = {"ok": True, "voice": voice, "pitch": pitch}
        self.calls.append(result)
        return result

    def trigger_many(self, notes):
        results = [
            {"ok": True, "voice": note["voice"], "pitch": note["pitch"]}
            for note in notes
        ]
        self.calls.extend(results)
        return results


def test_voice_debug_job_runs_all_eight_pitches(monkeypatch):
    import tools.midi_debug as midi_debug

    midi_debug = importlib.reload(midi_debug)
    fake = FakeMidiOut()
    monkeypatch.setattr(midi_debug, "midi_out", fake)
    monkeypatch.setattr(midi_debug, "list_catalog_voices", lambda: ["aespa_1"])
    monkeypatch.setattr(midi_debug, "load_voice_catalog", lambda: {"voices": {"aespa_1": {}}})

    started = midi_debug.start_test(
        {
            "mode": "voice",
            "voice": "aespa_1",
            "duration_ms": 30,
            "delay_ms": 30,
        }
    )
    assert started["running"] is True

    deadline = time.monotonic() + 1
    while midi_debug.status_payload()["job"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    finished = midi_debug.status_payload()["job"]
    assert finished["running"] is False
    assert finished["completed"] == 8
    assert [call["pitch"] for call in fake.calls] == list(range(8))


@pytest.fixture
def debug_runner(monkeypatch):
    import tools.midi_debug as midi_debug

    midi_debug = importlib.reload(midi_debug)
    fake = FakeMidiOut()
    monkeypatch.setattr(midi_debug, "midi_out", fake)
    monkeypatch.setattr(midi_debug, "list_catalog_voices", lambda: [])

    class ImmediateThread:
        def __init__(self, *, target, args, **kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    class TestStop:
        def __init__(self):
            self.stopped = False
            self.waits = []

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def clear(self):
            self.stopped = False

        def wait(self, seconds):
            self.waits.append(seconds)
            return self.stopped

    monkeypatch.setattr(midi_debug.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(midi_debug, "_job_stop", TestStop())
    return midi_debug, fake


def test_status_includes_unregistered_catalog_members_in_numeric_order(debug_runner, monkeypatch):
    debug, _ = debug_runner
    monkeypatch.setattr(debug, "list_catalog_voices", lambda: ["bts_2", "custom_1"])
    monkeypatch.setattr(debug, "load_voice_catalog", lambda: {"voices": {
        "bts_10": {}, "aespa_2": {}, "bts_2": {}, "aespa_1": {},
    }})

    assert debug.status_payload()["voices"] == [
        "aespa_1", "aespa_2", "bts_2", "bts_10", "custom_1",
    ]


def test_all_voices_visits_every_member_and_pitch_sequentially(debug_runner, monkeypatch):
    debug, fake = debug_runner
    monkeypatch.setattr(debug, "load_voice_catalog", lambda: {"voices": {
        "bts_1": {}, "aespa_2": {}, "aespa_1": {},
    }})
    monkeypatch.setattr(fake, "trigger_many", lambda _: pytest.fail("batch output is not a sweep"))

    finished = debug.start_test({
        "mode": "all_voices", "voice": "bts_1", "pitch": 7,
        "duration_ms": 180, "delay_ms": 30,
    })

    assert [(call["voice"], call["pitch"]) for call in fake.calls] == [
        (voice, pitch) for voice in ["aespa_1", "aespa_2", "bts_1"] for pitch in range(8)
    ]
    assert finished["completed"] == finished["total"] == 24
    assert finished["running"] is False
    assert finished["stopped"] is False
    assert finished["failed"] == 0
    assert (finished["current_voice"], finished["current_pitch"]) == ("bts_1", 7)
    assert debug._job_stop.waits == [0.18] * 24


def test_selected_catalog_voice_works_without_orb_registration(debug_runner, monkeypatch):
    debug, fake = debug_runner
    monkeypatch.setattr(debug, "load_voice_catalog", lambda: {"voices": {"riize_6": {}}})

    finished = debug.start_test({"mode": "voice", "voice": "riize_6"})

    assert [call["pitch"] for call in fake.calls] == list(range(8))
    assert finished["completed"] == finished["total"] == 8
    assert debug._job_stop.waits == [0.22] * 7 + [0.18]


def test_stop_prevents_remaining_notes_and_new_job_resets_stop(debug_runner, monkeypatch):
    debug, fake = debug_runner
    monkeypatch.setattr(debug, "load_voice_catalog", lambda: {"voices": {"bts_1": {}, "bts_2": {}}})
    trigger = fake.trigger

    def stop_on_ninth_note(*args, **kwargs):
        result = trigger(*args, **kwargs)
        if len(fake.calls) == 9:
            debug.stop_test()
        return result

    monkeypatch.setattr(fake, "trigger", stop_on_ninth_note)
    finished = debug.start_test({"mode": "all_voices"})
    assert finished["completed"] == 9
    assert finished["total"] == 16
    assert finished["stopped"] is True
    assert finished["running"] is False

    restarted = debug.start_test({"mode": "note", "voice": "bts_2", "pitch": 4})
    assert restarted["stopped"] is False
    assert restarted["completed"] == restarted["total"] == 1
    assert fake.calls[-1] == {"ok": True, "voice": "bts_2", "pitch": 4}


def test_sweep_keeps_failure_count_after_result_history_rolls_over(debug_runner, monkeypatch):
    debug, fake = debug_runner
    monkeypatch.setattr(debug, "load_voice_catalog", lambda: {"voices": {
        f"group_{member}": {} for member in range(1, 11)
    }})
    trigger = fake.trigger

    def fail_first_note(*args, **kwargs):
        result = trigger(*args, **kwargs)
        if len(fake.calls) == 1:
            result.update(ok=False, error="port not open")
        return result

    monkeypatch.setattr(fake, "trigger", fail_first_note)
    finished = debug.start_test({"mode": "all_voices"})
    assert finished["completed"] == finished["total"] == 80
    assert finished["failed"] == 1
    assert len(finished["results"]) == 64
    assert all(result["ok"] for result in finished["results"])


def test_sweep_rejects_empty_catalog(debug_runner, monkeypatch):
    debug, fake = debug_runner
    monkeypatch.setattr(debug, "load_voice_catalog", lambda: {"voices": {}})
    with pytest.raises(ValueError, match="voice"):
        debug.start_test({"mode": "all_voices"})
    assert fake.calls == []
