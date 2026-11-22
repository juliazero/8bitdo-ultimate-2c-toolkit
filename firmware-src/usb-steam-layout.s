@ SPDX-License-Identifier: GPL-3.0-or-later
@ USB D-input only: old 9 payload bytes
@   buttons[2], hat, lx, ly, rx, ry, lt, rt
@ become the Bluetooth D-input order
@   hat, lx, ly, rx, ry, lt, rt, buttons[2].
@
@ Called instead of stock `movs r2,#0; movs r3,#0` at 0xef88 in
@ the USB DInput packer 0xede8, called by DInput task at 0xf1cc, with
@ r4=9-byte stack input buffer and r1=USB input report destination.
@ Preserve r0/r1; return with r2=r3=0 as required by the stock loop.
    .text
    .global usb_d_steam_repack
usb_d_steam_repack:
    tpush {r0, r1, lr}
    tloadrb r0, [r4, #0]
    tloadrb r1, [r4, #1]
    tloadrb r2, [r4, #2]
    tstorerb r2, [r4, #0]
    tloadrb r2, [r4, #3]
    tstorerb r2, [r4, #1]
    tloadrb r2, [r4, #4]
    tstorerb r2, [r4, #2]
    tloadrb r2, [r4, #5]
    tstorerb r2, [r4, #3]
    tloadrb r2, [r4, #6]
    tstorerb r2, [r4, #4]
    tloadrb r2, [r4, #7]
    tstorerb r2, [r4, #5]
    tloadrb r2, [r4, #8]
    tstorerb r2, [r4, #6]
    tstorerb r0, [r4, #7]
    tstorerb r1, [r4, #8]
    tmovs r2, #0
    tmovs r3, #0
    tpop {r0, r1, pc}
