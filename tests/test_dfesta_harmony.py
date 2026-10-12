"""DFesta choir contracts, including the editor and the shipped arrangements."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from server.game.scheduler import NOTE_PITCH, build_plan, parse_melody
from tools.melody_register_core import (
    normalize_melody, save_melody, to_save_payload, validate_melody,
)
import tools.melody_register_core as editor


ROOT = Path(__file__).resolve().parents[1]


def song():
    return {
        "songId": "choir", "bpm": 120,
        "dfesta": {"weights": {"melody": 70, "harmony_a": 15, "harmony_b": 15}},
        "steps": [
            {"note": "C4", "duration": 1, "harmony": ["E4", "G4"]},
            {"note": "G4", "duration": 0.5, "harmony": ["B4", "D4"]},
            {"note": "REST", "duration": 1},
            {"note": "A4", "duration": 1},
        ],
    }


def voices(count):
    return {
        str(i): {"position": i + 1, "cell": i % 8 + 1, "voice": f"bts_{i + 1}"}
        for i in range(count)
    }


@pytest.mark.parametrize("count", [0, 1, 4, 5, 8, 10, 11, 12, 24, 48])
def test_every_member_sings_once_with_melody_majority_and_fixed_parts(count):
    melody = parse_melody(song())
    plan = build_plan("dfesta", melody, voices(count), rng=random.Random(7))
    first, second, rest, unison = plan.cues
    assert len(first.notes) == count
    assert len({note["voice"] for note in first.notes}) == count
    distribution = Counter(note["pitch"] for note in first.notes)
    if count >= 5:
        assert distribution[0] > count / 2
        assert distribution[2] >= 1 and distribution[4] >= 1
    else:
        assert all(note["pitch"] == 0 for note in first.notes)
    following = {note["voice"]: note["pitch"] for note in second.notes}
    for note in first.notes:
        assert following[note["voice"]] == {0: 4, 2: 6, 4: 1}[note["pitch"]]
    assert rest.notes == ()
    assert all(note["pitch"] == 5 for note in unison.notes)
    assert [cue.offset_s for cue in plan.cues] == pytest.approx([0.25, 0.75, 1, 1.5])
    assert {note["duration_ms"] for note in second.notes} <= {250}


def test_weighted_quota_and_random_members_are_independent():
    melody = parse_melody(song())
    first = build_plan("dfesta", melody, voices(10), rng=random.Random(1))
    repeat = build_plan("dfesta", melody, voices(10), rng=random.Random(1))
    other = build_plan("dfesta", melody, voices(10), rng=random.Random(2))
    assert first == repeat
    assert first.cues[0].notes != other.cues[0].notes
    assert sorted(Counter(note["pitch"] for note in first.cues[0].notes).values()) == [1, 2, 7]
    custom = song()
    custom["dfesta"]["weights"] = {"melody": 60, "harmony_a": 30, "harmony_b": 10}
    plan = build_plan("dfesta", parse_melody(custom), voices(20), rng=random.Random(1))
    assert Counter(note["pitch"] for note in plan.cues[0].notes) == {0: 12, 2: 6, 4: 2}


def test_duplicates_unknowns_and_snapshot_changes_do_not_add_singers():
    cells = voices(8)
    cells["duplicate"] = dict(cells["0"])
    cells["unknown"] = {"voice": "unknown"}
    cells["empty"] = {"voice": ""}
    plan = build_plan("dfesta", parse_melody(song()), cells, rng=random.Random(1))
    cells.clear()
    assert len(plan.cues[0].notes) == 8


@pytest.mark.parametrize("mode", ["listen", "play"])
def test_harmony_does_not_change_listen_or_play(mode):
    original = song()
    legacy = deepcopy(original)
    legacy.pop("dfesta")
    for step in legacy["steps"]:
        step.pop("harmony", None)
    assert build_plan(mode, parse_melody(original), voices(12)) == build_plan(
        mode, parse_melody(legacy), voices(12)
    )


def test_legacy_json_stays_unison_and_metadata_is_not_needed_for_playback():
    raw = song()
    raw.pop("dfesta")
    for step in raw["steps"]:
        step.pop("harmony", None)
    plan = build_plan("dfesta", parse_melody(raw), voices(10))
    assert {note["pitch"] for note in plan.cues[0].notes} == {0}
    assert parse_melody(raw) == parse_melody({**raw, "title": "Title", "artist": "Artist"})


@pytest.mark.parametrize("harmony", [None, [], ["E4"], ["E4", "G4", "B4"],
    "E4 G4", ["D5", "G4"], ["F#4", "G4"], ["REST", "G4"], [True, "G4"]])
def test_invalid_harmony_is_rejected_by_runtime_and_editor(harmony):
    raw = song()
    raw["steps"][0]["harmony"] = harmony
    with pytest.raises(ValueError):
        parse_melody(raw)
    assert validate_melody(raw)


@pytest.mark.parametrize("weights", [None, {}, [70, 15, 15],
    {"melody": 50, "harmony_a": 25, "harmony_b": 25},
    {"melody": 70, "harmony_a": 0, "harmony_b": 15},
    {"melody": 70, "harmony_a": -1, "harmony_b": 15},
    {"melody": True, "harmony_a": 15, "harmony_b": 15},
    {"melody": float("nan"), "harmony_a": 15, "harmony_b": 15},
    {"melody": float("inf"), "harmony_a": 15, "harmony_b": 15}])
def test_invalid_weights_are_rejected(weights):
    raw = song()
    raw["dfesta"]["weights"] = weights
    with pytest.raises(ValueError):
        parse_melody(raw)


def test_harmony_cannot_sound_during_a_melody_rest():
    raw = song()
    raw["steps"][2]["harmony"] = ["E4", "G4"]
    with pytest.raises(ValueError, match="rest together"):
        parse_melody(raw)
    raw["steps"][2]["harmony"] = ["REST", "REST"]
    assert build_plan("dfesta", parse_melody(raw), voices(8)).cues[2].notes == ()


def test_editor_round_trip_and_file_save_preserve_harmony(tmp_path, monkeypatch):
    raw = song()
    normalized = normalize_melody(raw, "choir")
    payload = to_save_payload(normalized)
    assert payload["dfesta"] == raw["dfesta"]
    assert payload["steps"][0]["harmony"] == ["E4", "G4"]
    assert normalized["steps"][0]["harmony"] is not raw["steps"][0]["harmony"]
    monkeypatch.setattr(editor, "melodies_dir", lambda: tmp_path)
    saved = save_melody(normalized)
    assert parse_melody(json.loads(saved.read_text(encoding="utf-8"))) == parse_melody(raw)
    normalized["steps"][0]["harmony"] = ["C6", "G4"]
    before = saved.read_bytes()
    with pytest.raises(ValueError):
        save_melody(normalized)
    assert saved.read_bytes() == before


@pytest.mark.parametrize("melody_id, count", [("dynamite", 8), ("Iam", 11), ("biplane", 10)])
def test_shipped_arrangements_have_three_distinct_diatonic_chord_tones(melody_id, count):
    raw = json.loads((ROOT / "data" / "melodies" / f"{melody_id}.json").read_text(encoding="utf-8"))
    assert len(raw["steps"]) == 12
    pitched = [step for step in raw["steps"] if step["note"] != "REST"]
    assert len(pitched) == count
    semitones = dict(zip(NOTE_PITCH, [0, 2, 4, 5, 7, 9, 11, 12]))
    for step in pitched:
        notes = [step["note"], *step["harmony"]]
        assert all(note in NOTE_PITCH for note in notes)
        pcs = {semitones[note] % 12 for note in notes}
        assert len(pcs) == 3
        assert any({(pitch - root) % 12 for pitch in pcs} in ({0, 4, 7}, {0, 3, 7}) for root in pcs)
    melody = parse_melody(raw)
    assert parse_melody(to_save_payload(normalize_melody(raw))) == melody
    plan = build_plan("dfesta", melody, voices(count), rng=random.Random(5))
    for cue, step in zip(plan.cues, melody.steps):
        assert all(0 <= note["pitch"] <= 7 for note in cue.notes)
        if step.pitch is not None:
            assert len(cue.notes) == count
            assert len({note["pitch"] for note in cue.notes}) == 3
            assert sum(note["pitch"] == step.pitch for note in cue.notes) > count / 2


def test_iam_swing_pairs_fill_exact_beats_after_editor_save():
    raw = json.loads((ROOT / "data" / "melodies" / "Iam.json").read_text(encoding="utf-8"))
    melody = parse_melody(to_save_payload(normalize_melody(raw)))
    assert sum(step.duration_beats for step in melody.steps[4:6]) == pytest.approx(1, abs=1e-12)
    assert sum(step.duration_beats for step in melody.steps[6:8]) == pytest.approx(1, abs=1e-12)
    assert sum(step.duration_beats for step in melody.steps) == pytest.approx(11, abs=1e-12)
    assert build_plan("dfesta", melody, voices(11)).cues[8].offset_s == pytest.approx(0.25 + 6 * 60 / 122)


@pytest.mark.parametrize("note,harmony", [("C4", ["E4", "G4"]), ("REST", []), ("C4", [])])
def test_gui_step_apply_preserves_or_clears_harmony_without_a_window(note, harmony):
    from tools.melody_register_gui import MelodyRegisterApp

    data = normalize_melody(song())
    def var(value):
        return SimpleNamespace(get=lambda: value)
    app = SimpleNamespace(
        _data=data, _selected=0, velocity_var=var("83"),
        step_note_var=var(note), step_dur_var=var("0.5"), step_lyric_var=var(""),
        harmony_a_var=var(harmony[0] if harmony else ""),
        harmony_b_var=var(harmony[1] if harmony else ""),
        _checkpoint=lambda: None, _changed=lambda: None,
    )
    MelodyRegisterApp._apply_step_edit(app)
    data = app._data
    assert data["steps"][0]["note"] == note
    assert data["steps"][0].get("harmony", []) == harmony
    assert data["steps"][0]["duration"] == 0.5
    assert data["steps"][0]["velocity"] == 83
