from pathlib import Path

import pytest

from server.game.melody_manager import MelodyManager, rotation_plan_from_config
from server.game.melody_manager import game_timers_from_config, game_rules_from_melody


def test_timers_are_global_and_old_song_timers_are_ignored():
    song = {"game_rules": {"time_over_s": 1, "idle_s": 2, "dfesta_presses_required": 3}}
    rules = {**game_rules_from_melody(song), **game_timers_from_config(
        {"game_rules": {"time_over_s": 180, "idle_s": 45}})}
    assert rules == {"time_over_s": 180, "idle_s": 45, "dfesta_presses_required": 3}
    assert game_timers_from_config({}) == {"time_over_s": 120, "idle_s": 30}


@pytest.mark.parametrize("value", [0, -1, True, "bad", float("inf")])
def test_invalid_global_timers_are_rejected(value):
    with pytest.raises(ValueError):
        game_timers_from_config({"game_rules": {"idle_s": value}})


AVAILABLE = ["dynamite", "Iam", "biplane"]


def _melody_directory(tmp_path: Path) -> Path:
    for melody_id in AVAILABLE:
        (tmp_path / f"{melody_id}.json").write_text("{}\n", encoding="utf-8")
    return tmp_path


def test_single_melody_setting_remains_fixed():
    plan = rotation_plan_from_config({"melody": "Iam"}, AVAILABLE)

    assert plan.mode == "fixed"
    assert plan.fixed_id == "Iam"
    assert plan.playlist == ("Iam",)


def test_sequence_advances_only_after_successful_commit(tmp_path):
    config = {
        "melody": "dynamite",
        "melody_rotation": {
            "mode": "sequence",
            "playlist": AVAILABLE,
        },
    }
    manager = MelodyManager(_melody_directory(tmp_path))

    assert manager.preview(config) == "dynamite"
    assert manager.reserve(config) == "dynamite"
    manager.cancel()
    assert manager.reserve(config) == "dynamite"

    manager.commit()
    assert manager.reserve(config) == "Iam"
    manager.commit()
    assert manager.reserve(config) == "biplane"
    manager.commit()
    assert manager.reserve(config) == "dynamite"


@pytest.mark.parametrize(
    ("rotation", "message"),
    [
        ({"mode": "random", "playlist": ["dynamite"]}, "fixed or sequence"),
        ({"mode": "sequence", "playlist": []}, "must contain melody ids"),
        (
            {"mode": "sequence", "playlist": ["dynamite", "dynamite"]},
            "duplicate",
        ),
        ({"mode": "sequence", "playlist": ["missing"]}, "does not exist"),
    ],
)
def test_invalid_rotation_settings_are_rejected(rotation, message):
    with pytest.raises(ValueError, match=message):
        rotation_plan_from_config(
            {"melody": "dynamite", "melody_rotation": rotation},
            AVAILABLE,
        )



def test_added_file_joins_sequence_without_restarting_cursor(tmp_path):
    manager = MelodyManager(_melody_directory(tmp_path))
    config = {"melody": "dynamite", "melody_rotation": {"mode": "sequence", "playlist": AVAILABLE}}
    assert manager.reserve(config) == "dynamite"
    manager.commit()
    (tmp_path / "new_song.json").write_text("{}", encoding="utf-8")
    for expected in ["Iam", "biplane", "new_song", "dynamite"]:
        assert manager.reserve(config) == expected
        manager.commit()
    fixed = {"melody": "Iam", "melody_rotation": {"mode": "fixed", "playlist": ["Iam"]}}
    assert manager.reserve(fixed) == "Iam"
    manager.commit()
    assert manager.reserve(fixed) == "Iam"
