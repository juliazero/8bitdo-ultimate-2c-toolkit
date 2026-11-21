# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay the documented Pro 2 init commands through compiled code and stock ATT.
Unsupported vibration steps are checked and reported, never treated as success.
"""
import argparse,hashlib,json
from pathlib import Path
from tools import switch2_layout as layout
from tools.build_switch2_gatt import BASE as GATT_BASE
from tools.check_switch2_bonds import expanded
from tools.check_switch2_gatt import GattCPU
from tools.check_switch2_pairing import ADDRESS,CTX,REQUEST,TRANSCRIPT
from tools.switch2_prototype import input_report
from u2c_customfw.firmware import image,recipes

def check(frame,build):
    code,symbols=expanded(frame,build)
    table=(build/"gatt-prototype.bin").read_bytes();start=GATT_BASE-image.BASE
    code.extend(bytes([255])*(start-len(code)));code[start:start+len(table)]=table
    c=GattCPU(code)
    for i,value in enumerate(ADDRESS):c.seed(CTX+59+i,value,1)
    c.call(symbols["y_att_disconnect"]);c.call(0x11e64,0x843178,40,8);c.call(0x121a0);c.call(0x13628,GATT_BASE)
    steps=[];unsupported=[]
    def att(data):
        c.seed(0x843a24,c.rd8(0x843a23),1)
        packet=bytes((2,len(data)+4))+len(data).to_bytes(2,"little")+b"\x04\x00"+data
        for i,value in enumerate(packet):c.seed(REQUEST+i,value,1)
        return c.call(0x138c0,0x40,REQUEST)
    def write(handle,data):
        pointer=att(b"\x12"+handle.to_bytes(2,"little")+data)
        assert c.rd8(pointer+6)==0x13,(handle,hex(pointer))
    def command(packet,label,body=None):
        before=len(c.notifications)
        assert att(bytes.fromhex("521600")+packet)==0
        notes=c.notifications[before:]
        assert len(notes)==1 and notes[0][0]==0x1e,(label,notes)
        reply=notes[0][1]
        assert reply[:8]==bytes((packet[0],1,1,packet[3],0x10,0x78,0,0))
        if body is not None:assert reply[8:]==body,(label,reply.hex())
        assert c.rd8(layout.Y_PENDING)==0
        steps.append(label)
        return reply
    def request(command,sub,payload=b""):
        return bytes((command,0x91,1,sub,0,len(payload),0,0))+payload
    def memory(address,size):
        reply=command(request(2,4,bytes((size,0x7e,0,0))+address.to_bytes(4,"little")),"read_"+hex(address))
        assert len(reply)==size+16
    # An undersized MTU must not create a permanently pending 32-byte reply.
    c.seed(0x842f20,23,2);c.seed(0x842f22,23,2);write(0x1f,b"\x01\x00")
    before=len(c.notifications);att(bytes.fromhex("5216001691010100000000"))
    assert len(c.notifications)==before and c.rd8(layout.Y_PENDING)==0
    c.seed(0x842f20,83,2);att(bytes.fromhex("025300"))
    assert c.rd(0x842f22,2)==83
    write(5,b"\x01\x00");assert c.rd(layout.Y_BOOTSTRAP,2)==1;steps.append("bootstrap_write")
    command(request(7,1),"bootstrap_command",b"\x00")
    memory(0x13000,64)
    command(request(0x16,1),"startup_16",bytes(24))
    for index,packet in enumerate(TRANSCRIPT):command(packet,"pair_"+str(index))
    assert c.rd8(CTX+30)==2 and c.flash
    # These are documented init steps, but Pro 2 motor decoding is not implemented.
    for packet,label in ((request(0xa,2,bytes.fromhex("03000000")),"vibration_sample"),
                          (request(0xa,8,bytes.fromhex("01 ff ff ff ff ff ff ff ff 35 00 46 00 00 00 00 00 00 00 00")),"vibration_data")):
        before=len(c.notifications);att(bytes.fromhex("521600")+packet)
        assert len(c.notifications)==before+1 and c.rd8(layout.Y_PENDING)==0
        assert c.notifications[-1][1][0:2]==bytes((packet[0],4))
        unsupported.append(label)
    command(request(9,7,bytes.fromhex("0100000000000000")),"player_assignment",b"")
    assert c.rd8(layout.Y_PLAYER)==1
    command(request(0xc,2,bytes.fromhex("2f000000")),"feature_mask",bytes(4))
    for address,size in ((0x13080,64),(0x130c0,64),(0x1fc040,64),(0x13040,16),(0x13100,24)):
        memory(address,size)
    opaque=bytes.fromhex("01200300000ae81c3b797d8b3a0ae89c4258a00b420ae89c4158a00b41")
    assert len(opaque)==29
    command(request(0x11,3),"startup_11_03",opaque)
    memory(0x13060,32)
    command(request(0x11,1),"startup_11_01",bytes.fromhex("01000000"))
    command(request(0xc,4,bytes.fromhex("2f000000")),"enable_input",bytes(4))
    write(0x10,bytes.fromhex("8500"));assert c.rd(layout.Y_RATE,2)==0x85;steps.append("rate_descriptor")
    write(0xf,b"\x01\x00");steps.append("input_subscription")
    for address in (0x8433ec,0x8433f6,0x8433e4,0x8433e6):c.seed(address,32767,2)
    c.seed(0x843415,100,1);c.seed(0x84342c,1<<13);c.seed(0x800518,0,1)
    c.seed(0x843a24,c.rd8(0x843a23),1)
    assert c.call(symbols["y_input_from_controller"])==0
    assert c.notifications[-1]==(0xe,input_report(buttons=("B",)))
    # Malformed/unsupported start commands must not mutate player or feature state.
    for packet in (request(9,7,bytes.fromhex("1000000000000000")),
                   request(9,7,bytes.fromhex("0101000000000000")),
                   request(0x16,1,b"\x00"),request(0x11,4),request(0x11,3,b"\x00")):
        snapshot=bytes(c.rd8(CTX+i) for i in range(0x80))
        before=len(c.notifications);att(bytes.fromhex("521600")+packet)
        assert len(c.notifications)==before
        assert bytes(c.rd8(CTX+i) for i in range(0x80))==snapshot
    c.call(symbols["y_att_disconnect"])
    assert c.rd8(layout.Y_PLAYER)==0 and c.rd(layout.Y_RATE,2)==0 and c.rd(layout.Y_BOOTSTRAP,2)==0
    return {"ok":True,"stack_peak_bytes":c.stack_top-c.minimum_sp,"stack_margin_bytes":c.minimum_sp-c.stack_floor,"supported_steps":steps,"unsupported_steps":unsupported,
            "first_input_report_checked":True,"invalid_start_commands_checked":5,"startup_replay_complete":False,
            "ll_encryption_in_this_replay":False,"hardware_led_changed":False,
            "radio_tested":False,"installed":False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--backup",type=Path,required=True)
    p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"));a=p.parse_args()
    raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=="__main__":main()
