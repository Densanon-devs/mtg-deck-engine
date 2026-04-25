"""Build script for the Densa Deck desktop binary.

Usage:
    python scripts/build_desktop.py

This will:
  1. Verify pyinstaller is installed
  2. Clean any previous build
  3. Run pyinstaller with the spec file
  4. Verify the binary works
  5. Print the output location
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = REPO_ROOT / "densa-deck.spec"
BUILD_DIR = REPO_ROOT / "build"
DIST_DIR = REPO_ROOT / "dist"


def check_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        print("ERROR: PyInstaller not installed.")
        print("Install with: pip install -e .[desktop]")
        return False


def clean():
    print("Cleaning previous build artifacts...")
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)


def build():
    print("Running PyInstaller...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC_FILE), "--clean", "--noconfirm"],
        cwd=str(REPO_ROOT),
    )
    return result.returncode == 0


def verify():
    binary = DIST_DIR / "densa-deck" / ("densa-deck.exe" if sys.platform == "win32" else "densa-deck")
    if not binary.exists():
        print(f"ERROR: Binary not found at {binary}")
        return False

    print(f"Verifying binary at {binary}...")
    # Capture as bytes + decode with errors="replace" so Rich's box-drawing
    # characters (which aren't representable in Windows' default cp1252)
    # don't blow up subprocess's auto-decode and leave us with `out=None`.
    result = subprocess.run([str(binary), "--help"], capture_output=True, timeout=30)
    if result.returncode != 0:
        print(f"ERROR: Binary failed to run: {result.stderr.decode('utf-8', errors='replace')}")
        return False
    help_out = result.stdout.decode("utf-8", errors="replace")

    if "densa-deck" not in help_out.lower():
        print(f"WARNING: Unexpected output: {help_out[:200]}")
        return False

    # Smoke test the analyst runtime. This catches the class of bug that
    # shipped in v0.1.1 / v0.1.2 / v0.1.3 where llama_cpp (or a transitive
    # dep like numpy / jinja2 / diskcache / typing_extensions) was missing
    # from the bundle and the analyst panel showed
    # "Model file is present but llama-cpp-python failed to load (...)"
    # after the user downloaded the GGUF.
    #
    # `densa-deck analyst show` prints "llama-cpp-python: importable" when
    # the import succeeds in the frozen env, or "import failed: <reason>"
    # otherwise.
    print("Smoke-testing `analyst show` (verifies llama_cpp imports)...")
    result = subprocess.run(
        [str(binary), "analyst", "show"], capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"ERROR: `analyst show` failed: {result.stderr.decode('utf-8', errors='replace')}")
        return False
    out = result.stdout.decode("utf-8", errors="replace")
    if "importable" in out.lower():
        print("  llama-cpp-python: importable — analyst runtime OK.")
    else:
        print("ERROR: analyst runtime smoke test FAILED.")
        print("  Expected `analyst show` to print 'llama-cpp-python: importable'.")
        print("  Got:")
        for line in out.splitlines():
            print(f"    {line}")
        print()
        print("  This usually means llama_cpp or one of its deps (numpy,")
        print("  jinja2, diskcache, typing_extensions, markupsafe) didn't")
        print("  make it into the PyInstaller bundle. Check densa-deck.spec")
        print("  hidden_imports + excludes.")
        return False

    print("Binary verified successfully.")
    return True


def main():
    print("=" * 60)
    print("Building Densa Deck Desktop Binary")
    print("=" * 60)

    if not check_pyinstaller():
        sys.exit(1)

    clean()

    if not build():
        print("Build failed.")
        sys.exit(1)

    if not verify():
        print("Verification failed.")
        sys.exit(1)

    output_dir = DIST_DIR / "densa-deck"
    size_mb = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file()) / (1024 * 1024)

    print()
    print("=" * 60)
    print("BUILD COMPLETE")
    print("=" * 60)
    print(f"Output: {output_dir}")
    print(f"Size:   {size_mb:.1f} MB")
    print()
    print("To distribute, zip the entire densa-deck folder:")
    print(f"  cd {DIST_DIR}")
    print("  zip -r densa-deck-windows.zip densa-deck/")
    print()


if __name__ == "__main__":
    main()
