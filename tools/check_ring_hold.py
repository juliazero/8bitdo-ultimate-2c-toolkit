# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute compiled five-second Home ring poll in TC32; no device access."""
import importlib.util,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from u2c_customfw.firmware import image,recipes
from tools.build_ring_hold import bl_target,POLL

def check():
 spec=importlib.util.spec_from_file_location('tc32_ring',ROOT/'firmware-src/test_longrumble.py')
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 img=bytearray([255])*image.APP_SIZE
 tail=(recipes.BLOBS/'ringtail5.bin').read_bytes();old=(recipes.BLOBS/'ringtail.bin').read_bytes()
 assert tail[:len(old)]==old
 img[0x18400-image.BASE:0x18400-image.BASE+len(tail)]=tail
 assert bl_target((recipes.BLOBS/'pollhook5.bin').read_bytes(),0xa484)==POLL
 old_entry=bl_target((recipes.BLOBS/'pollhook.bin').read_bytes(),0xa484)
 old_wdog=(recipes.BLOBS/'wdog.bin').read_bytes();wdog=(recipes.BLOBS/'wdog5.bin').read_bytes()
 assert len(wdog)==len(old_wdog)
 assert sum(bl_target(wdog[i:i+4],0x20+i)==POLL for i in range(0,len(wdog)-3,2))==1
 assert sum(a!=b for a,b in zip(old_wdog,wdog))<=4
 L4,R4=1<<26,1<<25
 class CPU(mod.Controller):
  def __init__(self,level=1,percent=50,alerts=False):
   frame=bytearray(img);frame[0x18ff0-image.BASE]=1 if alerts else 0
   self.writes=[];super().__init__(bytes(frame));self.stock_calls=0;self.motors=[];self.saves=[]
   self.seed(0x843415,percent,1);self.seed(0x843424,3300);self.seed(0x843929,255,1)
   self.seed(0x8416d3,128,1);self.seed(0x8416d2,1,1);self.seed(0x841640,1,1);self.seed(0x841641,level,1)
  def seed(self,a,v,n=4):
   for i in range(n):self.ram[a+i]=(v>>(8*i))&255
  def wr8(self,a,v):
   assert (0x841640<=a<0x84165c or 0x800500<=a<0x800800 or a==0x843929 or mod.SP0-0x400<=a<mod.SP0 or mod.CONNECTED<=a<mod.CONNECTED+4),hex(a)
   self.writes.append((a,v&255));self.ram[a]=v&255
  def trap(self,pc,r):
   if pc==0xcfa4:self.stock_calls+=1;return True
   if pc==0xfa68:self.motors.append(r[:3]);return True
   if pc==0xd538:self.saves.append(self.rd8(0x843929));return True
   if pc==0xd36c:return True
   if pc in (0xfe98,0xd056,0xdac4):raise AssertionError(f'unexpected flash/reboot/power-off {pc:#x}')
   return False
  def poll(self,ms,buttons):
   self.seed(0x800740,(ms*16000)&0xffffffff);self.seed(0x84344c,buttons);self.call(POLL);assert self.rd8(0x8416d2)==1 and self.rd8(0x8416d3)==128
  @property
  def level(self):return self.rd8(0x841641)
 c=CPU();c.poll(0,L4|R4);c.poll(4999,L4|R4);assert c.level==1
 c.poll(5000,L4|R4);assert c.level==2 and c.rd8(0x841645)==15
 c.poll(15000,L4|R4);assert c.level==2
 c.poll(15100,L4);c.poll(15200,L4|R4);c.poll(20200,L4|R4);assert c.level==3
 assert c.stock_calls==7
 c=CPU();c.poll(0,L4|R4);c.poll(100,0);c.poll(10000,0);assert c.level==1
 c=CPU();c.poll(0,L4|R4);c.poll(4900,R4);c.poll(5000,L4|R4);c.poll(9900,L4|R4);assert c.level==1
 c.poll(10000,L4|R4);assert c.level==2
 for first in (L4,R4):
  c=CPU();c.poll(0,first);c.poll(1000,L4|R4);c.poll(6000,L4|R4);assert c.level==2
 c=CPU(5);c.poll(267000,L4|R4);c.poll(272000,L4|R4);assert c.level==0
 c=CPU();c.seed(0x841654,1);c.poll(100,0);assert c.motors==[[0,0,1]]
 ring_tick=bl_target((recipes.BLOBS/'ringhook.bin').read_bytes(),0xd4f0)
 for percent in (100,15,5):
  for external,full in ((0,0),(1,0),(1,1)):
   c=CPU(percent=percent,alerts=True)
   c.seed(0x800518,external,1);c.seed(0x843423,full,1)
   for ms in range(0,15001,100):
    c.poll(ms,L4|R4)
    if ms%200==0:c.call(ring_tick,1,0x180,mod.PACKET)
    assert c.level==(1 if ms<5000 else 2),(percent,external,full,ms,c.level)
   assert c.saves==[2],(percent,external,full,c.saves)
 print('ok: LED ticks, battery alerts at 100/15/5%, one change after 5s, one save, chord state preserved')
 print({'ok':True,'compiled_bytes':len(tail)-(POLL-0x18400),'tail_free':0x18ff0-0x18400-len(tail),'cases':['5s boundary','one shot','release/rearm','short tap','interrupted hold','either paddle first','wrap','clock wrap','stock chords','motor stop']})
 return True
if __name__=='__main__':check()
