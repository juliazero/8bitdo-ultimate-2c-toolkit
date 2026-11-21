# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an offline TC32 Pro 2 pairing core. Never creates an installable image."""
import argparse,json,os,struct,subprocess
from pathlib import Path
from tools import switch2_layout as layout
from tools.build_ring_hold import elf_sections,bl,bl_target
ROOT=Path(__file__).resolve().parents[1]
ENTRY=0x1a000

def build(compiler,output):
    output.mkdir(parents=True,exist_ok=True)
    compiler=compiler.resolve();env=dict(os.environ)
    env["PATH"]=str(compiler.parent)+os.pathsep+env.get("PATH","")
    objects=[]
    for stem in ("switch2-pairing","switch2-bonds","switch2-input","switch2-security","switch2-memory","switch2-profile","switch2-controls"):
        obj=output/(stem+".o");objects.append(obj)
        subprocess.run([str(compiler),"-Os","-Wall","-Werror","-ffreestanding","-fno-builtin",
                        "-fno-jump-tables","-ffunction-sections","-fdata-sections","-c",
                        str(ROOT/("firmware-src/"+stem+".c")),"-o",str(obj)],check=True,env=env)
    obj=output/"switch2-combined.o"
    subprocess.run([str(compiler),"-nostdlib","-r",*[str(p) for p in objects],"-o",str(obj)],check=True,env=env)
    data=obj.read_bytes();headers,sections=elf_sections(data)
    assigned={};position=ENTRY
    for name,(header,raw) in sections.items():
        if (name.startswith(".text.") or name.startswith(".rodata")) and raw:
            alignment=max(4,header[8]);position=(position+alignment-1)&~(alignment-1)
            assigned[headers.index(header)]=position;position+=len(raw)
    assert position<=ENTRY+8192
    sh,syms=sections[".symtab"];strings_header=headers[sh[6]]
    strings=data[strings_header[4]:strings_header[4]+strings_header[5]]
    symbols=[];exports={}
    for off in range(0,len(syms),sh[9]):
        name,value,size,info,other,section=struct.unpack_from("<IIIBBH",syms,off)
        name=strings[name:].split(b"\0",1)[0].decode()
        if info&15==2:value&=~1
        address=assigned[section]+value if section in assigned else {"ble_aes_le":0x16ba4,"ble_notify":0x1458c,"stock_l2cap_rx":0x14798,"stock_rx_buffer":0x1478c,"flash_unlock":0xbd00,"flash_erase":0xfe98,"register_ltk_handler":0x121bc,"stock_ltk_reply":0x130d8,"stock_ltk_negative":0x13114,"stock_public_address":0x11188,"stock_gatt_table":0x13628,"stock_adv_params":0x116c0,"stock_adv_data":0x11614,"stock_scan_response":0x11690,"stock_adv_enable":0x11598,"stock_disconnect":0x12438,"stock_indicator":0xd3d8,"save_settings":0xd538,"stock_bt_mac":bl_target((ROOT/"u2c_customfw/firmware/blobs/btmac.bin").read_bytes(),0xa138),"old_bt_home":bl_target((ROOT/"u2c_customfw/firmware/blobs/bthome.bin").read_bytes(),0xa478),"stock_transport":0xc384,"stock_reboot":0xd056,"stock_home":0xd0b0}.get(name)
        symbols.append((name,address,info&15==2))
        if section in assigned and name:exports[name]=address
    payload=bytearray(position-ENTRY)
    for name,(header,raw) in sections.items():
        index=headers.index(header)
        if index in assigned:payload[assigned[index]-ENTRY:assigned[index]-ENTRY+len(raw)]=raw
    for name,(header,raw) in sections.items():
        if header[1]!=9 or header[7] not in assigned:continue
        base=assigned[header[7]]
        for off in range(0,len(raw),8):
            at,info=struct.unpack_from("<II",raw,off);kind=info&255
            symbol,target,function=symbols[info>>8];assert target is not None,(symbol,kind)
            pos=base+at-ENTRY
            if kind==2:
                addend=int.from_bytes(payload[pos:pos+4],"little")
                payload[pos:pos+4]=((target+addend)|int(function)).to_bytes(4,"little")
            elif bl_target(payload[pos:pos+4],at)==at:
                payload[pos:pos+4]=bl(base+at,target)
            else:raise AssertionError(("unsupported relocation",kind,symbol,hex(at)))
    (output/"pairing-prototype.bin").write_bytes(payload)
    report={"offline_only":True,"installable":False,"entry":ENTRY,"bytes":len(payload),"symbols":exports,"ram_layout":layout.write_manifest(output)}
    (output/"pairing-prototype.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=ROOT/"build/switch2-pairing")
    args=parser.parse_args();print(json.dumps(build(args.compiler,args.output),indent=2))
if __name__=="__main__":main()
