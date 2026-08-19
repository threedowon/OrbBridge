"""Listen·Play·DFesta 재생 시각을 Python에서 관리한다.

브라우저의 여러 ``setTimeout``에 분산돼 있던 재생 시각을 하나의
``PlaybackScheduler``로 모은다. 변경 불가능한 cue 목록을 만든 뒤,
취소 가능한 worker 하나가 monotonic 절대시각 기준으로 실행한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import logging
from math import isfinite
from pathlib import Path
import threading
import time
from typing import Callable, Mapping, Sequence


LOGGER = logging.getLogger(__name__)
NOTE_PITCH = {
    "C4": 0,
    "D4": 1,
    "E4": 2,
    "F4": 3,
    "G4": 4,
    "A4": 5,
    "B4": 6,
    "C5": 7,
}
REST_NAMES = {"R", "REST"}
PLAYBACK_LEAD_S = 0.25


class PlaybackMode(str, Enum):
    """OrbBridge가 지원하는 세 가지 하드웨어 재생 명령이다."""

    LISTEN = "listen"
    PLAY = "play"
    DFESTA = "dfesta"


@dataclass(frozen=True)
class MelodyStep:
    """Scheduler 형식으로 정규화한 멜로디 한 칸이다."""

    index: int
    pitch: int | None
    duration_beats: float


@dataclass(frozen=True)
class Melody:
    """멜로디 JSON에서 읽고 검증한 BPM과 순서가 있는 step이다."""

    bpm: float
    steps: tuple[MelodyStep, ...]

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.bpm


@dataclass(frozen=True)
class PlaybackCue:
    """절대 offset과 해당 시각에 출력할 MIDI 음표 묶음이다."""

    step: int
    offset_s: float
    duration_ms: int
    notes: tuple[dict, ...]


@dataclass(frozen=True)
class PlaybackPlan:
    """Scheduler worker가 소비하는 변경 불가능한 입력이다."""

    mode: PlaybackMode
    cues: tuple[PlaybackCue, ...]
    duration_s: float


def _positive_number(value, label: str) -> float:
    """JSON 숫자를 변환하고 bool·NaN·0 이하 값을 거부한다."""

    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive number") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a positive number")
    return number


def _pitch_from_name(value) -> int | None:
    """C4~C5를 Ableton pitch index 8개로 바꾸고 쉼표는 ``None``으로 반환한다."""

    name = str(value or "").strip().upper()
    if name in REST_NAMES:
        return None
    if name not in NOTE_PITCH:
        raise ValueError(f"unsupported melody note: {value}")
    return NOTE_PITCH[name]


def parse_melody(raw: Mapping) -> Melody:
    """멜로디 JSON을 Scheduler 데이터로 정규화한다."""

    if not isinstance(raw, Mapping):
        raise ValueError("melody must be an object")
    bpm = _positive_number(raw.get("bpm"), "bpm")
    raw_steps = raw.get("steps", raw.get("notes"))
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, (str, bytes)):
        raise ValueError("melody steps must be a non-empty array")
    if not raw_steps:
        raise ValueError("melody steps must be a non-empty array")

    steps = []
    for index, raw_step in enumerate(raw_steps):
        if isinstance(raw_step, Mapping):
            note = raw_step.get("note", raw_step.get("name"))
            duration = raw_step.get("duration", raw_step.get("dur", 1))
        else:
            note = raw_step
            duration = 1
        steps.append(
            MelodyStep(
                index=index,
                pitch=_pitch_from_name(note),
                duration_beats=_positive_number(duration, f"step {index + 1} duration"),
            )
        )
    return Melody(bpm=bpm, steps=tuple(steps))


def load_melody(path: Path) -> Melody:
    """UTF-8 멜로디 파일 하나를 읽고 검증한다."""

    with Path(path).open(encoding="utf-8") as source:
        return parse_melody(json.load(source))


def _cell_pitch(cell: Mapping) -> int | None:
    """GameEngine 셀 snapshot에서 배치된 행의 pitch를 구한다."""

    raw_cell = cell.get("cell")
    if raw_cell is not None:
        try:
            return (int(raw_cell) - 1) % 8
        except (TypeError, ValueError):
            return None
    note = str(cell.get("note") or "").strip().upper()
    # GameEngine 음표에는 octave가 없다. 실제 cell 번호가 있으면 마지막 C는
    # pitch 7이지만, 행 정보가 없는 fallback에서는 C를 낮은 C로 처리한다.
    return {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}.get(note)


def _ordered_cells(cells: Mapping[str, Mapping]) -> list[Mapping]:
    """MIDI 묶음의 순서가 재현되도록 셀을 물리적 순서로 정렬한다."""

    def key(cell: Mapping) -> tuple[int, int]:
        try:
            return int(cell.get("position", 0)), int(cell.get("cell", 0))
        except (TypeError, ValueError):
            return 0, 0

    return sorted(
        (cell for cell in cells.values() if isinstance(cell, Mapping)),
        key=key,
    )


def build_plan(
    mode: PlaybackMode | str,
    melody: Melody,
    cells: Mapping[str, Mapping] | None = None,
) -> PlaybackPlan:
    """worker 시작 전에 모든 음표와 절대 offset을 계산한다.

    Listen은 전용 ``_listen`` 경로로 즉시 시작한다.
    Play는 배치된 오브를 실제 열과 pitch로 재생한다.
    DFesta는 배치된 고유 voice가 목표 멜로디를 함께 재생하게 한다.
    """

    mode = PlaybackMode(mode)
    cells = cells or {}
    ordered_cells = _ordered_cells(cells)
    lead_s = 0.0 if mode is PlaybackMode.LISTEN else PLAYBACK_LEAD_S
    beat_s = melody.beat_seconds
    offset_s = lead_s

    # 같은 voice의 오브가 여러 번 있어도 DFesta에서는 한 번만 사용한다.
    dfesta_voices = []
    seen_voices = set()
    for cell in ordered_cells:
        voice = str(cell.get("voice") or "").strip()
        if not voice or voice == "unknown" or voice in seen_voices:
            continue
        seen_voices.add(voice)
        dfesta_voices.append(voice)

    cues = []
    for step in melody.steps:
        slot_s = step.duration_beats * beat_s
        full_duration_ms = max(40, round(slot_s * 1000))
        notes = []

        if mode is PlaybackMode.LISTEN and step.pitch is not None:
            # 기존 브라우저 미리듣기처럼 칸보다 조금 짧게 재생하고,
            # 긴 음이 다음 cue와 겹치지 않도록 최대 길이를 제한한다.
            preview_ms = max(40, round(min(slot_s * 0.92, 0.9) * 1000))
            notes.append(
                {
                    "voice": "_listen",
                    "pitch": step.pitch,
                    "duration_ms": preview_ms,
                }
            )
            duration_ms = preview_ms
        elif mode is PlaybackMode.PLAY:
            for cell in ordered_cells:
                try:
                    position = int(cell.get("position", 0))
                except (TypeError, ValueError):
                    continue
                if position != step.index + 1:
                    continue
                voice = str(cell.get("voice") or "").strip()
                pitch = _cell_pitch(cell)
                if not voice or voice == "unknown" or pitch is None:
                    continue
                notes.append(
                    {
                        "voice": voice,
                        "pitch": pitch,
                        "duration_ms": full_duration_ms,
                    }
                )
            duration_ms = full_duration_ms
        elif mode is PlaybackMode.DFESTA and step.pitch is not None:
            notes.extend(
                {
                    "voice": voice,
                    "pitch": step.pitch,
                    "duration_ms": full_duration_ms,
                }
                for voice in dfesta_voices
            )
            duration_ms = full_duration_ms
        else:
            duration_ms = full_duration_ms

        cues.append(
            PlaybackCue(
                step=step.index,
                offset_s=offset_s,
                duration_ms=duration_ms,
                notes=tuple(notes),
            )
        )
        offset_s += slot_s

    return PlaybackPlan(mode=mode, cues=tuple(cues), duration_s=offset_s)


class PlaybackScheduler:
    """취소 가능한 백그라운드 스레드에서 재생 plan 하나만 실행한다."""

    def __init__(
        self,
        *,
        send_notes: Callable[[list[dict]], object],
        publish_event: Callable[[dict], object],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(send_notes) or not callable(publish_event):
            raise TypeError("send_notes and publish_event must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._send_notes = send_notes
        self._publish_event = publish_event
        self._clock = clock
        self._lock = threading.Lock()
        self._run_id = 0
        self._mode: PlaybackMode | None = None
        self._cancel: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._closed = False

    def snapshot(self) -> dict:
        """스레드 객체를 노출하지 않고 현재 재생 식별자를 반환한다."""

        with self._lock:
            thread = self._thread
            return {
                "active": bool(thread and thread.is_alive()),
                "run_id": self._run_id,
                "mode": self._mode.value if self._mode is not None else None,
            }

    def start(
        self,
        mode: PlaybackMode | str,
        melody: Melody,
        cells: Mapping[str, Mapping] | None = None,
    ) -> dict:
        """이전 재생을 취소하고 새 snapshot으로 만든 plan을 시작한다."""

        plan = build_plan(mode, melody, cells)
        with self._lock:
            if self._closed:
                raise RuntimeError("scheduler is closed")
            if self._cancel is not None:
                self._cancel.set()
            self._run_id += 1
            run_id = self._run_id
            cancel = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(run_id, plan, cancel),
                daemon=True,
                name=f"orbbridge-{plan.mode.value}-{run_id}",
            )
            self._cancel = cancel
            self._thread = thread
            self._mode = plan.mode
            thread.start()
        return {
            "run_id": run_id,
            "mode": plan.mode.value,
            "step_count": len(plan.cues),
        }

    def cancel(self) -> None:
        """호출한 런타임 스레드를 막지 않고 취소를 요청한다."""

        with self._lock:
            if self._cancel is not None:
                self._cancel.set()

    def close(self, timeout: float = 2.0) -> None:
        """재생을 멈추고 이후 시작을 거부하며 중복 호출은 무시한다."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            cancel = self._cancel
            thread = self._thread
            if cancel is not None:
                cancel.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    def _safe_publish(self, event: dict) -> None:
        try:
            self._publish_event(event)
        except Exception:
            LOGGER.exception("scheduler event publish failed")

    def _safe_send(self, notes: tuple[dict, ...]) -> None:
        if not notes:
            return
        try:
            self._send_notes([dict(note) for note in notes])
        except Exception:
            # MIDI 오류가 Scheduler 시각 처리와 다음 beat 이벤트를 중단하지 않게 한다.
            LOGGER.exception("scheduler MIDI send failed")

    def _run(
        self,
        run_id: int,
        plan: PlaybackPlan,
        cancel: threading.Event,
    ) -> None:
        started_at = self._clock()
        self._safe_publish(
            {
                "type": "start",
                "run_id": run_id,
                "mode": plan.mode.value,
                "step_count": len(plan.cues),
            }
        )
        cancelled = False

        for cue in plan.cues:
            remaining = started_at + cue.offset_s - self._clock()
            if remaining > 0 and cancel.wait(remaining):
                cancelled = True
                break
            if cancel.is_set():
                cancelled = True
                break

            # 이벤트 발행과 MIDI 출력은 같은 Python beat에 속한다.
            # screen은 이 이벤트를 표시할 뿐 다음 시각을 계산하지 않는다.
            event = {
                "type": "beat",
                "run_id": run_id,
                "mode": plan.mode.value,
                "step": cue.step,
                "duration_ms": cue.duration_ms,
                "notes": [dict(note) for note in cue.notes],
            }
            self._safe_publish(event)
            self._safe_send(cue.notes)

        if not cancelled:
            remaining = started_at + plan.duration_s - self._clock()
            if remaining > 0 and cancel.wait(remaining):
                cancelled = True
            elif cancel.is_set():
                cancelled = True

        self._safe_publish(
            {
                "type": "end",
                "run_id": run_id,
                "mode": plan.mode.value,
                "cancelled": cancelled,
            }
        )
        with self._lock:
            if self._run_id == run_id:
                self._cancel = None
                self._thread = None
                self._mode = None
