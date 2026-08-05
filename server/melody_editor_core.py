"""활성 screen 멜로디를 불러오고 저장하는 Melody Editor 핵심 로직이다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app_paths import data_dir
from melody_register_core import (
    BEAT_COLS,
    list_melody_ids,
    load_melody,
    melody_duration_sec,
    melody_path,
    melodies_dir,
    new_melody_template,
    save_melody,
    validate_melody,
)

DEFAULT_MELODY_ID = "biplane"

BIPLANE_TAIL_STEPS: tuple[dict[str, Any], ...] = (
    {"note": "E4", "duration": 1, "lyric": "멀"},
    {"note": "E4", "duration": 1, "lyric": "리"},
    {"note": "C5", "duration": 2, "lyric": "라"},
)


def active_melody_id() -> str:
    path = data_dir() / "config.json"
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as f:
                doc = json.load(f)
            active = str(doc.get("melody", "")).strip()
            if active:
                return active
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_MELODY_ID


def resolve_melody_id(preferred: str, *, update_config: bool = False) -> str:
    """존재하는 멜로디를 고르고 필요하면 활성 설정을 대체 항목으로 갱신한다."""
    preferred = preferred.strip()
    if preferred and melody_path(preferred).is_file():
        return preferred

    ids = list_melody_ids()
    if DEFAULT_MELODY_ID in ids:
        fallback = DEFAULT_MELODY_ID
    elif ids:
        fallback = ids[0]
    else:
        raise FileNotFoundError(
            "melodies 폴더에 노래 JSON이 없습니다. 새 노래를 만들거나 JSON 파일을 추가하세요."
        )

    if update_config and preferred != fallback:
        set_active_melody(fallback)
    return fallback


def load_active_melody() -> tuple[str, dict[str, Any]]:
    preferred = active_melody_id()
    file_id = resolve_melody_id(preferred, update_config=True)
    return load_melody_for_editor(file_id)


def load_melody_for_editor(file_id: str) -> tuple[str, dict[str, Any]]:
    file_id = file_id.strip()
    data = load_melody(file_id)
    data["steps"] = normalize_beat_steps(data.get("steps") or [], file_id=file_id)
    return file_id, data


def set_active_melody(file_id: str) -> None:
    file_id = file_id.strip()
    if not file_id:
        raise ValueError("songId가 비어 있습니다.")
    server_config = data_dir() / "config.json"
    if server_config.is_file():
        try:
            with server_config.open(encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            doc = {}
    else:
        doc = {}
    doc["melody"] = file_id
    server_config.parent.mkdir(parents=True, exist_ok=True)
    server_config.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_melody(file_id: str) -> tuple[str, dict[str, Any]]:
    safe = file_id.strip().replace(" ", "_")
    if not safe:
        raise ValueError("songId가 비어 있습니다.")
    if melody_path(safe).exists():
        raise ValueError(f"{safe}.json 이 이미 있습니다.")
    data = new_melody_template(safe)
    data["steps"] = normalize_beat_steps(data.get("steps") or [], file_id=safe)
    save_melody(data, file_id=safe)
    set_active_melody(safe)
    return safe, data


def save_melody_editor(
    data: dict[str, Any],
    *,
    file_id: str,
    old_file_id: str | None = None,
    set_active: bool = True,
) -> Path:
    target = (file_id or old_file_id or active_melody_id()).strip()
    errors = validate_melody(data)
    if errors:
        raise ValueError("\n".join(errors))
    path = save_melody(data, file_id=target, state=None)
    if old_file_id and old_file_id.strip() != target:
        old_path = melody_path(old_file_id.strip())
        if old_path != path and old_path.is_file():
            old_path.unlink()
    if set_active:
        set_active_melody(target)
    return path


def normalize_beat_steps(steps: list[dict[str, Any]], *, file_id: str = "") -> list[dict[str, Any]]:
    """step을 12칸으로 맞추고 이전 9칸 biplane 형식을 변환한다."""
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
) -> Path:
    target = file_id or data.get("_file_id") or active_melody_id()
    return save_melody_editor(
        data,
        file_id=target,
        old_file_id=data.get("_file_id"),
        set_active=True,
    )


def timing_summary(data: dict[str, Any]) -> str:
    steps = data.get("steps") or []
    n = len(steps)
    sec = melody_duration_sec(data)
    bpm = int(data.get("bpm") or 120)
    beat_ms = 60_000 / max(1, bpm)
    return f"{n}박 · {sec:.2f}s · 1박={beat_ms:.0f}ms"
