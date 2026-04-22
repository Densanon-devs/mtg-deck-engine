# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Densa Deck desktop binary.

Build with:
    pyinstaller densa-deck.spec --clean

Output:
    dist/densa-deck/        (folder mode — faster startup, smaller per-file)
    dist/densa-deck.exe     (single-file mode — slower startup, easier to ship)

This spec uses folder mode by default for better performance.
"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# Collect all submodules of our package and key dependencies
hidden_imports = (
    collect_submodules("densa_deck")
    + collect_submodules("rich")
    + collect_submodules("pydantic")
    + collect_submodules("httpx")
    # pywebview is optional but add it when present so the desktop app
    # ships inside the bundle without needing a separate install.
    + collect_submodules("webview", on_error="ignore")
    # llama-cpp-python powers the optional analyst model. Lazy-imported
    # inside densa_deck.analyst so PyInstaller's static analysis misses
    # it — list explicitly.
    + collect_submodules("llama_cpp", on_error="ignore")
)

# llama-cpp-python ships native DLLs (llama.dll, ggml-*.dll, mtmd.dll)
# alongside the Python package. Without these, `import llama_cpp` fails
# at runtime and the Settings panel shows "Analyst model not installed"
# even when the GGUF file is present on disk.
llama_binaries = collect_dynamic_libs("llama_cpp") if True else []
llama_datas = collect_data_files("llama_cpp") if True else []

a = Analysis(
    ["src/densa_deck/__main__.py"],
    pathex=["src"],
    binaries=llama_binaries,
    # Ship the desktop app's HTML/CSS/JS assets inside the bundle. Without
    # this PyInstaller strips the static/ dir and the frozen app launches
    # with a blank window. llama_cpp's data files (metadata, etc.) come in
    # via collect_data_files so the analyst model path resolves correctly.
    datas=[
        ("src/densa_deck/app/static/*", "densa_deck/app/static"),
    ] + llama_datas,
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
    name="densa-deck",
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
    icon="assets/densa-deck.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="densa-deck",
)
