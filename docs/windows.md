# Windows

Requires Windows 10/11 and Python 3.12 or later. From the repository directory in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m u2c_customfw.web
```

Open the local URL printed by the server in Edge or Chrome. For the command line:

```powershell
.\.venv\Scripts\python.exe -m u2c_customfw --help
```

The Windows backend uses HIDAPI with the built-in Windows HID driver. No Zadig,
WinUSB replacement or administrator access is required. Close Pad8Lab and other
firmware tools before connecting. HID usage pages select the vendor update interface;
the normal Xbox gamepad interface remains with its system driver.

With the wired Xbox 360 tweak, Windows can bind `xusb22` to the entire USB
device and expose only a synthesized input HID collection (usage page 1).
In that case the toolkit displays **XInput only**: gamepad diagnostics work,
but the running firmware channel (usage page `0xff7a`) is unavailable. Pad8Lab's
HID transport also requires that vendor collection; seeing an XInput gamepad
does not imply firmware access.

To access the controller firmware in this state, unplug USB, hold Home for about
12 seconds to turn it fully off, then hold **LB + RB while plugging USB back in**.
The small light below the Home ring blinks white. The bootloader uses a separate
HID identity and can be used for backup, firmware identification, installation and
restore. The Xbox 360 tweak remains installed unless you choose to change it.
For a paired dongle in XInput mode, turn the controller off and reconnect the
dongle on its own. No system-driver replacement is performed.

When the dongle restarts into or out of its bootloader, HIDAPI may raise
`OSError: read error` because the old USB interface disappears. The toolkit
tolerates that exact Windows error only at those restart commands, then checks
for the expected device. Before erasing, it still verifies bootloader identity
`0x301c`, the bootloader/calibration against the backup and the staged-update
record. Read errors during ordinary memory access or verification remain errors.
If an earlier version stopped immediately after entering bootloader, restart the
toolkit and use Restore from that mode; the device need not be forced back to
normal mode first.

Connect by USB for firmware operations. Start with device detection and a backup;
the existing backup requirements and firmware safeguards also apply on Windows.
Bootloader reports include the zero report ID required by the Windows HID driver.
Bluetooth detection follows the bus metadata and BLE service path, as in Pad8Lab.
After changing the Bluetooth identity, remove the old pairing in Windows Settings
and pair the controller again.

User data is stored in `%LOCALAPPDATA%\u2c-customfw`. Keep the backup files.
The per-device operation lock prevents concurrent toolkit processes from sending
interleaved commands; it cannot lock out unrelated tools such as Pad8Lab.

Diagnostics use the browser Gamepad API. Focus the page and press a controller
button to expose it. The first controller with a standard mapping is selected;
disconnect other gamepads when testing. Stick, button and trigger input is available.
Stock Bluetooth (`2dc8:301b`) with the browser's 10-axis HID usage layout is
normalized to the regular graphical controller test: SDL-compatible button names,
four stick axes, separate triggers and the hat/D-pad. This is a UI mapping and
does not change the controller's Bluetooth identity. Browser-provided standard
mapping takes precedence. Other unknown layouts retain raw numbered diagnostics.
For the stock mapping, native nonblocking HID input supplies analog trigger values
directly from the report's Gas/Brake bytes (0–255), avoiding browser axis slots
that can be unused or expose a different range. Until a native report is available,
SDL's digital trigger buttons can highlight pressed controls, but are never used
to fill the analog bars. Missing analog data is labeled explicitly; a brief
network interruption retains a native sample for at most 3 seconds. Native input
is delivered over one continuous HTTP stream at roughly 20 ms intervals, instead
of a separate HTTP request per report. The page paints each streamed sample
immediately. A two-second inactivity watchdog reconnects a stalled stream;
explicit disconnects clear the sample immediately;
a connected device waiting for its first fresh packet does not clear a valid
sample. Only trigger values use this cache; browser sticks and buttons stay live.
This input path
sends no output commands to the controller.
Vibration tests prefer browser haptics when available (such as Chromium).
When the browser exposes input but no vibration actuator (such as Firefox),
XInput controllers can use the native Windows XInput API. Exactly one
XInput controller and one browser gamepad must be connected to avoid ambiguous
selection. The test activates the selected motor(s) at moderate strength for
450 ms, then sends a stop command. Native tests cannot queue behind another test,
and leaving/hiding the diagnostics page requests an early stop. Delayed native
start requests expire after 1.5 seconds; POST failures never replay the effect.
Firmware operations exclude vibration tests.
Other gamepads use browser haptics when available. The direct Bluetooth vibration
test is unavailable in this browser bridge.

For one connected Bluetooth controller, battery percentage is read from Windows'
cached device property (`System.Devices.BatteryLife`), following that controller's
HID parent chain. The UI refreshes it on connection and every 10 seconds in both
Firefox and Chromium. It does not invent charging/full status from a percentage
or convert XInput's coarse battery levels into percentages. Missing or ambiguous
readings stay unknown. Windows controls how frequently the underlying cached
percentage is updated by the controller.

The Home ring in Settings works over Bluetooth with the Xbox identity, Home ring
and Rumble over Bluetooth tweaks installed. The Windows bridge sends the same
marked 9-byte output report as the Mac app, via HIDAPI. No firmware update is
required just to adjust the light. Brightness changes are temporary unless Save
is selected. Bluetooth provides no acknowledgement of the applied brightness;
"Sent" confirms report delivery, not that the required tweaks are installed.
The stock Bluetooth identity lacks this custom output handler. For that mode,
change brightness by USB or with L4 + R4 when the Home ring tweak is installed.

Automated tests cover report framing, device selection metadata, timeouts and
process locking. A complete backup/flash/restore cycle on physical hardware still
needs validation on Windows; simulator success is not hardware validation.
The user has confirmed dongle firmware backup and successful controller firmware
installation from bootloader mode on Windows, including the restart out of
bootloader. The user also confirmed restoring the dongle to original 1.03 and
flashing the controller starting in normal mode.

### Backup performance

The Windows backend drains queued HID replies without waiting before each request.
Previously, each empty-queue check used a timed 1 ms read, which on the tested
Windows system added substantial scheduling overhead. A read-only bootloader
benchmark measured 128 requests at 1.98 seconds before and 0.51 seconds after
the change (3.86x faster), with identical data in four runs. This measures transport
throughput rather than the duration of a complete backup. Both full backup passes
and checksum verification remain enabled.

To reproduce with exactly one device in bootloader mode and no toolkit job running:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/benchmark_windows_reads.py
```

The benchmark only reads memory; it does not erase, write or restart the device.

The Windows HTTP server uses an exclusive listening socket and a bounded graceful
shutdown to reduce connection resets. Progress streams use HTTP chunk framing,
so their completion does not depend on a clean TCP close. The browser retries a
failed read request once; requests that trigger operations are never retried.
The UI accepts the task's terminal event immediately instead of waiting for a clean
stream close. If progress disconnects earlier, it polls the authenticated status
endpoint using that task's unique ID. This never starts the backup or flash again.

### Home ring brightness over Bluetooth

The optional **Read Home ring brightness over Bluetooth** tweak (`bt_ring_read`)
requires **XInput mode over Bluetooth** and **Connection and idle fixes**. For
DInput also enable **Bluetooth X / D mode selection**, and for writes enable
**Rumble over Bluetooth**. It repurposes the disabled BLE OTA service as a
read-only characteristic with two bytes (initialized flag and level 0–5).
After installing over USB, remove and pair the X and D profiles again so
Windows discovers the changed GATT table. The Windows Settings tab reads the
live level once on entry and provides **Read Ring Level** for changes made on
the controller. A Bluetooth save request is not an acknowledgement of a flash
write; power-cycle the controller to verify persistence. This feature does not
use the Nintendo Y profile, nor does it add support for the 2.4 GHz receiver.

## Dongle 2.4 GHz XInput battery interface investigation

The dongle v1.03 **already advertises three USB interfaces in XInput mode**.
Its configuration descriptor at firmware address `0xf228` (106 bytes) contains
interface 0 (`ff/5d/01`, Xbox 360), interface 1 (`03/01/01`, HID), and
interface 2 (`03/00/00`, HID) with interrupt IN `0x83` and OUT `0x06`.
The HID report descriptor for interface 2 is 33 bytes. These are properties of
the firmware image, not evidence of access through the installed Windows driver.

On Windows, the Xbox controller driver can bind to the whole device and hide
interface 2 from HIDAPI. **Do not replace XUSB with WinUSB, Zadig or another
driver** merely to read battery data; that could disable gamepad input and
rumble. Before making any experimental USB descriptor tweak, capture the actual
interface/driver topology in the working XInput profile:

```powershell
python -m tools.dongle_xinput_interfaces --output xinput-interfaces.json
```

This diagnostic **does not** send USB commands, alter device drivers or write
firmware. It lists HIDAPI paths and relevant Windows PnP device instances,
including parent, service and compatible IDs. A report with no vendor HID path
indicates that the current HID transport cannot access the battery packet;
it does not mean the dongle firmware stopped receiving telemetry.

In DInput 2.4 GHz, the existing read-only RF battery path already works. The
Toolkit's scheduled battery polling is still 60 seconds; the XInput feature
remains unavailable until a device interface can be safely opened without
interfering with XUSB.
