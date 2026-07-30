import {
  BEAT_COLS,
  isPitchNote,
  isRestNote,
  melodyBeatCount,
  noteAt,
  rowCount,
  rowToNoteIn,
} from './data.js';
import { playTone, isMidiMode } from './audio.js';
import { draw } from './staff.js';

/**
 * Browser-side presentation model.
 *
 * M3 deliberately keeps no playback timers here. Python emits each scheduled
 * beat, and this object only updates the staff highlight and placed-orb grid.
 */
export function createEngine() {
  let melody = null;
  let voice = 0;
  let grid = [];
  let locked = [];
  let beat = -1;
  let phase = 'idle';
  let hintOn = false;
  let previewStep = -1;
  let playbackMode = null;
  let orbPositions = new Map();
  let listeners = {};

  function gridCols() {
    return BEAT_COLS;
  }

  function beats() {
    return melodyBeatCount(melody);
  }

  function rows() {
    return melody ? rowCount(melody) : 0;
  }

  function emptyGrid() {
    return Array.from({ length: gridCols() }, () => Array(rows()).fill(-1));
  }

  function emit(evt, data) {
    listeners[evt]?.forEach(fn => fn(data));
  }

  function redraw() {
    draw({ grid, locked, melody, beat, voice, previewBeat: previewStep });
  }

  function clearOrbAt(col, row) {
    if (col < 0 || col >= gridCols() || row < 0 || row >= rows()) return;
    grid[col][row] = -1;
  }

  /**
   * Derive correct-column locks from the server-fed grid.
   * Python owns the actual completion decision; these locks are visual only.
   */
  function syncLockedFromGrid() {
    if (!melody) return;
    for (let col = 0; col < beats(); col++) {
      const entry = melody.notes[col];
      if (isRestNote(entry)) {
        locked[col] = true;
      } else if (isPitchNote(entry)) {
        locked[col] = grid[col][noteAt(entry)] >= 0;
      }
    }
  }

  function clearPlaybackHighlight() {
    playbackMode = null;
    beat = -1;
    previewStep = -1;
  }

  return {
    on(evt, fn) {
      (listeners[evt] ??= []).push(fn);
      return () => {
        listeners[evt] = listeners[evt].filter(listener => listener !== fn);
      };
    },

    /**
     * Reset the presentation model when a new server game is prepared.
     * ``autoStart`` remains accepted for compatibility with older callers.
     */
    load(nextMelody, nextVoice = 0, _opts = {}) {
      melody = nextMelody;
      voice = nextVoice;
      grid = emptyGrid();
      locked = new Array(beats()).fill(false);
      orbPositions = new Map();
      phase = 'idle';
      hintOn = false;
      clearPlaybackHighlight();
      syncLockedFromGrid();
      redraw();
      emit('phase', 'idle');
      emit('hint', false);
    },

    setVoice(nextVoice) {
      voice = nextVoice;
      redraw();
    },

    toggleHint() {
      hintOn = !hintOn;
      emit('hint', hintOn);
      return hintOn;
    },

    get hintOn() {
      return hintOn;
    },

    /**
     * Development-only local placement. Real table placements use
     * ``placeExternal`` after the Python server accepts the event.
     */
    place(col, row) {
      if (!melody || col < 0 || col >= gridCols() || row < 0 || row >= rows()) {
        return;
      }
      const occupied = grid[col][row] >= 0;
      grid[col][row] = occupied ? -1 : voice;
      if (!occupied) {
        const note = rowToNoteIn(row, melody.scale);
        playTone(note.freq, voice, 0.32, 0, { noteName: note.name });
      }
      syncLockedFromGrid();
      redraw();
    },

    /**
     * Apply one accepted server placement and keep one location per orb UID.
     * MIDI placement sound already came from Python, so the browser stays mute
     * in MIDI mode and only uses Web Audio in the development fallback mode.
     */
    placeExternal(col, row, voiceId = 0, orbId = null, { silent = false } = {}) {
      if (!melody || voiceId < 0) return;
      if (col < 0 || col >= gridCols() || row < 0 || row >= rows()) return;

      if (orbId && orbPositions.has(orbId)) {
        const previous = orbPositions.get(orbId);
        clearOrbAt(previous.col, previous.row);
      }

      grid[col][row] = voiceId;
      if (orbId) orbPositions.set(orbId, { col, row });
      syncLockedFromGrid();

      const note = rowToNoteIn(row, melody.scale);
      playTone(note.freq, voiceId, 0.32, 0, {
        noteName: note.name,
        // A reconnect snapshot reconstructs visual state and must never replay
        // every placed orb through either the MIDI or Web Audio fallback.
        silent: silent || isMidiMode(),
      });
      emit('gridChange');
      redraw();
    },

    /** Remove one server-confirmed placement from the presentation grid. */
    removeExternal(col, row, orbId = null) {
      if (!melody) return;
      if (col < 0 || col >= gridCols() || row < 0 || row >= rows()) return;
      clearOrbAt(col, row);

      if (orbId) {
        const previous = orbPositions.get(orbId);
        if (previous?.col === col && previous?.row === row) {
          orbPositions.delete(orbId);
        }
      } else {
        for (const [id, position] of orbPositions) {
          if (position.col === col && position.row === row) {
            orbPositions.delete(id);
            break;
          }
        }
      }

      syncLockedFromGrid();
      emit('gridChange');
      redraw();
    },

    /** Mark the start of a Python-owned playback run. */
    beginPlayback(mode) {
      playbackMode = mode;
      phase = 'playing';
      beat = -1;
      previewStep = -1;
      emit('playbackStart', mode);
      redraw();
    },

    /**
     * Render one beat that has already become due in Python.
     * No browser timeout or duration calculation is performed here.
     */
    showPlaybackBeat(mode, col) {
      if (!melody || col < 0 || col >= beats()) return;
      playbackMode = mode;
      if (mode === 'listen') {
        previewStep = col;
        beat = -1;
        emit('previewBeat', col);
      } else {
        beat = col;
        previewStep = -1;
        emit('beat', col);
      }
      redraw();
    },

    /** Clear the highlight after Python reports playback completion/cancel. */
    endPlayback(mode) {
      if (playbackMode && mode && playbackMode !== mode) return;
      clearPlaybackHighlight();
      phase = 'idle';
      emit('playbackEnd', mode);
      redraw();
    },

    /** Stop only visual playback state; the caller separately cancels Python. */
    stop() {
      clearPlaybackHighlight();
      phase = 'idle';
      redraw();
      emit('phase', 'idle');
    },

    reset() {
      if (melody) this.load(melody, voice);
    },

    redraw,
    get phase() {
      return phase;
    },
    get beat() {
      return beat;
    },
    get locked() {
      return [...locked];
    },
    get melody() {
      return melody;
    },
  };
}
