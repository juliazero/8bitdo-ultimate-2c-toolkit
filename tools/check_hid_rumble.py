# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline execution of stock controller/dongle motor report parsers. No HID access."""
import argparse,hashlib,importlib.util,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from u2c_customfw.firmware import image
from u2c_customfw.ops import dongle
from u2c_customfw.web.hid_rumble import packet
spec=importlib.util.spec_from_file_location('tc32',ROOT/'firmware-src/test_longrumble.py');cpu=importlib.util.module_from_spec(spec);spec.loader.exec_module(cpu)
class Parser(cpu.Controller):
    def __init__(self,bank,receiver=False):
        self.receiver=receiver;self.calls=[]
        super().__init__(bank)
        self.wr8(0x842e38,1);self.wr8(0x842d41,1)
    def wr8(self,a,v):
        assert 0x840000<=a<0x848000 or 0x800000<=a<0x810000,hex(a)
        self.ram[a]=v&255
    def trap(self,pc,r):
        if not self.receiver and pc==0xfa68:
            self.calls.append(tuple(r[:3]));return True
        if self.receiver and pc in (0xc50c,0xd0c4,0x1164):
            self.calls.append((pc,*r[:4]));r[0]=0;return True
        return False
    def run_report(self,p):
        for i,v in enumerate(p):self.wr8(cpu.PACKET+i,v)
        self.call(0xe078 if self.receiver else 0xf684,cpu.PACKET)
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--controller',type=Path,required=True);ap.add_argument('--dongle',type=Path,required=True);a=ap.parse_args()
    data=a.controller.read_bytes();bank=next(data[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B) if hashlib.sha256(data[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256)
    receiver=dongle.target(a.dongle.read_bytes()[dongle.BASE:dongle.END],[]);assert hashlib.sha256(receiver).hexdigest()==dongle.STOCK_SHA256
    for values in ((80,80),(80,0),(0,80),(0,0)):
        p=packet(*values);c=Parser(bank);c.run_report(p);assert c.calls==[(*values,0)],c.calls
        d=Parser(receiver,True);d.run_report(p)
        assert d.rd8(0x842f15)==values[0] and d.rd8(0x842f16)==values[1]
        assert d.calls[0][0]==0xc50c and d.calls[0][2:]==(int.from_bytes(p[5:7],'little'),*values),d.calls
        for bad in (bytes(64),bytes([0x81,5,4,8])+p[4:]):
            c=Parser(bank);c.run_report(bad);assert not c.calls
    print('OK: stock USB D-input motor parser, dongle D-input forwarding, left/right/both/stop; offline only, radio and scheduler not emulated')
if __name__=='__main__':main()
