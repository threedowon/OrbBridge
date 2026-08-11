"""Dot Matrix의 최신 표시 상태를 보관하고 재연결 때 복원한다."""

from __future__ import annotations

from copy import deepcopy
import math
import threading
import time

from app.device_manager import DeviceManager


class MatrixDisplay:
    """화면 내용을 결정하는 코드와 USB 전송을 분리하는 얇은 표시 관리자다."""

    def __init__(self, devices: DeviceManager, *, brightness: int = 64) -> None:
        self._devices = devices
        self._lock = threading.RLock()
        self._brightness = max(0, min(255, int(brightness)))
        self._current = {"cmd": "clear"}
        self._last_sent_command: dict | None = None
        self._last_payload: dict | None = None
        self._last_sent_at: float | None = None
        self._last_error = ""
        self._pending = True
        self._seq = 0

    def handle_device_connected(self, role: str, _status: dict) -> None:
        if role != "dotmatrix":
            return
        self.resend()

    def show(self, view: str, *, transition: str = "none", **content) -> dict:
        """펌웨어가 해석할 view와 내용만 저장·전송한다."""
        view_name = str(view or "").strip()
        if not view_name:
            raise ValueError("dotmatrix view must not be empty")
        command = {
            "cmd": "show",
            "view": view_name,
            "transition": str(transition or "none"),
            **content,
        }
        return self._set_current(command)

    def clear(self) -> dict:
        return self._set_current({"cmd": "clear"})

    def set_brightness(self, value: int) -> dict:
        brightness = max(0, min(255, int(value)))
        with self._lock:
            self._brightness = brightness
        sent = self._devices.send(
            "dotmatrix", {"cmd": "brightness", "value": brightness}
        )
        with self._lock:
            if not sent:
                self._pending = True
        return {"ok": True, "sent": sent, "brightness": brightness}

    def sync_game(self, game: dict, *, stage: int, melody: str) -> dict:
        """현재 게임 상태를 펌웨어 독립적인 game_status view로 변환한다."""
        state = str(game.get("state") or "READY")
        if state == "READY":
            return self.clear()

        timers = game.get("timers") or {}
        remaining = timers.get("remaining_time_over_s")
        seconds = None
        if remaining is not None:
            seconds = max(0, int(math.ceil(float(remaining))))
        with self._lock:
            previous_state = self._current.get("state")
        transition = "fade" if previous_state != state else "none"
        return self.show(
            "game_status",
            transition=transition,
            state=state,
            stage=int(stage),
            melody=str(melody),
            seconds=seconds,
        )

    def resend(self) -> dict:
        """재연결된 Dot Matrix에 밝기와 최신 화면을 한 번 다시 보낸다."""
        with self._lock:
            brightness = self._brightness
            self._last_sent_command = None
        brightness_sent = self._devices.send(
            "dotmatrix", {"cmd": "brightness", "value": brightness}
        )
        current = self._send_current(force=True)
        current["brightness_sent"] = brightness_sent
        return current

    def status(self) -> dict:
        with self._lock:
            return {
                "brightness": self._brightness,
                "current": deepcopy(self._current),
                "pending": self._pending,
                "seq": self._seq,
                "last_payload": deepcopy(self._last_payload),
                "last_sent_at": self._last_sent_at,
                "last_error": self._last_error,
            }

    def _set_current(self, command: dict) -> dict:
        with self._lock:
            unchanged = command == self._current
            self._current = deepcopy(command)
            if unchanged and self._last_sent_command == command and not self._pending:
                return {"ok": True, "sent": False, "unchanged": True}
        return self._send_current()

    def _send_current(self, *, force: bool = False) -> dict:
        with self._lock:
            command = deepcopy(self._current)
            if not force and command == self._last_sent_command and not self._pending:
                return {"ok": True, "sent": False, "unchanged": True}
            self._seq += 1
            payload = {**command, "seq": self._seq}

        sent = self._devices.send("dotmatrix", payload)
        with self._lock:
            self._last_payload = payload
            self._pending = not sent
            if sent:
                self._last_sent_command = command
                self._last_sent_at = time.time() * 1000
                self._last_error = ""
            else:
                self._last_error = "dotmatrix 연결 대기"
        return {"ok": True, "sent": sent, "payload": payload}
