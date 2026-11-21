# Porting to another operating system

The firmware patching, backup, restore, protocol and web application logic is shared across platforms. Platform-specific code lives primarily in `u2c_customfw/transport/` and the diagnostics integration.

## Transport backend

Implement a transport backend with these operations:

- `list_devices() -> list[DeviceInfo]` enumerates supported HID devices and supplies the vendor ID, product ID, primary usage page, transport name, display name and an opaque handle.
- `open_channel(dev, report_id) -> Channel` opens the selected device interface.
- `Channel.xfer(report, timeout) -> bytes | None` sends a report and receives a response, returning `None` on timeout.
- `Channel.close()` releases any open device resources.

The running controller's vendor update interface uses output report ID `0x81` and input report ID `0x02`. The bootloader uses unnumbered 64-byte reports. Implementations must handle report IDs according to the platform's HID API, including differences in whether the ID byte is included in reads and writes.

Select the new backend in `u2c_customfw/transport/__init__.py`. Keep the firmware protocol and safety checks independent of the operating system.

## Diagnostics and permissions

The diagnostics interface is platform-specific: macOS uses GameController, Linux uses evdev, and Windows uses the browser Gamepad API. A new platform needs its own input and optional force-feedback bridge, or must clearly mark unsupported diagnostics.

Document device permissions and reconnect behavior for the new platform. Test enumeration, read-only firmware operations, update-mode transitions, backup and restore before attempting any firmware write. A successful bootloader transition alone does not validate flashing.

For the existing Linux implementation, see [linux.md](linux.md).
For Windows HIDAPI installation and validation status, see [windows.md](windows.md).
