/* SPDX-License-Identifier: GPL-3.0-or-later
 * Offline Pro 2 pairing core. No installed hooks, radio access or flash journal.
 * Wire facts: ndeadly/switch2_controller_research, command 0x15.
 */
#include "switch2-pairing.h"
extern void ble_aes_le(const u8 *key, const u8 *block, u8 *result);
static const u8 component[16] = {
    0x5c,0xf6,0xee,0x79,0x2c,0xdf,0x05,0xe1,
    0xba,0x2b,0x63,0x25,0xc4,0x1a,0x5f,0x10
};
void y_pair_disconnect(struct y_pairing *ctx) {
    u32 i;
    ctx->stage=0; ctx->pending_count=0;
    for(i=0;i<12;i++) ctx->pending_hosts[i]=0;
    for(i=0;i<16;i++) ctx->pending_ltk[i]=0;
}
/* Caller must provide a 25-byte response buffer and ensure delivery is possible.
 * Return 0 for malformed/unsupported requests; never invent a Nintendo NACK.
 * Commit is RAM only. Persistent storage must be integrated separately.
 */
u32 y_pair_request(struct y_pairing *ctx, const u8 *req, u32 length, u8 *out) {
    u32 i,n,sub,reply=0;
    if(length<8 || length!=8u+req[5]) return 0;
    if(req[0]!=0x15 || req[1]!=0x91 || req[2]!=1 || req[4] || req[6] || req[7]) return 0;
    n=req[5];sub=req[3];
    if(sub==1) {
        if(n) {
            u32 count;
            if(n<2 || req[8] || (req[9]!=1 && req[9]!=2)) return 0;
            count=req[9];
            if(n!=2+6*count) return 0;
            /* The two protocol address slots may name the same adapter.
             * Switch2Connect v3.1 intentionally repeats its local address. */
            for(i=0;i<count*6;i++) ctx->pending_hosts[i]=req[10+i];
            for(;i<12;i++) ctx->pending_hosts[i]=0;
            for(i=0;i<16;i++) ctx->pending_ltk[i]=0;
            ctx->pending_count=count;ctx->stage=1;
        }
        out[8]=1;out[9]=4;out[10]=1;
        for(i=0;i<6;i++) out[11+i]=ctx->address[5-i];
        reply=17;
    } else if(sub==4) {
        if(ctx->stage<1 || ctx->stage>3 || n!=17 || req[8]) return 0;
        for(i=0;i<16;i++) {
            ctx->pending_ltk[i]=req[9+i]^component[i];
            out[9+i]=component[i];
        }
        ctx->stage=2;out[8]=1;reply=25;
    } else if(sub==2) {
        if((ctx->stage!=2 && ctx->stage!=3) || n!=17 || req[8]) return 0;
        ble_aes_le(ctx->pending_ltk,req+9,out+9);
        /* SDK e() returns little-endian wire output; Nintendo's B2 is not reversed. */
        for(i=0;i<8;i++) { u8 temp=out[9+i];out[9+i]=out[24-i];out[24-i]=temp; }
        ctx->stage=3;out[8]=1;reply=25;
    } else if(sub==3) {
        if((ctx->stage!=3 && ctx->stage!=4) || n!=1 || req[8]) return 0;
        if(ctx->stage==3) {
            ctx->count=ctx->pending_count;
            for(i=0;i<12;i++) ctx->hosts[i]=ctx->pending_hosts[i];
            for(i=0;i<16;i++) {ctx->ltk[i]=ctx->pending_ltk[i];ctx->pending_ltk[i]=0;}
            ctx->stage=4;
        }
        out[8]=1;reply=9;
    } else return 0;
    out[0]=0x15;out[1]=1;out[2]=1;out[3]=sub;
    out[4]=0x10;out[5]=0x78;out[6]=0;out[7]=0;
    return reply;
}

/* Scratch addresses for the offline GATT integration harness only.
 * Production RAM allocation and startup hooks are not installed by this file. */
#define Y_CTX ((struct y_pairing *)0x840070)
#define Y_REPLY ((u8 *)0x841740)
#define Y_PENDING (*(volatile u8 *)0x8424f4)
#define Y_REPLY_CCC (*(volatile unsigned short *)0x8417d0)
#define Y_ALT_REPLY_CCC (*(volatile unsigned short *)0x8412ce)
#define Y_MTU (*(volatile unsigned short *)0x842f22)
/* Full Nintendo memory-read response: 16-byte header + 64 data bytes. */
#define Y_REPLY_CAPACITY 80u
extern int ble_notify(int connection,int handle,u8 *value,int length);
void y_reply_poll(void) {
    u32 length=Y_PENDING;
    if(length && (Y_REPLY_CCC==1 || Y_ALT_REPLY_CCC==1) && length+3<=Y_MTU &&
       !ble_notify(0x40,Y_REPLY_CCC==1?0x1e:0x1a,Y_REPLY,length)) Y_PENDING=0;
}
/* Keep the 65-byte rollback snapshot off every ordinary command's call stack. */
static __attribute__((noinline)) u32 durable_commit(const u8 *request,u32 length) {
    struct y_pairing before;u32 i,result;
    for(i=0;i<sizeof(before);i++) ((u8 *)&before)[i]=((u8 *)Y_CTX)[i];
    result=y_command_request(Y_CTX,request,length,Y_REPLY);
    if(result && before.stage==3 && !y_bond_save(Y_CTX)) {
        for(i=0;i<sizeof(before);i++) ((u8 *)Y_CTX)[i]=((u8 *)&before)[i];
        return 0;
    }
    return result;
}
#include "switch2-rumble.h"
int y_att_write(u8 *packet) {
    const u8 *request;
    u32 length,needed,sub,handle;
    int offset;
    if(packet[6]!=0x52 || packet[8]) return 1;
    handle=packet[7];
    if(handle!=0x12 && handle!=0x14 && handle!=0x16) return 1;
    length=packet[2]|((u32)packet[3]<<8);
    if(length<3 || length>Y_MTU) return 1;
    length-=3;
    request=packet+9;
    offset=y_rumble_att_route(handle,request,length);
    if(offset<0) return 1; /* vibration-only or invalid output */
    request+=(u32)offset;length-=(u32)offset;
    if(length<8 || (Y_REPLY_CCC!=1 && Y_ALT_REPLY_CCC!=1) || Y_PENDING) return 1;
    sub=request[3];
    if(request[0]==2) {if(length!=16) return 1;needed=16+request[8];}
    else if(request[0]==0x16) needed=32;
    else if(request[0]==0x11 && sub==3) needed=37;
    else if(request[0]==0x15) needed=(sub==1 ? 17 : sub==3 ? 9 : 25);
    else needed=(request[0]==0x10 || (request[0]==0x0c && sub==1))?20:12;
    if(needed>Y_REPLY_CAPACITY || needed+3>Y_MTU) return 1;
    if(request[0]==0x15 && sub==3) length=durable_commit(request,length);
    else length=y_command_request(Y_CTX,request,length,Y_REPLY);
    if(length) {Y_PENDING=length;y_reply_poll();}
    return 1; /* consumed: SDK must not copy command bytes into a backing value */
}

void y_att_disconnect(void) {
    *(volatile u8 *)0x843bfa=0; /* discard any partial L2CAP command */
    y_pair_disconnect(Y_CTX);
    motor_set(0,0,0); /* a disconnected Y session must not retain a haptic hold */
    *(volatile unsigned short *)0x8412ca=0;
    *(volatile unsigned short *)0x8412cc=0;
    *(volatile unsigned short *)0x8412ce=0;
    *(volatile unsigned short *)0x8417d0=0;
    *(volatile unsigned short *)0x8417d2=0;
    *(volatile unsigned short *)0x8400b2=0;
    Y_PENDING=0;
    *(volatile unsigned short *)0x8424f6=0;
    *(volatile unsigned short *)0x8424fe=0;
    y_input_reset();
    y_security_reset();
}

/* Stock startup a17c registers only 32 bytes for L2CAP reassembly. A fragmented
 * 25-byte pairing command needs 34 bytes including ATT/L2CAP/LL headers.
 * Share the idle reply buffer: no command response exists until RX completes.
 * Output starts nine bytes before the assembled command, so forward copies in
 * the command handlers remain safe. Pending replies must never be overwritten.
 */
extern u8 *stock_l2cap_rx(u8 *);
extern void stock_rx_buffer(u8 *,u32);
u8 *y_l2cap_rx(u8 *packet) {
    u32 id,n,length;
    if(!*(volatile u8 *)0x8400b1) return stock_l2cap_rx(packet);
    id=packet[0]&3;
    if(id==3) return stock_l2cap_rx(packet);
    if(Y_PENDING) return (u8 *)0;
    n=packet[1];
    if(n>61) return (u8 *)0;
    if(id==2) {
        *(volatile u8 *)0x843bfa=0;
        if(n<4) return (u8 *)0;
        length=packet[2]|((u32)packet[3]<<8);
        if(n>length+4) return (u8 *)0;
        if(n!=length+4) {
            if(packet[4]!=4 || packet[5] || length>Y_REPLY_CAPACITY-9u) return (u8 *)0;
            stock_rx_buffer(Y_REPLY,Y_REPLY_CAPACITY);
        }
    }
    return stock_l2cap_rx(packet);
}
