@ SPDX-License-Identifier: GPL-3.0-or-later
@ D-input only, called at 0x7676 after stock builds its 9-byte gamepad
@ packet on the stack, immediately before send_report(sp,9,report_id=1).
@ r4 points to the stack packet. Reorder button/hat/axes/triggers to
@ the Bluetooth D-input HID report layout without modifying any RF data.
    .text
    .global dongle_d_steam_repack
dongle_d_steam_repack:
    tpush {r0, lr}
    tloadrb r0, [r4, #0]
    tloadrb r3, [r4, #1]
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
    tstorerb r3, [r4, #8]
    tmovs r1, #9
    tmovs r2, #1
    tpop {r0, pc}
