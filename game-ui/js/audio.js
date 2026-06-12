import { VOICES } from './data.js';

let ctx = null;

export function unlock() {
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

export function playTone(freq, voiceId, dur = 0.32, when = 0) {
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
  gain.connect(c.destination);
  osc.start(t);
  osc.stop(t + dur + 0.04);
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
    const freq = [261.63, 293.66, 329.63, 349.23, 392, 440, 493.88, 523.25][noteIdx];
    const t = c.currentTime + i * step + 0.15;
    playTone(freq, voiceId, 0.38, t);
    if (onBeat) setTimeout(() => onBeat(i), (i * step + 0.15) * 1000);
  });
}
