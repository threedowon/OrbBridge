import json
import threading

import pytest

from server.inout.matrix_display import MatrixDisplay


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.closed = False
        self.fail = False
        self.short = False
        self.fail_close = False

    def write(self, data):
        if self.fail:
            raise OSError("disconnected")
        self.writes.append(json.loads(data.decode("ascii")))
        return len(data) - int(self.short)

    def close(self):
        self.closed = True
        if self.fail_close:
            raise OSError("close failed")


class FakeSerial:
    def __init__(self, device):
        self.device = device
        self.opens = 0
        self.fail = False

    def Serial(self, *args, **kwargs):
        self.opens += 1
        if self.fail:
            raise OSError("missing port")
        return self.device


@pytest.fixture
def rig():
    now = [0.0]
    device = FakeDevice()
    serial = FakeSerial(device)
    matrix = MatrixDisplay(
        {"enabled": True}, serial_module=serial,
        catalog={"voices": {"ive_5": {"labels": {"en": "LEESEO"}}}},
        clock=lambda: now[0],
    )
    matrix.BOOT_S = 0
    return matrix, device, serial, now


def running(seconds=119.2):
    return {"state": "RUNNING", "timers": {"remaining_time_over_s": seconds}}


def playback(mode, active=True, run_id=1):
    return {"mode": mode, "active": active, "run_id": run_id}


def test_ready_audio_waits_for_display_write_and_not_heartbeats(rig):
    matrix, device, _, clock = rig
    matrix.BOOT_S = 1.6
    screens = []
    matrix._on_screen_change = screens.append
    matrix.update({"state": "READY"})
    assert not screens and not device.writes
    clock[0] = 1.6
    matrix.update({"state": "READY"})
    matrix.update({"state": "READY"})
    clock[0] = 3
    matrix.update({"state": "READY"})
    assert screens == ["ready"]


def test_configured_texts_keep_semantic_events_and_offline_protocol(rig):
    _, device, serial, clock = rig
    screens = []
    matrix = MatrixDisplay({"enabled": True, "texts": {
        "ready": "HELLO", "farewell": "HELLO", "offline": "NO CONNECTION",
        "loading": "WAIT", "press_dfesta": "PRESS BUTTON",
    }}, serial_module=serial, catalog={"voices": {}}, clock=lambda: clock[0],
        on_screen_change=screens.append)
    matrix.BOOT_S = 0
    matrix.update({"state": "READY"})
    assert device.writes[-1] == {"type": "status", "text": "HELLO", "offline_text": "NO CONNECTION"}
    matrix.update({"state": "READY", "tray": {"present": False},
                   "timers": {"since_table_cleared_s": 0}})
    assert screens == ["ready", "farewell"]
    game = {**running(), "initial_hint": {"pending": True}, "is_correct": True}
    matrix.update(game)
    assert device.writes[-1]["text"] == "WAIT"
    game["initial_hint"]["pending"] = False
    matrix.update(game, playback("LISTEN"))
    assert device.writes[-1]["text"] == "PRESS BUTTON"
    assert device.writes[-1]["arrow"] == "right"
    matrix.update(game, playback("DFESTA"))
    assert device.writes[-1]["text"] == "COMPLETE"
    assert "arrow" not in device.writes[-1]


def test_dfesta_prompt_replaces_countdown_with_arrow_then_restores_timer(rig):
    matrix, device, _, now = rig
    matrix.texts["press_dfesta"] = "PRESS DFESTA BUTTON!"
    matrix.update({**running(45), "is_correct": True})
    assert device.writes[-1] == {
        "type": "status", "text": "PRESS DFESTA BUTTON!", "arrow": "right"}
    count = len(device.writes)
    matrix.update({**running(44), "is_correct": True})
    assert len(device.writes) == count
    now[0] += 1
    matrix.update({**running(44), "is_correct": True})
    assert device.writes[-1]["arrow"] == "right"
    matrix.update({**running(43), "is_correct": False})
    assert device.writes[-1] == {"type": "display", "text": "", "seconds": 43}


@pytest.mark.parametrize("text", ["가나다", "X" * 33, "A\nB", 12])
def test_invalid_status_text_is_rejected(text):
    with pytest.raises(ValueError):
        MatrixDisplay({"texts": {"ready": text}})


def test_running_name_countdown_and_duplicate_suppression(rig):
    matrix, device, _, _ = rig
    matrix.show_voice("ive_5", running())
    assert device.writes == []  # placement/MIDI path does no serial I/O
    matrix.update(running())
    matrix.update(running())
    matrix.update(running(118.8))
    assert device.writes == [
        {"type": "display", "text": "IVE-LEESEO", "seconds": 120},
        {"type": "display", "text": "IVE-LEESEO", "seconds": 119},
    ]


@pytest.mark.parametrize("mode,message", [
    ("listen", "HINT"), ("play", "PLAYING"), ("dfesta", "COMPLETE"),
])
def test_playback_message_returns_to_latest_orb_on_end_or_cancel(rig, mode, message):
    matrix, device, _, _ = rig
    matrix.update(running(), playback(mode))
    assert device.writes[-1] == {"type": "status", "text": message}
    matrix.show_voice("ive_5", running())
    matrix.update(running(), playback(mode))
    assert device.writes[-1]["text"] == message
    matrix.update(running(98), playback(mode, False))
    assert device.writes[-1] == {"type": "display", "text": "IVE-LEESEO", "seconds": 98}


def test_replaced_playback_uses_live_mode_not_old_end_events(rig):
    matrix, device, _, _ = rig
    for mode, message in [("listen", "HINT"), ("play", "PLAYING"), ("listen", "HINT")]:
        matrix.update(running(), playback(mode))
        assert device.writes[-1]["text"] == message


@pytest.mark.parametrize("reason,first", [
    ("manual", "PLEASE CLEAR TABLE"), ("idle", "TIME OVER"),
    ("time_over", "TIME OVER"), ("complete", "COMPLETE"),
])
def test_reset_progression_cannot_restart_on_repeated_poll(rig, reason, first):
    matrix, device, _, now = rig
    reset = {"state": "RESETTING", "end_reason": reason}
    matrix.update(reset, playback("listen"))
    assert device.writes[-1]["text"] == first
    now[0] = 1.9
    matrix.update(reset)
    assert device.writes[-1]["text"] == first
    now[0] = 2
    matrix.update(reset)
    assert device.writes[-1]["text"] == "PLEASE CLEAR TABLE"
    now[0] = 10000
    matrix.update(reset)
    assert device.writes[-1]["text"] == "PLEASE CLEAR TABLE"
    matrix.update({"state": "READY"}, playback("dfesta"))
    assert device.writes[-1]["text"] == "PLACE TRAY"


def test_final_dfesta_stays_complete_until_audio_ends_then_clears(rig):
    matrix, device, _, now = rig
    reset = {"state": "RESETTING", "end_reason": "complete"}
    matrix.update(reset, playback("dfesta"))
    now[0] = 60
    matrix.update(reset, playback("dfesta"))
    assert device.writes[-1]["text"] == "COMPLETE"
    matrix.update(reset, playback("dfesta", False))
    now[0] += 2
    matrix.update(reset)
    assert device.writes[-1]["text"] == "PLEASE CLEAR TABLE"
    now[0] += 2
    matrix.update(reset)
    assert device.writes[-1]["text"] == "PLEASE CLEAR TABLE"


@pytest.mark.parametrize("state", ["RUNNING", "RESETTING"])
def test_dfesta_displays_title_only_without_changing_completion_semantics(rig, state):
    matrix, device, _, now = rig
    screens = []
    matrix._on_screen_change = screens.append
    game = {**running(), "state": state, "end_reason": "complete",
            "melody_title": "BTS - Dynamite"}
    matrix.update(game, playback("dfesta"))
    assert device.writes[-1] == {"type": "status", "text": "BTS - Dynamite", "scroll": True}
    now[0] = 1
    matrix.update(game, playback("dfesta"))
    assert device.writes[-1] == device.writes[-2]  # Stable heartbeat keeps scroll moving.
    assert screens == ["complete"]
    matrix.update(game, playback("dfesta", False))
    if state == "RESETTING":
        now[0] += 2
        matrix.update(game)
        assert device.writes[-1]["text"] == "PLEASE CLEAR TABLE"
    else:
        assert device.writes[-1]["type"] == "display"
    assert "scroll" not in device.writes[-1]


@pytest.mark.parametrize("title", [None, "", "  ", 123])
def test_missing_title_falls_back_to_configured_complete(rig, title):
    matrix, device, _, _ = rig
    matrix.texts["complete"] = "WELL DONE"
    matrix.update({**running(), "melody_title": title}, playback("DFESTA"))
    assert device.writes[-1] == {"type": "status", "text": "WELL DONE"}


@pytest.mark.parametrize("time_over_s", [60, None])
@pytest.mark.parametrize("title", ["BTS - DYNAMITE", ""])
def test_dfesta_title_remains_while_correct_after_playback(rig, time_over_s, title):
    from server.game.game_engine import GameEngine

    matrix, device, _, now = rig
    screens = []
    matrix._on_screen_change = screens.append
    game = GameEngine({1: "C"}, time_over_s=time_over_s, idle_s=None,
                      tray_present=True, clock=lambda: now[0])
    game.start()
    game.claim_initial_hint()
    game.add_orb(orb_id="a", slave=1, cell=1)

    def update(current_playback=None):
        matrix.update({**game.snapshot(), "melody_title": title}, current_playback)

    update()
    assert device.writes[-1]["text"] == "PRESS DFESTA!"
    assert game.press_dfesta()
    update(playback("DFESTA"))
    expected = {"type": "status", "text": title, "scroll": True} if title else {
        "type": "status", "text": "COMPLETE"}
    assert device.writes[-1] == expected
    for seconds in (1, 3, 10):
        now[0] = seconds
        update(playback("DFESTA", False))
        assert device.writes[-1] == expected
    assert screens == ["press_dfesta", "complete"]

    game.remove_orb(slave=1, cell=1)
    update()
    if time_over_s is None:
        assert device.writes[-1] == {"type": "status", "text": "PLEASE CLEAR TABLE"}
    else:
        assert device.writes[-1] == {"type": "display", "text": "", "seconds": 50}
        game.add_orb(orb_id="a", slave=1, cell=1)
        update()
        assert device.writes[-1]["text"] == "PRESS DFESTA!"


@pytest.mark.parametrize("reason,expected", [
    ("manual", "PLEASE CLEAR TABLE"), ("idle", "TIME OVER"), ("time_over", "TIME OVER"),
])
def test_held_dfesta_title_does_not_override_game_exit(rig, reason, expected):
    matrix, device, _, _ = rig
    game = {**running(), "is_correct": True, "completion": {"dfesta_presses": 1},
            "melody_title": "BTS - DYNAMITE"}
    # The display can recover the title without having observed playback start.
    matrix.update(game)
    assert device.writes[-1]["text"] == "BTS - DYNAMITE"
    matrix.update({**game, "state": "RESETTING", "end_reason": reason})
    assert device.writes[-1] == {"type": "status", "text": expected}
    matrix.update({"state": "READY"})
    assert device.writes[-1]["text"] == "PLACE TRAY"
    matrix.update({**game, "completion": {"dfesta_presses": 0}})
    assert device.writes[-1]["text"] == "PRESS DFESTA!"


def test_long_title_is_not_cut_to_32_and_new_song_does_not_reuse_old_title(rig):
    matrix, device, _, _ = rig
    title = "A VERY LONG SONG TITLE THAT MUST REMAIN ON ONE LINE"
    matrix.update({**running(), "melody_title": title}, playback("dfesta"))
    assert device.writes[-1]["text"] == title
    assert device.writes[-1]["scroll"] is True
    matrix.update({"state": "READY"})
    matrix.update({**running(), "melody_title": "New song"}, playback("dfesta", run_id=2))
    assert device.writes[-1]["text"] == "New song"


def test_title_normalization_and_escaped_packet_fit_matrix_input_buffer(rig):
    matrix, device, _, _ = rig
    matrix.update({**running(), "melody_title": "A\nB\t가"}, playback("dfesta"))
    assert device.writes[-1]["text"] == "A B ?"
    matrix._configure_offline = True
    matrix.texts["offline"] = '"' * 32
    matrix.update({**running(), "melody_title": '\\"' * 100}, playback("dfesta"))
    payload = device.writes[-1]
    assert len(payload["text"]) == 128
    assert len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")) < 384


def test_title_does_not_override_other_screens(rig):
    matrix, device, _, _ = rig
    game = {**running(), "melody_title": "Dynamite"}
    for mode, expected in [("listen", "HINT"), ("play", "PLAYING")]:
        matrix.update(game, playback(mode))
        assert device.writes[-1] == {"type": "status", "text": expected}
    matrix.update({**game, "is_correct": True})
    assert device.writes[-1] == {"type": "status", "text": "PRESS DFESTA!", "arrow": "right"}


def test_ready_and_new_session_do_not_retain_name_or_status(rig):
    matrix, device, _, _ = rig
    matrix.show_voice("ive_5", running())
    matrix.update(running())
    matrix.clear_voice()
    matrix.update(running())
    assert device.writes[-1]["text"] == ""
    matrix.update({"state": "READY"})
    assert device.writes[-1]["text"] == "PLACE TRAY"
    matrix.update(running(100))
    assert device.writes[-1] == {"type": "display", "text": "", "seconds": 100}
    matrix.update({"state": "unexpected"})
    assert device.writes[-1] == {"type": "clear"}


@pytest.mark.parametrize("seconds", [None, "bad", float("nan"), float("inf")])
def test_invalid_timer_clears_playback_without_crashing(rig, seconds):
    matrix, device, _, _ = rig
    matrix.update(running(), playback("listen"))
    matrix.update(running(seconds))
    assert device.writes[-1] == {"type": "status", "text": "PLACE ORB"}


def test_reconnect_retries_only_latest_screen_and_close_failure_is_safe(rig):
    matrix, device, serial, now = rig
    device.fail = device.fail_close = True
    matrix.update(running(), playback("listen"))
    assert device.closed
    for _ in range(100):
        matrix.update({"state": "READY"})
    assert serial.opens == 1
    device.fail = False
    now[0] = 1
    matrix.update({"state": "READY"})
    assert device.writes[-1]["text"] == "PLACE TRAY"
    matrix.close()
    now[0] = 20
    matrix.update(running())
    assert serial.opens == 2


def test_boot_wait_and_heartbeat_recover_silent_reboot(rig):
    matrix, device, serial, now = rig
    matrix.BOOT_S = 1.6
    matrix.update({"state": "READY"})
    now[0] = 1.59
    matrix.update(running(), playback("listen"))
    assert device.writes == []
    now[0] = 1.6
    matrix.update(running(), playback("play"))
    assert device.writes[-1]["text"] == "PLAYING"
    now[0] = 2.6
    matrix.update(running(), playback("play"))
    assert len(device.writes) == 2
    assert serial.opens == 1


def test_connection_status_tracks_boot_failure_recovery_and_close(rig):
    matrix, device, serial, now = rig
    matrix.BOOT_S = 1.6
    assert matrix.connection_status()["state"] == "waiting"
    assert serial.opens == 0  # Reading status must not perform serial I/O.
    serial.fail = True
    matrix.update({"state": "READY"})
    status = matrix.connection_status()
    assert status["state"] == "disconnected"
    assert status["connected"] is False
    assert status["error"] == "missing port"

    serial.fail = False
    now[0] = 1
    matrix.update({"state": "READY"})
    assert matrix.connection_status()["state"] == "connecting"
    assert matrix.connection_status()["connected"] is False
    now[0] = 2.6
    matrix.update({"state": "READY"})
    assert matrix.connection_status() == {
        "enabled": True, "port": "COM6", "state": "connected",
        "connected": True, "error": "",
    }

    device.short = True
    now[0] = 4
    matrix.update({"state": "READY"})
    assert matrix.connection_status()["connected"] is False
    assert matrix.connection_status()["error"] == "incomplete matrix command"
    device.short = False
    matrix.BOOT_S = 0
    now[0] = 5
    matrix.update({"state": "READY"})
    assert matrix.connection_status()["connected"] is True
    assert matrix.connection_status()["error"] == ""
    matrix.close()
    assert matrix.connection_status()["state"] == "disconnected"


def test_connection_status_distinguishes_disabled_and_missing_serial(rig):
    matrix, _, _, _ = rig
    disabled = MatrixDisplay({"enabled": False}, catalog={"voices": {}})
    disabled.update({"state": "READY"})
    assert disabled.connection_status()["state"] == "disabled"
    assert disabled.connection_status()["connected"] is False
    matrix._serial = None
    matrix.update({"state": "READY"})
    assert matrix.connection_status()["state"] == "disconnected"
    assert matrix.connection_status()["error"] == "pyserial not installed"


def test_missing_port_and_short_writes_are_retried_without_busy_loop(rig):
    matrix, device, serial, now = rig
    serial.fail = True
    for _ in range(10):
        matrix.update({"state": "READY"})
    assert serial.opens == 1
    serial.fail = False
    now[0] = 1
    device.short = True
    matrix.update({"state": "READY"})
    assert device.closed
    now[0] = 2
    device.short = False
    matrix.update({"state": "READY"})
    assert len(device.writes) == 2


def test_slow_serial_cannot_block_orb_placement(rig):
    matrix, device, _, _ = rig
    entered, release = threading.Event(), threading.Event()
    original = device.write

    def blocked(data):
        entered.set()
        assert release.wait(2)
        return original(data)

    device.write = blocked
    worker = threading.Thread(target=matrix.update, args=(running(),))
    worker.start()
    try:
        assert entered.wait(1)
        assert matrix.connection_status()["connected"] is False
        placed = threading.Event()
        placing = threading.Thread(target=lambda: (
            matrix.show_voice("ive_5", running()), placed.set()
        ))
        placing.start()
        assert placed.wait(0.5)
        placing.join()
    finally:
        release.set()
        worker.join()
    matrix.update(running())
    assert device.writes[-1]["text"] == "IVE-LEESEO"


def test_oversized_and_non_font_labels_cannot_overflow_serial_frame(rig):
    matrix, device, _, _ = rig
    matrix.show_voice("A" * 300 + "\n한글_1", running())
    matrix.update(running())
    assert len(device.writes[-1]["text"]) == 32
    assert len(json.dumps(device.writes[-1])) < 160
    matrix.show_voice("한글_1", running())
    matrix.update(running())
    assert device.writes[-1]["text"] == "??-1"


@pytest.mark.parametrize("duration", [0, 4, 5])
def test_goodbye_blackout_then_place_tray_uses_cleanup_clock(rig, duration):
    matrix, device, _, now = rig
    matrix.farewell_duration_s = duration
    # Even a reset/clear completed between display polls must show GOODBYE.
    ready = {"state": "READY", "tray": {"present": False, "seated": False},
             "timers": {"since_table_cleared_s": 0}}
    matrix.update(ready)
    assert device.writes[-1]["text"] == ("GOODBYE" if duration else "PLACE TRAY")
    if duration:
        now[0] = duration
        ready["timers"]["since_table_cleared_s"] = duration
        matrix.update(ready)
        assert device.writes[-1] == {"type": "clear"}
        now[0] = duration * 2
        ready["timers"]["since_table_cleared_s"] = duration * 2
        matrix.update(ready)
        assert device.writes[-1]["text"] == "PLACE TRAY"


@pytest.mark.parametrize("elapsed", [0.5, 4.5])
def test_new_game_interrupts_goodbye_and_blackout_without_place_tray(rig, elapsed):
    matrix, device, _, now = rig
    matrix.update({"state": "READY", "tray": {"present": False},
                   "timers": {"since_table_cleared_s": elapsed}})
    matrix.update(running(), playback("listen"))
    assert device.writes[-1]["text"] == "HINT"
    now[0] = 10
    matrix.show_voice("ive_5", running())
    matrix.update(running())
    assert device.writes[-1]["text"] == "IVE-LEESEO"
    assert not any(p.get("text") == "PLACE TRAY" for p in device.writes)


@pytest.mark.parametrize("reason", ["idle", "time_over"])
@pytest.mark.parametrize("observed_reset", [False, True])
def test_timeout_hold_survives_immediate_cleanup(rig, reason, observed_reset):
    matrix, device, _, now = rig
    matrix.update(running())
    if observed_reset:
        matrix.update({"state": "RESETTING", "end_reason": reason})
    ready = {"state": "READY", "cleanup_end_reason": reason,
             "tray": {"present": False}, "timers": {}}
    for elapsed, expected in [(0, "TIME OVER"), (1.99, "TIME OVER"),
                              (2, "GOODBYE"), (5.99, "GOODBYE"),
                              (6, None), (9.99, None), (10, "PLACE TRAY")]:
        now[0] = elapsed
        ready["timers"]["since_table_cleared_s"] = elapsed
        matrix.update(ready)
        assert device.writes[-1] == ({"type": "clear"} if expected is None else
                                     {"type": "status", "text": expected})


def test_timeout_buffer_is_interrupted_by_new_game(rig):
    matrix, device, _, now = rig
    matrix.update({"state": "READY", "cleanup_end_reason": "idle",
                   "tray": {"present": False},
                   "timers": {"since_table_cleared_s": 0}})
    assert device.writes[-1]["text"] == "TIME OVER"
    matrix.update(running(), playback("listen"))
    assert device.writes[-1]["text"] == "HINT"
    now[0] = 3
    matrix.update(running())
    assert device.writes[-1]["type"] == "display"


def test_late_cleanup_does_not_repeat_timeout(rig):
    matrix, device, _, now = rig
    matrix.update({"state": "RESETTING", "end_reason": "idle"})
    now[0] = 10
    matrix.update({"state": "READY", "cleanup_end_reason": "idle",
                   "tray": {"present": False},
                   "timers": {"since_table_cleared_s": 0}})
    assert device.writes[-1]["text"] == "GOODBYE"

@pytest.mark.parametrize('occupied', [False, True])
def test_tray_removal_shows_goodbye_only_when_orbs_are_also_gone(rig, occupied):
    from server.game.game_engine import GameEngine
    matrix, device, _, now = rig
    game = GameEngine({'1': 'C'}, time_over_s=60, idle_s=30, clock=lambda: now[0])
    game.set_tray(True, True)
    game.start()
    matrix.update(game.snapshot())
    if occupied:
        game.add_orb(orb_id='a', slave=1, cell=1)
    game.set_tray(False, False)
    matrix.update(game.snapshot(), playback('PLAY'))
    if occupied:
        assert game.snapshot()['state'] == 'RUNNING'
        assert device.writes[-1]['text'] != 'GOODBYE'
        game.remove_orb(slave=1, cell=1)
        matrix.update(game.snapshot())
    assert device.writes[-1]['text'] == 'GOODBYE'
    now[0] += matrix.farewell_duration_s
    matrix.update(game.snapshot())
    assert device.writes[-1] == {'type': 'clear'}
