/* SPDX-License-Identifier: GPL-3.0-or-later
 * Prototype journal. Addresses are reserved only inside the offline harness.
 * Not installed; never touches stock D/X bonds or controller calibration.
 */
#include "switch2-pairing.h"
#define SECTOR_A 0x3d000u
#define SECTOR_B 0x3e000u
#define NONE 0xffffffffu
#define flash_write(a,n,d) ((void (*)(u32,u32,u8 *))*(volatile u32 *)0x842e3c)(a,n,d)
#define flash_read(a,n,d) ((void (*)(u32,u32,u8 *))*(volatile u32 *)0x842e50)(a,n,d)
extern void flash_unlock(void);
extern void flash_erase(u32);
struct scan {u32 found,sequence,address,free_a,free_b;};
static u32 get32(const u8 *p) {return p[0]|((u32)p[1]<<8)|((u32)p[2]<<16)|((u32)p[3]<<24);}
static void put32(u8 *p,u32 x) {p[0]=x;p[1]=x>>8;p[2]=x>>16;p[3]=x>>24;}
static u32 crc(const u8 *p) {
    u32 i,j,value=0xffff;
    for(i=0;i<46;i++) {value^=p[i];for(j=0;j<8;j++) value=(value>>1)^((value&1)?0xa001:0);}
    return value;
}
static u32 valid(const u8 *r,struct y_pairing *ctx) {
    u32 i;
    if(get32(r)!=0x59433255 || r[4]!=1 || r[5]<1 || r[5]>2 || r[6] || r[7] || r[63]) return 0;
    if(!get32(r+8) || get32(r+8)==NONE) return 0;
    for(i=0;i<6;i++) if(r[12+i]!=ctx->address[i]) return 0;
    for(i=48;i<63;i++) if(r[i]!=255) return 0;
    return crc(r)==(r[46]|((u32)r[47]<<8));
}
static void scan(struct scan *s,struct y_pairing *ctx) {
    u32 words[16],i,j,address,blank;u8 *r=(u8 *)words;
    s->found=0;s->sequence=0;s->address=NONE;s->free_a=NONE;s->free_b=NONE;
    for(i=0;i<128;i++) {
        address=(i<64?SECTOR_A:SECTOR_B)+(i&63)*64;
        flash_read(address,64,r);
        blank=1;for(j=0;j<16;j++) if(words[j]!=NONE) {blank=0;break;}
        if(blank) {
            if(i<64 && s->free_a==NONE) s->free_a=address;
            if(i>=64 && s->free_b==NONE) s->free_b=address;
        } else if(valid(r,ctx) && (!s->found || get32(r+8)>s->sequence)) {
            s->found=1;s->sequence=get32(r+8);s->address=address;
        }
    }
}
u32 y_bond_load(struct y_pairing *ctx) {
    struct scan s;u32 words[16],i;u8 *r=(u8 *)words;
    ctx->count=0;for(i=0;i<12;i++) ctx->hosts[i]=0;for(i=0;i<16;i++) ctx->ltk[i]=0;
    scan(&s,ctx);
    if(!s.found) return 0;
    flash_read(s.address,64,r);
    if(!valid(r,ctx)) return 0;
    ctx->count=r[5];
    for(i=0;i<12;i++) ctx->hosts[i]=r[18+i];
    for(i=0;i<16;i++) ctx->ltk[i]=r[30+i];
    return 1;
}
u32 y_bond_save(struct y_pairing *ctx) {
    struct scan s;u32 words[16],verify_words[16],i,target,erase=NONE,sum;
    u8 *r=(u8 *)words,*verify=(u8 *)verify_words;
    if(ctx->count<1 || ctx->count>2) return 0;
    scan(&s,ctx);
    if(s.sequence>=0xfffffffeu) return 0; /* do not wrap sequence ordering */
    if(s.found) {
        if(s.address<SECTOR_B) {target=s.free_a;if(target==NONE) target=s.free_b;if(target==NONE) erase=SECTOR_B;}
        else {target=s.free_b;if(target==NONE) target=s.free_a;if(target==NONE) erase=SECTOR_A;}
    } else {target=s.free_a;if(target==NONE) target=s.free_b;}
    if(erase!=NONE) target=erase;
    if(target==NONE) return 0; /* no valid old record: never erase unknown data */
    for(i=0;i<64;i++) r[i]=255;
    put32(r,0x59433255);r[4]=1;r[5]=ctx->count;r[6]=0;r[7]=0;put32(r+8,s.sequence+1);
    for(i=0;i<6;i++) r[12+i]=ctx->address[i];
    for(i=0;i<12;i++) r[18+i]=ctx->hosts[i];
    for(i=0;i<16;i++) r[30+i]=ctx->ltk[i];
    sum=crc(r);r[46]=sum;r[47]=sum>>8;
    flash_unlock();
    if(erase!=NONE) flash_erase(erase);
    flash_write(target,63,r);
    flash_read(target,64,verify);
    for(i=0;i<63;i++) if(r[i]!=verify[i]) return 0;
    r[63]=0;flash_write(target+63,1,r+63);
    flash_read(target,64,verify);
    return valid(verify,ctx) && get32(verify+8)==s.sequence+1;
}
