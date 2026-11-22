import unittest
from unittest.mock import MagicMock, patch

from tools import dongle_xinput_battery as diag
from u2c_customfw import transport
from u2c_customfw.ops import probe


class XInputBatteryTest(unittest.TestCase):
    def info(self, path, page=0xFF7A, pid=0x310A):
        return transport.DeviceInfo(0x2DC8, pid, page, 'USB', '8BitDo', path, 0x114)

    def test_selects_only_mi_02_vendor_hid(self):
        dev = self.info(b'\\\\?\\HID#VID_2DC8&PID_310A&MI_02#ABC')
        listing = [self.info(b'MI_00', 1), self.info(b'MI_01', 1), dev,
                   self.info(b'MI_02', 0xff7a, 0x301c)]
        self.assertIs(diag.identify(listing), dev)

    def test_missing_or_ambiguous_vendor_is_rejected(self):
        a = self.info(b'&MI_02')
        for listing in ([], [self.info(b'MI_01')], [a, a]):
            with self.subTest(listing=listing), self.assertRaises(RuntimeError):
                diag.identify(listing)

    def test_reads_only_after_dongle_identity_check(self):
        dev = self.info(b'&MI_02')
        ch = MagicMock()
        open_channel = MagicMock(return_value=ch)
        result = {'percent': 78, 'mv': 3961}
        with patch.object(probe, 'check_not_dongle', side_effect=probe.PairedDongle('dongle')) as identity, \
             patch.object(diag, 'read_rf_battery', return_value=result) as rf:
            self.assertEqual(diag.read_one([dev], open_channel), result)
        identity.assert_called_once_with(ch)
        rf.assert_called_once_with(ch)
        open_channel.assert_called_once_with(dev, 0x81)
        ch.close.assert_called_once()

    def test_wired_controller_is_not_read_as_dongle(self):
        ch = MagicMock()
        with patch.object(probe, 'check_not_dongle', return_value=None), \
             patch.object(diag, 'read_rf_battery') as rf:
            with self.assertRaisesRegex(RuntimeError, 'wired controller'):
                diag.read_one([self.info(b'&MI_02')], lambda d, rid: ch)
        rf.assert_not_called()
        ch.close.assert_called_once()
