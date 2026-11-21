# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from tests.test_firmware import synthetic
from tests.test_ops import Base,STOCK_A,controller_flash,noprogress
from u2c_customfw.firmware import image,recipes
from u2c_customfw.ops import patch,restore,plan
from u2c_customfw.sim import SimBoot

NEEDS=('bt_xbox','bt_modes','dim_leds','ring_normal','settings','cpu_rest')

class Switch2RecipeTest(unittest.TestCase):
    def test_round_trip_with_optional_tweaks_and_custom_name(self):
        stock,constants=synthetic()
        for extras in ((),('usb_dinput',),('bt_rumble','bt_rumble_long','battery_log','ble_interval')):
            keys=(*NEEDS,*extras,'bt_switch2')
            data=recipes.build(stock,keys,names={'bt_xbox':'My Xbox'},**constants)
            state=recipes.identify(data,**constants)
            self.assertTrue(state.known)
            self.assertEqual(set(state.recipes),set(keys))
            self.assertEqual(state.names['bt_xbox'],'My Xbox')
            changed=bytearray(data);changed[0x1a050-image.BASE]^=1
            self.assertFalse(recipes.identify(bytes(changed),**constants).known)
    def test_force_y_is_not_available(self):
        self.assertNotIn('bt_switch2_force', recipes.RECIPES)

    def test_dependencies_and_legacy_app(self):
        stock,constants=synthetic()
        for need in ('bt_modes','dim_leds','settings','cpu_rest'):
            with self.assertRaises(recipes.RecipeError):
                recipes.build(stock,[k for k in (*NEEDS,'bt_switch2') if k!=need],**constants)
        ordinary=recipes.build(stock,NEEDS,**constants)
        old=ordinary[:image.BANK_A_ERASE_END-image.BASE]
        self.assertEqual(recipes.identify(old,**constants).recipes,tuple(sorted(NEEDS)))

class Switch2InstallTest(Base):
    def test_install_and_remove_via_standard_patch(self):
        original=controller_flash(); saved=self.backup_of(original);device=SimBoot(original)
        selected=(*NEEDS,'bt_switch2')
        install=patch.plan_patch(device,saved,STOCK_A,selected)
        self.assertEqual(install.other_sectors,[0x1a000,0x1b000,0x1c000])
        plan.apply(device,install,noprogress)
        self.assertIn('bt_switch2',recipes.identify(bytes(device.f[image.BASE:image.APP_END])).recipes)
        uninstall=patch.plan_patch(device,saved,STOCK_A,NEEDS)
        plan.apply(device,uninstall,noprogress)
        self.assertEqual(bytes(device.f[0x1a000:image.APP_END]),b'\xff'*0x3000)
        self.assertEqual(bytes(device.f[:image.BASE]),original[:image.BASE])
        self.assertEqual(bytes(device.f[image.APP_END:]),original[image.APP_END:])
    def test_stock_restore_removes_extension(self):
        original=controller_flash();saved=self.backup_of(original);device=SimBoot(original)
        plan.apply(device,patch.plan_patch(device,saved,STOCK_A,(*NEEDS,'bt_switch2')),noprogress)
        stock=restore.plan_restore(device,saved,'stock',STOCK_A)
        self.assertEqual(stock.other_sectors,[0x1a000,0x1b000,0x1c000])
        plan.apply(device,stock,noprogress)
        self.assertEqual(bytes(device.f),original)
