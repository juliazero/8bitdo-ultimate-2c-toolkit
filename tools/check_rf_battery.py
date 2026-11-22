# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the compiled RF battery hook with stock TC32 instruction emulator.

This verifies packet composition and unmodified input bytes. It does NOT
simulate a 2.4 GHz receiver's acceptance of the longer RF packet.
"""
import argparse
from pathlib import Path
from tools.check_bt_modes import CPU
from u2c_customfw.firmware import image, recipes
from u2c_customfw.protocol.rf_battery import parse


class BatteryCPU(CPU):
    def __init__(self, frame):
        self.original = bytes([0x8f] + list(range(1, 20)))
        self.builder_calls = 0
        super().__init__(frame)

    def wr8(self, address, value):
        if 0x843980 <= address < 0x8439a0 or address in (0x843415, 0x843423, 0x843424, 0x843425, 0x800518):
            self.ram[address] = value & 255
            return
        super().wr8(address, value)

    def trap(self, address, registers):
        if address == 0xca0:
            self.builder_calls += 1
            for i, value in enumerate(self.original):
                self.seed(0x843980 + i, value, 1)
            return True
        return super().trap(address, registers)


def check(stock):
    frame = recipes.build(stock, ['rf_battery_tx'])
    assert set(recipes.identify(frame).recipes) == {'rf_battery_tx'}
    cpu = BatteryCPU(frame)
    cpu.seed(0x843415, 77, 1)
    cpu.seed(0x843424, 3900, 2)
    cpu.seed(0x843423, 1, 1)
    cpu.seed(0x800518, 1, 1)
    for n in (1, 2):
        cpu.call(0x19a00)
        packet = bytes(cpu.rd8(0x843980 + i) for i in range(28))
        assert packet[:20] == cpu.original
        assert parse(packet) == {'percent': 77, 'mv': 3900, 'full': True, 'charging': True, 'sequence': n}
        assert cpu.builder_calls == n
    return {'controller_report_builder_trapped': True, 'controller_hook_executed': True,
            'stock_report_bytes_unchanged': True, 'trailer_decoded': True,
            'hardware_long_packet_accepted': False, 'flash_performed': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock', type=Path, required=True, help='Verified 0x12400-byte bank A from firmware 1.09')
    print(check(p.parse_args().stock.read_bytes()))
