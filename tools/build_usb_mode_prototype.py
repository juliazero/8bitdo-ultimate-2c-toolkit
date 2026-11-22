# SPDX-License-Identifier: GPL-3.0-or-later
"""Build TC32 USB mode code. No device access. The integrated output backs usb_dinput.
A small explicit Thumb-1 encoding subset is mapped to TC32 as in our interpreter.
This avoids a host/toolchain dependency; all PC-relative ranges are checked.
The standalone 0x18400 output is offline-only; --integrated uses 0x9a70.
"""
from pathlib import Path
import struct

ENTRY = 0x18400
STATE, STATE_IMAGE, STATE_SIZE = 0x84165c, 0x765c, 20
RAW, TIMER, USB, MODE = 0x84344c, 0x800740, 0x8434d8, 0x842e38
HOME, B, X = 1 << 17, 1 << 12, 1 << 4
HOLD, QUIET = 48_000_000, 32_000_000  # 3 s / 2 s on the stock 16 MHz tick
HAPTIC_ENTRY, HAPTIC_FALLBACK = 0x198D0, 16_000_000  # 1 s since mode change
HAPTIC_STOP = 3_200_000  # explicit motor stop after 200 ms; 16 MHz timer
MAP = [15,14,10,11,0,1,5,4,2,3,7,6,13,12,8,9]


class Code:
    def __init__(self, base=ENTRY):
        self.base = base
        self.data, self.labels, self.fixups = bytearray(), {}, []
    @property
    def pc(self): return self.base + len(self.data)
    def label(self, name):
        assert name not in self.labels
        self.labels[name] = self.pc
    def op(self, thumb):
        self.data += struct.pack('<H', MAP[thumb >> 12] << 12 | thumb & 4095)
    def mov(self, rd, value): self.op(0x2000 | rd << 8 | value)
    def cmp(self, rn, value): self.op(0x2800 | rn << 8 | value)
    def load(self, rd, rn, offset=0): self.op(0x6800 | (offset // 4) << 6 | rn << 3 | rd)
    def store(self, rd, rn, offset=0): self.op(0x6000 | (offset // 4) << 6 | rn << 3 | rd)
    def byte(self, rd, rn, offset=0): self.op(0x7800 | offset << 6 | rn << 3 | rd)
    def store_byte(self, rd, rn, offset=0): self.op(0x7000 | offset << 6 | rn << 3 | rd)
    def half(self, rd, rn, offset=0): self.op(0x8800 | (offset // 2) << 6 | rn << 3 | rd)
    def literal(self, rd, value):
        self.fixups.append(('literal', self.pc, rd, value)); self.op(0)
    def branch(self, label, cond=None):
        self.fixups.append(('branch', self.pc, cond, label)); self.op(0)
    def call(self, target):
        self.fixups.append(('call', self.pc, None, target)); self.op(0); self.op(0)
    def finish(self):
        while self.pc & 3: self.op(0x46c0)
        pool = {}
        for kind, pc, reg, value in self.fixups:
            if kind == 'literal' and value not in pool:
                pool[value] = self.pc
                self.data += struct.pack('<I', value)
        for kind, pc, arg, value in self.fixups:
            target = self.labels[value] if isinstance(value, str) else value
            if kind == 'literal':
                delta = pool[value] - ((pc + 4) & ~3)
                assert 0 <= delta <= 1020 and delta % 4 == 0
                ops = [0x4800 | arg << 8 | delta // 4]
            elif kind == 'call':
                delta = target - pc - 4
                assert delta % 2 == 0 and -(1 << 22) <= delta < (1 << 22)
                ops = [0xf000 | (delta >> 12 & 2047), 0xf800 | (delta >> 1 & 2047)]
            else:
                delta = target - pc - 4
                assert delta % 2 == 0
                if arg is None:
                    assert -2048 <= delta <= 2046
                    ops = [0xe000 | (delta // 2 & 2047)]
                else:
                    assert -256 <= delta <= 254, (pc, target)
                    ops = [0xd000 | arg << 8 | (delta // 2 & 255)]
            encoded = b''.join(struct.pack('<H', MAP[t >> 12] << 12 | t & 4095) for t in ops)
            self.data[pc - self.base:pc - self.base + len(encoded)] = encoded
        return bytes(self.data)


def build(entry=ENTRY, vendor_targets=(0xf56c,0xf594)):
    c = Code(entry)
    # Main-loop 100 ms call, after the stock slider task; independent of a484.
    c.label('main'); c.op(0xb510); c.call(0xd024); c.call(HAPTIC_ENTRY); c.op(0xbd10)
    c.label('poll'); c.op(0xb570)  # preserve r4-r6/lr
    c.literal(4, STATE); c.literal(6, TIMER); c.load(6, 6)
    c.literal(0, USB); c.byte(0, 0); c.cmp(0, 2); c.branch('reset', 1)
    c.literal(0, 0x800518); c.byte(0, 0); c.op(0x07c0); c.branch('reset', 0)  # lsls r0,#31 checks power bit 0
    c.load(0, 4, 16); c.mov(1, 1); c.op(0x4208); c.branch('reset', 1)
    c.mov(1, 2); c.op(0x4208); c.branch('buttons', 0)
    c.load(1, 4, 12); c.op(0x1a70)  # r0 = timer - last_vendor
    c.literal(1, QUIET); c.op(0x4288); c.branch('reset', 3)  # unsigned below
    c.label('buttons'); c.literal(0, RAW); c.load(0, 0)
    c.literal(1, HOME); c.op(0x4208); c.branch('reset', 0)
    c.load(1, 4, 8); c.cmp(1, 0); c.branch('done', 1)  # require Home release
    c.literal(1, HOME | B | X); c.op(0x4008)
    c.literal(1, HOME | B); c.op(0x4288); c.branch('choose_d', 0)
    c.literal(1, HOME | X); c.op(0x4288); c.branch('not_chord', 1)
    c.mov(5, 1); c.branch('chosen')
    c.label('choose_d'); c.mov(5, 2)
    c.label('chosen'); c.load(0, 4); c.op(0x42a8); c.branch('elapsed', 0)
    c.store(5, 4); c.store(6, 4, 4); c.branch('done')
    c.label('elapsed'); c.load(1, 4, 4); c.op(0x1a70)
    c.literal(1, HOLD); c.op(0x4288); c.branch('done', 3)
    c.mov(0, 1); c.store(0, 4, 8)
    c.cmp(5, 2); c.branch('switch_x', 1)
    c.call(0xeffc); c.branch('persist')
    c.label('switch_x'); c.call(0xefe4)
    c.label('persist'); c.literal(5, 0x843910); c.byte(0, 5, 5)
    c.mov(1, 1); c.op(0x4388)  # clear only saved USB bit, preserve Bluetooth bits
    c.literal(2, MODE); c.byte(1, 2); c.op(0x4308)
    c.byte(1, 5, 5); c.op(0x4288); c.branch('done', 0)
    c.op(0x7168); c.call(0xd538)  # strb r0,[r5,#5]; stock journal save
    # Arm a one-shot cue, rather than vibrating while the old USB identity
    # is still present. The helper runs on subsequent main-loop ticks.
    c.load(0, 4, 16); c.mov(1, 4); c.op(0x4308); c.store(0, 4, 16)
    c.store(6, 4, 4)  # capture exact switch time, not chord start
    c.branch('done')
    c.label('not_chord')  # Share the reset path, preserving Home release handling.
    c.label('reset'); c.mov(0, 0); c.store(0, 4)
    c.literal(1, RAW); c.load(1, 1); c.literal(2, HOME); c.op(0x4211); c.branch('done', 1)
    c.store(0, 4, 8)
    c.label('done'); c.op(0xbd70)
    # Monitor both routes before the unmodified vendor handlers. Parameters:
    # Battery reads are handled before the activity guard: they neither lock nor delay mode switching.
    # Settings/Home-ring commands impose quiet time but no permanent lock.
    # Other vendor traffic retains the existing update safety policy.
    # Share the identical packet handling code between both stock vendor paths.
    # Keep the original r0 packet pointer, the two original stock handlers,
    # and callee-saved r4/r5 intact. This also frees room for USB switch feedback.
    c.label('vendor_a'); c.mov(2, 0); c.branch('vendor_common')
    c.label('vendor_b'); c.mov(2, 1)
    c.label('vendor_common'); c.op(0xb530); c.op(0x1c04); c.op(0x1c15)  # push r4,r5,lr; r4=packet; r5=route
    c.call('battery_reply'); c.cmp(0, 0); c.branch('vendor_done', 1)
    c.op(0x1c20); c.call('activity'); c.op(0x1c20)
    c.cmp(5, 0); c.branch('vendor_b_stock', 1)
    c.call(vendor_targets[0]); c.branch('vendor_done')
    c.label('vendor_b_stock'); c.call(vendor_targets[1])
    c.label('vendor_done'); c.op(0xbd30)
    c.label('startup'); c.op(0xb510); c.call(0xf014)
    c.literal(0, 0x800518); c.byte(0, 0); c.mov(1, 1); c.op(0x4208); c.branch('startup_done', 0)  # never re-enumerate USB on battery
    c.literal(0, 0x843915); c.byte(0, 0); c.mov(1, 1); c.op(0x4008)
    c.literal(2, MODE); c.byte(2, 2); c.op(0x4290); c.branch('startup_done', 0)
    c.cmp(0, 0); c.branch('startup_x', 0); c.call(0xeffc); c.branch('startup_done')
    c.label('startup_x'); c.call(0xefe4)
    c.label('startup_done'); c.op(0xbd10)
    c.label('activity'); c.op(0xb530); c.op(0x1c05)
    c.byte(0, 5); c.cmp(0, 0x81); c.branch('activity_done', 1)
    c.literal(4, STATE); c.literal(0, TIMER); c.load(0, 0); c.store(0, 4, 12)
    c.load(0, 4, 16); c.mov(1, 2); c.op(0x4308); c.store(0, 4, 16)
    # Stock motor diagnostic is RAM-only. Keep the quiet period without write-locking modes.
    c.byte(0, 5, 1); c.cmp(0, 0x11); c.branch('activity_read', 1)
    c.byte(0, 5, 2); c.cmp(0, 4); c.branch('lock', 1)
    c.byte(0, 5, 3); c.cmp(0, 8); c.branch('activity_done', 0); c.branch('lock')
    c.label('activity_read'); c.cmp(0, 5); c.branch('lock', 1)
    c.byte(0, 5, 2); c.cmp(0, 5); c.branch('activity_done', 0)
    c.cmp(0, 0); c.branch('lock', 1)
    c.byte(0, 5, 3); c.cmp(0, 0x21); c.branch('activity_done', 0)
    # Settings and Home-ring commands (RAM changes, saved settings and reload) are not
    # firmware/update writes. They still enforce the 2s USB quiet period.
    # Subtracting 0x52 makes an unsigned range check (0x52..0x5A); 0x59 is
    # reserved for the host-only Settings stack diagnostic, also safe.
    c.op(0x3852); c.cmp(0, 8); c.branch('activity_done', 9)
    c.label('lock'); c.load(0, 4, 16); c.mov(1, 1); c.op(0x4308); c.store(0, 4, 16)
    c.label('activity_done'); c.op(0xbd30)
    # Only read replies: game input and vendor reads share D-input's IN endpoint.
    # Stock discards a read reply if a moving stick already queued an input report.
    # Give that one pending input packet at most 16 ms to leave, then return.
    c.label('read_reply'); c.op(0xb570); c.op(0x1c04); c.op(0x1c0d); c.mov(6,17)
    c.label('read_send'); c.op(0x1c20); c.op(0x1c29); c.call(0xeb04)
    c.cmp(0,0); c.branch('read_done',1)
    c.literal(1,USB); c.byte(1,1); c.cmp(1,2); c.branch('read_done',1)
    c.literal(1,MODE); c.byte(1,1); c.cmp(1,1); c.branch('read_done',1)
    c.op(0x3e01); c.branch('read_done',0)
    c.literal(0,1000); c.call(0xfe0); c.branch('read_send')
    c.label('read_done'); c.op(0xbd70)
    # Vendor command 81 05 00 5b: battery percentage, raw millivolts, charged latch,
    # external power. Return 1 only for our command so stock D-input diagnostics,
    # update mode and the other vendor handlers remain untouched.
    c.label('battery_reply')
    c.load(1, 0); c.literal(2, 0x5b000581); c.op(0x4291); c.branch('battery_no', 1)
    c.op(0xb510); c.op(0xb090)  # push {r4,lr}; sub sp,#64
    c.op(0xac00)  # r4 = sp
    c.op(0x1c20); c.mov(1, 0); c.mov(2, 64); c.call(0x103f8)  # stock_clear(response,0,64)
    c.mov(0, 0x5b); c.store_byte(0, 4, 0)
    c.literal(1, 0x843415); c.byte(0, 1); c.store_byte(0, 4, 1)
    c.literal(1, 0x843424); c.half(0, 1); c.store_byte(0, 4, 2)
    c.op(0x0a00); c.store_byte(0, 4, 3)  # lsrs r0,r0,#8
    c.literal(1, 0x843423); c.byte(0, 1); c.store_byte(0, 4, 4)
    c.literal(1, 0x800518); c.byte(0, 1); c.store_byte(0, 4, 5)
    c.op(0x1c20); c.mov(1, 63); c.mov(2, 2); c.call(0xeb7c)
    c.mov(0, 1); c.op(0xb010); c.op(0xbd10)  # return true; free frame
    c.label('battery_no'); c.mov(0, 0); c.op(0x4770) # bx lr
    blob = c.finish()
    assert entry + len(blob) <= (0x9cac if entry < 0xa000 else 0x18ff0)
    # Encode only the existing call replacements; never overwrite stock data blindly.
    hooks = {}
    for addr, label, original in ((0xe414,'read_reply',0xeb04),(0xa414,'startup',0xf014),(0xa488,'main',0xd024),(0xf83c,'vendor_a',0xf56c),(0xf842,'vendor_b',0xf594)):
        def bl(target):
            d = target - addr - 4
            return b''.join(struct.pack('<H', MAP[t >> 12] << 12 | t & 4095)
                            for t in (0xf000 | (d >> 12 & 2047),0xf800 | (d >> 1 & 2047)))
        hooks[addr] = (bl(original), bl(c.labels[label]))
    return blob, c.labels, hooks


def build_haptic(poll_addr):
    """USB-only post-reenumeration cue, in free space after the BT-mode code.

    Flag 0x4 means a mode changed; 0x8 means an unconfigured USB state was
    observed after the change; 0x10 means a short haptic is active and must
    be explicitly stopped. A newly configured USB triggers immediately.
    If the USB stack cycles entirely inside a blocking stock mode setter and
    no unconfigured tick can be observed, fall back to a 1-second minimum
    post-switch delay, *still requiring configured USB state*.
    """
    c = Code(HAPTIC_ENTRY)
    c.op(0xb510)  # push r4,lr
    c.call(poll_addr)  # original 100 ms mode polling, before checking readiness
    c.literal(4, STATE)
    c.load(0, 4, 16)
    # Independent stop path: USB-mode changes can suspend the stock 10-ms
    # motor countdown. Never rely on the hold byte alone to stop the cue.
    c.mov(1, 16); c.op(0x4208); c.branch('check_start', 0)
    c.literal(1, TIMER); c.load(1, 1)
    c.load(2, 4, 4); c.op(0x1a89)  # elapsed since explicit haptic start
    c.literal(2, HAPTIC_STOP); c.op(0x4291); c.branch('done', 3)
    c.mov(1, 16); c.op(0x4388); c.store(0, 4, 16)
    # A nonzero hold overrides an existing motor hold in both stock firmware
    # and the optional bt_rumble_long patch. Set BOTH strengths to zero.
    c.mov(0, 0); c.mov(1, 0); c.mov(2, 1); c.call(0xfa68)
    c.branch('done')
    c.label('check_start')
    c.mov(1, 4); c.op(0x4208); c.branch('done', 0)
    c.literal(1, USB); c.byte(1, 1); c.cmp(1, 2); c.branch('ready', 0)
    c.mov(1, 8); c.op(0x4308); c.store(0, 4, 16); c.branch('done')
    c.label('ready')
    c.mov(1, 8); c.op(0x4208); c.branch('buzz', 1)
    c.literal(1, TIMER); c.load(1, 1)
    c.load(2, 4, 4); c.op(0x1a89)  # elapsed = clock - mode-switch timestamp
    c.literal(2, HAPTIC_FALLBACK); c.op(0x4291); c.branch('done', 3)
    c.label('buzz')
    c.mov(1, 12); c.op(0x4388)
    c.mov(1, 16); c.op(0x4308); c.store(0, 4, 16)  # stop pending, clear start flags
    c.literal(1, TIMER); c.load(1, 1); c.store(1, 4, 4)  # time from start of pulse
    c.mov(0, 70); c.mov(1, 70); c.mov(2, 12); c.call(0xfa68)
    c.label('done'); c.op(0xbd10)
    data = c.finish()
    assert HAPTIC_ENTRY + len(data) <= 0x19A00, (len(data), 'haptic code overlaps 2.4 GHz battery code')
    return data


def integrated(blobs):
    """Preserve the entire Battery log payload and chain existing ring vendors."""
    blobs = Path(blobs)
    def target(pc, name):
        raw = (blobs / name).read_bytes()
        inverse = {v:i for i,v in enumerate(MAP)}
        words = [int.from_bytes(raw[i:i+2], 'little') for i in (0,2)]
        words = [inverse[x >> 12] << 12 | (x & 4095) for x in words]
        offset = (words[0] & 2047) << 12 | (words[1] & 2047) << 1
        return pc + 4 + offset - ((offset & (1 << 22)) * 2)
    code, labels, hooks = build(0x9a70, (target(0xf83c,'vhooka.bin'), target(0xf842,'vhookb.bin')))
    log = (blobs / 'battlog.bin').read_bytes()
    assert len(log) <= 0x9a70 - 0x9964
    area = bytearray(log.ljust(0x9cac - 0x9964, b'\0'))
    area[0x9a70 - 0x9964:0x9a70 - 0x9964 + len(code)] = code
    results = {'usbmode.bin':bytes(area), 'usb-haptic-ready.bin':build_haptic(labels['poll'])}
    for address, name in ((0xe414,'usbread.bin'),(0xa414,'usbstart.bin'),(0xa488,'usbhook.bin'), (0xf83c,'usbvendora.bin'), (0xf842,'usbvendorb.bin')):
        results[name] = hooks[address][1]
    return results, code, labels


if __name__ == '__main__':
    import argparse, json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integrated', type=Path, help='Build compatible blobs in this blob directory')
    args = parser.parse_args()
    if args.integrated:
        blobs, code, labels = integrated(args.integrated)
        for name, data in blobs.items(): (args.integrated / name).write_bytes(data)
        print(json.dumps({'integrated':True,'code_bytes':len(code),'remaining_bytes':0x9cac-0x9a70-len(code)}))
    else:
        blob, labels, hooks = build()
        target = Path(__file__).resolve().parents[1] / 'firmware-src' / 'usb-mode-prototype.hex'
        target.write_text(blob.hex(' ') + '\n', encoding='ascii')
        print(json.dumps({'offline_only':True,'bytes':len(blob),'entry':hex(ENTRY),'output':str(target)}))
