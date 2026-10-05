import { state, subscribe } from '../state.js';
import { icons } from '../icons.js';
import { fmtClock } from '../utils.js';

const SERVICES = [
  { key: 'tracking', label: 'TRK', port: 8000 },
  { key: 'risk', label: 'RSK', port: 8001 },
  { key: 'maneuver', label: 'MNV', port: 8002 },
  { key: 'optimizer', label: 'OPT', port: 8003 },
];

export function initTopbar({ onNavigate, onDemoInject, onToggleLog }) {
  document.getElementById('brandMark').innerHTML = icons.satellite;
  document.getElementById('demoBtnIcon').innerHTML = icons.alertTriangle;
  document.getElementById('logToggleIcon').innerHTML = icons.terminal;

  const strip = document.getElementById('serviceStrip');
  if (strip) strip.innerHTML = '';

  document.getElementById('navTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('.nav-tab');
    if (!btn) return;
    onNavigate(btn.dataset.view);
  });

  document.getElementById('demoBtn').addEventListener('click', onDemoInject);
  document.getElementById('logToggle').addEventListener('click', onToggleLog);

  const fmtUTCStr = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  const fmtISTStr = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

  const updateClock = () => {
    const now = new Date();
    document.getElementById('missionClock').innerHTML = `<b>${fmtISTStr.format(now)}</b> IST <span style="opacity:0.4; margin:0 4px">|</span> <b>${fmtUTCStr.format(now)}</b> GMT`;
  };

  setInterval(updateClock, 1000);
  updateClock();

  subscribe((topic) => {
    if (topic === 'serviceStatus') renderServiceChips();
    if (topic === 'conjunctions') renderCounts();
    if (topic === 'view') setActiveTab();
  });

  renderServiceChips();
  renderCounts();
  setActiveTab();
}

function renderServiceChips() {
  // Service status pills removed from UI per design cleanup
}

function renderCounts() {
  const alertCount = state.conjunctions.length;
  const badge = document.getElementById('conjCount');
  badge.textContent = String(alertCount);
  badge.classList.toggle('has-alert', alertCount > 0);
}

function setActiveTab() {
  document.querySelectorAll('.nav-tab').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${state.view}`));
}

export function toast(title, message, kind = 'info') {
  const stack = document.getElementById('toastStack');
  const el = document.createElement('div');
  el.className = `toast ${kind === 'warn' ? 'warn' : kind === 'crit' ? 'crit' : ''}`;
  el.innerHTML = `<div class="toast-title">${title}</div><div>${message}</div>`;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .25s ease, transform .25s ease';
    el.style.opacity = '0';
    el.style.transform = 'translateX(14px)';
    setTimeout(() => el.remove(), 260);
  }, 4200);
}

export function renderLog() {
  const list = document.getElementById('logList');
  document.getElementById('logCount').textContent = `${state.log.length} EVENTS`;
  list.innerHTML = state.log.slice(0, 80).map((e) => {
    const cls = e.kind === 'warn' ? 'evt-warn' : e.kind === 'crit' ? 'evt-crit' : e.kind === 'ok' ? 'evt-ok' : '';
    return `<div class="log-entry ${cls}"><span class="lt">${fmtClock(e.t)}</span><span class="lm">${e.message}</span></div>`;
  }).join('');
}

export function initLogDrawer() {
  const drawer = document.getElementById('logDrawer');
  subscribe((topic) => { if (topic === 'log') renderLog(); });
  return {
    toggle() { drawer.classList.toggle('open'); },
  };
}
