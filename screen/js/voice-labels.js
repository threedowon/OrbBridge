/** voice → KR/EN display names (from GET /api/voices → VOICES) */

import { VOICES } from './data.js';

function findVoice(voiceName) {
  const id = String(voiceName || '').trim();
  if (!id) return null;
  return VOICES.find(v => v.name === id) ?? null;
}

export function setVoiceLabels(_voices) {
  /* labels는 setVoices() 시 VOICES에 포함됨 — 호환용 no-op */
}

export function voiceDisplay(voiceName) {
  const id = String(voiceName || '').trim();
  const v = findVoice(id);
  const labels = v?.labels || {};
  const kr = String(labels.kr ?? v?.label_kr ?? '').trim();
  const en = String(labels.en ?? v?.label_en ?? '').trim();
  const color = v?.color || '';

  let primary = id;
  let secondary = '';
  if (kr && en) {
    primary = kr;
    secondary = en;
  } else if (kr) {
    primary = kr;
    secondary = en || id;
  } else if (en) {
    primary = en;
    secondary = id;
  }

  return { voice: id, kr, en, primary, secondary, color };
}
