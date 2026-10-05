import { csvUrl, get, qs } from '../api.js';
import { agentPanel } from '../agent-panel.js';
import { term } from '../drawers.js';
import { age, debounce, empty, errorBox, esc, fmt, h, hashQuery, loading, objLink, pager, prov, riskBadge, RISKS,
  setHashQuery, table, typeTag } from '../ui.js';

export async function render(root, { app }) {
  const q0 = hashQuery();
  const state = { q: q0.q || '', norad: q0.norad || '', from: q0.from || '', to: q0.to || '',
    when: q0.when || 'upcoming', sort: q0.sort || '', risk: q0.risk || '', page: Number(q0.page) || 1 };

  let threshold = '';
  try { threshold = (await get('/stats')).screening_threshold_km; } catch { /* optional */ }
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Conjunction screening</div><h1>Close approaches</h1>
    <p>Every predicted approach closer than ${threshold ? `<strong>${fmt.num(threshold, 0)} km</strong>` : 'the screening threshold'}
    between a watchlist satellite and any tracked object, with the time of closest approach (TCA), miss distance and relative velocity.</p></div>
    <div class="row" id="exportBox"></div></div>`));
  const filters = h(`<form class="filters" autocomplete="off">
    <label class="field wide"><span>Object name</span><input type="search" name="q" placeholder="CARTOSAT, STARLINK…"></label>
    <label class="field"><span>NORAD number</span><input type="number" name="norad" min="1" placeholder="25544"></label>
    <label class="field"><span>Show</span><select name="when"><option value="upcoming">Upcoming</option><option value="all">All (incl. past)</option></select></label>
    <label class="field"><span>TCA from</span><input type="date" name="from"></label>
    <label class="field"><span>TCA to</span><input type="date" name="to"></label>
    <label class="field"><span>Sort</span><select name="sort"><option value="">By time</option><option value="miss">Closest first</option></select></label>
    <fieldset class="field" style="border:0;padding:0;margin:0"><span>Risk level</span><div class="row">
      ${RISKS.map((r) => `<label class="check"><input type="checkbox" name="risk" value="${r}"> ${riskBadge(r)}</label>`).join('')}</div></fieldset>
  </form>`);
  root.appendChild(filters);
  const detail = h('<div></div>');
  root.appendChild(detail);
  const results = h(`<div class="table-card">${loading()}</div>`);
  root.appendChild(results);

  for (const k of ['q', 'norad', 'from', 'to', 'when', 'sort']) filters.elements[k].value = state[k];
  const riskSet = new Set(state.risk.split(',').filter(Boolean));
  filters.querySelectorAll('input[name=risk]').forEach((c) => { c.checked = riskSet.has(c.value); });

  const params = () => ({ q: state.q, norad: state.norad, from: state.from, to: state.to ? `${state.to}T23:59:59` : '',
    when: state.when, sort: state.sort, risk: state.risk });
  if (app.can('analyst')) root.querySelector('#exportBox').innerHTML = '<a class="btn" id="exportBtn">Export CSV</a>';

  let seq = 0;
  async function load() {
    const mine = ++seq;
    setHashQuery({ ...params(), to: state.to, page: state.page > 1 ? state.page : '', event: hashQuery().event });
    const exp = root.querySelector('#exportBtn');
    if (exp) exp.href = csvUrl('/conjunctions', params());
    results.innerHTML = loading();
    try {
      const data = await get('/conjunctions' + qs({ ...params(), page: state.page, page_size: 50 }));
      if (mine !== seq) return;
      results.innerHTML = '';
      if (!data.items.length) { results.innerHTML = empty('No close approaches match', ' Try “All (incl. past)” or widen the dates.'); return; }
      results.appendChild(table([
        { label: 'TCA (UTC)', render: (r) => `<span class="num">${fmt.dt(r.time_of_closest_approach).replace(' UTC', '')}</span><div class="small muted">${fmt.rel(r.time_of_closest_approach)}</div>` },
        { label: 'Watched object', render: (r) => `${objLink(r.primary_norad, r.primary_name)}<div class="small">${typeTag(r.primary_type)}</div>` },
        { label: 'Other object', render: (r) => `${objLink(r.secondary_norad, r.secondary_name)}<div class="small">${typeTag(r.secondary_type)}</div>` },
        { label: 'Miss distance', num: true, render: (r) => fmt.km(r.miss_distance_km, 3) },
        { label: 'Rel. velocity', num: true, render: (r) => `${fmt.num(r.relative_velocity, 2)} km/s` },
        { label: 'Pc', num: true, render: (r) => (r.probability_of_collision == null ? '—' : Number(r.probability_of_collision).toExponential(1)) },
        { label: 'Risk', render: (r) => riskBadge(r.risk_level) },
      ], data.items, { onRow: (r) => showDetail(r.event_id) }));
      results.appendChild(pager(data.total, data.page, data.page_size, (p) => { state.page = p; load(); }));
    } catch (err) {
      if (mine === seq) results.innerHTML = errorBox(err);
    }
  }

  let stopAgent = null;
  async function showDetail(id, assessmentId) {
    setHashQuery({ ...params(), to: state.to, page: state.page > 1 ? state.page : '', event: id });
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
        setHashQuery({ ...params(), to: state.to, page: state.page > 1 ? state.page : '' });
      });
      detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      detail.innerHTML = errorBox(err);
    }
  }

  const onChange = () => {
    for (const k of ['q', 'norad', 'from', 'to', 'when', 'sort']) state[k] = filters.elements[k].value;
    state.risk = [...filters.querySelectorAll('input[name=risk]:checked')].map((c) => c.value).join(',');
    state.page = 1;
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
