# 2.4 GHz battery readout — Linux and macOS validation

The receiver/controller firmware tweaks have been physically verified on Windows for **both DInput and XInput**. Their firmware bytes are unchanged here. The Linux/macOS paths below are **not yet verified on physical hardware**; do not represent them as supported until tested.

## Existing implementation

- Controller tweak `rf_battery_tx` adds a checksummed eight-byte trailer to the RF packet.
- Receiver tweak `dongle_rf_tail` retains all 28 bytes; `dongle_rf_read` exposes the buffer through the existing vendor HID firmware-read command.
- `u2c_customfw/ops/rf_battery.py` reads 32 bytes at `0x843108`; `u2c_customfw/protocol/rf_battery.py` checks the marker, checksum, voltage and percentage.
- `/api/battery` in `u2c_customfw/web/server.py` uses this path for detected receivers, but the host must first expose the dongle vendor HID collection; a gamepad input node alone is insufficient.

## Linux checks

1. Install the project's udev rules, reconnect the dongle and confirm vendor HID device visibility (`lsusb -d 2dc8:`; `/dev/hidraw*`; `udevadm info`). Check permissions before running as root.
2. Test with DInput 2.4 GHz connected; check `/api/battery` or **Diagnostics → Read battery**. Verify percentage, millivolts and changes as charge varies.
3. Repeat in XInput 2.4 GHz: determine whether the vendor interface is exposed by HIDRAW or whether only XUSB is bound by the driver. If only XUSB appears, record the VID:PID, interface and driver instead of concluding that RF telemetry is absent.
4. Verify that receiver identity detection and controller-vs-dongle discrimination are correct, with no writes to the receiver.

## macOS checks

1. Identify the receiver in each mode using IOKit and System Information; verify that a vendor HID interface is present and can be opened by `u2c_customfw/transport/iokit.py`. macOS may claim the Xbox-compatible gamepad interfaces.
2. Check **Diagnostics → Read battery** in both DInput and XInput; note authorization errors separately from absent HID nodes.
3. Confirm that the reported value originates from the RF buffer, not merely GameController's own battery metadata, and compare against a direct USB reading.

## Acceptance criteria

Each platform/mode combination is confirmed only after a valid, checksummed trailer has been read **from the dongle through the native transport**. Record OS version, VID:PID, HID interface, driver, success/error, percentage and millivolts. No new firmware tweak or automatic transport workaround should be added before those tests.
