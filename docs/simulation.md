# Firmware laboratory

The Windows/Linux app can run without a controller:

```powershell
& '.\Firmware Toolkit.exe' --simulator
```

From source: `python -m u2c_customfw.desktop --simulator`. The default profile is
controller USB; change it in the panel. `--simulate PROFILE` is also supported.
Profiles: `controller-usb`, `controller-boot`, `controller-bluetooth` (D-input),
`controller-bluetooth-xinput`,
`dongle-idle`, `dongle-xinput`, `dongle-dinput`, `dongle-boot`.

The window title says Simulation. Each launch creates synthetic controller and
dongle flash and a temporary data folder, removed on exit. The real data folder
and HID transport are not used. The virtual panel drives buttons, sticks and
triggers in Diagnostics; vibration tests only light a virtual motor indicator. The
files produced here are synthetic and must never be installed on hardware.

All profiles start without a backup. Create a backup explicitly before testing
Restore or installation; fixture memory is not listed as a saved backup.

The normal app APIs, backup, firmware identification, checks and flash routines
run against this memory. Entering update mode changes the simulated USB identity;
the bootloader uses the same flash and restart returns to normal mode. In
`dongle-xinput`, discovery deliberately returns the ambiguous controller USB ID,
so the app must recognize the receiver from its memory.

`--disconnect-after 100` makes the device stop answering after 100 protocol
requests. This is useful for testing failure handling; it does not simulate radio
quality, scheduler timing or all electrical behavior of a flash chip.

This laboratory models the protocol. It does **not** execute new firmware code:
a successful simulated install proves that the app constructed, wrote and read
back the image, not that the patch works on the controller.

## Virtual controller panel

The simulation window has two device categories: **Controller** (USB - Update mode, USB,
Dongle 2.4 GHz, Bluetooth) and **Dongle** (USB - Update mode, USB - Idle).
XInput/D-input is not a connection choice. Select Dongle / USB - Idle, install
the D-input tweak, then select Controller / Dongle 2.4 GHz. Hold B, power off,
and power on while B remains held to enter D-input. Use X the same way to
return to XInput. Without a modifier, the remembered mode is restored. Without
the dongle D-input tweak, startup stays in XInput. Diagnostics shows the active mode.
Both firmware images survive changing the selected device and connection.

The rear switch has **2.4 GHz** (default) and **Bluetooth** positions. Its choice
is retained across app launches in `simulator-settings.json`; firmware and backup
files remain isolated in the temporary simulation folder. Selecting a wireless
connection also updates the rear switch. While USB is attached, changing the
switch does not change the active connection. **Disconnect USB** replaces Power
in normal USB and controller update mode. It disconnects the USB interface,
waits two seconds, then uses the selected wireless transport. Power can then
turn the controller off; turning it off during the wait cancels reconnection.

The panel includes buttons, stick/trigger sliders, Power and Release All Buttons
(also releases both triggers). A pointer press holds a button until release;
double-tap within 350 ms latches it. Tap a latched button to release it.
Focus loss releases momentary holds, retaining deliberately latched buttons.
Keyboard Space/Enter also supports momentary holds.

Hold Home for 12 seconds without USB to power off; held B/X survives the power
cycle. This models the documented full shutdown duration, not all stock power
management. Home + L4 + R4 held for 3 seconds enters Controller / USB - Update
mode. Connecting Controller / USB with L4 + R4 (or LB + RB) held also selects
update mode. The Home/paddle gesture is user-reported hardware behavior; its
three-second threshold is a model choice, not a firmware timing measurement.
Home ring Save persists in simulated flash and survives power cycles.
By default Bluetooth identity is derived from the installed firmware: original
firmware is D-input, and Xbox requires the bt_xbox tweak. Choosing a transport
profile does not install a missing tweak. Starting the explicit
`--simulate controller-bluetooth-xinput` fixture seeds that tweak.

Bluetooth startup has no experimental override. Its identity is determined by the
installed firmware; holding B/X does not add a protocol missing from that image.

This panel runs a Python behavior model, not the TC32 firmware. Neither clicking
a mode nor passing these model tests proves that the corresponding patch exists.
Settings saved by the simulated firmware persist in its shared flash across
channel reopenings and virtual power cycles. Each new app session starts fresh.
L4/R4 start unmapped. Virtual input applies live/saved button remapping before
back-button expansion, plus modeled stick/trigger shaping. Settings Live View
receives the same processed axes as Diagnostics. Backup also works in the dongle bootloader profile.

## Instruction-level firmware tests

There is a separate limited TC32/Thumb interpreter in
`firmware-src/test_longrumble.py`. It executes actual instruction bytes of the
installed rumble routines and models the motor hardware. It is not a complete
SoC, Bluetooth stack or dongle emulator.

Run it against the verified original bank in one of your controller backups:

```powershell
python tools/check_firmware_emulator.py --backup 'C:\path\controller-backup.bin'
```

The helper accepts only a full backup containing the known original firmware
1.09. It uses only the application bank, not calibration or pairing data, and
never opens a device. Tests cover the existing long-rumble patch, not the proposed
Bluetooth mode selector.

## Bluetooth X/B + Home investigation

Requested Bluetooth behavior: from off, hold B and press Home for D-input;
X + Home selects Xbox, and Home alone restores the last selection. This needs
a new firmware patch. The removed experimental override is no longer available.

Original controller firmware 1.09 contains a USB mode selector, but its physical
B/X entry sequence is **not validated**. A physical attempt remained in XInput.
The earlier test injected the button word and variant flag before calling `0xf014`;
it did not execute complete startup and cannot prove how to enter the mode.
The simulator does not allow USB power-off or force USB mode from B/X.
Use Disconnect USB before powering off.
Firmware-command restart is separate from the physical Power control.

Stock Wireless USB D-input uses **2dc8:301b**, sharing its PID with Bluetooth
but using a different report layout. The current `usb_dinput` tweak uses the
stock wired variant's **301d** PID to avoid Steam's Bluetooth HIDAPI decoder.
Dongle D-input is 301c. Both USB controller identities are recognized by the app.
`python tools/check_usb_startup.py --backup PATH` checks the original startup
selector; the working runtime B/X + Home patch is described below.

A standalone 26-byte selector prototype (`firmware-src/btmode-selector.hex`)
runs in the TC32 interpreter: B selects D-input, X selects Xbox; no modifier preserves the supplied saved
mode, defaulting to Xbox only if unset. Persistence is the caller's responsibility.
Run `python tools/check_bluetooth_selector.py` to check its instruction bytes.
This only proves the leaf routine; it has no startup hook and does not change
Bluetooth identity or reports. It is not included in firmware recipes.

No installable mode-selector patch exists yet. Current `bt_xbox` changes both
the report packer at `0xb908` and the GATT Report Map length/pointer at
`0x177a0`/`0x177a8`, plus PnP ID, report references, appearance and device names.
A selector must choose matching identity, descriptor and report encoder before
advertising; changing the label or one mode flag is insufficient.

There is also an existing compatibility constraint: Long rumble reuses the stock
D-input Report Map area at `0x1734c`. A dual-mode build cannot simply point back
to that area while keeping Long rumble; it must preserve or relocate the stock
map. The boot-time button scan and initialization hook have not yet been
validated in the interpreter. Pairing/descriptor caching must also be checked on
actual hosts after switching profiles.

Next firmware work: locate and execute the startup/button-scan path, identify a
hook before GATT initialization, preserve both complete profile descriptions,
and add instruction-level tests for B, X and no modifier. The protocol laboratory
can exercise the host-side consequences but is not evidence of that boot logic.

## USB runtime mode prototype

See [USB D-input switching](usb-mode-prototype.md) for the experimental
`usb_dinput` recipe and B/X + Home tests. Install it on the simulated controller
through the normal backup/install flow, then hold the gesture in Diagnostics.
The protocol model follows installation/removal; instruction tests separately
execute the real code. The user confirmed B/X + Home switches both directions
on hardware. The newer 301d Steam compatibility change still needs a physical test.

The dongle has Connect USB / Disconnect USB in idle and update modes. A separate
Connect dongle / Disconnect dongle button also works while testing the pad.
Unplugging the receiver removes its firmware/input interfaces without turning
off the controller or losing either firmware image. Reconnect restores discovery.

## Battery test values

The panel exposes independent battery percentage (0..100) and voltage (0..5000
mV), preserved across connection changes within a session. Diagnostics receives
the percentage/voltage; USB derives external power and charging indication.
These values do not model discharge, charging physics or hardware shutdown.
The instruction-level USB checks seed factory RAM fields `0x843415` and
`0x843424` and cover 100/50/15/5/0 percent at 4200/3700/3300/3000/2700 mV.
No automatic percentage-to-voltage curve is assumed.

The ring gesture model follows the installed code: updated firmware requires
five seconds of L4 + R4, older accepted firmware keeps the immediate behavior.
The compiled-code check is separate from this Python model.

## Installed Bluetooth selector and persistent USB

The `bt_modes` subordinate tweak now implements real startup B/X selection,
with Xbox as the first-use default and Home alone retaining the saved choice.
The model follows installation/removal and the saved settings byte, separately
from USB's persisted mode. It does not emulate pairing/host descriptor caching.
See [Bluetooth modes](bluetooth-modes.md) for instructions, memory layout,
physical-test status and actual instruction-level tests. The earlier standalone
leaf prototype and investigation sections are historical; `bt_modes` supersedes
their statement that no installable Bluetooth selector exists.

With the current Bluetooth mode selection tweak installed, hold Home + B/X for
3 seconds while connected through Bluetooth or 2.4 GHz. The model restarts and
reconnects after 2 seconds, retaining each profile's choice. The receiver still
needs its D-input tweak. A startup chord must be released before another switch;
Home alone and the update chord retain their handling. This models the new
compiled hook; it does not simulate over-the-air pairing or encryption.
