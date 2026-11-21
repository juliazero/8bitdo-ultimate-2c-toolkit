# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only input for stock Bluetooth and dongle D-input HID."""
import threading

from ..transport.windows import _hid


def decode(report, product=0x301B, usb=False):
    if usb or product in (0x301C, 0x301D):
        # USB D-input descriptor: 15 buttons + pad, hat + pad, X/Y/Z/Rz, Gas/Brake.
        # Normalize to the Bluetooth decoder's layout; report 02 is update data.
        if len(report) < 10 or report[0] != 1:
            return None
        report = bytes([1, report[3]]) + bytes(report[4:8]) + bytes(report[8:10]) + bytes(report[1:3]) + bytes([0])

    # Captured 2dc8:301b descriptor: hat, X/Y/Z/Rz, Gas/Brake, 16 buttons.
    if len(report) < 11 or report[0] != 1:
        return None
    buttons = {0: "A", 1: "B", 2: "L4", 5: "R4", 3: "X", 4: "Y", 6: "LB", 7: "RB", 10: "View", 11: "Menu", 12: "Home", 13: "L3", 14: "R3"}
    mask = int.from_bytes(report[8:10], "little")
    pressed = [label for bit, label in buttons.items() if mask & (1 << bit)]
    lt, rt = report[7] / 255, report[6] / 255
    if lt > .1 or mask & (1 << 8):
        pressed.append("LT")
    if rt > .1 or mask & (1 << 9):
        pressed.append("RT")
    directions = (("Up",), ("Up", "Right"), ("Right",), ("Down", "Right"), ("Down",), ("Down", "Left"), ("Left",), ("Up", "Left"))
    hat = report[1] & 15
    if hat < 8:
        pressed.extend(directions[hat])
    axis = lambda index: report[index] * 2 / 255 - 1
    return {"available": True, "analogTriggers": True, "lx": axis(2), "ly": -axis(3), "rx": axis(4), "ry": -axis(5), "lt": lt, "rt": rt, "pressed": pressed}


class StockGamepad:
    def __init__(self):
        self.lock = threading.Lock()
        self.device = None
        self.path = None
        self.last = None
        self.product = 0x301B
        self.usb = False

    def _close(self):
        if self.device is not None:
            self.device.close()
        self.device, self.path, self.last = None, None, None

    def snapshot(self):
        with self.lock:
            try:
                hid = _hid()
                devices = [d for d in hid.enumerate(0x2DC8, 0)
                           if d.get("usage_page") == 1 and d.get("usage") == 5
                           and ((d.get("product_id", 0x301B) == 0x301B and d.get("bus_type") == 2)
                                or (d.get("product_id") in (0x301B, 0x301C, 0x301D) and d.get("bus_type") == 1))]
                if len(devices) != 1:
                    self._close()
                    return {"available": False, "connected": False}
                path = devices[0]["path"]
                if path != self.path:
                    self._close()
                    self.device = hid.device()
                    self.device.open_path(path)
                    self.device.set_nonblocking(1)
                    self.path = path
                    self.product = devices[0].get("product_id", 0x301B)
                    self.usb = devices[0].get("bus_type") == 1
                for _ in range(64):
                    packet = self.device.read(64)
                    if not packet:
                        break
                    state = decode(bytes(packet), self.product, usb=self.usb)
                    if state:
                        self.last = state
                return self.last or {"available": False, "connected": True}
            except (OSError, RuntimeError):
                self._close()
                return {"available": False}


GAMEPAD = StockGamepad()
