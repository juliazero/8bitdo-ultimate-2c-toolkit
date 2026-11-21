# Building experimental Y firmware from source

## Rumble fix (21 November 2026)

The Pro Controller 2's GATT `0x0012` vibration report is **42 bytes**:
1 report-id byte, two 16-byte actuator blocks, and 9 reserved bytes.
The original Y implementation mistakenly required a 33-byte report and thus
ignored normal full-length vibration writes. A write to `0x0016` instead
starts with the same 33-byte prefix followed by at least an 8-byte Nintendo
command (41 bytes minimum). The new firmware handles both lengths and
preserves the command offset. A zero-amplitude report stops the ordinary
8BitDo motors with a one-tick zero-strength command so an active hold cannot
ignore the stop.

These changes are **source-only until the Y code is compiled with TC32**.
Rebuilding the Windows desktop EXE does not update the controller firmware.
The existing embedded `switch2-*` blobs are not updated by this change.
No hardware or console validation has been performed. First validate the
resulting full image against a verified stock backup, then test vibration
in Y mode using a host that sends Pro Controller 2 reports to GATT 0x0012.

The checked-in `u2c_customfw/firmware/blobs/switch2-*` files are **precompiled**.
Changing `firmware-src/switch2-*.c` or `switch2-rumble.h` does not alter what the
ordinary toolkit installs. Do not assume the HD-rumble source change is present
in the existing binaries.

Prerequisites: the Telink **TC32** cross-compiler (not system GCC), Python 3.12+
and a complete 512-KiB backup made from your own device containing a verified
8BitDo v1.09 stock bank. Keep the backup private and outside Git. All commands
below must run in the project root. On Windows, supply a Windows path to the
`tc32-elf-gcc.exe` binary. These tools only prepare files; they do not flash.

```sh
python tools/build_switch2_pairing.py --compiler /path/to/tc32-elf-gcc --output build/switch2-pairing
python tools/build_switch2_gatt.py --build build/switch2-pairing
python tools/build_switch2_trial.py --backup /path/to/backup.bin --build build/switch2-pairing --output build/switch2-trial
```

`build/switch2-trial/controller-y-experimental-full.bin` is an experimental
image for offline inspection. The trial checks exact stock-hash and extension
area preconditions and runs emulator checks, but **does not prove safe flash or
Bluetooth interoperability**. Confirm the generated metadata and the motor
callbacks before using it with any device. The project build harness may reject
new code that exceeds the 8-KiB Y firmware code limit or requires unsupported
relocations; do not bypass those checks.

A maintainers-only refresh of installable recipe blobs requires:

```sh
python tools/build_switch2_recipe.py --backup /path/to/backup.bin --build build/switch2-pairing
python tools/make_constants.py /path/to/verified-stock-1.09.dat
```

Recompute `u2c_customfw/firmware/blobs/MANIFEST` after exporting and validate
against a **verified stock** backup. The manifest must list SHA-256 for each
blob and `stock_hashes.py` must match the updated recipe. These are separate
release steps; do not copy binaries blindly from an unverified image.

**Analog triggers:** the Nintendo Switch 2 Pro input reports only contain
buttons for ZL/ZR; gradual LT/RT require an Xbox-compatible profile, or a
separately implemented NSO GameCube identity and report layout.
