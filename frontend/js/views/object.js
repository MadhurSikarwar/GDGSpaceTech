import { csvUrl, del, get, post, qs } from '../api.js';
import { lineChart, slot } from '../charts.js';
import { age, empty, errorBox, esc, fmt, h, isoInput, loading, objLink, prov, riskBadge, table, toast, typeTag } from '../ui.js';
import { updateAlertBadge } from '../app.js';

export async function render(root, { params, app }) {
  const norad = Number(params[0]);
  root.innerHTML = loading();
  let d;
  try {
    d = await get(`/objects/${norad}`);
  } catch (err) {
    root.innerHTML = errorBox(err);
    return;
  }
  const o = d.object;
  const orbit = d.current_orbit;
  root.innerHTML = '';

  const head = h(`<div class="object-head"><div>
      <div class="eyebrow">${esc(o.object_type)}${d.watchlist ? ' · on the screening watchlist' : ''}</div>
      <h1>${esc(o.name)}</h1>
      <div class="ids"><span class="mono">NORAD ${o.norad_id}</span>${o.intl_designator ? `<span class="mono">COSPAR ${esc(o.intl_designator)}</span>` : ''}
        ${typeTag(o.object_type)}<span class="tag plain">${esc(o.status)}</span>
        ${o.region_name ? `<span class="tag plain">${esc(o.region_name)}</span>` : ''}
        ${!o.decay_date && !o.in_earth_orbit ? `<span class="tag accent">Beyond Earth orbit · centre ${esc(o.orbit_center || '?')}${o.orbit_type === 'DOC' ? ' · docked' : ''}</span>` : ''}
        ${o.data_status === 'NEA' ? '<span class="tag plain">No public elements</span>' : ''}</div></div>
      <div class="row" id="actions"></div></div>`);
  root.appendChild(head);

  const actions = head.querySelector('#actions');
  if (orbit) actions.insertAdjacentHTML('beforeend', `<a class="btn" href="#/globe?norad=${norad}">View on globe</a>`);
  const subBtn = h(`<button class="btn primary"></button>`);
  const paintSub = () => { subBtn.textContent = d.subscribed ? 'Subscribed ✓' : 'Subscribe to alerts'; subBtn.classList.toggle('primary', !d.subscribed); };
  if (app.user) {
    paintSub();
    subBtn.addEventListener('click', async () => {
      try {
        if (d.subscribed) await del(`/me/subscriptions/${norad}`); else await post('/me/subscriptions', { norad_id: norad });
        d.subscribed = !d.subscribed;
        paintSub();
        toast(d.subscribed ? `You will be alerted about close approaches of ${o.name}` : 'Unsubscribed');
        updateAlertBadge();
      } catch (err) { toast(err.message, 'error'); }
    });
    if (!o.decay_date) actions.appendChild(subBtn);
  } else if (!o.decay_date) {
    actions.insertAdjacentHTML('beforeend', `<a class="btn primary" href="#/login?next=${encodeURIComponent(location.hash)}">Log in to subscribe</a>`);
  }

  const L = d.launch;
  const grid = h(`<div class="grid cols-3"></div>`);
  root.appendChild(grid);
  grid.appendChild(h(`<section class="card"><div class="card-head"><h2>Current orbit</h2>
      <span class="sub">${orbit ? `epoch ${fmt.dt(orbit.epoch)} · age ${age(orbit.epoch)}` : ''}</span></div>
    ${orbit ? `<p style="margin:-6px 0 12px">${prov({ src: orbit.source, fetched: orbit.fetched_at, model: 'SGP4 mean elements' })}</p>` : ''}
    ${orbit ? `<dl class="facts">
      <dt>Perigee × apogee</dt><dd>${fmt.num(orbit.perigee_km, 1)} × ${fmt.num(orbit.apogee_km, 1)} km</dd>
      <dt>Mean altitude</dt><dd>${fmt.km(orbit.mean_altitude_km)}</dd>
      <dt>Inclination</dt><dd>${fmt.num(orbit.inclination, 3)}°</dd>
      <dt>Period</dt><dd>${fmt.num(orbit.period_min, 2)} min</dd>
      <dt>Eccentricity</dt><dd>${fmt.num(orbit.eccentricity, 6)}</dd>
      <dt>RAAN / arg. perigee</dt><dd>${fmt.num(orbit.raan, 2)}° / ${fmt.num(orbit.arg_perigee, 2)}°</dd>
      <dt>B* drag term</dt><dd>${Number(orbit.bstar).toExponential(3)}</dd>
      <dt>Element set</dt><dd>#${esc(orbit.element_set_no ?? '—')} · ${esc(orbit.source)}</dd>
    </dl>` : empty(o.decay_date ? `Re-entered on ${o.decay_date}` : 'No current element set',
      o.decay_date ? '' : ' This object is not in the ingested CelesTrak groups.')}</section>`));

  grid.appendChild(h(`<section class="card"><div class="card-head"><h2>Launch</h2><span class="sub mono">${esc(L?.launch_id || '')}</span></div>
    ${L ? `<dl class="facts">
      <dt>Date</dt><dd>${fmt.dt(L.launch_date)}</dd>
      <dt>Vehicle</dt><dd>${esc(L.vehicle_name || '—')}${L.vehicle_org ? `<div class="small muted">built by ${esc(L.vehicle_org)}</div>` : ''}</dd>
      <dt>Site</dt><dd>${esc(L.site_name || '—')}${L.site_country ? `<div class="small muted">${esc(L.site_country)}${L.latitude != null ? ` · ${fmt.num(L.latitude, 2)}°, ${fmt.num(L.longitude, 2)}°` : ''}</div>` : ''}</dd>
      <dt>Outcome</dt><dd>${esc(L.outcome)}</dd>
    </dl>` : empty('No launch record')}</section>`));

  grid.appendChild(h(`<section class="card"><div class="card-head"><h2>Ownership</h2><span class="sub">changes over time</span></div>
    ${d.ownership.length ? `<ul class="timeline">${d.ownership.map((w) => `<li class="${w.to_date ? '' : 'current'}">
      <div><strong>${esc(w.org_name)}</strong> <span class="small muted">${esc(w.org_type)}${w.country_name ? ` · ${esc(w.country_name)}` : ''}</span></div>
      <div class="when">${fmt.date(w.from_date)} → ${w.to_date ? fmt.date(w.to_date) : 'present'}</div></li>`).join('')}</ul>`
      : empty('No ownership record')}</section>`));

  grid.appendChild(h(`<section class="card"><div class="card-head"><h2>Missions</h2></div>
    ${d.missions.length ? d.missions.map((m) => `<div style="margin-bottom:10px"><strong>${esc(m.name)}</strong>
      <div class="small muted">${esc(m.purpose)}${m.org_name ? ` · ${esc(m.org_name)}` : ''} · ${fmt.int(m.members)} object${m.members === 1 ? '' : 's'}</div></div>`).join('')
      : empty('No mission recorded')}</section>`));

  const kids = d.children;
  grid.appendChild(h(`<section class="card"><div class="card-head"><h2>Lineage</h2><span class="sub">parent and fragments</span></div>
    ${d.parent ? `<p>${o.object_type === 'Debris' ? 'Broke away from' : 'Released from'} ${objLink(d.parent.norad_id, d.parent.name)} <span class="small muted">(${esc(d.parent.object_type)})</span></p>` : ''}
    ${kids.total ? `<p><strong>${fmt.int(kids.total)}</strong> catalogued ${kids.total === 1 ? 'piece' : 'pieces'} came from this object; ${fmt.int(kids.on_orbit)} ${kids.on_orbit === 1 ? 'is' : 'are'} still in orbit.</p>
      <div class="small">${kids.items.slice(0, 12).map((k) => objLink(k.norad_id, `${k.norad_id}`)).join(', ')}${kids.total > 12 ? ' …' : ''}</div>`
      : (d.parent ? '' : empty('No parent or fragments recorded'))}</section>`));

  const re = d.reentry;
  grid.appendChild(h(`<section class="card"><div class="card-head"><h2>Re-entry</h2></div>
    ${o.decay_date ? `<p>Re-entered on <strong>${fmt.date(o.decay_date)}</strong>.</p>`
      : re ? `<p>Predicted around <strong>${fmt.date(re.predicted_decay_date)}</strong></p>
        <p class="small muted">${fmt.num(re.days_remaining, 0)} days (10–90 %: ${fmt.num(re.lower_days, 0)}–${fmt.num(re.upper_days, 0)} days).
        Model ${esc(re.model_version)}; assumes no propulsive manoeuvres.</p>`
      : empty('No re-entry predicted', ' Not currently decaying, or not enough history yet.')}</section>`));

  const conj = h(`<section class="card flush" style="margin-top:16px"><div class="card-head"><h2>Close approaches</h2>
    <a class="small" href="#/conjunctions?norad=${norad}&when=all">All events for this object →</a></div><div></div></section>`);
  root.appendChild(conj);
  const cbox = conj.lastElementChild;
  if (!d.conjunctions.length) cbox.innerHTML = empty('No close approaches recorded', d.watchlist ? '' : ' Only watchlist objects are screened as primaries; others appear when a watched satellite passes near them.');
  else cbox.appendChild(table([
    { label: 'TCA (UTC)', render: (r) => `${fmt.dt(r.time_of_closest_approach)}<div class="small muted">${fmt.rel(r.time_of_closest_approach)}</div>` },
    { label: 'Other object', render: (r) => (r.primary_norad === norad ? objLink(r.secondary_norad, r.secondary_name) : objLink(r.primary_norad, r.primary_name)) },
    { label: 'Miss distance', num: true, render: (r) => fmt.km(r.miss_distance_km, 3) },
    { label: 'Rel. velocity', num: true, render: (r) => `${fmt.num(r.relative_velocity, 2)} km/s` },
    { label: 'Risk', render: (r) => riskBadge(r.risk_level) },
    { label: '', render: (r) => `<a class="btn sm" href="#/globe?event=${r.event_id}">Replay 3D</a>` },
  ], d.conjunctions));

  if (orbit) groundContacts(root, norad);
  if (app.can('analyst')) await altitudeHistory(root, norad, o.name);
}

async function groundContacts(root, norad) {
  const card = h(`<section class="card flush" style="margin-top:16px"><div class="card-head"><h2>Ground-station contacts</h2>
    <span class="sub">next 24 h · SvalSat, Fairbanks, McMurdo and ISRO ISTRAC · elevation mask per station</span></div><div>${loading()}</div></section>`);
  root.appendChild(card);
  const box = card.lastElementChild;
  try {
    const { items } = await get(`/objects/${norad}/passes?hours=24`);
    box.innerHTML = '';
    if (!items.length) { box.innerHTML = empty('No contacts in the next 24 hours', ' The ground track never rises above any station’s elevation mask.'); return; }
    box.appendChild(table([
      { label: 'Station', render: (p) => esc(p.station) },
      { label: 'AOS (UTC)', render: (p) => `${fmt.dt(p.aos)}<div class="small muted">${fmt.rel(p.aos)}</div>` },
      { label: 'LOS (UTC)', render: (p) => fmt.dt(p.los).slice(11) },
      { label: 'Duration', num: true, render: (p) => `${fmt.num(p.duration_min, 1)} min` },
      { label: 'Max elevation', num: true, render: (p) => `${fmt.num(p.max_elevation_deg, 1)}°` },
    ], items.slice(0, 20)));
  } catch (err) { box.innerHTML = errorBox(err); }
}

async function altitudeHistory(root, norad, name) {
  const to = new Date();
  const from = new Date(Date.now() - 730 * 86400e3);
  const card = h(`<section class="card" style="margin-top:16px"><div class="card-head"><h2>Altitude history</h2>
    <div class="row"><input type="date" name="from" value="${isoInput(from)}" style="width:150px">
      <input type="date" name="to" value="${isoInput(to)}" style="width:150px">
      <a class="btn sm" id="histCsv">Export element sets</a></div></div>
    <div id="histStats" class="small muted" style="margin-bottom:8px"></div>
    <div class="chart-box"><canvas id="histChart" aria-label="Altitude of ${esc(name)} over time"></canvas></div></section>`);
  root.appendChild(card);
  const load = async () => {
    const p = { norad, from: card.querySelector('[name=from]').value, to: card.querySelector('[name=to]').value + 'T23:59:59' };
    card.querySelector('#histCsv').href = csvUrl('/reports/history', p);
    const stats = card.querySelector('#histStats');
    try {
      const r = await get('/reports/altitude-loss' + qs(p));
      const old = window.Chart?.getChart(card.querySelector('#histChart'));
      if (old) old.destroy();
      if (r.samples < 2) {
        stats.textContent = `${r.samples} element set${r.samples === 1 ? '' : 's'} in this period — history builds up with every ingest (or import older history from Space-Track).`;
      } else {
        stats.innerHTML = `${fmt.int(r.samples)} element sets over ${fmt.num(r.span_days, 1)} days · mean altitude changed by
          <strong>${fmt.num(r.change_km, 2)} km</strong> · trend <strong>${fmt.num(r.rate_km_per_day, 3)} km/day</strong>`;
      }
      const pts = (k) => r.series.map((s) => ({ x: new Date(s.epoch).getTime(), y: s[k] }));
      lineChart(card.querySelector('#histChart'), { yTitle: 'altitude (km)', series: [
        { label: 'Apogee', points: pts('apogee_km'), color: slot(1) },
        { label: 'Mean altitude', points: pts('mean_altitude_km'), color: slot(0) },
        { label: 'Perigee', points: pts('perigee_km'), color: slot(2) },
      ] });
    } catch (err) {
      stats.innerHTML = errorBox(err);
    }
  };
  card.querySelectorAll('input[type=date]').forEach((i) => i.addEventListener('change', load));
  load();
}
