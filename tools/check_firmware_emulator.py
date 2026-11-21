# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the existing TC32 instruction tests from a verified stock bank in a backup.
Only reads the backup. No physical device access, calibration export or flashing.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from u2c_customfw.firmware import image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, type=Path)
    args = parser.parse_args()
    data = args.backup.read_bytes()
    if len(data) != image.FLASH_SIZE:
        parser.error("Expected a full 512 KiB controller backup")
    stock = next((data[start:start + image.BANK_A_SIZE] for start in (image.BASE, image.BANK_B)
                  if hashlib.sha256(data[start:start + image.BANK_A_SIZE]).hexdigest() == image.STOCK_SHA256), None)
    if stock is None:
        parser.error("No verified original controller firmware 1.09 found in either bank")
    spec = importlib.util.spec_from_file_location("tc32_rumble", ROOT / "firmware-src" / "test_longrumble.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="u2c-cpu-test-") as tmp:
        dat = Path(tmp) / "verified-stock.dat"
        dat.write_bytes(bytes(0x1C) + stock)
        saved = sys.argv
        try:
            sys.argv = [str(spec.origin), str(dat)]
            module.main()
        finally:
            sys.argv = saved


if __name__ == "__main__":
    main()
