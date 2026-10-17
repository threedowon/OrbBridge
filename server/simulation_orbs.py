"""Software controls can use virtual orbs without registering physical IDs."""

from server.data_store import load_mapping
from server.voice_catalog import load_voice_catalog


TRAY_SIZE = 12


def virtual_orb_mapping() -> dict:
    voices = sorted(load_voice_catalog()["voices"])
    if not voices:
        return {}
    return {
        f"__simulation__:{voices[index % len(voices)]}:{index // len(voices)}": {
            "voice": voices[index % len(voices)],
            "simulated": True,
        }
        for index in range(max(TRAY_SIZE, len(voices)))
    }


def simulation_orb_mapping(registered: dict | None = None) -> dict:
    """Prefer registered orbs; supplement a short tray from the voice catalog.

    Virtual IDs only exist in memory and are accepted by the simulation API.
    Physical registration and unknown-ID handling are unchanged.
    """
    registered = load_mapping() if registered is None else registered
    mapping = {
        orb_id: dict(entry, voice=str(entry["voice"]).strip())
        for orb_id, entry in sorted(registered.items())
        if isinstance(entry, dict) and str(entry.get("voice") or "").strip()
    }
    if len(mapping) < TRAY_SIZE:
        for orb_id, entry in virtual_orb_mapping().items():
            mapping.setdefault(orb_id, entry)
    return mapping
