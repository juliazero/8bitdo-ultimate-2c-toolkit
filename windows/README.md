# Windows

The build script selects **Python 3.12 x64** independently of the system's default Python (including Python 3.15). If missing, it offers to install
3.12 through `winget`. Run from the repository root:

```powershell
.\windows\build.ps1
```

Or from `windows/`:

```powershell
.\build.ps1
```

The script automatically creates its own `.venv-windows` environment, upgrades
pip, installs `.[desktop]`, PyInstaller, Pillow and **binary** `hidapi 0.15.0`
(without compiling the HID extension), and builds the portable EXE and ZIP in
`dist/`. It does **not** require administrator rights or Visual C++ Build Tools.
Python needs to be available as `py` or `python` on your machine.

Optional flags:

- `-RecreateVenv` recreates `.venv-windows` (use after changing Python versions).
- `-SkipInstall` skips package installation when dependencies are already present.

If `hidapi` reports that no matching wheel exists, check that Python is **x64**
and within versions 3.12-3.14, and that your pip index provides wheels. The
script explicitly refuses to compile `hidapi` from source.

The resulting EXE requires Microsoft Edge WebView2 Runtime. No drivers are
installed by the build script.

The Python launcher (`py.exe`) is optional. An installed Python 3.12 can also be
detected from standard installation paths. To specify it explicitly, use
`./build.ps1 -PythonPath "C:\Python312\python.exe"`.

## Firmware tweak development

The normal Windows build packages the versioned firmware blobs as-is. It does
not rebuild or flash firmware and requires neither TC32 nor a controller backup.

Developers changing firmware sources can use the existing rebuild commands in
`tools/` and `firmware-src/` with a verified stock reference. For example:

```powershell
..\.venv-windows\Scripts\python.exe ..\tools\rebuild_switch2_tweak.py --compiler "C:\tc32\bin\tc32-elf-gcc.exe" --stock "C:\private\stock-image.bin"
```

Run this from `windows/`. Rebuilt blobs require independent validation before
being committed or distributed. The Toolkit's normal **Install Tweaks** workflow
remains the only installation route; a desktop EXE build does not flash hardware.
