// Run with node tests/diagnostics-read-scheduling.cjs; no controller needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('u2c_customfw/web/static/app.js', 'utf8');
const diagnostics = fs.readFileSync('u2c_customfw/web/static/diagnostics.js', 'utf8');
const cable = {mode: 'cable', vendor: 0x2dc8, product: 0x310a, name: 'Ultimate 2C'};
const clone = o => JSON.parse(JSON.stringify(o));
const flush = () => new Promise(setImmediate);

async function testFirmware() {
  let devices = [cable], reads = 0, release, now = 1000, timer, delay;
  const context = vm.createContext({
    Date: {now: () => now}, document: {hidden: false}, setInterval() {}, clearTimeout() {}, setTimeout(fn, ms) {timer = fn; delay = ms; return 1;},
    tab: 'firmware', st: null, fw: null, busy: false, reading: false, CONTROLLER: ['cable', 'cable_dinput', 'boot'],
    $: () => ({}), render() {}, renderDisclaimer() {}, matchInstalled() {},
    api: async (_, path) => {
      if (path === '/api/state') return {devices: clone(devices), accepted: true};
      assert.equal(path, '/api/firmware');
      reads++;
      if (reads === 1) await new Promise(r => {release = r;});
      return {known: true, version: 1.09, recipes: []};
    }
  });
  const run = code => vm.runInContext(code, context);
  run(app.slice(app.indexOf('let refreshing ='), app.indexOf('document.querySelectorAll("[data-icon]")')));
  run(app.slice(app.indexOf('const firmwareDeviceKey ='), app.indexOf('window.addEventListener?.("gamepadconnected"')));
  const first = run('refresh()');
  await flush();
  assert.equal(reads, 1);
  // Gamepad enumeration arrives while the initial full firmware read is active.
  await run('refresh()');
  context.tab = 'diagnostics';
  await run('refresh()');
  release(); await first; await flush();
  assert.equal(reads, 1);
  context.tab = 'firmware';
  devices = [{...cable, name: 'Resolved product name'}, {mode: 'bluetooth_xbox', vendor: 0x45e, product: 0xb13, name: 'Xbox'}];
  await run('refresh()');
  assert.equal(reads, 1, 'late Bluetooth enumeration and USB name resolution do not repeat the USB read');
  devices = [cable];
  await run('refresh()');
  assert.equal(reads, 1, 'unchanged device reuses its initial firmware result');
  await run('refresh(true)');
  assert.equal(reads, 2, 'manual Read Again forces an actual read');
  context.tab = 'diagnostics';
  devices = [{...cable, mode: 'cable_dinput', product: 0x301d}];
  run('scheduleDeviceRefresh()'); run('scheduleDeviceRefresh()');
  assert.equal(delay, 500, 'one debounced notification');
  assert.equal(reads, 2, 'no read before debounce');
  context.cableBatteryBusy = true; await run('watchDevices()'); assert.equal(reads, 2);
  context.cableBatteryBusy = false; context.testPending = 'left';
  await run('watchDevices()'); assert.equal(reads, 2);
  context.testPending = null; await run('watchDevices()');
  assert.equal(reads, 3, 'read in Diagnostics after notification');
  context.tab = 'firmware'; await run('watchDevices()');
  context.tab = 'settings'; await run('refresh()');
  assert.equal(reads, 3, 'all tabs reuse the completed read');
  devices = []; await run('watchDevices()'); assert.equal(context.fw, null);
  devices = [cable]; now += 1000; await run('watchDevices()');
  now += 1000; await run('watchDevices()'); assert.equal(reads, 4, 'read after reconnect in Settings');
  await run('watchDevices()'); assert.equal(reads, 4);
  console.log('Shared firmware read, cache, stable enumeration, reconnect and busy checks: passed');
}

async function testBattery() {
  let now = 1000, calls = 0, release;
  const context = vm.createContext({navigator: {platform: 'Win32'}, document: {hidden: false}, window: {},
    st: {devices: [clone(cable)]}, busy: false, reading: false, refreshing: false,
    Date: {now: () => now}, $: () => null, api: async (_, path) => {
      assert.equal(path, '/api/battery'); calls++;
      if (calls === 3) await new Promise(r => {release = r;});
      return {source: 'cable', percent: 77};
    }
  });
  const run = code => vm.runInContext(code, context);
  run(diagnostics); run('testVisible = true; syncCableBattery()');
  now = 5999; run('pollCableBattery()'); assert.equal(calls, 0);
  now = 6000; context.reading = true; run('pollCableBattery()'); assert.equal(calls, 0);
  context.reading = false; run('pollCableBattery()'); await flush(); assert.equal(calls, 1);
  now = 65999; run('pollCableBattery()'); assert.equal(calls, 1);
  now = 66000; run('testPending = "left"; pollCableBattery()'); assert.equal(calls, 1);
  run('testPending = null; pollCableBattery()'); await flush(); assert.equal(calls, 2);
  // Manual read still works, then delays the next scheduled read; no duplicate requests.
  now = 67000; const pending = run('readCableBattery()');
  await run('readCableBattery()'); assert.equal(calls, 3);
  context.st = {devices: []}; run('syncCableBattery()');
  context.st = {devices: [{...cable, mode: 'cable_dinput', product: 0x301d}]}; run('syncCableBattery()');
  release(); await pending; assert.equal(run('cableBattery'), null, 'discard result from old USB mode');
  now = 70000; run('pollCableBattery()'); assert.equal(calls, 3);
  now = 72000; run('testVisible = false; pollCableBattery()'); assert.equal(calls, 3);
  run('testVisible = true; pollCableBattery()'); await flush(); assert.equal(calls, 4);
  assert.equal(run('cableBattery.percent'), 77);
  console.log('USB battery 5s settle, 60s interval, busy/vibration checks and reconnection: passed');
}
function testDiagnosticsGate() {
  const context = vm.createContext({navigator: {platform: 'Win32'}, window: {}, document: {},
    st: {devices: [cable]}, fw: null, reading: false, busy: false,
    firmwareTarget: state => state.devices.some(d => d.mode === 'cable'),
    $: () => null});
  vm.runInContext(diagnostics, context);
  const run = code => vm.runInContext(code, context);
  run('testState = {connected: true, extended: true, foreground: true}');
  assert.equal(run('testMode()'), 'identifying');
  context.reading = true;
  assert.equal(run('testMode()'), 'identifying');
  context.reading = false; context.fw = {known: true};
  assert.equal(run('testMode()'), 'live');
  context.fw = {error: 'unavailable'};
  assert.equal(run('testMode()'), 'live', 'failed read does not permanently block diagnostics');
  context.fw = null; context.st = {devices: []};
  assert.equal(run('testMode()'), 'live', 'unrelated gamepad is not gated');
  console.log('Diagnostics firmware identification gate: passed');
}
(async () => {await testFirmware(); await testBattery(); testDiagnosticsGate();})().catch(e => {console.error(e); process.exitCode = 1;});
