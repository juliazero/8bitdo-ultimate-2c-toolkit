const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/diagnostics.js', 'utf8');
const elements = new Map();
const events = [];
const neutral = () => ({id: 'Unknown Gamepad (Vendor: 2dc8 Product: 301b)', connected: true, mapping: '',
  axes: [-.010, -.010, -.010, -1, -1, -.010, 0, 0, 0, 3.286],
  buttons: Array.from({length: 16}, () => ({value: 0, pressed: false})),
});
let pad = neutral();
const context = vm.createContext({navigator: {platform: 'Win32', getGamepads: () => [pad]},
  window: {}, document: {hasFocus: () => true}, AbortSignal,
  api: async () => ({level: 1, state: 'reported'}),
  st: {devices: [{mode: 'bluetooth'}]}, busy: false,
  $: id => {if (!elements.has(id)) elements.set(id, {}); return elements.get(id);},
});
vm.runInContext(source, context);
context.window.u2cDiagnostics = event => events.push(event);
const map = () => vm.runInContext('mapUltimate2cBluetooth(windowsPad())', context);
const result = map();
assert.equal(result.extended, true);
assert.equal(result.raw, false);
assert.equal(result.name, '8BitDo Ultimate 2C Wireless Controller');
assert.equal(result.lt, 0);
assert.equal(result.rt, 0);
// The native stream must stay closed in Settings, but standard DInput
// Gas/Brake axes should still provide analog live trigger travel.
vm.runInContext('windowsView = "settings"', context);
pad = neutral();
assert.deepEqual([map().lt, map().rt], [0,0]);
pad.axes[3] = 0; pad.axes[4] = .5; // Gas=half, Brake=three quarters.
const settingsAnalog = map();
assert.deepEqual([settingsAnalog.lt, settingsAnalog.rt], [.75,.5]);
assert.equal(settingsAnalog.analogTriggers, true);
vm.runInContext('windowsView = "diagnostics"', context);
pad = neutral();
assert.equal(result.pressed.length, 0, 'out-of-range neutral hat must not press the D-pad');
for (const [index, name] of [[0,'A'],[1,'B'],[2,'L4'],[5,'R4'],[3,'X'],[4,'Y'],[6,'LB'],[7,'RB'],[8,'LT'],[9,'RT'],[10,'View'],[11,'Menu'],[12,'Home'],[13,'L3'],[14,'R3']]) {
  pad = neutral();
  pad.buttons[index] = {value: 1, pressed: true};
  assert.deepEqual(Array.from(map().pressed), [name]);
}
pad = neutral();
pad.axes[0] = 1; pad.axes[1] = -1; pad.axes[2] = -.5; pad.axes[5] = .75;
pad.axes[4] = 0; pad.axes[3] = 1;
const moved = map();
assert.deepEqual([moved.lx,moved.ly,moved.rx,moved.ry,moved.lt,moved.rt], [1,1,-.5,-.75,0,0]);
pad = neutral(); pad.axes[3] = 0; pad.axes[4] = 0;
assert.deepEqual([map().lt, map().rt], [0, 0], 'inactive browser axis slots must not create half-pressed triggers');
const directions = [['Up'],['Up','Right'],['Right'],['Down','Right'],['Down'],['Down','Left'],['Left'],['Up','Left']];
for (let i = 0; i < 8; i++) {
  pad = neutral(); pad.axes[9] = i / 3.5 - 1;
  assert.deepEqual(Array.from(map().pressed), directions[i]);
}
pad = neutral(); pad.id = 'Unknown Gamepad (Vendor: 1234 Product: 301b)';
assert.equal(map(), null, 'do not apply this mapping to other products');
pad = neutral(); pad.mapping = 'standard';
assert.equal(map(), null, 'preserve the browser standard mapping');
pad = neutral(); pad.axes = [0, 0];
assert.equal(map(), null, 'unsupported raw layouts retain the diagnostic fallback');
pad = neutral();
context.nativePadId = pad.id;
vm.runInContext('windowsStockPad = nativePadId; windowsStockAt = Date.now(); windowsStockState = {available: true, analogTriggers: true, lt: .25, rt: .75, pressed: ["LT", "RT"]}', context);
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).lt, .25);
assert.equal(events.at(-1).rt, .75);
context.triggerSample = events.at(-1);
elements.set('test-lt', {style: {}}); elements.set('test-rt', {style: {}});
vm.runInContext('paintTriggers(triggerSample)', context);
assert.equal(elements.get('test-lt').style.width, '25%');
assert.equal(elements.get('test-rt').style.width, '75%');
for (const [byte, percent] of [[0,0],[64,25],[128,50],[192,75],[255,100]]) {
  context.triggerSample = {analogTriggers: true, lt: byte / 255, rt: byte / 255};
  vm.runInContext('paintTriggers(triggerSample)', context);
  assert.equal(elements.get('test-lt').style.width, `${percent}%`);
  assert.equal(elements.get('test-rt').style.width, `${percent}%`);
}
pad.buttons[8] = pad.buttons[9] = {value: 1, pressed: true};
vm.runInContext('windowsStockAt = Date.now() - 1000; windowsTick()', context);
context.triggerSample = events.at(-1);
vm.runInContext('paintTriggers(triggerSample)', context);
assert.equal(elements.get('test-lt').style.width, '25%', 'a brief HTTP gap must retain the actual analog sample');
assert.equal(elements.get('test-trigger-note').hidden, true);
pad.axes[0] = .8; pad.buttons[0] = {value: 1, pressed: true};
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).lx, .8, 'analog cache must not freeze browser stick input');
assert.ok(events.at(-1).pressed.includes('A'));
vm.runInContext('windowsStockAt = Date.now() - 4000; windowsTick()', context);
context.triggerSample = events.at(-1);
vm.runInContext('paintTriggers(triggerSample)', context);
assert.equal(elements.get('test-lt').style.width, '0%', 'digital trigger press must not saturate the analog bar');
assert.equal(elements.get('test-trigger-note').hidden, false);
context.lastState = events.at(-2);
vm.runInContext('testState = lastState; testVisible = true; mountLive = () => { globalThis.mountedLive = true; }; diagnosticsRender()', context);
assert.equal(context.mountedLive, true, 'stock Bluetooth must use the regular graphical controller test');
console.log('Ultimate 2C stock Bluetooth mapping: passed');
(async () => {
  await Promise.resolve();
  vm.runInContext('windowsPoll = 1; windowsStockPad = windowsPad().id; windowsStockAt = Date.now(); windowsStockState = {available: true, analogTriggers: true, lt: .25, rt: .75}', context);
  context.reply = {available: false, connected: true};
  vm.runInContext('acceptWindowsStockState(windowsPad(), reply)', context);
  assert.equal(vm.runInContext('windowsStockState.lt', context), .25, 'waiting for a fresh report must not clear valid input');
  context.reply = {available: false, connected: false};
  vm.runInContext('acceptWindowsStockState(windowsPad(), reply)', context);
  assert.equal(vm.runInContext('windowsStockState', context), null, 'an actual disconnect clears the sample immediately');
  console.log('Analog trigger continuity through request delays: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

pad = neutral(); pad.id = 'Unknown Gamepad (Vendor: 2dc8 Product: 301c)';
assert.equal(map().category, '2.4 GHz dongle · D-input');
assert.equal(map().raw, false);
console.log('Dongle D-input browser mapping: passed');

pad = neutral();
pad.id = 'Unknown Gamepad (Vendor: 2dc8 Product: 301d)';
vm.runInContext('st.devices = [{mode:"cable_dinput"}]; windowsTick()', context);
assert.equal(events.at(-1).category, 'USB \u00b7 D-input');
assert.equal(events.at(-1).name, '8BitDo Ultimate 2C Wireless Controller');
console.log('USB D-input mapping and transport: passed');

pad = neutral();
pad.id = 'Unknown Gamepad (Vendor: 2dc8 Product: 301b)';
vm.runInContext('st.devices = [{mode:"cable_dinput"}]; windowsTick()', context);
assert.equal(events.at(-1).category, 'USB \u00b7 D-input');
console.log('Wireless 2C USB identity is distinguished from Bluetooth: passed');

vm.runInContext('testState={category:"Bluetooth · D-input"}',context);
assert(vm.runInContext('testControls().includes("L4") && testControls().includes("R4")',context));
vm.runInContext('testState={category:"USB · XInput"}',context);
assert(!vm.runInContext('testControls().includes("L4") || testControls().includes("R4")',context));

pad=neutral();pad.mapping='standard';context.nativePadId=pad.id;
vm.runInContext('windowsStockPad=nativePadId;windowsStockAt=Date.now();windowsStockState={lt:0,rt:0,pressed:["L4","R4"]}',context);
vm.runInContext('windowsTick()',context);
assert(events.at(-1).pressed.includes('L4') && events.at(-1).pressed.includes('R4'));

pad = neutral(); pad.mapping = 'standard'; pad.axes = [1,1];
pad.buttons[0] = {pressed: true, value: 1};
context.nativePadId = pad.id;
vm.runInContext(`windowsStockPad = nativePadId; windowsStockAt = Date.now(); windowsStockState = {
  available:true, lx:-.25, ly:.5, rx:.75, ry:-1, lt:.2, rt:.8, pressed:['Y','Down','L4']}; windowsTick()`,context);
assert.deepEqual(Array.from(events.at(-1).pressed), ['A','L4'], 'browser buttons must not be replaced by HID');
assert.deepEqual(['lx','ly','rx','ry','lt','rt'].map(k=>events.at(-1)[k]), [1,-1,0,-0,0,0]);
console.log('Hybrid mapping preserves browser sticks and buttons: passed');
