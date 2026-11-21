# SPDX-License-Identifier: GPL-3.0-or-later
import re,unittest
from pathlib import Path
from tools import switch2_layout as layout
from tools.check_switch2_transport import FragmentCPU
from u2c_customfw.firmware import image

class HousekeepingCPU(FragmentCPU):
    def __init__(self,frame,base):
        self.io_base=base;self.calls=[]
        super().__init__(frame)
    def wr8(self,a,value):
        if a in (self.io_base,self.io_base+16,self.io_base+32):
            self.ram[a]=value&255;return
        super().wr8(a,value)
    def trap(self,pc,r):
        if pc==0xfaa0:
            self.calls.append((r[13],tuple(self.rd8(self.io_base+i) for i in (0,16,32))))
            return True
        return super().trap(pc,r)

class Pro2RamLayoutTest(unittest.TestCase):
    def test_regions_fit_reserved_tails_without_bss_cache_or_stack_overlap(self):
        result=layout.validate()
        self.assertEqual(result["allocated_bytes"],239)
        self.assertEqual(result["tail_capacity"],240)
        for address,size in layout.REGIONS.values():
            self.assertGreaterEqual(address,0x840000)
            self.assertLessEqual(address+size,0x842500)
        self.assertEqual(layout.STACK_TOP-layout.STACK_FLOOR,784)
    def test_c_address_definitions_match_test_layout(self):
        root=Path(__file__).resolve().parents[1]/"firmware-src"
        definitions={"switch2-pairing.c":{"Y_CTX":layout.CTX,"Y_REPLY":layout.Y_REPLY,"Y_PENDING":layout.Y_PENDING,"Y_REPLY_CCC":layout.Y_REPLY_CCC},
                     "switch2-input.c":{"MASK":layout.Y_MASK,"ENABLED":layout.Y_ENABLED,"CONFIGURED":layout.Y_CONFIGURED,"COUNTER":layout.Y_COUNTER,"PLAYER":layout.Y_PLAYER,"INPUT_CCC":layout.Y_INPUT_CCC,"PENDING":layout.Y_PENDING,"SHADOW":layout.Y_SHADOW},
                     "switch2-security.c":{"CTX":layout.CTX,"PEER":layout.Y_PEER,"PEER_VALID":layout.Y_PEER_VALID}}
        for file,entries in definitions.items():
            source=(root/file).read_text(encoding="utf-8")
            for name,address in entries.items():
                match=re.search(r"^#define "+name+r" .*?(0x[0-9a-f]+)",source,re.M)
                self.assertIsNotNone(match,(file,name))
                self.assertEqual(int(match.group(1),16),address,(file,name))
    def test_relocated_housekeeping_preserves_register_writes_and_call_stack(self):
        original,relocated,shim=layout.led_code()
        plain=bytearray([255])*image.APP_SIZE
        plain[0x1738:0x1738+len(original)]=original
        moved=bytearray(plain)
        moved[0x1738:0x1740]=shim
        offset=layout.LED_ENTRY-image.BASE
        moved[offset:offset+len(relocated)]=relocated
        base=int.from_bytes(original[-4:],"little")
        for values in ((255,255,255),(0,0,0),(0xa5,0x3c,0x96)):
            cpus=[HousekeepingCPU(frame,base) for frame in (plain,moved)]
            for cpu in cpus:
                for delta,value in zip((0,16,32),values):cpu.seed(base+delta,value,1)
                cpu.call(0x1738)
            self.assertEqual(cpus[0].calls,cpus[1].calls)
            self.assertEqual(len(cpus[0].calls),1)
            expected=(values[0]&0x7f,values[1]&~0x20,values[2]&~2)
            self.assertEqual(cpus[1].calls[0][1],expected)

