// Analytical reports (SRS 3.6) for Analysts and Administrators. Every chart has
// a table view with the exact numbers and a CSV export.
import { csvUrl, get, qs } from '../api.js';
import { barChart, destroyAll, lineChart, slot, typeColor } from '../charts.js';
import { empty, errorBox, esc, fmt, h, isoInput, loading, objLink, table, typeTag } from '../ui.js';

const TABS = [
  ['regions', 'Objects per region'],
  ['debris', 'Debris by country'],
  ['reentries', 'Re-entries per year'],
  ['regionYear', 'Region × year'],
  ['altitude', 'Altitude & decay rate'],
  ['monthly', 'Monthly altitude'],
  ['decaying', 'Fastest decaying'],
  ['reentry', 'Re-entry predictions'],
];

export async function render(root) {
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Analyst</div><h1>Reports</h1>
    <p>Relational reports run as SQL over MySQL views; long-term history is summarised by MongoDB MapReduce and
    aggregation jobs and stored back in MySQL summary tables.</p></div></div>`));
  const tabs = h(`<div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button role="tab" data-k="${k}">${l}</button>`).join('')}</div>`);
  const body = h('<div></div>');
  root.append(tabs, body);
  const show = (k) => {
    destroyAll();
    tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.k === k));
    history.replaceState(null, '', `#/reports?tab=${k}`);
    // Each tab renders into its own holder: if the user switches tabs before a slow report
    // finishes, the superseded report lands in a detached element instead of doubling up.
    const holder = h('<div></div>');
    body.replaceChildren(holder);
    REPORTS[k](holder).catch((err) => { holder.innerHTML = errorBox(err); });
  };
  tabs.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) show(b.dataset.k); });
  const initial = new URLSearchParams(location.hash.split('?')[1] || '').get('tab');
  show(TABS.some(([k]) => k === initial) ? initial : 'regions');
}

// A card with a chart / table toggle and a CSV link.
function reportCard(body, { title, sub = '', csv }) {
  const card = h(`<section class="card"><div class="card-head"><div><h2>${title}</h2><div class="sub">${sub}</div></div>
    <div class="row"><div class="view-toggle" role="group"><button data-v="chart" class="active">Chart</button><button data-v="table">Table</button></div>
    ${csv ? `<a class="btn sm" href="${csv}">Export CSV</a>` : ''}</div></div>
    <div class="chart-box tall"><canvas></canvas></div><div class="tbl hidden"></div></section>`);
  body.appendChild(card);
  card.querySelector('.view-toggle').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    card.querySelectorAll('.view-toggle button').forEach((x) => x.classList.toggle('active', x === b));
    card.querySelector('.chart-box').classList.toggle('hidden', b.dataset.v !== 'chart');
    card.querySelector('.tbl').classList.toggle('hidden', b.dataset.v !== 'table');
  });
  return { card, canvas: card.querySelector('canvas'), tbl: card.querySelector('.tbl') };
}

const typeSeries = (rows, keys) => keys.map(([t, k]) => ({ label: t, data: rows.map((r) => r[k]), color: typeColor(t) }));
const TYPE_KEYS = [['Payload', 'payloads'], ['Rocket Body', 'rocket_bodies'], ['Debris', 'debris'], ['Unknown', 'unknown']];

function objectPicker(body, onLoad, { days = 730, dates = true } = {}) {
  const form = h(`<form class="filters">
    <label class="field"><span>NORAD number</span><input type="number" name="norad" min="1" value="25544" required></label>
    ${dates ? `<label class="field"><span>From</span><input type="date" name="from" value="${isoInput(new Date(Date.now() - days * 86400e3))}"></label>
    <label class="field"><span>To</span><input type="date" name="to" value="${isoInput(new Date())}"></label>` : ''}
    <div class="field"><span>&nbsp;</span><button class="btn primary">Run report</button></div></form>`);
  body.appendChild(form);
  const out = h('<div></div>');
  body.appendChild(out);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    destroyAll();
    const p = { norad: form.norad.value };
    if (dates) { p.from = form.from.value; p.to = `${form.to.value}T23:59:59`; }
    out.innerHTML = loading();
    onLoad(p, out).catch((err) => { out.innerHTML = errorBox(err); });
  });
  form.requestSubmit();
}

const REPORTS = {
  async regions(body) {
    const { items } = await get('/reports/regions');
    const { canvas, tbl } = reportCard(body, { title: 'Number of objects in each orbital region',
      sub: 'Current_Orbit joined with the Orbit_Region altitude bands (mean altitude); region is never stored per object',
      csv: csvUrl('/reports/regions') });
    barChart(canvas, { labels: items.map((r) => r.region_name), horizontal: true, stacked: true, xTitle: 'objects',
      series: typeSeries(items, TYPE_KEYS) });
    tbl.appendChild(table([
      { label: 'Region', key: 'region_name' },
      { label: 'Band (km)', num: true, render: (r) => `${fmt.int(r.min_altitude_km)}–${fmt.int(r.max_altitude_km)}` },
      { label: 'Payloads', num: true, render: (r) => fmt.int(r.payloads) },
      { label: 'Rocket bodies', num: true, render: (r) => fmt.int(r.rocket_bodies) },
      { label: 'Debris', num: true, render: (r) => fmt.int(r.debris) },
      { label: 'Unknown', num: true, render: (r) => fmt.int(r.unknown) },
      { label: 'Total', num: true, render: (r) => `<strong>${fmt.int(r.total_objects)}</strong>` },
    ], items));
  },

  async debris(body) {
    const controls = h(`<div class="row" style="margin-bottom:12px"><span class="small muted">Rank by</span>
      <div class="view-toggle" role="group"><button data-s="" class="active">All debris in orbit</button><button data-s="leo">Debris in LEO</button></div></div>`);
    body.appendChild(controls);
    const holder = h('<div></div>');
    body.appendChild(holder);
    const draw = async (sort) => {
      destroyAll();
      holder.innerHTML = loading();
      const { items } = await get('/reports/debris-by-country' + qs({ sort }));
      holder.innerHTML = '';
      const top = items.slice(0, 15);
      const key = sort === 'leo' ? 'debris_in_leo' : 'debris_on_orbit';
      const { canvas, tbl } = reportCard(holder, { title: 'Debris owned by each country',
        sub: 'Debris still in orbit, by the country of its current owner (Space_Object → Object_Ownership → Organisation → Country). Top 15 shown.',
        csv: csvUrl('/reports/debris-by-country', { sort }) });
      barChart(canvas, { labels: top.map((r) => r.country_name), horizontal: true, xTitle: sort === 'leo' ? 'debris pieces in LEO' : 'debris pieces in orbit',
        series: [{ label: 'Debris', data: top.map((r) => r[key]), color: typeColor('Debris') }] });
      tbl.appendChild(table([
        { label: 'Country', render: (r) => esc(r.country_name) },
        { label: 'Debris in orbit', num: true, render: (r) => fmt.int(r.debris_on_orbit) },
        { label: 'In LEO', num: true, render: (r) => fmt.int(r.debris_in_leo) },
        { label: 'With current orbit', num: true, render: (r) => fmt.int(r.debris_with_current_orbit) },
      ], items));
    };
    controls.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      controls.querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
      draw(b.dataset.s).catch((err) => { holder.innerHTML = errorBox(err); });
    });
    await draw('');
  },

  async reentries(body) {
    const { items } = await get('/reports/reentries-per-year');
    const { canvas, tbl } = reportCard(body, { title: 'Re-entries per year', sub: 'Objects whose decay date falls in each year',
      csv: csvUrl('/reports/reentries-per-year') });
    barChart(canvas, { labels: items.map((r) => r.year), stacked: true, yTitle: 're-entries', series: typeSeries(items, TYPE_KEYS) });
    tbl.appendChild(table([
      { label: 'Year', key: 'year' },
      { label: 'Payloads', num: true, render: (r) => fmt.int(r.payloads) },
      { label: 'Rocket bodies', num: true, render: (r) => fmt.int(r.rocket_bodies) },
      { label: 'Debris', num: true, render: (r) => fmt.int(r.debris) },
      { label: 'Total', num: true, render: (r) => `<strong>${fmt.int(r.reentries)}</strong>` },
    ], [...items].reverse()));
  },

  async regionYear(body) {
    const { items } = await get('/reports/region-year');
    if (!items.length) { body.innerHTML = empty('No summary yet', ' Run the aggregation job (Admin → Jobs).'); return; }
    const years = [...new Set(items.map((r) => r.year))].sort();
    const regions = [...new Map(items.map((r) => [r.region_id, r.region_name])).entries()];
    const { canvas, tbl } = reportCard(body, { title: 'Objects per orbital region per year',
      sub: 'MongoDB aggregation pipeline over the full element-set history; region bands taken from MySQL; stored in summary_region_year',
      csv: csvUrl('/reports/region-year') });
    barChart(canvas, { labels: years, stacked: true, yTitle: 'objects',
      series: regions.map(([id, n], i) => ({ label: n, color: slot(i),
        data: years.map((y) => items.find((r) => r.year === y && r.region_id === id)?.object_count || 0) })) });
    tbl.appendChild(table([{ label: 'Year', key: 'year' }, { label: 'Region', key: 'region_name' },
      { label: 'Objects', num: true, render: (r) => fmt.int(r.object_count) }, { label: 'Computed', render: (r) => fmt.dt(r.computed_at) }], items));
  },

  async altitude(body) {
    objectPicker(body, async (p, out) => {
      const r = await get('/reports/altitude-loss' + qs(p));
      out.innerHTML = '';
      const tiles = h(`<div class="tiles">
        <div class="tile"><div class="label">Object</div><div class="value" style="font-size:18px">${objLink(r.norad_id, r.name)}</div></div>
        <div class="tile"><div class="label">Element sets</div><div class="value">${fmt.int(r.samples)}</div><div class="foot">${r.span_days ? `${fmt.num(r.span_days, 1)} days` : ''}</div></div>
        <div class="tile"><div class="label">Altitude change</div><div class="value">${r.change_km != null ? fmt.km(r.change_km, 2) : '—'}</div></div>
        <div class="tile"><div class="label">Rate of altitude loss</div><div class="value">${r.rate_km_per_day != null ? `${fmt.num(-r.rate_km_per_day, 3)}` : '—'}</div>
          <div class="foot">km/day (least-squares trend)${r.rate_km_per_year != null ? ` · ${fmt.num(-r.rate_km_per_year, 1)} km/yr` : ''}</div></div></div>`);
      out.appendChild(tiles);
      if (r.samples < 2) {
        out.appendChild(h(`<div class="card">${empty('Not enough history in this period', ' History grows with every ingest; older history can be imported from Space-Track (Admin → Jobs).')}</div>`));
        return;
      }
      const { canvas, tbl } = reportCard(out, { title: `Altitude of ${esc(r.name)}`, sub: 'From the MongoDB orbit_history collection',
        csv: csvUrl('/reports/history', p) });
      const pts = (k) => r.series.map((s) => ({ x: new Date(s.epoch).getTime(), y: s[k] }));
      lineChart(canvas, { yTitle: 'altitude (km)', series: [
        { label: 'Apogee', points: pts('apogee_km'), color: slot(1) },
        { label: 'Mean altitude', points: pts('mean_altitude_km'), color: slot(0) },
        { label: 'Perigee', points: pts('perigee_km'), color: slot(2) }] });
      tbl.appendChild(table([{ label: 'Epoch', render: (s) => fmt.dt(s.epoch) },
        { label: 'Mean altitude', num: true, render: (s) => fmt.num(s.mean_altitude_km, 3) },
        { label: 'Perigee', num: true, render: (s) => fmt.num(s.perigee_km, 3) },
        { label: 'Apogee', num: true, render: (s) => fmt.num(s.apogee_km, 3) }], [...r.series].reverse().slice(0, 500)));
    });
  },

  async monthly(body) {
    objectPicker(body, async (p, out) => {
      const { items } = await get('/reports/monthly-altitude' + qs({ norad: p.norad }));
      out.innerHTML = '';
      if (!items.length) { out.appendChild(h(`<div class="card">${empty('No monthly summary for this object', ' The MapReduce job summarises the history nightly (or run “aggregation” from Admin → Jobs).')}</div>`)); return; }
      const { canvas, tbl } = reportCard(out, { title: `Monthly average altitude · NORAD ${esc(p.norad)}`,
        sub: 'MongoDB MapReduce (map: emit per object-month; reduce: sum/count/min/max; finalize: average) → summary_monthly_altitude',
        csv: csvUrl('/reports/monthly-altitude', { norad: p.norad }) });
      barChart(canvas, { labels: items.map((r) => String(r.month).slice(0, 7)), yTitle: 'mean altitude (km)',
        series: [{ label: 'Average altitude', data: items.map((r) => r.avg_altitude_km), color: slot(0) }] });
      tbl.appendChild(table([{ label: 'Month', render: (r) => String(r.month).slice(0, 7) },
        { label: 'Average', num: true, render: (r) => fmt.num(r.avg_altitude_km, 2) },
        { label: 'Min', num: true, render: (r) => fmt.num(r.min_altitude_km, 2) },
        { label: 'Max', num: true, render: (r) => fmt.num(r.max_altitude_km, 2) },
        { label: 'Element sets', num: true, render: (r) => fmt.int(r.samples) }], items));
    }, { dates: false });
  },

  async decaying(body) {
    const { items } = await get('/reports/fastest-decaying?days=30');
    const card = h(`<section class="card flush"><div class="card-head"><div><h2>Objects losing altitude fastest</h2>
      <div class="sub">Change in mean altitude over the last 30 days, from a MongoDB aggregation over the history</div></div>
      <a class="btn sm" href="${csvUrl('/reports/fastest-decaying', { days: 30 })}">Export CSV</a></div><div></div></section>`);
    body.appendChild(card);
    if (!items.length) { card.lastElementChild.innerHTML = empty('Not enough history yet', ' Needs at least three element sets per object across a day or more.'); return; }
    card.lastElementChild.appendChild(table([
      { label: 'Object', render: (r) => objLink(r.norad_id, r.name) }, { label: 'Type', render: (r) => typeTag(r.object_type) },
      { label: 'Altitude now', num: true, render: (r) => fmt.km(r.current_altitude_km, 1) },
      { label: 'Rate', num: true, render: (r) => `${fmt.num(-r.rate_km_per_day, 3)} km/day` },
      { label: 'Element sets', num: true, render: (r) => fmt.int(r.samples) }], items));
  },

  async reentry(body) {
    const { items } = await get('/reentry?limit=500');
    const card = h(`<section class="card flush"><div class="card-head"><div><h2>Re-entry predictions</h2>
      <div class="sub">Gradient-boosted regression trained on objects that already re-entered; quantile models give the 10–90 % interval</div></div></div><div></div></section>`);
    body.appendChild(card);
    if (!items.length) { card.lastElementChild.innerHTML = empty('No predictions yet', ' Train the model (Admin → Jobs → reentry_train) after importing re-entered objects’ history.'); return; }
    card.lastElementChild.appendChild(table([
      { label: 'Object', render: (r) => objLink(r.norad_id, r.name) }, { label: 'Type', render: (r) => typeTag(r.object_type) },
      { label: 'Altitude', num: true, render: (r) => fmt.km(r.mean_altitude_km, 0) },
      { label: 'Days left', num: true, render: (r) => fmt.num(r.days_remaining, 0) },
      { label: '10–90 %', num: true, render: (r) => `${fmt.num(r.lower_days, 0)}–${fmt.num(r.upper_days, 0)}` },
      { label: 'Expected', render: (r) => fmt.date(r.predicted_decay_date) }, { label: 'Model', render: (r) => esc(r.model_version) }], items));
  },
};
