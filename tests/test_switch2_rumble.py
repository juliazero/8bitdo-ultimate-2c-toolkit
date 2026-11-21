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
unsigned get_value(unsigned i) {return i==0?strong:i==1?weak:i==2?hold:calls;}
void clear(void) {strong=weak=hold=calls=0;}
''')
        shared = Path(cls.tmp.name) / 'test.so'
        subprocess.run(['cc', '-std=c99', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT/'firmware-src'), str(source), '-o', str(shared)], check=True)
        cls.lib = ctypes.CDLL(str(shared))
        cls.lib.invoke.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint]
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
            self.assertEqual(self.lib.get_value(2), 2 if level else 0)

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
