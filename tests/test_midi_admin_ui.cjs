const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'admin/software/index.html'), 'utf8');
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
  new vm.Script(match[1]);
}

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.value = '';
    this.disabled = false;
    this.textContent = '';
  }
  set innerHTML(value) { this.children = []; }
  appendChild(child) { this.children.push(child); }
  get options() {
    return this.children.flatMap(child => child.tag === 'option' ? [child] : child.options);
  }
}
const elements = new Map();
const buttons = [new Element('button'), new Element('button'), new Element('button')];
const logs = [];
const context = {
  lastMidiJobId: 0,
  setDot() {},
  addLog(...args) { logs.push(args); },
  document: {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement(tag) { return new Element(tag); },
    querySelectorAll(selector) {
      assert.equal(selector, '[data-midi-mode]');
      return buttons;
    },
  },
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('    function renderMidiStatus('),
  html.indexOf('    async function fetchMidiStatus(')), context);

const voices = Object.keys(JSON.parse(fs.readFileSync(path.join(root, 'data/voice_catalog.json'), 'utf8')).voices).sort();
const midi = {ready: true, opened_ports: ['test']};
context.renderMidiStatus({midi, voices, job: {id: 0, running: false}});
const select = elements.get('midi-voice');
assert.deepEqual(select.options.map(option => option.value), voices);
assert.equal(select.children.length, new Set(voices.map(voice => voice.replace(/_\d+$/, ''))).size);
select.value = 'riize_6';
const groups = [...select.children];
context.renderMidiStatus({midi, voices, job: {
  id: 1, mode: 'all_voices', running: true, completed: 9, total: voices.length * 8,
  current_voice: 'bts_1', current_pitch: 0, failed: 1,
}});
assert.equal(select.value, 'riize_6');
assert.deepEqual(select.children, groups);
assert.equal(select.disabled, true);
assert(buttons.every(button => button.disabled));
assert.equal(elements.get('btn-midi-stop').disabled, false);
assert.match(elements.get('midi-job').textContent, /전체 순회.*9\/\d+.*실패 1.*bts_1 \/ C/);

context.renderMidiStatus({midi, voices, job: {
  id: 1, mode: 'all_voices', running: false, completed: 9, total: voices.length * 8,
  stopped: true, failed: 1, results: [],
}});
assert.match(elements.get('midi-job').textContent, /^중지/);
assert.match(logs[0][1], /전체 순회 중지.*실패 1/);
assert.equal(elements.get('btn-midi-stop').disabled, true);
assert.equal(select.disabled, false);
assert(buttons.every(button => !button.disabled));

context.renderMidiStatus({midi, voices: [], job: {id: 0, running: false}});
assert.equal(select.options.length, 0);
assert(buttons.every(button => button.disabled));
assert.equal(select.disabled, true);

// Timer controls belong to the system tab and appear before its live status grid.
const systemStart = html.indexOf('<div class="system-section">');
const timerStart = html.indexOf('<form id="timer-settings"');
const statsStart = html.indexOf('<div class="stats-grid">', systemStart);
assert(systemStart < timerStart && timerStart < statsStart);
assert.equal((html.match(/id="timer-settings"/g) || []).length, 1);
assert(!/data-view="settings"/.test(html));
assert.equal((html.match(/role="tab" /g) || []).length, 3);
console.log(`MIDI UI: ${voices.length} voices, grouped selection, progress, stop/failure states, timer placement pass`);
