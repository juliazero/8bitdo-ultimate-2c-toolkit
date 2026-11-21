# SPDX-License-Identifier: GPL-3.0-or-later
"""Guarded, forced-Y startup overlay for interpreter use only. Never a flash image."""
from tools.build_ring_hold import bl, bl_target
from tools.build_usb_mode_prototype import Code
from u2c_customfw.firmware import image


def prepare(frame, symbols):
    result=bytearray(frame)
    def target(pc):
        at=pc-image.BASE
        return bl_target(result[at:at+4],pc)
    # Reject unknown startup code rather than patch an arbitrary firmware revision.
    for pc, expected in ((0xa138,0x11188),(0xa184,0x15bac),
                         (0xa20e,0x11614),(0xa216,0x11690),(0xa21c,0x11598)):
        assert target(pc)==expected, ('unexpected startup call',hex(pc),hex(target(pc)))
    result[0xa138-image.BASE:0xa13c-image.BASE]=bl(0xa138,symbols['y_profile_identity'])
    c=Code(0xa184)
    c.call(symbols['y_profile_ready']);c.cmp(0,0)
    c.labels['enable']=0xa21a
    c.branch('enable',0)
    c.label('failed');c.branch('failed')  # fail closed; never advertise partial setup
    payload=c.finish()
    assert len(payload)<=0xa21a-0xa184
    result[0xa184-image.BASE:0xa184-image.BASE+len(payload)]=payload
    return result
