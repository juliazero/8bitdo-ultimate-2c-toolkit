# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the compiled journal with simulated flash and power cuts. No device I/O."""
import argparse,hashlib,json
from pathlib import Path
from tools import switch2_layout as layout
from tools.check_switch2_pairing import PairCPU,CTX,ADDRESS
from u2c_customfw.firmware import image,recipes

class PowerCut(Exception):pass

class FlashCPU(PairCPU):
    max_steps=1000000
    def __init__(self,frame,flash=None,program_limit=None,erase_limit=None):
        self.flash={} if flash is None else flash
        self.program_limit=program_limit;self.erase_limit=erase_limit
        self.unlocked=False;self.erases=[];self.writes=[]
        super().__init__(frame)
        self.seed(0x842e3c,0x20000);self.seed(0x842e50,0x20010)
    def trap(self,pc,r):
        if pc==0xbd00:self.unlocked=True;return True
        if pc in (0x20000,0x20010):
            address,size,pointer=r[:3]
            assert 0x3d000<=address<=address+size<=0x3f000 and size>0
            if pc==0x20010:
                for i in range(size):self.wr8(pointer+i,self.flash.get(address+i,255))
            else:
                assert self.unlocked
                self.writes.append((address,size))
                for i in range(size):
                    if self.program_limit==0:raise PowerCut()
                    if self.program_limit is not None:self.program_limit-=1
                    old=self.flash.get(address+i,255);value=self.rd8(pointer+i)
                    self.flash[address+i]=old&value
            return True
        if pc==0xfe98:
            address=r[0];assert self.unlocked and address in (0x3d000,0x3e000)
            self.erases.append(address)
            for i in range(4096):
                if self.erase_limit==0:raise PowerCut()
                if self.erase_limit is not None:self.erase_limit-=1
                self.flash[address+i]=255
            return True
        return super().trap(pc,r)

def expanded(frame,build):
    metadata=json.loads((build/"pairing-prototype.json").read_text(encoding="utf-8"))
    payload=(build/"pairing-prototype.bin").read_bytes()
    code=bytearray(frame);offset=metadata["entry"]-image.BASE
    if len(code)<offset+len(payload):code.extend(bytes([255])*(offset+len(payload)-len(code)))
    code[offset:offset+len(payload)]=payload
    return layout.prepare_frame(code),metadata["symbols"]

def check(frame,build):
    code,symbols=expanded(frame,build)
    def cpu(flash=None,**limits):
        c=FlashCPU(code,flash,**limits)
        for i,v in enumerate(ADDRESS):c.seed(CTX+59+i,v,1)
        return c
    def set_bond(c,value):
        c.seed(CTX+30,2,1)
        for i in range(12):c.seed(CTX+31+i,0x20+i,1)
        for i in range(16):c.seed(CTX+43+i,value+i,1)
    def key(c):return bytes(c.rd8(CTX+43+i) for i in range(16))
    c=cpu();assert c.call(symbols["y_bond_load"],CTX)==0
    set_bond(c,0x40);old=key(c)
    assert c.call(symbols["y_bond_save"],CTX)==1 and not c.erases
    baseline=dict(c.flash)
    cold=cpu(dict(baseline));assert cold.call(symbols["y_bond_load"],CTX)==1 and key(cold)==old
    # Every byte boundary in body + commit byte; old record must remain readable.
    for cut in range(65):
        f=dict(baseline);writer=cpu(f,program_limit=cut);set_bond(writer,0x60)
        try:writer.call(symbols["y_bond_save"],CTX)
        except PowerCut:pass
        reader=cpu(f);assert reader.call(symbols["y_bond_load"],CTX)==1
        assert key(reader)==(bytes(range(0x60,0x70)) if cut==64 else old),cut
    # Wrong profile identity cannot load another identity's key.
    wrong=cpu(dict(baseline));wrong.seed(CTX+59,ADDRESS[0]^4,1)
    assert wrong.call(symbols["y_bond_load"],CTX)==0 and key(wrong)==bytes(16)
    # CRC rejects a corrupted key record instead of loading it.
    corrupt=dict(baseline);corrupt[0x3d000+30]^=1
    reader=cpu(corrupt);assert reader.call(symbols["y_bond_load"],CTX)==0
    # Fill both sectors with valid synthetic records to exercise reclamation.
    def record(sequence,value):
        r=bytearray([255])*64
        r[:8]=bytes.fromhex("55 32 43 59 01 02 00 00")
        r[8:12]=sequence.to_bytes(4,"little");r[12:18]=ADDRESS
        r[18:30]=bytes(range(0x20,0x2c));r[30:46]=bytes(range(value,value+16))
        crc=0xffff
        for byte in r[:46]:
            crc^=byte
            for _ in range(8):crc=(crc>>1)^(0xa001 if crc&1 else 0)
        r[46:48]=crc.to_bytes(2,"little");r[63]=0
        return r
    for corrupt_offset in (30,63):
        f=dict(baseline)
        newer=record(2,0x60)
        if corrupt_offset==63:newer[63]=0x7f
        else:newer[corrupt_offset]^=1
        for k,value in enumerate(newer):f[0x3d040+k]=value
        reader=cpu(f)
        assert reader.call(symbols["y_bond_load"],CTX)==1 and key(reader)==old
    saturated={0x3d000+k:value for k,value in enumerate(record(0xfffffffe,0x40))}
    writer=cpu(saturated);set_bond(writer,0x60)
    assert writer.call(symbols["y_bond_save"],CTX)==0 and not writer.writes and not writer.erases
    full={}
    for slot in range(128):
        start=0x3d000+slot*64
        for i,v in enumerate(record(slot+1,0x40)):full[start+i]=v
    for cut in (0,1,63,2048,4095):
        f=dict(full);writer=cpu(f,erase_limit=cut);set_bond(writer,0x60)
        try:writer.call(symbols["y_bond_save"],CTX)
        except PowerCut:pass
        assert writer.erases==[0x3d000] # latest record stays in the other sector
        reader=cpu(f);assert reader.call(symbols["y_bond_load"],CTX)==1 and key(reader)==old
    writer=cpu(dict(full));set_bond(writer,0x60)
    assert writer.call(symbols["y_bond_save"],CTX)==1 and writer.erases==[0x3d000]
    reader=cpu(writer.flash);assert reader.call(symbols["y_bond_load"],CTX)==1 and key(reader)==bytes(range(0x60,0x70))
    # Unknown full data with no valid record is never erased.
    unknown={a:0 for a in range(0x3d000,0x3f000)}
    writer=cpu(unknown);set_bond(writer,0x60)
    assert writer.call(symbols["y_bond_save"],CTX)==0 and not writer.erases and not writer.writes
    return {"ok":True,"cold_boot_load_checked":True,"program_cut_points":65,
            "erase_cut_points":5,"profile_identity_checked":True,"crc_checked":True,
            "sector_reclamation_checked":True,"unknown_data_preserved":True,
            "corrupt_newest_falls_back":True,"sequence_overflow_refused":True,
            "flash_io_emulated":True,"physical_flash_written":False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--backup",type=Path,required=True)
    p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"));a=p.parse_args()
    raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=="__main__":main()
