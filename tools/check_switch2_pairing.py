# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute compiled TC32 Pro 2 pairing, including stock AES. No device access."""
import argparse,hashlib,json,random
from pathlib import Path
from tools import switch2_layout as layout
from tools.check_switch2_crypto import CryptoCPU
from tools.switch2_pairing import PairingSession
from u2c_customfw.firmware import image,recipes
CTX=0x840070;REQUEST=0x843d00;RESPONSE=0x843d40
ADDRESS=bytes.fromhex("98e255c21688")
TRANSCRIPT=tuple(bytes.fromhex(x) for x in (
    "15910101000e0000000281eb3aebf14880eb3aebf148",
    "1591010400110000003503e92982877124bea80c664615834b",
    "1591010200110000006fc6df8ad8fedf15bb8c15e91f320544",
    "159101030001000000"))

class PairCPU(CryptoCPU):
    stack_top=0x844000
    stack_floor=0x843cf0
    def wr8(self,a,v):
        if CTX<=a<CTX+65 or RESPONSE<=a<RESPONSE+25 or self.stack_floor<=a<self.stack_top:
            self.ram[a]=v&255;return
        super().wr8(a,v)

def check(frame,build):
    report=json.loads((build/"pairing-prototype.json").read_text(encoding="utf-8"))
    payload=(build/"pairing-prototype.bin").read_bytes()
    assert report["offline_only"] and not report["installable"] and report["entry"]==0x1a000
    assert len(payload)==report["bytes"]<=8192
    # Expanded image exists only in interpreter memory, never passed to flash ops.
    code=bytearray(frame)
    offset=report["entry"]-image.BASE
    if len(code)<offset+len(payload):code.extend(bytes([255])*(offset+len(payload)-len(code)))
    code[offset:offset+len(payload)]=payload
    code=layout.prepare_frame(code)
    c=PairCPU(code);model=PairingSession(ADDRESS)
    for i,v in enumerate(ADDRESS):c.seed(CTX+59+i,v,1)
    symbols=report["symbols"];accepted=rejected=0
    def state():
        stages={"idle":0,"addresses":1,"keys":2,"response":3,"committed":4}
        assert c.rd8(CTX)==stages[model.stage]
        assert c.rd8(CTX+1)==len(model._hosts)
        assert c.rd8(CTX+30)==len(model.bonds)
        for i,bond in enumerate(model.bonds):
            assert bytes(c.rd8(CTX+31+6*i+j) for j in range(6))==bond.host[::-1]
            assert bytes(c.rd8(CTX+43+j) for j in range(16))==bond.ltk_wire
        if model._ltk is not None:
            assert bytes(c.rd8(CTX+14+j) for j in range(16))==model._ltk
    def request(data):
        nonlocal accepted,rejected
        assert len(data)<=64
        before=bytes(c.rd8(CTX+i) for i in range(65))
        for i,v in enumerate(data):c.seed(REQUEST+i,v,1)
        for i in range(25):c.seed(RESPONSE+i,0xa5,1)
        try:expected=model.handle(data)
        except ValueError:expected=None
        length=c.call(symbols["y_pair_request"],CTX,REQUEST,len(data),RESPONSE)
        if expected is None:
            assert length==0,(data.hex(),length)
            assert bytes(c.rd8(CTX+i) for i in range(65))==before
            assert all(c.rd8(RESPONSE+i)==0xa5 for i in range(25))
            rejected+=1
        else:
            assert length==len(expected),(data.hex(),length,len(expected))
            assert bytes(c.rd8(RESPONSE+i) for i in range(length))==expected
            assert all(c.rd8(RESPONSE+i)==0xa5 for i in range(length,25))
            accepted+=1
        state()
    for packet in TRANSCRIPT[:1]+TRANSCRIPT[1:3]+TRANSCRIPT[2:]:request(packet)
    # Committed key survives a disconnect; a partial new exchange cannot replace it.
    c.call(symbols["y_pair_disconnect"],CTX);model.disconnect();state()
    for packet in TRANSCRIPT[:2]:request(packet)
    c.call(symbols["y_pair_disconnect"],CTX);model.disconnect();state()
    request(TRANSCRIPT[3])
    rng=random.Random(0x2c02)
    for _ in range(300):
        packet=bytearray(rng.choice(TRANSCRIPT))
        if rng.randrange(4)==0:packet=packet[:rng.randrange(len(packet)+1)]
        else:
            index=rng.randrange(len(packet));packet[index]^=1<<rng.randrange(8)
        request(bytes(packet))
    # Finish with a complete fresh exchange after arbitrary malformed traffic.
    c.call(symbols["y_pair_disconnect"],CTX);model.disconnect()
    for packet in TRANSCRIPT:request(packet)
    return {"ok":True,"compiled_bytes":len(payload),"accepted_requests":accepted,
            "rejected_requests":rejected,"stock_aes_executed":True,"aes_peripheral_emulated":True,
            "persistent_storage_exercised":False,"gatt_callback_exercised":False,"radio_tested":False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backup",type=Path,required=True)
    p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"))
    a=p.parse_args();raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=="__main__":main()
