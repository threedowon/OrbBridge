"""ESP 마스터의 USB 시리얼 JSON을 읽는 하드웨어 입력 모듈이다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import re
import threading

try:
    import serial
except ImportError:  # table 모드는 pyserial 없이도 실행할 수 있다.
    serial = None


StatusCallback = Callable[[bool, str, str], None]
MessageCallback = Callable[[dict], None]

# 마스터 펌웨어가 2초마다 보내는 하트비트: "#master alive relay=… bad=…"
_HEARTBEAT_RE = re.compile(r"^#master alive\b.*\bbad=(\d+)")


def _heartbeat_bad_count(line: str) -> int | None:
    """하트비트면 누적 CRC 실패 수를, 그 외 펌웨어 메시지면 None을 준다."""

    match = _HEARTBEAT_RE.match(line)
    return int(match.group(1)) if match else None


class SerialWriter:
    """읽기 루프가 연 포트로 PC → 마스터 한 줄 명령을 보낸다.

    포트는 읽기 루프가 소유하므로 같은 장치를 공유한다. 연결이 없으면
    조용히 실패만 알리고, 재연결 시 호출자가 최신 상태를 다시 보낸다.
    """

    def __init__(self) -> None:
        self._device = None
        self._lock = threading.Lock()

    def attach(self, device) -> None:
        with self._lock:
            self._device = device

    def detach(self) -> None:
        with self._lock:
            self._device = None

    def send_line(self, line: str) -> bool:
        """한 줄을 보내고 성공 여부를 돌려준다."""

        with self._lock:
            device = self._device
            if device is None:
                return False
            try:
                device.write((line + "\n").encode("ascii"))
                return True
            except Exception as exc:  # 연결 끊김 — 다음 연결에서 다시 보낸다.
                print(f"[serial] 명령 전송 실패 ({line}): {exc}")
                self._device = None
                return False


def run_serial_loop(
    serial_config: Mapping,
    on_message: MessageCallback,
    on_status: StatusCallback,
    stop_event: threading.Event,
    *,
    retry_delay_s: float = 5.0,
    writer: "SerialWriter | None" = None,
) -> None:
    """연결이 끊겨도 재시도하며 한 줄 JSON을 런타임으로 전달한다."""

    port = str(serial_config.get("port", "COM13"))
    baudrate = int(serial_config.get("baudrate", 115200))
    serial_module = serial
    if serial_module is None:
        on_status(False, port, "pyserial이 설치되지 않음")
        return

    while not stop_event.is_set():
        on_status(False, port, "연결 중…")
        try:
            device = serial_module.Serial(
                port, baudrate, timeout=1, write_timeout=1
            )
        except serial_module.SerialException as exc:
            error = str(exc)
            print(f"[serial] 포트 연결 실패 ({port}): {exc}")
            on_status(False, port, error)
            if stop_event.wait(retry_delay_s):
                break
            continue

        print(f"[serial] 연결됨: {port}")
        if writer is not None:
            writer.attach(device)
        on_status(True, port, "")
        # 연결 전에 쌓인 실패까지 새 실패로 보고하지 않도록,
        # 첫 하트비트 값을 기준선으로 삼는다(ESP 재부팅 시에도 자연히 리셋).
        last_bad_count: int | None = None
        try:
            while not stop_event.is_set():
                line = device.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("#"):
                    bad_count = _heartbeat_bad_count(line)
                    if bad_count is None:
                        print(f"[master] {line}")
                    elif last_bad_count is not None and bad_count > last_bad_count:
                        # 2초마다 오는 하트비트 자체는 노이즈 → 숨기고,
                        # RS485 신호 품질이 나빠진 순간만 알린다.
                        print(
                            f"[master] RS485 CRC 실패 +{bad_count - last_bad_count}"
                            f" (누적 {bad_count})"
                        )
                    if bad_count is not None:
                        last_bad_count = bad_count
                    continue
                if not line.startswith("{"):
                    continue
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("시리얼 메시지는 JSON 객체여야 한다")
                    on_message(message)
                except json.JSONDecodeError:
                    print(f"[WARN] JSON 파싱 실패: {line[:120]}")
                except (ValueError, RuntimeError) as exc:
                    print(f"[WARN] 이벤트 처리 실패: {exc}")
        except serial_module.SerialException as exc:
            error = str(exc)
            print(f"[serial] 연결 끊김 ({port}): {exc}")
            on_status(False, port, error)
        finally:
            if writer is not None:
                writer.detach()
            device.close()

        if stop_event.wait(retry_delay_s):
            break

    on_status(False, port, "중지됨")
