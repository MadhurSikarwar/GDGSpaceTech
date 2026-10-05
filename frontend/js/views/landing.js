// Landing page: the site opens on the live 3D globe. Everything shown is real:
// the catalogue, the propagated positions, the upcoming close approaches and
// the statistics all come from /api/landing and /api/visual.
import { get } from '../api.js';
import { CatalogCloud, CESIUM_VERSION, createViewer, loadCesium, RISK_COLORS, ringCanvas, sampledOrbit, TYPE_COLORS,
  TYPE_NAMES } from '../globe-core.js';
import { esc, fmt, riskBadge } from '../ui.js';

const SPEEDS = [1, 30, 120];
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

const pad = (n, w = 2) => String(Math.floor(n)).padStart(w, '0');
function tminus(ms) {
  if (ms <= 0) return 'T+00:00:00';
  const s = ms / 1000;
  const d = Math.floor(s / 86400);
  const core = `${pad((s % 86400) / 3600)}:${pad((s % 3600) / 60)}:${pad(s % 60)}`;
  return `T−${d ? `${d}d ` : ''}${core}`;
}
const deg = (v, pos, neg, w = 2) => `${Math.abs(v).toFixed(2).padStart(w + 3, '0')}°${v >= 0 ? pos : neg}`;
const latlon = (lat, lon) => `${deg(lat, 'N', 'S')} ${deg(lon, 'E', 'W', 3)}`;
const utc = (d) => d.toISOString().slice(11, 19);
const iso = (s) => new Date(s.endsWith('Z') ? s : `${s}Z`);

export async function render(root, { app }) {
  root.innerHTML = template();
  const $ = (s) => root.querySelector(s);
  const timers = [];
  const cleanups = [];
  const boot = $('#lpBoot');
  const bootLine = (html) => boot.insertAdjacentHTML('beforeend', `<div>${html}</div>`);
  const hideBoot = () => { boot.style.opacity = '0'; setTimeout(() => boot.remove(), 900); };
  timers.push(setTimeout(hideBoot, 9000));  // never linger, whatever happens

  // live wall clock in the kicker
  const clock = () => { $('#kClock').textContent = `${utc(new Date())} UTC`; };
  clock();
  timers.push(setInterval(clock, 1000));

  // The transparent top bar turns solid once the hero scrolls away.
  const onScroll = () => document.body.classList.toggle('scrolled', window.scrollY > window.innerHeight * 0.6);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  cleanups.push(() => { window.removeEventListener('scroll', onScroll); document.body.classList.remove('scrolled'); });

  // reveal-on-scroll
  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
  }), { rootMargin: '0px 0px -8% 0px' });
  root.querySelectorAll('.reveal').forEach((el) => io.observe(el));
  cleanups.push(() => io.disconnect());

  let data;
  try {
    data = await get('/landing');
  } catch (err) {
    bootLine(`<b>!</b> ${esc(err.message)}`);
    return () => { timers.forEach(clearInterval); cleanups.forEach((f) => f()); };
  }
  if (!root.isConnected) return undefined;
  bootLine(`› catalogue ··········· <b>${fmt.int(data.counts.total)}</b> objects`);
  fillHero(root, data, app, timers);
  fillSpectrum(root, data);
  fillBento(root, data, app);
  fillPipeline(root, data);
  fillDebris(root, data);
  fillCta(root, data, app);

  // ---- the globe ------------------------------------------------------
  let viewer;
  let cloud;
  try {
    const C = await loadCesium();
    if (!root.isConnected) return undefined;
    bootLine(`› cesiumjs ${CESIUM_VERSION} ······· <b>ready</b>`);
    viewer = createViewer(C, $('#lpGlobe'), { widgets: false, interactive: false });
    const hero = startHero(C, viewer, root, data, (n) => {
      bootLine(`› sgp4 ················ <b>${fmt.int(n)}</b> objects propagated`);
      setTimeout(hideBoot, 1200);
    });
    cloud = hero.cloud;
    timers.push(...hero.timers);
    // Stop rendering the globe while the hero is scrolled out of view.
    const vis = new IntersectionObserver(([e]) => { viewer.useDefaultRenderLoop = e.isIntersecting; }, { threshold: 0 });
    vis.observe($('.lp-hero'));
    cleanups.push(() => vis.disconnect());
  } catch (err) {
    bootLine(`<b>!</b> ${esc(err.message)}`);
  }

  return () => {
    timers.forEach(clearInterval);
    cleanups.forEach((f) => f());
    if (cloud) cloud.destroy();
    if (viewer && !viewer.isDestroyed()) viewer.destroy();
  };
}

// ---------------------------------------------------------------------------
function template() {
  return `
  <div class="lp-globe" id="lpGlobe" aria-hidden="true"></div>
  <div class="lp-shade" aria-hidden="true"></div>
  <div class="lp-boot" id="lpBoot" aria-hidden="true"><div>orbitwatch // system start</div></div>

  <section class="lp-hero" aria-label="Live tracking">
    <div class="lp-hud" aria-hidden="true">
      <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
      <div class="readout r-tl" id="hudCam">camera —</div>
      <div class="readout r-br" id="hudFrame">earth-fixed frame · sgp4 / wgs-72</div>
    </div>
    <div class="lp-copy">
      <div class="lp-kicker"><span class="lp-live">Live</span><span class="lp-sep">/</span><span id="kClock">--:--:-- UTC</span>
        <span class="lp-sep">/</span><span>Satellite &amp; debris tracking</span></div>
      <h1 class="lp-title"><span class="count" id="hCount">——</span> objects<br><span class="dim">in Earth orbit.</span></h1>
      <p class="lp-lead">OrbitWatch tracks every catalogued satellite, rocket body and debris fragment, keeps the history of every
        orbit, and screens watched satellites for close approaches every <span id="hInterval">few</span> hours.</p>
      <div class="lp-ctas">
        <a class="btn primary lg" href="#/globe">Explore the live globe <span class="arrow">→</span></a>
        <a class="btn lg ghost-line" href="#/catalog">Search the catalogue</a>
      </div>
    </div>
    <aside>
      <div class="lp-panel" id="nextPanel"><div class="lp-panel-head"><span>Next close approach</span><span>—</span></div></div>
      <div class="lp-panel" id="issPanel"><div class="lp-panel-head"><span>Tracking</span><span>—</span></div></div>
    </aside>
    <div class="lp-ticker" aria-label="Upcoming close approaches">
      <div class="lp-ticker-label">Upcoming approaches</div>
      <div class="lp-ticker-viewport"><div class="lp-ticker-track" id="ticker"></div></div>
    </div>
  </section>

  <section class="lp-section grid-bg" id="where">
    <div class="lp-inner">
      <div class="lp-sec-head reveal"><div class="lp-index">01 / Altitude</div>
        <div><h2>Orbits cluster where they are useful.</h2><p id="whereLead"></p></div></div>
      <div class="lp-spectrum reveal" id="spectrum"></div>
      <div class="lp-legend" id="specLegend"></div>
      <div class="lp-figs reveal" id="figs"></div>
    </div>
  </section>

  <section class="lp-section" id="features">
    <div class="lp-inner">
      <div class="lp-sec-head reveal"><div class="lp-index">02 / Capabilities</div>
        <div><h2>A catalogue, a history and a watch, in one system.</h2>
        <p>Each panel below is a part of OrbitWatch, shown with the live data behind it.</p></div></div>
      <div class="lp-bento" id="bento"></div>
    </div>
  </section>

  <section class="lp-section grid-bg" id="pipeline">
    <div class="lp-inner">
      <div class="lp-sec-head reveal"><div class="lp-index">03 / Data flow</div>
        <div><h2>From a public element set to an alert.</h2><p id="pipeLead"></p></div></div>
      <div class="reveal" id="pipe"></div>
    </div>
  </section>

  <section class="lp-section" id="debris">
    <div class="lp-inner">
      <div class="lp-sec-head reveal"><div class="lp-index">04 / Fragmentation</div>
        <div><h2>Most debris comes from a few break-ups.</h2><p id="debrisLead"></p></div></div>
      <div class="lp-frag reveal" id="frag"></div>
    </div>
  </section>

  <section class="lp-cta" id="cta"></section>
  <footer class="lp-footer" id="footer"></footer>`;
}

// ---------------------------------------------------------------------------
function fillHero(root, data, app, timers) {
  const $ = (s) => root.querySelector(s);
  const c = data.counts;
  $('#hCount').textContent = fmt.int(c.on_orbit);
  if (data.ingest_interval_hours) $('#hInterval').textContent = fmt.num(data.ingest_interval_hours, 0);

  const ev = data.hot[0] || data.upcoming[0];
  const next = $('#nextPanel');
  if (ev) {
    const tca = iso(ev.time_of_closest_approach);
    next.innerHTML = `<div class="lp-panel-head"><span>Next ${ev.risk_level === 'CRITICAL' ? 'critical' : 'high-risk'} approach</span>${riskBadge(ev.risk_level)}</div>
      <div class="lp-countdown" id="countdown"></div>
      <div class="lp-pair"><a href="#/object/${ev.primary_norad}">${esc(ev.primary_name)}</a><span class="x">×</span><a href="#/object/${ev.secondary_norad}">${esc(ev.secondary_name)}</a></div>
      <dl class="lp-metrics">
        <div><dt>Miss distance</dt><dd>${fmt.num(ev.miss_distance_km, 3)} km</dd></div>
        <div><dt>Rel. velocity</dt><dd>${fmt.num(ev.relative_velocity, 2)} km/s</dd></div>
        <div><dt>TCA</dt><dd>${utc(tca)} UTC</dd></div>
        <div><dt>Location</dt><dd>${ev.tca_lat != null ? latlon(ev.tca_lat, ev.tca_lon) : '—'}</dd></div>
      </dl>
      <div class="row" style="margin-top:14px;justify-content:space-between">
        <a class="small mono" href="#/globe?event=${ev.event_id}">REPLAY IN 3D →</a>
        <a class="small mono" href="#/conjunctions">ALL ${fmt.int(Object.values(data.week_by_risk).reduce((a, b) => a + b, 0))} THIS WEEK →</a></div>`;
    const tick = () => { const el = root.querySelector('#countdown'); if (el) el.textContent = tminus(tca - Date.now()); };
    tick();
    timers.push(setInterval(tick, 1000));
  } else {
    next.innerHTML = `<div class="lp-panel-head"><span>Next close approach</span></div><p class="muted small">No approaches predicted yet — the screening job has not run.</p>`;
  }

  const s = data.spotlight;
  const iss = $('#issPanel');
  if (s) {
    iss.innerHTML = `<div class="lp-panel-head"><span>Tracking · ${esc(s.name)}</span><span class="mono">${s.norad_id}</span></div>
      <dl class="lp-telemetry">
        <dt>Sub-point</dt><dd id="issLL">—</dd>
        <dt>Altitude</dt><dd id="issAlt">—</dd>
        <dt>Perigee × apogee</dt><dd>${fmt.num(s.perigee_km, 1)} × ${fmt.num(s.apogee_km, 1)} km</dd>
        <dt>Inclination</dt><dd>${fmt.num(s.inclination, 3)}°</dd>
        <dt>Period</dt><dd>${fmt.num(s.period_min, 2)} min</dd>
        <dt>Sim time</dt><dd id="issT">—</dd>
      </dl>`;
  } else {
    iss.remove();
  }

  const items = data.upcoming.map((e) => `<a class="lp-tick" href="#/conjunctions?event=${e.event_id}">
      <span class="t">${utc(iso(e.time_of_closest_approach))}Z</span>
      <span>${esc(e.primary_name)} <span class="muted">×</span> ${esc(e.secondary_name)}</span>
      <span class="km">${fmt.num(e.miss_distance_km, 2)} km</span>${riskBadge(e.risk_level)}</a>`).join('');
  $('#ticker').innerHTML = items + items;  // doubled for a seamless loop
}

// ---------------------------------------------------------------------------
function startHero(C, viewer, root, data, onFirstLoad) {
  const $ = (s) => root.querySelector(s);
  const timers = [];
  let speed = REDUCED ? 1 : 30;
  viewer.clock.currentTime = C.JulianDate.now();
  viewer.clock.multiplier = speed;
  viewer.clock.shouldAnimate = true;

  let first = true;
  let paintFrame = () => {};
  const cloud = new CatalogCloud(C, viewer, { span: 240, pixelSize: 2.2, onLoad: (cl) => {
    if (first) { first = false; onFirstLoad(cl.order.length); }
    paintFrame();
  } });

  const wide = window.innerWidth > 1100;
  viewer.camera.setView({ destination: C.Cartesian3.fromDegrees(74, 14, wide ? 19_000_000 : 30_000_000) });
  if (wide) viewer.camera.lookLeft(0.17);   // Earth to the right of the copy

  let last = performance.now();
  viewer.scene.preRender.addEventListener(() => {
    const now = performance.now();
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    if (!REDUCED) viewer.camera.rotate(C.Cartesian3.UNIT_Z, -0.01 * dt);
  });

  // Pulsing rings where upcoming high-risk encounters will happen (position at TCA).
  const ring = ringCanvas('#ffffff');
  data.hot.forEach((e, i) => {
    if (e.tca_lat == null) return;
    const color = C.Color.fromCssColorString(RISK_COLORS[e.risk_level]);
    const phase = i / 7;
    const cycle = () => ((performance.now() / 1900) + phase) % 1;
    viewer.entities.add({
      position: C.Cartesian3.fromDegrees(e.tca_lon, e.tca_lat, e.tca_alt_km * 1000),
      point: { pixelSize: 4, color },
      billboard: { image: ring, color: new C.CallbackProperty(() => color.withAlpha(REDUCED ? 0.8 : 1 - cycle()), false),
        scale: new C.CallbackProperty(() => (REDUCED ? 0.4 : 0.18 + cycle() * 0.75), false) },
      label: i < 3 && window.innerWidth > 900 ? { text: `${e.primary_name} × ${e.secondary_name}`, font: '500 11px "IBM Plex Mono", monospace',
        fillColor: C.Color.fromCssColorString('#e9eef4'), showBackground: true,
        backgroundColor: C.Color.fromCssColorString('#06080b').withAlpha(0.75), backgroundPadding: new C.Cartesian2(6, 4),
        pixelOffset: new C.Cartesian2(14, -14), horizontalOrigin: C.HorizontalOrigin.LEFT, scale: 1 } : undefined,
    });
  });

  // Featured orbits (ISS and a sun-synchronous watched satellite) glide along a glowing track.
  const accent = C.Color.fromCssColorString('#ff6b2c');
  const featured = [];
  const draw = async (norad, k) => {
    try {
      const o = await sampledOrbit(C, norad, viewer.clock.currentTime, 2);
      if (viewer.isDestroyed()) return;
      if (featured[k]) viewer.entities.remove(featured[k].entity);
      const label = norad === 25544 ? 'ISS' : (data.spotlight?.norad_id === norad ? data.spotlight.name : `NORAD ${norad}`);
      const entity = viewer.entities.add({
        position: o.prop,
        point: { pixelSize: k === 0 ? 7 : 5, color: C.Color.WHITE, outlineColor: accent, outlineWidth: 2 },
        label: k === 0 ? { text: label, font: '600 11px "IBM Plex Mono", monospace', fillColor: C.Color.WHITE,
          pixelOffset: new C.Cartesian2(10, -10), horizontalOrigin: C.HorizontalOrigin.LEFT } : undefined,
        path: { leadTime: o.periodS, trailTime: o.periodS * 0.12, width: k === 0 ? 3 : 2, resolution: 30,
          material: new C.PolylineGlowMaterialProperty({ glowPower: 0.2, taperPower: 1, color: accent.withAlpha(k === 0 ? 0.95 : 0.55) }) },
      });
      featured[k] = { entity, until: C.JulianDate.addSeconds(o.start, o.periodS * 0.95, new C.JulianDate()) };
    } catch { /* decorative */ }
  };
  data.featured.slice(0, 2).forEach((n, k) => draw(n, k));
  timers.push(setInterval(() => {
    data.featured.slice(0, 2).forEach((n, k) => {
      const f = featured[k];
      if (f && C.JulianDate.greaterThan(viewer.clock.currentTime, f.until)) draw(n, k);
    });
  }, 2000));

  // Telemetry and HUD readouts.
  const issId = data.spotlight?.norad_id;
  timers.push(setInterval(() => {
    if (viewer.isDestroyed()) return;
    const t = viewer.clock.currentTime;
    const p = issId ? cloud.positionOf(issId, t) : null;
    if (p) {
      const g = C.Cartographic.fromCartesian(p);
      const ll = root.querySelector('#issLL');
      if (ll) {
        ll.textContent = latlon(C.Math.toDegrees(g.latitude), C.Math.toDegrees(g.longitude));
        root.querySelector('#issAlt').textContent = `${fmt.num(g.height / 1000, 1)} km`;
        root.querySelector('#issT').textContent = `${utc(C.JulianDate.toDate(t))} UTC`;
      }
    }
    const cam = viewer.camera.positionCartographic;
    $('#hudCam').textContent = `cam ${latlon(C.Math.toDegrees(cam.latitude), C.Math.toDegrees(cam.longitude))} · ${fmt.int(cam.height / 1000)} km`;
  }, 250));

  const frame = $('#hudFrame');
  paintFrame = () => {
    const sw = data.space_weather;
    frame.innerHTML = `earth-fixed frame · sgp4 / wgs-72${sw ? `<br>space weather kp ${fmt.num(sw.kp, 1)} · f10.7 ${fmt.num(sw.f107, 0)}` : ''}<br>${fmt.int(cloud.order.length || data.counts.with_current_orbit)} objects · time
      ${SPEEDS.map((s) => `<button data-speed="${s}" style="pointer-events:auto;background:none;border:0;padding:0 4px;cursor:pointer;font:inherit;color:${s === speed ? '#ff8a55' : 'inherit'}">×${s}</button>`).join('')}`;
  };
  paintFrame();
  frame.addEventListener('click', (e) => {
    const b = e.target.closest('[data-speed]');
    if (!b) return;
    speed = Number(b.dataset.speed);
    viewer.clock.multiplier = speed;
    if (speed === 1) viewer.clock.currentTime = C.JulianDate.now();
    paintFrame();
  });
  return { cloud, timers };
}

// ---------------------------------------------------------------------------
// 01: altitude spectrum — every object with a current orbit, log-binned by mean altitude.
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
  // regime bands
  const bands = [[150, 2000, 'LEO'], [2000, 35586, 'MEO'], [35586, 35986, ''], [35986, 60000, 'HEO']];
  bands.forEach(([a, b, name], i) => {
    svg += `<rect x="${x(a)}" y="${TOP - 24}" width="${x(b) - x(a)}" height="${BASE - TOP + 24}" fill="${i % 2 ? 'rgba(140,170,200,0.025)' : 'transparent'}"/>`;
    if (name) svg += `<text class="band-label" x="${x(a) + 8}" y="${TOP - 10}">${name}</text>`;
    svg += `<line x1="${x(a)}" x2="${x(a)}" y1="${TOP - 24}" y2="${BASE}" stroke="rgba(140,170,200,0.12)" stroke-dasharray="2 4"/>`;
  });
  // gridlines + y labels
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
  // stacked bars, 2px surface gap between neighbours
  let peak = null;
  for (const [b, v] of [...bins.entries()].sort((p, q) => p[0] - q[0])) {
    const a0 = 10 ** (b / bpd); const a1 = 10 ** ((b + 1) / bpd);
    if (a1 < 150 || a0 > 60000) continue;
    const x0 = x(Math.max(a0, 150)) + 1; const w = Math.max(x(Math.min(a1, 60000)) - x(Math.max(a0, 150)) - 2, 1);
    let acc = 0;
    for (const t of TYPE_NAMES) {
      const n = v[t] || 0;
      if (!n) continue;
      const h = Math.max(((n) / yMax) * (BASE - TOP), 1.5);
      svg += `<rect x="${x0}" y="${BASE - acc - h}" width="${w}" height="${h}" fill="${colors[t]}"/>`;
      acc += h;
    }
    if (!peak || v.total > peak.n) peak = { n: v.total, a0, a1, x: x0 + w / 2, y: BASE - acc };
  }
  // annotations
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

  // hover read-out
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
      ${TYPE_NAMES.filter((t) => v[t]).map((t, i) => `<div class="row"><i style="background:${colors[t]}"></i>${t}<span style="margin-left:auto">${fmt.int(v[t])}</span></div>`).join('')}
      <div class="row" style="border-top:1px solid var(--border-strong);margin-top:4px;padding-top:4px">Total<span style="margin-left:auto">${fmt.int(v.total)}</span></div>`;
    tip.style.left = `${e.clientX - el.getBoundingClientRect().left}px`;
    tip.style.top = `${e.clientY - el.getBoundingClientRect().top}px`;
    tip.classList.remove('hidden');
  });
  svgEl.addEventListener('mouseleave', () => tip.classList.add('hidden'));

  // lead + figures
  const all = totals.reduce((a, b) => a + b, 0);
  let leo = 0;
  for (const [b, v] of bins) if (10 ** ((b + 1) / bpd) <= 2000) leo += v.total;
  const geo = data.regions.find((r) => r.region_name.startsWith('GEO'))?.total_objects || 0;
  root.querySelector('#whereLead').innerHTML = `Of the <strong>${fmt.int(all)}</strong> objects with a current orbit,
    <strong>${fmt.num((leo / all) * 100, 0)} %</strong> are below 2,000 km.${peak ? ` The busiest band, ${fmt.int(peak.a0)}–${fmt.int(peak.a1)} km,
    holds <strong>${fmt.int(peak.n)}</strong> of them;` : ''} the geostationary ring 35,786 km up holds <strong>${fmt.int(geo)}</strong>.`;
  const c = data.counts;
  root.querySelector('#figs').innerHTML = [
    [c.on_orbit, 'objects in orbit'], [c.debris_on_orbit, 'debris fragments in orbit'],
    [c.rocket_bodies_on_orbit, 'spent rocket bodies in orbit'], [c.countries, 'countries in the catalogue'],
  ].map(([v, k]) => `<div><div class="v">${fmt.int(v)}</div><div class="k">${k}</div></div>`).join('');
}

// ---------------------------------------------------------------------------
// 02: capability panels, each with live data from the page it links to.
function fillBento(root, data, app) {
  const c = data.counts;
  const week = data.week_by_risk;
  const weekTotal = Object.values(week).reduce((a, b) => a + b, 0) || 1;
  const ev = data.hot[0] || data.upcoming[0];
  const s = data.spotlight;
  const rel = (t) => fmt.rel(t);
  const riskbar = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].filter((r) => week[r])
    .map((r) => `<div title="${r}: ${week[r]}" style="flex:${week[r]};background:${RISK_COLORS[r]}"></div>`).join('');

  const regionMax = Math.max(...data.regions.map((r) => r.total_objects), 1);
  const cards = [];
  cards.push(`<article class="lp-card span-4 tall reveal"><span class="no">MODULE 01 · SCREENING</span>
    <h3>Close-approach screening</h3>
    <p>Every ${fmt.num(data.ingest_interval_hours || 4, 0)} hours the ${fmt.int(c.watchlist)} watched satellites — the ISS and India's fleet — are propagated
      with SGP4 against all ${fmt.int(c.with_current_orbit)} tracked objects for the next 24 hours. Every approach under
      ${fmt.num(data.screening_threshold_km || 10, 0)} km becomes an event with its time of closest approach, miss distance and relative velocity.</p>
    <div class="preview">
      <div class="spread small mono muted"><span>NEXT 7 DAYS · ${fmt.int(weekTotal)} EVENTS</span>
        <span>${['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].filter((r) => week[r]).map((r) => `${r} ${week[r]}`).join(' · ')}</span></div>
      <div class="lp-riskbar">${riskbar}</div>
      <table class="lp-mini"><tbody>${data.upcoming.slice(0, 7).map((e) => `<tr>
        <td class="muted">${utc(iso(e.time_of_closest_approach))}Z<br><span class="small">${rel(e.time_of_closest_approach)}</span></td>
        <td class="nm">${esc(e.primary_name)} <span>×</span> ${esc(e.secondary_name)}</td>
        <td class="r">${fmt.num(e.miss_distance_km, 2)} km</td><td class="r">${riskBadge(e.risk_level)}</td></tr>`).join('')}</tbody></table>
    </div><a class="go" href="#/conjunctions">Open close approaches <span>→</span></a></article>`);

  cards.push(`<article class="lp-card span-2 reveal"><span class="no">MODULE 02 · REPLAY</span>
    <h3>Watch it happen in 3D</h3>
    <p>Any event can be replayed on the CesiumJS globe, ±30 minutes around closest approach, with the separation measured live.</p>
    <div class="preview">${replayArt(ev)}</div>
    ${ev ? `<a class="go" href="#/globe?event=${ev.event_id}">Replay ${esc(ev.primary_name)} <span>→</span></a>` : ''}</article>`);

  cards.push(`<article class="lp-card span-2 reveal"><span class="no">MODULE 03 · ALERTS</span>
    <h3>Subscribe to any object</h3>
    <p>When screening records a new approach, a MySQL trigger alerts every subscriber of either object.</p>
    <div class="preview">${ev ? `<div class="lp-alert"><div class="when"><span>Alert · ${utc(new Date())}Z</span>${riskBadge(ev.risk_level)}</div>
      <b>${esc(ev.primary_name)}</b> will pass <b>${fmt.num(ev.miss_distance_km, 3)} km</b> from ${esc(ev.secondary_name)}
      at ${utc(iso(ev.time_of_closest_approach))} UTC.</div>` : ''}</div>
    <a class="go" href="${app.user ? '#/alerts' : '#/register'}">${app.user ? 'My alerts' : 'Create an account'} <span>→</span></a></article>`);

  const ai = data.ai;
  const shown = ai ? ai.steps.filter((x) => x.actor !== 'system') : [];
  const aiSteps = shown.length > 9 ? [...shown.slice(0, 3), ...shown.slice(-6)] : shown;  // keep the end: decision and guardrails
  const aiOut = ai ? `<div class="lp-ai-out"><div class="k">Latest recommendation · ${esc(ai.primary_name)} × ${esc(ai.secondary_name)}</div>
        <div class="d">${esc((ai.decision || '').replace(/_/g, ' ').toLowerCase())}</div>
        <div class="mono small">${ai.delta_v_mps != null ? `Δv ${fmt.num(ai.delta_v_mps, 3)} m/s ${esc(ai.burn_direction || '')} · ` : ''}Pc ${ai.pc_before != null ? Number(ai.pc_before).toExponential(1) : '—'}${ai.pc_after != null ? ` → ${Number(ai.pc_after).toExponential(1)}` : ''} · ${esc(ai.engine.replace('groq:', ''))}</div></div>` : '';
  const aiTrace = aiSteps.length
    ? aiSteps.map((x) => `<li class="a-${x.actor}"><span class="who">${x.actor}${x.tool_name ? ` · ${esc(x.tool_name)}` : ''}</span><span class="what">${esc(x.summary)}</span></li>`).join('')
    : '<li class="a-system"><span class="who">agent</span><span class="what">No assessment has been run yet. An analyst can start one from any close approach.</span></li>';
  cards.push(`<article class="lp-card span-6 reveal lp-ai"><span class="no">MODULE 07 · AGENTIC AI</span>
    <div class="lp-ai-grid"><div>
      <h3>An AI agent that reasons over the physics</h3>
      <p>For any close approach, an LLM (Groq) decides which deterministic tools to run — SGP4 re-propagation,
        Foster probability of collision, NOAA space weather, ${fmt.int(data.ground_stations || 0)} ground stations and a Clohessy–Wiltshire
        Δv optimiser — reads what they return, and recommends what operators should do. Guardrails reject any manoeuvre the
        constraint checker did not pass, every step is stored in MySQL for audit, and a burn always needs human approval.</p>
      ${aiOut}
      <a class="go" href="${ai ? `#/conjunctions?event=${ai.event_id}` : '#/conjunctions'}">${ai ? 'Open this assessment' : 'Run an assessment'} <span>→</span></a>
    </div>
    <ol class="lp-ai-trace" aria-label="Agent trace">${aiTrace}</ol>
    </div></article>`);

  cards.push(`<article class="lp-card span-3 reveal"><span class="no">MODULE 04 · CATALOGUE</span>
    <h3>${fmt.int(c.total)} objects, fully linked</h3>
    <p>Each object with its launch, vehicle, site, owner over time, missions, and the fragments that broke away from it.</p>
    <div class="preview">${s ? `<dl class="lp-sheet">
      <dt>Object</dt><dd>${esc(s.name)} · ${s.norad_id}</dd><dt>COSPAR</dt><dd>${esc(s.intl_designator || '—')}</dd>
      <dt>Launched</dt><dd>${fmt.date(s.launch_date)} · ${esc(s.vehicle || '—')}</dd><dt>Site</dt><dd>${esc(s.site || '—')}</dd>
      <dt>Owner</dt><dd>${esc(s.org_name || '—')}</dd><dt>Orbit</dt><dd>${fmt.int(s.perigee_km)} × ${fmt.int(s.apogee_km)} km · ${fmt.num(s.inclination, 1)}°</dd></dl>` : ''}
      <form class="lp-search" id="lpSearch"><input type="search" name="q" placeholder="Name, NORAD or COSPAR — e.g. CARTOSAT"><button class="btn">Search</button></form></div>
    <a class="go" href="#/catalog">Browse the catalogue <span>→</span></a></article>`);

  cards.push(`<article class="lp-card span-3 reveal"><span class="no">MODULE 05 · ANALYTICS</span>
    <h3>Reports from two databases</h3>
    <p>SQL views over MySQL for the catalogue; MapReduce and aggregation over the sharded MongoDB history for long-term trends.</p>
    <div class="preview"><table class="lp-mini"><tbody>${data.regions.map((r) => `<tr><td class="nm" style="width:46%">${esc(r.region_name)}</td>
      <td><div style="height:8px;width:${Math.max((r.total_objects / regionMax) * 100, 0.6)}%;background:var(--t-payload)"></div></td>
      <td class="r" style="width:70px">${fmt.int(r.total_objects)}</td></tr>`).join('')}</tbody></table></div>
    <a class="go" href="${app.can('analyst') ? '#/reports' : '#/dashboard'}">${app.can('analyst') ? 'Open reports' : 'Open the dashboard'} <span>→</span></a></article>`);

  cards.push(`<article class="lp-card span-6 reveal"><span class="no">MODULE 06 · ACCESS</span>
    <h3>Roles enforced by the database, not just the app</h3>
    <p>Each role connects to MySQL as its own account with its own privileges. A Public Viewer's session cannot read the users table even if the application asked it to.</p>
    <div class="preview"><div class="lp-roles">
      <div><b>Public Viewer</b><code>r_viewer</code><p>Catalogue, orbits, close approaches, the globe; own subscriptions and alerts.</p></div>
      <div><b>Analyst</b><code>r_analyst</code><p>Everything above, plus reports, the orbital history in MongoDB, and CSV exports.</p></div>
      <div><b>Administrator</b><code>r_admin</code><p>Users and roles, reference data, the watchlist, thresholds, jobs and logs. No schema changes.</p></div>
    </div></div></article>`);

  const bento = root.querySelector('#bento');
  bento.innerHTML = cards.join('');
  const form = bento.querySelector('#lpSearch');
  if (form) form.addEventListener('submit', (e) => { e.preventDefault(); location.hash = `#/catalog?q=${encodeURIComponent(form.q.value.trim())}`; });
  const io = new IntersectionObserver((entries) => entries.forEach((e) => { if (e.isIntersecting) e.target.classList.add('in'); }));
  bento.querySelectorAll('.reveal').forEach((el) => io.observe(el));
}

function replayArt(ev) {
  const miss = ev ? `${fmt.num(ev.miss_distance_km, 3)} km` : '';
  return `<svg viewBox="0 0 300 150" style="width:100%;height:auto;display:block" aria-hidden="true">
    <circle cx="150" cy="190" r="118" fill="none" stroke="rgba(140,170,200,0.18)"/>
    <path d="M10 118 C 90 40, 210 40, 290 118" fill="none" stroke="#e9eef4" stroke-width="1.5" stroke-dasharray="3 4" opacity="0.8"/>
    <path d="M40 18 C 110 70, 190 90, 285 74" fill="none" stroke="#f0a58c" stroke-width="1.5" stroke-dasharray="3 4" opacity="0.8"/>
    <circle cx="150" cy="61" r="4" fill="#d03b3b"/><circle cx="150" cy="61" r="11" fill="none" stroke="#d03b3b" opacity="0.6"/>
    <text x="164" y="44" fill="#e9eef4" style="font:500 11px 'IBM Plex Mono',monospace">TCA · ${miss}</text>
    <circle cx="96" cy="71" r="3.5" fill="#e9eef4"/><circle cx="118" cy="49" r="3.5" fill="#f0a58c"/>
  </svg>`;
}

// ---------------------------------------------------------------------------
// 03: pipeline
function fillPipeline(root, data) {
  const c = data.counts;
  const weekTotal = Object.values(data.week_by_risk).reduce((a, b) => a + b, 0);
  root.querySelector('#pipeLead').textContent = `Public element sets are downloaded on a schedule, kept forever in a sharded MongoDB history,
    reduced to the latest orbit of each object in MySQL, screened with SGP4, and turned into events, alerts and reports — each step a transaction.`;
  const stages = [
    ['01', 'Sources', 'CelesTrak publishes fresh element sets every few hours; the SATCAT and GCAT describe each object.',
      ['CelesTrak GP · every ' + fmt.num(data.ingest_interval_hours || 4, 0) + ' h', 'SATCAT + GCAT · weekly', 'Space-Track · history'],
      `${fmt.int(c.with_current_orbit)} element sets`],
    ['02', 'MongoDB history', 'Every element set ever downloaded, append-only, with its original JSON and a download log.',
      ['sharded on norad_id', '2 shards × 3-node replica sets', 'MapReduce + aggregation'], 'unique (norad_id, epoch)'],
    ['03', 'MySQL catalogue', 'Objects, launches, owners and missions in 3NF; the newest element set lands in Current_Orbit in one transaction.',
      ['21 tables · 9 views', 'triggers + stored procedures', 'roles per account'], `${fmt.int(c.total)} objects`],
    ['04', 'SGP4 screening', 'Watched satellites against everything, propagated together, with each candidate refined to the millisecond.',
      ['24 h horizon', 'linear TCA refinement', `threshold ${fmt.num(data.screening_threshold_km || 10, 0)} km`],
      `${fmt.int(c.watchlist)} × ${fmt.int(c.with_current_orbit)} objects`],
    ['05', 'Events, alerts & AI', 'New events fire a trigger that alerts subscribers; an LLM agent can assess any event and recommend a manoeuvre.',
      ['Conjunction_Event + Pc', 'Alert via trigger', 'agent trace in MySQL'], `${fmt.int(weekTotal)} events this week`],
  ];
  root.querySelector('#pipe').innerHTML = `<div class="lp-flow" aria-hidden="true"><svg viewBox="0 0 1000 40" preserveAspectRatio="none">
      <path d="M0 20 H1000"/><path class="pulse" d="M0 20 H1000"/></svg></div>
    <div class="lp-pipe">${stages.map(([no, h, p, list, kpi]) => `<div class="lp-stage"><span class="no">${no}</span><h4>${h}</h4>
      <p>${p}</p><ul>${list.map((l) => `<li>${l}</li>`).join('')}</ul><div class="kpi">${kpi}</div></div>`).join('')}</div>`;
}

// ---------------------------------------------------------------------------
// 04: fragmentation and re-entries
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

  // re-entries per year
  const years = data.reentries;
  const W = 560; const H = 190; const L = 36; const B = 160; const T = 16;
  const ymax = Math.max(...years.map((y) => y.n), 1);
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
    const h = (d.n / yTop) * (B - T);
    svg += `<rect x="${L + (d.year - y0) * bw + 0.5}" y="${B - h}" width="${Math.max(bw - 1, 1)}" height="${h}" fill="${d === peak ? '#ff6b2c' : 'rgba(233,238,244,0.55)'}"><title>${d.year}: ${fmt.int(d.n)}</title></rect>`;
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
function fillCta(root, data, app) {
  const c = data.counts;
  root.querySelector('#cta').innerHTML = `<svg class="orbit-art" viewBox="0 0 720 720" aria-hidden="true">
      <circle cx="360" cy="360" r="92" fill="#0c1016" stroke="rgba(140,170,200,0.25)"/>
      ${[[150, 60, -24, 70], [210, 90, 18, 110], [290, 120, -8, 160]].map(([rx, ry, rot, dur], i) => `
      <g transform="rotate(${rot} 360 360)"><ellipse cx="360" cy="360" rx="${rx}" ry="${ry}" fill="none" stroke="rgba(140,170,200,0.16)"/>
        <circle r="${i === 0 ? 4 : 3}" fill="${i === 0 ? '#ff6b2c' : '#e9eef4'}">${REDUCED ? '' : `<animateMotion dur="${dur}s" repeatCount="indefinite"
          path="M ${360 - rx} 360 a ${rx} ${ry} 0 1 0 ${rx * 2} 0 a ${rx} ${ry} 0 1 0 ${-rx * 2} 0"/>`}</circle></g>`).join('')}
    </svg>
    <div class="lp-inner" style="position:relative">
      <div class="lp-index" style="display:inline-block;margin-bottom:28px">05 / Start watching</div>
      <h2>Pick a satellite.<br>We'll tell you when something gets close.</h2>
      <p>Create a free account, subscribe to any of the ${fmt.int(c.on_orbit)} objects in orbit, and OrbitWatch alerts you
        whenever the screening finds a new close approach.</p>
      <div class="lp-ctas">${app.user
        ? '<a class="btn primary lg" href="#/alerts">Go to my alerts <span class="arrow">→</span></a>'
        : '<a class="btn primary lg" href="#/register">Create an account <span class="arrow">→</span></a>'}
        <a class="btn lg ghost-line" href="#/globe">Open the globe</a></div>
    </div>`;
  root.querySelector('#footer').innerHTML = `<div class="cols">
      <div><h5>OrbitWatch</h5><p>Satellite and space-debris tracking with close-approach alerts. A Database Management Systems
        (CD252IA) project, Department of Information Science and Engineering, RV College of Engineering.</p></div>
      <div><h5>Explore</h5><ul><li><a href="#/globe">3D globe</a></li><li><a href="#/catalog">Catalogue</a></li>
        <li><a href="#/conjunctions">Close approaches</a></li><li><a href="#/dashboard">Dashboard</a></li></ul></div>
      <div><h5>Data</h5><ul><li>CelesTrak element sets &amp; SATCAT</li><li>GCAT — J. McDowell (CC-BY 4.0)</li>
        <li>Space-Track.org history</li><li>CesiumJS · Natural Earth II</li></ul></div>
      <div><h5>Team</h5><ul><li>Madhur Rishi Sikarwar · 1RV24IS067</li><li>Mayur M Deekshith · 1RV24IS069</li></ul>
        <h5 style="margin-top:18px">Stack</h5><ul><li>MySQL 8.4 · MongoDB 8.0 (sharded)</li><li>Flask · SGP4 · scikit-learn</li></ul></div>
    </div>
    <div class="base"><span>Positions are SGP4 predictions from public element sets — screening-grade, not operational.</span>
      <span>Data as of ${data.last_update.ingest ? `${data.last_update.ingest.replace('T', ' ').slice(0, 16)} UTC` : '—'}</span></div>`;
}
