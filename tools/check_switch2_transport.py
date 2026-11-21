# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute stock TC32 notification, FIFO initialization and fragmentation checks for the offline Y prototype. No device I/O."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from u2c_customfw.firmware import image,recipes
from tools.check_bt_modes import CPU,mod
from tools.switch2_prototype import input_report,MIN_ATT_MTU

class NotifyCPU(CPU):
    def __init__(self,frame):
        self.sent=[];super().__init__(frame)
    def trap(self,pc,r):
        if pc==0x1458c:return False  # execute the real library routine
        if pc==0x1484c:
            ptr=self.rd(r[13],4);size=self.rd(r[13]+4,4)
            self.sent.append(bytes(self.rd8(ptr+i) for i in range(size)))
            r[0]=0;return True  # boundary: lower L2CAP/radio queue is not modeled
        return super().trap(pc,r)

class FragmentCPU(NotifyCPU):
    """Execute notification + L2CAP fragmentation; stop at the radio enqueue callback."""
    def __init__(self,frame,slots=8,occupied=0):
        self.fragments=[];super().__init__(frame)
        self.seed(0x843a32,8,1)       # original link-state getter: connected
        self.seed(0x843ab6,27,1)      # original maximum LL TX payload getter
        self.seed(0x843a22,slots,1)   # TX FIFO entry count
        self.seed(0x843a23,occupied,1)
        self.seed(0x8435ac,0x122fd)   # original radio enqueue callback
    def rd8(self,a):
        # Stock copies flash 0x6000..0x8500 into low executable RAM at boot.
        if 0 <= a < 0x2500:return self.img[a]
        return super().rd8(a)
    def wr8(self,a,v):
        if a==0x800643:self.ram[a]=v&255;return
        super().wr8(a,v)
    def trap(self,pc,r):
        if pc in (0x1458c,0x1484c,0x1abc):return False
        if pc==0x8504:
            # TC32 compiler's signed-division helper; arithmetic boundary only.
            a,b=(v if v<0x80000000 else v-(1<<32) for v in r[:2])
            assert b
            q=abs(a)//abs(b)
            r[0]=(-q if (a<0)!=(b<0) else q)&0xffffffff
            return True
        if pc==0x14a54:
            assert r[0]==0x40 and r[3]==0x122fd
            size=self.rd8(r[1]+1)
            assert size<=27
            self.fragments.append(bytes(self.rd8(r[1]+i) for i in range(size+2)))
            r[0]=0;return True
        return super().trap(pc,r)

def check_fragmentation(frame):
    packet=input_report(buttons=("A","GL"),counter=71)
    cases=[]
    for slots,occupied,n,accepted in ((4,0,16,True),(4,0,63,False),(8,0,63,True),(8,2,63,True),(8,3,63,False)):
        c=FragmentCPU(frame,slots,occupied);c.seed(0x842f22,MIN_ATT_MTU,2)
        for i,v in enumerate(packet):c.seed(mod.PACKET+i,v,1)
        result=c.call(0x1458c,0x40,0x000e,mod.PACKET,n)
        assert bool(c.fragments)==accepted,(slots,occupied,n,result,c.fragments)
        if accepted:
            assert c.fragments[0][0]==2 and all(f[0]==1 for f in c.fragments[1:])
            assembled=b"".join(f[2:] for f in c.fragments)
            assert int.from_bytes(assembled[:2],"little")==n+3
            assert assembled[2:7]==bytes.fromhex("04 00 1b 0e 00")
            assert assembled[7:]==packet[:n]
        else:assert result==0x81
        cases.append({"slots":slots,"occupied":occupied,"payload":n,"accepted":accepted,
                      "fragment_lengths":[len(f)-2 for f in c.fragments],"return":result})
    return {"cases":cases,"stock_tx_slots":8,"callback_executed":False}

class InitCPU(FragmentCPU):
    """Execute the stock initializers; emulate only RAM and register storage."""
    def wr8(self,a,v):
        if 0x843a00<=a<0x843b28 or 0x8435ac<=a<0x8435b0 or 0x800c00<=a<0x800c40:
            self.ram[a]=v&255;return
        super().wr8(a,v)

def check_initialization(frame):
    c=InitCPU(frame)
    assert c.call(0x11e64,0x843178,40,8)==0
    assert c.call(0x11ea4,0x8432b8,64,4)==0
    # Names follow the TX callback's actual reads, not the order of SDK calls.
    assert c.rd(0x843a20,2)==40 and c.rd8(0x843a22)==8
    assert c.rd(0x843a28,4)==0x843178
    assert c.rd(0x843a6c,2)==64 and c.rd8(0x843a6e)==4
    assert c.rd(0x843a74,4)==0x8432b8
    assert c.rd(0x800c08,2)==0x32b8 and c.rd8(0x800c0a)==4
    assert c.rd8(0x800c0b)==1
    c.call(0x121a0)
    assert c.rd(0x8435ac,4)==0x122fd
    return {"tx_slots":8,"tx_entry":40,"tx_base":"0x843178",
            "rx_slots":4,"rx_entry":64,"rx_base":"0x8432b8",
            "callback":"0x122fd","hardware_registers_emulated":True}

class QueueCPU(InitCPU):
    """Execute the real enqueue callback with encryption disabled; no radio DMA."""
    def wr8(self,a,v):
        if 0x843178<=a<0x8432b8:
            self.ram[a]=v&255;return
        super().wr8(a,v)
    def trap(self,pc,r):
        if pc==0x14a54:return False
        return super().trap(pc,r)

def check_queue(frame):
    c=QueueCPU(frame)
    c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
    c.seed(0x842f22,MIN_ATT_MTU,2)
    packet=input_report(buttons=("B","GR"),counter=123)
    for i,v in enumerate(packet):c.seed(mod.PACKET+i,v,1)
    # Exercise wrap-around in the actual software FIFO, modeling only consumption.
    for index in range(16):
        c.seed(0x843a23,index,1);c.seed(0x843a24,index,1)
        assert c.call(0x1458c,0x40,0xe,mod.PACKET,len(packet))==0
        assert c.rd8(0x843a23)==index+3
        chunks=[]
        for offset in range(3):
            base=0x843178+((index+offset)&7)*40
            size=c.rd8(base+5)
            assert c.rd(base,4)==size+2
            chunks.append(bytes(c.rd8(base+6+j) for j in range(size)))
        assert b"".join(chunks)==bytes.fromhex("42 00 04 00 1b 0e 00")+packet
    return {"callback_executed":True,"encryption_enabled":False,
            "reports_checked":16,"fifo_wrap_checked":True,"radio_dma_executed":False}

class ConnectionCPU(FragmentCPU):
    def __init__(self,frame):
        self.accepted=False
        super().__init__(frame)
    def trap(self,pc,r):
        if pc==0x12cf8:
            self.accepted=True
            return True  # boundary: actual link setup/timers are not executed
        return super().trap(pc,r)

def check_connection_interval(frame):
    from tools.build_usb_mode_prototype import Code
    original=ConnectionCPU(frame)
    assert original.thumb(0x11938)==0x1f8a  # subs r2,r1,#6
    assert original.rd(0x11a00,4)==3194      # upper bound: 3200 minus 6
    code=Code(0x11938);code.op(0x1f0a)      # subs r2,r1,#4
    candidate=bytearray(frame)
    candidate[0x11938-image.BASE:0x1193a-image.BASE]=code.data
    candidate[0x11a00-image.BASE:0x11a04-image.BASE]=(3196).to_bytes(4,"little")
    cases=[]
    for patched,data in ((False,frame),(True,candidate)):
        for interval in (0,3,4,5,6,3200,3201,65535):
            c=ConnectionCPU(data)
            packet=bytearray(40)
            packet[0x19]=1                 # transmit window size
            packet[0x1c:0x1e]=interval.to_bytes(2,"little")
            packet[0x20:0x22]=(200).to_bytes(2,"little")
            packet[0x22:0x27]=bytes.fromhex("ff ff ff ff 1f")
            packet[0x27]=5                 # hop increment
            for index,value in enumerate(packet):c.seed(mod.PACKET+index,value,1)
            c.call(0x11928,mod.PACKET,0)
            expected=(4 if patched else 6)<=interval<=3200
            assert c.accepted==expected,(patched,interval,c.accepted)
            cases.append({"candidate":patched,"interval_units":interval,"accepted":c.accepted})
    return {"cases":cases,"candidate_saved":False,"production_changed":False,
            "link_setup_executed":False,"scope":"initial CONNECT_IND validation only",
            "lower_check":"0x11938","upper_range_literal":"0x11a00"}

def check_gatt_transport(frame):
    from tools.switch2_gatt import Pro2Gatt
    gatt=Pro2Gatt(bytes.fromhex("98e255c21688"))
    assert gatt.receive(bytes.fromhex("024200")).response==bytes.fromhex("034200")
    for ccc in (0xf,0x1f):
        assert gatt.receive(b"\x12"+ccc.to_bytes(2,"little")+b"\x01\x00").response==b"\x13"
    requests=("15910101000e0000000281eb3aebf14880eb3aebf148",
              "1591010400110000003503e92982877124bea80c664615834b",
              "1591010200110000006fc6df8ad8fedf15bb8c15e91f320544",
              "159101030001000000")
    notifications=[]
    for request in requests:
        result=gatt.receive(bytes.fromhex("521400"+request))
        assert result.rejected is None and len(result.notifications)==1
        notifications.extend(result.notifications)
    notifications.append(gatt.input_notification(buttons=("A","GL")))
    counts=[]
    for notification in notifications:
        c=QueueCPU(frame);c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
        c.seed(0x842f22,gatt.mtu,2)
        payload=notification[3:]
        for index,value in enumerate(payload):c.seed(mod.PACKET+index,value,1)
        assert c.call(0x1458c,0x40,int.from_bytes(notification[1:3],"little"),mod.PACKET,len(payload))==0
        count=c.rd8(0x843a23);assembled=bytearray()
        for slot in range(count):
            base=0x843178+slot*40;size=c.rd8(base+5)
            assembled.extend(c.rd8(base+6+j) for j in range(size))
        expected=(len(notification)).to_bytes(2,"little")+b"\x04\x00"+notification
        assert assembled==expected
        counts.append(count)
    return {"pairing_notifications":4,"input_notifications":1,"queued_fragments":counts,
            "gatt_is_offline_model":True,"actual_tc32_queue_executed":True}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--backup",type=Path,required=True);a=p.parse_args()
    data=a.backup.read_bytes()
    stock=next((data[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(data[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error("No verified original 1.09 bank")
    from tools.check_switch2_crypto import check_encryption,check_session_key
    from tools.check_switch2_link import check_link_setup
    frame=recipes.whole(stock);packet=input_report()
    cases=[]
    for mtu,n,accepted in ((23,16,True),(23,20,True),(23,21,False),(23,63,False),(MIN_ATT_MTU,63,True)):
        c=NotifyCPU(frame);c.seed(0x842f22,mtu,2)
        for i,v in enumerate(packet):c.seed(mod.PACKET+i,v,1)
        result=c.call(0x1458c,0x40,0x000e,mod.PACKET,n)
        assert bool(c.sent)==accepted,(mtu,n,result,c.sent)
        if accepted:assert c.sent==[packet[:n]]
        cases.append({"mtu":mtu,"payload":n,"library_accepted":accepted,"return":result})
    print(json.dumps({"ok":True,"physical_tested":False,"radio_tested":False,"cases":cases,"initialization":check_initialization(frame),"fragmentation":check_fragmentation(frame),"queue":check_queue(frame),"connection_interval":check_connection_interval(frame),"gatt_transport":check_gatt_transport(frame),"encryption":check_encryption(frame),"link_setup":check_link_setup(frame),"session_key":check_session_key(frame)},indent=2))
if __name__ == "__main__":main()
