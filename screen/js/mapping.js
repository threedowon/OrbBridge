import { noteNameToRow, BEAT_COLS } from './data.js';

/** table 행 1–8 → 음표 (C4–C5) */
export const ROW_NOTES = ['C4', 'D4', 'E4', 'F4', 'G4', 'A4', 'B4', 'C5'];
export const ROW_LABELS = ['C', 'D', 'E', 'F', 'G', 'A', 'B', 'C'];

/** GET /api/voices에서 불러온 orb_mapping voice의 VOICES index다. */
let VOICE_INDEX = {
  aespa_1: 0,
  aespa_2: 1,
  aespa_3: 2,
  aespa_4: 3,
  ive_1: 4,
  ive_2: 5,
  ive_3: 6,
  ive_4: 7,
  cortis_1: 8,
};

export function setVoiceIndex(index) {
  if (index && typeof index === 'object') {
    VOICE_INDEX = { ...index };
  }
}

/** cell 1–8 → row 1–8 (도~도), cell 9–16 → row 1–8 */
export function cellToTableRow(cell) {
  return cell <= 8 ? cell : cell - 8;
}

/** slave와 cell을 beat 열로 바꾸며 cell 1–8은 Q1, 9–16은 Q2다. */
export function slaveCellToCol(slave, cell) {
  const q = cell <= 8 ? 1 : 2;
  const col = q + (slave - 1) * 2 - 1;
  if (col < 0 || col >= BEAT_COLS) {
    throw new Error(`column out of range: slave=${slave} cell=${cell}`);
  }
  return col;
}

export function tableRowToStaffRow(tableRow, scale) {
  const name = ROW_NOTES[tableRow - 1];
  if (!name) throw new Error(`invalid table row: ${tableRow}`);
  return noteNameToRow(name, scale);
}

export function tableToStaff(slave, cell, scale) {
  const col = slaveCellToCol(slave, cell);
  const tableRow = cellToTableRow(cell);
  const row = tableRowToStaffRow(tableRow, scale);
  return { col, row, noteName: ROW_NOTES[tableRow - 1] };
}

export function voiceToIndex(voice) {
  const id = String(voice || '').trim();
  if (!id || id === 'unknown') return -1;
  if (id in VOICE_INDEX) return VOICE_INDEX[id];
  console.warn('unknown voice, ignored:', id);
  return -1;
}
