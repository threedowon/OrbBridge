"""게임 시작마다 사용할 멜로디를 고정 또는 순차 방식으로 선택한다."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


MODE_FIXED = "fixed"
MODE_SEQUENCE = "sequence"
SUPPORTED_MODES = {MODE_FIXED, MODE_SEQUENCE}


def available_melody_ids(directory: Path) -> list[str]:
    """멜로디 폴더의 JSON 파일 ID를 정렬해 반환한다."""

    return sorted(path.stem for path in Path(directory).glob("*.json"))


def _normalize_playlist(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("melody_rotation.playlist must be an array")

    playlist: list[str] = []
    for value in raw:
        melody_id = str(value or "").strip()
        if not melody_id:
            raise ValueError("melody_rotation.playlist contains an empty id")
        if melody_id in playlist:
            raise ValueError(f"melody_rotation.playlist contains a duplicate: {melody_id}")
        playlist.append(melody_id)
    if not playlist:
        raise ValueError("melody_rotation.playlist must not be empty")
    return tuple(playlist)


@dataclass(frozen=True)
class MelodyRotationPlan:
    """검증된 멜로디 선택 설정이다."""

    mode: str
    playlist: tuple[str, ...]
    fixed_id: str

    @property
    def signature(self) -> tuple[str, tuple[str, ...], str]:
        return self.mode, self.playlist, self.fixed_id

    @property
    def first_id(self) -> str:
        return self.fixed_id if self.mode == MODE_FIXED else self.playlist[0]


def rotation_plan_from_config(
    config: Mapping,
    available_ids: Iterable[str],
) -> MelodyRotationPlan:
    """config의 멜로디 선택 설정과 실제 파일 목록을 함께 검증한다."""

    available = tuple(dict.fromkeys(str(value).strip() for value in available_ids))
    available = tuple(value for value in available if value)
    if not available:
        raise ValueError("data/melodies contains no melody JSON")

    fixed_id = str(config.get("melody") or "").strip()
    if not fixed_id:
        fixed_id = available[0]

    raw_rotation = config.get("melody_rotation")
    if raw_rotation is None:
        if fixed_id not in available:
            raise ValueError(f"configured melody does not exist: {fixed_id}")
        return MelodyRotationPlan(MODE_FIXED, (fixed_id,), fixed_id)
    if not isinstance(raw_rotation, Mapping):
        raise ValueError("melody_rotation must be an object")

    mode = str(raw_rotation.get("mode") or MODE_FIXED).lower().strip()
    if mode not in SUPPORTED_MODES:
        raise ValueError("melody_rotation.mode must be fixed or sequence")

    raw_playlist = raw_rotation.get("playlist")
    playlist = (
        _normalize_playlist(raw_playlist)
        if raw_playlist is not None
        else (fixed_id,)
    )
    missing = [melody_id for melody_id in playlist if melody_id not in available]
    if missing:
        raise ValueError(f"melody file does not exist: {', '.join(missing)}")

    if mode == MODE_FIXED:
        if fixed_id not in available:
            raise ValueError(f"configured melody does not exist: {fixed_id}")
        return MelodyRotationPlan(mode, playlist, fixed_id)
    return MelodyRotationPlan(mode, playlist, playlist[0])


class MelodyRotator:
    """성공한 게임 시작에만 순서를 한 칸 전진시키는 선택기이다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plan: MelodyRotationPlan | None = None
        self._next_index = 0
        self._pending_index: int | None = None

    def _sync_plan(self, plan: MelodyRotationPlan) -> None:
        if self._plan is None or self._plan.signature != plan.signature:
            self._plan = plan
            self._next_index = 0
            self._pending_index = None

    def preview(self, config: Mapping, available_ids: Iterable[str]) -> str:
        """순서를 소비하지 않고 다음 게임의 멜로디를 반환한다."""

        plan = rotation_plan_from_config(config, available_ids)
        with self._lock:
            self._sync_plan(plan)
            if plan.mode == MODE_FIXED:
                return plan.fixed_id
            return plan.playlist[self._next_index]

    def reserve(self, config: Mapping, available_ids: Iterable[str]) -> str:
        """다음 멜로디를 예약하며 commit 전에는 순서를 바꾸지 않는다."""

        plan = rotation_plan_from_config(config, available_ids)
        with self._lock:
            self._sync_plan(plan)
            if plan.mode == MODE_FIXED:
                self._pending_index = None
                return plan.fixed_id
            if self._pending_index is None:
                self._pending_index = self._next_index
            return plan.playlist[self._pending_index]

    def commit(self) -> None:
        """게임 시작 성공을 확정하고 다음 순번으로 이동한다."""

        with self._lock:
            plan = self._plan
            pending = self._pending_index
            if plan is not None and plan.mode == MODE_SEQUENCE and pending is not None:
                self._next_index = (pending + 1) % len(plan.playlist)
            self._pending_index = None

    def cancel(self) -> None:
        """실패한 시작 예약을 버리고 같은 멜로디를 다음에 다시 사용한다."""

        with self._lock:
            self._pending_index = None

    def snapshot(self) -> dict:
        """운영 화면용 현재 선택 정책과 다음 멜로디를 반환한다."""

        with self._lock:
            plan = self._plan
            if plan is None:
                return {"mode": MODE_FIXED, "playlist": [], "next_melody": None}
            next_id = (
                plan.fixed_id
                if plan.mode == MODE_FIXED
                else plan.playlist[self._next_index]
            )
            return {
                "mode": plan.mode,
                "playlist": list(plan.playlist),
                "next_melody": next_id,
            }
