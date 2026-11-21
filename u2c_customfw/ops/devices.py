# SPDX-License-Identifier: GPL-3.0-or-later
"""Find the controller (or dongle) in whatever mode it is in."""
from dataclasses import dataclass
import sys

from .. import transport
from ..transport import DeviceInfo

# (vendor, product, usage page or None) -> mode. The update channel of the running controller is vendor page 0xff7a.
MODES = (
    (0x2DC8, 0x310A, 0xFF7A, "cable"),
    (0x045E, 0x028E, 0xFF7A, "cable_360"),
    (0x2DC8, 0x3208, None, "boot"),
    (0x2DC8, 0x301C, 0xFFA0, "dongle"),
    (0x2DC8, 0x301C, 0xFF7A, "dongle_paired"),
    (0x045E, 0x0B13, None, "bluetooth_xbox"),
    (0x2DC8, 0x301B, None, "bluetooth"),
)


DONGLE_BCD = 0x0103   # the patched dongle's bcdDevice; the controller by cable keeps 0x0114, so the same 045e:028e tells them apart
REAL_XBOX = "Microsoft"   # a real Xbox controller's manufacturer: it has the 2C's Xbox IDs over Bluetooth, and isn't ours

XINPUT_NOTE = ("Windows detects the USB Xbox 360 input interface, but its driver does not expose the firmware update channel. "
               "Input and vibration can still be tested in Diagnostics. To back up, read or change the controller firmware, "
               "unplug the cable, hold Home for about 12 seconds to turn it fully off, then hold LB + RB while plugging in the cable. "
               "The small light below the Home ring will blink white (update mode). "
               "If this is the dongle, turn the controller off and reconnect the dongle on its own.")


@dataclass
class Found:
    info: DeviceInfo
    mode: str


def find() -> list[Found]:
    """Every controller-like device found (two units are two entries); a Bluetooth pad shows several HID interfaces
    and is listed once. Other HID devices are ignored."""
    out: list[Found] = []
    listing = transport.list_devices()
    for d in listing:
        # USB D-input: 301b on the battery-equipped model, 301d on the batteryless variant. Its gamepad
        # collection contains input 02 / output 81 for the update channel.
        if d.vendor == 0x2DC8 and d.product in (0x301B, 0x301D) and d.usage_page == 1 and d.transport == 'USB':
            out.append(Found(d, 'cable_dinput'))
            continue
        # In D-input Windows exposes reports 02/81 within the gamepad collection.
        # Treat it as paired: firmware reads are allowed, flashing requires idle.
        if (sys.platform == "win32" and (d.vendor, d.product, d.usage_page, d.usage) == (0x2DC8, 0x301C, 1, 5)
                and d.transport == "USB" and not any(
                    other.vendor == d.vendor and other.product == d.product and other.usage_page in (0xFFA0, 0xFF7A)
                    for other in listing)):
            out.append(Found(d, "dongle_paired"))
            continue
        if (sys.platform == "win32" and (d.vendor, d.product, d.usage_page) == (0x045E, 0x028E, 1)
                and d.transport == "USB" and not any(
                    (other.vendor, other.product, other.version, other.usage_page) ==
                    (d.vendor, d.product, d.version, 0xFF7A) for other in listing)):
            # XUSB synthesizes a gamepad collection but hides the vendor interface.
            # Never treat this input handle as a firmware channel.
            out.append(Found(d, "xinput_only"))
            continue
        for vendor, product, page, mode in MODES:
            if d.vendor == vendor and d.product == product and (page is None or d.usage_page == page):
                if mode.startswith("bluetooth"):
                    if "Bluetooth" not in d.transport or any(f.mode == mode for f in out) or d.manufacturer == REAL_XBOX:
                        continue
                out.append(Found(d, "dongle_paired" if mode == "cable_360" and d.version == DONGLE_BCD else mode))
    return out
