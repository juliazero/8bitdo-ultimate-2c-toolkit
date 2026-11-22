# SPDX-License-Identifier: GPL-3.0-or-later
"""Run stock and tweaked dongle's actual gamepad report builder in TC32 VM."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from u2c_customfw.ops import dongle, devices
from u2c_customfw.ops.plan import OpError
from u2c_customfw.transport import DeviceInfo
from tools.build_ring_hold import bl_target
from tools.build_dongle_steam_layout import ENTRY, HOOK, RUNTIME_HOOK, MAP, MAP_LENGTH, build

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dongle_steam_tc32',ROOT/'firmware-src/test_longrumble.py')
tc=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

class ReportCPU(tc.Controller):
    def rd8(self, a):
        # The first part of the 0x6000-based application is copied into
        # executable RAM starting at zero. Execute that RAM copy, rather
        # than its flash-image address (which hid the broken BL).
        if 0 <= a < 0x3000:
            return self.img[a]
        return super().rd8(a)

    def __init__(self, bank):
        super().__init__(bank)
        self.sent=[]

    def wr8(self, a, v):
        if 0x843000 <= a < 0x845000 or tc.SP0-0x400 <= a < tc.SP0:
            self.ram[a]=v&255
        else: super().wr8(a,v)

    def trap(self, pc, r):
        if pc==0x12bb0:
            for i in range(r[2]):self.wr8(r[0]+i,r[1])
            return True
        if pc in (0x1288, 0x7288):
            self.sent.append((r[2],bytes(self.rd8(r[0]+i) for i in range(r[1]))))
            return True
        return super().trap(pc,r)

class DongleSteamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This is the exact vendor bank restored from the provided 1.03 backup.
        # Independent tests use recipe metadata and do not depend on this file.
        path=Path('/mnt/data/dongle-stock-recovered.bin')
        cls.bank=path.read_bytes() if path.exists() else None

    def test_patch_compiled_assembler(self):
        bindir=Path('/tmp/toolchain32/tc32/bin')
        if not (bindir/'tc32-elf-as').exists():self.skipTest('TC32 compiler unavailable')
        self.assertEqual(build(*(bindir/('tc32-elf-'+tool) for tool in ('as','ld','objcopy'))),dongle.D_STEAM_CODE)

    def test_patches_and_rollback(self):
        if self.bank is None:self.skipTest('No local stock bank')
        self.assertTrue(dongle.state(self.bank)[0])
        self.assertEqual(self.bank[MAP-dongle.BASE:MAP-dongle.BASE+MAP_LENGTH],dongle.D_STOCK_MAP)
        keys=['dongle_dinput','dongle_dinput_steam','dongle_rf_tail','dongle_rf_read']
        patched=dongle.target(self.bank,keys)
        self.assertEqual(dongle.state(patched), (True,tuple(k for k in dongle.TWEAKS if k in keys)))
        self.assertEqual(bl_target(patched[HOOK-dongle.BASE:HOOK-dongle.BASE+4],RUNTIME_HOOK),ENTRY)
        self.assertNotEqual(bl_target(patched[HOOK-dongle.BASE:HOOK-dongle.BASE+4],HOOK),ENTRY)
        self.assertEqual(patched[ENTRY-dongle.BASE:ENTRY-dongle.BASE+44],dongle.D_STEAM_CODE)
        self.assertEqual(dongle.target(patched,['dongle_dinput','dongle_rf_tail','dongle_rf_read']),dongle.target(self.bank,['dongle_dinput','dongle_rf_tail','dongle_rf_read']))
        with self.assertRaisesRegex(OpError, 'requires the dongle DInput'):
            dongle.target(self.bank,['dongle_dinput_steam'])

    def test_hook_addresses_executable_ram_not_flash_image(self):
        self.assertEqual(RUNTIME_HOOK, 0x1676)
        self.assertEqual(bl_target(dongle.D_STEAM_HOOK, RUNTIME_HOOK), ENTRY)
        # Old patch encoded the call using the image address (0x7676).
        # At runtime it jumped to 0x9fb0 instead of the helper at 0xffb0.
        from tools.build_ring_hold import bl
        old_hook = bl(HOOK, ENTRY)
        self.assertEqual(bl_target(old_hook, RUNTIME_HOOK), 0x9FB0)
        self.assertNotEqual(old_hook, dongle.D_STEAM_HOOK)

    def test_old_image_relative_hook_crashes_during_actual_ram_execution(self):
        if self.bank is None:self.skipTest('No local stock bank')
        keys = ['dongle_dinput', 'dongle_dinput_steam', 'dongle_rf_read', 'dongle_rf_tail']
        bad = bytearray(dongle.target(self.bank, keys))
        from tools.build_ring_hold import bl
        bad[HOOK-dongle.BASE:HOOK-dongle.BASE+4] = bl(HOOK, ENTRY)
        cpu = ReportCPU(bytes(bad))
        with self.assertRaisesRegex(AssertionError, 'unexpected write'):
            cpu.call(0x14e8, 0xabcdef, 0x123456, 0x9abcdef, 0x5a5a)
        self.assertFalse(cpu.sent)

    def test_stock_and_new_reporting_are_exact_reorder(self):
        if self.bank is None:self.skipTest('No local stock bank')
        patched=dongle.target(self.bank,['dongle_dinput','dongle_dinput_steam','dongle_rf_read','dongle_rf_tail'])
        for values in ((0,0,0,0),(0x10ff,0x80ff,0,0),(0xffffffff,0x12345678,0xaabbccdd,0x5a5a5a5a), (0,0,0xdeadbeef,0)):
            old=ReportCPU(self.bank); new=ReportCPU(patched)
            old.call(0x14e8,*values);new.call(0x14e8,*values)
            self.assertEqual(len(old.sent),1)
            self.assertEqual(len(new.sent),1)
            a=old.sent[0]; b=new.sent[0]
            self.assertEqual(a[0],b[0])
            self.assertEqual(a[0],1)
            self.assertEqual(len(a[1]),9)
            self.assertEqual(b[1],a[1][2:]+a[1][:2])

    def test_dongle_with_pid_301b_is_not_mistaken_for_usb_controller(self):
        d=DeviceInfo(0x2dc8,0x301b,1,'USB','8BitDo',b'path',0x200,5)
        ctrl=DeviceInfo(0x2dc8,0x301b,1,'USB','8BitDo',b'ctrl',1,5)
        with patch.object(devices.transport,'list_devices',return_value=[d]):
            self.assertEqual([f.mode for f in devices.find()],['dongle_paired'])
        with patch.object(devices.transport,'list_devices',return_value=[ctrl]):
            self.assertEqual([f.mode for f in devices.find()],['cable_dinput'])

if __name__=='__main__':unittest.main()

class SteamDongleDiscoveryRegressionTest(unittest.TestCase):
    """Both paired and idle dongle states must remain reachable after PID=301B."""
    def dongle(self, page, usage=0, handle=b"dongle", version=0x0200):
        return DeviceInfo(0x2dc8, 0x301b, page, 'USB', '8BitDo', handle, version, usage)

    def test_idle_dongle_vendor_channel_is_discoverable_for_restore(self):
        for page in (0xFFA0, 0xFF7A):
            with self.subTest(page=hex(page)), patch.object(devices.transport,'list_devices',return_value=[self.dongle(page)]):
                found=devices.find()
                self.assertEqual([f.mode for f in found], ['dongle'])
                self.assertEqual(found[0].info.usage_page, page)

    def test_paired_gamepad_and_service_are_not_two_devices(self):
        gamepad=self.dongle(1,5,b'gamepad')
        service=self.dongle(0xFF7A,0,b'service')
        for order in ((gamepad,service),(service,gamepad)):
            with patch.object(devices.transport,'list_devices',return_value=list(order)):
                self.assertEqual([(f.mode,f.info.handle) for f in devices.find()], [('dongle_paired',b'gamepad')])

    def test_usb_controller_with_same_pid_is_still_not_dongle(self):
        controller=self.dongle(1,5,b'controller',version=1)
        with patch.object(devices.transport,'list_devices',return_value=[controller]):
            self.assertEqual([f.mode for f in devices.find()],['cable_dinput'])

    def test_dongle_is_not_merged_with_an_actual_usb_controller(self):
        controller=self.dongle(1,5,b'controller',version=1)
        receiver=self.dongle(0xFFA0,0,b'dongle')
        with patch.object(devices.transport,'list_devices',return_value=[controller,receiver]):
            self.assertEqual([f.mode for f in devices.find()],['cable_dinput','dongle'])
