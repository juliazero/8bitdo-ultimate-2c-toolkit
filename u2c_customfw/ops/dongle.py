# SPDX-License-Identifier: GPL-3.0-or-later
"""The 2.4 GHz dongle: read its firmware, back it up, and write its bank A with any of its tweaks.

Reads are the controller's `81 05 05` (protocol/app.py), idle (2dc8:301c) or paired. A write follows the stream proven on
this dongle (docs, dongle-analysis): `81 05 00 51 00` on the idle dongle, which restarts it into its bootloader
(2dc8:3208, identity 0x301c); there, the bootloader must match the backup; erase 0x6000-0x10000, write all but the first
46 bytes, which hold the app header, read back, write the header last, read the whole bank back, restart with cmd 7. If
anything stops before the header is written, the bootloader finds no app and stays in update mode, where Restore works.
Nothing outside bank A is ever written: the update record, bank B, the calibration and the licence sector stay as they are."""
import datetime
import errno
import hashlib
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import protocol
from ..firmware.recipes import BLOBS
from ..protocol import app, boot
from .plan import OpError
from .probe import parse_version

BASE, SIZE = 0x6000, 0xA000   # bank A: the whole application
END = BASE + SIZE
FLASH, BOOT_END, CAL = 0x20000, 0x6000, 0x1E000   # the whole part, the bootloader, a few unit-specific calibration bytes
RECORD = 0x12000              # the update record; the version times 100 at +0x0c
STOCK_SHA256 = "15caaa7c705629c768dc7c22417abf047cb06b499376c5c8b8fe14c0f606028c"     # 8BitDo's v1.03
CHUNK = 46                    # the largest read the dongle answers
DINPUT = (BLOBS / "dinput.bin").read_bytes()
RF_READ = (BLOBS / "dongle-rf-read.bin").read_bytes()
RF_HOOK = (BLOBS / "dongle-rf-hook.bin").read_bytes()   # link_mode, firmware-src/dongle.s
# Dongle stock DInput HID map is byte-identical to the controller's stock USB
# DInput map. The new descriptor reorders only its 9-byte input payload;
# service/output IDs 0x02 and 0x81 remain unchanged.
D_STEAM_MAP = (BLOBS / "usbsteam-map.bin").read_bytes()
D_STOCK_MAP = (D_STEAM_MAP[:8] + D_STEAM_MAP[68:92] + D_STEAM_MAP[8:31]
               + D_STEAM_MAP[31:51] + D_STEAM_MAP[51:68] + D_STEAM_MAP[92:])
D_STEAM_CODE = (BLOBS / "dongle-steam.bin").read_bytes()
D_STEAM_HOOK = (BLOBS / "dongle-steam-hook.bin").read_bytes()
TWEAKS = {  # key: (title, edits as (address, 8BitDo's bytes, the tweak's bytes))
    "dongle_360": ("Xbox 360 controller over 2.4 GHz", (
        (0xF1A0, bytes.fromhex("12 01 00 02 00 00 00 08 c8 2d 0a 31 14 01 01 02 03 01"),   # the X-input device descriptor,
         bytes.fromhex("12 01 00 02 ff ff ff 08 5e 04 8e 02 03 01 01 02 03 01")),          # as 045e:028e, class ff, bcdDevice 0x0103
    )),
    "dongle_dinput": ("D-input mode with B + Home", (
        (0x6940, bytes.fromhex("0d 90 72 9a"), bytes.fromhex("0f 90 02 9b")),   # at link-up (RAM code, run at 0x940): bl 0xde28 -> bl 0xff48
        (0xFF48, bytes(len(DINPUT)), DINPUT),                                   # link_mode, in the zeros after the image
    )),
    "dongle_dinput_steam": ("DInput: Steam-compatible Bluetooth mapping", (
        # Wired/dongle DInput is normally 2dc8:301c. A Bluetooth-style report
        # with PID 301b is only safe when both the data layout AND the HID
        # descriptor are transformed together. XUSB VID/PID is untouched.
        (0xF2F2, bytes.fromhex("1c30"), bytes.fromhex("1b30")),
        (0xF334, D_STOCK_MAP, D_STEAM_MAP),
        # Called only from the mode==1 handler image 0x773C -> 0x74E8,
        # executed from RAM 0x173C -> 0x14E8. The hook stored at image
        # 0x7676 runs at 0x1676, so the BL MUST use the RAM PC when
        # targeting flash-resident helper 0xFFB0. The old image-PC-relative
        # branch landed at 0x9FB0 and crashed the DInput task.
        (0x7676, bytes.fromhex("09 a1 01 a2"), D_STEAM_HOOK),
        (0xFFB0, bytes(len(D_STEAM_CODE)), D_STEAM_CODE),
    )),
    "dongle_rf_tail": ("Capture extended RF payload (28 bytes)", (
        # After classification of RF report IDs 8D/8E/8F/90/92, the stock
        # receive handler at 0x6C60 copies 20 bytes from the 64-byte radio
        # staging buffer (r5+5) to the 32-byte diagnostic report buffer.
        # Extend ONLY the copy count; checksum and input processing still
        # operate on the stock first 20 bytes. Bytes 20..27 are experimental
        # and must not be interpreted unless a complete valid trailer exists.
        (0x6C64, bytes.fromhex("14 a2"), bytes.fromhex("1c a2")),
    )),
    "dongle_rf_read": ("Read RF packet over USB", (
        # 0xD598 calls the stock flash copier 0xCD94; optionally intercept only
        # 0x843108 (up to 32 bytes), leaving every flash read untouched.
        (0xD598, bytes.fromhex("ff 97 fc 9b"), RF_HOOK),
        (0xFF88, bytes(len(RF_READ)), RF_READ),
    )),
}


# The product string, a name per mode: the handler at 0xdbb4 sends 0xf144 in X-input and D-input alike, and 0xf508 (the
# Wukong edition's name) when 0x84305e == 2. A tweak's name goes with it: X-input's with dongle_360, at 0xf144. D-input's,
# with dongle_dinput, replaces the Wukong check with `cmp r3, #1` (the mode, still in r3), so D-input takes the branch to
# 0xf508, which holds the name. Each string's length is sent from a `movs r3, #len` before it. Checked by running the handler in an emulator.
@dataclass(frozen=True)
class DongleName:
    label: str
    default: str
    stock: str                                  # 8BitDo's string there: firmware with it reads as the tweak with no name chosen
    at: int
    size: int                                   # the slot: 8BitDo's string exactly
    length_at: int                              # movs r3, #len
    code: tuple[tuple[int, bytes, bytes], ...] = ()   # (address, 8BitDo's bytes, the name's) that make the slot the mode's
    suggestion: str = ""                        # offered in the app; never written unless chosen

    @property
    def max_len(self) -> int:
        return (self.size - 2) // 2


NAMES = {   # tweak -> its name
    # default: what a tweak writes when no name is chosen, 8BitDo's own; D-input's then stays as 1.03 has it (the X-input
    # string in both modes, the Wukong check untouched), and only a chosen D-input name brings in the code below
    "dongle_360": DongleName("X-input name", "8BitDo Ultimate 2C Wireless Controller", "8BitDo Ultimate 2C Wireless Controller", 0xF144, 0x4E, 0xDBC4,
                             suggestion="8BitDo Ultimate 2C (Xbox 360)"),
    "dongle_dinput": DongleName("D-input name", "8BitDo Ultimate 2C Wireless Controller", "8BitDo Ultimate 2C Wireless (WUKONG)", 0xF508, 0x4A, 0xDBD4,
                                ((0xDBBC, bytes.fromhex("080b1b2802ab"), bytes.fromhex("01ab01ab01ab")),),   # ldr r3, =0x84305e; ldrh r3, [r3]; cmp r3, #2 -> cmp r3, #1 x3
                                suggestion="8BitDo Ultimate 2C (D-input)"),
}


def valid_name(text, max_len: int) -> bool:
    return (isinstance(text, str) and 0 < len(text) <= max_len and all(" " <= c <= "~" for c in text)
            and text == text.strip())


def _slot(n: DongleName, text: str) -> bytes:
    return (bytes([2 + 2 * len(text), 3]) + text.encode("utf-16-le")).ljust(n.size, b"\0")


def _read_name(n: DongleName, bank: bytes) -> str | None:
    """The name a slot holds, if it's one: a string descriptor, zeros after it, and its length sent with it."""
    region = bank[n.at - BASE:n.at - BASE + n.size]
    k = region[0]
    if region[1] != 3 or k % 2 or not 4 <= k <= n.size or region[k:] != bytes(n.size - k) or bank[n.length_at - BASE] != k:
        return None
    text = "".join(chr(int.from_bytes(region[i:i + 2], "little")) for i in range(2, k, 2))
    return text if valid_name(text, n.max_len) else None


@dataclass
class DongleFirmware:
    known: bool
    version: float | None
    tweaks: tuple[str, ...]
    names: dict = field(default_factory=dict)   # tweak -> the name it holds; None for one installed before names


def _read(ch, addr: int, n: int, progress=None) -> bytes:
    out = bytearray()
    for a in range(addr, addr + n, CHUNK):
        c = app.read(ch, a, min(CHUNK, addr + n - a))
        if c is None:
            raise OpError(f"The dongle didn't answer at {a:#x}. Unplug it, plug it back in and try again.")
        out += c
        if progress and len(out) % 0x2000 < CHUNK:
            progress(len(out), n)
    return bytes(out)


def _analyse(bank: bytes, stock_sha256: str | None = None) -> tuple[bool, tuple[str, ...], dict]:
    base, on, names = bytearray(bank), [], {}
    for key, (_, edits) in TWEAKS.items():
        held = [bank[a - BASE:a - BASE + len(new)] for a, _, new in edits]
        if held == [new for _, _, new in edits]:
            on.append(key)
            for a, old, _ in edits:
                base[a - BASE:a - BASE + len(old)] = old
        elif held != [old for _, old, _ in edits]:
            return False, (), {}
    for key, n in NAMES.items():
        code = [bank[a - BASE:a - BASE + len(new)] for a, _, new in n.code]
        stock = code == [old for _, old, _ in n.code] and bank[n.at - BASE:n.at - BASE + n.size] == _slot(n, n.stock)
        if stock and (key not in on or n.code):   # 8BitDo's: no tweak, or one installed before names (D-input's string then unused)
            names |= {key: None} if key in on else {}
            continue
        text = _read_name(n, bank) if key in on else None
        if text is None or code != [new for _, _, new in n.code]:
            return False, (), {}
        names[key] = text
        base[n.at - BASE:n.at - BASE + n.size] = _slot(n, n.stock)
        base[n.length_at - BASE] = 2 + 2 * len(n.stock)
        for a, old, _ in n.code:
            base[a - BASE:a - BASE + len(old)] = old
    known = hashlib.sha256(base).hexdigest() == (stock_sha256 or STOCK_SHA256)
    return (known, tuple(on), names) if known else (False, (), {})


def state(bank: bytes, stock_sha256: str | None = None) -> tuple[bool, tuple[str, ...]]:
    """(known, its tweaks) for a bank A: 8BitDo's v1.03 by hash once each tweak found whole is undone, its name with it.
    A tweak found only in part, or a name that isn't one, makes it unknown."""
    return _analyse(bank, stock_sha256)[:2]


def names(bank: bytes, stock_sha256: str | None = None) -> dict:
    """tweak -> the name it holds (None for one installed before names), for the tweaks with a name."""
    return _analyse(bank, stock_sha256)[2]


def describe(tweaks) -> str:
    return f"firmware 1.03 with {' and '.join(t for k, (t, _) in TWEAKS.items() if k in tweaks)}" if tweaks else "the original firmware 1.03"


def read_firmware(ch, stock_sha256: str | None = None, in_boot: bool = False) -> DongleFirmware:
    """v1.03 as 8BitDo ships it or with tweaks, by hash; otherwise the version its update record gives.
    in_boot: ch is the bootloader's channel, for a dongle left in update mode."""
    read = (lambda a, n: boot.read(ch, a, n)) if in_boot else (lambda a, n: _read(ch, a, n))
    try:
        known, tweaks, held = _analyse(read(BASE, SIZE), stock_sha256)
        return DongleFirmware(True, 1.03, tweaks, held) if known else DongleFirmware(False, parse_version(read(RECORD, 16)), ())
    except protocol.ProtocolError as e:
        raise OpError(str(e)) from e


def unit_of(full: bytes) -> bytes:
    """What tells this dongle from another one: its bootloader and its calibration bytes, compared, never shown."""
    return full[:BOOT_END] + full[CAL:CAL + 0x40]


def read_unit(ch, in_boot: bool = False) -> bytes:
    read = (lambda a, n: boot.read(ch, a, n)) if in_boot else (lambda a, n: _read(ch, a, n))
    try:
        return read(0, BOOT_END) + read(CAL, 0x40)
    except protocol.ProtocolError as e:
        raise OpError(str(e)) from e


def backup(ch, store: Path, progress, in_boot=False) -> Path:
    """The whole 128 KB read twice, compared, and kept as dongle-YYYYMMDD-HHMMSS.bin."""
    read = boot.read if in_boot else _read
    a = read(ch, 0, FLASH, lambda d, t: progress("pass 1", d, t))
    b = read(ch, 0, FLASH, lambda d, t: progress("pass 2", d, t))
    if a != b:
        raise OpError("The two reads of the dongle's memory differ, so nothing was saved. Try again in another USB port.")
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = store / f"dongle-{datetime.datetime.now():%Y%m%d-%H%M%S}.bin"
    path.write_bytes(a)
    sha = hashlib.sha256(a).hexdigest()
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise OpError(f"{path.name} didn't read back identically.")
    with open(store / "SHA256SUMS", "a") as f:
        f.write(f"{sha}  {path.name}\n")
    return path


def unit_backups(store: Path, unit: bytes) -> list[tuple[Path, bytes]]:
    """This dongle's backups, newest first."""
    out = []
    for p in sorted(Path(store).glob("dongle-*.bin"), reverse=True):
        data = p.read_bytes()
        if len(data) == FLASH and unit_of(data) == unit:
            out.append((p, data))
    return out


def target(bank: bytes, tweaks, chosen=None) -> bytes:
    """Bank A with exactly these tweaks, each with the name chosen for it (tweak -> name), or else 8BitDo's own ("" too),
    made from a bank the app recognises and checked by hash."""
    if "dongle_dinput_steam" in tweaks and "dongle_dinput" not in tweaks:
        raise OpError("Bluetooth-style DInput mapping requires the dongle DInput mode tweak. Nothing was written.")
    chosen = {k: v for k, v in (chosen or {}).items() if v != ""}
    for key, text in chosen.items():
        if key not in NAMES or key not in tweaks:
            raise OpError(f"A name for {key}, which isn't being installed or has no name. Nothing was written.")
        if not valid_name(text, NAMES[key].max_len):
            raise OpError(f"The dongle's {NAMES[key].label} must be 1 to {NAMES[key].max_len} characters of letters, digits, spaces "
                          "and punctuation, without spaces at either end. Nothing was written.")
    if not state(bank)[0]:
        raise OpError("This backup's dongle firmware isn't one the app recognises, so the new firmware can't be made from it. Nothing was written.")
    new = bytearray(bank)
    for key, (_, edits) in TWEAKS.items():
        for a, old, tweak in edits:
            new[a - BASE:a - BASE + len(old)] = tweak if key in tweaks else old
    for key, n in NAMES.items():
        named = key in tweaks and key in chosen
        text = chosen[key] if named else n.stock
        new[n.at - BASE:n.at - BASE + n.size] = _slot(n, text)
        new[n.length_at - BASE] = 2 + 2 * len(text)
        for a, old, code in n.code:
            new[a - BASE:a - BASE + len(old)] = code if named else old
    new = bytes(new)
    known, on, held = _analyse(new)
    if not known or on != tuple(k for k in TWEAKS if k in tweaks) or any(held.get(k) != v for k, v in chosen.items()):
        raise OpError("The new dongle firmware doesn't match its known checksum. Nothing was written.")
    return new


def _one(find, mode):
    found = [f for f in find() if f.mode == mode]
    return found[0].info if len(found) == 1 else None


def _wait(find, mode, seconds, step):
    end = time.time() + seconds
    while time.time() < end:
        info = _one(find, mode)
        if info:
            return info
        time.sleep(step)
    return None


# Linux hidraw may return EIO when the dongle disconnects immediately after
# accepting the update-mode command. This is *not* evidence of success: the
# caller must still detect and identify the bootloader before doing anything.
_REENUMERATION_ERRNOS = frozenset((errno.EIO, errno.ENODEV, errno.ESHUTDOWN))


def _reenumeration_error(error):
    # Windows HIDAPI reports an unplug during a pending read without an errno.
    # Accept only its exact read-disconnect error at the two restart commands;
    # callers must rediscover the device, and verify boot identity before writes.
    return (error.errno in _REENUMERATION_ERRNOS or
            (sys.platform == "win32" and error.errno is None and error.args == ("read error",)))


def _request_update_mode(ch):
    try:
        ch.xfer(bytes([0x81, 0x05, 0x00, 0x51, 0x00]).ljust(64, b"\0"), 0.5)
    except OSError as e:
        if not _reenumeration_error(e):
            raise
        # An expected USB disconnect is verified by finding and identifying
        # the dongle bootloader; never interpret EIO alone as success.


def _request_restart(ch):
    try:
        ch.xfer(boot.request(7), 0.5)
    except OSError as exc:
        if not _reenumeration_error(exc):
            raise


def mode_test(find, open_channel, wait: float = 0.3) -> str:
    """Non-flashing diagnostic: switch idle dongle -> boot -> idle.

    Does not issue erase (4) or write (3). If boot identification fails,
    leave the device untouched and report the error.
    """
    if _one(find, "boot") is not None:
        raise OpError("A bootloader is already connected. Unplug other devices and reconnect the dongle normally.")
    idle = _one(find, "dongle")
    if idle is None:
        raise OpError("Connect only the idle dongle (2dc8:301c), with the controller off.")
    ch = open_channel(idle, 0x81)
    try:
        _request_update_mode(ch)
    finally:
        ch.close()
    info = _wait(find, "boot", 8, wait)
    if info is None:
        raise OpError("Dongle did not appear in update mode. No flash operations were performed.")
    ch = open_channel(info, 0)
    try:
        if boot.identify(ch) != boot.DONGLE_ID:
            raise OpError("Bootloader identity is not 0x301c. No restart or flash commands were sent.")
        _request_restart(ch)
    finally:
        ch.close()
    if _wait(find, "dongle", 10, wait) is None:
        raise OpError("Dongle was identified in update mode, but did not reappear as 2dc8:301c. Replug it; no flash operations were performed.")
    return "Dongle switched to bootloader (identity 0x301c) and returned to normal mode. No firmware was erased or written."


STAGED_MAGIC = (0xAEB14D6C).to_bytes(4, "little")
STAGED = ("An update is staged on this dongle, and its update-mode code would install it over the new firmware the next "
          "time it starts. Nothing was written. Unplug the dongle and plug it back in once, then try again.")


def staged(record: bytes) -> bool:
    """The bootloader's own test before it applies a staged update (0xd44): the magic at +0x18 and a length of 1 to
    0x3efff at +0x20. Writing bank A then would be undone at the next start."""
    return record[0x18:0x1C] == STAGED_MAGIC and 1 <= int.from_bytes(record[0x20:0x24], "little") <= 0x3EFFF


INTERRUPTED = "The dongle may stay in update mode until it's finished. Use Restore to install your backup or 8BitDo's original firmware 1.03."


def apply(find, open_channel, unit_backup: bytes, bank: bytes, progress, wait: float = 0.3) -> bool:
    """Write bank A as described above; True if the dongle came back on its own afterwards. find and open_channel are
    cli's, so the dongle can be followed as it restarts."""
    assert len(bank) == SIZE and len(unit_backup) == FLASH   # only bank A, never anything else
    boot_info = _one(find, "boot")
    if boot_info is None:
        idle = _one(find, "dongle")
        if idle is None:
            raise OpError("The dongle isn't connected on its own. Turn the controller off and try again. Nothing was written.")
        ch = open_channel(idle, 0x81)
        try:
            if read_unit(ch) != unit_of(unit_backup):
                raise OpError("This dongle doesn't match the backup, so nothing was written.")
            if staged(_read(ch, RECORD, 0x24)):
                raise OpError(STAGED)
            _request_update_mode(ch)
        finally:
            ch.close()
        boot_info = _wait(find, "boot", 8, wait)
        if boot_info is None:
            raise OpError("The dongle didn't restart into update mode, so nothing was written. Unplug it and plug it back in.")
    ch = open_channel(boot_info, 0)
    try:
        try:
            if boot.identify(ch) != boot.DONGLE_ID:
                raise OpError("The device in update mode isn't the dongle, so nothing was written.")
            if boot.read(ch, 0, BOOT_END) != unit_backup[:BOOT_END] or boot.read(ch, CAL, 0x40) != unit_backup[CAL:CAL + 0x40]:
                raise OpError("The dongle in update mode doesn't match the backup, so nothing was written.")
            if staged(boot.read(ch, RECORD, 0x24)):
                raise OpError(STAGED)
        except protocol.ProtocolError as e:
            raise OpError(f"{e}. Nothing was written.") from e
        try:
            boot.erase(ch, BASE, SIZE)
            done = 0
            for a in range(BASE + CHUNK, END, CHUNK):
                blk = bank[a - BASE:min(a + CHUNK, END) - BASE]
                assert BASE < a and a + len(blk) <= END
                if blk != b"\xff" * len(blk):
                    boot.write_chunk(ch, a, SIZE, blk)
                done += len(blk)
                progress("write", done, SIZE)
            if boot.read(ch, BASE + CHUNK, SIZE - CHUNK) != bank[CHUNK:]:
                raise OpError("The dongle's firmware read back doesn't match what was written. " + INTERRUPTED)
            boot.write_chunk(ch, BASE, SIZE, bank[:CHUNK])
            progress("write", SIZE, SIZE)
            if boot.read(ch, BASE, SIZE) != bank:
                raise OpError("The dongle's firmware read back doesn't match what was written. " + INTERRUPTED)
        except protocol.ProtocolError as e:
            raise OpError(f"{e}. {INTERRUPTED}") from e
        _request_restart(ch)
    finally:
        ch.close()
    return _wait(find, "dongle", 10, wait) is not None
