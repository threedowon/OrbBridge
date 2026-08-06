import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

from app.app_paths import data_dir, melodies_dir
from app.melody_manager import (
    answer_sequence_from_melody,
    game_rules_from_melody,
    load_melody_document,
)
from tools.melody_register_core import normalize_melody, to_save_payload
import tools.melody_register_core as melody_register_core


def test_all_project_json_lives_under_data():
    json_files = [
        path
        for path in ROOT.rglob("*.json")
        if ".pytest_cache" not in path.parts
    ]

    assert json_files
    assert all(path.is_relative_to(ROOT / "data") for path in json_files)
    assert data_dir() == ROOT / "data"
    assert melodies_dir() == ROOT / "data" / "melodies"
    assert not (ROOT / "app" / "stage_data.json").exists()


def test_one_melody_document_drives_answer_playback_and_rules():
    config = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    document = load_melody_document(
        ROOT / "data" / "melodies" / f"{config['melody']}.json"
    )

    assert answer_sequence_from_melody(document) == {
        "1": "C",
        "2": "A",
        "3": "G",
        "4": "F",
        "5": "E",
        "6": "D",
        "7": "C",
        "8": "C",
        "9": "R",
        "10": "R",
        "11": "R",
        "12": "R",
    }
    assert game_rules_from_melody(document) == {
        "time_over_s": 120.0,
        "idle_s": 30.0,
        "dfesta_presses_required": 1,
    }


def test_configured_playlist_points_to_valid_melody_documents():
    config = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    rotation = config["melody_rotation"]

    assert rotation["mode"] == "sequence"
    assert rotation["playlist"] == ["dynamite", "Iam", "biplane"]
    for melody_id in rotation["playlist"]:
        document = load_melody_document(
            ROOT / "data" / "melodies" / f"{melody_id}.json"
        )
        assert document["songId"] == melody_id
        assert document["steps"]
        assert len(answer_sequence_from_melody(document)) == len(document["steps"])
        assert game_rules_from_melody(document)["dfesta_presses_required"] >= 1


def test_melody_register_saves_sequence_without_overwriting_other_config(monkeypatch):
    original = {
        "melody": "dynamite",
        "input_mode": "serial",
    }
    saved = []
    monkeypatch.setattr(melody_register_core, "load_config", lambda: dict(original))
    monkeypatch.setattr(
        melody_register_core,
        "list_melody_ids",
        lambda: ["dynamite", "Iam", "biplane"],
    )
    monkeypatch.setattr(melody_register_core, "save_config", saved.append)

    result = melody_register_core.save_rotation_settings(
        "sequence",
        ["Iam", "biplane", "dynamite"],
    )

    assert result["playlist"] == ["Iam", "biplane", "dynamite"]
    assert saved[0]["melody"] == "Iam"
    assert saved[0]["melody_rotation"] == {
        "mode": "sequence",
        "playlist": ["Iam", "biplane", "dynamite"],
    }
    assert saved[0]["input_mode"] == "serial"
    assert "audio_output" not in saved[0]


def test_melody_register_round_trip_preserves_game_rules():
    source = {
        "songId": "test",
        "title": "Test",
        "artist": "",
        "bpm": 120,
        "range": ["C4", "C5"],
        "game_rules": {
            "time_over_s": 90,
            "idle_s": 20,
            "dfesta_presses_required": 2,
        },
        "steps": [{"step": 1, "note": "C4", "duration": 1}],
    }

    normalized = normalize_melody(source, "test")
    saved = to_save_payload(normalized)

    assert saved["game_rules"] == {
        "time_over_s": 90.0,
        "idle_s": 20.0,
        "dfesta_presses_required": 2,
    }


def test_web_clients_load_the_server_melody_contract():
    screen = (ROOT / "web" / "screen" / "js" / "melody-loader.js").read_text(
        encoding="utf-8"
    )
    table = (ROOT / "web" / "table" / "index.html").read_text(encoding="utf-8")

    assert "/api/melody" in screen
    assert "/api/melody" in table
    assert "/screen/melodies" not in screen
    assert "/screen/melodies" not in table
