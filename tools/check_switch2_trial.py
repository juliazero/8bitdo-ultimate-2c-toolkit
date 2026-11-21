# SPDX-License-Identifier: GPL-3.0-or-later
"""Check final Y firmware dispatch and install/rollback on simulated flash only."""
import argparse,hashlib,json
from pathlib import Path
from tools.build_switch2_trial import assemble,ACTIVE,END
from tools.check_switch2_profile import ProfileCPU
from tools.check_switch2_pairing import REQUEST
from tools import switch2_layout as layout
from tools.switch2_prototype import advertisement
from u2c_customfw.firmware import image,recipes
from u2c_customfw.ops import backup,restore,plan
from u2c_customfw.sim import SimBoot


class TrialCPU(ProfileCPU):
    def wr8(self,a,value):
        if 0x800400<=a<0x801000 or 0x801200<=a<0x801400:
            self.ram[a]=value&255;return  # GPIO/radio register storage, no electrical model
        super().wr8(a,value)
    def trap(self,pc,r):
        if pc==0xa4ec:return False
        if pc==0x10e34:
            self.startup_seen=True;self.radio_arguments=tuple(r[:4]);self.seed(0x84344c,0);return False
        if pc in (0xc324,0xc288,0xfe0,0x1210):return True
        if pc==0xc33c:
            self.matrix_row=getattr(self,'matrix_row',0)+1
            r[0]=2 if ((self.matrix_row==1 and self.physical_keys&(1<<5)) or
                      (self.matrix_row==2 and self.physical_keys&(1<<4)) or
                      (self.matrix_row==3 and self.physical_keys&(1<<12))) else 0
            return True  # boundary: matrix column GPIO readings
        if pc==0xb8bc:
            self.stock_pairing_called=True;return True  # ordinary D/X pairing boundary
        if pc==0xb89c:raise AssertionError('Y tried stock address renewal')
        if pc==0xa070:
            self.disconnect_event=tuple(r[:2]);r[0]=0;return True
        return super().trap(pc,r)

def check(raw,build,path):
    stock=next(raw[a:a+image.BANK_A_SIZE] for a in (image.BASE,image.BANK_B)
        if hashlib.sha256(raw[a:a+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256)
    frame,report=assemble(stock,build)
    symbols=json.loads((build/'pairing-prototype.json').read_text())['symbols']
    factory=bytes.fromhex('123456789abc')
    def cpu(held=0,d=0,forced=False,early=None,saved=None):
        code=bytearray(frame)
        if forced:
            for address in (0xa138,0xa184,0xa4b8):
                data=(recipes.BLOBS/f'switch2-force-{address:x}.bin').read_bytes()
                code[address-image.BASE:address-image.BASE+4]=data
        c=TrialCPU(code);c.physical_keys=held;c.seed(0x800500,0x10,1)
        for i,v in enumerate(frame[0x18084-image.BASE:0x18230-image.BASE]):c.seed(0x842e00+i,v,1)
        c.seed(0x842e3c,0x20000);c.seed(0x842e50,0x20010)
        c.seed(0x8416d2,d,1);c.seed(0x843915,d*2 if saved is None else saved,1);c.seed(0x84344c,held if early is None else early)
        # Execute compiled bt_start and the stock 0x10e34 radio initializer.
        # Peripheral storage and matrix GPIO columns remain modeled.
        c.startup_target=report['startup_target']
        c.call(report['dispatch']['force_start' if forced else 'startup'],11,22,33,0x10e35)
        assert c.startup_seen and c.radio_arguments==(11,22,33,0x10e35)
        c.seed(0x84344c,0)  # radio startup no longer has to preserve the chord
        for i,v in enumerate(factory):c.seed(REQUEST+i,v,1)
        c.call(0xa138,REQUEST)
        return c
    for d in (0,1):
        c=cpu(d=d)
        assert c.rd8(ACTIVE)==0
        expect=factory[:5]+bytes((factory[5]^(0 if d else 2),))
        assert bytes(c.rd8(0x843a78+i) for i in range(6))==expect
        c.call(report['dispatch']['register_connected'],3,0xa0bd)
        assert c.rd(0x843544,4)==0xa0bd
    for previous_mode in (0,1):
        stale=cpu(0,previous_mode,early=1<<5)
        assert stale.rd8(ACTIVE)==0  # released Y must not survive as a stale latch
        for key in (1<<4,1<<12):
            other=cpu(key,previous_mode)
            assert other.rd8(ACTIVE)==0
    for previous_mode in (0,1):
        selected=cpu(1<<5,previous_mode,early=0)
        assert selected.rd8(ACTIVE)==1
        assert bytes(selected.rd8(0x843a78+i) for i in range(6))==factory[:5]+bytes((factory[5]^4,))
    for previous_mode in (0,1):
        forced=cpu(0,previous_mode,True)
        assert forced.rd8(ACTIVE)==1
        assert bytes(forced.rd8(0x843a78+i) for i in range(6))==factory[:5]+bytes((factory[5]^4,))
        forced.seed(0x843a32,0,1);forced.call(0xa184)
        assert forced.rd(0x843648,4)==0x1c000 and forced.rd8(0x843a2c)==1
    for usb in (0,1):
        initial=0x80|usb|2
        picked=cpu(1<<5,saved=initial)
        remembered=picked.rd8(0x843915)
        assert remembered==initial|4 and len(picked.saves)==1
        resumed=cpu(saved=remembered)
        assert resumed.rd8(ACTIVE)==1 and resumed.saves==[]
        for key,mode in ((1<<4,0),(1<<12,2),(1<<5,4)):
            live=cpu(saved=0x80|usb)
            live.seed(0x84342c,1<<28);live.seed(0x84344c,1<<28);live.call(report['dispatch']['home'])
            live.seed(0x84344c,(1<<28)|(1<<17)|key)
            for tick in range(30):live.call(report['dispatch']['home'])
            assert not live.reboots
            live.call(report['dispatch']['home'])
            assert len(live.reboots)==1 and live.rd8(0x843915)==0x80|usb|mode
            live.call(report['dispatch']['home']);assert len(live.reboots)==1
            restarted=cpu(saved=live.rd8(0x843915))
            assert restarted.rd8(ACTIVE)==int(mode==4)
        blocked=cpu(1<<5,saved=initial)
        blocked.seed(0x84344c,(1<<28)|(1<<17)|(1<<5))
        for tick in range(40):blocked.call(report['dispatch']['home'])
        assert not blocked.reboots  # startup hold is latched until Home is released
        blocked.seed(0x800518,1,1);blocked.seed(0x8416d3,0,1)
        for tick in range(40):blocked.call(report['dispatch']['home'])
        assert not blocked.reboots  # USB cannot invoke the Bluetooth shortcut
    c=cpu(1<<5)
    assert c.rd8(ACTIVE)==1
    assert bytes(c.rd8(0x843a78+i) for i in range(6))==factory[:5]+bytes((factory[5]^4,))
    c.seed(0x843a32,0,1);c.call(0xa184)
    assert c.rd8(0x843a2c)==1 and c.rd(0x843648,4)==0x1c000
    c.call(report['dispatch']['register_connected'],3,0xa0bd)
    assert c.rd(0x843544,4)==report['dispatch']['app_connected']|1
    c.call(c.rd(0x843544,4),3,REQUEST)
    assert c.rd(0x8436bc,4)==1 and c.rd8(0x843457)==1 and c.rd8(layout.Y_PAIRING)==0
    c.call(0x11e64,0x843178,40,8);c.call(0x121a0)
    c.seed(0x843a32,8,1);c.seed(0x842f22,83,2)
    c.seed(layout.Y_INPUT_CCC,1,2);c.seed(layout.Y_ENABLED,3,1)
    for a in (0x8433ec,0x8433f6,0x8433e4,0x8433e6):c.seed(a,32768,2)
    c.seed(0x843415,100,1);c.seed(0x84342c,1<<13)
    c.call(report['dispatch']['input'])
    assert c.notifications[-1][0]==0xe and len(c.notifications[-1][1])==63
    assert c.notifications[-1][1][2]&1
    c.call(report['dispatch']['register_disconnected'],4,0xa071)
    assert c.rd(0x843548,4)==report['dispatch']['app_disconnected']|1
    c.seed(layout.Y_PEER_VALID,1,1);c.seed(layout.Y_INPUT_CCC,1,2)
    before_writes=list(c.writes);c.seed(0x843a32,0,1)
    c.call(c.rd(0x843548,4),4,REQUEST)
    assert c.rd8(layout.Y_PEER_VALID)==0 and c.rd(layout.Y_INPUT_CCC,2)==0
    assert not hasattr(c,'disconnect_event') and c.writes==before_writes
    assert c.rd(0x8436bc,4)==0 and c.rd8(0x843a2c)==1 and c.rd8(0x843457)==2
    for prior_mode in (0,1):
        ordinary=cpu(d=prior_mode)
        ordinary.call(report['dispatch']['pairing'])
        assert ordinary.stock_pairing_called
    for connected in (False,True):
        pairing=cpu(1<<5);pairing.seed(0x843a32,0,1);pairing.call(0xa184)
        pairing.call(0x11e64,0x843178,40,8);pairing.call(0x121a0)
        pairing.seed(0x8436bc,int(connected));pairing.seed(0x843a32,8 if connected else 1,1)
        pairing.seed(layout.CTX+30,1,1)
        for i in range(6):pairing.seed(layout.CTX+31+i,i+1,1)
        before=bytes(pairing.rd8(layout.CTX+30+i) for i in range(35))
        own=bytes(pairing.rd8(0x843a78+i) for i in range(6))
        assert pairing.call(report['dispatch']['pairing'])==0
        assert not hasattr(pairing,'stock_pairing_called') and not pairing.writes and not pairing.erases
        assert bytes(pairing.rd8(0x843a78+i) for i in range(6))==own
        assert bytes(pairing.rd8(layout.CTX+30+i) for i in range(35))==before
        assert bytes(pairing.rd8(0x842e84+i) for i in range(31))==advertisement()
        assert pairing.rd8(ACTIVE)==1
        assert pairing.rd8(layout.Y_PAIRING)==1 and pairing.rd8(0x843457)==3
        if connected:
            pairing.seed(0x843a32,0,1)
            pairing.call(report['dispatch']['app_disconnected'],4,REQUEST)
            assert pairing.rd8(0x843457)==3
        pairing.call(report['dispatch']['app_connected'],3,REQUEST)
        assert pairing.rd8(layout.Y_PAIRING)==0 and pairing.rd8(0x843457)==1
        if connected:
            assert pairing.rd8(0x843178+6)==2 and pairing.rd8(0x843178+7)==0x13
        else:assert pairing.rd8(0x843a2c)==1
    transitions=[]
    for mode in (2,3):
        indicator=TrialCPU(frame);indicator.seed(0x843452,0,1)
        states=[]
        for tick in range(24):
            indicator.call(0xd36c,mode,0x180,0x843452)
            states.append(indicator.rd8(0x80050b)&0x80)
        transitions.append(sum(a!=b for a,b in zip(states,states[1:])))
    assert transitions[1]>transitions[0],transitions
    # Existing restore planner and writer must install extension and remove it on rollback.
    full=bytearray(raw);full[image.BASE:END]=frame
    def b(data):return backup.Backup(path,hashlib.sha256(data).hexdigest(),recipes.identify(data[image.BASE:image.APP_END]),bytes(data))
    device=SimBoot(raw)
    install=restore.plan_restore(device,b(full),'backup',None)
    assert set(range(0x1a000,END,image.SECTOR))<=set(install.other_sectors)
    plan.apply(device,install,lambda *args:None)
    assert bytes(device.f[image.BASE:END])==frame
    assert bytes(device.f[:image.BASE])==raw[:image.BASE]
    assert bytes(device.f[image.CONFIG_START:])==raw[image.CONFIG_START:]
    undo=restore.plan_restore(device,b(raw),'backup',None)
    plan.apply(device,undo,lambda *args:None)
    assert bytes(device.f)==raw
    return {'ok':True,'y_and_dx_identity_dispatch_checked':True,'y_mode_memory_and_live_shortcuts_checked':True,'y_startup_and_input_hook_checked':True,
        'application_connected_callback_checked':True,'full_backup_install_and_rollback_checked':True,
        'y_pair_button_preserves_identity_and_bonds':True,'fresh_physical_y_scan_checked':True,'real_radio_initializer_executed':True,'forced_y_without_buttons_checked':True,'hardware_written':False,'hardware_tested':False,'console_tested':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backup',type=Path,required=True)
    p.add_argument('--build',type=Path,default=Path('build/switch2-pairing'));a=p.parse_args()
    print(json.dumps(check(a.backup.read_bytes(),a.build,a.backup),indent=2))
if __name__=='__main__':main()






