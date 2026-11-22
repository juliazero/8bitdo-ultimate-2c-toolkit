# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the actual X/D ATT tables and ring output handler on an original bank.

    python -m tools.check_bt_ring --stock /path/to/verified-bank-a.bin

No device I/O. Stock firmware is supplied locally and is never packaged.
"""
import argparse
from pathlib import Path
from tools.check_switch2_gatt import GattCPU, REQUEST
from tools.switch2_layout import prepare_frame
from u2c_customfw.firmware import recipes
from u2c_customfw.transport.ring_protocol import SERVICE_UUID, LEVEL_UUID


class RingCPU(GattCPU):
    def trap(self, pc, registers):
        if pc == 0x18390:
            return False  # Execute the real callback, not the rumble test stub.
        return super().trap(pc, registers)


def check(stock):
    for long in (False, True):
        keys = ['bt_ring_read', 'bt_xbox', 'bt_modes', 'bt_rumble', 'fixes', 'ring_normal']
        if long:
            keys.append('bt_rumble_long')
        frame = recipes.build(stock, keys)
        assert set(recipes.identify(frame).recipes) == set(keys)
        for profile, table in [('X', 0x1743c), ('D', 0x19000)]:
            cpu = RingCPU(prepare_frame(frame))
            cpu.call(0x11e64, 0x843178, 40, 8)
            cpu.call(0x121a0)
            cpu.call(0x13628, table)
            cpu.seed(0x842f20, 66, 2)
            cpu.seed(0x842f22, 23, 2)

            def request(att):
                rf = bytes((2, len(att) + 4)) + len(att).to_bytes(2, 'little') + b'\x04\x00' + att
                for i, value in enumerate(rf):
                    cpu.seed(REQUEST + i, value, 1)
                response = cpu.call(0x138c0, 0x40, REQUEST)
                return bytes(cpu.rd8(response + 6 + i) for i in range(cpu.rd(response + 2, 2))) if response else b''

            assert request(bytes.fromhex('102e00ffff0028')) == bytes.fromhex('11142e003000') + SERVICE_UUID.bytes[::-1]
            assert request(bytes.fromhex('082f0030000328')) == bytes.fromhex('09152f00023000') + LEVEL_UUID.bytes[::-1]
            cpu.seed(0x841640, 1, 1)
            for level in range(6):
                request(bytes([0x52, 0x21, 0, 0x80, level, 0, 0, 0, 0, 0, 0]))
                assert cpu.rd8(0x841641) == level
                assert request(bytes.fromhex('0a3000')) == bytes([0x0b, 1, level])
            request(bytes([0x52, 0x21, 0, 0x80, 255, 1, 0, 0, 0, 0, 0]))
            assert cpu.rd8(0x841645) == 1  # Save requested in LED task; not a flash ACK.
            request(bytes.fromhex('12300000ff'))
            assert request(bytes.fromhex('0a3000')) == bytes([0x0b, 1, 5])
            print(f'{profile}, long rumble={long}: service discovery, six writes/reads, save queue, read-only service passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    check(parser.parse_args().stock.read_bytes())
