# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline reference for Switch 2 pairing commands, not radio/firmware support.
Target integration must use the SDK AES engine; the reference below is for tests.
Wire facts: ndeadly/switch2_controller_research/commands.md, command 0x15.
"""
from dataclasses import dataclass,field

PUBLIC_COMPONENT=bytes.fromhex("5cf6ee792cdf05e1ba2b6325c41a5f10")

def _mul(a,b):
    product=0
    for _ in range(8):
        if b&1:product^=a
        a=((a<<1) ^ (0x11b if a&128 else 0))&255
        b>>=1
    return product

def _sbox(x):
    inverse=0
    if x:
        inverse=1;base=x;power=254
        while power:
            if power&1:inverse=_mul(inverse,base)
            base=_mul(base,base);power>>=1
    result=inverse^0x63
    for shift in range(1,5):result^=((inverse<<shift)|(inverse>>(8-shift)))&255
    return result

_SBOX=tuple(_sbox(i) for i in range(256))

def aes128_block(key,block):
    if not isinstance(key,bytes) or len(key)!=16 or not isinstance(block,bytes) or len(block)!=16:
        raise ValueError("AES reference requires a 16-byte key and block")
    expanded=list(key);rcon=1
    while len(expanded)<176:
        tail=expanded[-4:]
        if len(expanded)%16==0:
            tail=[_SBOX[x] for x in tail[1:]+tail[:1]]
            tail[0]^=rcon;rcon=_mul(rcon,2)
        for x in tail:expanded.append(expanded[-16]^x)
    state=[x^y for x,y in zip(block,expanded[:16])]
    for step in range(1,11):
        state=[_SBOX[state[i%4+4*((i//4+i%4)%4)]] for i in range(16)]
        if step!=10:
            mixed=[]
            for column in range(4):
                a,b,c,d=state[column*4:column*4+4];total=a^b^c^d
                mixed.extend((a^total^_mul(a^b,2),b^total^_mul(b^c,2),
                              c^total^_mul(c^d,2),d^total^_mul(d^a,2)))
            state=mixed
        state=[x^y for x,y in zip(state,expanded[16*step:16*(step+1)])]
    return bytes(state)

@dataclass(frozen=True)
class Bond:
    host:bytes
    ltk_wire:bytes=field(repr=False)

class PairingSession:
    """Command-response state only. Persistence is an in-memory explicit commit."""
    def __init__(self,address,component=PUBLIC_COMPONENT):
        if not isinstance(address,bytes) or len(address)!=6:raise ValueError("Invalid device address")
        if not isinstance(component,bytes) or len(component)!=16:raise ValueError("Invalid public component")
        self.address=address;self.component=component;self.bonds=()
        self.disconnect()
    def disconnect(self):
        self.stage="idle";self._hosts=();self._ltk=None
    def handle(self,request):
        if not isinstance(request,bytes) or len(request)<8:raise ValueError("Truncated command")
        command,direction,transport,subcommand,unknown,length,pad0,pad1=request[:8]
        if (command,direction,transport,unknown,pad0,pad1)!=(0x15,0x91,1,0,0,0):
            raise ValueError("Not a supported Bluetooth pairing request")
        payload=request[8:]
        if len(payload)!=length:raise ValueError("Incorrect request length")
        if subcommand==1:
            if payload:
                if len(payload)<2 or payload[0]!=0 or payload[1] not in (1,2) or len(payload)!=2+6*payload[1]:
                    raise ValueError("Invalid host address list")
                hosts=tuple(payload[i:i+6][::-1] for i in range(2,len(payload),6))
                self._hosts=hosts;self._ltk=None;self.stage="addresses"
            reply=bytes((1,4,1))+self.address[::-1]
        elif subcommand==4:
            if self.stage not in ("addresses","keys","response") or len(payload)!=17 or payload[0]:
                raise ValueError("Key exchange is out of order or malformed")
            self._ltk=bytes(a^b for a,b in zip(payload[1:],self.component))
            self.stage="keys";reply=bytes((1,))+self.component
        elif subcommand==2:
            if self.stage not in ("keys","response") or len(payload)!=17 or payload[0]:
                raise ValueError("Challenge is out of order or malformed")
            reply=bytes((1,))+aes128_block(self._ltk[::-1],payload[1:][::-1])
            self.stage="response"  # generated; host must verify it before finalizing
        elif subcommand==3:
            if self.stage not in ("response","committed") or payload!=bytes(1):raise ValueError("Pairing is not ready to commit")
            if self.stage=="response":
                self.bonds=tuple(Bond(host,self._ltk) for host in self._hosts)
                self.stage="committed";self._ltk=None
            reply=bytes((1,))
        else:raise ValueError("Pairing subcommand not implemented")
        return bytes((0x15,1,1,subcommand,0x10,0x78,0,0))+reply
