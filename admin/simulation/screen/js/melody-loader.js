import {
  BEAT_COLS,
  NOTE_REST,
  buildScale,
  buildSlotLayout,
  noteNameToRow,
} from './data.js';

export function parseMelodyJson(raw, fileId = 'custom') {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('멜로디 JSON은 객체 형식이어야 합니다.');
  }

  const steps = raw.steps ?? raw.notes;
  if (!Array.isArray(steps) || steps.length === 0) {
    throw new Error('"steps" 배열이 필요합니다.');
  }
  if (steps.length > BEAT_COLS) {
    throw new Error(`steps는 최대 ${BEAT_COLS}개까지 가능합니다.`);
  }

  const range = raw.range ?? ['C4', 'C5'];
  const scale = buildScale(range);

  const notes = steps.map((step, i) => {
    const label = step.step ?? i + 1;
    const rawName = String(step.note ?? '').trim();
    const name = rawName.toUpperCase();

    const dur = step.duration ?? step.dur ?? 1;
    if (typeof dur !== 'number' || dur <= 0) {
      throw new Error(`step ${label}: duration이 올바르지 않습니다.`);
    }

    // REST와 이전 미사용 표기인 X·?를 쉼표로 처리한다.
    if (name === NOTE_REST || name === 'R' || name === 'X' || name === '?' || rawName === '-') {
      return { name: NOTE_REST, n: -1, dur };
    }
    if (!rawName) {
      throw new Error(`step ${label}: note를 입력하세요`);
    }

    return {
      name: rawName,
      n: noteNameToRow(rawName, scale),
      dur,
    };
  });

  const layout = buildSlotLayout(notes);

  return {
    songId: raw.songId ?? fileId,
    title: raw.title ?? 'Untitled',
    artist: raw.artist ?? '',
    bpm: raw.bpm ?? 90,
    range,
    scale,
    notes,
    visibleSlots: layout.visibleSlots,
    slotToDisplayCol: layout.slotToDisplayCol,
  };
}

export async function loadActiveMelody() {
  const res = await fetch('/api/melody', { cache: 'no-store' });
  if (!res.ok) throw new Error('활성 멜로디를 불러올 수 없습니다.');
  const raw = await res.json();
  return parseMelodyJson(raw, raw.songId ?? 'custom');
}

