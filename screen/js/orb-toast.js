import { voiceDisplay } from './voice-labels.js';

let toastEl = null;
let hideTimer = null;

function ensureToast() {
  if (toastEl) return toastEl;
  toastEl = document.createElement('div');
  toastEl.id = 'orb-toast';
  toastEl.className = 'orb-toast';
  toastEl.setAttribute('role', 'status');
  toastEl.setAttribute('aria-live', 'polite');
  toastEl.hidden = true;
  toastEl.innerHTML = `
    <span class="orb-toast-voice"></span>
    <span class="orb-toast-kr"></span>
    <span class="orb-toast-en"></span>
  `;
  document.body.appendChild(toastEl);
  return toastEl;
}

export function showOrbPlaced(voiceName) {
  const el = ensureToast();
  const d = voiceDisplay(voiceName);
  const voiceEl = el.querySelector('.orb-toast-voice');
  const krEl = el.querySelector('.orb-toast-kr');
  const enEl = el.querySelector('.orb-toast-en');

  voiceEl.textContent = d.voice;
  voiceEl.style.display = '';
  krEl.style.display = 'none';
  enEl.style.display = 'none';

  if (d.voice === 'unknown') {
    voiceEl.style.display = 'none';
    krEl.textContent = 'unknown';
    krEl.style.display = '';
    el.style.setProperty('--orb-accent', '#888');
  } else if (d.color) {
    el.style.setProperty('--orb-accent', d.color);
  }

  el.hidden = false;
  el.classList.remove('show');
  void el.offsetWidth;
  el.classList.add('show');

  if (hideTimer) clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => { el.hidden = true; }, 320);
  }, 2800);
}
