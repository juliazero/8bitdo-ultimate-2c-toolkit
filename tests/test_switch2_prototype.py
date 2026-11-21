# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from tools.switch2_prototype import advertisement,input_report,MIN_ATT_MTU

class Switch2WireTest(unittest.TestCase):
    def test_published_discovery_vector_fits_legacy_ble_advertising(self):
        expected=bytes.fromhex("02 01 06 1b ff 53 05 01 00 03 7e 05 69 20 00 01 00 00 00 00 00 00 00 0f 00 00 00 00 00 00 00")
        self.assertEqual(advertisement(),expected)
        self.assertEqual(len(expected),31)
        host=bytes.fromhex("12 34 56 78 9a bc")
        reconnect=advertisement("reconnect",host)
        self.assertEqual(reconnect[17:23],host[::-1])
        wake=advertisement("wake",host)
        self.assertEqual(wake[16],0x81)
        self.assertEqual(wake[:16]+wake[17:],reconnect[:16]+reconnect[17:])
    def test_neutral_and_independent_controls_without_invented_sensor_data(self):
        neutral=input_report()
        self.assertEqual(len(neutral),63)
        self.assertEqual(neutral[:15],bytes.fromhex("00 24 00 00 00 00 08 80 00 08 80 30 00 00 00"))
        self.assertEqual(neutral[15:],bytes(48))
        sample=input_report(buttons=("A","ZL","GL","Home"),sticks=(0,4095,4095,0),counter=255,battery_level=0,external_power=True,charging=True)
        self.assertEqual(sample[:11],bytes.fromhex("ff 03 02 20 09 00 f0 ff ff 0f 00"))
        self.assertEqual(MIN_ATT_MTU,66)
    def test_bad_input_fails_before_producing_packets(self):
        for kwargs in ({"sticks":(0,0,0,4096)},{"buttons":["Xbox"]},{"counter":256},{"battery_level":100}):
            with self.assertRaises(ValueError):input_report(**kwargs)
        for args in (("wake",None),("reconnect",b"short"),("discovery",bytes(6)),("other",None)):
            with self.assertRaises(ValueError):advertisement(*args)

    def test_notification_interpreter_address_calculation_preserves_flags(self):
        from tools.check_bt_modes import CPU,mod
        from tools.build_usb_mode_prototype import Code
        from u2c_customfw.firmware import recipes,image
        entry=0x18000
        for words,expected in (((0xa802,0x4770),mod.SP0+8),((0xa001,0x4770),entry+8),((0x2000,0x2800,0xa902,0xd101,0x2001,0x4770,0x2002,0x4770),1)):
            code=Code(entry)
            for word in words:code.op(word)
            frame=bytearray([255])*image.APP_SIZE
            frame[entry-image.BASE:entry-image.BASE+len(code.data)]=code.data
            self.assertEqual(CPU(frame).call(entry),expected)

    def test_interpreter_arithmetic_shift_immediate(self):
        from tools.check_bt_modes import CPU
        from tools.build_usb_mode_prototype import Code
        from u2c_customfw.firmware import image
        for shift,value,expected in ((8,29,0),(8,0x80000000,0xff800000),(32,0x80000000,0xffffffff),(32,0x7fffffff,0)):
            code=Code(0x18000)
            code.op(0x1000|((shift&31)<<6));code.op(0x4770)
            frame=bytearray([255])*image.APP_SIZE
            frame[0x18000-image.BASE:0x18000-image.BASE+len(code.data)]=code.data
            self.assertEqual(CPU(frame).call(0x18000,value),expected)

    def test_interpreter_multiple_load_store_and_base_in_list(self):
        from tools.check_bt_modes import CPU,mod
        from tools.build_usb_mode_prototype import Code
        from u2c_customfw.firmware import image
        for words,expected in (((0xc006,0x3808,0xc806,0x4610,0x4770),0xaabbccdd),
                               ((0xc006,0x3808,0xc803,0x4770),0x12345678)):
            code=Code(0x18000)
            for word in words:code.op(word)
            frame=bytearray([255])*image.APP_SIZE
            frame[0x18000-image.BASE:0x18000-image.BASE+len(code.data)]=code.data
            cpu=CPU(frame)
            self.assertEqual(cpu.call(0x18000,mod.PACKET,0x12345678,0xaabbccdd),expected)
            self.assertEqual(cpu.rd(mod.PACKET,4),0x12345678)
            self.assertEqual(cpu.rd(mod.PACKET+4,4),0xaabbccdd)
