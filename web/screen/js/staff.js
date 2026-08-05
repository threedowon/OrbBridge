import {
  isPitchNote,
  isRestNote,
  melodyBeatCount,
  noteAt,
  durAt,
  rowCount,
  VOICES,
} from './data.js';
import { voiceDisplay } from './voice-labels.js';

const MARGIN_L = 24;
const MARGIN_T = 20;
const R_SMALL = 4.5;   /* 오답 — 직경 d */
const R_LARGE = 6.75;  /* 정답 — 직경 1.5d */
const CELL_H = 58;

const PAL = {
  bg: '#f5f2e9',
  track: 'rgba(0,0,0,.06)',
  ink: '#141414',
  playGlow: 'rgba(255, 175, 130, 0.5)',
};

const GLOW_FILL = 'rgba(99, 179, 237, 0.55)';
const PATH_STROKE = 18;
const PATH_BLUR = 22;
const GLOW_BLEED = 56;

let canvas, ctx, cellH;
let svgEl, pathLayer, blobLayer;
let colXs = [];
let colWs = [];
let beatN = 0;
let rowN = 8;
let hovered = null;
let hintOn = false;
let onCellClick = null;
let lastState = null;

function visibleSlots(melody) {
  return melody?.visibleSlots ?? [];
}

function displayColForSlot(melody, slot) {
  return melody?.slotToDisplayCol?.[slot] ?? -1;
}

function computeCols(melody, innerW) {
  const slots = visibleSlots(melody);
  const n = slots.length;
  if (n === 0) {
    return { xs: [MARGIN_L, MARGIN_L + innerW], widths: [innerW], n: 0 };
  }
  const durs = slots.map(slot => durAt(melody.notes[slot]));
  const sum = durs.reduce((a, b) => a + b, 0) || 1;
  const widths = durs.map(d => (d / sum) * innerW);
  const xs = [MARGIN_L];
  for (let i = 0; i < n; i++) xs.push(xs[i] + widths[i]);
  return { xs, widths, n };
}

function colCenter(displayCol) {
  return colXs[displayCol] + colWs[displayCol] / 2;
}

function cellCenter(displayCol, r) {
  return {
    x: colCenter(displayCol),
    y: MARGIN_T + r * cellH + cellH / 2,
  };
}

function cellCenterForSlot(melody, slot, r) {
  const displayCol = displayColForSlot(melody, slot);
  if (displayCol < 0) return null;
  return cellCenter(displayCol, r);
}

function targetRow(melody, slot) {
  const entry = melody.notes[slot];
  if (!entry || isRestNote(entry)) return -1;
  return noteAt(entry);
}

function correctRow(state, slot) {
  const { melody, grid } = state;
  const tr = targetRow(melody, slot);
  if (tr < 0) return -1;
  return grid[slot][tr] >= 0 ? tr : -1;
}

function beatOnPath(state, slot) {
  if (hintOn) return isPitchNote(state.melody.notes[slot]);
  return correctRow(state, slot) >= 0;
}

function showLargeAtTarget(state, slot) {
  return beatOnPath(state, slot);
}

function drawGlowPath(points) {
  if (points.length < 2) return;
  ctx.save();
  ctx.strokeStyle = GLOW_FILL;
  ctx.lineWidth = PATH_STROKE;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.filter = `blur(${PATH_BLUR}px)`;
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i].x, points[i].y);
  ctx.stroke();
  ctx.restore();
}

function initGlowLayers() {
  svgEl = document.getElementById('staff-glow');
  pathLayer = document.getElementById('path-layer');
  blobLayer = document.getElementById('glow-blob-layer');
  ensurePathBlurFilter();
}

function ensurePathBlurFilter() {
  if (!svgEl || svgEl.querySelector('#path-blur-filter')) return;
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML =
    `<filter id="path-blur-filter" x="-100%" y="-100%" width="300%" height="300%">` +
    `<feGaussianBlur stdDeviation="${PATH_BLUR}"/></filter>`;
  svgEl.prepend(defs);
}

function syncSvgSize(w, h) {
  if (!svgEl) return;
  svgEl.setAttribute('viewBox', `${-GLOW_BLEED} ${-GLOW_BLEED} ${w + GLOW_BLEED * 2} ${h + GLOW_BLEED * 2}`);
}

function pathD(points) {
  return `M ${points[0].x} ${points[0].y}` +
    points.slice(1).map(p => ` L ${p.x} ${p.y}`).join('');
}

function updateGlowPath(points, w, h) {
  if (!blobLayer) return;
  if (svgEl) syncSvgSize(w, h);

  blobLayer.replaceChildren();
  const solo = points.length === 1;
  for (const p of points) {
    const blob = document.createElement('div');
    blob.className = solo ? 'glow-blob glow-blob--solo' : 'glow-blob';
    blob.style.left = `${(p.x / w) * 100}%`;
    blob.style.top = `${(p.y / h) * 100}%`;
    blobLayer.appendChild(blob);
  }

  if (pathLayer) {
    if (points.length >= 2) {
      pathLayer.innerHTML =
        `<path d="${pathD(points)}" fill="none" stroke="${GLOW_FILL}" ` +
        `stroke-width="${PATH_STROKE}" stroke-linecap="round" stroke-linejoin="round" ` +
        `filter="url(#path-blur-filter)"/>`;
    } else {
      pathLayer.innerHTML = '';
    }
  }
}

function drawOrb(x, y, radius, { glow = false } = {}) {
  if (glow) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, radius + 14, 0, Math.PI * 2);
    ctx.fillStyle = PAL.playGlow;
    ctx.fill();
    ctx.restore();
  }

  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = PAL.ink;
  ctx.fill();
}

function drawRestMark(x, y) {
  ctx.save();
  ctx.fillStyle = 'rgba(0, 0, 0, 0.35)';
  ctx.font = '600 16px "DM Sans", system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('𝄽', x, y);
  ctx.restore();
}

function drawOrbLabel(x, y, voiceId, radius) {
  const voice = VOICES[voiceId];
  if (!voice) return;
  const { kr, en } = voiceDisplay(voice.name);
  if (!kr && !en) return;

  const lx = x + radius + 7;
  ctx.save();
  ctx.textAlign = 'left';

  if (kr) {
    ctx.textBaseline = en ? 'bottom' : 'middle';
    ctx.font = '600 13px "DM Sans", system-ui, sans-serif';
    ctx.fillStyle = PAL.ink;
    ctx.fillText(kr, lx, en ? y - 2 : y);
  }
  if (en) {
    ctx.textBaseline = 'top';
    ctx.font = 'italic 12px "Instrument Serif", Georgia, serif';
    ctx.fillStyle = 'rgba(0, 0, 0, 0.42)';
    ctx.fillText(en, lx, kr ? y + 2 : y);
  }
  ctx.restore();
}

function measureStageWidth() {
  const stage = canvas?.closest?.('.staff-stage');
  const fromDom = stage?.clientWidth ?? canvas?.parentElement?.clientWidth ?? 0;
  const fromWindow = Math.min(window.innerWidth - 48, 1200);
  // flex shrink로 DOM 폭이 비정상적으로 작으면 창 폭 기준 사용
  if (fromDom < fromWindow * 0.55) return Math.max(fromWindow, 640);
  return Math.max(fromDom, 640);
}

function computeCanvasLayout(melody, wrapW) {
  const rows = rowCount(melody);
  const innerW = Math.max(wrapW - MARGIN_L * 2, 1);
  cellH = CELL_H;
  const h = MARGIN_T + cellH * rows + 24;
  const w = MARGIN_L * 2 + innerW;
  return { w, h, rows, innerW };
}

export function initStaff(el, clickCb) {
  canvas = el;
  ctx = canvas.getContext('2d');
  onCellClick = clickCb;
  initGlowLayers();

  const resize = () => {
    const wrapW = measureStageWidth();
    if (lastState?.melody) {
      const layout = computeCanvasLayout(lastState.melody, wrapW);
      rowN = layout.rows;
      const cols = computeCols(lastState.melody, layout.innerW);
      colXs = cols.xs;
      colWs = cols.widths;
      beatN = cols.n;
      canvas.width = layout.w;
      canvas.height = layout.h;
      canvas.style.width = '100%';
      canvas.style.height = 'auto';
    }
    if (lastState) paint(lastState);
  };

  canvas.addEventListener('click', e => {
    const cell = hitTest(e);
    if (cell) onCellClick?.(cell.slot, cell.row);
  });
  canvas.addEventListener('mousemove', e => {
    hovered = hitTest(e);
    if (lastState) paint(lastState);
  });
  canvas.addEventListener('mouseleave', () => {
    hovered = null;
    if (lastState) paint(lastState);
  });

  resize();
  window.addEventListener('resize', resize);
  return {
    resize,
    setHint: v => {
      hintOn = v;
      if (lastState) paint(lastState);
    },
  };
}

function hitTest(e) {
  const r = canvas.getBoundingClientRect();
  const sx = canvas.width / r.width;
  const sy = canvas.height / r.height;
  const x = (e.clientX - r.left) * sx;
  const y = (e.clientY - r.top) * sy;
  const row = Math.floor((y - MARGIN_T) / cellH);
  if (row < 0 || row >= rowN) return null;
  let displayCol = -1;
  for (let c = 0; c < beatN; c++) {
    if (x >= colXs[c] && x < colXs[c] + colWs[c]) { displayCol = c; break; }
  }
  if (displayCol < 0 || !lastState?.melody) return null;
  const slot = lastState.melody.visibleSlots[displayCol];
  if (slot === undefined) return null;
  return { slot, row, displayCol };
}

export function draw(state) {
  if (!ctx || !state) return;
  lastState = state;
  const wrapW = measureStageWidth();
  const layout = computeCanvasLayout(state.melody, wrapW);
  rowN = layout.rows;
  const cols = computeCols(state.melody, layout.innerW);
  colXs = cols.xs;
  colWs = cols.widths;
  beatN = cols.n;
  canvas.width = layout.w;
  canvas.height = layout.h;
  canvas.style.width = '100%';
  canvas.style.height = 'auto';
  paint(state);
}

function paint(state) {
  const { grid, melody, beat, previewBeat = -1 } = state;
  const playheadSlot = previewBeat >= 0 ? previewBeat : beat;
  const slots = visibleSlots(melody);
  const rows = rowCount(melody);
  const W = canvas.width;
  const H = canvas.height;

  ctx.clearRect(0, 0, W, H);

  ctx.strokeStyle = PAL.track;
  ctx.lineWidth = 1;
  for (let displayCol = 0; displayCol < beatN; displayCol++) {
    const x = colCenter(displayCol);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, H);
    ctx.stroke();
  }

  const pathPoints = [];
  for (const slot of slots) {
    const r = correctRow(state, slot);
    if (r < 0) continue;
    const pt = cellCenterForSlot(melody, slot, r);
    if (pt) pathPoints.push(pt);
  }
  drawGlowPath(pathPoints);
  updateGlowPath(pathPoints, W, H);

  if (playheadSlot >= 0 && playheadSlot < melodyBeatCount(melody)) {
    const displayCol = displayColForSlot(melody, playheadSlot);
    if (displayCol >= 0) {
      ctx.save();
      ctx.fillStyle = previewBeat >= 0
        ? 'rgba(255, 175, 130, 0.22)'
        : 'rgba(255, 175, 130, 0.14)';
      ctx.fillRect(colXs[displayCol], MARGIN_T, colWs[displayCol], rows * cellH);
      ctx.restore();
    }
  }

  for (const slot of slots) {
    const entry = melody.notes[slot];
    const displayCol = displayColForSlot(melody, slot);
    if (displayCol < 0) continue;
    const tr = targetRow(melody, slot);
    const midY = MARGIN_T + (rows * cellH) / 2;

    if (isRestNote(entry)) {
      drawRestMark(colCenter(displayCol), midY);
      continue;
    }

    if (tr < 0) continue;

    if (showLargeAtTarget(state, slot)) {
      const pt = cellCenter(displayCol, tr);
      const isPlay = slot === playheadSlot && playheadSlot >= 0;
      const voiceId = grid[slot][tr];
      drawOrb(pt.x, pt.y, R_LARGE, { glow: isPlay });
      if (voiceId >= 0) drawOrbLabel(pt.x, pt.y, voiceId, R_LARGE);
    }

    for (let r = 0; r < rows; r++) {
      if (grid[slot][r] < 0) continue;
      if (r === tr && showLargeAtTarget(state, slot)) continue;
      const pt = cellCenter(displayCol, r);
      const voiceId = grid[slot][r];
      drawOrb(pt.x, pt.y, R_SMALL);
      drawOrbLabel(pt.x, pt.y, voiceId, R_SMALL);
    }
  }

  if (hovered) {
    const { slot, row } = hovered;
    const pt = cellCenterForSlot(melody, slot, row);
    if (!pt) return;
    ctx.save();
    ctx.globalAlpha = 0.12;
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, R_LARGE + 6, 0, Math.PI * 2);
    ctx.fillStyle = PAL.ink;
    ctx.fill();
    ctx.restore();
  }
}

export function flash(slot, row, ok) {
  if (!lastState?.melody) return;
  const pt = cellCenterForSlot(lastState.melody, slot, row);
  if (!pt) return;
  let f = 0;
  const run = () => {
    f++;
    const a = 1 - f / 8;
    ctx.save();
    ctx.globalAlpha = a;
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, R_LARGE + f * 2, 0, Math.PI * 2);
    ctx.strokeStyle = ok ? 'rgba(70, 130, 255, 0.9)' : 'rgba(255, 120, 90, 0.85)';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
    if (f < 8) requestAnimationFrame(run);
  };
  run();
}
