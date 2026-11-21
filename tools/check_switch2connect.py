# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay Switch2Connect v3.1 channels and startup through compiled firmware.
Reference: TommyWabg/Switch2Connect v3.1 src/controller.py. No device I/O.
"""
import argparse,hashlib,json
from pathlib import Path
from tools.check_switch2_bonds import expanded
from tools.check_switch2_gatt import GattCPU
from tools.check_switch2_pairing import CTX,REQUEST,ADDRESS
from tools import switch2_layout as layout
from tools.switch2_prototype import input_report,common_report
from u2c_customfw.firmware import image,recipes

SEQUENCE=((3,13,bytes.fromhex('0100ffffffffffff')),(7,1,b''),(22,1,b''),(21,3,b'\0'),
          (12,2,bytes.fromhex('94000000')),(17,3,b''),
          (10,8,bytes.fromhex('01 ff ff ff ff ff ff ff ff 35 00 46 00 00 00 00 00 00 00 00')),
          (12,4,bytes.fromhex('94000000')),(3,10,bytes.fromhex('09000000')),
          (16,1,b''),(1,12,b''),(9,7,bytes.fromhex('0100000000000000')))

class CompatCPU(GattCPU):
    def trap(self,pc,r):
        if pc==0x1458c and getattr(self,'interrupt_pairing',False):
            self.interrupt_pairing=False
            self.seed(CTX,2,1)
            for i in range(16):self.seed(CTX+14+i,0xa5,1)
        return super().trap(pc,r)

def check(frame,build):
    code,symbols=expanded(frame,build);code=bytearray(code)
    table=(build/'gatt-prototype.bin').read_bytes();start=0x1c000-image.BASE
    code[start:start+len(table)]=table
    c=CompatCPU(code)
    for i,value in enumerate(ADDRESS):c.seed(CTX+59+i,value,1)
    c.call(symbols['y_att_disconnect']);c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
    c.call(0x13628,0x1c000);c.seed(0x842f20,83,2)
    def consume():c.seed(0x843a24,c.rd8(0x843a23),1)
    def att(data):
        consume();packet=bytes((2,len(data)+4))+len(data).to_bytes(2,'little')+b'\x04\0'+data
        for i,v in enumerate(packet):c.seed(REQUEST+i,v,1)
        return c.call(0x138c0,0x40,REQUEST)
    att(bytes.fromhex('025300'))
    # Windows discovers services before subscribing. Every group must advance;
    # an empty final 1801 service made the stock SDK return end handle zero.
    start=1;groups=[]
    while start<=0x2f:
        response=att(bytes((0x10,start&255,start>>8,255,255,0,0x28)))
        size=c.rd(response+2,2)
        payload=bytes(c.rd8(response+6+i) for i in range(size))
        if payload[0]==1:
            assert payload[1]==0x10 and payload[4]==0x0a,payload.hex()
            break
        assert payload[0]==0x11 and payload[1] in (6,20),payload.hex()
        width=payload[1];assert (len(payload)-2)%width==0
        for offset in range(2,len(payload),width):
            first=int.from_bytes(payload[offset:offset+2],'little')
            last=int.from_bytes(payload[offset+2:offset+4],'little')
            assert start<=first<=last<=0x2f,(start,first,last)
            groups.append((first,last));start=last+1
    assert groups==[(1,7),(8,0x2a),(0x2b,0x2f)],groups
    att(bytes.fromhex('121b000100'))
    assert c.rd(layout.CCC[0x1a],2)==1 and c.rd(layout.Y_REPLY_CCC,2)==0
    def command(cmd,sub,data):
        before=len(c.notifications);packet=bytes((cmd,0x91,1,sub,0,len(data),0,0))+data
        att(bytes.fromhex('521400')+packet)
        assert len(c.notifications)==before+1,(cmd,sub,c.notifications[before:])
        handle,response=c.notifications[-1];assert handle==0x1a and response[0]==cmd
        assert c.rd8(layout.Y_PENDING)==0
        return response
    rejected=[]
    for cmd,sub,data in SEQUENCE:
        result=command(cmd,sub,data)
        expected=4 if (cmd,sub) in ((3,13),(21,3),(10,8),(1,12)) else 1
        assert result[1]==expected,(cmd,sub,result.hex())
        if expected==4:rejected.append([cmd,sub])
    assert c.rd8(layout.Y_ENABLED)==3 and c.rd8(layout.Y_MASK)==0
    info=command(2,4,bytes.fromhex('407e000000300100'))[16:]
    assert int.from_bytes(info[18:20],'little')==0x057e and int.from_bytes(info[20:22],'little')==0x2069
    assert info[2:16].decode().startswith('U2')
    for address in (0x1fc042,0x130a8,0x1fc062,0x130e8):
        response=command(2,4,bytes((11,0x7e,0,0))+address.to_bytes(4,'little'))
        assert len(response)==27
    att(bytes.fromhex('120b000100'))
    c.seed(0x843424,3700,2);c.seed(0x843415,50,1);c.seed(0x800518,0,1)
    for address in (0x8433ec,0x8433f6,0x8433e4,0x8433e6):c.seed(address,32768,2)
    c.seed(0x84342c,(1<<13)|(1<<26))
    consume();assert c.call(symbols['y_input_from_controller'])==0
    assert c.notifications[-1]==(0xa,common_report(buttons=('B','GL'),voltage_mv=3700))
    # 32-bit common counter carry, without touching committed bond fields.
    for i in range(4):c.seed(CTX+14+i,255,1)
    consume();assert c.call(symbols['y_input_from_controller'])==0
    assert c.notifications[-1][1][:4]==bytes([255])*4
    assert all(c.rd8(CTX+14+i)==0 for i in range(4))
    for stage in (1,2,3):
        c.seed(CTX,stage,1);snapshot=bytes(c.rd8(CTX+i) for i in range(65))
        assert c.call(symbols['y_input_from_controller'])==0xffffffff
        assert bytes(c.rd8(CTX+i) for i in range(65))==snapshot
    c.seed(CTX,0,1)
    # Both report subscribers are served fairly, with one queued report per tick.
    c.seed(layout.Y_INPUT_CCC,1,2);handles=[]
    for _ in range(4):
        consume();assert c.call(symbols['y_input_from_controller'])==0
        handles.append(c.notifications[-1][0])
    assert handles.count(0xa)==handles.count(0xe)==2
    c.seed(layout.Y_INPUT_CCC,0,2)
    for i in range(4):c.seed(CTX+14+i,7 if i==0 else 0,1)
    c.interrupt_pairing=True;consume()
    assert c.call(symbols['y_input_from_controller'])==0
    assert c.notifications[-1][1][:4]==bytes((7,0,0,0))
    assert bytes(c.rd8(CTX+14+i) for i in range(16))==bytes((0xa5,))*16
    assert not c.writes and not c.erases
    # Replay discoverer.add_controller -> Controller.pair(), not just initialize().
    # All writes below are to the interpreter's simulated flash only.
    from tools.switch2_pairing import PairingSession
    c.call(symbols['y_att_disconnect']);att(bytes.fromhex('121b000100'))
    host=bytes.fromhex('1371da7d1a00');model=PairingSession(ADDRESS)
    pairs=((1,b'\0\x02'+host+host),
           (4,bytes.fromhex('00eabd4713893542c679ee07f2532c6c31')),
           (2,bytes.fromhex('0040b08a5fcd1f9b41125cac6f3838a073')),(3,b'\0'))
    for sub,data in pairs:
        expected=model.handle(bytes((21,0x91,1,sub,0,len(data),0,0))+data)
        assert command(21,sub,data)==expected
    assert c.rd8(CTX)==4 and c.rd8(CTX+30)==2
    assert bytes(c.rd8(CTX+31+i) for i in range(12))==host+host
    cold=CompatCPU(code,c.flash)
    for i,value in enumerate(ADDRESS):cold.seed(CTX+59+i,value,1)
    assert cold.call(symbols['y_bond_load'],CTX)==1
    assert bytes(cold.rd8(CTX+31+i) for i in range(12))==host+host
    assert bytes(cold.rd8(CTX+43+i) for i in range(16))==model.bonds[0].ltk_wire
    att(bytes.fromhex('120b000100'));consume()
    assert c.call(symbols['y_input_from_controller'])==0
    assert c.notifications[-1][0]==0xa
    return {'ok':True,'primary_service_discovery_checked':True,'switch2connect_pair_and_cold_boot_checked':True,'startup_commands':len(SEQUENCE),'explicitly_unsupported':rejected,
            'reply_channel':'0x1a','input_channel':'0x0a','common_report_and_counter_checked':True,
            'pairing_scratch_guard_checked':True,'dual_subscription_checked':True,
            'stack_peak_bytes':c.stack_top-c.minimum_sp,'stack_margin_bytes':c.minimum_sp-c.stack_floor,
            'reference':'Switch2Connect v3.1','hardware_tested':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backup',type=Path,required=True)
    p.add_argument('--build',type=Path,default=Path('build/switch2-pairing'));a=p.parse_args()
    raw=a.backup.read_bytes();stock=next(raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
        if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256)
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=='__main__':main()

