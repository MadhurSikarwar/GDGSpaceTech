// Interactive CesiumJS globe (SRS 3.8), the flagship view:
//   * every object with a current orbit as a smoothly moving point (SGP4, Earth-fixed), filterable by
//     type and orbital regime; day/night Earth with the real terminator;
//   * layers: upcoming close approaches placed where they happen, synthetic demo objects (labelled),
//     ground stations;
//   * select any object to see its orbit and provenance, follow it with the camera;
//   * replay any close approach (real or synthetic) around its TCA, and replay an approved or
//     recommended avoidance burn: nominal versus post-burn orbit (SIMULATED), with the burn and both TCAs.
import { get } from '../api.js';
import { term } from '../drawers.js';
import { ACCENT, CatalogCloud, createViewer, dayView, loadCesium, orbitRing, REGIMES, RISK_COLORS, sampledOrbit, sampledTrack,
  shapeCanvas, ringCanvas, SYN_COLOR, TYPE_COLORS, TYPE_NAMES } from '../globe-core.js';
import { age, esc, fmt, hashQuery, objLink, prov, riskBadge, setHashQuery, simTag, synTag, typeTag } from '../ui.js';

export async function render(root, { app }) {
  const q = hashQuery();
  root.innerHTML = `<div class="globe-page"><div id="cesium"></div>
    <div class="globe-panel globe-tools" id="tools">
      <button class="globe-toggle" id="toolsToggle" aria-expanded="false"><span>Search, filters &amp; layers</span><span id="toggleCount" class="mono"></span></button>
      <div class="spread"><h2>Live globe</h2><span class="small mono muted" id="clockLabel"></span></div>
      <div class="small muted" id="globeStatus" style="margin-top:4px">Loading CesiumJS…</div>
      <input type="search" id="globeSearch" placeholder="Find an object — name or NORAD" style="margin-top:12px" disabled aria-label="Find an object">
      <div class="search-results" id="searchResults"></div>
      <div class="sect"><h3><span>Object type</span><span>shown</span></h3><div class="globe-legend" id="legend"></div></div>
      <div class="sect"><h3><span>${term('regime', 'Orbital regime')}</span></h3><div class="chips" id="regimes"></div></div>
      <div class="sect"><h3><span>Layers</span></h3><div class="globe-legend" id="layers">
        <label><input type="checkbox" data-layer="cloud" checked><i style="background:${ACCENT}"></i>Catalogue</label>
        <label><input type="checkbox" data-layer="markers" checked><i style="background:${RISK_COLORS.CRITICAL};border-radius:1px;transform:rotate(45deg)"></i>Close approaches<span class="count" id="mkCount"></span></label>
        <div class="chips" id="mkRisk" style="margin:-2px 0 4px 22px"><button class="chip" data-risk="CRITICAL" aria-pressed="true">Critical</button>
          <button class="chip" data-risk="HIGH" aria-pressed="true">High</button><button class="chip" data-risk="MEDIUM" aria-pressed="false">Medium</button></div>
        <label><input type="checkbox" data-layer="demo" checked><i style="background:${SYN_COLOR};border-radius:1px"></i>Synthetic demo objects<span class="count" id="synCount"></span></label>
        <label><input type="checkbox" data-layer="stations"><i style="background:#c9d4e2;border-radius:1px"></i>Ground stations</label></div></div>
      <div class="sect globe-hot" id="hot"></div>
    </div>
    <div class="globe-panel globe-info hidden" id="info" aria-live="polite"></div>
    <div class="globe-panel hidden" id="hover" style="padding:4px 8px;pointer-events:none;font:500 11px var(--f-mono);z-index:9"></div>
    <div class="globe-hud" id="hud"></div>
    <div class="globe-credits" id="credits"></div>
    <div class="globe-toolbar" role="toolbar" aria-label="Camera and time">
      <span class="lbl">VIEW</span>
      <div class="grp"><button data-cam="globe" title="Whole Earth">Globe</button><button data-cam="leo" title="Low Earth orbit">LEO</button>
        <button data-cam="geo" title="Geostationary belt">GEO belt</button><button data-cam="polar" title="Over the North Pole">Polar</button>
        <button data-cam="follow" id="followBtn" title="Follow the selected object">Follow</button></div>
      <span class="lbl">TIME</span>
      <div class="grp"><button data-time="live" title="Real time">Live</button><button data-time="10">×10</button><button data-time="60">×60</button>
        <button data-time="300">×300</button><button data-time="pause" title="Pause / resume">❚❚</button></div>
    </div>
  </div>`;
  const $ = (s) => root.querySelector(s);
  const status = $('#globeStatus');
  $('#toolsToggle').addEventListener('click', () => {
    const open = $('#tools').classList.toggle('open');
    $('#toolsToggle').setAttribute('aria-expanded', String(open));
  });

  let C;
  try { C = await loadCesium(); } catch (err) { status.textContent = err.message; return undefined; }
  if (!root.isConnected) return undefined;

  const viewer = createViewer(C, $('#cesium'), { interactive: true, creditContainer: root.querySelector('#credits') });
  viewer.camera.setView({ destination: dayView(C, 26_000_000) });
  const accent = C.Color.fromCssColorString(ACCENT);
  const synColor = C.Color.fromCssColorString(SYN_COLOR);
  const bg = C.Color.fromCssColorString('#04070b').withAlpha(0.82);
  const labelFont = '500 11.5px "IBM Plex Mono", monospace';

  // ---- catalogue cloud, filters ----------------------------------------------
  const paintLegend = (cl) => {
    $('#legend').innerHTML = TYPE_NAMES.map((t, i) => `<label><input type="checkbox" data-t="${i}" ${cl.visible.has(i) ? 'checked' : ''}>
      <i style="background:${TYPE_COLORS[i]}"></i>${t}<span class="count">${fmt.int(cl.counts[i])}</span></label>`).join('');
    $('#regimes').innerHTML = REGIMES.map(([name], i) => `<button class="chip" data-r="${i}" aria-pressed="${cl.regimes.has(i)}">${name}
      <span class="muted">${fmt.int(cl.regimeCounts[i])}</span></button>`).join('');
    const cov = app.system?.coverage;
    status.innerHTML = `${fmt.int(cl.order.length)} objects · SGP4, Earth-fixed${cov ? ` · ${prov({ note: `coverage ${fmt.num(cov.coverage_pct, 1)}% of objects in orbit` })}` : ''}`;
    $('#toggleCount').textContent = `${fmt.int(cl.order.length)} objects`;
  };
  const cloud = new CatalogCloud(C, viewer, { span: 60, pixelSize: 1.9, onLoad: paintLegend });
  // ?debug in the page URL exposes the viewer for automated checks (frames can then be rendered by hand)
  if (new URLSearchParams(location.search).has('debug')) window.__owGlobe = { C, viewer, cloud };
  $('#legend').addEventListener('change', (e) => cloud.setTypeVisible(Number(e.target.dataset.t), e.target.checked));
  $('#regimes').addEventListener('click', (e) => {
    const b = e.target.closest('[data-r]');
    if (!b) return;
    const on = b.getAttribute('aria-pressed') !== 'true';
    b.setAttribute('aria-pressed', String(on));
    cloud.setRegimeVisible(Number(b.dataset.r), on);
  });

  // ---- HUD and clock ------------------------------------------------------------
  const timer = setInterval(() => {
    const t = C.JulianDate.toDate(viewer.clock.currentTime).toISOString();
    const offset = (C.JulianDate.toDate(viewer.clock.currentTime) - Date.now()) / 1000;
    const isLive = Math.abs(offset) < 5 && viewer.clock.multiplier === 1;
    $('#clockLabel').textContent = `${t.slice(11, 19)} UTC ×${viewer.clock.multiplier}`;
    $('#hud').innerHTML = `<b>${t.slice(0, 10)} ${t.slice(11, 19)} UTC</b> · ×${viewer.clock.multiplier}${isLive ? ' · LIVE' : ` · ${offset > 0 ? '+' : '−'}${fmt.num(Math.abs(offset) / 60, 0)} min`}<br>
      ${fmt.int(cloud.visibleCount())} objects shown · ${viewer.clock.shouldAnimate ? 'running' : 'paused'}`;
    if (cloud.error) status.textContent = cloud.error.message;
  }, 500);

  // ---- selection -------------------------------------------------------------------
  let selected = null;
  let orbitEntity = null;
  let follower = null;
  const info = $('#info');
  const clearSelection = () => {
    if (selected) cloud.highlight(selected, false);
    selected = null;
    if (orbitEntity) { viewer.entities.remove(orbitEntity); orbitEntity = null; }
    stopFollow();
  };
  function stopFollow() {
    viewer.trackedEntity = undefined;
    if (follower) { viewer.entities.remove(follower); follower = null; }
    $('#followBtn').classList.remove('on');
  }
  async function select(norad, fly = false) {
    clearReplay();
    if (selected) cloud.highlight(selected, false);
    stopFollow();
    selected = norad;
    const rec = cloud.highlight(norad, true);
    if (fly && rec) viewer.camera.flyToBoundingSphere(new C.BoundingSphere(rec.point.position, 1_600_000), { duration: 1.6 });
    if (orbitEntity) { viewer.entities.remove(orbitEntity); orbitEntity = null; }
    info.classList.remove('hidden');
    info.innerHTML = '<div class="muted small mono">LOADING…</div>';
    try {
      const [d, orbit] = await Promise.all([get(`/objects/${norad}`), sampledOrbit(C, norad, viewer.clock.currentTime, 1).catch(() => null)]);
      if (selected !== norad || viewer.isDestroyed()) return;
      if (orbit) {
        // the orbit itself (closed, inertial), turned with the Earth; plus the object riding on it
        orbitEntity = viewer.entities.add({
          polyline: orbit.teme ? { positions: orbitRing(C, orbit.teme), width: 1.6, arcType: C.ArcType.NONE,
            material: accent.withAlpha(0.8) } : undefined,
          position: orbit.prop,
          path: orbit.teme ? undefined : { leadTime: orbit.periodS, trailTime: 0, width: 2, resolution: 30, material: accent.withAlpha(0.85) },
        });
      }
      const o = d.object;
      const co = d.current_orbit;
      info.innerHTML = `<div class="spread"><h2>${esc(o.name)}</h2><button class="icon-btn" id="closeInfo" aria-label="Close"><svg><use href="#i-close"/></svg></button></div>
        <div class="row small" style="margin:8px 0 12px"><span class="mono muted">NORAD ${o.norad_id}</span>${typeTag(o.object_type)}</div>
        <dl class="telemetry">
          <dt>Owner</dt><dd>${esc(o.org_name || '—')}${o.country_name ? ` · ${esc(o.country_name)}` : ''}</dd>
          <dt>Region</dt><dd>${esc(o.region_name || '—')}</dd>
          ${co ? `<dt>${term('perigee', 'Perigee × apogee')}</dt><dd>${fmt.int(co.perigee_km)} × ${fmt.int(co.apogee_km)} km</dd>
          <dt>Inclination</dt><dd>${fmt.num(co.inclination, 2)}°</dd><dt>Period</dt><dd>${fmt.num(co.period_min, 1)} min</dd>
          <dt>${term('epoch', 'Epoch')}</dt><dd>${fmt.dt(co.epoch)}</dd><dt>Epoch age</dt><dd>${age(co.epoch)}</dd>` : ''}
          <dt>Launched</dt><dd>${fmt.date(o.launch_date)}</dd>
        </dl>
        ${co ? `<p style="margin-top:10px">${prov({ src: co.source, fetched: co.fetched_at })}</p>` : ''}
        <div class="row" style="margin-top:12px"><button class="btn sm" id="followSel">Follow</button>${objLink(o.norad_id, 'Full details →')}</div>
        <p class="small muted" style="margin-top:10px">The ring is the object's orbit (SGP4, one full period), drawn in space and turned with the Earth, so it always closes through the object.</p>`;
      info.querySelector('#closeInfo').addEventListener('click', () => { info.classList.add('hidden'); clearSelection(); });
      info.querySelector('#followSel').addEventListener('click', () => follow());
    } catch (err) {
      info.innerHTML = `<div>${esc(err.message)}</div>`;
    }
  }

  function follow() {
    if (viewer.trackedEntity) { stopFollow(); return; }
    let target = replay.followTarget;
    if (!target && selected) {
      follower = viewer.entities.add({ position: new C.CallbackProperty((time) => cloud.positionOf(selected, time), false),
        point: { pixelSize: 1, color: C.Color.TRANSPARENT } });
      target = follower;
    }
    if (!target) { info.classList.remove('hidden'); info.innerHTML = '<div class="small muted">Select an object (click a point or search) to follow it.</div>'; return; }
    viewer.trackedEntity = target;
    $('#followBtn').classList.add('on');
  }

  // ---- picking and hover -------------------------------------------------------
  const handler = new C.ScreenSpaceEventHandler(viewer.scene.canvas);
  handler.setInputAction((click) => {
    const picked = viewer.scene.pick(click.position);
    if (!C.defined(picked)) return;
    if (typeof picked.id === 'number') { select(picked.id); return; }
    const props = picked.id?.properties;
    if (props?.eventId) { replayEvent(props.eventId.getValue()); return; }
    if (props?.norad) select(props.norad.getValue());
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
      let text = null;
      if (C.defined(picked) && typeof picked.id === 'number' && names) text = `${names.get(picked.id) || ''} · ${picked.id}`;
      else if (C.defined(picked) && picked.id?.properties?.hover) text = picked.id.properties.hover.getValue();
      if (text) {
        hover.textContent = text;
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

  // ---- search -------------------------------------------------------------------
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
      $('#tools').classList.remove('open');
      select(Number(b.dataset.id), true);
    });
  });

  // ---- layer: close-approach markers ---------------------------------------------
  const markerEntities = [];
  const markerRisks = new Set(['CRITICAL', 'HIGH']);
  const markerImg = Object.fromEntries(Object.entries(RISK_COLORS).map(([k, c]) => [k, shapeCanvas(c, 'diamond', 16, 1.6)]));
  const showMarkers = () => {
    const on = $('[data-layer="markers"]').checked;
    for (const en of markerEntities) en.show = on && markerRisks.has(en.properties.risk.getValue());
    $('#mkCount').textContent = fmt.int(markerEntities.filter((en) => en.show).length);
  };
  $('#mkRisk').addEventListener('click', (e) => {
    const b = e.target.closest('[data-risk]');
    if (!b) return;
    const on = b.getAttribute('aria-pressed') !== 'true';
    b.setAttribute('aria-pressed', String(on));
    if (on) markerRisks.add(b.dataset.risk); else markerRisks.delete(b.dataset.risk);
    showMarkers();
  });
  get('/visual/conjunction-markers').then(({ items }) => {
    if (viewer.isDestroyed()) return;
    for (const m of items) {
      const [x, y, z] = m.ecef_km;
      markerEntities.push(viewer.entities.add({
        position: new C.Cartesian3(x * 1000, y * 1000, z * 1000),
        billboard: { image: markerImg[m.risk], scale: m.risk === 'CRITICAL' ? 0.95 : 0.75,
          scaleByDistance: new C.NearFarScalar(1.5e6, 1.1, 5e7, 0.55) },
        properties: { eventId: m.event_id, risk: m.risk,
          hover: `${m.risk} · ${m.primary.name} × ${m.secondary.name} · ${fmt.num(m.miss_km, 3)} km · TCA ${m.tca.slice(5, 16).replace('T', ' ')} UTC` },
      }));
    }
    showMarkers();
    $('#hot').innerHTML = items.length ? `<h3 style="margin-bottom:6px">Replay a close approach</h3>${items.slice(0, 5).map((e) => `<a href="#/globe?event=${e.event_id}" data-ev="${e.event_id}">
      <span>${esc(e.primary.name)} × ${esc(e.secondary.name)}</span><span class="mono">${fmt.num(e.miss_km, 2)} km ${riskBadge(e.risk)}</span></a>`).join('')}` : '';
  }).catch(() => { $('#mkCount').textContent = '—'; });
  $('#hot').addEventListener('click', (e) => {
    const a = e.target.closest('[data-ev]');
    if (!a) return;
    e.preventDefault();
    $('#tools').classList.remove('open');
    replayEvent(Number(a.dataset.ev));
  });

  // ---- layer: synthetic demo objects -----------------------------------------------
  const synEntities = [];
  const synImg = shapeCanvas(SYN_COLOR, 'square', 16, 2);
  let synTimer = null;
  const loadDemo = async () => {
    try {
      const iso = C.JulianDate.toDate(viewer.clock.currentTime).toISOString();
      const d = await get(`/demo/positions?t=${encodeURIComponent(iso)}&span=120`);
      if (viewer.isDestroyed()) return;
      synEntities.splice(0).forEach((en) => viewer.entities.remove(en));
      $('#synCount').textContent = fmt.int(d.items.length);
      const t0 = C.JulianDate.fromIso8601(d.t);
      for (const o of d.items) {
        const p0 = new C.Cartesian3(...o.pos.map((v) => v * 1000));
        const p1 = o.pos2 ? new C.Cartesian3(...o.pos2.map((v) => v * 1000)) : p0;
        synEntities.push(viewer.entities.add({
          show: $('[data-layer="demo"]').checked,
          position: new C.CallbackProperty((time) => {
            const f = Math.min(Math.max(C.JulianDate.secondsDifference(time, t0) / 120, 0), 1);
            return C.Cartesian3.lerp(p0, p1, f, new C.Cartesian3());
          }, false),
          billboard: { image: synImg, scale: 0.8 },
          label: { text: `${o.designation} · SYNTHETIC`, font: labelFont, fillColor: synColor, showBackground: true, backgroundColor: bg,
            pixelOffset: new C.Cartesian2(12, -12), horizontalOrigin: C.HorizontalOrigin.LEFT, scale: 0.85,
            distanceDisplayCondition: new C.DistanceDisplayCondition(0, 2.2e7) },
          properties: { hover: `${o.designation} — synthetic demo debris (not a real object)` },
        }));
      }
    } catch { $('#synCount').textContent = '—'; }
  };
  loadDemo();
  synTimer = setInterval(loadDemo, 100000);

  // ---- layer: ground stations ------------------------------------------------------
  const stationEntities = [];
  get('/ground-stations').then(({ items }) => {
    if (viewer.isDestroyed()) return;
    for (const s of items) {
      stationEntities.push(viewer.entities.add({
        show: false,
        position: C.Cartesian3.fromDegrees(Number(s.longitude), Number(s.latitude), Number(s.altitude_m)),
        point: { pixelSize: 6, color: C.Color.fromCssColorString('#c9d4e2'), outlineColor: C.Color.BLACK, outlineWidth: 1 },
        label: { text: s.station_id, font: labelFont, fillColor: C.Color.fromCssColorString('#c9d4e2'), showBackground: true,
          backgroundColor: bg, pixelOffset: new C.Cartesian2(10, 0), horizontalOrigin: C.HorizontalOrigin.LEFT, scale: 0.85 },
        properties: { hover: `${s.name} · mask ${s.min_elevation_deg}°` },
      }));
    }
  }).catch(() => {});

  $('#layers').addEventListener('change', (e) => {
    const layer = e.target.dataset.layer;
    const on = e.target.checked;
    if (layer === 'cloud') cloud.points.show = on;
    if (layer === 'markers') showMarkers();
    if (layer === 'demo') synEntities.forEach((en) => { en.show = on; });
    if (layer === 'stations') stationEntities.forEach((en) => { en.show = on; });
  });

  // ---- camera and time ----------------------------------------------------------------
  const CAMS = {
    globe: () => viewer.camera.flyTo({ destination: dayView(C, 26_000_000), duration: 1.6 }),
    leo: () => viewer.camera.flyTo({ destination: C.Cartesian3.fromDegrees(78, 12, 9_000_000), duration: 1.6 }),
    geo: () => viewer.camera.flyTo({ destination: C.Cartesian3.fromDegrees(75, 0, 115_000_000), duration: 1.8 }),
    polar: () => viewer.camera.flyTo({ destination: C.Cartesian3.fromDegrees(0, 90, 22_000_000), orientation: { heading: 0, pitch: -Math.PI / 2, roll: 0 }, duration: 1.8 }),
    follow: () => follow(),
  };
  root.querySelector('.globe-toolbar').addEventListener('click', (e) => {
    const cam = e.target.closest('[data-cam]')?.dataset.cam;
    if (cam) { if (cam !== 'follow') stopFollow(); CAMS[cam](); return; }
    const t = e.target.closest('[data-time]')?.dataset.time;
    if (!t) return;
    if (t === 'live') {
      if (!replay.active) {
        viewer.clock.currentTime = C.JulianDate.now();
        viewer.clock.clockRange = C.ClockRange.UNBOUNDED;
      }
      viewer.clock.multiplier = 1;
      viewer.clock.shouldAnimate = true;
    } else if (t === 'pause') viewer.clock.shouldAnimate = !viewer.clock.shouldAnimate;
    else { viewer.clock.multiplier = Number(t); viewer.clock.shouldAnimate = true; }
  });

  // ---- replays: close approaches (real or synthetic) and simulated burns ---------------
  const replay = { entities: [], active: false, followTarget: null };
  function clearReplay() {
    replay.entities.splice(0).forEach((en) => viewer.entities.remove(en));
    replay.active = false;
    replay.followTarget = null;
    viewer.clock.clockRange = C.ClockRange.UNBOUNDED;
  }
  const add = (spec) => { const en = viewer.entities.add(spec); replay.entities.push(en); return en; };
  const speedButtons = '<button class="btn sm" data-speed="1">×1</button><button class="btn sm" data-speed="20">×20</button><button class="btn sm" data-speed="60">×60</button>';
  // one delegated listener; each replay sets the instants its buttons jump to
  let jumps = {};
  function wireSpeeds(panel, j) { jumps = j; }
  info.addEventListener('click', (ev) => {
    const sp = ev.target.closest('[data-speed]');
    if (sp) { viewer.clock.multiplier = Number(sp.dataset.speed); viewer.clock.shouldAnimate = true; }
    const j = ev.target.closest('[data-jump]');
    if (j && jumps[j.dataset.jump]) {
      viewer.clock.currentTime = C.JulianDate.addSeconds(jumps[j.dataset.jump], -20, new C.JulianDate());
      viewer.clock.multiplier = 1;
      viewer.clock.shouldAnimate = true;
    }
    if (ev.target.closest('[data-close-replay]')) {
      clearReplay();
      info.classList.add('hidden');
      setHashQuery({});
      CAMS.globe();
      viewer.clock.currentTime = C.JulianDate.now();
    }
  });

  function encounterEntities(props, labels, tints, tca, missKm, risk) {
    props.forEach((prop, k) => {
      add({ position: prop, properties: labels[k].norad ? { norad: labels[k].norad } : undefined,
        point: { pixelSize: 9, color: tints[k], outlineColor: C.Color.BLACK, outlineWidth: 1.5 },
        label: { text: labels[k].text, font: labelFont, fillColor: tints[k], showBackground: true, backgroundColor: bg,
          pixelOffset: new C.Cartesian2(14, k ? 16 : -16), horizontalOrigin: C.HorizontalOrigin.LEFT },
        path: { leadTime: 600, trailTime: 600, width: 2, resolution: 10, material: tints[k].withAlpha(0.85) } });
    });
    const both = (time) => [props[0].getValue(time), props[1].getValue(time)];
    add({ polyline: { positions: new C.CallbackProperty((time) => { const [a, b] = both(time); return a && b ? [a, b] : []; }, false),
      width: 1.5, material: new C.PolylineDashMaterialProperty({ color: C.Color.fromCssColorString(RISK_COLORS.MEDIUM) }) } });
    add({ position: new C.CallbackProperty((time) => { const [a, b] = both(time); return a && b ? C.Cartesian3.midpoint(a, b, new C.Cartesian3()) : undefined; }, false),
      label: { text: new C.CallbackProperty((time) => { const [a, b] = both(time); return a && b ? `${fmt.num(C.Cartesian3.distance(a, b) / 1000, 1)} km` : ''; }, false),
        font: labelFont, fillColor: C.Color.fromCssColorString(RISK_COLORS.MEDIUM), showBackground: true, backgroundColor: bg, pixelOffset: new C.Cartesian2(0, -20) } });
    const atTca = props[0].getValue(tca);
    const rc = C.Color.fromCssColorString(RISK_COLORS[risk] || RISK_COLORS.MEDIUM);
    if (atTca) {
      add({ position: atTca, point: { pixelSize: 7, color: rc },
        label: { text: `TCA · ${fmt.num(missKm, 3)} km`, font: labelFont, fillColor: C.Color.WHITE, showBackground: true,
          backgroundColor: rc.withAlpha(0.85), pixelOffset: new C.Cartesian2(0, 24) } });
      viewer.camera.flyToBoundingSphere(new C.BoundingSphere(atTca, 2_600_000), { duration: 2 });
    }
  }

  function setReplayClock(start, stop, current, multiplier = 20) {
    Object.assign(viewer.clock, { startTime: start, stopTime: stop, clockRange: C.ClockRange.LOOP_STOP, multiplier, shouldAnimate: true });
    viewer.clock.currentTime = current;
  }
  const scrubber = () => `<div class="scrub"><input type="range" id="scrub" min="0" max="1000" value="0" aria-label="Replay time">
    <span class="mono small" id="scrubT"></span></div>`;
  info.addEventListener('input', (ev) => {
    if (ev.target.id !== 'scrub') return;
    const c = viewer.clock;
    const span = C.JulianDate.secondsDifference(c.stopTime, c.startTime);
    c.currentTime = C.JulianDate.addSeconds(c.startTime, (Number(ev.target.value) / 1000) * span, new C.JulianDate());
  });
  const scrubTimer = setInterval(() => {
    const el = info.querySelector('#scrub');
    if (!el || !replay.active || viewer.isDestroyed()) return;
    const c = viewer.clock;
    const span = C.JulianDate.secondsDifference(c.stopTime, c.startTime);
    if (document.activeElement !== el) el.value = String(Math.round((C.JulianDate.secondsDifference(c.currentTime, c.startTime) / span) * 1000));
    info.querySelector('#scrubT').textContent = `${C.JulianDate.toDate(c.currentTime).toISOString().slice(11, 19)} UTC`;
  }, 250);

  async function replayEvent(eventId, synthetic = false) {
    clearSelection();
    clearReplay();
    replay.active = true;
    setHashQuery(synthetic ? { demo_event: eventId } : { event: eventId });
    info.classList.remove('hidden');
    info.innerHTML = '<div class="muted small mono">LOADING CLOSE APPROACH…</div>';
    try {
      let e;
      let names2;
      let tr;
      if (synthetic) {
        tr = await get(`/demo/events/${eventId}/track`);
        const { items } = await get('/demo');
        const ev = items.flatMap((s) => (s.events || []).map((x) => ({ ...x, target_name: s.target_name, target_norad: s.target_norad })))
          .find((x) => x.demo_event_id === eventId);
        if (!ev) throw new Error('This synthetic scenario has been cleared.');
        e = { event_id: eventId, risk_level: ev.risk_level, time_of_closest_approach: ev.time_of_closest_approach, miss_distance_km: ev.miss_distance_km,
          relative_velocity: ev.relative_velocity, probability_of_collision: ev.probability_of_collision,
          primary_norad: ev.target_norad, primary_name: ev.target_name, secondary_name: `${ev.designation} (SYNTHETIC)` };
        names2 = [{ text: ev.target_name, norad: ev.target_norad }, { text: `${ev.designation} · SYNTHETIC` }];
      } else {
        const [d, track] = await Promise.all([get(`/conjunctions/${eventId}`), get(`/conjunctions/${eventId}/track`)]);
        e = d.event;
        tr = track;
        names2 = [{ text: d.primary.name, norad: d.primary.norad_id }, { text: d.secondary.name, norad: d.secondary.norad_id }];
      }
      if (viewer.isDestroyed()) return;
      const tca = C.JulianDate.fromIso8601(tr.tca);
      setReplayClock(C.JulianDate.addSeconds(tca, -1800, new C.JulianDate()), C.JulianDate.addSeconds(tca, 1800, new C.JulianDate()),
        C.JulianDate.addSeconds(tca, -240, new C.JulianDate()));
      const props = tr.objects.map((o) => sampledTrack(C, tca, o.offsets_s, o.ecef_km));
      const tints = [C.Color.WHITE, synthetic ? synColor : C.Color.fromCssColorString('#ec835a')];
      if (props.length === 2) encounterEntities(props, names2, tints, tca, e.miss_distance_km, e.risk_level);
      replay.followTarget = replay.entities[0];
      info.innerHTML = `<div class="spread"><h2>${synthetic ? 'Synthetic' : 'Close'} approach #${e.event_id}</h2><div class="row">${synthetic ? synTag() : ''}${riskBadge(e.risk_level)}</div></div>
        <p style="margin:10px 0">${objLink(e.primary_norad, e.primary_name)} <span class="muted mono">×</span> ${synthetic ? esc(e.secondary_name) : objLink(e.secondary_norad, e.secondary_name)}</p>
        <dl class="telemetry">
          <dt>${term('tca', 'TCA')}</dt><dd>${fmt.dt(e.time_of_closest_approach)}</dd>
          <dt>${term('miss', 'Miss distance')}</dt><dd>${fmt.km(e.miss_distance_km, 3)}</dd>
          <dt>Rel. velocity</dt><dd>${fmt.num(e.relative_velocity, 2)} km/s</dd>
          <dt>${term('pc', 'Pc')}</dt><dd>${e.probability_of_collision == null ? '—' : Number(e.probability_of_collision).toExponential(2)}</dd>
        </dl>
        <p style="margin-top:12px"><a class="mono small" href="${synthetic ? `#/demo?event=${e.event_id}` : `#/conjunctions?event=${e.event_id}`}">AI DECISION SUPPORT →</a></p>
        ${scrubber()}
        <div class="row" style="margin-top:10px">${speedButtons}<button class="btn sm primary" data-jump="tca">Jump to TCA</button><button class="btn sm ghost" data-close-replay>Close</button></div>
        <p class="small muted" style="margin-top:12px">Replays ±30 minutes around TCA with each object's current element set. The dashed line is the live separation.</p>`;
      wireSpeeds(info, { tca });
    } catch (err) {
      info.innerHTML = `<div>${esc(err.message)}</div>`;
    }
  }

  async function replaySimulation(assessmentId) {
    clearSelection();
    clearReplay();
    replay.active = true;
    info.classList.remove('hidden');
    info.innerHTML = '<div class="muted small mono">COMPUTING THE SIMULATED BURN…</div>';
    try {
      const [sim, a] = await Promise.all([get(`/assessments/${assessmentId}/simulation`), get(`/assessments/${assessmentId}`)]);
      if (viewer.isDestroyed()) return;
      const start = C.JulianDate.fromIso8601(sim.start);
      const burn = C.JulianDate.fromIso8601(sim.burn.time);
      const tca = C.JulianDate.fromIso8601(sim.tca.time);
      const end = C.JulianDate.addSeconds(start, sim.offsets_s[sim.offsets_s.length - 1], new C.JulianDate());
      setReplayClock(start, end, C.JulianDate.addSeconds(burn, -45, new C.JulianDate()), 30);
      const before = sampledTrack(C, start, sim.offsets_s, sim.primary_before_ecef_km);
      const after = sampledTrack(C, start, sim.offsets_s, sim.primary_after_ecef_km);
      const sec = sampledTrack(C, start, sim.offsets_s, sim.secondary_ecef_km);
      const green = C.Color.fromCssColorString('#35b779');
      const secColor = sim.synthetic ? synColor : C.Color.fromCssColorString('#ec835a');
      add({ position: before, point: { pixelSize: 6, color: C.Color.WHITE.withAlpha(0.7) },
        path: { leadTime: 7200, trailTime: 7200, width: 1.5, resolution: 20, material: new C.PolylineDashMaterialProperty({ color: C.Color.WHITE.withAlpha(0.55), dashLength: 12 }) },
        properties: { hover: `${sim.primary_name}: nominal orbit (no burn)` } });
      const afterEntity = add({ availability: new C.TimeIntervalCollection([new C.TimeInterval({ start: burn, stop: end })]),
        position: after, point: { pixelSize: 9, color: green, outlineColor: C.Color.BLACK, outlineWidth: 1.5 },
        label: { text: `${sim.primary_name} · post-burn (SIMULATED)`, font: labelFont, fillColor: green, showBackground: true, backgroundColor: bg,
          pixelOffset: new C.Cartesian2(14, -16), horizontalOrigin: C.HorizontalOrigin.LEFT },
        path: { leadTime: 7200, trailTime: 7200, width: 2.2, resolution: 20, material: green } });
      add({ position: sec, point: { pixelSize: 9, color: secColor, outlineColor: C.Color.BLACK, outlineWidth: 1.5 },
        label: { text: `${sim.secondary_name}${sim.synthetic ? ' · SYNTHETIC' : ''}`, font: labelFont, fillColor: secColor, showBackground: true,
          backgroundColor: bg, pixelOffset: new C.Cartesian2(14, 16), horizontalOrigin: C.HorizontalOrigin.LEFT },
        path: { leadTime: 900, trailTime: 900, width: 1.6, resolution: 10, material: secColor.withAlpha(0.85) } });
      // burn: a flare that expands for ten seconds of simulated time after ignition
      const burnPos = before.getValue(burn);
      const flare = ringCanvas(ACCENT, 96, 4);
      add({ position: burnPos, billboard: { image: flare, scale: new C.CallbackProperty((time) => {
        const dt = C.JulianDate.secondsDifference(time, burn);
        return dt >= 0 && dt <= 600 ? 0.3 + (dt / 600) * 1.6 : 0;
      }, false), color: new C.CallbackProperty((time) => {
        const dt = C.JulianDate.secondsDifference(time, burn);
        return accent.withAlpha(dt >= 0 && dt <= 600 ? 1 - dt / 600 : 0);
      }, false) },
      point: { pixelSize: 6, color: accent },
      label: { text: `BURN · ${fmt.num(sim.burn.dv_mps, 3)} m/s ${sim.burn.direction || ''}`, font: labelFont, fillColor: accent, showBackground: true,
        backgroundColor: bg, pixelOffset: new C.Cartesian2(0, -22) } });
      const tcaPos = before.getValue(tca);
      if (tcaPos) {
        add({ position: tcaPos, point: { pixelSize: 6, color: C.Color.fromCssColorString(RISK_COLORS.CRITICAL) },
          label: { text: `TCA · ${fmt.num(sim.miss_before_km, 3)} → ${fmt.num(sim.miss_after_km, 3)} km`, font: labelFont, fillColor: C.Color.WHITE,
            showBackground: true, backgroundColor: C.Color.fromCssColorString(RISK_COLORS.CRITICAL).withAlpha(0.85), pixelOffset: new C.Cartesian2(0, 24) } });
      }
      const pair = (time) => [(C.JulianDate.greaterThanOrEquals(time, burn) ? after : before).getValue(time), sec.getValue(time)];
      add({ polyline: { positions: new C.CallbackProperty((time) => { const [p, s2] = pair(time); return p && s2 ? [p, s2] : []; }, false),
        width: 1.2, material: new C.PolylineDashMaterialProperty({ color: C.Color.fromCssColorString(RISK_COLORS.MEDIUM) }) } });
      add({ position: new C.CallbackProperty((time) => { const [p, s2] = pair(time); return p && s2 ? C.Cartesian3.midpoint(p, s2, new C.Cartesian3()) : undefined; }, false),
        label: { text: new C.CallbackProperty((time) => { const [p, s2] = pair(time); return p && s2 ? `${fmt.num(C.Cartesian3.distance(p, s2) / 1000, 2)} km` : ''; }, false),
          font: labelFont, fillColor: C.Color.fromCssColorString(RISK_COLORS.MEDIUM), showBackground: true, backgroundColor: bg, pixelOffset: new C.Cartesian2(0, -20) } });
      replay.followTarget = afterEntity;
      viewer.camera.flyToBoundingSphere(new C.BoundingSphere(burnPos, 3_000_000), { duration: 2 });
      const d = a.decision_record;
      info.innerHTML = `<div class="spread"><h2>Avoidance burn</h2><div class="row">${simTag()}${sim.synthetic ? synTag() : ''}</div></div>
        <p class="small" style="margin:10px 0">${esc(sim.primary_name)} avoiding ${esc(sim.secondary_name)} · assessment #${assessmentId}
          ${d ? `· <span class="tag ${d.status === 'APPROVED' ? 'ok' : 'bad'}">${esc(d.status.toLowerCase())}</span>` : '· <span class="tag warn">not yet approved</span>'}</p>
        <div class="sim-legend"><i style="background:rgba(255,255,255,.6)"></i>nominal orbit (no burn)<i style="background:#35b779"></i>after the burn (simulated)
          <i style="background:${sim.synthetic ? SYN_COLOR : '#ec835a'}"></i>${esc(sim.secondary_name)}<i style="background:${ACCENT}"></i>burn</div>
        <dl class="telemetry">
          <dt>${term('dv', 'Δv')}</dt><dd>${fmt.num(sim.burn.dv_mps, 3)} m/s ${esc(sim.burn.direction || '')}</dd>
          <dt>Burn</dt><dd>${fmt.dt(sim.burn.time)}</dd>
          <dt>Miss at TCA</dt><dd>${fmt.num(sim.miss_before_km, 3)} → <span class="status-ok">${fmt.num(sim.miss_after_km, 3)} km</span></dd>
          <dt>Closest after burn</dt><dd>${fmt.num(sim.closest_after.distance_km, 3)} km at ${sim.closest_after.time.slice(11, 19)} UTC</dd>
        </dl>
        ${scrubber()}
        <div class="row" style="margin-top:10px">${speedButtons}<button class="btn sm" data-jump="burn">Jump to burn</button><button class="btn sm primary" data-jump="tca">Jump to TCA</button><button class="btn sm ghost" data-close-replay>Close</button></div>
        <p class="small muted" style="margin-top:12px">${esc(sim.model)}. OrbitWatch has no command uplink: nothing is sent to a spacecraft.</p>`;
      wireSpeeds(info, { burn, tca });
    } catch (err) {
      info.innerHTML = `<div>${esc(err.message)}</div>`;
    }
  }

  if (q.assessment) replaySimulation(Number(q.assessment));
  else if (q.event) replayEvent(Number(q.event));
  else if (q.demo_event) replayEvent(Number(q.demo_event), true);
  else if (q.norad) setTimeout(() => select(Number(q.norad), true), 1200);

  return () => {
    clearInterval(timer);
    clearInterval(synTimer);
    clearInterval(scrubTimer);
    handler.destroy();
    cloud.destroy();
    viewer.destroy();
  };
}
