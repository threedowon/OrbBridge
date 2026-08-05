"""오브 매핑과 함께 쓰는 voice 표시·MIDI 이름 메타데이터를 관리한다."""

from __future__ import annotations

import json

from app.app_paths import data_dir
from app.data_store import MAPPING_PATH, VOICE_RE, load_mapping, voice_group


VOICE_CATALOG_PATH = data_dir() / "voice_catalog.json"

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
    "aespa_1": {"color": "#f472b6", "wave": "sine", "filter": 900, "gain": 0.20},
    "aespa_2": {"color": "#5eead4", "wave": "triangle", "filter": 2200, "gain": 0.18},
    "aespa_3": {"color": "#fbbf24", "wave": "sawtooth", "filter": 1100, "gain": 0.16},
    "aespa_4": {"color": "#a78bfa", "wave": "sine", "filter": 2000, "gain": 0.19},
    "ive_1": {"color": "#60a5fa", "wave": "square", "filter": 1400, "gain": 0.14},
    "ive_2": {"color": "#4ade80", "wave": "triangle", "filter": 1600, "gain": 0.17},
    "ive_3": {"color": "#fb923c", "wave": "sine", "filter": 800, "gain": 0.21},
    "ive_4": {"color": "#f87171", "wave": "sawtooth", "filter": 1800, "gain": 0.15},
    "cortis_1": {"color": "#c084fc", "wave": "triangle", "filter": 2400, "gain": 0.18},
}


def _read_labels(profile: dict) -> dict[str, str]:
    """현재 labels와 이전 label_kr·label_en 형식을 함께 읽는다."""
    labels = profile.get("labels")
    if isinstance(labels, dict):
        korean = str(labels.get("kr", "") or "").strip()
        english = str(labels.get("en", "") or "").strip()
    else:
        korean = str(profile.get("label_kr", "") or "").strip()
        english = str(profile.get("label_en", "") or "").strip()
    return {"kr": korean, "en": english}


def normalize_profile(profile: dict) -> dict:
    """표시·합성음·MIDI 이름 확인에 필요한 필드만 정규화한다."""
    wave = str(profile.get("wave", "sine"))
    if wave not in WAVES:
        wave = "sine"
    return {
        "color": str(profile.get("color", COLORS[0])),
        "wave": wave,
        "filter": int(profile.get("filter", 1400)),
        "gain": float(profile.get("gain", 0.18)),
        "labels": _read_labels(profile),
    }


def compact_profile(profile: dict) -> dict:
    """빈 이름을 제외한 JSON 저장 형식으로 변환한다."""
    normalized = normalize_profile(profile)
    result = {
        "color": normalized["color"],
        "wave": normalized["wave"],
        "filter": normalized["filter"],
        "gain": normalized["gain"],
    }
    labels = {key: value for key, value in normalized["labels"].items() if value}
    if labels:
        result["labels"] = labels
    return result


def load_voice_catalog() -> dict:
    """voice 카탈로그를 읽고 형식을 검증한다."""
    if not VOICE_CATALOG_PATH.is_file():
        return {"voices": {}}
    with VOICE_CATALOG_PATH.open(encoding="utf-8") as source:
        document = json.load(source)
    voices = document.get("voices") if isinstance(document, dict) else None
    if not isinstance(voices, dict):
        raise ValueError("voice_catalog.voices must be an object")
    return {"voices": voices}


def save_voice_catalog(catalog: dict) -> None:
    """voice 카탈로그를 안정적인 키 순서로 저장한다."""
    raw = catalog.get("voices", {})
    voices = {
        name: compact_profile(profile)
        for name, profile in sorted(raw.items())
        if isinstance(profile, dict)
    }
    with VOICE_CATALOG_PATH.open("w", encoding="utf-8") as destination:
        json.dump({"voices": voices}, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


def generate_profile(voice: str) -> dict:
    """새 voice에 재현 가능한 표시색과 합성음 설정을 만든다."""
    base = SEED_PROFILES.get(voice)
    if base:
        return normalize_profile(base)

    match = VOICE_RE.match(voice or "")
    group = voice_group(voice)
    member_number = int(match.group(2)) if match else 1
    groups = sorted({voice_group(name) for name in SEED_PROFILES} | {group})
    group_index = groups.index(group) if group in groups else 0
    return normalize_profile(
        {
            "color": COLORS[group_index % len(COLORS)],
            "wave": WAVES[(member_number - 1) % len(WAVES)],
            "filter": FILTERS[(group_index + member_number - 1) % len(FILTERS)],
            "gain": GAINS[(group_index + member_number - 1) % len(GAINS)],
        }
    )


def ensure_voice_profile(voice: str) -> dict:
    """카탈로그에 voice 프로필이 없으면 생성한다."""
    catalog = load_voice_catalog()
    voices = catalog.setdefault("voices", {})
    if voice not in voices:
        voices[voice] = generate_profile(voice)
        save_voice_catalog(catalog)
    return normalize_profile(voices[voice])


def unique_voices_from_mapping(mapping: dict) -> list[str]:
    """오브 매핑에 실제 사용된 voice만 정렬해 반환한다."""
    names = {str(info.get("voice", "")).strip() for info in mapping.values()}
    return sorted(name for name in names if name)


def list_catalog_voices(mapping: dict | None = None) -> list[str]:
    """현재 오브 매핑이 사용하는 voice 목록을 반환한다."""
    source = mapping if mapping is not None else load_mapping()
    return unique_voices_from_mapping(source)


def sync_mapping_voices_to_catalog(mapping: dict | None = None) -> None:
    """매핑된 모든 voice의 표시 프로필을 카탈로그에 보장한다."""
    mapping = mapping if mapping is not None else load_mapping()
    catalog = load_voice_catalog()
    profiles = catalog.setdefault("voices", {})
    changed = False
    for name in unique_voices_from_mapping(mapping):
        if name not in profiles:
            profiles[name] = generate_profile(name)
            changed = True
    if changed:
        save_voice_catalog(catalog)


def voice_api_payload(mapping: dict | None = None) -> dict:
    """screen의 표시와 합성음 fallback에 필요한 voice 목록을 만든다."""
    mapping = mapping if mapping is not None else load_mapping()
    catalog = load_voice_catalog()
    profiles = catalog.setdefault("voices", {})
    voices: list[dict] = []
    index: dict[str, int] = {}

    for voice_id, name in enumerate(unique_voices_from_mapping(mapping)):
        profile = normalize_profile(profiles.get(name) or generate_profile(name))
        entry = {
            "id": voice_id,
            "name": name,
            "color": profile["color"],
            "wave": profile["wave"],
            "filter": profile["filter"],
            "gain": profile["gain"],
        }
        labels = {key: value for key, value in profile["labels"].items() if value}
        if labels:
            entry["labels"] = labels
        voices.append(entry)
        index[name] = voice_id

    return {"voices": voices, "index": index}


def ensure_voice_catalog_file() -> None:
    """voice 카탈로그를 만들고 현재 간결 형식으로 정리한다."""
    if VOICE_CATALOG_PATH.is_file():
        catalog = load_voice_catalog()
    else:
        catalog = {"voices": {}}

    profiles = catalog.setdefault("voices", {})
    mapping = load_mapping() if MAPPING_PATH.is_file() else {}
    for name in unique_voices_from_mapping(mapping):
        profiles.setdefault(name, generate_profile(name))

    normalized = {
        "voices": {
            name: compact_profile(profile)
            for name, profile in sorted(profiles.items())
            if isinstance(profile, dict)
        }
    }
    if normalized != catalog or not VOICE_CATALOG_PATH.is_file():
        save_voice_catalog(normalized)
