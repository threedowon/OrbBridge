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
