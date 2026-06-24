"""Melody Editor — load/save the active screen melody (config.json → active)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from melody_register_core import (
    load_melody,
    melodies_dir,
    melody_duration_sec,
    save_melody,
    validate_melody,
)

DEFAULT_MELODY_ID = "biplane"


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
    return file_id, load_melody(file_id)


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
    beats = sum(float(s.get("duration") or 1) for s in steps)
    sec = melody_duration_sec(data)
    bpm = int(data.get("bpm") or 120)
    beat_ms = 60_000 / max(1, bpm)
    return f"{len(steps)} steps · {beats:g}박 · {sec:.2f}s · 1박={beat_ms:.0f}ms"
