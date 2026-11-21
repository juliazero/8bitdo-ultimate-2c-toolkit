# SPDX-License-Identifier: GPL-3.0-or-later
"""Export generic Y recipe blobs from a verified stock bank, never unit data."""
import argparse,hashlib
from pathlib import Path
from tools.build_switch2_trial import assemble,KEYS,END
from u2c_customfw.firmware import image,recipes

REGIONS=((0x14a62,4),(0x7738,156),(0xa138,4),(0xa184,4),(0xa2e4,4),(0xa478,4),
         (0xd156,4),(0xa4b8,4),(0xa22a,4),(0xa23a,4),(0x11938,2),(0x11a00,4),(0x1a000,0x3000))

def export(stock,build,output):
    frame,report=assemble(stock,build)
    base=recipes.build(stock,KEYS)
    covered={a for start,size in REGIONS for a in range(start,start+size)}
    assert len(base)==len(frame)==END-image.BASE
    assert all(i+image.BASE in covered for i,(a,b) in enumerate(zip(base,frame)) if a!=b)
    output.mkdir(parents=True,exist_ok=True)
    for address,size in REGIONS:
        (output/f'switch2-{address:x}.bin').write_bytes(frame[address-image.BASE:address-image.BASE+size])
    return len(REGIONS)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backup',type=Path,required=True)
    p.add_argument('--build',type=Path,default=Path('build/switch2-pairing'))
    p.add_argument('--output',type=Path,default=recipes.BLOBS);a=p.parse_args()
    raw=a.backup.read_bytes()
    stock=next((raw[i:i+image.BANK_A_SIZE] for i in (image.BASE,image.BANK_B)
        if hashlib.sha256(raw[i:i+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256),None)
    if stock is None:p.error('No verified original 1.09 bank')
    print('Exported',export(stock,a.build,a.output),'generic Y blobs; regenerate stock hashes and blob manifest after changes.')
if __name__=='__main__':main()
