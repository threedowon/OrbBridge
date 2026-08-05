import { NOTES, beatCount, setVoices } from './data.js';
import {
  isMidiMode,
  playSuccess,
  playTone,
  setAudioOwnerCheck,
  setAudioOutputMode,
  unlock,
} from './audio.js';
import { initStaff } from './staff.js';
import { createEngine } from './engine.js';
import { initConfetti, burst } from './confetti.js';
import { loadMelodyFile } from './melody-loader.js';
import { setVoiceIndex, tableToStaff, voiceToIndex } from './mapping.js';
import { setVoiceLabels } from './voice-labels.js';
import { showOrbPlaced } from './orb-toast.js';

const WS_URL = `ws://${location.hostname || '127.0.0.1'}:8765`;
const AUDIO_OWNER_KEY = 'orbbridge-audio-owner';
const tabId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
const engine = createEngine();

let staffApi = null;
let currentMelody = null;
let ws = null;
let gameRunning = false;
let gameState = 'READY';
let lastCompleteRevision = -1;
let playbackRunId = 0;
let wsSnapshotReceived = false;
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

const connEl = document.getElementById('conn-dot');

function setConn(on) {
  const label = on ? 'Connected' : 'Disconnected';
  if (!connEl) return;
  connEl.className = 'conn-dot' + (on ? ' on' : '');
  connEl.title = label;
  connEl.setAttribute('aria-label', label);
}

/** 열려 있는 screen 탭 중 하나만 브라우저 fallback 오디오를 출력한다. */
function ownsAudio() {
  try {
    return localStorage.getItem(AUDIO_OWNER_KEY) === tabId;
  } catch {
    return true;
  }
}

function claimAudio(force = false) {
  try {
    const owner = localStorage.getItem(AUDIO_OWNER_KEY);
    // 직접 누른 start는 오래된 탭에서 오디오 소유권을 넘겨받는 입력이다.
    if (force || !owner || owner === tabId) {
      localStorage.setItem(AUDIO_OWNER_KEY, tabId);
      return true;
    }
    return false;
  } catch {
    return true;
  }
}

async function prepareMelody(melodyId) {
  currentMelody = await loadMelodyFile(melodyId);
}

async function fetchActiveMelodyId() {
  try {
    const response = await fetch('/screen/melodies/config.json');
    if (response.ok) {
      const config = await response.json();
      return config.active ?? 'biplane';
    }
  } catch {
    // 서버 시작 중에도 screen을 열 수 있도록 번들 기본값을 사용한다.
  }
  return 'biplane';
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

/** 재생 시각은 Python이 관리하므로 화면 상태만 준비한다. */
function setupPlaySession() {
  gameRunning = false;
  playbackRunId = 0;
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
  const result = await apiPost('/api/game/start');
  if (result.game) applyGameSnapshot(result.game, false);
  gameRunning = gameState === 'RUNNING';
}

async function loadVoiceCatalog() {
  try {
    const response = await fetch('/api/voices');
    if (!response.ok) return;
    const data = await response.json();
    if (data.voices?.length) setVoices(data.voices);
    if (data.voices) setVoiceLabels(data.voices);
    if (data.index) setVoiceIndex(data.index);
  } catch (error) {
    console.warn('voice catalog unavailable, using defaults', error);
  }
}

async function loadAudioOutputMode() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) return;
    const data = await response.json();
    setAudioOutputMode(data.audio_output === 'midi' ? 'midi' : 'web');
    console.info('[audio] output =', data.audio_output || 'web');
  } catch {
    setAudioOutputMode('web');
  }
}

async function enterPlayView() {
  if (!currentMelody) return;
  unlock();
  claimAudio(true);
  await loadAudioOutputMode();
  await loadVoiceCatalog();
  try {
    await apiPost('/api/game/reset');
  } catch {
    // 서버가 시작 중이면 이어지는 start 요청에서 다시 확인한다.
  }
  setupPlaySession();
  show('play');
  try {
    await startGame();
  } catch (error) {
    console.error('시작 실패:', error.message);
    show('start');
  }
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
    playSuccess();
  }
}

async function syncGameState() {
  try {
    const response = await fetch('/api/game/state');
    if (!response.ok) return;
    applyGameSnapshot(await response.json(), false);
  } catch {
    // 서버가 준비되면 WebSocket 재연결에서 다시 동기화한다.
  }
}

/** WebSocket 연결 직후 받은 snapshot으로 화면 상태를 복구한다. */
async function applyWebSocketSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return;

  const game = snapshot.game;
  if (game) applyGameSnapshot(game, false);
  if (!currentMelody || !staffApi) return;

  await loadVoiceCatalog();
  engine.load(currentMelody, 0, { autoStart: false });

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
      engine.placeExternal(col, row, voiceIndex, info.id, { silent: true });
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

/**
 * web 모드는 개발용 fallback이다. 시각 기준은 Python에 있으며,
 * 서버 beat가 도착하면 해당 음표를 즉시 재생한다.
 */
function playWebBeat(mode, durationMs, encodedNotes) {
  if (isMidiMode() || !ownsAudio()) return;
  let notes;
  try {
    notes = JSON.parse(encodedNotes || '[]');
  } catch {
    return;
  }
  if (!Array.isArray(notes) || !notes.length) return;

  const coherent = mode === 'dfesta';
  for (const item of notes) {
    const pitch = Math.max(0, Math.min(7, Number(item.pitch) || 0));
    const note = NOTES[pitch];
    const voiceIndex = mode === 'listen' ? 0 : voiceToIndex(item.voice);
    if (!note || voiceIndex < 0) continue;
    playTone(note.freq, voiceIndex, Math.max(0.04, durationMs / 1000), 0, {
      preview: mode === 'listen',
      noteName: note.name,
      polyphony: notes.length,
      coherent,
      volume: coherent ? 0.72 : 0.88,
    });
  }
}

function showPlaybackBeat(runId, mode, step, durationMs, encodedNotes) {
  const parsedRunId = Number(runId) || 0;
  if (parsedRunId < playbackRunId) return;
  if (parsedRunId > playbackRunId) beginPlayback(parsedRunId, mode);
  engine.showPlaybackBeat(mode, Number(step));
  playWebBeat(mode, Number(durationMs) || 40, encodedNotes);
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
      break;
    case '/game/playback/start':
      beginPlayback(args[0], args[1]);
      break;
    case '/game/playback/beat':
      showPlaybackBeat(args[0], args[1], args[2], args[3], args[4]);
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
    setConn(true);
    // 서버가 즉시 전체 snapshot을 보낸다. 시작 경합에 대비해 지연 HTTP
    // fallback 하나만 유지한다.
    setTimeout(() => {
      if (!wsSnapshotReceived && ws?.readyState === WebSocket.OPEN) {
        void syncGameState();
      }
    }, 750);
  };
  ws.onclose = () => {
    setConn(false);
    setTimeout(connectWs, 2000);
  };
  ws.onerror = () => setConn(false);
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

document.getElementById('btn-enter').addEventListener('click', () => enterPlayView());

document.body.addEventListener('click', () => {
  unlock();
  claimAudio();
}, { once: true });

window.addEventListener('storage', event => {
  if (event.key === AUDIO_OWNER_KEY && event.newValue !== tabId) {
    engine.stop();
  }
});

window.addEventListener('beforeunload', () => {
  if (!ownsAudio()) return;
  try {
    localStorage.removeItem(AUDIO_OWNER_KEY);
  } catch {
    // 비공개 탐색에서는 Storage를 사용할 수 없을 수 있다.
  }
});

window.addEventListener('pagehide', () => {
  try {
    const body = JSON.stringify({});
    const blob = new Blob([body], { type: 'application/json' });
    if (navigator.sendBeacon) navigator.sendBeacon('/api/game/end', blob);
  } catch {
    // 페이지 종료 처리가 화면 이동을 막지 않게 한다.
  }
});

setAudioOwnerCheck(() => ownsAudio());
claimAudio();
initConfetti();
connectWs();

(async () => {
  try {
    await apiPost('/api/game/reset');
  } catch {
    // 서버가 준비되면 아래 초기 상태 동기화에서 복구한다.
  }
  gameRunning = false;

  await loadVoiceCatalog();
  await loadAudioOutputMode();
  try {
    const melodyId = await fetchActiveMelodyId();
    await prepareMelody(melodyId);
  } catch (error) {
    console.error(error);
    document.getElementById('btn-enter').disabled = true;
  }
  show('start');
})();
