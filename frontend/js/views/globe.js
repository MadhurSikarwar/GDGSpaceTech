// Interactive CesiumJS globe (SRS 3.8): the whole catalogue as smoothly moving
// points, the orbit of any selected object, and a replay of any close approach
// around its time of closest approach.
import { get } from '../api.js';
import { CatalogCloud, createViewer, loadCesium, RISK_COLORS, sampledOrbit, TYPE_COLORS, TYPE_NAMES } from '../globe-core.js';
import { esc, fmt, hashQuery, objLink, riskBadge, typeTag } from '../ui.js';

export async function render(root) {
  const q = hashQuery();
  root.innerHTML = `<div class="globe-page"><div id="cesium"></div>
    <div class="globe-panel globe-tools">
      <div class="spread"><h2>Live globe</h2><span class="small mono muted" id="clockLabel"></span></div>
      <div class="small muted" id="globeStatus" style="margin-top:4px">Loading CesiumJS…</div>
      <input type="search" id="globeSearch" placeholder="Find an object — name or NORAD" style="margin-top:12px" disabled>
      <div class="search-results" id="searchResults"></div>
      <div class="globe-legend" id="legend"></div>
      <div class="globe-hot" id="hot"></div>
    </div>
    <div class="globe-panel globe-info hidden" id="info"></div>
    <div class="globe-panel hidden" id="hover" style="padding:4px 8px;pointer-events:none;font:500 11px var(--f-mono)"></div>
  </div>`;
  const $ = (s) => root.querySelector(s);
  const status = $('#globeStatus');

  let C;
  try { C = await loadCesium(); } catch (err) { status.textContent = err.message; return undefined; }
  if (!root.isConnected) return undefined;

  const viewer = createViewer(C, $('#cesium'), { widgets: true, interactive: true });
  viewer.camera.setView({ destination: C.Cartesian3.fromDegrees(78, 15, 26_000_000) });
  const paintLegend = (cl) => {
    $('#legend').innerHTML = TYPE_NAMES.map((t, i) => `<label><input type="checkbox" data-t="${i}" ${cl.visible.has(i) ? 'checked' : ''}>
      <i style="background:${TYPE_COLORS[i]}"></i>${t}<span class="count">${fmt.int(cl.counts[i])}</span></label>`).join('');
    status.textContent = `${fmt.int(cl.order.length)} objects · SGP4, Earth-fixed frame`;
  };
  const cloud = new CatalogCloud(C, viewer, { span: 60, pixelSize: 2.6, onLoad: paintLegend });
  $('#legend').addEventListener('change', (e) => cloud.setTypeVisible(Number(e.target.dataset.t), e.target.checked));

  const timer = setInterval(() => {
    $('#clockLabel').textContent = `${C.JulianDate.toDate(viewer.clock.currentTime).toISOString().slice(11, 19)} UTC ×${viewer.clock.multiplier}`;
    if (cloud.error) status.textContent = cloud.error.message;
  }, 500);

  // ---- selection ---------------------------------------------------------
  let selected = null;
  let orbitEntity = null;
  const accent = C.Color.fromCssColorString('#ff6b2c');
  async function select(norad, fly = false) {
    if (selected) cloud.highlight(selected, false);
    selected = norad;
    const rec = cloud.highlight(norad, true);
    if (fly && rec) viewer.camera.flyToBoundingSphere(new C.BoundingSphere(rec.point.position, 1_600_000), { duration: 1.6 });
    if (orbitEntity) { viewer.entities.remove(orbitEntity); orbitEntity = null; }
    const info = $('#info');
    info.classList.remove('hidden');
    info.innerHTML = '<div class="muted small mono">LOADING…</div>';
    try {
      const [d, orbit] = await Promise.all([get(`/objects/${norad}`),
        sampledOrbit(C, norad, viewer.clock.currentTime, 1).catch(() => null)]);
      if (selected !== norad || viewer.isDestroyed()) return;
      if (orbit) {
        orbitEntity = viewer.entities.add({
          position: orbit.prop,
          path: { leadTime: orbit.periodS, trailTime: 0, width: 3, resolution: 30,
            material: new C.PolylineGlowMaterialProperty({ glowPower: 0.2, color: accent.withAlpha(0.9) }) },
        });
      }
      const o = d.object;
      const co = d.current_orbit;
      info.innerHTML = `<div class="spread"><h2>${esc(o.name)}</h2><button class="btn sm" id="closeInfo" aria-label="Close">×</button></div>
        <div class="row small" style="margin:8px 0 12px"><span class="mono muted">NORAD ${o.norad_id}</span>${typeTag(o.object_type)}</div>
        <dl class="facts small">
          <dt>Owner</dt><dd>${esc(o.org_name || '—')}${o.country_name ? ` · ${esc(o.country_name)}` : ''}</dd>
          <dt>Region</dt><dd>${esc(o.region_name || '—')}</dd>
          ${co ? `<dt>Perigee × apogee</dt><dd>${fmt.int(co.perigee_km)} × ${fmt.int(co.apogee_km)} km</dd>
          <dt>Inclination</dt><dd>${fmt.num(co.inclination, 2)}°</dd><dt>Period</dt><dd>${fmt.num(co.period_min, 1)} min</dd>` : ''}
          <dt>Launched</dt><dd>${fmt.date(o.launch_date)}</dd>
        </dl>
        <p style="margin-top:12px">${objLink(o.norad_id, 'Open full details →')}</p>
        <p class="small muted">The glowing line is the next orbit from the clock time, in the Earth-fixed frame.</p>`;
      info.querySelector('#closeInfo').addEventListener('click', () => {
        info.classList.add('hidden');
        if (orbitEntity) { viewer.entities.remove(orbitEntity); orbitEntity = null; }
        cloud.highlight(selected, false);
        selected = null;
      });
    } catch (err) {
      info.innerHTML = `<div>${esc(err.message)}</div>`;
    }
  }

  const handler = new C.ScreenSpaceEventHandler(viewer.scene.canvas);
  handler.setInputAction((click) => {
    const picked = viewer.scene.pick(click.position);
    if (C.defined(picked) && typeof picked.id === 'number') select(picked.id);
    else if (C.defined(picked) && picked.id?.properties?.norad) select(picked.id.properties.norad.getValue());
  }, C.ScreenSpaceEventType.LEFT_CLICK);

  let names = null;
  const hover = $('#hover');
  let pending = false;
  handler.setInputAction((move) => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      if (viewer.isDestroyed()) return;
      const picked = viewer.scene.pick(move.endPosition);
      if (C.defined(picked) && typeof picked.id === 'number' && names) {
        hover.textContent = `${names.get(picked.id) || ''} · ${picked.id}`;
        hover.style.left = `${move.endPosition.x + 14}px`;
        hover.style.top = `${move.endPosition.y + 10}px`;
        hover.classList.remove('hidden');
        viewer.canvas.style.cursor = 'pointer';
      } else {
        hover.classList.add('hidden');
        viewer.canvas.style.cursor = '';
      }
    });
  }, C.ScreenSpaceEventType.MOUSE_MOVE);

  // ---- search ------------------------------------------------------------
  get('/visual/names').then((r) => {
    names = new Map(r.ids.map((id, i) => [id, r.names[i]]));
    const box = $('#globeSearch');
    box.disabled = false;
    box.addEventListener('input', () => {
      const s = box.value.trim().toUpperCase();
      const out = $('#searchResults');
      if (s.length < 2) { out.innerHTML = ''; return; }
      const hits = [];
      for (const [id, name] of names) {
        if (String(id).startsWith(s) || name.toUpperCase().includes(s)) hits.push([id, name]);
        if (hits.length >= 12) break;
      }
      out.innerHTML = hits.map(([id, name]) => `<button data-id="${id}">${esc(name)} <span class="muted mono">${id}</span></button>`).join('')
        || '<div class="small muted" style="padding:6px 8px">No object with a current orbit matches.</div>';
    });
    $('#searchResults').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-id]');
      if (!b) return;
      $('#searchResults').innerHTML = '';
      box.value = '';
      select(Number(b.dataset.id), true);
    });
  });

  // ---- upcoming high-risk approaches, one click to replay ----------------
  get('/conjunctions?risk=CRITICAL,HIGH&page_size=6').then(({ items }) => {
    if (!items.length) return;
    $('#hot').innerHTML = `<h3 style="margin-bottom:6px">Replay a close approach</h3>${items.map((e) => `<a href="#/globe?event=${e.event_id}">
      <span>${esc(e.primary_name)} × ${esc(e.secondary_name)}</span><span class="mono">${fmt.num(e.miss_distance_km, 2)} km</span></a>`).join('')}`;
  }).catch(() => {});

  // ---- conjunction replay --------------------------------------------------
  if (q.event) replay(Number(q.event));
  else if (q.norad) setTimeout(() => select(Number(q.norad), true), 1200);

  async function replay(eventId) {
    const info = $('#info');
    info.classList.remove('hidden');
    info.innerHTML = '<div class="muted small mono">LOADING CLOSE APPROACH…</div>';
    try {
      const [d, tr] = await Promise.all([get(`/conjunctions/${eventId}`), get(`/conjunctions/${eventId}/track`)]);
      if (viewer.isDestroyed()) return;
      const e = d.event;
      const tca = C.JulianDate.fromIso8601(tr.tca);
      const start = C.JulianDate.addSeconds(tca, -1800, new C.JulianDate());
      const stop = C.JulianDate.addSeconds(tca, 1800, new C.JulianDate());
      Object.assign(viewer.clock, { startTime: start, stopTime: stop, clockRange: C.ClockRange.LOOP_STOP, multiplier: 20, shouldAnimate: true });
      viewer.clock.currentTime = C.JulianDate.addSeconds(tca, -240, new C.JulianDate());
      viewer.timeline.zoomTo(start, stop);

      const objs = [d.primary, d.secondary];
      const tint = [C.Color.WHITE, C.Color.fromCssColorString('#ff8a55')];
      const props = tr.objects.map((o, k) => {
        const prop = new C.SampledPositionProperty();
        prop.setInterpolationOptions({ interpolationDegree: 5, interpolationAlgorithm: C.LagrangePolynomialApproximation });
        o.offsets_s.forEach((s, i) => {
          const [x, y, z] = o.ecef_km[i];
          prop.addSample(C.JulianDate.addSeconds(tca, s, new C.JulianDate()), new C.Cartesian3(x * 1000, y * 1000, z * 1000));
        });
        viewer.entities.add({
          position: prop,
          properties: { norad: o.norad_id },
          point: { pixelSize: 10, color: tint[k], outlineColor: C.Color.BLACK, outlineWidth: 1.5 },
          label: { text: objs[k]?.name || String(o.norad_id), font: '600 12px "IBM Plex Mono", monospace', fillColor: tint[k],
            showBackground: true, backgroundColor: C.Color.fromCssColorString('#06080b').withAlpha(0.75),
            pixelOffset: new C.Cartesian2(14, k ? 16 : -16), horizontalOrigin: C.HorizontalOrigin.LEFT },
          path: { leadTime: 600, trailTime: 600, width: 2.5, resolution: 10,
            material: new C.PolylineGlowMaterialProperty({ glowPower: 0.18, color: tint[k].withAlpha(0.9) }) },
        });
        return prop;
      });
      if (props.length === 2) {
        const both = (time) => [props[0].getValue(time), props[1].getValue(time)];
        viewer.entities.add({ polyline: { positions: new C.CallbackProperty((time) => { const [a, b] = both(time); return a && b ? [a, b] : []; }, false),
          width: 1.5, material: new C.PolylineDashMaterialProperty({ color: C.Color.fromCssColorString('#fab219') }) } });
        viewer.entities.add({
          position: new C.CallbackProperty((time) => { const [a, b] = both(time); return a && b ? C.Cartesian3.midpoint(a, b, new C.Cartesian3()) : undefined; }, false),
          label: { text: new C.CallbackProperty((time) => { const [a, b] = both(time); return a && b ? `${fmt.num(C.Cartesian3.distance(a, b) / 1000, 1)} km` : ''; }, false),
            font: '500 12px "IBM Plex Mono", monospace', fillColor: C.Color.fromCssColorString('#fab219'), showBackground: true,
            backgroundColor: C.Color.fromCssColorString('#06080b').withAlpha(0.75), pixelOffset: new C.Cartesian2(0, -20) },
        });
        const atTca = props[0].getValue(tca);
        const risk = C.Color.fromCssColorString(RISK_COLORS[e.risk_level]);
        viewer.entities.add({ position: atTca, point: { pixelSize: 7, color: risk },
          label: { text: `TCA · ${fmt.num(e.miss_distance_km, 3)} km`, font: '500 12px "IBM Plex Mono", monospace', fillColor: C.Color.WHITE,
            showBackground: true, backgroundColor: risk.withAlpha(0.85), pixelOffset: new C.Cartesian2(0, 24) } });
        viewer.camera.flyToBoundingSphere(new C.BoundingSphere(atTca, 2_600_000), { duration: 2 });
      }

      info.innerHTML = `<div class="spread"><h2>Close approach #${e.event_id}</h2>${riskBadge(e.risk_level)}</div>
        <p style="margin:10px 0">${objLink(e.primary_norad, e.primary_name)} <span class="muted mono">×</span> ${objLink(e.secondary_norad, e.secondary_name)}</p>
        <dl class="facts small">
          <dt>TCA</dt><dd>${fmt.dt(e.time_of_closest_approach)}</dd>
          <dt>Miss distance</dt><dd>${fmt.km(e.miss_distance_km, 3)}</dd>
          <dt>Rel. velocity</dt><dd>${fmt.num(e.relative_velocity, 2)} km/s</dd>
          <dt>Pc</dt><dd>${e.probability_of_collision == null ? '—' : Number(e.probability_of_collision).toExponential(2)}</dd>
        </dl>
        <p style="margin-top:12px"><a class="mono small" href="#/conjunctions?event=${e.event_id}">AI DECISION SUPPORT →</a></p>
        <div class="row" style="margin-top:14px">
          <button class="btn sm" data-speed="1">×1</button><button class="btn sm" data-speed="20">×20</button>
          <button class="btn sm" data-speed="60">×60</button><button class="btn sm primary" id="jumpTca">Jump to TCA</button></div>
        <p class="small muted" style="margin-top:12px">Replays ±30 minutes around TCA. The dashed line is the live separation.
        Uses each object's current element set, so it can differ slightly from the screening run.</p>`;
      info.addEventListener('click', (ev) => {
        const sp = ev.target.closest('[data-speed]');
        if (sp) { viewer.clock.multiplier = Number(sp.dataset.speed); viewer.clock.shouldAnimate = true; }
        if (ev.target.id === 'jumpTca') {
          viewer.clock.currentTime = C.JulianDate.addSeconds(tca, -20, new C.JulianDate());
          viewer.clock.multiplier = 1;
        }
      });
    } catch (err) {
      info.innerHTML = `<div>${esc(err.message)}</div>`;
    }
  }

  return () => {
    clearInterval(timer);
    handler.destroy();
    cloud.destroy();
    viewer.destroy();
  };
}
