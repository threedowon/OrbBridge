"""ESP 마스터의 USB 시리얼 JSON을 읽는 하드웨어 입력 모듈이다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import threading

try:
    import serial
except ImportError:  # table 모드는 pyserial 없이도 실행할 수 있다.
    serial = None


StatusCallback = Callable[[bool, str, str], None]
MessageCallback = Callable[[dict], None]


def run_serial_loop(
    serial_config: Mapping,
    on_message: MessageCallback,
    on_status: StatusCallback,
    stop_event: threading.Event,
    *,
    retry_delay_s: float = 5.0,
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
            device = serial_module.Serial(port, baudrate, timeout=1)
        except serial_module.SerialException as exc:
            error = str(exc)
            print(f"[serial] 포트 연결 실패 ({port}): {exc}")
            on_status(False, port, error)
            if stop_event.wait(retry_delay_s):
                break
            continue

        print(f"[serial] 연결됨: {port}")
        on_status(True, port, "")
        try:
            while not stop_event.is_set():
                line = device.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                # 펌웨어 진단 주석(#master alive ...)은 콘솔에 출력하지 않는다.
                if line.startswith("#"):
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
            device.close()

        if stop_event.wait(retry_delay_s):
            break

    on_status(False, port, "중지됨")
