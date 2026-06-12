import { noteAt, durAt, rowCount } from './data.js';

const MARGIN_L = 24;
const MARGIN_T = 20;
const R_SMALL = 4.5;   /* 오답 — 직경 d */
const R_LARGE = 6.75;  /* 정답 — 직경 1.5d */

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

function computeCols(melody, innerW) {
  const notes = melody.notes;
  const n = notes.length;
  const durs = notes.map(durAt);
  const sum = durs.reduce((a, b) => a + b, 0);
  const widths = durs.map(d => (d / sum) * innerW);
  const xs = [MARGIN_L];
  for (let i = 0; i < n; i++) xs.push(xs[i] + widths[i]);
  return { xs, widths, n };
}

function colCenter(c) {
  return colXs[c] + colWs[c] / 2;
}

function cellCenter(c, r) {
  return {
    x: colCenter(c),
    y: MARGIN_T + r * cellH + cellH / 2,
  };
}

function targetRow(melody, c) {
  return noteAt(melody.notes[c]);
}

function correctRow(state, c) {
  const { melody, grid } = state;
  const tr = targetRow(melody, c);
  return grid[c][tr] >= 0 ? tr : -1;
}

function beatOnPath(state, c) {
  if (hintOn) return true;
  return correctRow(state, c) >= 0;
}

function showLargeAtTarget(state, c) {
  return beatOnPath(state, c);
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
  if (!pathLayer || !blobLayer) return;
  syncSvgSize(w, h);

  blobLayer.replaceChildren();
  for (const p of points) {
    const blob = document.createElement('div');
    blob.className = 'glow-blob';
    const padW = w + GLOW_BLEED * 2;
    const padH = h + GLOW_BLEED * 2;
    blob.style.left = `${((p.x + GLOW_BLEED) / padW) * 100}%`;
    blob.style.top = `${((p.y + GLOW_BLEED) / padH) * 100}%`;
    blobLayer.appendChild(blob);
  }

  if (points.length >= 2) {
    pathLayer.innerHTML =
      `<path d="${pathD(points)}" fill="none" stroke="${GLOW_FILL}" ` +
      `stroke-width="${PATH_STROKE}" stroke-linecap="round" stroke-linejoin="round" ` +
      `filter="url(#path-blur-filter)"/>`;
  } else {
    pathLayer.innerHTML = '';
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

function computeCanvasLayout(melody, wrapW) {
  const rows = rowCount(melody);
  const innerW = wrapW - MARGIN_L * 2;
  const targetH = Math.min(window.innerHeight * 0.62, 560);
  cellH = Math.max(46, Math.min(58, (targetH - MARGIN_T - 24) / rows));
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
    const wrap = canvas.parentElement?.parentElement ?? canvas.parentElement;
    const wrapW = Math.max(wrap.clientWidth, 320);
    if (lastState?.melody) {
      const layout = computeCanvasLayout(lastState.melody, wrapW);
      rowN = layout.rows;
      const cols = computeCols(lastState.melody, layout.innerW);
      colXs = cols.xs;
      colWs = cols.widths;
      beatN = cols.n;
      canvas.width = layout.w;
      canvas.height = layout.h;
    }
    if (lastState) paint(lastState);
  };

  canvas.addEventListener('click', e => {
    const cell = hitTest(e);
    if (cell) onCellClick?.(cell.col, cell.row);
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
  let col = -1;
  for (let c = 0; c < beatN; c++) {
    if (x >= colXs[c] && x < colXs[c] + colWs[c]) { col = c; break; }
  }
  if (col < 0) return null;
  return { col, row };
}

export function draw(state) {
  if (!ctx || !state) return;
  lastState = state;
  const wrap = canvas.parentElement?.parentElement ?? canvas.parentElement;
  const wrapW = Math.max(wrap.clientWidth, 320);
  const layout = computeCanvasLayout(state.melody, wrapW);
  rowN = layout.rows;
  const cols = computeCols(state.melody, layout.innerW);
  colXs = cols.xs;
  colWs = cols.widths;
  beatN = cols.n;
  canvas.width = layout.w;
  canvas.height = layout.h;
  paint(state);
}

function paint(state) {
  const { grid, locked, melody, beat, previewBeat = -1 } = state;
  const playhead = previewBeat >= 0 ? previewBeat : beat;
  const n = melody.notes.length;
  const rows = rowCount(melody);
  const W = canvas.width;
  const H = canvas.height;

  ctx.clearRect(0, 0, W, H);

  ctx.strokeStyle = PAL.track;
  ctx.lineWidth = 1;
  for (let c = 0; c < n; c++) {
    const x = colCenter(c);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, H);
    ctx.stroke();
  }

  const pathPoints = [];
  for (let c = 0; c < n; c++) {
    const r = correctRow(state, c);
    if (r < 0) continue;
    pathPoints.push(cellCenter(c, r));
  }
  drawGlowPath(pathPoints);
  updateGlowPath(pathPoints, W, H);

  if (playhead >= 0 && playhead < n) {
    ctx.save();
    ctx.fillStyle = previewBeat >= 0
      ? 'rgba(255, 175, 130, 0.22)'
      : 'rgba(255, 175, 130, 0.14)';
    ctx.fillRect(colXs[playhead], MARGIN_T, colWs[playhead], rows * cellH);
    ctx.restore();
  }

  for (let c = 0; c < n; c++) {
    const tr = targetRow(melody, c);

    if (showLargeAtTarget(state, c)) {
      const pt = cellCenter(c, tr);
      const isPlay = c === playhead && playhead >= 0;
      drawOrb(pt.x, pt.y, R_LARGE, { glow: isPlay });
    }

    for (let r = 0; r < rows; r++) {
      if (grid[c][r] < 0) continue;
      if (r === tr && showLargeAtTarget(state, c)) continue;
      const pt = cellCenter(c, r);
      drawOrb(pt.x, pt.y, R_SMALL);
    }
  }

  if (hovered) {
    const { col, row } = hovered;
    const pt = cellCenter(col, row);
    ctx.save();
    ctx.globalAlpha = 0.12;
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, R_LARGE + 6, 0, Math.PI * 2);
    ctx.fillStyle = PAL.ink;
    ctx.fill();
    ctx.restore();
  }
}

export function flash(col, row, ok) {
  const pt = cellCenter(col, row);
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
