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
import { EncounterReplay, glowCanvas } from '../encounter.js';
import { ACCENT, CatalogCloud, createViewer, dayView, indiaSun, indiaView, INDIA, loadCesium, orbitRing, REGIMES, RISK_COLORS, sampledOrbit, sampledTrack,
  shapeCanvas, ringCanvas, SYN_COLOR, TYPE_COLORS, TYPE_NAMES } from '../globe-core.js';
import { istDate, istHMS, utcHMS } from '../time.js';
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
    <div class="lock" id="lock" aria-hidden="true"><div class="lock-box"><i></i><i></i><i></i><i></i></div><pre class="lock-ro" id="lockRo"></pre></div>
    <div class="globe-hud" id="hud"></div>
    <div class="globe-credits" id="credits"></div>
    <div class="globe-toolbar" role="toolbar" aria-label="Camera and time">
      <span class="lbl">VIEW</span>
      <div class="grp"><button data-cam="india" title="Centred on India: day and night follow the IST clock">India</button><button data-cam="globe" title="Whole Earth, sunlit side">Sunlit</button><button data-cam="leo" title="Low Earth orbit">LEO</button>
        <button data-cam="geo" title="Geostationary belt">GEO belt</button><button data-cam="polar" title="Over the North Pole">Polar</button>
        <button data-cam="follow" id="followBtn" title="Follow the selected object">Follow</button></div>
      <span class="lbl">TIME</span>
      <div class="grp"><button data-time="live" title="Real time">Live</button><button data-time="10">×10</button><button data-time="60">×60</button>
        <button data-time="300">×300</button><button data-time="pause" title="Pause / resume">❚❚</button></div>
    </div>
  </div>`;
  const $ = (s) => root.querySelector(s);
  const status = $('#globeStatus');
  // A replay (close approach or simulated burn) is run by encounter.js; this page hides everything else meanwhile.
  const replay = { active: false, encounter: null, saved: null };
  $('#toolsToggle').addEventListener('click', () => {
    const open = $('#tools').classList.toggle('open');
    $('#toolsToggle').setAttribute('aria-expanded', String(open));
  });

  let C;
  try { C = await loadCesium(); } catch (err) { status.textContent = err.message; return undefined; }
  if (!root.isConnected) return undefined;

  const viewer = createViewer(C, $('#cesium'), { interactive: true, creditContainer: root.querySelector('#credits') });
  viewer.camera.setView({ destination: indiaView(C, 24_000_000) });
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
  const cloud = new CatalogCloud(C, viewer, { span: 60, pixelSize: 1.8, onLoad: paintLegend, adaptive: true });
  cloud.colors = cloud.colors.map((col) => col.withAlpha(0.74));      // translucent: the Earth stays visible through the catalogue
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
  const over = { n: 0, wall: 0 };
  const timer = setInterval(() => {
    const now = C.JulianDate.toDate(viewer.clock.currentTime);
    const offset = (now - Date.now()) / 1000;
    const isLive = Math.abs(offset) < 5 && viewer.clock.multiplier === 1;
    const sun = indiaSun(now);
    const phase = sun > 0 ? 'day' : sun > -6 ? 'twilight' : 'night';
    if (!over.wall || performance.now() - over.wall > 2000) { over.n = cloud.countOver(INDIA.box); over.wall = performance.now(); }
    $('#clockLabel').textContent = `${istHMS(now)} IST ×${viewer.clock.multiplier}`;
    $('#hud').innerHTML = `<b>${istDate(now)} ${istHMS(now)} IST</b> · ${utcHMS(now)} UTC · ×${viewer.clock.multiplier}${isLive ? ' · LIVE' : ` · ${offset > 0 ? '+' : '−'}${fmt.num(Math.abs(offset) / 60, 0)} min`}<br>
      India: <b>${phase}</b> (Sun ${sun >= 0 ? '+' : '−'}${Math.abs(sun).toFixed(0)}°) · ${fmt.int(over.n)} objects over the Indian region<br>
      ${fmt.int(cloud.visibleCount())} objects shown · ${viewer.clock.shouldAnimate ? 'running' : 'paused'}`;
    if (cloud.error) status.textContent = cloud.error.message;
  }, 500);

  // ---- selection -------------------------------------------------------------------
  let selected = null;
  let orbitEntity = null;
  let marker = null;              // the selected object: a glowing marker with its name, riding the orbit ring
  const info = $('#info');
  const clearSelection = () => {
    if (selected) cloud.highlight(selected, false);
    selected = null;
    if (orbitEntity) { viewer.entities.remove(orbitEntity); orbitEntity = null; }
    if (marker) { viewer.entities.remove(marker); marker = null; }
    stopFollow();
  };
  function stopFollow() {
    viewer.trackedEntity = undefined;
    $('#followBtn').classList.remove('on');
  }
  async function select(norad, fly = false) {
    clearReplay();
    if (selected) cloud.highlight(selected, false);
    stopFollow();
    selected = norad;
    if (marker) { viewer.entities.remove(marker); marker = null; }
    marker = viewer.entities.add({
      position: new C.CallbackProperty((time) => cloud.positionOf(norad, time), false),
      billboard: { image: glowCanvas(ACCENT), scale: 0.6 },
      point: { pixelSize: 7, color: C.Color.WHITE, outlineColor: accent, outlineWidth: 2 },
      label: { text: names?.get(norad) || `NORAD ${norad}`, font: labelFont, fillColor: C.Color.WHITE, showBackground: true, backgroundColor: bg,
        backgroundPadding: new C.Cartesian2(6, 4), pixelOffset: new C.Cartesian2(16, -18), horizontalOrigin: C.HorizontalOrigin.LEFT },
    });
    const rec = cloud.highlight(norad, true);
    if (fly && rec) viewer.camera.flyToBoundingSphere(new C.BoundingSphere(rec.point.position, 1_600_000), { duration: 1.6 });
    if (orbitEntity) { viewer.entities.remove(orbitEntity); orbitEntity = null; }
    info.classList.remove('hidden');
    info.innerHTML = '<div class="muted small mono">LOADING…</div>';
    try {
      const from = C.JulianDate.addSeconds(viewer.clock.currentTime, -600, new C.JulianDate());
      const [d, orbit] = await Promise.all([get(`/objects/${norad}`), sampledOrbit(C, norad, from, 1.15).catch(() => null)]);
      if (selected !== norad || viewer.isDestroyed()) return;
      if (orbit) {
        // the orbit itself (closed, inertial), turned with the Earth; plus the object riding on it
        orbitEntity = viewer.entities.add({
          polyline: orbit.teme ? { positions: orbitRing(C, orbit.teme), width: 1.6, arcType: C.ArcType.NONE,
            material: accent.withAlpha(0.8) } : undefined,
          position: orbit.prop,
          // a comet tail: where it was in the last ten minutes
          path: { leadTime: 0, trailTime: 600, width: 4, resolution: 20, material: new C.PolylineGlowMaterialProperty({ glowPower: 0.2, color: accent.withAlpha(0.9) }) },
        });
      }
      if (marker) marker.label.text = d.object.name;
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
    const target = marker;
    if (!target) { info.classList.remove('hidden'); info.innerHTML = '<div class="small muted">Select an object (click a point or search) to follow it.</div>'; return; }
    viewer.trackedEntity = target;
    $('#followBtn').classList.add('on');
  }

  // ---- target lock: a bracket reticle and a live readout that follow the selected object ----------------------
  // Both come from the same interpolated position as the marker itself; they hide while the object is behind the Earth.
  const lock = $('#lock');
  const lockRo = $('#lockRo');
  const occluder = new C.EllipsoidalOccluder(C.Ellipsoid.WGS84, viewer.camera.positionWC);
  const lockWin = new C.Cartesian2();
  let lockRead = 0;
  let lockId = null;
  const removeLock = viewer.scene.postRender.addEventListener(() => {
    const pos = selected ? cloud.positionOf(selected, viewer.clock.currentTime) : null;
    if (!pos) { lock.classList.remove('on'); lockId = null; return; }
    occluder.cameraPosition = viewer.camera.positionWC;
    const win = occluder.isPointVisible(pos) ? viewer.scene.cartesianToCanvasCoordinates(pos, lockWin) : undefined;
    if (!win) { lock.classList.remove('on'); lockId = null; return; }
    if (lockId !== selected) { lockId = selected; lock.classList.remove('on'); void lock.offsetWidth; }   // restart the lock-on animation
    lock.classList.add('on');
    lock.style.transform = `translate3d(${win.x.toFixed(1)}px, ${win.y.toFixed(1)}px, 0)`;
    const now = performance.now();
    if (now - lockRead > 250) {
      lockRead = now;
      const c = C.Cartographic.fromCartesian(pos);
      const lat = C.Math.toDegrees(c.latitude);
      const lon = C.Math.toDegrees(c.longitude);
      lockRo.textContent = `ALT ${fmt.int(c.height / 1000)} KM\n${Math.abs(lat).toFixed(1)}°${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(1)}°${lon >= 0 ? 'E' : 'W'}`;
    }
  });

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
          hover: `${m.risk} · ${m.primary.name} × ${m.secondary.name} · ${fmt.num(m.miss_km, 3)} km · TCA ${fmt.dt(m.tca)}` },
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
          show: $('[data-layer="demo"]').checked && !replay.active,
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
    india: () => viewer.camera.flyTo({ destination: indiaView(C, 24_000_000), orientation: { heading: 0, pitch: -Math.PI / 2, roll: 0 }, duration: 1.6 }),
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
  const page = root.querySelector('.globe-page');
  function hideOthers(on) {
    if (on) {
      replay.saved = true;
      cloud.points.show = false;
      markerEntities.forEach((en) => { en.show = false; });
      synEntities.forEach((en) => { en.show = false; });
      stationEntities.forEach((en) => { en.show = false; });
      hover.classList.add('hidden');
    } else if (replay.saved) {
      replay.saved = null;
      cloud.points.show = $('[data-layer="cloud"]').checked;
      showMarkers();
      synEntities.forEach((en) => { en.show = $('[data-layer="demo"]').checked; });
      stationEntities.forEach((en) => { en.show = $('[data-layer="stations"]').checked; });
    }
  }
  function clearReplay() {
    if (replay.encounter) { replay.encounter.destroy(); replay.encounter = null; }
    replay.active = false;
    page.querySelectorAll('.enc-loading').forEach((n) => n.remove());
  }
  function closeReplay() {
    clearReplay();
    setHashQuery({});
    info.classList.add('hidden');
    CAMS.india();
  }
  const showLoading = (text) => {
    const el = document.createElement('div');
    el.className = 'enc-loading';
    el.textContent = text;
    page.appendChild(el);
    return () => el.remove();
  };
  // the closed orbit of one object (TEME ring) and its period, or null when it has no current orbit (synthetic objects)
  const ringFor = (norad, time) => (norad && /^\d+$/.test(String(norad))
    ? sampledOrbit(C, Number(norad), time, 1).then((o) => ({ teme: o.teme, periodS: o.periodS })).catch(() => null) : Promise.resolve(null));
  const fail = (err) => { clearReplay(); info.classList.remove('hidden'); info.innerHTML = `<div>${esc(err.message)}</div>`; };

  async function replayEvent(eventId, synthetic = false) {
    clearSelection();
    clearReplay();
    replay.active = true;
    setHashQuery(synthetic ? { demo_event: eventId } : { event: eventId });
    info.classList.add('hidden');
    const done = showLoading('Loading the close approach');
    try {
      const win = '?before=5400&after=900&step=15';      // a full orbit of lead-in, then the pass
      let e; let tr; let prim; let sec;
      if (synthetic) {
        tr = await get(`/demo/events/${eventId}/track${win}`);
        const { items } = await get('/demo');
        const ev = items.flatMap((sc) => (sc.events || []).map((x) => ({ ...x, target_name: sc.target_name, target_norad: sc.target_norad })))
          .find((x) => x.demo_event_id === eventId);
        if (!ev) throw new Error('This synthetic scenario has been cleared.');
        e = { event_id: eventId, risk_level: ev.risk_level, time_of_closest_approach: ev.time_of_closest_approach, miss_distance_km: ev.miss_distance_km,
          relative_velocity: ev.relative_velocity, probability_of_collision: ev.probability_of_collision };
        prim = { name: ev.target_name, norad: ev.target_norad };
        sec = { name: ev.designation, norad: null };
      } else {
        const [d, track] = await Promise.all([get(`/conjunctions/${eventId}`), get(`/conjunctions/${eventId}/track${win}`)]);
        e = d.event; tr = track;
        prim = { name: d.primary.name, norad: d.primary.norad_id };
        sec = { name: d.secondary.name, norad: d.secondary.norad_id };
      }
      if (tr.objects.length < 2) throw new Error('One of the two objects has no current orbit, so this approach cannot be replayed.');
      if (viewer.isDestroyed() || !replay.active) return;
      const tca = C.JulianDate.fromIso8601(tr.tca);
      const [o1, o2] = tr.objects;
      const first = Math.max(o1.offsets_s[0], o2.offsets_s[0]);
      const last = Math.min(o1.offsets_s[o1.offsets_s.length - 1], o2.offsets_s[o2.offsets_s.length - 1]);
      const [ring1, ring2] = await Promise.all([ringFor(prim.norad, tca), ringFor(sec.norad, tca)]);
      if (viewer.isDestroyed() || !replay.active) return;
      replay.encounter = new EncounterReplay(C, viewer, page, {
        kind: 'event', synthetic, risk: e.risk_level, primary: prim, secondary: sec,
        tca, tcaIso: tr.tca, start: C.JulianDate.addSeconds(tca, first, new C.JulianDate()), end: C.JulianDate.addSeconds(tca, last, new C.JulianDate()),
        burn: null, before: sampledTrack(C, tca, o1.offsets_s, o1.ecef_km), after: null, secondaryTrack: sampledTrack(C, tca, o2.offsets_s, o2.ecef_km),
        miss: { before: e.miss_distance_km, after: null }, rel_velocity: e.relative_velocity, pc: e.probability_of_collision,
        ringPrimary: ring1?.teme || null, ringSecondary: ring2?.teme || null, periodS: ring1?.periodS || null,
        detailsHref: synthetic ? `#/demo?event=${e.event_id}` : `#/conjunctions?event=${e.event_id}`,
        hideOthers, onClose: closeReplay,
      });
    } catch (err) {
      fail(err);
    } finally {
      done();
    }
  }

  async function replaySimulation(assessmentId) {
    clearSelection();
    clearReplay();
    replay.active = true;
    info.classList.add('hidden');
    const done = showLoading('Computing the simulated burn');
    try {
      const [sim, a] = await Promise.all([get(`/assessments/${assessmentId}/simulation`), get(`/assessments/${assessmentId}`)]);
      if (viewer.isDestroyed() || !replay.active) return;
      const start = C.JulianDate.fromIso8601(sim.start);
      const burn = C.JulianDate.fromIso8601(sim.burn.time);
      const tca = C.JulianDate.fromIso8601(sim.tca.time);
      const end = C.JulianDate.addSeconds(start, sim.offsets_s[sim.offsets_s.length - 1], new C.JulianDate());
      const subj = a.subject || {};
      const [ring1, ring2] = await Promise.all([ringFor(subj.primary_norad, tca), sim.synthetic ? null : ringFor(subj.secondary_norad, tca)]);
      if (viewer.isDestroyed() || !replay.active) return;
      replay.encounter = new EncounterReplay(C, viewer, page, {
        kind: 'burn', synthetic: !!sim.synthetic, primary: { name: sim.primary_name, norad: subj.primary_norad },
        secondary: { name: sim.secondary_name, norad: subj.secondary_norad },
        tca, tcaIso: sim.tca.time, start, end,
        burn: { time: burn, iso: sim.burn.time, dv_mps: sim.burn.dv_mps, direction: sim.burn.direction },
        before: sampledTrack(C, start, sim.offsets_s, sim.primary_before_ecef_km), after: sampledTrack(C, start, sim.offsets_s, sim.primary_after_ecef_km),
        secondaryTrack: sampledTrack(C, start, sim.offsets_s, sim.secondary_ecef_km),
        miss: { before: sim.miss_before_km, after: sim.miss_after_km }, rel_velocity: null, pc: a.pc_before,
        ringPrimary: ring1?.teme || null, ringSecondary: ring2?.teme || null, periodS: ring1?.periodS || null,
        detailsHref: a.demo_event_id ? `#/demo?event=${a.demo_event_id}` : `#/conjunctions?event=${a.event_id}`,
        hideOthers, onClose: closeReplay,
      });
    } catch (err) {
      fail(err);
    } finally {
      done();
    }
  }

  if (q.assessment) replaySimulation(Number(q.assessment));
  else if (q.event) replayEvent(Number(q.event));
  else if (q.demo_event) replayEvent(Number(q.demo_event), true);
  else if (q.norad) setTimeout(() => select(Number(q.norad), true), 1200);

  return () => {
    clearInterval(timer);
    clearInterval(synTimer);
    clearReplay();
    removeLock();
    handler.destroy();
    cloud.destroy();
    viewer.destroy();
  };
}
