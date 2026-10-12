"""Device-independent MIDI monitor DSP. MIDI keys select eight diatonic samples."""
import math
import queue
import re
import time
import numpy as np

KEYS = tuple(range(36, 44))
PITCHES = (60, 62, 64, 65, 67, 69, 71, 72)
NAMES = ('C4', 'D4', 'E4', 'F4', 'G4', 'A4', 'B4', 'C5')


def mapped_pitch(key, octave=0):
    return PITCHES[key - 36] + 12 * octave if key in KEYS else None


def match_port(actual, configured):
    """Accept backend numeric suffixes, never ambiguous substring matches."""
    return bool(re.fullmatch(re.escape(configured.strip()) + r'(?:\s+\d+)*', actual.strip(), re.I))


def clean_settings(raw):
    # Legacy offsets treated selector keys as chromatic notes; do not reuse them.
    if not isinstance(raw, dict) or raw.get('version') != 2:
        raw = {'version': 2}
    try:
        db = float(raw.get('volume_db', -18))
        if not math.isfinite(db):
            db = -18
        octave = int(raw.get('octave', 0))
    except (TypeError, ValueError, OverflowError):
        db, octave = -18, 0
    def bounded(name, default, low, high):
        try:
            value = float(raw.get(name, default))
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (ValueError, TypeError):
            return default
    return {'version': 2, 'volume_db': max(-48, min(0, db)),
            'note_length_ms': bounded('note_length_ms', 150, 0, 2000),
            'release_ms': bounded('release_ms', 8, 2, 80),
            'mono': raw.get('mono', True) is not False,
            'exclusive': raw.get('exclusive') is True,
            'muted': raw.get('muted') is True, 'octave': max(-2, min(2, octave)),
            'tone': raw.get('tone') if raw.get('tone') in ('Sine', 'Soft') else 'Sine'}


class MonitorEngine:
    """Audio thread owns voices; input callbacks enqueue bounded commands only."""
    def __init__(self, sample_rate=48000):
        self.sample_rate = sample_rate
        self.commands = queue.Queue(maxsize=4096)
        self.voices = {}
        self.position = 0
        self.volume_db = -18.0
        self.muted = False
        self.tone = 'Sine'
        self.note_length_ms = 150
        self.release_ms = 8
        self.mono = True
        self.queue_ms = 0.0
        self.gain = 10 ** (-18 / 20)
        self.overflows = 0
        self.force_panic = False
        self.meter = {'peak': 0.0, 'voices': 0, 'keys': ()}

    def send(self, action, key=None, pitch=None, velocity=0, gate_ms=None):
        try:
            self.commands.put_nowait((action, key, pitch, velocity, gate_ms, time.perf_counter()))
        except queue.Full:
            self.overflows += 1
            self.force_panic = True

    def reset(self):
        """Only while the audio stream is stopped; discard stale queued playback."""
        self.voices.clear()
        while True:
            try:
                self.commands.get_nowait()
            except queue.Empty:
                break
        self.force_panic = False
        self.meter = {'peak': 0.0, 'voices': 0, 'keys': ()}

    def render(self, frames):
        rate = self.sample_rate
        if self.force_panic:
            self.force_panic = False
            while True:
                try:
                    self.commands.get_nowait()
                except queue.Empty:
                    break
            for voice in self.voices.values():
                voice['release'] = True
        for _ in range(4096):
            try:
                action, key, pitch, velocity, gate_ms, received_at = self.commands.get_nowait()
            except queue.Empty:
                break
            if action == 'on' and velocity > 0:
                self.queue_ms = max(0, (time.perf_counter() - received_at) * 1000)
                if len(self.voices) >= 256 and key not in self.voices:
                    continue
                if self.mono and key[0] != 'local':
                    for previous_key, previous in self.voices.items():
                        if previous_key[:2] == key[:2] and previous_key != key:
                            previous['release'] = True
                old = self.voices.get(key)
                length = self.note_length_ms if gate_ms is None else gate_ms
                self.voices[key] = {'pitch': pitch, 'velocity': min(127, velocity) / 127,
                                    'level': old['level'] if old else 0.0,
                                    'release': False, 'age': 0,
                                    'gate': round(rate * length / 1000) if length > 0 else rate * 120}
            elif action == 'off' or (action == 'on' and velocity == 0):
                if key in self.voices:
                    self.voices[key]['release'] = True
            elif action in ('panic', 'channel_off'):
                for voice_key, voice in self.voices.items():
                    if action == 'panic' or voice_key[:2] == key:
                        voice['release'] = True

        samples = np.arange(1, frames + 1, dtype=np.float64)
        times = (self.position + samples - 1) / rate
        mix = np.zeros(frames)
        weight = np.zeros(frames)
        envelope_peak = np.zeros(frames)
        dead = []
        keys = set()
        waves = {}
        for key, voice in self.voices.items():
            release_samples = max(1, rate * self.release_ms / 1000)
            if voice['release']:
                envelope = np.maximum(0, voice['level'] - samples / release_samples)
            else:
                remaining = max(0, voice['gate'] - voice['age'])
                envelope = np.minimum(1, voice['level'] + samples / (rate * .003))
                if remaining < frames:
                    gate_level = min(1, voice['level'] + remaining / (rate * .003))
                    envelope = np.where(samples <= remaining, envelope,
                                        np.maximum(0, gate_level - (samples - remaining) / release_samples))
                    voice['release'] = True
            voice['level'] = float(envelope[-1])
            voice['age'] += frames
            if voice['pitch'] not in waves:
                phase = 2 * np.pi * (440 * 2 ** ((voice['pitch'] - 69) / 12)) * times
                wave = np.sin(phase)
                if self.tone == 'Soft':
                    wave = (wave + .15 * np.sin(2 * phase)) / 1.15
                waves[voice['pitch']] = wave
            wave = waves[voice['pitch']]
            mix += wave * envelope * voice['velocity']
            weight += envelope
            envelope_peak = np.maximum(envelope_peak, envelope)
            if voice['release'] and voice['level'] == 0:
                dead.append(key)
            elif key[-1] in KEYS:
                keys.add(key[-1])
        for key in dead:
            del self.voices[key]
        # Weighted average keeps simultaneous members from multiplying loudness.
        # Velocity is NOT in the denominator: quiet MIDI remains quiet.
        # Preserve the common attack/release even when many voices fade together.
        mix *= envelope_peak / np.maximum(1e-12, weight)
        target = 0.0 if self.muted else 10 ** (self.volume_db / 20)
        gains = target + (self.gain - target) * np.exp(-samples / (rate * .015))
        self.gain = float(gains[-1])
        output = (mix * gains * .85).astype(np.float32)
        self.position += frames
        self.meter = {'peak': float(np.max(np.abs(output))), 'voices': len(self.voices), 'keys': tuple(keys)}
        return output
