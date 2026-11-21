# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, call

from u2c_customfw import state, transport
from u2c_customfw.transport import windows
from u2c_customfw.ops import devices
from u2c_customfw import cli


class WindowsTransportTest(unittest.TestCase):
    def test_backend(self):
        with patch.object(sys, "platform", "win32"):
            self.assertIs(transport._backend(), windows)

    def test_enumeration_preserves_usage_version_and_bluetooth_bus(self):
        hid = Mock()
        hid.enumerate.side_effect = [[dict(path=b"usb", vendor_id=0x2dc8, product_id=0x310a,
                                          usage_page=0xff7a, release_number=0x114)],
                                     [dict(path=b"hid#{00001812-0000-1000-8000-00805f9b34fb}",
                                           vendor_id=0x45e, product_id=0xb13)]]
        with patch.object(windows, "_hid", return_value=hid):
            devices = windows.list_devices()
        self.assertEqual((devices[0].usage_page, devices[0].version, devices[0].handle), (0xff7a, 0x114, b"usb"))
        self.assertEqual([d.transport for d in devices], ["USB", "Bluetooth"])

    def channel(self, rid, responses):
        device = Mock()
        device.read.side_effect = [[], *responses]
        device.write.side_effect = len
        with patch.object(windows, "_hid", return_value=Mock(device=lambda: device)):
            channel = windows.open_channel(transport.DeviceInfo(0, 0, 0, "USB", "test", b"path"), rid)
        return channel, device

    def test_bootloader_prepends_zero_but_does_not_strip_response(self):
        response = bytes(range(64))
        ch, dev = self.channel(0, [response])
        self.assertEqual(ch.xfer(response), response)
        dev.write.assert_called_once_with(b"\0" + response)
        ch.close()
        dev.close.assert_called_once()

    def test_numbered_reports_and_rejected_late_response(self):
        ch, dev = self.channel(0x81, [b"late", b"\x02answer"])
        report = b"\x81" + bytes(63)
        self.assertEqual(ch.xfer(report, accept=lambda r: r.startswith(b"\x02")), b"\x02answer")
        dev.write.assert_called_once_with(report)

    def test_timeout_and_short_write(self):
        ch, dev = self.channel(0, [[]])
        self.assertIsNone(ch.xfer(bytes(64)))
        dev.read.side_effect = None
        dev.read.return_value = []
        dev.write.side_effect = None
        dev.write.return_value = 1
        with self.assertRaisesRegex(OSError, "short HID write"):
            ch.xfer(bytes(64))

    def test_invalid_reports_never_write(self):
        ch, dev = self.channel(0x81, [])
        for data in (bytes(63), bytes(65), bytes(64)):
            with self.assertRaises(ValueError):
                ch.xfer(data)
        dev.write.assert_not_called()

    def test_failed_open_closes_device(self):
        device = Mock()
        device.open_path.side_effect = OSError("access denied")
        with patch.object(windows, "_hid", return_value=Mock(device=lambda: device)):
            with self.assertRaises(transport.PermissionDenied):
                windows.open_channel(transport.DeviceInfo(0, 0, 0, "USB", "test", b"path"), 0)
        device.close.assert_called_once()

    def test_queue_drain_never_waits_and_restores_blocking_reads(self):
        ch, dev = self.channel(0, [])
        dev.read.side_effect = [b"stale", b"also stale", []]
        ch._drain()
        self.assertEqual(dev.read.call_args_list, [call(64), call(64), call(64)])
        self.assertEqual(dev.set_nonblocking.call_args_list, [call(1), call(0)])

    def test_queue_drain_restores_blocking_mode_after_disconnect(self):
        ch, dev = self.channel(0, [])
        dev.read.side_effect = OSError("disconnected")
        with self.assertRaises(OSError):
            ch._drain()
        self.assertEqual(dev.set_nonblocking.call_args_list, [call(1), call(0)])

    def test_queue_drain_is_bounded(self):
        ch, dev = self.channel(0, [])
        dev.read.side_effect = None
        dev.read.return_value = b"unsolicited"
        ch._drain()
        self.assertEqual(dev.read.call_count, 32)
        dev.set_nonblocking.assert_called_with(0)


class StateTest(unittest.TestCase):
    def test_windows_store(self):
        with patch.object(sys, "platform", "win32"), patch.dict(os.environ, LOCALAPPDATA="C:/Local"):
            self.assertEqual(state.default_store(), Path("C:/Local/u2c-customfw"))

    def test_lock_excludes_second_client_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = state.lock_device(Path(tmp))
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(state.lock_device(Path(tmp)))
            finally:
                state.unlock_device(first)
            second = state.lock_device(Path(tmp))
            self.assertIsNotNone(second)
            state.unlock_device(second)


class WindowsBluetoothRingTest(unittest.TestCase):
    def api(self):
        device = Mock()
        device.write.return_value = 9
        hid = Mock()
        hid.device.return_value = device
        hid.enumerate.return_value = [dict(path=b"bluetooth", bus_type=2, usage_page=1, usage=5)]
        return hid, device

    def test_ring_uses_nine_byte_marked_bluetooth_report(self):
        hid, device = self.api()
        with patch.object(windows, "_hid", return_value=hid):
            windows.set_bluetooth_ring(2, False)
        hid.enumerate.assert_called_once_with(0x045e, 0x0b13)
        device.open_path.assert_called_once_with(b"bluetooth")
        device.write.assert_called_once_with(bytes([3, 0x80, 2, 0, 0, 0, 0, 0, 0]))
        device.read.assert_not_called()   # firmware sends no acknowledgement for this command
        device.close.assert_called_once()

    def test_save_current_level_and_windows_output_padding(self):
        hid, device = self.api()
        device.write.return_value = 64
        with patch.object(windows, "_hid", return_value=hid):
            windows.set_bluetooth_ring(255, True)
        device.write.assert_called_once_with(bytes([3, 0x80, 255, 1, 0, 0, 0, 0, 0]))

    def test_missing_or_multiple_controllers_are_not_selected_arbitrarily(self):
        hid, device = self.api()
        item = hid.enumerate.return_value[0]
        with patch.object(windows, "_hid", return_value=hid):
            for devices in ([], [item, item], [dict(item, bus_type=1)], [dict(item, usage=2)]):
                hid.enumerate.return_value = devices
                with self.assertRaises(OSError):
                    windows.set_bluetooth_ring(1)
        device.open_path.assert_not_called()

    def test_invalid_levels_do_not_open_a_device(self):
        hid, device = self.api()
        with patch.object(windows, "_hid", return_value=hid):
            for level in (-1, 6, True, "2"):
                with self.assertRaises(ValueError):
                    windows.set_bluetooth_ring(level)
        device.open_path.assert_not_called()

    def test_failed_write_and_short_write_always_close_device(self):
        for fail in (True, False):
            hid, device = self.api()
            if fail:
                device.write.side_effect = OSError("disconnected")
            else:
                device.write.return_value = 3
            with patch.object(windows, "_hid", return_value=hid), self.assertRaises(OSError):
                windows.set_bluetooth_ring(1)
            device.close.assert_called_once()


class WindowsInputOnlyTest(unittest.TestCase):
    def info(self, page=1, version=0x114):
        return transport.DeviceInfo(0x045e, 0x028e, page, "USB", "Controller", b"input", version)

    def test_xusb_input_is_visible_but_cannot_open_as_firmware(self):
        with patch.object(sys, "platform", "win32"), patch.object(transport, "list_devices", return_value=[self.info()]), \
                patch.object(cli, "open_channel") as open_channel:
            self.assertEqual([f.mode for f in devices.find()], ["xinput_only"])
            with self.assertRaises(cli.Exit) as error:
                cli.pick()
            self.assertIn("LB + RB", error.exception.msg)
            open_channel.assert_not_called()

    def test_vendor_channel_takes_precedence_over_synthesized_input(self):
        with patch.object(sys, "platform", "win32"), patch.object(transport, "list_devices", return_value=[self.info(), self.info(0xff7a)]):
            self.assertEqual([f.mode for f in devices.find()], ["cable_360"])

    def test_separate_input_only_device_is_not_hidden_by_dongle(self):
        with patch.object(sys, "platform", "win32"), patch.object(transport, "list_devices", return_value=[self.info(), self.info(0xff7a, 0x103)]):
            self.assertEqual([f.mode for f in devices.find()], ["xinput_only", "dongle_paired"])

    def test_return_from_bootloader_recognizes_xinput(self):
        with patch.object(cli, "find", return_value=[devices.Found(self.info(), "xinput_only")]):
            self.assertTrue(cli._back())




class DongleDinputDetectionTest(unittest.TestCase):
    def test_single_windows_gamepad_collection_carries_update_reports(self):
        info = transport.DeviceInfo(0x2DC8, 0x301C, 1, 'USB', '8BitDo', b'input', 1, 5)
        with patch.object(sys, 'platform', 'win32'), patch.object(transport, 'list_devices', return_value=[info]):
            found = devices.find()
        self.assertEqual([f.mode for f in found], ['dongle_paired'])
        self.assertIs(found[0].info, info)

    def test_separate_vendor_collection_does_not_duplicate_dongle(self):
        gamepad = transport.DeviceInfo(0x2DC8, 0x301C, 1, 'USB', '8BitDo', b'input', 1, 5)
        update = transport.DeviceInfo(0x2DC8, 0x301C, 0xFF7A, 'USB', '8BitDo', b'update')
        with patch.object(sys, 'platform', 'win32'), patch.object(transport, 'list_devices', return_value=[gamepad, update]):
            found = devices.find()
        self.assertEqual([f.mode for f in found], ['dongle_paired'])
        self.assertIs(found[0].info, update)
