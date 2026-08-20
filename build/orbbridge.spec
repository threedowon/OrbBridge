# PyInstaller 설정 — Python 런타임과 Admin UI를 묶는다.
# 빌드: pyinstaller build/orbbridge.spec --noconfirm

from pathlib import Path

block_cipher = None

build_dir = Path(SPECPATH)
root_dir = build_dir.parent
server_dir = root_dir / "server"


def _collect_datas():
    datas = [
        (str(root_dir / "admin"), "admin"),
    ]
    data = root_dir / "data"
    if data.is_dir():
        datas.append((str(data), "data"))
    return datas


a = Analysis(
    [str(server_dir / "launcher.py")],
    pathex=[str(root_dir)],
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
        "mido.backends.rtmidi",
        "rtmidi",
        "tools.midi_debug",
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
