# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from u2c_customfw import cli, state
from u2c_customfw.firmware import image, recipes
from u2c_customfw.ops import dongle
from u2c_customfw.ops.plan import OpError
from u2c_customfw.protocol import app, boot
from u2c_customfw.simulation import installed, PROFILES


def args(**values):
    return SimpleNamespace(**{**dict(write=False, tweaks="", backup=None, download=False, source="backup",
        accept_disclaimer=False, force_unknown=False, say=lambda text: None, hint=lambda text: None,
        progress=lambda *a: None), **values})


def restart_controller(world):
    """Firmware-command restart, distinct from physically powering off on USB."""
    app.enter_update_mode(cli.open_channel(cli.find()[0].info, 0x81))
    cli.open_channel(cli.find()[0].info, 0).xfer(boot.request(7))


class SimulationTest(unittest.TestCase):
    def test_home_power_hold_preserves_modifier_and_usb_stays_powered(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'controller-bluetooth') as world:
            with patch('u2c_customfw.simulation.time.monotonic', return_value=100):
                world.control(dict(action='button', button='Home', down=True))
                world.control(dict(action='button', button='B', down=True))
            with patch('u2c_customfw.simulation.time.monotonic', return_value=111.99):
                self.assertTrue(world.virtual_state()['input']['connected'])
            with patch('u2c_customfw.simulation.time.monotonic', return_value=112):
                self.assertFalse(world.virtual_state()['input']['connected'])
                self.assertIn('B',world.pressed)
                world.control(dict(action='button',button='Home',down=False))
                self.assertTrue(world.control(dict(action='power'))['input']['connected'])
            world.control(dict(action='connection',device='controller',connection='usb'))
            with patch('u2c_customfw.simulation.time.monotonic', return_value=200):
                world.control(dict(action='button',button='Home',down=True))
            with patch('u2c_customfw.simulation.time.monotonic', return_value=220):
                self.assertTrue(world.virtual_state()['input']['connected'])
                self.assertEqual(world.profile,'controller-usb')

    def test_update_chord_and_usb_attachment_with_paddles(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'controller-bluetooth') as world:
            original=bytes(world.controller)
            with patch('u2c_customfw.simulation.time.monotonic',return_value=100):
                for button in ('Home','L4','R4'): world.control(dict(action='button',button=button,down=True))
            with patch('u2c_customfw.simulation.time.monotonic',return_value=102.99):
                self.assertNotEqual(world.virtual_state()['profile'],'controller-boot')
            with patch('u2c_customfw.simulation.time.monotonic',return_value=103):
                snap=world.virtual_state()
                self.assertEqual(snap['profile'],'controller-boot')
                self.assertEqual(snap['selected_connection'] if 'selected_connection' in snap else world.selected_connection,'update')
                self.assertEqual(cli.find()[0].mode,'boot')
            self.assertEqual(bytes(world.controller),original)
            world.control(dict(action='connection',device='controller',connection='bluetooth'))
            for button in ('L4','R4'): world.control(dict(action='button',button=button,down=True))
            snap=world.control(dict(action='connection',device='controller',connection='usb'))
            self.assertEqual(snap['profile'],'controller-boot')
            self.assertEqual(snap['connection'],'update')
            self.assertEqual(bytes(world.controller),original)

    def test_connection_selection_and_rf_startup_require_installed_tweak(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            def choose(device, connection):
                return world.control(dict(action='connection', device=device, connection=connection))
            def cycle(button=None):
                world.control(dict(action='release'))
                if button: world.control(dict(action='button', button=button, down=True))
                world.control(dict(action='power'))
                self.assertEqual(world.virtual_state()['device'], 'controller')
                self.assertEqual(world.virtual_state()['connection'], 'dongle')
                if button: self.assertIn(button, world.pressed)
                return world.control(dict(action='power'))
            choose('controller', 'dongle')
            self.assertEqual(cycle('B')['profile'], 'dongle-xinput')
            controller = bytes(world.controller)
            choose('dongle', 'usb')
            state.accept(Path(tmp))
            cli.cmd_backup(args(), Path(tmp))
            cli.cmd_patch(args(write=True, tweaks='dongle_dinput'), Path(tmp))
            self.assertTrue(world._dongle_dinput())
            self.assertEqual(bytes(world.controller), controller)
            choose('controller', 'dongle')
            snap = cycle('B')
            self.assertEqual(snap['profile'], 'dongle-dinput')
            self.assertEqual(snap['input']['category'], '2.4 GHz dongle · D-input')
            self.assertEqual(cycle()['profile'], 'dongle-dinput')
            snap = cycle('X')
            self.assertEqual(snap['profile'], 'dongle-xinput')
            self.assertEqual(snap['input']['category'], '2.4 GHz dongle · XInput')
            self.assertEqual(cycle()['profile'], 'dongle-xinput')
            choose('dongle', 'usb')
            cli.cmd_patch(args(write=True, tweaks=''), Path(tmp))
            choose('controller', 'dongle')
            self.assertEqual(cycle('B')['profile'], 'dongle-xinput')
            self.assertEqual(choose('controller', 'bluetooth')['profile'], 'controller-bluetooth')
            self.assertEqual(choose('controller', 'update')['profile'], 'controller-boot')
            self.assertEqual(choose('dongle', 'update')['profile'], 'dongle-boot')
            self.assertEqual(choose('dongle', 'usb')['profile'], 'dongle-idle')
            with self.assertRaises(ValueError): choose('dongle', 'bluetooth')

    def test_usb_cannot_power_off_or_force_an_unverified_mode(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            world.control(dict(action='button', button='B', down=True))
            before = bytes(world.controller)
            with self.assertRaisesRegex(ValueError, 'Disconnect USB'):
                world.control(dict(action='power'))
            self.assertTrue(world.connected)
            self.assertEqual(cli.find()[0].mode, 'cable')
            self.assertEqual(bytes(world.controller), before)
            restart_controller(world)
            self.assertEqual(cli.find()[0].mode, 'cable')
            world.control(dict(action='connection', device='controller', connection='dongle'))
            world.control(dict(action='power'))
            self.assertEqual(world.profile, 'dongle-idle')
            # Exercise recognition of a saved D-input image without pretending
            # a button sequence can currently create it on physical hardware.
            world.controller[image.SETTINGS + 5] = 1
            world.control(dict(action='connection', device='controller', connection='usb'))
            self.assertFalse(world.usb_dinput)  # battery-equipped stock ignores the saved wired-mode byte
            world.usb_dinput = True  # exercise discovery of an actual running D-input state
            self.assertEqual(cli.find()[0].mode, 'cable_dinput')
            cli.cmd_backup(args(), Path(tmp))
            self.assertTrue(list(Path(tmp).glob('controller-20*.bin')))

    def test_rear_switch_persists_and_unplug_usb_waits_two_seconds(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / 'prefs.json'
            for switch in ('dongle', 'bluetooth'):
                with installed(Path(tmp) / switch, preferences_path=prefs) as world:
                    if switch == 'dongle': self.assertEqual(world.rear_switch, 'dongle')
                    world.control(dict(action='rear_switch', value=switch))
                    self.assertEqual(world.profile, 'controller-usb')
                    with patch('u2c_customfw.simulation.time.monotonic', return_value=100):
                        snap = world.control(dict(action='disconnect_usb'))
                    self.assertEqual(snap['connection'], switch)
                    self.assertTrue(snap['reconnecting'])
                    self.assertFalse(snap['input']['connected'])
                    self.assertEqual(world.rear_switch, switch)
                    with patch('u2c_customfw.simulation.time.monotonic', return_value=101.99):
                        self.assertEqual(world.find(), [])
                    with patch('u2c_customfw.simulation.time.monotonic', return_value=102):
                        self.assertTrue(world.find())
                        self.assertTrue(world.virtual_state()['input']['connected'])
                    self.assertFalse(world.virtual_state()['reconnecting'])
                    world.control(dict(action='power'))
                    self.assertFalse(world.virtual_state()['input']['connected'])
                with installed(Path(tmp) / (switch + '-reopen'), preferences_path=prefs) as world:
                    self.assertEqual(world.rear_switch, switch)

    def test_update_mode_usb_disconnect_and_power_off_cancels_transition(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'controller-boot') as world:
            with patch('u2c_customfw.simulation.time.monotonic', return_value=100):
                world.control(dict(action='disconnect_usb'))
                world.control(dict(action='power'))
            with patch('u2c_customfw.simulation.time.monotonic', return_value=103):
                self.assertFalse(world.virtual_state()['input']['connected'])
                self.assertIsNone(world.reconnect_at)

    def test_dongle_usb_disconnect_reconnect_keeps_flash_and_controller_state(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'dongle-xinput') as world:
            before = bytes(world.receiver)
            world.control(dict(action='dongle_usb', connected=False))
            self.assertEqual(cli.find(), [])
            self.assertFalse(world.virtual_state()['input']['connected'])
            self.assertTrue(world.connected)  # the pad stays powered, searching
            self.assertEqual(world.connection, 'dongle')
            world.control(dict(action='dongle_usb', connected=True))
            self.assertTrue(cli.find())
            self.assertTrue(world.virtual_state()['input']['connected'])
            self.assertEqual(bytes(world.receiver), before)
            world.control(dict(action='connection', device='dongle', connection='update'))
            world.control(dict(action='dongle_usb', connected=False))
            self.assertEqual(cli.find(), [])
            world.control(dict(action='dongle_usb', connected=True))
            self.assertEqual(cli.find()[0].mode, 'boot')

    def test_usb_patch_install_hold_b_x_and_ram_only_restore(self):
        from unittest.mock import patch as mock_patch
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            state.accept(Path(tmp))
            cli.cmd_backup(args(), Path(tmp))
            cli.cmd_patch(args(write=True, tweaks='ring_normal,usb_dinput'), Path(tmp))
            self.assertIn('usb_dinput', world._controller_keys())
            saved = bytes(world.controller[image.CONFIG_START:])
            def hold(button, start):
                world.control(dict(action='release'))
                world.control(dict(action='button', button=button, down=True))
                with mock_patch('u2c_customfw.simulation.time.monotonic', return_value=start):
                    world.control(dict(action='button', button='Home', down=True))
                    world.virtual_state()
                with mock_patch('u2c_customfw.simulation.time.monotonic', return_value=start+2.99):
                    initial = world.usb_dinput
                    self.assertEqual(world.virtual_state()['usb_dinput'], initial)
                with mock_patch('u2c_customfw.simulation.time.monotonic', return_value=start+3):
                    return world.virtual_state()
            world.usb_last_vendor = None
            self.assertTrue(hold('B',100)['usb_dinput'])
            self.assertEqual(cli.find()[0].mode, 'cable_dinput')
            self.assertFalse(hold('X',200)['usb_dinput'])
            self.assertEqual(cli.find()[0].mode, 'cable')
            self.assertEqual(bytes(world.controller[image.CONFIG_START:]), saved)
            restart_controller(world)
            self.assertFalse(world.usb_dinput)
            cli.cmd_restore(args(write=True, source='stock'), Path(tmp))
            self.assertNotIn('usb_dinput', world._controller_keys())
            self.assertFalse(hold('B',300)['usb_dinput'])

    def test_upgrade_first_usb_patch_is_recognized_and_rewrites_code(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            state.accept(Path(tmp))
            cli.cmd_backup(args(), Path(tmp))
            built=recipes.build(cli.fetch.fetch_stock(), ['ring_normal','usb_dinput'])
            world.controller[image.BASE:image.APP_END]=built
            old=(recipes.BLOBS/'usbmode01.bin').read_bytes()
            world.controller[0x9964:0x9964+len(old)]=old
            self.assertEqual(set(world._controller_keys()), {'ring_normal','usb_dinput'})
            request=args(write=True,tweaks='ring_normal,usb_dinput')
            cli.cmd_patch(request,Path(tmp))
            self.assertNotEqual(old,(recipes.BLOBS/'usbmode.bin').read_bytes())
            self.assertEqual(world.controller[0x9964:0x9964+len(old)],(recipes.BLOBS/'usbmode.bin').read_bytes())
            self.assertFalse(getattr(request,'unchanged',False))

    def test_battery_percent_voltage_and_usb_power_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            snap=world.control(dict(action='battery',percent=5,millivolts=3000))
            self.assertEqual(snap['input']['battery']['level'],.05)
            self.assertEqual(snap['input']['battery']['voltage_mv'],3000)
            self.assertEqual(snap['input']['battery']['state'],'charging')
            self.assertTrue(snap['hardware']['external_power'])
            world.control(dict(action='connection',device='controller',connection='bluetooth'))
            snap=world.virtual_state()
            self.assertFalse(snap['hardware']['external_power'])
            self.assertEqual(snap['input']['battery']['state'],'discharging')
            self.assertEqual(snap['hardware']['battery_percent'],5)
            world.control(dict(action='battery',percent=100))
            self.assertEqual(world.battery_mv,3000)
            for request in (dict(percent=-1),dict(millivolts=5001),dict(percent=True)):
                with self.assertRaises(ValueError):world.control(dict(action='battery',**request))

    def test_ring_chord_needs_five_seconds_and_saves_afterwards(self):
        from unittest.mock import patch as mock_patch
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            built=recipes.build(cli.fetch.fetch_stock(),['ring_normal'])
            world.controller[image.BASE:image.APP_END]=built
            with mock_patch('u2c_customfw.simulation.time.monotonic',return_value=100):
                world.control(dict(action='button',button='L4',down=True))
                world.control(dict(action='button',button='R4',down=True))
                world.virtual_state()
            with mock_patch('u2c_customfw.simulation.time.monotonic',return_value=104.99):
                self.assertEqual(world.virtual_state()['ring']['live'],0)
            with mock_patch('u2c_customfw.simulation.time.monotonic',return_value=105):
                self.assertEqual(world.virtual_state()['ring']['live'],1)
                self.assertEqual(world.ring_state()['startup'],0)
            with mock_patch('u2c_customfw.simulation.time.monotonic',return_value=108):
                self.assertEqual(world.virtual_state()['ring']['startup'],1)
            with mock_patch('u2c_customfw.simulation.time.monotonic',return_value=120):
                self.assertEqual(world.virtual_state()['ring']['live'],1)
                world.control(dict(action='release'))
            self.assertEqual(world.ring_state()['chord_hold_seconds'],5)

    def test_profiles_and_boundaries_are_restored(self):
        find, channel, constants = cli.find, cli.open_channel, recipes._constants
        expected = ['cable', 'boot', 'bluetooth', 'bluetooth_xbox', 'dongle', 'cable', 'dongle_paired', 'boot']
        self.assertEqual(len(PROFILES), len(expected))
        for profile, mode in zip(PROFILES, expected):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                with installed(Path(tmp), profile) as world:
                    self.assertEqual(cli.find()[0].mode, mode)
                    self.assertIs(cli.find()[0].info.handle, world)
                self.assertIs(cli.find, find)
                self.assertIs(cli.open_channel, channel)
                self.assertIs(recipes._constants, constants)

    def test_receiver_update_mode_and_flash_are_shared(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'dongle-idle') as world:
            ch = cli.open_channel(cli.find()[0].info, 0x81)
            app.enter_update_mode(ch)
            ch = cli.open_channel(cli.find()[0].info, 0)
            self.assertEqual(boot.identify(ch), 0x301C)
            self.assertEqual(boot.read(ch, 0, 32), bytes(world.receiver[:32]))
            ch.xfer(boot.request(7))
            self.assertEqual(cli.find()[0].mode, 'dongle')

    def test_ambiguous_receiver_backup_uses_receiver_size(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'dongle-xinput') as world:
            cli.cmd_backup(args(), Path(tmp))
            backups = list(Path(tmp).glob('dongle-*.bin'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), world.receiver)
            self.assertEqual(backups[0].stat().st_size, 0x20000)
            self.assertTrue(all(packet[:3] == bytes.fromhex('81 05 05') for packet in world.trace))

    def test_controller_install_returns_to_usb_and_preserves_calibration(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'controller-usb') as world:
            state.accept(Path(tmp))
            before = bytes(world.controller)
            cli.cmd_backup(args(), Path(tmp))
            cli.cmd_patch(args(write=True, tweaks='cable_360'), Path(tmp))
            self.assertEqual(cli.find()[0].mode, 'cable_360')
            self.assertEqual(world.controller[:image.BOOT_END], before[:image.BOOT_END])
            self.assertEqual(world.controller[image.CONFIG_START:], before[image.CONFIG_START:])
            self.assertIn('cable_360', recipes.identify(bytes(world.controller[image.BASE:image.APP_END])).recipes)

    def test_receiver_patch_round_trip_preserves_bootloader_and_calibration(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'dongle-idle') as world:
            state.accept(Path(tmp))
            before = bytes(world.receiver)
            cli.cmd_backup(args(), Path(tmp))
            cli.cmd_patch(args(write=True, tweaks='dongle_dinput'), Path(tmp))
            self.assertEqual(world.profile, 'dongle-idle')
            self.assertEqual(dongle.state(bytes(world.receiver[dongle.BASE:dongle.END])), (True, ('dongle_dinput',)))
            self.assertEqual(world.receiver[:dongle.BOOT_END], before[:dongle.BOOT_END])
            self.assertEqual(world.receiver[dongle.END:], before[dongle.END:])

    def test_disconnect_during_backup_does_not_create_partial_backup(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'dongle-xinput', 10) as world:
            with self.assertRaises(OpError):
                cli.cmd_backup(args(), Path(tmp))
            self.assertEqual(cli.find(), [])
            self.assertEqual(list(Path(tmp).glob('dongle-*.bin')), [])

    def test_first_launch_has_no_precreated_backups_in_any_profile(self):
        for profile in PROFILES:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), profile):
                self.assertEqual(list(Path(tmp).glob('*.bin')), [])

    def test_backup_in_receiver_bootloader(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'dongle-boot') as world:
            cli.cmd_backup(args(), Path(tmp))
            files = [p for p in Path(tmp).glob('dongle-*.bin') if p.name != 'dongle-sim.bin']
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), world.receiver)
            self.assertFalse(list(Path(tmp).glob('controller-20*.bin')))

    def test_saved_settings_survive_reopening_and_power_cycle(self):
        from u2c_customfw import settings
        from u2c_customfw.ops import settings as ops
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'controller-usb') as world:
            ch = cli.open_channel(cli.find()[0].info, 0x81)
            self.assertEqual(ops.load(ch)['mapping'], {'l4': 0, 'r4': 0})
            values = {**settings.DEFAULTS, 'dz_l': 5, 'vibration': 50}
            ops.change(ch, values)
            app.set_mapping(ch, 1, 1 << 13)
            self.assertTrue(ops.save(ch, values, {'l4': 1 << 13, 'r4': 0}, False))
            ch.close()
            world.app_channel = None  # a fresh running instance reloads its flash
            again = cli.open_channel(cli.find()[0].info, 0x81)
            kept = ops.load(again)
            self.assertEqual(kept['values'], values)
            self.assertEqual(kept['mapping'], {'l4': 1 << 13, 'r4': 0})

    def test_experimental_bluetooth_startup_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'controller-bluetooth') as world:
            with self.assertRaisesRegex(ValueError, 'Unknown simulation action'):
                world.control({'action':'experimental_startup','enabled':True})
            world.control({'action':'button','button':'X','down':True})
            world.control({'action':'power'})
            world.control({'action':'power'})
            self.assertEqual(world.profile, 'controller-bluetooth')
            self.assertNotIn('experimental_startup', world.virtual_state())

    def test_virtual_input_and_invalid_events(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            state = world.control({'action':'button','button':'A','down':True})
            self.assertIn('A', state['input']['pressed'])
            state = world.control({'action':'axis','axis':'lt','value':.5})
            self.assertEqual(state['input']['lt'], .5)
            with self.assertRaises(ValueError):
                world.control({'action':'profile','profile':'real-usb'})

    def test_dongle_power_cycle_keeps_receiver_connection(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp), 'dongle-xinput') as world:
            world.control({'action':'power'})
            self.assertEqual(world.profile, 'dongle-idle')
            world.control({'action':'power'})
            self.assertEqual(world.profile, 'dongle-xinput')
            self.assertEqual(world.connection, 'dongle')

    def test_virtual_remapping_uses_live_and_saved_settings_without_chaining(self):
        from u2c_customfw import settings
        from u2c_customfw.ops import settings as ops
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            built = recipes.build(cli.fetch.fetch_stock(), ['settings', 'ring_normal'])
            world.controller[image.BASE:image.BASE + len(built)] = built
            ch = cli.open_channel(cli.find()[0].info, 0x81)
            values = {**settings.DEFAULTS, 'remap_13': 12, 'remap_12': 4}
            ops.change(ch, values)  # A->B, B->X; raw A must become B, not X.
            state = world.control({'action':'button','button':'A','down':True})
            self.assertEqual(state['held'], ['A'])
            self.assertEqual(state['input']['pressed'], ['B'])
            self.assertTrue(ops.save(ch, values))
            restart_controller(world)
            self.assertEqual(world.virtual_state()['input']['pressed'], ['B'])
            ch = cli.open_channel(cli.find()[0].info, 0x81)
            app.set_mapping(ch, 1, 1 << 13)
            world.control({'action':'button','button':'A','down':False})
            state = world.control({'action':'button','button':'L4','down':True})
            self.assertEqual(state['input']['pressed'], ['A'], 'back mapping expands after remap')
            world.control({'action':'button','button':'L4','down':False})
            app.set_field(ch, settings.BY_NAME['remap_13'].number, settings.NOTHING)
            state = world.control({'action':'button','button':'A','down':True})
            self.assertEqual(state['input']['pressed'], [])

    def test_virtual_axes_apply_live_trigger_and_inversion_settings(self):
        from u2c_customfw import settings
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            built = recipes.build(cli.fetch.fetch_stock(), ['settings', 'ring_normal'])
            world.controller[image.BASE:image.BASE + len(built)] = built
            ch = cli.open_channel(cli.find()[0].info, 0x81)
            for field, value in [('lt_start', 40), ('lt_end', 60), ('invert', 1)]:
                app.set_field(ch, settings.BY_NAME[field].number, value)
            self.assertEqual(world.control({'action':'axis','axis':'lt','value':.2})['input']['lt'], 0)
            self.assertEqual(world.control({'action':'axis','axis':'lt','value':.8})['input']['lt'], 1)
            self.assertLess(world.control({'action':'axis','axis':'lx','value':.8})['input']['lx'], 0)

    def test_ring_save_survives_power_cycle_and_unsaved_changes_do_not(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            built = recipes.build(cli.fetch.fetch_stock(), ['ring_5'])
            world.controller[image.BASE:image.BASE + len(built)] = built
            ch = cli.open_channel(cli.find()[0].info, 0x81)
            self.assertEqual(world.ring_state()['startup'], 2)
            app.set_ring(ch, 1)
            self.assertEqual(world.ring_state()['live'], 1)
            self.assertEqual(world.ring_state()['startup'], 2)
            app.save_ring(ch)
            self.assertEqual(world.controller[image.RING_SAVED], 0x21)
            restart_controller(world)
            self.assertEqual(world.ring_state()['live'], 1)
            ch = cli.open_channel(cli.find()[0].info, 0x81)
            app.set_ring(ch, 3)
            restart_controller(world)
            self.assertEqual(world.ring_state()['live'], 1)

    def test_release_clears_buttons_and_triggers_without_changing_profile(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            world.control({'action':'button','button':'A','down':True})
            world.control({'action':'button','button':'L4','down':True})
            world.control({'action':'axis','axis':'lt','value':.7})
            world.control({'action':'axis','axis':'rt','value':1})
            state = world.control({'action':'release'})
            self.assertEqual(state['held'], [])
            self.assertEqual(state['input']['pressed'], [])
            self.assertEqual((state['input']['lt'],state['input']['rt']), (0,0))
            self.assertEqual(state['profile'], 'controller-usb')

    def test_bluetooth_identity_tracks_firmware_after_restore(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            stock = cli.fetch.fetch_stock()
            with self.assertRaises(ValueError):
                world.control({'action':'profile','profile':'controller-bluetooth-xinput'})
            built = recipes.build(stock, ['bt_xbox'])
            world.controller[image.BASE:image.BASE + len(built)] = built
            world.control({'action':'profile','profile':'controller-bluetooth-xinput'})
            self.assertEqual(cli.find()[0].mode, 'bluetooth_xbox')
            world.controller[image.BASE:image.APP_END] = recipes.whole(stock)
            self.assertEqual(cli.find()[0].mode, 'bluetooth')
            self.assertEqual(world.virtual_state()['profile'], 'controller-bluetooth')
            self.assertEqual(world.virtual_state()['input']['category'], 'Bluetooth · D-input')
            self.assertFalse(world.virtual_state()['capabilities']['bt_xbox'])
            with self.assertRaises(ValueError):
                world.control({'action':'profile','profile':'controller-bluetooth-xinput'})
            world.control({'action':'button','button':'X','down':True})
            world.control({'action':'power'})
            world.control({'action':'power'})
            self.assertEqual(cli.find()[0].mode, 'bluetooth', 'a button cannot install a missing tweak')

    def test_bluetooth_ring_model_checks_tweaks_and_persists_save(self):
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            with self.assertRaises(ValueError):
                world.control({'action':'ring','level':1,'save':True})
            built = recipes.build(cli.fetch.fetch_stock(), ['bt_xbox','bt_rumble','ring_normal'])
            world.controller[image.BASE:image.BASE + len(built)] = built
            world.control({'action':'profile','profile':'controller-bluetooth-xinput'})
            world.control({'action':'ring','level':1,'save':False})
            self.assertEqual(world.ring_state()['startup'], 0)
            world.control({'action':'ring','level':255,'save':True})
            world.control({'action':'power'})
            world.control({'action':'power'})
            self.assertEqual(world.ring_state()['live'], 1)

    def test_external_device_handles_are_refused(self):
        from u2c_customfw.transport import DeviceInfo
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
            real = DeviceInfo(0x2DC8, 0x310A, 0xFF7A, 'USB', 'real', b'physical-path')
            with self.assertRaises(OSError):
                cli.open_channel(real, 0x81)

    def test_native_diagnostics_never_access_hardware(self):
        from u2c_customfw.web import server
        with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)):
            self.assertFalse(server.WINDOWS_RUMBLE.status()['available'])
            self.assertFalse(server.WINDOWS_RUMBLE.rumble(0, 'default')['ok'])
            self.assertFalse(server.WINDOWS_STOCK_GAMEPAD.snapshot()['connected'])
