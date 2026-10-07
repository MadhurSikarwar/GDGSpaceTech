import { csvUrl, get, qs } from '../api.js';
import { agentPanel } from '../agent-panel.js';
import { term } from '../drawers.js';
import { age, debounce, empty, errorBox, esc, fmt, h, hashQuery, loading, objLink, pager, prov, riskBadge, RISKS,
  setHashQuery, table, typeTag } from '../ui.js';

// Time windows measured from now (recomputed on every load, so "next 6 hours" stays the next six hours).
const WINDOWS = { '6h': ['next 6 hours', -15 * 60000, 6 * 3600000], '24h': ['next 24 hours', -15 * 60000, 24 * 3600000],
  '72h': ['next 72 hours', -15 * 60000, 72 * 3600000], past24h: ['past 24 hours', -24 * 3600000, 0] };
const RISK_VAR = { LOW: 'var(--r-low)', MEDIUM: 'var(--r-medium)', HIGH: 'var(--r-high)', CRITICAL: 'var(--r-critical)' };

export async function render(root, { app }) {
  const q0 = hashQuery();
  const state = { q: q0.q || '', norad: q0.norad || '', from: q0.from || '', to: q0.to || '', win: WINDOWS[q0.win] ? q0.win : '',
    when: q0.when || 'upcoming', sort: q0.sort || '', risk: q0.risk || '', page: Number(q0.page) || 1 };

  // The page does not wait for the screening threshold (a cold /stats can take a third of a second): the sentence
  // names it as soon as the figure arrives, and the table is requested at once.
  const head = h(`<div class="page-head"><div><div class="eyebrow">Conjunction screening</div><h1>Close approaches</h1>
    <p>Every predicted approach closer than <span id="threshold">the screening threshold</span>
    between a watchlist satellite and any tracked object, with the time of closest approach (TCA), miss distance and relative velocity.</p></div>
    <div class="row" id="exportBox"></div></div>`);
  root.appendChild(head);
  get('/stats').then((s) => { if (s.screening_threshold_km) head.querySelector('#threshold').innerHTML = `<strong>${fmt.num(s.screening_threshold_km, 0)} km</strong>`; }).catch(() => {});
  const filters = h(`<form class="filters" autocomplete="off">
    <label class="field wide"><span>Object name</span><input type="search" name="q" placeholder="CARTOSAT, STARLINK…"></label>
    <label class="field"><span>NORAD number</span><input type="number" name="norad" min="1" placeholder="25544"></label>
    <label class="field"><span>Show</span><select name="when"><option value="upcoming">Upcoming</option><option value="all">All (incl. past)</option></select></label>
    <label class="field"><span>TCA from</span><input type="date" name="from"></label>
    <label class="field"><span>TCA to</span><input type="date" name="to"></label>
    <label class="field"><span>Sort</span><select name="sort"><option value="">By time</option><option value="miss">Closest first</option></select></label>
  </form>`);
  root.appendChild(filters);
  // risk (any combination) and a window of time from now: chips, with the number of events behind each risk chip
  const chipRows = h(`<div class="chip-rows">
    <div class="chips risk-chips" role="group" aria-label="Risk level">${RISKS.map((r) => `<button type="button" class="chip" data-risk="${r}" aria-pressed="false"><i class="rdot" style="background:${RISK_VAR[r]}"></i>${r}<span class="n"></span></button>`).join('')}</div>
    <div class="chips win-chips" role="group" aria-label="Time window"><button type="button" class="chip" data-win="" aria-pressed="false">Any time</button>${
      Object.entries(WINDOWS).map(([k, [label]]) => `<button type="button" class="chip" data-win="${k}" aria-pressed="false">${label}</button>`).join('')}</div></div>`);
  root.appendChild(chipRows);
  const bar = h(`<div class="result-bar"><div class="result-count" id="count" aria-live="polite">Loading…</div><div class="active-filters" id="active"></div></div>`);
  root.appendChild(bar);
  const detail = h('<div></div>');
  root.appendChild(detail);
  const results = h(`<div class="table-card">${loading()}</div>`);
  root.appendChild(results);

  const syncForm = () => {
    for (const k of ['q', 'norad', 'from', 'to', 'when', 'sort']) filters.elements[k].value = state[k];
    const riskSet = new Set(state.risk.split(',').filter(Boolean));
    chipRows.querySelectorAll('[data-risk]').forEach((b) => b.setAttribute('aria-pressed', String(riskSet.has(b.dataset.risk))));
    chipRows.querySelectorAll('[data-win]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.win === state.win)));
  };
  syncForm();

  // what goes in the address (and so in a shared link) versus what is asked of the server (a window is turned into real times)
  const urlParams = () => ({ q: state.q, norad: state.norad, from: state.from, to: state.to, win: state.win, when: state.when, sort: state.sort, risk: state.risk });
  const apiParams = () => {
    const base = { q: state.q, norad: state.norad, sort: state.sort, risk: state.risk };
    if (state.win) {
      const [, before, after] = WINDOWS[state.win];
      // the server orders "upcoming" soonest first and anything else newest first: a window ahead reads forwards, the past one backwards
      return { ...base, from: new Date(Date.now() + before).toISOString(), to: new Date(Date.now() + after).toISOString(), when: state.win === 'past24h' ? 'all' : 'upcoming' };
    }
    return { ...base, from: state.from, to: state.to ? `${state.to}T23:59:59` : '', when: state.when };
  };
  if (app.can('analyst')) root.querySelector('#exportBox').innerHTML = '<a class="btn" id="exportBtn">Export CSV</a>';

  // ---- the line above the table: how many, in what order, and the filters in force (each one clears with a click) ----
  const SORT_LABEL = { '': 'time of closest approach', miss: 'miss distance' };
  function paintActive(total) {
    if (total !== undefined) {
      bar.querySelector('#count').innerHTML = `<b>${fmt.int(total)}</b>${total === 1 ? 'close approach' : 'close approaches'}<span class="muted"> · by ${SORT_LABEL[state.sort] || 'time'}</span>`;
    }
    const active = [];
    if (state.q) active.push(['q', `“${state.q}”`]);
    if (state.norad) active.push(['norad', `NORAD ${state.norad}`]);
    if (state.win) active.push(['win', WINDOWS[state.win][0]]);
    if (!state.win && state.when === 'all') active.push(['when', 'including past']);
    if (!state.win && state.from) active.push(['from', `from ${state.from}`]);
    if (!state.win && state.to) active.push(['to', `to ${state.to}`]);
    for (const r of state.risk.split(',').filter(Boolean)) active.push([`risk:${r}`, r.toLowerCase()]);
    bar.querySelector('#active').innerHTML = active.map(([k, label]) => `<button type="button" class="fchip" data-clear="${k}" title="Remove this filter">${esc(label)}<span aria-hidden="true">×</span></button>`).join('')
      + (active.length > 1 ? '<button type="button" class="fchip clear" data-clear="*">Clear all</button>' : '');
  }
  bar.addEventListener('click', (e) => {
    const key = e.target.closest('[data-clear]')?.dataset.clear;
    if (!key) return;
    const reset = { q: '', norad: '', from: '', to: '', win: '', when: 'upcoming', risk: '' };
    if (key === '*') Object.assign(state, reset);
    else if (key.startsWith('risk:')) state.risk = state.risk.split(',').filter((r) => r && r !== key.slice(5)).join(',');
    else state[key] = reset[key];
    state.page = 1;
    syncForm();
    load();
  });
  chipRows.addEventListener('click', (e) => {
    const risk = e.target.closest('[data-risk]')?.dataset.risk;
    const win = e.target.closest('[data-win]');
    if (risk) {
      const set = new Set(state.risk.split(',').filter(Boolean));
      if (set.has(risk)) set.delete(risk); else set.add(risk);
      state.risk = RISKS.filter((r) => set.has(r)).join(',');
    } else if (win) {
      state.win = win.dataset.win;
      if (state.win) { state.from = ''; state.to = ''; }
    } else return;
    state.page = 1;
    syncForm();
    load();
  });
  const paintFacets = (facets) => {
    if (!facets?.risk) return;
    chipRows.querySelectorAll('[data-risk]').forEach((b) => { b.querySelector('.n').textContent = fmt.int(facets.risk[b.dataset.risk] || 0); });
  };

  let seq = 0;
  async function load() {
    const mine = ++seq;
    setHashQuery({ ...urlParams(), page: state.page > 1 ? state.page : '', event: hashQuery().event });
    const exp = root.querySelector('#exportBtn');
    if (exp) exp.href = csvUrl('/conjunctions', apiParams());
    results.innerHTML = loading();
    paintActive();
    try {
      get('/conjunctions/facets' + qs(apiParams())).then((f) => { if (mine === seq) paintFacets(f); }).catch(() => {});
      const data = await get('/conjunctions' + qs({ ...apiParams(), page: state.page, page_size: 50 }));
      if (mine !== seq) return;
      paintActive(data.total);
      results.innerHTML = '';
      if (!data.items.length) { results.innerHTML = empty('No close approaches match', ' Try “Any time”, remove a risk level, or widen the dates.'); return; }
      results.appendChild(table([
        { label: 'TCA (IST · UTC)', sort: 'time', render: (r) => fmt.when(r.time_of_closest_approach) },
        { label: 'Watched object', render: (r) => `${objLink(r.primary_norad, r.primary_name)}<div class="small">${typeTag(r.primary_type)}</div>` },
        { label: 'Other object', render: (r) => `${objLink(r.secondary_norad, r.secondary_name)}<div class="small">${typeTag(r.secondary_type)}</div>` },
        { label: 'Miss distance', num: true, sort: 'miss', render: (r) => fmt.km(r.miss_distance_km, 3) },
        { label: 'Rel. velocity', num: true, render: (r) => `${fmt.num(r.relative_velocity, 2)} km/s` },
        { label: 'Pc', num: true, render: (r) => (r.probability_of_collision == null ? '—' : Number(r.probability_of_collision).toExponential(1)) },
        { label: 'Risk', render: (r) => riskBadge(r.risk_level) },
      ], data.items, { sort: state.sort || 'time', onSort: (key) => { state.sort = key === 'time' ? '' : key; state.page = 1; syncForm(); load(); }, onRow: (r) => showDetail(r.event_id) }));
      results.appendChild(pager(data.total, data.page, data.page_size, (p) => { state.page = p; load(); }));
    } catch (err) {
      if (mine === seq) results.innerHTML = errorBox(err);
    }
  }

  let stopAgent = null;
  async function showDetail(id, assessmentId) {
    setHashQuery({ ...urlParams(), page: state.page > 1 ? state.page : '', event: id });
    detail.innerHTML = `<div class="card" style="margin-bottom:14px">${loading()}</div>`;
    try {
      const d = await get(`/conjunctions/${id}`);
      const e = d.event;
      const side = (o, label) => `<div><h3>${label}</h3><p style="margin-top:6px">${objLink(o.norad_id, o.name)} ${typeTag(o.object_type)}</p>
        <dl class="facts small"><dt>Owner</dt><dd>${esc(o.org_name || '—')}${o.country_name ? ` · ${esc(o.country_name)}` : ''}</dd>
        <dt>Orbit</dt><dd>${o.orbit?.perigee_km != null ? `${fmt.int(o.orbit.perigee_km)} × ${fmt.int(o.orbit.apogee_km)} km, ${fmt.num(o.orbit.inclination, 1)}°` : '—'}</dd>
        <dt>${term('epoch', 'Element epoch')}</dt><dd>${fmt.dt(o.orbit?.epoch)}<div class="small muted">age ${age(o.orbit?.epoch)}</div></dd>
        <dt>${term('provenance', 'Source')}</dt><dd>${o.orbit?.source ? prov({ src: o.orbit.source, fetched: o.orbit.fetched_at }) : '—'}</dd></dl></div>`;
      const ar = d.archive_risk;
      const archiveNote = e.origin === 'orbitalguard-archive' ? `<div class="banner info"><svg class="icon"><use href="#i-info"/></svg><div>
        <strong>Archived event.</strong> Screened by OrbitWatch's previous version (OrbitalGuard) from real CelesTrak data; kept for the record, it sent no alerts.
        ${ar ? `OrbitalGuard's final score: ${riskBadge(ar.risk_level)} ${fmt.num(ar.risk_score, 2)} after ${fmt.int(ar.reassessments)} re-assessments (${esc(ar.uncertainty_model || '')}).` : ''}</div></div>` : '';
      detail.innerHTML = `<section class="card bezel" style="margin-bottom:14px">${archiveNote}
        <div class="card-head"><h2>Event #${e.event_id} ${riskBadge(e.risk_level)}</h2>
          <div class="row"><a class="btn accent" href="#/globe?event=${e.event_id}">Replay in 3D</a><button class="btn ghost" id="closeDetail">Close</button></div></div>
        <div class="grid cols-3">
          <div><h3>Encounter</h3><dl class="facts" style="margin-top:8px">
            <dt>TCA</dt><dd>${fmt.dt(e.time_of_closest_approach)}<div class="small muted">${fmt.rel(e.time_of_closest_approach)}</div></dd>
            <dt>${term('miss', 'Miss distance')}</dt><dd><strong>${fmt.km(e.miss_distance_km, 3)}</strong></dd>
            <dt>Relative velocity</dt><dd>${fmt.num(e.relative_velocity, 3)} km/s</dd>
            <dt>${term('pc', 'Probability of collision')}</dt><dd>${e.probability_of_collision == null ? '—' : Number(e.probability_of_collision).toExponential(2)}<div class="small muted">Foster 2D, 20 m hard-body radius</div></dd>
            <dt>Recorded</dt><dd>${fmt.dt(e.created_at)}${e.updated_at !== e.created_at ? `<div class="small muted">refined ${fmt.dt(e.updated_at)}</div>` : ''}</dd></dl></div>
          ${side(d.primary, 'Watched object')}${side(d.secondary, 'Other object')}
        </div></section>`;
      if (stopAgent) stopAgent();
      stopAgent = await agentPanel(detail, e.event_id, app, { assessmentId });
      detail.querySelector('#closeDetail').addEventListener('click', () => {
        if (stopAgent) { stopAgent(); stopAgent = null; }
        detail.innerHTML = '';
        setHashQuery({ ...urlParams(), page: state.page > 1 ? state.page : '' });
      });
      detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      detail.innerHTML = errorBox(err);
    }
  }

  const onChange = () => {
    for (const k of ['q', 'norad', 'from', 'to', 'when', 'sort']) state[k] = filters.elements[k].value;
    if (state.from || state.to) state.win = '';                  // a date range of one's own replaces the window from now
    state.page = 1;
    syncForm();
    load();
  };
  filters.addEventListener('change', onChange);
  filters.elements.q.addEventListener('input', debounce(onChange, 300));
  filters.addEventListener('submit', (e) => e.preventDefault());
  load();
  if (q0.event) showDetail(Number(q0.event));
  else if (q0.assessment) {
    // deep link from the event log / dashboard: open the assessment's event (synthetic ones live in the demo lab)
    get(`/assessments/${Number(q0.assessment)}`).then((a) => {
      if (a.demo_event_id) location.hash = `#/demo?assessment=${a.assessment_id}`;
      else if (a.event_id) showDetail(a.event_id, a.assessment_id);
    }).catch((err) => { detail.innerHTML = errorBox(err); });
  }
  return () => { if (stopAgent) stopAgent(); };
}
