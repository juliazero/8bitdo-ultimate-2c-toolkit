# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the compiled Y GATT write callback and motor setter (no device I/O)."""
import argparse
import hashlib
import json
from pathlib import Path

from tools.build_switch2_trial import assemble
from tools.check_switch2_gatt import GattCPU
from tools.check_switch2_pairing import REQUEST
from u2c_customfw.firmware import image


class MotorCPU(GattCPU):
    def __init__(self, frame):
        self.motor_calls = []
        super().__init__(frame)

    def trap(self, pc, registers):
        if pc == 0xfa68:
            self.motor_calls.append(tuple(registers[:3]))
            return True  # capture only; do not run hardware-related motor registers
        return super().trap(pc, registers)


def frame_value(low, high):
    return (333 | (low << 10) | (333 << 20) | (high << 30)).to_bytes(5, 'little')


def vibration(left, right):
    return (b'\x00\x50' + frame_value(left, 0) + bytes(10)
            + b'\x50' + frame_value(right, 0) + bytes(10))


def check(stock, build):
    frame, _ = assemble(stock, build)
    cpu = MotorCPU(frame)
    cpu.call(0x11e64, 0x843178, 40, 8)
    cpu.call(0x121a0)
    cpu.call(0x13628, 0x1c000)
    cpu.seed(0x842f20, 66, 2)
    cpu.seed(0x842f22, 66, 2)

    def write(handle, value):
        att = bytes((0x52, handle, 0)) + value
        raw = bytes((2, len(att) + 4)) + len(att).to_bytes(2, 'little') + b'\x04\x00' + att
        for i, byte in enumerate(raw):
            cpu.seed(REQUEST + i, byte, 1)
        assert cpu.call(0x138c0, 0x40, REQUEST) == 0

    command = bytes.fromhex('07 91 01 01 00 00 00 00')
    write(0x16, command)  # actual Pro 2 bare command on combined-report handle
    assert cpu.motor_calls == [], cpu.motor_calls
    write(0x12, vibration(600, 100))  # real hosts send 33-byte actuator-only reports
    assert cpu.motor_calls[-1] == (150, 25, 20), cpu.motor_calls
    write(0x16, vibration(200, 400) + command)
    assert cpu.motor_calls[-1] == (50, 100, 20), cpu.motor_calls
    write(0x12, vibration(0, 0) + bytes(9))  # 42-byte padded form stays supported
    assert cpu.motor_calls[-1] == (0, 0, 1), cpu.motor_calls
    assert len(cpu.motor_calls) == 3
    # Captured duplicated 33-byte reports from Switch2Connect.
    for sample,expected in (
        ('e1 00 10 1e 00',(0,0,1)),
        ('e1 a0 19 1e 00',(154,0,20)),
        ('e1 00 10 9e 76',(0,118,20)),
        ('e1 a0 19 9e 76',(154,118,20)),
    ):
        block=b'\x59'+bytes.fromhex(sample)*3
        write(0x12,b'\x00'+block*2)
        assert cpu.motor_calls[-1]==expected,cpu.motor_calls
    # The Switch 2 also sends predefined vibration samples as ordinary
    # vendor commands on 0x0016. They are not 33-byte HD Rumble packets.
    cpu.seed(0x8412ce, 1, 2)  # command response CCCD
    def vendor(sub, payload):
        command = bytes((0x0a, 0x91, 1, sub, 0, len(payload), 0, 0)) + payload
        write(0x16, command)
        assert cpu.notifications[-1] == (0x1a, bytes((0x0a, 1, 1, sub, 0x10, 0x78, 0, 0)))
    vendor(2, bytes((3, 0, 0, 0)))  # documented connection click
    assert cpu.motor_calls[-1] == (80, 80, 16)
    vendor(2, bytes((0, 0, 0, 0)))  # silence
    assert cpu.motor_calls[-1] == (0, 0, 1)
    vendor(8, bytes.fromhex('01ffffffffffffffff3500460000000000000000'))
    # Raw vendor vibration remains acknowledged, but its envelope encoding
    # is not interpreted as HD Rumble without a matching hardware trace.
    assert len(cpu.motor_calls) == 9
    # The stock button processing can synthesise Home alongside Start+Select.
    # Verify the compiled input adapter never advertises that chord as Home.
    symbols = json.loads((build/'pairing-prototype.json').read_text())['symbols']
    cpu.call(symbols['y_input_reset'])
    cpu.seed(0x8412cc, 1, 2)  # native input CCCD
    for axis in (0x8433ec, 0x8433f6, 0x8433e4, 0x8433e6):
        cpu.seed(axis, 32768, 2)
    cpu.seed(0x843415, 50, 1)
    cpu.seed(0x800518, 0, 1)
    def buttons(word, raw):
        cpu.seed(0x84342c, word, 4)
        cpu.seed(0x84344c, raw, 4)
        cpu.seed(0x843a24, cpu.rd8(0x843a23), 1)
        assert cpu.call(symbols['y_input_from_controller']) == 0
        handle, report = cpu.notifications[-1]
        assert handle == 0x0e
        return int.from_bytes(report[2:5], 'little')
    home = 1 << 16
    assert buttons(0, 1 << 17) & home
    assert not buttons((1 << 0) | (1 << 3) | (1 << 17), 1 << 17) & home
    assert not buttons((1 << 0) | (1 << 3), 1 << 17) & home
    return {'ok': True, 'compiled_gatt_dispatch': True,
            'bare_command_0016_accepted': True,
            'vibration_0012_and_0016_delivered': True,
            'explicit_stop_delivered': True, 'vendor_sample_command_checked': True,
            'home_chord_checked': True,
            'motor_calls': cpu.motor_calls, 'device_tested': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backup', type=Path, required=True)
    p.add_argument('--build', type=Path, required=True)
    args = p.parse_args()
    raw = args.backup.read_bytes()
    stock = next((raw[start:start + image.BANK_A_SIZE]
                  for start in (image.BASE, image.BANK_B)
                  if hashlib.sha256(raw[start:start + image.BANK_A_SIZE]).hexdigest()
                  == image.STOCK_SHA256), None)
    if stock is None:
        p.error('Verified original firmware 1.09 required')
    print(json.dumps(check(stock, args.build), indent=2))


if __name__ == '__main__':
    main()
