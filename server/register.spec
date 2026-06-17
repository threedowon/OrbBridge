# PyInstaller spec — Orb Register desktop app (Windows)
# Build: pyinstaller register.spec --noconfirm

block_cipher = None

a = Analysis(
    ["register_tool.py"],
    pathex=[],
    binaries=[],
    datas=[],
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
