const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('u2c_customfw/web/static/app.js', 'utf8');
async function check({ terminal, streamError = false, recover = [], expected, reads }) {
  const elements = new Map();
  let readCount = 0, posts = 0, polls = 0;
  const reader = {
    async read() {
      readCount++;
      if (streamError || readCount > 1) throw new Error('Error in input stream');
      return {done: false, value: new TextEncoder().encode(`data: ${JSON.stringify(terminal)}\n\n`)};
    },
    cancel: () => Promise.resolve(), releaseLock() {},
  };
  const context = vm.createContext({
    busy: false, st: {storage_dir: 'C:/Backups and Logs'}, token: 'test', TextDecoder, AbortSignal,
    $: id => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); },
    render() {}, refresh() {}, onDongle: () => true, line() {}, setPct() {},
    STEPS: {}, LOST: 'Task status unavailable',
    setTimeout: fn => fn(),
    api: async method => {
      if (method === 'POST') { posts++; return {job: 'job-123'}; }
      polls++;
      const response = recover.shift();
      if (response instanceof Error || !response) throw response || new Error('offline');
      return response;
    },
    fetch: async () => ({ok: true, body: {getReader: () => reader}}),
  });
  vm.runInContext(source.slice(source.indexOf('async function runJob('), source.indexOf('function ask(')), context);
  await vm.runInContext('runJob("Backing up", "/api/backup", {}, "Backup saved")', context);
  assert.equal(elements.get('dock-status').textContent, expected);
  if (expected === 'Backup saved') assert.match(elements.get('dock-message').textContent, /Saved in: C:\/Backups and Logs/);
  assert.equal(posts, 1, 'recovery must never repeat the operation');
  if (reads !== undefined) assert.equal(readCount, reads);
  assert.equal(context.busy, false);
  return polls;
}
(async () => {
  await check({terminal: {type: 'done', message: 'Backup saved'}, expected: 'Backup saved', reads: 1});
  await check({terminal: {type: 'error', message: 'Read failed'}, expected: 'Not completed', reads: 1});
  const polls = await check({streamError: true, recover: [
    new Error('reset'), {running: true, result: null},
    {running: false, result: {type: 'done', message: 'Backup saved'}},
  ], expected: 'Backup saved'});
  assert.equal(polls, 3);
  await check({streamError: true, recover: [{running: false, result: {type: 'error', message: 'Read failed'}}], expected: 'Not completed'});
  assert.equal(await check({streamError: true, expected: 'Not completed'}), 5);
  console.log('Job stream recovery: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
