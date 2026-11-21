# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the standalone B/X startup-selector prototype in the TC32 interpreter.
This does NOT emulate startup, advertising or HID profile initialization.
"""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from u2c_customfw.firmware import image


def run():
    spec = importlib.util.spec_from_file_location("tc32_selector", ROOT / "firmware-src" / "test_longrumble.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = (ROOT / "firmware-src" / "btmode-selector.hex").read_text(encoding="utf-8")
    code = bytes.fromhex(" ".join(line.split("#", 1)[0] for line in text.splitlines()))
    address = 0x18FC0  # arbitrary test address, not an approved firmware hook
    payload = bytearray(image.APP_SIZE)
    payload[address - image.BASE:address - image.BASE + len(code)] = code
    cases = (("Home, saved Xbox", 1 << 17, 1, 1),
             ("Home, saved D-input", 1 << 17, 0, 0),
             ("Home, no saved mode", 1 << 17, 255, 1),
             ("X + Home, saved D-input", (1 << 4) | (1 << 17), 0, 1),
             ("B + Home, saved Xbox", (1 << 12) | (1 << 17), 1, 0))
    for label, buttons, saved, expected in cases:
        actual = module.Controller(bytes(payload)).call(address, buttons, saved)
        assert actual == expected, (label, actual, expected)
        print(label + " -> " + ("Xbox" if actual else "D-input"))
    print("Selector instructions passed; complete Bluetooth firmware patch is not implemented.")


if __name__ == "__main__":
    run()
