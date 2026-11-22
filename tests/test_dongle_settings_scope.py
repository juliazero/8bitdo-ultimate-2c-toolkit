# SPDX-License-Identifier: GPL-3.0-or-later
"""Prevent the documentation-only dongle Settings stage from altering tested patches."""
import unittest
from pathlib import Path
from u2c_customfw.firmware import recipes
from u2c_customfw.ops import dongle


class DongleSettingsScopeTest(unittest.TestCase):
    def test_existing_bluetooth_recipes_remain_separate(self):
        keys = ("bt_xbox", "bt_modes", "bt_rumble", "bt_rumble_long", "bt_switch2")
        self.assertTrue(all(k in recipes.RECIPES for k in keys))
        self.assertNotIn("bt_multimode", recipes.RECIPES)
        self.assertEqual(recipes.RECIPES["bt_switch2"].requires,
                         ("bt_modes", "dim_leds", "settings", "cpu_rest"))

    def test_existing_dongle_tweaks_remain_separate(self):
        self.assertEqual(tuple(dongle.TWEAKS),
                         ("dongle_360", "dongle_dinput", "dongle_dinput_steam",
                          "dongle_rf_tail", "dongle_rf_read"))

    def test_y_description_matches_hardware_validation(self):
        page = Path("u2c_customfw/web/static/index.html").read_text()
        self.assertIn("NS2 Pro Controller mode (Y)", page)
        self.assertIn("no gyroscope, NFC hardware or Chat button", page)
        self.assertIn("vibration have been tested", page)
