# Diagnostics

The Diagnostics tab helps you check how macOS sees the controller after installing tweaks, and gather information if something goes wrong.

- **Controller test:** shows what macOS's GameController reports, which is what a game gets, including the battery when macOS provides it. Press each button, stick and trigger to see it light up, and make the controller vibrate either the way a native game would or by sending the report straight to the controller. If only the direct report vibrates, the firmware isn't the problem.
- **Firmware details:** what the app read from the controller or the dongle, including where the version came from, whether a firmware update is pending, and how the bootloader identifies itself.
- **Problem report:** a text you can attach to a bug report.
- **Battery log:** since 1.1, firmware installed with Processor rest writes down the battery's voltage and level once a minute while the controller runs on battery, in a part of its memory nothing else uses. The log stays on the controller through power-offs and holds about 270 hours; then it stops. Restoring your backup leaves it as it is. **Save Battery Log** reads it by cable and saves it as a CSV file in the app's folder, so you can send it with a problem report. It helps make the battery percentage more accurate.
- **Install Firmware 1.09 Anyway…:** the last resort for a controller whose firmware can't be identified.

The controller test is only available in the Mac app for now, because it uses macOS's GameController directly. Everything else in the tab also works when you run the app from source.

## USB / dongle D-input motor tests on Windows

Diagnostics can test both motors or each motor separately using the stock vendor
HID output report `81 11 04 08`, without switching to XInput or installing a new
rumble firmware tweak. This covers controller USB D-input (301b/301d) and the
paired dongle D-input (301c, requiring its existing mode-restoration tweak).
Bluetooth D-input is not included. A unique matching USB device and browser
controller are required. Each test lasts 450 ms and sends an explicit zero-strength
stop, also on interruption; leaving the tab requests an early stop.

This tests the motors directly. It does not add DirectInput force-feedback support
to games, Steam or the browser Gamepad API. The UI labels this **Test Both Motors**
rather than **Vibrate Like a Game**.

The controller's original v1.09 parser is at 0xf684, called from 0xf84e; bytes
7 and 8 go to motor_set without an XInput-mode gate. The receiver v1.03 parser
at 0xe078 accepts the same header, records strengths at RAM 0x842f15/16 and
prepares forwarding. Bytes 5/6 contain its timing value; the diagnostic sends 450
and still explicitly stops from the host. No firmware writes or update commands
are sent. `tools/check_hid_rumble.py` executes both original parsers offline;
radio delivery and physical vibration still require hardware testing.

Older USB mode-switching patches conservatively lock B/X + Home after any unknown
vendor command, including this motor test, until restart. The updated USB D-input
patch exempts only the exact motor header `81 11 04 08`; the 2-second quiet period
and all write/update locks remain. Reinstall that tweak with the current app to
retain switching after a vibration test. The motor test itself needs no new patch.

D-input diagnostics show independent L4 and R4 checks (button bits 2 and 5),
including Bluetooth, USB and dongle reports. The extra checks are hidden in
XInput; they are omitted from its completed-control count.

Windows diagnostics can test the authored Bluetooth D-input output report with
Bluetooth mode selection and Rumble over Bluetooth installed. The helper checks
report sizes/IDs and the vendor output usage before writing. Each test lasts
450 ms and always sends stop, including interruption. This is a toolkit motor
test, not automatic D-input force feedback support in games. Updating from the
old one-byte vendor output requires re-pairing the D-input profile once.

### Battery reading over USB (Windows and Linux)

Diagnostics asks the controller itself through the normal USB vendor channel
(`81 05 00 5b`); this path is OS-independent. The browser calls the local
`GET /api/battery` endpoint. It first verifies the controller's identity with
an ordinary flash read, and never forwards this query to a receiver posing as
a controller. The response can include the firmware-reported percentage,
voltage, charge-full latch, and external-power flag.

**Firmware limitation:** this command is not part of stock firmware 1.09.
Some builds contain it, but the currently retained known-good D-input vendor
hooks do not route to the new battery handler. In that case Diagnostics must
show *unavailable*, not invent a battery percentage. Do not restore the newer
vendor hooks merely for battery display: they caused D-input rumble and
bootloader-entry regressions on hardware. A separate firmware fix is needed,
with on-device regression testing, before USB reporting can be guaranteed.

When the controller is plugged in as an Xbox 360 and the OS hides the vendor
interface, this app cannot read the firmware's battery command either. Use
USB D-input or update mode for firmware operations; do not confuse the dongle
with a direct USB controller. No validated battery query is currently known
for the Ultimate 2C 2.4 GHz receiver. The application does not send battery
queries to that receiver or infer percentage from unrelated bytes.
