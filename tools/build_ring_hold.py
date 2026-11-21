# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile/link only the new ring poll into existing tail slack; preserve published ABI.
No device access. Retains every byte of the published shared ring block and links
three verified direct-call relocations in the separately compiled C function.
"""
import argparse,os,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
POLL=0x18f50
TC_TO_THUMB=[4,5,8,9,7,6,11,10,14,15,2,3,13,12,1,0]
THUMB_TO_TC={v:k for k,v in enumerate(TC_TO_THUMB)}
def bl_target(data,pc):
 a,b=struct.unpack('<HH',data)
 a=TC_TO_THUMB[a>>12]<<12|(a&4095);b=TC_TO_THUMB[b>>12]<<12|(b&4095)
 if a>>11!=30 or b>>11!=31:return None
 off=(a&2047)<<12|(b&2047)<<1
 return pc+4+off-((off&(1<<22))*2)
def bl(pc,target):
 d=target-pc-4
 assert d%2==0 and -(1<<22)<=d<(1<<22)
 return b''.join(struct.pack('<H',THUMB_TO_TC[x>>12]<<12|(x&4095)) for x in (0xf000|(d>>12&2047),0xf800|(d>>1&2047)))
def elf_sections(data):
 assert data[:6]==b'\x7fELF\x01\x01'
 off=struct.unpack_from('<I',data,32)[0];size,count,names=struct.unpack_from('<HHH',data,46)
 headers=[struct.unpack_from('<10I',data,off+i*size) for i in range(count)]
 h=headers[names];strings=data[h[4]:h[4]+h[5]]
 sections={}
 for h in headers:
  name=strings[h[0]:].split(b'\0',1)[0].decode()
  sections[name]=(h,data[h[4]:h[4]+h[5]])
 return headers,sections

def build(compiler,output,blobs):
 output.mkdir(parents=True,exist_ok=True)
 compiler=compiler.resolve();env=dict(os.environ);env['PATH']=str(compiler.parent)+os.pathsep+env.get('PATH','')
 obj=output/'ring.o'
 subprocess.run([str(compiler),'-Os','-Wall','-Werror','-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections','-c',str(ROOT/'firmware-src/packer.c'),'-o',str(obj)],check=True,env=env)
 data=obj.read_bytes();headers,sections=elf_sections(data)
 _,raw=sections['.text.main_poll'];code=bytearray(raw)
 old=(blobs/'ringtail.bin').read_bytes();old_entry=bl_target((blobs/'pollhook.bin').read_bytes(),0xa484)
 assert 0x18400<=old_entry<0x18400+len(old)
 # Resolve settings_poll from the original main poll's three calls.
 legacy_len=len(sections['.text.main_poll_legacy'][1])
 body=old[old_entry-0x18400:old_entry-0x18400+legacy_len]
 calls=[bl_target(body[i:i+4],old_entry+i) for i in range(0,len(body)-3,2)]
 calls=[a for a in calls if a is not None]
 assert len(calls)==3 and 0xfa68 in calls and 0xcfa4 in calls,calls
 target=next(a for a in calls if a not in (0xfa68,0xcfa4))
 symbols={'motor_set':0xfa68,'stock_chords':0xcfa4,'settings_poll':target}
 rel_header,rel=sections['.rel.text.main_poll'];symh=headers[rel_header[6]];strh=headers[symh[6]]
 strings=data[strh[4]:strh[4]+strh[5]];seen=[]
 for i in range(0,len(rel),8):
  at,info=struct.unpack_from('<II',rel,i);sym=info>>8
  name_at=struct.unpack_from('<I',data,symh[4]+sym*symh[9])[0]
  name=strings[name_at:].split(b'\0',1)[0].decode();assert name in symbols,name
  assert bl_target(code[at:at+4],at)==at  # compiler's CALL addend, not arbitrary data
  code[at:at+4]=bl(POLL+at,symbols[name]);seen.append(name)
 assert sorted(seen)==sorted(symbols)
 assert len(old)<=POLL-0x18400 and POLL+len(code)<=0x18ff0
 tail=old.ljust(POLL-0x18400,b'\xff')+code
 wdog=bytearray((blobs/'wdog.bin').read_bytes());patched=0
 for i in range(0,len(wdog)-3,2):
  if bl_target(wdog[i:i+4],0x20+i)==old_entry:
   wdog[i:i+4]=bl(0x20+i,POLL);patched+=1
 assert patched==1
 results={'ringtail5.bin':tail,'pollhook5.bin':bl(0xa484,POLL),'wdog5.bin':bytes(wdog)}
 for name,value in results.items():(blobs/name).write_bytes(value)
 print({'function_bytes':len(code),'tail_bytes':len(tail),'free_tail_bytes':0x18ff0-0x18400-len(tail),'settings_poll':hex(target)})
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--compiler',required=True,type=Path)
 p.add_argument('--output',type=Path,default=ROOT/'build/ring-hold');p.add_argument('--blobs',type=Path,default=ROOT/'u2c_customfw/firmware/blobs')
 a=p.parse_args();build(a.compiler,a.output.resolve(),a.blobs.resolve())
