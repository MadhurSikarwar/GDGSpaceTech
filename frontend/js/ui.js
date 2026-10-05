// Small DOM and formatting helpers shared by every view.

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const NUM = new Intl.NumberFormat('en-US');
export const fmt = {
  int: (n) => (n == null ? '—' : NUM.format(Math.round(Number(n)))),
  num: (n, d = 1) => (n == null || Number.isNaN(Number(n)) ? '—'
    : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })),
  km: (n, d = 1) => (n == null ? '—' : `${fmt.num(n, d)} km`),
  // API datetimes are UTC ("...Z"); orbital work is always shown in UTC.
  dt: (iso) => {
    if (!iso) return '—';
    const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
    if (Number.isNaN(d.getTime())) return esc(iso);
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  },
  date: (iso) => (iso ? String(iso).slice(0, 10) : '—'),
  rel: (iso) => {
    if (!iso) return '';
    const t = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
    const s = (t - Date.now()) / 1000;
    const a = Math.abs(s);
    const unit = a < 3600 ? [Math.round(a / 60), 'min'] : a < 86400 * 2 ? [Math.round(a / 3600), 'h']
      : [Math.round(a / 86400), 'd'];
    return s >= 0 ? `in ${unit[0]} ${unit[1]}` : `${unit[0]} ${unit[1]} ago`;
  },
};

export const TYPE_SLUG = { 'Payload': 'payload', 'Rocket Body': 'rocket', 'Debris': 'debris', 'Unknown': 'unknown' };
export const TYPES = ['Payload', 'Rocket Body', 'Debris', 'Unknown'];
export const RISKS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

export const typeTag = (t) => (t ? `<span class="tag t-${TYPE_SLUG[t] || 'unknown'}"><i></i>${esc(t)}</span>` : '—');
// Risk is a status: shape + label, never colour alone.
export const riskBadge = (r) => (r ? `<span class="badge risk-${esc(r)}"><i></i>${esc(r)}</span>` : '—');
export const objLink = (id, name) => `<a href="#/object/${Number(id)}">${esc(name ?? id)}</a>`;

export const loading = (msg = 'Loading…') => `<div class="loading">${esc(msg)}</div>`;
export const empty = (title, body = '') => `<div class="empty"><strong>${esc(title)}</strong>${esc(body)}</div>`;
export const errorBox = (err) => `<div class="error-box">${esc(err?.message || err)}</div>`;

export function table(columns, rows, { onRow, rowClass } = {}) {
  const head = columns.map((c) => `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`).join('');
  const body = rows.map((r, i) => `<tr data-i="${i}" class="${onRow ? 'clickable' : ''} ${rowClass ? rowClass(r) : ''}">${
    columns.map((c) => `<td class="${c.num ? 'num' : ''} ${c.cls || ''}">${c.render ? c.render(r) : esc(r[c.key] ?? '—')}</td>`).join('')
  }</tr>`).join('');
  const el = h(`<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
  if (onRow) {
    el.querySelector('tbody').addEventListener('click', (e) => {
      if (e.target.closest('a, button, input, select')) return;
      const tr = e.target.closest('tr');
      if (tr) onRow(rows[Number(tr.dataset.i)], e);
    });
  }
  return el;
}

export function pager(total, page, size, onPage) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total ? (page - 1) * size + 1 : 0;
  const el = h(`<div class="pager">
    <span>${fmt.int(from)}–${fmt.int(Math.min(total, page * size))} of ${fmt.int(total)}</span>
    <span class="row">
      <button class="btn sm" data-p="${page - 1}" ${page <= 1 ? 'disabled' : ''}>Previous</button>
      <span>Page ${fmt.int(page)} of ${fmt.int(pages)}</span>
      <button class="btn sm" data-p="${page + 1}" ${page >= pages ? 'disabled' : ''}>Next</button>
    </span></div>`);
  el.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-p]');
    if (b && !b.disabled) onPage(Number(b.dataset.p));
  });
  return el;
}

export function toast(msg, kind = 'info') {
  const box = document.getElementById('toasts');
  const el = h(`<div class="toast ${kind === 'error' ? 'error' : ''}">${esc(msg)}</div>`);
  box.appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 6000 : 3500);
}

export function debounce(fn, ms = 300) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// Read/write view state in the hash query, so Back/Forward and links keep filters.
export function hashQuery() {
  const [, q = ''] = location.hash.split('?');
  return Object.fromEntries(new URLSearchParams(q));
}

export function setHashQuery(params, { replace = true } = {}) {
  const [path] = location.hash.split('?');
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, v);
  const next = `${path || '#/'}${p.toString() ? '?' + p : ''}`;
  if (replace) history.replaceState(null, '', next);
  else location.hash = next;
}

export function modal(title, bodyHtml, { submitLabel = 'Save', onSubmit } = {}) {
  const dlg = h(`<dialog class="modal"><form method="dialog">
      <h2>${esc(title)}</h2><div class="modal-body">${bodyHtml}</div>
      <div class="form-error"></div>
      <div class="actions"><button class="btn ghost" value="cancel" type="button" data-close>Cancel</button>
      <button class="btn primary" type="submit">${esc(submitLabel)}</button></div></form></dialog>`);
  document.body.appendChild(dlg);
  const form = dlg.querySelector('form');
  dlg.querySelector('[data-close]').addEventListener('click', () => dlg.close());
  dlg.addEventListener('close', () => dlg.remove());
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await onSubmit(new FormData(form), dlg);
      dlg.close();
    } catch (err) {
      dlg.querySelector('.form-error').textContent = err.message;
    }
  });
  dlg.showModal();
  return dlg;
}

export function isoInput(date) {
  return date.toISOString().slice(0, 10);
}

// ---- provenance -----------------------------------------------------------
const toDate = (iso) => (iso ? new Date(String(iso).endsWith('Z') || String(iso).includes('+') ? iso : `${iso}Z`) : null);

// "4.2 h", "1.6 d": age of an instant relative to now.
export function age(iso) {
  const d = toDate(iso);
  if (!d || Number.isNaN(d.getTime())) return '—';
  const h = Math.abs(Date.now() - d.getTime()) / 3600000;
  return h < 1 ? `${Math.round(h * 60)} min` : h < 48 ? `${fmt.num(h, 1)} h` : `${fmt.num(h / 24, 1)} d`;
}

// Where a number came from: source, when it was downloaded, how old the data itself is, the model used.
export function prov({ src, fetched, epoch, model, note } = {}) {
  const parts = [];
  if (src) parts.push(`<span class="src">${esc(src)}</span>`);
  if (fetched) parts.push(`fetched ${esc(fmt.rel(fetched))}`);
  if (epoch) parts.push(`epoch age ${esc(age(epoch))}`);
  if (model) parts.push(esc(model));
  if (note) parts.push(esc(note));
  return `<span class="prov">${parts.join('<span class="sep">·</span>')}</span>`;
}

export const unavailable = (title, body = '') => `<div class="unavailable"><strong>${esc(title)}</strong>${body}</div>`;
export const synTag = () => '<span class="tag syn" title="Synthetic demo data, not a real object">SYNTHETIC</span>';
export const simTag = () => '<span class="tag sim" title="No command uplink exists: execution is simulated">SIMULATED</span>';
