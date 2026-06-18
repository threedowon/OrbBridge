import { noteNameToRow } from './data.js';

/** table 행 1–8 → 음표 (C4–C5) */
export const ROW_NOTES = ['C4', 'D4', 'E4', 'F4', 'G4', 'A4', 'B4', 'C5'];
export const ROW_LABELS = ['C', 'D', 'E', 'F', 'G', 'A', 'B', 'C'];

/** orb_mapping.json voice → VOICES index (loaded from GET /api/voices) */
let VOICE_INDEX = {
  aspa_1: 0,
  aspa_2: 1,
  aspa_3: 2,
  aspa_4: 3,
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

export function cellToTableRow(cell) {
  return Math.floor((cell + 1) / 2);
}

/** slave + cell(Q슬롯) → 멜로디 beat column (0-based) */
export function slaveCellToCol(slave, cell) {
  const q = cell % 2 === 1 ? 1 : 2;
  return q + (slave - 1) * 2 - 1;
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
  if (voice in VOICE_INDEX) return VOICE_INDEX[voice];
  console.warn('unknown voice, using default:', voice);
  return 0;
}
