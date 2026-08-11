from collections import deque
import json
import time
import types

from app.device_manager import DeviceManager


class FakeSerialException(OSError):
    pass


class FakeDevice:
    def __init__(self, role, device_id, firmware):
        self.role = role
        self.device_id = device_id
        self.firmware = firmware
        self.lines = deque()
        self.writes = []
        self.timeout = 0.1
        self.is_open = True
        self.fail_writes = False

    def write(self, raw):
        if self.fail_writes:
            raise FakeSerialException("disconnected")
        message = json.loads(raw.decode("utf-8"))
        self.writes.append(message)
        if message.get("cmd") == "hello":
            self.lines.append(
                (
                    json.dumps(
                        {
                            "type": "hello",
                            "protocol": "orbbridge",
                            "version": 1,
                            "role": self.role,
                            "device_id": self.device_id,
                            "fw": self.firmware,
                            "nonce": message["nonce"],
                        }
                    )
                    + "\n"
                ).encode()
            )
        elif message.get("cmd") == "ping":
            self.lines.append(b'{"type":"pong"}\n')
        return len(raw)

    def readline(self):
        return self.lines.popleft() if self.lines else b""

    def close(self):
        self.is_open = False


class FakeHardware:
    def __init__(self):
        self.ports = []
        self.devices = {}
        self.serial = types.SimpleNamespace(
            Serial=self.open,
            SerialException=FakeSerialException,
        )

    def add(self, port, role, device_id, firmware="0.1.0"):
        device = FakeDevice(role, device_id, firmware)
        self.devices[port] = device
        if port not in self.ports:
            self.ports.append(port)
        return device

    def open(self, port, *_args, **_kwargs):
        device = self.devices.get(port)
        if device is None:
            raise FakeSerialException("missing port")
        device.is_open = True
        return device

    def list_ports(self):
        return [types.SimpleNamespace(device=port) for port in self.ports]


def wait_until(predicate, timeout=1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition timed out")


def make_manager(hardware, messages):
    return DeviceManager(
        {
            "baudrate": 115200,
            "scan_interval_s": 0.05,
            "handshake_timeout_s": 0.2,
            "ping_interval_s": 0.05,
            "device_timeout_s": 0.3,
        },
        messages.append,
        serial_module=hardware.serial,
        port_provider=hardware.list_ports,
    )


def test_handshake_identifies_master_and_dotmatrix_and_routes_messages():
    hardware = FakeHardware()
    master = hardware.add("COM3", "master", "MASTER-01", "1.2.0")
    dotmatrix = hardware.add("COM7", "dotmatrix", "MATRIX-01", "0.1.0")
    messages = []
    manager = make_manager(hardware, messages)

    try:
        manager.start()
        wait_until(
            lambda: all(
                item["connected"]
                for item in manager.status()["devices"].values()
            )
        )

        status = manager.status()
        assert status["devices"]["master"]["port"] == "COM3"
        assert status["devices"]["master"]["device_id"] == "MASTER-01"
        assert status["devices"]["dotmatrix"]["port"] == "COM7"
        assert status["devices"]["dotmatrix"]["firmware"] == "0.1.0"

        master.lines.append(b'{"type":"game","action":"start"}\n')
        wait_until(lambda: messages == [{"type": "game", "action": "start"}])

        assert manager.send(
            "dotmatrix", {"cmd": "show", "view": "game_status"}
        )
        assert any(
            message.get("cmd") == "show" for message in dotmatrix.writes
        )
    finally:
        manager.stop()

    assert manager.status()["running"] is False


def test_disconnected_dotmatrix_is_found_again_on_new_com_port():
    hardware = FakeHardware()
    hardware.add("COM3", "master", "MASTER-01")
    old_matrix = hardware.add("COM7", "dotmatrix", "MATRIX-01")
    manager = make_manager(hardware, [])

    try:
        manager.start()
        wait_until(
            lambda: manager.status()["devices"]["dotmatrix"]["connected"]
        )

        old_matrix.fail_writes = True
        assert manager.send("dotmatrix", {"cmd": "clear"}) is False
        hardware.ports.remove("COM7")
        hardware.add("COM11", "dotmatrix", "MATRIX-01")
        manager.request_rescan()

        wait_until(
            lambda: manager.status()["devices"]["dotmatrix"]["connected"]
            and manager.status()["devices"]["dotmatrix"]["port"] == "COM11"
        )
        assert manager.status()["devices"]["dotmatrix"]["device_id"] == "MATRIX-01"
    finally:
        manager.stop()


def test_unknown_role_does_not_claim_a_device_slot():
    hardware = FakeHardware()
    hardware.add("COM4", "other", "OTHER-01")
    manager = make_manager(hardware, [])

    try:
        manager.start()
        wait_until(lambda: manager.status()["last_scan_at"] is not None)
        time.sleep(0.25)
        assert not any(
            item["connected"] for item in manager.status()["devices"].values()
        )
    finally:
        manager.stop()

