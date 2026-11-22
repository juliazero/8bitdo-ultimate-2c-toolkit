# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from unittest import mock

from u2c_customfw import cli, state
from u2c_customfw.firmware import image, recipes
from u2c_customfw.ops import backup as bkp, battery as battery_ops, devices, probe, report
from u2c_customfw import transport
from u2c_customfw.protocol import app
from u2c_customfw.sim import SimApp, SimBoot
from tests.test_ops import K, STOCK_A, controller_flash
from u2c_customfw.web import jobs, server


class WebTest(unittest.TestCase):
    def setUp(self):
        self.store = Path(tempfile.mkdtemp())
        self.httpd, self.token = server.make_server(self.store, 0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.saved = dict(jobs.ACTIONS)
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), jobs.ACTIONS.update(self.saved)))

    def call(self, method, path, body=None, token="auto"):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body or {}).encode() if method == "POST" else None)
        if token:
            req.add_header("X-Token", self.token if token == "auto" else token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            e.close()
            return e.code, body

    def events(self):
        status, text = self.call("GET", "/api/events")
        self.assertEqual(status, 200)
        return [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ")]

    def test_the_battery_comes_from_the_controller_by_cable_and_from_macos_over_bluetooth(self):
        cable = transport.DeviceInfo(0x045E, 0x028E, 0xFF7A, "USB", "x", None)
        dev = SimApp(controller_flash())
        with mock.patch.object(cli, "find", lambda: [devices.Found(cable, "cable_360")]), mock.patch.object(cli, "open_channel", lambda i, rid: dev):
            self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]),
                             {"source": "cable", "percent": 77, "mv": 3870, "full": False, "external": True, "charging": True})
            held = state.lock_device(self.store)   # a task or the command line has the controller
            self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]), {"busy": True})
            state.unlock_device(held)
            dev.battery = None   # firmware without the command
            self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]), {"source": "cable", "unavailable": True})
        paired = SimApp(b"\xff" * 0x20000)   # a dongle with the controller's ID: its 0x10000 reads blank
        with mock.patch.object(cli, "find", lambda: [devices.Found(cable, "cable_360")]), mock.patch.object(cli, "open_channel", lambda i, rid: paired):
            self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]), {})
            self.assertFalse([s for s in paired.sent if s.startswith("8105005b")])
        bt = transport.DeviceInfo(0x045E, 0x0B13, 0x0001, "Bluetooth Low Energy", "8BitDo Ultimate 2C (X/S)", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(bt, "bluetooth_xbox")]), \
                mock.patch.object(battery_ops, "bluetooth_percent", lambda name: 64 if name == "8BitDo Ultimate 2C (X/S)" else None):
            self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]), {"source": "bluetooth", "percent": 64, "name": "8BitDo Ultimate 2C (X/S)"})
        with mock.patch.object(cli, "find", lambda: [devices.Found(bt, "bluetooth_xbox")]), mock.patch.object(battery_ops, "bluetooth_percent", lambda name: None):
            self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]), {"source": "bluetooth", "name": "8BitDo Ultimate 2C (X/S)"})   # macOS has no level
        with mock.patch.object(cli, "find", lambda: [devices.Found(bt, "bluetooth_xbox"), devices.Found(bt, "bluetooth_xbox")]):
            self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]), {})   # two Xbox-ID pads: which one is unknown
        self.assertEqual(self.call("GET", "/api/battery", token=None)[0], 403)

    def test_usb_dinput_battery_uses_the_same_vendor_channel_on_all_hosts(self):
        info = transport.DeviceInfo(0x2dc8, 0x301b, 1, "USB", "controller", b"hidraw")
        dev = SimApp(controller_flash())
        with mock.patch.object(cli, "find", return_value=[devices.Found(info, "cable_dinput")]), \
             mock.patch.object(cli, "open_channel", return_value=dev) as opening:
            data = json.loads(self.call("GET", "/api/battery")[1])
        self.assertEqual((data["source"], data["percent"], data["mv"]), ("cable", 77, 3870))
        opening.assert_called_once_with(info, 0x81)

    def test_battery_does_not_probe_xinput_only_or_receiver(self):
        for mode, vendor, product in (("xinput_only", 0x45e, 0x28e), ("dongle_paired", 0x2dc8, 0x301c)):
            info = transport.DeviceInfo(vendor, product, 1, "USB", "device", None)
            with self.subTest(mode=mode), mock.patch.object(cli, "find", return_value=[devices.Found(info, mode)]), \
                 mock.patch.object(cli, "open_channel") as opening:
                self.assertEqual(json.loads(self.call("GET", "/api/battery")[1]), {})
                opening.assert_not_called()

    def test_a_short_read_in_this_core_is_waited_for(self):
        runner = jobs.Runner(self.store)
        self.assertTrue(runner.claim())
        threading.Timer(0.3, runner.release).start()   # the battery poll, finishing
        self.assertTrue(runner.claim())   # the page's firmware read gets the controller instead of "another task"
        runner.release()

    def test_the_battery_log_is_read_by_cable_and_saved_as_csv(self):
        import struct
        flash = bytearray(controller_flash())
        rows = [struct.pack("<BBHHBB", 3, 0xC3, m, 4100 - m, 99, 2) for m in range(1, 181)]   # 3 h on battery, connected
        flash[0x53000:0x53000 + 8 * len(rows)] = b"".join(rows)
        info = transport.DeviceInfo(0x045E, 0x028E, 0xFF7A, "USB", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable_360")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(bytes(flash))), mock.patch.dict("os.environ", {"U2C_SIM": "1"}):
            got = json.loads(self.call("POST", "/api/battery-log")[1])
        self.assertEqual(got["records"], 180)
        self.assertIn("3.0 hours on battery from 1 power-on", got["summary"])
        lines = (self.store / got["name"]).read_text().splitlines()
        self.assertEqual(lines[0], "power_on,minute,mv,percent,connected,rumble,interval_ms")
        self.assertEqual(lines[1], "3,1,4099,99,1,0,12.48")
        self.assertEqual(len(lines), 181)
        blank = controller_flash()   # nothing logged yet
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable_360")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(blank)):
            self.assertEqual(json.loads(self.call("POST", "/api/battery-log")[1]), {"records": 0})
        with mock.patch.object(cli, "find", lambda: []):
            self.assertIn("directly by USB", json.loads(self.call("POST", "/api/battery-log")[1])["error"])

    def test_battery_log_exports_from_controller_bootloader_without_writes(self):
        import struct
        flash = bytearray(controller_flash())
        flash[0x53000:0x53008] = struct.pack("<BBHHBB", 1, 195, 1, 4000, 80, 2)
        device = SimBoot(bytes(flash))
        info = transport.DeviceInfo(0x2dc8, 0x3208, 0x8c, "USB", "boot", None)
        with mock.patch.object(cli, "find", return_value=[devices.Found(info, "boot")]), \
                mock.patch.object(cli, "open_channel", return_value=device) as opened, mock.patch.dict("os.environ", {"U2C_SIM": "1"}):
            result = json.loads(self.call("POST", "/api/battery-log")[1])
        self.assertEqual(result["records"], 1)
        opened.assert_called_once_with(info, 0)
        self.assertEqual(bytes(device.f), bytes(flash))
        self.assertFalse(self.httpd.runner.running)

    def test_battery_log_rejects_dongle_bootloader(self):
        device = SimBoot(bytes(0x20000))
        info = transport.DeviceInfo(0x2dc8, 0x3208, 0x8c, "USB", "boot", None)
        with mock.patch.object(cli, "find", return_value=[devices.Found(info, "boot")]), mock.patch.object(cli, "open_channel", return_value=device):
            result = json.loads(self.call("POST", "/api/battery-log")[1])
        self.assertIn("isn't the controller", result["error"])
        self.assertFalse(self.httpd.runner.running)

    def test_battery_log_does_not_open_wireless_connections(self):
        info = transport.DeviceInfo(0x45e, 0xb13, 1, "Bluetooth", "pad", None)
        for mode in ("bluetooth_xbox", "dongle", "dongle_paired"):
            with mock.patch.object(cli, "find", return_value=[devices.Found(info, mode)]), mock.patch.object(cli, "open_channel") as opened:
                result = json.loads(self.call("POST", "/api/battery-log")[1])
            self.assertIn("cannot be exported", result["error"])
            opened.assert_not_called()

    def test_report_endpoint_with_a_bad_body(self):
        with mock.patch.object(cli, "find", lambda: []):
            for body in ({"macos": "x"}, {"boot_identity": "x"}, {"macos": {"name": 5}}):
                self.assertEqual(self.call("POST", "/api/report", body)[0], 200)

    def test_store_folder_button_is_authenticated_and_only_opens_the_app_store(self):
        with mock.patch.object(server, "open_store") as opened:
            self.assertEqual(self.call("POST", "/api/open-store", token=None)[0], 403)
            opened.assert_not_called()
            self.assertEqual(self.call("POST", "/api/open-store", {"path": "some-other-folder"})[0], 200)
            opened.assert_called_once_with(self.store)
            opened.side_effect = OSError("file manager unavailable")
            status, body = self.call("POST", "/api/open-store")
            self.assertEqual(status, 422)
            self.assertIn("file manager unavailable", body)
        self.assertEqual(json.loads(self.call("GET", "/api/state")[1])["storage_dir"], str(self.store.resolve()))

    def test_store_folder_uses_windows_file_explorer(self):
        with mock.patch.object(server.sys, "platform", "win32"), mock.patch.object(server.os, "startfile", create=True) as opened:
            server.open_store(self.store)
        opened.assert_called_once_with(str(self.store.resolve()))

    def test_report_endpoint_uses_the_extras(self):
        with mock.patch.object(cli, "find", lambda: []):
            text = json.loads(self.call("POST", "/api/report", {"macos": {"name": "Pad"}, "pending": True})[1])["text"]
        self.assertIn("Pad", text)
        self.assertIn("Pending update: yes", text)

    def ring_call(self, body, flash=None, mode="cable_360"):
        built = recipes.build(STOCK_A, ["ring_5"], **K)
        info = transport.DeviceInfo(0x045E, 0x028E, 0xFF7A, "USB", "x", None)
        self.dev = SimApp(controller_flash(built) if flash is None else flash)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, mode)]), mock.patch.object(cli, "open_channel", lambda i, rid: self.dev):
            return self.call("POST", "/api/ring", body)

    def test_api_ring_sends_the_level(self):
        sent = []
        with mock.patch.object(app, "set_ring", lambda ch, level: sent.append(level)):
            self.assertEqual(self.ring_call({"level": 2})[0], 200)
        self.assertEqual(sent, [2])

    def test_api_ring_saves(self):
        sent = []
        with mock.patch.object(app, "save_ring", lambda ch: sent.append("save")):
            self.assertEqual(self.ring_call({"save": True})[0], 200)
        self.assertEqual(sent, ["save"])

    def test_api_ring_refuses_an_unknown_level(self):
        for level in (6, -1, "2", 1.5):
            self.assertEqual(self.ring_call({"level": level})[0], 400, level)
        self.assertEqual(self.ring_call({})[0], 400)

    def test_api_ring_refuses_without_the_tweak(self):
        status, text = self.ring_call({"level": 2}, flash=controller_flash())
        self.assertEqual(status, 409)
        self.assertIn("Home ring", text)

    def test_a_paired_dongle_is_refused_by_cable_calls(self):
        dongle = bytearray(controller_flash(recipes.build(STOCK_A, ["ring_5", "settings"], **K)))
        dongle[0x10000:0x10010] = b"\xff" * 16   # how probe tells the dongle, which shares the controller's USB ID when paired
        status, text = self.ring_call({"level": 2}, flash=bytes(dongle), mode="cable")
        self.assertEqual(status, 409)
        self.assertIn("2.4 GHz dongle", text)
        self.assertFalse(any(s.startswith(("81050052", "81050053")) for s in self.dev.sent))
        self.settings_device(mode="cable").flash = bytes(dongle)
        got = json.loads(self.call("GET", "/api/settings")[1])
        self.assertFalse(got["available"])
        self.assertIn("2.4 GHz dongle", got["note"])
        got = json.loads(self.call("GET", "/api/firmware")[1])   # a stock dongle with the controller on: what to do
        self.assertEqual(got["device"], "dongle")
        self.assertTrue(got["paired"])
        self.assertNotIn("error", got)

    def test_api_ring_refuses_in_update_mode(self):
        self.assertEqual(self.ring_call({"level": 2}, mode="boot")[0], 409)

    def test_xinput_receiver_with_controller_usb_id_is_reported_as_paired_dongle(self):
        from types import SimpleNamespace
        flash = bytearray(controller_flash())
        flash[0x10000:0x10010] = bytes([255]) * 16
        info = transport.DeviceInfo(0x2DC8, 0x310A, 0xFF7A, "USB", "8BitDo", None, 0x114)
        fw = SimpleNamespace(known=True, version=1.03, tweaks=("dongle_dinput",), names={})
        with mock.patch.object(cli, "find", return_value=[devices.Found(info, "cable")]), \
                mock.patch.object(cli, "open_channel", side_effect=lambda *args: SimApp(bytes(flash))), \
                mock.patch.object(server.dongle, "read_firmware", return_value=fw) as read, \
                mock.patch.object(server.dongle, "read_unit", return_value=b"unit"), \
                mock.patch.object(server.dongle, "unit_backups", return_value=[]):
            status, body = self.call("GET", "/api/firmware")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["device"], "dongle")
        self.assertTrue(data["paired"])
        self.assertEqual(data["version"], 1.03)
        self.assertNotIn("error", data)
        read.assert_called_once()

    def test_firmware_endpoint_reports_the_ring_level(self):
        built = recipes.build(STOCK_A, ["ring_5"], **K)
        flash = bytearray(controller_flash(built))
        flash[image.RING_SAVED] = 0x21  # 25 %, saved while 5 % was installed
        info = transport.DeviceInfo(0x045E, 0x028E, 0xFF7A, "USB", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable_360")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(bytes(flash))), \
                mock.patch.object(recipes, "_constants", lambda a=None, b=None, c=None: (K["stock_sha256"], K["region_sha256"], K["remainder_sha256"])):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data["ring_level"], 1)
        self.assertEqual(data["tweaks"][-1]["title"], recipes.RECIPES["ring_25"].title)

    def test_token_required(self):
        self.assertEqual(self.call("GET", "/api/state", token=None)[0], 403)
        self.assertEqual(self.call("GET", "/api/state", token="wrong")[0], 403)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)

    def test_patch_refused_before_accepting_the_disclaimer(self):
        status, text = self.call("POST", "/api/patch", {"tweaks": ["cable_360"], "base": "backup", "write": False})
        self.assertEqual(status, 403)
        self.assertIn("Accept the disclaimer first.", text)

    def test_accept_is_remembered(self):
        self.assertFalse(json.loads(self.call("GET", "/api/state")[1])["accepted"])
        self.assertEqual(self.call("POST", "/api/accept")[0], 200)
        self.assertTrue(state.accepted(self.store))
        self.assertTrue(json.loads(self.call("GET", "/api/state")[1])["accepted"])

    def test_progress_arrives_in_order_and_ends_with_done(self):
        def fake(args, store):
            args.say("starting")
            args.progress("write", 50, 100)
            args.progress("write", 100, 100)
        jobs.ACTIONS["backup"] = fake
        self.assertEqual(self.call("POST", "/api/backup")[0], 202)
        ev = self.events()
        self.assertEqual([e["type"] for e in ev], ["log", "progress", "progress", "done"])
        self.assertEqual(ev[-1], {"type": "done", "message": "starting", "unchanged": False})
        self.assertEqual(ev[2], {"type": "progress", "label": "write", "done": 100, "total": 100})

    def test_job_status_requires_auth_and_matches_the_requested_job(self):
        jobs.ACTIONS["backup"] = lambda args, store: args.say("Backup saved")
        status, response = self.call("POST", "/api/backup")
        self.assertEqual(status, 202)
        job_id = json.loads(response)["job"]
        with self.httpd.runner.cond:
            self.assertTrue(self.httpd.runner.cond.wait_for(lambda: not self.httpd.runner.running, timeout=5))
        self.assertEqual(self.call("GET", "/api/job?job=" + job_id, token=None)[0], 403)
        self.assertEqual(self.call("GET", "/api/job?job=other-job")[0], 404)
        status, response = self.call("GET", "/api/job?job=" + job_id)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"running": False, "result": {"type": "done", "message": "Backup saved", "unchanged": False}})

    def test_native_windows_rumble_is_authenticated_validated_and_exclusive(self):
        with mock.patch.object(server.sys, "platform", "win32"), \
                mock.patch.object(server, "WINDOWS_RUMBLE") as rumble, mock.patch.object(server.HID_RUMBLE,"status",return_value={"available":False}):
            rumble.status.return_value = {"available": True, "index": 2}
            rumble.rumble.return_value = {"ok": True}
            self.assertEqual(self.call("GET", "/api/windows-rumble", token=None)[0], 403)
            self.assertEqual(json.loads(self.call("GET", "/api/windows-rumble")[1]), {"available": True, "index": 2})
            self.assertEqual(self.call("POST", "/api/windows-rumble", {"index": True, "locality": "left"})[0], 400)
            self.assertEqual(self.call("POST", "/api/windows-rumble", {"index": 2, "locality": "left", "expires": 1})[0], 408)
            self.assertTrue(self.httpd.runner.claim())
            try:
                self.assertEqual(self.call("POST", "/api/windows-rumble", {"index": 2, "locality": "left"})[0], 409)
                rumble.rumble.assert_not_called()
            finally:
                self.httpd.runner.release()
            self.assertEqual(self.call("POST", "/api/windows-rumble", {"index": 2, "locality": "left"})[0], 200)
            rumble.rumble.assert_called_once_with(2, "left")
            self.assertFalse(self.httpd.runner.running)

    def test_vendor_rumble_route_is_authenticated_and_exclusive(self):
        with mock.patch.object(server.sys,'platform','win32'),mock.patch.object(server,'HID_RUMBLE') as rumble:
            rumble.status.return_value={'available':True,'kind':'vendor','product':0x301d}
            rumble.rumble.return_value={'ok':True};rumble.stop.return_value={'ok':True}
            self.assertEqual(json.loads(self.call('GET','/api/windows-rumble')[1])['kind'],'vendor')
            req={'kind':'vendor','locality':'left'}
            self.assertEqual(self.call('POST','/api/windows-rumble',req,token=None)[0],403)
            self.assertEqual(self.call('POST','/api/windows-rumble',{**req,'expires':1})[0],408)
            self.assertTrue(self.httpd.runner.claim())
            try:
                self.assertEqual(self.call('POST','/api/windows-rumble',req)[0],409)
                self.assertEqual(self.call('POST','/api/windows-rumble/stop',{'kind':'vendor'})[0],200)
                rumble.rumble.assert_not_called()
            finally:self.httpd.runner.release()
            self.assertEqual(self.call('POST','/api/windows-rumble',req)[0],200)
            rumble.rumble.assert_called_once_with('left')

    def test_native_stop_is_available_during_an_active_test(self):
        with mock.patch.object(server.sys, "platform", "win32"), mock.patch.object(server, "WINDOWS_RUMBLE") as rumble:
            rumble.stop.return_value = {"ok": True}
            self.assertTrue(self.httpd.runner.claim())
            try:
                self.assertEqual(self.call("POST", "/api/windows-rumble/stop", {"index": 2}, token=None)[0], 403)
                self.assertEqual(self.call("POST", "/api/windows-rumble/stop", {"index": 4})[0], 400)
                self.assertEqual(self.call("POST", "/api/windows-rumble/stop", {"index": 2})[0], 200)
                rumble.stop.assert_called_once_with(2)
            finally:
                self.httpd.runner.release()

    def test_windows_battery_is_authenticated_and_read_only(self):
        with mock.patch.object(server.sys, "platform", "win32"), mock.patch.object(server, "windows_battery", return_value={"level": .73, "state": "reported", "source": "Windows Bluetooth"}) as battery:
            self.assertEqual(self.call("GET", "/api/windows-battery", token=None)[0], 403)
            battery.assert_not_called()
            status, body = self.call("GET", "/api/windows-battery")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["level"], .73)
            self.assertFalse(self.httpd.runner.running)

    def test_stock_bluetooth_input_is_authenticated_and_read_only(self):
        with mock.patch.object(server.sys, "platform", "win32"), mock.patch.object(server, "WINDOWS_STOCK_GAMEPAD") as gamepad:
            gamepad.snapshot.return_value = {"available": True, "lt": 0, "rt": .5}
            self.assertEqual(self.call("GET", "/api/windows-stock-gamepad", token=None)[0], 403)
            gamepad.snapshot.assert_not_called()
            status, body = self.call("GET", "/api/windows-stock-gamepad")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["rt"], .5)
            gamepad.close.assert_called_once()
            self.assertFalse(self.httpd.runner.running)

    def test_stock_bluetooth_stream_delivers_each_state_and_ends_on_disconnect(self):
        with mock.patch.object(server.sys, "platform", "win32"), mock.patch.object(server, "WINDOWS_STOCK_GAMEPAD") as gamepad:
            self.assertEqual(self.call("GET", "/api/windows-stock-stream", token=None)[0], 403)
            gamepad.snapshot.assert_not_called()
            states = [{"available": True, "lt": value, "rt": value} for value in (0, .25, .75)] + [{"available": False, "connected": False}]
            gamepad.snapshot.side_effect = states
            status, body = self.call("GET", "/api/windows-stock-stream")
            self.assertEqual(status, 200)
            received = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
            self.assertEqual(received, states)
            gamepad.close.assert_called_once()
            self.assertTrue(self.httpd.RequestHandlerClass.disable_nagle_algorithm)

    def test_bluetooth_ring_validates_auth_and_releases_device_after_failure(self):
        with mock.patch.object(server.sys, "platform", "win32"), \
                mock.patch("u2c_customfw.transport.windows.set_bluetooth_ring") as ring:
            self.assertEqual(self.call("POST", "/api/bluetooth-ring", {"level": 2, "save": False}, token=None)[0], 403)
            self.assertEqual(self.call("POST", "/api/bluetooth-ring", {"level": True})[0], 400)
            self.assertTrue(self.httpd.runner.claim())
            try:
                self.assertEqual(self.call("POST", "/api/bluetooth-ring", {"level": 2})[0], 409)
                ring.assert_not_called()
            finally:
                self.httpd.runner.release()
            self.assertEqual(self.call("POST", "/api/bluetooth-ring", {"level": 2, "save": False})[0], 200)
            ring.assert_called_once_with(2, False)
            ring.side_effect = OSError("disconnected")
            status, body = self.call("POST", "/api/bluetooth-ring", {"level": 255, "save": True})
            self.assertEqual(status, 422)
            self.assertIn("disconnected", body)
            self.assertFalse(self.httpd.runner.running)

    def test_the_end_event_carries_the_last_sentence_and_whether_anything_changed(self):
        def fake(args, store):
            args.say("Installing changes 3 bytes of the firmware.")
            args.say("The controller already has this firmware, so nothing was written.")
            args.unchanged = True
        jobs.ACTIONS["backup"] = fake
        self.call("POST", "/api/backup")
        self.assertEqual(self.events()[-1], {"type": "done", "message": "The controller already has this firmware, so nothing was written.", "unchanged": True})

    def test_command_line_hints_stay_out_of_the_app(self):
        def fake(args, store):
            args.say("The check passed.")
            args.hint("Add --write to install.")
        jobs.ACTIONS["backup"] = fake
        self.assertEqual(self.call("POST", "/api/backup", {})[0], 202)
        ev = self.events()
        self.assertEqual(ev[-1]["type"], "done")
        self.assertEqual([e["text"] for e in ev if e["type"] == "log"], ["The check passed."])

    def test_patch_and_restore_pass_their_parameters_to_the_job(self):
        seen = {}

        def fake(args, store):
            seen.update(tweaks=args.tweaks, write=args.write, download=args.download, source=args.source,
                        force_unknown=args.force_unknown, expect=args.expect, file=getattr(args, "file", None), names=(args.bt_name, args.cable_name, args.xinput_name, args.dinput_name), device=args.device)
        state.accept(self.store)
        jobs.ACTIONS["patch"] = fake
        jobs.ACTIONS["restore"] = fake
        self.assertEqual(self.call("POST", "/api/patch", {"tweaks": ["bt_xbox", "cable_360"], "base": "download", "write": True})[0], 202)
        self.events()
        self.assertEqual(seen, dict(tweaks="bt_xbox,cable_360", write=True, download=True, source="backup", force_unknown=False, expect=None, file=None, names=(None, None, None, None), device=None))
        for sent, got in (("dongle", "dongle"), ("controller", "controller"), ("printer", None)):
            self.call("POST", "/api/patch", {"tweaks": [], "device": sent})
            self.events()
            self.assertEqual(seen["device"], got)
        self.call("POST", "/api/patch", {"tweaks": ["bt_xbox", "cable_360"], "names": {"bt_xbox": "My Pad", "cable_360": "Wired", "dim_leds": "x"}})
        self.events()
        self.assertEqual(seen["names"], ("My Pad", "Wired", None, None))   # only the two names there are
        self.call("POST", "/api/patch", {"tweaks": ["bt_xbox"], "names": {"bt_xbox": ["not", "text"]}})
        self.events()
        self.assertEqual(seen["names"], (None, None, None, None))
        self.call("POST", "/api/patch", {"tweaks": ["dongle_360", "dongle_dinput"], "names": {"dongle_360": "X", "dongle_dinput": "D", "bt_xbox": "B"}})
        self.events()
        self.assertEqual(seen["names"], ("B", None, "X", "D"))   # passed on; the dongle's install uses only its own
        self.assertEqual(self.call("POST", "/api/restore", {"source": "stock", "write": False})[0], 202)
        self.events()
        self.assertEqual((seen["source"], seen["write"], seen["force_unknown"]), ("stock", False, False))
        self.call("POST", "/api/restore", {"source": "backup", "backup": "controller-20260101-000000.bin", "file": "/etc/passwd"})
        self.events()
        self.assertEqual((seen["expect"], seen["file"]), ("controller-20260101-000000.bin", None))
        self.call("POST", "/api/restore", {"source": "stock", "write": True, "force_unknown": True})
        self.events()
        self.assertTrue(seen["force_unknown"])

    def test_jobs_are_logged_for_the_problem_report(self):
        def fake(args, store):
            args.say("The check passed and nothing was written.")
            if args.write:
                raise cli.Exit(2, "Nothing was written.")
        state.accept(self.store)
        jobs.ACTIONS["patch"] = fake
        self.call("POST", "/api/patch", {"tweaks": ["dim_leds"], "write": False})
        self.events()
        self.call("POST", "/api/patch", {"tweaks": ["dim_leds"], "write": True})
        self.events()
        log = report.log_tail(self.store)
        for line in ("job patch", "'tweaks': 'dim_leds'", "The check passed and nothing was written.", "done", "error: Nothing was written."):
            self.assertIn(line, log)
        self.assertNotIn(str(self.store), log)

    def test_a_quiet_job_gets_keepalives_and_then_its_end(self):
        import http.client
        release = threading.Event()
        jobs.ACTIONS["backup"] = lambda args, store: release.wait(5)
        with mock.patch.object(server, "KEEPALIVE_S", 0.05):
            self.call("POST", "/api/backup")
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
            conn.request("GET", "/api/events", headers={"X-Token": self.token})
            r = conn.getresponse()
            self.assertEqual(r.read(13), b": keepalive\n\n")
            release.set()
            self.assertIn(b'"type": "done"', r.read())
            conn.close()

    def test_errors_become_error_events(self):
        def fake(args, store):
            raise RuntimeError("boom")
        jobs.ACTIONS["backup"] = fake
        self.call("POST", "/api/backup")
        ev = self.events()
        self.assertEqual(ev[-1]["type"], "error")
        self.assertIn("boom", ev[-1]["message"])

    def test_nothing_uses_the_controller_while_another_process_does(self):
        held = state.lock_device(self.store)   # as the command line, or another copy of the app, holds it
        self.addCleanup(state.unlock_device, held)
        jobs.ACTIONS["backup"] = lambda args, store: None
        self.assertEqual(self.call("POST", "/api/backup")[0], 409)
        self.assertEqual(self.call("GET", "/api/firmware")[0], 409)
        self.assertIsNone(state.lock_device(self.store))
        state.unlock_device(held)
        self.assertEqual(self.call("POST", "/api/backup")[0], 202)
        self.events()

    def test_a_newer_version_is_asked_once_per_launch(self):
        from u2c_customfw import update
        found = {"version": "9.9.9", "url": "https://github.com/x"}
        with mock.patch.dict("os.environ", {"U2C_SIM": ""}), mock.patch.object(update, "newer", return_value=found) as newer:
            self.assertEqual(json.loads(self.call("GET", "/api/update")[1]), found)
            self.assertEqual(json.loads(self.call("GET", "/api/update")[1]), found)
            self.assertEqual(newer.call_count, 1)
            self.assertEqual(self.call("GET", "/api/update", token=None)[0], 403)

    def test_busy_says_whether_a_write_is_running(self):
        release = threading.Event()
        state.accept(self.store)
        jobs.ACTIONS["patch"] = lambda args, store: release.wait(5)
        busy = lambda: json.loads(self.call("GET", "/api/busy")[1])["writing"]
        self.assertFalse(busy())
        self.call("POST", "/api/patch", {"tweaks": ["dim_leds"], "write": False})
        self.assertFalse(busy())   # a check writes nothing
        release.set(); self.events(); release.clear()
        self.call("POST", "/api/patch", {"tweaks": ["dim_leds"], "write": True})
        self.assertTrue(busy())
        release.set(); self.events()
        self.assertFalse(busy())
        self.assertEqual(self.call("GET", "/api/busy", token=None)[0], 403)

    def test_second_job_while_busy_is_409(self):
        release = threading.Event()
        jobs.ACTIONS["backup"] = lambda args, store: release.wait(5)
        self.assertEqual(self.call("POST", "/api/backup")[0], 202)
        self.assertEqual(self.call("POST", "/api/backup")[0], 409)
        release.set()
        self.events()

    def settings_device(self, record=None, installed=True, mode="cable"):
        from tests.test_settings import flash_with
        dev = SimApp(flash_with(installed, record))
        info = transport.DeviceInfo(0x2DC8, 0x310A, 0xFF7A, "USB", "x", None)
        finder = mock.patch.object(cli, "find", lambda: [devices.Found(info, mode)])
        opener = mock.patch.object(cli, "open_channel", lambda i, rid: dev)
        finder.start(); opener.start()
        self.addCleanup(finder.stop); self.addCleanup(opener.stop)
        return dev

    def test_settings_lists_fields_and_values(self):
        self.settings_device()
        code, body = self.call("GET", "/api/settings")
        got = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(got["available"])
        self.assertEqual(got["values"]["dz_l"], 14)
        self.assertIn({"name": "vibration", "low": 0, "high": 100, "default": 100, "choices": [0, 25, 50, 75, 100]}, got["fields"])

    def test_settings_change_and_save(self):
        dev = self.settings_device()
        self.assertEqual(self.call("POST", "/api/settings", {"set": {"dz_l": 7}})[0], 200)
        self.assertEqual(dev.fields[0], 7)
        from u2c_customfw import settings
        values = dict({f.name: f.default for f in settings.FIELDS}, dz_l=7)
        self.assertEqual(self.call("POST", "/api/settings", {"save": True, "values": values})[0], 200)
        self.assertEqual(settings.parse(dev.flash[settings.SECTOR:settings.SECTOR + settings.SIZE]), values)

    def test_opening_the_settings_drops_what_was_not_saved(self):
        dev = self.settings_device()
        dev.fields[0] = 20  # a change from before, in the controller's RAM
        got = json.loads(self.call("GET", "/api/settings")[1])
        self.assertEqual((got["values"]["dz_l"], dev.fields[0]), (14, 14))

    def test_save_checks_the_mapping_and_the_switch_too(self):
        from u2c_customfw import settings
        from u2c_customfw.ops import settings as ops
        dev = self.settings_device()
        values = {f.name: f.default for f in settings.FIELDS}
        self.call("POST", "/api/settings", {"mapping": {"slot": "r4", "mask": 1}})
        self.assertEqual(self.call("POST", "/api/settings", {"save": True, "values": values, "kept_mapping": {"r4": 1, "l4": 0}, "kept_dead_zone_off": False})[0], 200)
        with mock.patch.object(ops, "CONFIRM_SECONDS", 0.05):
            code, _ = self.call("POST", "/api/settings", {"save": True, "values": values, "kept_mapping": {"r4": 2, "l4": 0}})
        self.assertEqual(code, 409)
        self.assertEqual(self.call("POST", "/api/settings", {"save": True, "values": values, "kept_mapping": {"r4": 1}})[0], 400)

    def test_a_change_notices_a_controller_that_has_gone(self):
        dev = self.settings_device()
        real = dev.xfer
        calls = []
        def fading(req, timeout=0.5, accept=None):
            if req[2] == 5 and int.from_bytes(req[14:18], "little") == 0xD1C8:
                calls.append(1)
                if len(calls) > 1:
                    return None
            return real(req, timeout)
        dev.xfer = fading
        self.assertEqual(self.call("POST", "/api/settings", {"set": {"dz_l": 7}})[0], 409)

    def test_save_reports_failure_when_read_back_differs(self):
        from u2c_customfw import settings
        from u2c_customfw.ops import settings as ops
        dev = self.settings_device()
        dev.drop_saves = True
        with mock.patch.object(ops, "CONFIRM_SECONDS", 0.05):
            code, body = self.call("POST", "/api/settings", {"save": True, "values": {f.name: f.default for f in settings.FIELDS}})
        self.assertEqual(code, 409)
        self.assertIn("confirm", json.loads(body)["error"])

    def test_cable_lost_during_save_is_an_error(self):
        from u2c_customfw import settings
        dev = self.settings_device()
        self.assertEqual(self.call("GET", "/api/settings")[0], 200)
        real = dev.xfer
        dev.xfer = lambda req, timeout=0.5, accept=None: real(req, timeout) if req[3] == 0x55 else None  # the save goes out, then nothing answers
        code, body = self.call("POST", "/api/settings", {"save": True, "values": {f.name: f.default for f in settings.FIELDS}})
        self.assertEqual(code, 409)
        self.assertNotIn("Saved", body)

    def test_settings_refused_during_a_job(self):
        self.settings_device()
        release = threading.Event()
        jobs.ACTIONS["backup"] = lambda args, store: release.wait(5)
        self.call("POST", "/api/backup")
        code, _ = self.call("POST", "/api/settings", {"set": {"dz_l": 7}})
        release.set()
        self.assertEqual(code, 409)

    def test_out_of_range_refused_before_the_device(self):
        dev = self.settings_device()
        sent = len(dev.sent)
        for body in ({"set": {"dz_l": 31}}, {"set": {"nothing": 1}}, {"set": {}}, {"set": 5}, {"values": {}}, {}, {"save": True}, {"save": True, "values": {"dz_l": 7}}):
            self.assertEqual(self.call("POST", "/api/settings", body)[0], 400, body)
        self.assertEqual(len(dev.sent), sent)

    def test_wrong_type_refused(self):
        dev = self.settings_device()
        for v in (True, "7", 7.0, None, [7]):
            self.assertEqual(self.call("POST", "/api/settings", {"set": {"dz_l": v}})[0], 400, v)
        self.assertEqual(dev.fields[0], 14)

    def test_mask_outside_allowed_bits_refused(self):
        self.settings_device()
        for m in ({"slot": "r4", "mask": 1 << 16}, {"slot": "x", "mask": 1}, {"slot": "l4", "mask": -1}, {"slot": "l4", "mask": True}, {"mask": 1}):
            self.assertEqual(self.call("POST", "/api/settings", {"mapping": m})[0], 400, m)
        self.assertEqual(self.call("POST", "/api/settings", {"dead_zone_off": 1})[0], 400)

    def test_not_available_without_the_tweak(self):
        self.settings_device(installed=False)
        got = json.loads(self.call("GET", "/api/settings")[1])
        self.assertFalse(got["available"])
        self.assertIn("Controller settings", got["note"])
        self.assertEqual(self.call("POST", "/api/settings", {"set": {"dz_l": 7}})[0], 409)

    def test_not_available_in_update_mode(self):
        self.settings_device(mode="boot")
        self.assertFalse(json.loads(self.call("GET", "/api/settings")[1])["available"])
        self.assertEqual(self.call("POST", "/api/settings", {"set": {"dz_l": 7}})[0], 409)

    def test_never_sends_dangerous_commands(self):
        dev = self.settings_device()
        self.call("GET", "/api/settings")
        self.call("POST", "/api/settings", {"set": {"dz_l": 7}})
        self.call("POST", "/api/settings", {"reset": True})
        self.call("POST", "/api/settings", {"mapping": {"slot": "r4", "mask": 1}})
        self.call("POST", "/api/settings", {"dead_zone_off": True})
        allowed = ("81050021", "810505", "8105c1", "81050052", "81050053", "81050054", "81050055", "81050056", "81050057", "81050058", "8105005a")
        for s in dev.sent:
            self.assertTrue(s.startswith(allowed), s)

    def test_firmware_endpoint_names_the_tweaks(self):
        built = recipes.build(STOCK_A, ["bt_xbox", "cable_360"], **K)
        info = transport.DeviceInfo(0x2DC8, 0x310A, 0xFF7A, "USB", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(controller_flash(built))), \
                mock.patch.object(recipes, "_constants", lambda a=None, b=None, c=None: (K["stock_sha256"], K["region_sha256"], K["remainder_sha256"])):
            status, text = self.call("GET", "/api/firmware")
        data = json.loads(text)
        self.assertEqual(status, 200, text)
        self.assertTrue(data["known"])
        self.assertEqual(data["recipes"], ["bt_xbox", "cable_360"])
        self.assertIn("XInput mode over Bluetooth", " ".join(data["titles"]))
        self.assertEqual(data["names"], {"bt_xbox": {"label": "Bluetooth name", "default": "8BitDo Ultimate 2C (X)", "max": 27, "held": "8BitDo Ultimate 2C (X)"},
                                         "cable_360": {"label": "Name over cable", "default": "8BitDo Ultimate 2C Wireless Controller", "max": 38, "held": "8BitDo Ultimate 2C Wireless Controller"}})
        self.assertEqual((data["version"], data["version_from"]), (1.09, "update record"))
        self.assertEqual((data["pending"], data["boot_identity"]), (False, None))
        self.assertEqual([(t["key"], t["on"]) for t in data["tweaks"]],
                         [("bt_xbox", True), ("bt_rumble", False), ("bt_rumble_long", False), ("bt_modes", False), ("cable_360", True), ("dim_leds", False), ("battery_alerts", False), ("settings", False), ("fixes", False), ("bt_ring_read", False), ("stick_precision", False), ("cpu_rest", False), ("usb_dinput", False), ("fast_scan", False), ("ble_fast", False), ("bt_switch2", False), ("ring", False)])

    def test_firmware_endpoint_in_update_mode_names_the_backup_restore_uses(self):
        flash = controller_flash()
        (self.store / "controller-20260101-000000.bin").write_bytes(flash)
        damaged = bytearray(flash); damaged[image.BASE:image.BASE + 0x1000] = b"\xff" * 0x1000
        (self.store / "controller-20260102-000000.bin").write_bytes(bytes(damaged))
        info = transport.DeviceInfo(0x2DC8, 0x3208, 0x8C, "USB", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "boot")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimBoot(bytes(damaged))), \
                mock.patch.object(recipes, "_constants", lambda a=None, b=None, c=None: (K["stock_sha256"], K["region_sha256"], K["remainder_sha256"])):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data["restore_backup"], {"name": "controller-20260101-000000.bin", "taken": "1 Jan 2026 at 00:00",
                                                  "firmware": "the original firmware 1.09"})

    def test_firmware_endpoint_by_cable_names_no_backup(self):
        info = transport.DeviceInfo(0x2DC8, 0x310A, 0xFF7A, "USB", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(controller_flash())), \
                mock.patch.object(recipes, "_constants", lambda a=None, b=None, c=None: (K["stock_sha256"], K["region_sha256"], K["remainder_sha256"])):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertIsNone(data["restore_backup"])
        self.assertFalse(data["update"])   # the original firmware: nothing to update

    def test_firmware_endpoint_by_cable_names_the_backup_restore_uses(self):
        flash = controller_flash()
        (self.store / "controller-20260101-000000.bin").write_bytes(flash)
        info = transport.DeviceInfo(0x045E, 0x028E, 0x01, "USB", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable_360")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(flash)), \
                mock.patch.object(recipes, "_constants", lambda a=None, b=None, c=None: (K["stock_sha256"], K["region_sha256"], K["remainder_sha256"])):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data["restore_backup"]["name"], "controller-20260101-000000.bin")

    def test_firmware_endpoint_reads_the_idle_dongle(self):
        from tests.test_dongle import XBOX, K as DK, flash
        from u2c_customfw.ops import dongle
        info = transport.DeviceInfo(0x2DC8, 0x301C, 0xFFA0, "USB", "IDLE", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "dongle")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(flash(XBOX))), \
                mock.patch.object(dongle, "STOCK_SHA256", DK["stock_sha256"]):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data, {"device": "dongle", "paired": False, "boot": False, "backed": False, "restore_backup": None, "stock_ok": True,
                                "known": True, "version": 1.03,
                                "tweaks": [{"key": "dongle_360", "title": "Xbox 360 controller over 2.4 GHz", "on": True},
                                           {"key": "dongle_dinput", "title": "D-input mode with B + Home", "on": False}],
                                "names": {"dongle_360": {"label": "X-input name", "default": "8BitDo Ultimate 2C Wireless Controller", "max": 38,
                                                         "held": "8BitDo Ultimate 2C Wireless Controller"},   # installed before names
                                          "dongle_dinput": {"label": "D-input name", "default": "8BitDo Ultimate 2C Wireless Controller", "max": 36, "held": None}}})

    def test_a_dongle_left_in_update_mode_is_named_and_its_backup_offered(self):
        from tests.test_dongle import STOCK, K as DK, flash, World
        from u2c_customfw.ops import dongle
        full = flash(STOCK)
        (Path(self.store) / "dongle-20261108-120000.bin").write_bytes(full)
        broken = bytearray(full); broken[dongle.BASE:dongle.END] = b"\xff" * dongle.SIZE
        w = World(bytes(broken), start="boot")
        with mock.patch.object(cli, "find", w.find), mock.patch.object(cli, "open_channel", w.open), \
                mock.patch.object(dongle, "STOCK_SHA256", DK["stock_sha256"]):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual((data["device"], data["boot"], data["known"], data["backed"], data["stock_ok"]), ("dongle", True, False, True, True))
        self.assertEqual(data["restore_backup"]["name"], "dongle-20261108-120000.bin")

    def test_the_hidden_tweak_is_never_shown(self):
        built = recipes.build(STOCK_A, ["bt_xbox", "ble_interval"], **K)
        info = transport.DeviceInfo(0x2DC8, 0x310A, 0xFF7A, "USB", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(controller_flash(built))), \
                mock.patch.object(recipes, "_constants", lambda a=None, b=None, c=None: (K["stock_sha256"], K["region_sha256"], K["remainder_sha256"])):
            data = json.loads(self.call("GET", "/api/firmware")[1])
            st = recipes.identify(built)
        self.assertEqual(st.recipes, ("ble_interval", "bt_xbox"))   # installed, and still recognised
        self.assertEqual(data["recipes"], ["bt_xbox"])
        self.assertNotIn("ble_interval", [t["key"] for t in data["tweaks"]])
        title = recipes.RECIPES["ble_interval"].title
        for text in (json.dumps(data), report.report([], st, ""), bkp.describe(st)):
            self.assertNotIn(title, text)
            self.assertNotIn("ble_interval", text)
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["patch", "--help"])
        self.assertNotIn("ble_interval", out.getvalue())

    def test_firmware_endpoint_with_only_bluetooth_explains(self):
        info = transport.DeviceInfo(0x045E, 0x0B13, 1, "Bluetooth Low Energy", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "bluetooth_xbox")]):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data["note"], "Connect the controller by cable to see or change its firmware. Over Bluetooth, you can test it in Diagnostics and change the Home ring in Settings.")
        info = transport.DeviceInfo(0x2DC8, 0x301B, 1, "Bluetooth Low Energy", "x", None)
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "bluetooth")]):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data["note"], "Connect the controller by cable to see or change its firmware.")   # the ring needs the Xbox tweak

    def test_firmware_endpoint_with_xinput_only_explains_bootloader(self):
        info = transport.DeviceInfo(0x045e, 0x028e, 1, "USB", "Controller", None, 0x114)
        with mock.patch.object(cli, "find", return_value=[devices.Found(info, "xinput_only")]), \
                mock.patch.object(cli, "open_channel") as opened:
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data["note"], devices.XINPUT_NOTE)
        opened.assert_not_called()

    def test_firmware_endpoint_marks_a_permission_error(self):
        info = transport.DeviceInfo(0x2DC8, 0x310A, 0xFF7A, "USB", "x", None)

        def refused(i, rid):
            raise transport.PermissionDenied("macOS refused access to the controller.")
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable")]), mock.patch.object(cli, "open_channel", refused):
            data = json.loads(self.call("GET", "/api/firmware")[1])
        self.assertEqual(data, {"error": "macOS refused access to the controller.", "permission": True})

    def test_firmware_endpoint_is_refused_while_a_job_runs(self):
        release = threading.Event()
        jobs.ACTIONS["backup"] = lambda args, store: release.wait(5)
        self.call("POST", "/api/backup")
        self.assertEqual(self.call("GET", "/api/firmware")[0], 409)
        release.set()
        self.events()

    def test_no_job_starts_while_the_firmware_is_being_read(self):
        reading, release = threading.Event(), threading.Event()

        def slow_read(ch, mode):
            reading.set()
            release.wait(5)
            return probe.Firmware(recipes.State(True, ()), 1.09, "update record")
        info = transport.DeviceInfo(0x2DC8, 0x310A, 0xFF7A, "USB", "x", None)
        jobs.ACTIONS["backup"] = lambda args, store: None
        with mock.patch.object(cli, "find", lambda: [devices.Found(info, "cable")]), \
                mock.patch.object(cli, "open_channel", lambda i, rid: SimApp(controller_flash())), \
                mock.patch.object(probe, "read_firmware", slow_read):
            t = threading.Thread(target=self.call, args=("GET", "/api/firmware"), daemon=True)
            t.start()
            self.assertTrue(reading.wait(5))
            self.assertEqual(self.call("POST", "/api/backup")[0], 409)   # the device is in use
            release.set()
            t.join(5)
            self.assertEqual(self.call("POST", "/api/backup")[0], 202)   # free again
            self.events()

    def test_malformed_json_is_a_400_not_a_dropped_connection(self):
        for raw in (b"{not json", b"[1, 2]"):
            req = urllib.request.Request(self.base + "/api/accept", method="POST", data=raw, headers={"X-Token": self.token})
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(e.exception.code, 400)
            e.exception.close()

    def test_a_write_keeps_the_mac_awake_until_it_ends(self):
        runner, gate, awake = jobs.Runner(self.store), threading.Event(), mock.Mock()
        with mock.patch.dict(jobs.ACTIONS, patch=lambda args, store: gate.wait(5)), \
                mock.patch.object(jobs, "keep_awake", return_value=awake) as keep:
            self.assertTrue(runner.start("patch", write=True))
            awake.terminate.assert_not_called()
            gate.set()
            with runner.cond:
                self.assertTrue(runner.cond.wait_for(lambda: not runner.running, timeout=5))
            awake.terminate.assert_called_once()
            self.assertTrue(runner.start("patch", write=False))   # checking first writes nothing
            self.assertEqual(keep.call_count, 1)

    def test_a_job_that_fails_to_start_frees_the_device(self):
        runner = jobs.Runner(self.store)
        with mock.patch.object(jobs, "get_logger", side_effect=OSError("no log")):
            with self.assertRaises(OSError):
                runner.start("backup")
        self.assertFalse(runner.running)
        with mock.patch.dict(jobs.ACTIONS, backup=lambda args, store: None):
            self.assertTrue(runner.start("backup"))

    def test_running_and_the_end_event_change_together(self):
        runner = jobs.Runner(self.store)
        jobs.ACTIONS["backup"] = lambda args, store: None
        bad = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                with runner.cond:
                    last = runner.events[-1]["type"] if runner.events else None
                    terminal = last in ("done", "error")
                    if (terminal and runner.running) or (runner.events and not terminal and not runner.running):
                        bad.append((last, runner.running))   # finished without its end event, or an end event while running
        t = threading.Thread(target=sample, daemon=True)
        t.start()
        for _ in range(200):
            while not runner.start("backup"):
                pass
            with runner.cond:
                runner.cond.wait_for(lambda: runner.events and runner.events[-1]["type"] in ("done", "error"), timeout=5)
        stop.set(); t.join()
        self.assertEqual(bad, [])

    def test_wrong_host_header_is_refused(self):
        req = urllib.request.Request(self.base + "/api/state", headers={"X-Token": self.token, "Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 403)


class BrowserTest(unittest.TestCase):
    def test_started_by_hand_the_page_opens_in_the_browser_but_not_under_the_mac_app_or_the_simulation(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(server.should_open_browser())
        for env in ({"U2C_EXIT_ON_EOF": "1"}, {"U2C_SIM": "1"}, {"U2C_NO_BROWSER": "1"}):
            with mock.patch.dict("os.environ", env, clear=True):
                self.assertFalse(server.should_open_browser(), env)


class SimulationStoreTest(unittest.TestCase):
    def test_the_simulation_leaves_the_real_store_alone(self):
        import os
        import subprocess
        import sys
        home = Path(tempfile.mkdtemp())
        env = dict(os.environ, U2C_SIM="1", HOME=str(home), PYTHONPATH=str(Path(__file__).parent.parent))
        proc = subprocess.Popen([sys.executable, "-m", "u2c_customfw.web"], stdout=subprocess.PIPE, text=True, env=env)
        try:
            self.assertTrue(proc.stdout.readline().startswith("U2C_URL "))
        finally:
            proc.terminate(); proc.wait(); proc.stdout.close()
        self.assertEqual(list(home.rglob("controller-*.bin")), [])


if __name__ == "__main__":
    unittest.main()


class OrphanTest(unittest.TestCase):
    def test_the_core_exits_once_the_app_is_gone_but_never_during_a_task(self):
        checks = []

        class Busy:
            @property
            def running(self):
                checks.append(1)
                return len(checks) < 3   # a task that ends after two more checks
        exited = []
        server.exit_when_orphaned(Busy(), stdin=io.StringIO("anything\n"), wait=0, exit=exited.append)
        self.assertEqual((len(checks), exited), (3, [0]))


class ShutdownTest(unittest.TestCase):
    def setUp(self):   # serve() opens the browser when started by hand: not from the tests
        p = mock.patch.dict("os.environ", {"U2C_NO_BROWSER": "1"}); p.start(); self.addCleanup(p.stop)

    def test_ctrl_c_closes_server_without_traceback(self):
        httpd = mock.MagicMock()
        httpd.server_address = ("127.0.0.1", 41941)
        httpd.serve_forever.side_effect = KeyboardInterrupt
        httpd.runner.running = False
        with mock.patch.object(server, "make_server", return_value=(httpd, "token")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                server.serve(Path("/unused"))
        self.assertIn("Stopping WebUI...", output.getvalue())
        httpd.server_close.assert_called_once_with()

    def test_ctrl_c_waits_for_active_firmware_job(self):
        httpd = mock.MagicMock()
        httpd.server_address = ("127.0.0.1", 41941)
        httpd.serve_forever.side_effect = KeyboardInterrupt
        httpd.runner.running = True
        with mock.patch.object(server, "make_server", return_value=(httpd, "token")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                server.serve(Path("/unused"))
        self.assertIn("Waiting for the current operation to finish...", output.getvalue())
        httpd.runner.cond.wait_for.assert_called_once()
        httpd.server_close.assert_called_once_with()
