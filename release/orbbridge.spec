# PyInstaller spec — OrbBridge demo bundle (Windows)
# Build: release/build_release.ps1

import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPEC).resolve().parent.parent
SERVER = ROOT / "server"

datas = [
    (str(ROOT / "screen"), "screen"),
    (str(ROOT / "table"), "table"),
    (str(ROOT / "admin"), "admin"),
    (str(SERVER / "config.table.json"), "server"),
    (str(SERVER / "stage_data.json"), "server"),
    (str(SERVER / "orb_mapping.json"), "server"),
    (str(SERVER / "voice_catalog.json"), "server"),
]

_sounds = SERVER / "sounds"
if _sounds.is_dir():
    datas.append((str(_sounds), "server/sounds"))

a = Analysis(
    [str(SERVER / "launcher.py")],
    pathex=[str(SERVER)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "websockets",
        "websockets.legacy",
        "websockets.legacy.server",
        "websockets.server",
        "pythonosc",
        "pythonosc.dispatcher",
        "pythonosc.osc_server",
        "pythonosc.udp_client",
        "serial",
        "socketserver",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OrbBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OrbBridge",
)
