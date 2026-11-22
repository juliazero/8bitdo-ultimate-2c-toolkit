import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from tools import dongle_report_probe as probe


class Device:
    def __init__(self, reports):
        self.reports = iter(reports)
        self.closed = False
    def open_path(self, path):
        self.path = path
    def set_nonblocking(self, enabled):
        self.nonblocking = enabled
    def read(self, size):
        return next(self.reports, [])
    def close(self):
        self.closed = True


class HID:
    def __init__(self, reports):
        self.dev = Device(reports)
    def enumerate(self, vid, pid):
        assert (vid, pid) == (0x2DC8, 0x301C)
        return [{'usage_page': 1, 'usage': 5, 'path': b'gamepad'},
                {'usage_page': 0xff00, 'usage': 1, 'path': b'vendor'}]
    def device(self):
        return self.dev


class TestCapture(TestCase):
    def test_changes_and_read_only(self):
        hid = HID([[1, 2, 3], [1, 2, 3], [1, 2, 4]])
        with TemporaryDirectory() as td, mock.patch.object(probe.time, 'monotonic', side_effect=[0, 0, .05, .1, .15, .2, .25, .5]):
            output = Path(td) / 'log.jsonl'
            n = probe.collect(hid, .5, output, diff_only=True)
            records = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(n, 2)
        self.assertEqual([r['hex'] for r in records], ['01 02 03', '01 02 04'])
        self.assertTrue(hid.dev.closed)

    def test_absent_dinput_interface(self):
        h = HID([])
        h.enumerate = lambda *_: []
        with TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, 'XInput'):
                probe.collect(h, 0.01, Path(td) / 'log.jsonl')

    def test_xinput_battery_requires_windows(self):
        with mock.patch.object(probe.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, 'Windows-only'):
                probe.xinput_battery()
