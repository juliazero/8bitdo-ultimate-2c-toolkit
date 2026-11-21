/* SPDX-License-Identifier: GPL-3.0-or-later
 * X/D/Y selection shares the existing byte +5 settings journal.
 * bit 0: USB D-input; bit 1: Bluetooth D-input; bit 2: Bluetooth Y.
 */
#include "switch2-pairing.h"
#define RAW (*(volatile u32 *)0x84344c)
#define SAVED (*(volatile u8 *)0x843915)
#define D_MODE (*(volatile u8 *)0x8416d2)
#define CHORD (*(volatile u8 *)0x8416d3)
#define ACTIVE (*(volatile u8 *)0x8400b1)
#define HOME (1u<<17)
#define B (1u<<12)
#define X (1u<<4)
#define Y (1u<<5)
extern void save_settings(void);
extern void stock_bt_mac(const u8 *);
extern void old_bt_home(void);
extern void stock_home(void);
extern int stock_transport(void);
extern void stock_reboot(void);
extern void y_profile_identity(const u8 *);
static u32 remembered(void) {return SAVED==255 ? 0 : SAVED;}
static u32 choose(u32 previous,u32 key) {
    if(key==Y) return previous|4;
    if(key==B) return (previous&~4u)|2;
    if(key==X) return previous&~6u;
    return previous;
}
static void keep(u32 next) {
    if(next!=SAVED) {SAVED=next;save_settings();}
}
void y_select_identity(const u8 *factory) {
    u32 next=choose(remembered(),RAW&(X|B|Y));
    keep(next);
    D_MODE=(next>>1)&1;ACTIVE=(next>>2)&1;CHORD=255;
    if(ACTIVE) y_profile_identity(factory);else stock_bt_mac(factory);
}
void y_home(void) {
    u32 key,kind,count;
    if(!(RAW&HOME) || (*(volatile u8 *)0x800518&1) ||
       (RAW&((1u<<25)|(1u<<26))) || stock_transport()) {
        old_bt_home();return;
    }
    key=RAW&(X|B|Y);
    if(key!=X && key!=B && key!=Y) {CHORD=0;stock_home();return;}
    if(CHORD==255) return;
    kind=key==X?64:key==B?128:192;
    if((CHORD&192)!=kind) {CHORD=kind;return;}
    count=(CHORD&63)+1;CHORD=kind|count;
    if(count<30) return;
    CHORD=255;keep(choose(remembered(),key));stock_reboot();
}
