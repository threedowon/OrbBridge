"""Melody Editor — load/save the active screen melody (config.json → active)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from melody_register_core import (
    BEAT_COLS,
    load_melody,
    melodies_dir,
    melody_duration_sec,
    save_melody,
    validate_melody,
)

DEFAULT_MELODY_ID = "biplane"

BIPLANE_TAIL_STEPS: tuple[dict[str, Any], ...] = (
    {"note": "E4", "duration": 1, "lyric": "멀"},
    {"note": "E4", "duration": 1, "lyric": "리"},
    {"note": "C5", "duration": 2, "lyric": "라"},
)


def melodies_config_path() -> Path:
    return melodies_dir() / "config.json"


def active_melody_id() -> str:
    path = melodies_config_path()
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as f:
                doc = json.load(f)
            active = str(doc.get("active", "")).strip()
            if active:
                return active
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_MELODY_ID


def load_active_melody() -> tuple[str, dict[str, Any]]:
    file_id = active_melody_id()
    data = load_melody(file_id)
    data["steps"] = normalize_beat_steps(data.get("steps") or [], file_id=file_id)
    return file_id, data


def normalize_beat_steps(steps: list[dict[str, Any]], *, file_id: str = "") -> list[dict[str, Any]]:
    """Pad or trim steps to BEAT_COLS (12). Upgrades legacy 9-step biplane in place."""
    out = [dict(s) for s in steps]
    if len(out) == 9 and file_id == "biplane":
        for extra in BIPLANE_TAIL_STEPS:
            out.append(dict(extra))
    if len(out) > BEAT_COLS:
        out = out[:BEAT_COLS]
    last_note = str(out[-1].get("note", "C4") or "C4") if out else "C4"
    while len(out) < BEAT_COLS:
        out.append({"note": last_note, "duration": 1.0})
    for i, step in enumerate(out):
        step["step"] = i + 1
    return out


def save_active_melody(
    data: dict[str, Any],
    *,
    file_id: str | None = None,
    sync_stage: bool = True,
) -> Path:
    target = file_id or data.get("_file_id") or active_melody_id()
    errors = validate_melody(data)
    if errors:
        raise ValueError("\n".join(errors))
    return save_melody(data, file_id=target, sync_stage=sync_stage, state=None)


def timing_summary(data: dict[str, Any]) -> str:
    steps = data.get("steps") or []
    n = len(steps)
    sec = melody_duration_sec(data)
    bpm = int(data.get("bpm") or 120)
    beat_ms = 60_000 / max(1, bpm)
    return f"{n}박 · {sec:.2f}s · 1박={beat_ms:.0f}ms"
