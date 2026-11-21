# Windows

Install **64-bit Python 3.12** (recommended; 3.13 and 3.14 are also supported)
and then run this from the repository root:

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
