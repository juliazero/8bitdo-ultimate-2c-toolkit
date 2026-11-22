// Run with node tests/windows-diagnostics.cjs; no browser or controller required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const events = [];
let pad = null;
let effect = null;
const nativeRequests = [];
const context = vm.createContext({
  navigator: { platform: 'Win32', getGamepads: () => [null, pad] },
  document: { hasFocus: () => true },
  window: {}, clearInterval() {}, setInterval() { return 1; },
  AbortSignal,
  token: 'test', fetch: async (url, options) => { nativeRequests.push({url, options}); return {ok: true, json: async () => ({available: true, index: 2, ok: true})}; },
});
vm.runInContext(fs.readFileSync('u2c_customfw/web/static/diagnostics.js', 'utf8'), context);
context.window.u2cDiagnostics = e => events.push(e);
vm.runInContext('bridge().postMessage({op: "start"})', context);
assert.equal(events.at(-1).connected, false);
pad = { connected: true, mapping: 'standard', id: 'Xbox', axes: [0.5, -0.25, 0, 1],
  buttons: Array.from({length: 17}, (_, i) => ({pressed: i === 0, value: i === 6 ? 0.75 : 0})),
  vibrationActuator: { playEffect: (name, options) => { effect = {name, ...options}; return Promise.resolve('complete'); }, reset: () => Promise.resolve() } };
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).type, 'input');
assert.equal(events.at(-1).ly, 0.25);
assert.equal(events.at(-1).lt, 0.75);
assert.equal(events.at(-1).pressed[0], 'A');
assert.equal(events.at(-2).extended, true);
vm.runInContext('windowsNativeRumble = {available: true, index: 2}', context);
const postsBeforeBrowserTest = nativeRequests.filter(r => r.options.method === 'POST').length;
vm.runInContext('bridge().postMessage({op: "vibrate", locality: "left", id: 5})', context);
assert.equal(effect.duration, 450);
assert.equal(effect.weakMagnitude, 0);
assert.equal(effect.strongMagnitude, 0.3);
assert.equal(nativeRequests.filter(r => r.options.method === 'POST').length, postsBeforeBrowserTest, 'browser haptics take priority over native requests');
vm.runInContext('bridge().postMessage({op: "stop"})', context);
console.log('Windows diagnostics bridge: passed');

pad.vibrationActuator = null;
vm.runInContext('windowsNativeRumble = {available: true, index: 2}; windowsTick()', context);
assert.deepEqual(Array.from(events.at(-1).localities), ['default', 'left', 'right']);
vm.runInContext('bridge().postMessage({op: "vibrate", locality: "right", id: 6})', context);
const native = nativeRequests.at(-1);
assert.equal(native.url, '/api/windows-rumble');
assert.equal(native.options.method, 'POST');
assert.equal(native.options.headers['X-Token'], 'test');
const request = JSON.parse(native.options.body);
assert.equal(request.index, 2);
assert.equal(request.locality, 'right');
assert.ok(request.expires >= Date.now());
const postsBeforeDuplicate = nativeRequests.filter(r => r.options.method === 'POST').length;
vm.runInContext('bridge().postMessage({op: "vibrate", locality: "left", id: 7})', context);
assert.equal(nativeRequests.filter(r => r.options.method === 'POST').length, postsBeforeDuplicate, 'do not queue overlapping native tests');
vm.runInContext('bridge().postMessage({op: "stop"})', context);
assert.equal(nativeRequests.at(-1).url, '/api/windows-rumble/stop');
assert.deepEqual(JSON.parse(nativeRequests.at(-1).options.body), {index: 2});
console.log('Native XInput vibration bridge: passed');

vm.runInContext('bridge().postMessage({op: "ring", level: 2, save: false, id: 8})', context);
assert.equal(nativeRequests.at(-1).url, '/api/bluetooth-ring');
assert.equal(nativeRequests.at(-1).options.headers['X-Token'], 'test');
assert.deepEqual(JSON.parse(nativeRequests.at(-1).options.body), {level: 2, save: false});
setImmediate(() => {
  const result = events.find(e => e.op === 'ring' && e.id === 8);
  assert.equal(result.ok, true, 'ring bridge must return a result, not leave the Settings timer waiting');
});

// Exercise the firmware details renderer with a later dongle tweak enabled.
const source = fs.readFileSync('u2c_customfw/web/static/app.js', 'utf8');
let rows;
const ui = vm.createContext({
  $: () => ({}), st: { devices: [] }, reading: false,
  fw: {device: 'dongle', known: true, version: 1.03, tweaks: [
    {on: false, title: 'Xbox 360'}, {on: true, title: 'D-input'}, {on: true, title: 'Custom name'}]},
  detailRows: (_, value) => { rows = value; },
});
vm.runInContext(source.slice(source.indexOf('function details()'), source.indexOf('function detailRows(')), ui);
vm.runInContext('details()', ui);
assert.deepEqual(Array.from(rows[1][1]), ['1.03 with 2 tweaks', 'D-input', 'Custom name']);
ui.fw.tweaks[2].on = false;
vm.runInContext('details()', ui);
assert.deepEqual(Array.from(rows[1][1]), ['1.03 with 1 tweak', 'D-input']);
ui.fw.tweaks[1].on = false;
vm.runInContext('details()', ui);
assert.equal(rows[1][1], 'Original 1.03');
console.log('Dongle firmware details: passed');

context.st = {devices: [{name: '8BitDo Ultimate 2C Wireless Controller'}]};
pad.id = 'Kontroler konsoli Xbox 360 dla systemu Windows (STANDARD GAMEPAD)';
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).name, '8BitDo Ultimate 2C Wireless Controller');
pad.id = 'Sony DualSense';
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).name, 'Sony DualSense');
context.st = {devices: []}; pad.id = 'Xbox';
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).name, 'Xbox', 'an unrelated Xbox must not be relabeled');

// A receiver can initially enumerate as the controller's cable identity.
const firmwareContext = vm.createContext({busy: false, CONTROLLER: ['cable', 'cable_360', 'boot'],
  st: {devices: [{mode: 'cable', product: 0x310a}]}, fw: null, reading: false,
  api: async () => ({device: 'dongle', paired: true, known: true, version: 1.03}),
  render() {}, matchInstalled() {},
});
vm.runInContext(source.slice(source.indexOf('const firmwareTarget ='), source.indexOf('const firmwareIOBusy =')), firmwareContext);
vm.runInContext(source.slice(source.indexOf('let fwSeq = 0;'), source.indexOf('document.querySelectorAll("[data-icon]")')), firmwareContext);
(async () => {
  vm.runInContext(source.slice(source.indexOf('const firmwareDeviceKey ='), source.indexOf('// System and browser notifications')), firmwareContext);
  const originalKey = vm.runInContext('firmwareDeviceKey(st)', firmwareContext);
  await vm.runInContext('readFirmware()', firmwareContext);
  assert.equal(firmwareContext.st.devices[0].mode, 'dongle_paired');
  assert.equal(firmwareContext.fw.device, 'dongle');
  assert.equal(vm.runInContext('firmwareDeviceKey(st)', firmwareContext), originalKey,
    'correcting the receiver mode must not look like a USB reconnect');
  await vm.runInContext('readFirmware()', firmwareContext);
  assert.equal(vm.runInContext('firmwareDeviceKey(st)', firmwareContext), originalKey,
    'manual reads must preserve the original enumeration mode');
  assert.notEqual(vm.runInContext('firmwareDeviceKey({devices: []})', firmwareContext), originalKey,
    'actual disconnection must still trigger a refresh');

  console.log('XInput receiver identity correction: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

// A receiver shares cable descriptors until the firmware read identifies it.
context.st = {devices: [{mode: 'cable', name: '8BitDo Ultimate 2C Wireless Controller'}]};
context.reading = true; context.refreshing = false; context.fw = null;
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).category, 'Identifying connection…');
context.st.devices[0].mode = 'dongle_paired';
context.fw = {device: 'dongle', paired: true}; context.reading = false;
vm.runInContext('windowsTick()', context);
assert.equal(events.at(-1).category, '2.4 GHz dongle · XInput');
console.log('Connection label waits for firmware identification: passed');

// D-input uses the vendor motor test even without standard browser mapping.
pad.id='Unknown Gamepad (Vendor: 2dc8 Product: 301d)';pad.mapping='';pad.vibrationActuator=null;
vm.runInContext("windowsNativeActive=null; windowsNativeRumble={available:true,kind:'vendor',product:0x301d}",context);
assert.equal(vm.runInContext('windowsNativeTarget()?.kind',context),'vendor');
vm.runInContext("windowsBridge.postMessage({op:'vibrate',locality:'right',id:'vendor-test'})",context);
assert.equal(JSON.parse(nativeRequests.at(-1).options.body).kind,'vendor');
vm.runInContext('windowsStopRumble()',context);
assert.equal(JSON.parse(nativeRequests.at(-1).options.body).kind,'vendor');
pad.id='Other controller';assert.equal(vm.runInContext('windowsNativeTarget()',context),null);
console.log('D-input vendor motor diagnostics: passed');
