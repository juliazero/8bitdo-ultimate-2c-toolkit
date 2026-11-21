# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct motor diagnostic through the stock USB vendor report, not game force feedback."""
import threading
from .. import cli
from ..transport import PermissionDenied
from ..transport.windows import _hid
from ..firmware.recipes import BLOBS


def packet(left, right):
    data=bytearray(64)
    data[:4]=bytes.fromhex('81 11 04 08')
    data[5:7]=(450 if left or right else 0).to_bytes(2,"little")
    data[7:9]=bytes((left,right))
    return bytes(data)


def bluetooth_packet(left,right):
    # Vendor report 2, Xbox-compatible eight-byte payload; magnitudes use 0..100.
    return bytes((2,3,0,0,left*2//5,right*2//5,45 if left or right else 1,0,0))

def is_bluetooth(f):
    return f.mode=='bluetooth' and f.info.vendor==0x2dc8 and f.info.product==0x301b and f.info.transport=='Bluetooth'

def supports_bluetooth_report(data):
    # Windows HIDAPI reconstructs descriptors from HID caps: compare the report
    # layout rather than requiring byte-for-byte equality with firmware source.
    state={};stack=[];bits={};vendor_output=False;offset=0
    while offset<len(data):
        tag=data[offset];offset+=1
        if tag==0xfe:return False
        size=(0,1,2,4)[tag&3]
        if offset+size>len(data):return False
        value=int.from_bytes(data[offset:offset+size],'little');offset+=size
        tag &= 0xfc
        if tag in (4,0x74,0x84,0x94):state[tag]=value
        elif tag==0xa4:stack.append(dict(state))
        elif tag==0xb4:
            if not stack:return False
            state=stack.pop()
        elif tag in (0x80,0x90):
            key=(state.get(0x84,0),tag)
            bits[key]=bits.get(key,0)+state.get(0x74,0)*state.get(0x94,0)
            if tag==0x90:vendor_output |= state.get(4,0)>=0xff00
    return bits=={(1,0x80):80,(2,0x90):64} and vendor_output

class HIDRumble:
    def __init__(self):
        self.lock=threading.Lock()
        self.stop_event=threading.Event()

    def target(self):
        found=cli.find()
        targets=[f for f in found if f.info.vendor==0x2dc8 and f.info.transport=='USB' and
                 ((f.mode=='cable_dinput' and f.info.product in (0x301b,0x301d)) or
                  (f.mode=='dongle_paired' and f.info.product==0x301c))]
        if len(targets)==1:return targets[0]
        if targets:return None
        candidates=[f for f in found if is_bluetooth(f)]
        if len(candidates)!=1:return None
        device=_hid().device()
        try:
            device.open_path(candidates[0].info.handle)
            # Never write vendor commands to stock or older one-byte-output firmware.
            if supports_bluetooth_report(bytes(device.get_report_descriptor())):return candidates[0]
        finally:device.close()
        return None

    def status(self):
        try:
            f=self.target()
            return {'available':bool(f),'kind':'vendor',**({'product':f.info.product,'transport':f.info.transport} if f else {})}
        except (OSError,RuntimeError,PermissionDenied):
            return {'available':False,'kind':'vendor'}

    def stop(self):
        self.stop_event.set()
        return {'ok':True}

    def rumble(self, locality):
        if locality not in ('default','left','right'):
            return {'ok':False,'error':'Invalid vibration request.'}
        if not self.lock.acquire(blocking=False):
            return {'ok':False,'error':'A vibration test is already running.'}
        ch=None
        try:
            f=self.target()
            if f is None: return {'ok':False,'error':'Connect exactly one Ultimate 2C in D-input with diagnostic vibration support.'}
            if is_bluetooth(f):
                ch=_hid().device();ch.open_path(f.info.handle)
                def send(left,right):
                    data=bluetooth_packet(left,right)
                    if ch.write(data)<len(data):raise OSError('Short Bluetooth vibration write')
            else:
                ch=cli.open_channel(f.info,0x81)
                def send(left,right):ch.xfer(packet(left,right),0)
            left,right={'default':(80,80),'left':(80,0),'right':(0,80)}[locality]
            self.stop_event.clear()
            try:
                send(left,right)
                self.stop_event.wait(.45)
            finally:
                try: send(0,0)
                except OSError: send(0,0)
            return {'ok':True}
        except (OSError,RuntimeError,PermissionDenied) as exc:
            return {'ok':False,'error':str(exc)}
        finally:
            try:
                if ch is not None: ch.close()
            finally: self.lock.release()


RUMBLE=HIDRumble()
