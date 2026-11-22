/* SPDX-License-Identifier: GPL-3.0-or-later
 * Experimental controller RF telemetry. The optional GUI tweak installs this
 * hook only when explicitly selected; longer RF packets are unverified on hardware.
 */
typedef unsigned char u8;
typedef unsigned short u16;
#define PACKET ((volatile u8 *)0x843980)
#define PCT (*(volatile u8 *)0x843415)
#define MV (*(volatile u16 *)0x843424)
#define FULL (*(volatile u8 *)0x843423)
#define PD0 (*(volatile u8 *)0x800518)
void stock_rf_pack(void);

/* Pass through normal report generation, then populate a distinguishable
 * 8-byte trailer. Bytes 0..19, including stock input checksum, are untouched.
 * Trailer is NOT transmitted by stock firmware: dd78 copies only 20 bytes.
 * The opt-in recipe increases transmitter length; receiver acceptance is NOT verified. */
void rf_battery_pack(void)
{
    u8 x, flags, i;
    u16 mv;
    stock_rf_pack();
    PACKET[20] = 0xa5;
    PACKET[21] = 0x5a;
    PACKET[22] = PCT <= 100 ? PCT : 0xff;
    mv = MV;
    PACKET[23] = (u8)mv;
    PACKET[24] = (u8)(mv >> 8);
    flags = (FULL != 0) | ((PD0 & 1) << 1);
    PACKET[25] = flags;
    PACKET[26] = PACKET[26] + 1;
    x = 0xc3;
    for (i = 20; i <= 26; ++i) x ^= PACKET[i];
    PACKET[27] = x;
}
