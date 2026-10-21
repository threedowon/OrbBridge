"""Translate the master's any/both sensor report into the configured tray state."""

from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class TrayReading:
    present: bool | None
    seated: bool | None
    ttl_s: float | None = None
    baseline: bool = False


class TrayDebouncer:
    """Confirm physical changes without blocking serial input or extending its lease.

    Call feed for each master report and poll from the server loop. The caller
    serializes both operations. Boot/reconnect readings establish a baseline;
    they must not create a placement edge in the game engine.
    """

    REPORT_TTL_S = 3.0

    def __init__(self, config: dict, *, clock=time.monotonic):
        self.placement_s = self._delay(config, "tray_placement_delay_s", 0.5)
        self.removal_s = self._delay(config, "tray_removal_delay_s", 1.5)
        self._clock = clock
        self.reset()

    @staticmethod
    def _delay(config: dict, key: str, default: float) -> float:
        value = config.get(key, default)
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError(f"config.{key} must be a finite non-negative number")
        return float(value)

    def reset(self) -> None:
        self._stable = None
        self._raw = None
        self._changed_at = 0.0
        self._expires_at = None
        self._last_sent = None
        self._baseline = False

    def feed(self, present: bool, seated: bool) -> None:
        if type(present) is not bool or type(seated) is not bool or (seated and not present):
            raise ValueError("invalid tray state")
        now = self._clock()
        state = (present, seated)
        if self._expires_at is None or now >= self._expires_at:
            self.reset()
            self._stable = state
            self._baseline = True
        if state != self._raw:
            self._raw = state
            self._changed_at = now
        self._expires_at = now + self.REPORT_TTL_S

    def poll(self) -> TrayReading | None:
        if self._expires_at is None:
            return None
        now = self._clock()
        if now >= self._expires_at:
            self.reset()
            return TrayReading(None, None)
        delay = self.placement_s if self._raw[0] else self.removal_s
        if self._raw != self._stable and now - self._changed_at >= delay:
            self._stable = self._raw
        marker = (self._stable, self._expires_at)
        if marker == self._last_sent:
            return None
        self._last_sent = marker
        reading = TrayReading(*self._stable, self._expires_at - now, self._baseline)
        self._baseline = False
        return reading


def hall_sensor_count(config: dict) -> int:
    count = config.get("hall_sensor_count", 2)
    if type(count) is not int or count not in (1, 2):
        raise ValueError("config.hall_sensor_count must be 1 or 2")
    return count


def tray_state(data: dict, *, hardware: bool, config: dict) -> tuple[bool, bool]:
    if not isinstance(data, dict) or type(data.get("present")) is not bool:
        raise ValueError("tray.present must be a boolean")
    present = data["present"]
    seated = data.get("seated") if hardware else data.get("seated", present)
    if type(seated) is not bool or (seated and not present):
        raise ValueError("invalid tray.seated")
    # Master already reports present=A OR B and seated=A AND B.
    if hardware and hall_sensor_count(config) == 1:
        seated = present
    return present, seated
