const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'admin/hardware/index.html'), 'utf8');
  const dots = {};
  const badge = {setAttribute(name, value) {this[name] = value;}};
  const context = {
    setDot(id, state) {dots[id] = state;},
    document: {getElementById(id) {assert.equal(id, 'matrix-status'); return badge;}},
    renderStatus() {},
    fetch: async () => ({ok: false, status: 503}),
  };
  vm.createContext(context);
  vm.runInContext(html.slice(html.indexOf('    function renderMatrixStatus('),
    html.indexOf('    function renderStatus(')), context);
  vm.runInContext(html.slice(html.indexOf('    async function fetchStatus('),
    html.indexOf('    function renderMidiStatus(')), context);

  for (const [state, expected] of [['connected', 'ok'], ['disconnected', 'err'],
    ['connecting', 'warn'], ['waiting', 'warn'], ['disabled', 'warn']]) {
    context.renderMatrixStatus({state, connected: state === 'connected', port: 'COM6'});
    assert.equal(dots['dot-matrix'], expected);
    assert.match(badge.title, /COM6/);
    assert.equal(badge['aria-label'], badge.title);
  }
  context.renderMatrixStatus({state: 'disconnected', error: 'missing port'});
  assert.match(badge.title, /missing port/);
  context.renderMatrixStatus({state: 'connected', connected: true});
  await context.fetchStatus();
  assert.equal(dots['dot-matrix'], 'warn');
  assert.match(badge.title, /상태 확인 불가/);
  console.log('Matrix badge: connection colors, error tooltip, unavailable API pass');
})().catch(error => {console.error(error); process.exitCode = 1;});
