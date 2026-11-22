# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline-only USB report layout compatibility; no device firmware is flashed."""
import hashlib
import importlib.util
import unittest
from pathlib import Path

from u2c_customfw.firmware import image, recipes
from u2c_customfw.ops import dongle
from tests.test_firmware import synthetic
from tools.build_ring_hold import bl, bl_target
from tools.build_usb_steam_layout import ENTRY, HOOK, MAP, MAP_LENGTH, reorder_descriptor
from tools.check_bt_modes import reports

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tc32_steam_usb', ROOT / 'firmware-src/test_longrumble.py')
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)


class CPU(tc.Controller):
    def wr8(self, addr, value):
        if not (0x44444444 <= addr < 0x4444444d or tc.SP0 - 0x100 <= addr < tc.SP0 or tc.CONNECTED <= addr < tc.CONNECTED + 4):
            raise AssertionError(f'Unexpected USB Steam patch write at {addr:#x}')
        self.ram[addr] = value & 255


class USBReportCPU(tc.Controller):
    """Execute the stock USB DInput packer and capture writes to its actual FIFO."""
    FIFO = 0x80011C

    def __init__(self, bank):
        super().__init__(bank)
        self.fifo = []
        self.max_steps = 12000

    def wr8(self, address, value):
        if address < 0x800000:
            raise AssertionError(f"Unexpected flash modification {address:#x}")
        if address == self.FIFO:
            self.fifo.append(value & 0xff)
        self.ram[address] = value & 0xff

    def trap(self, pc, registers):
        if pc == 0x103F8:  # Original memset: used to zero the stack frame.
            for i in range(registers[2]):
                self.wr8(registers[0] + i, registers[1])
            return True
        return super().trap(pc, registers)


class SteamUSBTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        full = Path('/mnt/data/controller-stock-analysis.bin')
        cls.k = {}
        if full.exists():
            data = full.read_bytes()
            stock = data[image.BASE:image.BASE + image.BANK_A_SIZE]
            if hashlib.sha256(stock).hexdigest() == image.STOCK_SHA256:
                cls.stock = stock
        if not hasattr(cls, 'stock'):
            # Portable, non-vendor synthetic image with the known original
            # 145-byte DInput descriptor reconstructed from the patch blob.
            random_stock, _ = synthetic()
            candidate = bytearray(random_stock)
            candidate[MAP-image.BASE:MAP-image.BASE+MAP_LENGTH] = dongle.D_STOCK_MAP
            cls.stock = bytes(candidate)
            base = recipes.whole(cls.stock)
            cls.k = dict(
                stock_sha256=hashlib.sha256(cls.stock).hexdigest(),
                region_sha256={(e.addr,e.size): hashlib.sha256(base[e.addr-image.BASE:e.addr-image.BASE+e.size]).hexdigest()
                               for r in recipes.RECIPES.values() for e in r.edits},
                remainder_sha256=hashlib.sha256(recipes.masked(base)).hexdigest())
        cls.installed = recipes.build(cls.stock, ['ring_normal', 'usb_dinput', 'usb_dinput_steam'], **cls.k)

    def test_stock_and_new_report_maps(self):
        old = self.stock[MAP-image.BASE:MAP-image.BASE+MAP_LENGTH]
        new = self.installed[MAP-image.BASE:MAP-image.BASE+MAP_LENGTH]
        self.assertEqual(new, reorder_descriptor(old))
        self.assertEqual(reports(old), reports(new))
        self.assertEqual(new[-53:], old[-53:])  # LED and vendor report definitions unchanged
        self.assertEqual(new[0:8], old[0:8])
        self.assertEqual(new[8:31], old[32:55])  # hat first
        self.assertEqual(new[31:51], old[55:75])  # four stick axes
        self.assertEqual(new[51:68], old[75:92])  # analog triggers
        self.assertEqual(new[68:92], old[8:32])  # independent buttons, incl. L4/R4
        self.assertEqual(new[124:145].count(bytes.fromhex('85 81')), 1)

    def test_hook_and_reversible_recipe(self):
        keys = ['ring_normal', 'usb_dinput', 'usb_dinput_steam']
        self.assertEqual(set(recipes.identify(self.installed, **self.k).recipes), set(keys))
        self.assertEqual(self.installed[0x17afe-image.BASE:0x17b00-image.BASE], bytes.fromhex('1b30'))
        self.assertEqual(bl_target(self.installed[HOOK-image.BASE:HOOK-image.BASE+4],HOOK),ENTRY)
        self.assertEqual(self.installed[ENTRY-image.BASE:ENTRY-image.BASE+44], (recipes.BLOBS/'usbsteam.bin').read_bytes())
        self.assertEqual(set(recipes.identify(recipes.build(self.stock, ['ring_normal','usb_dinput'], **self.k), **self.k).recipes),
                         {'ring_normal', 'usb_dinput'})
        with self.assertRaises(recipes.RecipeError):
            recipes.build(self.stock, ['usb_dinput_steam'], **self.k)

    def test_all_bytes_reordered_exactly_and_callee_saved(self):
        for buttons in (0, 1, 4, 32, 128, 0xffff):
            for hat in (0, 1, 4, 7, 15):
                old = bytes([buttons & 255, (buttons >> 8) & 255, hat, 11, 22, 33, 44, 55, 66])
                c = CPU(self.installed)
                for i, value in enumerate(old):
                    c.ram[0x44444444 + i] = value
                c.call(ENTRY)
                actual = bytes(c.rd8(0x44444444 + i) for i in range(9))
                self.assertEqual(actual, old[2:9]+old[:2])

    def test_stock_usb_dinput_transmit_fifo_callsite(self):
        """Regression: hook must affect 0xEDE8's USB FIFO, not an unrelated routine."""
        # Stock USB DInput calls 0xEDE8 from its mode-1 report task at 0xF1CC.
        # The FIFO stream includes report ID 0x01 before the nine payload bytes.
        for args in ((0, 0, 0, 0), (0xffffffff, 0x12345678, 0xaabbccdd, 0xa5a5a5a5),
                     (0x00020001, 0, 0, 0)):
            original = USBReportCPU(recipes.build(self.stock, ['ring_normal', 'usb_dinput'], **self.k))
            patched = USBReportCPU(self.installed)
            original.call(0xEDE8, *args)
            patched.call(0xEDE8, *args)
            self.assertEqual(len(original.fifo), 10)
            self.assertEqual(len(patched.fifo), 10)
            self.assertEqual(original.fifo[0], 1)  # input report ID, unchanged
            self.assertEqual(patched.fifo[0], 1)
            self.assertEqual(bytes(patched.fifo[1:]), bytes(original.fifo[3:]+original.fifo[1:3]))

    def test_does_not_modify_xinput_or_rf_battery(self):
        usb = recipes.build(self.stock,['ring_normal','usb_dinput'], **self.k)
        # Only USB DInput HID descriptor, USB DInput packet hook and added code differ;
        # RF battery / XInput and vendor report handlers remain byte-for-byte untouched.
        changed = {0x17afe, *range(MAP,MAP+MAP_LENGTH), *range(HOOK,HOOK+4), *range(ENTRY,ENTRY+44)}
        diffs = {image.BASE+i for i,(a,b) in enumerate(zip(usb,self.installed)) if a!=b}
        self.assertTrue(diffs <= changed)
        self.assertEqual(self.installed[0x9964-image.BASE:0x9cac-image.BASE],usb[0x9964-image.BASE:0x9cac-image.BASE])
        self.assertEqual(self.installed[0xf83c-image.BASE:0xf846-image.BASE],usb[0xf83c-image.BASE:0xf846-image.BASE])

if __name__ == '__main__':unittest.main()
