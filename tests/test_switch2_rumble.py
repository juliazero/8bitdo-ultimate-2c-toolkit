# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the actual TC32 rumble conversion as native C and test packet handling."""
import ctypes
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def packed(low, high):
    bits = (333 | (low << 10) | (333 << 20) | (high << 30))
    return bits.to_bytes(5, 'little')


def packet(left, right, count=1):
    def block(samples):
        return bytes([0x40 | (len(samples) << 4)]) + b''.join(packed(*x) for x in samples) + bytes((3-len(samples))*5)
    return bytes([0]) + block(left[:count]) + block(right[:count])


@unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
class Switch2RumbleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        source = Path(cls.tmp.name) / 'driver.c'
        source.write_text('''#include "switch2-rumble.h"
static unsigned strong,weak,hold,calls;
void motor_set(u32 s,u32 w,u32 h) {strong=s;weak=w;hold=h;calls++;}
void invoke(const u8 *p,u32 len) {y_rumble_write(p,len);}
int route(u32 handle,const u8 *p,u32 len) {return y_rumble_att_route(handle,p,len);}
unsigned get_value(unsigned i) {return i==0?strong:i==1?weak:i==2?hold:calls;}
void clear(void) {strong=weak=hold=calls=0;}
''')
        shared = Path(cls.tmp.name) / 'test.so'
        subprocess.run(['cc', '-std=c99', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT/'firmware-src'), str(source), '-o', str(shared)], check=True)
        cls.lib = ctypes.CDLL(str(shared))
        cls.lib.invoke.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint]
        cls.lib.route.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint]
        cls.lib.route.restype = ctypes.c_int
        cls.lib.get_value.argtypes = [ctypes.c_uint]
        cls.lib.get_value.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def feed(self, raw):
        values = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        self.lib.invoke(values, len(raw))

    def test_full_range_and_two_independent_motors(self):
        self.lib.clear()
        for level in (0, 100, 205, 512, 800, 1023):
            self.feed(packet([(level, 0)], [(0, level)]))
            expected = (level * 255 + 511)//1023
            self.assertEqual(self.lib.get_value(0), expected)
            self.assertEqual(self.lib.get_value(1), expected)
            self.assertEqual(self.lib.get_value(2), 2 if level else 1)

    def routed(self, handle, raw):
        values = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        return self.lib.route(handle, values, len(raw))

    def test_gatt_output_length_includes_reserved_bytes(self):
        self.lib.clear()
        payload = packet([(350, 0)], [(0, 500)])
        self.assertEqual(len(payload), 33)
        self.assertEqual(self.routed(0x12, payload), -1)
        self.assertEqual(self.lib.get_value(3), 0)
        self.assertEqual(self.routed(0x12, payload + bytes(9)), -1)
        self.assertEqual(self.lib.get_value(3), 1)
        self.assertEqual(self.lib.get_value(0), (350 * 255 + 511)//1023)
        self.assertEqual(self.lib.get_value(1), (500 * 255 + 511)//1023)

    def test_combined_output_command_offset(self):
        self.lib.clear()
        payload = packet([(800, 0)], [(100, 0)])
        command = bytes.fromhex('0a 91 01 02 00 04 00 00 03 00 00 00')
        self.assertEqual(self.routed(0x16, payload + command), 33)
        self.assertEqual(self.lib.get_value(3), 1)
        self.assertEqual(self.routed(0x16, payload + command[:7]), -1)
        self.assertEqual(self.lib.get_value(3), 1)
        self.assertEqual(self.routed(0x14, command), 0)
        self.assertEqual(self.lib.get_value(3), 1)

    def test_stop_report_overrides_active_hold(self):
        self.lib.clear()
        self.routed(0x12, packet([(800, 0)], [(700, 0)])+bytes(9))
        self.routed(0x12, packet([(0, 0)], [(0, 0)])+bytes(9))
        self.assertEqual(tuple(self.lib.get_value(i) for i in range(4)), (0, 0, 1, 2))

    def test_max_over_three_samples_and_saturation(self):
        self.lib.clear()
        self.feed(packet([(0, 0), (200, 300), (300, 800)], [(0, 0), (30, 0), (10, 0)], 3))
        self.assertEqual(self.lib.get_value(0), 255)
        self.assertEqual(self.lib.get_value(1), (30 * 255 + 511)//1023)

    def test_rejects_bad_size_report_id_and_header(self):
        self.lib.clear()
        valid = packet([(100, 0)], [(200, 0)])
        for corrupt in (valid[:-1], bytes([1])+valid[1:], valid[:1]+bytes([0])+valid[2:]):
            self.feed(corrupt)
        self.assertEqual(self.lib.get_value(3), 0)

if __name__ == '__main__':
    unittest.main()
