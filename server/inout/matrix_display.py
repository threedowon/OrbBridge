"""게임 정보를 192×32 도트매트릭스로 보내는 출력 모듈이다."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping

from server.voice_catalog import load_voice_catalog

try:
    import serial
except ImportError:  # 도트매트릭스를 사용하지 않으면 pyserial 없이도 실행할 수 있다.
    serial = None


class MatrixDisplay:
    """가장 최근에 놓인 오브와 게임 제한 시간을 초 단위로 전송한다."""

    def __init__(self, config: Mapping, *, serial_module=None, catalog=None) -> None:
        self.enabled = bool(config.get("enabled", False))
        self.port = str(config.get("port", "COM6"))
        self.baudrate = int(config.get("baudrate", 115200))
        self._serial = serial if serial_module is None else serial_module
        self._voices = (catalog or load_voice_catalog()).get("voices", {})
        self._device = None
        self._closed = False
        self._last_heartbeat = None
        self._text = ""
        self._last_seconds: int | None = None
        self._lock = threading.Lock()
        self._connect()

    def _connect(self) -> bool:
        if self._closed or not self.enabled or self._device is not None:
            return self._device is not None
        if self._serial is None:
            print("[dotmatrix] pyserial이 설치되지 않음")
            return False
        try:
            self._device = self._serial.Serial(
                self.port,
                self.baudrate,
                timeout=1,
                write_timeout=1,
            )
        except Exception as exc:
            print(f"[dotmatrix] 포트 연결 실패 ({self.port}): {exc}")
            return False
        print(f"[dotmatrix] 연결됨: {self.port}")
        return True

    def _write(self, payload: dict) -> bool:
        if not self._connect():
            return False
        line = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        try:
            self._device.write(line.encode("ascii"))
            return True
        except Exception as exc:
            print(f"[dotmatrix] 연결 끊김 ({self.port}): {exc}")
            self._device.close()
            self._device = None
            return False

    def _voice_text(self, voice: str) -> str:
        group, _, index = voice.rpartition("_")
        profile = self._voices.get(voice) or {}
        labels = profile.get("labels") or {}
        member = str(labels.get("en") or index or voice)
        return f"{group or voice}-{member}".upper()

    def show_voice(self, voice: str, snapshot: dict) -> None:
        """최근 오브 이름을 저장하고 현재 시간을 즉시 보낸다."""
        with self._lock:
            self._text = self._voice_text(voice)
            self._last_seconds = None
        self.update(snapshot)

    def update(self, snapshot: dict) -> None:
        """표시 초가 바뀌었을 때만 화면을 갱신한다."""
        with self._lock:
            if not self.enabled or self._closed:
                return
            now = time.monotonic()
            if self._last_heartbeat is None or now - self._last_heartbeat >= 1.0:
                if self._write({"type": "heartbeat"}):
                    self._last_heartbeat = now
            if snapshot.get("state") != "RUNNING":
                if self._text and self._write({"type": "clear"}):
                    self._text = ""
                    self._last_seconds = None
                return

            remaining = (snapshot.get("timers") or {}).get(
                "remaining_time_over_s"
            )
            if not self._text or remaining is None:
                return
            seconds = max(0, math.ceil(float(remaining)))
            if seconds == self._last_seconds:
                return
            if self._write(
                {"type": "display", "text": self._text, "seconds": seconds}
            ):
                self._last_seconds = seconds

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._device is not None:
                try:
                    # clear supports older firmware; shutdown also blanks diagnostic views.
                    self._device.write(b'{"type":"clear"}\n{"type":"shutdown"}\n')
                except Exception as exc:
                    print(f"[dotmatrix] 종료 소등 전송 실패: {exc}")
                finally:
                    self._device.close()
                    self._device = None
