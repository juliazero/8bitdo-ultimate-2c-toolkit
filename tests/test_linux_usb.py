"""Linux USB transport and device routing regression tests."""
import unittest
import sys
from unittest.mock import patch

from u2c_customfw.ops import devices
from u2c_customfw.transport import DeviceInfo
from u2c_customfw.transport import libusb
if sys.platform.startswith("linux"):
    from u2c_customfw.transport import linux


def info(vid, pid, page, handle, version=0):
    return DeviceInfo(vid, pid, page, "USB", "test", handle, version)


class LinuxUsbTest(unittest.TestCase):
    def test_stock_and_patched_controller_modes(self):
        items = [info(0x2DC8, 0x310A, 0xFF7A, "/dev/hidraw0"),
                 info(0x045E, 0x028E, 0xFF7A, (1, 2), 0x0114),
                 info(0x2DC8, 0x301C, 0xFFA0, "/dev/hidraw1")]
        with patch.object(devices.transport, "list_devices", return_value=items):
            self.assertEqual([d.mode for d in devices.find()], ["cable", "cable_360", "dongle"])

    def test_paired_receiver_mode(self):
        with patch.object(devices.transport, "list_devices", return_value=[
                info(0x045E, 0x028E, 0xFF7A, (1, 3), devices.DONGLE_BCD)]):
            self.assertEqual([d.mode for d in devices.find()], ["dongle_paired"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux hidraw only")
    def test_linux_routes_stock_to_hidraw_and_xbox_to_libusb(self):
        with patch.object(linux.hidraw, "open_channel", return_value="hidraw") as h, \
                patch.object(linux.libusb, "open_channel", return_value="libusb") as u:
            self.assertEqual(linux.open_channel(info(0x2DC8, 0x310A, 0xFF7A, "/dev/hidraw0"), 0x81), "hidraw")
            self.assertEqual(linux.open_channel(info(0x045E, 0x028E, 0xFF7A, (1, 2)), 0x81), "libusb")
            h.assert_called_once()
            u.assert_called_once()

    def test_libusb_rejects_oversized_or_wrong_report_before_usb_write(self):
        ch = object.__new__(libusb.LibusbChannel)
        ch.rid = 0x81
        with self.assertRaisesRegex(ValueError, "longer than 64"):
            ch.xfer(b"\x81" * 65)
        with self.assertRaisesRegex(ValueError, "does not match"):
            ch.xfer(b"\x80\x05")


if __name__ == "__main__":
    unittest.main()
