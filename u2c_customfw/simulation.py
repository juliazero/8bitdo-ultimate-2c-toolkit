# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated protocol laboratory, using synthetic firmware only.

This models device discovery, flash, update-mode transitions and disconnects.
It does not execute the controller CPU or emulate Bluetooth/radio hardware.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import time
import os
import random
from pathlib import Path
from unittest.mock import patch

from . import cli, sim
from .firmware import image, recipes
from .ops import devices, dongle
from .transport import DeviceInfo

PROFILES = ("controller-usb", "controller-boot", "controller-bluetooth", "controller-bluetooth-xinput", "dongle-idle",
            "dongle-xinput", "dongle-dinput", "dongle-boot")


def dongle_fixture():
    rnd = random.Random(103)
    bank = bytearray(rnd.randbytes(dongle.SIZE))
    for _, edits in dongle.TWEAKS.values():
        for address, old, _ in edits:
            bank[address - dongle.BASE:address - dongle.BASE + len(old)] = old
    for slot in dongle.NAMES.values():
        bank[slot.at - dongle.BASE:slot.at - dongle.BASE + slot.size] = dongle._slot(slot, slot.stock)
        bank[slot.length_at - dongle.BASE] = 2 + 2 * len(slot.stock)
        for address, old, _ in slot.code:
            bank[address - dongle.BASE:address - dongle.BASE + len(old)] = old
    flash = bytearray([255]) * dongle.FLASH
    flash[:dongle.BOOT_END] = rnd.randbytes(dongle.BOOT_END)
    flash[dongle.BASE:dongle.END] = bank
    flash[dongle.CAL:dongle.CAL + 64] = rnd.randbytes(64)
    flash[dongle.RECORD + 12] = 103
    return flash, hashlib.sha256(bank).hexdigest()


class World:
    def __init__(self, profile, controller_flash, receiver_flash, disconnect_after=None, preferences_path=None):
        if profile not in PROFILES:
            raise ValueError("Unknown simulation profile")
        self.preferences_path = Path(preferences_path) if preferences_path else None
        self.rear_switch = 'dongle'
        if self.preferences_path:
            try:
                saved = json.loads(self.preferences_path.read_text(encoding='utf-8'))
                if saved.get('rear_switch') in ('dongle', 'bluetooth'): self.rear_switch = saved['rear_switch']
            except (OSError, ValueError, AttributeError): pass
        self.reconnect_at = None
        self.dongle_attached = True
        self.home_since = None
        self.wireless_gesture = None
        self.home_fired = False
        self.update_since = None
        self.usb_gesture = None
        self.usb_fired = False
        self.usb_last_vendor = None
        self.usb_write_lock = False
        self.profile = profile
        self.connection = "dongle" if profile.startswith("dongle-") else "bluetooth" if profile.startswith("controller-bluetooth") else "usb"
        self.selected_device, self.selected_connection = self._selection(profile)
        self.rf_saved_mode = "d" if profile == "dongle-dinput" else "x"
        self.app_channel = None
        self.live_ring = None
        self.pressed = set()
        self.axes = dict(lx=0, ly=0, rx=0, ry=0, lt=0, rt=0)
        self.battery_percent = 100
        self.battery_mv = 4200
        self.ring_chord_since = None
        self.ring_chord_fired = False
        self.ring_save_at = None
        self.motor_until = 0
        self.motor = "off"
        self.controller = bytearray(controller_flash)
        self.receiver = bytearray(receiver_flash)
        self.usb_dinput = False
        self.disconnect_after = disconnect_after
        self.connected, self.requests, self.trace = True, 0, []

    def _save_switch(self, value):
        if self.preferences_path:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            self.preferences_path.write_text(json.dumps({'rear_switch': value}), encoding='utf-8')
        self.rear_switch = value

    def _bt_xbox_active(self):
        keys=self._controller_keys()
        saved=self.controller[image.SETTINGS+5]
        return 'bt_xbox' in keys and not ('bt_modes' in keys and saved!=255 and saved&2)

    def _bt_startup(self):
        if 'bt_modes' in self._controller_keys():
            saved=self.controller[image.SETTINGS+5]
            value=0 if saved==255 else saved
            held=self.pressed & {'B','X'}
            target=(value|2) if held=={'B'} else (value&~2) if held=={'X'} else value
            if target!=value:self.controller[image.SETTINGS+5]=target
        return 'controller-bluetooth-xinput' if self._bt_xbox_active() else 'controller-bluetooth'

    def _wireless_profile(self):
        if self.rear_switch == 'bluetooth':
            return 'controller-bluetooth-xinput' if self._bt_xbox_active() else 'controller-bluetooth'
        return 'dongle-dinput' if self.rf_saved_mode == 'd' and self._dongle_dinput() else 'dongle-xinput'

    def _unplug_usb(self):
        self.selected_device, self.selected_connection = 'controller', self.rear_switch
        self.connection = self.rear_switch
        self.profile = self._wireless_profile()
        self.connected, self.app_channel, self.live_ring = False, None, None
        self.reconnect_at = time.monotonic() + 2

    def _tick(self):
        if self.reconnect_at is not None and time.monotonic() >= self.reconnect_at:
            self.reconnect_at = None
            self.connection = self.selected_connection = self.rear_switch
            self.profile = self._wireless_profile()
            self.connected = True
        self._home_poll()
        self._usb_poll()
        self._ring_poll()

    def _home_poll(self):
        now = time.monotonic()
        if 'Home' not in self.pressed:
            self.home_since, self.home_fired, self.update_since = None, False, None
            self.wireless_gesture = None
            return
        active = self.selected_device == 'controller' and self.connected and self.profile != 'dongle-idle'
        if not active or self.profile.endswith('-boot') or self.home_fired:
            self.home_since, self.update_since = None, None
            return
        if {'L4', 'R4'} <= self.pressed:
            if self.update_since is None: self.update_since = now
            if now - self.update_since >= 3:
                self.profile = 'controller-boot'
                self.connection = 'usb'
                self.selected_connection = 'update'
                self.app_channel, self.live_ring, self.reconnect_at = None, None, None
                self.home_fired = True
            return  # update chord takes precedence over ring/power shortcuts
        self.update_since = None
        if self.connection == 'usb':
            self.home_since = None
            return
        mode_buttons=self.pressed & {'B','X'}
        live_switch=('bt_modes' in self._controller_keys() and
                     self.controller[0xa478:0xa47c]==(recipes.BLOBS/'bthome.bin').read_bytes())
        if live_switch and len(mode_buttons)==1 and not (self.pressed & {'L4','R4'}):
            gesture=next(iter(mode_buttons))
            if self.wireless_gesture is None or self.wireless_gesture[0]!=gesture:
                self.wireless_gesture=(gesture,now)
            elif now-self.wireless_gesture[1]>=3:
                if self.connection=='bluetooth':self._bt_startup()
                else:self._rf_startup()
                self.home_fired=True
                self.connected=False
                self.app_channel,self.live_ring=None,None
                self.reconnect_at=now+2
            return
        self.wireless_gesture=None
        if self.home_since is None: self.home_since = now
        if now - self.home_since >= 12:
            self.home_fired = True
            self.control({'action': 'power'})

    def _ring_poll(self):
        ring = self.ring_state()
        active = self.connected and self.profile != 'dongle-idle' and not self.profile.endswith('-boot') and ring['available']
        now = time.monotonic()
        if not active or not {'L4','R4'} <= self.pressed:
            self.ring_chord_since, self.ring_chord_fired = None, False
        elif not self.ring_chord_fired:
            if self.ring_chord_since is None: self.ring_chord_since = now
            if now - self.ring_chord_since >= ring['chord_hold_seconds']:
                self.live_ring = (ring['live'] + 1) % len(recipes.RING_ORDER)
                if self.app_channel is not None: self.app_channel.ring_level = self.live_ring
                self.ring_chord_fired, self.ring_save_at = True, now + 3
        if active and self.ring_save_at is not None and now >= self.ring_save_at:
            self.controller[image.RING_SAVED] = ring['installed'] << 4 | self.ring_state()['live']
            self.ring_save_at = None

    def _usb_poll(self):
        if not self.connected or self.profile != 'controller-usb' or 'usb_dinput' not in self._controller_keys():
            self.usb_gesture = None
            return
        now = time.monotonic()
        if 'Home' not in self.pressed: self.usb_fired = False
        if self.usb_write_lock or (self.usb_last_vendor is not None and now - self.usb_last_vendor < 2):
            self.usb_gesture = None
            return
        if self.usb_fired: return
        target = True if {'B','Home'} <= self.pressed and 'X' not in self.pressed else False if {'X','Home'} <= self.pressed and 'B' not in self.pressed else None
        if target is None:
            self.usb_gesture = None
        elif self.usb_gesture is None or self.usb_gesture[0] != target:
            self.usb_gesture = (target, now)
        elif now - self.usb_gesture[1] >= 3:
            self.usb_dinput, self.usb_fired = target, True
            if self.controller[0xa414:0xa418] == (recipes.BLOBS/'usbstart.bin').read_bytes():
                saved=self.controller[image.SETTINGS+5]
                normalized=0 if saved==255 else saved
                if normalized&1 != int(target): self.controller[image.SETTINGS+5]=(normalized&~1)|int(target)
            self.app_channel = None

    def _usb_activity(self, request):
        if self.profile != 'controller-usb' or not request or request[0] != 0x81: return
        self.usb_last_vendor = time.monotonic()
        if bytes(request[:4]) == bytes.fromhex('81 11 04 08'): return
        if not (len(request) >= 4 and request[1] == 5 and
                (request[2] == 5 or (request[2] == 0 and request[3] == 0x21))):
            self.usb_write_lock = True

    @staticmethod
    def _selection(profile):
        if profile in ('dongle-idle', 'dongle-boot'):
            return 'dongle', 'update' if profile.endswith('-boot') else 'usb'
        return 'controller', ('update' if profile.endswith('-boot') else
                              'dongle' if profile.startswith('dongle-') else
                              'bluetooth' if profile.startswith('controller-bluetooth') else 'usb')

    def _usb_startup(self):
        self.usb_gesture, self.usb_fired, self.usb_last_vendor, self.usb_write_lock = None, False, None, False
        # Stock f014 ignores B/X and saved mode on the battery-equipped model.
        # 843470 is the batteryless-variant flag, not USB connection state.
        saved=self.controller[image.SETTINGS+5]
        persistent='usb_dinput' in self._controller_keys() and self.controller[0xa414:0xa418] == (recipes.BLOBS/'usbstart.bin').read_bytes()
        self.usb_dinput = persistent and saved != 255 and bool(saved&1)

    def _dongle_dinput(self):
        known, keys = dongle.state(bytes(self.receiver[dongle.BASE:dongle.END]))
        return known and 'dongle_dinput' in keys

    def _rf_startup(self):
        mode = 'd' if 'B' in self.pressed else 'x' if 'X' in self.pressed else self.rf_saved_mode
        if not self._dongle_dinput():
            mode = 'x'
        self.rf_saved_mode = mode
        return 'dongle-dinput' if mode == 'd' else 'dongle-xinput'

    @property
    def is_dongle(self):
        return self.profile.startswith("dongle-")

    @property
    def flash(self):
        return self.receiver if self.is_dongle else self.controller

    def _controller_keys(self):
        return recipes.identify(bytes(self.controller[image.BASE:image.APP_END])).recipes

    def ring_state(self):
        identified = recipes.identify(bytes(self.controller[image.BASE:image.APP_END]))
        installed = next((i for i, key in enumerate(recipes.RING_ORDER) if key in identified.recipes), None)
        if installed is None or identified.outdated:
            return {"available": False, "startup": None, "live": None}
        raw = self.controller[image.RING_SAVED]
        startup = raw & 15 if raw >> 4 == installed and raw & 15 < len(recipes.RING_ORDER) else installed
        live = self.live_ring if self.live_ring is not None else startup
        current = (recipes.BLOBS / 'ringtail5.bin').read_bytes()[0x18f50 - 0x18400:]
        hold = 5 if self.controller[0x18f50:0x18f50 + len(current)] == current else 0
        return {"available": True, "installed": installed, "startup": startup, "live": live, "chord_hold_seconds": hold}

    def find(self):
        self._tick()
        if not self.connected or (self.is_dongle and not self.dongle_attached):
            return []
        profile = self.profile
        mode, vendor, product, page, bcd = "cable", 0x2DC8, 0x310A, 0xFF7A, 0x114
        if profile.endswith("-boot"):
            mode, product, page = "boot", 0x3208, 0x8C
        elif profile == "dongle-idle":
            mode, product, page, bcd = "dongle", 0x301C, 0xFFA0, 0x103
        elif profile == "dongle-dinput":
            mode, product, page, bcd = "dongle_paired", 0x301C, 1, 1
        elif profile == "dongle-xinput":
            # Exactly the ambiguous stock identity that caused the recent bugs.
            mode = "cable"
        elif profile.startswith("controller-bluetooth"):
            xbox = self._bt_xbox_active()
            mode, vendor, product, page = ("bluetooth_xbox", 0x045E, 0x0B13, 1) if xbox else ("bluetooth", 0x2DC8, 0x301B, 1)
        elif profile == "controller-usb":
            keys = recipes.identify(bytes(self.controller[image.BASE:image.APP_END])).recipes
            if self.usb_dinput:
                mode, product, page, bcd = "cable_dinput", int.from_bytes(self.controller[0x17afe:0x17b00], "little") if "usb_dinput" in keys else 0x301B, 1, 1
            elif "cable_360" in keys:
                mode, vendor, product = "cable_360", 0x045E, 0x028E
        transport = "Bluetooth" if mode.startswith("bluetooth") else "USB"
        name = "Simulated 8BitDo Ultimate 2C Wireless Controller"
        info = DeviceInfo(vendor, product, page, transport, name, self, bcd, 5 if page == 1 else 1)
        return [devices.Found(info, mode)]

    def open(self, info, report_id):
        if info.handle is not self or not self.connected or (self.is_dongle and not self.dongle_attached):
            raise OSError("Simulation device disconnected")
        world = self
        boot_mode = self.profile.endswith("-boot")
        expected = 0 if boot_mode else 0x81
        if report_id != expected:
            raise ValueError("Incorrect simulated report ID")
        if self.profile.startswith("controller-bluetooth"):
            raise OSError("No firmware update channel over Bluetooth")

        def before(request):
            if not world.connected or (world.is_dongle and not world.dongle_attached): return False
            world._usb_activity(request)
            world.requests += 1
            if world.disconnect_after is not None and world.requests > world.disconnect_after:
                world.connected = False
                return False
            world.trace.append(bytes(request))
            return True

        if boot_mode:
            class Boot(sim.SimBoot):
                def xfer(self, request, timeout=0.5, accept=None):
                    if not before(request):
                        return None
                    reply = super().xfer(request, timeout, accept)
                    if int.from_bytes(request[1:3], "little") == 7:
                        world.profile = "dongle-idle" if world.is_dongle else "controller-usb"
                        world.selected_device, world.selected_connection = world._selection(world.profile)
                        if world.profile == "controller-usb": world._usb_startup()
                        world.app_channel = None
                        world.live_ring = None
                    return reply
            channel = Boot(bytes(self.flash))
            channel.f = self.flash
            return channel

        if self.app_channel is not None:
            return self.app_channel
        class App(sim.SimApp):
            def xfer(self, request, timeout=0.5, accept=None):
                if not before(request):
                    return None
                if bytes(request[:5]) == bytes.fromhex("81 05 00 51 00"):
                    world.profile = "dongle-boot" if world.is_dongle else "controller-boot"
                    world.selected_device, world.selected_connection = world._selection(world.profile)
                    return None
                self.flash = bytes(world.flash)
                reply = super().xfer(request, timeout, accept)
                if not world.is_dongle and bytes(request[:3]) == bytes.fromhex('81 05 00'):
                    ring = world.ring_state()
                    if ring['available'] and request[3] == 0x52 and request[4] < len(recipes.RING_ORDER):
                        self.ring_level = world.live_ring = request[4]
                    elif ring['available'] and request[3] == 0x53:
                        data = bytearray(self.flash)
                        data[image.RING_SAVED] = (ring['installed'] << 4) | getattr(self, 'ring_level', ring['startup'])
                        self.flash = bytes(data)
                world.flash[:] = self.flash
                return reply
        self.app_channel = App(bytes(self.flash))
        return self.app_channel


    def virtual_state(self):
        self._tick()
        import time
        live = self.connected and (not self.is_dongle or self.dongle_attached) and not self.profile.endswith("-boot") and self.profile != "dongle-idle"
        from . import settings as record
        from .ops import settings as settings_ops
        import math
        word_names = ["Menu", "L3", "R3", "View", "X", "Y", "Right", "Left", "Down", "Up", "LB", "RB", "B", "A", "LT", "RT"]
        hook = record.INSTALLED_HOOK
        installed = self.controller[hook:hook + 4] == settings_ops._HOOK
        ram = self.app_channel if not self.is_dongle else None
        saved = record.parse(bytes(self.controller[record.SECTOR:record.SECTOR + record.SIZE]))
        values = dict(record.DEFAULTS)
        if installed:
            values = dict(zip((f.name for f in record.FIELDS), ram.fields)) if ram else (saved or values)
        # Remapping is simultaneous, before stock_scan expands the back buttons.
        pressed = set(self.pressed) - set(word_names[:14])
        for bit, name in enumerate(word_names[:14]):
            target = values['remap_' + str(bit)]
            if name in self.pressed and target != record.NOTHING:
                pressed.add(word_names[target])
        for slot, back, offset in ((0, "R4", 17), (1, "L4", 21)):
            if back in self.pressed:
                mask = int.from_bytes(self.controller[image.SETTINGS + offset:image.SETTINGS + offset + 4], "little")
                if ram and slot in ram.mapping: mask = ram.mapping[slot]
                pressed.update(name for bit, name in enumerate(word_names) if mask & (1 << bit))
        axes = dict(self.axes)
        if installed:
            for side in ('lt', 'rt'):
                raw = round(axes[side] * 255)
                lo, hi = values[side + '_start'] * 255 // 100, values[side + '_end'] * 255 // 100
                axes[side] = (0 if raw <= lo else 255 if raw >= hi else (raw - lo) * 255 // (hi - lo)) / 255
            dz_off = ram.dead_zone_off if ram and ram.dead_zone_off is not None else self.controller[image.SETTINGS + 6] == 1
            for side, xkey, ykey, shift in (('l','lx','ly',0),('r','rx','ry',2)):
                x, y = round(axes[xkey] * 32767), round(axes[ykey] * 32767)
                radius = math.isqrt(x*x + y*y)
                dz = 0 if dz_off else values['dz_' + side] * 32767 // 100
                outer = values['outer_' + side] * 32767 // 100
                magnitude = 0 if radius <= dz else min(65535, (radius - dz) * 32767 // (outer - dz))
                if magnitude < 32767:
                    square = magnitude * magnitude // (2 * 32767)
                    curve = values['curve_' + side]
                    magnitude = square * 2 if curve == 1 else magnitude + magnitude // 2 - square if curve == 2 else magnitude
                for key, raw, bit in ((xkey,x,0),(ykey,y,1)):
                    value = max(-1, min(1, raw * magnitude / (max(radius,1) * 32767)))
                    axes[key] = -value if values['invert'] & (1 << (shift + bit)) else value
        if axes['lt'] > .1: pressed.add('LT')
        if axes['rt'] > .1: pressed.add('RT')
        profile = self.profile
        has_xbox = self._bt_xbox_active()
        if profile.startswith('controller-bluetooth'):
            profile = 'controller-bluetooth-xinput' if has_xbox else 'controller-bluetooth'
        return {"dongle_attached": self.dongle_attached, "usb_dinput": self.usb_dinput,
                "usb_switch_locked": self.usb_write_lock, "rear_switch": self.rear_switch, "reconnecting": self.reconnect_at is not None,
                "device": self.selected_device, "connection": self.selected_connection,
                "profile": profile, "connected": self.connected, "profiles": list(PROFILES),
                "hardware": {"battery_percent": self.battery_percent, "battery_mv": self.battery_mv,
                             "external_power": self.selected_device == "controller" and self.connection == "usb" and self.connected},
                "capabilities": {"bt_xbox": has_xbox, "dongle_dinput": self._dongle_dinput()},
                "ring": self.ring_state(), "raw_axes": dict(self.axes),
                "held": sorted(self.pressed),
                "motors": self.motor if time.monotonic() < self.motor_until else "off",
                "input": {"type": "state", "connected": live, "name": "Virtual 8BitDo Ultimate 2C",
                    "category": ("2.4 GHz dongle · " + ('D-input' if profile == 'dongle-dinput' else 'XInput')
                                 if self.connection == 'dongle' else
                                 "Bluetooth · " + ('XInput' if profile.endswith('xinput') else 'D-input')
                                 if self.connection == 'bluetooth' else "USB · " + ("D-input" if self.usb_dinput else "XInput")), "extended": True, "raw": False,
                    "foreground": True, "localities": (["default", "left", "right"] if self.connection != "bluetooth" or ("bt_rumble" in self._controller_keys() and (has_xbox or self.controller[0x19f00:0x19f78] == (recipes.BLOBS/"btdmap.bin").read_bytes())) else []),
                    "battery": {"level": self.battery_percent / 100, "voltage_mv": self.battery_mv,
                                "state": ("full" if self.battery_percent == 100 else "charging") if self.selected_device == "controller" and self.connection == "usb" and self.connected else "discharging"}, "pressed": sorted(pressed if (self.connection == "usb" and self.usb_dinput) or profile in ("controller-bluetooth","dongle-dinput") else pressed - {"L4", "R4"}),
                    "analogTriggers": True, **axes}}

    def control(self, request):
        self._tick()
        action = request.get("action")
        buttons = {"A", "B", "X", "Y", "Home", "LB", "RB", "LT", "RT", "L3", "R3", "L4", "R4", "View", "Menu", "Up", "Down", "Left", "Right"}
        if action == 'battery':
            percent, mv = request.get('percent',self.battery_percent), request.get('millivolts',self.battery_mv)
            if type(percent) is not int or not 0 <= percent <= 100 or type(mv) is not int or not 0 <= mv <= 5000:
                raise ValueError('Battery needs 0..100 percent and 0..5000 mV')
            self.battery_percent, self.battery_mv = percent, mv
        elif action == 'dongle_usb':
            attached = request.get('connected')
            if type(attached) is not bool: raise ValueError('Invalid dongle USB state')
            self.dongle_attached = attached
            self.app_channel = None
        elif action == 'rear_switch':
            value = request.get('value')
            if value not in ('dongle', 'bluetooth'): raise ValueError('Invalid rear switch position')
            self._save_switch(value)
            if self.selected_device == 'controller' and self.connection != 'usb':
                if self.reconnect_at is not None or (self.connected and self.profile != 'dongle-idle'):
                    self._unplug_usb()
                else:
                    self.connection = self.selected_connection = value
                    self.profile = 'dongle-idle' if value == 'dongle' else self._wireless_profile()
                    self.connected = value == 'dongle'
        elif action == 'disconnect_usb':
            if self.selected_device != 'controller' or self.connection != 'usb':
                raise ValueError('The controller is not connected by USB')
            self._unplug_usb()
        elif action == "release":
            self.pressed.clear()
            self.axes['lt'] = self.axes['rt'] = 0
        elif action == "button":
            button, down = request.get("button"), request.get("down")
            if button not in buttons or type(down) is not bool: raise ValueError("Invalid button")
            if (button == 'Home' and down and button not in self.pressed and
                    self.selected_device == 'controller' and not self.profile.endswith('-boot') and
                    (not self.connected or self.profile == 'dongle-idle')):
                self.control({'action': 'power'})
                if 'bt_modes' in self._controller_keys() and self.pressed & {'B','X'}: self.home_fired=True
            if down: self.pressed.add(button)
            else: self.pressed.discard(button)
        elif action == "axis":
            key, value = request.get("axis"), request.get("value")
            if key not in self.axes or type(value) not in (int, float) or not (-1 if key not in ('lt','rt') else 0) <= value <= 1:
                raise ValueError("Invalid axis")
            self.axes[key] = value
        elif action == "connection":
            device, connection = request.get('device'), request.get('connection')
            choices = {'controller': {'update', 'usb', 'dongle', 'bluetooth'},
                       'dongle': {'update', 'usb'}}
            if device not in choices or connection not in choices[device]:
                raise ValueError('Invalid device or connection')
            enter_update = device == 'controller' and connection == 'usb' and ({'L4','R4'} <= self.pressed or {'LB','RB'} <= self.pressed)
            if enter_update: connection = 'update'
            self.home_since, self.update_since = None, None
            self.reconnect_at = None
            if device == "controller" and connection in ("dongle", "bluetooth"): self._save_switch(connection)
            self.selected_device, self.selected_connection = device, connection
            self.connection = 'dongle' if device == 'dongle' else 'usb' if connection == 'update' else connection
            if device == 'dongle':
                self.profile = 'dongle-boot' if connection == 'update' else 'dongle-idle'
            elif connection == 'dongle':
                self.profile = self._rf_startup()
            elif connection == 'bluetooth':
                self.profile = self._bt_startup()
            else:
                self.profile = 'controller-boot' if connection == 'update' else 'controller-usb'
            if device == "controller" and connection == "usb": self._usb_startup()
            self.connected, self.app_channel, self.live_ring = True, None, None
            self.pressed.clear()
            self.axes = dict(lx=0, ly=0, rx=0, ry=0, lt=0, rt=0)
        elif action == "profile":
            profile = request.get("profile")
            if profile not in PROFILES: raise ValueError("Invalid profile")
            if profile == 'controller-bluetooth-xinput' and 'bt_xbox' not in self._controller_keys():
                raise ValueError('Install XInput mode over Bluetooth first. Original firmware uses Bluetooth D-input.')
            self.reconnect_at = None
            self.selected_device, self.selected_connection = self._selection(profile)
            self.profile, self.connected, self.app_channel = profile, True, None
            self.live_ring = None
            self.connection = "dongle" if profile.startswith("dongle-") else "bluetooth" if profile.startswith("controller-bluetooth") else "usb"
            self.pressed.clear()
            self.axes = dict(lx=0, ly=0, rx=0, ry=0, lt=0, rt=0)
        elif action == "power":
            if self.connection == "usb": raise ValueError("Disconnect USB before turning the controller off")
            if self.selected_device == "dongle": raise ValueError("Select Controller to power the controller on or off")
            if self.profile.endswith('-boot'): raise ValueError("Choose a normal-mode profile before using Power")
            if self.reconnect_at is not None:
                self.reconnect_at = None
                self.profile = 'dongle-idle' if self.connection == 'dongle' else self.profile
                self.connected = self.connection == 'dongle'
            elif self.connected and self.profile != 'dongle-idle':
                if self.is_dongle: self.profile = 'dongle-idle'
                else: self.connected = False
            else:
                if self.connection == 'usb':
                    self._usb_startup()
                    self.profile, self.connected = 'controller-usb', True
                else:
                    if 'Y' in self.pressed: raise ValueError("S/Pro Controller startup is not implemented")
                    if self.connection == 'dongle':
                        mode = 'd' if self._rf_startup() == 'dongle-dinput' else 'x'
                    else:
                        mode = 'x' if self._bt_startup().endswith('xinput') else 'd'
                    self.connected = True
                    self.profile = ('dongle-dinput' if mode == 'd' else 'dongle-xinput') if self.connection == 'dongle' else ('controller-bluetooth' if mode == 'd' else 'controller-bluetooth-xinput')
            self.app_channel = None
            self.live_ring = None
            self.ring_chord_since, self.ring_chord_fired, self.ring_save_at = None, False, None
        elif action == "ring":
            level, save = request.get('level'), request.get('save', False)
            keys, ring = self._controller_keys(), self.ring_state()
            if self.connection != 'bluetooth' or not self._bt_xbox_active() or not {'bt_xbox', 'bt_rumble'} <= set(keys) or not ring['available']:
                raise ValueError('Bluetooth ring requires Xbox, Rumble and Home ring tweaks')
            if type(level) is not int or level not in (*range(len(recipes.RING_ORDER)),255) or type(save) is not bool:
                raise ValueError('Invalid ring command')
            if level != 255: self.live_ring = level
            if save:
                self.controller[image.RING_SAVED] = (ring['installed'] << 4) | self.ring_state()['live']
        elif action == "vibrate":
            if self.connection == 'bluetooth' and ('bt_rumble' not in self._controller_keys() or (not self._bt_xbox_active() and self.controller[0x19f00:0x19f78] != (recipes.BLOBS/'btdmap.bin').read_bytes())):
                raise ValueError('Bluetooth vibration needs the Xbox profile and Rumble over Bluetooth tweak')
            locality = request.get('locality')
            if locality not in ('default','left','right'): raise ValueError("Invalid motor")
            self.motor, self.motor_until = locality, time.monotonic() + .45
        else: raise ValueError("Unknown simulation action")
        return self.virtual_state()


@contextmanager
def installed(store, profile="controller-usb", disconnect_after=None, preferences_path=None):
    """Install in one dedicated process; restore every patched boundary on exit."""
    from .web import server
    from .transport import windows
    store = Path(store)
    with ExitStack() as stack:
        for owner, key in ((cli, "find"), (cli, "open_channel"), (recipes, "_constants"), (cli.fetch, "fetch_stock")):
            stack.enter_context(patch.object(owner, key, getattr(owner, key)))
        stack.enter_context(patch.dict(os.environ, U2C_SIM="1"))
        sim.install(store)
        fixture = store / "controller-sim.bin"
        controller = fixture.read_bytes()
        fixture.unlink()  # fixture is memory initialization, not a user-created backup
        if profile == "controller-bluetooth-xinput":
            flash = bytearray(controller)
            stock = controller[image.BASE:image.BANK_A_END]
            built = recipes.build(stock, ["bt_xbox"])
            flash[image.BASE:image.BASE + len(built)] = built
            controller = bytes(flash)
        receiver, digest = dongle_fixture()
        stack.enter_context(patch.object(dongle, "STOCK_SHA256", digest))
        world = World(profile, controller, receiver, disconnect_after, preferences_path)
        stack.enter_context(patch.object(server, "SIMULATION_WORLD", world))
        stack.enter_context(patch.object(cli, "find", world.find))
        stack.enter_context(patch.object(cli, "open_channel", world.open))
        # Firmware routes are simulated; diagnostic side channels must never
        # vibrate or enumerate a real controller from this process.
        class NoHardware:
            def snapshot(self):
                return {"available": False, "connected": False, "type": "state"}
            def status(self):
                return {"available": False}
            def rumble(self, *args):
                return {"ok": False, "error": "Input/vibration is not emulated in the firmware laboratory."}
            def stop(self, *args):
                return {"ok": True}
        for name in ("WINDOWS_STOCK_GAMEPAD", "WINDOWS_RUMBLE", "HID_RUMBLE", "GAMEPAD"):
            stack.enter_context(patch.object(server, name, NoHardware()))
        stack.enter_context(patch.object(server, "windows_battery", lambda: {"level": -1, "state": "unknown"}))
        def no_bluetooth(*args, **kwargs):
            raise OSError("Bluetooth output is not emulated")
        stack.enter_context(patch.object(windows, "set_bluetooth_ring", no_bluetooth))
        (store / "SIMULATED.txt").write_text("Synthetic firmware. These files must never be installed on hardware.\n", encoding="utf-8")
        try:
            yield world
        finally:
            import logging
            log = logging.getLogger("u2c_customfw")
            for handler in list(log.handlers):
                if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).parent == store.resolve():
                    log.removeHandler(handler)
                    handler.close()
