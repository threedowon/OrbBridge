"""Ordered game feedback on a dedicated MIDI port, outside the runtime worker."""

from __future__ import annotations

import logging
import math
from queue import Empty, Queue
import threading

from server.inout import midi_out


DEFAULT_NOTES = {
    "ready": 60, "tray_placed": 61, "tray_removed": 62,
    "loading": 63, "correct": 64, "time_over": 65, "farewell": 66,
}
LOGGER = logging.getLogger(__name__)


class GameEvents:
    def __init__(self, config: dict, *, send_note=None):
        self.enabled = bool(config.get("enabled", True))
        self.notes = {**DEFAULT_NOTES, **config.get("notes", {})}
        values = list(self.notes.values())
        if any(type(n) is not int or not 0 <= n <= 127 for n in values) or len(set(values)) != len(values):
            raise ValueError("event notes must be distinct MIDI integers from 0 to 127")
        self.velocity = config.get("velocity", 127)
        if type(self.velocity) is not int or not 1 <= self.velocity <= 127:
            raise ValueError("event velocity must be an integer from 1 to 127")
        self.duration_ms = float(config.get("note_duration_ms", 30))
        if not math.isfinite(self.duration_ms) or self.duration_ms <= 0:
            raise ValueError("event note_duration_ms must be positive")
        self._send = send_note or midi_out.event_note
        self._queue = Queue()
        self._observed_correct = False
        self._desired_correct = False
        self._gate_pending = False
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="orbbridge-event-midi", daemon=True)
        self._thread.start()

    def emit(self, event: str) -> None:
        if self.enabled and not self._closed:
            self._queue.put(("event", event))

    def observe(self, previous: dict, current: dict, action: str) -> None:
        """Called in runtime command order. No MIDI or serial I/O here."""
        if not self.enabled or self._closed:
            return
        state = current.get("state")
        initial = current.get("initial_hint", {})
        correct = (state == "RUNNING" and bool(current.get("is_correct"))
                   and not initial.get("pending")
                   and not current.get("completion", {}).get("dfesta_presses", 0))
        if correct != self._observed_correct:
            self._observed_correct = correct
            self._queue.put(("gate", correct))
        before_tray, tray = previous.get("tray", {}), current.get("tray", {})
        if action == "tray":
            if before_tray.get("seated") is False and tray.get("seated") is True:
                self.emit("tray_placed")
            if before_tray.get("present") is True and tray.get("present") is False:
                self.emit("tray_removed")
        if previous.get("state") != "RUNNING" and state == "RUNNING" and initial.get("delay_s", 0) > 0:
            self.emit("loading")
        if previous.get("state") == "RUNNING" and state != "RUNNING":
            reason = current.get("end_reason") or current.get("cleanup_end_reason")
            if reason in ("idle", "time_over"):
                self.emit("time_over")

    def screen_changed(self, screen: str) -> None:
        if screen in ("ready", "farewell"):
            self.emit(screen)

    def flush(self, timeout=2.0) -> None:
        barrier = threading.Event()
        self._queue.put(("barrier", barrier))
        if not barrier.wait(timeout):
            raise RuntimeError("event MIDI worker did not finish")

    def _send_gate(self):
        self._gate_pending = True
        result = self._send(self.notes["correct"], velocity=self.velocity,
                            duration_ms=None, active=self._desired_correct)
        self._gate_pending = not result.get("ok")
        return result

    def _run(self) -> None:
        while True:
            try:
                kind, value = self._queue.get(timeout=0.2)
            except Empty:
                if self.enabled and (self._desired_correct or self._gate_pending):
                    try:
                        self._send_gate()  # Idempotent; restores a held note after MIDI reconnect.
                    except Exception:
                        LOGGER.exception("event MIDI gate retry failed")
                continue
            try:
                if kind == "stop":
                    if self.enabled and (self._desired_correct or self._gate_pending):
                        self._desired_correct = False
                        self._send_gate()
                    return
                if kind == "barrier":
                    value.set()
                elif kind == "gate":
                    self._desired_correct = value
                    self._send_gate()
                else:
                    result = self._send(self.notes[value], velocity=self.velocity,
                                        duration_ms=self.duration_ms)
                    if not result.get("ok"):
                        LOGGER.warning("event %s: %s", value, result.get("error"))
            except Exception:
                LOGGER.exception("event MIDI output failed")
            finally:
                self._queue.task_done()
                if kind == "stop":
                    return

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put(("stop", None))
            self._thread.join(timeout=2.0)
