import { buildScale, noteNameToRow } from './data.js';

export function parseMelodyJson(raw, fileId = 'custom') {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('멜로디 JSON은 객체 형식이어야 합니다.');
  }

  const steps = raw.steps ?? raw.notes;
  if (!Array.isArray(steps) || steps.length === 0) {
    throw new Error('"steps" 배열이 필요합니다.');
  }

  const range = raw.range ?? ['C4', 'C5'];
  const scale = buildScale(range);

  const notes = steps.map((step, i) => {
    const label = step.step ?? i + 1;
    const name = step.note;

    if (!name || name === '?') {
      throw new Error(`step ${label}: note를 입력하세요 (현재 "?")`);
    }

    const dur = step.duration ?? step.dur ?? 1;
    if (typeof dur !== 'number' || dur <= 0) {
      throw new Error(`step ${label}: duration이 올바르지 않습니다.`);
    }

    return { n: noteNameToRow(name, scale), dur };
  });

  return {
    songId: raw.songId ?? fileId,
    title: raw.title ?? 'Untitled',
    artist: raw.artist ?? '',
    bpm: raw.bpm ?? 90,
    range,
    scale,
    notes,
  };
}

export async function loadMelodyFile(name) {
  const res = await fetch(`/screen/melodies/${name}.json`);
  if (!res.ok) throw new Error(`melodies/${name}.json 을 불러올 수 없습니다.`);
  const raw = await res.json();
  return parseMelodyJson(raw, name);
}
