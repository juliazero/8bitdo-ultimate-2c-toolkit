const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/settings.js', 'utf8');
const context = vm.createContext({
  st: {devices: [{mode: 'cable_dinput'}]},
  fw: {recipes: ['settings']},
  busy: false,
});
vm.runInContext(source.slice(0, source.indexOf('function settingsMount()')), context);
assert.equal(vm.runInContext('settingsAccess()', context), null,
  'USB DInput with settings tweak must be allowed to open Settings');
context.fw.recipes = [];
assert.match(vm.runInContext('settingsAccess()', context), /Controller settings isn't installed/);
context.fw = {recipes: ['settings']};
context.st.devices = [{mode: 'dongle_paired'}];
assert.match(vm.runInContext('settingsAccess()', context), /dongle has none/);
context.st.devices = [{mode: 'boot'}];
assert.match(vm.runInContext('settingsAccess()', context), /update mode/);
console.log('USB DInput Settings accessibility: passed');
