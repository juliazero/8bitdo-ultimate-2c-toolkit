# Experimental RF battery telemetry (controller 1.09 / receiver 1.03)

**Verified on Windows with one physical 2.4 GHz controller/receiver pair in both DInput and XInput modes.**
The controller's original packet is 20 bytes; the optional transmitter tweak
extends it to 28 and the dongle's `dongle_rf_tail` tweak retains the trailer.
A user capture confirmed valid checksummed battery trailers for all 99 tested
samples. Other hardware/firmware combinations are not yet verified. Preserve
a known-good USB backup before installing any experimental firmware tweak.

## Implementation

- Controller opt-in tweak **Send battery status over 2.4 GHz** (`rf_battery_tx`)
  wraps the stock RF input builder at `0xca0` from its call site `0xa350`,
  preserves bytes 0..19 including input checksum, and adds an 8-byte trailer
  at `0x843980+20`.
- Stock sender `0xdd78` normally copies only 20 bytes. The recipe extends the
  copy length and its two packet-length header variants to include eight more.
  Receiver compatibility was confirmed on the tested physical receiver. This is not a separate RF packet.
- Trailer offsets: 20=`A5`, 21=`5A`, 22=battery percentage, 23-24=voltage mV LE,
  25=flags (bit0 full, bit1 USB supply), 26=sequence modulo 256,
  27=XOR(C3, bytes20..26).
- Dongle tweak **Read RF packet over USB** (`dongle_rf_read`) remains
  **separate and optional**. It exposes 32 bytes of the most recently received
  RF frame at RAM address `0x843108` through a narrowly intercepted firmware
  read request. It does not flash the controller or send RF commands.
- Toolkit Diagnostics `/api/battery` recognizes a valid trailer from the
  `dongle_paired` DInput vendor interface and shows battery percentage, voltage,
  and external supply state. The existing 60-second poll is reused. On Windows,
  XInput stock-VID/PID dongles are detected by reading the blank flash signature
  before using the same RF battery reader. Windows XInput and DInput reads
  have been physically confirmed. Linux and macOS need separate testing.

## Hardware verification

1. Keep original firmware backups for both the controller and receiver. Before
   installing, ensure you can restore the controller over **USB**.
2. The receiver already has `dongle_rf_read`; keep that tweak enabled.
3. In Firmware Toolkit, choose the **controller** and enable `rf_battery_tx`
   explicitly. Use **Check First** and **Install Tweaks** (no manual firmware
   build required). This is a deliberately opt-in experimental tweak.
4. Unplug the controller USB cable and connect through the 2.4 GHz **DInput**
   dongle. Immediately check buttons, sticks, and rumble; if they no longer work,
   **stop and restore the controller by USB**.
5. If input still works, run:

   `python -m tools.dongle_rf_probe --seconds 20 --output rf-battery-test.jsonl`

   JSON lines gain a `battery` field only when a valid marker, checksum, percentage
   and voltage are present. The toolkit Diagnostics tab also polls automatically.
6. Verify the reported percentage and voltage against the USB reading, then
   repeat at a different battery charge state. Power-source flag means USB
   supply detected; it is not a validated charging-current measurement.

## Caveats

On other firmware/hardware versions, longer frames could be dropped entirely, or could reach a receive handler
that assumes the old size. The emulator tests only verify TC32 instruction
execution, bytes 0..19, trailer formatting, and that the firmware recipe
composes; **they do not verify RF link operation**. The GUI tweak is disabled
by default. If the hardware cannot accept longer frames, a future implementation
will require a dedicated RF message type and receiver-side parsing.

## Experimental receiver-side RF tail capture (2026-11-22)

Reviewing the original receiver 1.03 code at `0x6C60` found a **second 20-byte
limit** even after a longer RF frame has been received: it copies exactly 20
bytes from the radio staging block at `r5+5` into the diagnostic RAM buffer
at `0x843108`. The checksum thereafter examines only bytes 0..19. Therefore,
the original `dongle_rf_read` result of zero bytes at offsets 20..27 cannot
prove that RF transmission itself is still restricted to 20 bytes.

An **independent, opt-in** receiver tweak `dongle_rf_tail` changes the single
instruction at `0x6C64` from `tmovs r2, #20` to `tmovs r2, #28` before the
existing copying routine. It leaves frame classification, the controller's
input format, checksum, USB output, the DInput mode tweak and RF registers
unchanged. The staging buffer is a 64-byte radio block, and the destination
already supports 32-byte diagnostic reads. This is nevertheless a **hardware
experiment**: it may copy stale bytes if the radio did not receive 28 bytes.
The only meaningful success is a **valid marked and checksummed** battery
trailer that updates while the controller is active.

To test: keep controller `rf_battery_tx` and receiver `dongle_rf_read` enabled,
then enable **Experimental: capture 28-byte RF payload** on the dongle. Use
**Check First**, keep your dongle backup, and install through Firmware Toolkit.
Immediately verify that buttons, sticks and rumble still work; if they do
not, restore the dongle by removing `dongle_rf_tail` (or restore from backup).
Then run:

```powershell
python -m tools.dongle_rf_probe --seconds 20 --output rf-tail-test.jsonl
```

The tool adds a `battery` field only for a valid trailer. If there is no
battery field, **do not** report a valid battery level: the length or radio
staging path still needs analysis. No changes to the controller are required
for this specific follow-up test.

### XInput 2.4 GHz on Windows (2DC8:310A)

The **unpatched** XInput identity of the dongle is `2DC8:310A`, which is
also used by a directly connected USB controller. The 2.4 GHz receiver
already exposes a separate Windows HID vendor interface `MI_02`, usage page
`0xFF7A`, alongside the standard XUSB gamepad interface. You do **not** need
to replace the Xbox driver, change the USB descriptor, or flash a further
tweak for XInput battery access.

The Toolkit's `/api/battery` path now checks the existing read-only flash
signature at address `0x10000` before choosing a command. A real controller
uses its standard USB battery command; a dongle (blank signature of 16
`FF` bytes) instead reads the RF battery trailer at `0x843108` via the
**same already-open vendor HID channel**. DInput continues using its existing
paired-dongle read path. UI polling is unchanged (initial 5-second settle,
then every 60 seconds).

With the dongle connected in XInput mode, the standalone, read-only check is:

```powershell
python -m tools.dongle_xinput_battery
```

If this returns `{"source": "dongle", "unavailable": true}`, the channel
was accessible but no valid telemetry was returned. If it cannot open the
channel or does not receive a reply, report the exact error. Firmware and
battery trailer patches already installed on the controller and dongle
remain required for actual telemetry. Do not interpret XInput's generic
`wired/full` state as the controller's battery percentage.
