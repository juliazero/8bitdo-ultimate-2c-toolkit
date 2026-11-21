# Build with Python 3.12+ after installing the desktop extras.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Missing $python. Create a venv and install .[desktop], PyInstaller, Pillow." }
Push-Location $root
try {
    & $python tools/build_desktop.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
