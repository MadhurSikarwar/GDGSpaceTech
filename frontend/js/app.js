// OrbitWatch single-page shell: hash router, role-aware navigation, alert badge.
import { get, post } from './api.js';
import { esc, toast } from './ui.js';
import { destroyAll } from './charts.js';

const RANK = { viewer: 1, analyst: 2, admin: 3 };

export const app = {
  user: null,
  _lookups: null,
  async refreshUser() {
    const { user } = await get('/auth/me');
    this.user = user;
    renderChrome();
    return user;
  },
  can(role) { return !!this.user && RANK[this.user.role] >= RANK[role]; },
  async lookups() {
    if (!this._lookups) this._lookups = await get('/lookups');
    return this._lookups;
  },
  go(hash) { location.hash = hash; },
  toast,
};

const ROUTES = [
  { re: /^\/?$/, view: 'landing', title: 'Live satellite & debris tracking', full: true, landing: true },
  { re: /^\/dashboard$/, view: 'dashboard', title: 'Dashboard' },
  { re: /^\/catalog$/, view: 'catalog', title: 'Catalogue' },
  { re: /^\/object\/(\d+)$/, view: 'object', title: 'Object' },
  { re: /^\/conjunctions$/, view: 'conjunctions', title: 'Close approaches' },
  { re: /^\/globe$/, view: 'globe', title: '3D globe', full: true },
  { re: /^\/alerts$/, view: 'alerts', title: 'My alerts', role: 'viewer' },
  { re: /^\/reports$/, view: 'reports', title: 'Reports', role: 'analyst' },
  { re: /^\/admin$/, view: 'admin', title: 'Administration', role: 'admin' },
  { re: /^\/login$/, view: 'auth', title: 'Log in', mode: 'login' },
  { re: /^\/register$/, view: 'auth', title: 'Create account', mode: 'register' },
];

const NAV = [
  ['#/globe', 'Globe'], ['#/dashboard', 'Dashboard'], ['#/catalog', 'Catalogue'], ['#/conjunctions', 'Close approaches'],
  ['#/alerts', 'My alerts', 'viewer'], ['#/reports', 'Reports', 'analyst'], ['#/admin', 'Admin', 'admin'],
];

// A UTC clock in the top bar: every time in OrbitWatch is UTC.
function tickClock() {
  const el = document.getElementById('utcClock');
  if (el) el.innerHTML = `<b>${new Date().toISOString().slice(11, 19)}</b> UTC`;
}
setInterval(tickClock, 1000);

const BELL = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/></svg>`;

function renderChrome() {
  const path = location.hash.replace(/^#/, '').split('?')[0] || '/';
  document.getElementById('nav').innerHTML = NAV
    .filter(([, , role]) => !role || app.can(role))
    .map(([href, label]) => {
      const p = href.slice(1);
      const active = path.startsWith(p);
      return `<a href="${href}" class="${active ? 'active' : ''}">${label}</a>`;
    }).join('');
  const box = document.getElementById('userbox');
  if (app.user) {
    box.innerHTML = `<span class="utc" id="utcClock"></span><a class="bell" href="#/alerts" title="My alerts" aria-label="My alerts">${BELL}<span class="count hidden" id="alertCount"></span></a>
      <div class="userchip"><strong>${esc(app.user.name)}</strong><span>${esc(app.user.role_label)}</span></div>
      <button class="btn sm ghost" id="logoutBtn">Log out</button>`;
    box.querySelector('#logoutBtn').addEventListener('click', async () => {
      await post('/auth/logout');
      app.user = null;
      toast('Logged out');
      renderChrome();
      route();
    });
    updateAlertBadge();
  } else {
    box.innerHTML = `<span class="utc" id="utcClock"></span><a class="btn sm ghost" href="#/login">Log in</a><a class="btn sm primary" href="#/register">Create account</a>`;
  }
  tickClock();
}

export async function updateAlertBadge() {
  const el = document.getElementById('alertCount');
  if (!el || !app.user) return;
  try {
    const { unacknowledged } = await get('/me/alerts/count');
    el.textContent = unacknowledged > 99 ? '99+' : unacknowledged;
    el.classList.toggle('hidden', !unacknowledged);
  } catch { /* badge is best-effort */ }
}

let cleanup = null;
let seq = 0;

async function route() {
  const [rawPath] = location.hash.replace(/^#/, '').split('?');
  const path = rawPath || '/';
  const r = ROUTES.find((x) => x.re.test(path)) || ROUTES[0];
  const params = path.match(r.re)?.slice(1) || [];
  const view = document.getElementById('view');
  const mine = ++seq;

  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  destroyAll();
  renderChrome();
  view.className = r.full ? 'view full' : 'view';
  document.body.classList.toggle('landing', !!r.landing);
  document.title = `${r.title} · OrbitWatch`;

  if (r.role && !app.can(r.role)) {
    view.innerHTML = app.user
      ? `<div class="card auth-card"><h1>Not available</h1><p class="muted">This page needs the ${r.role === 'admin' ? 'Administrator' : 'Analyst'} role. An administrator can change your role.</p></div>`
      : `<div class="card auth-card"><h1>Log in required</h1><p class="muted">Log in to see this page.</p><p><a class="btn primary" href="#/login?next=${encodeURIComponent(location.hash)}">Log in</a></p></div>`;
    return;
  }
  try {
    const mod = await import(`./views/${r.view}.js`);
    if (mine !== seq) return;
    view.innerHTML = '';
    cleanup = (await mod.render(view, { params, mode: r.mode, app })) || null;
  } catch (err) {
    console.error(err);
    if (mine === seq) view.innerHTML = `<div class="error-box">Could not load this page: ${esc(err.message)}</div>`;
  }
  if (!r.full) view.focus({ preventScroll: true });
}

window.addEventListener('hashchange', route);
setInterval(updateAlertBadge, 60000);

// Keep the page's idea of the session in step with the server's. A page that fires several
// requests gets several 401s at once; they share one check so the user sees one message.
let resyncing = null;
function resync(reason) {
  if (!resyncing) resyncing = doResync(reason).finally(() => { resyncing = null; });
  return resyncing;
}
async function doResync(reason) {
  const before = app.user ? `${app.user.user_id}:${app.user.role}` : '';
  try { await app.refreshUser(); } catch { return; }
  const after = app.user ? `${app.user.user_id}:${app.user.role}` : '';
  if (before !== after) {
    if (before && !after) toast('Your session has ended. Log in again to continue.', 'error');
    else if (reason === 'focus') toast('Your account details changed.');
    route();
  }
}
window.addEventListener('ow:unauthorized', () => resync('401'));
document.addEventListener('visibilitychange', () => { if (!document.hidden) resync('focus'); });

(async () => {
  try { await app.refreshUser(); } catch { renderChrome(); }
  route();
})();
