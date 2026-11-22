# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import Mock, patch, AsyncMock
from u2c_customfw.transport import windows, windows_ring
from u2c_customfw.transport.ring_protocol import decode_level
from u2c_customfw.firmware import recipes, image
from tests.test_firmware import synthetic


class BluetoothRingTest(unittest.TestCase):
    def test_decode(self):
        for level in range(6):
            self.assertEqual(decode_level(bytes([1, level])), level)
        for value in (b'', b'\x00\x01', b'\x01\x06', b'\x01\x01\x00'):
            with self.assertRaises(ValueError):
                decode_level(value)

    def test_dinput_write(self):
        hid, device = Mock(), Mock()
        hid.enumerate.return_value = [dict(path=b'pad', bus_type=2, usage_page=1, usage=5)]
        hid.device.return_value = device
        device.write.side_effect = len
        with patch.object(windows, '_hid', return_value=hid):
            windows.set_bluetooth_ring(3, True, profile='dinput')
        hid.enumerate.assert_called_once_with(0x2dc8, 0x301b)
        device.write.assert_called_once_with(bytes([2, 0x80, 3, 1, 0, 0, 0, 0, 0]))
        device.close.assert_called_once()

    def test_read_selects_one_controller(self):
        with patch.object(windows_ring, '_ring_device', return_value=(None, b'pad', 2)) as select, patch.object(windows_ring, 'bluetooth_address', return_value=123) as address, patch.object(windows_ring, '_read', new_callable=AsyncMock, return_value=4) as read:
            self.assertEqual(windows_ring.read_bluetooth_ring('dinput'), 4)
        select.assert_called_once_with('dinput')
        address.assert_called_once_with(b'pad')
        read.assert_awaited_once_with(123)

    def test_recipe_roundtrip_and_profile_copy(self):
        stock, hashes = synthetic()
        base = ['bt_xbox', 'fixes', 'ring_normal', 'bt_modes', 'bt_rumble']
        previous = recipes.build(stock, base, **hashes)
        self.assertEqual(set(recipes.identify(previous, **hashes).recipes), set(base))
        keys = base + ['bt_ring_read']
        frame = recipes.build(stock, keys, **hashes)
        self.assertEqual(set(recipes.identify(frame, **hashes).recipes), set(keys))
        for start in (0x1743c, 0x19000):
            self.assertEqual(int.from_bytes(frame[start-image.BASE:start-image.BASE+4], 'little'), 48)
        self.assertEqual(frame[0x178c8-image.BASE:0x178cc-image.BASE], bytes.fromhex('40168400'))


if __name__ == '__main__':
    unittest.main()
