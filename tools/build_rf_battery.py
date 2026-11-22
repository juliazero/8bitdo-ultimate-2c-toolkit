# SPDX-License-Identifier: GPL-3.0-or-later
"""Build optional RF-battery controller hook (experimental; stock RF length is 20)."""
import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 0x19A00
HOOK = 0xA350
LIMIT = 0x19B00


def build(compiler, output, blobs):
    compiler = Path(compiler).resolve()
    tools = compiler.parent
    output = Path(output)
    blobs = Path(blobs)
    output.mkdir(parents=True, exist_ok=True)
    blobs.mkdir(parents=True, exist_ok=True)

    def run(program, *args):
        subprocess.run([str(tools / program), *map(str, args)], check=True)

    source = ROOT / 'firmware-src'
    obj = output / 'rf-battery.o'
    elf = output / 'rf-battery.elf'
    code = output / 'rf-battery.bin'
    run('tc32-elf-gcc', '-Os', '-Wall', '-Werror', '-ffreestanding', '-fno-builtin',
        '-ffunction-sections', '-c', source / 'rf-battery.c', '-o', obj)
    run('tc32-elf-ld', '-N', f'-Ttext={ENTRY:#x}', '--defsym', 'stock_rf_pack=0xca0', '-e',
        'rf_battery_pack', obj, '-o', elf)
    run('tc32-elf-objcopy', '-O', 'binary', '-j', '.text', elf, code)
    payload = code.read_bytes()
    if not 0 < len(payload) <= LIMIT - ENTRY:
        raise ValueError('RF battery code too large')

    hook_obj = output / 'rf-battery-hook.o'
    hook_elf = output / 'rf-battery-hook.elf'
    hook_bin = output / 'rf-battery-hook.bin'
    run('tc32-elf-as', source / 'rf-battery-hook.s', '-o', hook_obj)
    run('tc32-elf-ld', '-N', f'-Ttext={HOOK:#x}', '--defsym', f'rf_battery_pack={ENTRY:#x}',
        hook_obj, '-o', hook_elf)
    run('tc32-elf-objcopy', '-O', 'binary', '-j', '.text', hook_elf, hook_bin)
    if len(hook_bin.read_bytes()) != 4:
        raise ValueError('Expected a four-byte TC32 branch hook')
    for p in (code, hook_bin):
        (blobs / p.name).write_bytes(p.read_bytes())
    return len(payload)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'build/rf-battery')
    p.add_argument('--blobs', type=Path, default=ROOT / 'u2c_customfw/firmware/blobs')
    args = p.parse_args()
    print(f'RF telemetry code: {build(args.compiler, args.output, args.blobs)} bytes')
