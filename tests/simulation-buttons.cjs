const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let now=0;
const sent=[];
const ctx=vm.createContext({performance:{now:()=>now},console,Set,Map});
vm.runInContext(fs.readFileSync('u2c_customfw/web/static/simulation.js','utf8'),ctx);
ctx.send=(name,down)=>{sent.push([name,down]);return Promise.resolve();};
vm.runInContext('labButtonSend=send',ctx);
function button(name) {
  const b={style:{},setPointerCapture(){}};ctx.b=b;ctx.name=name;
  vm.runInContext('labBindButton(b,name)',ctx);return b;
}
function event(pointerId=1){return {button:0,pointerId,preventDefault(){},type:'pointerup'};}
const a=button('A');
a.onpointerdown(event());assert.deepEqual(sent.pop(),['A',true]);
a.onpointerup(event());assert.deepEqual(sent.pop(),['A',false]);
now=100;a.onpointerdown(event());a.onpointerup(event());
assert.deepEqual(sent.pop(),['A',true]);assert.equal(sent.length,0); // second tap latched
now=1000;a.onpointerdown(event());a.onpointerup(event());
assert.deepEqual(sent.pop(),['A',false]);assert.deepEqual(sent.pop(),['A',true]);
const b=button('B');now=2000;b.onpointerdown(event(2));
vm.runInContext('labReleaseMomentary()',ctx);
assert.deepEqual(sent.pop(),['B',false]);assert.deepEqual(sent.pop(),['B',true]);
now=3000;a.onpointerdown(event());a.onpointercancel({...event(),type:'pointercancel'});
assert.deepEqual(sent.pop(),['A',false]);sent.length=0;
a.onkeydown({key:' ',repeat:false,preventDefault(){}});a.onkeyup({key:' ',preventDefault(){}});
assert.deepEqual(sent,[['A',true],['A',false]]);
console.log('OK: momentary, double tap, unlatch, focus loss, cancel, keyboard');
