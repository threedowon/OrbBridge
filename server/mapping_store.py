"""Load / save orb_mapping.json and voice helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
MAPPING_PATH = BACKEND_DIR / "orb_mapping.json"
VOICE_RE = re.compile(r"^([a-z0-9]+)_(\d+)$", re.I)


def normalize_id(raw: str) -> str:
    """Normalize DS2431 / orb id to `AA:BB:CC:...` uppercase hex."""
    if not raw or not str(raw).strip():
        raise ValueError("empty id")

    if ":" in raw:
        parts = [p.strip() for p in raw.split(":") if p.strip()]
        hex_only = "".join(parts).upper()
        if not re.fullmatch(r"[0-9A-F]+", hex_only):
            raise ValueError(f"invalid id: {raw}")
        if len(hex_only) % 2:
            hex_only = "0" + hex_only
        return ":".join(hex_only[i : i + 2] for i in range(0, len(hex_only), 2))

    hex_only = re.sub(r"[^0-9A-Fa-f]", "", raw).upper()
    if not hex_only:
        raise ValueError(f"invalid id: {raw}")
    if len(hex_only) % 2:
        hex_only = "0" + hex_only
    return ":".join(hex_only[i : i + 2] for i in range(0, len(hex_only), 2))


def load_mapping(path: Path | None = None) -> dict:
    p = path or MAPPING_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_mapping(mapping: dict, path: Path | None = None) -> None:
    p = path or MAPPING_PATH
    ordered = dict(sorted(mapping.items(), key=lambda kv: kv[0]))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
        f.write("\n")


def voice_group(voice: str) -> str:
    m = VOICE_RE.match(voice or "")
    return m.group(1).lower() if m else voice


def list_groups(mapping: dict) -> list[str]:
    groups = {voice_group(v["voice"]) for v in mapping.values() if v.get("voice")}
    return sorted(groups)


def suggest_voice(mapping: dict, group: str) -> str:
    group = group.lower().strip()
    nums = []
    for info in mapping.values():
        voice = info.get("voice", "")
        m = VOICE_RE.match(voice)
        if m and m.group(1).lower() == group:
            nums.append(int(m.group(2)))
    n = max(nums, default=0) + 1
    return f"{group}_{n}"


def validate_voice(voice: str) -> str:
    voice = voice.strip()
    if not VOICE_RE.fullmatch(voice):
        raise ValueError("voice must look like aspa_1, ive_2, cortis_1")
    return voice
