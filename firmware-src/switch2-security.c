/* SPDX-License-Identifier: GPL-3.0-or-later
 * Profile-local LTK callbacks for offline integration. No startup hook installed.
 */
#include "switch2-pairing.h"
#define CTX ((struct y_pairing *)0x840070)
#define PEER_VALID (*(volatile u8 *)0x8424fd)
#define PEER ((volatile u8 *)0x8412c4)
extern void register_ltk_handler(void *);
extern int stock_ltk_reply(u32,const u8 *);
extern int stock_ltk_negative(u32);
extern void y_att_disconnect(void);
void y_security_reset(void) {
    u32 i;PEER_VALID=0;for(i=0;i<6;i++) PEER[i]=0;
}
/* SDK connection callback: header points at LL header, followed by InitA. */
void y_connected(u32 handle,const u8 *header) {
    u32 i;
    y_att_disconnect();
    if(handle!=0x40) return;
    for(i=0;i<6;i++) PEER[i]=header[2+i];
    PEER_VALID=1;
}
int y_ltk_request(u32 handle,const u8 *random,u32 ediv) {
    u32 slot,i,equal;
    /* Custom pairing stores a host-bound LTK, not an SMP EDIV/Rand lookup tuple. */
    (void)random;(void)ediv;
    if(handle!=0x40) return 2;
    if(PEER_VALID && CTX->count>=1 && CTX->count<=2) {
        for(slot=0;slot<CTX->count;slot++) {
            equal=1;
            for(i=0;i<6;i++) if(PEER[i]!=CTX->hosts[slot*6+i]) equal=0;
            if(equal) return stock_ltk_reply(handle,CTX->ltk);
        }
    }
    return stock_ltk_negative(handle);
}
void y_security_install(void) {
    y_security_reset();
    register_ltk_handler((void *)y_ltk_request);
    *(volatile u32 *)0x843684=(u32)y_connected;
}
