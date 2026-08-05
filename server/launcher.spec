# PyInstaller 설정 — Python 런타임과 세 웹 인터페이스를 묶는다.
# 빌드: server/build_launcher.bat

import sys
from pathlib import Path

block_cipher = None

spec_dir = Path(SPECPATH)
server_dir = spec_dir
root_dir = spec_dir.parent


def _collect_datas():
    datas = [
        (str(root_dir / "table"), "table"),
        (str(root_dir / "screen"), "screen"),
        (str(root_dir / "admin"), "admin"),
    ]
    data = root_dir / "data"
    if data.is_dir():
        datas.append((str(data), "data"))
    sounds = server_dir / "sounds"
    if sounds.is_dir():
        datas.append((str(sounds), "server/sounds"))
    return datas


a = Analysis(
    ["launcher.py"],
    pathex=[str(server_dir)],
    binaries=[],
    datas=_collect_datas(),
    hiddenimports=[
        "websockets",
        "websockets.legacy",
        "websockets.legacy.server",
        "serial",
        "serial.tools",
        "serial.tools.list_ports",
        "serial.tools.list_ports_common",
        "serial.tools.list_ports_windows",
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
