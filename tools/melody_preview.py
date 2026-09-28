"""Render a velocity-sensitive reference tone preview, independent of live MIDI."""
from __future__ import annotations

from array import array
from io import BytesIO
from math import pi, sin
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Callable
import wave

from server.game.scheduler import parse_melody

SAMPLE_RATE = 22050
FREQUENCIES = (261.6256, 293.6648, 329.6276, 349.2282, 391.9954, 440, 493.8833, 523.2511)
_lock = threading.RLock()
_cancel: threading.Event | None = None


def render_preview(steps, bpm, cancel=None) -> bytes:
    melody = parse_melody({"bpm": bpm, "steps": steps})
    seconds = sum(step.duration_beats for step in melody.steps) * melody.beat_seconds
    if seconds > 120:
        raise ValueError("미리듣기는 최대 120초까지 가능합니다.")
    samples = array("h")
    offset = 0.0
    for step in melody.steps:
        if cancel is not None and cancel.is_set():
            return b""
        offset += step.duration_beats * melody.beat_seconds
        count = round(offset * SAMPLE_RATE) - len(samples)
        if count <= 0:
            continue
        if step.pitch is None:
            samples.extend(array("h", [0]) * count)
            continue
        # Preserve the entire slot, with a short silent tail and click-free envelope.
        sounding = max(1, round(count * 0.88))
        fade = max(1, min(round(SAMPLE_RATE * 0.006), sounding // 2))
        amplitude = 9000 * step.velocity / 127
        freq = FREQUENCIES[step.pitch]
        samples.extend(int(amplitude * min(1, i / fade, (sounding - 1 - i) / fade)
                           * sin(2 * pi * freq * i / SAMPLE_RATE)) for i in range(sounding))
        samples.extend(array("h", [0]) * (count - sounding))
    if sys.byteorder != "little":
        samples.byteswap()
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(samples.tobytes())
    return output.getvalue()


def stop_preview() -> None:
    global _cancel
    with _lock:
        if _cancel is not None:
            _cancel.set()
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(None, 0)
            _cancel = None


def play_melody(steps: list[dict], bpm: int, on_step: Callable[[int], None] | None = None,
                on_error: Callable[[str], None] | None = None) -> str | None:
    global _cancel
    if sys.platform != "win32":
        return "기준음 미리듣기는 현재 Windows에서 지원합니다. 실제 음원은 Ableton에서 확인하세요."
    try:
        melody = parse_melody({"bpm": bpm, "steps": steps})
        if sum(step.duration_beats for step in melody.steps) * melody.beat_seconds > 120:
            raise ValueError("미리듣기는 최대 120초까지 가능합니다.")
    except ValueError as exc:
        return str(exc)
    stop_preview()
    cancel = threading.Event()
    with _lock:
        _cancel = cancel

    def notify(index):
        if on_step:
            on_step(index)

    def run():
        import winsound
        try:
            audio = render_preview(steps, bpm, cancel)
            if not audio or cancel.is_set():
                return
            with TemporaryDirectory(prefix="orbbridge-preview-") as directory:
                path = Path(directory) / "preview.wav"
                path.write_bytes(audio)
                with _lock:
                    if cancel.is_set():
                        return
                    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                start, offset = time.perf_counter(), 0.0
                for i, step in enumerate(melody.steps):
                    if cancel.wait(max(0.0, start + offset - time.perf_counter())):
                        return
                    notify(i)
                    offset += step.duration_beats * melody.beat_seconds
                cancel.wait(max(0.0, start + offset - time.perf_counter()))
                # The file must stay alive until the async player releases it.
                with _lock:
                    if _cancel is cancel:
                        winsound.PlaySound(None, 0)
        except Exception as exc:
            if on_error and not cancel.is_set():
                on_error(str(exc))
        finally:
            notify(-1)

    threading.Thread(target=run, daemon=True, name="orbbridge-melody-preview").start()
    return None
