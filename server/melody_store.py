"""멜로디 JSON에서 게임 판정과 실행 규칙을 함께 읽는다."""

from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_TIME_OVER_S = 120
DEFAULT_IDLE_S = 30
DEFAULT_DFESTA_PRESSES_REQUIRED = 1
REST_NAMES = {"R", "REST", "X", "?", "-"}


def load_melody_document(path: Path) -> dict:
    """UTF-8 멜로디 JSON 하나를 읽고 객체 형식인지 검증한다."""
    with Path(path).open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict):
        raise ValueError("melody must be an object")
    return document


def answer_sequence_from_melody(document: Mapping) -> dict[str, str]:
    """멜로디 step을 GameEngine의 1번 기준 정답 시퀀스로 변환한다."""
    steps = document.get("steps", document.get("notes"))
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise ValueError("melody steps must be a non-empty array")
    if not steps:
        raise ValueError("melody steps must be a non-empty array")

    sequence: dict[str, str] = {}
    for index, step in enumerate(steps, start=1):
        note = step.get("note", step.get("name")) if isinstance(step, Mapping) else step
        name = str(note or "").strip().upper()
        if name in REST_NAMES:
            sequence[str(index)] = "R"
            continue
        if name not in {"C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"}:
            raise ValueError(f"unsupported melody note: {note}")
        sequence[str(index)] = name[0]
    return sequence


def _positive_rule(value, default, name: str, *, integer: bool = False):
    """게임 규칙 숫자를 검증하고 누락된 값에는 기본값을 적용한다."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive number")
    if integer:
        if not number.is_integer():
            raise ValueError(f"{name} must be a positive integer")
        return int(number)
    return number


def game_rules_from_melody(document: Mapping) -> dict:
    """멜로디의 game_rules를 검증된 런타임 규칙으로 정규화한다."""
    raw = document.get("game_rules") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("game_rules must be an object")
    return {
        "time_over_s": _positive_rule(
            raw.get("time_over_s"),
            DEFAULT_TIME_OVER_S,
            "time_over_s",
        ),
        "idle_s": _positive_rule(
            raw.get("idle_s"),
            DEFAULT_IDLE_S,
            "idle_s",
        ),
        "dfesta_presses_required": _positive_rule(
            raw.get("dfesta_presses_required"),
            DEFAULT_DFESTA_PRESSES_REQUIRED,
            "dfesta_presses_required",
            integer=True,
        ),
    }
