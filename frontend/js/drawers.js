// Side drawers restored from OrbitalGuard: the space-domain glossary and the live event log.
import { live, loadRecent, onLive } from './live.js';
import { esc } from './ui.js';
import { asDate, istHMS, utcHMS } from './time.js';

const drawers = {};
let openName = null;

function setOpen(name, open) {
  const d = drawers[name];
  if (!d) return;
  const was = d.el.classList.contains('open');
  if (open && !was) d.returnTo = document.activeElement;           // where the keyboard goes back to when the drawer closes
  d.el.classList.toggle('open', open);
  d.el.setAttribute('aria-hidden', String(!open));
  d.el.inert = !open;
  document.querySelectorAll(`[data-drawer="${name}"]`).forEach((b) => b.setAttribute('aria-expanded', String(open)));
  if (open) {
    for (const other of Object.keys(drawers)) if (other !== name) setOpen(other, false);
    openName = name;
    d.onOpen?.();
    setTimeout(() => d.el.querySelector('input, button')?.focus({ preventScroll: true }), 60);
  } else if (openName === name) openName = null;
  if (!open && was) {                                              // a closed drawer is inert: focus must not fall back to the top of the page
    const back = d.returnTo;
    d.returnTo = null;
    const at = document.activeElement;
    if (back?.isConnected && back !== document.body && (d.el.contains(at) || at === document.body)) back.focus({ preventScroll: true });
  }
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
  const ok = !Number.isNaN(asDate(r.occurred_at).getTime());
  const hhmm = ok ? `${istHMS(r.occurred_at)} IST` : '';
  const day = ok ? `${utcHMS(r.occurred_at)} UTC` : '';
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

// ---- the glossary: any element with data-term="<key>" opens the drawer at that entry ---------------
// (restored from OrbitalGuard's glossary drawer, revised for OrbitWatch)
const CATEGORIES = ['COLLISION', 'ORBITS', 'MANEUVERS', 'DATA', 'WEATHER', 'GROUND'];

const GLOSSARY = [
  { key: 'tca', term: 'TCA', full: 'Time of Closest Approach', cat: 'COLLISION',
    desc: 'The UTC instant at which two objects reach their minimum separation.',
    detail: 'OrbitWatch screens every watchlist satellite against the catalogue with SGP4 on a coarse grid, then refines each candidate on a 1 s grid and a linear step to the true minimum.' },
  { key: 'miss', term: 'Miss distance', full: 'Separation at TCA', cat: 'COLLISION',
    desc: 'The distance between the two objects at the time of closest approach.',
    detail: 'Every approach closer than the configurable screening threshold (10 km by default) is recorded as a close approach (conjunction event).' },
  { key: 'pc', term: 'Pc', full: 'Probability of collision', cat: 'COLLISION',
    desc: 'The probability, from 0 to 1, that the two objects actually collide during the encounter.',
    detail: 'Computed with the Foster 2D method: the combined position uncertainty is projected onto the encounter plane and integrated over a disk of the combined hard-body radius.' },
  { key: 'foster', term: 'Foster 2D method', full: 'Encounter-plane probability integral', cat: 'COLLISION',
    desc: 'The standard analytical method for short, fast encounters.',
    detail: 'Assumes straight-line relative motion near TCA and Gaussian position errors; valid when the relative velocity is high (most LEO encounters).' },
  { key: 'hbr', term: 'HBR', full: 'Hard-body radius', cat: 'COLLISION',
    desc: 'The radius of a sphere enclosing both objects; contact happens if the miss vector falls inside it.',
    detail: 'OrbitWatch uses a combined 20 m by default; the agent may vary it.' },
  { key: 'covariance', term: 'Covariance', full: 'Position uncertainty', cat: 'COLLISION',
    desc: 'How uncertain each object\'s predicted position is, along radial, in-track and cross-track axes.',
    detail: 'Public element sets carry no covariance, so OrbitWatch models it from the element-set age, object type and space-weather drag: in-track error grows fastest because drag is not perfectly modelled.' },
  { key: 'risk', term: 'Risk level', full: 'LOW / MEDIUM / HIGH / CRITICAL', cat: 'COLLISION',
    desc: 'The screening tier from miss distance and relative velocity (database function fn_risk_level).',
    detail: 'The decision agent combines it with the Pc tier; the higher of the two drives the workflow.' },
  { key: 'synthetic', term: 'Synthetic debris', full: 'Demo object (not real)', cat: 'COLLISION',
    desc: 'An object OrbitWatch creates on request to produce a guaranteed close approach for demonstrations.',
    detail: 'Its orbit is a genuine SGP4 element set fitted to the planned encounter, but it lives only in the demo tables: it is never part of the catalogue, statistics, reports, alerts or exports.' },
  { key: 'sgp4', term: 'SGP4', full: 'Simplified General Perturbations 4', cat: 'ORBITS',
    desc: 'The analytical propagator element sets are fitted for; it predicts position and velocity at any time.',
    detail: 'Models Earth oblateness (J2-J4) and a simple drag term (B*). Deep-space orbits (periods over 225 min) use its SDP4 branch, which adds lunar and solar gravity. Accuracy is roughly a kilometre at epoch, degrading by kilometres per day.' },
  { key: 'omm', term: 'TLE / OMM', full: 'Two-line element set / Orbit Mean-elements Message', cat: 'ORBITS',
    desc: 'The formats of a mean element set: the orbit at one epoch, meant for SGP4.',
    detail: 'OrbitWatch ingests OMM JSON from CelesTrak and Space-Track; the OrbitalGuard archive is in TLE form. Both are parsed to the same fields.' },
  { key: 'epoch', term: 'Epoch', full: 'Element-set reference time', cat: 'ORBITS',
    desc: 'The instant an element set describes. Its age (now minus epoch) is the main driver of prediction error.',
    detail: 'OrbitWatch shows epoch age next to every orbit and excludes element sets older than the screening limit (30 days by default) from screening.' },
  { key: 'teme', term: 'TEME', full: 'True Equator, Mean Equinox frame', cat: 'ORBITS',
    desc: 'The inertial-like frame SGP4 outputs.',
    detail: 'Converted to the Earth-fixed frame (by Greenwich sidereal time) for the globe and ground-station geometry.' },
  { key: 'ric', term: 'RIC', full: 'Radial, In-track, Cross-track frame', cat: 'ORBITS',
    desc: 'A frame that moves with the satellite: radial (up), in-track (along the velocity), cross-track (orbit normal).',
    detail: 'Uncertainty and manoeuvres are expressed in it.' },
  { key: 'regime', term: 'LEO / MEO / GEO', full: 'Orbital regimes', cat: 'ORBITS',
    desc: 'Low Earth orbit (below 2,000 km), medium Earth orbit, and the geostationary belt near 35,786 km.',
    detail: 'OrbitWatch\'s region table defines the altitude bands; the region of an object is derived from its current mean altitude, never stored.' },
  { key: 'perigee', term: 'Perigee / apogee', full: 'Lowest / highest point of the orbit', cat: 'ORBITS',
    desc: 'Altitudes above the equatorial radius of the orbit\'s closest and farthest points.',
    detail: 'Computed by MySQL as generated columns from mean motion and eccentricity.' },
  { key: 'bstar', term: 'B*', full: 'SGP4 drag term', cat: 'ORBITS',
    desc: 'A fitted drag coefficient in the element set; larger values mean faster decay.',
    detail: 'A feature of the re-entry model, together with the measured rate of altitude loss.' },
  { key: 'reentry', term: 'Re-entry prediction', full: 'Remaining orbital lifetime', cat: 'ORBITS',
    desc: 'An estimate of when a decaying object will re-enter, with a 10-90 % interval.',
    detail: 'A gradient-boosted regression trained only on objects that have already re-entered and evaluated on held-out objects. If the training data or the held-out score are insufficient, predictions are shown as unavailable.' },
  { key: 'cw', term: 'CW equations', full: 'Clohessy-Wiltshire relative motion', cat: 'MANEUVERS',
    desc: 'Linear equations for motion relative to a circular reference orbit.',
    detail: 'They map a small impulse at the burn to a position shift at TCA in closed form, which is how candidate burns are evaluated and how an approved burn is simulated.' },
  { key: 'dv', term: 'Δv', full: 'Delta-v', cat: 'MANEUVERS',
    desc: 'The velocity change of a burn, in m/s: the fuel cost of an avoidance manoeuvre.',
    detail: 'The optimiser (SLSQP) finds the smallest Δv that brings Pc under 1e-5 within drift limits.' },
  { key: 'slsqp', term: 'SLSQP', full: 'Sequential least-squares programming', cat: 'MANEUVERS',
    desc: 'A constrained optimiser: minimise |Δv| subject to Pc, drift and (after a rejection) minimum miss-distance limits.',
    detail: '' },
  { key: 'drift', term: 'Slot drift', full: 'Semi-major-axis change', cat: 'MANEUVERS',
    desc: 'How far an in-track burn moves the satellite\'s orbit from its slot; bounded at 5 km.',
    detail: '' },
  { key: 'posigrade', term: 'Posigrade / retrograde', full: 'Burn direction along the velocity', cat: 'MANEUVERS',
    desc: 'Posigrade thrust raises the orbit on the far side; retrograde lowers it. Normal / anti-normal burns tilt the plane.',
    detail: '' },
  { key: 'approval', term: 'Approval gate', full: 'Human in the loop', cat: 'MANEUVERS',
    desc: 'No recommended manoeuvre is acted on without an analyst\'s explicit approval; rejections need a reason and can trigger a re-plan.',
    detail: 'OrbitWatch has no command uplink: an approved burn is executed in simulation only and labelled SIMULATED.' },
  { key: 'provenance', term: 'Provenance', full: 'Source and freshness', cat: 'DATA',
    desc: 'Where a number came from, when it was downloaded, and how old the underlying data is.',
    detail: 'Every current orbit records its source (CelesTrak or Space-Track), download time and download id; every data source records its last successful update.' },
  { key: 'celestrak', term: 'CelesTrak', full: 'Public element sets and SATCAT', cat: 'DATA',
    desc: 'Element sets of active satellites and selected debris groups, and the catalogue of every object.',
    detail: 'Fetched at most every two hours, per CelesTrak\'s policy.' },
  { key: 'spacetrack', term: 'Space-Track', full: '18th Space Defense Squadron data', cat: 'DATA',
    desc: 'The authoritative source: the full on-orbit GP catalogue, element-set history and decay messages.',
    detail: 'Needs a free account; OrbitWatch stays under its request limits (30/min, 300/h).' },
  { key: 'coverage', term: 'Orbital coverage', full: 'Objects with a current orbit', cat: 'DATA',
    desc: 'The share of objects in Earth orbit that have a usable current element set.',
    detail: 'Objects without one (classified, lost, or not published by the configured sources) cannot be screened or shown on the globe.' },
  { key: 'swpc', term: 'NOAA SWPC', full: 'Space Weather Prediction Center', cat: 'WEATHER',
    desc: 'Source of the geomagnetic and solar indices OrbitWatch uses.',
    detail: '' },
  { key: 'f107', term: 'F10.7', full: '10.7 cm solar radio flux', cat: 'WEATHER',
    desc: 'A proxy for solar extreme-ultraviolet output, which heats and expands the upper atmosphere.',
    detail: 'More flux means more drag and faster-growing in-track uncertainty.' },
  { key: 'kp', term: 'Kp / Ap', full: 'Geomagnetic activity indices', cat: 'WEATHER',
    desc: 'Planetary geomagnetic disturbance, 0 (quiet) to 9 (extreme storm).',
    detail: 'Storms inflate the drag scalar applied to the covariance model.' },
  { key: 'aos', term: 'AOS / LOS', full: 'Acquisition / loss of signal', cat: 'GROUND',
    desc: 'When a satellite rises above / sets below a ground station\'s elevation mask.',
    detail: 'A burn must be uplinked during a contact window before the burn time.' },
  { key: 'mask', term: 'Elevation mask', full: 'Minimum usable elevation', cat: 'GROUND',
    desc: 'The angle above the horizon a satellite must clear for a station to contact it.',
    detail: '' },
  { key: 'stations', term: 'Ground network', full: 'Stations used for contact windows', cat: 'GROUND',
    desc: 'Svalbard, Fairbanks and McMurdo (polar) plus ISRO\'s ISTRAC stations in Bengaluru, Lucknow and Mauritius.',
    detail: '' },
];

// for the command palette
export const glossaryTerms = () => GLOSSARY;
