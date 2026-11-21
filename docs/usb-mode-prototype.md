# USB D-input switching (experimental)

The stock USB startup entry procedure is unresolved. The `usb_dinput` recipe
adds a running-controller gesture rather than assuming the stock startup
selector will see the held buttons. It is instruction-tested; the user has also confirmed both USB mode changes
on a physical battery-equipped Wireless controller. It does not enable powering off on USB.

## Behavior

- USB power and configured USB state (`0x8434d8 == 2`) must both be present.
  The earlier variant-flag gate at `0x843470` was incorrect and has been replaced.
- Hold physical B + Home continuously for 3 seconds: select USB D-input.
- Hold physical X + Home continuously for 3 seconds: select USB XInput.
- B + X + Home is rejected. A different gesture restarts the 3-second timer.
- Release Home after a switch before another switch is accepted.
- USB mode is persisted in bit 0 of stock settings byte +5, using the original settings journal only when that bit changes. Bit 1 holds the independent Bluetooth choice.
- Existing stock mode setters avoid re-enumeration when already in that mode.
- On an actual mode change, stock `0xe9e8` with its delay constant changed from 200 to 600 ms drops USB's pull-up, waits 600 ms,
  reconnects and clears the stock USB transfer flags. Power remains present.

Input uses the physical word `0x84344c`, not remapped game input, so Settings
button remapping does not change the gesture. Time uses unsigned deltas on the
stock 16 MHz 32-bit timer, including wrap. The prototype polls on the existing
100 ms main-loop branch: actual acceptance occurs at the next poll after 3 s.

## Operation guards

Both existing vendor callback routes remain called. Before them, the prototype
records packets beginning with vendor report ID `81`. Reads, the `00 21` session opener and the exact motor-test header `81 11 04 08` reset
the hold and enforce two seconds without traffic before a fresh gesture.
Any other vendor operation, including unknown command families, locks switching until
firmware restart. This includes write unlock, writes, update-mode entry and
settings changes. The prototype intentionally has no command to clear that lock.
This is stricter than the eventual UI: for example, changing ring brightness
also locks switching until restart. Arbitrary unknown non-vendor packets do not
clear an already acquired lock.

The original settings-save function is called only for a changed choice. No application-bank write, reboot or power-off function is called by the gesture. This is a bounded first prototype, not a verified hardware
safety guarantee. Peripheral USB timing and host re-enumeration still require
physical testing; the interpreter has no USB host, radio or charging model.

## Placement and compatibility

The integrated 460-byte code sits at `0x9a70`, in the unused tail of the dead
soft-float area already reclaimed by Battery log (`0x9964..0x9cac`). The full
existing Battery log payload stays byte-for-byte unchanged; 112 bytes remain
after the new code. No application-area expansion or use of `0x19000` is needed.

The 20-byte state sits at `0x84165c..0x841670`, between the 28-byte Home ring
state and Battery log state at `0x841670`. Settings starts at `0x841680`.
A C compile-time assertion prevents Home ring state from growing into this gap.
The existing ring recipe initializes the state via its zeroed RAM-code region,
so a Home ring level is required (the WebUI includes normal automatically).

The `0xa488` call wraps and preserves stock task `0xd024`. Vendor calls at
`0xf83c` and `0xf842` wrap the existing Home ring/Settings handlers rather than
bypassing them. Recipe supersession preserves correct identification with and
without Battery log. Builds/removal are tested with every Home ring level.

The experimental checkbox is off by default. In the simulator:

1. Start `Simulator.cmd` (or `--simulator`) and select Controller / USB.
2. Create a backup, select **USB D-input switching (experimental)** and install.
3. Wait until the firmware read completes. Open Diagnostics; leave at least
   two seconds without settings/firmware requests before starting the gesture.
4. Latch B and Home, hold for 3 seconds; Diagnostics should report USB D-input.
5. Release Home (Release All Buttons is convenient), then latch X and Home
   for 3 seconds to return to XInput. Do not power off while USB is connected.

A configuration/write command locks mode switching until firmware restart.
The startup hook restores the saved USB choice after restart, including after unplugging and powering off. The user confirmed physical switching in both directions; these automated
results still establish code behavior and application workflow rather than
USB driver timing or complete hardware safety. No physical device was flashed in these tests.

## Reproduce

```powershell
python tools/build_usb_mode_prototype.py
python tools/check_usb_mode_prototype.py --backup 'C:\path\controller-backup.bin'
```

The builder uses an explicit, range-checked Thumb-1 encoding subset mapped to
TC32. Without arguments it writes the isolated `usb-mode-prototype.hex`.
`--integrated u2c_customfw/firmware/blobs` writes the compatible installed blobs;
`firmware-src/build.sh` runs this after rebuilding the shared ring/log blobs. The checker extracts
a hash-verified original bank from a backup and overlays the prototype in
process memory. It never enumerates a device or writes a full firmware image.

Instruction-level checks execute the added bytes, both stock mode setters,
stock re-enumeration and stock device-descriptor getters. Only analog register
access, delay, the unrelated stock slider task and vendor handlers are trapped.
Analog pull-up transitions and the 200000-microsecond delay are asserted. Stock
unexpected erase/reboot/power-off calls and memory writes fail the tests. The stock settings-save boundary is trapped and checked for save-on-change and preservation of the Bluetooth bit.
The suite also checks USB/power gates, exact hold duration, interrupted holds,
release latch, same-mode requests, both traffic routes, quiet interval, write
lock, timer wrap and unplug/replug. It does not execute the complete startup,
button-scan hardware, USB interrupts or a host driver.

Before physical testing: review the installed tweak set and all hook/hash
changes, verify update-channel behavior in both USB identities and
prepare the controller's existing backup and bootloader recovery path. Do not
interpret the protocol simulator or these instruction tests as physical proof.

The first USB mode payload is retained as `usbmode01.bin` only for identification
and upgrade. Reinstalling the same checkbox writes the corrected payload even
when the set of selected tweaks is unchanged. Factory Wireless USB D-input uses 301b; the current tweak uses the wired variant's 301d PID for Steam compatibility.

## Steam HIDAPI compatibility

The USB D-input tweak now changes only the USB D-input descriptor PID at
`0x17afe` from `301b` to `301d`, the PID already used by the original firmware's
batteryless wired D-input descriptor. Bluetooth and XInput identities are unchanged.
The USB report descriptor, input packet packing and update interface are unchanged.
Earlier USB-mode images with PID 301b remain recognized and can be upgraded.
Reinstall the USB D-input tweak using the updated app to apply the change.

Reason: Steam's local controller log selected HIDAPI for USB 301b with the same
mapping as Bluetooth. The [SDL 8BitDo driver](https://github.com/libsdl-org/SDL/blob/main/src/joystick/hidapi/SDL_hidapi_8bitdo.c)
selects by VID/PID and assumes the Ultimate 2C Bluetooth layout: Guide is bit 4
of byte 9. In stock USB D-input byte 9 is LT, so pressing LT can look like Guide
and other buttons. Steam's Guide chord configuration can then open the keyboard
or switch windows. A generic HID/SDL tester can still decode the descriptor
correctly. PID 301d avoids that Bluetooth-specific driver selection.
This is verified at image/instruction level; Steam behavior on physical hardware
still needs confirmation. Steam may ask for a new generic-controller mapping.

Dongle D-input remains 301c. Its separately saved Steam mapping is independent
of the USB controller mapping. The observed manual mapping in the local Steam log
used digital trigger buttons and overlapping left-stick axes; a new Steam mapping
must be calibrated for this device rather than copied from Bluetooth.

## Persistent USB mode

The 0xa414 call now reaches `startup`, which preserves the complete original
0xf014 initializer and reapplies the saved USB bit using the real stock mode
setters. The choice at RAM 0x843915 (record 0x73000 +5), bit 0, is saved through
0xd538 only when changing modes. Other bits, including Bluetooth bit 1, are kept.
No additional save occurs on startup or a repeated same-mode gesture.
The user confirmed physical USB B/X switching and both motor tests before this
persistence addition; cold-start persistence still needs physical confirmation.
Previous USB payloads and the original startup call remain recognized for upgrade.

## Read replies while playing

D-input input reports and vendor read replies share an IN endpoint. Stock
0xeb04 returns zero immediately when the endpoint is busy, and the read handler
at 0xe414 ignored that failure. The USB tweak now hooks only that read reply:
a failed send in configured D-input is retried at 1 ms intervals, at most 17
attempts (16 ms waiting). The original packet and length are preserved. Success
returns immediately; unconfigured USB or XInput never waits. No write/erase
command is retried. Instruction tests cover immediate success, busy intervals,
a permanently busy endpoint and disconnect/XInput gates.
