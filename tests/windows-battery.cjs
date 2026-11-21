const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/diagnostics.js', 'utf8');
let pads = [{id: 'pad', connected: true, mapping: 'standard', axes: [], buttons: [], vibrationActuator: {playEffect() {}}}];
const events = [];
const calls = [];
const context = vm.createContext({navigator: {platform: 'Win32', getGamepads: () => pads}, document: {hasFocus: () => true},
  window: {}, AbortSignal, clearInterval() {}, setInterval() {return 1;}, token: 'test',
  api: async (method, path) => {calls.push(path); return {level: .73, state: 'reported', source: 'Windows Bluetooth'};},
});
vm.runInContext(source, context);
const handleDiagnostics = context.window.u2cDiagnostics;
context.window.u2cDiagnostics = event => events.push(event);
(async () => {
  vm.runInContext('bridge().postMessage({op: "start"})', context);
  await new Promise(setImmediate);
  vm.runInContext('windowsTick()', context);
  assert.equal(events.at(-1).battery.level, .73);
  assert.ok(calls.includes('/api/windows-battery'), 'battery must refresh even with Chromium browser haptics');
  assert.equal(vm.runInContext('batteryText({level: 0, state: "reported"})', context), '0 %');
  pads.push({...pads[0], id: 'other'});
  vm.runInContext('windowsTick()', context);
  assert.equal(events.at(-1).battery.level, -1, 'do not attribute a battery reading to an ambiguous gamepad');
  pads = [{id: '8BitDo Ultimate 2C Wireless', connected: true, mapping: '', axes: [.25, -.5], buttons: [{value: 1, pressed: true}]}];
  vm.runInContext('windowsTick()', context);
  assert.equal(events.at(-1).connected, true);
  assert.equal(events.at(-1).extended, false);
  assert.equal(events.at(-1).raw, true);
  assert.equal(events.at(-1).rawAxes[0], .25);
  assert.equal(events.at(-1).pressed.length, 0, 'do not invent Xbox names for nonstandard buttons');
  vm.runInContext('windowsNativeRumble = {available: true, index: 0}', context);
  assert.equal(vm.runInContext('windowsNativeTarget()', context), null, 'unmapped Bluetooth must not control an unrelated XInput slot');
  const rawState = events.at(-2), rawInput = events.at(-1);
  const elements = new Map();
  Object.assign(context, {st: {devices: []}, busy: false, tab: 'diagnostics', refresh() {},
    $: id => {if (!elements.has(id)) elements.set(id, {}); return elements.get(id);},
  });
  vm.runInContext('testVisible = true', context);
  handleDiagnostics(rawState);
  handleDiagnostics(rawInput);
  assert.equal(elements.get('test-raw-name').textContent, '8BitDo Ultimate 2C Wireless Controller');
  assert.equal(elements.get('test-raw-buttons').textContent, 'Buttons: 1: 1.00');
  assert.match(elements.get('test-raw-axes').textContent, /1: 0.250/);
  pads = [];
  vm.runInContext('windowsTick()', context);
  assert.equal(events.at(-1).connected, false);
  console.log('Windows battery bridge: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
