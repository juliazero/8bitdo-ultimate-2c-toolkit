# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from tools.check_switch2_bonds import FlashCPU,PowerCut
from tools.check_switch2_transport import mod
from u2c_customfw.firmware import image

class JournalFlashModelTest(unittest.TestCase):
    def test_operations_are_confined_to_the_candidate_journal(self):
        c=FlashCPU(bytes(image.APP_SIZE))
        c.trap(0xbd00,[0]*16)
        for address,length in ((0x3b000,64),(0x3cfc0,64),(0x3f000,1),(0x3efff,2)):
            with self.assertRaises(AssertionError):c.trap(0x20000,[address,length,mod.PACKET]+[0]*13)
        for address in (0x3b000,0x3f000,0x3d040):
            with self.assertRaises(AssertionError):c.trap(0xfe98,[address]+[0]*15)
    def test_write_protection_nor_bits_and_partial_programming(self):
        c=FlashCPU(bytes(image.APP_SIZE));c.seed(mod.PACKET,0x5a,1)
        args=[0x3d000,1,mod.PACKET]+[0]*13
        with self.assertRaises(AssertionError):c.trap(0x20000,args)
        c.trap(0xbd00,[0]*16);c.trap(0x20000,args)
        c.seed(mod.PACKET,0xff,1);c.trap(0x20000,args)
        self.assertEqual(c.flash[0x3d000],0x5a)
        c.program_limit=1
        c.seed(mod.PACKET,0x11,1);c.seed(mod.PACKET+1,0x22,1)
        with self.assertRaises(PowerCut):c.trap(0x20000,[0x3d040,2,mod.PACKET]+[0]*13)
        self.assertEqual(c.flash[0x3d040],0x11)
        self.assertEqual(c.flash.get(0x3d041,255),255)
