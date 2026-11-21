# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Linux gamepad diagnostics via evdev; no firmware access."""
import os
import threading
import time

try:
    from evdev import InputDevice, list_devices, ecodes, ff
except ImportError:
    InputDevice = None
    ff = None

BUTTONS = {"BTN_SOUTH": "A", "BTN_EAST": "B", "BTN_NORTH": "X", "BTN_WEST": "Y",
           "BTN_TL": "LB", "BTN_TR": "RB", "BTN_THUMBL": "L3", "BTN_THUMBR": "R3",
           "BTN_SELECT": "View", "BTN_START": "Menu", "BTN_MODE": "Home"}
BUTTON_CODES = {getattr(ecodes, code): label for code, label in BUTTONS.items()} if InputDevice is not None else {}
AXES = {"ABS_X": "lx", "ABS_Y": "ly", "ABS_RX": "rx", "ABS_RY": "ry",
        "ABS_Z": "lt", "ABS_RZ": "rt", "ABS_HAT0X": "hatx", "ABS_HAT0Y": "haty"}
# Standard HID gamepads can expose the right stick as Z/RZ and triggers as Gas/Brake.
HID_AXES = {"ABS_X": "lx", "ABS_Y": "ly", "ABS_Z": "rx", "ABS_RZ": "ry",
            "ABS_BRAKE": "lt", "ABS_GAS": "rt", "ABS_HAT0X": "hatx", "ABS_HAT0Y": "haty"}

def _dinput(dev):
    info=getattr(dev,'info',None)
    return getattr(info,'vendor',None)==0x2dc8 and getattr(info,'product',None) in (0x301b,0x301c,0x301d)

def _category(dev):
    if not _dinput(dev):return 'Linux evdev'
    info=dev.info
    return ('2.4 GHz dongle' if info.product==0x301c else 'Bluetooth' if info.bustype==5 else 'USB')+' · D-input'

class Gamepad:
    def __init__(self):
        self.lock = threading.Lock()
        self.device = None
        self.path = None
        self.error = None
        self.values = {k: 0.0 for k in ("lx", "ly", "rx", "ry", "lt", "rt")}
        self.pressed = set()
        self.axis_info = {}
        self.axes = AXES
        self.last_scan = 0
        self.worker = None
        self.ff_lock = threading.Lock()
        self.ff_timer = None
        self.ff_effect = None

    def _choose(self):
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities()
                keys = caps.get(ecodes.EV_KEY, [])
                axes = caps.get(ecodes.EV_ABS, [])
                if ecodes.BTN_SOUTH not in keys or ecodes.ABS_X not in [a[0] if isinstance(a, tuple) else a for a in axes]:
                    dev.close(); continue
                name = dev.name.lower()
                if not any(s in name for s in ("8bitdo", "xbox", "x-box")):
                    dev.close(); continue
                return dev
            except (OSError, PermissionError):
                continue
        return None

    def _loop(self, dev):
        try:
            for event in dev.read_loop():
                with self.lock:
                    if self.device is not dev: break
                    if event.type == ecodes.EV_KEY:
                        name = BUTTON_CODES.get(event.code)
                        if _dinput(dev) and event.code in (ecodes.BTN_C,ecodes.BTN_Z):name="L4" if event.code==ecodes.BTN_C else "R4"
                        if name:
                            (self.pressed.add if event.value else self.pressed.discard)(name)
                    elif event.type == ecodes.EV_ABS:
                        name = self.axes.get(ecodes.ABS.get(event.code, ""))
                        if not name: continue
                        if name in ("hatx", "haty"):
                            for label in (("Left", "Right") if name == "hatx" else ("Up", "Down")):
                                self.pressed.discard(label)
                            if event.value:
                                self.pressed.add(("Left" if event.value < 0 else "Right") if name == "hatx" else ("Up" if event.value < 0 else "Down"))
                            continue
                        info = self.axis_info.get(event.code)
                        if not info or info.max == info.min: continue
                        if name in ("lt", "rt"):
                            value = (event.value - info.min) / (info.max - info.min)
                        else:
                            value = 2 * (event.value - info.min) / (info.max - info.min) - 1
                            if name in ("ly", "ry"): value = -value
                        self.values[name] = max(-1.0 if name not in ("lt", "rt") else 0.0, min(1.0, value))
        except OSError:
            pass
        finally:
            with self.lock:
                if self.device is dev:
                    self.device = None
            self._stop_rumble(dev)
            dev.close()

    def _stop_rumble(self, dev):
        with self.ff_lock:
            if self.ff_timer is not None:
                self.ff_timer.cancel()
                self.ff_timer = None
            if self.ff_effect is not None:
                try:
                    dev.write(ecodes.EV_FF, self.ff_effect, 0)
                    dev.erase_effect(self.ff_effect)
                except OSError:
                    pass
                self.ff_effect = None

    def rumble(self, locality="default"):
        """Play one short, bounded evdev FF_RUMBLE effect; never touches firmware."""
        if locality not in ("default", "left", "right"):
            return {"ok": False, "error": "Unsupported vibration mode"}
        with self.lock:
            dev = self.device
            if dev is None:
                return {"ok": False, "error": "No connected gamepad"}
            try:
                supported = dev.capabilities().get(ecodes.EV_FF, [])
                if ecodes.FF_RUMBLE not in supported:
                    return {"ok": False, "error": "The Linux driver does not expose FF_RUMBLE for this controller."}
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            self._stop_rumble(dev)
            # A short, moderate-strength test. Side-specific controls depend on driver support.
            strong, weak = {"default": (0x5000, 0x5000), "left": (0x5000, 0), "right": (0, 0x5000)}[locality]
            effect = ff.Effect(
                ecodes.FF_RUMBLE, -1, 0, ff.Trigger(0, 0), ff.Replay(450, 0),
                ff.EffectType(ff_rumble_effect=ff.Rumble(strong, weak)),
            )
            try:
                with self.ff_lock:
                    effect_id = dev.upload_effect(effect)
                    self.ff_effect = effect_id
                    dev.write(ecodes.EV_FF, effect_id, 1)
                    self.ff_timer = threading.Timer(0.6, self._stop_rumble, args=(dev,))
                    self.ff_timer.daemon = True
                    self.ff_timer.start()
            except (OSError, ValueError) as exc:
                self._stop_rumble(dev)
                return {"ok": False, "error": f"Vibration failed: {exc}"}
        return {"ok": True}

    def snapshot(self):
        if InputDevice is None:
            return {"type": "state", "connected": False, "error": "Install python-evdev: pip install evdev"}
        with self.lock:
            if self.device is not None:
                dev = self.device
                try:
                    rumble = ecodes.FF_RUMBLE in dev.capabilities().get(ecodes.EV_FF, [])
                except OSError:
                    rumble = False
                return {
                    "type": "input", "connected": True,
                    "name": dev.name, "category": _category(dev),
                    "localities": (["default", "left", "right"] if rumble else []),
                    "extended": True, "foreground": True,
                    "battery": {"level": -1, "state": "unknown"},
                    "pressed": sorted(self.pressed), **self.values,
                }
            now = time.monotonic()
            if now - self.last_scan < 1:
                return {"type": "state", "connected": False, "error": self.error}
            self.last_scan = now
        try:
            dev = self._choose()
        except OSError as e:
            self.error = str(e)
            dev = None
        if dev is None:
            return {"type": "state", "connected": False, "error": self.error or "No readable 8BitDo/Xbox gamepad event device. Check /dev/input/event* permissions."}
        with self.lock:
            self.device = dev
            self.error = None
            self.pressed.clear()
            self.axis_info = {}
            supported_axes = {item[0] if isinstance(item, tuple) else item
                              for item in dev.capabilities().get(ecodes.EV_ABS, [])}
            self.axes = (HID_AXES if {ecodes.ABS_GAS, ecodes.ABS_BRAKE} <= supported_axes
                         and not {ecodes.ABS_RX, ecodes.ABS_RY} & supported_axes else AXES)
            for code_name in self.axes:
                try:
                    code = getattr(ecodes, code_name)
                    self.axis_info[code] = dev.absinfo(code)
                except (OSError, KeyError, ValueError):
                    pass
            self.values = {k: 0.0 for k in self.values}
            for code, info in self.axis_info.items():
                key = self.axes.get(ecodes.ABS.get(code, ""))
                if info.max <= info.min:
                    continue
                if key in ("lt", "rt"):
                    self.values[key] = max(0.0, min(1.0, (info.value - info.min) / (info.max - info.min)))
                elif key in ("lx", "ly", "rx", "ry"):
                    value = 2 * (info.value - info.min) / (info.max - info.min) - 1
                    self.values[key] = max(-1.0, min(1.0, -value if key in ("ly", "ry") else value))
            try:
                rumble = ecodes.FF_RUMBLE in dev.capabilities().get(ecodes.EV_FF, [])
            except OSError:
                rumble = False
            self.worker = threading.Thread(target=self._loop, args=(dev,), daemon=True)
            self.worker.start()
        return {"type": "state", "connected": True, "name": dev.name, "category": _category(dev), "localities": (["default", "left", "right"] if rumble else []), "extended": True, "foreground": True, "battery": {"level": -1, "state": "unknown"}}

GAMEPAD = Gamepad()
