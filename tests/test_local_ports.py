import json

from server import data_store


def test_local_ports_override_and_stay_out_of_shared_saves(tmp_path, monkeypatch):
    shared = tmp_path / "config.json"
    monkeypatch.setattr(data_store, "CONFIG_PATH", shared)
    shared.write_text(json.dumps({"serial": {"port": "COM7", "baudrate": 115200},
                                 "matrix_display": {"port": "COM8", "enabled": True}}))
    config = data_store.load_config()
    local = tmp_path / "ports.json"
    assert json.loads(local.read_text())["serial"]["port"] == "COM7"
    local.write_text(json.dumps({"serial": {"port": "COM19"},
                                "matrix_display": {"port": "COM3"}}))
    config = data_store.load_config()
    assert config["serial"] == {"port": "COM19", "baudrate": 115200}
    assert config["matrix_display"]["port"] == "COM3"
    before = local.read_bytes()
    data_store.save_config(config)
    assert "port" not in json.loads(shared.read_text())["serial"]
    assert "port" not in json.loads(shared.read_text())["matrix_display"]
    assert config["serial"]["port"] == "COM19"
    assert local.read_bytes() == before
    assert data_store.load_config() == config


def test_first_run_defaults_and_unrelated_json(tmp_path, monkeypatch):
    shared = tmp_path / "config.json"
    shared.write_text('{}')
    monkeypatch.setattr(data_store, "CONFIG_PATH", shared)
    assert data_store.load_config() == {"serial": {"port": "COM19"},
                                       "matrix_display": {"port": "COM3"}}
    other = tmp_path / "other.json"
    data_store.save_config({"serial": {"port": "COM99"}}, other)
    assert data_store.load_config(other) == {"serial": {"port": "COM99"}}
