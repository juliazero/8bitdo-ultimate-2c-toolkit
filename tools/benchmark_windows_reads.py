# SPDX-License-Identifier: GPL-3.0-or-later
r"""Compare old/new HID queue draining using read-only bootloader requests.

Run from the repository: .venv\Scripts\python tools/benchmark_windows_reads.py
Requires one controller or dongle in bootloader mode. No erase/write/restart.
"""
import hashlib
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from u2c_customfw import state
from u2c_customfw.ops.devices import find
from u2c_customfw.protocol import boot
from u2c_customfw.transport.windows import WindowsChannel


class PreviousChannel(WindowsChannel):
    def _drain(self):
        for _ in range(32):
            if not self.device.read(64, 1):
                break


def main():
    if sys.platform != "win32":
        raise SystemExit("This benchmark requires Windows.")
    lock = state.lock_device(state.default_store())
    if lock is None:
        raise SystemExit("Another toolkit operation is running. Wait for it to finish.")
    try:
        found = [f for f in find() if f.mode == "boot"]
        if len(found) != 1:
            raise SystemExit("Connect exactly one device in bootloader mode.")
        expected = None
        timings = {"previous": [], "optimized": []}
        size = 128 * boot.CHUNK
        for label, channel_type in [("previous", PreviousChannel), ("optimized", WindowsChannel)] * 2:
            ch = channel_type(found[0].info, 0)
            try:
                if boot.identify(ch) not in (boot.CONTROLLER_ID, boot.DONGLE_ID):
                    raise SystemExit("Bootloader identity not recognized.")
                start = time.perf_counter()
                data = boot.read(ch, 0, size)
                elapsed = time.perf_counter() - start
            finally:
                ch.close()
            if expected is not None and data != expected:
                raise SystemExit("Reads differ; benchmark failed.")
            expected = data
            timings[label].append(elapsed)
            print(f"{label}: {elapsed:.3f}s / 128 requests ({elapsed / 128 * 1000:.2f} ms/request)", flush=True)
        old, new = (sum(timings[label]) / 2 for label in ("previous", "optimized"))
        print(f"Speedup: {old / new:.2f}x; identical {size}-byte reads, SHA256 {hashlib.sha256(expected).hexdigest()}")
    finally:
        state.unlock_device(lock)


if __name__ == "__main__":
    main()
