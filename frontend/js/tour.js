// Guided tour (restored from OrbitalGuard, rebuilt without a third-party library).
// Steps point at real elements; a step whose element is not on screen (another
// page, a phone layout) is skipped. Esc or Skip ends the tour; completion is
// remembered per browser so the tour is only offered once.
import { esc } from './ui.js';

const KEY = 'orbitwatch.tour.v2';

const STEPS = [
  { sel: '.brand', title: 'OrbitWatch', text: 'An orbital operations console: the full space-object catalogue, the history of every orbit, close-approach screening, collision probability and decision support. Every number comes from the database.' },
  { sel: '#nav, #burger', title: 'Navigation', text: 'The globe, the mission dashboard, the catalogue, close approaches, the synthetic-debris demo lab, your alerts, analyst reports and administration (by role).' },
  { sel: '#statusBtn, #burger', title: 'System status', text: 'One glance at whether OrbitWatch is healthy: live updates, how fresh the orbital data is, and whether the scheduler is refreshing it. Click it for the details of each part.' },
  { sel: '#userbox [data-drawer="log"], #burger', title: 'Event log', text: 'A live feed of ingestion runs, screenings, AI assessments, manoeuvre decisions and demo actions, filtered to what your role may see.' },
  { sel: '#helpBtn, #burger', title: 'Help', text: 'This tour and the glossary: every term — TCA, Pc, SGP4, RIC, Δv — explained. Dotted labels anywhere in the app open the glossary at that term.' },
  { sel: '.bell, #userbox .btn.primary', title: 'Alerts', text: 'Follow satellites to be alerted (in the app, and by e-mail if you want) when a new close approach is predicted for them.' },
  { sel: '#cesium, .lp-globe', title: 'The globe', text: 'Every object with a current orbit, propagated with SGP4 and moving in real time. Filter by type and regime, pick an object to see its orbit, and replay close approaches.' },
];

let active = null;

export function tourSeen() {
  try { return localStorage.getItem(KEY) === 'done'; } catch { return true; }
}

function markSeen() {
  try { localStorage.setItem(KEY, 'done'); } catch { /* private mode: fine */ }
}

function target(step) {
  for (const s of step.sel.split(',')) {
    const el = document.querySelector(s.trim());
    if (el) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden') return el;
    }
  }
  return null;
}

export function startTour() {
  endTour();
  const steps = STEPS.filter((s) => target(s));
  if (!steps.length) return;
  const spot = document.createElement('div');
  spot.className = 'tour-spot';
  const pop = document.createElement('div');
  pop.className = 'tour-pop';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-live', 'polite');
  document.body.append(spot, pop);
  let i = 0;
  const place = () => {
    const step = steps[i];
    const el = target(step);
    if (!el) { if (i < steps.length - 1) { i += 1; place(); } else endTour(true); return; }
    const r = el.getBoundingClientRect();
    const pad = 6;
    const big = r.width > window.innerWidth * 0.8 && r.height > window.innerHeight * 0.6;
    Object.assign(spot.style, big
      ? { left: '24px', top: `${window.innerHeight * 0.18}px`, width: `${window.innerWidth - 48}px`, height: `${window.innerHeight * 0.5}px` }
      : { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px` });
    pop.innerHTML = `<div class="step">Step ${i + 1} of ${steps.length}</div><h2>${esc(step.title)}</h2><p>${esc(step.text)}</p>
      <div class="bar"><i style="width:${((i + 1) / steps.length) * 100}%"></i></div>
      <div class="actions"><button class="btn ghost sm" data-a="skip">Skip tour</button>
      <span class="row">${i ? '<button class="btn sm" data-a="prev">Back</button>' : ''}
      <button class="btn primary sm" data-a="next">${i === steps.length - 1 ? 'Finish' : 'Next'}</button></span></div>`;
    const pr = pop.getBoundingClientRect();
    const below = r.bottom + 14 + pr.height < window.innerHeight;
    let top = big ? window.innerHeight * 0.18 + 20 : below ? r.bottom + 14 : Math.max(12, r.top - pr.height - 14);
    let left = big ? 40 : Math.min(Math.max(12, r.left), window.innerWidth - pr.width - 12);
    if (window.innerWidth < 600) { left = (window.innerWidth - pr.width) / 2; top = Math.min(top, window.innerHeight - pr.height - 12); }
    Object.assign(pop.style, { top: `${top}px`, left: `${left}px` });
    pop.querySelector('[data-a="next"]').focus({ preventScroll: true });
  };
  pop.addEventListener('click', (e) => {
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'next') { if (i === steps.length - 1) endTour(true); else { i += 1; place(); } }
    if (a === 'prev') { i = Math.max(0, i - 1); place(); }
    if (a === 'skip') endTour(true);
  });
  const onKey = (e) => {
    if (e.key === 'Escape') endTour(true);
    if (e.key === 'ArrowRight') pop.querySelector('[data-a="next"]').click();
    if (e.key === 'ArrowLeft' && i) { i -= 1; place(); }
  };
  const onResize = () => place();
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', onResize);
  active = { spot, pop, onKey, onResize };
  place();
}

export function endTour(done = false) {
  if (!active) return;
  active.spot.remove();
  active.pop.remove();
  document.removeEventListener('keydown', active.onKey);
  window.removeEventListener('resize', active.onResize);
  active = null;
  if (done) markSeen();
}
