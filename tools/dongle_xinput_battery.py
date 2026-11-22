# SPDX-License-Identifier: GPL-3.0-or-later
"""Read one RF-battery sample via the existing Windows XInput dongle HID MI_02.

Does not flash firmware or alter the XUSB driver. The stock XInput dongle
shares its 2DC8:310A USB identity with the wired controller, so this tool
requires a vendor usage page and MI_02 and confirms the dongle's blank flash
signature before asking for the optional RF battery trailer.

    python -m tools.dongle_xinput_battery
"""
import json
import sys

from u2c_customfw import transport
from u2c_customfw.ops import probe
from u2c_customfw.ops.plan import OpError
from u2c_customfw.protocol import ProtocolError
from u2c_customfw.transport import PermissionDenied
from u2c_customfw.ops.rf_battery import read as read_rf_battery


def identify(items):
    """Find an unambiguous Windows vendor HID interface, never an XUSB input."""
    candidates = [d for d in items if d.vendor == 0x2DC8 and d.product == 0x310A
                  and d.transport == "USB" and d.usage_page == 0xFF7A
                  and "&MI_02" in (d.handle.decode(errors="replace") if isinstance(d.handle, bytes) else str(d.handle)).upper()]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one 2DC8:310A MI_02 vendor HID interface; found {len(candidates)}.")
    return candidates[0]


def read_one(found, open_channel):
    dev = identify(found)
    ch = open_channel(dev, 0x81)
    try:
        try:
            probe.check_not_dongle(ch)
        except probe.PairedDongle:
            return read_rf_battery(ch)
        raise RuntimeError("The device is a wired controller, not the 2.4 GHz dongle. Nothing was changed.")
    finally:
        ch.close()


def main():
    if sys.platform != 'win32':
        print('This diagnostic requires Windows.', file=sys.stderr)
        return 1
    try:
        result = read_one(transport.list_devices(), transport.open_channel)
    except (OSError, RuntimeError, ValueError, OpError, PermissionDenied, ProtocolError) as exc:
        print(f'XInput dongle battery read: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'source': 'dongle', **(result or {'unavailable': True})}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
