# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental *read-only* dongle RF-buffer snapshots via the existing 81 05 05 channel.

The dongle firmware's 1.03 mode selector reads the first byte of the last RF
packet from 0x843108 (firmware-src/dongle.s). The *stock* 81 05 05 command
reads flash, NOT RAM: use the optional dongle_rf_read tweak in Firmware Toolkit
before using this tool. The packet battery layout is still UNKNOWN. This probe
never flashes, changes settings, or issues a session/unprotect command. Use
with exactly one dongle paired in DInput mode.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from u2c_customfw import transport
from u2c_customfw.ops import devices
from u2c_customfw.protocol import app
from u2c_customfw.protocol.rf_battery import parse as parse_battery

RF_PACKET = 0x843108
FLASH_SIGNATURE = 0x10000
READ_SIZE = 32


def select_dongle(found):
    """Reject ambiguous devices and ordinary wired controllers."""
    candidates = [item for item in found if item.mode == 'dongle_paired']
    if len(candidates) != 1 or len([x for x in found if x.mode in ('dongle', 'dongle_paired')]) != 1:
        raise RuntimeError('Connect one paired 2.4 GHz dongle in DInput mode. Other modes may hide its vendor HID channel.')
    return candidates[0]


def capture(ch, *, seconds, interval, output, clock=time.monotonic, pause=time.sleep):
    """Read a known RF-buffer address. Fail closed if RAM reads are unsupported."""
    signature = app.read(ch, FLASH_SIGNATURE, 16, tries=1)
    if signature != b'\xff' * 16:
        raise RuntimeError('Dongle identity check failed: 0x10000 is not blank. No RAM reads attempted.')
    first = app.read(ch, RF_PACKET, READ_SIZE, tries=1)
    if first is None or len(first) != READ_SIZE:
        raise RuntimeError('The dongle did not answer a RAM read at 0x843108. This transport may only support flash reads.')
    count = 0
    start = clock()
    previous = None
    with output.open('w', encoding='utf-8') as stream:
        while True:
            elapsed = clock() - start
            if elapsed >= seconds:
                break
            packet = first if count == 0 else app.read(ch, RF_PACKET, READ_SIZE, tries=1)
            if packet is None or len(packet) != READ_SIZE:
                raise RuntimeError(f'RF buffer stopped responding after {count} snapshots; partial file: {output}')
            if packet != previous:
                entry = {'elapsed_ms': round(elapsed * 1000), 'address': f'0x{RF_PACKET:06x}',
                         'length': len(packet), 'hex': packet.hex(' ')}
                battery = parse_battery(packet)
                if battery is not None:
                    entry['battery'] = battery
                stream.write(json.dumps(entry) + '\n')
                count += 1
                previous = packet
            pause(interval)
    if previous == bytes([0xff]) * READ_SIZE and count == 1:
        raise RuntimeError("Only FF bytes were returned. Check that the dongle_rf_read firmware tweak is installed; no RF data was captured.")
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--interval', type=float, default=0.2)
    parser.add_argument('--output', type=Path, default=Path('dongle-rf-snapshots.jsonl'))
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 120 or not 0.05 <= args.interval <= 5:
        parser.error('Use --seconds in (0,120] and --interval between 0.05 and 5')
    try:
        found = select_dongle(devices.find())
        ch = transport.open_channel(found.info, 0x81)
        try:
            n = capture(ch, seconds=args.seconds, interval=args.interval, output=args.output)
        finally:
            ch.close()
    except (OSError, RuntimeError, ImportError, ValueError) as exc:
        print(f'RF read-only probe: {exc}', file=sys.stderr)
        return 1
    print(f'Saved {n} distinct snapshots to {args.output}. No firmware or settings were changed.')
    print('Valid battery trailers are included as battery fields. Absence means telemetry was not received.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
