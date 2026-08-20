import json
from pathlib import Path

from server.web import web_server
from server.inout.serial_io import run_serial_loop
from server.voice_catalog import list_catalog_voices, voice_api_payload


ROOT = Path(__file__).resolve().parents[1]


def test_m6_directories_match_runtime_roles():
    assert (ROOT / "server" / "main.py").is_file()
    assert (ROOT / "server" / "game" / "game_engine.py").is_file()
    assert (ROOT / "server" / "game" / "game_runtime.py").is_file()
    assert (ROOT / "server" / "game" / "scheduler.py").is_file()
    assert (ROOT / "server" / "game" / "melody_manager.py").is_file()
    assert (ROOT / "server" / "inout" / "serial_io.py").is_file()
    assert (ROOT / "server" / "inout" / "matrix_display.py").is_file()
    assert (ROOT / "server" / "inout" / "midi_out.py").is_file()
    assert (ROOT / "server" / "web" / "web_server.py").is_file()
    assert (ROOT / "server" / "web" / "websocket_service.py").is_file()
    for moved in (
        "game_engine.py",
        "game_runtime.py",
        "scheduler.py",
        "melody_manager.py",
        "serial_io.py",
        "matrix_display.py",
        "midi_out.py",
        "web_server.py",
        "websocket_service.py",
    ):
        assert not (ROOT / "server" / moved).exists()
    assert (ROOT / "firmware" / "dotmatrix" / "dotmatrix.ino").is_file()
    assert (ROOT / "admin" / "hardware" / "index.html").is_file()
    assert (ROOT / "admin" / "software" / "index.html").is_file()
    assert (ROOT / "admin" / "simulation" / "screen" / "index.html").is_file()
    assert (ROOT / "admin" / "simulation" / "table" / "index.html").is_file()
    assert not (ROOT / "web").exists()
    assert (ROOT / "tools" / "orb_register.py").is_file()
    assert (ROOT / "tools" / "melody_register.py").is_file()
    assert (ROOT / "tools" / "midi_debug.py").is_file()
    assert (ROOT / "tools" / "debug" / "__main__.py").is_file()
    assert (ROOT / "audioengine" / "loopmidi_portsetup.reg").is_file()

    for legacy in ("app", "screen", "table", "debug"):
        assert not (ROOT / legacy).exists()


def test_config_has_one_source_and_uses_web_name():
    config = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))

    assert not (ROOT / "data" / "config.table.json").exists()
    assert "web" in config
    assert "mock" not in config
    assert "debug" not in config
    assert "audio_output" not in config
    assert config["matrix_display"]["baudrate"] == 115200


def test_removed_sample_and_editor_paths_do_not_return():
    assert not (ROOT / "data" / "sounds").exists()
    assert not list(ROOT.rglob("*voice_register*"))
    assert not list(ROOT.rglob("*melody_editor*"))
    assert not (ROOT / "server" / "melody_store.py").exists()
    assert not (ROOT / "server" / "melody_rotation.py").exists()
    assert not (ROOT / "server" / "voice_store.py").exists()
    assert (ROOT / "server" / "game" / "melody_manager.py").is_file()
    assert (ROOT / "server" / "voice_catalog.py").is_file()
    assert not (
        ROOT / "admin" / "simulation" / "screen" / "js" / "audio.js"
    ).exists()

    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for folder in (ROOT / "server", ROOT / "tools", ROOT / "admin")
        for path in folder.rglob("*")
        if path.is_file() and path.suffix in {".py", ".js", ".html"}
    )
    for legacy in ("config.table.json", "mock_server", "start_mock_server", "/sounds/"):
        assert legacy not in runtime_text


def test_voice_catalog_contains_metadata_without_missing_sample_paths():
    catalog = json.loads(
        (ROOT / "data" / "voice_catalog.json").read_text(encoding="utf-8")
    )

    assert catalog["voices"]
    assert all("notes" not in profile for profile in catalog["voices"].values())
    assert all("color" in profile for profile in catalog["voices"].values())
    assert all("wave" not in profile for profile in catalog["voices"].values())
    assert all("filter" not in profile for profile in catalog["voices"].values())
    assert all("gain" not in profile for profile in catalog["voices"].values())


def test_explicit_empty_mapping_does_not_reload_saved_mapping():
    assert list_catalog_voices({}) == []
    assert voice_api_payload({}) == {"voices": [], "index": {}}


def test_web_server_uses_the_admin_static_root():
    assert web_server.ADMIN_ROOT == ROOT / "admin"
    assert web_server.HARDWARE_ADMIN_UI == ROOT / "admin" / "hardware"
    assert web_server.SOFTWARE_ADMIN_UI == ROOT / "admin" / "software"
    assert web_server.SCREEN_SIM_UI == ROOT / "admin" / "simulation" / "screen"
    assert web_server.TABLE_SIM_UI == ROOT / "admin" / "simulation" / "table"


def test_windows_launchers_use_module_entrypoints_without_removed_paths():
    scripts = [
        ROOT / "start.bat",
        ROOT / "tools" / "melody_register.bat",
        ROOT / "tools" / "orb_register.bat",
        ROOT / "tools" / "midi_debug.bat",
        ROOT / "tools" / "debug" / "debug.bat",
        ROOT / "build" / "build_orbbridge.bat",
        ROOT / "build" / "build_orb_register.bat",
    ]

    assert all(path.is_file() for path in scripts)
    text = "\n".join(path.read_text(encoding="utf-8") for path in scripts).lower()
    assert "bridge\\bridge.py" not in text
    assert "python main.py" not in text
    assert "melody_editor" not in text
    assert "-m server.launcher" in text
    assert "-m tools.melody_register" in text
    assert "-m tools.debug" in text
    assert "-m tools.midi_debug" in text


def test_admin_exposes_midi_debug_without_screen_midi_legacy():
    server = (
        ROOT / "server" / "web" / "web_server.py"
    ).read_text(encoding="utf-8")
    admin = (ROOT / "admin" / "software" / "index.html").read_text(
        encoding="utf-8"
    )
    screen = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "admin" / "simulation" / "screen").rglob("*.js")
    )

    assert "/api/admin/midi/status" in server
    assert "/api/admin/midi/test" in server
    assert "/api/admin/midi/stop" in server
    assert "/api/admin/midi/test" in admin
    assert "/api/midi/note" not in server
    assert "/api/midi/notes" not in server
    assert "AudioContext" not in screen


def test_software_admin_combines_display_and_control():
    hardware = (ROOT / "admin" / "hardware" / "index.html").read_text(
        encoding="utf-8"
    )
    software = (ROOT / "admin" / "software" / "index.html").read_text(
        encoding="utf-8"
    )
    table = (
        ROOT / "admin" / "simulation" / "table" / "index.html"
    ).read_text(encoding="utf-8")
    screen_html = (
        ROOT / "admin" / "simulation" / "screen" / "index.html"
    ).read_text(encoding="utf-8")
    screen_js = (
        ROOT / "admin" / "simulation" / "screen" / "js" / "app.js"
    ).read_text(encoding="utf-8")
    server = (
        ROOT / "server" / "web" / "web_server.py"
    ).read_text(encoding="utf-8")

    assert hardware != software
    assert "const CONTROL_MODE = 'hardware'" in hardware
    assert "const CONTROL_MODE = 'software'" in software
    assert "/admin/simulation/screen/" not in hardware
    assert 'src="/admin/simulation/screen/"' in software
    assert 'src="/admin/simulation/table/"' in software
    assert 'href="/admin/software"' in hardware
    assert 'href="/admin"' in software
    assert 'href="/admin/display"' not in hardware + software
    assert "status-dots" not in table
    assert "conn-dot" not in screen_html
    assert '"/admin/software"' in server
    assert '"/admin/display"' in server
    assert "/api/admin/game/auto-complete" in software
    assert "/api/admin/orbs/collect" in software
    assert "/api/admin/game/auto-complete" in server
    assert "/api/admin/orbs/collect" in server
    assert "btn-enter" not in screen_html
    assert "/api/game/start" not in screen_js
    assert "/api/game/reset" not in screen_js
    assert "/api/game/end" not in screen_js


def test_browser_tabs_open_in_operator_order():
    launcher = (ROOT / "server" / "launcher.py").read_text(encoding="utf-8")
    windows = (ROOT / "start.bat").read_text(encoding="utf-8")
    paths = ("/admin", "/admin/software")

    for text in (launcher, windows):
        positions = [text.index(path) for path in paths]
        assert positions == sorted(positions)
        assert "8080/admin/display" not in text
        assert "8080/screen" not in text


def test_loopmidi_registry_ports_match_portmapping():
    mapping = json.loads(
        (ROOT / "data" / "portmapping.json").read_text(encoding="utf-8")
    )
    expected = {entry["port"] for entry in mapping.values()}
    registry = (
        ROOT / "audioengine" / "loopmidi_portsetup.reg"
    ).read_text(encoding="utf-8")
    ports_section = registry.split("\\loopMIDI\\Ports]", 1)[1]
    actual = {
        line.split('"', 2)[1]
        for line in ports_section.splitlines()
        if line.startswith('"') and "=dword:00000001" in line
    }

    assert expected <= actual
    assert actual - expected == {"event"}


def test_serial_reader_forwards_json_and_reports_status(monkeypatch):
    import threading
    import types

    stop_event = threading.Event()
    statuses = []
    messages = []

    class FakeDevice:
        def __init__(self):
            self.lines = [
                b"#master alive\n",
                b"boot log\n",
                b'{"type":"game","action":"start"}\n',
            ]
            self.closed = False

        def readline(self):
            return self.lines.pop(0) if self.lines else b""

        def close(self):
            self.closed = True

    device = FakeDevice()
    fake_serial = types.SimpleNamespace(
        Serial=lambda *args, **kwargs: device,
        SerialException=RuntimeError,
    )
    monkeypatch.setattr("server.inout.serial_io.serial", fake_serial)

    def on_message(message):
        messages.append(message)
        stop_event.set()

    run_serial_loop(
        {"port": "COM_TEST", "baudrate": 115200},
        on_message,
        lambda connected, port, error: statuses.append((connected, port, error)),
        stop_event,
        retry_delay_s=0,
    )

    assert messages == [{"type": "game", "action": "start"}]
    assert statuses[0] == (False, "COM_TEST", "연결 중…")
    assert statuses[1] == (True, "COM_TEST", "")
    assert statuses[-1] == (False, "COM_TEST", "중지됨")
    assert device.closed is True
