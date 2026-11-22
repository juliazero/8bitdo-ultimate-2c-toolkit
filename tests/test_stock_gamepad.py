import unittest
from unittest.mock import Mock, patch

from u2c_customfw.web import windows_stock_gamepad as stock


class StockGamepadTest(unittest.TestCase):
    def report(self, gas=0, brake=0, buttons=0, hat=15):
        return bytes([1, hat, 127, 127, 127, 127, gas, brake]) + buttons.to_bytes(2, 'little') + bytes([100])

    def test_usb_controller_dinput_layout_and_selection(self):
        packet = bytes([1, 2, 0, 15, 127, 127, 127, 127, 64, 128])
        self.assertEqual(stock.decode(packet, 0x301B, usb=True), stock.decode(packet, 0x301C))
        hid, device = Mock(), Mock()
        hid.enumerate.return_value = [dict(path=b'usb-pad', usage_page=1, usage=5, bus_type=1, product_id=0x301B)]
        hid.device.return_value = device
        device.read.side_effect = [packet, []]
        with patch.object(stock, '_hid', return_value=hid):
            result = stock.StockGamepad().snapshot()
        self.assertEqual((result['lt'], result['rt']), (128 / 255, 64 / 255))
        self.assertIn('B', result['pressed'])
        device.write.assert_not_called()

    def test_bluetooth_style_usb_and_dongle_reports(self):
        from u2c_customfw.ops import dongle
        # The new DInput report ID 1 contains 9 bytes, not BLE's 10th battery byte.
        packet = bytes([1,15,127,127,127,127,64,128, (1<<2)|(1<<5), 0])
        self.assertTrue({'L4','R4'} <= set(stock.decode(packet,0x301B,usb=True,bt_layout=True)['pressed']))
        self.assertEqual(stock.decode(packet,0x301B,usb=True,bt_layout=True)['lt'],128/255)
        self.assertFalse(stock.bluetooth_layout(dongle.D_STOCK_MAP))
        self.assertTrue(stock.bluetooth_layout(dongle.D_STEAM_MAP))
        hid,device=Mock(),Mock()
        hid.enumerate.return_value=[dict(path=b'patched-dongle',usage_page=1,usage=5,bus_type=1,product_id=0x301B)]
        hid.device.return_value=device
        device.get_report_descriptor.return_value=dongle.D_STEAM_MAP
        device.read.side_effect=[packet,[]]
        with patch.object(stock,'_hid',return_value=hid):
            result=stock.StockGamepad().snapshot()
        self.assertIn('L4',result['pressed'])
        self.assertIn('R4',result['pressed'])
        self.assertEqual((result['lt'],result['rt']),(128/255,64/255))

    def test_released_triggers_are_zero(self):
        result = stock.decode(self.report())
        self.assertEqual((result['lt'], result['rt'], result['pressed']), (0, 0, []))

    def test_analog_trigger_range_and_independent_sides(self):
        for gas, brake in ((0, 255), (255, 0), (64, 128), (255, 255)):
            result = stock.decode(self.report(gas, brake))
            self.assertEqual(result['lt'], brake / 255)
            self.assertEqual(result['rt'], gas / 255)

    def test_buttons_hat_and_wrong_reports(self):
        result = stock.decode(self.report(buttons=1 << 3, hat=1))
        self.assertEqual(result['pressed'], ['X', 'Up', 'Right'])
        self.assertIsNone(stock.decode(bytes(11)))
        self.assertIsNone(stock.decode(bytes([1])))

    def test_nonblocking_reads_select_latest_report_and_clear_on_disconnect(self):
        hid, device = Mock(), Mock()
        hid.enumerate.return_value = [dict(path=b'pad', usage_page=1, usage=5, bus_type=2)]
        hid.device.return_value = device
        device.read.side_effect = [self.report(gas=255), self.report(brake=128), []]
        helper = stock.StockGamepad()
        with patch.object(stock, '_hid', return_value=hid):
            result = helper.snapshot()
            self.assertEqual((result['lt'], result['rt']), (128 / 255, 0))
            device.set_nonblocking.assert_called_once_with(1)
            device.write.assert_not_called()
            hid.enumerate.return_value = []
            self.assertEqual(helper.snapshot(), {'available': False, 'connected': False})
            device.close.assert_called_once()

    def test_suspend_closes_handle_and_prevents_reopen_until_resumed(self):
        hid, device = Mock(), Mock()
        hid.enumerate.return_value = [dict(path=b'usb-pad', usage_page=1, usage=5, bus_type=1, product_id=0x301B)]
        hid.device.return_value = device
        device.read.return_value = []
        helper = stock.StockGamepad()
        with patch.object(stock, '_hid', return_value=hid):
            helper.snapshot()
            self.assertIs(helper.device, device)
            helper.suspend(True)
            device.close.assert_called_once()
            self.assertEqual(helper.snapshot(), {"available": False, "connected": False})
            self.assertEqual(hid.device.call_count, 1, "no reopen while Settings is active")
            helper.suspend(False)
            helper.snapshot()
            self.assertEqual(hid.device.call_count, 2, "Diagnostics can reopen after Settings")

    def test_multiple_controllers_do_not_select_arbitrarily(self):
        hid = Mock()
        hid.enumerate.return_value = [dict(path=b'pad', usage_page=1, usage=5, bus_type=2)] * 2
        with patch.object(stock, '_hid', return_value=hid):
            self.assertEqual(stock.StockGamepad().snapshot(), {'available': False, 'connected': False})
        hid.device.assert_not_called()

    def test_connected_input_wait_and_unchanged_reports_do_not_signal_disconnect(self):
        hid, device = Mock(), Mock()
        hid.enumerate.return_value = [dict(path=b'pad', usage_page=1, usage=5, bus_type=2)]
        hid.device.return_value = device
        device.read.side_effect = [[], self.report(brake=128), [], []]
        helper = stock.StockGamepad()
        with patch.object(stock, '_hid', return_value=hid):
            self.assertEqual(helper.snapshot(), {'available': False, 'connected': True})
            sample = helper.snapshot()
            self.assertEqual(helper.snapshot(), sample)
        self.assertEqual(sample['lt'], 128 / 255)


    def test_dongle_report_layout_and_range(self):
        # Descriptor: buttons16, hat8, X/Y/Z/Rz8, Gas/Brake8; report ID 01.
        for gas, brake in ((0, 255), (255, 0), (64, 128), (255, 255)):
            packet = bytes([1, 1, 0, 15, 127, 127, 127, 127, gas, brake])
            result = stock.decode(packet, 0x301C)
            self.assertEqual((result['lt'], result['rt']), (brake / 255, gas / 255))
            self.assertIn('A', result['pressed'])
        self.assertIsNone(stock.decode(bytes([2]) + bytes(63), 0x301C))
        self.assertIsNone(stock.decode(bytes([1, 0]), 0x301C))

    def test_dongle_usb_input_is_selected_without_writing(self):
        hid, device = Mock(), Mock()
        hid.enumerate.return_value = [dict(path=b'dongle', usage_page=1, usage=5, bus_type=1, product_id=0x301C)]
        hid.device.return_value = device
        device.read.side_effect = [bytes([1, 0, 0, 15, 127, 127, 127, 127, 64, 128]), []]
        helper = stock.StockGamepad()
        with patch.object(stock, '_hid', return_value=hid):
            result = helper.snapshot()
        self.assertEqual((result['lt'], result['rt']), (128 / 255, 64 / 255))
        device.write.assert_not_called()
        device.open_path.assert_called_once_with(b'dongle')

    def test_independent_back_buttons_in_bluetooth_usb_and_dongle(self):
        for mask,label in ((1<<2,'L4'),(1<<5,'R4')):
            self.assertEqual(stock.decode(self.report(buttons=mask))['pressed'],[label])
            packet=bytes([1,mask,0,15,127,127,127,127,0,0])
            for pid in (0x301c,0x301d):self.assertEqual(stock.decode(packet,pid)['pressed'],[label])

    def test_bluetooth_path_fallback_and_explicit_close(self):
        hid, device = Mock(), Mock()
        hid.enumerate.return_value = [dict(path=b"BTHLE#pad", usage_page=1, usage=5, product_id=0x301B)]
        hid.device.return_value = device
        device.read.side_effect = [self.report(buttons=1<<4), []]
        helper = stock.StockGamepad()
        with patch.object(stock, '_hid', return_value=hid):
            state = helper.snapshot()
            self.assertEqual((state['pressed'], state['transport'], state['product']), (['Y'], 'Bluetooth', 0x301B))
        helper.close(); helper.close()
        self.assertIsNone(helper.last)
        self.assertIsNone(helper.path)
        device.close.assert_called_once()
        device.write.assert_not_called()
