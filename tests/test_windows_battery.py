import ctypes as C
import sys
import unittest
from unittest.mock import Mock, patch

from u2c_customfw.transport import DeviceInfo
from u2c_customfw.web import windows_battery as battery


class WindowsBatteryTest(unittest.TestCase):
    def test_percentage_follows_only_the_selected_hid_parent_chain(self):
        api = Mock()
        def locate(pointer, identifier, flags):
            pointer._obj.value = 1
            return 0
        def parent(pointer, node, flags):
            pointer._obj.value = node + 1
            return 0
        def identifier(node, buffer, length, flags):
            buffer.value = "BTHLE\\DEV_TEST" if node == 3 else "HID\\TEST"
            return 0
        api.CM_Locate_DevNodeW.side_effect = locate
        api.CM_Get_Parent.side_effect = parent
        api.CM_Get_Device_IDW.side_effect = identifier
        def prop(function, node, key):
            if key is battery.INSTANCE:
                return 0x12, "HID\\TEST\0".encode("utf-16-le")
            return (3, bytes([73])) if node == 3 else None
        with patch.object(battery, "_api", return_value=api), patch.object(battery, "_property", side_effect=prop):
            self.assertEqual(battery._percentage(b"hid-interface"), 73)
        self.assertEqual(api.CM_Get_Parent.call_count, 2)

    def test_battery_does_not_follow_bluetooth_adapter_parents(self):
        api = Mock()
        api.CM_Locate_DevNodeW.side_effect = lambda pointer, *args: setattr(pointer._obj, "value", 1) or 0
        api.CM_Get_Device_IDW.side_effect = lambda node, buffer, *args: setattr(buffer, "value", "BTHLE\\DEV_TEST") or 0
        with patch.object(battery, "_api", return_value=api), patch.object(battery, "_property", side_effect=[(0x12, "id\0".encode("utf-16-le")), (3, bytes([255]))]):
            self.assertIsNone(battery._percentage("hid-interface"))
        api.CM_Get_Parent.assert_not_called()

    def test_snapshot_matches_controller_and_preserves_zero_percent(self):
        controller = DeviceInfo(0x45e, 0xb13, 1, "Bluetooth", "pad", b"controller")
        mouse = DeviceInfo(0x45e, 0x999, 1, "Bluetooth", "mouse", b"mouse")
        with patch.object(sys, "platform", "win32"), patch.object(battery.windows, "list_devices", return_value=[mouse, controller]), patch.object(battery, "_percentage", return_value=0) as read:
            self.assertEqual(battery.snapshot(), {"level": 0, "state": "reported", "source": "Windows Bluetooth"})
            read.assert_called_once_with(b"controller")

    def test_multiple_controllers_or_missing_data_remain_unknown(self):
        controller = DeviceInfo(0x45e, 0xb13, 1, "Bluetooth", "pad", b"controller")
        with patch.object(sys, "platform", "win32"), patch.object(battery.windows, "list_devices", return_value=[controller, controller]), patch.object(battery, "_percentage") as read:
            self.assertEqual(battery.snapshot()["level"], -1)
            read.assert_not_called()
        with patch.object(sys, "platform", "win32"), patch.object(battery.windows, "list_devices", return_value=[controller]), patch.object(battery, "_percentage", return_value=None):
            self.assertEqual(battery.snapshot()["level"], -1)

    def test_property_key_has_native_windows_layout(self):
        self.assertEqual(C.sizeof(battery.PropertyKey), 20)
