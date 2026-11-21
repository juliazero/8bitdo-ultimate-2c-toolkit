# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import itertools
import os
import random
import unittest
from pathlib import Path

from u2c_customfw.firmware import image, recipes
from u2c_customfw.firmware.recipes import RecipeError
from u2c_customfw.ops import update_available, with_hidden

KEYS = ["bt_xbox", "bt_rumble", "cable_360", "dim_leds"]
VALID = [list(c) for n in range(5) for c in itertools.combinations(KEYS, n) if "bt_rumble" not in c or "bt_xbox" in c]
RING = {"ring_off": 0, "ring_0_1": 1, "ring_1": 10, "ring_5": 50, "ring_25": 250}  # PWM compare value, period 1000
RING_LEVEL_ADDR = 0x76D0


def synthetic():
    """A random 'stock' bank A, with the recipes' stock hashes and the remainder hash recomputed for it."""
    rnd = random.Random(4)
    stock = bytearray(rnd.randbytes(image.BANK_A_SIZE))
    stock[0x18274 - image.BASE:] = bytes(image.BANK_A_END - 0x18274)  # the zero tail, as in the real image
    stock = bytes(stock)
    whole = recipes.whole(stock)
    region_sha = {(e.addr, e.size): hashlib.sha256(whole[e.addr - image.BASE:e.addr - image.BASE + e.size]).hexdigest()
                  for r in recipes.RECIPES.values() for e in r.edits}
    return stock, dict(stock_sha256=hashlib.sha256(stock).hexdigest(), region_sha256=region_sha,
                       remainder_sha256=hashlib.sha256(recipes.masked(whole)).hexdigest())


class RecipeTest(unittest.TestCase):
    def setUp(self):
        self.stock, self.k = synthetic()

    def test_every_valid_combination_builds_and_identifies(self):
        for keys in VALID:
            img = recipes.build(self.stock, keys, **self.k)
            st = recipes.identify(img, **self.k)
            self.assertTrue(st.known, keys)
            self.assertEqual(st.recipes, tuple(sorted(keys)), keys)

    def test_rumble_needs_bluetooth_identity(self):
        with self.assertRaises(RecipeError) as e:
            recipes.build(self.stock, ["bt_rumble"], **self.k)
        self.assertIn("bt_xbox", str(e.exception))

    def test_unknown_key_refused(self):
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, ["turbo"], **self.k)

    def test_wrong_stock_refused(self):
        bad = bytearray(self.stock); bad[0x100] ^= 1
        with self.assertRaises(RecipeError):
            recipes.build(bytes(bad), ["cable_360"], **self.k)

    def test_one_changed_byte_makes_the_image_unknown(self):
        img = recipes.build(self.stock, ["bt_xbox", "cable_360"], **self.k)
        for addr in [e.addr for r in recipes.RECIPES.values() for e in r.edits] + [0x9000]:
            bad = bytearray(img); bad[addr - image.BASE] ^= 0x40
            self.assertFalse(recipes.identify(bytes(bad), **self.k).known, hex(addr))

    def test_edits_stay_inside_the_app_area(self):
        for r in recipes.RECIPES.values():
            for e in r.edits:
                self.assertGreaterEqual(e.addr, image.BASE)
                self.assertLessEqual(e.addr + e.size, image.APP_END)

    def test_stock_identifies_as_stock(self):
        st = recipes.identify(self.stock, **self.k)
        self.assertEqual((st.known, st.recipes), (True, ()))


class ControllerNameTest(unittest.TestCase):
    NEW, OLD = b"8BitDo Ultimate 2C (X)", b"8BitDo Ultimate 2C (One)"
    GATT, SCAN = 0x172DC - image.BASE, 0x17254 - image.BASE + 2

    def setUp(self):
        self.stock, self.k = synthetic()
        self.built = recipes.build(self.stock, ["bt_xbox"], **self.k)

    def with_old_name(self, img, *at):
        out = bytearray(img)
        for a in at:
            out[a:a + len(self.OLD)] = self.OLD
        if self.GATT in at and self.SCAN in at:
            out[0x17488-image.BASE:0x1748c-image.BASE]=len(self.OLD).to_bytes(4,'little')
            out[self.SCAN-2]=len(self.OLD)+1
            out[0xa214-image.BASE]=len(self.OLD)+2
        return bytes(out)

    def test_new_installs_use_the_new_name(self):
        self.assertEqual(self.built[self.GATT:self.GATT + len(self.NEW)], self.NEW)
        self.assertEqual(self.built[self.SCAN:self.SCAN + len(self.NEW)], self.NEW)

    def test_a_chosen_bluetooth_name_goes_everywhere_the_name_and_its_length_live(self):
        img = recipes.build(self.stock, ["bt_xbox"], names={"bt_xbox": "My Pad"}, **self.k)
        at = lambda a, n: img[a - image.BASE:a - image.BASE + n]
        self.assertEqual(at(0x172DC, 27), b"My Pad".ljust(27, b"\0"))                    # GATT device name
        self.assertEqual(at(0x17488, 4), (6).to_bytes(4, "little"))                      # ... its length
        self.assertEqual(at(0x17254, 29), bytes([7, 9]) + b"My Pad".ljust(27, b"\0"))    # scan response
        self.assertEqual(at(0xA214, 2), bytes([8, 0xA1]))                                # ... its length: movs r1, #8
        st = recipes.identify(img, **self.k)
        self.assertEqual((st.recipes, st.names), (("bt_xbox",), {"bt_xbox": "My Pad"}))
        self.assertEqual(recipes.identify(self.built, **self.k).names, {"bt_xbox": "8BitDo Ultimate 2C (X)"})
        self.assertEqual(recipes.build(self.stock, ["bt_xbox"], names={"bt_xbox": "8BitDo Ultimate 2C (X)"}, **self.k), self.built)

    def test_names_are_only_printable_text_of_the_slots_length(self):
        self.assertEqual(recipes.build(self.stock, ["bt_xbox"], names={"bt_xbox": ""}, **self.k), self.built)   # "": the default
        for bad in ("x" * 28, " lead", "trail ", "tab\there", "caf\u00e9", 7):
            with self.assertRaises(RecipeError, msg=repr(bad)):
                recipes.build(self.stock, ["bt_xbox"], names={"bt_xbox": bad}, **self.k)
        recipes.build(self.stock, ["bt_xbox"], names={"bt_xbox": "x" * 27}, **self.k)
        with self.assertRaises(RecipeError):   # a name only for a tweak being installed, and one that has a name
            recipes.build(self.stock, ["bt_xbox"], names={"cable_360": "x"}, **self.k)
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, ["bt_xbox", "dim_leds"], names={"dim_leds": "x"}, **self.k)

    def test_a_name_whose_copies_disagree_makes_the_image_unknown(self):
        img = bytearray(recipes.build(self.stock, ["bt_xbox"], names={"bt_xbox": "My Pad"}, **self.k))
        img[0xA214 - image.BASE] = 9   # the scan response's length no longer the name's
        self.assertFalse(recipes.identify(bytes(img), **self.k).known)
        img = bytearray(recipes.build(self.stock, ["bt_xbox"], names={"bt_xbox": "My Pad"}, **self.k))
        img[0x172DC - image.BASE + 20] = 0x41   # something after the name's end
        self.assertFalse(recipes.identify(bytes(img), **self.k).known)

    def test_a_controller_with_the_old_name_is_still_recognised(self):
        st = recipes.identify(self.with_old_name(self.built, self.GATT, self.SCAN), **self.k)
        self.assertEqual((st.known, st.recipes), (True, ("bt_xbox",)))

    def test_any_other_name_is_unknown(self):
        bad = bytearray(self.built)
        bad[self.GATT:self.GATT + 3] = b"XXX"
        self.assertFalse(recipes.identify(bytes(bad), **self.k).known)

    def test_every_combination_installed_with_the_old_name_is_still_recognised(self):
        for keys in VALID + [k + [r] for k in VALID for r in RING]:
            if "bt_xbox" not in keys:
                continue
            st = recipes.identify(self.with_old_name(recipes.build(self.stock, keys, **self.k), self.GATT, self.SCAN), **self.k)
            self.assertEqual((st.known, st.recipes), (True, tuple(sorted(keys))), keys)

    def test_the_published_tweaks_keep_their_bytes(self):
        """Firmware installed by 0.1.x, 0.2.0, 0.3.0 and 0.4.0 is only recognised while these match: changing a blob needs an `accepts` for the old one."""
        published = {
        "packer.bin": "1ba223dee721be22c4a64575784013bedebb0e5706b5bd4a93cd760b985424e9",
        "noop.bin": "7817034cdd42530d23894be6e9dd41a777ec2a407cc73c69ac0665fc047e4d1e",
        "rumble02.bin": "7948fb82193d6e9f033d83fe4efe015adebc5fe33b3570b6aff6344f11ae0426",
        "led.bin": "7876889cb9c2981ee4e78338f2eca0589f431d509f361f6b5d029f5eeb7eecd1",
        "hook.bin": "012f37b995249381906e85cb32b127cfebd08f92ed34253b1d93ffe690b8a28e",
        "report-map.bin": "a681e2cb8b5d9466f258e2f07f0fcc518ec438231af1b61b125c213d2c62e165",
        "ring02.bin": "3230e6f5f644389a1cf7de4d8d12f7c0c5994b1d3fefa3156e1658b708336b35",
        "ringhook02.bin": "36bb496143d7a87026b50fe0220ef246c168c3b6b6251f4286b33edf1498222d",
        "rumble03.bin": "f4be669cb33f916fa8e494c6b7d77bd2c29d165fff7fee62ccf9b7952c6e092d",
        "ringhook03.bin": "c5c17678cb98c19b506922d7b7a33a82dfe50a9e144183eab2ed7d6ff4facd0d",
        "battlog11.bin": "a928125ce55ea206b7c432f4c77b83f92ebfa2d532d8f5c1e2830d7d2e692244",   # 1.1: the battery log
        "wdog.bin": "ee8ef69d98f097cc6567ff6f16a29b5cf7fceedb6055bbf87db778fcdd2590a8",      # 1.0: Processor rest
        "stall.bin": "f4c60c286a6141864bc56fee16cd98f8d00e8e8845ba11269901874301bebe36",
        "hstall.bin": "6dd074c9b178b74f2391e6f72f0c86bb3e04258456ab86d89a03b96e8872f0e3",
        "resthook.bin": "5b563ff31772634b7ab5357f39b9d40f0751216101cda2df58c60fd13a20100a",
        "ringtail03.bin": "ad42bf2804072507723597786cbcb4dd1650a9932523d8c355561a08844f0943",
        "pollhook03.bin": "5c15dd176afa3d1f0ad7a6b1ba8b47c08d0151eb21b3adecf93384608d30a21b",
        "vhooka03.bin": "a55172fb21ac5276c2c817c4b541762a44f9db77bb1bdbcef228af6761133ea9",
        "vhookb03.bin": "000981e512e1fef4ea304ba898cd2e74ce2f53636f8583f8ebfc4af0df08abf1",
        "hbuzza.bin": "e9c799c9b8c8dc2c84205e80f44aab7bb7583c0edf4b292cb1726764f4916b68",
        "hbuzzb.bin": "030dc683f5e6d6e46824dea6bd771693c8888c8fcfda37490f7e2e2a453edee8",
        "hgamea.bin": "09b2c2feac8fff2690e7be555675634d3933448e1041eee6c04cfa8500e5e241",
        "hgameb.bin": "676ce0dbd8e61ba4eb9b75b870f86e2dfd58c577321fb4c85f875a8940fd4747",
        "hidle.bin": "021a52c52e6f7932d3aa12059aca08a1cdcdf9126c3a66f1013b7248a9955546",
        "hidletask.bin": "140c10dba0cb03d49e0e8bada0d3f0544aa16764f65cc320aa2f7018e4e39e4f",
        "hota.bin": "e61616020a14bbe20d617776972a0fd2fd3c77034dc9fc8fb392c8679d1b5d26",
        "hscan.bin": "fab672f16abbe46b94882f28294d7b140259149b2ea3aead6503b8622243e861",
        "hstickl.bin": "f8e649a57bed7bb4b8fd75a48f895567b3ba5256f8d8f5fcebfda6332ed0178d",
        "hstickr.bin": "729e37e7e527fb10cbcb165f354a77851db6ed59ca155fe82556dbefad0786bf",
        "hsweep.bin": "285ef7b2d92ca8f32ab9b83ed3d3fe6adc4e760b54532a3802c5ceaa75be0d5c",
        "hunplug.bin": "6beee17a70d71fa42438a212ba1b33b3829acdde507e30348e323cee8f703fe6",
        "pollhook.bin": "39f5251bcf8179cd85115b631cbeecd771dbe39fb856e6bc245156828b84694e",
        "ringhook.bin": "e5c2e40947f7369c2b0e4afe98c39395a0a1b9f9248de7ef69283e12b0d02330",
        "ringtail10.bin": "0e7f0b38fd8de0b272a270066ca681e9c660cecf29265279e8b107b4bacc0ae8",   # 1.0-1.1: alerts at 15 % and 5 %
        "rumble.bin": "7acd917ab3f4dd6a54ab272a19f13e877a010dc17f9d8c899e28ea52125af5cf",
        "sram.bin": "c3f0966446812c97bb976ba0f28b9a4b8a158939d6f158107faa747c3b546699",
        "vhooka10.bin": "9d23fcfe77ae92d7215d02012a693d4a93778e695c6ab3f56954694ee94ff858",
        "vhookb10.bin": "e0c27de8ca721f16eb4541dc9e9a70cda2f7ed2ec67c5fa552317b1f9ae35bff",
        }
        for name, sha in published.items():
            self.assertEqual(hashlib.sha256((recipes.BLOBS / name).read_bytes()).hexdigest(), sha, name)
        self.assertEqual(recipes.PREVIOUS_BT_NAME, "8BitDo Ultimate 2C (X/S)")
        self.assertEqual([level for _, _, level in recipes.RING_LEVELS], [0, 1, 10, 50, 250, 0xFFFF])

    def test_the_old_name_alone_does_not_count_as_the_tweak(self):
        bad = bytearray(self.stock)
        bad[self.GATT:self.GATT + len(self.OLD)] = self.OLD
        self.assertFalse(recipes.identify(bytes(bad), **self.k).known)


class PastThePayloadTest(unittest.TestCase):
    def setUp(self):
        self.stock, self.k = synthetic()

    def test_stock_is_recognised_with_a_blank_tail(self):
        for img in (self.stock, recipes.whole(self.stock)):
            st = recipes.identify(img, **self.k)
            self.assertEqual((st.known, st.recipes), (True, ()))

    def test_a_non_blank_tail_with_no_recipe_is_unknown(self):
        bad = bytearray(recipes.whole(self.stock))
        bad[image.APP_END - image.BASE - 1] = 0
        self.assertFalse(recipes.identify(bytes(bad), **self.k).known)

    def test_build_covers_the_whole_app_area(self):
        self.assertEqual(len(recipes.build(self.stock, ["dim_leds"], **self.k)), image.APP_SIZE)

    def test_a_tweak_without_code_past_the_payload_leaves_it_blank(self):
        img = recipes.build(self.stock, ["dim_leds"], **self.k)
        self.assertEqual(img[image.BANK_A_SIZE:], b"\xff" * (image.APP_SIZE - image.BANK_A_SIZE))

    def test_the_ring_code_is_past_the_payload(self):
        img = recipes.build(self.stock, ["ring_5"], **self.k)
        self.assertNotEqual(img[0x18400 - image.BASE:0x18404 - image.BASE], b"\xff" * 4)


class UpdateNoticeTest(unittest.TestCase):
    """The rule: when a tweak changes, the person is told. A changed blob keeps its old bytes as an accepted form (see
    test_the_published_tweaks_keep_their_bytes), and every accepted form must make the app say an update exists."""
    ALL = ["bt_xbox", "bt_rumble", "cable_360", "dim_leds", "battery_alerts", "settings", "fixes", "stick_precision", "cpu_rest", "ring_5"]

    def setUp(self):
        self.stock, self.k = synthetic()

    def test_every_earlier_form_of_a_tweak_says_an_update_exists(self):
        keys = with_hidden(self.ALL)
        fresh = recipes.build(self.stock, keys, **self.k)
        st = recipes.identify(fresh, **self.k)
        self.assertEqual((st.known, st.older, update_available(st)), (True, False, False))
        checked = 0
        for k in keys:
            for e in recipes.RECIPES[k].edits:
                if e.text or (e.superseded_by in keys):   # names are the person's choice, not a tweak's code
                    continue
                for old in e.accepts:
                    img = bytearray(fresh); img[e.addr - image.BASE:e.addr - image.BASE + e.size] = old
                    st = recipes.identify(bytes(img), **self.k)
                    self.assertEqual((st.known, st.recipes), (True, tuple(sorted(keys))), (k, hex(e.addr)))
                    self.assertTrue(st.older and update_available(st), (k, hex(e.addr)))
                    checked += 1
        self.assertGreaterEqual(checked, 4)   # rumble 0.2 and 0.3, the 1.0-1.1 ring code, 1.1's battery log

    def test_a_hidden_tweak_that_every_install_has_now_says_an_update_exists(self):
        old = recipes.build(self.stock, [k for k in with_hidden(self.ALL) if k != "battery_table"], **self.k)   # what 1.1 installed
        st = recipes.identify(old, **self.k)
        self.assertTrue(st.known and not st.older and update_available(st))
        self.assertTrue(update_available(recipes.identify(recipes.build(self.stock, ["cpu_rest", "ring_5", "ble_interval", "battery_table"], **self.k), **self.k)), "1.0's Processor rest, without the log")
        self.assertFalse(update_available(recipes.identify(recipes.whole(self.stock), **self.k)), "the original firmware has nothing to update")


class FromEarlierVersionsTest(unittest.TestCase):
    """Firmware installed by 0.2.0 must stay recognised, with its level."""

    def setUp(self):
        self.stock, self.k = synthetic()

    def from_0_2(self, tweaks, level):
        img = bytearray(recipes.build(self.stock, tweaks, **self.k))
        rumble = (recipes.BLOBS / "rumble02.bin").read_bytes()
        if "bt_rumble" in tweaks:
            img[0x18390 - image.BASE:0x18400 - image.BASE] = rumble.ljust(0x70, b"\0")
        img[0xA3FA - image.BASE] = 0
        hook = (recipes.BLOBS / "ringhook02.bin").read_bytes()
        img[0xD4F0 - image.BASE:0xD4F4 - image.BASE] = hook
        img[0x7640 - image.BASE:0x76D0 - image.BASE] = (recipes.BLOBS / "ring02.bin").read_bytes().ljust(0x90, b"\0")
        img[RING_LEVEL_ADDR - image.BASE:RING_LEVEL_ADDR - image.BASE + 2] = level.to_bytes(2, "little")
        return bytes(img)

    def test_firmware_from_0_2_is_still_recognised(self):
        for ring, value in RING.items():
            for keys in ([], ["dim_leds"], KEYS):
                st = recipes.identify(self.from_0_2(keys, value), **self.k)
                self.assertEqual((st.known, st.recipes), (True, tuple(sorted(keys + [ring]))), ring)
                self.assertTrue(st.outdated, ring)

    def from_0_3(self, tweaks, ring):
        img = bytearray(recipes.build(self.stock, tweaks + [ring], **self.k))
        for addr, name in ((0xD4F0, "ringhook03"), (0xF83C, "vhooka03"), (0xF842, "vhookb03"), (0xA484, "pollhook03")):
            img[addr - image.BASE:addr - image.BASE + 4] = (recipes.BLOBS / f"{name}.bin").read_bytes()
        tail = (recipes.BLOBS / "ringtail03.bin").read_bytes().ljust(recipes.RING_CODE[1] - recipes.RING_CODE[0], b"\xff")
        img[recipes.RING_CODE[0] - image.BASE:recipes.RING_CODE[1] - image.BASE] = tail
        if "bt_rumble" in tweaks:
            img[0x18390 - image.BASE:0x18400 - image.BASE] = (recipes.BLOBS / "rumble03.bin").read_bytes().ljust(0x70, b"\0")
        return bytes(img)

    def test_firmware_from_0_3_is_still_recognised(self):
        for ring in list(RING) + ["ring_normal"]:
            for keys in ([], ["dim_leds"], KEYS, ["battery_alerts"]):
                st = recipes.identify(self.from_0_3(keys, ring), **self.k)
                self.assertEqual((st.known, st.recipes), (True, tuple(sorted(keys + [ring]))), ring)
                self.assertTrue(st.outdated, ring)

    def test_settings_need_a_ring_level(self):
        with self.assertRaises(RecipeError) as e:
            recipes.build(self.stock, ["settings"], **self.k)
        self.assertIn("Home ring", str(e.exception))

    def test_settings_build_and_identify_with_any_level(self):
        for ring in list(RING) + ["ring_normal"]:
            for keys in ([], KEYS):
                img = recipes.build(self.stock, keys + [ring, "settings"], **self.k)
                st = recipes.identify(img, **self.k)
                self.assertEqual((st.known, st.outdated, st.recipes), (True, False, tuple(sorted(keys + [ring, "settings"]))), ring)

    def test_settings_without_the_tail_is_unknown(self):
        bad = bytearray(recipes.build(self.stock, ["ring_5", "settings"], **self.k))
        bad[recipes.RING_CODE[0] - image.BASE:recipes.RING_CODE[1] - image.BASE] = b"\xff" * (recipes.RING_CODE[1] - recipes.RING_CODE[0])
        self.assertFalse(recipes.identify(bytes(bad), **self.k).known)

    def test_every_settings_hook_is_one_call_into_the_tail(self):
        def target(addr, b):
            hi, lo = int.from_bytes(b[:2], "little"), int.from_bytes(b[2:], "little")
            self.assertEqual((hi >> 11, lo >> 11), (0x12, 0x13), hex(addr))  # TC32's tjl
            off = (hi & 0x7FF) << 12 | (lo & 0x7FF) << 1
            return addr + 4 + (off - (1 << 23) if off & (1 << 22) else off)
        for e in recipes.RECIPES["settings"].edits:
            if e.addr in (recipes.SETTINGS_FLAG, 0x7210):  # a flag, and code rather than a call
                continue
            run = e.addr - 0x6000 if e.addr < 0x8500 else e.addr  # 0x6000-0x8500 runs from RAM, 0x6000 lower
            self.assertEqual(e.size, 4, hex(e.addr))
            t = target(run, e.new)
            self.assertTrue(recipes.RING_CODE[0] < t < recipes.RING_CODE[1] or 0x1210 <= t < 0x12D0, hex(e.addr))

    def test_fixes_need_a_ring_level_and_identify(self):
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, ["fixes"], **self.k)
        st = recipes.identify(recipes.build(self.stock, ["ring_5", "fixes"], **self.k), **self.k)
        self.assertEqual(st.recipes, ("fixes", "ring_5"))

    def test_fixes_points_the_update_handler_into_the_tail(self):
        img = recipes.build(self.stock, ["ring_5", "fixes"], **self.k)
        pointer = int.from_bytes(img[0x178CC - image.BASE:0x178D0 - image.BASE], "little")
        self.assertTrue(pointer & 1 and recipes.RING_CODE[0] < pointer < recipes.RING_CODE[1], hex(pointer))

    def test_published_hashes_cover_exactly_the_recipes_regions(self):
        from u2c_customfw.firmware import stock_hashes
        regions = {(e.addr, e.size) for r in recipes.RECIPES.values() for e in r.edits}
        self.assertEqual(set(stock_hashes.REGION_SHA256), regions, "run tools/make_constants.py after changing a recipe's edits")

    def test_softer_rumble_start_stays_out(self):
        self.assertNotIn("soft_rumble", recipes.RECIPES)  # dropped in 0.4; ble_interval is back in 0.5, hidden until tried

    def test_finer_sticks_change_the_threshold_in_all_four_places(self):
        edits = {e.addr: e.new for e in recipes.RECIPES["stick_precision"].edits}
        for addr in (0xB1F2, 0xB208, 0xB220, 0xB238):
            self.assertEqual(edits[addr], bytes([recipes.STICK_THRESHOLD]), hex(addr))
        self.assertEqual(recipes.STICK_THRESHOLD, 14)
        self.assertEqual(len(edits[0xA2DC]), 4)

    def test_finer_sticks_need_the_tail(self):
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, ["stick_precision"], **self.k)

    def test_all_new_tweaks_together_with_every_other(self):
        keys = KEYS + ["ring_5", "battery_alerts", "settings", "fixes", "stick_precision"]
        st = recipes.identify(recipes.build(self.stock, keys, **self.k), **self.k)
        self.assertEqual((st.known, st.recipes), (True, tuple(sorted(keys))))

    FOUR = KEYS + ["ring_5", "battery_alerts", "settings", "fixes", "stick_precision"]  # what 0.4.0 installs

    def test_cpu_rest_installs_the_hooks_the_islands_and_the_flag(self):
        img = recipes.build(self.stock, self.FOUR + ["cpu_rest"], **self.k)
        at = lambda a, n: img[a - image.BASE:a - image.BASE + n]
        for addr, name in ((0xA484, "resthook.bin"), (0xA4AE, "hstall.bin"), (0x6020, "wdog5.bin"), (0x848C, "stall.bin")):
            blob = recipes._blob(name)
            self.assertEqual(at(addr, len(blob)), blob, name)
        self.assertEqual(at(0x18FF2, 1), b"\1")
        st = recipes.identify(img, **self.k)
        self.assertEqual((st.known, st.recipes), (True, tuple(sorted(self.FOUR + ["cpu_rest"]))))

    def test_the_battery_log_comes_with_cpu_rest_and_1_0s_cpu_rest_is_still_recognised(self):
        img = recipes.build(self.stock, self.FOUR + ["cpu_rest", "battery_log"], **self.k)
        at = lambda a, n: img[a - image.BASE:a - image.BASE + n]
        self.assertEqual(at(0xA484, 4), recipes._blob("loghook.bin"))   # the 100 ms call goes to the log first
        self.assertEqual(at(0x9964, len(recipes._blob("battlog.bin"))), recipes._blob("battlog.bin"))
        self.assertEqual(at(0x6020, len(recipes._blob("wdog5.bin"))), recipes._blob("wdog5.bin"))   # Processor rest's own code as before
        st = recipes.identify(img, **self.k)
        self.assertEqual((st.known, st.recipes), (True, tuple(sorted(self.FOUR + ["cpu_rest", "battery_log"]))))
        with_11 = bytearray(img)   # 1.1's log, which unlocked the flash, still counts as installed
        with_11[0x9964 - image.BASE:0x9CAC - image.BASE] = recipes._blob("battlog11.bin").ljust(0x9CAC - 0x9964, b"\0")
        self.assertEqual(recipes.identify(bytes(with_11), **self.k).recipes, st.recipes)
        old = recipes.build(self.stock, self.FOUR + ["cpu_rest"], **self.k)   # what 1.0 installed
        self.assertEqual(recipes.identify(old, **self.k).recipes, tuple(sorted(self.FOUR + ["cpu_rest"])))
        ring_only = recipes.build(self.stock, KEYS + ["ring_5"], **self.k)    # the call as the ring alone has it
        self.assertEqual(recipes.identify(ring_only, **self.k).recipes, tuple(sorted(KEYS + ["ring_5"])))
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, self.FOUR + ["battery_log"], **self.k)
        from u2c_customfw.ops import shown
        self.assertNotIn("battery_log", shown(st.recipes))

    def test_cpu_rest_needs_a_ring_level(self):
        with self.assertRaises(RecipeError) as e:
            recipes.build(self.stock, ["cpu_rest"], **self.k)
        self.assertIn("ring level", str(e.exception))

    def test_the_app_offers_cpu_rest_on_by_default(self):
        html = (Path(recipes.__file__).parents[1] / "web" / "static" / "index.html").read_text()
        self.assertIn('value="cpu_rest" checked', html)
        js = (Path(recipes.__file__).parents[1] / "web" / "static" / "app.js").read_text()
        self.assertIn('"cpu_rest"', js.split("RING_CODE_USERS = ")[1].split(";")[0])  # it needs the ring's code

    def test_ble_interval_asks_for_12_5_ms(self):
        img = recipes.build(self.stock, ["ble_interval"], **self.k)
        self.assertEqual(img[0xA0DE - image.BASE:0xA0E6 - image.BASE], bytes.fromhex("0aa00aa100a2c8a3"))  # min 10, max 10, latency 0, timeout 200
        self.assertEqual(recipes.identify(img, **self.k).recipes, ("ble_interval",))

    def test_ble_fast_asks_for_7_5_ms_in_place_of_the_12_5_ms_request(self):
        img = recipes.build(self.stock, ["ble_interval", "ble_fast"], **self.k)
        self.assertEqual(img[0xA0DE - image.BASE:0xA0E6 - image.BASE], bytes.fromhex("06a006a100a2c8a3"))  # min 6, max 6, latency 0, timeout 200
        self.assertEqual(recipes.identify(img, **self.k).recipes, ("ble_fast", "ble_interval"))
        with self.assertRaises(RecipeError) as e:
            recipes.build(self.stock, ["ble_fast"], **self.k)
        self.assertIn("ble_interval", str(e.exception))   # it replaces the request every install makes

    def test_the_app_offers_faster_bluetooth_off_by_default(self):
        html = (Path(recipes.__file__).parents[1] / "web" / "static" / "index.html").read_text()
        self.assertIn('value="ble_fast">', html)

    def test_long_rumble_hooks_motor_set_and_puts_its_code_in_the_stock_report_map(self):
        keys = ["bt_xbox", "bt_rumble", "bt_rumble_long"]
        img = recipes.build(self.stock, keys, **self.k)
        at = lambda a, n: img[a - image.BASE:a - image.BASE + n]
        code = (recipes.BLOBS / "longrumble.bin").read_bytes()
        start, end = recipes.LONG_RUMBLE_CODE
        self.assertEqual(at(start, end - start), code.ljust(end - start, b"\0"))   # the map bt_xbox no longer points at

        def target(a):   # where the tjl at a goes: Thumb's bl with each halfword's top nibble F turned into 9
            hi, lo = int.from_bytes(at(a, 2), "little"), int.from_bytes(at(a + 2, 2), "little")
            self.assertEqual((hi >> 11, lo >> 11), (0b10010, 0b10011))
            off = (hi & 0x7FF) << 12 | (lo & 0x7FF) << 1
            return a + 4 + off - (off & 1 << 22) * 2
        self.assertEqual(target(0xFA72), start)                       # instead of motor_set's two shifts of the hold: long_hold
        self.assertTrue(start < target(0xFA88) < start + len(code))   # instead of its read of the hold on a zero-hold call: rearm
        callback = int.from_bytes(at(0x17764, 4), "little")             # the output report's write callback: long_write
        self.assertEqual(at(0x17764, 4), (recipes.BLOBS / "lrwrite.bin").read_bytes())
        self.assertTrue(callback & 1 and start < callback < start + len(code))
        self.assertEqual(recipes.build(self.stock, keys[:2], **self.k)[0x17764 - image.BASE:0x17768 - image.BASE], (0x18391).to_bytes(4, "little"))
        self.assertEqual(recipes.identify(img, **self.k).recipes, tuple(sorted(keys)))
        self.assertEqual(recipes.identify(recipes.build(self.stock, keys[:2], **self.k), **self.k).recipes, ("bt_rumble", "bt_xbox"))
        with self.assertRaises(RecipeError) as e:
            recipes.build(self.stock, ["bt_xbox", "bt_rumble_long"], **self.k)
        self.assertIn("bt_rumble", str(e.exception))

    def test_the_app_offers_long_rumble_off_by_default(self):
        html = (Path(recipes.__file__).parents[1] / "web" / "static" / "index.html").read_text()
        self.assertIn('value="bt_rumble_long">', html)

    def test_a_cable_name_is_chosen_with_the_xbox_360_tweak(self):
        plain = recipes.build(self.stock, ["cable_360"], names={"cable_360": "Q"}, **self.k)
        default = recipes.build(self.stock, ["cable_360"], **self.k)   # no name chosen: 8BitDo's, bytes as they were
        stock_name = recipes.STOCK_CABLE_NAME   # 8BitDo's bytes there (checked against the real 1.09 in RealStockTest)
        self.assertEqual(default[0x1792C - image.BASE:0x1792C - image.BASE + 0x4E], bytes([0x4E, 3]) + stock_name.encode("utf-16-le"))
        self.assertEqual(default[0xE91C - image.BASE], 0x4E)
        self.assertEqual(recipes.identify(default, **self.k).names, {"cable_360": recipes.STOCK_CABLE_NAME})
        name = recipes.CABLE_NAME   # the suggestion, chosen
        img = recipes.build(self.stock, ["cable_360"], names={"cable_360": name}, **self.k)
        at = lambda a, n: img[a - image.BASE:a - image.BASE + n]
        n = 2 + 2 * len(name)
        self.assertEqual(at(0x1792C, 2), bytes([n, 3]))                       # a string descriptor of 54 bytes
        self.assertEqual(at(0x1792C + 2, n - 2).decode("utf-16-le"), name)
        self.assertEqual(at(0x1792C + n, 0x4E - n), bytes(0x4E - n))           # the rest of 8BitDo's 78-byte name, cleared
        self.assertEqual(at(0xE91C, 1), bytes([n]))                           # movs r3, #n: the length it's sent with
        self.assertEqual([i + image.BASE for i in range(len(img)) if img[i] != plain[i]][:1], [0xE91C])   # nothing else differs
        st = recipes.identify(img, **self.k)
        self.assertEqual((st.recipes, st.names), (("cable_360",), {"cable_360": name}))
        img = recipes.build(self.stock, ["cable_360"], names={"cable_360": "Z" * 38}, **self.k)
        self.assertEqual(img[0x1792C - image.BASE:0x1792C - image.BASE + 78], bytes([78, 3]) + "Z".encode("utf-16-le") * 38)
        self.assertEqual(recipes.identify(img, **self.k).names, {"cable_360": "Z" * 38})
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, ["cable_360"], names={"cable_360": "Z" * 39}, **self.k)

    def test_the_app_offers_a_name_by_cable_under_the_xbox_360_switch(self):
        html = (Path(recipes.__file__).parents[1] / "web" / "static" / "index.html").read_text()
        self.assertIn('data-for="cable_360"', html)
        self.assertNotIn('value="cable_name"', html)

    def test_fast_scan_halves_the_scan_period_and_keeps_home_and_turbo_timings(self):
        img = recipes.build(self.stock, ["fast_scan"], **self.k)
        at = lambda a, n: img[a - image.BASE:a - image.BASE + n]
        self.assertEqual(at(0xD288, 4), (40000).to_bytes(4, "little"))  # 2.5 ms of the 16 MHz system tick
        self.assertEqual(at(0xC8E2, 2), bytes.fromhex("13aa"))  # Home counts 20 scans, still 50 ms
        self.assertEqual(at(0xC8E6, 2), bytes.fromhex("14a2"))
        self.assertEqual(at(0xC640, 2), bytes.fromhex("0bae"))  # turbo's phase every 12 scans, still 30 ms
        self.assertEqual(recipes.identify(img, **self.k).recipes, ("fast_scan",))

    def test_the_app_offers_faster_buttons_but_not_the_connection_request(self):
        html = (Path(recipes.__file__).parents[1] / "web" / "static" / "index.html").read_text()
        self.assertIn('value="fast_scan"', html)
        self.assertNotIn('value="ble_interval"', html)  # no switch: every install includes it (app.js), macOS keeps its 15 ms anyway
        js = (Path(recipes.__file__).parents[1] / "web" / "static" / "app.js").read_text()
        self.assertIn('"ble_interval"', js.split("const tweaks = ")[1].split("\n")[0])

    def test_firmware_from_0_4_is_still_recognised_without_cpu_rest(self):
        """0.4.0's blobs are pinned in test_the_published_tweaks_keep_their_bytes, so this is what a controller on 0.4.0 holds."""
        for keys in ([], ["ring_5"], self.FOUR):
            st = recipes.identify(recipes.build(self.stock, keys, **self.k), **self.k)
            self.assertEqual((st.known, st.recipes, st.outdated), (True, tuple(sorted(keys)), False), keys)

    def test_current_firmware_is_not_outdated(self):
        self.assertFalse(recipes.identify(recipes.build(self.stock, ["ring_5"], **self.k), **self.k).outdated)

    def test_earlier_versions_cant_be_installed(self):
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, ["ring_5@0.2"], **self.k)

    def test_the_new_hook_without_its_code_is_unknown(self):
        bad = bytearray(recipes.build(self.stock, ["ring_5"], **self.k))
        bad[0x18400 - image.BASE:0x18FF0 - image.BASE] = b"\xff" * (0x18FF0 - 0x18400)
        self.assertFalse(recipes.identify(bytes(bad), **self.k).known)


class BatteryAlertsTest(unittest.TestCase):
    def setUp(self):
        self.stock, self.k = synthetic()

    def test_alerts_need_a_ring_level(self):
        with self.assertRaises(RecipeError):
            recipes.build(self.stock, ["battery_alerts"], **self.k)

    def test_alerts_identify_with_each_level(self):
        for ring in [*RING, "ring_normal"]:
            st = recipes.identify(recipes.build(self.stock, [ring, "battery_alerts"], **self.k), **self.k)
            self.assertEqual((st.known, st.recipes), (True, tuple(sorted([ring, "battery_alerts"]))), ring)

    def test_alerts_without_the_ring_are_unknown(self):
        img = bytearray(recipes.whole(self.stock))
        img[0x18FF0 - image.BASE] = 1
        self.assertFalse(recipes.identify(bytes(img), **self.k).known)


class RingTest(unittest.TestCase):
    def setUp(self):
        self.stock, self.k = synthetic()

    def test_each_level_identifies_alone_and_with_the_other_tweaks(self):
        for ring in RING:
            for keys in ([], ["dim_leds"], KEYS):
                st = recipes.identify(recipes.build(self.stock, keys + [ring], **self.k), **self.k)
                self.assertTrue(st.known, ring)
                self.assertEqual(st.recipes, tuple(sorted(keys + [ring])))

    def test_each_level_stores_its_pwm_value(self):
        for ring, value in RING.items():
            img = recipes.build(self.stock, [ring], **self.k)
            self.assertEqual(int.from_bytes(img[RING_LEVEL_ADDR - image.BASE:RING_LEVEL_ADDR - image.BASE + 2], "little"), value, ring)

    def test_levels_differ_only_in_the_level_word(self):
        a, b = (recipes.build(self.stock, [k], **self.k) for k in ("ring_1", "ring_25"))
        at = RING_LEVEL_ADDR - image.BASE
        self.assertEqual(a[:at] + a[at + 2:], b[:at] + b[at + 2:])

    def test_two_levels_are_refused(self):
        with self.assertRaises(RecipeError) as e:
            recipes.build(self.stock, ["ring_1", "ring_5"], **self.k)
        self.assertIn("one", str(e.exception))

    def test_a_level_value_no_recipe_has_makes_the_image_unknown(self):
        bad = bytearray(recipes.build(self.stock, ["ring_1"], **self.k))
        bad[RING_LEVEL_ADDR - image.BASE] = 77
        self.assertFalse(recipes.identify(bytes(bad), **self.k).known)

    def test_a_half_installed_ring_makes_the_image_unknown(self):
        bad = bytearray(recipes.build(self.stock, ["ring_1"], **self.k))
        bad[RING_LEVEL_ADDR - image.BASE:RING_LEVEL_ADDR - image.BASE + 2] = self.stock[RING_LEVEL_ADDR - image.BASE:RING_LEVEL_ADDR - image.BASE + 2]
        self.assertFalse(recipes.identify(bytes(bad), **self.k).known)


@unittest.skipUnless(os.environ.get("U2C_STOCK_DAT"), "set U2C_STOCK_DAT to a local ultimate-2c-controller-v1.09.dat")
class RealStockTest(unittest.TestCase):
    def setUp(self):
        self.stock = Path(os.environ["U2C_STOCK_DAT"]).read_bytes()[0x1C:]

    def test_stock_identifies(self):
        st = recipes.identify(self.stock)
        self.assertEqual((st.known, st.recipes), (True, ()))

    def test_with_no_name_chosen_the_cable_name_stays_8bitdos(self):
        img = recipes.build(self.stock, ["cable_360"])
        for a, n in ((0x1792C, 0x4E), (0xE91C, 1)):
            self.assertEqual(img[a - image.BASE:a - image.BASE + n], self.stock[a - image.BASE:a - image.BASE + n])

    def test_matches_the_payload_verified_on_hardware(self):
        if "U2C_REF_PAYLOAD" not in os.environ:  # a payload flashed and verified on a real controller (not in the repo)
            self.skipTest("set U2C_REF_PAYLOAD to compare with a payload verified on hardware")
        ref = Path(os.environ["U2C_REF_PAYLOAD"])
        self.assertEqual(recipes.build(self.stock, ["bt_xbox", "bt_rumble", "cable_360"]), ref.read_bytes())

    def test_all_combinations_identify(self):
        for keys in VALID + [[r] for r in RING] + [KEYS + [r] for r in RING]:
            self.assertEqual(recipes.identify(recipes.build(self.stock, keys)).recipes, tuple(sorted(keys)))

    def test_names_equal_to_8bitdos_own_still_identify(self):
        # 8BitDo's Bluetooth name, chosen with the Xbox tweak, writes 8BitDo's bytes in some of its places: still the tweak
        img = recipes.build(self.stock, ["bt_xbox", "cable_360"], names={"bt_xbox": "8BitDo Ultimate 2C Wireless"})
        st = recipes.identify(img)
        self.assertEqual((st.known, st.recipes, st.names["bt_xbox"]), (True, ("bt_xbox", "cable_360"), "8BitDo Ultimate 2C Wireless"))
        # the Xbox 360 tweak as earlier versions installed it, with 8BitDo's cable name, is still that tweak
        img = recipes.build(self.stock, ["cable_360"], names={"cable_360": recipes.STOCK_CABLE_NAME})
        self.assertEqual(img[0x1792C - image.BASE:0x1792C - image.BASE + 0x4E], self.stock[0x1792C - image.BASE:0x1792C - image.BASE + 0x4E])
        self.assertEqual(img[0xE91C - image.BASE], self.stock[0xE91C - image.BASE])
        self.assertEqual((recipes.identify(img).recipes, recipes.identify(img).names), (("cable_360",), {"cable_360": recipes.STOCK_CABLE_NAME}))
        self.assertEqual(recipes.identify(recipes.build(self.stock, ["cable_360"])).names, {"cable_360": recipes.STOCK_CABLE_NAME})   # none chosen
        self.assertEqual(recipes.identify(self.stock).names, {})


if __name__ == "__main__":
    unittest.main()
