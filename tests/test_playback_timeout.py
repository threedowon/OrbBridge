"""TIME OVER drains the accepted sequence, but cannot start another one."""
import threading

import pytest

from server.game.game_engine import GameEngine
from server.game.scheduler import parse_melody
from server.inout.hint_led import HintLedBridge
from test_game_events import event_main
from test_main_runtime_integration import tray_main


@pytest.mark.parametrize("mode", ["play", "listen", "dfesta"])
def test_time_over_waits_for_last_note_and_led_then_announces_end(event_main, monkeypatch, mode):
    main, clock, writes, events, notes, held = event_main
    monkeypatch.setattr(main, "_new_game_engine", lambda: GameEngine(
        {"1": "C", "2": "D"}, time_over_s=1, idle_s=None, clock=lambda: clock[0]))
    main.set_tray({"present": True})
    main._update_matrix_display()
    runtime = main.get_game_runtime()
    runtime.add_orb(orb_id="a", slave=1, cell=1, voice="test_1")
    runtime.add_orb(orb_id="b", slave=1, cell=10, voice="test_2")
    monkeypatch.setattr(main, "playback_melody", parse_melody({
        "bpm": 600, "steps": [{"note": "C4"}, {"note": "D4"}]}))
    first, release, ended = threading.Event(), threading.Event(), threading.Event()
    sent, cues, lights = [], [], []
    monkeypatch.setattr(main, "_hint_led", HintLedBridge(lambda c: lights.append(c) or True))
    def send(batch):
        if not sent:
            first.set()
            assert release.wait(3)
        sent.extend(batch)
    publish = main._scheduler_publish_event
    def on_event(event):
        publish(event)
        cues.append(event)
        if event["type"] == "end":
            ended.set()
    monkeypatch.setattr(main, "_scheduler_send_notes", send)
    monkeypatch.setattr(main, "_scheduler_publish_event", on_event)
    main._playback_scheduler = None
    try:
        assert getattr(main, "request_" + mode)()["ok"]
        assert first.wait(2)
        clock[0] = 1
        runtime.publish_state_change(runtime.tick())
        main._update_matrix_display()
        events.flush()
        assert main.get_game_state()["state"] == "RUNNING"
        assert main.get_game_state()["timers"]["remaining_time_over_s"] == 0
        assert writes[-1].get("text") != "TIME OVER"
        assert (65, True) not in notes
        assert not held
        run = main._playback_scheduler.display_snapshot()["run_id"]
        for request in (main.request_play, main.request_listen, main.request_dfesta):
            assert request()["ignored"]
        assert main._playback_scheduler.display_snapshot()["run_id"] == run
        release.set()
        assert ended.wait(2)
        main._update_matrix_display()
        events.flush()
        assert [cue["step"] for cue in cues if cue["type"] == "beat"] == [0, 1]
        assert cues[-1]["cancelled"] is False
        assert len(sent) >= 2
        prefix = {"play": "P", "listen": "H1,", "dfesta": "Q"}[mode]
        assert len([c for c in lights if c.startswith(prefix)]) == 2
        assert main.get_game_state()["end_reason"] == "time_over"
        assert writes[-1]["text"] == "TIME OVER"
        assert notes.count((65, True)) == 1
        assert not held  # No DFESTA-ready note is reactivated after the final note.
    finally:
        release.set()
        main._playback_scheduler.close()


def test_old_end_cannot_release_replacement_sequence_deadline(event_main):
    main, clock, _, events, notes, _ = event_main
    main.set_tray({"present": True})
    clock[0] = 3
    runtime = main.get_game_runtime()
    runtime.barrier()
    main._update_matrix_display()
    runtime.add_orb(orb_id="a", slave=1, cell=1)
    clock[0] = 30
    runtime.record_activity()
    clock[0] = 50
    # Refresh idle before expiry; the replacement began before time ran out.
    runtime.record_activity()
    main._scheduler_before_start({"run_id": 2, "mode": "play"})
    main._scheduler_before_start({"run_id": 3, "mode": "listen"})
    clock[0] = 70
    runtime.publish_state_change(runtime.tick())
    assert main.get_game_state()["state"] == "RUNNING"
    main._scheduler_publish_event({"type": "end", "run_id": 2, "mode": "play", "cancelled": True})
    assert main.get_game_state()["state"] == "RUNNING"
    main._scheduler_publish_event({"type": "end", "run_id": 3, "mode": "listen", "cancelled": False})
    events.flush()
    assert main.get_game_state()["end_reason"] == "time_over"
    assert notes.count((65, True)) == 1


@pytest.mark.parametrize("action", ["reset", "wrong_answer"])
def test_deferred_timeout_still_allows_explicit_interruption(event_main, monkeypatch, action):
    main, clock, _, events, _, _ = event_main
    monkeypatch.setattr(main, "_new_game_engine", lambda: GameEngine(
        {"1": "C"}, time_over_s=1, idle_s=None, clock=lambda: clock[0]))
    main.set_tray({"present": True})
    main._update_matrix_display()
    runtime = main.get_game_runtime()
    runtime.add_orb(orb_id="a", slave=1, cell=1)
    playback = main.request_dfesta()["playback"]
    main._scheduler_before_start(playback)
    clock[0] = 1
    runtime.publish_state_change(runtime.tick())
    assert main.get_game_state()["state"] == "RUNNING"
    cancellations = main._playback_scheduler.cancel_count
    if action == "reset":
        main.reset_game()
    else:
        runtime.remove_orb(slave=1, cell=1)
    assert main._playback_scheduler.cancel_count > cancellations
    main._scheduler_publish_event({"type": "end", **playback, "cancelled": True})
    events.flush()
    assert main.get_game_state()["end_reason"] == (
        "manual" if action == "reset" else "time_over")
    assert not main._hold_time_over_for_playback()
