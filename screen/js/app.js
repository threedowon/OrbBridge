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
let gamePhase = 'READY';
let lastFinishRevision = -1;
let playbackRunId = 0;

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

/** Only one open screen tab may produce browser fallback audio. */
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
    // A direct start click is explicit ownership transfer from a stale tab.
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
    // Bundled default keeps the screen usable while the server starts.
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

/** Prepare visual state only; Python now owns all playback scheduling. */
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
  gameRunning = gamePhase === 'RUNNING';
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
    // A server that is still starting will be retried by the start request.
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
  if (!currentMelody || !gameRunning || !ownsAudio()) return;
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
  if (!currentMelody || !ownsAudio()) return;
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
    snapshot.phase,
    snapshot.finish_reason,
    snapshot.revision,
    announce,
  );
}

function applyGameState(phase, reason = null, revision = null, announce = true) {
  const wasRunning = gameRunning;
  gamePhase = phase || gamePhase;
  gameRunning = gamePhase === 'RUNNING';

  if (!gameRunning && wasRunning) engine.stop();

  if (
    announce
    && gamePhase === 'FINISHED'
    && reason === 'complete'
    && revision !== lastFinishRevision
    && wasRunning
  ) {
    lastFinishRevision = revision;
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
    // WebSocket reconnect will retry after the server becomes available.
  }
}

/** Ignore delayed UDP/WS events that belong to an older Python playback run. */
function beginPlayback(runId, mode) {
  const parsedRunId = Number(runId) || 0;
  if (parsedRunId < playbackRunId) return;
  playbackRunId = parsedRunId;
  engine.beginPlayback(mode);
}

/**
 * Web mode remains a development fallback. Timing still comes from Python:
 * this function plays notes immediately when their server beat arrives.
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

  const coherent = mode === 'dispensa';
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

function handleWsMessage(message) {
  const { address, args } = message;
  switch (address) {
    case '/orb/added':
      handleOrbAdded(args[0], args[1], args[2], args[5])
        .catch(error => console.warn(error));
      break;
    case '/orb/removed':
      handleOrbRemoved(args[0], args[1], args[4]);
      break;
    case '/game/end':
      applyGameState('FINISHED', args[0] || 'manual');
      break;
    case '/game/reset':
      applyGameState('RESETTING');
      break;
    case '/game/reset_complete':
      applyGameState('READY');
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
    // Legacy command messages remain visible to OSC consumers, but the screen
    // intentionally does not start its own timers for them after M3.
    case '/game/listen':
    case '/game/play':
    case '/game/dispensa':
      break;
  }
}

function connectWs() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    setConn(true);
    void syncGameState();
  };
  ws.onclose = () => {
    setConn(false);
    setTimeout(connectWs, 2000);
  };
  ws.onerror = () => setConn(false);
  ws.onmessage = event => {
    try {
      handleWsMessage(JSON.parse(event.data));
    } catch {
      // Ignore malformed bridge messages; the next state snapshot self-heals.
    }
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
    // Storage can be unavailable in private browsing mode.
  }
});

window.addEventListener('pagehide', () => {
  try {
    const body = JSON.stringify({ reason: 'screen_reload' });
    const blob = new Blob([body], { type: 'application/json' });
    if (navigator.sendBeacon) navigator.sendBeacon('/api/game/end', blob);
  } catch {
    // Page shutdown must never block navigation.
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
    // Initial state sync below will recover when the server is ready.
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
