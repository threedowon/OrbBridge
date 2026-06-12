import { beatCount, VOICES } from './data.js';
import { unlock, playSuccess } from './audio.js';
import { initStaff } from './staff.js';
import { createEngine } from './engine.js';
import { initConfetti, burst } from './confetti.js';
import { loadMelodyFile } from './melody-loader.js';
import { tableToStaff, voiceToIndex } from './mapping.js';

const WS_URL = `ws://${location.hostname || '127.0.0.1'}:8765`;
const engine = createEngine();

let staffApi = null;
let currentMelody = null;
let ws = null;
let gameRunning = false;
let pendingBegin = false;

const views = {
  play: document.getElementById('view-play'),
  done: document.getElementById('view-done'),
};

function show(name) {
  Object.values(views).forEach(v => v.classList.remove('active'));
  views[name].classList.add('active');
}

function getBeats() {
  return currentMelody ? beatCount(currentMelody) : 0;
}

function buildBeatTrack() {
  const track = document.getElementById('beat-track');
  track.innerHTML = '';
  for (let i = 0; i < getBeats(); i++) {
    const d = document.createElement('div');
    d.className = 'beat-dot';
    track.appendChild(d);
  }
}

function updateBeatTrack(beat) {
  document.querySelectorAll('.beat-dot').forEach((d, i) => {
    d.classList.toggle('current', i === beat);
    d.classList.toggle('locked', engine.locked[i]);
  });
}

function buildVoices() {
  const wrap = document.getElementById('voice-dots');
  wrap.innerHTML = '';
  VOICES.forEach((v, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'voice-dot' + (i === 0 ? ' selected' : '');
    btn.style.background = v.color;
    btn.title = v.name;
    btn.textContent = v.name[0];
    btn.addEventListener('click', () => {
      unlock();
      engine.setVoice(i);
      wrap.querySelectorAll('.voice-dot').forEach(d => d.classList.remove('selected'));
      btn.classList.add('selected');
    });
    wrap.appendChild(btn);
  });
}

const statusEl = document.getElementById('status-line');
const phaseEl = document.getElementById('phase-pill');
const connEl = document.getElementById('conn');

function setPhase(text, cls) {
  phaseEl.textContent = text;
  phaseEl.className = 'phase-pill' + (cls ? ' ' + cls : '');
}

function setStatus(text, cls = '') {
  statusEl.textContent = text;
  statusEl.className = 'status-line' + (cls ? ' ' + cls : '');
}

function setConn(on) {
  connEl.textContent = on ? 'Connected' : 'Disconnected';
  connEl.className = 'badge ' + (on ? 'on' : 'off');
}

function updateScore() {
  document.getElementById('score-current').textContent = engine.locked.filter(Boolean).length;
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

async function loadGame(melodyId) {
  unlock();
  try { await apiPost('/api/game/end', { reason: 'load' }); } catch { /* server idle */ }
  gameRunning = false;
  pendingBegin = false;
  currentMelody = await loadMelodyFile(melodyId);

  document.getElementById('play-title').textContent = currentMelody.title;
  document.getElementById('play-artist').textContent = currentMelody.artist ?? '';
  document.getElementById('play-bpm').textContent = `${currentMelody.bpm} BPM`;
  document.getElementById('score-total').textContent = getBeats();

  show('play');
  gameRunning = false;

  engine.load(currentMelody, 0, { autoStart: false });
  buildBeatTrack();
  updateScore();
  setPhase('대기', '');
  setStatus('▶ 시작을 누르면 테이블에서 오브를 올릴 수 있습니다');

  if (!staffApi) {
    staffApi = initStaff(document.getElementById('staff-canvas'), null);
  } else {
    staffApi.resize();
  }
  engine.redraw();
}

function playListen() {
  unlock();
  engine.listen();
  setPhase('듣기', 'active');
  setStatus('목표 멜로디 재생 중…', 'live');
}

function playBeatLoop() {
  if (!gameRunning || !currentMelody) return;
  unlock();
  engine.playLoop();
  setPhase('진행 중', 'active');
  setStatus('비트 진행 중 — 올려둔 orb 소리 재생', 'live');
}

function handleOrbAdded(slave, cell, voice) {
  if (!currentMelody || !gameRunning) return;
  try {
    const { col, row, noteName } = tableToStaff(slave, cell, currentMelody.scale);
    if (col >= getBeats()) return;
    engine.placeExternal(col, row, voiceToIndex(voice));
    setStatus(`테이블 Q${col + 1} [${noteName}] → 비트 ${col + 1}`, '');
  } catch (err) {
    console.warn('orb map failed', err);
  }
}

function handleOrbRemoved(slave, cell) {
  if (!currentMelody || !gameRunning) return;
  try {
    const { col, row, noteName } = tableToStaff(slave, cell, currentMelody.scale);
    if (col >= getBeats()) return;
    engine.removeExternal(col, row);
    setStatus(`제거 Q${col + 1} [${noteName}]`, '');
  } catch (err) {
    console.warn('orb remove map failed', err);
  }
}

function handleWsMessage(msg) {
  const { address, args } = msg;
  switch (address) {
    case '/orb/added':
      handleOrbAdded(args[0], args[1], args[2]);
      break;
    case '/orb/removed':
      handleOrbRemoved(args[0], args[1]);
      break;
    case '/game/end':
      if (gameRunning) {
        gameRunning = false;
        engine.stop();
        setPhase('종료', '');
        setStatus('게임이 종료되었습니다', 'warn');
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

engine.on('previewBeat', (i) => {
  document.querySelectorAll('.beat-dot').forEach((d, j) => d.classList.toggle('current', j === i));
});
engine.on('previewEnd', () => {
  document.querySelectorAll('.beat-dot').forEach(d => d.classList.remove('current'));
  if (pendingBegin) {
    pendingBegin = false;
    setPhase('준비', 'active');
    setStatus('orb를 배치한 뒤 테이블에서 재생을 누르세요', 'live');
  } else if (gameRunning && (engine.phase === 'running' || engine.phase === 'finishing')) {
    setPhase('진행 중', 'active');
    setStatus('비트 진행 중 — 올려둔 orb 소리 재생', 'live');
  } else if (gameRunning) {
    setPhase('준비', 'active');
    setStatus('orb를 배치한 뒤 테이블에서 재생을 누르세요', 'live');
  } else {
    setStatus('▶ 시작을 누르면 테이블에서 오브를 올릴 수 있습니다');
  }
});
engine.on('playOnceEnd', () => {
  updateBeatTrack(-1);
  setPhase('준비', 'active');
  setStatus('재생 완료 — orb를 조정하고 다시 재생하세요', 'live');
});
engine.on('beat', updateBeatTrack);
engine.on('lock', () => { updateScore(); updateBeatTrack(engine.beat ?? -1); });
engine.on('loop', updateScore);

engine.on('tick', ({ col, correct, hasAny, locked }) => {
  if (engine.phase === 'finishing') {
    setStatus('완성된 멜로디 재생 중…', 'live');
    setPhase('재생', 'done');
    return;
  }
  if (correct && locked) setStatus(`비트 ${col + 1} — 정답 확정`, 'ok');
  else if (!hasAny) setStatus(`비트 ${col + 1} — 테이블에 오브를 올려보세요`, '');
  else if (!correct) setStatus(`비트 ${col + 1} — 위치가 달라요`, 'warn');
  else setStatus(`비트 ${col + 1} — 정답!`, 'ok');
  setPhase('진행 중', 'active');
});

engine.on('phase', (p) => {
  if (p === 'running') setPhase('진행 중', 'active');
  if (p === 'finishing') { setPhase('재생', 'done'); setStatus('모든 음 확정! 멜로디를 감상하세요', 'ok'); }
  if (p === 'idle' && !gameRunning) { setPhase('대기', ''); }
});

engine.on('complete', async ({ loops, time }) => {
  gameRunning = false;
  try { await apiPost('/api/game/end', { reason: 'complete' }); } catch (e) { console.error(e); }

  document.getElementById('done-title').textContent = '멜로디 완성!';
  const who = currentMelody.artist
    ? `${currentMelody.title} — ${currentMelody.artist}`
    : currentMelody.title;
  document.getElementById('done-sub').textContent = `${who} · ${loops}루프 · ${time}초`;
  document.getElementById('stat-time').textContent = time;
  document.getElementById('stat-loops').textContent = loops;
  show('done');
  burst();
  playSuccess();
});

document.getElementById('btn-start').addEventListener('click', async () => {
  if (gameRunning) return;
  unlock();
  try {
    await apiPost('/api/game/start');
    gameRunning = true;
    pendingBegin = true;
    engine.listen();
    setPhase('듣기', 'active');
    setStatus('목표 멜로디 재생 중…', 'live');
  } catch (e) {
    setStatus(`시작 실패: ${e.message}`, 'warn');
  }
});

document.getElementById('btn-hint').addEventListener('click', () => {
  const on = engine.toggleHint();
  staffApi?.setHint(on);
  document.getElementById('btn-hint').textContent = on ? '힌트 끄기' : '힌트';
});

document.getElementById('btn-reset').addEventListener('click', async () => {
  if (gameRunning) {
    try { await apiPost('/api/game/end', { reason: 'reset' }); } catch { /* */ }
  }
  gameRunning = false;
  pendingBegin = false;
  engine.reset();
  buildBeatTrack();
  updateScore();
  staffApi?.setHint(false);
  document.getElementById('btn-hint').textContent = '힌트';
  setPhase('대기', '');
  setStatus('초기화됐습니다. 다시 시작하세요');
});

document.getElementById('btn-restart').addEventListener('click', () => loadGame(currentMelody?.songId ?? 'biplane'));

document.body.addEventListener('click', () => unlock(), { once: true });

buildVoices();
initConfetti();
connectWs();

loadGame('biplane').catch(err => {
  console.error(err);
  setStatus(err.message, 'warn');
});
