/* SPDX-License-Identifier: GPL-3.0-or-later
 * Switch 2 HD Rumble -> 8BitDo dual-ERM approximation.
 * This header is also compiled with native test harnesses.
 */
#ifndef SWITCH2_RUMBLE_H
#define SWITCH2_RUMBLE_H
#include "switch2-pairing.h"
/* Switch 2 Pro vibration reports use two 16-byte actuator blocks.
 * Each starts with 0x40 | (sample_count << 4) | sequence, followed by
 * one to three 5-byte samples: LF frequency/amplitude, HF frequency/amplitude,
 * four unsigned 10-bit fields packed little-endian. The 2C has conventional
 * ERM motors, so frequency is deliberately discarded; per-grip amplitudes are
 * collapsed to one strength. This is an approximation, not HD Rumble.
 */
/* band: 0 = combined per-grip, 1 = LF only, 2 = HF only. */
static u32 y_rumble_strength(const u8 *block,u32 band) {
    u32 n=(block[0]>>4)&3, i, maximum=0;
    if((block[0]&0xc0)!=0x40 || !n) return 0;
    for(i=0;i<n;i++) {
        const u8 *sample=block+1+i*5;
        u32 low=((u32)sample[1]>>2)|(((u32)sample[2]&15)<<6);
        u32 high=((u32)sample[3]>>6)|((u32)sample[4]<<2);
        u32 combined=band==1?low:band==2?high:low+high;
        if(combined>1023) combined=1023;
        if(combined>maximum) maximum=combined;
    }
    /* Exact divide by 1023 for this bounded numerator without __udivsi3. */
    u32 scaled=maximum*255u+511u;
    return (scaled+(scaled>>10))>>10;
}
extern void motor_set(u32 strong,u32 weak,u32 hold);
/* GATT 0x0012 can carry a 33-byte actuator-only output or a 42-byte
 * report (the same 33 bytes plus 9 reserved bytes). Some hosts,
 * including Switch2Connect on Windows, transmit the shorter version.
 * GATT 0x0016 starts with the same 33-byte actuator
 * prefix, then the Nintendo command header and optional payload.
 * The decoder only needs the common prefix, not the reserved tail.
 */
#define Y_RUMBLE_PREFIX_LENGTH 33u
#define Y_RUMBLE_OUTPUT_LENGTH 42u
#define Y_RUMBLE_COMBINED_HEADER_LENGTH 41u

static void y_rumble_write(const u8 *value,u32 length,u32 split_duplicate) {
    u32 left,right,i,band=0;
    if(length<Y_RUMBLE_PREFIX_LENGTH || value[0]!=0 ||
       (value[1]&0xc0)!=0x40 || (value[17]&0xc0)!=0x40) return;
    /* Switch2Connect 3.3 sends identical blocks in its 33-byte writes,
     * encoding strong/weak as LF/HF rather than as separate grips.
     * Limit this ERM compatibility heuristic to the observed wire format.
     * Native padded/combined reports and different blocks remain per-grip.
     */
    if(split_duplicate && length==Y_RUMBLE_PREFIX_LENGTH) {
        for(i=0;i<16;i++) if(value[1+i]!=value[17+i]) break;
        if(i==16) band=1;
    }
    left=y_rumble_strength(value+1,band);
    right=y_rumble_strength(value+17,band?2:0);
    /* A bounded envelope is refreshed by each packet; use a one-tick
     * zero-strength write to explicitly stop the stock motor routine. */
    motor_set(left,right,(left||right)?20:1);
}
/* Return the command-header offset for a combined report, -1 if the
 * entire report is vibration-only or invalid. An invalid vibration
 * packet must never be parsed as a Nintendo command.
 */
static int y_rumble_att_route(u32 handle,const u8 *value,u32 length) {
    if(handle==0x12) {
        if(length==Y_RUMBLE_PREFIX_LENGTH || length==Y_RUMBLE_OUTPUT_LENGTH)
            y_rumble_write(value,length,1);
        return -1;
    }
    if(handle==0x16) {
        /* The Pro 2 initialisation transcript uses bare command headers on
         * 0x0016 as well as full rumble+command packets. Reject malformed
         * bare writes rather than interpreting their bytes as vibration. */
        if(length>=8 && value[1]==0x91 && value[2]==1 &&
           length==8u+value[5]) return 0;
        if(length<Y_RUMBLE_COMBINED_HEADER_LENGTH) return -1;
        y_rumble_write(value,Y_RUMBLE_PREFIX_LENGTH,0);
        return (int)Y_RUMBLE_PREFIX_LENGTH;
    }
    return 0;
}
#endif
