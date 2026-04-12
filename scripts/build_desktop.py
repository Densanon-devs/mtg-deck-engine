"""Build script for the MTG Deck Engine desktop binary.

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
SPEC_FILE = REPO_ROOT / "mtg-engine.spec"
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
    binary = DIST_DIR / "mtg-engine" / ("mtg-engine.exe" if sys.platform == "win32" else "mtg-engine")
    if not binary.exists():
        print(f"ERROR: Binary not found at {binary}")
        return False

    print(f"Verifying binary at {binary}...")
    result = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"ERROR: Binary failed to run: {result.stderr}")
        return False

    if "mtg-engine" not in result.stdout.lower():
        print(f"WARNING: Unexpected output: {result.stdout[:200]}")
        return False

    print("Binary verified successfully.")
    return True


def main():
    print("=" * 60)
    print("Building MTG Deck Engine Desktop Binary")
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

    output_dir = DIST_DIR / "mtg-engine"
    size_mb = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file()) / (1024 * 1024)

    print()
    print("=" * 60)
    print("BUILD COMPLETE")
    print("=" * 60)
    print(f"Output: {output_dir}")
    print(f"Size:   {size_mb:.1f} MB")
    print()
    print("To distribute, zip the entire mtg-engine folder:")
    print(f"  cd {DIST_DIR}")
    print("  zip -r mtg-engine-windows.zip mtg-engine/")
    print()


if __name__ == "__main__":
    main()
