"""Dev vs PyInstaller paths — bundle assets + writable data beside exe."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_SEED_FILES = (
    "config.json",
    "orb_mapping.json",
    "stage_data.json",
    "voice_catalog.json",
)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def repo_root() -> Path:
    """Bundled UI (screen/, table/, admin/) and server templates."""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Writable JSON + user data (server/ in dev, exe folder when frozen)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def server_bundle_dir() -> Path:
    """Bundled server defaults (config templates, stage_data seed)."""
    if is_frozen():
        return Path(sys._MEIPASS) / "server"
    return Path(__file__).resolve().parent


def sounds_dir() -> Path:
    bundled = server_bundle_dir() / "sounds"
    if bundled.is_dir():
        return bundled
    return data_dir() / "sounds"


def ensure_data_files() -> None:
    """Copy default JSON into data_dir on first run (frozen) or create missing files."""
    dest = data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    bundle = server_bundle_dir()

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
            table_cfg = bundle / "config.table.json"
            if table_cfg.is_file():
                shutil.copy2(table_cfg, target)
            else:
                target.write_text(
                    '{"current_stage":1,"melody":"biplane","input_mode":"table",'
                    '"mock":{"host":"127.0.0.1","port":8080},'
                    '"serial":{"port":"COM13","baudrate":115200},'
                    '"osc":{"host":"127.0.0.1","port":9000}}\n',
                    encoding="utf-8",
                )

    sounds_dir().mkdir(parents=True, exist_ok=True)

    bundled_sounds = server_bundle_dir() / "sounds"
    user_sounds = data_dir() / "sounds"
    if bundled_sounds.is_dir() and not any(user_sounds.glob("*")):
        shutil.copytree(bundled_sounds, user_sounds, dirs_exist_ok=True)
