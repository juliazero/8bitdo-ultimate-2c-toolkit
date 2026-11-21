# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from tools.check_switch2_crypto import CryptoCPU
from u2c_customfw.firmware import image

class AesPeripheralTest(unittest.TestCase):
    def test_register_word_order_and_known_aes_block(self):
        c=CryptoCPU(bytes(image.APP_SIZE))
        key=bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        plain=bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        for i,v in enumerate(key):c.wr8(0x800710+i,v)
        c.wr8(0x800700,0)
        self.assertEqual(c.rd8(0x800700),2)
        for i in range(0,16,4):c.wr(0x800708,int.from_bytes(plain[i:i+4],"little"),4)
        self.assertEqual(c.rd8(0x800700),4)
        result=b"".join(c.rd(0x800708,4).to_bytes(4,"little") for _ in range(4))
        self.assertEqual(result.hex(),"3ad77bb40d7a3660a89ecaf32466ef97")
        self.assertEqual(c.blocks,1)
        with self.assertRaises(AssertionError):c.rd(0x800708,4)
        c.wr8(0x800700,0)
        with self.assertRaises(AssertionError):c.rd(0x800708,4)
    def test_model_rejects_unexpected_memory_writes(self):
        c=CryptoCPU(bytes(image.APP_SIZE))
        for address in (0x10000,0x8432b8,0x844000):
            with self.assertRaises(AssertionError):c.wr8(address,1)
