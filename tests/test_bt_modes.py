# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from u2c_customfw import cli,state
from u2c_customfw.firmware import image,recipes
from u2c_customfw.simulation import installed
from tests.test_simulation import args,restart_controller

class BluetoothModesTest(unittest.TestCase):
    def install(self,world,extra=()):
        stock=cli.fetch.fetch_stock();keys=['bt_xbox','bt_modes',*extra]
        built=recipes.build(stock,keys);world.controller[image.BASE:image.APP_END]=built
        return keys
    def test_profile_copy_tracks_names_fixes_and_rejects_damage(self):
        with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)):
            stock=cli.fetch.fetch_stock()
            for extras in ([],['bt_rumble','bt_rumble_long','settings','fixes','ring_normal','usb_dinput']):
                keys=['bt_xbox','bt_modes',*extras]
                built=recipes.build(stock,keys,names={'bt_xbox':'My Xbox pad'})
                self.assertEqual(set(recipes.identify(built).recipes),set(keys))
                e=next(e for e in recipes.RECIPES['bt_modes'].edits if e.copy_from is not None)
                self.assertEqual(built[e.addr-image.BASE:e.addr-image.BASE+e.size],e.content(built))
                damaged=bytearray(built);damaged[e.addr-image.BASE+12]^=1
                self.assertFalse(recipes.identify(damaged).known)
            self.assertTrue(recipes.identify(recipes.whole(stock)[:0x19000-image.BASE]).known)
    def test_pre_identity_version_remains_recognized(self):
        with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)):
            stock=cli.fetch.fetch_stock()
            old=bytearray(recipes.build(stock,['bt_xbox','bt_modes']))
            for addr,name in ((0xa318,'bttable01.bin'),(0xa216,'btscan01.bin'),(0xa2e4,'btpack01.bin'),(0x19540,'btmodes01.bin')):
                value=(recipes.BLOBS/name).read_bytes()
                if addr==0x19540:value=value.ljust(len((recipes.BLOBS/'btmodes.bin').read_bytes()),b'\xff')
                old[addr-image.BASE:addr-image.BASE+len(value)]=value
            for e in recipes.RECIPES['bt_modes'].edits:
                if e.addr in (0xa138,0xa184,0xa478):old[e.addr-image.BASE:e.addr-image.BASE+4]=e.accepts[0]
            self.assertEqual(set(recipes.identify(bytes(old)).recipes),{'bt_xbox','bt_modes'})
            self.assertTrue(recipes.identify(bytes(old)).known)

    def test_default_xbox_saved_b_x_and_independent_usb_bit(self):
        with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)) as world:
            self.install(world,['ring_normal','usb_dinput'])
            world.controller[image.SETTINGS+5]=0
            choose=lambda:world.control(dict(action='connection',device='controller',connection='bluetooth'))
            self.assertEqual(choose()['input']['category'],'Bluetooth · XInput')
            def cycle(button=None):
                world.control(dict(action='release'))
                world.control(dict(action='power'))
                if button:world.control(dict(action='button',button=button,down=True))
                return world.control(dict(action='button',button='Home',down=True))
            self.assertEqual(cycle('B')['input']['category'],'Bluetooth · D-input')
            self.assertEqual(cli.find()[0].mode,'bluetooth')
            self.assertEqual(world.virtual_state()['input']['localities'],[])
            with self.assertRaises(ValueError):world.control(dict(action='vibrate',locality='default'))
            self.assertEqual(cycle()['input']['category'],'Bluetooth · D-input')
            self.assertEqual(cycle('X')['input']['category'],'Bluetooth · XInput')
            self.assertEqual(cycle()['input']['category'],'Bluetooth · XInput')
            world.control(dict(action='release'))
            world.control(dict(action='connection',device='controller',connection='usb'))
            world.controller[image.SETTINGS+5]=2
            world.usb_last_vendor=None
            with patch('u2c_customfw.simulation.time.monotonic',return_value=100):
                for b in ('B','Home'):world.control(dict(action='button',button=b,down=True))
            with patch('u2c_customfw.simulation.time.monotonic',return_value=103):world.virtual_state()
            self.assertEqual(world.controller[image.SETTINGS+5],3)
            world.control(dict(action='release'));restart_controller(world)
            self.assertTrue(world.usb_dinput)
            self.assertEqual(choose()['input']['category'],'Bluetooth · D-input')
            self.assertEqual(cycle('X')['input']['category'],'Bluetooth · XInput')
            self.assertEqual(world.controller[image.SETTINGS+5],1)
            world.control(dict(action='release'));world.control(dict(action='connection',device='controller',connection='usb'))
            self.assertTrue(world.usb_dinput)
    def test_install_from_persisted_usb_dinput_returns_to_dinput(self):
        with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)) as world:
            state.accept(Path(tmp))
            stock=cli.fetch.fetch_stock()
            world.controller[image.BANK_B:image.BANK_B+image.BANK_A_SIZE]=stock
            world.controller[image.BASE:image.APP_END]=recipes.build(stock,['ring_normal','usb_dinput'])
            world.controller[image.SETTINGS+5]=1
            world.control(dict(action='connection',device='controller',connection='usb'))
            self.assertEqual(cli.find()[0].mode,'cable_dinput')
            cli.cmd_backup(args(),Path(tmp))
            cli.cmd_patch(args(write=True,tweaks='ring_normal,usb_dinput,bt_xbox,bt_modes'),Path(tmp))
            self.assertEqual(cli.find()[0].mode,'cable_dinput')
            self.assertIn('bt_modes',world._controller_keys())

    def test_real_install_and_restore_clear_extension_preserving_configuration(self):
        with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)) as world:
            state.accept(Path(tmp));cli.cmd_backup(args(),Path(tmp))
            before=bytes(world.controller[image.CONFIG_START:])
            cli.cmd_patch(args(write=True,tweaks='bt_xbox,bt_modes'),Path(tmp))
            self.assertIn('bt_modes',world._controller_keys())
            self.assertNotEqual(world.controller[0x19000:0x1a000],b'\xff'*0x1000)
            cli.cmd_restore(args(write=True,source='stock'),Path(tmp))
            self.assertEqual(world.controller[0x19000:0x1a000],b'\xff'*0x1000)
            self.assertEqual(world.controller[image.CONFIG_START:],before)

    def test_live_wireless_chord_restarts_and_keeps_pairing_regions(self):
        from u2c_customfw.ops import dongle
        for connection in ('bluetooth','dongle'):
            with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)) as world:
                self.install(world)
                world.receiver[dongle.BASE:dongle.END]=dongle.target(bytes(world.receiver[dongle.BASE:dongle.END]),['dongle_dinput'])
                world.control(dict(action='connection',device='controller',connection=connection))
                bonds=bytes(world.controller[0x3b000:0x3d000])+bytes(world.controller[0x7c000:0x7e000])
                for button,start,expected in (('B',100,'D-input'),('X',200,'XInput')):
                    with patch('u2c_customfw.simulation.time.monotonic',return_value=start):
                        world.control(dict(action='release'))
                        for key in (button,'Home'):world.control(dict(action='button',button=key,down=True))
                    with patch('u2c_customfw.simulation.time.monotonic',return_value=start+2.99):self.assertFalse(world.virtual_state()['reconnecting'])
                    with patch('u2c_customfw.simulation.time.monotonic',return_value=start+3):self.assertTrue(world.virtual_state()['reconnecting'])
                    with patch('u2c_customfw.simulation.time.monotonic',return_value=start+5):
                        result=world.virtual_state();self.assertTrue(result['connected']);self.assertTrue(result['input']['category'].endswith(expected))
                    with patch('u2c_customfw.simulation.time.monotonic',return_value=start+9):self.assertFalse(world.virtual_state()['reconnecting'])
                self.assertEqual(bytes(world.controller[0x3b000:0x3d000])+bytes(world.controller[0x7c000:0x7e000]),bonds)

    def test_previous_default_name_migrates_but_custom_name_is_kept(self):
        for held,expected in ((recipes.PREVIOUS_BT_NAME,'8BitDo Ultimate 2C (X)'),('Julia pad','Julia pad')):
            with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)) as world:
                state.accept(Path(tmp))
                stock=cli.fetch.fetch_stock()
                world.controller[image.BANK_B:image.BANK_B+image.BANK_A_SIZE]=stock
                world.controller[image.BASE:image.APP_END]=recipes.build(stock,['bt_xbox','bt_modes'],names={'bt_xbox':held})
                cli.cmd_backup(args(),Path(tmp))
                cli.cmd_patch(args(write=True,tweaks='bt_xbox,bt_modes'),Path(tmp))
                self.assertEqual(recipes.identify(bytes(world.controller[image.BASE:image.APP_END])).names['bt_xbox'],expected)
