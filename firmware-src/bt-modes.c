// SPDX-License-Identifier: GPL-3.0-or-later
// Dual Bluetooth HID profiles. No table copy in RAM; stock settings and SDK pairing journals.
typedef unsigned char u8;
typedef unsigned int u32;
#define D_MODE (*(volatile u8 *)0x8416d2)
#define RAW (*(volatile u32 *)0x84344c)
#define SAVED (*(volatile u8 *)0x843915)
#define WORD (*(volatile u32 *)0x84342c)
#define HALF(a) (*(volatile unsigned short *)(a))
void stock_radio(u32,u32,u32,u32);
void stock_table(void *);
void stock_scan_response(void *,u32);
void save_settings(void);
int pack_xbox(void);
int ble_notify(int,int,u8 *,int);
void after_notify(int);

void bt_start(u32 a,u32 b,u32 c,u32 d)
{
    u32 held, saved, next;
    held=RAW;
    stock_radio(a,b,c,d);
    saved=SAVED; next=saved;
    if ((held & ((1u<<12)|(1u<<4))) == (1u<<12)) next |= 2;
    else if ((held & ((1u<<12)|(1u<<4))) == (1u<<4)) next &= ~2u;
    D_MODE=(next>>1)&1;
    if (next!=saved) { SAVED=next; save_settings(); }
}
void bt_table(void *table)
{
    stock_table(D_MODE ? (void *)0x19000 : table);
}
void bt_scan(void *data,u32 len)
{
    if (D_MODE) { data=(void *)0x194f0; len=24; }
    stock_scan_response(data,len);
}
static const u8 src[15]={13,12,26,4,5,25,10,11,14,15,3,0,17,1,2};
static u32 flip(u32 v) { v>>=8; return v==127 ? 127 : (255-v); }
static u32 dhat(u32 b)
{
    u32 u=(b>>9)&1,r=(b>>6)&1,d=(b>>8)&1,l=(b>>7)&1;
    if(u&&r)return 1;if(d&&r)return 3;if(d&&l)return 5;if(u&&l)return 7;
    if(u)return 0;if(r)return 2;if(d)return 4;if(l)return 6;return 15;
}
int stock_output(u8 *packet);
int bt_ignore(u8 *packet) { return stock_output(packet); }
int bt_pack_d(void)
{
    u8 p[10];u32 b,bits=0,i;int ret;
    b=WORD;
    p[0]=dhat(b);p[1]=flip(HALF(0x8433ec));p[2]=HALF(0x8433f6)>>8;
    p[3]=flip(HALF(0x8433e4));p[4]=HALF(0x8433e6)>>8;
    p[5]=HALF(0x8433e8);p[6]=HALF(0x84340e);
    for(i=0;i<15;i++)if((b>>src[i])&1)bits|=1u<<i;
    p[7]=bits;p[8]=bits>>8;p[9]=*(volatile u8 *)0x843415;
    ret=ble_notify(0x40,0x1d,p,10);after_notify(ret);return ret;
}

// SDK copies all six bytes during this call; the factory address in flash is unchanged.
void stock_mac(u8 *);
void bt_mac(u8 *factory)
{
    u8 address[6];u32 i;
    for(i=0;i<6;i++) address[i]=factory[i];
    if(!D_MODE) address[5]^=2; // separate Xbox identity; keep address type bits and unit-specific suffix
    stock_mac(address);
}

// One alignment byte beside D_MODE; 255 blocks a held startup chord until release.
#define CHORD (*(volatile u8 *)0x8416d3)
void stock_home(void);
int stock_transport(void); // 0 = Bluetooth, 1 = 2.4 GHz, original slider state
void stock_reboot(void);
void bt_home(void)
{
    u32 held=RAW, next, kind, count;
    if(!(held&(1u<<17))) { CHORD=0; stock_home(); return; }
    // Keep wired operation and update/paddle combinations on their original path.
    if((*(volatile u8 *)0x800518 & 1) ||
       (held&((1u<<25)|(1u<<26)))) { CHORD=0; stock_home(); return; }
    held &= (1u<<17)|(1u<<12)|(1u<<4);
    if(held==((1u<<17)|(1u<<12))) kind=128;
    else if(held==((1u<<17)|(1u<<4))) kind=64;
    else { CHORD=0; stock_home(); return; }
    if(CHORD==255) return;
    count=CHORD;
    if((count&192)!=kind) { CHORD=kind; return; }
    count=(count&63)+1;
    CHORD=kind|count;
    if(count<30) return; // existing main-loop cadence: 30 full 100 ms intervals
    CHORD=255;
    if(!stock_transport()) {
        next=SAVED;
        if(kind==128) next|=2; else next&=~2u;
        if(next!=SAVED) { SAVED=next; save_settings(); }
    }
    else {
        volatile u8 *record=(volatile u8 *)0x843910;
        next=kind==128;
        // Same saved RF choice as stock Home power-off: B sets +7; X clears +7/+8.
        if(record[7]!=next || (!next && record[8])) {
            record[7]=next;
            if(!next) record[8]=0;
            save_settings();
        }
    }
    // The receiver selects its USB mode from the first packet of a fresh RF link.
    stock_reboot();
}

void delay_us(u32);
void stock_rf_start(void);
void bt_rf_start(void)
{
    u32 i;
    // After MCU reset the radio is not initialized yet. Allow the receiver to
    // expire its old link before beginning a new handshake with the saved mode.
    // Stock startup disables the watchdog; the optional watchdog arms on the first main-loop poll.
    for(i=0;i<10;i++) delay_us(100000);
    stock_rf_start();
}
