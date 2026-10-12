from array import array
from copy import deepcopy
from io import BytesIO
import json
import wave
from types import SimpleNamespace

import pytest

from server.game.scheduler import build_plan, parse_melody
from tools import melody_register_core as editor
from tools.melody_piano_roll import (
    duration_label, edit_step, grid_beats, set_note, snap_duration, step_at,
    step_spans, twelve_steps,
)
from tools.melody_preview import render_preview, SAMPLE_RATE


@pytest.mark.parametrize("mode", ["listen", "play", "dfesta"])
def test_step_velocity_reaches_every_member_and_harmony(mode):
    raw = {"bpm": 120, "steps": [
        {"note": "C4", "duration": 1, "velocity": 43, "harmony": ["E4", "G4"]},
        {"note": "D4", "duration": 1, "velocity": 112, "harmony": ["F4", "A4"]},
        {"note": "REST", "duration": 1, "velocity": 1},
    ]}
    cells = {str(i): {"voice": f"member_{i}", "position": i % 2 + 1, "cell": 1} for i in range(10)}
    plan = build_plan(mode, parse_melody(raw), cells)
    assert {note["velocity"] for note in plan.cues[0].notes} == {43}
    assert {note["velocity"] for note in plan.cues[1].notes} == {112}
    assert plan.cues[2].notes == ()
    if mode == "dfesta":
        assert len(plan.cues[0].notes) == 10
        assert {note["pitch"] for note in plan.cues[0].notes} == {0, 2, 4}


@pytest.mark.parametrize("velocity", [0, -1, 128, True, False, "80", 80.5, None])
def test_invalid_velocity_is_rejected_and_not_saved(velocity, tmp_path, monkeypatch):
    raw = editor.new_melody_template("test")
    raw["steps"][0]["velocity"] = velocity
    monkeypatch.setattr(editor, "melodies_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="velocity"):
        parse_melody(raw)
    with pytest.raises(ValueError, match="velocity"):
        editor.save_melody(raw)
    assert not (tmp_path / "test.json").exists()


def test_velocity_and_harmony_round_trip_and_legacy_default(tmp_path, monkeypatch):
    raw = editor.new_melody_template("test")
    edit_step(raw, 0, note="C4", duration="1/3", velocity="57", harmony=["E4", "G4"], lyric="la")
    raw["steps"][1].pop("velocity")
    monkeypatch.setattr(editor, "melodies_dir", lambda: tmp_path)
    editor.save_melody(raw)
    loaded = editor.load_melody("test")
    assert loaded["steps"][0] == raw["steps"][0]
    assert loaded["steps"][1]["velocity"] == 127
    assert parse_melody(loaded).steps[1].velocity == 127
    assert len(loaded["steps"]) == 12


def test_triplet_snap_and_free_duration_preserve_sequential_steps():
    data = editor.new_melody_template("new")
    assert all(step["note"] == "REST" for step in data["steps"])
    assert grid_beats("1/8", True) == pytest.approx(1/3)
    assert grid_beats("1/16", True) == pytest.approx(1/6)
    for i in range(3):
        data["steps"][i]["duration"] = snap_duration(0.36, "1/8", True)
    assert step_spans(data["steps"])[3][0] == pytest.approx(1)
    assert step_at(data["steps"], 1.01) == 3
    assert len(data["steps"]) == 12
    assert snap_duration(0.37, "1/8", False, False) == 0.37
    assert snap_duration(-2) > 0
    assert duration_label(2/3) == "2/3"


def test_rest_clears_harmony_but_preserves_time_and_velocity():
    step = {"note": "C4", "harmony": ["E4", "G4"], "duration": 2/3, "velocity": 68}
    set_note(step, "REST")
    assert step == {"note": "REST", "duration": 2/3, "velocity": 68}
    assert len(twelve_steps([step])) == 12
    assert len([step]) == 1
    with pytest.raises(ValueError):
        twelve_steps([step] * 13)


def test_invalid_inspector_edit_is_atomic():
    data = editor.new_melody_template("test")
    before = deepcopy(data)
    with pytest.raises(ValueError):
        edit_step(data, 0, note="C4", duration="1/3", velocity="200", harmony=[])
    assert data == before


def test_preview_has_full_slot_lengths_rests_and_velocity_scaling():
    data = [
        {"note": "C4", "duration": 1, "velocity": 127},
        {"note": "C4", "duration": 1, "velocity": 64},
        {"note": "REST", "duration": 1},
    ]
    with wave.open(BytesIO(render_preview(data, 120))) as audio:
        assert audio.getnframes() == round(1.5 * SAMPLE_RATE)
        pcm = array("h", audio.readframes(audio.getnframes()))
    count = SAMPLE_RATE // 2
    loud = max(abs(x) for x in pcm[:count])
    quiet = max(abs(x) for x in pcm[count:2*count])
    assert quiet / loud == pytest.approx(64/127, abs=0.002)
    assert all(x == 0 for x in pcm[2*count:])
    assert all(x == 0 for x in pcm[int(count * 0.90):count])


def test_canvas_click_resize_and_velocity_keep_twelve_slots():
    from tools.melody_register_gui import MelodyRegisterApp, PAD, RULER, ROW, VELOCITY_TOP

    app = MelodyRegisterApp.__new__(MelodyRegisterApp)
    app._data = editor.new_melody_template("mouse")
    app._drag = None
    app._selected = 0
    app._undo, app._redo = [], []
    app.canvas = SimpleNamespace(focus_set=lambda: None, canvasx=lambda x: x)
    app.zoom_var = SimpleNamespace(get=lambda: 96)
    app.grid_var = SimpleNamespace(get=lambda: "1/8")
    app.triplet_var = SimpleNamespace(get=lambda: True)
    app.snap_var = SimpleNamespace(get=lambda: True)
    app._select = lambda index: setattr(app, "_selected", index)
    app._changed = lambda: None
    event = lambda x, y: SimpleNamespace(x=x, y=y)
    # First slot: E4, without adding/deleting a step.
    app._press(event(PAD + 20, RULER + 5 * ROW + 10))
    assert app._data["steps"][0]["note"] == "E4"
    app._press(event(PAD + 96 - 4, RULER + 5 * ROW + 10))
    app._motion(event(PAD + 61, RULER + 5 * ROW + 10))
    assert app._data["steps"][0]["duration"] == pytest.approx(2/3)
    app._press(event(PAD + 20, VELOCITY_TOP + 51))
    assert app._data["steps"][0]["velocity"] == 64
    assert len(app._data["steps"]) == 12
    assert all(step["note"] == "REST" for step in app._data["steps"][1:])


def test_preview_triplets_do_not_accumulate_rounding_error():
    data = [{"note": "D4", "duration": 1/3, "velocity": 60}] * 12
    with wave.open(BytesIO(render_preview(data, 120))) as audio:
        assert audio.getnframes() == 2 * SAMPLE_RATE
