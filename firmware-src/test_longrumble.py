#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run Long rumble over Bluetooth in a small Thumb-1 interpreter, on the image the app builds, and check it.

    PYTHONPATH=. python3 firmware-src/test_longrumble.py ultimate-2c-controller-v1.09.dat

The image comes from recipes.build (8BitDo's v1.09 from the .dat, plus the tweaks), and every instruction is decoded as
the controller decodes it (TC32's top nibble mapped back to Thumb's). So the rumble handler (rumble_write, 0x18390),
the stock motor setter (0xfa68) with the two hooks, the code at 0x1734c and the stock 10 ms motor routine (0xfaa0) all
run as built, each write through the callback pointer at 0x17764 as Telink's stack calls it. Only the motor driver (0xf93c) is modelled: it records each motor's strength. The Bluetooth main loop is
modelled as what it does to the motors: motor_set(0, 0, 0) on every pass (0xa4ae), then the motor routine.
"""
import struct
import sys
from pathlib import Path

from u2c_customfw.firmware import image, recipes

TC32_TO_THUMB = {0x0: 0x4, 0x1: 0x5, 0x2: 0x8, 0x3: 0x9, 0x4: 0x7, 0x5: 0x6, 0x6: 0xB, 0x7: 0xA,
                 0x8: 0xE, 0x9: 0xF, 0xA: 0x2, 0xB: 0x3, 0xC: 0xD, 0xD: 0xC, 0xE: 0x1, 0xF: 0x0}
RET, SP0 = 0xFFFFFFF0, 0x847F00
MOTOR_SET, MOTOR_TICK, MOTOR_DRIVE, CALLBACK = 0xFA68, 0xFAA0, 0xF93C, 0x17764
STRONG, WEAK, HOLD, DUR, LOOPS, STAMP, TIMER, CONNECTED = 0x8434EF, 0x8434F0, 0x8434F1, 0x8434F2, 0x8434F3, 0x8434F4, 0x800740, 0x8436BC
PACKET = 0x845000
WRITABLE = set(range(STRONG, STAMP + 4))   # the motor variables, our two bytes, the motor routine's timestamp
SET_KEYS = ["bt_xbox", "bt_rumble", "cable_360", "dim_leds", "fast_scan", "ble_interval", "ble_fast"]   # as installed


class Controller:
    def __init__(self, img: bytes):
        self.img, self.ram, self.motor, self.now = img, {}, [0, 0], 0
        self.wr32(CONNECTED, 1)

    # memory
    def rd8(self, a):
        if image.BASE <= a < image.BASE + len(self.img):
            return self.img[a - image.BASE]
        return self.ram.get(a, 0)

    def wr8(self, a, v):
        if a < 0x800000:
            raise AssertionError(f"write to flash {a:#x}")
        if not (a in WRITABLE or SP0 - 0x400 <= a < SP0 or a in (TIMER, TIMER + 1, TIMER + 2, TIMER + 3, CONNECTED)
                or PACKET <= a < PACKET + 64 or CONNECTED <= a < CONNECTED + 4):
            raise AssertionError(f"unexpected write to {a:#x}")
        self.ram[a] = v & 0xFF

    def rd(self, a, n):
        return sum(self.rd8(a + i) << 8 * i for i in range(n))

    def wr(self, a, v, n):
        for i in range(n):
            self.wr8(a + i, v >> 8 * i)

    def wr32(self, a, v):
        self.wr(a, v, 4)

    # Thumb-1 interpreter (the instructions these routines use, and the rest of their classes)
    def call(self, pc, *args):
        r = list(args) + [0] * (16 - len(args))
        r[4:8] = [0x44444444, 0x55555555, 0x66666666, 0x77777777]
        r[13], r[14] = getattr(self, "stack_top", SP0), RET
        saved = r[4:8]
        n = z = c = v = False

        def addsub(a, b, sub):
            nonlocal n, z, c, v
            res = (a - b if sub else a + b) & 0xFFFFFFFF
            n, z = bool(res >> 31), res == 0
            c = a >= b if sub else a + b > 0xFFFFFFFF
            v = bool(((a ^ b) & (a ^ res) if sub else ~(a ^ b) & (a ^ res)) >> 31 & 1)
            return res

        def nz(res):
            nonlocal n, z
            n, z = bool(res >> 31 & 1), (res & 0xFFFFFFFF) == 0
            return res & 0xFFFFFFFF

        steps = 0
        pc &= ~1
        while pc != RET:
            if hasattr(self, "stack_floor"):
                self.minimum_sp = min(getattr(self, "minimum_sp", r[13]), r[13])
                assert self.stack_floor <= r[13] <= self.stack_top, ("stack outside reserved space",hex(r[13]))
            steps += 1
            assert steps < getattr(self, "max_steps", 5000), "runaway"
            if self.trap(pc, r):
                pc = r[14] & ~1
                continue
            t = self.thumb(pc)
            nxt = pc + 2
            lo3, mid3 = t & 7, t >> 3 & 7
            if t >> 11 == 0b00000:                                   # lsls rd, rm, #imm
                sh, a = t >> 6 & 31, r[mid3]
                if sh:
                    c = bool(a >> (32 - sh) & 1)
                r[lo3] = nz(a << sh)
            elif t >> 11 == 0b00001:                                 # lsrs rd, rm, #imm
                sh, a = t >> 6 & 31 or 32, r[mid3]
                c = bool(a >> (sh - 1) & 1)
                r[lo3] = nz(a >> sh)
            elif t >> 11 == 0b00010:                                 # asrs rd, rm, #imm
                sh, a = t >> 6 & 31 or 32, r[mid3]
                c = bool(a >> (sh - 1) & 1)
                signed = a - (1 << 32) if a >> 31 else a
                r[lo3] = nz(signed >> sh)
            elif t >> 9 == 0b0001100:                                # adds rd, rn, rm
                r[lo3] = addsub(r[mid3], r[t >> 6 & 7], False)
            elif t >> 9 == 0b0001101:                                # subs rd, rn, rm
                r[lo3] = addsub(r[mid3], r[t >> 6 & 7], True)
            elif t >> 9 == 0b0001110:                                # adds rd, rn, #imm3
                r[lo3] = addsub(r[mid3], t >> 6 & 7, False)
            elif t >> 9 == 0b0001111:                                # subs rd, rn, #imm3
                r[lo3] = addsub(r[mid3], t >> 6 & 7, True)
            elif t >> 11 == 0b00100:                                 # movs rd, #imm8
                r[t >> 8 & 7] = nz(t & 0xFF)
            elif t >> 11 == 0b00101:                                 # cmp rn, #imm8
                addsub(r[t >> 8 & 7], t & 0xFF, True)
            elif t >> 11 == 0b00110:                                 # adds rd, #imm8
                r[t >> 8 & 7] = addsub(r[t >> 8 & 7], t & 0xFF, False)
            elif t >> 11 == 0b00111:                                 # subs rd, #imm8
                r[t >> 8 & 7] = addsub(r[t >> 8 & 7], t & 0xFF, True)
            elif t >> 10 == 0b010000:                                # ALU rd, rm
                op, a, b = t >> 6 & 15, r[lo3], r[mid3]
                if op == 0x0: r[lo3] = nz(a & b)
                elif op == 0x1: r[lo3] = nz(a ^ b)
                elif op in (0x2,0x3,0x4):                         # register LSL/LSR/ASR
                    shift=b&255
                    if shift:
                        if op==2:
                            c=bool(a>>(32-shift)&1) if shift<=32 else False
                            a=(a<<shift)&0xffffffff if shift<32 else 0
                        elif op==3:
                            c=bool(a>>(shift-1)&1) if shift<=32 else False
                            a=a>>shift if shift<32 else 0
                        else:
                            c=bool(a>>(min(shift,32)-1)&1)
                            signed=a-(1<<32) if a>>31 else a
                            a=(signed>>min(shift,32))&0xffffffff
                    r[lo3]=nz(a)
                elif op in (0x5, 0x6):                            # adc / sbc
                    carry = int(c)
                    total = a + b + carry if op == 0x5 else a - b - (1 - carry)
                    result = total & 0xFFFFFFFF
                    n, z = bool(result >> 31), result == 0
                    c = total > 0xFFFFFFFF if op == 0x5 else total >= 0
                    v = bool(((~(a ^ b) if op == 0x5 else a ^ b) & (a ^ result)) >> 31 & 1)
                    r[lo3] = result
                elif op == 0x8: nz(a & b)
                elif op == 0x9: r[lo3] = addsub(0, b, True)  # rsbs rd, rm, #0
                elif op == 0xA: addsub(a, b, True)
                elif op == 0xC: r[lo3] = nz(a | b)
                elif op == 0xD: r[lo3] = nz(a * b)
                elif op == 0xE: r[lo3] = nz(a & ~b)
                elif op == 0xF: r[lo3] = nz(~b)
                else: raise AssertionError(f"ALU op {op:#x} at {pc:#x}")
            elif t >> 12 == 0b1100:                                  # stm/ldm rn!, {r0-r7}
                base, registers = t >> 8 & 7, t & 255
                assert registers, "empty register list"
                address = r[base]
                values = list(r)
                for register in range(8):
                    if registers & (1 << register):
                        if t & 0x800: r[register] = self.rd(address, 4)
                        else: self.wr(address, values[register], 4)
                        address += 4
                if not (t & 0x800) or not registers & (1 << base):
                    r[base] = address & 0xFFFFFFFF
            elif t & 0xFF87 == 0x4700:                               # bx rm
                nxt = r[t >> 3 & 15] & ~1
            elif t == 0x46C0:                                        # nop (mov r8, r8)
                pass
            elif t >> 10 == 0b010001:                               # high-register add/cmp/mov, blx
                op, rd, rm = t >> 8 & 3, (t & 7) | (t >> 4 & 8), t >> 3 & 15
                a, b = (pc + 4 if rd == 15 else r[rd]), (pc + 4 if rm == 15 else r[rm])
                if op == 1: addsub(a, b, True)
                elif op == 3:
                    r[14], nxt = pc + 2 | 1, b & ~1
                else:
                    value = (a + b if op == 0 else b) & 0xFFFFFFFF
                    if rd == 15: nxt = value & ~1
                    else: r[rd] = value
            elif t >> 11 == 0b01001:                                 # ldr rd, [pc, #imm]
                r[t >> 8 & 7] = self.rd((pc + 4 & ~3) + (t & 0xFF) * 4, 4)
            elif t >> 12 == 0b0101:                                 # register-offset loads/stores
                op = t >> 9 & 7
                size = (4, 2, 1, 1, 4, 2, 1, 2)[op]
                a = r[mid3] + r[t >> 6 & 7]
                if op in (0, 1, 2): self.wr(a, r[lo3], size)
                else:
                    value = self.rd(a, size)
                    if op in (3, 7) and value & (1 << (size * 8 - 1)): value -= 1 << (size * 8)
                    r[lo3] = value & 0xFFFFFFFF
            elif t >> 11 in (0b01100, 0b01101, 0b01110, 0b01111, 0b10000, 0b10001):   # str/ldr, strb/ldrb, strh/ldrh #imm5
                kind, load = t >> 13, t >> 11 & 1
                size = {0b011: 4 if not t >> 12 & 1 else 1, 0b100: 2}[kind]
                a = r[mid3] + (t >> 6 & 31) * size
                if load:
                    r[lo3] = self.rd(a, size)
                else:
                    self.wr(a, r[lo3], size)
            elif t >> 12 == 0b1010:                                 # adr/add rd, sp, #imm8*4; flags unchanged
                base = r[13] if t & 0x800 else ((pc + 4) & ~3)
                r[t >> 8 & 7] = (base + (t & 255) * 4) & 0xFFFFFFFF
            elif t & 0xFF00 == 0xB000:                             # add/sub sp, #imm7*4
                r[13] = (r[13] + (-(t & 127) * 4 if t & 128 else (t & 127) * 4)) & 0xFFFFFFFF
            elif t >> 12 == 0b1001:                                 # str/ldr rd, [sp, #imm8*4]
                a = r[13] + (t & 255) * 4
                if t & 0x800: r[t >> 8 & 7] = self.rd(a, 4)
                else: self.wr32(a, r[t >> 8 & 7])
            elif t >> 9 == 0b1011010:                                # push {rlist, lr?}
                for i in reversed([i for i in range(8) if t >> i & 1] + ([14] if t & 0x100 else [])):
                    r[13] -= 4
                    self.wr32(r[13], r[i])
            elif t >> 9 == 0b1011110:                                # pop {rlist, pc?}
                for i in [i for i in range(8) if t >> i & 1] + ([15] if t & 0x100 else []):
                    val = self.rd(r[13], 4)
                    r[13] += 4
                    if i == 15:
                        nxt = val & ~1
                    else:
                        r[i] = val
            elif t >> 12 == 0b1101 and t >> 8 & 15 < 14:             # b<cond>
                cond, off = t >> 8 & 15, (t & 0xFF) - (t & 0x80) * 2
                take = (z, not z, c, not c, n, not n, v, not v, c and not z, not c or z,
                        n == v, n != v, not z and n == v, z or n != v)[cond]
                if take:
                    nxt = pc + 4 + off * 2
            elif t >> 11 == 0b11100:                                 # b
                nxt = pc + 4 + ((t & 0x7FF) - (t & 0x400) * 2) * 2
            elif t >> 11 == 0b11110:                                 # bl (two halves)
                t2 = self.thumb(pc + 2)
                assert t2 >> 11 == 0b11111, f"bl second half at {pc + 2:#x}"
                off = (t & 0x7FF) << 12 | (t2 & 0x7FF) << 1
                to = pc + 4 + off - (off & 1 << 22) * 2
                if to == MOTOR_DRIVE:                                # f93c(strength, motor): record it
                    self.motor[r[1]] = r[0] & 0xFF
                    r[0:4], r[12] = [0xD0, 0xD1, 0xD2, 0xD3], 0xDC
                    nxt = pc + 4
                else:
                    r[14] = pc + 4 | 1
                    nxt = to
            else:
                raise AssertionError(f"unhandled instruction {t:04x} at {pc:#x}")
            pc = nxt
        assert r[4:8] == saved, f"r4-r7 not kept by the call at {hex(steps)}"
        return r[0]

    def trap(self, pc, registers):
        """Optional peripheral boundary for other instruction-level checks."""
        return False

    def thumb(self, pc):
        hw = self.rd(pc, 2)
        return TC32_TO_THUMB[hw >> 12] << 12 | hw & 0xFFF

    # the firmware around it
    def tick_ms(self, bluetooth=True):
        """One millisecond of the main loop: the Bluetooth branch's motor_set(0, 0, 0), then the motor routine, which acts
        once more than 10 ms of the 16 MHz timer have passed since it last did."""
        self.now += 1
        self.wr32(TIMER, self.now * 16001 & 0xFFFFFFFF)
        if bluetooth:
            self.call(MOTOR_SET, 0, 0, 0)
        self.call(MOTOR_TICK)

    def write(self, mask, strong, weak, duration, loop, delay=0, lt=0, rt=0, short=False):
        """A host's write of Xbox rumble report 3 to the output characteristic, as Telink's stack hands it over to the
        callback the attribute table points at. `short`: only the first 6 value bytes, up to the duration."""
        value = bytes([mask, lt, rt, strong, weak, duration, delay, loop])[:6 if short else 8]
        pkt = bytes([0, 0]) + struct.pack("<H", 3 + len(value)) + bytes(5) + value
        for i, b in enumerate(pkt):
            self.wr8(PACKET + i, b)
        self.call(self.rd(CALLBACK, 4), PACKET)

    def on(self):
        return self.motor != [0, 0]

    def run(self, ms, events=(), bluetooth=True):
        """Advance `ms` milliseconds; events are (at_ms, function). Returns the times the motors went on and off."""
        changes, was, events = [], self.on(), sorted(events, key=lambda e: e[0])
        start = self.now
        for _ in range(ms):
            while events and events[0][0] <= self.now - start:
                events.pop(0)[1]()
            self.tick_ms(bluetooth)
            if self.on() != was:
                was = self.on()
                changes.append((self.now - start, was))
        return changes


FAILS = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def near(changes, at, state, slack=30):
    return any(s == state and abs(t - at) <= slack for t, s in changes)


def ticks_until_off(c, request):
    """In 10 ms steps, so that every call of the motor routine acts: how many ticks the motors run after `request`."""
    request(c)
    ticks = 0
    while True:
        c.now += 10
        c.wr32(TIMER, c.now * 16001 & 0xFFFFFFFF)
        c.call(MOTOR_SET, 0, 0, 0)
        c.call(MOTOR_TICK)
        ticks += 1
        if not c.on() or ticks >= 70000:
            return ticks - 1


def main():
    dat = Path(sys.argv[1]).read_bytes()
    stock = dat[0x1C:]
    fixed = recipes.build(stock, SET_KEYS + ["bt_rumble_long"])
    plain = recipes.build(stock, SET_KEYS)
    check("the app recognises the result", recipes.identify(fixed).recipes == tuple(sorted(SET_KEYS + ["bt_rumble_long"])))

    android = lambda c: c.write(3, 100, 100, 255, 255)      # hid-microsoft: the longest duration and loop count, once
    stop = lambda c: c.write(3, 0, 0, 255, 255)

    c = Controller(plain)
    ch = c.run(12000, [(0, lambda: android(c)), (10000, lambda: stop(c))])
    check("before: Android's long rumble stops after 2.55 s", near(ch, 0, True) and near(ch, 2550, False), ch)

    c = Controller(fixed)
    mid = []
    ch = c.run(12000, [(0, lambda: android(c)), (5000, lambda: mid.append(list(c.motor))), (10000, lambda: stop(c))])
    check("Android's long rumble lasts until its stop, at full strength", len(ch) == 2 and ch[0][1] and ch[0][0] <= 15
          and not ch[1][1] and 10000 <= ch[1][0] <= 10025 and mid == [[250, 250]], (ch, mid))
    check("the stop ends the repeats", c.rd8(LOOPS) == 0 and c.rd8(STRONG) == 0)

    for image_, name, off in ((plain, "before", 1000), (fixed, "with the fix", 5000)):
        c = Controller(image_)
        ch = c.run(7000, [(0, lambda: c.write(3, 100, 100, 100, 4))])   # 1 s, four more times, no stop
        check(f"{name}: 1 s with loop count 4 lasts {off / 1000:g} s", len(ch) == 2 and near(ch, off, False, 20), ch)

    c = Controller(fixed)
    ch = c.run(3000, [(0, lambda: c.write(3, 100, 100, 100, 4, short=True))])
    check("a write without the loop count plays its duration once", len(ch) == 2 and near(ch, 1000, False, 20), ch)

    c = Controller(fixed)
    seen = []
    ch = c.run(7000, [(0, lambda: android(c)), (3000, lambda: c.write(3, 40, 20, 255, 255)),
                      (3015, lambda: seen.append(list(c.motor))), (6000, lambda: stop(c))])
    check("a new strength takes over at once and keeps going", seen == [[100, 50]] and near(ch, 6000, False) and len(ch) == 2, (ch, seen))

    c = Controller(fixed)
    ch = c.run(7000, [(0, lambda: android(c)), (3000, lambda: c.write(0x80, 0, 0, 0, 0)), (6000, lambda: stop(c))])
    check("a Home ring command leaves a long rumble alone", len(ch) == 2 and near(ch, 6000, False), ch)

    for image_, name in ((plain, "before"), (fixed, "with the fix")):
        c = Controller(image_)
        sdl = [(t, lambda: c.write(15, 50, 50, 255, 235)) for t in (0, 2000, 4000)] + [(6000, lambda: stop(c))]
        ch = c.run(7000, sdl)
        check(f"{name}: SDL's resent rumble runs until its stop", len(ch) == 2 and near(ch, 6000, False), ch)

    c = Controller(fixed)
    ch = c.run(2000, [(0, lambda: c.write(2, 100, 0, 50, 0))])
    check("a 0.5 s pulse still lasts 0.5 s", near(ch, 0, True) and near(ch, 500, False) and len(ch) == 2, ch)

    c = Controller(fixed)
    ch = c.run(9000, [(0, lambda: android(c)), (3000, lambda: c.wr32(CONNECTED, 0))])
    check("a disconnection ends it within the 2.55 s running", len(ch) == 2 and 3000 < ch[1][0] <= 3000 + 2600 and c.rd8(LOOPS) == 0, ch)

    c = Controller(fixed)
    ch = c.run(8000, [(0, lambda: android(c)), (4000, lambda: c.call(MOTOR_SET, 60, 60, 150))])
    check("the controller's own 1.5 s buzz plays and ends the repeats", c.rd8(LOOPS) == 0 and len(ch) == 2 and near(ch, 5500, False), ch)

    c = Controller(fixed)
    ch = c.run(1000, [(0, lambda: c.call(MOTOR_SET, 60, 60, 20))])
    check("the connect buzz still lasts 0.2 s", near(ch, 0, True) and near(ch, 200, False) and c.rd8(LOOPS) == 0, ch)

    c = Controller(fixed)
    ch = c.run(100, [(0, lambda: android(c)), (50, lambda: stop(c))])
    check("a stop is at once and leaves no repeats", near(ch, 50, False, 15) and c.rd8(LOOPS) == 0, ch)

    c = Controller(fixed)   # 2.4 GHz and cable: zero-hold sets and no Bluetooth main loop; stale repeats are dropped
    c.wr32(CONNECTED, 0)
    c.wr8(LOOPS, 5)
    c.call(MOTOR_SET, 200, 100, 0)
    c.run(30, bluetooth=False)
    check("2.4 GHz and cable rumble as before", c.motor == [200, 100] and c.rd8(LOOPS) == 0, c.motor)

    n = ticks_until_off(Controller(fixed), android)
    check("Android's request with no stop lasts 652.8 s, 255 ticks 256 times", n == 255 * 256, f"{n} ticks")
    n = ticks_until_off(Controller(fixed), lambda c: c.write(15, 50, 50, 255, 235))
    check("SDL's request with no resend or stop lasts 601.8 s, 255 ticks 236 times", n == 255 * 236, f"{n} ticks")

    print(f"{len(FAILS)} failed" if FAILS else "all passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
