# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the real TC32 RF diagnostic wrapper against a simulated flash/RAM map."""
import unittest
import os
from pathlib import Path

import importlib.util
_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('tc32_interpreter', _ROOT/'firmware-src'/'test_longrumble.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
Controller = _module.Controller
from u2c_customfw.ops import dongle


class RFCPU(Controller):
    OUT = 0x844000
    def __init__(self, img):
        self.rf = bytes(range(32))
        self.flash_calls = []
        super().__init__(img)
        for i, v in enumerate(self.rf):
            self.ram[0x843108+i] = v

    def wr8(self, addr, value):
        if self.OUT <= addr < self.OUT+64:
            self.ram[addr] = value & 255
        else:
            super().wr8(addr, value)

    def trap(self, pc, regs):
        if pc == 0xcd94:  # black-box boundary; original is the verified flash-read shim
            self.flash_calls.append((regs[0], regs[1], regs[2]))
            for i in range(regs[2]):
                self.wr8(regs[1]+i, (regs[0]+i) & 255)
            regs[0] = 1
            return True
        return False


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        backup = Path(os.getenv('U2C_DONGLE_TEST_BACKUP', '/nonexistent-u2c-dongle-test-backup.bin'))
        if not backup.is_file():
            raise unittest.SkipTest('Offline backup required for full instruction test')
        raw = backup.read_bytes()[dongle.BASE:dongle.END]
        if dongle.state(raw) != (True, ('dongle_dinput',)):
            raise unittest.SkipTest('Verified DInput 1.03 backup required')
        cls.stock = dongle.target(raw, [])

    def make(self):
        bank = dongle.target(self.stock, ['dongle_dinput', 'dongle_rf_read'])
        return RFCPU(bank)

    def test_ram_copy_and_zero_length(self):
        cpu = self.make()
        self.assertEqual(cpu.call(0xff88, 0x843108, cpu.OUT, 32), 1)
        self.assertEqual(bytes(cpu.rd8(cpu.OUT+i) for i in range(32)), bytes(range(32)))
        self.assertEqual(cpu.flash_calls, [])
        self.assertEqual(cpu.call(0xff88, 0x843108, cpu.OUT, 0), 1)

    def test_other_address_and_large_reads_still_use_flash(self):
        cpu = self.make()
        for addr, size in ((0x6000, 32), (0x843109, 32), (0x843108, 33), (0x10000, 16)):
            self.assertEqual(cpu.call(0xff88, addr, cpu.OUT, size), 1)
            self.assertEqual(cpu.flash_calls[-1], (addr, cpu.OUT, size))

    def test_hook_instruction(self):
        cpu = self.make()
        from tools.build_ring_hold import bl_target
        word = bytes(cpu.rd8(0xd598+i) for i in range(4))
        self.assertEqual(bl_target(word, 0xd598), 0xff88)

    def test_old_code_and_new_tweak_roundtrip(self):
        keys = ('dongle_dinput', 'dongle_rf_read')
        bank = dongle.target(self.stock, keys)
        self.assertEqual(dongle.state(bank), (True, keys))
        self.assertEqual(dongle.target(bank, ['dongle_dinput']), dongle.target(self.stock, ['dongle_dinput']))
        self.assertEqual(dongle.target(bank, []), self.stock)
