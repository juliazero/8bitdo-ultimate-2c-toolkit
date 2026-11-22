# SPDX-License-Identifier: GPL-3.0-or-later
"""Instruction-level regression: Settings vendor commands must not disable USB mode switching.

Run the actual packaged TC32 recipe with a synthetic stock bank; trap only stock
peripheral functions. No controller or flash writes are performed by this test.
"""
import importlib.util
import unittest
from pathlib import Path

from tests.test_ops import STOCK_A, K
from tools import build_usb_mode_prototype as usb
from u2c_customfw.firmware import recipes

MODULE = Path(__file__).resolve().parents[1] / 'firmware-src/test_longrumble.py'
spec = importlib.util.spec_from_file_location('u2c_test_cpu', MODULE)
cpu_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu_module)


class USBModeCPU(cpu_module.Controller):
    def __init__(self, old=False):
        flash = bytearray(recipes.build(STOCK_A, ['ring_normal', 'usb_dinput'], **K))
        if old:
            legacy = (recipes.BLOBS / 'usbmode-pre-settings-switch-fix.bin').read_bytes()
            flash[0x9964-0x6000:0x9964-0x6000+len(legacy)] = legacy
        self.old = old
        super().__init__(bytes(flash))
        self.saves = []
        self.buzzes = []
        self.vendor_calls = []
        self.seed(usb.USB, 2, 1)
        self.seed(0x800518, 1, 1)
        self.seed(usb.MODE, 0, 1)
        self.seed(0x843915, 0, 1)

    def seed(self, addr, value, length=4):
        for i in range(length):
            self.ram[addr + i] = value >> (8*i) & 0xff

    def wr8(self, addr, value):
        allowed = (usb.STATE <= addr < usb.STATE+usb.STATE_SIZE
                   or addr in (usb.MODE, 0x843915, 0x800125, 0x800126)
                   or cpu_module.SP0-0x400 <= addr < cpu_module.SP0
                   or cpu_module.PACKET <= addr < cpu_module.PACKET+64
                   or cpu_module.CONNECTED <= addr < cpu_module.CONNECTED+4)
        if not allowed:
            raise AssertionError(f'unexpected write {addr:#x} from PC {getattr(self, "last_pc", None)}')
        self.ram[addr] = value & 255

    def trap(self, pc, registers):
        self.last_pc = pc
        if pc == 0xd024:
            return True
        if pc in (0xeffc, 0xefe4):
            self.seed(usb.MODE, 1 if pc == 0xeffc else 0, 1)
            return True
        if pc == 0xd538:
            self.saves.append(self.rd8(0x843915))
            return True
        if pc == 0xfa68:
            self.buzzes.append(tuple(registers[:3]))
            return True
        if 0x18400 <= pc < 0x18ff0:  # Existing Home ring/Settings vendor callback.
            self.vendor_calls.append(pc)
            return True
        return False

    def poll(self, ms, buttons, usb_state=2, power=1):
        self.seed(usb.TIMER, ms*16000 & 0xffffffff)
        self.seed(usb.RAW, buttons)
        self.seed(usb.USB, usb_state, 1)
        self.seed(0x800518, power, 1)
        self.call(0x9a70)

    def packet(self, ms, hexpacket, route='vendor_a'):
        packet = bytes.fromhex(hexpacket)
        self.seed(usb.TIMER, ms*16000 & 0xffffffff)
        for i, value in enumerate(packet):
            self.seed(cpu_module.PACKET+i, value, 1)
        _, _, symbols = usb.integrated(recipes.BLOBS)
        self.call((0x9b30 if route == 'vendor_a' else 0x9b4a) if self.old else symbols[route], cpu_module.PACKET)


class USBModeSettingsTest(unittest.TestCase):
    def test_old_firmware_hooks_and_code_are_recognized_for_upgrade(self):
        from u2c_customfw.firmware import image
        keys = ['ring_normal', 'settings', 'usb_dinput', 'usb_dinput_steam', 'cpu_rest', 'battery_log']
        old = bytearray(recipes.build(STOCK_A, keys, **K))
        for addr, name in ((0x9964, 'usbmode'), (0xA414, 'usbstart'),
                           (0xF83C, 'usbvendora'), (0xF842, 'usbvendorb')):
            data = (recipes.BLOBS / (name+'-pre-settings-switch-fix.bin')).read_bytes()
            old[addr-image.BASE:addr-image.BASE+len(data)] = data
        state = recipes.identify(bytes(old), **K)
        self.assertTrue(state.known)
        self.assertTrue(state.older)
        self.assertEqual(set(state.recipes), set(keys))

    def test_settings_reload_is_the_original_switch_blocker(self):
        old = USBModeCPU(old=True)
        old.packet(0, '81 05 00 5a')
        old.poll(2000, usb.HOME | usb.B)
        old.poll(6000, usb.HOME | usb.B)
        self.assertEqual(old.rd8(usb.MODE), 0)
        self.assertTrue(old.rd(usb.STATE+16, 4) & 1)

    def test_settings_commands_allow_both_directions_and_one_buzz_each(self):
        for route in ('vendor_a', 'vendor_b'):
            cpu = USBModeCPU()
            for command in ('81 05 00 5a', '81 05 00 54 00 0e',
                            '81 05 00 55', '81 05 00 56', '81 05 00 57 00 00',
                            '81 05 00 58 01', '81 05 00 52 02', '81 05 00 53'):
                cpu.packet(0, command, route)
                self.assertEqual(cpu.rd(usb.STATE+16, 4) & 1, 0, command)
            cpu.poll(2000, usb.HOME | usb.B)
            cpu.poll(5000, usb.HOME | usb.B)
            self.assertEqual(cpu.rd8(usb.MODE), 1)
            self.assertFalse(cpu.buzzes, 'do not buzz at the instant the old USB identity is detached')
            cpu.poll(5100, 0, usb_state=0)
            self.assertFalse(cpu.buzzes, 'do not buzz while USB is unconfigured')
            cpu.poll(5200, 0, usb_state=2)
            self.assertEqual(cpu.buzzes, [(70, 70, 12)], 'buzz once after USB is configured again')
            cpu.poll(5400, 0)
            self.assertEqual(cpu.buzzes, [(70, 70, 12), (0, 0, 1)])
            cpu.poll(5500, usb.HOME | usb.X)
            cpu.poll(8500, usb.HOME | usb.X)
            self.assertEqual(cpu.rd8(usb.MODE), 0)
            self.assertEqual(cpu.buzzes, [(70, 70, 12), (0, 0, 1)])
            cpu.poll(8600, 0, usb_state=0)
            cpu.poll(8700, 0, usb_state=2)
            self.assertEqual(cpu.buzzes, [(70, 70, 12), (0, 0, 1), (70, 70, 12)])
            cpu.poll(9000, usb.HOME | usb.X)
            cpu.poll(12000, usb.HOME | usb.X)
            self.assertEqual(cpu.buzzes, [(70, 70, 12), (0, 0, 1), (70, 70, 12), (0, 0, 1)],
                             'same-mode gesture must not buzz')
            self.assertEqual(len(cpu.saves), 2)

    def test_no_buzz_before_configuration_and_timer_fallback_if_disconnect_is_unobserved(self):
        cpu = USBModeCPU()
        cpu.poll(1000, usb.HOME | usb.B)
        cpu.poll(4000, usb.HOME | usb.B)
        self.assertEqual(cpu.rd8(usb.MODE), 1)
        self.assertFalse(cpu.buzzes)
        # A USB detach/reconnect can occur entirely inside the 600-ms stock
        # setter delay, between the 100-ms application-level polls.
        cpu.poll(4500, 0, usb_state=2)
        self.assertFalse(cpu.buzzes)
        cpu.poll(5000, 0, usb_state=0)
        self.assertFalse(cpu.buzzes)
        cpu.poll(5100, 0, usb_state=2)
        self.assertEqual(cpu.buzzes, [(70, 70, 12)])
        cpu.poll(5800, 0)
        self.assertEqual(cpu.buzzes, [(70, 70, 12), (0, 0, 1)])

        missed = USBModeCPU()
        missed.poll(1000, usb.HOME | usb.B)
        missed.poll(4000, usb.HOME | usb.B)
        missed.poll(4400, 0, usb_state=2)
        self.assertFalse(missed.buzzes)
        missed.poll(5000, 0, usb_state=2)
        self.assertEqual(missed.buzzes, [(70, 70, 12)])
        missed.poll(6000, 0, usb_state=2)
        self.assertEqual(missed.buzzes, [(70, 70, 12), (0, 0, 1)])

    def test_previous_pre_haptic_firmware_is_recognized_as_older(self):
        from u2c_customfw.firmware import image
        old = bytearray(recipes.build(STOCK_A, ['ring_normal', 'usb_dinput'], **K))
        for address, stem in ((0x9964, 'usbmode'), (0xE414, 'usbread'),
                              (0xA414, 'usbstart'), (0xF83C, 'usbvendora'),
                              (0xF842, 'usbvendorb')):
            blob = (recipes.BLOBS / (stem + '-pre-haptic-ready.bin')).read_bytes()
            old[address-image.BASE:address-image.BASE+len(blob)] = blob
        patch = recipes.BLOBS / 'usb-haptic-ready.bin'
        size = patch.stat().st_size
        old[usb.HAPTIC_ENTRY-image.BASE:usb.HAPTIC_ENTRY-image.BASE+size] = b'\xff' * size
        state = recipes.identify(bytes(old), **K)
        self.assertTrue(state.known)
        self.assertTrue(state.older)
        self.assertEqual(set(state.recipes), {'ring_normal', 'usb_dinput'})

    def test_usb_haptic_explicitly_stops_after_200ms_without_motor_tick(self):
        cpu = USBModeCPU()
        cpu.poll(1000, usb.HOME | usb.B)
        cpu.poll(4000, usb.HOME | usb.B)
        cpu.poll(4100, 0, usb_state=0)
        cpu.poll(4200, 0, usb_state=2)
        self.assertEqual(cpu.buzzes, [(70, 70, 12)])
        cpu.poll(4300, 0)
        self.assertEqual(cpu.buzzes, [(70, 70, 12)])
        cpu.poll(4400, 0)
        self.assertEqual(cpu.buzzes, [(70, 70, 12), (0, 0, 1)],
                         'must command the motors OFF, not rely on stock motor ticks')
        cpu.poll(4600, 0)
        self.assertEqual(len(cpu.buzzes), 2, 'stop must be a one-shot')

    def test_previous_unbounded_usb_haptic_can_upgrade(self):
        from u2c_customfw.firmware import image
        old = bytearray(recipes.build(STOCK_A, ['ring_normal', 'usb_dinput'], **K))
        previous = (recipes.BLOBS / 'usb-haptic-ready-prev.bin').read_bytes()
        address = usb.HAPTIC_ENTRY - image.BASE
        old[address:address + len(previous)] = previous
        old[address + len(previous):address + len(recipes.BLOBS.joinpath('usb-haptic-ready.bin').read_bytes())] = b'\xff' * (len(recipes.BLOBS.joinpath('usb-haptic-ready.bin').read_bytes()) - len(previous))
        state = recipes.identify(bytes(old), **K)
        self.assertTrue(state.known)
        self.assertTrue(state.older)
        self.assertEqual(set(state.recipes), {'ring_normal','usb_dinput'})

    def test_update_commands_still_lock(self):
        for command in ('81 05 c1', '81 05 00 51 00', '81 05 00 60'):
            cpu = USBModeCPU()
            cpu.packet(0, command)
            self.assertTrue(cpu.rd(usb.STATE+16, 4)&1, command)
            cpu.poll(2000, usb.HOME|usb.B)
            cpu.poll(6000, usb.HOME|usb.B)
            self.assertEqual(cpu.rd8(usb.MODE), 0, command)
            self.assertFalse(cpu.buzzes)

    def test_not_usb_or_power_off_never_buzzes(self):
        for power, usb_state in ((0, 2), (1, 0)):
            cpu = USBModeCPU()
            cpu.poll(0, usb.HOME|usb.B, usb_state, power)
            cpu.poll(4000, usb.HOME|usb.B, usb_state, power)
            self.assertEqual(cpu.rd8(usb.MODE), 0)
            self.assertFalse(cpu.buzzes)


if __name__ == '__main__':
    unittest.main()
