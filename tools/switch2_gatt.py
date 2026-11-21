# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline ATT endpoint for the researched Pro 2 pairing/input channels.

This is a partial wire-level model, not a radio stack or installable GATT table.
Unknown services, init commands and combined vibration/command writes are refused.
"""
from dataclasses import dataclass
from tools.switch2_pairing import PairingSession
from tools.switch2_prototype import input_report

INPUT=0x000e
INPUT_CCC=0x000f
COMMAND=0x0014
REPLY=0x001e
REPLY_CCC=0x001f

@dataclass(frozen=True)
class Exchange:
    response: bytes | None = None
    notifications: tuple[bytes, ...] = ()
    # Write Commands have no ATT error response. Expose failures to offline tests.
    rejected: str | None = None

class AttError(ValueError):
    def __init__(self,code,handle,message):
        self.code,self.handle=code,handle
        super().__init__(message)

class Pro2Gatt:
    def __init__(self,address,maximum_mtu=66):
        if type(maximum_mtu) is not int or not 23<=maximum_mtu<=250:
            raise ValueError("MTU must be 23..250")
        self.maximum_mtu=maximum_mtu
        self.pairing=PairingSession(address)
        self.disconnect()
    def disconnect(self):
        self.mtu=23
        self.subscribed={INPUT:False,REPLY:False}
        self.pairing.disconnect()
    def _notify(self,handle,value):
        if not self.subscribed[handle]:raise AttError(0x03,handle,"Notifications are disabled")
        if len(value)>self.mtu-3:raise AttError(0x0d,handle,"Value exceeds negotiated MTU")
        return b"\x1b"+handle.to_bytes(2,"little")+value
    def input_notification(self,**controls):
        return self._notify(INPUT,input_report(**controls))
    def _read(self,handle):
        if handle in (INPUT_CCC,REPLY_CCC):
            value_handle=INPUT if handle==INPUT_CCC else REPLY
            return bytes((int(self.subscribed[value_handle]),0))
        if handle==INPUT:return input_report()
        raise AttError(0x02,handle,"Attribute read is not modeled")
    def _write(self,handle,value,with_response):
        if handle in (INPUT_CCC,REPLY_CCC):
            if len(value)!=2:raise AttError(0x0d,handle,"CCCD must contain two bytes")
            if value not in (b"\x00\x00",b"\x01\x00"):
                raise AttError(0x13,handle,"Only notification enable/disable is supported")
            self.subscribed[INPUT if handle==INPUT_CCC else REPLY]=bool(value[0])
            return ()
        if handle!=COMMAND:raise AttError(0x03,handle,"Output channel is not implemented")
        if with_response:raise AttError(0x03,handle,"Command channel is Write Without Response")
        # Never advance pairing when its response cannot be delivered in this model.
        if not self.subscribed[REPLY]:raise AttError(0x03,handle,"Subscribe to command responses first")
        # Largest implemented response is 25 bytes (key exchange/challenge).
        if len(value)<8:raise AttError(0x0d,handle,"Truncated Nintendo command")
        needed={1:17,2:25,3:9,4:25}.get(value[3],0)
        if needed>self.mtu-3:raise AttError(0x0d,handle,"Response exceeds negotiated MTU")
        try:reply=self.pairing.handle(value)
        except ValueError as error:raise AttError(0x0e,handle,str(error)) from error
        return (self._notify(REPLY,reply),)
    def receive(self,pdu):
        if not isinstance(pdu,bytes) or not pdu:raise ValueError("ATT PDU must be nonempty bytes")
        opcode=pdu[0];handle=int.from_bytes(pdu[1:3],"little") if len(pdu)>=3 else 0
        try:
            if len(pdu)>self.mtu:raise AttError(0x04,handle,"ATT PDU exceeds MTU")
            if opcode==0x02:
                if len(pdu)!=3:raise AttError(0x04,0,"Invalid Exchange MTU Request")
                peer=int.from_bytes(pdu[1:3],"little")
                if peer<23:raise AttError(0x04,0,"Invalid peer MTU")
                self.mtu=min(peer,self.maximum_mtu)
                return Exchange(b"\x03"+self.maximum_mtu.to_bytes(2,"little"))
            if opcode in (0x0a,0x0c):
                if len(pdu)!=(3 if opcode==0x0a else 5):raise AttError(0x04,handle,"Invalid read")
                value=self._read(handle)
                offset=0 if opcode==0x0a else int.from_bytes(pdu[3:5],"little")
                if offset>len(value):raise AttError(0x07,handle,"Invalid read offset")
                return Exchange(bytes((opcode+1,))+value[offset:offset+self.mtu-1])
            if opcode in (0x12,0x52):
                if len(pdu)<3:raise AttError(0x04,handle,"Truncated write")
                notes=self._write(handle,pdu[3:],opcode==0x12)
                return Exchange(b"\x13" if opcode==0x12 else None,notes)
            raise AttError(0x06,handle,"ATT operation is not implemented")
        except AttError as error:
            if opcode&0x40:return Exchange(rejected=str(error))
            return Exchange(b"\x01"+bytes((opcode,))+error.handle.to_bytes(2,"little")+bytes((error.code,)),rejected=str(error))
