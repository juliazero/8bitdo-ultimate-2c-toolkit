# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute LL encryption handshake with compiled profile callbacks. No radio I/O."""
import argparse,hashlib,json
from pathlib import Path
from tools import switch2_layout as layout
from tools.check_switch2_gatt import GattCPU
from tools.check_switch2_bonds import expanded
from tools.check_switch2_pairing import CTX,REQUEST,RESPONSE,ADDRESS,TRANSCRIPT
from tools.check_switch2_link import interval_candidate
from tools.check_switch2_transport import mod,input_report
from u2c_customfw.firmware import image,recipes
CRYPTO=0x8435cc

class SecurityCPU(GattCPU):
    def __init__(self,frame,flash=None):
        self.random_calls=[];self.app_events=[]
        super().__init__(frame,flash)
        self.seed(0x842e70,0x1ffff1)
    def wr8(self,a,value):
        if 0x800c00<=a<0x800c40 or 0x800f00<=a<0x800f04:
            self.ram[a]=value&255;return
        super().wr8(a,value)
    def trap(self,pc,r):
        if pc==0x1ffff0:
            self.app_events.append(tuple(r[:3]));return True # application event boundary
        if pc==0x10c20:
            size,destination=r[:2]
            assert (size,destination) in ((8,0x842eec),(4,0x842f0c))
            data=bytes(range(8,16)) if size==8 else bytes(range(0xa4,0xa8))
            for i,value in enumerate(data):self.wr8(destination+i,value)
            self.random_calls.append(size)
            return True # boundary: deterministic test RNG only
        return super().trap(pc,r)

def check(frame,build):
    code,symbols=expanded(interval_candidate(frame),build)
    root=Path(__file__).resolve().parents[1]
    session=json.loads((root/"tests/switch2-session-vector.json").read_text(encoding="utf-8-sig"))
    vectors=json.loads((root/"tests/switch2-ll-vectors.json").read_text(encoding="utf-8-sig"))
    # Establish a persistent bond by executing the compiled pairing core first.
    first=SecurityCPU(code)
    for i,value in enumerate(ADDRESS):first.seed(CTX+59+i,value,1)
    for request in TRANSCRIPT:
        for i,value in enumerate(request):first.seed(REQUEST+i,value,1)
        assert first.call(symbols["y_pair_request"],CTX,REQUEST,len(request),RESPONSE)>0
    assert first.call(symbols["y_bond_save"],CTX)==1
    baseline=dict(first.flash)
    def prepare(host):
        c=SecurityCPU(code,dict(baseline))
        for i,value in enumerate(ADDRESS):c.seed(CTX+59+i,value,1)
        assert c.call(symbols["y_bond_load"],CTX)==1
        c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
        c.call(symbols["y_security_install"])
        assert c.rd(0x8435f8,4)==symbols["y_ltk_request"]|1
        assert c.rd(0x843684,4)==symbols["y_connected"]|1
        c.seed(0x842ed0,0x843ac8);c.seed(0x843aa4,1000000);c.seed(0x843a32,0,1)
        packet=bytearray(40);packet[4]=5;packet[5]=34;packet[6:12]=host;packet[12:18]=ADDRESS[::-1]
        packet[25]=1;packet[28:30]=(4).to_bytes(2,"little")
        packet[32:34]=(200).to_bytes(2,"little");packet[34:39]=bytes.fromhex("ff ff ff ff 1f");packet[39]=5
        for i,value in enumerate(packet):c.seed(REQUEST+i,value,1)
        c.call(0x11928,REQUEST,0)
        assert c.rd8(0x843a32)==8 and c.rd8(layout.Y_PEER_VALID)==1
        assert bytes(c.rd8(layout.Y_PEER+i) for i in range(6))==host
        return c
    def receive(c,payload):
        packet=bytes(4)+bytes((3,len(payload)))+payload
        for i,value in enumerate(packet):c.seed(REQUEST+i,value,1)
        return c.call(0x125c0,REQUEST)
    def consume(c):c.seed(0x843a24,c.rd8(0x843a23),1)
    def queued(c,index):
        base=0x843178+(index&7)*40
        return bytes(c.rd8(base+4+i) for i in range(c.rd8(base+5)+2))
    enc_req=b"\x03"+bytes(10)+bytes.fromhex(session["skdm"])+bytes.fromhex(session["ivm"])
    outcomes=[]
    for host in (TRANSCRIPT[0][10:16],TRANSCRIPT[0][16:22]):
        c=prepare(host)
        assert receive(c,enc_req)==1
        assert c.random_calls==[8,4]
        assert queued(c,0)==bytes.fromhex("03 0d 04")+bytes.fromhex(session["skds"]+session["ivs"])
        assert c.rd8(CRYPTO+0x25)==2 and c.rd8(CRYPTO+0x26)==0
        consume(c);c.call(0x1248c)
        assert queued(c,1)==bytes.fromhex("03 01 05")
        assert c.rd8(CRYPTO+0x25)==3 and c.rd8(CRYPTO+0x26)==1
        assert bytes(c.rd8(CRYPTO+8+i) for i in range(16))==bytes.fromhex(session["session_key_register_order"])
        consume(c)
        incoming=next(v for v in vectors["control"] if v["direction"]==1)
        assert receive(c,bytes.fromhex(incoming["ciphertext"]))==1
        outgoing=next(v for v in vectors["control"] if v["direction"]==0)
        assert queued(c,2)==bytes.fromhex("03 05")+bytes.fromhex(outgoing["ciphertext"])
        assert c.rd8(CRYPTO+0x25)==5 and c.rd(CRYPTO,4)==1 and c.rd(CRYPTO+4,4)==1
        consume(c);c.seed(0x842f22,66,2)
        packet=input_report(buttons=("A","GL"),counter=71)
        for i,value in enumerate(packet):c.seed(mod.PACKET+i,value,1)
        assert c.call(0x1458c,0x40,0xe,mod.PACKET,len(packet))==0
        for offset,expected in enumerate(vectors["input"]):
            wire=queued(c,3+offset)
            assert wire[0]==expected["aad"] and wire[2:]==bytes.fromhex(expected["ciphertext"])
        assert c.rd(CRYPTO,4)==4
        c.call(symbols["y_att_disconnect"])
        assert c.rd8(layout.Y_PEER_VALID)==0
        outcomes.append({"saved_host_index":len(outcomes),"handshake_complete":True,"encrypted_report_checked":True})
    unknown=bytearray(TRANSCRIPT[0][10:16]);unknown[0]^=0x80
    c=prepare(bytes(unknown));receive(c,enc_req)
    assert c.rd8(CRYPTO+0x25)==9 and c.rd8(CRYPTO+0x26)==0
    consume(c);c.call(0x1248c)
    assert c.rd8(CRYPTO+0x25)==0 and c.rd8(CRYPTO+0x26)==0
    assert queued(c,1)==bytes.fromhex("03 02 0d 06")
    # Invalid callback handle cannot supply or reject a key for another connection.
    c=prepare(TRANSCRIPT[0][10:16])
    before=bytes(c.rd8(0x8435b8+i) for i in range(16))
    assert c.call(symbols["y_ltk_request"],0x41,0,0)==2
    assert bytes(c.rd8(0x8435b8+i) for i in range(16))==before
    assert c.rd8(CRYPTO+0x25)==0
    c.call(symbols["y_att_disconnect"])
    assert c.call(symbols["y_ltk_request"],0x40,0,0)==0
    assert c.rd8(CRYPTO+0x25)==9 # cleared peer state cannot reuse the stored key
    # Bad MIC must not advance the LL_START_ENC_RSP state.
    c=prepare(TRANSCRIPT[0][10:16]);receive(c,enc_req);consume(c);c.call(0x1248c);consume(c)
    bad=bytearray(bytes.fromhex(incoming["ciphertext"]))
    bad[-1]^=1
    assert receive(c,bytes(bad))==0
    assert c.rd8(CRYPTO+0x27)==1 and c.rd8(CRYPTO+0x25)!=5
    return {"ok":True,"saved_hosts":outcomes,"stock_ll_control_handler_executed":True,
            "stock_key_reply_executed":True,"unknown_host_rejected":True,"bad_mic_rejected":True,"wrong_handle_rejected":True,
            "disconnected_peer_cannot_reuse_key":True,
            "reference":"Windows .NET AES-CCM","rng_and_aes_hardware_emulated":True,
            "radio_isr_executed":False,"startup_hook_installed":False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--backup",type=Path,required=True)
    p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"));a=p.parse_args()
    raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=="__main__":main()
