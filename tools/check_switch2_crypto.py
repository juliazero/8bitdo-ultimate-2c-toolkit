# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute stock BLE encryption with an emulated AES register peripheral. Offline."""
from tools.check_switch2_transport import QueueCPU
from tools.switch2_pairing import aes128_block

CONTEXT=0x8435cc

class CryptoCPU(QueueCPU):
    def __init__(self,frame):
        self.aes_input=bytearray();self.aes_output=bytes(16);self.aes_read=0
        self.aes_status=2;self.blocks=0
        super().__init__(frame)
    def rd8(self,a):
        if a==0x800700:return self.aes_status
        return super().rd8(a)
    def wr8(self,a,v):
        if a==0x800700:
            self.aes_input=bytearray();self.aes_read=0;self.aes_status=2;return
        if 0x800710<=a<0x800720 or CONTEXT<=a<CONTEXT+0x50 or 0x843b20<=a<0x843b88:
            self.ram[a]=v&255;return
        super().wr8(a,v)
    def wr(self,a,v,n):
        if a==0x800708 and n==4:
            assert self.aes_status==2 and len(self.aes_input)<16
            self.aes_input.extend(v.to_bytes(4,"little"))
            if len(self.aes_input)==16:
                key=bytes(self.rd8(0x800710+i) for i in range(16))
                self.aes_output=aes128_block(key,bytes(self.aes_input))
                self.aes_status=4;self.blocks+=1
            return
        super().wr(a,v,n)
    def rd(self,a,n):
        if a==0x800708 and n==4:
            assert self.aes_status==4 and self.aes_read<16
            result=int.from_bytes(self.aes_output[self.aes_read:self.aes_read+4],"little")
            self.aes_read+=4
            return result
        return super().rd(a,n)
    def trap(self,pc,r):
        if pc in (0x2464,0x103f8):
            for i in range(r[2]):self.wr8(r[0]+i,r[1])
            return True  # memset memory boundary, not cryptography
        return super().trap(pc,r)

def check_encryption(frame):
    import json
    from pathlib import Path
    from tools.check_switch2_transport import mod,input_report
    vectors=json.loads((Path(__file__).resolve().parents[1]/"tests/switch2-ccm-vectors.json").read_text(encoding="utf-8-sig"))
    packet=input_report(buttons=("A","GL"),counter=71)
    cases=[]
    for direction in (0,1):
        c=CryptoCPU(frame)
        c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
        c.seed(0x842f22,66,2)
        for i,v in enumerate(bytes(range(16))):c.seed(CONTEXT+8+i,v,1)
        for i,v in enumerate(bytes(range(0xa0,0xa8))):c.seed(CONTEXT+29+i,v,1)
        c.seed(CONTEXT+0x26,direction+1,1)
        for i,v in enumerate(packet):c.seed(mod.PACKET+i,v,1)
        for i in range(8*40):c.seed(0x843178+i,0 if i%40<4 else 0xa5,1)
        for report in range(2):
            start=6+report*3
            c.seed(0x843a23,start,1);c.seed(0x843a24,start,1)
            assert c.call(0x1458c,0x40,0xe,mod.PACKET,len(packet))==0
            assert c.rd8(0x843a23)==start+3
            assert c.rd(CONTEXT,4)==(report+1)*3
            for fragment in range(3):
                counter=report*3+fragment
                vector=next(v for v in vectors if v["direction"]==direction and v["counter"]==counter)
                base=0x843178+((start+fragment)&7)*40
                encrypted=bytes.fromhex(vector["ciphertext"])
                assert c.rd(base,4)==len(encrypted)+2
                assert c.rd8(base+4)==vector["aad"] and c.rd8(base+5)==len(encrypted)
                actual=bytes(c.rd8(base+6+j) for j in range(len(encrypted)))
                assert actual==encrypted,(direction,counter,actual.hex(),encrypted.hex())
                assert all(c.rd8(base+j)==0xa5 for j in range(6+len(encrypted),40))
        cases.append({"direction_bit":direction,"encrypted_reports":2,"counter_after":6,"aes_blocks":c.blocks})
    return {"cases":cases,"independent_reference":"Windows .NET AesCcm",
            "reference_vectors":len(vectors),"fifo_entry_bytes":40,"maximum_used_bytes":37,
            "fifo_wrap_checked":True,"mic_bytes":4,"hardware_aes_emulated":True,
            "radio_transmission_tested":False,"session_key_installation_tested":False}

def check_session_key(frame):
    import json
    from pathlib import Path
    from tools.check_switch2_transport import mod,input_report
    from tools.switch2_pairing import PairingSession
    vector=json.loads((Path(__file__).resolve().parents[1]/"tests/switch2-session-vector.json").read_text(encoding="utf-8-sig"))
    pairing=PairingSession(bytes.fromhex("98e255c21688"))
    for request in ("15910101000e0000000281eb3aebf14880eb3aebf148",
                    "1591010400110000003503e92982877124bea80c664615834b",
                    "1591010200110000006fc6df8ad8fedf15bb8c15e91f320544",
                    "159101030001000000"):
        pairing.handle(bytes.fromhex(request))
    ltk=pairing.bonds[0].ltk_wire
    assert ltk==bytes.fromhex(vector["ltk_wire"])
    c=CryptoCPU(frame)
    fields=(ltk,bytes.fromhex(vector["skdm"]),bytes.fromhex(vector["skds"]),
            bytes.fromhex(vector["ivm"]),bytes.fromhex(vector["ivs"]))
    pointers=[];cursor=mod.PACKET
    for data in fields:
        pointers.append(cursor)
        for value in data:c.seed(cursor,value,1);cursor+=1
    # Standard ABI: fifth/sixth arguments are supplied on the caller's stack.
    c.seed(mod.SP0,pointers[4]);c.seed(mod.SP0+4,CONTEXT)
    c.call(0x16c34,*pointers[:4])
    actual_key=bytes(c.rd8(CONTEXT+8+i) for i in range(16))
    assert actual_key==bytes.fromhex(vector["session_key_register_order"])
    assert bytes(c.rd8(CONTEXT+29+i) for i in range(8))==fields[3]+fields[4]
    assert c.rd(CONTEXT,4)==0 and c.rd(CONTEXT+4,4)==0
    c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
    c.seed(0x842f22,66,2);c.seed(CONTEXT+0x26,1,1)
    packet=input_report(buttons=("A","GL"),counter=71)
    for i,value in enumerate(packet):c.seed(mod.PACKET+i,value,1)
    assert c.call(0x1458c,0x40,0xe,mod.PACKET,len(packet))==0
    assert c.rd8(0x843a23)==3 and c.rd(CONTEXT,4)==3
    for slot,expected in enumerate(vector["vectors"]):
        base=0x843178+slot*40;size=c.rd8(base+5)
        actual=bytes(c.rd8(base+6+i) for i in range(size))
        assert actual==bytes.fromhex(expected["ciphertext"])
    return {"pairing_model_used":True,"stock_session_derivation_executed":True,
            "encrypted_fragments_checked":3,"reference":"Windows .NET AES/AesCcm",
            "ll_control_handshake_executed":False,"hardware_aes_emulated":True,
            "radio_tested":False}
