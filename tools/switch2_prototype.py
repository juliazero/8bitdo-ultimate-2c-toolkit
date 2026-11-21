# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Switch 2 Pro wire-format experiment. No HID, Bluetooth, or flash I/O.

Not a controller emulator or an installable tweak. Protocol reference:
https://github.com/ndeadly/switch2_controller_research
"""
import argparse
import json

REPORT_SIZE = 63  # BLE value, without the USB report ID
MIN_ATT_MTU = REPORT_SIZE + 3
BUTTON_BITS = {
    "B":0, "A":1, "Y":2, "X":3, "R":4, "ZR":5, "Plus":6, "R3":7,
    "Down":8, "Right":9, "Left":10, "Up":11, "L":12, "ZL":13, "Minus":14, "L3":15,
    "Home":16, "Capture":17, "GR":18, "GL":19, "C":20,
}

def advertisement(kind="discovery", host=None):
    if kind not in ("discovery", "reconnect", "wake"):
        raise ValueError("Unknown advertisement kind")
    if kind == "discovery":
        if host is not None: raise ValueError("Discovery must not identify a host")
        address = bytes(6)
    else:
        if not isinstance(host, bytes) or len(host) != 6:
            raise ValueError("Provide six address bytes in display order")
        address = host[::-1]
    manufacturer = bytes.fromhex("53 05 01 00 03 7e 05 69 20 00 01")
    manufacturer += bytes((0x81 if kind == "wake" else 0,)) + address + bytes((15,)) + bytes(7)
    return bytes((2,1,6,len(manufacturer)+1,255)) + manufacturer

def _stick(x,y):
    if any(type(v) is not int or not 0 <= v <= 4095 for v in (x,y)):
        raise ValueError("Stick coordinates must be 12-bit integers")
    return (x | y << 12).to_bytes(3,"little")

def input_report(*, buttons=(), sticks=(2048,2048,2048,2048), counter=0,
                 battery_level=9, external_power=False, charging=False):
    if type(counter) is not int or not 0 <= counter <= 255:
        raise ValueError("Counter must be a byte")
    if type(battery_level) is not int or not 0 <= battery_level <= 9:
        raise ValueError("Battery level must be 0..9, not a percentage")
    if type(external_power) is not bool or type(charging) is not bool:
        raise ValueError("Power flags must be boolean")
    if len(sticks) != 4: raise ValueError("Provide four stick coordinates")
    mask = 0
    for name in buttons:
        if name not in BUTTON_BITS: raise ValueError("Unknown Nintendo button: " + str(name))
        mask |= 1 << BUTTON_BITS[name]
    packet = bytearray(REPORT_SIZE)
    packet[0] = counter
    packet[1] = battery_level << 2 | int(external_power) | int(charging) << 1
    packet[2:5] = mask.to_bytes(3,"little")
    packet[5:8], packet[8:11] = _stick(*sticks[:2]), _stick(*sticks[2:])
    packet[11] = 0x30
    # No IMU, NFC or headset data. Their initialization/calibration is unresolved.
    return bytes(packet)

def common_report(*,buttons=(),sticks=(2048,2048,2048,2048),counter=0,voltage_mv=0):
    buttons=tuple(buttons)
    if type(counter) is not int or not 0<=counter<=0xffffffff:raise ValueError('32-bit counter required')
    if type(voltage_mv) is not int or not 0<=voltage_mv<=65535:raise ValueError('Voltage must fit u16')
    native=input_report(buttons=buttons,sticks=sticks)
    bits={'B':2,'A':3,'Y':0,'X':1,'R':6,'ZR':7,'Plus':9,'R3':10,'Down':16,'Right':18,
          'Left':19,'Up':17,'L':22,'ZL':23,'Minus':8,'L3':11,'Home':12,'Capture':13,'GR':24,'GL':25,'C':14}
    packet=bytearray(63);packet[:4]=counter.to_bytes(4,'little')
    packet[4:8]=sum(1<<bits[name] for name in set(buttons)).to_bytes(4,'little')
    packet[10:16]=native[5:11];packet[31:33]=voltage_mv.to_bytes(2,'little');packet[41]=1
    return bytes(packet)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps({"offline_only":True,"installable":False,
                     "advertisement_bytes":len(advertisement()),
                     "discovery_hex":advertisement().hex(),
                     "input_bytes":len(input_report()),"minimum_att_mtu":MIN_ATT_MTU,
                     "remaining":["GATT server","pairing integration and persistent bonds",
                                  "link-layer encryption",
                                  "command responses/calibration","5ms link timing","console tests"]},indent=2))
if __name__ == "__main__": main()
