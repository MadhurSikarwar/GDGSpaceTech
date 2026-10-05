// OrbitWatch single-page shell: hash router, role-aware navigation, system status strip,
// live updates, mobile navigation, drawers (glossary, event log), guided tour and alert badge.
import { get, post } from './api.js';
import { destroyAll } from './charts.js';
import { initEventLog, initGlossary, toggleDrawer } from './drawers.js';
import { connect, live, onLive, reconnect } from './live.js';
import { startTour, tourSeen } from './tour.js';
import { esc, fmt, toast } from './ui.js';

const RANK = { viewer: 1, analyst: 2, admin: 3 };

export const app = {
  user: null,
  system: null,
  _lookups: null,
  async refreshUser() {
    const { user } = await get('/auth/me');
    const changed = (this.user?.user_id || null) !== (user?.user_id || null);
    this.user = user;
    renderChrome();
    if (changed && booted) reconnect();
    return user;
  },
  can(role) { return !!this.user && RANK[this.user.role] >= RANK[role]; },
  async lookups() {
    if (!this._lookups) this._lookups = await get('/lookups');
    return this._lookups;
  },
  go(hash) { location.hash = hash; },
  toast,
  live,
};
let booted = false;

const ROUTES = [
  { re: /^\/?$/, view: 'landing', title: 'Orbital operations console', full: true, landing: true },
  { re: /^\/dashboard$/, view: 'dashboard', title: 'Mission dashboard' },
  { re: /^\/catalog$/, view: 'catalog', title: 'Catalogue' },
  { re: /^\/object\/(\d+)$/, view: 'object', title: 'Object' },
  { re: /^\/conjunctions$/, view: 'conjunctions', title: 'Close approaches' },
  { re: /^\/globe$/, view: 'globe', title: '3D globe', full: true },
  { re: /^\/demo$/, view: 'demo', title: 'Synthetic-debris demo lab' },
  { re: /^\/alerts$/, view: 'alerts', title: 'My alerts', role: 'viewer' },
  { re: /^\/account$/, view: 'account', title: 'Account', role: 'viewer' },
  { re: /^\/reports$/, view: 'reports', title: 'Reports', role: 'analyst' },
  { re: /^\/admin$/, view: 'admin', title: 'Administration', role: 'admin' },
  { re: /^\/login$/, view: 'auth', title: 'Log in', mode: 'login' },
  { re: /^\/register$/, view: 'auth', title: 'Create account', mode: 'register' },
  { re: /^\/forgot$/, view: 'auth', title: 'Reset your password', mode: 'forgot' },
  { re: /^\/reset$/, view: 'auth', title: 'Choose a new password', mode: 'reset' },
];

const NAV = [
  ['#/globe', 'Globe'], ['#/dashboard', 'Dashboard'], ['#/catalog', 'Catalogue'], ['#/conjunctions', 'Close approaches'],
  ['#/demo', 'Demo lab'], ['#/alerts', 'My alerts', 'viewer'], ['#/reports', 'Reports', 'analyst'], ['#/admin', 'Admin', 'admin'],
];

const icon = (id) => `<svg><use href="#i-${id}"/></svg>`;

// ------------------------------------------------------------------ clock
function tickClock() {
  const t = new Date().toISOString().slice(11, 19);
  document.querySelectorAll('[data-utc]').forEach((el) => { el.innerHTML = `<b>${t}</b> UTC`; });
}
setInterval(tickClock, 1000);

// ------------------------------------------------------------------ system status
// One labelled button in the top bar ("All systems normal" or what is wrong); it opens a panel that says
// what each part is and what state it is in.
const LIVE_TEXT = {
  live: ['ok', 'Connected', 'Pages update the moment something happens (live stream).'],
  polling: ['warn', 'Polling', 'The live stream is unavailable; pages check for news every 30 s.'],
  connecting: ['warn', 'Connecting', 'Connecting to the live stream…'],
  offline: ['bad', 'Offline', 'Cannot reach the OrbitWatch server.'],
};

function systemSummary() {
  const s = app.system;
  if (!s) return null;
  const gp = s.sources.find((x) => x.source_key === 'celestrak_gp');
  const st = s.sources.find((x) => x.source_key === 'spacetrack_gp');
  const fresh = [gp, st].filter(Boolean).filter((x) => x.freshness === 'fresh');
  const newest = [gp, st].filter((x) => x && x.last_success_at).map((x) => x.last_success_at).sort().pop();
  const hb = s.scheduler.heartbeat || {};
  const ingest = (s.jobs || []).find((j) => j.job_id === 'ingest_and_screen');
  return {
    dataState: fresh.length ? 'ok' : newest ? 'warn' : 'bad',
    dataText: newest ? `newest download ${fmt.rel(newest)}` : 'never downloaded',
    tracked: s.coverage.with_orbit, catalogued: s.coverage.objects_in_orbit, coverage: s.coverage.coverage_pct,
    schedState: s.scheduler.running ? 'ok' : 'bad',
    schedText: s.scheduler.running
      ? `running${ingest?.next_run_at ? ` · next data refresh ${fmt.rel(ingest.next_run_at)}` : ''}`
      : hb.heartbeat_at ? `stopped ${fmt.rel(hb.heartbeat_at)}: data will not refresh on its own` : 'not started: data will not refresh on its own',
    spacetrack: s.spacetrack_configured, email: s.email_delivery,
  };
}

function overall() {
  const s = systemSummary();
  const [liveState] = LIVE_TEXT[live.state] || LIVE_TEXT.connecting;
  if (!s) return ['', 'Checking systems'];
  const problems = [];
  if (s.schedState !== 'ok') problems.push('Scheduler stopped');
  if (s.dataState === 'bad') problems.push('No orbital data');
  else if (s.dataState === 'warn') problems.push('Data getting old');
  if (liveState === 'bad') problems.push('Offline');
  else if (liveState === 'warn' && live.state !== 'connecting') problems.push('Live updates polling');
  if (!problems.length) return ['ok', 'All systems normal'];
  const worst = problems.some((p) => p.startsWith('Scheduler') || p.startsWith('No orbital') || p === 'Offline') ? 'bad' : 'warn';
  return [worst, problems.length === 1 ? problems[0] : `${problems.length} issues`];
}

function renderSysbar() {
  const bar = document.getElementById('sysbar');
  const [cls, text] = overall();
  bar.innerHTML = `<button class="status-pill" id="statusBtn" aria-haspopup="dialog" aria-expanded="false" title="System status">
      <span class="dot ${cls}"></span><span class="lbl">${esc(text)}</span><span class="caret">▾</span></button>
    <span class="utc" data-utc></span>`;
  bar.querySelector('#statusBtn').addEventListener('click', (e) => { e.stopPropagation(); toggleStatusPanel(); });
  if (document.getElementById('statusPanel')) paintStatusPanel();
  renderMobileStatus();
  tickClock();
}

function statusRows() {
  const s = systemSummary();
  const [lcls, ltext, ldesc] = LIVE_TEXT[live.state] || LIVE_TEXT.connecting;
  if (!s) return `<li><span class="dot"></span><div><b>Loading…</b></div></li>`;
  return `
    <li><span class="dot ${lcls}"></span><div><b>Live updates · ${esc(ltext)}</b><span>${esc(ldesc)}</span></div></li>
    <li><span class="dot ${s.dataState}"></span><div><b>Orbital data · ${esc(s.dataText)}</b>
      <span>Element sets are downloaded and screened automatically. ${fmt.int(s.tracked)} of ${fmt.int(s.catalogued)} catalogued objects in Earth orbit have a public orbit (${fmt.num(s.coverage, 1)}%).</span></div></li>
    <li><span class="dot ${s.schedState}"></span><div><b>Scheduler · ${esc(s.schedText)}</b>
      <span>Runs ingestion, screening, alert e-mails, backups and the re-entry model on a timetable.</span></div></li>
    <li><span class="dot ${s.spacetrack ? 'ok' : ''}"></span><div><b>Space-Track · ${s.spacetrack ? 'connected' : 'not configured'}</b>
      <span>${s.spacetrack ? 'Adds the debris and rocket bodies CelesTrak does not publish.' : 'Needed for the debris and rocket bodies CelesTrak does not publish, orbit history and re-entry training.'}</span></div></li>
    <li><span class="dot ${s.email ? 'ok' : ''}"></span><div><b>E-mail · ${s.email ? 'on' : 'not configured'}</b>
      <span>${s.email ? 'Alerts and password resets are e-mailed.' : 'Alerts stay in the app; reset e-mails wait in the outbox.'}</span></div></li>`;
}

function paintStatusPanel() {
  const panel = document.getElementById('statusPanel');
  if (panel) panel.querySelector('ul').innerHTML = statusRows();
}

function closeStatusPanel() {
  document.getElementById('statusPanel')?.remove();
  document.getElementById('statusBtn')?.setAttribute('aria-expanded', 'false');
}

function toggleStatusPanel() {
  if (document.getElementById('statusPanel')) { closeStatusPanel(); return; }
  closeUserMenu();
  const btn = document.getElementById('statusBtn');
  const r = btn.getBoundingClientRect();
  const panel = document.createElement('div');
  panel.className = 'menu status-panel';
  panel.id = 'statusPanel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'System status');
  panel.style.position = 'fixed';
  panel.style.top = `${r.bottom + 6}px`;
  panel.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 360))}px`;
  panel.innerHTML = `<div class="menu-head"><strong>System status</strong><span>What keeps OrbitWatch's data current</span></div>
    <ul class="status-list">${statusRows()}</ul>
    <a href="#/dashboard">Open the mission dashboard →</a>`;
  document.body.appendChild(panel);
  btn.setAttribute('aria-expanded', 'true');
}

async function refreshSystem() {
  try {
    app.system = await get('/system/status');
    renderSysbar();
    window.dispatchEvent(new CustomEvent('ow:system', { detail: app.system }));
  } catch { /* the strip keeps its last state */ }
}

// ------------------------------------------------------------------ chrome
function navLinks(path) {
  return NAV.filter(([, , role]) => !role || app.can(role)).map(([href, label]) => {
    const p = href.slice(1);
    return `<a href="${href}" class="${path.startsWith(p) ? 'active' : ''}">${label}</a>`;
  }).join('');
}

function renderChrome() {
  const path = location.hash.replace(/^#/, '').split('?')[0] || '/';
  document.getElementById('nav').innerHTML = navLinks(path);
  const box = document.getElementById('userbox');
  const tools = `<button class="icon-btn opt" id="helpBtn" title="Help: guided tour and glossary" aria-label="Help" aria-haspopup="menu" aria-expanded="false">${icon('help')}</button>
    <button class="icon-btn" data-drawer="log" title="Event log: what OrbitWatch is doing, live" aria-label="Open the event log" aria-expanded="false">${icon('log')}</button>`;
  if (app.user) {
    const initials = app.user.name.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
    box.innerHTML = `${tools}
      <a class="icon-btn bell" href="#/alerts" title="My alerts" aria-label="My alerts">${icon('bell')}<span class="count hidden" id="alertCount"></span></a>
      <button class="userchip" id="userBtn" aria-haspopup="menu" aria-expanded="false"><span class="av">${esc(initials)}</span>
        <span class="who"><strong>${esc(app.user.name)}</strong><span>${esc(app.user.role_label)}</span></span></button>`;
    box.querySelector('#userBtn').addEventListener('click', (e) => { e.stopPropagation(); toggleUserMenu(); });
    box.querySelector('#helpBtn').addEventListener('click', (e) => { e.stopPropagation(); toggleHelpMenu(); });
    updateAlertBadge();
  } else {
    box.innerHTML = `${tools}<a class="btn sm ghost" href="#/login">Log in</a><a class="btn sm primary" href="#/register">Create account</a>`;
    box.querySelector('#helpBtn').addEventListener('click', (e) => { e.stopPropagation(); toggleHelpMenu(); });
  }
  renderMobileNav(path);
}

function closeUserMenu() {
  document.getElementById('userMenu')?.remove();
  document.getElementById('userBtn')?.setAttribute('aria-expanded', 'false');
  document.getElementById('helpMenu')?.remove();
  document.getElementById('helpBtn')?.setAttribute('aria-expanded', 'false');
}

function toggleHelpMenu() {
  if (document.getElementById('helpMenu')) { closeUserMenu(); return; }
  closeUserMenu();
  closeStatusPanel();
  const btn = document.getElementById('helpBtn');
  const r = btn.getBoundingClientRect();
  const m = document.createElement('div');
  m.className = 'menu';
  m.id = 'helpMenu';
  m.setAttribute('role', 'menu');
  Object.assign(m.style, { position: 'fixed', top: `${r.bottom + 6}px`, right: `${Math.max(8, window.innerWidth - r.right)}px` });
  m.innerHTML = `<button role="menuitem" data-action="tour">${icon('tour')} Guided tour<span class="muted small" style="margin-left:auto">1 min</span></button>
    <button role="menuitem" data-drawer="glossary">${icon('book')} Glossary of terms</button>`;
  document.body.appendChild(m);
  btn.setAttribute('aria-expanded', 'true');
  m.addEventListener('click', () => setTimeout(closeUserMenu, 0));
}
function toggleUserMenu() {
  if (document.getElementById('userMenu')) { closeUserMenu(); return; }
  const btn = document.getElementById('userBtn');
  const r = btn.getBoundingClientRect();
  const m = document.createElement('div');
  m.className = 'menu';
  m.id = 'userMenu';
  m.setAttribute('role', 'menu');
  m.style.top = `${r.bottom + 6}px`;
  m.style.right = `${Math.max(8, window.innerWidth - r.right)}px`;
  m.style.position = 'fixed';
  m.innerHTML = `<div class="menu-head"><strong>${esc(app.user.name)}</strong><span>${esc(app.user.email)} · ${esc(app.user.role_label)}</span></div>
    <a href="#/account" role="menuitem">${icon('user')} Account &amp; notifications</a>
    <a href="#/account?tab=password" role="menuitem">${icon('key')} Change password</a>
    <a href="#/alerts" role="menuitem">${icon('bell')} My alerts</a>
    <hr><button role="menuitem" id="logoutBtn">${icon('out')} Log out</button>`;
  document.body.appendChild(m);
  btn.setAttribute('aria-expanded', 'true');
  m.querySelector('#logoutBtn').addEventListener('click', async () => {
    closeUserMenu();
    await post('/auth/logout');
    app.user = null;
    toast('Logged out');
    reconnect();
    renderChrome();
    route();
  });
  m.querySelector('a').focus();
}
document.addEventListener('click', (e) => {
  if (!e.target.closest('#userMenu, #helpMenu')) closeUserMenu();
  if (!e.target.closest('#statusPanel, #statusBtn')) closeStatusPanel();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeUserMenu(); closeStatusPanel(); setMobileNav(false); } });

// ------------------------------------------------------------------ mobile navigation
function setMobileNav(open) {
  const nav = document.getElementById('mobileNav');
  const scrim = document.getElementById('scrim');
  nav.classList.toggle('open', open);
  nav.setAttribute('aria-hidden', String(!open));
  document.getElementById('burger').setAttribute('aria-expanded', String(open));
  if (open) { scrim.classList.remove('hidden'); requestAnimationFrame(() => scrim.classList.add('on')); nav.querySelector('a, button')?.focus(); }
  else { scrim.classList.remove('on'); setTimeout(() => scrim.classList.add('hidden'), 200); }
  document.body.style.overflow = open ? 'hidden' : '';
}

function renderMobileStatus() {
  const el = document.getElementById('mnStatus');
  if (!el) return;
  el.innerHTML = `<ul class="status-list">${statusRows()}</ul><div class="small muted" style="margin-top:6px"><span data-utc></span></div>`;
}

function renderMobileNav(path) {
  const nav = document.getElementById('mobileNav');
  nav.innerHTML = `<div class="mn-head"><span class="brand-name">OrbitWatch</span>
      <button class="icon-btn" id="mnClose" aria-label="Close navigation">${icon('close')}</button></div>
    <nav aria-label="Main">${navLinks(path).replace(/<\/a>/g, '<span class="muted">›</span></a>')}</nav>
    <div class="mn-section"><h3>Tools</h3><div class="mn-tools">
      <button data-action="tour">${icon('tour')}Tour</button>
      <button data-drawer="glossary">${icon('book')}Glossary</button>
      <button data-drawer="log">${icon('log')}Event log</button></div></div>
    <div class="mn-section"><h3>System</h3><div class="sys-list" id="mnStatus"></div></div>
    <div class="mn-section">${app.user
      ? `<div class="small" style="margin-bottom:10px">${esc(app.user.name)} · <span class="muted">${esc(app.user.role_label)}</span></div>
         <div class="row"><a class="btn sm" href="#/account">Account</a><button class="btn sm ghost" id="mnLogout">Log out</button></div>`
      : '<div class="row"><a class="btn sm" href="#/login">Log in</a><a class="btn sm primary" href="#/register">Create account</a></div>'}</div>`;
  nav.querySelector('#mnClose').addEventListener('click', () => setMobileNav(false));
  nav.querySelector('#mnLogout')?.addEventListener('click', async () => {
    setMobileNav(false);
    await post('/auth/logout');
    app.user = null;
    reconnect();
    renderChrome();
    route();
  });
  nav.addEventListener('click', (e) => { if (e.target.closest('a, [data-drawer], [data-action]')) setMobileNav(false); });
  renderMobileStatus();
}

document.getElementById('burger').addEventListener('click', () => setMobileNav(!document.getElementById('mobileNav').classList.contains('open')));
document.getElementById('scrim').addEventListener('click', () => setMobileNav(false));
document.addEventListener('click', (e) => {
  const d = e.target.closest('[data-drawer]');
  if (d) { e.preventDefault(); toggleDrawer(d.dataset.drawer); return; }
  const a = e.target.closest('[data-action="tour"]');
  if (a) { e.preventDefault(); startTour(); }
});

// ------------------------------------------------------------------ alerts badge
function setBadge(n) {
  const el = document.getElementById('alertCount');
  if (!el || n == null) return;
  el.textContent = n > 99 ? '99+' : n;
  el.classList.toggle('hidden', !n);
}

export async function updateAlertBadge() {
  if (!app.user) return;
  try { setBadge((await get('/me/alerts/count')).unacknowledged); } catch { /* best effort */ }
}

// ------------------------------------------------------------------ router
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
      ? `<div class="card auth-card bezel"><h1>Not available</h1><p class="muted">This page needs the ${r.role === 'admin' ? 'Administrator' : 'Analyst'} role. An administrator can change your role.</p></div>`
      : `<div class="card auth-card bezel"><h1>Log in required</h1><p class="muted">Log in to see this page.</p><p><a class="btn primary" href="#/login?next=${encodeURIComponent(location.hash)}">Log in</a></p></div>`;
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
setInterval(refreshSystem, 60000);

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
document.addEventListener('visibilitychange', () => { if (!document.hidden) { resync('focus'); refreshSystem(); } });

// Live stream: status strip, alert badge, and a system refresh when data or jobs change.
onLive((type, data) => {
  if (type === 'state') renderSysbar();
  if (type === 'alerts') setBadge(data);
  if (type === 'log' && ['data', 'screening', 'job', 'system'].includes(data.category)) refreshSystem();
  if (type === 'log' && data.severity === 'critical') toast(data.message, 'error');
});

function offerTour() {
  if (tourSeen() || document.querySelector('.tour-offer')) return;
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast tour-offer';
  el.innerHTML = `<div style="margin-bottom:8px">New to OrbitWatch? A one-minute guided tour shows where everything is.</div>
    <div class="row"><button class="btn primary sm" data-go>Take the tour</button><button class="btn ghost sm" data-no>Not now</button></div>`;
  box.appendChild(el);
  el.querySelector('[data-go]').addEventListener('click', () => { el.remove(); startTour(); });
  el.querySelector('[data-no]').addEventListener('click', () => { el.remove(); try { localStorage.setItem('orbitwatch.tour.v2', 'done'); } catch { /* ignore */ } });
  setTimeout(() => el.remove(), 20000);
}

(async () => {
  initGlossary();
  initEventLog();
  try { await app.refreshUser(); } catch { renderChrome(); }
  renderSysbar();
  booted = true;
  connect();
  refreshSystem();
  await route();
  setTimeout(offerTour, 2500);
})();
