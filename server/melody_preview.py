"""Preview melody steps from Melody Register."""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

NOTE_FREQ = {
    "C4": 262,
    "D4": 294,
    "E4": 330,
    "F4": 349,
    "G4": 392,
    "A4": 440,
    "B4": 494,
    "C5": 523,
}

_lock = threading.Lock()
_cancel = threading.Event()
_thread: threading.Thread | None = None


def stop_preview() -> None:
    _cancel.set()


def _beep(freq: int, ms: int) -> None:
    ms = max(40, min(ms, 8000))
    if sys.platform == "win32":
        import winsound

        winsound.Beep(freq, ms)
        return
    # macOS / Linux — no stdlib tone; short sleep as rhythm guide
    time.sleep(ms / 1000)


def play_melody(
    steps: list[dict],
    bpm: int,
    on_step: Callable[[int], None] | None = None,
) -> str | None:
    """Play steps in a background thread. Returns error message or None."""
    global _thread

    if not steps:
        return "steps가 비어 있습니다."

    stop_preview()
    _cancel.clear()

    def run() -> None:
        beat_ms = 60_000 / max(1, bpm)
        for i, step in enumerate(steps):
            if _cancel.is_set():
                break
            note = str(step.get("note", "")).strip().upper()
            dur = float(step.get("duration") or 1)
            if on_step:
                try:
                    on_step(i)
                except Exception:
                    pass
            if note in ("REST", "X", "?", "R", "-") or not note:
                time.sleep(beat_ms * dur / 1000)
                continue
            freq = NOTE_FREQ.get(note, 440)
            _beep(freq, int(beat_ms * dur * 0.88))
        if on_step and not _cancel.is_set():
            try:
                on_step(-1)
            except Exception:
                pass

    with _lock:
        _thread = threading.Thread(target=run, daemon=True)
        _thread.start()
    return None
