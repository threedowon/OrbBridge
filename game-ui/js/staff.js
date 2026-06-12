import { noteAt, durAt, rowCount } from './data.js';

const MARGIN_L = 24;
const MARGIN_T = 20;
const R_LARGE = 13;
const R_SMALL = 4.5;

const PAL = {
  bg: '#f5f2e9',
  track: 'rgba(0,0,0,.06)',
  ink: '#141414',
  playGlow: 'rgba(255, 175, 130, 0.5)',
};

let canvas, ctx, cellH;
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

function pathVisible(state) {
  return state.previewBeat >= 0 || hintOn;
}

function beatOnPath(state, c) {
  const { locked, melody, grid, previewBeat } = state;
  if (previewBeat >= 0 || hintOn) return true;
  if (locked[c]) return true;
  return grid[c][targetRow(melody, c)] >= 0;
}

function drawGlowPath(points) {
  if (points.length < 2) return;

  const W = canvas.width;
  const H = canvas.height;
  const jointR = 24;
  const tubeW = 48;

  if (!drawGlowPath._shape) {
    drawGlowPath._shape = document.createElement('canvas');
    drawGlowPath._blur = document.createElement('canvas');
    drawGlowPath._blur2 = document.createElement('canvas');
  }
  const shape = drawGlowPath._shape;
  const blur1 = drawGlowPath._blur;
  const blur2 = drawGlowPath._blur2;
  shape.width = blur1.width = blur2.width = W;
  shape.height = blur1.height = blur2.height = H;

  const sctx = shape.getContext('2d');
  sctx.clearRect(0, 0, W, H);
  sctx.fillStyle = '#3d9eff';
  sctx.strokeStyle = '#3d9eff';
  sctx.lineCap = 'round';
  sctx.lineJoin = 'round';
  sctx.lineWidth = tubeW;

  sctx.beginPath();
  sctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i++) sctx.lineTo(points[i].x, points[i].y);
  sctx.stroke();

  for (const p of points) {
    sctx.beginPath();
    sctx.arc(p.x, p.y, jointR, 0, Math.PI * 2);
    sctx.fill();
  }

  const b1 = blur1.getContext('2d');
  b1.clearRect(0, 0, W, H);
  b1.filter = 'blur(22px)';
  b1.drawImage(shape, 0, 0);

  const b2 = blur2.getContext('2d');
  b2.clearRect(0, 0, W, H);
  b2.filter = 'blur(40px)';
  b2.drawImage(shape, 0, 0);

  ctx.save();
  ctx.globalAlpha = 0.42;
  ctx.drawImage(blur2, 0, 0);
  ctx.globalAlpha = 0.62;
  ctx.drawImage(blur1, 0, 0);
  ctx.globalAlpha = 0.88;
  ctx.filter = 'blur(4px)';
  ctx.drawImage(shape, 0, 0);
  ctx.filter = 'none';
  ctx.globalAlpha = 0.95;
  ctx.drawImage(shape, 0, 0);
  ctx.restore();

  for (const p of points) {
    ctx.save();
    const halo = ctx.createRadialGradient(p.x, p.y, jointR * 0.2, p.x, p.y, jointR * 1.8);
    halo.addColorStop(0, 'rgba(100, 185, 255, 0.55)');
    halo.addColorStop(1, 'transparent');
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(p.x, p.y, jointR * 1.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
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

export function initStaff(el, clickCb) {
  canvas = el;
  ctx = canvas.getContext('2d');
  onCellClick = clickCb;

  const resize = () => {
    const wrap = canvas.parentElement;
    const w = Math.max(wrap.clientWidth, 320);
    cellH = 52;
    if (lastState?.melody) {
      rowN = rowCount(lastState.melody);
      const { xs, widths, n } = computeCols(lastState.melody, w - MARGIN_L * 2);
      colXs = xs;
      colWs = widths;
      beatN = n;
      canvas.width = MARGIN_L * 2 + (w - MARGIN_L * 2);
      canvas.height = MARGIN_T + cellH * rowN + 16;
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
  const wrap = canvas.parentElement;
  const w = Math.max(wrap.clientWidth, 320);
  rowN = rowCount(state.melody);
  const layout = computeCols(state.melody, w - MARGIN_L * 2);
  colXs = layout.xs;
  colWs = layout.widths;
  beatN = layout.n;
  canvas.width = MARGIN_L * 2 + (w - MARGIN_L * 2);
  canvas.height = MARGIN_T + cellH * rowN + 16;
  paint(state);
}

function paint(state) {
  const { grid, locked, melody, beat, previewBeat = -1 } = state;
  const playhead = previewBeat >= 0 ? previewBeat : beat;
  const n = melody.notes.length;
  const rows = rowCount(melody);
  const W = canvas.width;
  const H = canvas.height;
  const showPath = pathVisible({ previewBeat, ...state });

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = PAL.bg;
  ctx.fillRect(0, 0, W, H);

  ctx.strokeStyle = PAL.track;
  ctx.lineWidth = 1;
  for (let c = 0; c < n; c++) {
    const x = colCenter(c);
    ctx.beginPath();
    ctx.moveTo(x, MARGIN_T - 4);
    ctx.lineTo(x, MARGIN_T + rows * cellH + 4);
    ctx.stroke();
  }

  const pathPoints = [];
  for (let c = 0; c < n; c++) {
    if (!beatOnPath(state, c)) continue;
    pathPoints.push(cellCenter(c, targetRow(melody, c)));
  }
  if (pathPoints.length >= 2) drawGlowPath(pathPoints);

  for (let c = 0; c < n; c++) {
    const tr = targetRow(melody, c);
    const showTarget = showPath || locked[c] || grid[c][tr] >= 0;

    if (showTarget) {
      const pt = cellCenter(c, tr);
      const isPlay = c === playhead && playhead >= 0;
      drawOrb(pt.x, pt.y, R_LARGE, { glow: isPlay });
    }

    for (let r = 0; r < rows; r++) {
      if (grid[c][r] < 0) continue;
      if (r === tr && showTarget) continue;
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
