from app.matrix_display import MatrixDisplay


class FakeDeviceManager:
    def __init__(self, connected=False):
        self.connected = connected
        self.messages = []

    def send(self, role, payload):
        assert role == "dotmatrix"
        if not self.connected:
            return False
        self.messages.append(dict(payload))
        return True


def test_same_view_is_not_sent_twice():
    devices = FakeDeviceManager(connected=True)
    display = MatrixDisplay(devices, brightness=40)

    first = display.show(
        "player_timer",
        transition="none",
        group="CORTIS",
        member="SEONGHYEON",
        seconds=60,
    )
    second = display.show(
        "player_timer",
        transition="none",
        group="CORTIS",
        member="SEONGHYEON",
        seconds=60,
    )

    assert first["sent"] is True
    assert second == {"ok": True, "sent": False, "unchanged": True}
    assert len(devices.messages) == 1


def test_latest_pending_view_is_restored_after_reconnect():
    devices = FakeDeviceManager(connected=False)
    display = MatrixDisplay(devices, brightness=55)

    display.show("player_timer", group="AESPA", member="A", seconds=60)
    display.show("player_timer", group="AESPA", member="A", seconds=59)
    assert display.status()["pending"] is True

    devices.connected = True
    display.handle_device_connected("dotmatrix", {})

    assert devices.messages[0] == {"cmd": "brightness", "value": 55}
    assert devices.messages[1]["cmd"] == "show"
    assert devices.messages[1]["seconds"] == 59
    assert display.status()["pending"] is False


def test_game_sync_clears_ready_and_sends_running_timer():
    devices = FakeDeviceManager(connected=True)
    display = MatrixDisplay(devices)

    display.sync_game(
        {
            "state": "RUNNING",
            "timers": {"remaining_time_over_s": 59.2},
        },
        stage=1,
        melody="dynamite",
    )
    display.sync_game({"state": "READY", "timers": {}}, stage=1, melody="dynamite")

    assert devices.messages[0]["view"] == "game_status"
    assert devices.messages[0]["seconds"] == 60
    assert devices.messages[-1]["cmd"] == "clear"

