"""개발 환경과 PyInstaller 실행 파일의 리소스·쓰기 경로를 관리한다."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_SEED_FILES = (
    "config.json",
    "orb_mapping.json",
    "voice_catalog.json",
    "portmapping.json",
)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def repo_root() -> Path:
    """번들 UI와 서버 템플릿의 루트 경로를 반환한다."""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """모든 JSON과 사용자 데이터를 보관하는 data 경로를 반환한다."""
    if is_frozen():
        return Path(sys.executable).resolve().parent / "data"
    return repo_root() / "data"


def bundled_data_dir() -> Path:
    """실행 파일에 포함된 기본 data 경로를 반환한다."""
    return repo_root() / "data"


def server_bundle_dir() -> Path:
    """번들에 포함된 서버 기본 파일 경로를 반환한다."""
    if is_frozen():
        return Path(sys._MEIPASS) / "server"
    return Path(__file__).resolve().parent


def sounds_dir() -> Path:
    bundled = server_bundle_dir() / "sounds"
    if bundled.is_dir():
        return bundled
    return data_dir() / "sounds"


def melodies_dir() -> Path:
    """판정·화면·재생이 함께 사용하는 멜로디 단일 원본 경로다."""
    return data_dir() / "melodies"


def bundled_melodies_dir() -> Path:
    return bundled_data_dir() / "melodies"


def ensure_data_files() -> None:
    """첫 실행 때 기본 JSON을 복사하거나 누락 파일을 생성한다."""
    dest = data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    bundle = bundled_data_dir()

    for name in _SEED_FILES:
        target = dest / name
        if target.exists():
            continue
        src = bundle / name
        if src.is_file():
            shutil.copy2(src, target)
        elif name == "orb_mapping.json":
            target.write_text("{}\n", encoding="utf-8")
        elif name == "config.json":
            table_cfg = bundled_data_dir() / "config.table.json"
            if table_cfg.is_file():
                shutil.copy2(table_cfg, target)
            else:
                target.write_text(
                    '{"current_stage":1,"melody":"biplane","input_mode":"table",'
                    '"mock":{"host":"127.0.0.1","port":8080},'
                    '"serial":{"port":"COM13","baudrate":115200}}\n',
                    encoding="utf-8",
                )

    sounds_dir().mkdir(parents=True, exist_ok=True)

    bundled_sounds = server_bundle_dir() / "sounds"
    user_sounds = data_dir() / "sounds"
    if bundled_sounds.is_dir() and not any(user_sounds.glob("*")):
        shutil.copytree(bundled_sounds, user_sounds, dirs_exist_ok=True)

    mdir = melodies_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    bundled_melodies = bundled_melodies_dir()
    if bundled_melodies.is_dir():
        for src in bundled_melodies.glob("*.json"):
            dest = mdir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
