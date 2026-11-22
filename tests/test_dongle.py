# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import random
import unittest

from u2c_customfw.ops import dongle
from u2c_customfw.ops.plan import OpError
from u2c_customfw.sim import SimApp

rnd = random.Random(5)


def made(bank: bytes, *keys) -> bytes:
    """bank with each tweak's regions as 8BitDo has them, or as the tweak makes them for keys."""
    out = bytearray(bank)
    for key, (_, edits) in dongle.TWEAKS.items():
        for a, old, new in edits:
            out[a - dongle.BASE:a - dongle.BASE + len(old)] = new if key in keys else old
    for n in dongle.NAMES.values():   # 8BitDo's names, as the app installed the tweaks before names: these are such installs
        out[n.at - dongle.BASE:n.at - dongle.BASE + n.size] = dongle._slot(n, n.stock)
        out[n.length_at - dongle.BASE] = 2 + 2 * len(n.stock)
        for a, old, _ in n.code:
            out[a - dongle.BASE:a - dongle.BASE + len(old)] = old
    return bytes(out)


def named(bank: bytes, *keys, **names) -> bytes:
    """As the app installs the tweaks with a name chosen for each (names, else the suggestion), and D-input's choice of string."""
    out = bytearray(made(bank, *keys))
    for key, n in dongle.NAMES.items():
        if key in keys:
            text = names.get(key, n.suggestion)
            desc = bytes([2 + 2 * len(text), 3]) + text.encode("utf-16-le")
            out[n.at - dongle.BASE:n.at - dongle.BASE + n.size] = desc.ljust(n.size, b"\0")
            out[n.length_at - dongle.BASE] = len(desc)
            for a, _, code in n.code:
                out[a - dongle.BASE:a - dongle.BASE + len(code)] = code
    return bytes(out)


STOCK = made(rnd.randbytes(dongle.SIZE))
XBOX, DINPUT, BOTH = made(STOCK, "dongle_360"), made(STOCK, "dongle_dinput"), made(STOCK, "dongle_360", "dongle_dinput")   # before names
NXBOX, NDINPUT, NBOTH = named(STOCK, "dongle_360"), named(STOCK, "dongle_dinput"), named(STOCK, "dongle_360", "dongle_dinput")   # names chosen
SUGGESTED = {k: n.suggestion for k, n in dongle.NAMES.items()}
OLD360 = {"dongle_360": "8BitDo Ultimate 2C Wireless Controller"}   # what an install before names reads as
K = dict(stock_sha256=hashlib.sha256(STOCK).hexdigest())


def flash(bank_a: bytes, version: int = 0x67) -> bytes:
    f = bytearray(b"\xff" * 0x20000)
    f[dongle.BASE:dongle.BASE + dongle.SIZE] = bank_a
    f[dongle.RECORD + 0x0C] = version
    return bytes(f)


class DongleFirmwareTest(unittest.TestCase):
    def test_original_firmware(self):
        self.assertEqual(dongle.read_firmware(SimApp(flash(STOCK)), **K), dongle.DongleFirmware(True, 1.03, ()))

    def test_xbox_360_tweak(self):
        self.assertEqual(dongle.read_firmware(SimApp(flash(XBOX)), **K), dongle.DongleFirmware(True, 1.03, ("dongle_360",), OLD360))
        self.assertEqual(dongle.read_firmware(SimApp(flash(NXBOX)), **K), dongle.DongleFirmware(True, 1.03, ("dongle_360",), {"dongle_360": "8BitDo Ultimate 2C (Xbox 360)"}))

    def test_d_input_tweak_alone_and_with_the_xbox_360_one(self):
        self.assertEqual(dongle.read_firmware(SimApp(flash(DINPUT)), **K), dongle.DongleFirmware(True, 1.03, ("dongle_dinput",), {"dongle_dinput": None}))
        self.assertEqual(dongle.read_firmware(SimApp(flash(BOTH)), **K), dongle.DongleFirmware(True, 1.03, ("dongle_360", "dongle_dinput"), OLD360 | {"dongle_dinput": None}))
        self.assertEqual(dongle.read_firmware(SimApp(flash(NBOTH)), **K).names, {"dongle_360": "8BitDo Ultimate 2C (Xbox 360)", "dongle_dinput": "8BitDo Ultimate 2C (D-input)"})

    def test_a_tweak_found_only_in_part_is_unknown(self):
        hook = dongle.TWEAKS["dongle_dinput"][1][0]
        part = bytearray(STOCK); part[hook[0] - dongle.BASE:hook[0] - dongle.BASE + 4] = hook[2]   # the call without its code
        self.assertEqual(dongle.state(bytes(part), **K), (False, ()))
        other = bytearray(XBOX); other[100] ^= 1   # a byte no tweak touches
        self.assertEqual(dongle.state(bytes(other), **K), (False, ()))

    def test_the_link_up_call_goes_to_link_mode_after_the_image(self):
        def bl(run, code):   # a TC32 bl: Thumb's with the top nibble 9 instead of f
            hi, lo = int.from_bytes(code[:2], "little"), int.from_bytes(code[2:], "little")
            self.assertEqual((hi >> 11, lo >> 11), (0x12, 0x13))
            off = (hi & 0x7FF) << 12 | (lo & 0x7FF) << 1
            return run + 4 + off - (off & 0x400000) * 2
        (call, stock, new), (code, blank, blob) = dongle.TWEAKS["dongle_dinput"][1]
        self.assertEqual(bl(call - dongle.BASE, stock), 0xDE28)   # RAM code runs at image - 0x6000: was init_usb_xinput
        self.assertEqual(bl(call - dongle.BASE, new), code)
        self.assertTrue(0xFF44 <= code and code + len(blob) <= dongle.END and blank == bytes(len(blob)) and code % 4 == 0)
        calls = [bl(code + i, blob[i:i + 4]) for i in range(0, len(blob) - 3, 2) if blob[i + 1] >> 3 == 0x12 and blob[i + 3] >> 3 == 0x13]
        self.assertEqual(sorted(calls), [0xDC9C, 0xDDF8, 0xDE28])   # dongle.s assembled for this address: re-enumerate, idle, X-input

    def test_rf_diagnostic_works_with_dinput_and_xbox_tweaks(self):
        for chosen in (("dongle_rf_read",), ("dongle_dinput", "dongle_rf_read"),
                       ("dongle_360", "dongle_rf_read"),
                       ("dongle_360", "dongle_dinput", "dongle_rf_read")):
            bank = made(STOCK, *chosen)
            self.assertEqual(dongle.state(bank, **K), (True, tuple(k for k in dongle.TWEAKS if k in chosen)))
            if "dongle_dinput" in chosen:
                self.assertEqual(bank[0xff48-dongle.BASE:0xff84-dongle.BASE], DINPUT[0xff48-dongle.BASE:0xff84-dongle.BASE])
            self.assertEqual(bank[0xff88-dongle.BASE:0xff88-dongle.BASE+len(dongle.RF_READ)], dongle.RF_READ)

    def test_other_firmware_gives_the_version_of_its_record(self):
        self.assertEqual(dongle.read_firmware(SimApp(flash(rnd.randbytes(dongle.SIZE), 0x68)), **K), dongle.DongleFirmware(False, 1.04, ()))

    def test_a_dongle_that_stops_answering_is_an_error(self):
        class Silent:
            def xfer(self, *a, **k):
                return None
        with self.assertRaises(OpError) as e:
            dongle.read_firmware(Silent(), **K)
        self.assertIn("dongle", str(e.exception))

    def test_the_real_hash_is_set(self):
        self.assertEqual(len(dongle.STOCK_SHA256), 64)


class PairedDongleTest(unittest.TestCase):
    def find(self, version):
        from unittest import mock
        from u2c_customfw import transport
        from u2c_customfw.ops import devices
        info = transport.DeviceInfo(0x045E, 0x028E, 0xFF7A, "USB", "8BitDo Ultimate 2C Wireless Controller", None, version)
        with mock.patch.object(transport, "list_devices", lambda: [info]):
            return [f.mode for f in devices.find()]

    def test_the_patched_dongle_is_told_from_the_controller_by_its_usb_version(self):
        self.assertEqual(self.find(0x0103), ["dongle_paired"])
        self.assertEqual(self.find(0x0114), ["cable_360"])

    def test_the_cli_refuses_the_paired_dongle(self):
        from unittest import mock
        from u2c_customfw import cli
        from u2c_customfw.ops import devices
        from u2c_customfw import transport
        info = transport.DeviceInfo(0x045E, 0x028E, 0xFF7A, "USB", "x", None, 0x0103)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "dongle_paired")]):
            with self.assertRaises(cli.Exit) as e:
                cli.pick(("cable", "cable_360"))
        self.assertIn("2.4 GHz dongle", e.exception.msg)


class World:
    """One simulated dongle that can go from idle (301c) to its bootloader (3208) and back, sharing one flash."""
    def __init__(self, flash_bytes, start="dongle"):
        from u2c_customfw import transport
        self.flash, self.mode = bytearray(flash_bytes), start
        self.infos = {"dongle": transport.DeviceInfo(0x2DC8, 0x301C, 0xFFA0, "USB", "IDLE", None),
                      "boot": transport.DeviceInfo(0x2DC8, 0x3208, 0x8C, "USB", "boot", None)}
        self.sent = []

    def find(self):
        from u2c_customfw.ops import devices
        return [devices.Found(self.infos[self.mode], self.mode)]

    def open(self, info, rid):
        world = self
        if self.mode == "dongle":
            class Idle(SimApp):
                def xfer(self, req, timeout=0.5, accept=None):
                    world.sent.append(req[:5].hex())
                    if req[:5] == b"\x81\x05\x00\x51\x00":
                        world.mode = "boot"
                        return None
                    self.flash = bytes(world.flash)
                    return SimApp.xfer(self, req, timeout)
            return Idle(bytes(self.flash))
        from u2c_customfw.sim import SimBoot

        class Boot(SimBoot):
            def xfer(self, r, timeout=0.5, accept=None):
                if int.from_bytes(r[1:3], "little") == 7:
                    world.mode = "dongle"
                    return None
                return SimBoot.xfer(self, r, timeout)
        b = Boot(bytes(self.flash))
        b.f = self.flash
        return b


class DongleWriteTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        self.store = Path(tempfile.mkdtemp())
        self.full = bytearray(flash(STOCK))
        self.full[0:dongle.BOOT_END] = rnd.randbytes(dongle.BOOT_END)
        self.full[dongle.CAL:dongle.CAL + 0x40] = rnd.randbytes(0x40)
        self.full = bytes(self.full)
        p = mock.patch.object(dongle, "STOCK_SHA256", K["stock_sha256"]); p.start(); self.addCleanup(p.stop)

    def test_a_verified_write_isnt_an_error_when_the_restart_drops_the_device(self):
        import errno
        w = World(self.full)
        opened = w.open

        def open_dropping(info, rid):   # Linux: the node goes away as the bootloader restarts on command 7
            ch = opened(info, rid)
            if w.mode == "boot":
                xfer = ch.xfer
                def drop(r, timeout=0.5, accept=None):
                    if int.from_bytes(r[1:3], "little") == 7:
                        w.mode = "dongle"
                        raise OSError(errno.ENODEV, "No such device")
                    return xfer(r, timeout, accept)
                ch.xfer = drop
            return ch
        self.assertTrue(dongle.apply(w.find, open_dropping, self.full, XBOX, lambda *a: None, wait=0.01))
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), XBOX)

    def test_backup_reads_the_whole_dongle_twice_and_keeps_it(self):
        path = dongle.backup(SimApp(self.full), self.store, lambda *a: None)
        self.assertTrue(path.name.startswith("dongle-"))
        self.assertEqual(path.read_bytes(), self.full)
        self.assertEqual([p for p, _ in dongle.unit_backups(self.store, dongle.unit_of(self.full))], [path])
        other = bytearray(self.full); other[dongle.CAL] ^= 1
        self.assertEqual(dongle.unit_backups(self.store, dongle.unit_of(bytes(other))), [])   # another dongle's calibration

    def test_target_derives_any_firmware_from_a_known_bank(self):
        self.assertEqual(dongle.target(STOCK, ["dongle_360"]), XBOX)          # no name chosen: 8BitDo's, as before names
        self.assertEqual(dongle.target(XBOX, []), STOCK)
        self.assertEqual(dongle.target(NXBOX, []), STOCK)
        self.assertEqual(dongle.target(XBOX, ["dongle_dinput"]), DINPUT)
        self.assertEqual(dongle.target(STOCK, ["dongle_dinput", "dongle_360"]), BOTH)
        self.assertEqual(dongle.target(BOTH, ["dongle_360"]), XBOX)
        self.assertEqual(dongle.target(NBOTH, ["dongle_360"]), XBOX)
        self.assertEqual(dongle.target(STOCK, ["dongle_360"], {"dongle_360": SUGGESTED["dongle_360"]}), NXBOX)   # chosen
        self.assertEqual(dongle.target(XBOX, ["dongle_360", "dongle_dinput"], SUGGESTED), NBOTH)
        self.assertEqual(dongle.target(NBOTH, ["dongle_360", "dongle_dinput"], {"dongle_360": "", "dongle_dinput": ""}), BOTH)   # "": 8BitDo's
        with self.assertRaises(OpError):
            dongle.target(rnd.randbytes(dongle.SIZE), ["dongle_360"])

    def test_installing_enters_update_mode_writes_only_bank_a_and_comes_back(self):
        w = World(self.full)
        dongle.apply(w.find, w.open, self.full, XBOX, lambda *a: None, wait=0.01)
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), XBOX)
        self.assertEqual(bytes(w.flash[:dongle.BASE]), self.full[:dongle.BASE])
        self.assertEqual(bytes(w.flash[dongle.END:]), self.full[dongle.END:])   # record, bank B, calibration, licence untouched
        self.assertIn("81050051 00".replace(" ", ""), w.sent)
        self.assertEqual(w.mode, "dongle")

    def test_a_dongle_already_in_update_mode_can_be_restored(self):
        broken = bytearray(self.full); broken[dongle.BASE:dongle.END] = b"\xff" * dongle.SIZE
        w = World(bytes(broken), start="boot")
        dongle.apply(w.find, w.open, self.full, STOCK, lambda *a: None, wait=0.01)
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), STOCK)

    def test_another_dongles_bootloader_stops_the_write_before_erasing(self):
        w = World(self.full)
        other = bytearray(self.full); other[10] ^= 1
        with self.assertRaises(OpError) as e:
            dongle.apply(w.find, w.open, bytes(other), XBOX, lambda *a: None, wait=0.01)
        self.assertIn("nothing was written", str(e.exception))
        self.assertEqual(bytes(w.flash), self.full)

    def test_a_staged_update_stops_the_write_before_update_mode_and_in_it(self):
        record = bytearray(self.full)
        record[dongle.RECORD + 0x18:dongle.RECORD + 0x1C] = dongle.STAGED_MAGIC
        record[dongle.RECORD + 0x20:dongle.RECORD + 0x24] = (0xA000).to_bytes(4, "little")
        for start in ("dongle", "boot"):
            w = World(bytes(record), start=start)
            with self.assertRaises(OpError) as e:
                dongle.apply(w.find, w.open, bytes(record), XBOX, lambda *a: None, wait=0.01)
            self.assertIn("staged", str(e.exception))
            self.assertEqual(bytes(w.flash), bytes(record))
            self.assertEqual(w.mode, start)   # idle: it never entered update mode
        record[dongle.RECORD + 0x20:dongle.RECORD + 0x24] = bytes(4)   # length 0, as on a tested dongle: nothing staged
        self.assertFalse(dongle.staged(bytes(record[dongle.RECORD:dongle.RECORD + 0x24])))

    def test_the_write_never_leaves_bank_a(self):
        with self.assertRaises(AssertionError):
            dongle.apply(None, None, self.full, STOCK + b"x", lambda *a: None)


class DongleCliTest(DongleWriteTest):
    def run_cmd(self, cmd, world, **kw):
        from types import SimpleNamespace
        from unittest import mock
        from u2c_customfw import cli, state
        state.accept(self.store)
        said = []
        args = SimpleNamespace(**{**dict(write=False, tweaks="", backup=None, download=False, source="backup", expect=None,
                                         accept_disclaimer=False, force_unknown=False), **kw})
        args.say, args.hint, args.progress = said.append, lambda t: None, lambda *a: None
        with mock.patch.object(cli, "find", world.find), mock.patch.object(cli, "open_channel", world.open), \
                mock.patch.object(dongle.time, "sleep", lambda s: None):
            cmd(args, self.store)
        return said

    def test_backup_paired_xinput_receiver_with_controller_identity(self):
        from u2c_customfw import cli, transport
        from u2c_customfw.ops import devices
        for mode, vendor, product in (("cable", 0x2DC8, 0x310A), ("cable_360", 0x045E, 0x028E)):
            with self.subTest(mode=mode):
                w = World(self.full)
                info = transport.DeviceInfo(vendor, product, 0xFF7A, "USB", "8BitDo", None, 0x114)
                w.find = lambda: [devices.Found(info, mode)]
                said = self.run_cmd(cli.cmd_backup, w)
                backups = list(self.store.glob("dongle-*.bin"))
                self.assertTrue(backups)
                self.assertTrue(all(p.read_bytes() == self.full for p in backups))
                self.assertFalse(list(self.store.glob("controller-*.bin")))
                self.assertIn("The dongle has", said[-1])
                self.assertEqual(bytes(w.flash), self.full)
                self.assertTrue(all(req.startswith("810505") for req in w.sent), w.sent)

    def test_back_up_then_install_then_restore_the_original(self):
        from u2c_customfw import cli
        w = World(self.full)
        said = self.run_cmd(cli.cmd_backup, w)
        self.assertIn("The dongle has the original firmware 1.03.", said[-1])
        said = self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360")
        self.assertTrue(said[-1].startswith("The check passed and nothing was written."), said)
        self.assertEqual(bytes(w.flash), self.full)   # a check only reads
        said = self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360", write=True)
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), XBOX)   # no name chosen: 8BitDo's
        self.assertTrue(said[-1].startswith("Done."), said)
        self.run_cmd(cli.cmd_restore, w, source="stock", write=True)
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), STOCK)

    def test_both_tweaks_install_together_and_an_unknown_one_is_refused(self):
        from u2c_customfw import cli
        w = World(self.full)
        self.run_cmd(cli.cmd_backup, w)
        said = self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360,dongle_dinput", write=True)
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), BOTH)
        self.assertIn("This installs firmware 1.03 with Xbox 360 controller over 2.4 GHz and D-input mode with B + Home on the dongle. "
                      "X-input name: “8BitDo Ultimate 2C Wireless Controller”. D-input name: “8BitDo Ultimate 2C Wireless Controller”.", said[0])
        said = self.run_cmd(cli.cmd_backup, w)
        self.assertIn("The dongle has firmware 1.03 with Xbox 360 controller over 2.4 GHz and D-input mode with B + Home.", said[-1])
        with self.assertRaises(cli.Exit) as e:
            self.run_cmd(cli.cmd_patch, w, tweaks="dongle_dinputt", write=True)
        self.assertEqual(e.exception.msg, "The dongle's tweaks are dongle_360, dongle_dinput, dongle_rf_tail, dongle_rf_read. Nothing was written.")
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), BOTH)

    def test_names_are_installed_and_checked(self):
        from u2c_customfw import cli
        w = World(self.full)
        self.run_cmd(cli.cmd_backup, w)
        said = self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360,dongle_dinput", xinput_name="My Dongle", dinput_name="My Dongle (D)", write=True)
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), named(STOCK, "dongle_360", "dongle_dinput", dongle_360="My Dongle", dongle_dinput="My Dongle (D)"))
        self.assertIn("X-input name: “My Dongle”. D-input name: “My Dongle (D)”.", said[0])
        self.assertEqual(dongle.names(bytes(w.flash[dongle.BASE:dongle.END]), **K), {"dongle_360": "My Dongle", "dongle_dinput": "My Dongle (D)"})
        with self.assertRaises(cli.Exit) as e:   # a name needs its tweak
            self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360", dinput_name="x", write=True)
        self.assertIn("--dinput-name needs the dongle_dinput tweak", e.exception.msg)
        with self.assertRaises(OpError):   # and must be text that fits
            self.run_cmd(cli.cmd_patch, w, tweaks="dongle_dinput", dinput_name="D" * 37, write=True)
        self.assertEqual(dongle.names(bytes(w.flash[dongle.BASE:dongle.END]), **K)["dongle_dinput"], "My Dongle (D)")   # nothing written
        self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360,dongle_dinput", write=True)   # reinstalling keeps the names chosen
        self.assertEqual(dongle.names(bytes(w.flash[dongle.BASE:dongle.END]), **K), {"dongle_360": "My Dongle", "dongle_dinput": "My Dongle (D)"})
        self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360,dongle_dinput", xinput_name="", dinput_name="", write=True)   # "": 8BitDo's
        self.assertEqual(bytes(w.flash[dongle.BASE:dongle.END]), BOTH)

    def test_a_name_whose_parts_disagree_or_without_its_tweak_is_unknown(self):
        bad = bytearray(NBOTH); bad[dongle.NAMES["dongle_dinput"].length_at - dongle.BASE] += 2   # the length sent isn't the name's
        self.assertFalse(dongle.state(bytes(bad), **K)[0])
        bad = bytearray(NXBOX)
        for a, _, code in dongle.NAMES["dongle_dinput"].code:   # D-input's string choice without the D-input tweak
            bad[a - dongle.BASE:a - dongle.BASE + len(code)] = code
        self.assertFalse(dongle.state(bytes(bad), **K)[0])
        bad = bytearray(NBOTH); bad[dongle.NAMES["dongle_360"].at - dongle.BASE + 2] = 0x01   # not printable text
        self.assertFalse(dongle.state(bytes(bad), **K)[0])

    def test_check_first_says_an_update_is_staged(self):
        from u2c_customfw import cli
        record = bytearray(self.full)
        record[dongle.RECORD + 0x18:dongle.RECORD + 0x1C] = dongle.STAGED_MAGIC
        record[dongle.RECORD + 0x20:dongle.RECORD + 0x24] = (0xA000).to_bytes(4, "little")
        w = World(bytes(record))
        self.run_cmd(cli.cmd_backup, w)
        with self.assertRaises(cli.Exit) as e:
            self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360")   # without --write
        self.assertIn("staged", e.exception.msg)
        self.assertEqual(bytes(w.flash), bytes(record))

    def test_without_a_backup_nothing_is_written(self):
        from u2c_customfw import cli
        w = World(self.full)
        with self.assertRaises(cli.Exit) as e:
            self.run_cmd(cli.cmd_patch, w, tweaks="dongle_360", write=True)
        self.assertIn("no backup of this dongle", e.exception.msg)
        self.assertEqual(w.sent, [s for s in w.sent if s.startswith("810505")])   # only reads went out

    def test_a_dongle_left_in_update_mode_restores_its_backup(self):
        from u2c_customfw import cli
        w = World(self.full)
        self.run_cmd(cli.cmd_backup, w)
        w.flash[dongle.BASE:dongle.END] = b"\xff" * dongle.SIZE
        w.mode = "boot"
        self.run_cmd(cli.cmd_restore, w, source="backup", write=True)
        self.assertEqual(bytes(w.flash), self.full)
