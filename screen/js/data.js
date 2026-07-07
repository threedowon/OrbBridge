/** Fallback when /api/voices is unavailable */
export let VOICES = [
  { id: 0, name: 'aspa_1',   color: '#f472b6', wave: 'sine',     filter: 900,  gain: 0.20 },
  { id: 1, name: 'aspa_2',   color: '#5eead4', wave: 'triangle', filter: 2200, gain: 0.18 },
  { id: 2, name: 'aspa_3',   color: '#fbbf24', wave: 'sawtooth', filter: 1100, gain: 0.16 },
  { id: 3, name: 'aspa_4',   color: '#a78bfa', wave: 'sine',     filter: 2000, gain: 0.19 },
  { id: 4, name: 'ive_1',    color: '#60a5fa', wave: 'square',   filter: 1400, gain: 0.14 },
  { id: 5, name: 'ive_2',    color: '#4ade80', wave: 'triangle', filter: 1600, gain: 0.17 },
  { id: 6, name: 'ive_3',    color: '#fb923c', wave: 'sine',     filter: 800,  gain: 0.21 },
  { id: 7, name: 'ive_4',    color: '#f87171', wave: 'sawtooth', filter: 1800, gain: 0.15 },
  { id: 8, name: 'cortis_1', color: '#c084fc', wave: 'triangle', filter: 2400, gain: 0.18 },
];

/** Replace timbre list from GET /api/voices */
export function setVoices(list) {
  if (!Array.isArray(list) || !list.length) return;
  VOICES = list.map((v, i) => ({ ...v, id: i }));
}

export const NOTES = [
  { name: 'C4', freq: 261.63 },
  { name: 'D4', freq: 293.66 },
  { name: 'E4', freq: 329.63 },
  { name: 'F4', freq: 349.23 },
  { name: 'G4', freq: 392.00 },
  { name: 'A4', freq: 440.00 },
  { name: 'B4', freq: 493.88 },
  { name: 'C5', freq: 523.25 },
];

export const FINISH_LOOPS = 1;

/** table / screen 공통 — 가로 박자 칸 수 */
export const BEAT_COLS = 12;

export const NOTE_REST = 'REST';
export const NOTE_SKIP = 'X';

export function noteName(entry) {
  if (typeof entry === 'string') return entry;
  return entry?.name ?? '';
}

export function isSkipNote(entry) {
  return noteName(entry).toUpperCase() === NOTE_SKIP;
}

export function isRestNote(entry) {
  return noteName(entry).toUpperCase() === NOTE_REST;
}

export function isPitchNote(entry) {
  return !isSkipNote(entry) && !isRestNote(entry);
}

export function buildSlotLayout(notes) {
  const visibleSlots = [];
  const slotToDisplayCol = Array(notes.length).fill(-1);
  notes.forEach((entry, slot) => {
    if (!isSkipNote(entry)) {
      slotToDisplayCol[slot] = visibleSlots.length;
      visibleSlots.push(slot);
    }
  });
  return { visibleSlots, slotToDisplayCol };
}

export function visibleBeatCount(melody) {
  return melody?.visibleSlots?.length ?? 0;
}

export function slotForDisplayCol(melody, displayCol) {
  return melody?.visibleSlots?.[displayCol] ?? -1;
}

export function buildScale(range) {
  if (!Array.isArray(range) || range.length !== 2) {
    throw new Error('range는 ["C4", "C5"] 형식의 2개 음표여야 합니다.');
  }
  const [low, high] = range;
  const li = NOTES.findIndex(n => n.name === low);
  const hi = NOTES.findIndex(n => n.name === high);
  if (li < 0 || hi < 0) throw new Error(`range 음표를 찾을 수 없습니다: ${low}, ${high}`);
  if (li > hi) throw new Error('range는 낮은 음 → 높은 음 순서여야 합니다.');
  return NOTES.slice(li, hi + 1);
}

export function rowCount(melody) {
  return melody.scale.length;
}

/** row 0 = scale 최고음 */
export function rowToNoteIn(row, scale) {
  return scale[scale.length - 1 - row];
}

export function noteNameToRow(name, scale) {
  const si = scale.findIndex(n => n.name === name);
  if (si < 0) throw new Error(`음역 밖 음표: ${name}`);
  return scale.length - 1 - si;
}

export function isStaffLine(noteName) {
  return /^[CEG]/.test(noteName);
}

/** @param {number | { n: number, dur?: number }} entry */
export function noteAt(entry) {
  return typeof entry === 'number' ? entry : entry.n;
}

/** @param {number | { n: number, dur?: number }} entry */
export function durAt(entry) {
  return typeof entry === 'number' ? 1 : (entry.dur ?? 1);
}

export function melodyBeatCount(melody) {
  return melody?.notes?.length ?? 0;
}

export function beatCount(melody) {
  return BEAT_COLS;
}
