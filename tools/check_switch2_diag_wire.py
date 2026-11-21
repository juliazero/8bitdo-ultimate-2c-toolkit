# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify diagnostic command and sticky flags in compiled TC32 GATT code."""
import argparse
import hashlib
from pathlib import Path

from tools.build_switch2_trial import assemble
from tools.check_switch2_rumble_wire import MotorCPU
from tools.check_switch2_pairing import REQUEST
from u2c_customfw.firmware import image



def check(stock, build):
    frame, _ = assemble(stock, build)
    cpu = MotorCPU(frame)
    cpu.call(0x11e64, 0x843178, 40, 8)
    cpu.call(0x121a0)
    cpu.call(0x13628, 0x1c000)
    cpu.seed(0x842f20, 83, 2)
    cpu.seed(0x842f22, 83, 2)
    cpu.seed(0x8412ce, 1, 2)

    def write(handle, value):
        # Simulate the host consuming prior notification data before the
        # next GATT write. Without this stock TX ring saturates artificially.
        cpu.seed(0x843a24, cpu.rd8(0x843a23), 1)
        att = bytes((0x52, handle, 0)) + value
        raw = bytes((2, len(att) + 4)) + len(att).to_bytes(2, 'little') + b'\x04\x00' + att
        for i, byte in enumerate(raw):
            cpu.seed(REQUEST + i, byte, 1)
        assert cpu.call(0x138c0, 0x40, REQUEST) == 0

    # Experimental firmware commands must be inert, including direct motor tests.
    for sub in range(1,14):
        before=len(cpu.notifications)
        write(0x16, bytes((0x7e,0x91,1,sub,0,0,0,0)))
        assert len(cpu.notifications)==before
    assert not cpu.motor_calls
    # Preserve the full 64-byte read, with its 16-byte header.
    mem = bytes((2,0x91,1,4,0,8,0,0,64,0x7e,0,0)) + (0x13000).to_bytes(4,'little')
    write(0x16, mem)
    channel,response=cpu.notifications[-1]
    assert channel==0x1a and len(response)==80
    assert response[:4]==bytes((2,1,1,4)) and response[8]==64
    return {'ok':True,'diagnostics_removed':True,'full_memory_reply':True,'device_tested':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    args = parser.parse_args()
    raw = args.backup.read_bytes()
    stock = next((raw[start:start + image.BANK_A_SIZE] for start in (image.BASE, image.BANK_B)
                  if hashlib.sha256(raw[start:start + image.BANK_A_SIZE]).hexdigest() == image.STOCK_SHA256), None)
    if stock is None:
        parser.error('A verified stock 1.09 backup is required')
    print(check(stock, args.build))


if __name__ == '__main__':
    main()
