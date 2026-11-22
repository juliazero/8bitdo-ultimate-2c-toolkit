## Firmware Toolkit 1.3.0 beta 1

Beta preview for the 8BitDo Ultimate 2C Wireless controller and its 2.4 GHz receiver.

### Packages

- **Windows x86_64:** portable ZIP containing `Firmware Toolkit.exe` (requires WebView2)
- **Linux x86_64:** AppImage (udev permissions may need separate setup)
- **macOS arm64:** application ZIP for Apple silicon
- **macOS x86_64:** application ZIP for Intel Macs

Each package includes a matching SHA-256 checksum file. Firmware flashing is optional; use **Check First** and make a controller/dongle backup before installing tweaks.

### Beta notes

- Includes the current Bluetooth, USB DInput, dongle and Steam-compatible mapping functionality from this repository.
- 2.4 GHz battery telemetry has been verified on Windows. **Linux and macOS still require physical hardware verification.**
- Packages are unsigned / not notarized. macOS may require **Open Anyway**; Windows may show a SmartScreen warning.
- Automated builds and smoke tests do **not** replace testing USB and Bluetooth behavior on actual hardware.
- This is a **pre-release**, not a stable update. The Homebrew stable tap is not changed by the beta release.

Please report regressions with the operating system, connection mode, firmware version and relevant diagnostics.
