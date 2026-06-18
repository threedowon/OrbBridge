import { NOTES, VOICES } from './data.js';

let ctx = null;
let previewOut = null;
/** @type {Set<HTMLAudioElement>} */
const activeSamples = new Set();

export function unlock() {
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

export function stopSamplePlayback() {
  for (const audio of activeSamples) {
    try {
      audio.pause();
      audio.currentTime = 0;
    } catch { /* ignore */ }
  }
  activeSamples.clear();
}

export function beginPreviewAudio() {
  stopPreviewAudio();
  const c = unlock();
  previewOut = c.createGain();
  previewOut.gain.value = 1;
  previewOut.connect(c.destination);
}

export function stopPreviewAudio() {
  stopSamplePlayback();
  if (!previewOut) return;
  try {
    previewOut.gain.cancelScheduledValues(0);
    previewOut.disconnect();
  } catch { /* ignore */ }
  previewOut = null;
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

function playSample(url, when = 0) {
  const c = unlock();
  const delayMs = when > 0 ? Math.max(0, (when - c.currentTime) * 1000) : 0;

  const start = () => {
    const audio = new Audio(url);
    audio.volume = 0.88;
    activeSamples.add(audio);
    const cleanup = () => activeSamples.delete(audio);
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

function playOscTone(freq, voiceId, dur, when, preview) {
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

  const peak = v.gain ?? 0.2;
  gain.gain.setValueAtTime(0, t);
  gain.gain.linearRampToValueAtTime(peak, t + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.001, t + dur);

  osc.connect(filt);
  filt.connect(gain);
  gain.connect(preview && previewOut ? previewOut : c.destination);
  osc.start(t);
  osc.stop(t + dur + 0.04);
}

export function playTone(freq, voiceId, dur = 0.32, when = 0, { preview = false, noteName = '' } = {}) {
  // start / 다시듣기 — 정답 멜로디는 Web Audio (Voice Register 샘플 사용 안 함)
  if (!preview) {
    const url = sampleForVoice(voiceId, noteName, freq);
    if (url) {
      playSample(url, when);
      return;
    }
  }
  playOscTone(freq, voiceId, dur, when, preview);
}

export function playSuccess() {
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
