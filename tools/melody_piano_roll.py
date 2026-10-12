"""Pure editing operations for the fixed-step, sequential piano roll."""
from copy import deepcopy
from fractions import Fraction
from math import floor, isfinite

from tools.melody_register_core import BEAT_COLS, NOTE_CHOICES, NOTE_REST
from server.game.scheduler import parse_melody

GRID_CHOICES = ("1/4", "1/8", "1/16", "1/32")
MIN_DURATION = 1 / 96
MAX_DURATION = 16.0


def grid_beats(division: str, triplet: bool = False) -> float:
    if division not in GRID_CHOICES:
        raise ValueError("Unsupported grid division")
    return float(Fraction(division) * 4 * (Fraction(2, 3) if triplet else 1))


def snap_duration(beats: float, division="1/16", triplet=False, snap=True) -> float:
    if not isfinite(beats):
        raise ValueError("Duration must be finite")
    quantum = grid_beats(division, triplet)
    if snap:
        return min(MAX_DURATION, max(1, floor(beats / quantum + 0.5)) * quantum)
    return min(MAX_DURATION, max(MIN_DURATION, round(beats, 6)))


def step_spans(steps):
    start = 0.0
    spans = []
    for step in steps:
        end = start + float(step["duration"])
        spans.append((start, end))
        start = end
    return spans


def step_at(steps, beat):
    for index, (start, end) in enumerate(step_spans(steps)):
        if start <= beat < end:
            return index
    return None


def twelve_steps(steps):
    if len(steps) > BEAT_COLS:
        raise ValueError("이 편집기는 최대 12 step을 지원합니다. 원본을 줄이지 않고 열기를 중단합니다.")
    result = deepcopy(steps)
    result.extend({"note": NOTE_REST, "duration": 1.0, "velocity": 127}
                  for _ in range(BEAT_COLS - len(result)))
    for i, step in enumerate(result):
        step["step"] = i + 1
        step.setdefault("velocity", 127)
    return result


def set_note(step, note):
    if note not in (*NOTE_CHOICES, NOTE_REST):
        raise ValueError("C4–C5의 8개 음 또는 REST만 사용할 수 있습니다.")
    step["note"] = note
    if note == NOTE_REST:
        step.pop("harmony", None)


def edit_step(data, index, *, note, duration, velocity, harmony, lyric=""):
    edited = deepcopy(data["steps"][index])
    set_note(edited, note)
    edited["duration"] = float(Fraction(str(duration)))
    if not MIN_DURATION <= edited["duration"] <= MAX_DURATION:
        raise ValueError("Length는 1/96–16박 사이로 입력하세요. 분수(예: 1/3)도 가능합니다.")
    edited["velocity"] = int(velocity) if isinstance(velocity, str) else velocity
    if note != NOTE_REST:
        if any(harmony):
            edited["harmony"] = list(harmony)
        else:
            edited.pop("harmony", None)
    if lyric.strip():
        edited["lyric"] = lyric.strip()
    else:
        edited.pop("lyric", None)
    parse_melody({"bpm": data["bpm"], "steps": [edited]})
    data["steps"][index] = edited


def duration_label(value):
    fraction = Fraction(value).limit_denominator(96)
    return str(fraction) if abs(float(fraction) - value) < 1e-10 else str(value)
