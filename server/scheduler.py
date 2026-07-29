"""Python-owned timing for Listen, Play, and Dispensa playback.

The browser used to own a set of ``setTimeout`` calls for these three modes.
That made playback depend on browser load and made MIDI timing differ between
screen tabs.  ``PlaybackScheduler`` builds an immutable cue list, then executes
it from one cancellable worker using absolute monotonic deadlines.
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
    """The three hardware playback commands supported by OrbBridge."""

    LISTEN = "listen"
    PLAY = "play"
    DISPENSA = "dispensa"


@dataclass(frozen=True)
class MelodyStep:
    """One melody slot normalized for the scheduler."""

    index: int
    pitch: int | None
    duration_beats: float


@dataclass(frozen=True)
class Melody:
    """Validated BPM and ordered steps loaded from a melody JSON file."""

    bpm: float
    steps: tuple[MelodyStep, ...]

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.bpm


@dataclass(frozen=True)
class PlaybackCue:
    """One absolute-offset beat and the MIDI notes due at that beat."""

    step: int
    offset_s: float
    duration_ms: int
    notes: tuple[dict, ...]


@dataclass(frozen=True)
class PlaybackPlan:
    """Immutable input consumed by a scheduler worker."""

    mode: PlaybackMode
    cues: tuple[PlaybackCue, ...]
    duration_s: float


def _positive_number(value, label: str) -> float:
    """Convert a JSON number and reject booleans, NaN, and non-positive data."""

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
    """Map C4..C5 to the eight Ableton pitch indices; rests return ``None``."""

    name = str(value or "").strip().upper()
    if name in REST_NAMES:
        return None
    if name not in NOTE_PITCH:
        raise ValueError(f"unsupported melody note: {value}")
    return NOTE_PITCH[name]


def parse_melody(raw: Mapping) -> Melody:
    """Normalize the Melody Editor JSON shape into scheduler data."""

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
    """Read and validate one UTF-8 melody file."""

    with Path(path).open(encoding="utf-8") as source:
        return parse_melody(json.load(source))


def _cell_pitch(cell: Mapping) -> int | None:
    """Resolve the placed row pitch from a GameEngine cell snapshot."""

    raw_cell = cell.get("cell")
    if raw_cell is not None:
        try:
            return (int(raw_cell) - 1) % 8
        except (TypeError, ValueError):
            return None
    note = str(cell.get("note") or "").strip().upper()
    # GameEngine cell notes omit the octave; the final C is still pitch 7
    # when the physical cell number is available. This fallback maps plain C
    # to the lower C because no row identity remains.
    return {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}.get(note)


def _ordered_cells(cells: Mapping[str, Mapping]) -> list[Mapping]:
    """Return stable physical order so MIDI batches are reproducible."""

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
    """Build all notes and absolute offsets before starting a worker.

    Listen uses the dedicated ``_listen`` route and starts immediately.
    Play reproduces each placed orb at its physical column and pitch.
    Dispensa makes every unique placed voice sing the target melody together.
    """

    mode = PlaybackMode(mode)
    cells = cells or {}
    ordered_cells = _ordered_cells(cells)
    lead_s = 0.0 if mode is PlaybackMode.LISTEN else PLAYBACK_LEAD_S
    beat_s = melody.beat_seconds
    offset_s = lead_s

    # Dispensa uses each voice once even if one orb appears more than once.
    dispensa_voices = []
    seen_voices = set()
    for cell in ordered_cells:
        voice = str(cell.get("voice") or "").strip()
        if not voice or voice == "unknown" or voice in seen_voices:
            continue
        seen_voices.add(voice)
        dispensa_voices.append(voice)

    cues = []
    for step in melody.steps:
        slot_s = step.duration_beats * beat_s
        full_duration_ms = max(40, round(slot_s * 1000))
        notes = []

        if mode is PlaybackMode.LISTEN and step.pitch is not None:
            # Match the former browser preview envelope: slightly shorter than
            # its slot and capped so long notes do not blur into the next cue.
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
        elif mode is PlaybackMode.DISPENSA and step.pitch is not None:
            notes.extend(
                {
                    "voice": voice,
                    "pitch": step.pitch,
                    "duration_ms": full_duration_ms,
                }
                for voice in dispensa_voices
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
    """Run one playback plan at a time on a cancellable background thread."""

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
        """Return current playback identity without exposing thread objects."""

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
        """Cancel the previous run and start a newly snapshotted plan."""

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
        """Request cancellation without blocking the caller's runtime thread."""

        with self._lock:
            if self._cancel is not None:
                self._cancel.set()

    def close(self, timeout: float = 2.0) -> None:
        """Stop playback and reject later starts. Repeated calls are safe."""

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
            # MIDI failure must not kill timing or suppress later beat events.
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

            # Publish and MIDI belong to one Python-owned beat. The screen only
            # renders this event; it does not calculate a future timeout.
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
