# Bluetooth-style Steam input mapping for USB and 2.4 GHz DInput (experimental)

Steam recognizes the Bluetooth DInput profile for the Ultimate 2C, including
L4/R4, but on USB and the 2.4 GHz receiver it asks the user to assign all
buttons. Merely switching the USB PID back from `301D` to `301B` is **not**
correct: the Steam/SDL HIDAPI parser may then apply the Bluetooth byte layout
to the original wired layout. This previously caused LT to be interpreted as
Guide/Home. These optional firmware patches change both the **DInput report
layout** and the HID report descriptor together.

## USB controller (firmware 1.09)

Choose **USB XInput / D-input switching** and the optional **USB DInput:
Bluetooth-style Steam mapping (experimental)**. Existing Bluetooth/XInput
reports, battery commands and vendor reports 0x02/0x81 remain unchanged.
The USB DInput PID becomes `2DC8:301B` instead of the earlier tweak's
`2DC8:301D`.

- Original input ID 1: `buttons_lo, buttons_hi, hat, lx, ly, rx, ry, lt, rt`
- New input ID 1: `hat, lx, ly, rx, ry, lt, rt, buttons_lo, buttons_hi`

The 145-byte HID report descriptor at `0x17B74` is reordered without changing
any field size or the vendor/output reports. The packer at `0xEDE8`, called by
the USB DInput input task at `0xF1CC`, writes ID 1 and its 9-byte payload to
USB's FIFO at `0x80011C`. The 4-byte call at `0xEF88` transfers control to the
44-byte helper at `0x19F80`; that helper reorders the payload in place before
the original byte-copy loop. All paths still send the same report length.

The TC32 emulator exercises the **actual stock firmware USB FIFO callsite**
with the old and the new firmware images, not just the helper in isolation.
For every tested gamepad state, the report ID stays `01` and all nine payload
bytes are reordered exactly.

Older USB DInput images that identified themselves as PID `301B` but used the
original layout remain identifiable, so they can be upgraded in place.

## 2.4 GHz dongle (firmware 1.03)

Choose **D-input mode with B + Home** and **DInput: Bluetooth-style Steam
mapping (experimental)**. Only the connected DInput device PID changes from
`301C` to `301B`; idle remains `301C` and XInput remains `310A`. The descriptor at `0xF334` matches the controller's new USB
DInput descriptor.

The actual stock DInput handler is stored at image address `0x74E8` and
executes from RAM address `0x14E8`; it creates a 9-byte report and sends it
through the RAM call target `0x1288`. Its mode check at image `0x773C` limits
the path to DInput. The patched branch stored at `0x7676` executes at RAM
`0x1676`, and correctly calls 44 bytes at flash `0xFFB0`, which perform the same
payload reorder. The stock XInput output path, vendor commands and 28-byte
battery telemetry read remain untouched. The TC32 emulator executes the
**real report generator** with the old and patched dongle images.

On USB, a controller and the dongle can now both use `2DC8:301B`. Their
`bcdDevice` values remain different (`0x0001` and `0x0200`, respectively) and
Toolkit uses this distinction to avoid treating a dongle as a controller when
reading firmware or battery. Never assume the PID alone identifies the device.

## Installation and hardware checks

1. Make a fresh backup of **both** controller and dongle in Firmware Toolkit.
2. Select the corresponding optional patch(es) and use **Check First** before
   **Install Tweaks**. Do not install a dongle patch while it is paired and
   active; switch the controller off first.
3. Reconnect USB/reinsert the dongle to force Windows/Steam to enumerate the
   new PID and HID descriptor.
4. Check in **Steam > Settings > Controller > Test Device Inputs** that A/B/X/Y,
   D-pad, both sticks, both triggers, Home, L4 and R4 map as expected. Verify
   vibration and battery reporting separately.
5. If the device behaves incorrectly, disable only the optional Steam mapping
   tweak (retaining DInput mode and your other working tweaks), or restore the
   saved firmware backup.

**These patches are physically unverified.** Having the same HID data order
and VID/PID as the Bluetooth profile does not prove Steam will reuse the same
logical mapping: Steam may incorporate transport type, bcdDevice or other
metadata when selecting a profile. The point of this experiment is to verify
that behavior, not to claim it is guaranteed.

## 2.4 GHz executable RAM callsite fix

The dongle's mode-1 report packer at image address `0x74E8` is **copied to
RAM and executed at `0x14E8`** (the first bank segment has a `0x6000` load
offset). The `dongle_dinput_steam` branch stored at image address `0x7676`
therefore executes at `0x1676`. The initial experimental recipe incorrectly
encoded that BL with PC=`0x7676`, so the CPU jumped to `0x9FB0` rather than
the helper at `0xFFB0`. This can fault the report task shortly after the
receiver connects in DInput, and host USB enumeration cannot be relied upon.

The corrected hook targets `0xFFB0` **from runtime PC `0x1676`**, preserving
the existing helper, descriptor and PID changes. The regression test now
executes the real report packer from its RAM address `0x14E8` instead of the
flash image's `0x74E8`. USB cable DInput has a separate implementation and
was not affected by this dongle-specific callsite issue.

This diagnosis is verified by decoding the binary branch targets and executing
the report-packer in a TC32 interpreter. Behavior on physical hardware still
requires confirmation. No additional USB reconnect hook is used.
