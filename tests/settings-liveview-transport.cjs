// Settings Live View must not keep a separate Windows HID report stream open.
// A second gamepad HID handle can interfere with the device re-enumerating
// when switching between USB DInput and XInput.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const pad = {id: '8BitDo Ultimate 2C Wireless Controller (Vendor: 2dc8 Product: 301b)',
  connected: true, mapping: '', axes: [0, 0, 0, 0], buttons: []};
const events = [];
const stockControl = [];
const context = vm.createContext({
  navigator: {platform: 'Win32', getGamepads: () => [pad]},
  document: {hasFocus: () => true, hidden: false}, window: {},
  clearInterval() {}, setInterval() {return 1;}, AbortSignal,
  token: 'test',
  fetch: (url, opts) => { if (url === '/api/windows-stock-control') stockControl.push(JSON.parse(opts.body).suspended); return Promise.resolve({ok: true}); },
});
vm.runInContext(fs.readFileSync('u2c_customfw/web/static/diagnostics.js', 'utf8'), context);
context.window.u2cDiagnostics = event => events.push(event);
vm.runInContext(`
  let streamed = 0, statusReads = 0, batteryReads = 0;
  streamWindowsStock = () => {streamed++;};
  windowsRumbleStatus = () => {statusReads++;};
  windowsBatteryStatus = () => {batteryReads++;};
`, context);
const value = expression => vm.runInContext(expression, context);
value('windowsBridge.postMessage({op:"start", view:"settings"})');
assert.equal(value('windowsView'), 'settings');
assert.deepEqual(stockControl, [true], 'Settings must explicitly release the server HID input handle');
assert.equal(value('streamed'), 0, 'Settings must not open the native HID stream');
assert.equal(value('statusReads'), 0, 'Settings must not probe native rumble');
assert.equal(value('batteryReads'), 0, 'Settings must not poll native battery');
assert.equal(events.at(-1).type, 'input', 'Settings Live View still receives browser input');
value('windowsTick()');
assert.equal(value('streamed'), 0, 'Further Settings ticks must not restart HID stream');
value('windowsBridge.postMessage({op:"pause"})');
assert.equal(value('windowsPoll'), null, 'Pause Live View must stop browser gamepad polling');
assert.deepEqual(stockControl, [true, true], 'Pause explicitly keeps the server HID handle closed');
value('windowsBridge.postMessage({op:"start", view:"settings"})');
assert.deepEqual(stockControl, [true, true, true], 'Resuming Settings must not open native HID');

value('windowsBridge.postMessage({op:"stop"})');
value('windowsBridge.postMessage({op:"start", view:"diagnostics"})');
assert.equal(value('windowsView'), 'diagnostics');
assert.deepEqual(stockControl, [true, true, true, false], 'Diagnostics may enable the native stream again');
assert.ok(value('streamed') > 0, 'Diagnostics retains native HID stream for L4/R4 and triggers');
assert.ok(value('statusReads') > 0, 'Diagnostics retains native rumble discovery');
assert.ok(value('batteryReads') > 0, 'Diagnostics retains native battery reporting');
value('windowsBridge.postMessage({op:"stop"})');
console.log('Windows Settings live view isolates HID streaming: passed');
