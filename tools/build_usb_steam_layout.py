# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the opt-in D-input USB report reorder, preserving service report IDs.

Compiler output is compared to the pinned assembler bytes; no stock firmware
image is distributed or reconstructed by this tool.
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

from tools.build_ring_hold import bl
from u2c_customfw.firmware import image

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 0x19F80
HOOK = 0xEF88
MAP = 0x17B74
MAP_LENGTH = 145
# Original stock report map has these complete, independently valid HID items.
# No original vendor descriptors are deleted or shortened.
SEGMENTS = ((0, 8), (32, 55), (55, 75), (75, 92), (8, 32), (92, 145))


def reorder_descriptor(stock_map: bytes) -> bytes:
    if len(stock_map) != MAP_LENGTH:
        raise ValueError('Expected the 145-byte original USB D-input HID descriptor')
    if stock_map[:8] != bytes.fromhex('05 01 09 05 a1 01 85 01'):
        raise ValueError('Unrecognized USB D-input report ID / collection')
    descriptor = b''.join(stock_map[a:b] for a, b in SEGMENTS)
    assert len(descriptor) == MAP_LENGTH
    assert descriptor.count(bytes.fromhex('85 81')) == 1  # vendor report ID 0x81
    assert descriptor.count(bytes.fromhex('85 02')) == 1  # vendor report ID 0x02
    assert descriptor[0:8] == stock_map[0:8]
    assert descriptor[-53:] == stock_map[92:]  # vendor and LED output preserved
    return descriptor


def build(assembler: Path, linker: Path, objcopy: Path) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        obj, elf, binary = (td / x for x in ('repack.o', 'repack.elf', 'repack.bin'))
        subprocess.run([str(assembler), '-o', str(obj), str(ROOT/'firmware-src/usb-steam-layout.s')], check=True)
        subprocess.run([str(linker), '-e', 'usb_d_steam_repack', f'-Ttext={ENTRY:#x}', '-o', str(elf), str(obj)], check=True)
        subprocess.run([str(objcopy), '-O', 'binary', str(elf), str(binary)], check=True)
        result = binary.read_bytes()
        assert len(result) == 44 and result[:2] == bytes.fromhex('03 65') and result[-2:] == bytes.fromhex('03 6d')
        return result


def cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bin-dir', type=Path, required=True, help='Directory containing tc32-elf-as, tc32-elf-ld and tc32-elf-objcopy')
    p.add_argument('--blobs', type=Path, default=ROOT/'u2c_customfw/firmware/blobs')
    a = p.parse_args()
    exe = lambda name: a.bin_dir / ('tc32-elf-' + name)
    payload = build(exe('as'), exe('ld'), exe('objcopy'))
    a.blobs.mkdir(parents=True, exist_ok=True)
    (a.blobs/'usbsteam.bin').write_bytes(payload)
    (a.blobs/'usbsteamhook.bin').write_bytes(bl(HOOK, ENTRY))
    print({'payload_bytes': len(payload), 'hook': hex(HOOK), 'entry': hex(ENTRY)})

if __name__ == '__main__': cli()
