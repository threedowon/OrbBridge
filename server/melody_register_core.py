"""Melody Register — load/save screen/melodies/*.json and optional stage_data sync."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from app_paths import data_dir, melodies_dir

NOTE_CHOICES = ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5")
NOTE_REST = "REST"
# Combobox 표시 순서 — 위로 갈수록 높은 음, 하단에 REST
NOTE_CHOICES_UI = tuple(reversed(NOTE_CHOICES))
EDITOR_NOTE_CHOICES_UI = (*NOTE_CHOICES_UI, NOTE_REST)
DEFAULT_RANGE = ["C4", "C5"]
BEAT_COLS = 12
LOG_LIMIT = 80
SKIP_FILES = {"config.json"}
DEFAULT_STAGE_TIME_LIMIT_S = 120
DEFAULT_STAGE_IDLE_LIMIT_S = 30
DEFAULT_STAGE_DISPENSA_PRESSES_REQUIRED = 1


def stage_data_path() -> Path:
    return data_dir() / "stage_data.json"


def melody_path(file_id: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", file_id.strip()).strip("_")
    if not safe:
        raise ValueError("songId / 파일명이 비어 있습니다.")
    return melodies_dir() / f"{safe}.json"


def list_melody_ids() -> list[str]:
    out: list[str] = []
    for path in sorted(melodies_dir().glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        out.append(path.stem)
    return out


def load_melody(file_id: str) -> dict[str, Any]:
    path = melody_path(file_id)
    if not path.is_file():
        raise FileNotFoundError(f"멜로디를 찾을 수 없습니다: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return normalize_melody(data, file_id)


def normalize_melody(raw: dict[str, Any], file_id: str = "") -> dict[str, Any]:
    steps_in = raw.get("steps") or raw.get("notes") or []
    steps: list[dict[str, Any]] = []
    for i, step in enumerate(steps_in):
        if not isinstance(step, dict):
            continue
        raw_note = str(step.get("note", "")).strip()
        note = normalize_step_note(raw_note) if raw_note else ""
        dur = step.get("duration", step.get("dur", 1))
        try:
            dur_f = float(dur)
        except (TypeError, ValueError):
            dur_f = 1.0
        entry: dict[str, Any] = {
            "step": int(step.get("step", i + 1)),
            "note": note,
            "duration": dur_f,
        }
        lyric = str(step.get("lyric", "")).strip()
        if lyric:
            entry["lyric"] = lyric
        steps.append(entry)

    for i, step in enumerate(steps):
        step["step"] = i + 1

    range_vals = raw.get("range", DEFAULT_RANGE)
    if not isinstance(range_vals, list) or len(range_vals) != 2:
        range_vals = list(DEFAULT_RANGE)

    return {
        "songId": str(raw.get("songId") or file_id or "custom"),
        "title": str(raw.get("title") or "Untitled"),
        "artist": str(raw.get("artist") or ""),
        "bpm": int(raw.get("bpm") or 120),
        "range": [str(range_vals[0]), str(range_vals[1])],
        "steps": steps,
        "_file_id": file_id or str(raw.get("songId") or "custom"),
    }


def validate_melody(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not data.get("songId", "").strip():
        errors.append("songId가 필요합니다.")
    try:
        bpm = int(data.get("bpm", 0))
        if bpm < 20 or bpm > 300:
            errors.append("BPM은 20–300 사이여야 합니다.")
    except (TypeError, ValueError):
        errors.append("BPM이 올바르지 않습니다.")

    steps = data.get("steps") or []
    if not steps:
        errors.append("steps가 최소 1개 필요합니다.")
        return errors
    if len(steps) > BEAT_COLS:
        errors.append(f"steps는 최대 {BEAT_COLS}개까지 가능합니다.")

    for step in steps:
        n = step.get("step")
        note = step.get("note", "")
        dur = step.get("duration", 0)
        if not note:
            errors.append(f"step {n}: note가 비어 있습니다.")
        elif not is_allowed_step_note(note):
            errors.append(f"step {n}: note가 올바르지 않습니다 ({note}).")
        if not isinstance(dur, (int, float)) or dur <= 0:
            errors.append(f"step {n}: duration은 0보다 커야 합니다.")
    return errors


def normalize_step_note(note: str) -> str:
    raw = str(note or "").strip()
    upper = raw.upper()
    # legacy: X / ? were "unused" — treat as rest
    if upper == NOTE_REST or upper in ("X", "?", "R") or raw == "-":
        return NOTE_REST
    if raw in NOTE_CHOICES:
        return raw
    return raw


def is_allowed_step_note(note: str) -> bool:
    note = normalize_step_note(note)
    return note in NOTE_CHOICES or note == NOTE_REST


def note_to_stage_key(note: str) -> str:
    note = normalize_step_note(note)
    if note == NOTE_REST:
        return "R"
    return note[0].upper()


def build_stage_sequence(steps: list[dict[str, Any]]) -> dict[str, str]:
    seq: dict[str, str] = {}
    for i, step in enumerate(steps, start=1):
        key = note_to_stage_key(step.get("note", ""))
        if key:
            seq[str(i)] = key
    return seq


def melody_duration_sec(data: dict[str, Any]) -> float:
    bpm = max(1, int(data.get("bpm") or 120))
    beat_ms = 60_000 / bpm
    total_beats = sum(float(s.get("duration") or 1) for s in data.get("steps") or [])
    return (total_beats * beat_ms) / 1000


def to_save_payload(data: dict[str, Any]) -> dict[str, Any]:
    steps_out: list[dict[str, Any]] = []
    for i, step in enumerate(data.get("steps") or [], start=1):
        entry: dict[str, Any] = {
            "step": i,
            "note": step.get("note", "?"),
            "duration": float(step.get("duration") or 1),
        }
        lyric = str(step.get("lyric", "")).strip()
        if lyric:
            entry["lyric"] = lyric
        steps_out.append(entry)

    return {
        "songId": data["songId"].strip(),
        "title": data.get("title", "").strip() or data["songId"].strip(),
        "artist": data.get("artist", "").strip(),
        "bpm": int(data.get("bpm") or 120),
        "range": list(data.get("range") or DEFAULT_RANGE),
        "steps": steps_out,
    }


def save_melody(
    data: dict[str, Any],
    *,
    file_id: str | None = None,
    sync_stage: bool = False,
    stage_number: int = 1,
    state: MelodyRegisterState | None = None,
) -> Path:
    payload = to_save_payload(data)
    errors = validate_melody(payload)
    if errors:
        raise ValueError("\n".join(errors))

    target_id = (file_id or data.get("_file_id") or payload["songId"]).strip()
    path = melody_path(target_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    stage_msg = ""
    if sync_stage:
        stage_msg = sync_stage_data(payload["steps"], stage_number=stage_number)

    msg = f"저장 · {path.name}"
    if stage_msg:
        msg += f" · {stage_msg}"
    if state:
        state.push_log("ok", msg)
    return path


def sync_stage_data(
    steps: list[dict[str, Any]],
    *,
    stage_number: int = 1,
) -> str:
    path = stage_data_path()
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = {"stages": []}

    stages = doc.setdefault("stages", [])
    stage = next((s for s in stages if s.get("stage") == stage_number), None)
    if stage is None:
        # Keep newly created stages aligned with the runtime's game-rule
        # defaults while preserving every field on existing stages.
        stage = {
            "stage": stage_number,
            "sequence": {},
            "time_limit": DEFAULT_STAGE_TIME_LIMIT_S,
            "idle_time_limit": DEFAULT_STAGE_IDLE_LIMIT_S,
            "dispensa_presses_required": (
                DEFAULT_STAGE_DISPENSA_PRESSES_REQUIRED
            ),
        }
        stages.append(stage)
        stages.sort(key=lambda s: int(s.get("stage", 0)))

    stage["sequence"] = build_stage_sequence(steps)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return f"stage_data stage {stage_number} 동기화"


def new_melody_template(file_id: str) -> dict[str, Any]:
    return normalize_melody(
        {
            "songId": file_id,
            "title": file_id.replace("_", " ").title(),
            "artist": "",
            "bpm": 120,
            "range": DEFAULT_RANGE,
            "steps": [{"step": 1, "note": "C4", "duration": 1}],
        },
        file_id,
    )


def delete_melody(file_id: str, state: MelodyRegisterState | None = None) -> None:
    path = melody_path(file_id)
    if not path.is_file():
        raise FileNotFoundError(f"멜로디를 찾을 수 없습니다: {path}")
    path.unlink()
    msg = f"삭제 · {path.name}"
    if state:
        state.push_log("warn", msg)


class MelodyRegisterState:
    def __init__(self) -> None:
        self.logs: list[dict] = []
        self._log_listeners: list[Callable[[dict], None]] = []

    def add_log_listener(self, cb: Callable[[dict], None]) -> None:
        self._log_listeners.append(cb)

    def push_log(self, level: str, message: str) -> None:
        entry = {"level": level, "message": message, "at": time.time()}
        self.logs = ([entry] + self.logs)[:LOG_LIMIT]
        for cb in self._log_listeners:
            try:
                cb(entry)
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        items = []
        for file_id in list_melody_ids():
            try:
                data = load_melody(file_id)
            except (OSError, json.JSONDecodeError):
                continue
            items.append(
                {
                    "file_id": file_id,
                    "title": data.get("title", file_id),
                    "bpm": data.get("bpm", 0),
                    "steps": len(data.get("steps") or []),
                    "seconds": round(melody_duration_sec(data), 2),
                }
            )
        return {"melodies": items, "logs": list(self.logs)}
