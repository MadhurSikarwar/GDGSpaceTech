// Command palette (Ctrl/Cmd+K, "/" or the search button): jump to a page, find an object by name or NORAD number,
// look a term up in the glossary, or run an action. It is the keyboard-first way around a site with many pages and
// 70,000 catalogue objects. Pages and actions come from the shell (they depend on who is logged in).
import { get } from './api.js';
import { glossaryTerms, showTerm } from './drawers.js';
import { esc } from './ui.js';

let provider = () => ({ pages: [], actions: [] });
let root = null;          // the open palette (null when closed)
let returnTo = null;      // where keyboard focus goes back to on close
let flat = [];            // the items on screen, in order
let active = 0;
let seq = 0;              // a slow answer for an old query must not replace the answer for the current one
let objects = [];         // the object matches for the current query
let timer = 0;

const mark = (text, q) => {
  const s = String(text ?? '');
  const i = q ? s.toLowerCase().indexOf(q) : -1;
  return i < 0 ? esc(s) : `${esc(s.slice(0, i))}<mark>${esc(s.slice(i, i + q.length))}</mark>${esc(s.slice(i + q.length))}`;
};

function build(q) {
  const { pages, actions } = provider();
  const needle = q.trim().toLowerCase();
  const words = needle.split(' ').filter(Boolean);
  const hit = (...fields) => words.every((w) => fields.some((f) => String(f || '').toLowerCase().includes(w)));
  const groups = [];
  const go = pages.filter((p) => hit(p.label, p.hint)).slice(0, 8);
  if (go.length) groups.push({ title: 'Go to', items: go.map((p) => ({ label: mark(p.label, needle), hint: esc(p.hint || ''), run: () => { location.hash = p.href; } })) });
  if (words.length && objects.length) {
    groups.push({ title: 'Objects', items: objects.map((o) => ({
      label: mark(o.name, needle),
      hint: esc(`NORAD ${o.norad_id} · ${o.object_type}${o.in_earth_orbit ? '' : o.decay_date ? ' · decayed' : ' · beyond Earth orbit'}`),
      run: () => { location.hash = `#/object/${o.norad_id}`; },
    })) });
  }
  const terms = words.length ? glossaryTerms().filter((g) => hit(g.term, g.full, g.key)).slice(0, 4) : [];
  if (terms.length) groups.push({ title: 'Glossary', items: terms.map((g) => ({ label: mark(g.term, needle), hint: esc(g.full), run: () => showTerm(g.key) })) });
  const acts = actions.filter((a) => hit(a.label, a.hint)).slice(0, 5);
  if (acts.length) groups.push({ title: 'Actions', items: acts.map((a) => ({ label: mark(a.label, needle), hint: esc(a.hint || ''), run: a.run })) });
  return groups;
}

function paint() {
  if (!root) return;
  const input = root.querySelector('input');
  const groups = build(input.value);
  flat = groups.flatMap((g) => g.items);
  active = Math.min(active, Math.max(flat.length - 1, 0));
  let n = 0;
  const html = groups.map((g) => `<div class="pal-group" role="presentation">${g.title}</div>${g.items.map((it) => {
    const i = n++;
    return `<div class="pal-item" role="option" id="pal-o${i}" data-i="${i}" aria-selected="${i === active}"><span class="pal-label">${it.label}</span><span class="pal-hint">${it.hint}</span></div>`;
  }).join('')}`).join('');
  root.querySelector('.pal-list').innerHTML = html
    || '<div class="pal-empty">Nothing matches. Try a satellite name (ISS), a NORAD number (25544), a term (TCA) or a page.</div>';
  input.setAttribute('aria-activedescendant', flat.length ? `pal-o${active}` : '');
  root.querySelector('.pal-list [aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
}

async function search(q) {
  const mine = ++seq;
  try {
    const d = await get(`/objects?${new URLSearchParams({ q, status: 'all', page_size: '6', sort: 'relevance' })}`);
    if (mine !== seq || !root) return;
    objects = d.items;
  } catch {
    if (mine !== seq) return;
    objects = [];
  }
  paint();
}

function onInput() {
  const q = root.querySelector('input').value.trim();
  active = 0;
  clearTimeout(timer);
  if (q.length >= 2) timer = setTimeout(() => search(q), 160);
  else { seq += 1; objects = []; }
  paint();
}

function run(i) {
  const item = flat[i];
  if (!item) return;
  closePalette();
  item.run();
}

export function openPalette() {
  if (root) return;
  returnTo = document.activeElement;
  active = 0;
  objects = [];
  const el = document.createElement('div');
  el.className = 'pal-scrim';
  el.innerHTML = `<div class="pal" role="dialog" aria-modal="true" aria-label="Search and jump">
      <div class="pal-head"><svg aria-hidden="true"><use href="#i-search"/></svg>
        <input type="text" role="combobox" aria-expanded="true" aria-controls="palList" aria-autocomplete="list" autocomplete="off" spellcheck="false"
          placeholder="Search objects, pages and terms" aria-label="Search objects, pages and terms"><kbd>Esc</kbd></div>
      <div class="pal-list" id="palList" role="listbox"></div>
      <div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span class="r">Ctrl K or / anywhere · ? for all shortcuts</span></div>
    </div>`;
  root = el;
  document.body.appendChild(el);
  const input = el.querySelector('input');
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (flat.length) active = (active + (e.key === 'ArrowDown' ? 1 : flat.length - 1)) % flat.length;
      paint();
    } else if (e.key === 'Enter') { e.preventDefault(); run(active); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePalette(); }
    else if (e.key === 'Tab') e.preventDefault();
  });
  el.addEventListener('mousedown', (e) => { if (e.target === el) closePalette(); });
  el.addEventListener('click', (e) => { const row = e.target.closest('.pal-item'); if (row) run(Number(row.dataset.i)); });
  el.addEventListener('mousemove', (e) => {
    const row = e.target.closest('.pal-item');
    if (!row || Number(row.dataset.i) === active) return;
    active = Number(row.dataset.i);
    el.querySelectorAll('.pal-item').forEach((r) => r.setAttribute('aria-selected', String(r === row)));
    input.setAttribute('aria-activedescendant', row.id);
  });
  paint();
  el.offsetWidth;                 // a layout pass, so the transition into .on runs
  el.classList.add('on');
  input.focus();
}

export function closePalette() {
  if (!root) return;
  const gone = root;
  root = null;
  clearTimeout(timer);
  seq += 1;
  gone.classList.remove('on');
  setTimeout(() => gone.remove(), 180);
  if (returnTo && returnTo.isConnected) returnTo.focus({ preventScroll: true });
}

// provide() returns { pages: [{ label, hint, href }], actions: [{ label, hint, run }] }, read each time the palette opens
export function initPalette(provide) {
  provider = provide;
  document.addEventListener('keydown', (e) => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable;
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (root) closePalette(); else openPalette();
    } else if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey && !root) {
      e.preventDefault();
      openPalette();
    }
  });
  document.addEventListener('click', (e) => { if (e.target.closest('#searchBtn, [data-action="search"]')) openPalette(); });
}
