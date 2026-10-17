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
    CONNECTION_FAILURE_LOG_LIMIT = 5
    DEFAULT_TEXTS = {
        "ready": "PLACE TRAY", "loading": "LOADING...", "hint": "HINT",
        "playing": "PLAYING", "complete": "COMPLETE", "press_dfesta": "PRESS DFESTA!",
        "time_over": "TIME OVER", "clear_table": "PLEASE CLEAR TABLE",
        "farewell": "GOODBYE", "place_orb": "PLACE ORB", "orb_check": "ORB CHECK",
        "offline": "OFFLINE",
    }

    def __init__(self, config: Mapping, *, serial_module=None, catalog=None,
                 clock=time.monotonic, on_screen_change=None) -> None:
        self.enabled = bool(config.get("enabled", False))
        self.port = str(config.get("port", "COM6"))
        self.baudrate = int(config.get("baudrate", 115200))
        self.texts = {**self.DEFAULT_TEXTS, **config.get("texts", {})}
        self._configure_offline = "offline" in config.get("texts", {})
        for key, value in self.texts.items():
            if not isinstance(value, str) or len(value) > 32 or any(not " " <= c <= "~" for c in value):
                raise ValueError(f"matrix_display.texts.{key} must be at most 32 printable ASCII characters")
        self._screen = None
        self._resolved_screen = None
        self._screen_revision = 0
        self._notified_revision = -1
        self._on_screen_change = on_screen_change
        try:
            farewell = float(config.get("farewell_duration_s", 4))
            self.farewell_duration_s = farewell if math.isfinite(farewell) and farewell >= 0 else 4.0
        except (TypeError, ValueError, OverflowError):
            self.farewell_duration_s = 4.0
        self._serial = serial if serial_module is None else serial_module
        self._voices = (catalog or load_voice_catalog()).get("voices", {})
        self._clock = clock
        self._device = None
        self._text = ""
        self._state = None
        self._reset_at = 0.0
        self._timeout_until = 0.0
        self._farewell_shift_s = 0.0
        self._last_payload = None
        self._last_sent_at = float("-inf")
        self._retry_at = 0.0
        self._connection_failure_logs = 0
        self._ready_at = 0.0
        self._closed = False
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._connection_state = "waiting" if self.enabled else "disabled"
        self._connection_error = ""

    def connection_status(self) -> dict:
        """Return cached serial status without opening or probing the device."""
        with self._lock:
            return {"enabled": self.enabled, "port": self.port,
                    "state": self._connection_state,
                    "connected": self._connection_state == "connected",
                    "error": self._connection_error}

    def _set_connection_status(self, state: str, error: str = "") -> None:
        with self._lock:
            self._connection_state = state
            self._connection_error = error

    def _disconnect(self, error: str = "") -> None:
        self._set_connection_status("disconnected" if self.enabled else "disabled", error)
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
        if self._serial is None:
            self._set_connection_status("disconnected", "pyserial not installed")
            return False
        if now < self._retry_at:
            return False
        self._retry_at = now + self.RETRY_S
        try:
            self._device = self._serial.Serial(
                self.port, self.baudrate, timeout=0, write_timeout=0.05,
            )
            # Opening the USB port can reboot the ESP32. Never sleep on a game thread.
            self._ready_at = self._clock() + self.BOOT_S
            self._last_payload = None
            self._set_connection_status("connecting")
        except Exception as exc:
            self._set_connection_status("disconnected", str(exc))
            if self._connection_failure_logs < self.CONNECTION_FAILURE_LOG_LIMIT:
                print(f"[dotmatrix] connection failed ({self.port}): {exc}")
                self._connection_failure_logs += 1
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

    def _status(self, key: str) -> dict:
        self._screen = key
        return {"type": "status", "text": self.texts[key]}

    def _payload(self, snapshot: dict, playback: dict, now: float) -> dict:
        self._screen = "blank"
        if snapshot.get("orb_inspection"):
            return self._status("orb_check")
        state = snapshot.get("state")
        mode = str(playback.get("mode") or "").upper()
        cleared = (snapshot.get("timers") or {}).get("since_table_cleared_s")
        cleared_timeout = snapshot.get("cleanup_end_reason") in ("idle", "time_over")
        if state != self._state:
            if state == "RUNNING":
                self._timeout_until = 0.0
                self._farewell_shift_s = 0.0
            elif state == "RESETTING":
                self._timeout_until = (
                    now + self.STATUS_HOLD_S
                    if snapshot.get("end_reason") in ("idle", "time_over") else 0.0
                )
            elif state == "READY":
                # Empty tables can leave RESETTING between two display polls.
                # Recover that terminal message, but do not replay old cleanups.
                if (cleared_timeout and self._timeout_until == 0.0
                        and cleared is not None and cleared < self.STATUS_HOLD_S):
                    self._timeout_until = now + self.STATUS_HOLD_S
                self._farewell_shift_s = (
                    max(0.0, self._timeout_until - now + cleared)
                    if cleared_timeout and cleared is not None else 0.0
                )
            self._state = state
            self._reset_at = now
            if state != "RUNNING":
                self._text = ""
        if state == "READY":
            if cleared_timeout and now < self._timeout_until:
                return self._status("time_over")
            if snapshot.get("cell_count", 0):
                return self._status("clear_table")
            if (snapshot.get("tray") or {}).get("present") is False and cleared is not None:
                cleared -= self._farewell_shift_s
                if cleared < self.farewell_duration_s:
                    return self._status("farewell")
                if cleared < self.farewell_duration_s * 2:
                    return {"type": "clear"}
            return self._status("ready")
        if state == "RESETTING":
            reason = snapshot.get("end_reason")
            if reason == "complete" and playback.get("active") and mode == "DFESTA":
                self._reset_at = now
                return self._status("complete")
            elapsed = now - self._reset_at
            terminal = {"complete": "complete", "idle": "time_over",
                        "time_over": "time_over"}.get(reason)
            if terminal:
                if elapsed < self.STATUS_HOLD_S:
                    return self._status(terminal)
            return self._status("clear_table")
        if state != "RUNNING":
            return {"type": "clear"}
        if (snapshot.get("initial_hint") or {}).get("pending"):
            return self._status("loading")
        if snapshot.get("is_correct") and not (playback.get("active") and mode == "DFESTA"):
            return self._status("press_dfesta")
        if playback.get("active"):
            message = {"LISTEN": "hint", "PLAY": "playing",
                       "DFESTA": "complete"}.get(mode)
            if message:
                return self._status(message)
        remaining = (snapshot.get("timers") or {}).get("remaining_time_over_s")
        try:
            seconds = float(remaining)
        except (TypeError, ValueError, OverflowError):
            seconds = float("nan")
        if not math.isfinite(seconds):
            return self._status("place_orb")
        # Keep the existing name/countdown protocol and layout, including before
        # the first placement (empty name, live countdown).
        return {"type": "display", "text": self._text,
                "seconds": min(2147483647, max(0, math.ceil(seconds)))}

    def update(self, snapshot: dict, playback: dict | None = None) -> None:
        """Send only current state, retry failures, and recover silent board resets."""
        with self._io_lock:
            if self._closed:
                return
            now = self._clock()
            with self._lock:
                payload = self._payload(snapshot, playback or {}, now)
            if self._screen != self._resolved_screen:
                self._resolved_screen = self._screen
                self._screen_revision += 1
            if not self.enabled:
                self._notify_screen()
                return
            # Existing firmware ignores this optional field. Updated firmware
            # remembers it for autonomous link-loss display.
            if self._configure_offline:
                payload["offline_text"] = self.texts["offline"]
            if not self._connect(now):
                return
            if payload == self._last_payload and now - self._last_sent_at < self.RESEND_S:
                self._notify_screen()
                return
            line = (json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                    + "\n").encode("ascii")
            try:
                written = self._device.write(line)
                if written != len(line):
                    raise OSError("incomplete matrix command")
            except Exception as exc:
                print(f"[dotmatrix] disconnected ({self.port}): {exc}")
                self._disconnect(str(exc))
                self._retry_at = self._clock() + self.RETRY_S
                return
            self._last_payload = payload
            self._last_sent_at = now
            self._set_connection_status("connected")
            self._notify_screen()

    def _notify_screen(self) -> None:
        if self._notified_revision != self._screen_revision:
            self._notified_revision = self._screen_revision
            if self._on_screen_change is not None:
                self._on_screen_change(self._screen)

    def close(self) -> None:
        with self._io_lock:
            self._closed = True
            self._disconnect()

