import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from app_paths import data_dir, melodies_dir
from melody_register_core import normalize_melody, to_save_payload
from melody_store import (
    answer_sequence_from_melody,
    game_rules_from_melody,
    load_melody_document,
)


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
    assert not (ROOT / "server" / "stage_data.json").exists()


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


def test_editor_round_trip_preserves_game_rules():
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
    screen = (ROOT / "screen" / "js" / "melody-loader.js").read_text(
        encoding="utf-8"
    )
    table = (ROOT / "table" / "index.html").read_text(encoding="utf-8")

    assert "/api/melody" in screen
    assert "/api/melody" in table
    assert "/screen/melodies" not in screen
    assert "/screen/melodies" not in table
