# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only capture of 8BitDo 2.4 GHz dongle HID input reports on Windows.

This does not flash or send commands to either the controller or dongle.
Only the dongle's DInput HID interface is readable with standard HIDAPI;
its XInput/XUSB interface need not appear here. Captured reports are not
necessarily raw 2.4 GHz radio packets and cannot establish battery telemetry.
"""
import argparse
import ctypes
import json
import sys
import time
from pathlib import Path


def collect(hid, duration, output, *, diff_only=False, interval=0.01):
    candidates = [d for d in hid.enumerate(0x2DC8, 0x301C)
                  if d.get('usage_page') == 1 and d.get('usage') == 5]
    if len(candidates) != 1:
        raise RuntimeError(f'Expected one dongle DInput gamepad HID interface; found {len(candidates)}. '
                           'The XInput mode may not expose this interface.')
    device = hid.device()
    device.open_path(candidates[0]['path'])
    device.set_nonblocking(True)
    previous = None
    count = 0
    start = time.monotonic()
    try:
        with output.open('w', encoding='utf-8') as stream:
            while time.monotonic() - start < duration:
                packet = bytes(device.read(64))
                if packet and (not diff_only or packet != previous):
                    record = {'elapsed_ms': round((time.monotonic() - start) * 1000),
                              'report_id': packet[0], 'length': len(packet), 'hex': packet.hex(' ')}
                    stream.write(json.dumps(record) + '\n')
                    count += 1
                if packet:
                    previous = packet
                else:
                    time.sleep(interval)
    finally:
        device.close()
    return count


def xinput_battery():
    """Ask Windows XInput for battery state, without sending vendor commands.

    XInput often reports third-party dongles as wired or disconnected; that is
    not a valid reading of the actual controller battery.
    """
    if sys.platform != 'win32':
        raise RuntimeError('XInput battery probing is Windows-only.')

    class Battery(ctypes.Structure):
        _fields_ = [('kind', ctypes.c_ubyte), ('level', ctypes.c_ubyte)]

    for library in ('xinput1_4.dll', 'xinput1_3.dll', 'xinput9_1_0.dll'):
        try:
            dll = ctypes.WinDLL(library)
            method = dll.XInputGetBatteryInformation
            method.argtypes = [ctypes.c_uint32, ctypes.c_ubyte, ctypes.POINTER(Battery)]
            method.restype = ctypes.c_uint32
            break
        except (OSError, AttributeError):
            continue
    else:
        raise RuntimeError('Windows XInputGetBatteryInformation is unavailable.')
    result = []
    for user in range(4):
        battery = Battery()
        status = method(user, 0, ctypes.byref(battery))  # BATTERY_DEVTYPE_GAMEPAD
        if status == 0:
            kinds = {0: 'disconnected', 1: 'wired/no battery information', 2: 'alkaline', 3: 'NiMH', 255: 'unknown'}
            levels = {0: 'empty', 1: 'low', 2: 'medium', 3: 'full'}
            result.append({'user': user, 'type': kinds.get(battery.kind, str(battery.kind)),
                           'level': levels.get(battery.level, str(battery.level)),
                           'reliable_percentage': False})
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=float, default=30)
    p.add_argument('--output', type=Path, default=Path('dongle-reports.jsonl'))
    p.add_argument('--all', action='store_true', help='Record repeated reports too (default: changes only)')
    p.add_argument('--xinput-battery', action='store_true', help='Query Windows XInput battery state (not a percentage)')
    args = p.parse_args(argv)
    if args.xinput_battery:
        try:
            print(json.dumps(xinput_battery(), indent=2))
            return 0
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if not 0 < args.seconds <= 3600:
        p.error('--seconds must be between 0 and 3600')
    try:
        from u2c_customfw.transport.windows import _hid
        n = collect(_hid(), args.seconds, args.output, diff_only=not args.all)
    except (OSError, RuntimeError, ImportError) as exc:
        print(f'Dongle report capture failed: {exc}', file=sys.stderr)
        return 1
    print(f'Saved {n} reports to {args.output}. No firmware or device settings were changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
