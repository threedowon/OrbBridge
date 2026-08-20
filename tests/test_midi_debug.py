import importlib
import time


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
