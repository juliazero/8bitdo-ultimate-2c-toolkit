# Bluetooth mode selection (experimental)

Install **XInput mode over Bluetooth** and its subordinate
**Bluetooth mode selection** tweak. From fully off, with the rear switch in
Bluetooth, hold B and press Home for generic D-input, or hold X and press Home
for Xbox. Home alone restores the last choice. The initial choice is Xbox.
Holding both B and X keeps the saved choice. While already on battery, hold Home + B or Home + X for about 3 seconds to
restart automatically in that profile. Release Home before another switch.
The shortcut also restarts a 2.4 GHz connection with B/X still held; D-input
there requires the receiver's D-input restoration tweak. Home alone and the
paddle/update chord retain their stock handling. USB retains its separate tweak.

Xbox means Bluetooth HID reports/identity compatible with Xbox Series X|S,
not a newer version of USB XInput. The Xbox profile retains the existing input
packer, rumble, Long rumble and Home ring features. The generic D-input profile
uses the original input layout, including the paddle bits, with an authored HID
map and a separate PnP ID/name. Its vendor output report 2 now has an eight-byte Xbox-compatible payload,
forwarded to the same guarded output callback as Xbox. With Rumble over Bluetooth
installed, Windows diagnostics can test the motors directly. This is not generic
D-input force feedback for games. The HID descriptor changes, so remove and pair
the D-input profile once after upgrading; Xbox pairing need not be removed.

Each profile has its own Bluetooth identity and pairing journal. D-input keeps
the factory address and journal at 0x7c000..0x7e000. Xbox derives a distinct
address by XORing address byte 5 with 2 and uses 0x3b000..0x3d000, two sectors
that are erased in verified stock. The factory address in flash is unchanged.
After installing this revision, pair each profile separately once. The host can
then retain both pairings. The new Xbox address has no old D-input keys.
The Xbox journal lies inside the normally protected flash area. Every Xbox
cold start now calls the original 0xbd00 flash-unlock routine before the SDK
mounts this journal. Without it, first pairing could work after a settings save
but reconnect after restart failed. D-input retains its original flash path.

## Firmware layout and persistence

The application erase/read/verify span is now **0x6000..0x1a000**, one sector
larger than before. 0x19000..0x1a000 was blank in the verified stock image.
Original payload size/write parameter stays 0x12400; bootloader, bank B,
update record, battery log, calibration and original bond regions are unchanged.
The SDK manages the additional Xbox bond journal separately from the app span.
Restore clears the additional sector as well. Old 0x13000-byte app-area files
remain recognized by padding the new sector with erased bytes.

The additional sector contains a derived 51-entry GATT table at 0x19000,
profile data at 0x194c8, compiled TC32 code at 0x19540, and the
120-byte authored D-input map at 0x19f00. The table is generated from the
already-patched original table with explicit profile fixups; name/OTA fixes
and common services remain intact. No vendor table binary is shipped.
Identification reconstructs the expected copy and rejects changed bytes.

Only **two RAM bytes, 0x8416d2..0x8416d4**, are needed: mode and hold state
in the alignment halfword after the unused
0x16d0 trampoline and the 16-bit ring level. The live radio routine at 0x16d4
is preserved. No large table is copied into RAM and the stack is not relocated.
The settings record's byte +5 uses bit 0 for USB D-input and bit 1 for Bluetooth
D-input. Zero means Xbox for each transport. Startup reads but does not save;
a changed explicit choice uses the original 0xd538 settings journal once.
The record is loaded by stock initialization before the startup hook.

Hooks preserve existing callbacks: 0xa4b8 forwards the original indirect boot
callback through 0xa4ec after capturing physical B/X; 0xa318 selects the GATT
table; 0xa216 chooses scan-response name/length; 0xa2e4 dispatches the input
packer. Mode selection completes before the GATT table and advertising initialize.
The raw physical word is used, so game button remapping does not change B/X.

## Validation

`python tools/check_bt_modes.py --backup PATH` executes actual compiled wrappers,
the original initial matrix scan, the original Xbox encoder and the authored
D-input encoder in the TC32 interpreter. Cases cover startup B/X/no modifier/both,
independent persisted USB/BT bits, save-on-change, both GATT descriptions,
names/PnP IDs, distinct stable addresses, separate SDK bond journal bases, and exact D-input button/hat/axis/trigger bytes compared against
the factory encoder (with the battery byte now reporting the actual percentage).
The full ring/settings/CPU rest/log/USB/Long rumble/fixes combination is checked.
The original indirect boot callback, GPIO row reads, notification transport and
settings writer are trapped; this is not a full BLE/radio/electrical emulator.

These tests and native GUI tests do not prove physical Bluetooth interoperability.
The user confirmed both profiles pair and accept input on the controller.
This revision's Xbox reconnect fix, moving-stick USB read fix and live wireless
switching still require physical validation. The resident bootloader is not modified.

## Nintendo Y profile

The Switch 2 Pro profile is experimental and separately documented below. Its BLE interface and key
exchange are proprietary, rather than standard HID-over-GATT plus SMP. A complete
implementation needs the GATT endpoints, command/response initialization,
input encoding and console pairing; changing the name or VID/PID is insufficient.
Primary reverse-engineering reference:
https://github.com/ndeadly/switch2_controller_research/blob/master/bluetooth_interface.md

## RF switching revision

Live RF switching now saves stock record byte +7 (1 for D-input, 0 for Xbox),
and clears byte +8 for Xbox, matching stock Home power-off at 0xd8e8. The previous
reboot-only shortcut omitted this. Hook 0xa410 waits one second before the original
RF initialization at 0xdb14, allowing the receiver's old link to expire. The stock
boot path has disabled the watchdog; the optional watchdog arms on its first
main-loop poll. This adds no flash or radio register writes during the delay.
The physical receiver timeout and resulting USB re-enumeration require testing.

The original Switch Pro uses Bluetooth Classic (BR/EDR), while this controller's
B80/TLSR8208 supports BLE and proprietary 2.4 GHz. A native Classic Switch Pro
profile is not a feasible small firmware tweak for this hardware. References:
https://github.com/ricardoquesada/bluepad32/blob/main/docs/supported_gamepads.md
https://www.telink-semi.com/products/bluetooth-le/tlsr8208

The default Xbox-profile name is now `8BitDo Ultimate 2C (X)`, matching `(D)`.
An existing default `(X/S)` migrates during reinstall; custom names are retained.
This renames the profile only: the Xbox-compatible BLE report protocol is unchanged.


## Experimental Switch 2 profile

In Tweaks, enable **Bluetooth Y: Switch 2 Pro Controller (experimental)** and use
the normal Install button. The toolkit selects the required Bluetooth mode
selection, Dimmer LEDs, Controller settings and Processor rest options. Existing
other choices are kept. To remove Y, uncheck it and install again; the extra code
sectors are erased and normal D/X behavior is restored.

With USB disconnected and the rear switch on Bluetooth, hold Y while pressing
Home from power-off. Select Y at every startup; Home without Y restores the saved
D/X mode. Y has its own Bluetooth address and pairing record. This is the Pro 2
BLE experiment for Switch 2, with buttons, sticks and battery reporting. It has no motion, NFC or audio support and has not been verified on a
Nintendo Switch 2 console. The bundled `bt_switch2` tweak now includes the
compiled HD-Rumble-to-ERM translator in its `switch2-*` firmware blobs; ordinary
Windows builds install these precompiled blobs and do **not** need TC32.

On this experimental profile, Home is read from the physical Home button
register (`0x84344c`, bit 17) so pressing Start + Select does not synthesize
Home. ZL/ZR retain their original digital trigger thresholds.

Vibration reports on GATT `0x0012` (42 bytes) and combined reports on `0x0016`
are mapped onto the two 8BitDo ERM motors. Bare Nintendo command requests on
`0x0016` are also accepted during initialization. A 20-tick bounded motor
hold bridges normal BLE report spacing; explicit zero-amplitude reports stop
the motors. This only approximates HD Rumble and requires physical validation.

### Y profile: LT/RT behave as digital ZL/ZR

The experimental Y profile imitates a Nintendo Switch 2 Pro Controller, whose
ZL/ZR inputs are digital buttons rather than analog axes. In
`firmware-src/switch2-input.c`, `y_input_from_controller()` reads the stock
processed button word (`0x84342c`) and maps its trigger bits into the Y report.
It does **not** read the analog LT/RT bytes (`0x84340e` and `0x8433e8`). The
stock button scanner asserts those digital trigger bits when the analog travel
crosses its threshold. Consequently, pushing LT/RT to roughly halfway produces
an abrupt 0-to-1 transition in Y mode. This is expected for the emulated
Nintendo profile, not a loss of analog precision in the controller's sensors.

To get the full analog 0–100% range, use the Xbox-compatible X profile, which
already transmits both LT/RT values. **Nintendo's Pro Controller 2 HID report has
no analog trigger fields**, so implementing an interpolated 0–100% response in
this Y profile would not be interpreted by the host. The Switch 2 NSO GameCube
controller supports analog triggers, but using its report format also requires
reimplementing the matching BLE identity, HID characteristics and initialization
sequence; simply writing two bytes into the Pro report is incorrect. See
https://github.com/ndeadly/switch2_controller_research/blob/master/hid_reports.md.
Any GameCube-compatible Y variant would need separate integration and
hardware/console verification before flashing firmware.

### Experimental Y: Start+Select and vendor vibration commands

The Switch 2 Pro profile does not report Home while both Start and Select
are down, even if the stock firmware asserts the Home bit in its button
word. This deliberately masks the unusual three-button Home+Start+Select
combination as well. The normal physical Home button remains mapped.

In addition to GATT `0x0012` HD Rumble reports, Nintendo sends vibration
vendor commands on GATT `0x0016` (`0x0A/0x02` for predefined samples and
`0x0A/0x08` for its separate vibration envelope). The selectable `bt_switch2`
firmware now recognises both commands: sample IDs 0 through 7 are approximated
with bounded conventional motor pulses; `0x0A/0x08` is acknowledged but its
undocumented 20-byte envelope is **not** interpreted as HD Rumble. This does
not prove that all Switch2Connect rumble tests will work. The real BLE input
and vibration command path must be tested on physical hardware.

The build ships the compiled Y blob in `u2c_customfw/firmware/blobs/`; normal
Windows application builds do not require TC32 or a private `build/` directory.
