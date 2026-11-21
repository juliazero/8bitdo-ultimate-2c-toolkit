/* SPDX-License-Identifier: GPL-3.0-or-later
 * Pro 2 scalar init commands and normalized input: offline integration only.
 */
#include "switch2-pairing.h"
#define MASK (*(volatile u8 *)0x8424f9)
#define ENABLED (*(volatile u8 *)0x8424fa)
#define CONFIGURED (*(volatile u8 *)0x8424fb)
#define PLAYER (*(volatile u8 *)0x8424fc)
#define COUNTER (*(volatile u8 *)0x8424f8)
#define INPUT_CCC (*(volatile unsigned short *)0x8412cc)
#define COMMON_CCC (*(volatile unsigned short *)0x8412ca)
#define CTX ((struct y_pairing *)0x840070)
#define MTU (*(volatile unsigned short *)0x842f22)
#define PENDING (*(volatile u8 *)0x8424f4)
#define SHADOW ((volatile u8 *)0x841790)
extern int ble_notify(int,int,u8 *,int);
void y_input_reset(void) {
    u32 i;
    MASK=0;ENABLED=3;CONFIGURED=0;COUNTER=0;PLAYER=0;
    for(i=0;i<63;i++) SHADOW[i]=0;
    SHADOW[6]=8;SHADOW[7]=128;SHADOW[9]=8;SHADOW[10]=128;SHADOW[11]=0x30;
}
static u32 unsupported(const u8 *r,u8 *out) {
    out[0]=r[0];out[1]=4;out[2]=1;out[3]=r[3];
    out[4]=0;out[5]=0x78;out[6]=0;out[7]=0;return 8;
}
u32 y_command_request(struct y_pairing *ctx,const u8 *r,u32 length,u8 *out) {
    u32 i,n,command,sub,size;
    if(length<8 || length!=8u+r[5] || r[1]!=0x91 || r[2]!=1 || r[4] || r[6] || r[7]) return 0;
    command=r[0];sub=r[3];n=r[5];
    if(command==3 && sub==0x0a) {
        if(n!=4 || r[8]!=9 || r[9] || r[10] || r[11]) return 0;
        out[0]=3;out[1]=1;out[2]=1;out[3]=0x0a;
        out[4]=0;out[5]=0xf8;out[6]=0;out[7]=0;
        return 8; /* native Pro 2 report 09 is already our only report format */
    }
    if(command==2) return y_memory_request(ctx,r,length,out);
    if(command==0x15) {
        size=y_pair_request(ctx,r,length,out);
        if(!size && sub==3 && n==1 && !r[8]) return unsupported(r,out);
        return size;
    }
    if((command==3 && sub==0x0d && n==8 && r[8]==1 && !r[9]) ||
       (command==0x0a && ((sub==8 && n==20) || (sub==2 && n==4))) ||
       (command==1 && ((sub==0x0c && !n) || (sub==1 && n==4))))
        return unsupported(r,out);
    if(command==7 && sub==1 && !n) {out[8]=0;size=9;}
    else if(command==0x16 && sub==1 && !n) {
        /* Observed compatibility reply; meaning remains unknown. */
        for(i=0;i<24;i++) out[8+i]=0;
        size=32;
    } else if(command==0x11 && !n && (sub==1 || sub==3)) {
        static const u8 compatibility[29]={
            1,0x20,3,0,0,0x0a,0xe8,0x1c,0x3b,0x79,0x7d,0x8b,0x3a,
            0x0a,0xe8,0x9c,0x42,0x58,0xa0,0x0b,0x42,0x0a,0xe8,0x9c,0x41,0x58,0xa0,0x0b,0x41
        };
        if(sub==1) {out[8]=1;out[9]=0;out[10]=0;out[11]=0;size=12;}
        else {for(i=0;i<29;i++) out[8+i]=compatibility[i];size=37;}
    } else if(command==9 && sub==7 && n==8) {
        if(r[8]>15) return 0;
        for(i=9;i<16;i++) if(r[i]) return 0;
        PLAYER=r[8];size=8; /* retained assignment, no Home-ring hardware writes */
    }
    else if(command==0x10 && sub==1 && !n) {
        /* Compatibility identity of the emulated, pre-headset Pro 2 profile. */
        const u8 identity[12]={1,0,14,2,12,0,0,0,255,255,255,255};
        for(i=0;i<12;i++) out[8+i]=identity[i];
        size=20;
    } else if(command==0x0c) {
        if(n!=4 || r[9] || r[10] || r[11] || sub<1 || sub>5) return 0;
        if((sub==4 || sub==5) && !CONFIGURED) return 0;
        for(i=0;i<12;i++) out[8+i]=0;
        size=12;
        if(sub==1) {out[12]=(r[8]&1)?7:0;out[13]=(r[8]&2)?7:0;size=20;}
        else if(sub==2) {MASK=r[8]&3;CONFIGURED=1; /* mask gates future changes, not existing features */}
        else if(sub==3) {MASK=0;ENABLED=0;CONFIGURED=0;}
        else if(sub==4) ENABLED|=r[8]&MASK;
        else ENABLED&=~(r[8]&MASK);
    } else return 0;
    out[0]=command;out[1]=1;out[2]=1;out[3]=sub;
    out[4]=0x10;out[5]=0x78;out[6]=0;out[7]=0;
    return size;
}
static void native_packet(u8 *p,const struct y_input *input) {
    u32 i,buttons,x,y;
    for(i=0;i<63;i++) p[i]=0;
    p[0]=COUNTER;p[1]=(input->battery_level<<2)|input->power_flags;
    buttons=(ENABLED&1)?input->buttons:0;
    p[2]=buttons;p[3]=buttons>>8;p[4]=buttons>>16;
    for(i=0;i<2;i++) {
        x=(ENABLED&2)?input->sticks[i*2]:2048;
        y=(ENABLED&2)?input->sticks[i*2+1]:2048;
        p[5+3*i]=x;p[6+3*i]=(x>>8)|(y<<4);p[7+3*i]=y>>4;
    }
    p[11]=0x30;
}
static int common_packet(u8 *p,const struct y_input *input) {
    static const u8 bits[21]={2,3,0,1,6,7,9,10,16,18,19,17,22,23,8,11,12,13,24,25,14};
    u32 i,buttons=0,x,y,voltage;u8 irq;
    for(i=0;i<63;i++) p[i]=0;
    /* Pending LTK bytes are scratch only at idle/committed stages. Input is
     * suppressed during stages 1..3, so a key exchange can never be overwritten.
     * Disconnect and a new pairing exchange zero this 32-bit common counter.
     */
    irq=*(volatile u8 *)0x800643;*(volatile u8 *)0x800643=0;
    if(CTX->stage!=0 && CTX->stage!=4) {*(volatile u8 *)0x800643=irq;return 0;}
    for(i=0;i<4;i++) p[i]=CTX->pending_ltk[i];
    *(volatile u8 *)0x800643=irq;
    if(ENABLED&1) for(i=0;i<21;i++) if(input->buttons&(1u<<i)) buttons|=1u<<bits[i];
    for(i=0;i<4;i++) p[4+i]=buttons>>(8*i);
    for(i=0;i<2;i++) {
        x=(ENABLED&2)?input->sticks[i*2]:2048;
        y=(ENABLED&2)?input->sticks[i*2+1]:2048;
        p[10+3*i]=x;p[11+3*i]=(x>>8)|(y<<4);p[12+3*i]=y>>4;
    }
    voltage=*(volatile unsigned short *)0x843424;
    p[31]=voltage;p[32]=voltage>>8;p[41]=1;return 1;
}
int y_input_send(const struct y_input *input) {
    u8 p[63],irq;u32 i,common;int result;
    if(!ENABLED || (INPUT_CCC!=1 && COMMON_CCC!=1) || MTU<66 || PENDING) return -1;
    if(CTX->stage!=0 && CTX->stage!=4) return -1;
    if(input->battery_level>9 || (input->power_flags&~3u) || (input->buttons&~0x1fffffu)) return -1;
    for(i=0;i<4;i++) if(input->sticks[i]>4095) return -1;
    common=COMMON_CCC==1 && (INPUT_CCC!=1 || (COUNTER&1));
    if(common) {if(!common_packet(p,input)) return -1;}else native_packet(p,input);
    result=ble_notify(0x40,common?0xa:0xe,p,63);
    if(!result) {
        if(common) {
            native_packet((u8 *)SHADOW,input);
            irq=*(volatile u8 *)0x800643;*(volatile u8 *)0x800643=0;
            if(CTX->stage==0 || CTX->stage==4)
                for(i=0;i<4;i++) if(++CTX->pending_ltk[i]) break;
            *(volatile u8 *)0x800643=irq;
        } else for(i=0;i<63;i++) SHADOW[i]=p[i];
        COUNTER++;
    }
    return result;
}

/* Physical adapter: positions follow Nintendo (bottom B, right A), while stock
 * processed WORD preserves configured remaps and trigger thresholds. Axis
 * X uses the calibrated stock polarity; Y is inverted for Nintendo up-positive
 * reports, as confirmed through Switch2Connect on both physical sticks.
 */
int y_input_from_controller(void) {
    static const u8 source[20]={13,12,4,5,11,15,0,2,8,6,7,9,10,14,3,1,17,255,25,26};
    static const u8 thresholds[9]={1,12,23,34,45,56,67,78,89};
    static const u32 axes[4]={0x8433ec,0x8433f6,0x8433e4,0x8433e6};
    struct y_input input;u32 i,word,value,percent;
    word=*(volatile u32 *)0x84342c;input.buttons=0;
    for(i=0;i<20;i++) if(source[i]!=255 && (word&(1u<<source[i]))) input.buttons|=1u<<i;
    for(i=0;i<4;i++) {
        value=*(volatile unsigned short *)axes[i];
        if(value==32767 || value==32768) value=2048;
        else {value>>=4;value=4095-value;}
        input.sticks[i]=value;
    }
    percent=*(volatile u8 *)0x843415;
    input.battery_level=0;
    for(i=0;i<9;i++) if(percent>=thresholds[i]) input.battery_level=i+1;
    input.power_flags=(*(volatile u8 *)0x800518)&1;
    if(input.power_flags && !*(volatile u8 *)0x843423) input.power_flags|=2;
    return y_input_send(&input);
}
