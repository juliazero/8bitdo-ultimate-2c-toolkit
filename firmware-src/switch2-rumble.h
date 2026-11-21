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
static u32 y_rumble_strength(const u8 *block) {
    u32 n=(block[0]>>4)&3, i, maximum=0;
    if((block[0]&0xc0)!=0x40 || !n) return 0;
    for(i=0;i<n;i++) {
        const u8 *sample=block+1+i*5;
        u32 low=((u32)sample[1]>>2)|(((u32)sample[2]&15)<<6);
        u32 high=((u32)sample[3]>>6)|((u32)sample[4]<<2);
        u32 combined=low+high;
        if(combined>1023) combined=1023;
        if(combined>maximum) maximum=combined;
    }
    return (maximum*255u+511u)/1023u;
}
extern void motor_set(u32 strong,u32 weak,u32 hold);
static void y_rumble_write(const u8 *value,u32 length) {
    u32 left,right;
    if(length!=33 || value[0]!=0 ||
       (value[1]&0xc0)!=0x40 || (value[17]&0xc0)!=0x40) return;
    left=y_rumble_strength(value+1);
    right=y_rumble_strength(value+17);
    /* 2C native strong/weak motors approximate the independent Pro 2 grips.
     * Bound each pulse: a lost BLE stop report must not leave motors running.
     */
    motor_set(left,right,(left||right)?2:0);
}
#endif
