# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real RX fragmentation, ATT dispatch and compiled Y pairing. No device I/O."""
import argparse,hashlib,json
from pathlib import Path
from tools.build_switch2_trial import assemble
from tools.check_switch2_gatt import GattCPU
from tools.check_switch2_pairing import CTX,REQUEST,ADDRESS
from tools.switch2_pairing import PairingSession
from tools import switch2_layout as layout
from u2c_customfw.firmware import image

def check(stock,build):
 code,report=assemble(stock,build)
 symbols=json.loads((build/'pairing-prototype.json').read_text())['symbols']
 def cpu(active=1):
  c=GattCPU(code)
  c.call(0x11e64,0x843178,40,8);c.call(0x121a0);c.call(0x13628,0x1c000)
  c.call(0x1478c,0x8436c0,32) # exact original startup a178..a17c
  c.seed(0x842f20,83,2);c.seed(0x842f22,83,2);c.seed(0x8400b1,active,1)
  for i,v in enumerate(ADDRESS):c.seed(CTX+59+i,v,1)
  return c
 def fragments(att):
  body=len(att).to_bytes(2,'little')+b'\x04\0'+att
  return [bytes((2 if i==0 else 1,len(body[i:i+27])))+body[i:i+27] for i in range(0,len(body),27)]
 def packet(c,data,entry=0x14a58):
  for i,v in enumerate(data):c.seed(REQUEST+i,v,1)
  return c.call(entry,0x40,REQUEST) if entry==0x14a58 else c.call(entry,REQUEST)
 key=bytes.fromhex('159101040011000000eabd4713893542c679ee07f2532c6c31')
 chunks=fragments(b'\x52\x14\0'+key);assert [len(x)-2 for x in chunks]==[27,5]
 old=cpu(0)
 assert packet(old,chunks[0],0x14798)==0 and packet(old,chunks[1],0x14798)==0
 assert old.rd(0x843bf8,2)==32 # the original receiver rejects the completed 34-byte packet
 c=cpu();c.call(symbols['y_att_disconnect']);c.seed(layout.CCC[0x1a],1,2)
 model=PairingSession(ADDRESS);host=bytes.fromhex('1371da7d1a00')
 requests=[bytes.fromhex('15910101000e00000002')+host+host,key,
           bytes.fromhex('15910102001100000040b08a5fcd1f9b41125cac6f3838a073'),bytes.fromhex('159101030001000000')]
 for req in requests:
  c.seed(0x843a24,c.rd8(0x843a23),1);before=len(c.notifications)
  shadow=bytes(c.rd8(layout.Y_SHADOW+i) for i in range(60))
  for chunk in fragments(b'\x52\x14\0'+req):packet(c,chunk)
  assert c.notifications[before:]==[(0x1a,model.handle(req))],req.hex()
  assert bytes(c.rd8(layout.Y_SHADOW+i) for i in range(60))==shadow
  assert c.rd8(0x843bfa)==0 and c.rd8(layout.Y_PENDING)==0
 assert c.rd8(CTX)==4 and c.rd8(CTX+30)==2
 cold=cpu()
 cold.flash=c.flash
 assert cold.call(symbols['y_bond_load'],CTX)==1
 assert bytes(cold.rd8(CTX+43+i) for i in range(16))==model.bonds[0].ltk_wire
 # Vibration initialization is unsupported, but must reply instead of timing out.
 req=bytes((10,0x91,1,8,0,20,0,0))+bytes(20)
 c.seed(0x843a24,c.rd8(0x843a23),1);before=len(c.notifications)
 for chunk in fragments(b'\x52\x14\0'+req):packet(c,chunk)
 assert c.notifications[before:]==[(0x1a,bytes.fromhex('0a01010810780000'))]
 # Queue pressure: preserve an unsent response even if another fragmented command arrives.
 c.seed(layout.Y_PENDING,8,1);snapshot=bytes(c.rd8(layout.Y_REPLY+i) for i in range(80))
 for chunk in chunks:packet(c,chunk)
 assert bytes(c.rd8(layout.Y_REPLY+i) for i in range(80))==snapshot
 c.seed(layout.Y_PENDING,0,1)
 packet(c,chunks[0]);assert c.rd8(0x843bfa)>0
 c.call(symbols['y_att_disconnect']);assert c.rd8(0x843bfa)==0
 # Oversized/new malformed starts cannot write outside the 80-byte buffer.
 shadow=bytes(c.rd8(layout.Y_SHADOW+i) for i in range(60))
 for data in (bytes.fromhex('02044a000400'),bytes.fromhex('020104'),bytes.fromhex('0101ff')):
  packet(c,data)
 assert bytes(c.rd8(layout.Y_SHADOW+i) for i in range(60))==shadow
 return {'ok':True,'stock_32_byte_failure_reproduced':True,'fragmented_full_pairing_checked':True,
         'in_place_response_and_cold_boot_checked':True,'pending_reply_preserved':True,
         'disconnect_and_bounds_checked':True,'fragmented_unsupported_reply_checked':True,
         'stack_peak_bytes':c.stack_top-c.minimum_sp,'stack_margin_bytes':c.minimum_sp-c.stack_floor,'hardware_written':False}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backup',type=Path,required=True);p.add_argument('--build',type=Path,default=Path('build/switch2-pairing'));a=p.parse_args()
 raw=a.backup.read_bytes();stock=next(raw[x:x+image.BANK_A_SIZE] for x in (image.BASE,image.BANK_B) if hashlib.sha256(raw[x:x+image.BANK_A_SIZE]).hexdigest()==image.STOCK_SHA256)
 print(json.dumps(check(stock,a.build),indent=2))
if __name__=='__main__':main()
