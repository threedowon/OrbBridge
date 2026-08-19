"""voice_catalog.json의 화면 표시 이름과 색상을 관리한다."""

from __future__ import annotations

import hashlib
import json

from server.app_paths import data_dir
from server.data_store import MAPPING_PATH, load_mapping


VOICE_CATALOG_PATH = data_dir() / "voice_catalog.json"
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


def _read_labels(profile: dict) -> dict[str, str]:
    """labels 객체만 읽으며 빈 값은 저장 단계에서 제외한다."""

    labels = profile.get("labels")
    if not isinstance(labels, dict):
        return {"kr": "", "en": ""}
    return {
        "kr": str(labels.get("kr", "") or "").strip(),
        "en": str(labels.get("en", "") or "").strip(),
    }


def normalize_profile(profile: dict) -> dict:
    """화면 표시에서 사용하는 color와 labels만 정규화한다."""

    return {
        "color": str(profile.get("color") or COLORS[0]),
        "labels": _read_labels(profile),
    }


def compact_profile(profile: dict) -> dict:
    """빈 라벨과 폐기된 합성음 필드를 제외한 저장 형식을 만든다."""

    normalized = normalize_profile(profile)
    result = {"color": normalized["color"]}
    labels = {key: value for key, value in normalized["labels"].items() if value}
    if labels:
        result["labels"] = labels
    return result


def load_voice_catalog() -> dict:
    if not VOICE_CATALOG_PATH.is_file():
        return {"voices": {}}
    with VOICE_CATALOG_PATH.open(encoding="utf-8") as source:
        document = json.load(source)
    voices = document.get("voices") if isinstance(document, dict) else None
    if not isinstance(voices, dict):
        raise ValueError("voice_catalog.voices must be an object")
    return {"voices": voices}


def save_voice_catalog(catalog: dict) -> None:
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
    """신규 voice에 재현 가능한 기본 표시색을 지정한다."""

    digest = hashlib.sha256(str(voice or "voice").encode("utf-8")).digest()
    return {"color": COLORS[digest[0] % len(COLORS)], "labels": {"kr": "", "en": ""}}


def ensure_voice_profile(voice: str) -> dict:
    catalog = load_voice_catalog()
    voices = catalog.setdefault("voices", {})
    if voice not in voices:
        voices[voice] = generate_profile(voice)
        save_voice_catalog(catalog)
    return normalize_profile(voices[voice])


def unique_voices_from_mapping(mapping: dict) -> list[str]:
    names = {str(info.get("voice", "")).strip() for info in mapping.values()}
    return sorted(name for name in names if name)


def list_catalog_voices(mapping: dict | None = None) -> list[str]:
    source = mapping if mapping is not None else load_mapping()
    return unique_voices_from_mapping(source)


def sync_mapping_voices_to_catalog(mapping: dict | None = None) -> None:
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
    """Screen에 voice ID·표시색·라벨 목록을 제공한다."""

    mapping = mapping if mapping is not None else load_mapping()
    profiles = load_voice_catalog().setdefault("voices", {})
    voices: list[dict] = []
    index: dict[str, int] = {}
    for voice_id, name in enumerate(unique_voices_from_mapping(mapping)):
        profile = normalize_profile(profiles.get(name) or generate_profile(name))
        entry = {"id": voice_id, "name": name, "color": profile["color"]}
        labels = {key: value for key, value in profile["labels"].items() if value}
        if labels:
            entry["labels"] = labels
        voices.append(entry)
        index[name] = voice_id
    return {"voices": voices, "index": index}


def ensure_voice_catalog_file() -> None:
    """파일을 만들고 현재 color·labels 전용 형식으로 정리한다."""

    catalog = load_voice_catalog() if VOICE_CATALOG_PATH.is_file() else {"voices": {}}
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
