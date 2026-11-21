# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute compiled Pro 2 init and normalized input through stock ATT/TX. Offline."""
import argparse,hashlib,json,struct
from pathlib import Path
from tools import switch2_layout as layout
from tools.build_switch2_gatt import BASE as GATT_BASE
from tools.check_switch2_gatt import GattCPU
from tools.check_switch2_pairing import CTX,REQUEST
from tools.switch2_prototype import input_report,BUTTON_BITS
from u2c_customfw.firmware import image,recipes
INPUT=0x843d40

def check(frame,build):
    metadata=json.loads((build/"pairing-prototype.json").read_text(encoding="utf-8"))
    symbols=metadata["symbols"];code=bytearray(frame)
    for address,name in ((0x1a000,"pairing-prototype.bin"),(GATT_BASE,"gatt-prototype.bin")):
        payload=(build/name).read_bytes();start=address-image.BASE
        if len(code)<start:code.extend(bytes([255])*(start-len(code)))
        code[start:start+len(payload)]=payload
    code=layout.prepare_frame(code)
    c=GattCPU(code);c.call(symbols["y_att_disconnect"])
    c.call(0x11e64,0x843178,40,8);c.call(0x121a0);c.call(0x13628,GATT_BASE)
    c.seed(0x842f20,66,2);c.seed(0x842f22,23,2)
    def consume():c.seed(0x843a24,c.rd8(0x843a23),1)
    def request(att):
        consume()
        p=bytes((2,len(att)+4))+len(att).to_bytes(2,"little")+b"\x04\x00"+att
        for i,value in enumerate(p):c.seed(REQUEST+i,value,1)
        return c.call(0x138c0,0x40,REQUEST)
    request(bytes.fromhex("024200"))
    for handle in (0x1f,0xf):request(bytes((0x12,handle,0,1,0)))
    def command(command,sub,payload=b"",handle=0x16):
        count=len(c.notifications)
        req=bytes((command,0x91,1,sub,0,len(payload),0,0))+payload
        assert request(bytes((0x52,handle,0))+req)==0
        return c.notifications[count:]
    def expected(command,sub,body):
        return [(0x1e,bytes((command,1,1,sub,0x10,0x78,0,0))+body)]
    def feature(sub,flags=0):return command(0x0c,sub,bytes((flags,0,0,0)))
    def seed_input(buttons=("A","GL","Home","ZR"),sticks=(0,4095,4095,0),battery_level=4,power=3):
        mask=sum(1<<BUTTON_BITS[b] for b in buttons)
        data=struct.pack("<I4HBB",mask,*sticks,battery_level,power)
        for i,value in enumerate(data):c.seed(INPUT+i,value,1)
        return {"buttons":buttons,"sticks":sticks,"battery_level":battery_level,
                "external_power":bool(power&1),"charging":bool(power&2)}
    controls=seed_input()
    feature(3)  # explicit clear disables the native buttons/sticks enabled at reset
    assert c.call(symbols["y_input_send"],INPUT)==0xffffffff
    for handle in (0x14,0x16):assert command(7,1,handle=handle)==expected(7,1,b"\x00")
    assert command(0x10,1)==expected(0x10,1,bytes.fromhex("01 00 0e 02 0c 00 00 00 ff ff ff ff"))
    assert feature(1,0x2f)==expected(0x0c,1,bytes.fromhex("00 00 00 00 07 07 00 00 00 00 00 00"))
    assert not feature(4,3) and c.rd8(layout.Y_ENABLED)==0 # mask must be initialized first
    assert feature(2,0x2f)==expected(0x0c,2,bytes(4))
    assert c.rd8(layout.Y_MASK)==3 and c.rd8(layout.Y_ENABLED)==0
    assert feature(4,0x2f)==expected(0x0c,4,bytes(4))
    assert c.rd8(layout.Y_ENABLED)==3
    consume();assert c.call(symbols["y_input_send"],INPUT)==0
    packet=input_report(counter=0,**controls)
    assert c.notifications[-1]==(0xe,packet)
    assert bytes(c.rd8(layout.Y_SHADOW+i) for i in range(63))==packet
    # Stock ATT read exposes the most recently accepted input report.
    start=c.rd8(0x843a23)
    result=request(bytes.fromhex("0a0e00"))
    # For long values stock ATT queues the response directly and returns NULL.
    assert result==0
    count=(c.rd8(0x843a23)-start)&255
    assembled=bytearray()
    for slot in range(count):
        base=0x843178+((start+slot)&7)*40
        size=c.rd8(base+5)
        assembled.extend(c.rd8(base+6+i) for i in range(size))
    assert assembled==bytes.fromhex("40 00 04 00 0b")+packet,assembled.hex()
    read_length=len(assembled)-5
    assert feature(5,1)==expected(0x0c,5,bytes(4))
    consume();assert c.call(symbols["y_input_send"],INPUT)==0
    assert c.notifications[-1]==(0xe,input_report(counter=1,**dict(controls,buttons=())))
    feature(5,2)
    assert c.call(symbols["y_input_send"],INPUT)==0xffffffff and c.rd8(layout.Y_COUNTER)==2
    feature(4,3)
    # A full queue does not consume a report counter or change the readable shadow.
    previous=bytes(c.rd8(layout.Y_SHADOW+i) for i in range(63))
    c.seed(0x843a23,10,1);c.seed(0x843a24,4,1)
    assert c.call(symbols["y_input_send"],INPUT)==0x81
    assert c.rd8(layout.Y_COUNTER)==2 and bytes(c.rd8(layout.Y_SHADOW+i) for i in range(63))==previous
    consume();c.seed(layout.Y_COUNTER,254,1)
    for counter in (254,255,0):
        consume();assert c.call(symbols["y_input_send"],INPUT)==0
        assert c.notifications[-1]==(0xe,input_report(counter=counter,**controls))
    for offset,value,size in ((12,10,1),(13,4,1),(4,4096,2),(0,1<<21,4)):
        seed_input();c.seed(INPUT+offset,value,size)
        assert c.call(symbols["y_input_send"],INPUT)==0xffffffff
    seed_input()
    # Physical adapter reads the existing processed controller register layout.
    source={13:"B",12:"A",4:"Y",5:"X",11:"R",15:"ZR",0:"Plus",2:"R3",
            8:"Down",6:"Right",7:"Left",9:"Up",10:"L",14:"ZL",3:"Minus",1:"L3",
            17:"Home",25:"GR",26:"GL"}
    for address in (0x8433ec,0x8433f6,0x8433e4,0x8433e6):c.seed(address,32767,2)
    c.seed(0x843415,100,1);c.seed(0x800518,0,1)
    for bit,name in source.items():
        consume();c.seed(0x84342c,1<<bit)
        counter=c.rd8(layout.Y_COUNTER)
        assert c.call(symbols["y_input_from_controller"])==0
        assert c.notifications[-1]==(0xe,input_report(buttons=(name,),counter=counter))
    c.seed(0x84342c,0)
    for address,value in ((0x8433ec,0),(0x8433f6,65535),(0x8433e4,65535),(0x8433e6,0)):
        c.seed(address,value,2)
    for percent,level in ((0,0),(1,1),(11,1),(12,2),(50,5),(88,8),(89,9),(100,9)):
        c.seed(0x843415,percent,1);c.seed(0x800518,1,1);c.seed(0x843423,0,1)
        counter=c.rd8(layout.Y_COUNTER);consume()
        assert c.call(symbols["y_input_from_controller"])==0
        assert c.notifications[-1]==(0xe,input_report(counter=counter,sticks=(4095,0,0,4095),
                                                   battery_level=level,external_power=True,charging=True))
    # Both physical stick pairs: isolate each axis and check both report formats.
    # Nintendo Y is up-positive; the stock D-input Y conversion was down-positive.
    from tools.switch2_prototype import common_report
    axes=(0x8433ec,0x8433f6,0x8433e4,0x8433e6)
    c.seed(0x843415,50,1);c.seed(0x800518,0,1)
    for index in range(4):
        for raw,expected in ((0,4095),(65535,0)):
            for address in axes:c.seed(address,32768,2)
            c.seed(axes[index],raw,2);values=[2048]*4;values[index]=expected
            counter=c.rd8(layout.Y_COUNTER);consume()
            assert c.call(symbols['y_input_from_controller'])==0
            assert c.notifications[-1]==(0xe,input_report(counter=counter,sticks=values,battery_level=5))
            c.seed(layout.Y_INPUT_CCC,0,2);c.seed(layout.CCC[0xa],1,2)
            c.seed(0x843424,3700,2);consume()
            common_counter=int.from_bytes(bytes(c.rd8(CTX+14+i) for i in range(4)),'little')
            assert c.call(symbols['y_input_from_controller'])==0
            assert c.notifications[-1]==(0xa,common_report(counter=common_counter,sticks=values,voltage_mv=3700))
            c.seed(layout.Y_INPUT_CCC,1,2);c.seed(layout.CCC[0xa],0,2)
    c.seed(0x800518,1,1)
    c.seed(0x843423,1,1);consume()
    assert c.call(symbols["y_input_from_controller"])==0
    assert c.notifications[-1][1][1]&3==1
    # Unsupported flash/IMU operations do not get fabricated success responses.
    assert not command(2,4,bytes.fromhex("10 7e 00 00 00 00 00 00"))
    assert not command(0x0c,6,bytes(4))
    feature(3)
    assert c.rd8(layout.Y_MASK)==0 and c.rd8(layout.Y_ENABLED)==0
    assert not feature(4,3)
    c.call(symbols["y_att_disconnect"])
    assert c.rd8(layout.Y_COUNTER)==0 and c.rd(layout.Y_INPUT_CCC,2)==0
    assert c.call(symbols["y_input_send"],INPUT)==0xffffffff
    return {"ok":True,"stack_peak_bytes":c.stack_top-c.minimum_sp,"stack_margin_bytes":c.minimum_sp-c.stack_floor,"compiled_bytes":metadata["bytes"],"command_handles":["0x14","0x16"],
            "feature_mask_checked":True,"input_reports_checked":5,"counter_wrap_checked":True,
            "stock_att_read_length":read_length,"queue_failure_preserves_counter":True,
            "unsupported_features_not_advertised":True,"physical_register_adapter_executed":True,"physical_button_vectors":19,
            "battery_boundary_vectors":8,"console_axis_orientation_verified":False,
            "radio_cadence_tested":False,"installed":False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--backup",type=Path,required=True)
    p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"));a=p.parse_args()
    raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=="__main__":main()
