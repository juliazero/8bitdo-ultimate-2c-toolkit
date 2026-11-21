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

To build on Windows with Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]" pyinstaller==6.22.3 Pillow==12.3.0
powershell -ExecutionPolicy Bypass -File windows/build.ps1
```

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

## Verification and CI

`desktop packages` in GitHub Actions builds Windows x86_64 and Linux x86_64 on
their native systems, and uploads artifacts. It can be run manually and also runs
for release tags and relevant pull requests. It does not publish a release.
Every build starts its frozen application in `--smoke-test report.json --smoke-ui` mode,
checks resources, dependencies and the local HTTP server, then loads the actual
embedded WebUI with a simulated controller. It verifies its tabs, secure context
and availability of the Gamepad API without using hardware. Linux CI uses Xvfb.
Linux additionally checks libusb and repeats the test through the AppImage.

The existing test workflow still runs on Windows, Linux and macOS. Hardware
flashing and Bluetooth behavior still require testing on each actual OS/device.
macOS retains the existing Swift application and `macos/build.sh` build process.

On Windows, double-click `Simulator.cmd` next to `Firmware Toolkit.exe` to
open the simulator. The launcher resolves the executable relative to itself,
so it also works after extracting or moving the application folder.
