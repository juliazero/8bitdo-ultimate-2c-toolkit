# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an experimental Y+Home firmware image. Never opens or flashes hardware."""
import argparse,hashlib,json
from pathlib import Path
from tools import switch2_layout as layout
from tools.build_usb_mode_prototype import Code
from tools.build_ring_hold import bl,bl_target
from tools.check_switch2_link import interval_candidate
from u2c_customfw.firmware import image,recipes
ACTIVE=0x8400b1
ENTRY=0x1c840
END=0x1d000
KEYS=('bt_xbox','bt_modes','bt_rumble','bt_rumble_long','cable_360','dim_leds','ring_normal','settings','cpu_rest','usb_dinput')


def assemble(stock,build):
    base=recipes.build(stock,KEYS)
    assert len(base)==image.APP_SIZE
    meta=json.loads((build/'pairing-prototype.json').read_text(encoding='utf-8'))
    symbols=meta['symbols']
    c=Code(ENTRY)
    def tail(target):c.literal(3,target|1);c.op(0x4718)
    def original(pc):return bl_target(base[pc-image.BASE:pc-image.BASE+4],pc)
    # Profile selection now runs once after the callback, avoiding two settings saves.
    c.label('startup');c.op(0x4718)  # bx r3: original radio callback, preserve all args
    c.label('identity');c.op(0xb510);c.op(0x1c04);c.call(0xc7a4)
    c.op(0x1c20);c.call(symbols['y_select_identity']);c.op(0xbd10)
    c.label('bonds');c.op(0xb40f);c.literal(0,ACTIVE);c.byte(0,0);c.cmp(0,0);c.op(0xbc0f)
    c.branch('old_bonds',0)
    c.label('y_bonds');c.op(0xb510);c.call(symbols['y_profile_ready']);c.cmp(0,0);c.branch('ready',0)
    c.label('failed');c.branch('failed')
    c.label('ready');c.op(0xbc10);c.op(0xbc08);tail(0xa21a)
    c.label('old_bonds');tail(original(0xa184))
    c.label('input');c.op(0xb40f);c.literal(0,ACTIVE);c.byte(0,0);c.cmp(0,0);c.op(0xbc0f)
    c.branch('old_input',0)
    c.op(0xb510);c.call(symbols['y_reply_poll']);c.call(symbols['y_input_from_controller']);c.op(0xbd10)
    c.label('old_input');tail(original(0xa2e4))
    c.label('receive');tail(symbols['y_l2cap_rx'])
    c.label('home');tail(symbols['y_home'])
    # Application connected event must not request the old D/X connection interval.
    c.label('register_connected');c.op(0xb40c);c.literal(2,ACTIVE);c.byte(2,2);c.cmp(2,0);c.op(0xbc0c)
    c.branch('register_stock',0);callback_fixup=len(c.fixups);c.literal(1,0)
    c.label('register_stock');tail(0x110d4)
    c.label('app_connected');c.op(0xb510);c.literal(2,0x8436bc);c.mov(3,1);c.store(3,2)
    c.literal(2,layout.Y_PAIRING);c.mov(3,0);c.op(0x7013)
    c.mov(0,4);c.mov(1,1);c.call(0xd3d8);c.op(0xbd10)
    # Resolve the callback literal through the assembler's label mechanism.
    item=c.fixups[callback_fixup]
    c.fixups[callback_fixup]=(item[0],item[1],item[2],c.labels['app_connected']|1)
    c.label('register_disconnected');c.op(0xb40c);c.literal(2,ACTIVE);c.byte(2,2);c.cmp(2,0);c.op(0xbc0c)
    c.branch('register_disconnect_stock',0);disconnect_fixup=len(c.fixups);c.literal(1,0)
    c.label('register_disconnect_stock');tail(0x110d4)
    c.label('app_disconnected');c.op(0xb510);c.call(symbols['y_att_disconnect'])
    # Stock a070 may renew the factory address through b89c. Keep Y isolated.
    c.literal(3,0x8436bc);c.mov(0,0);c.store(0,3)
    c.literal(3,layout.Y_PAIRING);c.byte(1,3);c.cmp(1,0);c.branch('search_indicator',0)
    c.mov(1,3);c.branch('set_indicator')
    c.label('search_indicator');c.mov(1,2)
    c.label('set_indicator');c.mov(0,4);c.call(0xd3d8)
    c.mov(0,1);c.call(0x11598);c.op(0xbd10)
    item=c.fixups[disconnect_fixup]
    c.fixups[disconnect_fixup]=(item[0],item[1],item[2],c.labels['app_disconnected']|1)
    c.label('pairing');c.op(0xb40f);c.literal(0,ACTIVE);c.byte(0,0);c.cmp(0,0);c.op(0xbc0f)
    c.branch('old_pairing',0);tail(symbols['y_profile_pairing'])
    c.label('old_pairing');tail(0xb8bc)
    # Diagnostic path bypasses physical-button selection at both startup and MAC.
    c.label('force_start');c.op(0xb40f);c.literal(0,ACTIVE);c.mov(1,1);c.op(0x7001);c.op(0xbc0f)
    c.op(0xb408);c.literal(3,original(0xa4b8)|1);c.op(0x469c);c.op(0xbc08);c.op(0x4760)
    c.label('force_identity');c.op(0xb40f);c.literal(0,ACTIVE);c.mov(1,1);c.op(0x7001);c.op(0xbc0f)
    tail(symbols['y_profile_identity'])
    payload=c.finish();assert ENTRY+len(payload)<=END
    out=bytearray(layout.prepare_frame(interval_candidate(base)))
    if len(out)<END-image.BASE:out.extend(bytes([255])*(END-image.BASE-len(out)))
    for address,name in ((0x1a000,'pairing-prototype.bin'),(0x1c000,'gatt-prototype.bin')):
        data=(build/name).read_bytes();assert address+len(data)<= (0x1c000 if address==0x1a000 else 0x1c800)
        out[address-image.BASE:address-image.BASE+len(data)]=data
    out[ENTRY-image.BASE:ENTRY-image.BASE+len(payload)]=payload
    for pc,label in ((0x14a62,'receive'),(0xd156,'pairing'),(0xa4b8,'startup'),(0xa138,'identity'),(0xa184,'bonds'),(0xa2e4,'input'),(0xa478,'home'),(0xa22a,'register_connected'),(0xa23a,'register_disconnected')):
        out[pc-image.BASE:pc-image.BASE+4]=bl(pc,c.labels[label])
    return bytes(out),{'experimental':True,'hardware_tested':False,'console_tested':False,
        'activation':'Hold X/B/Y while powering on in Bluetooth; Home alone restores the last mode. While on, hold Home+X/B/Y for 3 seconds to restart in that mode.',
        'persistent_y_selection':True,'vibration_supported':False,'keys':KEYS,
        'app_start':image.BASE,'app_end':END,'bytes':len(out),'sha256':hashlib.sha256(out).hexdigest(),
        'dispatch':c.labels,'startup_target':original(0xa4b8),'active_byte':ACTIVE,'ordinary_targets':{hex(pc):original(pc) for pc in (0xa138,0xa184,0xa2e4,0xa478)},
        'installation':'Use the generated full-backup image with explicit restore backup --backup PATH. Bootloader mode required. No automatic installation.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backup',type=Path,required=True);p.add_argument('--build',type=Path,default=Path('build/switch2-pairing'))
    p.add_argument('--output',type=Path,default=Path('build/switch2-trial'))
    a=p.parse_args();raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
        if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error('No verified original 1.09 bank')
    if len(raw)!=image.FLASH_SIZE:p.error('A complete backup is required to check the extension area')
    if raw[0x1a000:END]!=bytes([255])*(END-0x1a000):p.error('Extension area is not blank in this backup')
    data,report=assemble(stock,a.build)
    from tools.check_switch2_trial import check
    report['offline_validation']=check(raw,a.build,a.backup)
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/'controller-y-experimental.bin').write_bytes(data)
    full=bytearray(raw);full[image.BASE:END]=data
    assert full[:image.BASE]==raw[:image.BASE] and full[END:]==raw[END:]
    (a.output/'controller-y-experimental-full.bin').write_bytes(full)
    report['full_image_sha256']=hashlib.sha256(full).hexdigest()
    (a.output/'controller-y-experimental.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()




