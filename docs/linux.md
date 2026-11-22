# Linux support

The Linux port shares the firmware, backup, restore and web application logic with macOS. Linux-specific transport uses `hidraw` and, for the Xbox-compatible wired controller (`045e:028e`), PyUSB/libusb on USB interface 2. Diagnostics use `evdev` for gamepad input and force feedback.

## Requirements

- Linux with `hidraw`, `evdev`, `udev` and access to the USB device
- Python 3.12 or later
- `pyusb` and `evdev` (installed from `pyproject.toml` on Linux)
- libusb runtime for PyUSB

## AppImage and source development

For a desktop package, use the [AppImage build instructions](desktop-builds.md#linux-appimage).
A prebuilt AppImage does not require a system-wide Python installation.
System packages may provide their own dependency and udev setup.

### udev access

An AppImage or system package cannot assume it has access to `/dev/hidraw*`.
Install the provided udev rule once on the host, then reconnect the dongle or
controller:

```sh
sudo install -Dm644 udev/70-8bitdo-ultimate-2c.rules /etc/udev/rules.d/70-8bitdo-ultimate-2c.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw
```

`TAG+="uaccess"` grants permissions to the active local session on systems
using logind; headless machines may need different permissions. **Do not run
the Toolkit as root.**

For development directly from the source tree (no installer):

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m u2c_customfw devices
.venv/bin/python -m u2c_customfw.web
```

The last command opens a browser. If it does not, use the URL printed by the
server, including `?token=…`.

## Diagnostics

The web Diagnostics tab reads Linux gamepad input through `evdev` and can test rumble through `FF_RUMBLE` when supported by the device and permissions. Direct Bluetooth protocol reports are not available through this diagnostics bridge. The separate `python -m u2c_customfw.usb_diagnostic` command is available for low-level investigation.

## Bluetooth Xbox/X/S mode and xpadneo

On Linux, the controller's **modified Xbox/X/S Bluetooth mode** is supported through the [xpadneo](https://github.com/atar-axis/xpadneo) driver. This requirement is specific to that Bluetooth mode: the controller's **original Bluetooth HID mode** does not require xpadneo. The original HID mode does not expose `FF_RUMBLE` through the standard Linux input interface.

### Short rumble pulses instead of sustained vibration

With xpadneo v0.10.5, a contributor observed that `FF_RUMBLE` sometimes produced only a short pulse. On the tested device, overriding the detected quirks from `0x17` to `0x16` (disabling `XPADNEO_QUIRK_NO_PULSE`) restored sustained rumble:

```conf
# /etc/modprobe.d/8bitdo-ultimate-2c.conf
options hid_xpadneo quirks=E4:17:D8:37:B0:2D:22 ff_connect_notify=0
```

**Replace `E4:17:D8:37:B0:2D` with the controller's current Bluetooth address.** This is an example from one tested device, **not a universal address**. The same controller was previously observed as `E4:17:D8:37:B0:2C`, so do not assume an address stays unchanged. Check the `xpadneo` kernel log line ending in `[address] connected` and match that address exactly. The trailing `22` is decimal (`0x16` hexadecimal), and `ff_connect_notify=0` disables the connection notification vibration.

Reboot after changing module options. Avoid unloading `hid_xpadneo` while the controller is connected. Confirm the active override with:

```sh
modprobe -c | grep '^options hid_xpadneo'
sudo dmesg | grep -iE 'xpadneo|controller quirks|rumble streaming'
```

The expected controller log includes `controller quirks: 0x00000016` and `rumble streaming enabled`. If it still reports `0x00000017`, verify the Bluetooth address and whether another configuration overrides the option. This is a **device/driver-specific workaround**, not an upstream xpadneo fix or a guarantee for all firmware and driver versions.

## Dongle update-mode transition

During the dongle's switch to bootloader, Linux may return `EIO`, `ENODEV` or `ESHUTDOWN` because the USB device disconnects before acknowledging the request. These errors are tolerated **only for that transition request**. The flash procedure must still rediscover the bootloader, check its identity (`0x301c`) and perform the existing pre-write safeguards.

To verify mode switching without erasing or writing firmware, turn off the controller, leave only the dongle attached, close the web UI and run:

```sh
.venv/bin/python -m u2c_customfw dongle-mode-test
.venv/bin/python -m u2c_customfw devices
```

The first command restarts the dongle into bootloader and back; it is **not** a flash operation. Firmware updates remain inherently risky. Back up the original firmware and read [SAFEGUARDS.md](../SAFEGUARDS.md) before writing.

## Hardware validation

A contributor tested controller and dongle detection, firmware backup and flashing, bootloader transitions, controller inputs and rumble over USB and 2.4 GHz on Linux. Dongle flashing also succeeded with the `usb_oc` module loaded. This is contributor-reported validation, not a guarantee of compatibility across distributions or devices. macOS regression testing is still required.
