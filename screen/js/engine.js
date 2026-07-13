import {
  BEAT_COLS,
  FINISH_LOOPS,
  isPitchNote,
  isRestNote,
  melodyBeatCount,
  noteAt,
  durAt,
  rowCount,
  rowToNoteIn,
} from './data.js';
import { playTone, unlock, beginPreviewAudio, stopPreviewAudio } from './audio.js';
import { draw, flash } from './staff.js';

export function createEngine() {
  let melody = null;
  let voice = 0;
  let grid = [];
  let locked = [];
  let beat = -1;
  let timer = null;
  let phase = 'idle';
  let loopCount = 0;
  let finishCount = 0;
  let startTime = 0;
  let hintOn = false;
  let previewStep = -1;
  let previewTimers = [];
  let previewSession = 0;
  let resumeAfterPreview = false;
  let autoStart = true;
  let singlePass = false;
  let unisonMode = false;
  /** @type {number[]} voices of all placed orbs — used by dispensa */
  let dispensaVoices = [];
  let orbPositions = new Map();
  let listeners = {};

  function gridCols() {
    return BEAT_COLS;
  }

  function melodyBeats() {
    return melodyBeatCount(melody);
  }

  function beats() {
    return melodyBeats();
  }

  function rows() {
    return melody ? rowCount(melody) : 0;
  }

  function emptyGrid() {
    return Array.from({ length: gridCols() }, () => Array(rows()).fill(-1));
  }

  function beatMs() {
    return (60 / melody.bpm) * 1000;
  }

  /** 멜로디 steps duration → 초 (샘플 재생 길이) */
  function noteDurationSec(col) {
    const entry = melody?.notes?.[col];
    const beats = entry !== undefined ? durAt(entry) : 1;
    return (beatMs() * beats) / 1000;
  }

  function emit(evt, data) {
    listeners[evt]?.forEach(fn => fn(data));
  }

  function redraw() {
    draw({ grid, locked, melody, beat, voice, previewBeat: previewStep });
  }

  function clearPreview() {
    previewSession++;
    previewTimers.forEach(t => clearTimeout(t));
    previewTimers = [];
    previewStep = -1;
    resumeAfterPreview = false;
    stopPreviewAudio();
    redraw();
  }

  function stopTimer() {
    if (timer) clearTimeout(timer);
    timer = null;
    beat = -1;
  }

  function schedule(col, holdAfterCol = null) {
    if (!melody || col >= beats()) return;
    let delay;
    if (col === 0 && holdAfterCol !== null) {
      delay = beatMs() * durAt(melody.notes[holdAfterCol]);
    } else if (col === 0) {
      delay = 250;
    } else {
      delay = beatMs() * durAt(melody.notes[col - 1]);
    }
    timer = setTimeout(() => tick(col), delay);
  }

  function allPitchLocked() {
    if (!melody) return false;
    for (let c = 0; c < beats(); c++) {
      const entry = melody.notes[c];
      if (isPitchNote(entry) && !locked[c]) return false;
    }
    return true;
  }

  function syncLockedFromGrid() {
    if (!melody) return;
    for (let c = 0; c < beats(); c++) {
      const entry = melody.notes[c];
      if (isRestNote(entry)) {
        locked[c] = true;
      } else if (isPitchNote(entry)) {
        locked[c] = grid[c][noteAt(entry)] >= 0;
      }
    }
    if (phase === 'finishing' && !allPitchLocked()) {
      phase = 'running';
      finishCount = 0;
      emit('phase', 'running');
    }
  }

  function clearOrbAt(col, row) {
    if (col < 0 || col >= gridCols() || row < 0 || row >= rows()) return;
    grid[col][row] = -1;
  }

  function placedInCol(col) {
    const items = [];
    for (let r = 0; r < rows(); r++) {
      if (grid[col][r] >= 0) items.push({ row: r, voice: grid[col][r] });
    }
    return items;
  }

  /** Unique voice ids from every orb currently on the staff. */
  function allPlacedVoices() {
    const seen = new Set();
    const out = [];
    for (let c = 0; c < gridCols(); c++) {
      for (let r = 0; r < rows(); r++) {
        const v = grid[c][r];
        if (v >= 0 && !seen.has(v)) {
          seen.add(v);
          out.push(v);
        }
      }
    }
    return out;
  }

  function tick(col) {
    beat = col;
    emit('beat', col);

    const entry = melody.notes[col];
    const toneDur = noteDurationSec(col);
    const balls = placedInCol(col);

    if (unisonMode) {
      // 디페스타: 테이블 위 모든 오브 목소리로 정답 멜로디 재생
      if (isPitchNote(entry) && dispensaVoices.length) {
        const targetRow = noteAt(entry);
        if (targetRow >= 0) {
          const note = rowToNoteIn(targetRow, melody.scale);
          for (const v of dispensaVoices) {
            playTone(note.freq, v, toneDur, 0, { noteName: note.name });
          }
        }
      }
    } else {
      for (const { row, voice: v } of balls) {
        const note = rowToNoteIn(row, melody.scale);
        playTone(note.freq, v, toneDur, 0, { noteName: note.name });
      }
    }

    let correct = false;
    if (!unisonMode && isPitchNote(entry)) {
      const targetRow = noteAt(entry);
      correct = grid[col][targetRow] >= 0;
      if (correct && !locked[col]) {
        locked[col] = true;
        flash(col, targetRow, true);
        emit('lock', col);
      }
    } else if (isRestNote(entry)) {
      locked[col] = true;
    }

    emit('tick', { col, correct, hasAny: balls.length > 0, locked: locked[col] });
    redraw();

    const next = col + 1;
    if (next >= beats()) {
      if (singlePass) {
        singlePass = false;
        unisonMode = false;
        dispensaVoices = [];
        const holdMs = beatMs() * durAt(entry);
        timer = setTimeout(() => {
          timer = null;
          beat = -1;
          phase = 'idle';
          emit('playOnceEnd');
          redraw();
        }, holdMs);
        return;
      }
      loopCount++;
      emit('loop', loopCount);

      if (phase === 'finishing') {
        finishCount++;
        if (finishCount >= FINISH_LOOPS) {
          phase = 'done';
          stopTimer();
          emit('complete', { loops: loopCount, time: Math.round((Date.now() - startTime) / 1000) });
          redraw();
          return;
        }
      } else if (allPitchLocked()) {
        phase = 'finishing';
        finishCount = 0;
        emit('phase', 'finishing');
      }
      schedule(0, col);
      return;
    }

    schedule(next);
  }

  return {
    on(evt, fn) {
      (listeners[evt] ??= []).push(fn);
      return () => { listeners[evt] = listeners[evt].filter(f => f !== fn); };
    },

    load(m, v = 0, opts = {}) {
      clearPreview();
      stopTimer();
      melody = m;
      voice = v;
      autoStart = opts.autoStart ?? true;
      grid = emptyGrid();
      locked = new Array(beats()).fill(false);
      orbPositions = new Map();
      beat = -1;
      phase = 'idle';
      loopCount = 0;
      finishCount = 0;
      hintOn = false;
      syncLockedFromGrid();
      redraw();
      emit('phase', 'idle');
      emit('hint', false);
    },

    setVoice(v) { voice = v; redraw(); },

    toggleHint() {
      hintOn = !hintOn;
      emit('hint', hintOn);
      return hintOn;
    },

    get hintOn() { return hintOn; },

    place(col, row) {
      if (phase === 'done') return;
      clearPreview();
      const had = grid[col][row] >= 0;
      grid[col][row] = had ? -1 : voice;
      if (!had) {
        const note = rowToNoteIn(row, melody.scale);
        playTone(note.freq, voice, noteDurationSec(col), 0, { noteName: note.name });
      }
      if (autoStart && phase === 'idle') {
        phase = 'running';
        startTime = Date.now();
        emit('phase', 'running');
        schedule(0);
      }
      redraw();
    },

    placeExternal(col, row, voiceId = 0, orbId = null) {
      if (!melody || phase === 'done') return;
      if (voiceId < 0) return;
      if (col < 0 || col >= gridCols() || row < 0 || row >= rows()) return;
      clearPreview();

      if (orbId && orbPositions.has(orbId)) {
        const prev = orbPositions.get(orbId);
        clearOrbAt(prev.col, prev.row);
      }

      grid[col][row] = voiceId;
      if (orbId) orbPositions.set(orbId, { col, row });

      syncLockedFromGrid();
      const note = rowToNoteIn(row, melody.scale);
      playTone(note.freq, voiceId, noteDurationSec(col), 0, { noteName: note.name });
      emit('gridChange');
      redraw();
    },

    removeExternal(col, row, orbId = null) {
      if (!melody) return;
      if (col < 0 || col >= gridCols() || row < 0 || row >= rows()) return;
      clearOrbAt(col, row);

      if (orbId) {
        const prev = orbPositions.get(orbId);
        if (prev?.col === col && prev?.row === row) orbPositions.delete(orbId);
      } else {
        for (const [id, pos] of orbPositions) {
          if (pos.col === col && pos.row === row) {
            orbPositions.delete(id);
            break;
          }
        }
      }

      syncLockedFromGrid();
      emit('gridChange');
      redraw();
    },

    playLoop() {
      this._startSinglePass({ unison: false });
    },

    playDispensa() {
      this._startSinglePass({ unison: true });
    },

    _startSinglePass({ unison = false } = {}) {
      if (!melody || phase === 'done') return;
      clearPreview();
      stopTimer();
      beat = -1;
      singlePass = true;
      unisonMode = unison;
      dispensaVoices = unison ? allPlacedVoices() : [];
      locked = new Array(beats()).fill(false);
      syncLockedFromGrid();
      phase = 'running';
      if (!startTime) startTime = Date.now();
      emit('phase', 'running');
      redraw();
      schedule(0);
    },

    stop() {
      clearPreview();
      stopTimer();
      phase = 'idle';
      beat = -1;
      redraw();
      emit('phase', 'idle');
    },

    reset() {
      if (!melody) return;
      this.load(melody, voice, { autoStart });
    },

    listen(onBeat) {
      if (!melody) return;
      const wasRunning = phase === 'running' || phase === 'finishing';
      const resumeFrom = beat;
      if (wasRunning) resumeAfterPreview = true;
      stopTimer();
      singlePass = false;
      unisonMode = false;
      dispensaVoices = [];
      clearPreview();
      const session = previewSession;
      beginPreviewAudio();

      unlock();
      const step = beatMs() / 1000;
      let acc = 0;

      melody.notes.forEach((entry, i) => {
        const d = durAt(entry);
        const at = acc;
        const slotSec = d * step;
        const t = setTimeout(() => {
          if (session !== previewSession) return;
          if (isPitchNote(entry)) {
            const note = rowToNoteIn(noteAt(entry), melody.scale);
            const toneDur = Math.min(slotSec * 0.92, 0.9);
            playTone(note.freq, voice, toneDur, 0, {
              preview: true,
              noteName: note.name,
            });
          }
          previewStep = i;
          emit('previewBeat', i);
          redraw();
          onBeat?.(i);
        }, at * step * 1000);
        previewTimers.push(t);
        acc += d;
      });

      const endT = setTimeout(() => {
        if (session !== previewSession) return;
        const shouldResume = resumeAfterPreview && phase !== 'done';
        previewTimers = [];
        previewStep = -1;
        resumeAfterPreview = false;
        stopPreviewAudio();
        redraw();
        emit('previewEnd');
        if (shouldResume) schedule((resumeFrom + 1) % beats());
      }, acc * step * 1000 + 50);
      previewTimers.push(endT);
    },

    redraw,
    get phase() { return phase; },
    get beat() { return beat; },
    get locked() { return [...locked]; },
    get loopCount() { return loopCount; },
    get melody() { return melody; },
  };
}
