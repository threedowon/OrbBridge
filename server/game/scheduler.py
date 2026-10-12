"""Listen·Play·DFesta 재생 시각을 Python에서 관리한다.

브라우저의 여러 ``setTimeout``에 분산돼 있던 재생 시각을 하나의
``PlaybackScheduler``로 모은다. 변경 불가능한 cue 목록을 만든 뒤,
취소 가능한 worker 하나가 고해상도 perf_counter 절대시각 기준으로 실행한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from enum import Enum
import json
import logging
from math import isfinite
from pathlib import Path
from queue import SimpleQueue
import random
import sys
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
DFESTA_PARTS = ("melody", "harmony_a", "harmony_b")
DEFAULT_DFESTA_WEIGHTS = (70.0, 15.0, 15.0)


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
    harmony: tuple[int | None, int | None] | None = None
    velocity: int = 127


@dataclass(frozen=True)
class Melody:
    """멜로디 JSON에서 읽고 검증한 BPM과 순서가 있는 step이다."""

    bpm: float
    steps: tuple[MelodyStep, ...]
    dfesta_weights: tuple[float, float, float] = DEFAULT_DFESTA_WEIGHTS

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

    dfesta = raw.get("dfesta", {})
    if not isinstance(dfesta, Mapping):
        raise ValueError("dfesta must be an object")
    weights = dfesta.get("weights", dict(zip(DFESTA_PARTS, DEFAULT_DFESTA_WEIGHTS)))
    if not isinstance(weights, Mapping) or set(weights) != set(DFESTA_PARTS):
        raise ValueError("dfesta.weights must contain melody, harmony_a and harmony_b")
    parsed_weights = tuple(
        _positive_number(weights[part], f"dfesta.weights.{part}")
        for part in DFESTA_PARTS
    )
    if not isfinite(sum(parsed_weights)) or parsed_weights[0] <= sum(parsed_weights[1:]):
        raise ValueError("dfesta melody weight must exceed the two harmony weights combined")

    steps = []
    for index, raw_step in enumerate(raw_steps):
        if isinstance(raw_step, Mapping):
            note = raw_step.get("note", raw_step.get("name"))
            duration = raw_step.get("duration", raw_step.get("dur", 1))
        else:
            note = raw_step
            duration = 1
        pitch = _pitch_from_name(note)
        velocity = raw_step.get("velocity", 127) if isinstance(raw_step, Mapping) else 127
        if isinstance(velocity, bool) or not isinstance(velocity, int) or not 1 <= velocity <= 127:
            raise ValueError(f"step {index + 1} velocity must be an integer from 1 to 127")
        harmony = None
        if isinstance(raw_step, Mapping) and "harmony" in raw_step:
            values = raw_step["harmony"]
            if not isinstance(values, (list, tuple)) or len(values) != 2:
                raise ValueError(f"step {index + 1} harmony must contain exactly two notes")
            harmony = tuple(_pitch_from_name(value) for value in values)
            if any((value is None) != (pitch is None) for value in harmony):
                raise ValueError(f"step {index + 1} harmony must rest together with melody")
        steps.append(
            MelodyStep(
                index=index,
                pitch=pitch,
                duration_beats=_positive_number(duration, f"step {index + 1} duration"),
                harmony=harmony,
                velocity=velocity,
            )
        )
    return Melody(bpm=bpm, steps=tuple(steps), dfesta_weights=parsed_weights)


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


def _assign_dfesta_parts(
    voices: list[str], melody: Melody, rng: random.Random,
) -> dict[str, int]:
    """정수 인원 비율을 먼저 정하고 가수만 추첨한다. 한 plan 동안 성부는 고정된다."""

    count = len(voices)
    # 정상 게임은 곡의 각 유음 step에 오브가 필요하다(현재 곡은 8~11개).
    # 진단/중복 voice 등으로 인원이 부족하면 안전하게 기존 유니즌을 유지한다.
    if count < 5 or not any(step.harmony is not None for step in melody.steps):
        return dict.fromkeys(voices, 0)

    total_weight = sum(melody.dfesta_weights)
    targets = [count * (weight / total_weight) for weight in melody.dfesta_weights]
    counts = [count // 2 + 1, 1, 1]
    for _ in range(count - sum(counts)):
        part = max(range(3), key=lambda index: targets[index] - counts[index])
        counts[part] += 1
    shuffled = list(voices)
    rng.shuffle(shuffled)
    roles = [part for part, size in enumerate(counts) for _ in range(size)]
    return dict(zip(shuffled, roles))


def build_plan(
    mode: PlaybackMode | str,
    melody: Melody,
    cells: Mapping[str, Mapping] | None = None,
    *,
    rng: random.Random | None = None,
) -> PlaybackPlan:
    """worker 시작 전에 모든 음표와 절대 offset을 계산한다.

    Listen은 전용 ``_listen`` 경로로 즉시 시작한다.
    Play는 배치된 오브를 실제 열과 pitch로 재생한다.
    DFesta는 배치된 고유 voice를 멜로디 과반과 두 화음 성부로 나눈다.
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

    dfesta_parts = (
        _assign_dfesta_parts(dfesta_voices, melody, rng if rng is not None else random.Random())
        if mode is PlaybackMode.DFESTA else {}
    )

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
                    "velocity": step.velocity,
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
                        "velocity": step.velocity,
                    }
                )
            duration_ms = full_duration_ms
        elif mode is PlaybackMode.DFESTA and step.pitch is not None:
            pitches = (step.pitch, *(step.harmony or (step.pitch, step.pitch)))
            notes.extend(
                {
                    "voice": voice,
                    "pitch": pitches[dfesta_parts[voice]],
                    "duration_ms": full_duration_ms,
                    "velocity": step.velocity,
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
        clock: Callable[[], float] = time.perf_counter,
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
        self._workers: set[threading.Thread] = set()
        self._closed = False
        self._timing_lock = threading.Lock()
        self._timing_run_id = 0
        self._timing_mode: str | None = None
        self._timing_samples: deque[dict] = deque(maxlen=256)
        # WebSocket/화면 callback의 lock이나 I/O가 MIDI clock을 멈추지 않게 한다.
        self._events: SimpleQueue = SimpleQueue()
        self._publisher = threading.Thread(
            target=self._publish_loop, daemon=True, name="orbbridge-playback-events",
        )
        self._publisher.start()

    def snapshot(self) -> dict:
        """스레드 객체를 노출하지 않고 현재 재생 식별자를 반환한다."""

        with self._lock:
            thread = self._thread
            return {
                "active": bool(thread and thread.is_alive()),
                "run_id": self._run_id,
                "mode": self._mode.value if self._mode is not None else None,
                "timing": self._timing_snapshot(),
            }

    def _timing_snapshot(self) -> dict:
        with self._timing_lock:
            samples = [dict(sample) for sample in self._timing_samples]
            return {
                "run_id": self._timing_run_id,
                "mode": self._timing_mode,
                "python_version": sys.version.split()[0],
                "clock": "perf_counter" if self._clock is time.perf_counter else "custom",
                "clock_resolution_ms": (
                    time.get_clock_info("perf_counter").resolution * 1000
                    if self._clock is time.perf_counter else None
                ),
                "samples": samples,
                "max_dispatch_late_ms": max(
                    (sample["dispatch_late_ms"] for sample in samples), default=0.0,
                ),
                "max_send_ms": max(
                    (sample["send_ms"] for sample in samples), default=0.0,
                ),
                "max_note_on_late_ms": max(
                    (sample.get("note_on_late_ms", 0.0) for sample in samples), default=0.0,
                ),
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
            with self._timing_lock:
                self._timing_run_id = run_id
                self._timing_mode = plan.mode.value
                self._timing_samples.clear()
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
            self._workers.add(thread)
            # start는 run 순서로 먼저 queue에 넣고 MIDI worker를 시작한다.
            self._safe_publish({
                "type": "start", "run_id": run_id, "mode": plan.mode.value,
                "step_count": len(plan.cues),
            })
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
            threads = list(self._workers)
            if cancel is not None:
                cancel.set()
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
        self._events.put(None)
        if self._publisher is not threading.current_thread():
            self._publisher.join(max(0.0, deadline - time.monotonic()))

    def _safe_publish(self, event: dict) -> None:
        self._events.put(event)

    def _publish_loop(self) -> None:
        while True:
            event = self._events.get()
            if event is None:
                return
            try:
                self._publish_event(event)
            except Exception:
                LOGGER.exception("scheduler event publish failed")

    def _safe_send(self, notes: tuple[dict, ...]) -> object:
        if not notes:
            return
        try:
            return self._send_notes([dict(note) for note in notes])
        except Exception:
            # MIDI 오류가 Scheduler 시각 처리와 다음 beat 이벤트를 중단하지 않게 한다.
            LOGGER.exception("scheduler MIDI send failed")

    def _record_timing(self, run_id, cue, due, dispatched, finished, results):
        sample = {
            "step": cue.step,
            "offset_ms": round(cue.offset_s * 1000, 3),
            "note_count": len(cue.notes),
            "dispatch_late_ms": round(max(0.0, dispatched - due) * 1000, 3),
            "send_ms": round(max(0.0, finished - dispatched) * 1000, 3),
        }
        sent = [
            result for result in (results if isinstance(results, list) else [])
            if isinstance(result, dict) and result.get("ok")
            and "note_on_completed_s" in result
        ]
        sample["sent_count"] = len(sent)
        sample["error_count"] = sum(
            not result.get("ok", False)
            for result in (results if isinstance(results, list) else [])
            if isinstance(result, dict)
        )
        if sent:
            first = min(result["note_on_started_s"] for result in sent)
            last = max(result["note_on_started_s"] for result in sent)
            sample.update(
                note_on_late_ms=round(max(0.0, last - due) * 1000, 3),
                note_on_spread_ms=round((last - first) * 1000, 3),
                note_on_completed_late_ms=round(max(
                    0.0, max(result["note_on_completed_s"] for result in sent) - due,
                ) * 1000, 3),
                driver_send_ms=round(max(
                    result["note_on_completed_s"] - result["note_on_started_s"]
                    for result in sent
                ) * 1000, 3),
            )
        with self._timing_lock:
            if run_id == self._timing_run_id:
                self._timing_samples.append(sample)

    def _run(
        self,
        run_id: int,
        plan: PlaybackPlan,
        cancel: threading.Event,
    ) -> None:
        started_at = self._clock()
        cancelled = False

        for cue in plan.cues:
            if not self._wait_until(started_at + cue.offset_s, cancel):
                cancelled = True
                break

            # MIDI를 먼저 전송한다. 표시/진단 작업은 전송 뒤, 별도 publisher에서.
            dispatched = self._clock()
            results = self._safe_send(cue.notes)
            finished = self._clock()
            self._record_timing(
                run_id, cue, started_at + cue.offset_s, dispatched, finished, results,
            )
            event = {
                "type": "beat",
                "run_id": run_id,
                "mode": plan.mode.value,
                "step": cue.step,
                "duration_ms": cue.duration_ms,
                "notes": [dict(note) for note in cue.notes],
            }
            self._safe_publish(event)

        if not cancelled:
            cancelled = not self._wait_until(started_at + plan.duration_s, cancel)

        self._safe_publish(
            {
                "type": "end",
                "run_id": run_id,
                "mode": plan.mode.value,
                "cancelled": cancelled,
            }
        )
        with self._lock:
            self._workers.discard(threading.current_thread())
            if self._run_id == run_id:
                self._cancel = None
                self._thread = None
                self._mode = None

    def _wait_until(self, deadline: float, cancel: threading.Event) -> bool:
        """긴 대기는 즉시 취소 가능하게, 마지막 20ms는 짧은 sleep으로 기다린다.

        Windows Event.wait는 clock 해상도와 별개로 OS tick 단위로 깨울 수 있다.
        Python 3.11+의 sleep은 고해상도 waitable timer를 사용한다. busy spin 없이
        매번 deadline을 다시 확인하고, 마지막 구간은 2ms 이하의 sleep을 요청한다.
        """

        while not cancel.is_set():
            remaining = deadline - self._clock()
            if remaining <= 0:
                return True
            if remaining > 0.020:
                cancel.wait(remaining - 0.020)
            else:
                time.sleep(min(remaining, 0.002))
        return False
