# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Y RAM reservations in existing dead-code tails. No hardware writes."""
from pathlib import Path
import hashlib,json
from tools.build_usb_mode_prototype import Code
from tools.build_ring_hold import bl,bl_target
ROOT=Path(__file__).resolve().parents[1]
CTX=0x840070
Y_REPLY=0x841740
Y_SHADOW=0x841790
Y_PEER=0x8412c4
CCC={0xa:0x8412ca,0xe:0x8412cc,0x1a:0x8412ce,0x1e:0x8417d0,0x22:0x8417d2,0x26:0x8400b2}
Y_INPUT_CCC=CCC[0xe];Y_REPLY_CCC=CCC[0x1e]
Y_PAIRING=0x8424f5
Y_PENDING=0x8424f4;Y_RATE=0x8424f6
Y_COUNTER=0x8424f8;Y_MASK=0x8424f9;Y_ENABLED=0x8424fa;Y_CONFIGURED=0x8424fb
Y_PLAYER=0x8424fc;Y_PEER_VALID=0x8424fd;Y_BOOTSTRAP=0x8424fe
STACK_TOP=0x844000;STACK_FLOOR=0x843cf0
LED_ENTRY=0x1c800
CAVES=((0x840070,0x8400b4),(0x841740,0x8417d4),(0x8412c4,0x8412d0),(0x8424f4,0x842500))
REGIONS={"pairing":(CTX,65),"active":(0x8400b1,1),"pairing_indicator":(Y_PAIRING,1),"reply":(Y_REPLY,80),"input_shadow":(Y_SHADOW,63),"peer":(Y_PEER,6),
         "pending":(Y_PENDING,1),"rate":(Y_RATE,2),"counter":(Y_COUNTER,1),"mask":(Y_MASK,1),
         "enabled":(Y_ENABLED,1),"configured":(Y_CONFIGURED,1),"player":(Y_PLAYER,1),
         "peer_valid":(Y_PEER_VALID,1),"bootstrap":(Y_BOOTSTRAP,2),
         **{"ccc_"+hex(handle):(address,2) for handle,address in CCC.items()}}

EXPECTED_BLOBS={'wdog5.bin': 'd40ed4efac58aa880a6b82db3a3f2872aa3b891b7655db07da4701055eb91c9c', 'sram.bin': 'c3f0966446812c97bb976ba0f28b9a4b8a158939d6f158107faa747c3b546699', 'stall.bin': 'f4c60c286a6141864bc56fee16cd98f8d00e8e8845ba11269901874301bebe36', 'led.bin': '7876889cb9c2981ee4e78338f2eca0589f431d509f361f6b5d029f5eeb7eecd1'}

def validate():
    occupied=set()
    for name,(address,size) in REGIONS.items():
        assert any(lo<=address and address+size<=hi for lo,hi in CAVES),name
        for byte in range(address,address+size):
            assert byte not in occupied,(name,hex(byte))
            occupied.add(byte)
    assert len(occupied)==239
    # These payload lengths are prerequisites for reclaiming their padded tails.
    blobs=ROOT/"u2c_customfw/firmware/blobs"
    assert (blobs/"wdog5.bin").stat().st_size==80
    assert (blobs/"sram.bin").stat().st_size==180
    assert (blobs/"stall.bin").stat().st_size==104
    assert (blobs/"led.bin").stat().st_size==44
    for name,digest in EXPECTED_BLOBS.items():
        assert hashlib.sha256((blobs/name).read_bytes()).hexdigest()==digest,name
    return {"allocated_bytes":len(occupied),"tail_capacity":240,"stock_bss_overlap":False,
            "stack_overlap":False,"production_installed":False}

def led_code():
    original=(ROOT/"u2c_customfw/firmware/blobs/led.bin").read_bytes()
    assert len(original)==44 and bl_target(original[32:36],0x1758)==0xfaa0
    relocated=bytearray(original);relocated[32:36]=bl(LED_ENTRY+32,0xfaa0)
    code=Code(0x1738);code.literal(3,LED_ENTRY|1);code.op(0x4718)
    shim=code.finish();assert len(shim)==8
    return original,bytes(relocated),shim

def prepare_frame(frame):
    validate();result=bytearray(frame)
    # Verify the stock boot's BSS end and main-stack literal before reserving RAM.
    assert int.from_bytes(result[0x17c:0x180],"little")==STACK_FLOOR
    assert int.from_bytes(result[0x174:0x178],"little")==STACK_TOP
    for lo,hi in CAVES:result[lo-0x840000:hi-0x840000]=bytes(hi-lo)
    original,relocated,shim=led_code()
    result[0x1738:0x1740]=shim
    offset=LED_ENTRY-0x6000
    if len(result)<offset:result.extend(bytes([255])*(offset-len(result)))
    result[offset:offset+len(relocated)]=relocated
    return result

def write_manifest(output):
    report=validate()
    original,relocated,shim=led_code()
    (output/"housekeeping-relocated.bin").write_bytes(relocated)
    (output/"housekeeping-trampoline.bin").write_bytes(shim)
    report.update({"regions":{name:{"address":hex(address),"bytes":size} for name,(address,size) in REGIONS.items()},
                   "housekeeping_flash_entry":hex(LED_ENTRY),"stack_top":hex(STACK_TOP),
                   "stock_bss_end":hex(STACK_FLOOR),"interrupt_stack_validated":False,
                   "requires_ram_overlay":True,"blob_prerequisites":EXPECTED_BLOBS})
    (output/"ram-layout.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report

