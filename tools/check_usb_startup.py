# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the stock v1.09 USB selector with injected preconditions; no device access.
Hardware setup, disconnect/reconnect and flash-save drivers are trapped. The
actual selector, mode setters and descriptor selection instructions execute.
This does NOT prove a physical B/X startup sequence: full startup/button scan
and the conditions under which f014 is reached are outside this test.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from u2c_customfw.firmware import image


def check(stock):
    spec = importlib.util.spec_from_file_location('tc32_usb', ROOT / 'firmware-src/test_longrumble.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class USB(module.Controller):
        def __init__(self, saved, buttons, battery_mv=0):
            super().__init__(stock)
            self.saves = []
            self.wr8(0x800518, 1)
            self.wr32(0x843424, battery_mv)
            self.call(0xd794)  # compute the real variant flag, do not inject it
            assert self.rd8(0x843470) == (battery_mv <= 1999)
            self.wr8(0x843915, saved)
            self.wr32(0x84344c, buttons)
            self.wr8(0x842e38, 0xff)  # force mode setter to act
        def wr8(self, address, value):
            if not (0x800000 <= address < 0x810000 or 0x840000 <= address < 0x848000):
                raise AssertionError(f'Unexpected write {address:#x}')
            self.ram[address] = value & 255
        def trap(self, pc, registers):
            if pc == 0xd538:
                self.saves.append(self.rd8(0x843915))
                return True
            if pc in (0xaaa8, 0x1007c, 0x10144, 0xd84, 0xdc4, 0xe9e8):
                registers[0] = 0
                return True
            return False
    for saved, buttons, expected in ((0,0,0),(1,0,1),(255,0,0),(0,1<<12,1),(1,1<<4,0),(0,(1<<12)|(1<<4),1)):
        cpu = USB(saved, buttons)
        cpu.call(0xf014)
        assert cpu.rd8(0x842e38) == expected, (saved,buttons,cpu.rd8(0x842e38))
        assert cpu.rd8(0x843915) == (expected if buttons else saved)
        assert cpu.saves == ([expected] if buttons else [])
        descriptor = cpu.call(0xe860, module.PACKET)
        assert cpu.rd(descriptor+8,4) == (0x301d2dc8 if expected else 0x310a2dc8)
        config = cpu.call(0xe7c4, module.PACKET)
        report = cpu.call(0xe80c, module.PACKET, 1)
        print(f'ok saved={saved} buttons={buttons:#x}: {"D-input" if expected else "XInput"}, device={descriptor:#x}, configuration={config:#x}, report={report:#x}')
    for saved, buttons in ((0,1<<12),(1,1<<12),(1,1<<4),(1,0)):
        cpu = USB(saved, buttons, battery_mv=3700)
        cpu.call(0xf014)
        assert cpu.rd8(0x842e38) == 0 and not cpu.saves
        assert cpu.rd8(0x843915) == saved
    print('ok: battery-equipped Wireless at 3.7V ignores stock B/X and saved wired mode; batteryless variant selects it')
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup',required=True,type=Path)
    args=parser.parse_args()
    data=args.backup.read_bytes()
    stock=next((data[a:a+image.BANK_A_SIZE] for a in (image.BASE,image.BANK_B)
                if hashlib.sha256(data[a:a+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None: parser.error('No verified original firmware 1.09 in either bank')
    check(stock)


if __name__=='__main__': main()
