# Orb Register 데스크톱 앱 PyInstaller 설정이다.
# 빌드: pyinstaller build/orb_register.spec --noconfirm

from pathlib import Path

block_cipher = None

build_dir = Path(SPECPATH)
root_dir = build_dir.parent

a = Analysis(
    [str(root_dir / "tools" / "orb_register.py")],
    pathex=[str(root_dir)],
    binaries=[],
    datas=[(str(p), str(Path("data") / p.relative_to(root_dir / "data").parent))
           for p in (root_dir / "data").rglob("*")
           if p.is_file() and p.name != "ports.json"],
    hiddenimports=[
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
    name="OrbRegister",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name="OrbRegister",
)
