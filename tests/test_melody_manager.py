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


def test_random_draw_is_stable_until_commit_and_can_repeat(tmp_path):
    class Draws:
        calls = 0
        def randrange(self, size):
            self.calls += 1
            return [2, 2, 0][self.calls - 1]
    rng = Draws()
    manager = MelodyManager(_melody_directory(tmp_path), rng=rng)
    config = {"melody_rotation": {"mode": "random", "playlist": AVAILABLE}}
    assert manager.preview(config) == "biplane"
    assert manager.reserve(config) == "biplane"
    manager.cancel()
    assert manager.reserve(config) == "biplane"
    assert rng.calls == 1
    manager.commit()
    assert manager.reserve(config) == "biplane"
    manager.commit()
    assert manager.reserve(config) == "dynamite"
    assert rng.calls == 3


@pytest.mark.parametrize("mode", ["fixed", "sequence", "random"])
def test_deleted_legacy_references_fall_back_to_current_library(tmp_path, mode):
    (tmp_path / "new_song.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    config = {"melody": "biplane", "melody_rotation": {"mode": mode, "playlist": AVAILABLE}}
    manager = MelodyManager(tmp_path)
    assert manager.available_ids() == ["new_song"]
    assert manager.reserve(config) == "new_song"
    manager.commit()
    (tmp_path / "new_song.json").unlink()
    (tmp_path / "replacement.json").write_text("{}", encoding="utf-8")
    assert manager.reserve(config) == "replacement"


def test_empty_library_reports_clear_error():
    with pytest.raises(ValueError, match="contains no melody JSON"):
        rotation_plan_from_config({"melody_rotation": {"mode": "random"}}, [])


def test_bundled_melodies_seed_once_without_restoring_deleted_songs(tmp_path, monkeypatch):
    from server import app_paths
    bundle = tmp_path / "bundle"
    dest = tmp_path / "runtime"
    (bundle / "melodies").mkdir(parents=True)
    (bundle / "melodies" / "biplane.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app_paths, "data_dir", lambda: dest)
    monkeypatch.setattr(app_paths, "bundled_data_dir", lambda: bundle)
    app_paths.ensure_data_files()
    seeded = dest / "melodies" / "biplane.json"
    assert seeded.exists()
    seeded.unlink()
    (dest / "melodies" / "new_song.json").write_text("{}", encoding="utf-8")
    app_paths.ensure_data_files()
    assert not seeded.exists()
    assert (dest / "melodies" / "new_song.json").exists()


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
        ({"mode": "shuffle_typo", "playlist": ["dynamite"]}, "fixed, sequence or random"),
        ({"mode": "sequence", "playlist": [""]}, "must contain melody ids"),
        (
            {"mode": "sequence", "playlist": ["dynamite", "dynamite"]},
            "duplicate",
        ),
        ({"mode": "sequence", "playlist": "missing"}, "must be an array"),
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
