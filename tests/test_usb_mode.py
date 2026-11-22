# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from tools.build_usb_mode_prototype import integrated
from u2c_customfw.firmware import image,recipes
from u2c_customfw.simulation import installed


class USBModeImageTest(unittest.TestCase):
    def test_integrated_blobs_reproduce_and_fit_without_changing_log(self):
        blobs, code, labels=integrated(recipes.BLOBS)
        for name,data in blobs.items(): self.assertEqual((recipes.BLOBS/name).read_bytes(),data)
        log=(recipes.BLOBS/'battlog.bin').read_bytes()
        self.assertEqual(blobs['usbmode.bin'][:len(log)],log)
        self.assertLessEqual(0x9a70+len(code),0x9cac)
        self.assertEqual(labels['main'],0x9a70)

    def test_all_ring_levels_and_optional_log_identify_and_remove(self):
        from u2c_customfw import cli
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            stock=cli.fetch.fetch_stock()
            for ring in recipes.RING_ORDER:
                for extra in ([],['settings','cpu_rest','battery_log']):
                    keys=[ring,'usb_dinput',*extra]
                    built=recipes.build(stock,keys)
                    self.assertEqual(set(recipes.identify(built).recipes),set(keys))
                    without=recipes.build(stock,[ring,*extra])
                    self.assertEqual(built[0x18400-image.BASE:],without[0x18400-image.BASE:])
                    self.assertEqual(set(recipes.identify(without).recipes),{ring,*extra})

    def test_usb_pid_is_wired_only_and_previous_payload_is_upgradable(self):
        from u2c_customfw import cli
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            stock=cli.fetch.fetch_stock()
            keys=['ring_normal','usb_dinput']
            built=recipes.build(stock,keys)
            offset=0x17afe-image.BASE
            self.assertEqual(built[offset:offset+2],bytes.fromhex('1d30'))
            old=bytearray(built);old[offset:offset+2]=bytes.fromhex('1b30')
            found=recipes.identify(bytes(old))
            self.assertTrue(found.known)
            self.assertEqual(set(found.recipes),set(keys))
            # Exactly these two USB descriptor bytes differ from the previous version.
            changes=[i for i,(a,b) in enumerate(zip(old,built)) if a!=b]
            self.assertEqual(changes,[offset])

    def test_previous_usb_payload_before_motor_exemption_remains_recognized(self):
        from u2c_customfw import cli
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            keys=['ring_normal','usb_dinput']
            old=bytearray(recipes.build(cli.fetch.fetch_stock(),keys))
            old[0x9964-image.BASE:0x9cac-image.BASE]=(recipes.BLOBS/'usbmode02.bin').read_bytes()
            identified=recipes.identify(bytes(old))
            self.assertTrue(identified.known)
            self.assertEqual(set(identified.recipes),set(keys))

    def test_historical_payload_with_its_actual_vendor_hooks_upgrades(self):
        from u2c_customfw import cli
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            stock=cli.fetch.fetch_stock();keys=['ring_normal','usb_dinput']
            for version in ('01','02','03'):
                old=bytearray(recipes.build(stock,keys))
                old[0x9964-image.BASE:0x9cac-image.BASE]=(recipes.BLOBS/('usbmode'+version+'.bin')).read_bytes()
                old[0xf83c-image.BASE:0xf840-image.BASE]=bytes.fromhex('fa976b99')
                old[0xf842-image.BASE:0xf846-image.BASE]=bytes.fromhex('fa977099')
                start=next(e for e in recipes.RECIPES['usb_dinput'].edits if e.addr==0xa414)
                old[0xa414-image.BASE:0xa418-image.BASE]=start.accepts[0]
                self.assertEqual(set(recipes.identify(bytes(old)).recipes),set(keys),version)

    def test_stock_usb_patches_remain_applicable(self):
        # Compatibility is anchored to the official stock firmware, not to
        # intermediate local test builds with arbitrary hook bytes.
        from u2c_customfw import cli
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            stock=cli.fetch.fetch_stock()
            keys=['ring_normal','settings','cpu_rest','battery_log','usb_dinput']
            patched=recipes.build(stock,keys)
            self.assertEqual(set(recipes.identify(patched).recipes),set(keys))
            self.assertEqual(recipes.build(stock,keys),patched)

    def test_previous_ring_and_watchdog_payloads_remain_recognized(self):
        from u2c_customfw import cli
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            keys=['ring_normal','settings','cpu_rest','battery_log','usb_dinput']
            old=bytearray(recipes.build(cli.fetch.fetch_stock(),keys))
            old[0x18400-image.BASE:0x18ff0-image.BASE]=(recipes.BLOBS/'ringtail.bin').read_bytes().ljust(0xbf0,b'\xff')
            old[0x6020-image.BASE:0x60b4-image.BASE]=(recipes.BLOBS/'wdog.bin').read_bytes().ljust(0x94,b'\0')
            state=recipes.identify(old)
            self.assertTrue(state.known)
            self.assertEqual(set(state.recipes),set(keys))

    def test_previous_battery_lock_payload_is_recognized_for_upgrade(self):
        from u2c_customfw import cli
        from u2c_customfw.ops import update_available
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            keys = ['ring_normal', 'usb_dinput']
            old = bytearray(recipes.build(cli.fetch.fetch_stock(), keys))
            old[0x9964-image.BASE:0x9cac-image.BASE] = (recipes.BLOBS/'usbmode-pre-battery-lock-fix.bin').read_bytes()
            identified = recipes.identify(bytes(old))
            self.assertTrue(identified.known)
            self.assertEqual(set(identified.recipes), set(keys))
            self.assertTrue(identified.older)
            self.assertTrue(update_available(identified))
