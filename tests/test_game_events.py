from copy import deepcopy
import threading

import pytest

from server.game.game_engine import GameEngine
from server.game.game_runtime import GameRuntime
from server.inout.game_events import GameEvents
from test_main_runtime_integration import tray_main


@pytest.fixture
def event_main(tray_main, monkeypatch):
    main, clock, writes = tray_main
    notes, held = [], set()
    def send(note, **kwargs):
        if kwargs.get("duration_ms") is None:
            active = kwargs.get("active", True)
            if active == (note in held):
                return {"ok": True}
            (held.add if active else held.discard)(note)
        notes.append((note, kwargs.get("active", True)))
        return {"ok": True}
    events = GameEvents({}, send_note=send)
    main._game_events = events
    main._matrix_display._on_screen_change = events.screen_changed
    monkeypatch.setattr(main, "_new_game_engine", lambda: GameEngine(
        {"1": "C"}, time_over_s=60, idle_s=30, initial_hint_delay_s=3,
        initial_timer_delay_s=10, clock=lambda: clock[0]))
    try:
        yield main, clock, writes, events, notes, held
    finally:
        events.close()
        main._game_events = None


def test_loading_is_once_per_game_and_blocks_buttons(event_main):
    main, clock, writes, events, notes, _ = event_main
    main._update_matrix_display()
    main._update_matrix_display()
    main.set_tray({"present": True})
    main._update_matrix_display()
    assert writes[-1]["text"] == "LOADING..."
    assert not main._playback_scheduler.starts
    for request in (main.request_listen, main.request_play, main.request_dfesta):
        assert request()["reason"] == "loading"
    main.set_tray({"present": True})
    main.set_tray({"present": False})
    main.set_tray({"present": True})
    clock[0] = 2.9
    main.get_game_runtime().barrier()
    main._update_matrix_display()
    assert not main._playback_scheduler.starts
    clock[0] = 3
    main.get_game_runtime().barrier()
    main._update_matrix_display()
    assert writes[-1]["text"] == "HINT"
    assert len(main._playback_scheduler.starts) == 1
    for _ in range(3):
        main._update_matrix_display()
    events.flush()
    assert notes == [(60, True), (61, True), (63, True), (62, True), (61, True)]
    assert main.get_game_state()["timers"]["remaining_time_over_s"] == 60


def test_correct_gate_off_before_dfesta_and_farewell_then_ready(event_main):
    main, clock, writes, events, notes, held = event_main
    main.set_tray({"present": True})
    clock[0] = 3
    runtime = main.get_game_runtime()
    runtime.barrier()
    main._update_matrix_display()
    runtime.add_orb(orb_id="a", slave=1, cell=1)
    main._update_matrix_display()
    assert writes[-1]["text"] == "PRESS DFESTA!"
    events.flush()
    assert held == {64}
    runtime.remove_orb(slave=1, cell=1)
    runtime.add_orb(orb_id="a", slave=1, cell=1)
    events.flush()
    assert notes[-3:] == [(64, True), (64, False), (64, True)]
    old_start = main._playback_scheduler.start
    def start(*args, **kwargs):
        assert not held
        return old_start(*args, **kwargs)
    main._playback_scheduler.start = start
    assert main.request_dfesta()["counted"]
    assert not held
    runtime.remove_orb(slave=1, cell=1)
    main.set_tray({"present": False})
    main._update_matrix_display()
    events.flush()
    assert writes[-1]["text"] == "GOODBYE"
    assert notes[-1] == (66, True)
    assert (60, True) not in notes
    clock[0] = 7
    runtime.barrier()
    main._update_matrix_display()
    assert writes[-1] == {"type": "clear"}
    clock[0] = 11
    runtime.barrier()
    main._update_matrix_display()
    events.flush()
    assert writes[-1]["text"] == "PLACE TRAY"
    assert notes[-1] == (60, True)


def test_reset_cancels_pending_hint_and_next_session_has_own_delay(event_main):
    main, clock, _, events, notes, _ = event_main
    main.set_tray({"present": True})
    clock[0] = 1
    main.reset_game()
    main.set_tray({"present": False})
    clock[0] = 4
    main.get_game_runtime().barrier()
    main._update_matrix_display()
    assert not main._playback_scheduler.starts
    main.set_tray({"present": True})
    clock[0] = 6
    main.get_game_runtime().barrier()
    main._update_matrix_display()
    assert not main._playback_scheduler.starts
    clock[0] = 7
    main.get_game_runtime().barrier()
    main._update_matrix_display()
    events.flush()
    assert len(main._playback_scheduler.starts) == 1
    assert notes.count((63, True)) == 2


@pytest.mark.parametrize("reason", ["idle", "time_over"])
@pytest.mark.parametrize("clear_immediately", [False, True])
def test_timeout_event_survives_immediate_ready(reason, clear_immediately):
    notes = []
    events = GameEvents({}, send_note=lambda n, **kw: notes.append(n) or {"ok": True})
    try:
        previous = {"state": "RUNNING"}
        current = {"state": "READY" if clear_immediately else "RESETTING",
                   "cleanup_end_reason" if clear_immediately else "end_reason": reason}
        events.observe(previous, current, "tray")
        events.observe(current, current, "tick")
        events.flush()
        assert notes == [65]
    finally:
        events.close()


def test_partial_and_unknown_tray_reports_do_not_create_fake_edges():
    notes = []
    events = GameEvents({}, send_note=lambda n, **kw: notes.append(n) or {"ok": True})
    try:
        previous = {"tray": {"present": None, "seated": None}}
        for present, seated in [(True, True), (True, True), (True, False), (False, False),
                                (True, False), (True, True)]:
            current = {"tray": {"present": present, "seated": seated}}
            events.observe(previous, current, "tray")
            previous = deepcopy(current)
        events.flush()
        assert notes == [62, 61]
    finally:
        events.close()


def test_expired_tray_lease_between_ticks_is_not_placement():
    now, notes = [0.0], []
    events = GameEvents({}, send_note=lambda n, **kw: notes.append(n) or {"ok": True})
    engine = GameEngine({"1": "C"}, time_over_s=60, idle_s=30, clock=lambda: now[0])
    runtime = GameRuntime(engine, tick_interval_s=60, on_snapshot=events.observe)
    runtime.start()
    try:
        runtime.set_tray(False, False, ttl_s=3)
        now[0] = 3
        assert not runtime.set_tray(True, True, ttl_s=3).value
        events.flush()
        assert not notes
    finally:
        runtime.close()
        events.close()


def test_released_gate_does_not_reopen_midi_during_inspection():
    retried = threading.Event()
    released = [False]
    def send(note, **kwargs):
        if released[0]:
            retried.set()
        if not kwargs.get("active", True):
            released[0] = True
        return {"ok": True}
    events = GameEvents({}, send_note=send)
    try:
        correct = {"state": "RUNNING", "is_correct": True}
        events.observe({}, correct, "add_orb")
        events.observe(correct, {"state": "RESETTING"}, "reset")
        events.flush()
        assert released[0]
        assert not retried.wait(0.3)
    finally:
        events.close()
    assert not retried.is_set()


@pytest.mark.parametrize("reason", ["idle", "time_over", "manual"])
def test_game_end_releases_gate_before_terminal_sound(event_main, reason):
    main, clock, _, events, notes, held = event_main
    main.set_tray({"present": True})
    clock[0] = 3
    runtime = main.get_game_runtime()
    runtime.barrier()
    main._update_matrix_display()
    runtime.add_orb(orb_id="a", slave=1, cell=1)
    events.flush()
    assert held == {64}
    runtime.end_game(reason)
    events.flush()
    assert not held
    assert notes[-1:] == [(64, False)] if reason == "manual" else notes[-2:] == [(64, False), (65, True)]


def test_configured_delay_extends_existing_timer_grace(monkeypatch):
    from test_main_runtime_integration import _load_main_module
    import sys
    main = _load_main_module()
    now = [10.0]
    main.config["game_rules"]["initial_hint_delay_s"] = 2.5
    monkeypatch.setattr(main, "reload_melody_data", lambda: None)
    monkeypatch.setattr(main, "GameEngine", lambda *a, **kw: GameEngine(*a, **kw, clock=lambda: now[0]))
    try:
        main.start_game()
        runtime = main.get_game_runtime()
        assert not main._playback_scheduler.starts
        grace = 2.5 + main.build_plan(main.PlaybackMode.LISTEN, main.playback_melody).duration_s + 1
        now[0] = 12.5
        runtime.barrier()
        main._update_matrix_display()
        assert len(main._playback_scheduler.starts) == 1
        now[0] = 10 + grace + 0.5
        runtime.barrier()
        timers = runtime.snapshot()["timers"]
        assert timers["remaining_time_over_s"] == pytest.approx(main.time_over_s - 0.5)
        assert timers["remaining_idle_s"] == pytest.approx(main.idle_s - 0.5)
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
        for name in ("server.main", "server.inout.serial_io", "serial"):
            sys.modules.pop(name, None)
