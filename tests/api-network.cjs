const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/app.js', 'utf8');
async function test(method, responses, expectedCalls, error, signal) {
  let calls = 0;
  const context = vm.createContext({token: 'test', TypeError, signal,
    fetch: async () => { calls++; const response = responses.shift(); if (response instanceof Error) throw response; return response; },
  });
  vm.runInContext(source.slice(source.indexOf('const api ='), source.indexOf('const ICON =')), context);
  const run = vm.runInContext(`api('${method}', '/api/test', undefined, signal)`, context);
  if (error) await assert.rejects(run, error);
  else assert.equal((await run).ok, true);
  assert.equal(calls, expectedCalls);
}
const ok = () => ({ok: true, text: async () => '{"ok":true}'});
(async () => {
  await test('GET', [new TypeError('Failed to fetch'), ok()], 2);
  await test('GET', [{ok: true, text: async () => {throw new TypeError('input stream');}}, ok()], 2);
  await test('GET', [new TypeError('Failed to fetch'), new TypeError('Failed to fetch')], 2, /Failed to fetch/);
  await test('POST', [new TypeError('Failed to fetch')], 1, /Failed to fetch/);
  await test('GET', [{ok: false, status: 403, text: async () => '{"error":"forbidden"}'}], 1, /forbidden/);
  await test('GET', [new TypeError('aborted')], 1, /aborted/, {aborted: true});
  console.log('HTTP read retry and POST protection: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
