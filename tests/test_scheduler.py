import json
import threading
import time

import pytest

from server.game.scheduler import (
    PLAYBACK_LEAD_S,
    Melody,
    PlaybackMode,
    PlaybackScheduler,
    build_plan,
    parse_melody,
)


def melody_raw():
    return {
        "bpm": 120,
        "steps": [
            {"note": "C4", "duration": 0.5},
            {"note": "REST", "duration": 1},
            {"note": "G4", "duration": 1.5},
        ],
    }


def test_display_snapshot_excludes_cancelled_run_before_worker_exits():
    entered, release = threading.Event(), threading.Event()

    def send(notes):
        entered.set()
        release.wait(2)
        return []

    scheduler = PlaybackScheduler(send_notes=send, publish_event=lambda event: None)
    try:
        scheduler.start("listen", parse_melody(melody_raw()))
        assert entered.wait(1)
        assert scheduler.display_snapshot()["active"]
        assert scheduler.display_snapshot()["mode"] == "listen"
        scheduler.cancel()
        assert not scheduler.display_snapshot()["active"]
        assert "timing" not in scheduler.display_snapshot()
    finally:
        release.set()
        scheduler.close()


def cells():
    return {
        "1:1": {
            "position": 1,
            "cell": 1,
            "note": "C",
            "voice": "aespa_1",
        },
        "1:13": {
            "position": 2,
            "cell": 13,
            "note": "G",
            "voice": "aespa_1",
        },
        "2:5": {
            "position": 3,
            "cell": 5,
            "note": "G",
            "voice": "bts_1",
        },
    }


def test_parse_melody_normalizes_pitch_rest_and_duration():
    melody = parse_melody(melody_raw())

    assert melody.bpm == 120
    assert [step.pitch for step in melody.steps] == [0, None, 4]
    assert [step.duration_beats for step in melody.steps] == [0.5, 1, 1.5]


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"bpm": 0, "steps": [{"note": "C4"}]}, "bpm"),
        ({"bpm": 120, "steps": []}, "non-empty"),
        ({"bpm": 120, "steps": [{"note": "F#4"}]}, "unsupported"),
        (
            {"bpm": 120, "steps": [{"note": "C4", "duration": -1}]},
            "duration",
        ),
    ],
)
def test_parse_melody_rejects_invalid_input(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_melody(raw)


def test_listen_starts_immediately_and_uses_preview_route():
    plan = build_plan(PlaybackMode.LISTEN, parse_melody(melody_raw()))

    assert plan.cues[0].offset_s == 0
    assert plan.cues[0].notes == (
        {"voice": "_listen", "pitch": 0, "duration_ms": 230, "velocity": 127},
    )
    assert plan.cues[1].notes == ()
    assert plan.cues[2].offset_s == pytest.approx(0.75)


def test_play_uses_placed_column_voice_and_physical_pitch():
    plan = build_plan("play", parse_melody(melody_raw()), cells())

    assert plan.cues[0].offset_s == PLAYBACK_LEAD_S
    assert plan.cues[0].notes[0]["voice"] == "aespa_1"
    assert plan.cues[0].notes[0]["pitch"] == 0
    assert plan.cues[1].notes[0]["pitch"] == 4
    assert plan.cues[2].notes[0]["voice"] == "bts_1"


def test_dfesta_deduplicates_voices_and_uses_target_pitch():
    plan = build_plan("dfesta", parse_melody(melody_raw()), cells())

    assert plan.cues[0].notes == (
        {"voice": "aespa_1", "pitch": 0, "duration_ms": 250, "velocity": 127},
        {"voice": "bts_1", "pitch": 0, "duration_ms": 250, "velocity": 127},
    )
    assert plan.cues[1].notes == ()
    assert {note["pitch"] for note in plan.cues[2].notes} == {4}


def test_plan_uses_absolute_offsets_from_all_prior_durations():
    plan = build_plan("play", parse_melody(melody_raw()), cells())

    assert [cue.offset_s for cue in plan.cues] == pytest.approx(
        [0.25, 0.5, 1.0]
    )
    assert plan.duration_s == pytest.approx(1.75)


def test_scheduler_publishes_start_beats_midi_and_end():
    events = []
    batches = []
    ended = threading.Event()

    def publish(event):
        events.append(event)
        if event["type"] == "end":
            ended.set()

    scheduler = PlaybackScheduler(send_notes=batches.append, publish_event=publish)
    fast = Melody(bpm=6000, steps=parse_melody(melody_raw()).steps)
    try:
        result = scheduler.start("listen", fast)
        assert ended.wait(1)
    finally:
        scheduler.close()

    assert result == {"run_id": 1, "mode": "listen", "step_count": 3}
    assert [event["type"] for event in events] == [
        "start",
        "beat",
        "beat",
        "beat",
        "end",
    ]
    assert [batch[0]["pitch"] for batch in batches] == [0, 4]


def test_new_start_cancels_the_previous_run():
    events = []
    second_ended = threading.Event()

    def publish(event):
        events.append(event)
        if event["type"] == "end" and event["run_id"] == 2:
            second_ended.set()

    scheduler = PlaybackScheduler(send_notes=lambda notes: None, publish_event=publish)
    slow = parse_melody({"bpm": 60, "steps": [{"note": "C4"}]})
    fast = parse_melody({"bpm": 6000, "steps": [{"note": "D4"}]})
    try:
        scheduler.start("play", slow, cells())
        scheduler.start("listen", fast)
        assert second_ended.wait(1)
    finally:
        scheduler.close()

    first_end = next(
        event for event in events
        if event["type"] == "end" and event["run_id"] == 1
    )
    assert first_end["cancelled"] is True


def test_close_is_idempotent_and_rejects_new_playback():
    scheduler = PlaybackScheduler(
        send_notes=lambda notes: None,
        publish_event=lambda event: None,
    )
    scheduler.close()
    scheduler.close()

    with pytest.raises(RuntimeError, match="closed"):
        scheduler.start("listen", parse_melody(melody_raw()))


@pytest.mark.parametrize("blocked_event", ["start", "beat"])
def test_slow_display_cannot_hold_up_midi(blocked_event):
    entered = threading.Event()
    release = threading.Event()
    sent = threading.Event()
    batches = []

    def publish(event):
        if event["type"] == blocked_event:
            entered.set()
            release.wait(2)

    def send(notes):
        batches.append(notes)
        if len(batches) == 3:
            sent.set()

    scheduler = PlaybackScheduler(send_notes=send, publish_event=publish)
    melody = parse_melody({"bpm": 6000, "steps": ["C4", "D4", "E4"]})
    try:
        scheduler.start("listen", melody)
        assert entered.wait(1)
        assert sent.wait(0.5), "display callback blocked MIDI playback"
    finally:
        release.set()
        scheduler.close()


def test_wait_rechecks_deadline_after_early_wakeup(monkeypatch):
    now = [0.0]
    waits = []

    class EarlyWake:
        def is_set(self):
            return False

        def wait(self, timeout):
            waits.append(timeout)
            now[0] += max(timeout / 2, 0.001)
            return False

    def sleep(duration):
        now[0] += duration

    monkeypatch.setattr("server.game.scheduler.time.sleep", sleep)
    scheduler = PlaybackScheduler(
        send_notes=lambda notes: None, publish_event=lambda event: None,
        clock=lambda: now[0],
    )
    try:
        # The mock eventually reaches the 20ms precision window despite early wakes.
        assert scheduler._wait_until(0.05, EarlyWake())
        assert now[0] >= 0.05
        assert len(waits) > 1
        cancelled = threading.Event()
        cancelled.set()
        assert not scheduler._wait_until(1.0, cancelled)
    finally:
        scheduler.close()


def test_timing_distinguishes_dispatch_and_driver_delay():
    ended = threading.Event()

    def send(notes):
        started = time.perf_counter()
        time.sleep(0.025)
        return [{"ok": True, "note_on_started_s": started,
                 "note_on_completed_s": time.perf_counter()}]

    scheduler = PlaybackScheduler(
        send_notes=send,
        publish_event=lambda event: ended.set() if event["type"] == "end" else None,
    )
    try:
        scheduler.start("listen", parse_melody({"bpm": 6000, "steps": ["C4"]}))
        assert ended.wait(1)
        timing = scheduler.snapshot()["timing"]
        assert timing["clock"] == "perf_counter"
        sample = timing["samples"][0]
        assert sample["sent_count"] == 1
        assert sample["error_count"] == 0
        assert sample["driver_send_ms"] >= 20
        assert sample["send_ms"] >= 20
        assert sample["note_on_completed_late_ms"] >= sample["note_on_late_ms"] + 20
        # A second run owns a new diagnostic record, without old notes mixed in.
        ended.clear()
        scheduler.start("listen", parse_melody({"bpm": 6000, "steps": ["REST"]}))
        assert ended.wait(1)
        timing = scheduler.snapshot()["timing"]
        assert timing["run_id"] == 2
        assert len(timing["samples"]) == 1
        assert timing["samples"][0]["note_count"] == 0
    finally:
        scheduler.close()
