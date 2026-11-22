# U2C Firmware Toolkit

<p align="center">
  <a href="https://github.com/arithon-f/8bitdo-ultimate-2c-toolkit/releases/latest"><img src="https://img.shields.io/github/v/release/arithon-f/8bitdo-ultimate-2c-toolkit" alt="Latest release"></a>
  <a href="https://github.com/arithon-f/8bitdo-ultimate-2c-toolkit/releases"><img src="https://img.shields.io/github/downloads/arithon-f/8bitdo-ultimate-2c-toolkit/total" alt="Downloads"></a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue" alt="macOS 13 or later">
  <img src="https://img.shields.io/github/license/arithon-f/8bitdo-ultimate-2c-toolkit" alt="GPL-3.0 license">
</p>

<p align="center">
  <img src="docs/images/firmware.png" alt="The Firmware tab, showing firmware 1.09 with its tweaks next to the backup and restore steps" width="420">
  <img src="docs/images/diagnostics.png" alt="The Diagnostics tab, testing a controller over Bluetooth" width="420">
</p>

U2C Firmware Toolkit is a toolkit for the **8BitDo Ultimate 2C Wireless** and its **2.4 GHz dongle** on Windows, Linux and macOS: everything they need, in one app. It makes macOS and its games treat the controller as a native one, by cable, over Bluetooth and through the dongle, adds rumble and control over its light, and lets you test it, back both up and restore them. The firmware tweaks live in the controller and dongle themselves; on Linux, some connection modes additionally depend on system drivers (see the Linux guide).

Out of the box, macOS and most Mac games see the Ultimate 2C as a generic HID device, not as a game controller. After this, they see an Xbox controller.

> **This is a vibe-coded project, written with an AI assistant (Opus 5.5). There's no warranty, and you use it at your own risk.** Installing modified firmware probably voids the controller's and the dongle's warranty. Please read the [full disclaimer](DISCLAIMER.md) and the [safeguards](SAFEGUARDS.md) before using it. Not affiliated with or endorsed by 8BitDo.

## What it does

The app installs 8BitDo's own firmware with the tweaks you choose, small edits that change how the controller and the dongle behave. With them:

| Feature | What it does |
| --- | --- |
| **Native controller** | Over Bluetooth it presents itself as an Xbox Series X\|S controller, by cable and through the 2.4 GHz dongle as an Xbox 360 controller (through the dongle, at up to 1,000 reports a second), so macOS and its games recognise it every way. |
| **Rumble over Bluetooth** | Games can make it vibrate over Bluetooth, which the original firmware doesn't allow. |
| **Adjustable light** | Dim the ring around the Home button or turn it off, from the app or with L4 + R4. |
| **Battery alerts** | The ring and the motors tell you when the battery is low, and when charging starts and completes. |
| **Settings that change at once** | Change the dead zones, the stick and trigger ranges, the vibration strength, L4 and R4, the button layout and the power-off time from the app, without installing anything again. |
| **Fewer interruptions** | Playing only with the sticks no longer turns it off, a lost connection gets its full minute to come back, and unplugging the cable reconnects it over Bluetooth at once. |
| **Processor rest** | Over Bluetooth, the processor rests between radio events instead of running without a pause, and a watchdog restarts the controller if it ever stops responding. |
| **D-input through the dongle** | The controller's B + Home switch to D-input works again through the dongle, as with the dongle's first firmware, 1.00: it then presents itself as a generic gamepad, which Android handles natively, also on TVs. |

The app itself also tests the controller, backs up the controller and the dongle, and restores your backup or 8BitDo's original firmware at any time.

It works with the **Ultimate 2C Wireless on firmware 1.09** and its **2.4 GHz dongle on firmware 1.03**, on Macs with Apple silicon or Intel and macOS 13 or later. It doesn't work with other models. [docs/tweaks.md](docs/tweaks.md) explains each change in detail.

## Beta prerelease (Windows, Linux and macOS)

The current development version is **1.3.0b1**. Pushing the annotated Git tag
`v1.3.0b1` runs GitHub Actions to build a **Windows x86_64 ZIP with EXE**,
**Linux x86_64 AppImage**, and **macOS ZIPs for arm64 and Intel x86_64**. All
four packages are uploaded to the same GitHub **Pre-release** after successful
builds and smoke checks. See the [releases page](https://github.com/arithon-f/8bitdo-ultimate-2c-toolkit/releases)
for betas; `releases/latest` intentionally points to stable releases only.

Linux and macOS 2.4 GHz battery telemetry still need verification on physical
hardware. Beta packages are not signed or notarized; see platform instructions.

## Install (macOS 13 or later)

1. Download the zip for your Mac from the [releases page](https://github.com/arithon-f/8bitdo-ultimate-2c-toolkit/releases) and unzip it: `firmware-toolkit-<version>-macos-arm64.zip` for Apple silicon, or `firmware-toolkit-<version>-macos-x86_64.zip` for Intel. The Intel build has been tested under Rosetta, not yet on an Intel Mac.
2. Open the app once. macOS will block it because it is not signed: open **System Settings → Privacy & Security**, scroll to the message about the app and click **Open Anyway** (right-click → Open no longer works on current macOS). You can also remove it from quarantine with `xattr`, as the [FAQ](docs/faq.md#macos-wont-open-the-app) shows.
3. When asked, allow **Input Monitoring** (System Settings → Privacy & Security → Input Monitoring). The app needs it to talk to the controller and the dongle.

Or with [Homebrew](https://github.com/arithon-f/homebrew-tap), which replaces step 1:

```sh
# the app
brew trust --cask arithon-f/tap/firmware-toolkit
brew install --cask arithon-f/tap/firmware-toolkit
# the command line only
brew install arithon-f/tap/firmware-toolkit
```

Or from source, with Python 3.12+: `python3 -m u2c_customfw.web` opens the app in your browser, at a local address with a session token; `python3 -m u2c_customfw --help` is the command line. The command-line tool and the Python package keep the project's first name, `u2c-customfw`.

## Install (Linux)

See [Linux permissions, diagnostics and device support](docs/linux.md) and [AppImage packaging](docs/desktop-builds.md#linux-appimage). There is no project-specific Linux installer; use AppImage or a system package, and install udev rules separately if required. Source development uses a normal Python virtual environment. Platform-specific limitations are documented in the guide.

## Use

Windows 10/11 is also supported from source with Python 3.12+ and HIDAPI.
See [Windows installation and diagnostics](docs/windows.md) for PowerShell commands
and the current hardware-validation status.

The app's **Help** button explains each step and why each tweak is needed.

1. **Back up** with the controller connected by cable. The backup holds your controller's calibration and Bluetooth pairing keys too, and the app requires one before it writes anything.
2. **Install tweaks.** Choose them, use *Check First*, then *Install Tweaks…*, with the controller connected by cable. The app puts it in update mode to write and restarts it afterwards. If a Bluetooth tweak changed, remove the controller from Bluetooth settings and pair it again.
3. **Restore** your backup, or 8BitDo's original firmware 1.09, the same way, whenever you want to go back.

For the **dongle**, turn the controller off and plug the dongle in on its own. The steps are the same: back it up, switch its tweaks on or off and install, or restore. The app puts it in update mode by itself.

If something doesn't work, the [FAQ](docs/faq.md) covers the usual problems.

## Documentation

- [SAFEGUARDS.md](SAFEGUARDS.md): every check the app makes before and while it writes.
- [docs/faq.md](docs/faq.md): installing, update mode, re-pairing, rumble and compatibility.
- [docs/tweaks.md](docs/tweaks.md): what each tweak changes, for the controller and the dongle.
- [docs/settings.md](docs/settings.md): the Settings tab, which changes the controller without reinstalling.
- [docs/diagnostics.md](docs/diagnostics.md): the controller test, firmware details, problem reports and the battery log.
- [docs/firmware-notes.md](docs/firmware-notes.md): flash layout, update and bootloader protocols, RAM code.
- [docs/bluetooth-and-rumble.md](docs/bluetooth-and-rumble.md): how the Bluetooth Xbox identity and rumble work.
- [docs/linux.md](docs/linux.md): installing and using the toolkit on Linux.
- [docs/porting.md](docs/porting.md): adding support for other operating systems.
- [firmware-src/README.md](firmware-src/README.md): the C and assembly the tweaks add, and how to rebuild it.
- [docs/releasing.md](docs/releasing.md): publishing a new version, and how the Homebrew tap follows it.

## Other projects

Other people reached the same goal on their own, and these are worth a look:

- **[vomba/8bitdo-ultimate-2c-apple](https://github.com/vomba/8bitdo-ultimate-2c-apple)** changes just six bytes of firmware 1.09 so Apple uses its SN30 Pro+ profile: the smallest possible patch, without rumble. Its owner reports that it also works on Apple TV.
- **[Crazyawk/8bitdo-ultimate2c-macos-rumble](https://github.com/Crazyawk/8bitdo-ultimate2c-macos-rumble)** brings Bluetooth rumble to CrossOver games, with patched firmware and an SDL shim.
- **[lodo4nik/8bitdo-sdl-macos-fix](https://github.com/lodo4nik/8bitdo-sdl-macos-fix)** helps SDL games and CrossOver recognise the controller on macOS without touching its firmware, by changing one setting each time you log in.
- **[amirreza225/8bitdo-ultimate-2c-mac](https://github.com/amirreza225/8bitdo-ultimate-2c-mac)** makes the Ultimate 2C **Wired** work in Chrome and Amazon Luna, with a browser extension. This app doesn't support that model.

## Credits

This project builds on the reverse engineering of [jgus-org/8bitdo-2c-re-flake](https://github.com/jgus-org/8bitdo-2c-re-flake), the Xbox Series X|S descriptor captured by [xpadneo](https://github.com/atar-axis/xpadneo), the Home ring method of [supercypok/ultimate2c-ring](https://github.com/supercypok/ultimate2c-ring), and notes from [Crazyawk](https://github.com/Crazyawk/8bitdo-ultimate2c-macos-rumble), [vomba](https://github.com/vomba/8bitdo-ultimate-2c-apple) and [MoridaDE](https://github.com/MoridaDE/8bitdo-2c-battery-tray). No code was copied. [CREDITS.md](CREDITS.md) says what came from each.

## License

GPL-3.0-or-later; see `LICENSE`. 8BitDo firmware is not included in this repository. Not affiliated with or endorsed by 8BitDo.

## Experimental Bluetooth Y: firmware and triggers

The Switch 2 Pro Controller report exposes **digital ZL/ZR**, not analog LT/RT.
For smooth 0–100% analog triggers use the Xbox Bluetooth X profile. An NSO
GameCube-compatible mode would require a separate BLE identity and report
implementation. The Y haptic decoder is an experimental source-level change;
prebuilt Y firmware blobs have not been regenerated. See
[building the Y firmware](docs/switch2-build.md) before attempting a device test.

## Desktop packages

Build **Windows EXE + portable ZIP** on Windows, or a **Linux AppImage** on Linux
(Python 3.12 or newer). Both packages include the Python runtime; the EXE uses
Microsoft Edge WebView2, and the AppImage includes QtWebEngine.

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]" pyinstaller==6.22.3 Pillow==12.3.0
powershell -ExecutionPolicy Bypass -File windows/build.ps1
```

Linux (requires `libusb-1.0-0`, Qt desktop libraries and `appimagetool`):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[desktop]' pyinstaller==6.22.3 Pillow==12.3.0
./linux/build-appimage.sh /path/to/appimagetool
```

Outputs and SHA-256 checksums are created under `dist/`. The Windows EXE is
inside the extracted ZIP alongside `_internal`; do not move the EXE alone.
The AppImage requires udev permissions for device access. See
[desktop build prerequisites and troubleshooting](docs/desktop-builds.md),
[Linux](linux/README.md), [Windows](windows/README.md), and [macOS](macos/).
