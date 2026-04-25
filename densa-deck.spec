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
    # it — list explicitly. Also pull in its transitive runtime deps
    # (numpy, jinja2/markupsafe, diskcache, typing_extensions) because
    # llama_cpp imports several of them at module load time and missing
    # any one reproduces the "No module named X" error we hit on user
    # first-run of v0.1.2.
    + collect_submodules("llama_cpp", on_error="ignore")
    + collect_submodules("numpy", on_error="ignore")
    + collect_submodules("jinja2", on_error="ignore")
    + collect_submodules("markupsafe", on_error="ignore")
    + collect_submodules("diskcache", on_error="ignore")
    + ["typing_extensions"]
)

# llama-cpp-python ships native DLLs (llama.dll, ggml-*.dll, mtmd.dll)
# alongside the Python package. Without these, `import llama_cpp` fails
# at runtime and the Settings panel shows "Analyst model not installed"
# even when the GGUF file is present on disk.
llama_binaries = collect_dynamic_libs("llama_cpp") if True else []
llama_datas = collect_data_files("llama_cpp") if True else []

# Strip CUDA / cuBLAS DLLs from the llama_cpp binary set. The dev box
# happens to have a CUDA-built wheel installed for local benchmarking,
# which drags ~560 MB of cublasLt64_12.dll + cublas64_12.dll into the
# bundle. Customers don't have a matching CUDA toolkit, so even if
# those DLLs ship they wouldn't actually run on the customer's GPU —
# llama_cpp's runtime probe falls back to CPU when CUDA load fails.
# Releases historically shipped at ~50 MB binary; without this filter,
# the bundle balloons by an order of magnitude for zero customer value.
def _is_cuda_blob(entry):
    name = entry[0].lower() if isinstance(entry, tuple) else str(entry).lower()
    return any(tok in name for tok in (
        "cublas", "cudart", "cudnn", "cufft", "curand", "cusolver",
        "cusparse", "nvrtc", "nvjpeg", "nvjitlink",
    ))
llama_binaries = [b for b in llama_binaries if not _is_cuda_blob(b)]

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
        # numpy must stay included — llama-cpp-python imports it at
        # module load time and removing it causes the analyst model
        # panel to show "Model file is present but llama-cpp-python
        # failed to load (No module named 'numpy')" after a fresh
        # download.
        "pandas",
        "PIL",
        "test",
        "tests",
        "pytest",
        # Dev-environment heavy deps that PyInstaller's static analysis
        # drags in transitively even though densa_deck never imports
        # them. On a dev box with ML tooling installed (torch, scipy,
        # transformers, faiss, etc.) the unfiltered build balloons to
        # ~1.3 GB; v0.1.6 was ~250 MB. Listing every one of these here
        # keeps the bundle close to the historical size.
        "torch",
        "torchvision",
        "torchaudio",
        "scipy",
        "scipy.libs",
        "sklearn",
        "transformers",
        "tokenizers",
        "faiss",
        "faiss_cpu",
        "django",
        "psycopg2",
        "psycopg2_binary",
        "cryptography",
        "Pythonwin",
        "pythonwin",
        "win32com",
        "hf_xet",
        "huggingface_hub",
        "safetensors",
        "sentence_transformers",
        "accelerate",
        "datasets",
        "pyarrow",
        "sympy",
    ],
    noarchive=False,
)

def _is_cuda_dll(entry):
    """True for any binary whose dest-name is a CUDA / cuBLAS DLL.

    PyInstaller's automatic PE-import walker adds these when llama_cpp's
    .pyd files declare them as imports — even if we strip them from the
    explicit `collect_dynamic_libs` list. The cublasLt64_12.dll alone is
    467 MB. Customers don't have a matching CUDA toolkit installed, so
    those DLLs would either fail to load or be unused; llama_cpp falls
    back to CPU when CUDA isn't available. Stripping these entries from
    a.binaries keeps the bundle close to the historical ~50 MB size.
    """
    name = entry[0].lower()
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return any(tok in base for tok in (
        "cublas", "cudart", "cudnn", "cufft", "curand", "cusolver",
        "cusparse", "nvrtc", "nvjpeg", "nvjitlink",
    ))

a.binaries = [b for b in a.binaries if not _is_cuda_dll(b)]

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
