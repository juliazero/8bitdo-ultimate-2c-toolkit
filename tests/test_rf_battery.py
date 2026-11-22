# SPDX-License-Identifier: GPL-3.0-or-later
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from u2c_customfw.firmware import image, recipes
from u2c_customfw.protocol.rf_battery import parse
from u2c_customfw.ops.rf_battery import read
from tests.test_firmware import synthetic


class RFProtocolTest(unittest.TestCase):
    @staticmethod
    def sample(percent=77, mv=3912, flags=2, sequence=8):
        pkt = bytearray.fromhex('8f6f4200000000ff7fff7fff7fff7f0000383807')
        pkt += bytearray([0xa5,0x5a,percent,mv&255,mv>>8,flags,sequence,0])
        checksum = 0xc3
        for n in pkt[20:27]:checksum ^= n
        pkt[27] = checksum
        pkt += bytes.fromhex('723d5226')
        return bytes(pkt)

    def test_decode(self):
        self.assertEqual(parse(self.sample()), dict(percent=77, mv=3912, charging=True, full=False, sequence=8))
        for pct in (0,1,99,100):
            self.assertEqual(parse(self.sample(percent=pct))['percent'],pct)

    def test_rejects_stock_packets_and_corrupt_data(self):
        for name in ('rf-battery-normal.jsonl', 'rf-battery-charging.jsonl'):
            path = Path('/mnt/data') / name
            if path.exists():
                for line in path.read_text().splitlines():
                    self.assertIsNone(parse(bytes.fromhex(json.loads(line)['hex'])))
        # Known user-captured stock RF packet, without a trailer.
        self.assertIsNone(parse(bytes.fromhex('8f6f4200000000ff7fff7fff7fff7f00003838070000000000000000723d5226')))
        frame = self.sample()
        for index, value in ((0, 0x7f), (20, 0x01), (22, 101), (25, 0x80), (27, 0x00)):
            broken = bytearray(frame);broken[index] = value
            self.assertIsNone(parse(broken), (index, value))

    def test_dongle_read_uses_only_read_command(self):
        data = self.sample()
        with patch('u2c_customfw.ops.rf_battery.app.read',return_value=data) as request:
            self.assertEqual(read(object())['percent'],77)
            self.assertEqual(request.call_args.args[1:3],(0x843108,32))


class RFExecutableTest(unittest.TestCase):
    def test_compiled_tc32_hook_preserves_original_input(self):
        from tools.check_rf_battery import check
        # Use synthetic bank only for the exact patch region, no hardware access.
        # Execute the compiled instructions with a real stock image when present.
        path = Path('/mnt/data/research/8bitdo-ultimate-2c-toolkit/build/stock-analysis.bin')
        if not path.exists():
            self.skipTest('Verified controller firmware image not provided')
        original = path.read_bytes()
        self.assertTrue(check(original[image.BASE:image.BASE+image.BANK_A_SIZE])['stock_report_bytes_unchanged'])


class RFRecipeTest(unittest.TestCase):
    def test_compatible_tweaks(self):
        stock, hashes = synthetic()
        for keys in (['rf_battery_tx'], ['rf_battery_tx','battery_table'],
                     ['rf_battery_tx','bt_xbox','bt_modes'],
                     ['rf_battery_tx','bt_xbox','bt_modes','ring_normal', 'dim_leds','settings','cpu_rest','bt_switch2']):
            frame = recipes.build(stock,keys,**hashes)
            self.assertEqual(set(recipes.identify(frame,**hashes).recipes),set(keys))
            for at, original, expected in ((0xdd82,0x15,0x1d),(0xdd86,0x14,0x1c),
                                           (0xdd92,0x17,0x1f),(0xdd96,0x16,0x1e),
                                           (0xddc0,0x14,0x1c)):
                self.assertEqual(frame[at-image.BASE],expected)
            self.assertEqual(frame[0x19a00-image.BASE:0x19a08-image.BASE],recipes._blob('rf-battery.bin')[:8])


if __name__ == '__main__':unittest.main()
