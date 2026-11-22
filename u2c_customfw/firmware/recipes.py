# SPDX-License-Identifier: GPL-3.0-or-later
"""The tweaks, as hash-checked edits to stock v1.09 bank A, and identification of an image.

No 8BitDo bytes live here: an edit's stock content is known only by its SHA-256 (stock_hashes.py), and the rest of
bank A by the hash of stock with every edit region zeroed. Addresses are image addresses (flash = image).
"""
import hashlib
import struct
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import image
from .image import BASE, BANK_A_SIZE
from ..transport.ring_protocol import SERVICE_UUID, LEVEL_UUID

BLOBS = Path(__file__).parent / "blobs"
NAME = b"8BitDo Ultimate 2C (X)"
PREVIOUS_BT_NAME = "8BitDo Ultimate 2C (X/S)"
OLD_NAME = b"8BitDo Ultimate 2C (One)"  # what versions before 0.2 installed: still recognised, never written
OLD_NAME_LEN = 27
CABLE_NAME = "8BitDo Ultimate 2C (Wired)"   # by cable, in the same style as NAME: a suggestion, written only if chosen
STOCK_CABLE_NAME = "8BitDo Ultimate 2C Wireless Controller"   # 8BitDo's, which the dongle uses too; still recognised
PACKER_SLOT = (0xB908, 0xBAD4)
RING_LEVELS = (("ring_off", "Home ring: off", 0), ("ring_0_1", "Home ring: 0.1 %", 1), ("ring_1", "Home ring: 1 %", 10),
               ("ring_5", "Home ring: 5 %", 50), ("ring_25", "Home ring: 25 %", 250),
               ("ring_normal", "Home ring: normal", 0xFFFF))  # PWM1 compare value, period 1000; normal doesn't use it
OLD_RING_LEVELS = RING_LEVELS[:5]                             # what 0.2.0 could install
RING_ORDER = ("ring_normal", "ring_25", "ring_5", "ring_1", "ring_0_1", "ring_off")  # the firmware's level numbers
RING_CODE = (0x18400, 0x18FF0)                                # blank on stock
ALERTS_FLAG = 0x18FF0
SETTINGS_FLAG = 0x18FF1
REST_FLAG = 0x18FF2
LONG_RUMBLE_CODE = (0x1734C, 0x173C0)  # the stock Report Map: unused once bt_xbox points the Report Map attribute at its own
STICK_THRESHOLD = 14  # stock's is 20; noise at rest spans about 25 counts, and 14 lets it through about once every 2 s


class RecipeError(Exception):
    pass


@dataclass(frozen=True)
class Edit:
    addr: int
    new: bytes
    superseded_by: str | None = None  # recipe whose edit takes over this region when it is present
    accepts: tuple[bytes, ...] = ()   # earlier contents of this edit that still count as installed
    text: Callable[[str], bytes] | None = None   # a name edit: its bytes for a name (new is the default name's)

    copy_from: int | None = None  # derived from the already-built image; no vendor bytes shipped
    copy_overrides: tuple[tuple[int, bytes], ...] = ()

    def content(self, img):
        if self.copy_from is None: return self.new
        data=bytearray(img[self.copy_from-BASE:self.copy_from-BASE+self.size])
        for offset,value in self.copy_overrides:
            if offset < 0 or offset+len(value)>self.size: raise RecipeError('Copy fixup outside region')
            data[offset:offset+len(value)]=value
        return bytes(data)

    @property
    def size(self) -> int:
        return len(self.new)


@dataclass(frozen=True)
class NameSlot:
    """A name a recipe writes, chosen by the person: only text goes in, of printable ASCII, and each edit that depends on
    it (a length, a second copy) is written from it. Firmware holds a chosen name when the slot reads back as one and
    every one of the recipe's name edits is what that name gives."""
    label: str
    default: str
    max_len: int
    at: tuple[int, int]                    # the region the name is read back from
    read: Callable[[bytes], str | None]    # its text, or None if it isn't one written by this slot
    suggestion: str = ""                   # offered in the app; never written unless chosen


@dataclass(frozen=True)
class Recipe:
    key: str
    title: str
    requires: tuple[str, ...]
    edits: tuple[Edit, ...]
    group: str | None = None  # at most one recipe of a group can be installed
    requires_group: str | None = None  # one recipe of this group must be installed too
    earlier_form_of: str | None = None  # how an earlier version installed that recipe: recognised, not installable
    name: NameSlot | None = None


@dataclass(frozen=True)
class State:
    known: bool
    recipes: tuple[str, ...] = ()
    outdated: bool = False  # has a tweak in the form an earlier version installed it
    older: bool = False     # a tweak's code is an earlier version's (an accepted form): installing again updates it
    names: dict[str, str] = field(default_factory=dict)  # recipe -> the name it holds, for the recipes with a NameSlot


def _blob(name: str) -> bytes:
    return (BLOBS / name).read_bytes()


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _usb_string(text: str) -> bytes:
    return bytes([2 + 2 * len(text), 3]) + text.encode("utf-16-le")


def valid_name(text, max_len: int) -> bool:
    return (isinstance(text, str) and 0 < len(text) <= max_len and all(" " <= c <= "~" for c in text)
            and text == text.strip())


def _read_bt_name(region: bytes) -> str | None:   # the GATT name: the text, then zeros
    text = region.split(b"\0", 1)[0]
    if region[len(text):] != bytes(len(region) - len(text)):
        return None
    text = text.decode("latin-1")
    return text if valid_name(text, OLD_NAME_LEN) else None


def _read_usb_string(region: bytes) -> str | None:   # a string descriptor, then zeros
    n = region[0]
    if region[1] != 3 or n % 2 or not 4 <= n <= len(region) or region[n:] != bytes(len(region) - n):
        return None
    units = [int.from_bytes(region[i:i + 2], "little") for i in range(2, n, 2)]
    text = "".join(map(chr, units))
    return text if valid_name(text, (len(region) - 2) // 2) else None


def _make() -> dict[str, Recipe]:
    slot = PACKER_SLOT[1] - PACKER_SLOT[0]
    bt_name = lambda n: n.encode().ljust(OLD_NAME_LEN, b"\0")
    scan_name = lambda n: bytes([len(n) + 1, 9]) + n.encode().ljust(OLD_NAME_LEN, b"\0")
    cable_name = lambda n: _usb_string(n).ljust(0x4E, b"\0")

    def named(addr, text, **kw):
        return Edit(addr, text(DEFAULT_NAMES[addr]), text=text, **kw)
    return {r.key: r for r in (
        Recipe("bt_xbox", "XInput mode over Bluetooth", (), (
            Edit(PACKER_SLOT[0], _blob("packer.bin").ljust(slot, b"\0")),
            Edit(0x18274, _blob("report-map.bin")),
            Edit(0x18390, _blob("noop.bin"), superseded_by="bt_rumble"),
            Edit(0x177A0, _u32(len(_blob("report-map.bin")))),   # Report Map attribute: length
            Edit(0x177A8, _u32(0x18274)),                        # ... and value pointer
            Edit(0x17764, _u32(0x18391), superseded_by="bt_rumble_long"),  # output report write callback (Thumb-style odd)
            Edit(0x173C0, bytes.fromhex("025e04130b0905")),      # PnP ID: USB-IF, 045e:0b13, v0509
            Edit(0x18098, bytes.fromhex("0302")),                # output report reference: ID 3, output
            Edit(0x172C0, bytes.fromhex("c403")),                # GAP appearance: gamepad
            named(0x172DC, bt_name, accepts=(OLD_NAME.ljust(OLD_NAME_LEN,b"\0"),)),   # GATT device name
            named(0x17488, lambda n: _u32(len(n))),               # ... and its length
            named(0x17254, scan_name, accepts=(bytes([len(OLD_NAME) + 1, 9]) + OLD_NAME.ljust(OLD_NAME_LEN,b"\0"),)),  # scan response name
            named(0xA214, lambda n: bytes([len(n) + 2, 0xA1])),   # scan response length immediate
        ), name=NameSlot("Bluetooth name", NAME.decode(), OLD_NAME_LEN, (0x172DC, OLD_NAME_LEN), _read_bt_name)),
        Recipe("bt_rumble", "Rumble over Bluetooth", ("bt_xbox",), (
            Edit(0x18390, _blob("rumble.bin").ljust(0x70, b"\0"),
                 accepts=(_blob("rumble02.bin").ljust(0x70, b"\0"), _blob("rumble03.bin").ljust(0x70, b"\0"))),
        )),
        Recipe("bt_rumble_long", "Long rumble over Bluetooth", ("bt_rumble",), (
            Edit(LONG_RUMBLE_CODE[0], _blob("longrumble.bin").ljust(LONG_RUMBLE_CODE[1] - LONG_RUMBLE_CODE[0], b"\0")),
            Edit(0xFA72, _blob("lrhooka.bin")),                  # motor_set: any set with a hold ends the repeats
            Edit(0xFA88, _blob("lrhookb.bin")),                  # motor_set: once the hold runs out, the next repeat, while connected
            Edit(0x17764, _blob("lrwrite.bin")),                 # the write callback: long_write, rumble_write and then the repeats
        )),
        Recipe("bt_modes", "Bluetooth X / D mode selection", ("bt_xbox",), (
            Edit(0xA410, _blob("btrfstart.bin"), accepts=(bytes.fromhex("0390809b"),)),
            Edit(0x76D2, bytes((0,255)), accepts=(bytes((0,0x46)),)),
            Edit(0xA138, _blob("btmac.bin"), accepts=(bytes.fromhex("07902698"),_blob("btmac03.bin"))),
            Edit(0xA184, _blob("btbonds.bin"), accepts=(bytes.fromhex("0b90129d"),_blob("btbonds02.bin"),_blob("btbonds03.bin"))),
            Edit(0xA478, _blob("bthome.bin"), accepts=(bytes.fromhex("02901a9e"),_blob("bthome03.bin"))),
            Edit(0xA4B8, _blob("btstart.bin")),
            Edit(0xA318, _blob("bttable.bin"), accepts=(_blob("bttable01.bin"),_blob("bttable02.bin"),_blob("bttable03.bin"))),
            Edit(0xA216, _blob("btscan.bin"), accepts=(_blob("btscan01.bin"),_blob("btscan02.bin"),_blob("btscan03.bin"))),
            Edit(0xA2E4, _blob("btpack.bin"), accepts=(_blob("btpack01.bin"),_blob("btpack02.bin"),_blob("btpack03.bin"))),
            Edit(0x19000, bytes(51*24), copy_from=0x1743C, copy_overrides=(
                (0x177A0-0x1743C, _u32(len(_blob("btdmap.bin")))),
                (0x177A8-0x1743C, _u32(0x19F00)),
                (0x17764-0x1743C, _blob("btdnoop.bin")),
                (0x17488-0x1743C, _u32(22)),
                (0x17490-0x1743C, _u32(0x194D4)),
                (0x17598-0x1743C, _u32(0x194C8)),
                (0x17778-0x1743C, _u32(0x194D0)),
            )),
            Edit(0x194C8, _blob("btprofile.bin")),
            Edit(0x19540, _blob("btmodes.bin"), accepts=tuple(_blob(n).ljust(len(_blob("btmodes.bin")),b"\xff") for n in ("btmodes01.bin","btmodes02.bin","btmodes03.bin"))),
            Edit(0x19F00, _blob("btdmap.bin"), accepts=(_blob("btdmap01.bin"),)),
        )),
        Recipe("cable_360", "Xbox 360 controller over cable", (), (
            Edit(0x17994, bytes.fromhex("ffffff")),              # class, subclass, protocol
            Edit(0x17998, bytes.fromhex("5e048e02")),            # 045e:028e
            # the cable's USB product string, CABLE_NAME unless another is chosen; firmware with 8BitDo's name here (the
            # dongle's too) reads as this tweak as well. The "Wired Controller" one at 0x179f4 is the battery-less 2C Wired's, picked at 0xe902
            named(0x1792C, cable_name),
            named(0xE91C, lambda n: bytes([2 + 2 * len(n)])),     # ... and the length it's sent with: movs r3, #n
        ), name=NameSlot("Name over cable", STOCK_CABLE_NAME, 38, (0x1792C, 0x4E), _read_usb_string, CABLE_NAME)),
        Recipe("dim_leds", "Dimmer LEDs", (), (
            Edit(0x7738, bytes(156), superseded_by="bt_switch2", copy_from=0x7738, copy_overrides=((0,_blob("led.bin")),)),                      # main_tick, run from RAM at 0x1738
            Edit(0xA43C, _blob("hook.bin")),                     # main loop calls main_tick instead of 0xfaa0
        )),
        *(Recipe(key + at, title, (), (
            Edit(0xA3FA, b"\0"),                                 # the ring doesn't light up at startup
            Edit(0xD4F0, _blob(f"ringhook{form}.bin")),          # LED task calls ring_tick for the ring
            Edit(0xF83C, _blob(f"vhooka{form}.bin"), superseded_by="usb_dinput", accepts=() if form else (_blob("vhooka-prebaseline.bin"),)) ,            # vendor commands reach ring_vendor
            Edit(0xF842, _blob(f"vhookb{form}.bin"), superseded_by="usb_dinput", accepts=() if form else (_blob("vhookb-prebaseline.bin"),)) ,
            Edit(0xA484, _blob("pollhook5.bin" if not form else f"pollhook{form}.bin"), superseded_by="cpu_rest",
                 accepts=(_blob("pollhook.bin"),) if not form else ()),  # main loop samples the L4 + R4 chord; cpu_rest takes the call over
            Edit(0x7640, bytes(0x90)),                           # dead RAM code, now the ring's and settings' state
            Edit(0x76D0, level.to_bytes(2, "little")),           # the level installed
            Edit(RING_CODE[0], _blob("ringtail5.bin" if not form else f"ringtail{form}.bin").ljust(RING_CODE[1] - RING_CODE[0], b"\xff"),
                 accepts=tuple(_blob(n).ljust(RING_CODE[1]-RING_CODE[0],b"\xff") for n in ("ringtail.bin","ringtail10.bin","ringtail5-pre12.bin")) if not form else ()),
        ), "ring", earlier_form_of=key if form else None)
            for form, at in (("", ""), ("03", "@0.3")) for key, title, level in RING_LEVELS),
        *(Recipe(f"{key}@0.2", title, (), (
            Edit(0xA3FA, b"\0"),
            Edit(0xD4F0, _blob("ringhook02.bin")),
            Edit(0x7640, _blob("ring02.bin").ljust(0x90, b"\0")),
            Edit(0x76D0, level.to_bytes(2, "little")),
        ), "ring", earlier_form_of=key) for key, title, level in OLD_RING_LEVELS),
        Recipe("battery_alerts", "Battery alerts", (), (
            Edit(ALERTS_FLAG, b"\1"),
        ), requires_group="ring"),
        Recipe("settings", "Controller settings", (), (
            Edit(0xDCE6, _blob("hgamea.bin")),                   # the games' rumble over 2.4 GHz ...
            Edit(0xEAF4, _blob("hgameb.bin")),                   # ... and by cable, at the chosen intensity
            Edit(0xA0F8, _blob("hbuzza.bin")),                   # vibrating on connecting, Bluetooth ...
            Edit(0xDAA8, _blob("hbuzzb.bin")),                   # ... and 2.4 GHz
            Edit(0xD1C8, _blob("hidle.bin")),                    # the idle limit, from the settings
            Edit(0x6A90, _blob("hstickl.bin")),                  # each stick's dead zone code, run from RAM at 0xa90 ...
            Edit(0x6ABC, _blob("hstickr.bin")),                  # ... and 0xabc
            Edit(0xC910, _blob("hscan.bin")),                    # the button scan: triggers and remapping
            Edit(0x7210, _blob("sram.bin").ljust(0xC0, b"\0")),   # scan_hook, run from RAM at 0x1210 (dead RAM code)
            Edit(SETTINGS_FLAG, b"\1"),                          # the code is there with any Home ring level: this says it's in use
        ), requires_group="ring"),
        Recipe("fixes", "Connection and idle fixes", (), (
            Edit(0xA480, _blob("hidletask.bin")),                # sticks count as activity; a lost connection searches for its full time
            Edit(0xEA88, _blob("hunplug.bin")),                  # unplugging in the Bluetooth position restarts, to find the Mac at once
            Edit(0x178CC, _blob("hota.bin")),                    # the unencrypted Bluetooth firmware update's write handler
        ), requires_group="ring"),
        # The stock OTA service is the last service (handles 46..50). It is already
        # disabled by fixes; replace it with a read-only private service, keeping
        # the HID tables, report descriptors and all earlier handles unchanged.
        # No new executable code, RAM allocation or flash region is needed.
        Recipe("bt_ring_read", "Read Home ring brightness over Bluetooth", ("bt_xbox", "fixes"), (
            Edit(0x172C4, bytes((0x02, 48, 0)) + LEVEL_UUID.bytes[::-1]),
            Edit(0x172FC, LEVEL_UUID.bytes[::-1]),
            Edit(0x1731C, SERVICE_UUID.bytes[::-1]),
            Edit(0x1743C, _u32(48)),                            # highest attribute handle
            Edit(0x1788C, b"\x03\x00"),                      # service + declaration + value
            Edit(0x178BE, b"\x01"),                           # ATT read only
            Edit(0x178C0, _u32(2)),                             # ring init flag + live level
            Edit(0x178C8, _u32(0x841640)),                      # existing ring_state in RAM
        ), requires_group="ring"),
        Recipe("stick_precision", "Finer stick movement", (), (
            *(Edit(a, bytes([STICK_THRESHOLD])) for a in (0xB1F2, 0xB208, 0xB220, 0xB238)),  # the jitter filter's threshold
            Edit(0xA2DC, _blob("hsweep.bin")),                   # two ADC passes per report, so its average means something
        ), requires_group="ring"),
        Recipe("cpu_rest", "Processor rest", (), (
            Edit(0xA484, _blob("resthook.bin"), superseded_by="battery_log"),  # the main loop's 100 ms call feeds the watchdog first; takes over the ring's pollhook
            Edit(0x6020, _blob("wdog5.bin").ljust(0x94, b"\0"), accepts=(_blob("wdog.bin").ljust(0x94, b"\0"),)),  # rest_main_poll, run from RAM at 0x20 (dead RAM code)
            Edit(0x848C, _blob("stall.bin").ljust(0x74, b"\0")), # stall_hook, run from RAM at 0x248c (dead RAM code)
            Edit(0xA4AE, _blob("hstall.bin")),                   # the Bluetooth branch's motor call, then the stall
            Edit(REST_FLAG, b"\1"),
        ), requires_group="ring"),
        Recipe("battery_log", "Battery log", ("cpu_rest",), (   # installed with Processor rest, never shown
            Edit(0x9964, _blob("battlog.bin").ljust(0x9CAC - 0x9964, b"\0"), superseded_by="usb_dinput", accepts=(_blob("battlog11.bin").ljust(0x9CAC-0x9964,b"\0"),)),   # log_main_poll and battlog_poll, in the dead soft-float subtraction
            Edit(0xA484, _blob("loghook.bin")),                  # the main loop's 100 ms call: the log first, then rest_main_poll
        )),
        Recipe("usb_dinput", "USB XInput / D-input switching", (), (
            # Only USB mode changes arm this post-enumeration haptic callback.
            # The sector was erased (0xff) in all earlier builds; leave it
            # accepted for safe upgrade without reclassifying older firmware.
            Edit(0x198D0, _blob("usb-haptic-ready.bin"), accepts=(
                b"\xff" * len(_blob("usb-haptic-ready.bin")),
                _blob("usb-haptic-ready-prev.bin").ljust(len(_blob("usb-haptic-ready.bin")), b"\xff"),
            )),
            Edit(0x9964, _blob("usbmode.bin"), accepts=(_blob("usbmode-pre-haptic-ready.bin"), _blob("usbmode-pre-settings-switch-fix.bin"), _blob("usbmode-pre-battery-lock-fix.bin"), _blob("usbmode01.bin"), _blob("usbmode02.bin"), _blob("usbmode03.bin"), _blob("usbmode04.bin"), _blob("usbmode05.bin"), _blob("usbmode-pre12.bin"), _blob("usbmode-prevendor12.bin"), _blob("usbmode-prebaseline.bin"))),  # retains Battery log, then mode switching at 0x9a70
            # Use the stock wired D-input PID, avoiding the Bluetooth-only Steam/SDL parser.
            Edit(0x17AFE, bytes.fromhex("1d30"), superseded_by="usb_dinput_steam", accepts=(bytes.fromhex("1b30"),)),
            Edit(0xEA08, _u32(600000), accepts=(_u32(200000),)),  # 600 ms USB detach on a mode change
            Edit(0xE414, _blob("usbread.bin"), accepts=(_blob("usbread-pre-haptic-ready.bin"), bytes.fromhex("0090769b"),)),  # prioritize flash read replies over busy D-input reports
            Edit(0xA414, _blob("usbstart.bin"), accepts=(_blob("usbstart-pre-haptic-ready.bin"), _blob("usbstart-pre-settings-switch-fix.bin"), bytes.fromhex("0490fe9d"))),
            Edit(0xA488, _blob("usbhook.bin")),
            Edit(0xF83C, _blob("usbvendora.bin"), accepts=(_blob("usbvendora-pre-haptic-ready.bin"), _blob("usbvendora-pre-settings-switch-fix.bin"), bytes.fromhex("fa976b99"))),  # wraps the Home ring/Settings vendor handlers
            Edit(0xF842, _blob("usbvendorb.bin"), accepts=(_blob("usbvendorb-pre-haptic-ready.bin"), _blob("usbvendorb-pre-settings-switch-fix.bin"), bytes.fromhex("fa977099"))),
        ), requires_group="ring"),
        Recipe("usb_dinput_steam", "USB DInput: Steam-compatible Bluetooth mapping", ("usb_dinput",), (
            Edit(0x17AFE, bytes.fromhex("1b30")),  # Steam's known BT-DInput VID/PID; requires the same data layout
            Edit(0x17B74, _blob("usbsteam-map.bin")),  # reorder input fields; keep vendor 02/81 read/write reports
            Edit(0xEF88, _blob("usbsteamhook.bin")),  # only the stock USB DInput input packer
            Edit(0x19F80, _blob("usbsteam.bin")),  # 44 unused bytes following the Bluetooth DInput map
        )),
        Recipe("battery_table", "Battery level from a measured discharge", (), (   # in every install, never shown
            Edit(0x9CEC, _blob("battpct.bin").ljust(0x9D40 - 0x9CEC, b"\0")),   # battery_pct, in the dead count-leading-zeros and ffs
            Edit(0xB5B6, _blob("hpcta.bin")),                    # 0xb5a8 jumps over stock's linear formula ...
            Edit(0xB5C0, _blob("hpctb.bin")),                    # ... to the call that now gives the voltage to battery_pct
            Edit(0xB62C, _u32(3300)),                            # 1 % without smoothing only below 3300 mV, not below 3499
        )),
        # Experimental: a trailer only, leaving stock input bytes and checksum
        # untouched. RF payload length is extended from 20/22 to 28/30 bytes.
        # This may be incompatible with the receiver! Check 2.4GHz connection
        # and be prepared to restore controller firmware by USB.
        Recipe("rf_battery_tx", "Send battery status over 2.4 GHz", (), (
            Edit(0xA350, _blob("rf-battery-hook.bin")),
            Edit(0x19A00, _blob("rf-battery.bin")),
            Edit(0xDD82, bytes((0x1d,))),  # packet size 21 -> 29
            Edit(0xDD86, bytes((0x1c,))),  # payload size 20 -> 28
            Edit(0xDD92, bytes((0x1f,))),  # extended packet size 23 -> 31
            Edit(0xDD96, bytes((0x1e,))),  # extended payload size 22 -> 30
            Edit(0xDDC0, bytes((0x1c,))),  # copy 28, not 20, bytes from 0x843980
        )),
        Recipe("ble_interval", "Bluetooth connection request of 12.5 ms", (), (
            Edit(0xA0DE, bytes.fromhex("0aa00aa100a2c8a3"), superseded_by="ble_fast"),  # min = max 12.5 ms, latency 0, timeout 2 s, instead of 7.5 ms and latency 59
        )),
        Recipe("fast_scan", "Faster button reading", (), (
            Edit(0xD288, (40000).to_bytes(4, "little")),         # the scan period, in 16 MHz ticks: 2.5 ms instead of 5
            Edit(0xC8E2, bytes.fromhex("13aa")),                 # Home still needs 50 ms held: 20 scans
            Edit(0xC8E6, bytes.fromhex("14a2")),
            Edit(0xC640, bytes.fromhex("0bae")),                 # turbo still switches every 30 ms: 12 scans
        )),
        Recipe("ble_fast", "Faster Bluetooth input", ("ble_interval",), (
            Edit(0xA0DE, bytes.fromhex("06a006a100a2c8a3")),     # min = max 7.5 ms, the shortest Bluetooth allows; latency 0, timeout 2 s
        )),
    )}


DEFAULT_NAMES = {a: NAME.decode() for a in (0x172DC, 0x17488, 0x17254, 0xA214)} | {a: STOCK_CABLE_NAME for a in (0x1792C, 0xE91C)}
RECIPES: dict[str, Recipe] = _make()
# Y owns these hooks while installed; D/X dispatch remains in its payload.
RECIPES["bt_modes"] = replace(RECIPES["bt_modes"], edits=tuple(
    replace(e, superseded_by="bt_switch2") if e.addr in (0xa138,0xa184,0xa2e4,0xa478,0xa4b8) else e
    for e in RECIPES["bt_modes"].edits))
RECIPES["bt_switch2"] = Recipe("bt_switch2", "NS2 Pro Controller mode (Y)",
    ("bt_modes", "dim_leds", "settings", "cpu_rest"), tuple(
        Edit(address, _blob(f"switch2-{address:x}.bin"))
        for address in (0x7738,0xa138,0xa184,0xa2e4,0xa478,0xa4b8,0xa22a,0xa23a,0xd156,0x11938,0x11a00,0x14a62,0x1a000)))
# Only current Y patch bytes are recognized. Earlier experimental blobs are
# intentionally not accepted; unknown test firmware must be restored from a
# verified backup rather than silently migrated through obsolete patches.



def _constants(stock_sha256, region_sha256, remainder_sha256):
    if stock_sha256 is None or region_sha256 is None or remainder_sha256 is None:
        from . import stock_hashes
        return (stock_sha256 or image.STOCK_SHA256, region_sha256 or stock_hashes.REGION_SHA256,
                remainder_sha256 or stock_hashes.REMAINDER_SHA256)
    return stock_sha256, region_sha256, remainder_sha256


def whole(img: bytes) -> bytes:
    """The whole app area: a v1.09 payload gets its blank tail."""
    return img + b"\xff" * (image.APP_SIZE - len(img)) if len(img) in (BANK_A_SIZE, 0x19000-BASE, image.BANK_A_ERASE_END-BASE) else img


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _region(img: bytes, e: Edit) -> bytes:
    return img[e.addr - BASE:e.addr - BASE + e.size]


def masked(img: bytes) -> bytes:
    """The app area with every edit region zeroed: what no recipe touches."""
    out = bytearray(img)
    for r in RECIPES.values():
        for e in r.edits:
            out[e.addr - BASE:e.addr - BASE + e.size] = bytes(e.size)
    return bytes(out)


def build(stock: bytes, keys, stock_sha256=None, region_sha256=None, remainder_sha256=None, names=None) -> bytes:
    """The app area: stock plus the chosen tweaks, each name slot with its default or the name in names (recipe -> text;
    "" is the default too). Refuses anything but stock, an unknown tweak, a missing dependency or a name that isn't one."""
    stock_sha, regions, _ = _constants(stock_sha256, region_sha256, remainder_sha256)
    names = {k: v for k, v in (names or {}).items() if v != ""}
    if len(stock) != BANK_A_SIZE or _sha(stock) != stock_sha:
        raise RecipeError("the base image is not stock v1.09 bank A")
    keys = list(keys)
    for k in keys:
        if k not in RECIPES or RECIPES[k].earlier_form_of:
            raise RecipeError(f"unknown tweak {k!r}")
        for need in RECIPES[k].requires:
            if need not in keys:
                raise RecipeError(f"{k} needs {need}")
        if RECIPES[k].requires_group and not any(RECIPES[o].group == RECIPES[k].requires_group for o in keys):
            raise RecipeError(f"{k} needs a Home ring level")
    groups = [RECIPES[k].group for k in keys if RECIPES[k].group]
    if len(groups) != len(set(groups)):
        raise RecipeError("choose only one Home ring level")
    for k, text in names.items():
        slot = RECIPES[k].name if k in RECIPES else None
        if slot is None or k not in keys:
            raise RecipeError(f"a name for {k}, which isn't being installed or has no name")
        if not valid_name(text, slot.max_len):
            raise RecipeError(f"The {slot.label.lower()} must be 1 to {slot.max_len} characters of letters, digits, spaces and "
                              "punctuation, without spaces at either end.")
    base = whole(stock)
    img = bytearray(base)
    for key, r in RECIPES.items():  # recipe order: a dependent's edit lands over the one it supersedes
        if key not in keys:
            continue
        for e in r.edits:
            if _sha(_region(base, e)) != regions[(e.addr, e.size)]:
                raise RecipeError(f"{key}: stock bytes at {e.addr:#x} are not as expected")
            if e.copy_from is None:
                img[e.addr - BASE:e.addr - BASE + e.size] = e.text(names[key]) if e.text and key in names else e.new
    for key in keys:
        for e in RECIPES[key].edits:
            if e.copy_from is not None and e.superseded_by not in keys: img[e.addr-BASE:e.addr-BASE+e.size]=e.content(img)
    return bytes(img)


def _taken_over_by(o: Edit, e: Edit) -> bool:
    """Whether e's recipe takes o's region over, itself or through the recipes that take it over in turn."""
    k = o.superseded_by
    while k:
        if e in RECIPES[k].edits:
            return True
        k = next((x.superseded_by for x in RECIPES[k].edits if x.addr == o.addr), None)
    return False


def identify(bank_a: bytes, stock_sha256=None, region_sha256=None, remainder_sha256=None) -> State:
    """Which tweaks an app area holds, or unknown if any edit region is neither stock nor a tweak's, or the rest differs.
    A bare payload counts as having a blank tail."""
    _, regions, remainder = _constants(stock_sha256, region_sha256, remainder_sha256)
    bank_a = whole(bank_a)
    if len(bank_a) != image.APP_SIZE or _sha(masked(bank_a)) != remainder:
        return State(False)

    held = {}   # (addr, size) of each name edit -> the name its recipe's slot reads back, if every name edit agrees
    for k, r in RECIPES.items():
        text = r.name and r.name.read(bank_a[r.name.at[0] - BASE:r.name.at[0] - BASE + r.name.at[1]])
        if text is not None and all(_region(bank_a, e) == e.text(text) for e in r.edits if e.text):
            held |= {(e.addr, e.size): text for e in r.edits if e.text}

    accepted = set()   # (addr, size) of edits that hold an earlier version's bytes

    def edit_state(e):
        got = _region(bank_a, e)
        # An older USB DInput tweak used PID 301b while retaining the original
        # wired report layout. Do not mistake that single historical PID for
        # a partially installed new Steam layout recipe. Other edits of the
        # new recipe must remain exactly stock for this compatibility path.
        usb_steam = RECIPES["usb_dinput_steam"].edits
        if (e is usb_steam[0] and got == bytes.fromhex("1b30") and
                all(_sha(_region(bank_a, other)) == regions[(other.addr, other.size)]
                    for other in usb_steam[1:])):
            return "stock"
        if e.text and (e.addr, e.size) in held:   # a name, which may happen to equal 8BitDo's bytes here: as the recipe's other edits
            return "any" if _sha(got) == regions[(e.addr, e.size)] else "new"
        if got == e.content(bank_a) or got in e.accepts:
            if got != e.content(bank_a):
                accepted.add((e.addr, e.size))
            return "any" if _sha(got) == regions[(e.addr, e.size)] else "new"
        if _sha(got) == regions[(e.addr, e.size)]:
            return "stock"
        # the region a tweak takes over from another's (noop callback -> rumble) reads as untouched while the other is present,
        # also down a chain (the ring's pollhook -> cpu_rest's resthook -> battery_log's loghook)
        if any(o.addr == e.addr and _taken_over_by(o, e) and (got == o.content(bank_a).ljust(e.size, b"\0") or got in o.accepts)
               for r in RECIPES.values() for o in r.edits):
            return "stock"
        return "other"

    plain = {k: [x for x in (edit_state(e) for e in r.edits if e.superseded_by is None) if x != "any"] for k, r in RECIPES.items()}
    present = {k for k, s in plain.items() if s and all(x == "new" for x in s)}
    chosen = {RECIPES[k].group for k in present} - {None}
    for k, r in RECIPES.items():
        if r.group in chosen and k not in present:  # the group's other options share regions with the installed one
            continue
        s = plain[k] + [x for x in (edit_state(e) for e in r.edits if e.superseded_by is not None and e.superseded_by not in present) if x != "any"]
        if "other" in s or ("new" in s and "stock" in s):
            return State(False)
    def holds(k):   # every edit it still owns is its own; one whose edits all equal 8BitDo's bytes is absent
        s = [edit_state(e) for e in RECIPES[k].edits if e.superseded_by not in present]
        return all(x in ("new", "any") for x in s) and not (s and all(x == "any" for x in s))
    present = {k for k in RECIPES if holds(k)}
    for k in present:
        if any(need not in present for need in RECIPES[k].requires):
            return State(False)
        if RECIPES[k].requires_group and not any(RECIPES[o].group == RECIPES[k].requires_group for o in present):
            return State(False)
    keys = {RECIPES[k].earlier_form_of or k for k in present}
    chosen_names = {k: held[(e.addr, e.size)] for k in present for e in RECIPES[k].edits if e.text and (e.addr, e.size) in held}
    older = any((e.addr, e.size) in accepted for k in present for e in RECIPES[k].edits if not e.text and e.superseded_by not in present)
    return State(True, tuple(sorted(keys)), any(RECIPES[k].earlier_form_of for k in present), names=chosen_names, older=older)
