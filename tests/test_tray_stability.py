"""Physical tray wobble must not restart loading or manufacture GOODBYE."""
import pytest

from server.tray_input import TrayDebouncer
from test_game_events import event_main
from test_main_runtime_integration import tray_main


@pytest.fixture
def hardware_main(event_main, monkeypatch):
    main, clock, writes, events, notes, _ = event_main
    monkeypatch.setattr(main, "_tray_input", TrayDebouncer({}, clock=lambda: clock[0]))
    main._set_tray_source(True)

    def advance(t):
        clock[0] = t
        main.get_game_runtime().barrier()
        main._update_matrix_display()
        events.flush()
        return main.get_game_state()

    def report(t, present, seated=None):
        clock[0] = t
        main.handle_serial_message({"type": "tray", "present": present,
                                    "seated": present if seated is None else seated})
        return advance(t)

    return main, writes, notes, advance, report


@pytest.mark.parametrize("count", [1, 2])
def test_wobble_before_start_and_during_loading_keeps_one_game(hardware_main, count):
    main, writes, notes, advance, report = hardware_main
    main.config["hall_sensor_count"] = count
    seated = count == 2  # One-sensor mode must work with just one detecting.
    report(0, False)
    report(0.1, True, seated)
    report(0.4, False)
    report(0.5, True, seated)
    assert advance(0.99)["state"] == "READY"
    state = advance(1)
    assert state["state"] == "RUNNING"
    assert writes[-1]["text"] == "LOADING..."
    report(1.1, False)
    report(2.4, True, seated)
    assert advance(2.6)["state"] == "RUNNING"
    assert writes[-1]["text"] == "LOADING..."
    report(3.3, True, seated)
    advance(4)
    assert writes[-1]["text"] == "HINT"
    assert len(main._playback_scheduler.starts) == 1
    assert notes.count((61, True)) == 1  # Placement confirmed only once.
    assert notes.count((63, True)) == 1  # Loading deadline was not restarted.
    assert (62, True) not in notes and (66, True) not in notes


@pytest.mark.parametrize("with_orb", [False, True])
def test_real_removal_waits_then_requires_an_empty_table(hardware_main, with_orb):
    main, writes, notes, advance, report = hardware_main
    report(0, False)
    report(0.1, True)
    advance(0.6)
    runtime = main.get_game_runtime()
    if with_orb:
        runtime.add_orb(orb_id="a", slave=1, cell=1)
    report(1, False)
    report(2, False)  # Periodic master report does not restart removal wait.
    assert advance(2.49)["state"] == "RUNNING"
    state = advance(2.5)
    assert state["tray"]["present"] is False
    if with_orb:
        assert state["state"] == "RUNNING"
        assert (66, True) not in notes
        main._publish_transition(runtime.remove_orb(slave=1, cell=1))
        advance(2.6)
    assert main.get_game_state()["state"] == "READY"
    assert writes[-1]["text"] == "GOODBYE"
    assert notes.count((62, True)) == 1
    assert notes.count((66, True)) == 1


def test_two_sensor_mode_requires_both_stable(hardware_main):
    main, _, _, advance, report = hardware_main
    main.config["hall_sensor_count"] = 2
    report(0, False)
    report(0.1, True, False)
    assert advance(0.6)["state"] == "READY"
    report(1, True, True)
    report(1.4, True, False)
    report(1.5, True, True)
    assert advance(1.99)["state"] == "READY"
    assert advance(2)["state"] == "RUNNING"


@pytest.mark.parametrize("disconnect", [False, True])
def test_pending_change_cannot_start_a_game_after_disconnect_or_stale_input(hardware_main, disconnect):
    main, _, notes, advance, report = hardware_main
    report(0, False)
    report(0.1, True)
    if disconnect:
        main._update_serial_status(False, "", "disconnected")
        assert advance(0.6)["tray"]["present"] is None
    else:
        assert advance(3.2)["tray"]["present"] is None
    state = report(3.3, True)
    assert state["state"] == "READY"  # Reconnect is a baseline, not placement.
    assert (61, True) not in notes and (63, True) not in notes


def test_source_switch_cancels_pending_removal(hardware_main):
    main, _, _, advance, report = hardware_main
    report(0, False)
    report(0.1, True)
    advance(0.6)
    report(1, False)
    main._set_tray_source(True)
    assert advance(2.5)["tray"]["present"] is None
    assert main.get_game_state()["state"] == "RUNNING"


def test_software_tray_toggle_remains_immediate(hardware_main):
    main, _, _, _, _ = hardware_main
    main._set_tray_source(False)
    assert main.set_tray({"present": True})["game"]["state"] == "RUNNING"
    assert main.set_tray({"present": False})["game"]["state"] == "READY"
