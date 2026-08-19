import json

from server.inout.matrix_display import MatrixDisplay


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(json.loads(data.decode("ascii")))

    def close(self):
        self.closed = True


class FakeSerial:
    def __init__(self, device):
        self.device = device

    def Serial(self, *args, **kwargs):
        return self.device


def test_matrix_displays_latest_voice_and_updates_changed_seconds_only():
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
        {"type": "display", "text": "IVE-LEESEO", "seconds": 120},
        {"type": "display", "text": "IVE-LEESEO", "seconds": 119},
        {"type": "clear"},
    ]
    assert device.closed is True
