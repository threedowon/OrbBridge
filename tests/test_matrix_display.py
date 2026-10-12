import json

from server.inout.matrix_display import MatrixDisplay


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.extend(json.loads(line) for line in data.decode("ascii").splitlines())

    def close(self):
        self.closed = True


class FakeSerial:
    def __init__(self, device):
        self.device = device

    def Serial(self, *args, **kwargs):
        return self.device


def test_matrix_displays_latest_voice_and_updates_changed_seconds_only(monkeypatch):
    monkeypatch.setattr("server.inout.matrix_display.time.monotonic", lambda: 0.0)
    device = FakeDevice()
    matrix = MatrixDisplay(
        {"enabled": True, "port": "COM_TEST", "baudrate": 115200},
        serial_module=FakeSerial(device),
        catalog={
            "voices": {"ive_5": {"labels": {"en": "LEESEO"}}}
        },
    )
    running = {
        "state": "RUNNING",
        "timers": {"remaining_time_over_s": 119.2},
    }

    matrix.show_voice("ive_5", running)
    matrix.update(running)
    running["timers"]["remaining_time_over_s"] = 118.8
    matrix.update(running)
    matrix.update({"state": "RESETTING", "timers": {}})
    matrix.update({"state": "READY", "timers": {}})
    matrix.close()

    assert device.writes == [
        {"type": "heartbeat"},
        {"type": "display", "text": "IVE-LEESEO", "seconds": 120},
        {"type": "display", "text": "IVE-LEESEO", "seconds": 119},
        {"type": "clear"},
        {"type": "clear"},
        {"type": "shutdown"},
    ]
    assert device.closed is True


def test_idle_heartbeat_and_close_prevent_reconnect(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("server.inout.matrix_display.time.monotonic", lambda: now[0])
    device = FakeDevice()
    matrix = MatrixDisplay({"enabled": True}, serial_module=FakeSerial(device))
    matrix.update({"state": "READY"})
    now[0] = 0.5
    matrix.update({"state": "READY"})
    now[0] = 1.0
    matrix.update({"state": "READY"})
    assert device.writes == [{"type": "heartbeat"}] * 2
    matrix.close()
    sent = list(device.writes)
    matrix.close()
    now[0] = 10
    matrix.update({"state": "READY"})
    assert device.writes == sent
    assert device.closed


def test_close_still_closes_port_when_shutdown_write_fails():
    device = FakeDevice()
    matrix = MatrixDisplay({"enabled": True}, serial_module=FakeSerial(device))
    def fail(data):
        raise OSError("disconnected")
    device.write = fail
    matrix.close()
    assert device.closed
