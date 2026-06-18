"""Voice timbre + sample catalog — synced with orb_mapping.json."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from mapping_store import BACKEND_DIR, MAPPING_PATH, VOICE_RE, load_mapping, voice_group

VOICE_CATALOG_PATH = MAPPING_PATH.parent / "voice_catalog.json"
SOUNDS_DIR = BACKEND_DIR / "sounds"

NOTE_SLOTS: tuple[tuple[str, str], ...] = (
    ("C4", "도"),
    ("D4", "레"),
    ("E4", "미"),
    ("F4", "파"),
    ("G4", "솔"),
    ("A4", "라"),
    ("B4", "시"),
    ("C5", "도"),
)
NOTE_KEYS: tuple[str, ...] = tuple(k for k, _ in NOTE_SLOTS)
ROW_TO_NOTE_KEY = {i + 1: NOTE_KEYS[i] for i in range(len(NOTE_KEYS))}
LETTER_TO_NOTE_KEY = {
    "C": "C4",
    "D": "D4",
    "E": "E4",
    "F": "F4",
    "G": "G4",
    "A": "A4",
    "B": "B4",
}

WAVES = ("sine", "triangle", "sawtooth", "square")
COLORS = (
    "#f472b6",
    "#5eead4",
    "#fbbf24",
    "#a78bfa",
    "#60a5fa",
    "#4ade80",
    "#fb923c",
    "#f87171",
    "#c084fc",
)
FILTERS = (900, 2200, 1100, 2000, 1400, 1600, 800, 1800, 2400)
GAINS = (0.20, 0.18, 0.16, 0.19, 0.14, 0.17, 0.21, 0.15, 0.18)

SEED_PROFILES: dict[str, dict] = {
    "aspa_1": {"color": "#f472b6", "wave": "sine", "filter": 900, "gain": 0.20},
    "aspa_2": {"color": "#5eead4", "wave": "triangle", "filter": 2200, "gain": 0.18},
    "aspa_3": {"color": "#fbbf24", "wave": "sawtooth", "filter": 1100, "gain": 0.16},
    "aspa_4": {"color": "#a78bfa", "wave": "sine", "filter": 2000, "gain": 0.19},
    "ive_1": {"color": "#60a5fa", "wave": "square", "filter": 1400, "gain": 0.14},
    "ive_2": {"color": "#4ade80", "wave": "triangle", "filter": 1600, "gain": 0.17},
    "ive_3": {"color": "#fb923c", "wave": "sine", "filter": 800, "gain": 0.21},
    "ive_4": {"color": "#f87171", "wave": "sawtooth", "filter": 1800, "gain": 0.15},
    "cortis_1": {"color": "#c084fc", "wave": "triangle", "filter": 2400, "gain": 0.18},
}


def empty_notes() -> dict[str, str]:
    return {k: "" for k in NOTE_KEYS}


def load_voice_catalog() -> dict:
    if not VOICE_CATALOG_PATH.exists():
        return {"voices": _seed_catalog()}
    with open(VOICE_CATALOG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    voices = data.get("voices")
    if not isinstance(voices, dict):
        return {"voices": _seed_catalog()}
    return {"voices": voices}


def _seed_catalog() -> dict[str, dict]:
    out = {}
    for name, profile in SEED_PROFILES.items():
        out[name] = {**profile, "notes": empty_notes()}
    return out


def save_voice_catalog(catalog: dict) -> None:
    voices = dict(sorted(catalog.get("voices", {}).items()))
    with open(VOICE_CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump({"voices": voices}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_profile(profile: dict) -> dict:
    notes = profile.get("notes")
    if not isinstance(notes, dict):
        notes = empty_notes()
    else:
        notes = {k: str(notes.get(k, "") or "").strip() for k in NOTE_KEYS}
    out = {
        "color": profile.get("color", COLORS[0]),
        "wave": profile.get("wave", "sine"),
        "filter": profile.get("filter", 1400),
        "gain": profile.get("gain", 0.18),
        "notes": notes,
    }
    return out


def generate_profile(voice: str) -> dict:
    base = SEED_PROFILES.get(voice)
    if base:
        return normalize_profile({**base, "notes": empty_notes()})

    m = VOICE_RE.match(voice or "")
    group = voice_group(voice)
    num = int(m.group(2)) if m else 1

    groups = sorted({voice_group(v) for v in SEED_PROFILES} | {group})
    gi = groups.index(group) if group in groups else hash(group) % len(COLORS)
    wi = (num - 1) % len(WAVES)
    fi = (gi + num - 1) % len(FILTERS)
    gai = (gi + num - 1) % len(GAINS)

    return normalize_profile(
        {
            "color": COLORS[gi % len(COLORS)],
            "wave": WAVES[wi],
            "filter": FILTERS[fi],
            "gain": GAINS[gai],
            "notes": empty_notes(),
        }
    )


def ensure_voice_profile(voice: str) -> dict:
    catalog = load_voice_catalog()
    voices = catalog.setdefault("voices", {})
    if voice not in voices:
        voices[voice] = generate_profile(voice)
        save_voice_catalog(catalog)
    else:
        voices[voice] = normalize_profile(voices[voice])
    return voices[voice]


def unique_voices_from_mapping(mapping: dict) -> list[str]:
    names = {info.get("voice", "").strip() for info in mapping.values()}
    return sorted(v for v in names if v)


def list_catalog_voices(mapping: dict | None = None) -> list[str]:
    mapping = mapping or load_mapping()
    catalog = load_voice_catalog()
    names = set(unique_voices_from_mapping(mapping)) | set(catalog.get("voices", {}))
    return sorted(v for v in names if v)


def orb_count_for_voice(mapping: dict, voice: str) -> int:
    return sum(1 for info in mapping.values() if info.get("voice") == voice)


def relativize_path(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(BACKEND_DIR.resolve())
        return rel.as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve_media_path(stored: str) -> Path | None:
    if not stored or not str(stored).strip():
        return None
    p = Path(stored.strip())
    if not p.is_absolute():
        p = BACKEND_DIR / p
    return p if p.is_file() else None


def ingest_sample_file(voice: str, note_key: str, src: Path) -> str:
    """Copy sample into sounds/{voice}/ and return stored relative path."""
    src = src.resolve()
    if not src.is_file():
        raise FileError(f"not found: {src}")

    dest_dir = SOUNDS_DIR / voice
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{note_key}{src.suffix.lower()}"

    if src != dest.resolve():
        shutil.copy2(src, dest)
    return relativize_path(dest)


def save_voice_notes(voice: str, notes: dict[str, str], *, copy_into_sounds: bool = True) -> dict:
    """Persist note → file paths for a voice key (e.g. ive_1)."""
    voice = voice.strip()
    if not voice:
        raise ValueError("voice required")

    catalog = load_voice_catalog()
    profile = normalize_profile(catalog.get("voices", {}).get(voice) or generate_profile(voice))
    cleaned: dict[str, str] = empty_notes()

    for key in NOTE_KEYS:
        raw = str(notes.get(key, "") or "").strip()
        if not raw:
            continue
        src = resolve_media_path(raw)
        if not src:
            raise FileNotFoundError(f"{key}: {raw}")
        if copy_into_sounds:
            cleaned[key] = ingest_sample_file(voice, key, src)
        else:
            cleaned[key] = relativize_path(src)

    profile["notes"] = cleaned
    catalog.setdefault("voices", {})[voice] = profile
    save_voice_catalog(catalog)
    return profile


def resolve_note_key(note: str, row: int | None = None) -> str:
    if row is not None and row in ROW_TO_NOTE_KEY:
        return ROW_TO_NOTE_KEY[row]
    letter = str(note or "").strip().upper()
    if letter == "C" and row == 8:
        return "C5"
    return LETTER_TO_NOTE_KEY.get(letter, letter)


def sample_path_for(voice: str, note: str, row: int | None = None) -> Path | None:
    key = resolve_note_key(note, row)
    catalog = load_voice_catalog()
    profile = catalog.get("voices", {}).get(voice)
    if not profile:
        return None
    stored = normalize_profile(profile)["notes"].get(key, "")
    return resolve_media_path(stored)


def voice_api_payload(mapping: dict | None = None) -> dict:
    mapping = mapping or load_mapping()
    catalog = load_voice_catalog()
    profiles: dict[str, dict] = catalog.setdefault("voices", {})
    changed = False

    for name in unique_voices_from_mapping(mapping):
        if name not in profiles:
            profiles[name] = generate_profile(name)
            changed = True
        else:
            normalized = normalize_profile(profiles[name])
            if normalized != profiles[name]:
                profiles[name] = normalized
                changed = True

    if changed:
        save_voice_catalog(catalog)

    voices: list[dict] = []
    index: dict[str, int] = {}
    for i, name in enumerate(unique_voices_from_mapping(mapping)):
        p = normalize_profile(profiles[name])
        voices.append(
            {
                "id": i,
                "name": name,
                "color": p["color"],
                "wave": p["wave"],
                "filter": p["filter"],
                "gain": p.get("gain", 0.18),
                "notes": p["notes"],
            }
        )
        index[name] = i

    return {"voices": voices, "index": index, "note_keys": list(NOTE_KEYS)}


def ensure_voice_catalog_file() -> None:
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    if VOICE_CATALOG_PATH.exists():
        catalog = load_voice_catalog()
        changed = False
        for name, profile in list(catalog.get("voices", {}).items()):
            normalized = normalize_profile(profile)
            if normalized != profile:
                catalog["voices"][name] = normalized
                changed = True
        if changed:
            save_voice_catalog(catalog)
        return

    catalog: dict = {"voices": _seed_catalog()}
    mapping = load_mapping() if MAPPING_PATH.exists() else {}
    for name in unique_voices_from_mapping(mapping):
        if name not in catalog["voices"]:
            catalog["voices"][name] = generate_profile(name)
    save_voice_catalog(catalog)
