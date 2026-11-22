# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble opt-in DInput dongle HID layout mapper (stock XInput untouched)."""
import argparse
import subprocess
import tempfile
from pathlib import Path

from tools.build_ring_hold import bl
from tools.build_usb_steam_layout import ROOT, reorder_descriptor

ENTRY = 0xFFB0
HOOK = 0x7676
# 0x6000.. is the flash image of the first application block, which the
# receiver copies to RAM at 0x0000.. and executes there. The patched call
# runs at 0x1676, NOT at its image address 0x7676. The helper at 0xFFB0
# remains flash-resident; a BL encoded relative to 0x7676 jumps to 0x9FB0
# at runtime and never reaches the helper.
RAM_BASE = 0x6000
RUNTIME_HOOK = HOOK - RAM_BASE
MAP = 0xF334
MAP_LENGTH = 145


def build(assembler, linker, objcopy):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = ROOT / 'firmware-src/dongle-steam-layout.s'
        obj, elf, binary = (td / x for x in ('a.o','a.elf','a.bin'))
        subprocess.run([str(assembler),'-o',str(obj),str(src)],check=True)
        subprocess.run([str(linker),'-e','dongle_d_steam_repack',f'-Ttext={ENTRY:#x}', '-o',str(elf),str(obj)],check=True)
        subprocess.run([str(objcopy),'-O','binary',str(elf),str(binary)],check=True)
        payload = binary.read_bytes()
        assert len(payload) <= 0x10000 - ENTRY, 'Dongle code exceeds the app bank'
        return payload


def cli():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bin-dir',type=Path,required=True)
    p.add_argument('--blobs',type=Path,default=ROOT/'u2c_customfw/firmware/blobs')
    a=p.parse_args(); exe=lambda x:a.bin_dir/('tc32-elf-'+x)
    blob=build(exe('as'),exe('ld'),exe('objcopy'))
    a.blobs.mkdir(parents=True,exist_ok=True)
    (a.blobs/'dongle-steam.bin').write_bytes(blob)
    (a.blobs/'dongle-steam-hook.bin').write_bytes(bl(RUNTIME_HOOK,ENTRY))
    print(f'Dongle DInput steam mapper at {ENTRY:#x}: {len(blob)} bytes')

if __name__=='__main__':cli()
