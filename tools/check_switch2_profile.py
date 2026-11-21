# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute staged Y startup against stock BLE SDK and initialized RAM. Offline only."""
import argparse, hashlib, json
from pathlib import Path
from tools.check_switch2_security import SecurityCPU
from tools.check_switch2_bonds import expanded
from tools.check_switch2_pairing import CTX, REQUEST, RESPONSE, TRANSCRIPT
from tools.switch2_prototype import advertisement
from tools.switch2_startup_overlay import prepare as startup_overlay
from tools.check_switch2_transport import mod
from u2c_customfw.firmware import image, recipes


class ProfileCPU(SecurityCPU):
    def wr8(self,a,v):
        if a in (0x800f04,0x800f1c,0x800f1d,0x800f20,0x800f21):
            self.ram[a]=v&255;return
        super().wr8(a,v)
    def trap(self, pc, r):
        if pc==getattr(self,'fail_ready',None):r[0]=0x12;return True
        if pc==0xa18c:
            self.failed_closed=True;r[14]=mod.RET;return True
        if pc==0xa13c or pc==0xa220:
            self.startup_completed=True;r[14]=mod.RET;return True
        if pc in (0x15bac,0x15b50,0x15ba0,0x15f4c):
            raise AssertionError('Y executed original SMP journal')
        if pc==0x11690:return False  # execute SDK; parent BT tests intercept scan data
        return super().trap(pc,r)

def check(frame, build):
    code, symbols = expanded(frame, build)
    table = (build/'gatt-prototype.bin').read_bytes()
    offset = 0x1c000-image.BASE
    code = bytearray(code)
    if len(code)<offset+len(table): code.extend(bytes(offset+len(table)-len(code)))
    code[offset:offset+len(table)] = table
    unpatched=bytes(code)
    code=startup_overlay(code,symbols)
    factory = bytes.fromhex('12 34 56 78 9a bc')
    own = factory[:5]+bytes((factory[5]^4,))
    def read(c, address, size):
        return bytes(c.rd8(address+i) for i in range(size))
    def boot(flash=None):
        c = ProfileCPU(code, flash)
        data = frame[0x18084-image.BASE:0x18230-image.BASE]
        assert len(data)==0x1ac
        for i,v in enumerate(data): c.seed(0x842e00+i,v,1)
        # Model only SPI boundaries; all MAC/advertising SDK functions execute.
        c.seed(0x842e3c,0x20000); c.seed(0x842e50,0x20010)
        for i,v in enumerate(factory): c.seed(REQUEST+i,v,1)
        c.call(0xa138,REQUEST)
        assert read(c,REQUEST,6)==factory
        assert read(c,0x843a78,6)==own
        assert read(c,CTX+59,6)==own[::-1]
        # Cached directed advertising from another mode must be overwritten.
        c.seed(0x843a8e,4,1);c.seed(0x843a32,0,1)
        c.startup_completed=False
        assert c.call(0xa184)==0
        assert c.startup_completed
        assert c.rd8(0x843a2c)==1 and c.rd8(0x843a32)==1  # original SDK advertising enable
        assert c.rd8(0x843a8e)==0
        assert c.rd(0x843648,4)==0x1c000
        assert c.rd(0x8435f8,4)==symbols['y_ltk_request']|1
        assert c.rd(0x843684,4)==symbols['y_connected']|1
        assert (c.rd(0x842f20,2),c.rd(0x842f22,2))==(83,23)
        assert c.rd8(0x842e78+5)==37
        # Empty scan response leaves only the own-address header.
        scan=int.from_bytes(frame[0x116bc-image.BASE:0x116c0-image.BASE],'little')
        assert c.rd8(scan+5)==6, (hex(scan),c.rd8(scan+5))
        assert c.call(symbols['y_profile_scan'],REQUEST,31)==0
        return c
    first=boot()
    assert read(first,0x842e84,31)==advertisement()
    assert first.rd8(CTX+30)==0
    for packet in TRANSCRIPT:
        for i,v in enumerate(packet): first.seed(REQUEST+i,v,1)
        assert first.call(symbols['y_pair_request'],CTX,REQUEST,len(packet),RESPONSE)>0
    assert first.call(symbols['y_bond_save'],CTX)==1
    saved=dict(first.flash)
    again=boot(dict(saved))
    assert again.rd8(CTX+30)==2
    host=read(again,CTX+31,6)
    assert read(again,0x842e84,31)==advertisement('reconnect',host[::-1])
    assert again.flash==saved and not again.writes and not again.erases
    failed=ProfileCPU(code);failed.fail_ready=symbols['y_profile_ready']
    failed.seed(0x843a2c,0,1);failed.call(0xa184)
    assert failed.failed_closed and failed.rd8(0x843a2c)==0
    altered=bytearray(unpatched);altered[0xa21c-image.BASE]^=1
    try:startup_overlay(altered,symbols)
    except AssertionError:pass
    else:raise AssertionError('unrecognized startup was accepted')
    return {'ok':True,'offline_only':True,'installable':False,
            'stock_initialized_data_bytes':0x1ac,'stock_mac_and_advertising_sdk_executed':True,
            'discovery_and_bonded_restart_checked':True,'separate_profile_address':True,
            'forced_y_startup_overlay_checked':True,'original_smp_journal_bypassed':True,
            'initialization_error_blocks_advertising':True,'unknown_startup_rejected':True,
            'production_startup_hooks_installed':False,
            'remaining':['profile selection and production integration','radio and console tests']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backup',type=Path,required=True)
    p.add_argument('--build',type=Path,default=Path('build/switch2-pairing'))
    a=p.parse_args(); raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
                if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error('No verified original 1.09 bank')
    print(json.dumps(check(recipes.whole(stock),a.build),indent=2))
if __name__=='__main__':main()






