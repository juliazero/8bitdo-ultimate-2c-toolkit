# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a factory-layout Pro 2 GATT table for offline TC32 execution only."""
import argparse,json,struct,uuid
from pathlib import Path
from tools import switch2_layout as layout
BASE=0x1c000;RAM=0x843c00;COUNT=0x2f
READ=1;WRITE=2

def build(output):
    report=json.loads((output/"pairing-prototype.json").read_text(encoding="utf-8"))
    symbols=report["symbols"]
    table=bytearray((COUNT+1)*24);struct.pack_into("<I",table,0,COUNT)
    pool=bytearray();pool_base=BASE+len(table)
    def store(data):
        address=pool_base+len(pool);pool.extend(data);return address
    def uid(value):
        return int(value,16).to_bytes(2,"little") if len(value)==4 else uuid.UUID(value).bytes[::-1]
    def attribute(handle,type_uuid,value,permissions=READ,group=0,callback=0,ram=None,length=None):
        type_bytes=uid(type_uuid)
        value_ptr=store(value) if ram is None else ram
        size=len(value) if length is None else length
        struct.pack_into("<HBB5I",table,handle*24,group,permissions,len(type_bytes),size,
                         store(type_bytes),value_ptr,callback,0)
    def service(handle,value,end):attribute(handle,"2800",uid(value),group=end-handle+1)
    def characteristic(handle,type_uuid,properties,value=b"",permissions=READ,ram=None,length=None,callback=0):
        attribute(handle-1,"2803",bytes((properties,))+handle.to_bytes(2,"little")+uid(type_uuid))
        attribute(handle,type_uuid,value,permissions,callback=callback,ram=ram,length=length)
    rate="679d5510-5a24-4dee-9557-95df80486ecb"
    unknown="b746df8c-f358-495b-9cd2-e3bbeda4f979"
    service(1,"00c5af5d-1964-4e30-8f51-1956f96bd280",7)
    for handle,suffix,props in ((3,"81",2),(5,"82",8),(7,"83",2)):
        # Unknown auxiliary values are placeholders, not claimed implemented.
        if handle==5:
            characteristic(handle,"00c5af5d-1964-4e30-8f51-1956f96bd2"+suffix,props,
                           permissions=WRITE,ram=layout.Y_BOOTSTRAP,length=2)
        else:characteristic(handle,"00c5af5d-1964-4e30-8f51-1956f96bd2"+suffix,props,b"",0)
    service(8,"ab7de9be-89fe-49ad-828f-118f09df7fd0",0x2a)
    chars=((0xa,"ab7de9be-89fe-49ad-828f-118f09df7fd2",0x12),
           (0xe,"7492866c-ec3e-4619-8258-32755ffcc0f8",0x12),
           (0x12,"cc483f51-9258-427d-a939-630c31f72b05",4),
           (0x14,"649d4ac9-8eb7-4e6c-af44-1ea54fe5f005",4),
           (0x16,"3dacbc7e-6955-40b5-8eaf-6f9809e8b379",4),
           (0x18,"4147423d-fdae-4df7-a4f7-d23e5df59f8d",4),
           (0x1a,"c765a961-d9d8-4d36-a20a-5315b111836a",0x10),
           (0x1e,"506d9f7d-4278-4e95-a549-326ba77657e0",0x10),
           (0x22,"d3bd69d2-841c-4241-ab15-f86f406d2a80",0x10),
           (0x26,"ab7de9be-89fe-49ad-828f-118f09df7fde",0x12),
           (0x2a,"ab7de9be-89fe-49ad-828f-118f09df7fdf",4))
    ccc={};placeholder=[]
    for handle,type_uuid,props in chars:
        if handle in (0x14,0x16):
            characteristic(handle,type_uuid,props,permissions=WRITE,ram=layout.Y_REPLY,length=0,
                           callback=symbols["y_att_write"]|1)
        elif handle==0xa:
            # Common report notifications; no second 63-byte read shadow is reserved.
            characteristic(handle,type_uuid,0x10,permissions=0)
        elif handle in (0x1a,0x1e):
            characteristic(handle,type_uuid,props,permissions=0)
        elif handle==0xe:
            characteristic(handle,type_uuid,props,ram=layout.Y_SHADOW,length=63)
        else:
            characteristic(handle,type_uuid,props,permissions=0)
            placeholder.append(handle)
        if props&0x10:
            ccc[handle]=layout.CCC[handle]
            attribute(handle+1,"2902",b"",READ|WRITE,ram=ccc[handle],length=2)
            if handle in (0xa,0xe):
                attribute(handle+2,rate,b"",READ|WRITE,ram=layout.Y_RATE,length=2)
            else:attribute(handle+2,rate if handle in (0xa,0x26) else unknown,b"",0)
    assert ccc[0x1e]==layout.Y_REPLY_CCC
    service(0x2b,"1800",0x2f)
    characteristic(0x2d,"2a00",2,b"Pro Controller")
    characteristic(0x2f,"2a01",2,bytes.fromhex("c4 03"))
    # The stock SDK returns end=0 for a service at the final handle.
    # No Service Changed implementation exists, so omit the empty 1801 service.
    payload=bytes(table+pool)
    (output/"gatt-prototype.bin").write_bytes(payload)
    metadata={"offline_only":True,"installable":False,"entry":BASE,"bytes":len(payload),
              "attributes":COUNT,"record_size":24,"ccc":{hex(h):hex(p) for h,p in ccc.items()},
              "unimplemented_values":[hex(h) for h in placeholder]}
    (output/"gatt-prototype.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    return metadata

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--build",type=Path,default=Path("build/switch2-pairing"))
    a=p.parse_args();print(json.dumps(build(a.build),indent=2))
if __name__=="__main__":main()
