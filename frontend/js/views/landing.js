// Landing page: the site opens on the live 3D globe and tells its story in four short chapters while the camera
// travels with the scroll. Everything shown is real (/api/landing, /api/provenance, /api/visual). The long-form
// data sections (altitude spectrum, data flow, fragmentation) live on the Insights page (#/insights).
import { get } from '../api.js';
import { ACCENT, CatalogCloud, createViewer, INDIA, loadCesium, orbitRing, RISK_COLORS, ringCanvas, sampledOrbit,
  subsolarPoint } from '../globe-core.js';
import { bootLog, mountHud } from '../hud.js';
import { createGovernor } from '../perf.js';
import { bothClock } from '../time.js';
import { esc, fmt } from '../ui.js';

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const TRAIL_S = 600;     // the glowing tail behind a featured satellite, in simulated seconds
const clamp = (x, a, b) => Math.min(Math.max(x, a), b);
const lerp = (a, b, f) => a + (b - a) * f;
const ease = (x) => x * x * (3 - 2 * x);
const rad = (d) => (d * Math.PI) / 180;

// The hero shows the Earth with India in view and the day/night boundary across it, whatever the hour:
// centred on India, shifted (by at most 30 degrees) so the evening terminator sits right of centre.
function heroLon(now = new Date()) {
  const want = subsolarPoint(now).lon + 62;
  const d = ((want - INDIA.lon + 540) % 360) - 180;
  return INDIA.lon + clamp(d, -30, 30);
}

export async function render(root, { app }) {
  root.innerHTML = template();
  const $ = (s) => root.querySelector(s);
  const timers = [];
  const cleanups = [];

  const clock = () => { $('#kClock').textContent = bothClock(); };
  clock();
  timers.push(setInterval(clock, 1000));

  // The transparent top bar turns solid once the hero has scrolled away.
  const onScroll = () => document.body.classList.toggle('scrolled', window.scrollY > window.innerHeight * 0.6);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  cleanups.push(() => { window.removeEventListener('scroll', onScroll); document.body.classList.remove('scrolled'); });

  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
  }), { rootMargin: '0px 0px -10% 0px' });
  root.querySelectorAll('.reveal').forEach((el) => io.observe(el));
  cleanups.push(() => io.disconnect());

  let data;
  try {
    data = await get('/landing');
  } catch (err) {
    $('#hCount').textContent = '—';
    $('#hSrc').textContent = err.message;
    return () => { timers.forEach(clearInterval); cleanups.forEach((f) => f()); };
  }
  if (!root.isConnected) return undefined;
  fill(root, data, app);

  // A log of the real loading steps (each line turns on when that step finishes), once per browser session.
  let boot = null;
  try { if (!REDUCED && !sessionStorage.getItem('ow.boot')) { sessionStorage.setItem('ow.boot', '1'); boot = true; } } catch { boot = !REDUCED; }
  boot = boot ? bootLog(root, [['elements', 'Element sets'], ['engine', 'Globe engine'], ['cloud', 'Catalogue'], ['tiles', 'Imagery'], ['screen', 'Screening']]) : null;
  boot?.done('elements', `${fmt.int(data.counts.with_current_orbit)} objects`);
  boot?.done('screen', `${fmt.int(Object.values(data.week_by_risk).reduce((a, b) => a + b, 0))} this week · ${fmt.num(data.screening_threshold_km || 10, 0)} km`);

  // ---- the globe ------------------------------------------------------
  let viewer;
  let cloud;
  try {
    const C = await loadCesium();
    if (!root.isConnected) return undefined;
    boot?.done('engine', `CesiumJS ${C.VERSION}`);
    viewer = createViewer(C, $('#lpGlobe'), { interactive: false, light: true, creditContainer: root.querySelector('#lpCredits') });
    // the Earth's imagery arrives tile by tile: the log is finished only when the picture is
    const offTiles = viewer.scene.globe.tileLoadProgressEvent.addEventListener((n) => {
      if (n === 0 && viewer.scene.globe.tilesLoaded) { boot?.done('tiles', 'NASA GIBS'); offTiles(); }
    });
    const stops = [...root.querySelectorAll('[data-stop]')];
    const hud = mountHud(root, { labels: ['Live', 'Track', 'Screen', 'Decide', 'Start'], objects: data.counts.with_current_orbit,
      next: data.upcoming?.[0] || null,
      onJump: (i) => (i === 0 ? window.scrollTo({ top: 0, behavior: 'smooth' }) : stops[i]?.scrollIntoView({ behavior: 'smooth', block: 'center' })) });
    timers.push(setInterval(hud.tick, 1000));
    const g = startGlobe(C, viewer, root, data, { onPose: hud.update });
    g.cloud.onLoad = (c) => boot?.done('cloud', `${fmt.int(c.order.length)} points`);
    cloud = g.cloud;
    // ?debug in the page URL exposes the viewer for automated checks (frames can then be rendered and timed by hand)
    if (new URLSearchParams(location.search).has('debug')) window.__owLanding = { C, viewer, cloud, state: g.state };
    timers.push(...g.timers);
    cleanups.push(g.stop);
    // Stop drawing while the closing footer covers the whole screen.
    const vis = new IntersectionObserver(([e]) => { viewer.useDefaultRenderLoop = !(e.isIntersecting && e.intersectionRatio > 0.98); },
      { threshold: [0, 0.98, 1] });
    vis.observe($('.lp-footer'));
    cleanups.push(() => vis.disconnect());
    // the instruments step aside while the footer is on screen
    const hudVis = new IntersectionObserver(([e]) => hud.hide(e.isIntersecting && e.intersectionRatio > 0.1), { threshold: [0, 0.1, 0.2] });
    hudVis.observe($('.lp-footer'));
    cleanups.push(() => hudVis.disconnect());
  } catch (err) {
    $('#hSrc').textContent = err.message;
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
  <div class="lp-credits" id="lpCredits"></div>
  <div class="lp-shade" aria-hidden="true"></div>

  <section class="lp-stop lp-hero" data-stop aria-label="OrbitWatch">
    <div class="lp-copy">
      <div class="lp-kicker"><span class="lp-live">Live</span><span id="kClock">--:--:-- IST · --:--:-- UTC</span></div>
      <h1 class="lp-title"><span class="lp-count" id="hCount">——</span><span class="lp-unit">objects in orbit,<br>tracked live.</span></h1>
      <p class="lp-lead">Satellites, rocket bodies and debris, propagated from public orbital data and screened for close approaches.</p>
      <div class="lp-ctas">
        <a class="btn primary lg" href="#/globe">Open the live globe <span class="arrow">→</span></a>
        <a class="btn lg ghost-line" href="#/catalog">Search the catalogue</a>
      </div>
    </div>
    <div class="lp-rail"><span id="hSrc">Orbits from CelesTrak and Space-Track</span><span class="lp-scroll">Scroll</span></div>
  </section>

  <section class="lp-stop lp-chap" data-stop id="chTrack"><div class="inner reveal">
    <div class="no">01 · Track</div>
    <h2>Every object, propagated live.</h2>
    <p>Public element sets become positions with SGP4. Each orbit carries its source and its age, so a number on this site is never
      older than it says.</p>
    <div class="lp-facts" id="factsTrack"></div>
    <a class="lp-link" href="#/insights">How it works <span>→</span></a>
  </div></section>

  <section class="lp-stop lp-chap" data-stop id="chScreen"><div class="inner reveal">
    <div class="no">02 · Screen</div>
    <h2>Close approaches, found before they happen.</h2>
    <p>The ISS and India's satellites are screened against everything else in orbit for the next 24 hours. Every pass under the
      threshold becomes an event, with its time, distance and probability of collision.</p>
    <div class="lp-facts" id="factsScreen"></div>
    <a class="lp-link" href="#/conjunctions">Open close approaches <span>→</span></a>
  </div></section>

  <section class="lp-stop lp-chap" data-stop id="chDecide"><div class="inner reveal">
    <div class="no">03 · Decide</div>
    <h2>A recommendation you can check.</h2>
    <p>An AI agent calls deterministic physics tools and explains what it found. An analyst approves or rejects the burn, and the
      result is replayed on the globe. OrbitWatch has no command uplink: nothing is ever sent to a spacecraft.</p>
    <a class="lp-link" href="#/demo">Try the demo lab <span>→</span></a>
  </div></section>

  <section class="lp-stop lp-chap lp-final" data-stop id="chStart"><div class="inner reveal">
    <div class="no">04 · Start</div>
    <h2>Pick a satellite. Hear when something gets close.</h2>
    <div class="lp-ctas" id="finalCtas"></div>
  </div></section>

  <footer class="lp-footer" id="footer"></footer>`;
}

// ---------------------------------------------------------------------------
function countUp(el, to, ms = 1400) {
  if (REDUCED) { el.textContent = fmt.int(to); return; }
  const t0 = performance.now();
  const step = (now) => {
    const f = clamp((now - t0) / ms, 0, 1);
    el.textContent = fmt.int(Math.round(to * (1 - (1 - f) ** 3)));
    if (f < 1 && el.isConnected) requestAnimationFrame(step);
    else if (el.isConnected) { el.style.setProperty('--w', `${el.offsetWidth}px`); el.classList.add('done'); }
  };
  requestAnimationFrame(step);
}

function fill(root, data, app) {
  const $ = (s) => root.querySelector(s);
  const c = data.counts;
  countUp($('#hCount'), c.with_current_orbit);
  get('/provenance').then(({ coverage: cov, sources }) => {
    const live = sources.filter((x) => ['celestrak_gp', 'spacetrack_gp'].includes(x.source_key) && x.last_success_at);
    const newest = live.map((x) => x.last_success_at).sort().pop();
    const names = live.map((x) => (x.provider.startsWith('18th') ? 'Space-Track' : x.provider)).join(' + ') || 'CelesTrak';
    $('#hCount').textContent = fmt.int(cov.with_orbit);
    $('#hSrc').textContent = `${fmt.int(cov.with_orbit)} of ${fmt.int(cov.objects_in_orbit)} catalogued objects in orbit · ${names}${newest ? ` · ${fmt.rel(newest)}` : ''}`;
    $('#factsTrack').innerHTML = [
      [`${fmt.num(cov.coverage_pct, 1)}%`, 'of objects in orbit have a current orbit'],
      [`${fmt.num(data.ingest_interval_hours || 4, 0)} h`, 'between orbit refreshes'],
    ].map(([v, k]) => `<div><b>${v}</b><span>${k}</span></div>`).join('');
  }).catch(() => {
    $('#factsTrack').innerHTML = `<div><b>${fmt.int(c.with_current_orbit)}</b><span>objects with a current orbit</span></div>`;
  });
  const week = Object.values(data.week_by_risk).reduce((a, b) => a + b, 0);
  $('#factsScreen').innerHTML = [
    [fmt.int(week), 'close approaches predicted this week'],
    [`${fmt.num(data.screening_threshold_km || 10, 0)} km`, 'screening threshold'],
  ].map(([v, k]) => `<div><b>${v}</b><span>${k}</span></div>`).join('');
  $('#finalCtas').innerHTML = app.user
    ? '<a class="btn primary lg" href="#/alerts">Go to my alerts <span class="arrow">→</span></a><a class="btn lg ghost-line" href="#/globe">Open the globe</a>'
    : '<a class="btn primary lg" href="#/register">Create an account <span class="arrow">→</span></a><a class="btn lg ghost-line" href="#/globe">Open the globe</a>';
  $('#footer').innerHTML = `<div class="cols">
      <div><h5>OrbitWatch</h5><p>Satellite and space-debris tracking with close-approach alerts. A Database Management Systems
        (CD252IA) project, Department of Information Science and Engineering, RV College of Engineering.</p></div>
      <div><h5>Explore</h5><ul><li><a href="#/globe">3D globe</a></li><li><a href="#/catalog">Catalogue</a></li>
        <li><a href="#/conjunctions">Close approaches</a></li><li><a href="#/dashboard">Dashboard</a></li><li><a href="#/insights">How it works</a></li></ul></div>
      <div><h5>Data</h5><ul><li>CelesTrak element sets &amp; SATCAT</li><li>Space-Track.org (18th SDS)</li><li>GCAT — J. McDowell (CC-BY 4.0)</li>
        <li>NOAA SWPC · NASA GIBS · CesiumJS</li></ul></div>
      <div><h5>Team</h5><ul><li>Madhur Rishi Sikarwar · 1RV24IS067</li><li>Mayur M Deekshith · 1RV24IS069</li></ul></div>
    </div>
    <div class="base"><span>Positions are SGP4 predictions from public element sets: screening-grade, not operational.</span>
      <span>Data as of ${data.last_update.ingest ? esc(fmt.dt(data.last_update.ingest)) : '—'}</span></div>`;
}

// ---------------------------------------------------------------------------
// The globe and the scroll-driven camera.
function startGlobe(C, viewer, root, data, { onPose } = {}) {
  const timers = [];
  viewer.clock.currentTime = C.JulianDate.now();
  viewer.clock.multiplier = REDUCED ? 1 : 30;
  viewer.clock.shouldAnimate = true;
  const wide = window.innerWidth > 1100;
  const portrait = window.innerHeight > window.innerWidth * 1.1;

  // thin, translucent points: the Earth stays visible through the catalogue
  // the points drift slowly here: updating 32,000 of them 20 times a second (not 60) is invisible and far cheaper
  const cloud = new CatalogCloud(C, viewer, { span: 240, pixelSize: 1.5, minIntervalMs: 50 });
  cloud.colors = cloud.colors.map((col) => col.withAlpha(0.62));

  // Pulsing rings where upcoming high-risk encounters will happen (position at TCA).
  const ring = ringCanvas('#ffffff');
  data.hot.slice(0, 8).forEach((e, i) => {
    if (e.tca_lat == null) return;
    const color = C.Color.fromCssColorString(RISK_COLORS[e.risk_level]);
    const phase = i / 8;
    const cycle = () => ((performance.now() / 2100) + phase) % 1;
    viewer.entities.add({
      position: C.Cartesian3.fromDegrees(e.tca_lon, e.tca_lat, e.tca_alt_km * 1000),
      point: { pixelSize: 3.5, color },
      billboard: { image: ring, color: new C.CallbackProperty(() => color.withAlpha(REDUCED ? 0.8 : 1 - cycle()), false),
        scale: new C.CallbackProperty(() => (REDUCED ? 0.4 : 0.16 + cycle() * 0.7), false) },
    });
  });

  // Featured orbits (ISS and one watched satellite): the closed orbit, with the satellite riding it.
  const accent = C.Color.fromCssColorString(ACCENT);
  const featured = [];
  const draw = async (norad, k) => {
    try {
      const o = await sampledOrbit(C, norad, C.JulianDate.addSeconds(viewer.clock.currentTime, -TRAIL_S, new C.JulianDate()), 2);
      if (viewer.isDestroyed()) return;
      if (featured[k]) viewer.entities.remove(featured[k].entity);
      const entity = viewer.entities.add({
        position: o.prop,
        point: { pixelSize: k === 0 ? 6 : 4.5, color: C.Color.WHITE, outlineColor: accent.withAlpha(0.7), outlineWidth: 2 },
        polyline: o.teme ? { positions: orbitRing(C, o.teme), width: k === 0 ? 1.6 : 1.2, arcType: C.ArcType.NONE,
          material: accent.withAlpha(k === 0 ? 0.7 : 0.38) } : undefined,
        // a short glowing tail behind the satellite: the direction and the speed of travel
        path: REDUCED ? undefined : { leadTime: 0, trailTime: TRAIL_S, resolution: 10, width: k === 0 ? 3.4 : 2.6,
          material: new C.PolylineGlowMaterialProperty({ glowPower: 0.2, taperPower: 0.5, color: accent.withAlpha(0.95) }) },
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

  // ---- a weak GPU must not make the page's own scrolling feel slow ----------------------------
  // If frames are slow for several seconds in a row, draw a cheaper picture: lower resolution first, then a slower
  // refresh of the catalogue, then fewer imagery tiles, and finally no catalogue points.
  const governor = createGovernor({ steps: 4, onStep: (level) => {
    viewer.resolutionScale = [1, 0.85, 0.7, 0.6, 0.6][level];
    if (level >= 2) cloud.minIntervalMs = 120;
    if (level >= 3) viewer.scene.globe.maximumScreenSpaceError = 5;
    if (level >= 4) cloud.points.show = false;
  } });
  const born = performance.now();
  let prevFrame = born;
  const removeGovernor = viewer.scene.postRender.addEventListener(() => {
    const now = performance.now();
    if (now - born > 6000 && !document.hidden) governor.frame(now - prevFrame);
    prevFrame = now;
  });
  const onVisible = () => { prevFrame = performance.now(); governor.reset(); };
  document.addEventListener('visibilitychange', onVisible);
  if (new URLSearchParams(location.search).has('debug')) window.__owGovernor = governor;

  // ---- camera: one pose per chapter, blended by scroll position --------------------
  const stops = [...root.querySelectorAll('[data-stop]')];
  // Chapter 2 looks at where the highest-risk approaches will happen. Polar orbits cross near the poles, so they
  // cluster there: look straight down at the busier pole when at least three are within 35 degrees of it.
  const spots = data.hot.filter((e) => e.tca_lat != null);
  const meanLon = (list) => {
    let x = 0; let y = 0;
    list.forEach((e) => { x += Math.cos(rad(e.tca_lon)); y += Math.sin(rad(e.tca_lon)); });
    return (Math.atan2(y, x) * 180) / Math.PI;
  };
  const north = spots.filter((e) => e.tca_lat > 55);
  const south = spots.filter((e) => e.tca_lat < -55);
  const polar = Math.max(north.length, south.length) >= 3;
  const spot = (() => {
    if (polar) { const g = north.length >= south.length ? north : south; return { lon: meanLon(g), lat: g === north ? 82 : -82, polar: true }; }
    if (spots.length) return { lon: meanLon(spots), lat: spots.reduce((t, e) => t + e.tca_lat, 0) / spots.length, polar: false };
    return { lon: INDIA.lon, lat: 20, polar: false };
  })();
  const poses = [
    () => ({ lon: heroLon(), lat: 14, range: wide ? 13e6 : portrait ? 26e6 : 22e6, heading: 0, pitch: -90 }),
    () => ({ lon: INDIA.lon, lat: 12, range: wide ? 8.2e6 : portrait ? 11e6 : 12e6, heading: 0, pitch: -62 }),
    () => ({ lon: spot.lon, lat: spot.lat, range: wide ? 14e6 : portrait ? 22e6 : 18e6, heading: 0, pitch: spot.polar ? -90 : -76 }),
    () => ({ lon: INDIA.lon + 6, lat: 16, range: wide ? 12e6 : portrait ? 17e6 : 18e6, heading: -22, pitch: -68 }),
    () => ({ lon: heroLon() + 24, lat: 14, range: wide ? 17e6 : portrait ? 28e6 : 26e6, heading: 0, pitch: -90 }),
  ];
  let centres = [];
  const measure = () => { centres = stops.map((s) => s.getBoundingClientRect().top + window.scrollY + s.offsetHeight / 2); };
  measure();
  const position = () => {
    const mid = window.scrollY + window.innerHeight / 2;
    if (mid <= centres[0]) return 0;
    for (let i = 0; i < centres.length - 1; i++) {
      if (mid < centres[i + 1]) return i + (mid - centres[i]) / (centres[i + 1] - centres[i]);
    }
    return centres.length - 1;
  };
  const pose = (p) => {
    const i = Math.min(Math.floor(p), poses.length - 2);
    const f = ease(clamp(p - i, 0, 1));
    const a = poses[i]();
    const b = poses[i + 1]();
    const dl = ((b.lon - a.lon + 540) % 360) - 180;
    return { lon: a.lon + dl * f, lat: lerp(a.lat, b.lat, f), range: Math.exp(lerp(Math.log(a.range), Math.log(b.range), f)),
      heading: lerp(a.heading, b.heading, f), pitch: lerp(a.pitch, b.pitch, f) };
  };
  // the camera leans a little towards the pointer, so the globe feels like an object in front of you, not a picture
  const lean = { x: 0, y: 0, tx: 0, ty: 0 };
  const onPointer = (e) => { lean.tx = (e.clientX / window.innerWidth - 0.5) * 2; lean.ty = (e.clientY / window.innerHeight - 0.5) * 2; };
  const leaning = !REDUCED && matchMedia('(pointer: fine)').matches;
  if (leaning) window.addEventListener('pointermove', onPointer, { passive: true });
  let lastPose = 0;
  let target = position();
  let smooth = target;
  const onScroll = () => { target = position(); };
  const onResize = () => { measure(); onScroll(); };
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onResize);
  // fonts and the last data arrive after the first measurement and move the sections a little
  if (document.fonts?.ready) document.fonts.ready.then(onResize);
  const settle = setTimeout(onResize, 1500);
  let last = performance.now();
  const remove = viewer.scene.preRender.addEventListener(() => {
    const now = performance.now();
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    smooth += (target - smooth) * (1 - Math.exp(-dt * 9));          // follows the scroll within a fraction of a second
    const k = pose(smooth);
    const drift = REDUCED ? 0 : (now / 1000) * 0.18;           // the Earth turns slowly under the camera
    viewer.camera.lookAt(C.Cartesian3.fromDegrees(k.lon - drift, k.lat, 0),
      new C.HeadingPitchRange(rad(k.heading), rad(k.pitch), k.range));
    viewer.camera.lookAtTransform(C.Matrix4.IDENTITY);
    if (wide) viewer.camera.lookLeft(0.15);                    // Earth to the right of the copy
    else if (portrait) viewer.camera.lookDown(0.2);            // Earth above the copy
    if (leaning) {
      lean.x += (lean.tx - lean.x) * (1 - Math.exp(-dt * 4));
      lean.y += (lean.ty - lean.y) * (1 - Math.exp(-dt * 4));
      viewer.camera.lookRight(rad(lean.x * 0.9));
      viewer.camera.lookUp(rad(-lean.y * 0.6));
    }
    if (onPose && now - lastPose > 120) {
      lastPose = now;
      onPose({ range: k.range, lat: k.lat, lon: k.lon - drift, progress: smooth });
    }
  });
  const stop = () => { remove(); removeGovernor(); document.removeEventListener('visibilitychange', onVisible); clearTimeout(settle); window.removeEventListener('scroll', onScroll); window.removeEventListener('resize', onResize); window.removeEventListener('pointermove', onPointer); };
  return { cloud, timers, stop, state: () => ({ target, smooth, centres, scrollY: window.scrollY, vh: window.innerHeight }) };
}
