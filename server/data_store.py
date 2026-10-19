"""config, orb_mapping, portmapping 입출력과 voice 보조 기능을 제공한다."""

from __future__ import annotations

import json
import re
from pathlib import Path

from server.app_paths import data_dir, ensure_data_files

VOICE_RE = re.compile(r"^([a-z0-9]+)_(\d+)$", re.I)

DATA_DIR = data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
MAPPING_PATH = DATA_DIR / "orb_mapping.json"


def ensure_register_data() -> None:
    """오브 등록에 필요한 공용 data 파일을 준비한다."""
    ensure_data_files()


def load_config(path: Path | None = None) -> dict:
    """실행 설정 JSON을 객체로 읽는다."""
    target = path or CONFIG_PATH
    with target.open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict):
        raise ValueError("config must be an object")
    return document


def save_config(config: dict, path: Path | None = None) -> None:
    """실행 설정을 UTF-8 JSON으로 저장한다."""

    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    target = path or CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def normalize_id(raw: str) -> str:
    """DS2431·오브 ID를 대문자 `AA:BB:CC:...` 형식으로 정규화한다."""
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


def resolve_orb_key(orb_id: str, mapping: dict) -> str:
    """UI·시리얼 ID를 orb_mapping.json의 키로 변환한다.

    새 ID는 normalize_id 검증을 통과해야 한다. 이전 테스트 ID처럼 이미 저장된
    키는 16진수 형식이 아니어도 그대로 허용한다.
    """
    raw = str(orb_id).strip()
    if not raw:
        raise ValueError("empty id")
    if raw in mapping:
        return raw
    upper = raw.upper()
    for key in mapping:
        if key.upper() == upper:
            return key
    return normalize_id(raw)


def find_orb(orb_id: str, mapping: dict) -> tuple[str | None, dict | None]:
    """등록된 orb면 (canonical_key, entry), 없으면 (None, None)."""
    raw = str(orb_id or "").strip()
    if not raw:
        return None, None
    try:
        key = resolve_orb_key(raw, mapping)
    except ValueError:
        return None, None
    entry = mapping.get(key)
    if entry is None:
        return None, None
    return key, entry


def load_mapping(path: Path | None = None) -> dict:
    p = path or MAPPING_PATH
    versions = load_orb_versions(p)
    if versions is not None:
        return versions["mappings"][versions["active"]]
    with p.open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict):
        raise ValueError("orb_mapping must be an object")
    return document


def save_mapping(mapping: dict, path: Path | None = None) -> None:
    p = path or MAPPING_PATH
    ordered = dict(sorted(mapping.items(), key=lambda kv: kv[0]))
    versions = load_orb_versions(p)
    if versions is not None:
        versions["mappings"][versions["active"]] = ordered
        save_config(versions, orb_versions_path(p))
    else:
        save_config(ordered, p)


def orb_versions_path(path: Path | None = None) -> Path:
    return (path or MAPPING_PATH).with_suffix(".versions.json")


def load_orb_versions(path: Path | None = None) -> dict | None:
    p = orb_versions_path(path)
    if not p.exists():
        return None
    document = load_config(p)
    active = document.get("active")
    mappings = document.get("mappings", {})
    if active not in ("original", "male") or not isinstance(mappings.get(active), dict):
        raise ValueError("오브 버전 배정표가 올바르지 않습니다")
    return document


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
        raise ValueError("voice must look like aespa_1, ive_2, cortis_1")
    return voice
