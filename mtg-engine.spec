# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MTG Deck Engine desktop binary.

Build with:
    pyinstaller mtg-engine.spec --clean

Output:
    dist/mtg-engine/        (folder mode — faster startup, smaller per-file)
    dist/mtg-engine.exe     (single-file mode — slower startup, easier to ship)

This spec uses folder mode by default for better performance.
"""

from PyInstaller.utils.hooks import collect_submodules

# Collect all submodules of our package and key dependencies
hidden_imports = (
    collect_submodules("mtg_deck_engine")
    + collect_submodules("rich")
    + collect_submodules("pydantic")
    + collect_submodules("httpx")
    + collect_submodules("cryptography")
)

a = Analysis(
    ["src/mtg_deck_engine/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "test",
        "tests",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mtg-engine",
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
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="mtg-engine",
)
