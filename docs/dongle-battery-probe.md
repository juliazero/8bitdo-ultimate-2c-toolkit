# 2.4 GHz receiver battery investigation

This is a **read-only investigation tool**. It does not change firmware and
cannot prove that the raw 2.4 GHz radio protocol contains battery data.

On Windows, with the dongle connected in **XInput** mode:

```powershell
python -m tools.dongle_report_probe --xinput-battery
```

Windows may report the dongle as wired even though the controller itself is
wireless. Such a result is **not** a controller battery measurement. XInput only
provides broad categories, never a percentage. If multiple controllers are
connected, the XInput user index may not correspond to this dongle.

In **DInput** mode, capture HID input changes (move each stick, press a button,
then release; optionally compare charging and unplugged states):

```powershell
python -m tools.dongle_report_probe --seconds 45 --output dongle-reports.jsonl
```

If no interface is found, do not change firmware; the installed driver or
selected mode may not expose a compatible HID interface. The capture contains
normal gamepad input reports, **not** confirmed RF packets. The dongle's 1.03
DInput report layout is 10 bytes and has no documented battery field. Further
research will require either confirmed vendor status commands or a controlled
firmware-side capture of the RF state, with verified bounds and an explicit
rollback route.

## Experimental RF-buffer snapshots (no firmware changes)

The factory dongle's mode-switch handler reads the first byte of the most
recent radio packet from RAM address `0x843108`. This is grounded in
`firmware-src/dongle.s`, **not** a decoded battery field. The existing vendor
`81 05 05` read command routes through the flash-reading function at
`0xCD94`, so it does **not** read RAM on stock firmware. The optional
**Diagnostic RF packet read via USB** tweak intercepts just `0x843108` with a
length up to 32 bytes and copies that memory without disturbing any other
read commands. Install it from Firmware Toolkit while keeping the DInput tweak,
then pair
**one** dongle in DInput mode and run (with the controller connected):

```powershell
python -m tools.dongle_rf_probe --seconds 20 --output dongle-rf-snapshots.jsonl
```

The tool first verifies that the `0x10000` firmware address is blank, as it is
on the receiver; it then attempts read-only snapshots at `0x843108`. It does
not issue session start, write-unprotect, update, or flash commands. If it returns only `FF` despite moving sticks, verify the optional tweak was
installed and that the dongle was reconnected. The tool cannot determine
whether any particular byte represents a battery reading.

The output is raw and may contain control inputs; it does not reveal a
reliable battery percentage until packet fields are identified and validated
across different charging states.

## Installing and uninstalling the RF diagnostic tweak

From **Firmware Toolkit → Firmware → Dongle**, select **Diagnostic: read
RF packet over USB** and retain **D-input mode with B + Home** if you
currently use it. Turn the controller off so the unpaired/idle receiver is
visible, save a fresh backup, use **Check First**, then **Install Tweaks**.

**This is an experimental firmware change.** It has been compiled and tested
with a TC32 instruction emulator, but not tested on real receiver hardware.
Flashing can still fail; keep your original 128 KiB backup available. The
standard firmware restore procedure can recover the receiver if it enters
bootloader mode. After installing, power the receiver and controller back on,
select DInput, and execute `python -m tools.dongle_rf_probe --seconds 20`.
A single all-FF snapshot is treated as inconclusive rather than success.

After testing, deselect **Diagnostic: read RF packet over USB**, retain your
other selected tweaks, and reinstall. This restores the original flash-read
call and removes the 40-byte diagnostic procedure without affecting the
DInput or Xbox-identity patches.

The receive buffer might contain packet state and may be overwritten during
readout. No claims are made about a battery percentage or data stability.
