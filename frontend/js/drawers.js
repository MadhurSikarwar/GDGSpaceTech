// Side drawers restored from OrbitalGuard: the space-domain glossary and the live event log.
import { CATEGORIES, GLOSSARY } from './glossary-data.js';
import { live, loadRecent, onLive } from './live.js';
import { esc } from './ui.js';

const drawers = {};
let openName = null;

function setOpen(name, open) {
  const d = drawers[name];
  if (!d) return;
  d.el.classList.toggle('open', open);
  d.el.setAttribute('aria-hidden', String(!open));
  document.querySelectorAll(`[data-drawer="${name}"]`).forEach((b) => b.setAttribute('aria-expanded', String(open)));
  if (open) {
    for (const other of Object.keys(drawers)) if (other !== name) setOpen(other, false);
    openName = name;
    d.onOpen?.();
    setTimeout(() => d.el.querySelector('input, button')?.focus({ preventScroll: true }), 60);
  } else if (openName === name) openName = null;
}

export function toggleDrawer(name, force) {
  const isOpen = drawers[name]?.el.classList.contains('open');
  setOpen(name, force === undefined ? !isOpen : force);
}

document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openName) setOpen(openName, false); });

// ---------------------------------------------------------------- glossary
export function initGlossary() {
  const el = document.getElementById('glossaryDrawer');
  el.innerHTML = `<div class="dr-head"><div class="spread"><h2>Glossary</h2>
      <button class="icon-btn" data-close aria-label="Close glossary"><svg><use href="#i-close"/></svg></button></div>
      <div class="sub">${GLOSSARY.length} terms used across OrbitWatch</div>
      <input type="search" id="glSearch" placeholder="Search terms — TCA, Pc, SGP4…" style="margin-top:10px" aria-label="Search the glossary">
      <div class="dr-tools chips" id="glCats"><button class="chip" aria-pressed="true" data-c="">All</button>
      ${CATEGORIES.map((c) => `<button class="chip" aria-pressed="false" data-c="${c}">${c.toLowerCase()}</button>`).join('')}</div></div>
    <div class="dr-body" id="glList"></div>`;
  drawers.glossary = { el };
  const list = el.querySelector('#glList');
  const search = el.querySelector('#glSearch');
  let cat = '';
  const paint = () => {
    const q = search.value.trim().toLowerCase();
    const items = GLOSSARY.filter((g) => (!cat || g.cat === cat)
      && (!q || `${g.term} ${g.full} ${g.desc} ${g.detail}`.toLowerCase().includes(q)));
    list.innerHTML = items.map((g) => `<div class="gl-item" id="gl-${g.key}"><div class="t"><b>${esc(g.term)}</b><span>${g.cat}</span></div>
      <div class="full">${esc(g.full)}</div><p>${esc(g.desc)}</p>${g.detail ? `<div class="detail">${esc(g.detail)}</div>` : ''}</div>`).join('')
      || '<div class="empty"><strong>No term matches</strong></div>';
  };
  search.addEventListener('input', paint);
  el.querySelector('#glCats').addEventListener('click', (e) => {
    const b = e.target.closest('[data-c]');
    if (!b) return;
    cat = b.dataset.c;
    el.querySelectorAll('#glCats .chip').forEach((c) => c.setAttribute('aria-pressed', String(c === b)));
    paint();
  });
  el.querySelector('[data-close]').addEventListener('click', () => setOpen('glossary', false));
  paint();
  // telemetry labels with data-term="key" open the glossary at that entry
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-term]');
    if (!t || t.closest('#glossaryDrawer')) return;
    e.preventDefault();
    showTerm(t.dataset.term);
  });
}

export function showTerm(key) {
  const el = drawers.glossary?.el;
  if (!el) return;
  el.querySelector('#glSearch').value = '';
  el.querySelector('#glCats [data-c=""]').click();
  setOpen('glossary', true);
  const item = el.querySelector(`#gl-${CSS.escape(key)}`);
  if (item) {
    item.scrollIntoView({ block: 'center' });
    item.classList.remove('flash');
    void item.offsetWidth;
    item.classList.add('flash');
  }
}

// A glossary-linked label: <span class="term" data-term="pc">Pc</span>
export const term = (key, label) => `<span class="term" data-term="${esc(key)}" tabindex="0" role="button">${esc(label)}</span>`;

// ---------------------------------------------------------------- event log
const CAT_LABEL = { data: 'data', screening: 'screening', alert: 'alert', agent: 'AI agent', maneuver: 'manoeuvre',
  demo: 'synthetic demo', auth: 'account', admin: 'admin', system: 'system', job: 'job' };

function entityLink(r) {
  if (r.entity_type === 'assessment') return `#/conjunctions?assessment=${encodeURIComponent(r.entity_id)}`;
  if (r.entity_type === 'demo_event') return '#/demo';
  if (r.category === 'screening') return '#/conjunctions';
  if (r.category === 'data') return '#/dashboard';
  return null;
}

function row(r, fresh = false) {
  const t = new Date((r.occurred_at || '').endsWith('Z') ? r.occurred_at : `${r.occurred_at}Z`);
  const hhmm = Number.isNaN(t.getTime()) ? '' : t.toISOString().slice(11, 19);
  const day = Number.isNaN(t.getTime()) ? '' : t.toISOString().slice(5, 10);
  const link = entityLink(r);
  const by = r.detail && typeof r.detail === 'object' && r.detail.by ? ` · by ${esc(r.detail.by)}` : '';
  return `<div class="ev-row sev-${esc(r.severity)} ${fresh ? 'new' : ''}"><div class="tm">${hhmm}<br>${day}</div>
    <div><div class="cat">${r.category === 'demo' ? '<span class="tag syn" style="padding:0 5px;font-size:9px">SYN</span>' : ''}${esc(CAT_LABEL[r.category] || r.category)}${r.severity === 'warning' || r.severity === 'critical' ? ` · ${esc(r.severity)}` : ''}</div>
    <div class="msg">${link ? `<a href="${link}">${esc(r.message)}</a>` : esc(r.message)}${by}</div></div></div>`;
}

export function initEventLog() {
  const el = document.getElementById('logDrawer');
  el.innerHTML = `<div class="dr-head"><div class="spread"><h2>Event log</h2>
      <button class="icon-btn" data-close aria-label="Close event log"><svg><use href="#i-close"/></svg></button></div>
      <div class="sub" id="logSub">Live feed of what OrbitWatch is doing</div>
      <div class="dr-tools chips" id="logCats"><button class="chip" aria-pressed="true" data-c="">All</button>
      ${['data', 'screening', 'agent', 'maneuver', 'demo', 'system'].map((c) => `<button class="chip" aria-pressed="false" data-c="${c}">${CAT_LABEL[c]}</button>`).join('')}</div></div>
    <div class="dr-body" id="logList"><div class="loading">Loading</div></div>`;
  let cat = '';
  let loaded = false;
  const list = el.querySelector('#logList');
  const paint = () => {
    const items = live.recent.filter((r) => !cat || r.category === cat);
    list.innerHTML = items.map((r) => row(r)).join('') || '<div class="empty"><strong>Nothing logged yet</strong>Jobs, screenings, decisions and demo actions appear here as they happen.</div>';
  };
  drawers.log = { el, onOpen: async () => { if (!loaded) { loaded = true; await loadRecent(80); } paint(); } };
  el.querySelector('[data-close]').addEventListener('click', () => setOpen('log', false));
  el.querySelector('#logCats').addEventListener('click', (e) => {
    const b = e.target.closest('[data-c]');
    if (!b) return;
    cat = b.dataset.c;
    el.querySelectorAll('#logCats .chip').forEach((c) => c.setAttribute('aria-pressed', String(c === b)));
    paint();
  });
  const sub = el.querySelector('#logSub');
  onLive((type, data) => {
    if (type === 'log' && el.classList.contains('open') && (!cat || data.category === cat)) {
      list.insertAdjacentHTML('afterbegin', row(data, true));
      list.querySelector('.empty')?.remove();
    }
    if (type === 'reset') { loaded = false; if (el.classList.contains('open')) drawers.log.onOpen(); }
    if (type === 'state') {
      sub.textContent = { live: 'Live: new events appear as they happen', polling: 'Polling every 30 s (live stream unavailable)',
        connecting: 'Connecting to the live stream…', offline: 'Offline: cannot reach the server' }[data] || '';
    }
  });
}
