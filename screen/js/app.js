import { beatCount, setVoices } from './data.js';
import { unlock, playSuccess } from './audio.js';
import { initStaff } from './staff.js';
import { createEngine } from './engine.js';
import { initConfetti, burst } from './confetti.js';
import { loadMelodyFile } from './melody-loader.js';
import { setVoiceIndex, tableToStaff, voiceToIndex } from './mapping.js';

const WS_URL = `ws://${location.hostname || '127.0.0.1'}:8765`;
const engine = createEngine();

let staffApi = null;
let currentMelody = null;
let ws = null;
let gameRunning = false;
let pendingBegin = false;

const views = {
  start: document.getElementById('view-start'),
  play: document.getElementById('view-play'),
};

function show(name) {
  Object.values(views).forEach(v => v.classList.remove('active'));
  views[name].classList.add('active');
}

function getBeats() {
  return currentMelody ? beatCount(currentMelody) : 0;
}

const connEl = document.getElementById('conn-dot');

function setConn(on) {
  const label = on ? 'Connected' : 'Disconnected';
  if (connEl) {
    connEl.className = 'conn-dot' + (on ? ' on' : '');
    connEl.title = label;
    connEl.setAttribute('aria-label', label);
  }
}

async function prepareMelody(melodyId) {
  currentMelody = await loadMelodyFile(melodyId);
}

async function fetchActiveMelodyId() {
  try {
    const res = await fetch('/screen/melodies/config.json');
    if (res.ok) {
      const cfg = await res.json();
      return cfg.active ?? 'biplane';
    }
  } catch { /* fallback */ }
  return 'biplane';
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function setupPlaySession() {
  gameRunning = false;
  pendingBegin = false;
  engine.load(currentMelody, 0, { autoStart: false });
  staffApi?.setHint(false);

  if (!staffApi) {
    staffApi = initStaff(document.getElementById('staff-canvas'), null);
  } else {
    staffApi.resize();
  }
  engine.redraw();
}

async function startGame() {
  if (gameRunning) return;
  unlock();
  await apiPost('/api/game/start');
  gameRunning = true;
  pendingBegin = true;
  engine.listen();
}

async function loadVoiceCatalog() {
  try {
    const res = await fetch('/api/voices');
    if (!res.ok) return;
    const data = await res.json();
    if (data.voices?.length) setVoices(data.voices);
    if (data.index) setVoiceIndex(data.index);
  } catch (e) {
    console.warn('voice catalog unavailable, using defaults', e);
  }
}

async function enterPlayView() {
  if (!currentMelody) return;
  unlock();
  await loadVoiceCatalog();
  try { await apiPost('/api/game/end', { reason: 'load' }); } catch { /* server idle */ }
  setupPlaySession();
  show('play');
  try {
    await startGame();
  } catch (e) {
    console.error('시작 실패:', e.message);
    show('start');
  }
}

function playListen() {
  unlock();
  engine.listen();
}

function playBeatLoop() {
  if (!gameRunning || !currentMelody) return;
  unlock();
  engine.playLoop();
}

function handleOrbAdded(slave, cell, voice, orbId) {
  if (!currentMelody || !gameRunning) return;
  try {
    const { col, row } = tableToStaff(slave, cell, currentMelody.scale);
    if (col >= getBeats()) return;
    engine.placeExternal(col, row, voiceToIndex(voice), orbId ?? null);
  } catch (err) {
    console.warn('orb map failed', err);
  }
}

function handleOrbRemoved(slave, cell, orbId) {
  if (!currentMelody || !gameRunning) return;
  try {
    const { col, row } = tableToStaff(slave, cell, currentMelody.scale);
    if (col >= getBeats()) return;
    engine.removeExternal(col, row, orbId ?? null);
  } catch (err) {
    console.warn('orb remove map failed', err);
  }
}

function handleWsMessage(msg) {
  const { address, args } = msg;
  switch (address) {
    case '/orb/added':
      handleOrbAdded(args[0], args[1], args[2], args[5]);
      break;
    case '/orb/removed':
      handleOrbRemoved(args[0], args[1], args[4]);
      break;
    case '/game/end':
      if (gameRunning) {
        gameRunning = false;
        engine.stop();
      }
      break;
    case '/game/listen':
      playListen();
      break;
    case '/game/play':
      playBeatLoop();
      break;
  }
}

function connectWs() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connectWs, 2000); };
  ws.onerror = () => setConn(false);
  ws.onmessage = (e) => {
    try { handleWsMessage(JSON.parse(e.data)); } catch { /* ignore */ }
  };
}

engine.on('previewEnd', () => {
  if (pendingBegin) pendingBegin = false;
});

engine.on('complete', async () => {
  gameRunning = false;
  try { await apiPost('/api/game/end', { reason: 'complete' }); } catch (e) { console.error(e); }
  burst();
  playSuccess();
});

document.getElementById('btn-enter').addEventListener('click', () => enterPlayView());

document.body.addEventListener('click', () => unlock(), { once: true });

async function loadVoiceCatalog() {
  try {
    const res = await fetch('/api/voices');
    if (!res.ok) return;
    const data = await res.json();
    if (data.voices?.length) setVoices(data.voices);
    if (data.index) setVoiceIndex(data.index);
  } catch (e) {
    console.warn('voice catalog unavailable, using defaults', e);
  }
}

initConfetti();
connectWs();

(async () => {
  await loadVoiceCatalog();
  try {
    const id = await fetchActiveMelodyId();
    await prepareMelody(id);
  } catch (err) {
    console.error(err);
    document.getElementById('btn-enter').disabled = true;
  }
  show('start');
})();
