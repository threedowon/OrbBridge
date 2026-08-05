"""Voice Register의 저장 로직과 사용 통계를 제공한다."""

from __future__ import annotations

import time
from typing import Callable

from mapping_store import load_mapping
from voice_store import (
    NOTE_KEYS,
    ensure_voice_profile,
    list_catalog_voices,
    load_voice_catalog,
    normalize_profile,
    orb_count_for_voice,
    save_voice_notes,
)

LOG_LIMIT = 80


class VoiceRegisterState:
    def __init__(self) -> None:
        self.logs: list[dict] = []
        self._log_listeners: list[Callable[[dict], None]] = []

    def add_log_listener(self, cb: Callable[[dict], None]) -> None:
        self._log_listeners.append(cb)

    def push_log(self, level: str, message: str) -> None:
        entry = {"level": level, "message": message, "at": time.time()}
        self.logs = ([entry] + self.logs)[:LOG_LIMIT]
        for cb in self._log_listeners:
            try:
                cb(entry)
            except Exception:
                pass

    def snapshot(self) -> dict:
        mapping = load_mapping()
        catalog = load_voice_catalog()
        voices = []
        for voice in list_catalog_voices(mapping):
            profile = normalize_profile(catalog.get("voices", {}).get(voice) or {})
            notes = profile.get("notes", {})
            filled = sum(1 for k in NOTE_KEYS if notes.get(k))
            voices.append(
                {
                    "voice": voice,
                    "orb_count": orb_count_for_voice(mapping, voice),
                    "samples": filled,
                    "notes": notes,
                    "color": profile.get("color", ""),
                }
            )
        return {
            "voices": voices,
            "mapping_count": len(mapping),
            "logs": list(self.logs),
        }


def save_voice(
    voice: str,
    notes: dict[str, str],
    state: VoiceRegisterState | None = None,
    *,
    label_kr: str | None = None,
    label_en: str | None = None,
    preserve_notes: bool = False,
) -> None:
    ensure_voice_profile(voice)
    save_voice_notes(
        voice,
        notes,
        label_kr=label_kr,
        label_en=label_en,
        preserve_notes=preserve_notes,
    )
    filled = sum(1 for k in NOTE_KEYS if notes.get(k))
    kr = (label_kr or "").strip()
    label_hint = f" · {kr}" if kr else ""
    msg = f"저장 · {voice} ({filled}/8){label_hint}"
    if state:
        state.push_log("ok", msg)
    print(f"[voice-register] {msg}")
