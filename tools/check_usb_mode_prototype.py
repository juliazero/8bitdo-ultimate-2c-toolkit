# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute USB prototype + stock USB setters/re-enumeration in TC32. OFFLINE ONLY.
Only peripheral analog access/delay and unrelated stock tasks are trapped.
No device access, firmware output image, hardware flash or recipe registration.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from u2c_customfw.firmware import image, recipes
from tools import build_usb_mode_prototype as proto


def check(stock, keys=None):
    assert hashlib.sha256(stock).hexdigest()==image.STOCK_SHA256
    spec=importlib.util.spec_from_file_location('tc32_usb_mode',ROOT/'firmware-src/test_longrumble.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if keys is None:
        blob, symbols, hooks=proto.build()
        flash=bytearray(recipes.whole(stock))
        flash[proto.ENTRY-image.BASE:proto.ENTRY-image.BASE+len(blob)]=blob
        flash[proto.STATE_IMAGE-image.BASE:proto.STATE_IMAGE-image.BASE+proto.STATE_SIZE]=bytes(proto.STATE_SIZE)
        for address,(old,new) in hooks.items():
            assert flash[address-image.BASE:address-image.BASE+4]==old,hex(address)
            flash[address-image.BASE:address-image.BASE+4]=new
        assert bytes.fromhex((ROOT/'firmware-src/usb-mode-prototype.hex').read_text())==blob
    else:
        results, blob, symbols=proto.integrated(recipes.BLOBS)
        for name,data in results.items(): assert (recipes.BLOBS/name).read_bytes()==data
        flash=bytearray(recipes.build(stock, keys))
        assert set(recipes.identify(flash).recipes)==set(keys)
        assert flash[0x9964-image.BASE:0x9964-image.BASE+(recipes.BLOBS/'battlog.bin').stat().st_size]==(recipes.BLOBS/'battlog.bin').read_bytes()
        assert flash[0x18400-image.BASE:]==recipes.build(stock, [k for k in keys if k!='usb_dinput'])[0x18400-image.BASE:]
    if keys is not None:
        for version,pid in (('01',0x301b),('02',0x301b),('03',0x301d)):
            legacy=bytearray(flash)
            legacy[0x9964-image.BASE:0x9cac-image.BASE]=(recipes.BLOBS/('usbmode'+version+'.bin')).read_bytes()
            legacy[0xf83c-image.BASE:0xf840-image.BASE]=bytes.fromhex('fa976b99')
            legacy[0xf842-image.BASE:0xf846-image.BASE]=bytes.fromhex('fa977099')
            legacy[0xa414-image.BASE:0xa418-image.BASE]=stock[0xa414-image.BASE:0xa418-image.BASE]
            legacy[0x17afe-image.BASE:0x17b00-image.BASE]=pid.to_bytes(2,'little')
            identified=recipes.identify(bytes(legacy))
            assert identified.known and set(identified.recipes)==set(keys),(version,identified)
    class CPU(module.Controller):
        def __init__(self, battery_mv=3700, battery_percent=50):
            self.writes=[]
            super().__init__(bytes(flash))
            self.analog=0x80; self.edges=[]; self.delays=[]; self.vendor_calls=[]; self.slider_calls=0; self.saves=[]; self.reply_attempts=[]; self.reply_busy=0; self.battery_replies=[]
            self.seed(proto.USB,2,1);self.seed(0x800518,1,1)
            self.seed(0x843424,battery_mv)
            self.seed(0x843415,battery_percent,1)
            self.seed(0x843423,1,1)
            self.call(0xd794)
            assert self.rd8(0x843470)==(battery_mv <= 1999)  # actual variant detection
            self.seed(proto.MODE,0,1);self.seed(0x843915,0,1)
            self.seed(module.CONNECTED,0)
        def seed(self,a,v,n=4):
            for i in range(n):self.ram[a+i]=v>>(8*i)&255
        def wr8(self,a,v):
            allowed=(proto.STATE<=a<proto.STATE+proto.STATE_SIZE or
                     (keys is not None and 0x841640<=a<0x8416d4) or
                     a in (0x800125,0x800126,proto.MODE,0x843915,0x843470,0x8434d8,0x8434d9) or
                     module.SP0-0x400<=a<module.SP0 or
                     module.PACKET<=a<module.PACKET+64 or
                     module.CONNECTED<=a<module.CONNECTED+4)
            assert allowed,f'unexpected write {a:#x} at {getattr(self, "last_pc",0):#x}'
            self.writes.append((a,v&255));self.ram[a]=v&255
        def trap(self,pc,r):
            self.last_pc=pc
            if pc == 0x103f8:
                assert r[1] == 0 and r[2] == 64
                for i in range(64): self.wr8(r[0]+i,0)
                return True
            if pc == 0xeb7c:
                assert r[1] == 63 and r[2] == 2
                self.battery_replies.append(self.rd(r[0],6).to_bytes(6,"little"))
                return True
            if pc==0xeb04:
                self.reply_attempts.append(tuple(r[:2]));r[0]=int(self.reply_busy==0);self.reply_busy=max(0,self.reply_busy-1);return True
            if pc==0xd84:
                assert r[0]==11;r[0]=self.analog;return True
            if pc==0xdc4:
                assert r[0]==11;self.analog=r[1];self.edges.append(bool(r[1]&128));return True
            if pc==0xfe0:
                self.delays.append(r[0]);return True
            if pc in (0xf56c,0xf594):
                assert r[0]==module.PACKET;self.vendor_calls.append(pc);return True
            if pc==0xd024:self.slider_calls+=1;return True
            if pc==0xd538:
                self.saves.append(self.rd8(0x843915));return True
            if pc in (0xaaa8,0x1007c,0x10144):return True
            if pc in (0xfe98,0xd056,0xf550,0xdac4):
                raise AssertionError(f'save/erase/reboot/power-off called {pc:#x}')
            return False
        def poll(self,ms,buttons,usb=2,power=1):
            self.seed(proto.TIMER,(ms*16000)&0xffffffff)
            self.seed(proto.RAW,buttons)
            self.seed(proto.USB,usb,1);self.seed(0x800518,power,1)
            self.call(symbols['main'])
        def packet(self,ms,packet,route='vendor_b'):
            self.seed(proto.TIMER,(ms*16000)&0xffffffff)
            for i,v in enumerate(packet):self.seed(module.PACKET+i,v,1)
            self.call(symbols[route],module.PACKET)
        def descriptor(self):
            d=self.call(0xe860,module.PACKET)
            return self.rd(d+8,4)
    # Simulate a moving-stick packet keeping D-input's shared IN endpoint busy.
    for busy in (0,1,8,16,100):
        c=CPU();c.seed(proto.MODE,1,1);c.reply_busy=busy
        result=c.call(symbols['read_reply'],module.PACKET,64)
        assert result==int(busy<=16)
        assert len(c.reply_attempts)==min(busy+1,17)
        assert c.reply_attempts==[(module.PACKET,64)]*len(c.reply_attempts)
        assert c.delays==[1000]*(len(c.reply_attempts)-1)
    for mode,usb in ((0,2),(1,0)):
        c=CPU();c.seed(proto.MODE,mode,1);c.seed(proto.USB,usb,1);c.reply_busy=100
        assert c.call(symbols['read_reply'],module.PACKET,64)==0
        assert len(c.reply_attempts)==1 and not c.delays
    print('ok: busy D-input read replies retry for at most 16ms; XInput/disconnect do not wait')
    H,B,X=proto.HOME,proto.B,proto.X
    for mv,pct in ((4200,100),(3700,50),(3300,15),(3000,5),(2700,0)):
        low=CPU(mv,pct);low.poll(0,H|B);low.poll(3000,H|B)
        assert low.rd8(proto.MODE)==1 and low.descriptor()==(0x301d2dc8 if keys is not None else 0x301b2dc8)
        low.poll(3100,0);low.poll(3200,H|X);low.poll(6200,H|X)
        assert low.rd8(proto.MODE)==0
    for saved in (0,1,2,3):
        c=CPU();c.seed(0x843915,saved,1);c.call(symbols['startup'])
        assert c.rd8(proto.MODE)==saved&1
        assert c.rd8(0x843915)==saved and not c.saves
    for saved in (0,1,2,3):
        c=CPU();c.seed(0x843915,saved,1);c.seed(0x800518,0,1)
        c.call(symbols['startup'])
        assert c.rd8(proto.MODE)==0 and not c.delays and False not in c.edges
        assert c.rd8(0x843915)==saved and not c.saves
    print('ok: wireless startup never restores USB D-input or stalls the BLE startup for re-enumeration')
    c=CPU();c.seed(0x843915,2,1);c.poll(0,H|B);c.poll(3000,H|B)
    assert c.saves==[3] and c.rd8(0x843915)==3
    c.poll(3100,0);c.poll(3200,H|B);c.poll(6200,H|B);assert c.saves==[3]
    c.poll(6300,0);c.poll(6400,H|X);c.poll(9400,H|X);assert c.saves==[3,2]
    print('ok: saved USB restored on startup, other mode bits preserved, save only on actual change')
    print('ok: both USB directions at 4200/3700/3300/3000/2700 mV and 100/50/15/5/0 percent')
    cpu=CPU(); cpu.poll(0,H|B); cpu.poll(2999,H|B)
    assert cpu.rd8(proto.MODE)==0 and not cpu.edges
    cpu.poll(3000,H|B)
    assert cpu.rd8(proto.MODE)==1 and cpu.edges==[False,True] and cpu.delays==[600000 if keys else 200000]
    assert cpu.descriptor()==(0x301d2dc8 if keys is not None else 0x301b2dc8)
    cpu.poll(9000,H|B);cpu.poll(12000,H|X)
    assert len(cpu.edges)==2  # cannot change target without releasing Home
    cpu.poll(12100,0);cpu.poll(12200,H|X);cpu.poll(15200,H|X)
    assert cpu.rd8(proto.MODE)==0 and cpu.descriptor()==(0x028e045e if keys and 'cable_360' in keys else 0x310a2dc8)
    assert cpu.edges==[False,True,False,True] and cpu.delays==[600000 if keys else 200000]*2
    assert cpu.rd8(0x843915)==0  # saved USB selection unchanged
    cpu.poll(15300,0);cpu.poll(15400,H|X);cpu.poll(18400,H|X)
    assert len(cpu.edges)==4  # already XInput: stock setter avoids re-enumeration
    assert cpu.slider_calls==11
    print('ok: 3-second hold, both real mode setters, real 200ms re-enumeration, descriptors, one-shot, release, save-on-change')
    for buttons in (B,X,H,H|B|X,0):
        c=CPU();c.poll(0,buttons);c.poll(10000,buttons)
        assert not c.edges
    for usb,power in ((0,0),(0,1),(1,1),(3,1),(2,0)):
        c=CPU();c.poll(0,H|B,usb,power);c.poll(10000,H|B,usb,power)
        assert not c.edges
    c=CPU();c.poll(0,H|B);c.poll(2000,0);c.poll(2100,H|B);c.poll(5000,H|B)
    assert not c.edges;c.poll(5100,H|B);assert c.rd8(proto.MODE)==1
    print('ok: invalid chords, USB/power gates, interrupted hold')
    for route in ('vendor_a','vendor_b'):
        c=CPU();c.poll(0,H|B)
        c.packet(2500,bytes.fromhex('81 05 05 00 00'),route)
        c.poll(3000,H|B);c.poll(4499,H|B);assert not c.edges
        c.poll(4500,H|B);c.poll(7499,H|B);assert not c.edges
        c.poll(7500,H|B);assert c.rd8(proto.MODE)==1
        assert c.vendor_calls==[0xf56c if route=='vendor_a' else 0xf594]
    c=CPU();c.poll(0,H|B);c.poll(3000,H|B)
    c.packet(3500,bytes.fromhex('81 05 05 00 00'))
    c.poll(4000,H|X);c.poll(6000,H|X);c.poll(10000,H|X)
    assert c.rd8(proto.MODE)==1 and len(c.edges)==2
    c.poll(10100,0);c.poll(10200,H|X);c.poll(13200,H|X)
    assert c.rd8(proto.MODE)==0
    print('ok: both vendor routes retained, backup traffic resets hold, 2-second quiet interval')
    if keys is not None:
        for route, stock in (("vendor_a",0xf56c),("vendor_b",0xf594)):
            c=CPU(battery_mv=3821,battery_percent=63)
            c.packet(0,bytes.fromhex("81 05 00 5b"),route)
            assert c.battery_replies == [bytes((0x5b,63,0xed,0x0e,1,1))],(route,c.battery_replies)
            assert c.vendor_calls == []
            c.packet(100,bytes.fromhex("81 05 00 51 00"),route)
            assert c.vendor_calls == [stock],(route,c.vendor_calls)
        print('ok: cable battery reply on both vendor routes; update command remains on stock handler')
    for packet in ('81 05 c1 00 00','81 05 02 00 00','81 05 03 00 00','81 05 04 00 00',
                   '81 05 0b 00 00','81 05 c4 00 00','81 05 00 51 00','81 05 00 55 00','81 00 66 aa 64'):
        c=CPU();c.packet(0,bytes.fromhex(packet));c.poll(10000,H|B);c.poll(20000,H|B)
        assert not c.edges and c.rd(proto.STATE+16,4)&1==1
    c=CPU();c.packet(0,bytes.fromhex('81 05 00 21 01'));assert c.rd(proto.STATE+16,4)&1==0
    c.poll(2000,H|B);c.poll(5000,H|B);assert c.rd8(proto.MODE)==1
    for route in ('vendor_a','vendor_b'):
        c=CPU();c.packet(0,bytes.fromhex('81 11 04 08 00 c2 01 50 00'),route)
        assert c.rd(proto.STATE+16,4)&1==0
        c.poll(2000,H|B);c.poll(5000,H|B);assert c.rd8(proto.MODE)==1
    c=CPU();c.packet(0,bytes.fromhex('81 11 04 09'));assert c.rd(proto.STATE+16,4)&1==1
    print('ok: unknown/write/update commands lock until reset; read-session open remains allowed')
    c=CPU();c.poll(267000,H|B);c.poll(270000,H|B);assert c.rd8(proto.MODE)==1
    c=CPU();c.poll(0,H|B);c.poll(2000,H|B,0,0);c.poll(3000,H|B);c.poll(5999,H|B)
    assert not c.edges;c.poll(6000,H|B);assert c.rd8(proto.MODE)==1
    print('ok: 32-bit clock wrap and unplug/replug reset the hold')
    return {'ok':True,'offline_only':True,'bytes':len(blob),'physical_tested':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backup',required=True,type=Path)
    p.add_argument('--report',type=Path,help='Save non-sensitive test results as JSON')
    args=p.parse_args();data=args.backup.read_bytes()
    stock=next((data[a:a+image.BANK_A_SIZE] for a in (image.BASE,image.BANK_B)
                if hashlib.sha256(data[a:a+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error('No verified stock 1.09 bank')
    import json
    result=check(stock)
    variants=[['ring_normal','usb_dinput'],['ring_normal','settings','cpu_rest','battery_log','usb_dinput'],
              ['ring_25','settings','fixes','cpu_rest','battery_log','bt_xbox','bt_rumble','bt_rumble_long','cable_360','usb_dinput']]
    for keys in variants:
        print('INTEGRATED', ','.join(keys))
        result=check(stock, keys)
    result['integrated_variants']=len(variants)
    if args.report: args.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
