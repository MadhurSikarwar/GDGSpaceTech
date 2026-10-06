// Mission dashboard: the state of tracking, screening, decisions, data and the system itself.
// Every figure is read from the canonical database through the API; nothing is estimated here.
import { get } from '../api.js';
import { barChart, typeColor } from '../charts.js';
import { term } from '../drawers.js';
import { onLive } from '../live.js';
import { age, countUpIn, empty, errorBox, esc, fmt, h, loading, objLink, prov, riskBadge, synTag, table, typeTag, unavailable } from '../ui.js';
import { countdown } from '../hud.js';
import { nextEvents, radarSvg } from '../diagrams.js';
import { istHM, utcHM } from '../time.js';

const RISK_COLOR = { LOW: 'var(--r-low)', MEDIUM: 'var(--r-medium)', HIGH: 'var(--r-high)', CRITICAL: 'var(--r-critical)' };
const FRESH = { fresh: ['ok', 'fresh'], stale: ['warn', 'stale'], never: ['bad', 'never run'], archive: ['', 'archive'],
  'not configured': ['', 'not configured'] };

export async function render(root, { app }) {
  root.appendChild(h(`<div class="page-head"><div>
      <div class="eyebrow">Operations</div>
      <h1>Mission dashboard</h1>
      <p>Tracking coverage, upcoming close approaches, decisions, data freshness and system health — live from the database.</p></div>
      <div class="row"><a class="btn" href="#/conjunctions">Close approaches</a><a class="btn primary" href="#/globe">Open the globe <span class="arrow">→</span></a></div></div>`));
  const strip = h(`<div class="mc-strip" aria-label="Key figures">${'<div class="mc-kpi"><div class="skeleton" style="height:58px"></div></div>'.repeat(6)}</div>`);
  root.appendChild(strip);
  // Catalogue and environment: every figure the earlier dashboard showed, kept.
  const cat = h(`<div class="tiles quiet" aria-label="Catalogue and environment">${'<div class="tile"><div class="skeleton" style="height:52px"></div></div>'.repeat(6)}</div>`);
  root.appendChild(cat);
  const grid = h(`<div class="mc-grid">
    <section class="card c8 bezel" id="pTimeline"><div class="card-head"><h2>Close approaches · next 72 hours</h2>
      <span class="sub">MEDIUM and above, placed by ${term('tca', 'TCA')}</span></div><div id="tl">${loading()}</div>
      <div id="topEvents" style="margin-top:12px"></div></section>
    <section class="card c4" id="pHealth"><div class="card-head"><h2>System health</h2><span class="sub" id="hbAge"></span></div>
      <ul class="health" id="health">${loading()}</ul></section>
    <section class="card c5 bezel" id="pRadar"><div class="card-head"><h2>Threat radar</h2><span class="sub" id="radarSub"></span></div>
      <div class="radar" id="radar">${loading()}</div><div class="legend radar-legend" id="radarLegend"></div>
      <p class="radar-note">Clockwise from the top: time to closest approach · from the centre: miss distance</p></section>
    <section class="card c7 flush" id="pNext"><div class="card-head"><h2>Next close approaches</h2><span class="sub">live countdown, MEDIUM and above</span></div>
      <div id="nextList">${loading()}</div></section>
    <section class="card c6"><div class="card-head"><h2>Objects per orbital region</h2><span class="sub">Current_Orbit ⋈ Orbit_Region, by mean altitude</span></div>
      <div class="chart-box short"><canvas id="regionChart" aria-label="Objects per orbital region by type"></canvas></div></section>
    <section class="card c6"><div class="card-head"><h2>${term('coverage', 'Orbital coverage')} by object type</h2><span class="sub" id="covSub"></span></div>
      <div id="coverage">${loading()}</div></section>
    <section class="card flush c7"><div class="card-head"><h2>Decisions &amp; AI assessments</h2>
      <a class="small" href="#/conjunctions">Run an assessment →</a></div><div id="aiList">${loading()}</div></section>
    <section class="card c5"><div class="card-head"><h2>${term('reentry', 'Re-entry prediction')}</h2><span class="sub" id="reSub"></span></div>
      <div id="reentry">${loading()}</div></section>
    <section class="card c12"><div class="card-head"><h2>Data provenance</h2><span class="sub">every source, its last successful update and its freshness</span></div>
      <div id="sources">${loading()}</div></section>
  </div>`);
  root.appendChild(grid);
  const $ = (s) => root.querySelector(s);

  let stats;
  try {
    stats = await get('/stats');
  } catch (err) {
    strip.outerHTML = errorBox(err);
    return undefined;
  }

  // ---- catalogue and environment tiles --------------------------------------
  const cc = stats.counts;
  cat.innerHTML = `
    <div class="tile"><div class="label">Objects catalogued</div><div class="value">${fmt.int(cc.total)}</div>
      <div class="foot">${fmt.int(cc.decayed)} have re-entered</div></div>
    <div class="tile"><div class="label">In Earth orbit</div><div class="value">${fmt.int(cc.on_orbit)}</div>
      <div class="foot">${fmt.int(cc.payloads_on_orbit)} payloads · ${fmt.int(cc.beyond_earth_orbit)} more beyond Earth orbit</div></div>
    <div class="tile"><div class="label">Debris in orbit</div><div class="value">${fmt.int(cc.debris_on_orbit)}</div>
      <div class="foot">${fmt.num(100 * cc.debris_on_orbit / cc.on_orbit, 0)}% of objects in Earth orbit</div></div>
    <a class="tile" href="#/catalog?country=IN" title="Objects whose current owner is India (ISRO, NSIL, Pixxel and others)" style="text-decoration:none;color:inherit">
      <div class="label">India</div><div class="value">${fmt.int(cc.india_in_orbit)}</div>
      <div class="foot">${fmt.int(cc.india_payloads_in_orbit)} payloads in Earth orbit · ${fmt.int(cc.india_tracked)} with a live orbit</div></a>
    <div class="tile"><div class="label">Watchlist</div><div class="value">${fmt.int(cc.watchlist)}</div>
      <div class="foot">screened within ${fmt.num(stats.screening_threshold_km, 0)} km</div></div>
    <div class="tile sw-tile" id="swTile"><div class="label">${term('kp', 'Space weather')}</div><div class="value">—</div><div class="foot">NOAA SWPC</div></div>`;
  countUpIn(cat);
  get('/space-weather').then((sw) => {
    const t = cat.querySelector('#swTile');
    t.querySelector('.value').innerHTML = `Kp ${fmt.num(sw.kp, 1)}<small>${esc(sw.activity)}</small>`;
    t.querySelector('.foot').textContent = `F10.7 ${fmt.num(sw.f107, 0)} sfu · Ap ${fmt.num(sw.ap, 0)} · drag ×${fmt.num(sw.drag_scalar, 2)}${sw.live ? '' : ' · SIM baseline (NOAA unreachable)'}`;
    t.title = sw.live ? `NOAA SWPC, Kp interval starting ${fmt.dt(sw.observed_at)}. Drag scalar scales orbit uncertainty in collision probabilities.`
      : 'NOAA SWPC could not be reached: these are quiet-sun baseline values, not a reading.';
  }).catch(() => {});

  // ---- region chart ------------------------------------------------------
  barChart($('#regionChart'), {
    labels: stats.regions.map((r) => r.region_name), horizontal: true, stacked: true, xTitle: 'objects',
    series: [['Payload', 'payloads'], ['Rocket Body', 'rocket_bodies'], ['Debris', 'debris'], ['Unknown', 'unknown']]
      .map(([t, k]) => ({ label: t, data: stats.regions.map((r) => r[k]), color: typeColor(t) })),
  });

  // ---- system-driven panels (KPI strip, health, coverage, sources) ------
  let markers = [];
  let counted = false;
  const paintSystem = (s) => {
    if (!s) return;
    const c = stats.counts;
    const cov = s.coverage;
    const up = stats.upcoming_by_risk;
    const next24 = markers.filter((m) => new Date(m.tca) - Date.now() < 86400000).length;
    const high72 = markers.filter((m) => m.risk === 'HIGH' || m.risk === 'CRITICAL').length;
    const crit72 = markers.filter((m) => m.risk === 'CRITICAL').length;
    const gp = s.sources.find((x) => x.source_key === 'celestrak_gp');
    const st = s.sources.find((x) => x.source_key === 'spacetrack_gp');
    const newest = [gp, st].filter((x) => x && x.last_success_at).map((x) => x.last_success_at).sort().pop();
    const freshPct = cov.with_orbit ? (100 * cov.fresh_3d) / cov.with_orbit : 0;
    const re = s.reentry;
    strip.innerHTML = `
      <div class="mc-kpi"><div class="k">Objects tracked ${term('coverage', 'ⓘ')}</div><div class="v">${fmt.int(cov.with_orbit)}</div>
        <div class="f">with a current orbit, of ${fmt.int(cov.objects_in_orbit)} in Earth orbit</div></div>
      <div class="mc-kpi ${cov.coverage_pct < 80 ? 'warn' : ''}"><div class="k">Orbital coverage</div><div class="v">${fmt.num(cov.coverage_pct, 1)}<small>%</small></div>
        <div class="meter" title="CelesTrak / Space-Track / missing"><i style="width:${(100 * cov.from_celestrak) / cov.objects_in_orbit}%;background:var(--accent)"></i>
          <i style="width:${(100 * cov.from_spacetrack) / cov.objects_in_orbit}%;background:#78a9ec"></i></div>
        <div class="f">${fmt.int(cov.elements_missing)} without published elements in configured sources${s.spacetrack_configured ? '' : ' · Space-Track not configured'}</div></div>
      <div class="mc-kpi"><div class="k">Close approaches</div><div class="v">${fmt.int(next24)}<small>24 h</small></div>
        <div class="f">${fmt.int(Object.values(up).reduce((a, b) => a + b, 0))} in the next 7 days · threshold ${fmt.num(stats.screening_threshold_km, 0)} km</div></div>
      <div class="mc-kpi ${crit72 ? 'alert' : ''}"><div class="k">High-risk · 72 h</div><div class="v">${fmt.int(high72)}</div>
        <div class="f">${crit72 ? `${riskBadge('CRITICAL')} ${fmt.int(crit72)}` : 'no CRITICAL events'}</div></div>
      <div class="mc-kpi ${newest && Date.now() - new Date(newest.endsWith('Z') ? newest : `${newest}Z`).getTime() > 8 * 3600000 ? 'warn' : ''}"><div class="k">Data freshness ${term('epoch', 'ⓘ')}</div>
        <div class="v">${newest ? esc(age(newest)) : '—'}</div>
        <div class="f">since the last element-set download · ${fmt.num(freshPct, 0)}% of orbits have an epoch under 3 days</div></div>
      <div class="mc-kpi ${re.available ? '' : 'warn'}"><div class="k">Re-entry model</div><div class="v">${re.available ? fmt.int(re.predictions) : '—'}<small>${re.available ? 'predictions' : 'unavailable'}</small></div>
        <div class="f">${re.active_model ? `model ${esc(re.active_model.model_version)} · median error ${fmt.num(re.active_model.median_ae_days, 1)} d` : 'no model has passed evaluation yet'}</div></div>`;

    if (!counted) { counted = true; countUpIn(strip); }
    const hb = s.scheduler.heartbeat || {};
    $('#hbAge').textContent = hb.heartbeat_at ? `heartbeat ${age(hb.heartbeat_at)} ago` : '';
    const job = (id) => s.jobs.find((j) => j.job_id === id);
    const jobRow = (id, label) => {
      const j = job(id);
      if (!j) return `<li><span class="dot"></span><span class="nm">${label}<small>not scheduled yet</small></span><span class="st">—</span></li>`;
      const st = j.last_status;
      const cls = st === 'success' ? 'ok' : st === 'failed' ? 'bad' : st ? 'warn' : '';
      return `<li><span class="dot ${cls}"></span><span class="nm">${label}<small>${j.last_started_at ? `last ${esc(fmt.rel(j.last_started_at))} · ${esc(st || '')}` : 'not run yet'}${j.last_message && st !== 'success' ? ` — ${esc(j.last_message.slice(0, 90))}` : ''}</small></span>
        <span class="st">${j.next_run_at ? `next ${esc(fmt.rel(j.next_run_at))}` : '—'}</span></li>`;
    };
    $('#health').innerHTML = `
      <li><span class="dot ${s.scheduler.running ? 'ok' : 'bad'}"></span><span class="nm">Scheduler<small>${s.scheduler.running ? `running on ${esc(hb.host || '')}` : 'not running: data will not refresh automatically'}</small></span><span class="st">${s.scheduler.running ? 'up' : 'down'}</span></li>
      ${jobRow('ingest_and_screen', 'Ingest + screening')}
      ${jobRow('aggregation', 'History summaries')}
      ${jobRow('reentry_predict', 'Re-entry predictions')}
      ${jobRow('backup', 'Backup')}
      <li><span class="dot ${s.spacetrack_configured ? 'ok' : 'warn'}"></span><span class="nm">Space-Track<small>${s.spacetrack_configured ? 'credentials configured' : 'not configured: debris and rocket bodies outside CelesTrak groups have no orbit'}</small></span><span class="st">${s.spacetrack_configured ? 'on' : 'off'}</span></li>
      <li><span class="dot ${s.email_delivery ? 'ok' : 'warn'}"></span><span class="nm">E-mail delivery<small>${s.email_delivery ? 'SMTP configured' : 'SMTP not configured: alerts stay in the app'}</small></span><span class="st">${s.email_delivery ? 'on' : 'off'}</span></li>
      <li><span class="dot ${s.history_element_sets ? 'ok' : 'bad'}"></span><span class="nm">Orbit history (MongoDB)<small>sharded on norad_id, replica sets</small></span><span class="st">${s.history_element_sets == null ? 'down' : `${fmt.int(s.history_element_sets)} sets`}</span></li>`;

    $('#covSub').textContent = `${fmt.int(cov.with_orbit)} of ${fmt.int(cov.objects_in_orbit)} objects`;
    $('#coverage').innerHTML = `<div class="table-wrap"><table class="data"><thead><tr><th>Type</th><th class="num">In orbit</th><th class="num">With orbit</th><th>Coverage</th></tr></thead><tbody>
      ${cov.by_type.map((t) => {
        const pct = t.total ? (100 * t.with_orbit) / t.total : 0;
        return `<tr><td>${typeTag(t.object_type)}</td><td class="num">${fmt.int(t.total)}</td><td class="num">${fmt.int(t.with_orbit)}</td>
          <td style="min-width:140px"><div class="row" style="flex-wrap:nowrap"><div class="meter" style="flex:1"><i style="width:${pct}%;background:${typeColor(t.object_type)}"></i></div>
          <span class="mono small">${fmt.num(pct, 0)}%</span></div></td></tr>`;
      }).join('')}</tbody></table></div>
      <p class="small muted" style="margin-top:10px">${s.spacetrack_configured
        ? 'Element sets from CelesTrak and the Space-Track GP catalogue; newest epoch wins per object.'
        : 'Element sets come from CelesTrak\'s public groups, which publish active satellites and selected debris. Most debris and rocket bodies need the Space-Track GP catalogue (credentials not configured). No orbit is ever estimated to fill the gap.'}</p>`;

    $('#sources').innerHTML = `<div class="prov-card">${s.sources.map((x) => {
      const [cls, label] = FRESH[x.freshness] || ['', x.freshness];
      return `<div><div class="k"><span>${esc(x.provider)}</span><span><span class="dot ${cls}"></span> ${esc(label)}</span></div>
        <div class="v">${esc(x.name)}</div>
        <div class="m">${x.last_success_at ? `updated ${esc(fmt.rel(x.last_success_at))}` : 'no successful update yet'}${x.last_records != null ? ` · ${fmt.int(x.last_records)} records` : ''}${x.expected_interval_hours ? ` · expected every ${fmt.num(x.expected_interval_hours, 0)} h` : ''}</div>
        ${x.last_message ? `<div class="m">${esc(x.last_message.slice(0, 140))}</div>` : ''}</div>`;
    }).join('')}</div>`;

    $('#reSub').textContent = re.active_model ? `model ${re.active_model.model_version}` : '';
  };

  // ---- close-approach timeline: events per half hour, stacked by risk --------------
  const paintTimeline = () => {
    const now = Date.now();
    const hours = markers.map((m) => (new Date(m.tca) - now) / 3600000).filter((x) => x >= 0);
    const spanH = Math.min(72, Math.max(12, Math.ceil(Math.max(0, ...hours) / 6) * 6));
    $('#pTimeline h2').textContent = `Close approaches · next ${spanH} hours`;
    if (!markers.length) { $('#tl').innerHTML = empty(`No MEDIUM or higher close approaches in the next ${spanH} hours`); return; }
    const nb = spanH * 2;
    const bins = Array.from({ length: nb }, () => ({ CRITICAL: 0, HIGH: 0, MEDIUM: 0 }));
    for (const m of markers) {
      const i = Math.floor(((new Date(m.tca) - now) / 3600000) * 2);
      if (i >= 0 && i < nb && bins[i][m.risk] !== undefined) bins[i][m.risk] += 1;
    }
    const peak = Math.max(1, ...bins.map((b) => b.CRITICAL + b.HIGH + b.MEDIUM));
    const W = 1000;
    const H = 120;
    const bw = W / nb;
    let svg = '';
    bins.forEach((b, i) => {
      let y = H;
      for (const r of ['CRITICAL', 'HIGH', 'MEDIUM']) {
        if (!b[r]) continue;
        const hgt = Math.max(2, (b[r] / peak) * (H - 8));
        y -= hgt;
        const t0 = new Date(now + i * 1800000);
        svg += `<rect x="${i * bw + 1}" y="${y}" width="${Math.max(bw - 2, 1)}" height="${hgt - 1}" rx="1" fill="${RISK_COLOR[r]}">
          <title>${istHM(t0)}–${istHM(new Date(t0.getTime() + 1800000))} IST (${utcHM(t0)}–${utcHM(new Date(t0.getTime() + 1800000))} UTC) · ${b[r]} ${r}</title></rect>`;
      }
    });
    const ticks = [];
    const step = spanH <= 24 ? 3 : 12;
    for (let hh = 0; hh <= spanH; hh += step) ticks.push(`<line x1="${(hh / spanH) * W}" x2="${(hh / spanH) * W}" y1="0" y2="${H}" stroke="var(--hair)"/>
      <text x="${(hh / spanH) * W + 4}" y="${H + 14}" fill="var(--ink-3)" font-family="IBM Plex Mono" font-size="11">${hh ? `+${hh} h` : 'now'}</text>`);
    $('#tl').innerHTML = `<svg viewBox="0 -4 ${W} ${H + 22}" style="width:100%;height:auto;display:block" role="img"
        aria-label="Close approaches per half hour by risk level">${ticks.join('')}<line x1="0" x2="${W}" y1="${H}" y2="${H}" stroke="var(--border-strong)"/>${svg}</svg>
      <div class="legend" style="margin:8px 0 0">${['CRITICAL', 'HIGH', 'MEDIUM'].map((r) => `<span>${riskBadge(r)} ${fmt.int(markers.filter((m) => m.risk === r).length)}</span>`).join('')}
        <span class="muted">events per 30 minutes</span></div>`;
  };

  // ---- the radar and the countdown list: the same markers, by time and by miss distance -------------
  const paintRadar = () => {
    const now = Date.now();
    const maxKm = stats.screening_threshold_km || 10;
    const inSpan = markers.filter((m) => Date.parse(m.tca) >= now);
    const spanH = Math.min(72, Math.max(12, Math.ceil(Math.max(0, ...inSpan.map((m) => (Date.parse(m.tca) - now) / 3600000)) / 6) * 6));
    $('#radarSub').textContent = `next ${spanH} hours · ${fmt.int(inSpan.length)} events`;
    $('#radar').innerHTML = inSpan.length ? `${radarSvg(inSpan, { now, spanH, maxKm })}<div class="rd-sweep" aria-hidden="true"></div>`
      : empty('No MEDIUM or higher close approaches in the next 72 hours');
    $('#radarLegend').innerHTML = ['CRITICAL', 'HIGH', 'MEDIUM'].map((r) => `<span>${riskBadge(r)} ${fmt.int(inSpan.filter((m) => m.risk === r).length)}</span>`).join('');
    const next = nextEvents(markers, now, 8);
    const el = $('#nextList');
    el.innerHTML = '';
    if (!next.length) { el.innerHTML = empty('Nothing scheduled in the next 72 hours'); return; }
    el.appendChild(table([
      { label: 'T-minus', render: (m) => `<span class="tm" data-tm="${esc(m.tca)}">${countdown(Date.parse(m.tca) - now)}</span>` },
      { label: 'Objects', render: (m) => `${objLink(m.primary.norad_id, m.primary.name)}<div class="small muted">vs ${esc(m.secondary.name)}</div>` },
      { label: 'Miss', num: true, render: (m) => fmt.km(m.miss_km, 3) },
      { label: 'Pc', num: true, render: (m) => (m.pc == null ? '—' : Number(m.pc).toExponential(1)) },
      { label: 'Risk', render: (m) => riskBadge(m.risk) },
    ], next, { onRow: (m) => { location.hash = `#/conjunctions?event=${m.event_id}`; } }));
  };
  const tick = setInterval(() => {
    root.querySelectorAll('[data-tm]').forEach((e) => { e.textContent = countdown(Date.parse(e.dataset.tm) - Date.now()); });
  }, 1000);

  // Every MEDIUM-or-higher close approach of the next 72 hours in one compact request (the globe's marker feed stops at 150,
  // which used to cut the later events off every count and chart on this page).
  const loadMarkers = async () => {
    try { markers = (await get('/conjunctions/upcoming?hours=72')).items; } catch { markers = []; }
    paintTimeline();
    paintRadar();
    paintSystem(app.system);
  };

  const loadTop = () => get('/conjunctions?risk=CRITICAL,HIGH&page_size=6').then(({ items }) => {
    const el = $('#topEvents');
    el.innerHTML = '';
    if (!items.length) return;
    el.appendChild(table([
      { label: 'TCA (IST · UTC)', render: (r) => fmt.when(r.time_of_closest_approach) },
      { label: 'Objects', render: (r) => `${objLink(r.primary_norad, r.primary_name)}<div class="small muted">vs ${esc(r.secondary_name)}</div>` },
      { label: 'Miss', num: true, render: (r) => fmt.km(r.miss_distance_km, 3) },
      { label: 'Pc', num: true, render: (r) => (r.probability_of_collision == null ? '—' : Number(r.probability_of_collision).toExponential(1)) },
      { label: 'Risk', render: (r) => riskBadge(r.risk_level) },
    ], items, { onRow: (r) => { location.hash = `#/conjunctions?event=${r.event_id}`; } }));
  }).catch((err) => { $('#topEvents').innerHTML = errorBox(err); });

  const DEC = { APPROVED: '<span class="tag ok">approved · simulated</span>', REJECTED: '<span class="tag bad">rejected</span>' };
  const loadAssessments = () => get('/assessments?limit=8').then(({ items }) => {
    const el = $('#aiList');
    el.innerHTML = '';
    if (!items.length) { el.innerHTML = empty('No assessments yet', ' Open a close approach and run the AI decision support (Analyst role).'); return; }
    el.appendChild(table([
      { label: 'Event', render: (r) => `${r.synthetic ? `${synTag()} ` : ''}${esc(r.primary_name)}<div class="small muted">vs ${esc(r.secondary_name)}</div>` },
      { label: 'Recommendation', render: (r) => (r.status === 'complete' ? `${riskBadge(r.risk_tier)} <span class="small">${esc((r.decision || '').replace(/_/g, ' ').toLowerCase())}</span>` : esc(r.status)) },
      { label: 'Δv', num: true, render: (r) => (r.delta_v_mps == null ? '—' : `${fmt.num(r.delta_v_mps, 3)} m/s`) },
      { label: 'Decision', render: (r) => DEC[r.decision_status] || (r.decision === 'MANEUVER_RECOMMENDED' && r.status === 'complete' ? '<span class="tag warn">awaiting review</span>' : '<span class="muted small">—</span>') },
    ], items, { onRow: (r) => { location.hash = r.synthetic ? `#/demo?assessment=${r.assessment_id}` : `#/conjunctions?event=${r.event_id}`; }, rowClass: (r) => (r.synthetic ? 'syn' : '') }));
  }).catch((err) => { $('#aiList').innerHTML = errorBox(err); });

  const loadReentry = async () => {
    const el = $('#reentry');
    try {
      const [st, preds] = await Promise.all([get('/reentry/models'), get('/reentry?limit=6')]);
      if (!st.available || !preds.items.length) {
        const m = st.models[0];
        el.innerHTML = unavailable('Predictions unavailable',
          `<p style="margin:0 0 8px">No re-entry model has passed evaluation, so OrbitWatch does not predict lifetimes rather than guess.</p>
           ${m ? `<p class="small muted" style="margin:0">Latest model <span class="mono">${esc(m.model_version)}</span> (${esc(m.status)}): ${esc(m.notes || '')}</p>` : ''}
           <p class="small muted" style="margin:8px 0 0">Gate: ≥ ${st.gate.min_objects} re-entered objects with history; held-out median relative error &lt; ${st.gate.max_median_relative_error}; 10–90 % interval coverage ≥ ${st.gate.min_interval_coverage}.</p>`);
        return;
      }
      el.innerHTML = '';
      el.appendChild(table([
        { label: 'Object', render: (r) => `${objLink(r.norad_id, r.name)}<div class="small">${typeTag(r.object_type)}</div>` },
        { label: 'Alt.', num: true, render: (r) => fmt.km(r.mean_altitude_km, 0) },
        { label: 'Expected', render: (r) => `${fmt.date(r.predicted_decay_date)}<div class="small muted">${fmt.num(r.lower_days, 0)}–${fmt.num(r.upper_days, 0)} d (10–90 %)</div>` },
      ], preds.items));
      el.insertAdjacentHTML('beforeend', `<p style="margin-top:8px">${prov({ model: `model ${st.active_model.model_version}`, note: `held-out median error ${fmt.num(st.active_model.median_ae_days, 1)} d, interval coverage ${fmt.num(100 * st.active_model.interval_coverage, 0)} %` })}</p>`);
    } catch (err) { el.innerHTML = errorBox(err); }
  };

  const onSystem = (e) => paintSystem(e.detail);
  window.addEventListener('ow:system', onSystem);
  if (!app.system) { try { app.system = await get('/system/status'); } catch { /* the strip shows placeholders */ } }
  paintSystem(app.system);
  loadMarkers();
  loadTop();
  loadAssessments();
  loadReentry();
  const off = onLive((type, d) => {
    if (type !== 'log') return;
    if (['agent', 'maneuver', 'demo'].includes(d.category)) loadAssessments();
    if (d.category === 'screening') { loadMarkers(); loadTop(); }
  });
  return () => { window.removeEventListener('ow:system', onSystem); off(); clearInterval(tick); };
}
