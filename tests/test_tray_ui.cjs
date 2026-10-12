const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  for (const page of ['software', 'hardware']) {
    const html = fs.readFileSync(path.join(__dirname, '..', 'admin', page, 'index.html'), 'utf8');
    for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
      new vm.Script(match[1]);
    }
    const elements = new Map();
    const handlers = {};
    const calls = [];
    const context = {
      currentGameState: 'READY', currentCellCount: 0, adminActionRunning: false,
      currentTray: {present: false, seated: false}, traySimulation: true,
      document: {getElementById(id) {
        if (!elements.has(id)) elements.set(id, {
          disabled: false, attrs: {}, classList: {toggle() {}},
          setAttribute(k, v) {this.attrs[k] = v;},
          addEventListener(event, callback) {handlers[id + ':' + event] = callback;}
        });
        return elements.get(id);
      }},
      addLog() {}, fetchStatus() {},
      async postGame(path, body) {
        calls.push({path, body});
        return {game: {state: body.present ? 'RUNNING' : 'READY', cell_count: 0,
          tray: {present: body.present, seated: body.present}}};
      }
    };
    vm.createContext(context);
    const buttons = html.slice(html.indexOf('    function updateGameButtons()'),
      html.indexOf('    function setDot('));
    vm.runInContext(buttons, context);
    const start = html.indexOf("    document.getElementById('btn-game-tray').addEventListener");
    const end = html.indexOf("    document.querySelectorAll('[data-game-action]')", start);
    vm.runInContext(html.slice(start, end), context);
    context.updateGameButtons();
    const button = elements.get('btn-game-tray');
    assert.equal(button.textContent, 'TRAY OFF');
    assert.equal(button.attrs['aria-pressed'], 'false');
    await handlers['btn-game-tray:click']();
    assert.equal(calls[0].path, '/api/game/tray');
    assert.equal(calls[0].body.present, true);
    assert.equal(button.textContent, 'TRAY ON');
    assert.equal(button.attrs['aria-pressed'], 'true');
    await handlers['btn-game-tray:click']();
    assert.equal(calls[1].body.present, false);
    assert.equal(button.textContent, 'TRAY OFF');
    context.traySimulation = false;
    context.currentTray = {present: true, seated: false};
    context.updateGameButtons();
    assert.equal(button.textContent, 'TRAY PARTIAL');
    assert.equal(button.disabled, true);
    await handlers['btn-game-tray:click']();
    assert.equal(calls.length, 2);
    context.currentTray = {present: null, seated: null};
    context.updateGameButtons();
    assert.equal(button.attrs['aria-pressed'], 'mixed');
    context.traySimulation = true;
    context.adminActionRunning = true;
    await handlers['btn-game-tray:click']();
    assert.equal(calls.length, 2);
    console.log(page + ': scripts parse; tray toggle, labels, busy and hardware guards pass');
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
