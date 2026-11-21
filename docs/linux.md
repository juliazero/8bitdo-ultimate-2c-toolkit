# Linux support

The Linux port shares the firmware, backup, restore and web application logic with macOS. Linux-specific transport uses `hidraw` and, for the Xbox-compatible wired controller (`045e:028e`), PyUSB/libusb on USB interface 2. Diagnostics use `evdev` for gamepad input and force feedback.

## Requirements

- Linux with `hidraw`, `evdev`, `udev` and access to the USB device
- Python 3.12 or later
- `pyusb` and `evdev` (installed from `pyproject.toml` on Linux)
- libusb runtime for PyUSB

## Installation

From the repository root:

```sh
./linux/install.sh
```

If the installer doesn't run (a copy without the executable bit), use `bash linux/install.sh`. The installer creates `.venv`, installs the Python package and installs `udev/70-8bitdo-ultimate-2c.rules` using `sudo`. Unplug and reconnect the controller and dongle after installation. `TAG+="uaccess"` grants permissions to an active local desktop session with systemd-logind; headless setups may need custom permissions. **Do not run the toolkit as root.**

Manual installation is also possible:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m u2c_customfw devices
.venv/bin/python -m u2c_customfw.web
```

The last command opens the app in your browser. If it doesn't, open the address it prints, including `?token=…`: without the token the page loads but can't reach the controller ("forbidden").

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
