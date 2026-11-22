# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows HID transport using HIDAPI and the system HID driver.

Report framing and Bluetooth path detection follow Pad8Lab's Windows transport.
HIDAPI handles overlapped I/O, report lengths and cancellation on Windows.
"""
import time

from . import DeviceInfo, PermissionDenied


def _hid():
    try:
        import hid
    except ImportError as exc:
        raise RuntimeError("Windows HID support requires: python -m pip install hidapi") from exc
    return hid


def list_devices():
    hid = _hid()
    result = []
    for vendor in (0x2DC8, 0x045E):
        for item in hid.enumerate(vendor, 0):
            path = item["path"]
            text = (path.decode(errors="replace") if isinstance(path, bytes) else path).upper()
            bluetooth = (item.get("bus_type") == 2 or "BTHENUM" in text
                         or "00001812-0000-1000-8000-00805F9B34FB" in text)
            result.append(DeviceInfo(item["vendor_id"], item["product_id"],
                                     item.get("usage_page", 0), "Bluetooth" if bluetooth else "USB",
                                     item.get("product_string") or "8BitDo/Xbox HID device",
                                     path, item.get("release_number", 0), item.get("usage", 0)))
    return result


def _ring_device(profile):
    profiles = {"xinput": (0x045E, 0x0B13, 3), "dinput": (0x2DC8, 0x301B, 2)}
    if profile not in profiles:
        raise ValueError("Invalid Bluetooth profile")
    vid, pid, report_id = profiles[profile]
    hid = _hid()
    devices = []
    for item in hid.enumerate(vid, pid):
        path = item["path"]
        text = (path.decode(errors="replace") if isinstance(path, bytes) else path).upper()
        bluetooth = (item.get("bus_type") == 2 or "BTHENUM" in text
                     or "00001812-0000-1000-8000-00805F9B34FB" in text)
        if (bluetooth and item.get("usage_page") == 1 and item.get("usage") == 5
                and item.get("manufacturer_string") != "Microsoft"):
            devices.append(item)
    if len(devices) != 1:
        raise OSError("Connect exactly one controller in the selected Bluetooth profile to change the Home ring.")
    return hid, devices[0]["path"], report_id


def set_bluetooth_ring(level, save=False, profile="xinput"):
    """Send the ring tweak's marked X/D output report; there is no device ACK.

    This is a 9-byte Bluetooth report, not the 64-byte firmware update protocol.
    Level 255 saves the current brightness without changing it.
    """
    if type(level) is not int or level not in (*range(6), 255) or type(save) is not bool:
        raise ValueError("Invalid Home ring request")
    hid, path, report_id = _ring_device(profile)
    device = hid.device()
    try:
        device.open_path(path)
        report = bytes([report_id, 0x80, level, int(save), 0, 0, 0, 0, 0])
        written = device.write(report)
        # HIDAPI may pad to the collection's longest output report on Windows.
        if written < len(report):
            raise OSError(f"Short Bluetooth HID write: {written}/{len(report)} bytes")
    finally:
        device.close()


class WindowsChannel:
    def __init__(self, dev, report_id):
        self.rid = report_id
        self.device = _hid().device()
        try:
            self.device.open_path(dev.handle)
        except OSError as exc:
            self.device.close()
            raise PermissionDenied("Cannot open HID device. Reconnect it and close other controller tools (including Pad8Lab).") from exc

    def _drain(self):
        # A timed 1 ms read can cost an entire Windows scheduler tick, even
        # when the queue is empty. Poll without waiting before each request.
        self.device.set_nonblocking(1)
        try:
            for _ in range(32):
                if not self.device.read(64):
                    break
        finally:
            self.device.set_nonblocking(0)

    def xfer(self, report, timeout=0.5, accept=None):
        data = bytes(report)
        if len(data) != 64:
            raise ValueError("expected a 64-byte HID report")
        if self.rid and data[0] != self.rid:
            raise ValueError("Output report ID does not match channel")
        self._drain()
        # HIDAPI removes the synthetic ID on unnumbered input, but requires it on output.
        output = data if self.rid else b"\0" + data
        written = self.device.write(output)
        if written != len(output):
            raise OSError(f"short HID write: {written}/{len(output)} bytes")
        deadline = time.monotonic() + max(0, timeout)
        while time.monotonic() < deadline:
            reply = bytes(self.device.read(64, max(1, int((deadline - time.monotonic()) * 1000))))
            if not reply:
                return None
            if accept is None or accept(reply):
                return reply
        return None

    def close(self):
        self.device.close()


def open_channel(dev, report_id):
    return WindowsChannel(dev, report_id)
