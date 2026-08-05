import pytest

from app.melody_rotation import (
    MelodyRotator,
    rotation_plan_from_config,
)


AVAILABLE = ["dynamite", "Iam", "biplane"]


def test_legacy_melody_setting_remains_fixed():
    plan = rotation_plan_from_config({"melody": "Iam"}, AVAILABLE)

    assert plan.mode == "fixed"
    assert plan.first_id == "Iam"
    assert plan.playlist == ("Iam",)


def test_sequence_advances_only_after_successful_commit():
    config = {
        "melody": "dynamite",
        "melody_rotation": {
            "mode": "sequence",
            "playlist": AVAILABLE,
        },
    }
    rotator = MelodyRotator()

    assert rotator.preview(config, AVAILABLE) == "dynamite"
    assert rotator.reserve(config, AVAILABLE) == "dynamite"
    rotator.cancel()
    assert rotator.reserve(config, AVAILABLE) == "dynamite"

    rotator.commit()
    assert rotator.reserve(config, AVAILABLE) == "Iam"
    rotator.commit()
    assert rotator.reserve(config, AVAILABLE) == "biplane"
    rotator.commit()
    assert rotator.reserve(config, AVAILABLE) == "dynamite"


@pytest.mark.parametrize(
    ("rotation", "message"),
    [
        ({"mode": "random", "playlist": ["dynamite"]}, "fixed or sequence"),
        ({"mode": "sequence", "playlist": []}, "must not be empty"),
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
