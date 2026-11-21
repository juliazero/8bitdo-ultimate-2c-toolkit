const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/app.js', 'utf8');
const errors = [{hidden: true}, {hidden: true}];
const button = {};
let fail = false;
const calls = [];
const context = vm.createContext({
  document: {querySelectorAll: () => errors},
  api: async (...args) => {calls.push(args); if (fail) throw new Error('Folder unavailable'); return {ok: true};},
  button,
});
vm.runInContext(source.slice(source.indexOf('async function openStore('), source.indexOf('document.querySelectorAll("[data-open-store]"')), context);
(async () => {
  await vm.runInContext('openStore(button)', context);
  assert.equal(button.disabled, false);
  assert.equal(calls[0][0], 'POST');
  assert.equal(calls[0][1], '/api/open-store');
  assert.ok(errors.every(el => el.hidden));
  fail = true;
  await vm.runInContext('openStore(button)', context);
  assert.equal(button.disabled, false);
  assert.ok(errors.every(el => !el.hidden && el.textContent === 'Folder unavailable'));
  console.log('Open backups and logs folder: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
