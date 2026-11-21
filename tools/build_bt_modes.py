# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile dual Bluetooth profile wrappers into one additional app sector. Offline."""
import argparse,os,struct,subprocess
from pathlib import Path
from tools.build_ring_hold import elf_sections,bl,bl_target
from tools.build_usb_mode_prototype import Code
ROOT=Path(__file__).resolve().parents[1]
ENTRY=0x19540;LIMIT=0x19f00;STATE=0x8416d2;TABLE=0x19000;TABLE_SOURCE=0x1743c;TABLE_SIZE=51*24
DNAME=b'8BitDo Ultimate 2C (D)'
EXTERNAL={'stock_radio':0xa4ec,'stock_table':0x13628,'stock_scan_response':0x11690,
          'save_settings':0xd538,'pack_xbox':0xb908,'ble_notify':0x1458c,'after_notify':0x1abc,'stock_mac':0x11188,'stock_home':0xd0b0,'stock_transport':0xc384,'stock_reboot':0xd056,'stock_output':0x18390,'delay_us':0xfe0,'stock_rf_start':0xdb14}
HOOKS={0xa410:('btrfstart.bin','bt_rf_start'),0xa478:('bthome.bin','bt_home'),0xa138:('btmac.bin','bt_mac'),0xa184:('btbonds.bin','bt_bonds_tail'),0xa4b8:('btstart.bin','bt_start'),0xa318:('bttable.bin','bt_table_tail'),0xa216:('btscan.bin','bt_scan_tail'),0xa2e4:('btpack.bin','bt_pack_tail')}
def build(compiler,output,blobs):
    compiler=compiler.resolve();output.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ);env['PATH']=str(compiler.parent)+os.pathsep+env.get('PATH','')
    obj=output/'bt-modes.o'
    subprocess.run([str(compiler),'-Os','-Wall','-Werror','-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections','-c',str(ROOT/'firmware-src/bt-modes.c'),'-o',str(obj)],check=True,env=env)
    data=obj.read_bytes();headers,sections=elf_sections(data)
    assigned={};position=ENTRY
    for name,(h,raw) in sections.items():
        if (name.startswith('.text.') or name.startswith('.rodata')) and raw:
            alignment=max(4,h[8]);position=(position+alignment-1)&~(alignment-1)
            assigned[headers.index(h)]=position;position+=len(raw)
    assert position<=LIMIT,(position,LIMIT)
    sh,syms=sections['.symtab'];strh=headers[sh[6]];strings=data[strh[4]:strh[4]+strh[5]]
    symbols=[];exports={}
    for off in range(0,len(syms),sh[9]):
        name,value,size,info,other,section=struct.unpack_from('<IIIBBH',syms,off)
        name=strings[name:].split(b'\0',1)[0].decode()
        if info&15==2:value&=~1
        address=assigned[section]+value if section in assigned else EXTERNAL.get(name)
        symbols.append((name,address));
        if section in assigned and name:exports[name]=address
    payload=bytearray(position-ENTRY)
    for name,(h,raw) in sections.items():
        index=headers.index(h)
        if index in assigned:payload[assigned[index]-ENTRY:assigned[index]-ENTRY+len(raw)]=raw
    for name,(h,raw) in sections.items():
        if h[1]!=9 or h[7] not in assigned:continue
        base=assigned[h[7]]
        for off in range(0,len(raw),8):
            at,info=struct.unpack_from('<II',raw,off);kind=info&255;sym=info>>8
            symbol,target=symbols[sym];assert target is not None,(symbol,kind)
            pos=base+at-ENTRY
            if kind==2:
                addend=int.from_bytes(payload[pos:pos+4],'little');payload[pos:pos+4]=(target+addend).to_bytes(4,'little')
            elif bl_target(payload[pos:pos+4],at)==at:
                payload[pos:pos+4]=bl(base+at,target)
            else:raise AssertionError(('unsupported relocation',kind,symbol,hex(at)))
    # Tail dispatch preserves exactly the original caller's SP and LR on Xbox.
    # C wrappers add a nested frame; the closed BLE library is not assumed to tolerate it.
    for name,target in (('bt_table_tail',0x13628),('bt_scan_tail',0x11690),('bt_pack_tail',0xb908),('bt_bonds_tail',0x15bac)):
        while (ENTRY+len(payload))&3:payload.append(255)
        exports[name]=ENTRY+len(payload)
        code=Code(exports[name]);code.op(0xb40f)  # push r0-r3, keep incoming LR
        code.literal(0,STATE);code.byte(0,0);code.cmp(0,0);code.op(0xbc0f)
        code.branch('xbox',0)
        if name=='bt_table_tail':code.literal(0,TABLE)
        elif name=='bt_bonds_tail':code.literal(0,0x7c000)
        elif name=='bt_scan_tail':code.literal(0,0x194f0);code.mov(1,24)
        code.literal(3,(exports['bt_pack_d'] if name=='bt_pack_tail' else target)|1);code.op(0x4718)
        code.label('xbox')
        if name=='bt_bonds_tail':
            # SMP uses raw flash callbacks; unlike stock's top-of-flash journal,
            # this app-bank gap is write-protected after every cold boot.
            code.op(0xb510);code.call(0xbd00);code.op(0xbc10);code.op(0xbc08);code.op(0x469e)
            code.literal(0,0x3b000)
        code.literal(3,target|1);code.op(0x4718)
        payload+=code.finish()
    assert ENTRY+len(payload)<=LIMIT
    results={'btmodes.bin':bytes(payload),'btdnoop.bin':(exports['bt_ignore']+1).to_bytes(4,'little')}
    for pc,(name,symbol) in HOOKS.items():results[name]=bl(pc,exports[symbol])
    profile=bytearray(0x19520-0x194c8)
    profile[0:7]=bytes.fromhex('02 c8 2d 1b 30 01 00')
    profile[8:10]=bytes((2,2))
    profile[12:12+len(DNAME)]=DNAME
    response=bytes((len(DNAME)+1,9))+DNAME
    assert len(response)==24
    profile[0x194f0-0x194c8:0x194f0-0x194c8+len(response)]=response
    results['btprofile.bin']=bytes(profile)
    text=(ROOT/'firmware-src/bt-dinput-map.hex').read_text(encoding='ascii')
    results['btdmap.bin']=bytes.fromhex(' '.join(line.split('#')[0] for line in text.splitlines()))
    assert 0x19f00+len(results['btdmap.bin'])<=0x1a000
    for name,content in results.items():(blobs/name).write_bytes(content)
    print({'code_bytes':len(payload),'dmap_bytes':len(results['btdmap.bin']),'state_bytes':2,'symbols':exports})
    return exports
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--compiler',type=Path,required=True);p.add_argument('--output',type=Path,default=ROOT/'build/bt-modes');p.add_argument('--blobs',type=Path,default=ROOT/'u2c_customfw/firmware/blobs');a=p.parse_args()
    build(a.compiler,a.output,a.blobs)
