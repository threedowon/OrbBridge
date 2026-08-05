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
 * 브라우저 표시 모델이다.
 *
 * 재생 타이머는 Python에만 두며, 이 객체는 서버가 보낸 beat에 맞춰
 * 오선 하이라이트와 오브 배치 화면만 갱신한다.
 */
export function createEngine() {
  let melody = null;
  let voice = 0;
  let grid = [];
  let locked = [];
  let beat = -1;
  let playbackState = 'idle';
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

  /** 서버가 보낸 grid로 시각 표시용 정답 열 lock을 계산한다. */
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

    /** 서버에서 새 게임을 준비하면 표시 모델을 초기화한다. */
    load(nextMelody, nextVoice = 0) {
      melody = nextMelody;
      voice = nextVoice;
      grid = emptyGrid();
      locked = new Array(beats()).fill(false);
      orbPositions = new Map();
      playbackState = 'idle';
      hintOn = false;
      clearPlaybackHighlight();
      syncLockedFromGrid();
      redraw();
      emit('playbackState', 'idle');
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

    /** 개발용 로컬 배치이며 실제 테이블 입력은 서버 승인 뒤 placeExternal을 사용한다. */
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
     * 서버가 승인한 배치를 반영하고 orb UID마다 위치 하나만 유지한다.
     * MIDI 모드에서는 Python이 이미 소리를 냈으므로 브라우저는 음소거한다.
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
        // 재연결 snapshot은 화면만 복구하며 배치된 오브 소리를 다시 내지 않는다.
        silent: silent || isMidiMode(),
      });
      emit('gridChange');
      redraw();
    },

    /** 서버가 확인한 배치 하나를 표시 grid에서 제거한다. */
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

    /** Python이 관리하는 재생 시작을 표시한다. */
    beginPlayback(mode) {
      playbackMode = mode;
      playbackState = 'playing';
      beat = -1;
      previewStep = -1;
      emit('playbackStart', mode);
      redraw();
    },

    /** Python에서 시각이 도달한 beat 하나를 별도 브라우저 타이머 없이 표시한다. */
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

    /** Python이 재생 완료나 취소를 알리면 하이라이트를 지운다. */
    endPlayback(mode) {
      if (playbackMode && mode && playbackMode !== mode) return;
      clearPlaybackHighlight();
      playbackState = 'idle';
      emit('playbackEnd', mode);
      redraw();
    },

    /** 화면의 재생 상태만 멈추며 Python 취소는 호출부가 별도로 처리한다. */
    stop() {
      clearPlaybackHighlight();
      playbackState = 'idle';
      redraw();
      emit('playbackState', 'idle');
    },

    reset() {
      if (melody) this.load(melody, voice);
    },

    redraw,
    get playbackState() {
      return playbackState;
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
