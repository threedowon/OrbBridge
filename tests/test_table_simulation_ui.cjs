const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'admin/simulation/table/index.html'), 'utf8');
  for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
  const catalog = Array.from({length: 12}, (_, i) => ({
    id: `__simulation__:aespa_1:${i}`, voice: 'aespa_1', simulated: true,
  }));
  const calls = [];
  const element = () => ({
    children: [], handlers: {}, classList: {add() {}, toggle() {}},
    set innerHTML(_) {this.children = [];},
    appendChild(child) {this.children.push(child);},
    addEventListener(name, handler) {this.handlers[name] = handler;},
  });
  let failRequest = false;
  const context = {
    API: '', MIME: 'application/x-orbbridge-orb', TRAY_ORB_COUNT: 12,
    running: true, allOrbs: [], orbs: [], placed: new Map(), dragPayload: null,
    orbTrayEl: element(), document: {createElement: element},
    buildGroupColors() {}, updateOrbTrayState() {}, renderCell() {}, refreshDFestaState() {},
    orbColorForVoice: () => '#f472b6', setDragging() {}, setEmptyDragImage() {},
    async fetch(url, options) {
      if (url === '/api/simulation/orbs') return {ok: true, json: async () => catalog};
      assert.equal(url, '/api/simulation/event');
      const event = JSON.parse(options.body);
      calls.push(event);
      return {ok: !failRequest, text: async () => 'request rejected'};
    },
  };
  vm.createContext(context);
  for (const name of ['shufflePick', 'pickTrayOrbs', 'loadOrbs', 'buildOrbTray', 'setDragData',
    'colToSlave', 'colToQ', 'posToCell', 'key', 'findCellWithOrbId', 'sendEvent',
    'removeAt', 'placeOrb', 'moveOrb', 'handleCellDrop']) {
    const match = html.match(new RegExp(`    (?:async )?function ${name}\\([^]*?\\n    }`));
    assert.ok(match, name);
    vm.runInContext(match[0], context);
  }
  await context.loadOrbs();
  const buttons = context.orbTrayEl.children.map(slot => slot.children[0]);
  assert.equal(buttons.length, 12);
  assert.ok(buttons.every(button => button.draggable && button.title.includes('가상 오브')));
  buttons[0].handlers.dragstart({dataTransfer: {setData() {}}, preventDefault() {assert.fail('drag disabled');}});
  assert.equal(context.dragPayload.src, 'tray');
  await context.handleCellDrop(0, 1, context.dragPayload);
  assert.equal(context.placed.size, 1);
  const orbId = context.placed.get('0-1').id;
  assert.deepEqual(calls[0], {type: 'added', slave: 1, cell: 1, id: orbId});
  await context.handleCellDrop(3, 2, {src: 'cell', col: 0, row: 1});
  assert.equal(context.placed.has('0-1'), false);
  assert.equal(context.placed.get('3-2').id, orbId);
  assert.deepEqual(calls.slice(1), [
    {type: 'removed', slave: 1, cell: 1, id: orbId},
    {type: 'added', slave: 2, cell: 10, id: orbId},
  ]);
  await context.removeAt(3, 2);
  assert.equal(context.placed.size, 0);
  failRequest = true;
  await assert.rejects(context.placeOrb(0, 0, 1), /request rejected/);
  assert.equal(context.placed.size, 0);
  context.running = false;
  let prevented = false;
  buttons[0].handlers.dragstart({preventDefault() {prevented = true;}});
  assert.ok(prevented);
  console.log('table simulation: catalog, drag, move, remove, rejection and inactive guard pass');
})().catch(error => {console.error(error); process.exitCode = 1;});
