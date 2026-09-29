"""Static game information for the 192×32 LED matrix."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping

from server.voice_catalog import load_voice_catalog

try:
    import serial
except ImportError:
    serial = None


class MatrixDisplay:
    """Resolve the current screen; only the polling thread performs serial I/O."""

    STATUS_HOLD_S = 2.0
    RESEND_S = 1.0
    RETRY_S = 1.0
    BOOT_S = 1.6

    def __init__(self, config: Mapping, *, serial_module=None, catalog=None,
                 clock=time.monotonic) -> None:
        self.enabled = bool(config.get("enabled", False))
        self.port = str(config.get("port", "COM6"))
        self.baudrate = int(config.get("baudrate", 115200))
        self._serial = serial if serial_module is None else serial_module
        self._voices = (catalog or load_voice_catalog()).get("voices", {})
        self._clock = clock
        self._device = None
        self._text = ""
        self._state = None
        self._reset_at = 0.0
        self._last_payload = None
        self._last_sent_at = float("-inf")
        self._retry_at = 0.0
        self._ready_at = 0.0
        self._closed = False
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()

    def _disconnect(self) -> None:
        device, self._device = self._device, None
        self._last_payload = None
        if device is not None:
            try:
                device.close()
            except Exception:
                pass

    def _connect(self, now: float) -> bool:
        if self._device is not None:
            return now >= self._ready_at
        if self._serial is None or now < self._retry_at:
            return False
        self._retry_at = now + self.RETRY_S
        try:
            self._device = self._serial.Serial(
                self.port, self.baudrate, timeout=0, write_timeout=0.05,
            )
            # Opening the USB port can reboot the ESP32. Never sleep on a game thread.
            self._ready_at = self._clock() + self.BOOT_S
            self._last_payload = None
        except Exception as exc:
            print(f"[dotmatrix] connection failed ({self.port}): {exc}")
            return False
        return now >= self._ready_at

    def _voice_text(self, voice: str) -> str:
        group, _, index = voice.rpartition("_")
        profile = self._voices.get(voice) or {}
        labels = profile.get("labels") or {}
        member = str(labels.get("en") or index or voice)
        # The built-in font and serial frame have finite capacity. Keep ordinary
        # labels unchanged, but never leave the panel stuck on an oversized frame.
        text = f"{group or voice}-{member}".upper()
        return "".join(c if " " <= c <= "~" else "?" for c in text)[:32]

    def show_voice(self, voice: str, snapshot: dict) -> None:
        """Remember the latest placement without delaying its MIDI note."""
        if snapshot.get("state") != "RUNNING":
            return
        with self._lock:
            self._text = self._voice_text(voice)

    def clear_voice(self) -> None:
        """A new session must not inherit a name, even between two polls."""
        with self._lock:
            self._text = ""

    @staticmethod
    def _status(text: str) -> dict:
        return {"type": "status", "text": text}

    def _payload(self, snapshot: dict, playback: dict, now: float) -> dict:
        state = snapshot.get("state")
        mode = str(playback.get("mode") or "").upper()
        if state != self._state:
            self._state = state
            self._reset_at = now
            if state != "RUNNING":
                self._text = ""
        if state == "READY":
            return self._status("PLACE TRAY")
        if state == "RESETTING":
            reason = snapshot.get("end_reason")
            if reason == "complete" and playback.get("active") and mode == "DFESTA":
                self._reset_at = now
                return self._status("COMPLETE")
            elapsed = now - self._reset_at
            terminal = {"complete": "COMPLETE", "idle": "TIME OVER",
                        "time_over": "TIME OVER"}.get(reason)
            if terminal:
                if elapsed < self.STATUS_HOLD_S:
                    return self._status(terminal)
                elapsed -= self.STATUS_HOLD_S
            return self._status(
                "RESETTING" if elapsed < self.STATUS_HOLD_S else "CLEAR TABLE"
            )
        if state != "RUNNING":
            return {"type": "clear"}
        if playback.get("active"):
            message = {"LISTEN": "HINT", "PLAY": "PLAYING",
                       "DFESTA": "COMPLETE"}.get(mode)
            if message:
                return self._status(message)
        remaining = (snapshot.get("timers") or {}).get("remaining_time_over_s")
        try:
            seconds = float(remaining)
        except (TypeError, ValueError, OverflowError):
            seconds = float("nan")
        if not math.isfinite(seconds):
            return self._status("PLACE ORB")
        # Keep the existing name/countdown protocol and layout, including before
        # the first placement (empty name, live countdown).
        return {"type": "display", "text": self._text,
                "seconds": min(2147483647, max(0, math.ceil(seconds)))}

    def update(self, snapshot: dict, playback: dict | None = None) -> None:
        """Send only current state, retry failures, and recover silent board resets."""
        with self._io_lock:
            if not self.enabled or self._closed:
                return
            now = self._clock()
            with self._lock:
                payload = self._payload(snapshot, playback or {}, now)
            if not self._connect(now):
                return
            if payload == self._last_payload and now - self._last_sent_at < self.RESEND_S:
                return
            line = (json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                    + "\n").encode("ascii")
            try:
                written = self._device.write(line)
                if written != len(line):
                    raise OSError("incomplete matrix command")
            except Exception as exc:
                print(f"[dotmatrix] disconnected ({self.port}): {exc}")
                self._disconnect()
                self._retry_at = self._clock() + self.RETRY_S
                return
            self._last_payload = payload
            self._last_sent_at = now

    def close(self) -> None:
        with self._io_lock:
            self._closed = True
            self._disconnect()
