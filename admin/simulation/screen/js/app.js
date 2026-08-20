import { beatCount, setVoices } from './data.js';
import { initStaff } from './staff.js';
import { createEngine } from './engine.js';
import { initConfetti, burst } from './confetti.js';
import { loadActiveMelody, parseMelodyJson } from './melody-loader.js';
import { setVoiceIndex, tableToStaff, voiceToIndex } from './mapping.js';
import { showOrbPlaced } from './orb-toast.js';

const WS_URL = `ws://${location.hostname || '127.0.0.1'}:8765`;
const engine = createEngine();

let staffApi = null;
let currentMelody = null;
let ws = null;
let gameRunning = false;
let gameState = 'READY';
let lastCompleteRevision = -1;
let playbackRunId = 0;
let wsSnapshotReceived = false;
let playViewPrepared = false;
// snapshot 복구가 비동기여도 서버 전송 순서를 유지한다.
let wsMessageChain = Promise.resolve();

const views = {
  start: document.getElementById('view-start'),
  play: document.getElementById('view-play'),
};

function show(name) {
  Object.values(views).forEach(view => view.classList.remove('active'));
  views[name].classList.add('active');
}

function getBeats() {
  return currentMelody ? beatCount(currentMelody) : 0;
}

function prepareMelody(document) {
  currentMelody = parseMelodyJson(
    document,
    document?.songId ?? 'custom',
  );
  playViewPrepared = false;
  if (staffApi) {
    engine.load(currentMelody, 0);
    staffApi.resize();
    engine.redraw();
  }
}

/** 재생 시각은 Python이 관리하므로 화면 상태만 준비한다. */
function setupPlaySession() {
  playbackRunId = 0;
  engine.load(currentMelody, 0);
  staffApi?.setHint(false);

  if (!staffApi) {
    staffApi = initStaff(document.getElementById('staff-canvas'), null);
  } else {
    staffApi.resize();
  }
  engine.redraw();
}

async function loadVoiceCatalog() {
  try {
    const response = await fetch('/api/voices');
    if (!response.ok) return;
    const data = await response.json();
    if (data.voices?.length) setVoices(data.voices);
    if (data.index) setVoiceIndex(data.index);
  } catch (error) {
    console.warn('voice catalog unavailable, using defaults', error);
  }
}

async function syncViewToGameState() {
  const active = gameState === 'RUNNING' || gameState === 'RESETTING';
  if (!active || !currentMelody) {
    show('start');
    playViewPrepared = false;
    return;
  }

  await loadVoiceCatalog();
  if (!playViewPrepared) {
    setupPlaySession();
    playViewPrepared = true;
  }
  show('play');
}

async function handleOrbAdded(slave, cell, voice, orbId) {
  if (!currentMelody || !gameRunning) return;
  const voiceIndex = voiceToIndex(voice);
  if (voiceIndex < 0) {
    showOrbPlaced(voice || 'unknown');
    return;
  }
  try {
    const { col, row } = tableToStaff(slave, cell, currentMelody.scale);
    if (col >= getBeats()) return;
    engine.placeExternal(col, row, voiceIndex, orbId ?? null);
    await loadVoiceCatalog();
    engine.redraw();
    showOrbPlaced(voice);
  } catch (error) {
    console.warn('orb map failed', error);
  }
}

function handleOrbRemoved(slave, cell, orbId) {
  if (!currentMelody) return;
  try {
    const { col, row } = tableToStaff(slave, cell, currentMelody.scale);
    if (col >= getBeats()) return;
    engine.removeExternal(col, row, orbId ?? null);
  } catch (error) {
    console.warn('orb remove map failed', error);
  }
}

function applyGameSnapshot(snapshot, announce = true) {
  if (!snapshot) return;
  applyGameState(
    snapshot.state,
    snapshot.end_reason,
    snapshot.revision,
    announce,
  );
}

function applyGameState(state, reason = null, revision = null, announce = true) {
  const wasRunning = gameRunning;
  gameState = state || gameState;
  gameRunning = gameState === 'RUNNING';

  if (!gameRunning && wasRunning) engine.stop();

  if (
    announce
    && gameState === 'RESETTING'
    && reason === 'complete'
    && revision !== lastCompleteRevision
    && wasRunning
  ) {
    lastCompleteRevision = revision;
    burst();
  }
}

async function syncGameState() {
  try {
    const response = await fetch('/api/game/state');
    if (!response.ok) return;
    applyGameSnapshot(await response.json(), false);
    await syncViewToGameState();
  } catch {
    // 서버가 준비되면 WebSocket 재연결에서 다시 동기화한다.
  }
}

/** WebSocket 연결 직후 받은 snapshot으로 화면 상태를 복구한다. */
async function applyWebSocketSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return;

  if (snapshot.melody) prepareMelody(snapshot.melody);
  const game = snapshot.game;
  if (game) applyGameSnapshot(game, false);
  await syncViewToGameState();
  if (!currentMelody || !staffApi || gameState === 'READY') return;

  await loadVoiceCatalog();
  engine.load(currentMelody, 0);

  const cells = snapshot.cells || {};
  for (const [cellKey, info] of Object.entries(cells)) {
    if (!info?.id || info.synthetic) continue;
    const [slave, cell] = cellKey.split(':').map(Number);
    const voiceIndex = voiceToIndex(info.voice);
    if (!Number.isInteger(slave) || !Number.isInteger(cell) || voiceIndex < 0) {
      continue;
    }
    try {
      const { col, row } = tableToStaff(slave, cell, currentMelody.scale);
      if (col >= getBeats()) continue;
      engine.placeExternal(col, row, voiceIndex, info.id);
    } catch {
      // 잘못된 셀 하나가 나머지 snapshot 복구를 막지 않게 한다.
    }
  }

  const playback = snapshot.status?.playback || snapshot.playback;
  if (playback?.active) {
    beginPlayback(playback.run_id, playback.mode);
  }
  engine.redraw();
}

/** 이전 Python 재생에 속한 지연 실시간 이벤트를 무시한다. */
function beginPlayback(runId, mode) {
  const parsedRunId = Number(runId) || 0;
  if (parsedRunId < playbackRunId) return;
  playbackRunId = parsedRunId;
  engine.beginPlayback(mode);
}

function showPlaybackBeat(runId, mode, step) {
  const parsedRunId = Number(runId) || 0;
  if (parsedRunId < playbackRunId) return;
  if (parsedRunId > playbackRunId) beginPlayback(parsedRunId, mode);
  engine.showPlaybackBeat(mode, Number(step));
}

function endPlayback(runId, mode) {
  if ((Number(runId) || 0) !== playbackRunId) return;
  engine.endPlayback(mode);
}

async function handleWsMessage(message) {
  if (message?.type === 'snapshot') {
    wsSnapshotReceived = true;
    await applyWebSocketSnapshot(message.snapshot);
    return;
  }

  const { address } = message;
  const args = Array.isArray(message?.args) ? message.args : [];
  switch (address) {
    case '/orb/added':
      await handleOrbAdded(args[0], args[1], args[2], args[5]);
      break;
    case '/orb/removed':
      handleOrbRemoved(args[0], args[1], args[4]);
      break;
    case '/game/state':
      applyGameState(args[0], args[1] || null, args[2] ?? null);
      await syncViewToGameState();
      break;
    case '/game/melody':
      if (args[0]) {
        prepareMelody(args[0]);
        await syncViewToGameState();
      }
      break;
    case '/game/playback/start':
      beginPlayback(args[0], args[1]);
      break;
    case '/game/playback/beat':
      showPlaybackBeat(args[0], args[1], args[2]);
      break;
    case '/game/playback/end':
      endPlayback(args[0], args[1]);
      break;
  }
}

function connectWs() {
  ws = new WebSocket(WS_URL);
  wsSnapshotReceived = false;
  ws.onopen = () => {
    // 서버가 즉시 전체 snapshot을 보낸다. 시작 경합에 대비해 지연 HTTP
    // fallback 하나만 유지한다.
    setTimeout(() => {
      if (!wsSnapshotReceived && ws?.readyState === WebSocket.OPEN) {
        void syncGameState();
      }
    }, 750);
  };
  ws.onclose = () => {
    setTimeout(connectWs, 2000);
  };
  ws.onmessage = event => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      // 잘못된 메시지는 무시하고 다음 연결 snapshot에서 복구한다.
      return;
    }
    // snapshot을 먼저 처리하고 이후 실시간 이벤트가 비동기 복구를
    // 추월하지 않도록 브라우저 작업을 직렬화한다.
    wsMessageChain = wsMessageChain
      .then(() => handleWsMessage(message))
      .catch(error => console.warn('WebSocket message failed', error));
  };
}

initConfetti();
connectWs();

(async () => {
  await loadVoiceCatalog();
  try {
    currentMelody = await loadActiveMelody();
  } catch (error) {
    console.error(error);
    document.getElementById('start-status').textContent = '멜로디 로드 실패';
  }
  await syncGameState();
  await syncViewToGameState();
})();
