# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from tools.switch2_gatt import Pro2Gatt,AttError
from tests.test_switch2_pairing import ADDR,ADDRESS,KEY,CHALLENGE,COMMIT

def write(handle,value,command=False):
    return bytes((0x52 if command else 0x12,))+handle.to_bytes(2,"little")+value

class Pro2GattTest(unittest.TestCase):
    def ready(self):
        s=Pro2Gatt(ADDR)
        self.assertEqual(s.receive(bytes.fromhex("024200")).response,bytes.fromhex("034200"))
        self.assertEqual(s.receive(write(0x1f,b"\x01\x00")).response,b"\x13")
        return s
    def test_captured_pairing_over_att_and_disconnect(self):
        s=self.ready()
        expected=("15010101107800000104018816c255e298",
                  "1501010410780000015cf6ee792cdf05e1ba2b6325c41a5f10",
                  "150101021078000001134c97f511b9b6dd4d86fd40f536e9ed",
                  "150101031078000001")
        for request,reply in zip((ADDRESS,KEY,CHALLENGE,COMMIT),expected):
            exchange=s.receive(write(0x14,request,True))
            self.assertIsNone(exchange.response)
            self.assertIsNone(exchange.rejected)
            self.assertEqual(exchange.notifications,(bytes.fromhex("1b1e00"+reply),))
        self.assertEqual(len(s.pairing.bonds),2)
        s.disconnect()
        self.assertEqual(s.mtu,23)
        self.assertFalse(any(s.subscribed.values()))
        self.assertEqual(len(s.pairing.bonds),2)
        self.assertIsNotNone(s.receive(write(0x14,ADDRESS,True)).rejected)
    def test_notification_requires_mtu_and_subscription(self):
        s=Pro2Gatt(ADDR)
        with self.assertRaises(AttError):s.input_notification()
        s.receive(write(0xf,b"\x01\x00"))
        with self.assertRaises(AttError):s.input_notification()
        s.receive(bytes.fromhex("024200"))
        report=s.input_notification(buttons=("A",))
        self.assertEqual(len(report),66)
        self.assertEqual(report[:3],bytes.fromhex("1b0e00"))
        self.assertEqual(report[5:8],bytes.fromhex("020000"))
        s.receive(write(0xf,b"\x00\x00"))
        with self.assertRaises(AttError):s.input_notification()
    def test_invalid_cccd_and_write_channel_do_not_mutate_pairing(self):
        s=self.ready()
        for packet in (write(0x14,ADDRESS),write(0x16,ADDRESS,True),write(0x18,ADDRESS,True),write(0x14,KEY,True)):
            self.assertIsNotNone(s.receive(packet).rejected)
            self.assertEqual(s.pairing.stage,"idle")
        error=s.receive(write(0xf,b"\x02\x00"))
        self.assertEqual(error.response,bytes.fromhex("01120f0013"))
        self.assertFalse(s.subscribed[0xe])
    def test_mtu_rejection_and_blob_read(self):
        s=Pro2Gatt(ADDR)
        self.assertIsNotNone(s.receive(write(0x14,ADDRESS,True)).rejected)
        self.assertEqual(s.pairing.stage,"idle")
        self.assertEqual(s.receive(bytes.fromhex("021600")).response,bytes.fromhex("0102000004"))
        self.assertEqual(s.mtu,23)
        first=s.receive(bytes.fromhex("0a0e00")).response
        second=s.receive(bytes.fromhex("0c0e001600")).response
        third=s.receive(bytes.fromhex("0c0e002c00")).response
        from tools.switch2_prototype import input_report
        self.assertEqual(first[1:]+second[1:]+third[1:],input_report())
        self.assertEqual(s.receive(bytes.fromhex("0c0e004000")).response,bytes.fromhex("010c0e0007"))
