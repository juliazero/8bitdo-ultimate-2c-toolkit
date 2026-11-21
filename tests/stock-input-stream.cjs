const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/diagnostics.js', 'utf8');
const pad = {id: 'Unknown Gamepad (Vendor: 2dc8 Product: 301b)', mapping: '', connected: true,
  axes: [0,0,0,-1,-1,0,0,0,0,3.286], buttons: Array.from({length: 16}, () => ({value: 0, pressed: false}))};
let pipe, request;
const events = [];
const encoder = new TextEncoder();
const context = vm.createContext({navigator: {platform: 'Win32', getGamepads: () => [pad]}, document: {hasFocus: () => true}, window: {},
  token: 'test', AbortController, AbortSignal, TextDecoder, setTimeout, clearTimeout,
  api: async () => ({level: -1, state: 'unknown'}),
  fetch: async (url, options) => {
    request = {url, options};
    const body = new ReadableStream({start(controller) {pipe = controller;}});
    options.signal.addEventListener('abort', () => pipe.error(new Error('aborted')));
    return {ok: true, body};
  },
});
vm.runInContext(source, context);
context.window.u2cDiagnostics = event => events.push(event);
(async () => {
  vm.runInContext('windowsPoll = 1', context);
  const running = vm.runInContext('streamWindowsStock(windowsPad())', context);
  await Promise.resolve();
  assert.ok(request.url.startsWith('/api/windows-stock-stream?token='));
  const sample = value => encoder.encode('data: ' + JSON.stringify({available: true, analogTriggers: true, lt: value, rt: value}) + '\n\n');
  pipe.enqueue(sample(.25));
  await new Promise(setImmediate);
  assert.equal(events.at(-1).lt, .25, 'stream input must repaint immediately without a polling timer');
  const split = sample(.75);
  pipe.enqueue(split.slice(0, 10)); pipe.enqueue(split.slice(10));
  await new Promise(setImmediate);
  assert.equal(events.at(-1).rt, .75, 'split stream frames must preserve the new analog value');
  vm.runInContext('stopWindowsStockStream()', context);
  await running;
  assert.equal(request.options.signal.aborted, true);
  assert.equal(vm.runInContext('windowsStockState.lt', context), .75, 'canceling transport must not clear the valid sample');
  console.log('Continuous HID stream and immediate trigger rendering: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
