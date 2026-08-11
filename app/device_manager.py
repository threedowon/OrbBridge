"""Master와 Dot Matrix USB 장치를 handshake로 자동 식별해 관리한다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import json
import secrets
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # 하드웨어 없이 Table 모드와 웹 UI를 실행할 수 있다.
    serial = None
    list_ports = None


PROTOCOL = "orbbridge"
PROTOCOL_VERSION = 1
DEVICE_ROLES = ("master", "dotmatrix")

MasterMessageCallback = Callable[[dict], None]
DeviceConnectedCallback = Callable[[str, dict], None]


def _empty_device_status(role: str) -> dict:
    return {
        "role": role,
        "connected": False,
        "port": "",
        "device_id": "",
        "firmware": "",
        "protocol_version": None,
        "connected_at": None,
        "last_seen_at": None,
        "last_rx_at": None,
        "last_tx_at": None,
        "disconnected_at": None,
        "error": "",
    }


class DeviceManager:
    """두 USB 장치의 검색·식별·송수신·재연결을 한 스레드에서 처리한다."""

    def __init__(
        self,
        hardware_config: Mapping | None,
        on_master_message: MasterMessageCallback,
        *,
        serial_module=None,
        port_provider=None,
    ) -> None:
        cfg = dict(hardware_config or {})
        self.baudrate = int(cfg.get("baudrate", 115200))
        self.scan_interval_s = max(0.2, float(cfg.get("scan_interval_s", 3.0)))
        self.handshake_timeout_s = max(
            0.2, float(cfg.get("handshake_timeout_s", 2.5))
        )
        self.ping_interval_s = max(0.2, float(cfg.get("ping_interval_s", 2.0)))
        self.device_timeout_s = max(
            self.ping_interval_s * 2,
            float(cfg.get("device_timeout_s", 8.0)),
        )

        self._serial = serial if serial_module is None else serial_module
        if port_provider is not None:
            self._port_provider = port_provider
        elif list_ports is not None:
            self._port_provider = list_ports.comports
        else:
            self._port_provider = None

        self._on_master_message = on_master_message
        self._on_connected: DeviceConnectedCallback | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._scan_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._connections: dict[str, object] = {}
        self._last_seen_monotonic: dict[str, float] = {}
        self._rejected_ports: dict[str, float] = {}
        self._status = {
            "running": False,
            "scanning": False,
            "baudrate": self.baudrate,
            "last_scan_at": None,
            "last_error": "",
            "devices": {
                role: _empty_device_status(role) for role in DEVICE_ROLES
            },
        }

    def set_connected_callback(
        self, callback: DeviceConnectedCallback | None
    ) -> None:
        self._on_connected = callback

    def start(self) -> threading.Thread | None:
        """자동 검색 스레드를 한 번만 시작한다."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            if self._serial is None or self._port_provider is None:
                self._status["last_error"] = "pyserial이 설치되지 않음"
                return None
            self._stop_event.clear()
            self._scan_event.set()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="orbbridge-devices",
            )
            self._thread.start()
            return self._thread

    def stop(self) -> None:
        """검색 스레드와 열려 있는 두 COM을 종료한다."""
        self._stop_event.set()
        self._scan_event.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        for role in DEVICE_ROLES:
            self._disconnect(role, "중지됨")
        with self._lock:
            self._status["running"] = False
            self._status["scanning"] = False

    def request_rescan(self) -> dict:
        """연결된 장치는 유지하고 누락된 역할을 즉시 다시 검색한다."""
        with self._lock:
            self._status["last_error"] = ""
            self._rejected_ports.clear()
        self._scan_event.set()
        result = self.status()
        result["scan_requested"] = True
        return result

    def status(self) -> dict:
        """Admin API에서 바로 JSON으로 보낼 수 있는 현재 장치 상태다."""
        with self._lock:
            return deepcopy(self._status)

    def send(self, role: str, message: Mapping) -> bool:
        """식별된 장치 한 대에 한 줄 JSON을 전송한다."""
        if role not in DEVICE_ROLES:
            raise ValueError(f"unknown device role: {role}")
        payload = (
            json.dumps(
                dict(message), ensure_ascii=False, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        with self._lock:
            device = self._connections.get(role)
        if device is None:
            return False

        try:
            with self._write_lock:
                device.write(payload)
        except self._serial_exceptions() as exc:
            self._disconnect(role, str(exc))
            return False

        now_ms = time.time() * 1000
        with self._lock:
            status = self._status["devices"][role]
            status["last_tx_at"] = now_ms
            status["error"] = ""
        return True

    def _serial_exceptions(self) -> tuple[type[BaseException], ...]:
        serial_error = getattr(self._serial, "SerialException", OSError)
        if isinstance(serial_error, type) and issubclass(serial_error, BaseException):
            return (serial_error, OSError)
        return (OSError,)

    def _run(self) -> None:
        with self._lock:
            self._status["running"] = True
        next_scan = 0.0
        next_ping = 0.0
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                with self._lock:
                    missing = len(self._connections) < len(DEVICE_ROLES)
                if self._scan_event.is_set() or (missing and now >= next_scan):
                    self._scan_event.clear()
                    self._discover()
                    next_scan = time.monotonic() + self.scan_interval_s

                self._read_connected()
                now = time.monotonic()
                if now >= next_ping:
                    self._ping_connected()
                    next_ping = now + self.ping_interval_s
                self._drop_stale_devices(now)
                self._stop_event.wait(0.05)
        finally:
            with self._lock:
                self._status["running"] = False
                self._status["scanning"] = False

    def _discover(self) -> None:
        with self._lock:
            self._status["scanning"] = True
            self._status["last_scan_at"] = time.time() * 1000
            self._status["last_error"] = ""
            connected_ports = {
                status["port"]
                for status in self._status["devices"].values()
                if status["connected"] and status["port"]
            }

        try:
            port_entries = list(self._port_provider())
        except Exception as exc:
            with self._lock:
                self._status["last_error"] = str(exc)
                self._status["scanning"] = False
            return

        ports = sorted(
            {
                str(getattr(entry, "device", entry)).strip()
                for entry in port_entries
                if str(getattr(entry, "device", entry)).strip()
            }
        )
        now = time.monotonic()
        self._rejected_ports = {
            port: rejected_at
            for port, rejected_at in self._rejected_ports.items()
            if port in ports and now - rejected_at < 15.0
        }

        for port in ports:
            if port in connected_ports or port in self._rejected_ports:
                continue
            with self._lock:
                if len(self._connections) == len(DEVICE_ROLES):
                    break
            result = self._probe_port(port)
            if result is None:
                self._rejected_ports[port] = time.monotonic()
                continue
            role, device, hello = result
            self._register(role, port, device, hello)
            connected_ports.add(port)

        with self._lock:
            self._status["scanning"] = False

    def _probe_port(self, port: str):
        try:
            device = self._serial.Serial(
                port,
                self.baudrate,
                timeout=0.1,
                write_timeout=1,
            )
        except self._serial_exceptions():
            return None

        nonce = secrets.token_hex(4)
        hello = {
            "cmd": "hello",
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION,
            "nonce": nonce,
        }
        encoded = (
            json.dumps(hello, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        deadline = time.monotonic() + self.handshake_timeout_s
        next_send = 0.0

        try:
            while time.monotonic() < deadline and not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_send:
                    device.write(encoded)
                    next_send = now + 0.4
                raw = device.readline()
                message = self._decode_json(raw)
                if not message or message.get("type") != "hello":
                    continue
                if message.get("nonce") != nonce:
                    continue
                if message.get("protocol") != PROTOCOL:
                    continue
                try:
                    version = int(message.get("version"))
                except (TypeError, ValueError):
                    continue
                role = str(message.get("role") or "").strip().lower()
                if role not in DEVICE_ROLES or version != PROTOCOL_VERSION:
                    continue
                try:
                    device.timeout = 0
                except Exception:
                    pass
                return role, device, message
        except self._serial_exceptions():
            pass

        try:
            device.close()
        except Exception:
            pass
        return None

    def _register(self, role: str, port: str, device, hello: dict) -> None:
        callback = None
        status_copy = None
        with self._lock:
            if role in self._connections:
                current_port = self._status["devices"][role]["port"]
                self._status["last_error"] = (
                    f"duplicate {role}: {current_port}, {port}"
                )
            else:
                now_ms = time.time() * 1000
                self._connections[role] = device
                self._last_seen_monotonic[role] = time.monotonic()
                status = self._status["devices"][role]
                status.update(
                    {
                        "connected": True,
                        "port": port,
                        "device_id": str(hello.get("device_id") or ""),
                        "firmware": str(
                            hello.get("fw") or hello.get("firmware") or ""
                        ),
                        "protocol_version": PROTOCOL_VERSION,
                        "connected_at": now_ms,
                        "last_seen_at": now_ms,
                        "last_rx_at": now_ms,
                        "disconnected_at": None,
                        "error": "",
                    }
                )
                callback = self._on_connected
                status_copy = deepcopy(status)

        if role not in self._connections or self._connections.get(role) is not device:
            try:
                device.close()
            except Exception:
                pass
            return

        print(
            f"[device] {role} 연결 — {port} "
            f"id={status_copy['device_id'] or '-'} fw={status_copy['firmware'] or '-'}"
        )
        if callback is not None:
            try:
                callback(role, status_copy)
            except Exception as exc:
                print(f"[device] 연결 callback 실패 ({role}): {exc}")

    def _read_connected(self) -> None:
        with self._lock:
            connections = list(self._connections.items())
        for role, device in connections:
            try:
                if hasattr(device, "is_open") and not device.is_open:
                    self._disconnect(role, "포트가 닫힘")
                    continue
                for _ in range(32):
                    raw = device.readline()
                    if not raw:
                        break
                    self._handle_line(role, raw)
            except self._serial_exceptions() as exc:
                self._disconnect(role, str(exc))

    def _handle_line(self, role: str, raw) -> None:
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text:
            return
        now_ms = time.time() * 1000
        with self._lock:
            self._last_seen_monotonic[role] = time.monotonic()
            status = self._status["devices"][role]
            status["last_seen_at"] = now_ms
            status["last_rx_at"] = now_ms
            status["error"] = ""

        if text.startswith("#"):
            if role == "master":
                print(f"[master] {text}")
            return
        message = self._decode_json(text)
        if not message:
            return
        message_type = str(message.get("type") or "").lower()
        if message_type in ("hello", "pong"):
            return
        if role == "dotmatrix":
            if message_type == "error":
                with self._lock:
                    self._status["devices"][role]["error"] = str(
                        message.get("error") or "dotmatrix error"
                    )
            return
        try:
            self._on_master_message(message)
        except (ValueError, RuntimeError) as exc:
            print(f"[WARN] Master 이벤트 처리 실패: {exc}")

    @staticmethod
    def _decode_json(raw) -> dict | None:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="ignore").strip()
        else:
            text = str(raw or "").strip()
        if not text.startswith("{"):
            return None
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            return None
        return message if isinstance(message, dict) else None

    def _ping_connected(self) -> None:
        with self._lock:
            roles = list(self._connections)
        for role in roles:
            self.send(
                role,
                {
                    "cmd": "ping",
                    "protocol": PROTOCOL,
                    "version": PROTOCOL_VERSION,
                },
            )

    def _drop_stale_devices(self, now: float) -> None:
        with self._lock:
            stale = [
                role
                for role, last_seen in self._last_seen_monotonic.items()
                if now - last_seen > self.device_timeout_s
            ]
        for role in stale:
            self._disconnect(role, "ping 응답 없음")

    def _disconnect(self, role: str, error: str) -> None:
        with self._lock:
            device = self._connections.pop(role, None)
            self._last_seen_monotonic.pop(role, None)
            status = self._status["devices"][role]
            was_connected = bool(status["connected"])
            status["connected"] = False
            status["disconnected_at"] = time.time() * 1000
            status["error"] = error
        if device is not None:
            try:
                with self._write_lock:
                    device.close()
            except Exception:
                pass
        if was_connected:
            print(f"[device] {role} 연결 끊김 — {status['port']}: {error}")
            self._scan_event.set()
