import { NOTES, VOICES } from './data.js';

const SAMPLE_BASE_VOL = 0.88;
const NOTE_PITCH = {
  C4: 0, D4: 1, E4: 2, F4: 3, G4: 4, A4: 5, B4: 6, C5: 7,
};

/** 'web' | 'midi' — from GET /api/status */
let audioOutputMode = 'web';

let ctx = null;
let previewOut = null;
/** Shared bus: samples → gain → compressor → limiter → speakers. */
let masterGain = null;
let masterComp = null;
let masterLimiter = null;
/** @type {OscillatorNode[]} */
const activePreviewOscs = [];
/** @type {Set<HTMLAudioElement>} */
const orbSamples = new Set();
/** @type {Set<HTMLAudioElement>} */
const activeSamples = new Set();
let audioOwnerCheck = () => true;

export function setAudioOwnerCheck(fn) {
  audioOwnerCheck = typeof fn === 'function' ? fn : () => true;
}

export function setAudioOutputMode(mode) {
  audioOutputMode = mode === 'midi' ? 'midi' : 'web';
}

export function isMidiMode() {
  return audioOutputMode === 'midi';
}

export function unlock() {
  if (isMidiMode()) return null;
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === 'suspended') ctx.resume();
  ensureMasterBus(ctx);
  return ctx;
}

/**
 * Per-voice gain when N sounds play together.
 * - incoherent (different pitches): 1/√N keeps perceived loudness roughly flat
 * - coherent (same pitch, e.g. dispensa): closer to 1/N — same note stacks add up
 */
export function mixVolume(polyphony, base = SAMPLE_BASE_VOL, { coherent = false } = {}) {
  const n = Math.max(1, polyphony | 0);
  if (n <= 1) return Math.min(1, base);
  const denom = coherent ? Math.pow(n, 0.92) : Math.sqrt(n);
  return Math.min(1, Math.max(0.08, base / denom));
}

function ensureMasterBus(c) {
  if (masterLimiter) return;
  masterGain = c.createGain();
  masterGain.gain.value = 1;

  masterComp = c.createDynamicsCompressor();
  // Duck stacked voices before they clip
  masterComp.threshold.value = -26;
  masterComp.knee.value = 18;
  masterComp.ratio.value = 10;
  masterComp.attack.value = 0.003;
  masterComp.release.value = 0.22;

  // Hard-ish ceiling (high ratio / low threshold) as a simple limiter
  masterLimiter = c.createDynamicsCompressor();
  masterLimiter.threshold.value = -8;
  masterLimiter.knee.value = 2;
  masterLimiter.ratio.value = 20;
  masterLimiter.attack.value = 0.001;
  masterLimiter.release.value = 0.08;

  masterGain.connect(masterComp);
  masterComp.connect(masterLimiter);
  masterLimiter.connect(c.destination);
}

function sampleBus() {
  const c = unlock();
  return masterGain;
}

function stopSampleSet(pool) {
  for (const audio of pool) {
    try {
      audio.pause();
      audio.currentTime = 0;
    } catch { /* ignore */ }
  }
  pool.clear();
}

export function stopSamplePlayback() {
  stopSampleSet(activeSamples);
}

export function stopOrbSamplePlayback() {
  stopSampleSet(orbSamples);
}

function stopPreviewOscillators() {
  const c = ctx;
  const now = c ? c.currentTime : 0;
  for (const osc of activePreviewOscs) {
    try { osc.stop(now); } catch { /* already stopped */ }
    try { osc.disconnect(); } catch { /* ignore */ }
  }
  activePreviewOscs.length = 0;
  if (!previewOut) return;
  try {
    previewOut.gain.cancelScheduledValues(0);
    previewOut.gain.setValueAtTime(0, now);
    previewOut.disconnect();
  } catch { /* ignore */ }
  previewOut = null;
}

export function beginPreviewAudio() {
  if (isMidiMode()) {
    stopPreviewAudio();
    return;
  }
  stopPreviewAudio();
  stopOrbSamplePlayback();
  const c = unlock();
  previewOut = c.createGain();
  previewOut.gain.value = 1;
  previewOut.connect(c.destination);
}

export function stopPreviewAudio() {
  stopSamplePlayback();
  stopPreviewOscillators();
}

function noteNameFromFreq(freq) {
  let best = NOTES[0]?.name ?? 'C4';
  let bestDiff = Infinity;
  for (const n of NOTES) {
    const diff = Math.abs(n.freq - freq);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = n.name;
    }
  }
  return best;
}

function sampleUrl(stored) {
  if (!stored) return null;
  const s = String(stored).trim();
  if (!s) return null;
  if (/^https?:\/\//i.test(s)) return s;
  const path = s.replace(/^\//, '');
  if (path.startsWith('sounds/')) return `/${path}`;
  return `/sounds/${path}`;
}

function sampleForVoice(voiceId, noteName, freq) {
  const v = VOICES[voiceId] ?? VOICES[0];
  if (!v?.notes) return null;
  const key = noteName || noteNameFromFreq(freq);
  const stored = v.notes[key];
  return sampleUrl(stored);
}

function playSample(url, when = 0, { orb = false, volume = SAMPLE_BASE_VOL } = {}) {
  const c = unlock();
  const delayMs = when > 0 ? Math.max(0, (when - c.currentTime) * 1000) : 0;
  const pool = orb ? orbSamples : activeSamples;
  const vol = Math.max(0, Math.min(1, volume));

  const start = () => {
    const audio = new Audio(url);
    // Route through compressor bus so stacked voices auto-limit
    const src = c.createMediaElementSource(audio);
    const g = c.createGain();
    g.gain.value = vol;
    src.connect(g);
    g.connect(sampleBus());
    pool.add(audio);
    const cleanup = () => {
      pool.delete(audio);
      try { src.disconnect(); } catch { /* ignore */ }
      try { g.disconnect(); } catch { /* ignore */ }
    };
    audio.addEventListener('ended', cleanup, { once: true });
    audio.addEventListener('error', cleanup, { once: true });
    audio.play().catch(err => {
      cleanup();
      console.warn('sample play failed', url, err);
    });
  };

  if (delayMs > 0) setTimeout(start, delayMs);
  else start();
}

function playOscTone(freq, voiceId, dur, when, preview, volumeScale = 1) {
  const c = unlock();
  const v = VOICES[voiceId] ?? VOICES[0];
  const t = when > 0 ? when : c.currentTime;

  const osc = c.createOscillator();
  const gain = c.createGain();
  const filt = c.createBiquadFilter();

  osc.type = v.wave;
  osc.frequency.setValueAtTime(freq, t);
  filt.type = 'lowpass';
  filt.frequency.setValueAtTime(v.filter, t);

  const peak = (v.gain ?? 0.2) * Math.max(0, Math.min(1, volumeScale));
  gain.gain.setValueAtTime(0, t);
  gain.gain.linearRampToValueAtTime(peak, t + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.001, t + dur);

  osc.connect(filt);
  filt.connect(gain);
  gain.connect(preview && previewOut ? previewOut : sampleBus());
  osc.start(t);
  osc.stop(t + dur + 0.04);
  if (preview) activePreviewOscs.push(osc);
}

function pitchFromNoteName(noteName, freq) {
  if (noteName && NOTE_PITCH[noteName] != null) return NOTE_PITCH[noteName];
  const inferred = noteNameFromFreq(freq);
  return NOTE_PITCH[inferred] ?? 0;
}

function voiceNameFromId(voiceId) {
  const v = VOICES[voiceId] ?? VOICES[0];
  return v?.name || '_listen';
}

/** Same-tick playTone calls coalesce into one /api/midi/notes POST. */
let _midiBatch = [];
let _midiFlushScheduled = false;

function _flushMidiBatch() {
  _midiFlushScheduled = false;
  const batch = _midiBatch;
  _midiBatch = [];
  if (!batch.length) return;

  if (batch.length === 1) {
    fetch('/api/midi/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(batch[0]),
    }).catch(err => console.warn('midi note failed', err));
    return;
  }

  fetch('/api/midi/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notes: batch }),
  }).catch(err => console.warn('midi notes failed', err));
}

function postMidiNote({ voice, pitch, durationSec, preview = false }) {
  const duration_ms = Math.max(40, Math.round((durationSec || 0.32) * 1000));
  _midiBatch.push({
    voice: preview ? '_listen' : voice,
    pitch: Math.max(0, Math.min(7, pitch | 0)),
    duration_ms,
  });
  if (!_midiFlushScheduled) {
    _midiFlushScheduled = true;
    queueMicrotask(_flushMidiBatch);
  }
}

export function playTone(freq, voiceId, dur = 0.32, when = 0, {
  preview = false,
  noteName = '',
  volume = SAMPLE_BASE_VOL,
  polyphony = 1,
  /** Same pitch stacked (dispensa) — stronger per-voice duck. */
  coherent = false,
  /** Skip sound (e.g. placeExternal when server already MIDI-triggered). */
  silent = false,
} = {}) {
  if (!audioOwnerCheck() || silent) return;

  if (isMidiMode()) {
    const delayMs = when > 0 && ctx
      ? Math.max(0, (when - ctx.currentTime) * 1000)
      : (when > 1 ? when : 0);
    const send = () => postMidiNote({
      voice: voiceNameFromId(voiceId),
      pitch: pitchFromNoteName(noteName, freq),
      durationSec: dur,
      preview,
    });
    if (delayMs > 0) setTimeout(send, delayMs);
    else send();
    return;
  }

  const vol = polyphony > 1
    ? mixVolume(polyphony, volume, { coherent })
    : Math.min(1, volume);
  // 다시듣기/미리듣기 — Web Audio (박자 길이에 맞춤). 오브 재생만 목소리 샘플.
  if (!preview) {
    const url = sampleForVoice(voiceId, noteName, freq);
    if (url) {
      playSample(url, when, { orb: true, volume: vol });
      return;
    }
  }
  playOscTone(freq, voiceId, dur, when, preview, vol / SAMPLE_BASE_VOL);
}

export function playSuccess() {
  if (isMidiMode()) return;
  const c = unlock();
  [523, 659, 784, 1047].forEach((f, i) => {
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = 'sine';
    osc.frequency.value = f;
    const t = c.currentTime + i * 0.08;
    g.gain.setValueAtTime(0.12, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.35);
    osc.connect(g);
    g.connect(c.destination);
    osc.start(t);
    osc.stop(t + 0.4);
  });
}

export function playMelody(notes, bpm, voiceId, onBeat) {
  const c = unlock();
  const step = 60 / bpm;
  notes.forEach((noteIdx, i) => {
    const note = NOTES[noteIdx];
    const freq = note?.freq ?? 261.63;
    const t = c.currentTime + i * step + 0.15;
    playTone(freq, voiceId, 0.38, t, { noteName: note?.name ?? '' });
    if (onBeat) setTimeout(() => onBeat(i), (i * step + 0.15) * 1000);
  });
}
