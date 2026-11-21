/* SPDX-License-Identifier: GPL-3.0-or-later
 * Staged Y profile startup for offline integration. Call sites are not patched.
 */
#include "switch2-pairing.h"
#define PAIRING (*(volatile u8 *)0x8424f5)
#define CTX ((struct y_pairing *)0x840070)
extern void y_att_disconnect(void);
extern void y_security_install(void);
extern void stock_indicator(u32,u32);
extern void stock_public_address(const u8 *);
extern void stock_gatt_table(void *);
extern int stock_adv_params(u32,u32,u32,u32,u32,const u8 *,u32,u32);
extern int stock_adv_data(const u8 *,u32);
extern int stock_scan_response(const u8 *,u32);
/* Early stage: before advertising parameters are built from the own address. */
void y_profile_identity(const u8 *factory_le) {
    u8 address[6];u32 i;
    y_att_disconnect();
    for(i=0;i<6;i++) address[i]=factory_le[i];
    address[5]^=4; /* separate from factory D and the X profile's XOR 2 */
    for(i=0;i<6;i++) CTX->address[i]=address[5-i];
    y_bond_load(CTX);
    stock_public_address(address); /* SDK copies all six bytes during this call */
}
/* Late stage: after stock security setup, before the original advertising enable. */
int y_profile_ready(void) {
    static const u8 prefix[17]={2,1,6,0x1b,0xff,0x53,5,1,0,3,0x7e,5,0x69,0x20,0,1,0};
    u8 advertisement[31];u32 i;int result;
    PAIRING=0;
    stock_indicator(4,2);
    stock_gatt_table((void *)0x1c000);
    y_security_install();
    *(volatile unsigned short *)0x842f20=83;
    *(volatile unsigned short *)0x842f22=23;
    /* Force connectable undirected advertising, regardless of cached D/X bonds. */
    result=stock_adv_params(0x30,0x38,0,0,0,(const u8 *)0,7,0);
    if(result) return result;
    for(i=0;i<31;i++) advertisement[i]=0;
    for(i=0;i<17;i++) advertisement[i]=prefix[i];
    if(CTX->count>=1 && CTX->count<=2)
        for(i=0;i<6;i++) advertisement[17+i]=CTX->hosts[i];
    advertisement[23]=15;
    result=stock_adv_data(advertisement,31);
    if(result) return result;
    return stock_scan_response((const u8 *)0,0);
}
/* Required scan-response replacement: do not restore the old X/D name afterward. */
int y_profile_scan(const u8 *unused,u32 length) {
    (void)unused;(void)length;
    return stock_scan_response((const u8 *)0,0);
}


extern int stock_adv_enable(u32);
extern int stock_disconnect(u32);
/* User pairing button: retain the Y identity and journal, advertise discovery.
 * Never invoke stock b8bc/b89c: that renews the factory address and reboots.
 */
int y_profile_pairing(void) {
    static const u8 discovery[31]={2,1,6,0x1b,0xff,0x53,5,1,0,3,0x7e,5,0x69,0x20,0,1,0,
                                 0,0,0,0,0,0,15,0,0,0,0,0,0,0};
    u32 connected=*(volatile u32 *)0x8436bc;
    int result;
    stock_adv_enable(0);
    y_att_disconnect();
    result=stock_adv_data(discovery,31);
    if(result) return result;
    result=stock_scan_response((const u8 *)0,0);
    if(result) return result;
    PAIRING=1;
    stock_indicator(4,3);
    if(connected) return stock_disconnect(0x13);
    return stock_adv_enable(1);
}
