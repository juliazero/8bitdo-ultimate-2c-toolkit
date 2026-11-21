# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute virtual calibration reads through stock ATT and BLE TX. No hardware I/O."""
import argparse,hashlib,json
from pathlib import Path
from tools import switch2_layout as layout
from tools.build_switch2_gatt import BASE as GATT_BASE
from tools.check_switch2_bonds import expanded
from tools.check_switch2_gatt import GattCPU
from tools.check_switch2_pairing import ADDRESS,CTX,REQUEST
from u2c_customfw.firmware import image,recipes

def check(frame,build):
    code,symbols=expanded(frame,build)
    data=(build/"gatt-prototype.bin").read_bytes();start=GATT_BASE-image.BASE
    code.extend(bytes([255])*(start-len(code)));code[start:start+len(data)]=data
    c=GattCPU(code)
    for i,value in enumerate(ADDRESS):c.seed(CTX+59+i,value,1)
    c.call(symbols["y_att_disconnect"]);c.call(0x11e64,0x843178,40,8);c.call(0x121a0);c.call(0x13628,GATT_BASE)
    def consume():c.seed(0x843a24,c.rd8(0x843a23),1)
    def att(data):
        consume();packet=bytes((2,len(data)+4))+len(data).to_bytes(2,"little")+b"\x04\x00"+data
        for i,value in enumerate(packet):c.seed(REQUEST+i,value,1)
        return c.call(0x138c0,0x40,REQUEST)
    def mtu(value):
        c.seed(0x842f20,value,2);c.seed(0x842f22,23,2)
        att(b"\x02"+value.to_bytes(2,"little"))
        assert c.rd(0x842f22,2)==value
    def read(address,size,sub=4):
        request=bytes((2,0x91,1,sub,0,8,0,0,size,0x7e,0,0))+address.to_bytes(4,"little")
        before=len(c.notifications)
        att(b"\x52\x16\x00"+request)
        return c.notifications[before:]
    mtu(66);att(bytes.fromhex("121f000100"))
    assert not read(0x13000,64) and c.rd8(layout.Y_PENDING)==0
    mtu(83)
    # Guard the new 80-byte response allocation and adjacent profile state.
    c.seed((layout.Y_REPLY-1),0xa5,1);c.seed((layout.Y_REPLY+80),0x5a,1)
    snapshots={a:c.rd8(a) for a in range(CTX,CTX+0x80)}
    result=read(0x13000,64)
    assert len(result)==1 and result[0][0]==0x1e
    reply=result[0][1]
    assert len(reply)==80 and reply[:8]==bytes.fromhex("0201010410780000")
    assert reply[8:16]==bytes.fromhex("4000000000300100")
    assert reply[18:34]==b"U2"+ADDRESS.hex().upper().encode()+bytes(2)
    assert reply[34:38]==bytes.fromhex("7e056920")
    assert c.rd8((layout.Y_REPLY-1))==0xa5 and c.rd8((layout.Y_REPLY+80))==0x5a
    assert all(c.rd8(a)==value for a,value in snapshots.items())
    # The last reply occupied four real TX slots; reconstruct L2CAP and ATT.
    end=c.rd8(0x843a23);joined=bytearray()
    for index in range(end-4,end):
        base=0x843178+(index&7)*40;size=c.rd8(base+5)
        joined.extend(c.rd8(base+6+i) for i in range(size))
    assert joined==bytes.fromhex("530004001b1e00")+reply
    # A long busy reply stays intact until the queue can accept all four fragments.
    c.seed(0x843a23,10,1);c.seed(0x843a24,5,1)
    request=bytes.fromhex("0291010400080000407e000000300100")
    packet=bytes((2,len(request)+7))+(len(request)+3).to_bytes(2,"little")+bytes.fromhex("0400521600")+request
    for i,value in enumerate(packet):c.seed(REQUEST+i,value,1)
    c.call(0x138c0,0x40,REQUEST)
    assert c.rd8(layout.Y_PENDING)==80
    pending=bytes(c.rd8(layout.Y_REPLY+i) for i in range(80))
    assert pending==reply
    consume();c.call(symbols["y_reply_poll"])
    assert c.rd8(layout.Y_PENDING)==0 and c.notifications[-1]==(0x1e,reply)
    def decode_pair(data):
        return data[0]|((data[1]&15)<<8),(data[1]>>4)|(data[2]<<4)
    for address in (0x130a8,0x130e8):
        reply=read(address,9)[0][1];cal=reply[16:]
        assert tuple(decode_pair(cal[i:i+3]) for i in (0,3,6))==((2048,2048),(2047,2047),(2048,2048))
        for center,positive,negative in zip(decode_pair(cal[:3]),decode_pair(cal[3:6]),decode_pair(cal[6:9])):
            assert center-negative==0 and center+positive==4095
    # Captured console read windows, including uninitialized user calibration.
    for address,size in ((0x13080,64),(0x130c0,64),(0x1fc040,64),(0x13040,16),(0x13100,24),(0x13060,32)):
        result=read(address,size)
        assert len(result)==1 and len(result[0][1])==size+16
    assert read(0x1fc040,64)[0][1][16:]==bytes([255])*64
    for address,size,sub in ((0,16,4),(0x3d000,16,4),(0x73000,16,4),(0x76000,16,4),
                             (0xfffffffc,64,4),(0x1315f,2,4),(0x13000,0,4),(0x13000,65,4),(0x13000,16,5)):
        assert not read(address,size,sub)
    # Exercise the deepest response path with stock CCM enabled as well.
    for i,value in enumerate(bytes(range(16))):c.seed(0x8435cc+8+i,value,1)
    for i,value in enumerate(bytes(range(0xa0,0xa8))):c.seed(0x8435cc+29+i,value,1)
    c.seed(0x8435cc+0x26,1,1)
    encrypted=read(0x13000,64)
    assert len(encrypted)==1 and len(encrypted[0][1])==80
    assert c.rd(0x8435cc,4)==4
    assert not c.writes and not c.erases
    return {"ok":True,"stack_peak_bytes":c.stack_top-c.minimum_sp,"stack_margin_bytes":c.minimum_sp-c.stack_floor,"mtu_83_checked":True,"encrypted_large_response_checked":True,"large_response_bytes":80,"tx_fragments":4,"large_reply_retry_checked":True,
            "normalized_stick_calibration_checked":True,"console_read_windows_checked":6,
            "uninitialized_user_calibration_checked":True,"physical_flash_reads_exposed":False,
            "physical_flash_written":False,"console_compatibility_verified":False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--backup",type=Path,required=True)
    p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"));a=p.parse_args()
    raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=="__main__":main()
