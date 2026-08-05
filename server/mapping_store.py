"""orb_mapping.json 입출력과 voice 보조 기능을 제공한다."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

VOICE_RE = re.compile(r"^([a-z0-9]+)_(\d+)$", re.I)


def app_dir() -> Path:
    """쓰기 가능한 앱 데이터 경로를 반환한다."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BACKEND_DIR = app_dir()
MAPPING_PATH = BACKEND_DIR / "orb_mapping.json"
REGISTER_CONFIG_PATH = BACKEND_DIR / "config.json"


def ensure_register_data() -> None:
    """앱 옆에 누락된 orb_mapping.json과 config.json을 생성한다."""
    if not MAPPING_PATH.exists():
        MAPPING_PATH.write_text("{}\n", encoding="utf-8")

    if not REGISTER_CONFIG_PATH.exists():
        default = {"serial": {"port": "COM13", "baudrate": 115200}}
        REGISTER_CONFIG_PATH.write_text(
            json.dumps(default, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    from voice_store import ensure_voice_catalog_file

    ensure_voice_catalog_file()


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
        raise ValueError("voice must look like aespa_1, ive_2, cortis_1")
    return voice
