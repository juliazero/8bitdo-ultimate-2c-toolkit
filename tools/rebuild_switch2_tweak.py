# SPDX-License-Identifier: GPL-3.0-or-later
"""Recompile the Y (Switch 2 Pro) *selectable tweak* blobs, not a flash image.

This script never connects to or writes to a controller. It requires a verified
stock 1.09 flash dump used solely as a reference for deterministic patch bytes.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BLOBS = ROOT / 'u2c_customfw/firmware/blobs'
from u2c_customfw.firmware import image
from tools.build_switch2_recipe import REGIONS


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    script = str(args[0])
    command = [sys.executable, "-m", script.removesuffix(".py").replace("/", "."), *map(str, args[1:])]
    subprocess.run(command, cwd=ROOT, check=True)


def rebuild(compiler: Path, stock: Path):
    compiler = compiler.resolve()
    stock = stock.resolve()
    if not compiler.is_file():
        raise ValueError(f'TC32 compiler not found: {compiler}')
    if not stock.is_file():
        raise ValueError(f'Stock firmware backup not found: {stock}')
    raw = stock.read_bytes()
    if len(raw) != image.FLASH_SIZE:
        raise ValueError('Expected 512 KiB stock backup')
    if hashlib.sha256(raw[image.BASE:image.BASE + image.BANK_A_SIZE]).hexdigest() != image.STOCK_SHA256:
        raise ValueError('Backup bank A does not match verified stock firmware 1.09')
    if any(b != 0xff for b in raw[0x1a000:0x1d000]):
        raise ValueError('Stock backup Y extension region must be blank')

    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='build-switch2-', dir=ROOT / 'build') as directory:
        tmp = Path(directory)
        build = tmp / 'pairing'
        stage = tmp / 'blobs'
        run('tools/build_switch2_pairing.py', '--compiler', compiler, '--output', build)
        run('tools/build_switch2_gatt.py', '--build', build)
        run('tools/build_switch2_recipe.py', '--backup', stock, '--build', build, '--output', stage)
        expected = {f'switch2-{address:x}.bin':size for address,size in REGIONS}
        for name, size in expected.items():
            output = stage / name
            if not output.is_file() or output.stat().st_size != size:
                raise ValueError(f'Unexpected compiled blob: {name} expected {size} bytes')
        # Only current Y blobs are shipped. Do not regenerate legacy
        # experimental compatibility files when rebuilding.
        for pattern in ('switch2-*-pre*.bin', 'switch2-force-*.bin'):
            for obsolete in BLOBS.glob(pattern):
                obsolete.unlink()
        for name in expected:
            os.replace(stage / name, BLOBS / name)
        manifest = BLOBS / 'MANIFEST'
        manifest.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                         for p in sorted(BLOBS.glob('*.bin'))), encoding='ascii')
    print('Y firmware recompiled into selectable bt_switch2 tweak blobs.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--stock', type=Path, required=True)
    args = parser.parse_args()
    rebuild(args.compiler, args.stock)


if __name__ == '__main__':
    main()
