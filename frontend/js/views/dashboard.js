import { get } from '../api.js';
import { barChart, typeColor } from '../charts.js';
import { empty, errorBox, esc, fmt, h, loading, objLink, riskBadge, table, typeTag } from '../ui.js';

export async function render(root, { app }) {
  root.appendChild(h(`<div class="page-head"><div>
      <div class="eyebrow">Operations</div>
      <h1>Dashboard</h1>
      <p>The state of the catalogue, the next high-risk approaches and how fresh the data is.</p></div>
      <div class="row"><a class="btn" href="#/conjunctions">Close approaches</a><a class="btn primary" href="#/globe">Open the globe <span class="arrow">→</span></a></div></div>`));
  const body = h(`<div>${loading()}</div>`);
  root.appendChild(body);

  let stats;
  try {
    stats = await get('/stats');
  } catch (err) {
    body.innerHTML = errorBox(err);
    return;
  }
  const c = stats.counts;
  const up = stats.upcoming_by_risk;
  const upTotal = Object.values(up).reduce((a, b) => a + b, 0);
  const asOf = (k) => (stats.last_update[k] ? fmt.dt(stats.last_update[k]) : 'not yet run');
  body.innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="label">Objects catalogued</div><div class="value">${fmt.int(c.total)}</div>
        <div class="foot">${fmt.int(c.decayed)} have re-entered</div></div>
      <div class="tile"><div class="label">In Earth orbit</div><div class="value">${fmt.int(c.on_orbit)}</div>
        <div class="foot">${fmt.int(c.payloads_on_orbit)} payloads · ${fmt.int(c.beyond_earth_orbit)} more beyond Earth orbit</div></div>
      <div class="tile"><div class="label">Debris in orbit</div><div class="value">${fmt.int(c.debris_on_orbit)}</div>
        <div class="foot">tracked fragments</div></div>
      <div class="tile"><div class="label">Current orbits</div><div class="value">${fmt.int(c.with_current_orbit)}</div>
        <div class="foot">objects being propagated</div></div>
      <div class="tile"><div class="label">Close approaches, next 7 days</div><div class="value">${fmt.int(upTotal)}</div>
        <div class="foot">${['CRITICAL', 'HIGH'].filter((r) => up[r]).map((r) => `${riskBadge(r)} ${fmt.int(up[r])}`).join(' ') || 'none high-risk'}</div></div>
      <div class="tile"><div class="label">Watchlist</div><div class="value">${fmt.int(c.watchlist)}</div>
        <div class="foot">screened within ${fmt.num(stats.screening_threshold_km, 0)} km</div></div>
      <div class="tile sw-tile" id="swTile"><div class="label">Space weather</div><div class="value">—</div><div class="foot">NOAA SWPC</div></div>
    </div>
    <div class="grid cols-2">
      <section class="card">
        <div class="card-head"><h2>Objects per orbital region</h2><span class="sub">by mean altitude · Current_Orbit ⋈ Orbit_Region</span></div>
        <div class="chart-box"><canvas id="regionChart" aria-label="Objects per orbital region by type"></canvas></div>
      </section>
      <section class="card flush">
        <div class="card-head"><h2>Highest-risk upcoming approaches</h2><a class="small" href="#/conjunctions?risk=CRITICAL,HIGH">All close approaches →</a></div>
        <div id="topEvents">${loading()}</div>
      </section>
      <section class="card flush">
        <div class="card-head"><h2>Predicted re-entries</h2><span class="sub">regression model, 10–90 % interval</span></div>
        <div id="reentries">${loading()}</div>
      </section>
      <section class="card flush">
        <div class="card-head"><h2>Latest AI assessments</h2><span class="sub">LLM agent + deterministic guardrails</span></div>
        <div id="aiList">${loading()}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>Data freshness</h2></div>
        <dl class="facts">
          <dt>Orbits ingested</dt><dd>${asOf('ingest')}</dd>
          <dt>Screening run</dt><dd>${asOf('screening')}</dd>
          <dt>Catalogue refreshed</dt><dd>${asOf('catalog')}</dd>
          <dt>History summarised</dt><dd>${asOf('aggregation')}</dd>
        </dl>
        <p class="small muted" style="margin-top:14px">Element sets from CelesTrak; catalogue from the CelesTrak SATCAT and GCAT.
        Positions are SGP4 predictions from public two-line-element-quality data — good to a few kilometres, not operational-grade.</p>
      </section>
    </div>`;

  const regions = stats.regions;
  barChart(body.querySelector('#regionChart'), {
    labels: regions.map((r) => r.region_name),
    horizontal: true, stacked: true, xTitle: 'objects',
    series: [['Payload', 'payloads'], ['Rocket Body', 'rocket_bodies'], ['Debris', 'debris'], ['Unknown', 'unknown']]
      .map(([t, k]) => ({ label: t, data: regions.map((r) => r[k]), color: typeColor(t) })),
  });

  get('/conjunctions?risk=CRITICAL,HIGH&page_size=8').then(({ items }) => {
    const el = body.querySelector('#topEvents');
    el.innerHTML = '';
    if (!items.length) { el.innerHTML = empty('No high-risk approaches predicted'); return; }
    el.appendChild(table([
      { label: 'TCA (UTC)', render: (r) => `<span class="num">${fmt.dt(r.time_of_closest_approach).replace(' UTC', '')}</span><div class="small muted">${fmt.rel(r.time_of_closest_approach)}</div>` },
      { label: 'Objects', render: (r) => `${objLink(r.primary_norad, r.primary_name)}<div class="small muted">vs ${esc(r.secondary_name)}</div>` },
      { label: 'Miss', num: true, render: (r) => fmt.km(r.miss_distance_km, 2) },
      { label: 'Risk', render: (r) => riskBadge(r.risk_level) },
    ], items, { onRow: (r) => { location.hash = `#/conjunctions?event=${r.event_id}`; } }));
  }).catch((err) => { body.querySelector('#topEvents').innerHTML = errorBox(err); });

  get('/space-weather').then((sw) => {
    const t = body.querySelector('#swTile');
    t.querySelector('.value').innerHTML = `Kp ${fmt.num(sw.kp, 1)}<small>${esc(sw.activity)}</small>`;
    t.querySelector('.foot').textContent = `F10.7 ${fmt.num(sw.f107, 0)} sfu · drag ×${fmt.num(sw.drag_scalar, 2)}${sw.live ? '' : ' · fallback'}`;
  }).catch(() => {});

  get('/assessments?limit=6').then(({ items }) => {
    const el = body.querySelector('#aiList');
    el.innerHTML = '';
    if (!items.length) { el.innerHTML = empty('No assessments yet', ' Open a close approach and run the AI decision support (Analyst role).'); return; }
    el.appendChild(table([
      { label: 'Event', render: (r) => `${esc(r.primary_name)}<div class="small muted">vs ${esc(r.secondary_name)}</div>` },
      { label: 'Decision', render: (r) => (r.status === 'complete' ? `${riskBadge(r.risk_tier)} <span class="small">${esc((r.decision || '').replace(/_/g, ' ').toLowerCase())}</span>` : esc(r.status)) },
      { label: 'Δv', num: true, render: (r) => (r.delta_v_mps == null ? '—' : `${fmt.num(r.delta_v_mps, 3)} m/s`) },
      { label: 'Engine', render: (r) => `<span class="small mono muted">${esc(r.engine.replace('groq:', ''))}</span>` },
    ], items, { onRow: (r) => { location.hash = `#/conjunctions?event=${r.event_id}`; } }));
  }).catch((err) => { body.querySelector('#aiList').innerHTML = errorBox(err); });

  get('/reentry?limit=8').then(({ items }) => {
    const el = body.querySelector('#reentries');
    el.innerHTML = '';
    if (!items.length) {
      el.innerHTML = empty('No predictions yet', ' The model trains on the history of objects that have already re-entered.');
      return;
    }
    el.appendChild(table([
      { label: 'Object', render: (r) => `${objLink(r.norad_id, r.name)}<div class="small">${typeTag(r.object_type)}</div>` },
      { label: 'Altitude', num: true, render: (r) => fmt.km(r.mean_altitude_km, 0) },
      { label: 'Expected', render: (r) => `${fmt.date(r.predicted_decay_date)}<div class="small muted">${fmt.num(r.lower_days, 0)}–${fmt.num(r.upper_days, 0)} days</div>` },
    ], items));
  }).catch((err) => { body.querySelector('#reentries').innerHTML = errorBox(err); });
}
