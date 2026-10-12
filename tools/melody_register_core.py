"""data/melodies/*.json 단일 원본을 읽고 저장한다."""

from __future__ import annotations

from copy import deepcopy
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from server.app_paths import melodies_dir
from server.data_store import load_config, save_config
from server.game.scheduler import parse_melody
from server.game.melody_manager import (
    MODE_FIXED,
    MODE_SEQUENCE,
    game_rules_from_melody,
    rotation_plan_from_config,
)

NOTE_CHOICES = ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5")
NOTE_REST = "REST"
# Combobox에서 높은 음이 위에 오고 REST가 맨 아래에 오게 한다.
NOTE_CHOICES_UI = tuple(reversed(NOTE_CHOICES))
EDITOR_NOTE_CHOICES_UI = (*NOTE_CHOICES_UI, NOTE_REST)
DEFAULT_RANGE = ["C4", "C5"]
BEAT_COLS = 12
LOG_LIMIT = 80
SKIP_FILES = {"config.json"}


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


def load_rotation_settings() -> dict[str, Any]:
    """현재 멜로디 고정·순회 설정을 검증해 반환한다."""

    plan = rotation_plan_from_config(load_config(), list_melody_ids())
    return {
        "mode": plan.mode,
        "playlist": list(plan.playlist),
        "fixed_id": plan.fixed_id,
    }


def save_rotation_settings(mode: str, playlist: list[str]) -> dict[str, Any]:
    """Melody Register의 게임 순서를 config.json에 저장한다."""

    normalized = [str(value or "").strip() for value in playlist]
    if any(not value for value in normalized):
        raise ValueError("게임 순서에 빈 멜로디 ID가 있습니다.")
    if len(set(normalized)) != len(normalized):
        raise ValueError("게임 순서에 중복된 멜로디가 있습니다.")
    if not normalized:
        raise ValueError("게임 순서에 멜로디가 최소 1개 필요합니다.")

    normalized_mode = str(mode or "").lower().strip()
    if normalized_mode not in {MODE_FIXED, MODE_SEQUENCE}:
        raise ValueError("선택 방식은 fixed 또는 sequence여야 합니다.")

    config = load_config()
    config["melody"] = normalized[0]
    config["melody_rotation"] = {
        "mode": normalized_mode,
        "playlist": normalized,
    }
    plan = rotation_plan_from_config(config, list_melody_ids())
    save_config(config)
    return {
        "mode": plan.mode,
        "playlist": list(plan.playlist),
        "fixed_id": plan.fixed_id,
    }


def replace_melody_in_rotation(old_id: str, new_id: str) -> None:
    """멜로디 파일명 변경을 게임 순서에도 함께 반영한다."""

    settings = load_rotation_settings()
    playlist = [new_id if value == old_id else value for value in settings["playlist"]]
    save_rotation_settings(settings["mode"], playlist)


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
            "velocity": step.get("velocity", 127),
        }
        lyric = str(step.get("lyric", "")).strip()
        if lyric:
            entry["lyric"] = lyric
        if "harmony" in step:
            entry["harmony"] = deepcopy(step["harmony"])
        steps.append(entry)

    for i, step in enumerate(steps):
        step["step"] = i + 1

    range_vals = raw.get("range", DEFAULT_RANGE)
    if not isinstance(range_vals, list) or len(range_vals) != 2:
        range_vals = list(DEFAULT_RANGE)

    normalized = {
        "songId": str(raw.get("songId") or file_id or "custom"),
        "title": str(raw.get("title") or "Untitled"),
        "artist": str(raw.get("artist") or ""),
        "bpm": int(raw.get("bpm") or 120),
        "range": [str(range_vals[0]), str(range_vals[1])],
        "game_rules": game_rules_from_melody(raw),
        "steps": steps,
        "_file_id": file_id or str(raw.get("songId") or "custom"),
    }
    if "dfesta" in raw:
        normalized["dfesta"] = deepcopy(raw["dfesta"])
    return normalized


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

    try:
        game_rules_from_melody(data)
        parse_melody(data)
    except ValueError as exc:
        errors.append(str(exc))

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
    # 이전 형식에서 미사용을 뜻하던 X와 ?를 REST로 처리한다.
    if upper == NOTE_REST or upper in ("X", "?", "R") or raw == "-":
        return NOTE_REST
    if raw in NOTE_CHOICES:
        return raw
    return raw


def is_allowed_step_note(note: str) -> bool:
    note = normalize_step_note(note)
    return note in NOTE_CHOICES or note == NOTE_REST


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
            "velocity": step.get("velocity", 127),
        }
        lyric = str(step.get("lyric", "")).strip()
        if lyric:
            entry["lyric"] = lyric
        if "harmony" in step:
            entry["harmony"] = deepcopy(step["harmony"])
        steps_out.append(entry)

    payload = {
        "songId": data["songId"].strip(),
        "title": data.get("title", "").strip() or data["songId"].strip(),
        "artist": data.get("artist", "").strip(),
        "bpm": int(data.get("bpm") or 120),
        "range": list(data.get("range") or DEFAULT_RANGE),
        "game_rules": game_rules_from_melody(data),
        "steps": steps_out,
    }
    if "dfesta" in data:
        payload["dfesta"] = deepcopy(data["dfesta"])
    return payload


def save_melody(
    data: dict[str, Any],
    *,
    file_id: str | None = None,
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

    msg = f"저장 · {path.name}"
    if state:
        state.push_log("ok", msg)
    return path


def new_melody_template(file_id: str) -> dict[str, Any]:
    return normalize_melody(
        {
            "songId": file_id,
            "title": file_id.replace("_", " ").title(),
            "artist": "",
            "bpm": 120,
            "range": DEFAULT_RANGE,
            "steps": [
                {"step": i + 1, "note": NOTE_REST, "duration": 1, "velocity": 127}
                for i in range(BEAT_COLS)
            ],
        },
        file_id,
    )


def delete_melody(file_id: str, state: MelodyRegisterState | None = None) -> None:
    path = melody_path(file_id)
    if not path.is_file():
        raise FileNotFoundError(f"멜로디를 찾을 수 없습니다: {path}")
    remaining = [value for value in list_melody_ids() if value != file_id]
    if not remaining:
        raise ValueError("최소 1개의 멜로디는 유지해야 합니다.")

    settings = load_rotation_settings()
    playlist = [value for value in settings["playlist"] if value != file_id]
    if not playlist:
        playlist = [remaining[0]]
    save_rotation_settings(settings["mode"], playlist)
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
        return {
            "melodies": items,
            "rotation": load_rotation_settings(),
            "logs": list(self.logs),
        }
