// How OrbitWatch works: the long-form data sections that used to sit on the entrance page. Altitude spectrum,
// data flow, fragmentation and re-entries, database roles, and an index of the modules. All from /api/landing.
import { get } from '../api.js';
import { TYPE_COLORS, TYPE_NAMES } from '../globe-core.js';
import { errorBox, esc, fmt, h, loading } from '../ui.js';

export async function render(root, { app }) {
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">How it works</div><h1>The sky, and the data behind it</h1>
    <p>Where objects are, how the data flows from a public element set to an alert, where the debris came from, and who may see what.</p></div>
    <div class="row"><a class="btn" href="#/dashboard">Dashboard</a><a class="btn primary" href="#/globe">Open the globe <span class="arrow">→</span></a></div></div>`));
  const box = h('<div class="ins"></div>');
  root.appendChild(box);
  box.appendChild(h(loading('Loading')));
  let data;
  try { data = await get('/landing'); } catch (err) { box.innerHTML = ''; box.appendChild(h(errorBox(err.message))); return undefined; }
  if (!root.isConnected) return undefined;
  box.innerHTML = template();
  fillSpectrum(box, data);
  fillPipeline(box, data);
  fillDebris(box, data);
  fillModules(box, data, app);
  return undefined;
}

function template() {
  return `
  <section class="ins-sec" id="where">
    <div class="ins-head"><div class="ins-index">01 / Altitude</div>
      <div><h2>Orbits cluster where they are useful.</h2><p id="whereLead"></p></div></div>
    <div class="lp-spectrum" id="spectrum"></div>
    <div class="lp-legend" id="specLegend"></div>
    <div class="lp-figs" id="figs"></div>
  </section>

  <section class="ins-sec" id="pipeline">
    <div class="ins-head"><div class="ins-index">02 / Data flow</div>
      <div><h2>From a public element set to an alert.</h2><p id="pipeLead"></p></div></div>
    <div id="pipe"></div>
  </section>

  <section class="ins-sec" id="debris">
    <div class="ins-head"><div class="ins-index">03 / Fragmentation</div>
      <div><h2>Most debris comes from a few break-ups.</h2><p id="debrisLead"></p></div></div>
    <div class="lp-frag" id="frag"></div>
  </section>

  <section class="ins-sec" id="modules">
    <div class="ins-head"><div class="ins-index">04 / Modules</div>
      <div><h2>Seven parts, one database.</h2><p>Each module is a page of this site. The numbers beside them are live.</p></div></div>
    <div id="modList"></div>
    <div class="lp-roles" id="roles"></div>
  </section>`;
}

// ---------------------------------------------------------------------------
// 01: altitude spectrum, every object with a current orbit, log-binned by mean altitude.
function fillSpectrum(root, data) {
  const el = root.querySelector('#spectrum');
  const bpd = data.histogram.bins_per_decade;
  const bins = new Map();
  for (const r of data.histogram.rows) {
    const b = bins.get(r.bin) || { total: 0 };
    b[r.type] = (b[r.type] || 0) + r.n;
    b.total += r.n;
    bins.set(r.bin, b);
  }
  const W = 1200; const H = 400; const L = 54; const R = 12; const TOP = 40; const BASE = 318;
  const lo = Math.log10(150); const hi = Math.log10(60000);
  const x = (alt) => L + ((Math.log10(alt) - lo) / (hi - lo)) * (W - L - R);
  const totals = [...bins.values()].map((b) => b.total);
  const maxN = Math.max(...totals, 1);
  const step = maxN > 4000 ? 1000 : maxN > 1500 ? 500 : maxN > 400 ? 100 : 50;
  const yMax = Math.ceil(maxN / step) * step;
  const y = (n) => BASE - (n / yMax) * (BASE - TOP);
  const colors = Object.fromEntries(TYPE_NAMES.map((t, i) => [t, TYPE_COLORS[i]]));

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Number of objects by mean altitude, log-scaled altitude axis">`;
  const bands = [[150, 2000, 'LEO'], [2000, 35586, 'MEO'], [35586, 35986, ''], [35986, 60000, 'HEO']];
  bands.forEach(([a, b, name], i) => {
    svg += `<rect x="${x(a)}" y="${TOP - 24}" width="${x(b) - x(a)}" height="${BASE - TOP + 24}" fill="${i % 2 ? 'rgba(140,170,200,0.025)' : 'transparent'}"/>`;
    if (name) svg += `<text class="band-label" x="${x(a) + 8}" y="${TOP - 10}">${name}</text>`;
    svg += `<line x1="${x(a)}" x2="${x(a)}" y1="${TOP - 24}" y2="${BASE}" stroke="rgba(140,170,200,0.12)" stroke-dasharray="2 4"/>`;
  });
  svg += '<g class="axis">';
  for (let v = 0; v <= yMax; v += yMax / 4) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(140,170,200,${v ? 0.07 : 0.25})"/>`;
    svg += `<text x="${L - 10}" y="${y(v) + 3.5}" text-anchor="end">${fmt.int(v)}</text>`;
  }
  [200, 500, 1000, 2000, 5000, 10000, 20000, 35786].forEach((a) => {
    svg += `<line x1="${x(a)}" x2="${x(a)}" y1="${BASE}" y2="${BASE + 5}" stroke="rgba(140,170,200,0.35)"/>`;
    svg += `<text x="${x(a)}" y="${BASE + 20}" text-anchor="middle">${a >= 1000 ? `${fmt.num(a / 1000, a % 1000 ? 1 : 0)}k` : a}</text>`;
  });
  svg += `<text x="${W - R}" y="${BASE + 40}" text-anchor="end">mean altitude, km (log scale)</text>`;
  svg += `<text x="${L - 10}" y="${TOP - 26}" text-anchor="end">objects</text></g>`;
  let peak = null;
  for (const [b, v] of [...bins.entries()].sort((p, q) => p[0] - q[0])) {
    const a0 = 10 ** (b / bpd); const a1 = 10 ** ((b + 1) / bpd);
    if (a1 < 150 || a0 > 60000) continue;
    const x0 = x(Math.max(a0, 150)) + 1; const w = Math.max(x(Math.min(a1, 60000)) - x(Math.max(a0, 150)) - 2, 1);
    let acc = 0;
    for (const t of TYPE_NAMES) {
      const n = v[t] || 0;
      if (!n) continue;
      const hh = Math.max((n / yMax) * (BASE - TOP), 1.5);
      svg += `<rect x="${x0}" y="${BASE - acc - hh}" width="${w}" height="${hh}" fill="${colors[t]}"/>`;
      acc += hh;
    }
    if (!peak || v.total > peak.n) peak = { n: v.total, a0, a1, x: x0 + w / 2, y: BASE - acc };
  }
  const ann = [];
  const iss = data.spotlight?.mean_altitude_km;
  if (iss) ann.push([iss, 'ISS', 74]);
  ann.push([20200, 'GNSS (GPS, Galileo…)', 120], [35786, 'Geostationary', 74]);
  svg += '<g class="ann">';
  ann.forEach(([a, label, dy]) => {
    svg += `<line x1="${x(a)}" x2="${x(a)}" y1="${TOP + dy - 8}" y2="${BASE}" stroke="rgba(233,238,244,0.35)" stroke-dasharray="1 3"/>`;
    svg += `<text x="${x(a) + 6}" y="${TOP + dy}" class="strong">${label}</text><text x="${x(a) + 6}" y="${TOP + dy + 14}">${fmt.int(a)} km</text>`;
  });
  if (peak) {
    svg += `<line x1="${peak.x}" x2="${peak.x + 40}" y1="${peak.y - 4}" y2="${peak.y - 4}" stroke="rgba(233,238,244,0.5)"/>`;
    svg += `<text x="${peak.x + 46}" y="${peak.y}" class="strong">${fmt.int(peak.n)} objects</text>`;
    svg += `<text x="${peak.x + 46}" y="${peak.y + 14}">${fmt.int(peak.a0)}–${fmt.int(peak.a1)} km, the busiest band</text>`;
  }
  svg += '</g><rect class="hit" x="0" y="0" width="100%" height="100%" fill="transparent"/></svg>';
  el.innerHTML = svg + '<div class="lp-tip hidden"></div>';
  root.querySelector('#specLegend').innerHTML = TYPE_NAMES.map((t, i) => `<span><i style="background:${TYPE_COLORS[i]}"></i>${t}</span>`).join('');

  const svgEl = el.querySelector('svg');
  const tip = el.querySelector('.lp-tip');
  svgEl.addEventListener('mousemove', (e) => {
    const r = svgEl.getBoundingClientRect();
    const vx = ((e.clientX - r.left) / r.width) * W;
    if (vx < L || vx > W - R) { tip.classList.add('hidden'); return; }
    const alt = 10 ** (lo + ((vx - L) / (W - L - R)) * (hi - lo));
    const b = Math.floor(Math.log10(alt) * bpd);
    const v = bins.get(b);
    if (!v) { tip.classList.add('hidden'); return; }
    tip.innerHTML = `<div style="color:var(--ink-3);margin-bottom:4px">${fmt.int(10 ** (b / bpd))}–${fmt.int(10 ** ((b + 1) / bpd))} km</div>
      ${TYPE_NAMES.filter((t) => v[t]).map((t) => `<div class="row"><i style="background:${colors[t]}"></i>${t}<span style="margin-left:auto">${fmt.int(v[t])}</span></div>`).join('')}
      <div class="row" style="border-top:1px solid var(--border-strong);margin-top:4px;padding-top:4px">Total<span style="margin-left:auto">${fmt.int(v.total)}</span></div>`;
    tip.style.left = `${e.clientX - el.getBoundingClientRect().left}px`;
    tip.style.top = `${e.clientY - el.getBoundingClientRect().top}px`;
    tip.classList.remove('hidden');
  });
  svgEl.addEventListener('mouseleave', () => tip.classList.add('hidden'));

  const all = totals.reduce((a, b) => a + b, 0);
  let leo = 0;
  for (const [b, v] of bins) if (10 ** ((b + 1) / bpd) <= 2000) leo += v.total;
  const geo = data.regions.find((r) => r.region_name.startsWith('GEO'))?.total_objects || 0;
  root.querySelector('#whereLead').innerHTML = `Of the <strong>${fmt.int(all)}</strong> objects with a current orbit,
    <strong>${fmt.num((leo / all) * 100, 0)} %</strong> are below 2,000 km.${peak ? ` The busiest band, ${fmt.int(peak.a0)}–${fmt.int(peak.a1)} km,
    holds <strong>${fmt.int(peak.n)}</strong> of them;` : ''} the geostationary ring 35,786 km up holds <strong>${fmt.int(geo)}</strong>.`;
  const c = data.counts;
  root.querySelector('#figs').innerHTML = [
    [c.on_orbit, 'catalogued objects in Earth orbit'], [c.debris_on_orbit, 'catalogued debris in orbit'],
    [c.rocket_bodies_on_orbit, 'catalogued rocket bodies in orbit'], [c.countries, 'countries in the catalogue'],
  ].map(([v, k]) => `<div><div class="v">${fmt.int(v)}</div><div class="k">${k}</div></div>`).join('');
}

// ---------------------------------------------------------------------------
// 02: data flow
function fillPipeline(root, data) {
  const c = data.counts;
  const weekTotal = Object.values(data.week_by_risk).reduce((a, b) => a + b, 0);
  root.querySelector('#pipeLead').textContent = `Public element sets are downloaded on a schedule, kept forever in a sharded MongoDB history,
    reduced to the latest orbit of each object in MySQL, screened with SGP4, and turned into events, alerts and reports — each step a transaction.`;
  const stages = [
    ['01', 'Sources', 'CelesTrak and Space-Track publish element sets; the SATCAT and GCAT describe each object.',
      ['CelesTrak GP · every ' + fmt.num(data.ingest_interval_hours || 4, 0) + ' h', 'Space-Track GP · hourly at most', 'SATCAT + GCAT · weekly'],
      `${fmt.int(c.with_current_orbit)} element sets`],
    ['02', 'MongoDB history', 'Every element set ever downloaded, append-only, with its original JSON and a download log.',
      ['sharded on norad_id', '2 shards × 3-node replica sets', 'MapReduce + aggregation'], 'unique (norad_id, epoch)'],
    ['03', 'MySQL catalogue', 'Objects, launches, owners and missions in 3NF; the newest element set lands in Current_Orbit in one transaction.',
      ['40 tables · 12 views', 'triggers + stored procedures', 'roles per account'], `${fmt.int(c.total)} objects`],
    ['04', 'SGP4 screening', 'Watched satellites against everything, propagated together, with each candidate refined to the millisecond.',
      ['24 h horizon', 'linear TCA refinement', `threshold ${fmt.num(data.screening_threshold_km || 10, 0)} km`],
      `${fmt.int(c.watchlist)} × ${fmt.int(c.with_current_orbit)} objects`],
    ['05', 'Events, alerts & AI', 'New events fire a trigger that alerts subscribers; an LLM agent can assess any event and recommend a manoeuvre.',
      ['Conjunction_Event + Pc', 'Alert via trigger', 'agent trace in MySQL'], `${fmt.int(weekTotal)} events this week`],
  ];
  root.querySelector('#pipe').innerHTML = `<div class="lp-flow" aria-hidden="true"><svg viewBox="0 0 1000 40" preserveAspectRatio="none">
      <path d="M0 20 H1000"/><path class="pulse" d="M0 20 H1000"/></svg></div>
    <div class="lp-pipe">${stages.map(([no, hd, p, list, kpi]) => `<div class="lp-stage"><span class="no">${no}</span><h3>${hd}</h3>
      <p>${p}</p><ul>${list.map((l) => `<li>${l}</li>`).join('')}</ul><div class="kpi">${kpi}</div></div>`).join('')}</div>`;
}

// ---------------------------------------------------------------------------
// 03: fragmentation and re-entries
function fillDebris(root, data) {
  const frags = data.fragmentations;
  const top = frags[0];
  const c = data.counts;
  root.querySelector('#debrisLead').innerHTML = top
    ? `<strong>${esc(top.name)}</strong> alone accounts for <strong>${fmt.int(top.fragments)}</strong> catalogued fragments;
       <strong>${fmt.int(top.on_orbit)}</strong> of them are still in orbit. Debris is linked to the object it broke away from,
       so every fragment's lineage is one join away.`
    : 'Debris is linked to the object it broke away from, so every fragment’s lineage is one join away.';
  const max = Math.max(...frags.map((f) => f.fragments), 1);
  const rows = frags.map((f) => `<div class="lp-fragrow">
      <div class="nm"><a href="#/object/${f.norad_id}" style="color:inherit">${esc(f.name)}</a><small>${esc(f.intl_designator || '')} · ${esc(f.object_type)}</small></div>
      <div class="lp-fragbar" title="${fmt.int(f.fragments)} catalogued, ${fmt.int(f.on_orbit)} still in orbit">
        <div class="all" style="width:${(f.fragments / max) * 100}%"></div><div class="live" style="width:${(f.on_orbit / max) * 100}%"></div></div>
      <div class="n"><b>${fmt.int(f.fragments)}</b> fragments<br>${fmt.int(f.on_orbit)} in orbit</div></div>`).join('');

  const years = data.reentries;
  const W = 560; const H = 190; const L = 36; const B = 160; const T = 16;
  const ymax = Math.max(...years.map((yy) => yy.n), 1);
  const yStep = ymax > 2000 ? 1000 : 500;
  const yTop = Math.ceil(ymax / yStep) * yStep;
  const y0 = years[0]?.year || 1957; const y1 = years[years.length - 1]?.year || 2026;
  const bw = (W - L) / (y1 - y0 + 1);
  const peak = years.reduce((a, b) => (b.n > (a?.n || 0) ? b : a), null);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Objects re-entering per year, ${y0}–${y1}">`;
  for (let v = 0; v <= yTop; v += yStep) {
    const yy = B - (v / yTop) * (B - T);
    svg += `<line x1="${L}" x2="${W}" y1="${yy}" y2="${yy}" stroke="rgba(140,170,200,${v ? 0.07 : 0.25})"/><text x="${L - 6}" y="${yy + 3.5}" text-anchor="end">${fmt.int(v)}</text>`;
  }
  years.forEach((d) => {
    const hh = (d.n / yTop) * (B - T);
    svg += `<rect x="${L + (d.year - y0) * bw + 0.5}" y="${B - hh}" width="${Math.max(bw - 1, 1)}" height="${hh}" fill="${d === peak ? '#8ad8ea' : 'rgba(233,238,244,0.55)'}"><title>${d.year}: ${fmt.int(d.n)}</title></rect>`;
  });
  [y0, 1980, 2000, y1].forEach((yr) => {
    const anchor = yr === y1 ? 'end' : yr === y0 ? 'start' : 'middle';
    svg += `<text x="${L + (yr - y0) * bw + (yr === y1 ? bw : yr === y0 ? 0 : bw / 2)}" y="${B + 18}" text-anchor="${anchor}">${yr}</text>`;
  });
  if (peak) svg += `<text x="${L + (peak.year - y0) * bw - 4}" y="${B - (peak.n / yTop) * (B - T) - 6}" text-anchor="end" style="fill:#e9eef4">${peak.year}: ${fmt.int(peak.n)}</text>`;
  svg += '</svg>';

  root.querySelector('#frag').innerHTML = `<div><h3 style="margin-bottom:14px">Largest fragmentation events in the catalogue</h3>${rows}
      <div class="lp-legend"><span><i style="background:rgba(25,158,112,0.28)"></i>catalogued fragments</span><span><i style="background:var(--t-debris)"></i>still in orbit</span></div></div>
    <div><h3>Re-entries per year</h3><div class="lp-spark">${svg}</div>
      <p class="muted small" style="margin-top:10px">${fmt.int(c.decayed)} catalogued objects have re-entered since ${y0}. ${y1} is the year to date.</p></div>`;
}

// ---------------------------------------------------------------------------
// 04: the modules, as an index, and the database roles
function fillModules(root, data, app) {
  const c = data.counts;
  const week = Object.values(data.week_by_risk).reduce((a, b) => a + b, 0);
  const ev = data.hot[0] || data.upcoming[0];
  const rows = [
    ['01', 'Close-approach screening', `The ${fmt.int(c.watchlist)} watched satellites against ${fmt.int(c.with_current_orbit)} tracked objects, every ${fmt.num(data.ingest_interval_hours || 4, 0)} hours.`, `${fmt.int(week)} this week`, '#/conjunctions', 'Close approaches'],
    ['02', '3D replay', 'Any approach replayed on the globe around its closest approach, with the separation measured live.', ev ? `#${ev.event_id}` : '', ev ? `#/globe?event=${ev.event_id}` : '#/globe', 'Replay'],
    ['03', 'Alerts', 'A MySQL trigger alerts every subscriber of either object when screening records a new approach.', '', app.user ? '#/alerts' : '#/register', app.user ? 'My alerts' : 'Create an account'],
    ['04', 'Catalogue', 'Each object with its launch, vehicle, site, owner over time, missions, and the fragments that broke away from it.', `${fmt.int(c.total)} objects`, '#/catalog', 'Browse'],
    ['05', 'Reports', 'SQL views over MySQL for the catalogue; MapReduce and aggregation over the sharded MongoDB history for trends.', '', app.can('analyst') ? '#/reports' : '#/dashboard', app.can('analyst') ? 'Reports' : 'Dashboard'],
    ['06', 'AI decision support', 'An LLM agent chooses deterministic physics tools, explains the result and recommends a manoeuvre. A human approves it.', '', '#/demo', 'Demo lab'],
    ['07', 'Access', 'Each role connects to MySQL as its own account with its own privileges, so the database enforces who sees what.', '', '#/dashboard', 'Dashboard'],
  ];
  root.querySelector('#modList').innerHTML = rows.map(([no, name, text, stat, href, label]) => `<a class="ins-mod" href="${href}">
      <span class="no">${no}</span><span class="nm">${esc(name)}</span><span class="tx">${esc(text)}</span><span class="st">${esc(stat)}</span><span class="go">${esc(label)} →</span></a>`).join('');
  root.querySelector('#roles').innerHTML = `
      <div><b>Public Viewer</b><code>r_viewer</code><p>Catalogue, orbits, close approaches, the globe; own subscriptions and alerts.</p></div>
      <div><b>Analyst</b><code>r_analyst</code><p>Everything above, plus reports, the orbital history in MongoDB, CSV exports, and manoeuvre decisions.</p></div>
      <div><b>Administrator</b><code>r_admin</code><p>Users and roles, reference data, the watchlist, thresholds, jobs and logs. No schema changes.</p></div>`;
}
