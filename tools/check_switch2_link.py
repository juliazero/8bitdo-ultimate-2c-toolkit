# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute stock initial BLE link setup with register storage models. No radio I/O."""
from tools.check_switch2_transport import FragmentCPU,mod,image
from tools.build_usb_mode_prototype import Code

class LinkCPU(FragmentCPU):
    def __init__(self,frame):
        self.executed=set()
        super().__init__(frame)
        self.seed(0x843a32,0,1)
        # Pointer recovered from the stock boot data at flash 0x18154.
        self.seed(0x842ed0,0x843ac8)
    def wr8(self,a,v):
        if 0x840000<=a<0x844000 or 0x800000<=a<0x801000:
            self.ram[a]=v&255;return
        super().wr8(a,v)
    def trap(self,pc,r):
        self.executed.add(pc)
        return super().trap(pc,r)

def interval_candidate(frame):
    c=LinkCPU(frame)
    assert c.thumb(0x11938)==0x1f8a and c.rd(0x11a00,4)==3194
    candidate=bytearray(frame)
    code=Code(0x11938);code.op(0x1f0a)
    candidate[0x11938-image.BASE:0x1193a-image.BASE]=code.data
    candidate[0x11a00-image.BASE:0x11a04-image.BASE]=(3196).to_bytes(4,"little")
    return candidate

def check_link_setup(frame):
    cases=[]
    candidate=interval_candidate(frame)
    for patched,data in ((False,frame),(True,candidate)):
        for interval in (4,6,10):
            for clock in (1000000,0xffffff00):
                c=LinkCPU(data)
                packet=bytearray(40)
                packet[0x19]=1
                packet[0x1c:0x1e]=interval.to_bytes(2,"little")
                packet[0x20:0x22]=(200).to_bytes(2,"little")
                packet[0x22:0x27]=bytes.fromhex("ff ff ff ff 1f")
                packet[0x27]=5
                for i,v in enumerate(packet):c.seed(mod.PACKET+i,v,1)
                c.seed(0x843aa4,clock)
                c.call(0x11928,mod.PACKET,0)
                accepted=patched or interval>=6
                assert (0x12cf8 in c.executed)==accepted
                assert c.rd8(0x843a32)==(8 if accepted else 0)
                if accepted:
                    assert {0x12cf8,0x12bbc,0x12c54,0x12c18,0x12028,0x11f74,0x12150}<=c.executed
                    assert c.rd(0x843b50,2)==interval
                    assert c.rd(0x843b44,4)==interval*20000
                    assert c.rd(0x843b34,4)==(clock+20000)&0xffffffff
                    assert c.rd(0x843b3c,4)==32000000 # 200 * 10ms at 16MHz
                    expected=bytes((5*(i+1))%37 for i in range(37))
                    assert bytes(c.rd8(0x843ae4+i) for i in range(37))==expected
                    for _ in range(16):
                        before=c.rd(0x843b34,4)
                        c.call(0x12c18)
                        assert c.rd(0x843b34,4)==(before+interval*20000)&0xffffffff
                cases.append({"candidate":patched,"interval_units":interval,
                              "clock_wrap_case":clock>0xffff0000,"connected_state":accepted})
    return {"cases":cases,"real_setup_executed":True,"channel_hop_table_checked":True,
            "next_anchor_updates_per_link":16,"hardware_registers_emulated":True,
            "context_pointer_from_stock_boot":True,"radio_isr_executed":False,
            "production_changed":False,"candidate_saved":False}
