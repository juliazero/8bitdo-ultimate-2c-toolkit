#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate package versions, beta tag and native release assets before publishing."""
import argparse
import hashlib
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def version():
    with (ROOT / 'pyproject.toml').open('rb') as file:
        declared = tomllib.load(file)['project']['version']
    namespace = {}
    exec((ROOT / 'u2c_customfw/__init__.py').read_text(encoding='utf-8'), namespace)
    if declared != namespace['__version__']:
        raise ValueError('pyproject.toml and u2c_customfw/__init__.py versions differ')
    return declared


def verify(tag=None, assets=None):
    v = version()
    if tag is not None:
        if not re.fullmatch(r'\d+\.\d+\.\d+b[1-9]\d*', v):
            raise ValueError(f'{v} is not a PEP 440 beta version')
        if tag != f'v{v}':
            raise ValueError(f'tag {tag!r} does not match v{v}')
    if assets is not None:
        directory = Path(assets)
        names = [
            f'firmware-toolkit-{v}-windows-x86_64.zip',
            f'firmware-toolkit-{v}-linux-x86_64.AppImage',
            f'firmware-toolkit-{v}-macos-arm64.zip',
            f'firmware-toolkit-{v}-macos-x86_64.zip',
        ]
        for name in names:
            artifact = directory / name
            checksum = directory / (name + '.sha256')
            if not artifact.is_file() or not checksum.is_file() or artifact.stat().st_size == 0:
                raise ValueError(f'missing/empty release asset or checksum: {name}')
            expected_line = f'{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {name}\n'
            if checksum.read_text(encoding='ascii') != expected_line:
                raise ValueError(f'invalid checksum for {name}')
        actual = {p.name for p in directory.iterdir() if p.is_file()}
        expected = set(names) | {name + '.sha256' for name in names}
        if actual != expected:
            raise ValueError(f'unexpected assets: {sorted(actual ^ expected)}')
    return v


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', help='Tag name, e.g. v1.3.0b1')
    parser.add_argument('--assets', type=Path, help='Directory with four platform packages and checksums')
    args = parser.parse_args()
    try:
        print('Release validation passed:', verify(args.tag, args.assets))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
