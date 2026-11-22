# Controller Settings over the 2.4 GHz dongle — transport investigation

Status: **not implemented**. This document records verified code boundaries and the remaining reverse-engineering work. It is not a firmware tweak and does not change the recognized firmware layouts.

## Existing working pieces

- Controller Settings uses 64-byte vendor USB output report `0x81` and input report `0x02` (`u2c_customfw/protocol/app.py`, `u2c_customfw/ops/settings.py`). Memory reads use `81 05 05`; live settings commands `0x54`–`0x5A` have no reply. Save/reset confirmation relies on reading back controller flash sectors, not merely receiving a USB ACK.
- The controller-side `settings` tweak handles those commands when sent to the **controller**. The web server deliberately selects only `cable`, `cable_360` or `cable_dinput` for Settings (`u2c_customfw/web/server.py`).
- On 2.4 GHz, the USB HID belongs to the **dongle**. `dongle_rf_read` permits reading one receiver RAM buffer (`0x843108`); `dongle_rf_tail` increases the captured input packet length to 28 bytes. Together with controller `rf_battery_tx`, they deliver *controller -> dongle -> host* battery information. This establishes no host -> controller command transport.
- Both dongle XInput and DInput have working gamepad communication and vibration. Before reusing any radio transmission path, identify the precise RF transmit routine, framing, control packet ownership, acknowledgements, and receiver state. Do not assume the USB vendor command `81 05 00 54` can simply be forwarded unchanged.

## Proposed implementation sequence

1. **Reverse engineer stock dongle 1.03 downlink**: trace the USB rumble OUT handler to the RF packet transmission routine and determine payload format, sizes, checksum, and when the transmitter may safely be called. Trace the paired-state check and the DInput/XInput USB OUT interface differences. Do not modify the RF gamepad report path.
2. **Reverse engineer the controller 1.09 RF receive dispatch**: find where incoming downlink packets are decoded; identify a reserved, unambiguous command namespace for Settings. Preserve the original rumble handling and stock packet semantics. No EEPROM/flash writes without a decoded, validated command.
3. **Read-only transport probe first**: add an optional dongle tweak that requests a harmless controller identifier or settings-tweak signature. Add a distinct controller-side reply with transaction ID and result. Validate reply source, length, timeouts, and both USB modes; block transactions when the controller is disconnected.
4. **Settings read**: forward the equivalent of `81 05 05` in bounded chunks (max 46 bytes for existing protocol), with explicit target addresses restricted to necessary controller sectors. Require an RF response tagged with the transaction ID; never substitute a read of dongle flash. Recover safely after packet loss.
5. **Live changes, then Save/Reset**: only after readback is verified. Forward `0x54`–`0x5A` with strict length and value validation. The UI must confirm a save by subsequent *controller* readback, not a dongle acknowledgment. A timeout must never trigger automatic `WRITE_UNPROTECT`, erase, write, or bootloader commands.
6. **Native API and GUI**: add a distinct channel type (e.g. `DongleSettingsChannel`) with explicit controller-facing identity. Detect the receiver and check the required tweaks on *both* devices; report unavailable until negotiated. Keep existing `by_cable` and wired settings paths unchanged. Support `dongle_paired` in XInput and DInput only after the transport has been tested.

## Safety and upstream rules

- Keep all current Bluetooth, USB and dongle recipes and their IDs unchanged while implementing this feature.
- Make any RF Settings extension optional, and expose it through the existing upstream-style `Recipe`/`Edit` controller patches and `TWEAKS` dongle patches. Do not add migration shims for historic experimental firmware.
- Never treat the receiver as the controller in `settings_ops.read`: addresses such as `settings.SECTOR` refer to **controller** flash, not the dongle's 128 KiB flash.
- Protect against RF disconnection, wrong controller, duplicate or reordered responses, unsafe addresses, concurrent USB and RF writers, and a save interrupted by switching modes.

## Acceptance criteria

For both dongle XInput (`310A`/Xbox identity) and DInput (`301B` with Steam mapping): read the same Settings record as directly over controller USB; change a reversible setting, observe it live, save, reconnect, and confirm it persists; reset to defaults; unplug or turn off the controller during a request and confirm no unexpected write; verify gamepad input, rumble and battery telemetry still work. No firmware update operation may be exposed across this channel.
