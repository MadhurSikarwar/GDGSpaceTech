import { state, subscribe, setObjects, setTrajectory, setConjunctions, selectObject, setActiveConjunction, setServiceStatus, setView, pushLog, setRisk, enterExplore, exitExplore, setExploreConjunction } from './state.js';
import * as api from './api.js';
import { initGlobe } from './globe.js';
import { initTopbar, initLogDrawer, toast } from './panels/topbar.js';
import { initCatalog } from './panels/catalog.js';
import { initConjunctions } from './panels/conjunctions.js';
import { initPipeline, openConjunction } from './panels/pipeline.js';
import { icons } from './icons.js';
import { fmtKm, fmtNum, clamp, fmtCountdown, escapeHtml, fmtRelVel } from './utils.js';

const TYPE_COLOR_HEX = { SATELLITE: 0x56e3d1, DEBRIS: 0xf0a94e, ROCKET_BODY: 0xa99bf2, SYNTHETIC_DEBRIS: 0xf0616e, UNKNOWN: 0x8a95a8 };
const PRIMARY_COLOR = 0x56e3d1;
const SECONDARY_COLOR = 0xf0616e;
const POST_MANEUVER_COLOR = 0x4ad991;

let globe;
const trajectoryCache = new Map();
let singleSelectedId = null;
let conjunctionFocusIds = [];
let postManeuverGhostKey = null;
let tcaWatch = null; // { conj, tcaMinutes } — set while Explore Mode auto-plays toward a TCA

const BOOT_LINES = [
  'INITIALIZING SGP4 PROPAGATION ENGINE...',
  'CONNECTING TO TRACKING SERVICE :8000...',
  'LOADING ORBITAL CATALOG...',
  'RUNNING 2-STAGE CONJUNCTION SCREENING...',
  'MISSION CONSOLE ONLINE.',
];

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runBootSequence() {
  const bootLog = document.getElementById('bootLog');
  const bootBar = document.getElementById('bootBar');
  const overlay = document.getElementById('bootOverlay');
  for (let i = 0; i < BOOT_LINES.length; i++) {
    bootLog.textContent = BOOT_LINES[i];
    bootBar.style.width = `${(i + 1) * 20}%`;
    await wait(350);
  }
  await wait(600);
  overlay.classList.add('done');
  await wait(800);
  overlay.style.display = 'none';
}

async function main() {
  await runBootSequence();

  globe = initGlobe(document.getElementById('globeCanvas'));
  wireGlobeCallbacks();
  wireSelectionCard();
  wireTimelineControls();
  document.getElementById('catalogCollapseBtn').addEventListener('click', () => {
    document.querySelector('.catalog-float').classList.toggle('collapsed');
  });

  const logDrawer = initLogDrawer();
  initTopbar({
    onNavigate: (view) => setView(view),
    onDemoInject: handleDemoInject,
    onToggleLog: () => logDrawer.toggle(),
  });
  initCatalog({ onSelectObject: handleSelectObject, onSyncCatalog: handleSyncCatalog });
  initConjunctions({ onOpenConjunction: handleOpenConjunction, onRunScreen: handleRunScreen });
  initPipeline({ onApprove: handleApprove });

  subscribe((topic) => { if (topic === 'activeConjunction') onActiveConjunctionChanged(); });

  pushLog('Mission console initialized.');
  await Promise.all([refreshHealth(), loadObjects(), loadConjunctions()]);
  setInterval(refreshHealth, 15000);
}

// ---------------------------------------------------------------- health --

async function refreshHealth() {
  const [t, r, m, o] = await Promise.all([
    api.checkHealth(api.BASES.tracking),
    api.checkHealth(api.BASES.risk),
    api.checkHealth(api.BASES.maneuver),
    api.checkHealth(api.BASES.optimizer),
  ]);
  setServiceStatus('tracking', t ? 'live' : 'down');
  setServiceStatus('risk', r ? 'live' : 'sim');
  setServiceStatus('maneuver', m ? 'live' : 'sim');
  setServiceStatus('optimizer', o ? 'live' : 'sim');
}

// --------------------------------------------------------------- loading --

async function loadObjects() {
  const { data, live } = await api.getObjects();
  setObjects(data);
  globe.setObjects(data);
  document.getElementById('renderedCount').textContent = data.length;
  pushLog(`Loaded ${data.length} tracked object${data.length === 1 ? '' : 's'} ${live ? '(live)' : '(offline fixture)'}`, live ? 'info' : 'warn');
}

async function loadConjunctions() {
  const { data, live } = await api.getConjunctions();
  setConjunctions(data);
  pushLog(`${data.length} conjunction candidate${data.length === 1 ? '' : 's'} loaded ${live ? '(live)' : '(offline fixture)'}`);
  data.forEach((c) => { if (!state.risk.has(c.conjunction_id)) prefetchRisk(c); });
}

async function prefetchRisk(conj) {
  try {
    const res = await api.assessRisk(conj);
    setRisk(conj.conjunction_id, res);
  } catch { /* badge just stays PENDING */ }
}

async function fetchTrajectoryCached(catalogId) {
  if (trajectoryCache.has(catalogId)) return trajectoryCache.get(catalogId);
  const data = await api.getTrajectory(catalogId, 90, 1.0);
  trajectoryCache.set(catalogId, data);
  setTrajectory(catalogId, data);
  return data;
}

function findByCatalogId(catalogId) {
  return state.objectsById.get(catalogId);
}

// -------------------------------------------------------------- globe UX --

function wireGlobeCallbacks() {
  globe.onSelect((catalogId) => handleSelectObject(catalogId));
  globe.onFps((fps) => { document.getElementById('fpsValue').textContent = fps; });
  globe.onInteracted(() => document.getElementById('globeStage').classList.add('interacted'));
}

function wireSelectionCard() {
  document.getElementById('scExploreIcon').innerHTML = icons.fly;
  const injectIcon = document.getElementById('scInjectIcon');
  if (injectIcon) injectIcon.innerHTML = icons.alertTriangle;

  document.getElementById('scClose').addEventListener('click', () => {
    if (state.explore.active) handleExitExplore();
    else clearSelection();
  });
  document.getElementById('scExploreBtn').addEventListener('click', handleExplore);
  const injectBtn = document.getElementById('scInjectBtn');
  if (injectBtn) {
    injectBtn.addEventListener('click', () => {
      if (singleSelectedId) handleInjectOnTarget(singleSelectedId);
    });
  }
  document.getElementById('scExplore').addEventListener('click', (e) => {
    if (e.target.closest('#scBackLink')) handleExitExplore();
    if (e.target.closest('#scGotoPipeline')) {
      const conjId = state.explore.conjunctionId;
      if (conjId) { setView('pipeline'); openConjunction(conjId, true); }
    }
  });
}

function clearSelection() {
  const prev = singleSelectedId;
  singleSelectedId = null;
  selectObject(null);
  const card = document.getElementById('selectionCard');
  card.classList.add('hidden');
  card.classList.remove('exploring');
  const explorePane = document.getElementById('scExplore');
  explorePane.classList.add('hidden');
  explorePane.innerHTML = '';
  document.getElementById('tlCaption').textContent = 'Select an object to play its trajectory';
  if (prev && !conjunctionFocusIds.includes(prev)) globe.unfocus(prev);
}

async function handleSelectObject(catalogId) {
  const obj = findByCatalogId(catalogId);
  if (!obj) return;

  const prev = singleSelectedId;
  singleSelectedId = catalogId;
  selectObject(catalogId);
  if (prev && prev !== catalogId && !conjunctionFocusIds.includes(prev)) globe.unfocus(prev);

  renderSelectionCard(obj);
  const color = TYPE_COLOR_HEX[obj.object_type] ?? TYPE_COLOR_HEX.UNKNOWN;

  try {
    const traj = await fetchTrajectoryCached(catalogId);
    if (singleSelectedId !== catalogId) return; // selection changed while awaiting
    globe.focusTrajectory(catalogId, traj.trajectory, color);
    globe.setSimMinutes(state.simMinutes);
    const status = document.getElementById('scTrajStatus');
    if (status) status.textContent = `Trajectory loaded — ${traj.trajectory.length} pts / ${traj.propagation_horizon_minutes} min`;
    if (!state.explore.active) document.getElementById('tlCaption').textContent = `Trajectory playback — ${obj.name}`;
  } catch {
    const status = document.getElementById('scTrajStatus');
    if (status) status.textContent = 'Trajectory unavailable — tracking service unreachable.';
  }

  // Re-selecting (from the catalog, or another object on the globe, or the
  // conjunction partner) while already exploring re-targets Explore Mode at
  // the new object instead of dropping out of it.
  if (state.explore.active) handleExplore();
}

function renderSelectionCard(obj) {
  const card = document.getElementById('selectionCard');
  card.classList.remove('hidden');
  document.getElementById('scName').textContent = obj.name;
  const badge = document.getElementById('scBadge');
  badge.className = `type-badge ${obj.object_type}`;
  badge.innerHTML = `<span class="dot"></span>${obj.object_type.replace('_', ' ')}`;
  document.getElementById('scAlt').textContent = obj.state ? fmtKm(obj.state.altitude_km, 1) : '—';
  document.getElementById('scQuality').textContent = obj.data_quality?.quality ?? '—';
  document.getElementById('scAge').textContent = obj.data_quality ? `${fmtNum(obj.data_quality.data_age_hours, 2)}h` : '—';
  document.getElementById('scCatId').textContent = obj.catalog_id;
  const pos = obj.state?.position_km, vel = obj.state?.velocity_km_s;
  document.getElementById('scVec').innerHTML = pos && vel
    ? `POS  ${pos.x.toFixed(1)}, ${pos.y.toFixed(1)}, ${pos.z.toFixed(1)} km<br/>VEL  ${vel.x.toFixed(2)}, ${vel.y.toFixed(2)}, ${vel.z.toFixed(2)} km/s`
    : 'No state vector available.';
  document.getElementById('scTrajStatus').textContent = 'Loading 90-min trajectory…';
  const injectBtn = document.getElementById('scInjectBtn');
  if (injectBtn) {
    if (obj.object_type === 'DEBRIS' || obj.object_type === 'SYNTHETIC_DEBRIS') {
      injectBtn.textContent = 'DEBRIS TARGET';
      injectBtn.disabled = true;
    } else {
      injectBtn.innerHTML = `<span id="scInjectIcon">${icons.alertTriangle}</span> + INJECT DEBRIS`;
      injectBtn.disabled = false;
    }
  }
}

function nearestPointAtTime(trajectory, isoTime) {
  const target = new Date(isoTime).getTime();
  let best = null, bestDiff = Infinity;
  for (const p of trajectory) {
    const diff = Math.abs(new Date(p.timestamp).getTime() - target);
    if (diff < bestDiff) { bestDiff = diff; best = p; }
  }
  return best;
}

async function onActiveConjunctionChanged() {
  const conjId = state.activeConjunctionId;
  const prevIds = conjunctionFocusIds;

  if (!conjId) {
    prevIds.forEach((id) => { if (id !== singleSelectedId) globe.unfocus(id); });
    conjunctionFocusIds = [];
    globe.clearConjunctionMarker();
    clearPostManeuverGhost();
    renderTlMarkers();
    return;
  }

  const conj = state.conjunctions.find((c) => c.conjunction_id === conjId);
  if (!conj) return;

  clearPostManeuverGhost();
  const newIds = [conj.primary_object, conj.secondary_object];
  prevIds.forEach((id) => { if (!newIds.includes(id) && id !== singleSelectedId) globe.unfocus(id); });
  conjunctionFocusIds = newIds;
  renderTlMarkers();

  try {
    const [trajA, trajB] = await Promise.all([
      fetchTrajectoryCached(conj.primary_object),
      fetchTrajectoryCached(conj.secondary_object),
    ]);
    if (state.activeConjunctionId !== conjId) return;
    globe.focusTrajectory(conj.primary_object, trajA.trajectory, PRIMARY_COLOR);
    globe.focusTrajectory(conj.secondary_object, trajB.trajectory, SECONDARY_COLOR);
    globe.setSimMinutes(state.simMinutes);
    const tcaPoint = nearestPointAtTime(trajA.trajectory, conj.tca);
    if (tcaPoint) globe.showConjunctionAt({ x: tcaPoint.x, y: tcaPoint.y, z: tcaPoint.z });
  } catch (err) {
    console.warn('conjunction trajectory focus failed', err);
  }
}

function clearPostManeuverGhost() {
  if (postManeuverGhostKey) { globe.unfocus(postManeuverGhostKey); postManeuverGhostKey = null; }
}

// ------------------------------------------------------------ explore mode --
// Select → Explore → (Focused | Conjunction) → Return. Built entirely on the
// existing selection/trajectory/conjunction machinery above: Explore Mode
// adds a cinematic camera framing, a relevance-dimmed catalog cloud, and an
// extension of the existing selection card — it doesn't introduce a second
// scene, panel system, or API layer.

// Objects that stay bright in Explore Mode: the selected object, its
// conjunction partner, and whatever is physically near it right now.
//
// This used to be an altitude band (+/-120 km), which reads fine for a small
// catalog but collapses against a real one -- ~93% of the 10.8k live catalog
// sits within 120 km of the ISS shell, so almost nothing got dimmed and the
// "focused" view looked identical to the global one. Straight-line proximity
// is also what "nearby" actually means when investigating an encounter.
const NEIGHBOURHOOD_KM = 600;

function computeRelevantIds(selectedObj, conj) {
  const relevant = new Set([selectedObj.catalog_id]);
  if (conj) { relevant.add(conj.primary_object); relevant.add(conj.secondary_object); }
  const ref = selectedObj.state?.position_km;
  if (ref) {
    const r2 = NEIGHBOURHOOD_KM * NEIGHBOURHOOD_KM;   // compare squared; skip 10k sqrt calls
    for (const o of state.objects) {
      const p = o.state?.position_km;
      if (!p) continue;
      const dx = p.x - ref.x, dy = p.y - ref.y, dz = p.z - ref.z;
      if (dx * dx + dy * dy + dz * dz <= r2) relevant.add(o.catalog_id);
    }
  }
  return relevant;
}

function beginTcaApproach(conj) {
  const rawMinutes = (new Date(conj.tca) - Date.now()) / 60000;
  if (rawMinutes <= 0 || rawMinutes > 90) {
    // TCA already elapsed or outside the 90-min horizon — show the static
    // encounter rather than animating an approach that has already happened.
    setSimMinutes(clamp(rawMinutes, 0, 90));
    tcaWatch = null;
    return;
  }
  setSimMinutes(clamp(rawMinutes - 10, 0, rawMinutes));
  state.playing = true;
  updatePlayIcon();
  tcaWatch = { conj, tcaMinutes: rawMinutes };
}

function applyConjunctionToExplore(obj, conj) {
  setExploreConjunction(conj.conjunction_id);
  globe.setRelevance(computeRelevantIds(obj, conj));
  setActiveConjunction(conj.conjunction_id); // reuses the existing trajectory-fetch + TCA-marker flow

  const otherId = obj.catalog_id === conj.primary_object ? conj.secondary_object : conj.primary_object;
  const other = findByCatalogId(otherId);
  const posA = globe.kmToScene(obj.state.position_km);
  const posB = other?.state ? globe.kmToScene(other.state.position_km) : posA;
  globe.exploreConjunction(posA, posB, { duration: 2200 });

  beginTcaApproach(conj);
  renderExploreCard(obj, conj);
}

async function handleExplore() {
  const objId = state.selectedObjectId;
  const obj = findByCatalogId(objId);
  if (!obj || !obj.state) return;

  const conj = state.conjunctions.find((c) => c.primary_object === objId || c.secondary_object === objId);
  const wasActive = state.explore.active;
  enterExplore(objId, conj?.conjunction_id ?? null);
  if (!wasActive) pushLog(`Entering orbital analysis for ${obj.name}${conj ? ' — conjunction detected' : ''}.`, conj ? 'warn' : 'info');

  if (conj) {
    applyConjunctionToExplore(obj, conj);
  } else {
    tcaWatch = null;
    globe.setRelevance(computeRelevantIds(obj, null));
    globe.exploreObject(globe.kmToScene(obj.state.position_km), { duration: 1800 });
    renderExploreCard(obj, null);
  }
}

function handleExitExplore() {
  pushLog('Returning to global view.');
  tcaWatch = null;
  exitExplore();
  globe.clearRelevance();
  globe.returnToGlobal(1500);
  clearSelection();
}

function renderExploreCard(obj, conj) {
  const card = document.getElementById('selectionCard');
  card.classList.add('exploring');

  const vel = obj.state?.velocity_km_s;
  const speed = vel ? Math.hypot(vel.x, vel.y, vel.z) : null;
  const frame = obj.state?.reference_frame ?? 'TEME';

  let html = `
    <div class="sc-divider">
      <div class="sc-explore-head">
        <button class="sc-back-link" id="scBackLink">${icons.arrowSide} GLOBAL VIEW</button>
        <span class="sc-analysis-tag">Orbital Analysis</span>
      </div>
      <div class="sc-grid">
        <div class="sc-field"><label>Velocity</label><span>${speed !== null ? speed.toFixed(2) + ' km/s' : '—'}</span></div>
        <div class="sc-field"><label>Reference Frame</label><span>${escapeHtml(frame)}</span></div>
      </div>
    </div>`;

  if (conj) {
    const otherId = obj.catalog_id === conj.primary_object ? conj.secondary_object : conj.primary_object;
    const otherName = obj.catalog_id === conj.primary_object ? conj.secondary_object_name : conj.primary_object_name;
    const mins = (new Date(conj.tca) - Date.now()) / 60000;
    html += `
      <div class="sc-conj-block">
        <div class="sc-conj-title">${icons.alertTriangle} Conjunction Detected</div>
        <div class="sc-conj-grid">
          <div><span class="k">With</span><span class="v">${escapeHtml(otherName || otherId)}</span></div>
          <div><span class="k">TCA${mins > 0 ? '' : ' (elapsed)'}</span><span class="v">${fmtCountdown(mins)}</span></div>
          <div><span class="k">Min Separation</span><span class="v">${fmtKm(conj.closest_approach.distance_km)}</span></div>
          <div><span class="k">Rel. Velocity</span><span class="v">${fmtRelVel(conj.closest_approach.relative_velocity_km_s)}</span></div>
        </div>
        <button class="btn btn-primary btn-sm sc-goto-pipeline" id="scGotoPipeline">RISK ANALYSIS ${icons.chevronRight}</button>
      </div>`;
    document.getElementById('tlCaption').textContent = mins > 0
      ? `EXPLORE — ${obj.name} × ${otherName || otherId} — approaching TCA`
      : `EXPLORE — ${obj.name} × ${otherName || otherId} — TCA elapsed, showing screened geometry`;
  } else {
    html += `<div class="sc-conj-block none"><div class="sc-conj-title">${icons.check} No active conjunctions detected</div></div>`;
    document.getElementById('tlCaption').textContent = `EXPLORE — ${obj.name} trajectory playback`;
  }

  document.getElementById('scExplore').innerHTML = html;
  document.getElementById('scExplore').classList.remove('hidden');
}

function renderTlMarkers() {
  const track = document.getElementById('tlTrack');
  track.querySelectorAll('.tl-marker').forEach((m) => m.remove());
  const conj = state.conjunctions.find((c) => c.conjunction_id === state.activeConjunctionId);
  if (!conj) return;
  const mins = (new Date(conj.tca) - Date.now()) / 60000;
  if (mins < 0 || mins > 90) return;
  const marker = document.createElement('div');
  marker.className = 'tl-marker';
  marker.style.left = `${(mins / 90) * 100}%`;
  marker.title = `TCA · ${conj.conjunction_id}`;
  track.appendChild(marker);
}

// ---------------------------------------------------------- timeline HUD --

function wireTimelineControls() {
  document.getElementById('tlResetBtn').innerHTML = icons.rewind;
  document.getElementById('camResetBtn').innerHTML = icons.reset;
  updatePlayIcon();

  const track = document.getElementById('tlTrack');
  const handle = document.getElementById('tlHandle');

  const minutesFromEvent = (e) => {
    const rect = track.getBoundingClientRect();
    return clamp((e.clientX - rect.left) / rect.width, 0, 1) * 90;
  };

  let draggingHandle = false;
  handle.addEventListener('pointerdown', (e) => {
    draggingHandle = true;
    handle.setPointerCapture(e.pointerId);
    state.playing = false; updatePlayIcon();
  });
  handle.addEventListener('pointermove', (e) => { if (draggingHandle) setSimMinutes(minutesFromEvent(e)); });
  handle.addEventListener('pointerup', () => { draggingHandle = false; });
  track.addEventListener('pointerdown', (e) => { if (e.target !== handle) setSimMinutes(minutesFromEvent(e)); });

  document.getElementById('tlPlayBtn').addEventListener('click', () => {
    state.playing = !state.playing;
    updatePlayIcon();
  });
  document.getElementById('tlResetBtn').addEventListener('click', () => {
    setSimMinutes(0); state.playing = false; updatePlayIcon();
  });
  document.getElementById('tlSpeed').addEventListener('click', () => {
    const speeds = [1, 2, 5, 10];
    const idx = speeds.indexOf(state.playSpeed);
    state.playSpeed = speeds[(idx + 1) % speeds.length];
    document.getElementById('tlSpeed').textContent = `${state.playSpeed}×`;
  });
  document.getElementById('camResetBtn').addEventListener('click', () => globe.resetCamera());

  document.getElementById('modeInertial').addEventListener('click', () => setCameraMode(true));
  document.getElementById('modeFixed').addEventListener('click', () => setCameraMode(false));

  document.getElementById('toggleClouds').addEventListener('click', (e) => {
    const visible = !e.currentTarget.classList.contains('active');
    e.currentTarget.classList.toggle('active', visible);
    globe.setCloudsVisible(visible);
  });

  setSimMinutes(0);

  let lastTick = performance.now();
  function loop(now) {
    requestAnimationFrame(loop);
    // Cap the step: a backgrounded tab (or any long hitch) hands back a dt of
    // seconds, which at 10x would advance the clock by half an hour in one
    // frame and teleport the playhead.
    const dt = Math.min((now - lastTick) / 1000, 0.1);
    lastTick = now;
    if (state.playing) {
      let next = state.simMinutes + dt * state.playSpeed * 3;
      // Never step over an armed TCA. Without this the playhead can clear the
      // encounter and wrap to 0 in the same frame, so the check below -- which
      // only ever sees the post-wrap value -- never fires and the approach
      // loops forever without stopping at closest approach.
      if (tcaWatch && state.simMinutes <= tcaWatch.tcaMinutes && next > tcaWatch.tcaMinutes) {
        next = tcaWatch.tcaMinutes;
      }
      if (next >= 90) next = 0;
      setSimMinutes(next);
    }
    if (tcaWatch && state.simMinutes >= tcaWatch.tcaMinutes) {
      state.playing = false;
      updatePlayIcon();
      pushLog(`TCA reached for ${tcaWatch.conj.conjunction_id} — min separation ${fmtKm(tcaWatch.conj.closest_approach.distance_km)}.`, 'warn');
      tcaWatch = null;
    }
  }
  requestAnimationFrame(loop);
}

function setCameraMode(inertial) {
  globe.setAutoRotate(inertial);
  document.getElementById('modeInertial').classList.toggle('active', inertial);
  document.getElementById('modeFixed').classList.toggle('active', !inertial);
}

function setSimMinutes(m) {
  state.simMinutes = clamp(m, 0, 90);
  globe.setSimMinutes(state.simMinutes);
  const pct = (state.simMinutes / 90) * 100;
  document.getElementById('tlFill').style.width = `${pct}%`;
  document.getElementById('tlHandle').style.left = `${pct}%`;
  const mm = Math.floor(state.simMinutes);
  const ss = Math.round((state.simMinutes - mm) * 60);
  document.getElementById('tlTime').textContent = `T+${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

function updatePlayIcon() {
  document.getElementById('tlPlayBtn').innerHTML = state.playing ? icons.pause : icons.play;
}

// -------------------------------------------------------------- actions --

async function handleSyncCatalog(group) {
  toast('SYNCING CATALOG', `Ingesting "${group}" group…`);
  pushLog(`Requesting ingest (group=${group})…`);
  try {
    const res = await api.runIngest(group);
    pushLog(`Ingested ${res.catalog_ids?.length ?? 0} objects from ${res.source}.`, 'ok');
    await loadObjects();
  } catch (err) {
    console.error(err);
    toast('SYNC FAILED', 'Tracking service unreachable at :8000.', 'crit');
    pushLog('Catalog sync failed — tracking service unreachable.', 'crit');
  }
}

async function handleRunScreen() {
  toast('RUNNING SCREENING', 'Two-stage SGP4 screening across the tracked catalog…');
  try {
    await api.runScreen(90, 50.0);
    await loadConjunctions();
    toast('SCREENING COMPLETE', `${state.conjunctions.length} candidate(s) flagged.`);
  } catch (err) {
    console.error(err);
    toast('SCREENING FAILED', 'Tracking service unreachable at :8000.', 'crit');
  }
}

function handleOpenConjunction(conjId) {
  setView('pipeline');
  openConjunction(conjId, true);
}

async function handleInjectOnTarget(targetCatalogId) {
  const targetObj = findByCatalogId(targetCatalogId);
  const targetName = targetObj?.name || targetCatalogId;

  const topBtn = document.getElementById('demoBtn');
  const cardBtn = document.getElementById('scInjectBtn');
  if (topBtn) topBtn.disabled = true;
  if (cardBtn) {
    cardBtn.disabled = true;
    cardBtn.innerHTML = `<span class="spin" style="width:11px;height:11px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;display:inline-block"></span> INJECTING…`;
  }

  toast('INJECTING SYNTHETIC DEBRIS', `Simulating physical orbital encounter with ${targetName}…`, 'warn');
  pushLog(`Demo trigger: injecting synthetic debris targeting ${targetName} (${targetCatalogId})…`, 'warn');

  try {
    const result = await api.injectSyntheticDebris(targetCatalogId);
    pushLog(`Synthetic object ${result.synthetic_object_id} injected targeting ${targetName} — encounter detected.`, 'ok');

    // Refresh both objects (to render new synthetic debris) and all accumulated conjunctions
    await Promise.all([loadObjects(), loadConjunctions()]);

    const newConj = result.conjunction_candidates?.[0]
      || state.conjunctions.find((c) => c.secondary_object === result.synthetic_object_id);

    if (newConj) {
      const dist = newConj.closest_approach?.distance_km != null ? `${newConj.closest_approach.distance_km.toFixed(1)} km` : '';
      toast('CONJUNCTION DETECTED', `${targetName} × ${newConj.secondary_object_name || newConj.secondary_object} (${dist})`, 'crit');

      if (state.explore.active) {
        handleExitExplore();
      }

      // Automatically switch to the PIPELINE tab and run/display the pipeline for this conjunction
      setTimeout(() => {
        setView('pipeline');
        openConjunction(newConj.conjunction_id, true);
      }, 600);
    } else {
      toast('SYNTHETIC DEBRIS INJECTED', 'Check the Conjunctions tab.', 'warn');
    }
  } catch (err) {
    console.error(err);
    toast('DEMO INJECTION FAILED', 'Tracking service unreachable at :8000. Start the backend and retry.', 'crit');
    pushLog('Demo injection failed — tracking service unreachable.', 'crit');
  } finally {
    if (topBtn) topBtn.disabled = false;
    if (cardBtn) {
      cardBtn.disabled = false;
      cardBtn.innerHTML = `<span id="scInjectIcon">${icons.alertTriangle}</span> + INJECT DEBRIS`;
    }
  }
}

async function handleDemoInject() {
  // If a satellite is currently selected, inject debris targeting that satellite!
  // Otherwise, default to ISS 25544.
  const targetId = singleSelectedId || '25544';
  await handleInjectOnTarget(targetId);
}

async function handleApprove(conj, maneuver) {
  try {
    const primaryTraj = await fetchTrajectoryCached(conj.primary_object);
    const points = primaryTraj.trajectory;
    const tcaIdx = points.findIndex((p) => new Date(p.timestamp) >= new Date(conj.tca));
    const splitIdx = Math.max(1, Math.floor(state.simMinutes));
    const rampEndIdx = Math.max(splitIdx + 1, tcaIdx >= 0 ? tcaIdx : points.length - 1);

    const shifted = points.map((p, i) => {
      if (i <= splitIdx) return p;
      const growth = i >= rampEndIdx ? 1 : (i - splitIdx) / (rampEndIdx - splitIdx);
      const offset = maneuver.new_separation_km * growth;
      return { timestamp: p.timestamp, x: p.x + offset * 0.6, y: p.y + offset * 0.6, z: p.z + offset * 0.3, altitude_km: p.altitude_km };
    });

    clearPostManeuverGhost();
    postManeuverGhostKey = `${conj.primary_object}::post-maneuver`;
    globe.focusTrajectory(postManeuverGhostKey, shifted, POST_MANEUVER_COLOR);
    globe.setSimMinutes(state.simMinutes);
  } catch (err) {
    console.warn('post-maneuver visualization skipped', err);
  }
}

main().catch((err) => {
  console.error(err);
  pushLog(`Fatal initialization error: ${err.message}`, 'crit');
});
