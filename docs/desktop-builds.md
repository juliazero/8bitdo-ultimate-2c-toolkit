# Windows and Linux desktop builds

These packages include Python and show the WebUI inside a standalone application
window, without opening a browser tab. Windows uses Microsoft Edge WebView2;
Linux bundles QtWebEngine. Closing the window waits for the current operation to
finish. Backups remain in the normal per-user data directory, outside the app.
The existing macOS application continues to use its native Swift/WKWebView shell.

## Windows

Extract the entire ZIP and run `Firmware Toolkit.exe` inside its folder. Python
does not need to be installed. Keep the `_internal` directory next to the EXE.
Microsoft Edge WebView2 Runtime is required (normally already present on Windows
10/11). If missing, install it from [Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/).
The build is currently unsigned; it does not install a driver or require admin rights.

To build on Windows, install **64-bit Python 3.12** (3.13/3.14 also supported)
and run:

```powershell
.\windows\build.ps1
```

The script creates a dedicated `.venv-windows` environment automatically,
installs the desktop dependencies and prebuilt `hidapi==0.15.0` wheel, then
builds the EXE. It will not attempt to compile `hidapi` with MSVC. Use
`-RecreateVenv` after changing Python versions; `-SkipInstall` skips dependency
installation when they are already installed. See [Windows](../windows/README.md).

The ZIP and its SHA-256 file are written to `dist/`.

## Linux AppImage

Build on Linux of the target architecture, preferably Ubuntu 22.04 for x86_64.
The build machine needs Python 3.12 with libusb, desktop graphics libraries, and
[appimagetool 1.9.1](https://github.com/AppImage/appimagetool/releases/tag/1.9.1).
Builds on newer distributions may require a newer glibc on the target machine.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[desktop]" pyinstaller==6.22.3 Pillow==12.3.0
chmod +x /path/to/appimagetool-x86_64.AppImage
./linux/build-appimage.sh /path/to/appimagetool-x86_64.AppImage
chmod +x dist/*.AppImage
```

Run the AppImage as your normal user. Install the repository's udev rules once
(see [Linux setup](linux.md)); packaging does not grant USB/hidraw access. The
rules are also included under `usr/share/firmware-toolkit/udev/` in the AppImage.
If FUSE is unavailable, run `./firmware-toolkit-*.AppImage --appimage-extract-and-run`.
A graphical session is required; Python and QtWebEngine are bundled.

## GitHub Actions: build and publish beta releases

The [`desktop packages`](../.github/workflows/desktop.yml) workflow builds on
**four native GitHub-hosted runners**: Windows x86_64, Ubuntu 22.04 x86_64,
macOS arm64 and macOS Intel x86_64. On pull requests and manual dispatch it
uploads build artifacts for inspection, without creating a GitHub Release.

An annotated `vX.Y.ZbN` tag (currently `v1.3.0b1`) triggers the same builds
and, after **all four succeed**, a publishing job. It verifies the version,
exactly four expected packages and their SHA-256 checksums before publishing
them together as a GitHub **Pre-release**, never a latest stable release.
If any build or smoke check fails, the release is **not published**.
The release job only needs the standard `GITHUB_TOKEN` with `contents: write`;
ensure repository Actions settings permit workflow write access. See
[releasing](releasing.md) for instructions and tag examples.

Expected files for `1.3.0b1`:

- `firmware-toolkit-1.3.0b1-windows-x86_64.zip` (portable EXE and bundled runtime)
- `firmware-toolkit-1.3.0b1-linux-x86_64.AppImage`
- `firmware-toolkit-1.3.0b1-macos-arm64.zip`
- `firmware-toolkit-1.3.0b1-macos-x86_64.zip`
- A matching `.sha256` for each package

Windows and Linux builds run the frozen application's simulator/UI smoke test;
Linux also exercises the AppImage. The existing macOS Swift build packages the
native application but has **no hardware or GUI smoke test** on CI. The regular
test workflow is independent and runs the Python and JS suites on all three
operating systems. Hardware flashing and battery support still require tests
on actual devices; especially Linux and macOS RF battery telemetry.

On Windows, double-click `Simulator.cmd` next to `Firmware Toolkit.exe` to
open the simulator. The launcher resolves the executable relative to itself,
so it also works after extracting or moving the application folder.
