# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the stock ATT server with the compiled Pro 2 callback. Offline only."""
import argparse,hashlib,json
from pathlib import Path
from tools import switch2_layout as layout
from tools.build_switch2_gatt import BASE as GATT_BASE
from tools.check_switch2_bonds import FlashCPU
from tools.check_switch2_pairing import TRANSCRIPT,ADDRESS,CTX,REQUEST
from tools.switch2_pairing import PairingSession
from u2c_customfw.firmware import image,recipes

class GattCPU(FlashCPU):
    def __init__(self,frame,flash=None):
        self.notifications=[];self.suppress_programming=False
        super().__init__(frame,flash)
        # Boot normally copies this verified stock .data MTU-response template.
        template=bytes(frame[0x18198-image.BASE:0x181a1-image.BASE])
        assert template==bytes.fromhex("02 07 03 00 04 00 03 17 00")
        for i,value in enumerate(template):self.seed(0x842f14+i,value,1)
    def wr8(self,a,v):
        if 0x840000<=a<0x844000:
            self.ram[a]=v&255;return
        super().wr8(a,v)
    def trap(self,pc,r):
        if pc==0x20000 and self.suppress_programming:return True
        if pc==0x1458c:
            self.notifications.append((r[1],bytes(self.rd8(r[2]+i) for i in range(r[3]))))
        return super().trap(pc,r)

def check(frame,build):
    symbols=json.loads((build/"pairing-prototype.json").read_text(encoding="utf-8"))["symbols"]
    code=bytearray(frame)
    for address,name in ((0x1a000,"pairing-prototype.bin"),(GATT_BASE,"gatt-prototype.bin")):
        data=(build/name).read_bytes();position=address-image.BASE
        if len(code)<position:code.extend(bytes([255])*(position-len(code)))
        code[position:position+len(data)]=data
    code=layout.prepare_frame(code)
    c=GattCPU(code)
    for i,v in enumerate(ADDRESS):c.seed(CTX+59+i,v,1)
    c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
    c.call(0x13628,GATT_BASE);c.seed(0x842f20,66,2);c.seed(0x842f22,23,2)
    assert c.rd(0x843648,4)==GATT_BASE
    model=PairingSession(ADDRESS)
    def request(att):
        rf=bytes((2,len(att)+4))+len(att).to_bytes(2,"little")+b"\x04\x00"+att
        for i,v in enumerate(rf):c.seed(REQUEST+i,v,1)
        return c.call(0x138c0,0x40,REQUEST)
    result=request(bytes.fromhex("024200"))
    assert c.rd8(result+6)==3 and c.rd(result+7,2)==66,(hex(result),c.rd8(result+6),c.rd(result+7,2),c.rd(0x842f22,2))
    assert c.rd(0x842f22,2)==66
    # Actual stock server must write the reply CCCD backing RAM and return ATT ACK.
    result=request(bytes.fromhex("121f000100"))
    assert c.rd8(result+6)==0x13,(hex(result),c.rd8(result+6))
    assert c.rd(layout.Y_REPLY_CCC,2)==1
    for packet in TRANSCRIPT:
        # Model consumption between exchanges; enqueue/CCM routines remain real.
        c.seed(0x843a24,c.rd8(0x843a23),1)
        before=len(c.notifications)
        assert request(b"\x52\x14\x00"+packet)==0
        expected=model.handle(packet)
        assert c.notifications[before:]==[(0x1e,expected)]
        assert c.rd8(layout.Y_PENDING)==0
    assert c.rd8(CTX+30)==2
    assert bytes(c.rd8(CTX+43+i) for i in range(16))==model.bonds[0].ltk_wire
    # Queue saturation preserves exactly one pending reply for later retry.
    c.seed(0x843a23,10,1);c.seed(0x843a24,4,1)
    query=bytes.fromhex("1591010100000000")
    request(b"\x52\x14\x00"+query)
    assert c.rd8(layout.Y_PENDING)==17
    pending=bytes(c.rd8(layout.Y_REPLY+i) for i in range(25))
    request(b"\x52\x14\x00"+TRANSCRIPT[0])
    assert bytes(c.rd8(layout.Y_REPLY+i) for i in range(25))==pending
    c.seed(0x843a24,10,1)
    c.call(symbols["y_reply_poll"])
    assert c.rd8(layout.Y_PENDING)==0
    committed=bytes(c.rd8(CTX+30+i) for i in range(29))
    c.call(symbols["y_att_disconnect"])
    assert bytes(c.rd8(CTX+30+i) for i in range(29))==committed
    assert c.rd(layout.Y_REPLY_CCC,2)==0 and c.rd8(CTX)==0
    before=len(c.notifications)
    request(b"\x52\x14\x00"+TRANSCRIPT[0])
    assert len(c.notifications)==before and c.rd8(CTX)==0
    # Cold boot: rebuild RAM from the simulated persistent journal.
    flash=c.flash;c=GattCPU(code,flash)
    for i,v in enumerate(ADDRESS):c.seed(CTX+59+i,v,1)
    assert c.call(symbols["y_bond_load"],CTX)==1
    original_key=bytes(c.rd8(CTX+43+i) for i in range(16))
    assert original_key==model.bonds[0].ltk_wire
    c.call(0x11e64,0x843178,40,8);c.call(0x121a0);c.call(0x13628,GATT_BASE)
    c.seed(0x842f20,66,2);c.seed(0x842f22,23,2)
    request(bytes.fromhex("024200"));request(bytes.fromhex("121f000100"))
    changed_key=bytearray(TRANSCRIPT[1]);changed_key[9]^=1
    for packet in (TRANSCRIPT[0],bytes(changed_key),TRANSCRIPT[2]):
        c.seed(0x843a24,c.rd8(0x843a23),1)
        request(b"\x52\x14\x00"+packet)
    c.seed(0x843a24,c.rd8(0x843a23),1)
    c.suppress_programming=True;before=len(c.notifications)
    request(b"\x52\x14\x00"+TRANSCRIPT[3])
    assert len(c.notifications)==before and c.rd8(CTX)==3
    assert c.rd8(layout.Y_PENDING)==0
    assert bytes(c.rd8(CTX+43+i) for i in range(16))==original_key
    c.suppress_programming=False
    request(b"\x52\x14\x00"+TRANSCRIPT[3])
    assert len(c.notifications)==before+1 and c.rd8(CTX)==4
    assert c.notifications[-1][1]==bytes.fromhex("150101031078000001")
    writes=len(c.writes)
    c.seed(0x843a24,c.rd8(0x843a23),1)
    request(b"\x52\x14\x00"+TRANSCRIPT[3])
    assert len(c.writes)==writes and c.notifications[-1][1]==bytes.fromhex("150101031078000001")
    cold=GattCPU(code,c.flash)
    for i,v in enumerate(ADDRESS):cold.seed(CTX+59+i,v,1)
    assert cold.call(symbols["y_bond_load"],CTX)==1
    expected=bytearray(original_key);expected[0]^=1
    assert bytes(cold.rd8(CTX+43+i) for i in range(16))==expected
    return {"ok":True,"stack_peak_bytes":c.stack_top-c.minimum_sp,"stack_margin_bytes":c.minimum_sp-c.stack_floor,"stock_att_server_executed":True,"compiled_callback_executed":True,
            "pairing_exchanges":4,"mtu_exchange_checked":True,"cccd_write_checked":True,"queue_retry_checked":True,
            "disconnect_checked":True,"cold_boot_bond_loaded":True,
            "failed_commit_not_acknowledged":True,"commit_retry_checked":True,"duplicate_commit_does_not_rewrite":True,"radio_tested":False,"installed":False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--backup",type=Path,required=True)
    p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"));a=p.parse_args()
    raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=="__main__":main()
