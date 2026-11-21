# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute built Bluetooth mode wrappers/packers on verified stock. No device access."""
import argparse,hashlib,importlib.util,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from u2c_customfw.firmware import image,recipes
from tools.build_ring_hold import bl_target
STATE=0x8416d2;RAW=0x84344c;SAVED=0x843915;WORD=0x84342c
spec=importlib.util.spec_from_file_location('tc32_bt',ROOT/'firmware-src/test_longrumble.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
def reports(data):
    offset=0;size=count=0;report=0;bits={}
    while offset<len(data):
        tag=data[offset];offset+=1;length=(0,1,2,4)[tag&3]
        value=int.from_bytes(data[offset:offset+length],'little');offset+=length
        if tag&0xfc==0x74:size=value
        elif tag&0xfc==0x94:count=value
        elif tag&0xfc==0x84:report=value
        elif tag&0xfc in (0x80,0x90):bits[(report,'input' if tag&0xfc==0x80 else 'output')]=bits.get((report,'input' if tag&0xfc==0x80 else 'output'),0)+size*count
    return bits
class CPU(mod.Controller):
    def __init__(self,frame):
        self.delays=[];self.watchdogs=0;self.rf_starts=0;self.outputs=[];self.home_calls=0;self.reboots=[];self.unlocks=0;self.saves=[];self.notifications=[];self.responses=[];self.callbacks=[];self.results=[];self.abi=[]
        super().__init__(bytes(frame))
    def seed(self,a,v,n=4):
        for i in range(n):self.ram[a+i]=(v>>(8*i))&255
    def wr8(self,a,v):
        if 0x842e70<=a<0x842e74 or 0x842f98<=a<0x842f9c or 0x843a00<=a<0x843b00:
            self.ram[a]=v&255;return
        assert STATE<=a<STATE+2 or SAVED<=a<SAVED+4 or 0x843648<=a<0x84364c or mod.SP0-0x800<=a<mod.SP0 or mod.CONNECTED<=a<mod.CONNECTED+4 or mod.PACKET<=a<mod.PACKET+64,hex(a)
        self.ram[a]=v&255
    def trap(self,pc,r):
        if pc in (0xb908,0x13628,0x11690):self.abi.append((pc,r[13],r[14],r[0],r[1]))
        if pc==0xfe0:self.delays.append(r[0]);return True
        if pc==0xced8:self.watchdogs+=1;return True
        if pc==0xdb14:self.rf_starts+=1;return True
        if pc==0x18390:self.outputs.append(bytes(self.rd8(r[0]+i) for i in range(17)));r[0]=0;return True
        if pc==0xd0b0:self.home_calls+=1;return True
        if pc==0xd056:self.reboots.append((self.rd(RAW,4),self.rd8(SAVED)));return True
        if pc==0xbd00:self.unlocks+=1;return True
        if pc==0xa4ec:self.callbacks.append(tuple(r[:4]));return True
        if pc==0xd538:self.saves.append(self.rd8(SAVED));return True
        if pc==0x11690:
            self.responses.append(bytes(self.rd8(r[0]+i) for i in range(r[1])));return True
        if pc==0x1458c:
            assert r[0:2]==[0x40,0x1d];self.notifications.append(bytes(self.rd8(r[2]+i) for i in range(r[3])));r[0]=123;return True
        if pc==0x1abc:self.results.append(r[0]);return True
        if pc in (0x1040c,0x2478):
            for i in range(r[2]):self.wr8(r[0]+i,self.rd8(r[1]+i))
            return True
        return False

class BondCPU(CPU):
    """Real SDK journal logic; model only SPI flash and cold-boot protection."""
    def __init__(self,frame,flash=None):
        self.flash={} if flash is None else flash
        self.locked=True;self.flash_writes=[]
        super().__init__(frame)
        self.seed(0x842e3c,0x20000);self.seed(0x842e50,0x20010)
        self.seed(0x842f61,4,1)
    def rd8(self,a):
        if 0x3b000<=a<0x3d000 or 0x7c000<=a<0x7e000:return self.flash.get(a,255)
        return super().rd8(a)
    def wr8(self,a,v):
        if 0x842e00<=a<0x844000:self.ram[a]=v&255
        else:super().wr8(a,v)
    def trap(self,pc,r):
        if pc==0xbd00:self.locked=False
        if pc==0x20000:
            addr,n,source=r[:3]
            assert (0x3b000<=addr and addr+n<=0x3d000) or (0x7c000<=addr and addr+n<=0x7e000)
            if not self.locked or addr>=0x70000:
                self.flash_writes.append((addr,n))
                for i in range(n):self.flash[addr+i]=self.rd8(addr+i)&self.rd8(source+i)
            return True
        if pc==0x20010:
            addr,n,dest=r[:3]
            for i in range(n):self.wr8(dest+i,self.rd8(addr+i))
            return True
        if pc==0xfe98:
            addr=r[0]&~4095
            assert addr in (0x3b000,0x3c000,0x7c000,0x7d000)
            if not self.locked or addr>=0x70000:
                for i in range(4096):self.flash[addr+i]=255
            return True
        if pc==0x10394:
            r[0]=int(any(self.rd8(r[0]+i)!=self.rd8(r[1]+i) for i in range(r[2])))
            return True
        if pc==0x103f8:
            for i in range(r[2]):self.wr8(r[0]+i,r[1])
            return True
        return super().trap(pc,r)

class MatrixCPU(CPU):
    # Logical GPIO-matrix row reads, not a radio or electrical model.
    def __init__(self,frame,buttons):
        self.matrix_buttons=buttons;self.row=0;super().__init__(frame)
        self.seed(0x800500,0x10,1)
    def wr8(self,a,v):
        if 0x843300<=a<0x843500:self.ram[a]=v&255
        else:super().wr8(a,v)
    def trap(self,pc,r):
        if pc in (0xc324,0xc288,0xfe0,0x1210):return True
        if pc==0xc33c:
            self.row+=1
            r[0]=2 if (self.row==2 and 'X' in self.matrix_buttons) or (self.row==3 and 'B' in self.matrix_buttons) else 0
            return True
        return super().trap(pc,r)

def check(stock,keys):
    assert recipes.identify(recipes.whole(stock)).known
    built=recipes.build(stock,keys);identified=recipes.identify(built)
    assert built[0x76d4-image.BASE:0x7738-image.BASE]==stock[0x76d4-image.BASE:0x7738-image.BASE]  # radio code remains intact
    # The mode flag occupies only the unused alignment byte following bx r3 at 0x16d0.
    tc=int.from_bytes(stock[0x76d0-image.BASE:0x76d2-image.BASE],'little')
    assert mod.TC32_TO_THUMB[tc>>12]<<12 | tc&4095 == 0x4718
    assert identified.known and set(identified.recipes)==set(keys)
    target=lambda at:bl_target(built[at-image.BASE:at-image.BASE+4],at)
    start,table,scan,pack=(target(a) for a in (0xa4b8,0xa318,0xa216,0xa2e4))
    source=bytes(built[0x1743c-image.BASE:0x17904-image.BASE]);dual=bytes(built[0x19000-image.BASE:0x194c8-image.BASE])
    edit=next(e for e in recipes.RECIPES['bt_modes'].edits if e.copy_from is not None)
    changed=set()
    for at,value in edit.copy_overrides:changed.update(range(at,at+len(value)))
    assert all(a==b or i in changed for i,(a,b) in enumerate(zip(source,dual)))
    u32=lambda at:int.from_bytes(dual[at:at+4],'little')
    assert u32(0x17598-0x1743c)==0x194c8
    assert u32(0x177a8-0x1743c)==0x19f00
    dmap=(recipes.BLOBS/'btdmap.bin').read_bytes()
    assert u32(0x177a0-0x1743c)==len(dmap) and reports(dmap)=={(1,'input'):80,(2,'output'):64}
    for saved in range(4):
        for buttons in (0,1<<12,1<<4,(1<<12)|(1<<4)):
            c=CPU(built);c.seed(RAW,buttons);c.seed(SAVED,saved,1)
            c.call(start,1,2,3,4)
            expected=(saved|2) if buttons==1<<12 else (saved&~2) if buttons==1<<4 else saved
            assert c.rd8(SAVED)==expected and c.rd8(STATE)==bool(expected&2)
            assert c.saves==([expected] if expected!=saved else [])
            assert c.callbacks==[(1,2,3,4)]
            c.call(table,0x1743c);assert c.abi[-1][1:3]==(mod.SP0,mod.RET);assert c.rd(0x843648,4)==(0x19000 if expected&2 else 0x1743c)
            c.call(scan,0x17254,len(recipes.NAME)+2)
            expected_scan=bytes((23,9))+b'8BitDo Ultimate 2C (D)' if expected&2 else bytes(built[0x17254-image.BASE:0x17254-image.BASE+len(recipes.NAME)+2])
            assert c.responses[-1]==expected_scan,c.responses
            assert c.abi[-1][1:3]==(mod.SP0,mod.RET)
    for buttons in (set(),{'B'},{'X'},{'B','X'}):
        c=MatrixCPU(built,buttons);c.seed(SAVED,0,1);c.call(0xc7a4)
        raw=c.rd(RAW,4)
        assert bool(raw&(1<<12))==('B' in buttons) and bool(raw&(1<<4))==('X' in buttons)
        c.call(start,1,2,3,4);assert bool(c.rd8(STATE))==(buttons=={'B'})
    factory_address=bytes.fromhex('12 34 56 78 9a bc')
    identities=[];stores=[]
    for d_mode in (0,1,0,1):
        c=CPU(built);c.seed(STATE,d_mode,1)
        for i,v in enumerate(factory_address):c.seed(mod.PACKET+i,v,1)
        c.call(target(0xa138),mod.PACKET)
        observed=bytes(c.rd8(0x843a78+i) for i in range(6))
        expected=bytearray(factory_address)
        if not d_mode:expected[5]^=2
        assert observed==bytes(expected)
        assert bytes(c.rd8(mod.PACKET+i) for i in range(6))==factory_address
        c.call(target(0xa184),0x7c000)
        assert c.rd(0x842f98,4)==(0x7c000 if d_mode else 0x3b000)
        assert c.unlocks==(0 if d_mode else 1)
        identities.append(observed);stores.append(c.rd(0x842f98,4))
    assert identities[0]!=identities[1] and identities[0]==identities[2] and identities[1]==identities[3]
    assert stores==[0x3b000,0x7c000,0x3b000,0x7c000]
    # Execute the wireless gesture wrapper and real transport selector.
    home=target(0xa478)
    for bluetooth in (False,True):
        for saved in range(4):
            for button in (1<<12,1<<4):
                c=CPU(built);c.seed(SAVED,saved,1);c.seed(STATE+1,255,1)
                c.seed(WORD,(1<<28) if bluetooth else 0)
                # A startup chord must first be released, preventing reboot loops.
                c.seed(RAW,(1<<17)|button)
                for _ in range(40):c.call(home)
                assert not c.reboots and not c.home_calls
                c.seed(RAW,0);c.call(home)
                c.seed(RAW,(1<<17)|button);c.call(home)
                for _ in range(29):c.call(home)
                assert not c.reboots
                c.call(home)
                expected=((saved|2) if button==1<<12 else (saved&~2)) if bluetooth else saved
                assert c.reboots==[((1<<17)|button,expected)]
                assert c.saves==([expected] if expected!=saved or (not bluetooth and button==1<<12) else [])
                if not bluetooth:assert c.rd8(SAVED+2)==int(button==1<<12) and c.rd8(SAVED+3)==0
                for _ in range(35):c.call(home)
                assert len(c.reboots)==1 and c.home_calls==1
    for buttons,power in ((1<<17,0),((1<<17)|(1<<4)|(1<<12),0),((1<<17)|(1<<12)|(1<<25)|(1<<26),0),((1<<17)|(1<<12),1)):
        c=CPU(built);c.seed(RAW,buttons);c.seed(0x800518,power,1)
        for _ in range(40):c.call(home)
        assert c.home_calls==40 and not c.reboots
    # Regression: a protected app-gap journal cannot retain a pairing after reboot.
    blocked=BondCPU(built)
    for i in range(64):blocked.seed(mod.PACKET+i,0x5a if i==0 else i,1)
    assert blocked.call(0x15c44,0x3b000,64,mod.PACKET)==1
    assert not blocked.flash_writes
    journals={}
    for d_mode in (0,1,0,1):
        c=BondCPU(built,journals);c.seed(STATE,d_mode,1)
        c.call(target(0xa184),0x7c000);base=c.rd(0x842f98,4)
        c.call(0x15d98)
        exists=c.rd8(0x842f63)
        if exists:
            assert exists==1
            assert c.rd8(base)==0x5a and c.rd8(base+1)==(21 if d_mode else 42)
        else:
            for i in range(64):c.seed(mod.PACKET+i,0x5a if i==0 else (21 if d_mode else 42),1)
            assert c.call(0x15c44,base,64,mod.PACKET)==0
    assert journals[0x3b001]==42 and journals[0x7c001]==21
    # Recognize the previously shipped selector with original MAC/bond hooks.
    previous=bytearray(built)
    for at,name in ((0xa318,'bttable01.bin'),(0xa216,'btscan01.bin'),(0xa2e4,'btpack01.bin'),(0x19540,'btmodes01.bin')):
        value=(recipes.BLOBS/name).read_bytes()
        if at==0x19540:value=value.ljust(len((recipes.BLOBS/'btmodes.bin').read_bytes()),b'\xff')
        previous[at-image.BASE:at-image.BASE+len(value)]=value
    for at in (0xa138,0xa184,0xa478,0xa410):previous[at-image.BASE:at-image.BASE+4]=stock[at-image.BASE:at-image.BASE+4]
    previous[0x76d3-image.BASE]=0x46
    previous[0xa410-image.BASE:0xa414-image.BASE]=stock[0xa410-image.BASE:0xa414-image.BASE]
    previous[0x19f00-image.BASE:0x19f78-image.BASE]=(recipes.BLOBS/'btdmap01.bin').read_bytes()
    assert recipes.identify(bytes(previous)).known
    previous=bytearray(built)
    for at,name in ((0xa318,'bttable02.bin'),(0xa216,'btscan02.bin'),(0xa2e4,'btpack02.bin'),(0xa184,'btbonds02.bin'),(0x19540,'btmodes02.bin')):
        value=(recipes.BLOBS/name).read_bytes()
        if at==0x19540:value=value.ljust(len((recipes.BLOBS/'btmodes.bin').read_bytes()),b'\xff')
        previous[at-image.BASE:at-image.BASE+len(value)]=value
    previous[0xa478-image.BASE:0xa47c-image.BASE]=stock[0xa478-image.BASE:0xa47c-image.BASE]
    previous[0x76d3-image.BASE]=0x46
    previous[0xa410-image.BASE:0xa414-image.BASE]=stock[0xa410-image.BASE:0xa414-image.BASE]
    previous[0x19f00-image.BASE:0x19f78-image.BASE]=(recipes.BLOBS/'btdmap01.bin').read_bytes()
    assert recipes.identify(bytes(previous)).known
    previous=bytearray(built)
    for at,name in ((0xa318,'bttable03.bin'),(0xa216,'btscan03.bin'),(0xa2e4,'btpack03.bin'),(0xa184,'btbonds03.bin'),(0xa138,'btmac03.bin'),(0xa478,'bthome03.bin'),(0x19540,'btmodes03.bin')):
        value=(recipes.BLOBS/name).read_bytes()
        if at==0x19540:value=value.ljust(len((recipes.BLOBS/'btmodes.bin').read_bytes()),b'\xff')
        previous[at-image.BASE:at-image.BASE+len(value)]=value
    previous[0xa410-image.BASE:0xa414-image.BASE]=stock[0xa410-image.BASE:0xa414-image.BASE]
    previous[0x19f00-image.BASE:0x19f78-image.BASE]=(recipes.BLOBS/'btdmap01.bin').read_bytes()
    assert recipes.identify(bytes(previous)).known
    c=CPU(built);c.call(target(0xa410))
    assert c.delays==[100000]*10 and c.watchdogs==0 and c.rf_starts==1
    c=CPU(built)
    for i in range(17):c.seed(mod.PACKET+i,i,1)
    c.call(int.from_bytes((recipes.BLOBS/'btdnoop.bin').read_bytes(),'little'),mod.PACKET)
    assert c.outputs==[bytes(range(17))]
    # Compare the authored D-input encoder against the original encoder, including paddles and all hats.
    diagonals=[(1<<a)|(1<<b) for a,b in ((9,6),(8,6),(8,7),(9,7))]
    vectors=[0,*diagonals,*[1<<i for i in (0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,17,25,26)],0xffffffff]
    for word in vectors:
        c=CPU(built);factory=CPU(recipes.whole(stock))
        for cpu in (c,factory):
            cpu.seed(WORD,word);cpu.seed(0x843415,73,1)
            for a,value in ((0x8433ec,0),(0x8433f6,0x7fff),(0x8433e4,0xffff),(0x8433e6,0x1234),(0x8433e8,64),(0x84340e,191)):cpu.seed(a,value,2)
        c.seed(STATE,1,1);c.call(pack);factory.call(0xb908)
        assert c.notifications[-1][:9]==factory.notifications[-1][:9],(hex(word),c.notifications,factory.notifications)
        assert len(c.notifications[-1])==10 and c.notifications[-1][9]==73 and c.results==[123]
        c.seed(STATE,0,1);c.call(pack);assert c.abi[-1][0:3]==(0xb908,mod.SP0,mod.RET);assert len(c.notifications[-1])==16 and c.results[-1]==123
    # Derived copies are checked, not trusted as arbitrary data.
    corrupt=bytearray(built);corrupt[0x19000-image.BASE+10]^=1;assert not recipes.identify(bytes(corrupt)).known
    print('ok:',','.join(keys),'startup B/X/saved mode, independent USB bit, save-on-change, GATT/PnP/map/name, original D-input vectors, Xbox report, tamper rejection')
    return {'ok':True,'physical_tested':False,'flash_extension_bytes':4096,'ram_bytes':2,'repair_may_be_needed':True}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backup',type=Path,required=True);p.add_argument('--report',type=Path);a=p.parse_args();data=a.backup.read_bytes()
    stock=next(data[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B) if hashlib.sha256(data[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256)
    for keys in (['bt_xbox','bt_modes'],['bt_xbox','bt_modes','bt_rumble','bt_rumble_long','ring_25','settings','cpu_rest','battery_log','usb_dinput','fixes']):result=check(stock,keys)
    if a.report:a.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
if __name__=='__main__':main()
