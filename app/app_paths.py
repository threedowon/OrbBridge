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

_DEFAULT_DOCUMENTS = {
    "config.json": (
        '{"current_stage":1,"melody":"dynamite","input_mode":"table",'
        '"audio_output":"midi","web":{"host":"127.0.0.1","port":8080},'
        '"serial":{"port":"COM13","baudrate":115200}}\n'
    ),
    "orb_mapping.json": "{}\n",
    "voice_catalog.json": '{"voices":{}}\n',
    "portmapping.json": "{}\n",
}


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def repo_root() -> Path:
    """번들 UI와 데이터 템플릿이 들어 있는 프로젝트 루트를 반환한다."""
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
        else:
            target.write_text(_DEFAULT_DOCUMENTS[name], encoding="utf-8")

    mdir = melodies_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    bundled_melodies = bundled_melodies_dir()
    if bundled_melodies.is_dir():
        for src in bundled_melodies.glob("*.json"):
            dest = mdir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
