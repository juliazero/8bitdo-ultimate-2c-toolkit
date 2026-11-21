const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/settings.js', 'utf8');
const elements = new Map();
const buttons = Array.from({length: 6}, (_, i) => ({dataset: {live: String(i)}}));
const context = vm.createContext({
  st: {devices: [{mode: 'bluetooth_xbox'}]}, fw: {note: 'Bluetooth'}, busy: false,
  bridge: () => ({}), clearTimeout() {},
  document: {querySelectorAll: () => buttons},
  $: id => {if (!elements.has(id)) elements.set(id, {}); return elements.get(id);},
  setOn: (element, on) => {element.on = on;},
});
vm.runInContext(source.slice(source.indexOf('let ringLevel ='), source.indexOf('addEventListener("DOMContentLoaded"', source.indexOf('let ringLevel ='))), context);
vm.runInContext('ringRender()', context);
assert.equal(buttons.some(b => b.on), false);
assert.match(elements.get('ring-note').textContent, /Current brightness is not reported over Bluetooth/);
vm.runInContext('ringVia = "bluetooth"; ringDone(null, 2, false)', context);
assert.equal(buttons[2].on, true);
assert.match(elements.get('ring-note').textContent, /Last sent level/);
vm.runInContext('ringDone(null, null, true)', context);
assert.match(elements.get('ring-note').textContent, /does not report the current or saved brightness/);
context.st.devices = [{mode: 'bluetooth'}];
vm.runInContext('ringRender()', context);
assert.ok(buttons.every(button => button.disabled));
assert.match(elements.get('ring-note').textContent, /requires the Xbox Series X\|S and Rumble over Bluetooth tweaks/);
console.log('Bluetooth ring state labeling: passed');
