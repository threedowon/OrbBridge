from pathlib import Path

import pytest

from server.game.melody_manager import MelodyManager, rotation_plan_from_config


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

