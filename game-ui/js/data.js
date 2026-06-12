export const VOICES = [
  { id: 0, name: 'Warm',  color: '#f472b6', wave: 'sine',     mult: 1.0, filter: 900  },
  { id: 1, name: 'Bright', color: '#5eead4', wave: 'triangle', mult: 1.5, filter: 2200 },
  { id: 2, name: 'Bold',  color: '#fbbf24', wave: 'sawtooth', mult: 1.0, filter: 1100 },
  { id: 3, name: 'Soft',  color: '#a78bfa', wave: 'sine',     mult: 1.5, filter: 2000 },
];

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

export function beatCount(melody) {
  return melody?.notes?.length ?? 0;
}
