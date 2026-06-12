import { VOICES } from './data.js';

const cv = document.getElementById('confetti');
const cx = cv.getContext('2d');
let parts = [];

export function initConfetti() {
  const resize = () => { cv.width = innerWidth; cv.height = innerHeight; };
  resize();
  window.addEventListener('resize', resize);
}

export function burst() {
  cv.width = innerWidth;
  cv.height = innerHeight;
  parts = Array.from({ length: 100 }, () => ({
    x: Math.random() * cv.width,
    y: cv.height * 0.4 + Math.random() * cv.height * 0.2,
    vx: (Math.random() - 0.5) * 5,
    vy: -(Math.random() * 6 + 2),
    life: 1,
    r: Math.random() * 6 + 3,
    color: VOICES[Math.floor(Math.random() * VOICES.length)].color,
  }));
  frame();
}

function frame() {
  cx.clearRect(0, 0, cv.width, cv.height);
  parts.forEach(p => {
    p.x += p.vx;
    p.y += p.vy;
    p.vy += 0.12;
    p.life -= 0.014;
    cx.save();
    cx.globalAlpha = Math.max(0, p.life);
    cx.fillStyle = p.color;
    cx.beginPath();
    cx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    cx.fill();
    cx.restore();
  });
  parts = parts.filter(p => p.life > 0);
  if (parts.length) requestAnimationFrame(frame);
  else cx.clearRect(0, 0, cv.width, cv.height);
}
