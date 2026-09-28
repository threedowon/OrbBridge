"""멜로디 JSON의 로드·게임 규칙·게임별 선택을 한곳에서 관리한다."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Iterable, Mapping, Sequence


MODE_FIXED = "fixed"
MODE_SEQUENCE = "sequence"
SUPPORTED_MODES = {MODE_FIXED, MODE_SEQUENCE}

DEFAULT_TIME_OVER_S = 120
DEFAULT_IDLE_S = 30
DEFAULT_DFESTA_PRESSES_REQUIRED = 1
REST_NAMES = {"R", "REST", "X", "?", "-"}
PITCH_NAMES = {"C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"}


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
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
        raise ValueError("melody steps must be a non-empty array")

    sequence: dict[str, str] = {}
    for index, step in enumerate(steps, start=1):
        note = step.get("note", step.get("name")) if isinstance(step, Mapping) else step
        name = str(note or "").strip().upper()
        if name in REST_NAMES:
            sequence[str(index)] = "R"
        elif name in PITCH_NAMES:
            sequence[str(index)] = name[0]
        else:
            raise ValueError(f"unsupported melody note: {note}")
    return sequence


def _positive_rule(value, default, name: str, *, integer: bool = False):
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
    """곡별 규칙을 검증한다. 시간 제한은 공통 config에서만 읽는다."""

    raw = document.get("game_rules") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("game_rules must be an object")
    return {
        "dfesta_presses_required": _positive_rule(
            raw.get("dfesta_presses_required"),
            DEFAULT_DFESTA_PRESSES_REQUIRED,
            "dfesta_presses_required",
            integer=True,
        ),
    }


def game_timers_from_config(config: Mapping) -> dict:
    """모든 곡에 적용할 공통 시간 제한(초). 누락된 값은 기존 기본값 사용."""
    raw = config.get("game_rules", {})
    if not isinstance(raw, Mapping):
        raise ValueError("config.game_rules must be an object")
    return {
        "time_over_s": _positive_rule(raw.get("time_over_s"), DEFAULT_TIME_OVER_S, "time_over_s"),
        "idle_s": _positive_rule(raw.get("idle_s"), DEFAULT_IDLE_S, "idle_s"),
    }


def _normalize_playlist(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("melody_rotation.playlist must be an array")
    playlist = tuple(str(value or "").strip() for value in raw)
    if not playlist or any(not value for value in playlist):
        raise ValueError("melody_rotation.playlist must contain melody ids")
    if len(set(playlist)) != len(playlist):
        raise ValueError("melody_rotation.playlist contains a duplicate")
    return playlist


@dataclass(frozen=True)
class MelodyPlan:
    """검증된 멜로디 선택 설정이다."""

    mode: str
    playlist: tuple[str, ...]
    fixed_id: str

    @property
    def signature(self) -> tuple[str, tuple[str, ...], str]:
        return self.mode, self.playlist, self.fixed_id


def rotation_plan_from_config(
    config: Mapping,
    available_ids: Iterable[str],
) -> MelodyPlan:
    """config의 선택 설정과 실제 멜로디 파일 목록을 함께 검증한다."""

    available = tuple(dict.fromkeys(str(value).strip() for value in available_ids))
    available = tuple(value for value in available if value)
    if not available:
        raise ValueError("data/melodies contains no melody JSON")

    fixed_id = str(config.get("melody") or available[0]).strip()
    raw_rotation = config.get("melody_rotation")
    if raw_rotation is None:
        playlist = (fixed_id,)
        mode = MODE_FIXED
    else:
        if not isinstance(raw_rotation, Mapping):
            raise ValueError("melody_rotation must be an object")
        mode = str(raw_rotation.get("mode") or MODE_FIXED).lower().strip()
        if mode not in SUPPORTED_MODES:
            raise ValueError("melody_rotation.mode must be fixed or sequence")
        raw_playlist = raw_rotation.get("playlist")
        playlist = _normalize_playlist(raw_playlist) if raw_playlist is not None else (fixed_id,)

    missing = [melody_id for melody_id in playlist if melody_id not in available]
    if fixed_id not in available:
        missing.insert(0, fixed_id)
    if missing:
        raise ValueError(f"melody file does not exist: {', '.join(dict.fromkeys(missing))}")
    if mode == MODE_SEQUENCE:
        # Keep the configured order, then discover newly registered songs.
        playlist += tuple(value for value in sorted(available) if value not in playlist)
    return MelodyPlan(mode, playlist, fixed_id)


class MelodyManager:
    """멜로디 파일과 성공한 게임 시작 기준의 순번을 관리한다."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._plan: MelodyPlan | None = None
        self._next_index = 0
        self._pending_index: int | None = None

    def available_ids(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def load(self, melody_id: str) -> dict:
        return load_melody_document(self.directory / f"{melody_id}.json")

    def _sync_plan(self, config: Mapping) -> MelodyPlan:
        plan = rotation_plan_from_config(config, self.available_ids())
        if self._plan is None or self._plan.signature != plan.signature:
            next_id = None
            if (self._plan and self._plan.mode == plan.mode == MODE_SEQUENCE
                    and tuple(value for value in plan.playlist if value in self._plan.playlist)
                    == self._plan.playlist):
                next_id = self._plan.playlist[self._next_index]
            self._plan = plan
            self._next_index = plan.playlist.index(next_id) if next_id in plan.playlist else 0
            self._pending_index = None
        return plan

    def preview(self, config: Mapping) -> str:
        """순번을 소비하지 않고 다음 게임의 멜로디를 반환한다."""

        with self._lock:
            plan = self._sync_plan(config)
            return plan.fixed_id if plan.mode == MODE_FIXED else plan.playlist[self._next_index]

    def reserve(self, config: Mapping) -> str:
        """시작 완료 전까지 같은 멜로디를 예약한다."""

        with self._lock:
            plan = self._sync_plan(config)
            if plan.mode == MODE_FIXED:
                self._pending_index = None
                return plan.fixed_id
            if self._pending_index is None:
                self._pending_index = self._next_index
            return plan.playlist[self._pending_index]

    def commit(self) -> None:
        """게임 시작 성공을 확정하고 다음 순번으로 이동한다."""

        with self._lock:
            if self._plan and self._plan.mode == MODE_SEQUENCE and self._pending_index is not None:
                self._next_index = (self._pending_index + 1) % len(self._plan.playlist)
            self._pending_index = None

    def cancel(self) -> None:
        """실패한 시작 예약을 버리고 같은 멜로디를 유지한다."""

        with self._lock:
            self._pending_index = None

    def snapshot(self) -> dict:
        """Admin에 현재 선택 정책과 다음 멜로디를 제공한다."""

        with self._lock:
            if self._plan is None:
                return {"mode": MODE_FIXED, "playlist": [], "next_melody": None}
            next_id = (
                self._plan.fixed_id
                if self._plan.mode == MODE_FIXED
                else self._plan.playlist[self._next_index]
            )
            return {
                "mode": self._plan.mode,
                "playlist": list(self._plan.playlist),
                "next_melody": next_id,
            }
