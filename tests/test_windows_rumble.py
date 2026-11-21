# SPDX-License-Identifier: GPL-3.0-or-later
import ctypes as C
import threading
import unittest
from unittest.mock import Mock, patch

from u2c_customfw.web.windows_rumble import XInputRumble, Vibration


class WindowsRumbleTest(unittest.TestCase):
    def helper(self, connected=(0,)):
        rumble = XInputRumble()
        api = Mock()
        api.XInputGetState.side_effect = lambda index, buf: 0 if index in connected else 1167
        writes = []
        def send(index, pointer):
            value = C.cast(pointer, C.POINTER(Vibration)).contents
            writes.append((index, value.left, value.right))
            return 0
        api.XInputSetState.side_effect = send
        rumble.dll = api
        return rumble, writes

    def test_only_the_unique_connected_slot_is_available(self):
        self.assertEqual(self.helper((2,))[0].status(), {"available": True, "index": 2})
        self.assertFalse(self.helper(())[0].status()["available"])
        self.assertFalse(self.helper((0, 2))[0].status()["available"])

    def test_each_motor_and_stop(self):
        for mode, motors in (("default", (0x5000, 0x5000)), ("left", (0x5000, 0)), ("right", (0, 0x5000))):
            rumble, writes = self.helper((2,))
            with patch.object(rumble.stop_event, "wait") as wait:
                self.assertEqual(rumble.rumble(2, mode), {"ok": True})
            self.assertEqual(writes, [(2, *motors), (2, 0, 0)])
            wait.assert_called_once_with(0.45)

    def test_stop_is_sent_even_if_the_test_is_interrupted(self):
        rumble, writes = self.helper()
        with patch.object(rumble.stop_event, "wait", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                rumble.rumble(0, "default")
        self.assertEqual(writes[-1], (0, 0, 0))

    def test_invalid_or_changed_slot_never_vibrates_another_controller(self):
        rumble, writes = self.helper((1,))
        for index, mode in ((0, "default"), (True, "left"), (4, "left"), (1, "invalid")):
            self.assertFalse(rumble.rumble(index, mode)["ok"])
        self.assertEqual(writes, [])

    def test_driver_failure_is_not_reported_as_success(self):
        rumble, _ = self.helper()
        rumble.dll.XInputSetState.side_effect = None
        rumble.dll.XInputSetState.return_value = 1167
        self.assertFalse(rumble.rumble(0, "default")["ok"])

    def test_stop_interrupts_active_test_and_new_tests_are_not_queued(self):
        rumble, writes = self.helper()
        entered = threading.Event()
        original = rumble.dll.XInputSetState.side_effect
        def send(index, pointer):
            result = original(index, pointer)
            if writes[-1][1:3] != (0, 0):
                entered.set()
            return result
        rumble.dll.XInputSetState.side_effect = send
        results = []
        thread = threading.Thread(target=lambda: results.append(rumble.rumble(0, "default")))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(rumble.rumble(0, "left")["ok"])
            self.assertEqual(rumble.stop(0), {"ok": True})
        finally:
            rumble.stop_event.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [{"ok": True}])
        self.assertEqual(sum(left != 0 or right != 0 for _, left, right in writes), 1)
        self.assertEqual(writes[-1], (0, 0, 0))

    def test_stop_does_not_change_an_unrelated_controller(self):
        rumble, writes = self.helper()
        self.assertEqual(rumble.stop(1), {"ok": True})
        self.assertEqual(writes, [])
