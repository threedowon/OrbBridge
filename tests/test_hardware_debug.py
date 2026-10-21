import importlib
import json
import queue
import threading
import time
import urllib.error
import urllib.request

import pytest


class FakeSerial:
    def __init__(self, port, baud, **kwargs):
        self.port = port
        self.lines = queue.Queue()
        self.writes = []
        self.closed = False
        self.fail_write = False

    def read_until(self, *args):
        try:
            return self.lines.get(timeout=0.01)
        except queue.Empty:
            return b""

    def write(self, data):
        if self.fail_write:
            raise OSError("unplugged")
        self.writes.append(data)
        return len(data)

    def close(self):
        self.closed = True


@pytest.fixture
def debug(monkeypatch, tmp_path):
    import server.inout.hardware_io as module

    d = importlib.reload(module)
    monkeypatch.setattr(d, "LOG_DIR", tmp_path / "log")
    from server import text_log
    monkeypatch.setattr(text_log, "_sink", text_log.LineLog(d.LOG_DIR))
    devices = {}

    def connect(port, baud, **kwargs):
        device = FakeSerial(port, baud, **kwargs)
        devices[port] = device
        return device

    monkeypatch.setattr(d.serial, "Serial", connect)
    yield d, devices
    d.stop_serial()


def wait_for(predicate):
    end = time.monotonic() + 2
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_tray_json_is_forwarded_only_from_master(debug):
    d, devices = debug
    events = []
    d.on_message = events.append
    d.start_serial("COM1", 115200)
    wait_for(lambda: "COM1" in devices)
    event = {"type": "tray", "present": True, "seated": False}
    devices["COM1"].lines.put((json.dumps(event) + "\n").encode())
    wait_for(lambda: event in events)
    events.clear()
    d.configure_connection("direct", ["COM2"])
    wait_for(lambda: "COM2" in devices)
    d._handle_line(json.dumps(event), "COM2")
    assert events == []


def line(d, port, **event):
    d._handle_line(json.dumps(event), port, d._generation)


def test_master_health_requires_master_heartbeat(debug):
    d, devices = debug
    assert d._snapshot()["mode"] == "master"
    d.start_serial("COM1", 115200)
    wait_for(lambda: d._master["open"])
    assert d._snapshot()["master"]["health"] == "waiting"
    d._master["started_ms"] -= 9000
    line(d, "COM1", type="heartbeat", slave=1, state=0)
    assert d._snapshot()["master"]["health"] == "unresponsive"
    d._handle_line("#master alive relay=42 bad=2", "COM1", d._generation)
    assert d._snapshot()["master"]["health"] == "alive"
    d._master["last_heartbeat_ms"] -= 9000
    assert d._snapshot()["master"]["health"] == "unresponsive"
    d.stop_serial()
    assert d._snapshot()["master"]["health"] == "disconnected"


@pytest.mark.parametrize("mode,transport", [("master", {}), ("direct", {"transport": "usb"})])
def test_led_ack_is_forwarded_without_occupancy_changes(debug, mode, transport):
    d, devices = debug
    d.configure_connection(mode, ["COM1"])
    wait_for(lambda: "COM1" in devices and d._connections["COM1"]["open"])
    received = []
    d.on_message = received.append
    event = dict(type="led_ack", slave=1, effect=2, received_ms=123, applied_ms=130, **transport)
    line(d, "COM1", **event)
    assert received == [event]
    assert d._snapshot()["cells"] == []
    assert d._snapshot()["slaves"] == []
    assert b"C0\n" not in devices["COM1"].writes
    line(d, "COM1", **{**event, "effect": 10})
    line(d, "COM1", **{**event, "applied_ms": -1})
    assert received == [event]
    line(d, "COM1", **{**event, "ack_wait_ms": -1})
    assert received == [event]
    timed = {**event, "ack_wait_ms": 2500, "timing_capped": False}
    line(d, "COM1", **timed)
    assert received == [event, timed]


def test_multi_usb_route_snapshot_led_and_return_to_master(debug):
    d, devices = debug
    d.configure_connection("direct", ["COM2", "COM3"])
    wait_for(lambda: len(devices) == 2 and all(x.writes for x in devices.values()))
    generation = d._generation
    for sid, port in enumerate(("COM2", "COM3"), 1):
        assert devices[port].writes[0] == b"U1\n"
        line(d, port, type="heartbeat", slave=sid, state=1, transport="usb")
        line(d, port, type="snapshot", slave=sid, cell=1, id="01:23", transport="usb")
    state = d._snapshot()
    json.dumps(state)  # Public state must not contain serial devices/locks.
    assert len(state["cells"]) == 2
    assert all(s["online"] and s["msg_count"] == 0 for s in state["slaves"])
    assert d.send_led(3)
    assert all(b"C3\n" in x.writes for x in devices.values())
    line(d, "COM2", type="heartbeat", slave=1, state=0, transport="usb")
    assert len(d._snapshot()["cells"]) == 1
    old = list(devices.values())
    d.start_serial("COM1", 115200)
    assert all(x.closed and x.writes[-1] == b"U0\n" for x in old)
    assert not d._snapshot()["cells"]
    d._handle_line('{"type":"added","slave":1,"cell":1}', "COM2", generation)
    assert not d._snapshot()["cells"]
    wait_for(lambda: d._master["open"])
    assert d.send_led(0)
    assert devices["COM1"].writes == [b"C0\n"]


def test_direct_rejects_wrong_firmware_duplicate_ids_and_partial_led_failure(debug):
    d, devices = debug
    d.configure_connection("direct", ["COM2", "COM3"])
    wait_for(lambda: len(devices) == 2 and all(x.writes for x in devices.values()))
    line(d, "COM2", type="heartbeat", slave=1, state=1)
    assert not d._snapshot()["slaves"]
    assert not d.send_led(1)
    d.configure_connection("direct", ["COM2", "COM3"])
    wait_for(lambda: all(c["open"] for c in d._connections.values()))
    line(d, "COM2", type="heartbeat", slave=1, state=0, transport="usb")
    line(d, "COM3", type="heartbeat", slave=1, state=0, transport="usb")
    assert "중복" in d._connections["COM3"]["error"]
    assert not d.send_led(2)
    devices["COM2"].fail_write = True
    assert not d.send_led(2)


def test_serial_buffers_partial_lines_and_renews_usb_lease(debug):
    d, devices = debug
    d.configure_connection("direct", ["COM2"])
    wait_for(lambda: "COM2" in devices and devices["COM2"].writes)
    device = devices["COM2"]
    device.lines.put(b'{"type":"heartbeat","slave":2,')
    device.lines.put(b'"state":32768,"transport":"usb"}\n')
    wait_for(lambda: len(d._snapshot()["cells"]) == 1)
    assert d._snapshot()["cells"][0]["cell"] == 16
    wait_for(lambda: device.writes.count(b"U1\n") >= 2)


@pytest.mark.parametrize("mode,ports,baud", [
    ("invalid", [], 115200), ("master", ["COM1", "COM2"], 115200),
    ("direct", "COM1", 115200), ("direct", [None], 115200),
    ("master", [], "bad"), ("master", [], True),
])
def test_invalid_connection_preserves_current_route(debug, mode, ports, baud):
    d, _ = debug
    generation = d._generation
    with pytest.raises(ValueError):
        d.configure_connection(mode, ports, baud)
    assert d._generation == generation


def test_http_mode_switch_validation_and_state(debug, monkeypatch, tmp_path):
    from server.web import web_server
    from server import text_log
    monkeypatch.setattr(text_log, "SETTINGS_PATH", tmp_path / "log_settings.json")
    monkeypatch.setattr(text_log, "_sink", text_log.LineLog(tmp_path / "old"))
    d, _ = debug
    servers = []
    original = web_server._ThreadingHTTPServer
    def create(*args):
        server = original(*args)
        servers.append(server)
        return server
    monkeypatch.setattr(web_server, "_ThreadingHTTPServer", create)
    def configure(payload):
        d.configure_connection(payload.get("mode"), payload.get("ports"))
        return {"ok": True}
    web_server.start_http_server(
        {"host":"127.0.0.1", "port":0}, lambda e: None,
        get_status=lambda: {"hardware": d._snapshot()},
        get_hardware_ports=lambda: [], on_hardware_connect=configure,
        on_hardware_disconnect=lambda: d.stop_serial())
    wait_for(lambda: bool(servers))
    server = servers[0]
    base = f"http://127.0.0.1:{server.server_port}"
    def post(path, payload):
        req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            return json.load(response)
    try:
        with urllib.request.urlopen(base + "/api/admin/log-settings") as response:
            assert json.load(response)["directory"] == str(tmp_path / "old")
        updated = post("/api/admin/log-settings", {"directory": str(tmp_path / "new")})
        assert updated["ok"] and updated["directory"] == str((tmp_path / "new").resolve())
        with pytest.raises(urllib.error.HTTPError) as error:
            post("/api/admin/log-settings", {"directory": "relative"})
        assert error.value.code == 409
        assert post("/api/admin/hardware/connect", {"mode":"direct", "ports":[]})["ok"]
        with urllib.request.urlopen(base + "/api/status") as response:
            assert json.load(response)["hardware"]["mode"] == "direct"
        with urllib.request.urlopen(base + "/api/admin/hardware/ports") as response:
            assert json.load(response) == {"ports": []}
        with urllib.request.urlopen(base + "/api/admin/logs") as response:
            log_payload = json.load(response)
            assert isinstance(log_payload["logs"], list)
            assert isinstance(log_payload["issues"], list)
        from server.text_log import write_log
        write_log('[recover] slave=1 cell=2 → removed 유실 복구(screen+admin)')
        with urllib.request.urlopen(base + "/api/admin/logs") as response:
            assert any(r['key'] == 'removed' for r in json.load(response)['issues'])
        assert post('/api/admin/issues/clear', {})['ok']
        with urllib.request.urlopen(base + "/api/admin/logs") as response:
            payload = json.load(response)
            assert payload['issues'] == []
            assert any('removed 유실' in r['line'] for r in payload['logs'])
        with pytest.raises(urllib.error.HTTPError) as error:
            post("/api/admin/hardware/connect", [])
        assert error.value.code == 400
        assert post("/api/admin/hardware/connect", {"mode":"master", "ports":[]})["ok"]
        assert post("/api/admin/hardware/disconnect", {})["ok"]
    finally:
        server.shutdown()
        server.server_close()


def test_validated_usb_events_reach_game_callback_and_master_commands_stay_local(debug):
    d, devices = debug
    received = []
    d.on_message = received.append
    d.configure_connection("direct", ["COM2"])
    wait_for(lambda: "COM2" in devices and devices["COM2"].writes)
    line(d, "COM2", type="heartbeat", slave=1, state=0, transport="usb")
    line(d, "COM2", type="added", slave=1, cell=1, id="AA", transport="usb")
    assert [e["type"] for e in received] == ["heartbeat", "added"]
    assert not d.send_line("D1")
    assert d.send_line("P6,2,255,2000")
    assert d.send_line("P1,1,0,460")
    assert d.send_line("H1,16,2000,3000")
    assert d.send_line("H0,0,3,0")
    assert d.send_line("H0,0,4,600")
    assert d.send_line("H0,0,5,600")
    assert d.send_line("H0,0,5")
    assert not d.send_line("H0,0,6,600")
    assert d.send_line("T2000")
    assert d.send_line("B500000000008")
    assert not d.send_line("B900000000000")
    assert not d.send_line("B5000000000080")
    assert d.send_line("Q3,1,31,60000")
    assert not d.send_line("Q3,1,256,460")
    assert not d.send_line("Q3,1,31,60001")
    assert not d.send_line("T60001")
    assert not d.send_line("H1,1,460,3001")
    for invalid in ("P7,1,0,460", "P1,3,0,460", "P1,1,256,460", "P1,1,0,0", "P1,1,0,2001"):
        assert not d.send_line(invalid)
    assert not any(w.startswith(b"D") for w in devices["COM2"].writes)
    line(d, "COM2", type="game", action="start")
    assert len(received) == 2


def test_slave_six_identity_is_reported_before_and_after_usb_heartbeat(debug, monkeypatch):
    import types
    import serial.tools.list_ports
    d, devices = debug
    d.configure_connection("direct", ["COM30"])
    wait_for(lambda: "COM30" in devices and devices["COM30"].writes)
    d._handle_line("#slave id=6", "COM30", d._generation)
    assert d._snapshot()["connections"][0]["reported_slave_id"] == 6
    assert not d.send_led(3)  # Boot identity alone does not prove direct mode is ready.
    line(d, "COM30", type="heartbeat", slave=6, state=0, transport="usb")
    assert d._snapshot()["slaves"][0]["slave"] == 6
    assert d._snapshot()["slaves"][0]["online"]
    port = types.SimpleNamespace(device="COM30", description="USB", manufacturer="",
                                 hwid="", vid=0x1A86, pid=0x55D3)
    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: [port])
    assert d.list_serial_ports()[0]["slave_id"] == 6
    line(d, "COM30", type="heartbeat", slave=6, state=0, transport="usb",
         scan_count=1600, rom_seen_mask=32, crc_error_mask=32, led_effect=3, led_commands=2)
    diagnostic = d._snapshot()["connections"][0]["diagnostics"]
    assert diagnostic == {"scan_count": 1600, "rom_seen_mask": 32, "crc_error_mask": 32,
                          "led_effect": 3, "led_commands": 2}


def test_switch_waits_for_inflight_input_before_reset(debug):
    d, devices = debug
    entered, release = threading.Event(), threading.Event()
    order = []
    def receive(event):
        entered.set()
        release.wait(1)
        d.send_line("D0")  # Must not deadlock with mode change waiting for this reader.
        order.append("event")
    d.on_message = receive
    d.start_serial("COM1", 115200)
    wait_for(lambda: "COM1" in devices)
    devices["COM1"].lines.put(b'{"type":"game","action":"start"}\n')
    assert entered.wait(1)
    worker = threading.Thread(target=lambda: d.configure_connection(
        "direct", [], before_start=lambda: order.append("reset")))
    worker.start()
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert order == ["event", "reset"]


def test_main_usb_input_drives_game_and_snapshot_does_not_repeat_midi(debug, monkeypatch):
    import sys
    import types
    from test_main_runtime_integration import _load_main_module, PROJECT_ROOT

    d, devices = debug
    main = _load_main_module()
    monkeypatch.setattr(main, "hardware_io", d)
    monkeypatch.setattr(main, "_serial_writer", d)
    midi_calls = []
    midi = types.ModuleType("server.inout.midi_out")
    midi.cell_to_pitch = lambda cell: (int(cell) - 1) % 8
    midi.trigger = lambda *args, **kwargs: midi_calls.append(args) or {"ok": True}
    monkeypatch.setitem(sys.modules, "server.inout.midi_out", midi)
    uid = "AA:00:00:00:00:00:00:01"
    monkeypatch.setattr(main, "load_mapping", lambda: {uid: {"voice": "aespa_1"}})
    try:
        main.configure_hardware({"mode": "direct", "ports": ["COM2"]})
        wait_for(lambda: "COM2" in devices and devices["COM2"].writes)
        wait_for(lambda: main.get_game_state()["state"] == "READY")
        main.start_game()
        line(d, "COM2", type="heartbeat", slave=1, state=0, transport="usb")
        line(d, "COM2", type="added", slave=1, cell=1, id=uid, transport="usb")
        assert main.get_cells()["1:1"]["voice"] == "aespa_1"
        assert main.get_game_state()["state"] == "RUNNING"
        assert len(midi_calls) == 1
        revision = main.get_game_state()["revision"]
        for _ in range(3):
            line(d, "COM2", type="snapshot", slave=1, cell=1, id=uid, transport="usb")
        assert len(midi_calls) == 1
        assert main.get_game_state()["revision"] == revision
        # Firmware's state field repairs a lost removal after two stable heartbeats.
        line(d, "COM2", type="heartbeat", slave=1, state=0, transport="usb")
        line(d, "COM2", type="heartbeat", slave=1, state=0, transport="usb")
        assert main.get_cells() == {}
        line(d, "COM2", type="snapshot", slave=1, cell=1, id=uid, transport="usb")
        assert main.get_cells()["1:1"]["id"] == uid
        main.configure_hardware({"mode": "master", "ports": []})
        assert main.get_cells() == {}
        assert main.get_game_state()["state"] != "RUNNING"
        assert main.get_status()["hardware"]["mode"] == "master"
        assert devices["COM2"].writes[-1] == b"U0\n"
    finally:
        main.stop_serial_input()
        if main._game_runtime is not None:
            main._game_runtime.close()
        sys.path[:] = [entry for entry in sys.path if entry != str(PROJECT_ROOT)]
        for name in ("server.main", "serial", "server.inout.serial_io"):
            sys.modules.pop(name, None)


def loss_lines(d):
    return [line for path in sorted(d.LOG_DIR.glob("*.txt"))
            for line in path.read_text(encoding="utf-8").splitlines() if "통신 끊김 |" in line]


def test_outages_are_logged_without_browser_once_and_rearmed_on_recovery(debug, monkeypatch):
    d, devices = debug
    monkeypatch.setattr(d, "ONLINE_TIMEOUT_S", 0.1)
    d.configure_connection("direct", ["COM30"])
    wait_for(lambda: "COM30" in devices and devices["COM30"].writes)
    line(d, "COM30", type="heartbeat", slave=6, state=0, transport="usb")
    wait_for(lambda: len(loss_lines(d)) == 1)
    path = next(d.LOG_DIR.glob("*.txt"))
    text = path.read_text(encoding="utf-8")
    assert "Slave 6" in text and "COM30" in text and "mode=direct" in text
    assert "미수신" in text
    with d._lock:
        d._check_slave_losses_locked()
        d._check_slave_losses_locked()
    assert len(loss_lines(d)) == 1
    line(d, "COM30", type="heartbeat", slave=6, state=0, transport="usb")
    wait_for(lambda: len(loss_lines(d)) == 2)
    import re
    assert re.match(r"orbbridge_\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]+_001\.txt", path.name)
    assert re.match(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}\]", text)


def test_master_port_loss_logs_each_observed_slave_only_once(debug):
    d, devices = debug
    d.start_serial("COM1", 115200)
    wait_for(lambda: "COM1" in devices and d._master["open"])
    for sid in (1, 6):
        line(d, "COM1", type="heartbeat", slave=sid, state=0)
    devices["COM1"].fail_write = True
    assert not d.send_led(3)
    with d._lock:
        d._port_lost_locked(d._master, "same outage")
    lines = loss_lines(d)
    assert len(lines) == 2
    assert "Slave 1" in lines[0] and "Slave 6" in lines[1]
    assert all("mode=master" in entry and "USB/시리얼" in entry for entry in lines)


def test_normal_traffic_and_manual_disconnect_do_not_create_fault_log(debug):
    d, devices = debug
    d.configure_connection("direct", ["COM30"])
    wait_for(lambda: "COM30" in devices and devices["COM30"].writes)
    line(d, "COM30", type="heartbeat", slave=6, state=0, transport="usb")
    d.stop_serial()
    assert not loss_lines(d)
    assert "heartbeat" in next(d.LOG_DIR.glob("*.txt")).read_text(encoding="utf-8")
