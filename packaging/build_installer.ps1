# Build the Windows installer end-to-end.
#
# Prereqs (one-time):
#   1. `pip install -e '.[desktop]'` to get pyinstaller + pywebview
#   2. Install Inno Setup from https://jrsoftware.org/isinfo.php
#      (ISCC.exe ends up at "C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
#
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
#
# Outputs:
#   dist\densa-deck\              -> PyInstaller bundle (folder mode)
#   dist\MTG-Deck-Engine-Setup-<ver>.exe -> Inno Setup installer
#
# Code signing: add `& signtool sign ...` calls after the build steps if a
# cert is available. Unsigned installers still work — users see a Smart
# Screen warning they can click through.

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

try {
    # Step 1: PyInstaller — folder mode (faster startup than single-file)
    Write-Host "[1/2] Running PyInstaller..." -ForegroundColor Cyan
    pyinstaller densa-deck.spec --clean --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

    # Step 2: Inno Setup
    # Probe the three install paths Inno Setup 6 lands at: 32-bit Program
    # Files (the default), 64-bit Program Files (rare), and per-user
    # AppData\Local\Programs (what `winget install JRSoftware.InnoSetup`
    # picks when there's no UAC elevation available).
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    $ISCC = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $ISCC) {
        throw "ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isinfo.php (or via `winget install JRSoftware.InnoSetup`)."
    }
    Write-Host "[2/2] Running Inno Setup..." -ForegroundColor Cyan
    & $ISCC "packaging\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

    Write-Host "`nBuild complete. Installer in dist\." -ForegroundColor Green
    Get-ChildItem dist\*.exe | ForEach-Object { Write-Host "  $($_.FullName)" }
}
finally {
    Pop-Location
}
