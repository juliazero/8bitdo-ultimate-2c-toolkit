/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef SWITCH2_PAIRING_H
#define SWITCH2_PAIRING_H
typedef unsigned char u8;
typedef unsigned int u32;
struct y_pairing {
    u8 stage, pending_count, pending_hosts[12], pending_ltk[16];
    u8 count, hosts[12], ltk[16], address[6];
};
struct y_input {
    u32 buttons;
    unsigned short sticks[4];
    u8 battery_level,power_flags;
};
u32 y_pair_request(struct y_pairing *,const u8 *,u32,u8 *);
u32 y_memory_request(struct y_pairing *,const u8 *,u32,u8 *);
u32 y_command_request(struct y_pairing *,const u8 *,u32,u8 *);
int y_input_send(const struct y_input *);
void y_input_reset(void);
void y_security_reset(void);
u32 y_bond_save(struct y_pairing *ctx);
u32 y_bond_load(struct y_pairing *ctx);
#endif
