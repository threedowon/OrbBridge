"""Translate the master's any/both sensor report into the configured tray state."""


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
