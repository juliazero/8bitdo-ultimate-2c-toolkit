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

## Rebuilding experimental Y firmware

The desktop EXE build does **not** rebuild the firmware. Once you have the
Telink `tc32-elf-gcc.exe` toolchain and a verified **512-KiB backup** from your
own Ultimate 2C, run this in `windows/`:

```powershell
.\build-y.ps1 -Backup "C:\path\to\your-controller-backup.bin" -Compiler "C:\path\to\tc32-elf-gcc.exe"
```

If `tc32-elf-gcc.exe` is on `PATH`, you can omit `-Compiler`. The script
uses `.venv-windows` if available and runs the Y pairing, GATT, and trial-image
builders. It only prepares an experimental image for verification; **it never
flashes the controller**. See `docs/switch2-build.md` for important checks.

### Switch 2 Pro (Y) firmware bundled with the app

The repository already contains compiled `bt_switch2` firmware blobs, including
HD Rumble translation. Running `./build.ps1` packages these tracked blobs in
the Windows EXE/ZIP; **no TC32 toolchain, private backup, or `build/` directory
is required** for routine builds. Select **Switch 2 Pro Controller mode (Y)**
in the app and flash using the normal patch workflow.

Only developers changing `firmware-src/` need to regenerate the committed blobs.
The optional offline command (using a separate TC32 toolchain and a verified
stock 1.09 reference image) is:

```powershell
./build-tweaks.ps1 -PythonPath "..\.venv-windows\Scripts\python.exe" -Compiler "C:\tc32\bin\tc32-elf-gcc.exe" -StockBackup "C:\private\stock-image.bin"
```

The stock image, toolchain, and compiler outputs are **not** checked into Git.
Rebuilt blobs must be tested and committed before distributing a modified EXE.
The updated Y firmware has passed offline tests but **not hardware validation**.
Back up the controller first and verify hardware/firmware compatibility before flashing.
