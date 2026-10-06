// Encounter replay: two objects, their orbits and one camera that makes the motion visible.
//
// What it fixes: a replay used to be two 9-pixel dots in front of 30,000 catalogue points, seen from a fixed
// camera, at a speed where a satellite crossed the screen in minutes. Here the catalogue is hidden, each object is a
// bright, labelled marker with a comet tail riding its own drawn orbit, and the camera and the clock work together:
//
//   orbit view   the whole orbit fills the screen, fixed against the stars, so you watch the object circle the Earth
//   close-up     as the two objects approach, the camera dollies in on the pair while time slows, so a pass that lasts
//                a second at 10 km/s can be watched; afterwards it pulls back out
//
// Nothing here is invented: positions are the server's SGP4 tracks (Earth-fixed km), and for a manoeuvre the
// "after the burn" track is the Clohessy-Wiltshire simulation, labelled SIMULATED everywhere.
import { gmst, orbitRing } from './globe-core.js';
import { esc, fmt, riskBadge, simTag, synTag } from './ui.js';

const FONT = '500 12px "IBM Plex Mono", monospace';
const clamp = (x, a, b) => Math.min(Math.max(x, a), b);
const lerp = (a, b, f) => a + (b - a) * f;
const ease = (x) => x * x * (3 - 2 * x);
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a) => Math.hypot(a[0], a[1], a[2]);
const unit = (a) => { const n = len(a) || 1; return [a[0] / n, a[1] / n, a[2] / n]; };
const scale = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const addv = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const subv = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mixv = (a, b, f) => [lerp(a[0], b[0], f), lerp(a[1], b[1], f), lerp(a[2], b[2], f)];
// the component of `up` perpendicular to the viewing direction, as a unit vector
const orthoUp = (dir, up) => { const k = dot(up, dir); const u = subv(up, scale(dir, k)); return len(u) < 1e-6 ? [1, 0, 0] : unit(u); };

const hexRgb = (hex) => { const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
// A soft round glow with a bright core: what makes a 7-pixel point findable on a moving globe.
export function glowCanvas(hex, size = 96) {
  const [r, g, b] = hexRgb(hex);
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const x = c.getContext('2d');
  const m = size / 2;
  const grad = x.createRadialGradient(m, m, 0, m, m, m);
  grad.addColorStop(0, `rgba(${r},${g},${b},0.9)`);
  grad.addColorStop(0.14, `rgba(${r},${g},${b},0.6)`);
  grad.addColorStop(0.4, `rgba(${r},${g},${b},0.16)`);
  grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
  x.fillStyle = grad;
  x.fillRect(0, 0, size, size);
  return c;
}
function ringImage(hex, size = 64, width = 3) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const x = c.getContext('2d');
  x.strokeStyle = hex;
  x.lineWidth = width;
  x.beginPath();
  x.arc(size / 2, size / 2, size / 2 - width, 0, Math.PI * 2);
  x.stroke();
  return c;
}

const COLORS = { primary: '#f2f6fa', after: '#35b779', secondary: '#f0a58c', synthetic: '#e3a33b', sep: '#fab219', burn: '#8ad8ea' };

export class EncounterReplay {
  /**
   * spec: { kind: 'event' | 'burn', synthetic, risk, primary: {name, norad}, secondary: {name, norad},
   *   tca, start, end (JulianDate), tcaIso, burn: {time, iso, dv_mps, direction} | null,
   *   before, after (SampledPositionProperty; after is null without a burn), secondaryTrack,
   *   miss: {before, after}, rel_velocity, pc, ringPrimary, ringSecondary (TEME km or null), periodS,
   *   detailsHref, hideOthers(on), onClose() }
   */
  constructor(C, viewer, host, spec) {
    this.C = C;
    this.viewer = viewer;
    this.host = host;
    this.spec = spec;
    this.entities = [];
    this.cleanups = [];
    this.destroyed = false;
    this.speedMode = 'auto';
    this.viewMode = 'auto';
    this.w = 0;                                   // 0 = orbit view, 1 = close-up (smoothed)
    this.pulseAt = -1e9;
    this.prevBurnDt = null;
    this.sepKm = null;
    this.wide = window.innerWidth > 900;
    const P = () => new C.Cartesian3();
    this.s = { a: P(), b: P(), a2: P(), g: P() };
    this.base = clamp((spec.periodS || 5700) / 16, 150, 500);
    this.entry = { t0: performance.now(), pos: null, dir: null, up: null };
    this.lastUi = 0;
    this.dragging = false;
    this.prepareOrbitGeometry();
    this.buildEntities();
    this.buildUi();
    this.start();
  }

  // ---- geometry shared by the camera ------------------------------------------------------
  posActual(time, out) {
    const { C, spec } = this;
    const useAfter = spec.after && spec.burn && C.JulianDate.greaterThanOrEquals(time, spec.burn.time);
    return (useAfter ? spec.after : spec.before).getValue(time, out);
  }

  // The orbit viewed from above its plane, tilted toward the encounter, in the inertial frame (TEME): the
  // direction is fixed against the stars, and rotated into the Earth-fixed frame every frame.
  prepareOrbitGeometry() {
    const { C, spec } = this;
    const eci = (p, time) => {
      const th = gmst(C.JulianDate.toDate(time));
      const c = Math.cos(th); const s = Math.sin(th);
      return [c * p.x - s * p.y, s * p.x + c * p.y, p.z];
    };
    const t1 = C.JulianDate.addSeconds(spec.tca, -30, new C.JulianDate());
    const t2 = C.JulianDate.addSeconds(spec.tca, 30, new C.JulianDate());
    const p1 = spec.before.getValue(t1);
    const p2 = spec.before.getValue(t2);
    const pc = spec.before.getValue(spec.tca);
    if (!p1 || !p2 || !pc) { this.dTeme = unit([0.3, 0.3, 1]); this.radius = 7.0e6; return; }
    let n = unit(cross(eci(p1, t1), eci(p2, t2)));
    if (n[2] < 0) n = scale(n, -1);                      // look from the north side of the orbit plane
    const rTca = unit(eci(pc, spec.tca));
    // Almost face-on to the orbit (about 6 degrees off its axis). Perspective shrinks the far side of a low orbit
    // into the Earth's disc as soon as the tilt passes ~8 degrees, and the object would hide behind the planet
    // for half of every lap; face-on, the ring stays outside the disc and the object is always in view.
    this.dTeme = unit(addv(n, scale(rTca, 0.1)));
    this.radius = Math.hypot(pc.x, pc.y, pc.z);          // orbit radius, metres (the orbit is close to circular)
  }

  fitRange() {
    const f = this.viewer.camera.frustum;
    const halfV = (f.fovy || Math.PI / 3) / 2;
    const halfH = Math.atan(Math.tan(halfV) * (f.aspectRatio || 1.6));
    return (this.radius * 1.3) / Math.tan(Math.min(halfV, halfH));
  }

  // ---- entities -------------------------------------------------------------------------------
  buildEntities() {
    const { C, viewer, spec } = this;
    const col = (hex, a = 1) => C.Color.fromCssColorString(hex).withAlpha(a);
    const add = (e) => { const en = viewer.entities.add(e); this.entities.push(en); return en; };
    const bg = col('#030405', 0.8);
    const secHex = spec.synthetic ? COLORS.synthetic : COLORS.secondary;
    const trail = Math.max((spec.periodS || 5700) * 0.12, 300);
    const glow = {};
    const glowFor = (hex) => glow[hex] || (glow[hex] = glowCanvas(hex));
    const interval = (a, b, stopIncluded = true) => new C.TimeIntervalCollection([new C.TimeInterval({ start: a, stop: b, isStopIncluded: stopIncluded })]);

    const marker = ({ id, position, hex, label, labelDy = -18, availability, trailTime = trail, hollow = false, dashed = false }) => add({
      id: `enc-${id}`, availability, position,
      billboard: { image: hollow ? ringImage(hex, 48, 2.5) : glowFor(hex), scale: hollow ? 0.55 : 0.62, color: C.Color.WHITE },
      point: hollow ? undefined : { pixelSize: 7, color: col(hex), outlineColor: col('#000000', 0.7), outlineWidth: 1.5 },
      label: label ? { text: label, font: FONT, fillColor: col(hex), showBackground: true, backgroundColor: bg, backgroundPadding: new C.Cartesian2(6, 4),
        pixelOffset: new C.Cartesian2(16, labelDy), horizontalOrigin: C.HorizontalOrigin.LEFT, scale: 0.95 } : undefined,
      path: trailTime ? { leadTime: 0, trailTime, width: dashed ? 2 : 4, resolution: 15,
        material: dashed ? new C.PolylineDashMaterialProperty({ color: col(hex, 0.55), dashLength: 10 })
          : new C.PolylineGlowMaterialProperty({ glowPower: 0.2, color: col(hex, 0.92) }) } : undefined,
    });

    // the two orbits, drawn whole: the object is always on its ring
    if (spec.ringPrimary) add({ polyline: { positions: orbitRing(C, spec.ringPrimary), width: 1.4, arcType: C.ArcType.NONE, material: col(COLORS.primary, 0.42) } });
    if (spec.ringSecondary) add({ polyline: { positions: orbitRing(C, spec.ringSecondary), width: 1.4, arcType: C.ArcType.NONE, material: col(secHex, 0.4) } });

    const burn = spec.burn;
    if (burn && spec.after) {
      marker({ id: 'primary', position: spec.before, hex: COLORS.primary, label: spec.primary.name, availability: interval(spec.start, burn.time, false) });
      marker({ id: 'primary-after', position: spec.after, hex: COLORS.after, label: `${spec.primary.name} · after the burn`, availability: interval(burn.time, spec.end) });
      marker({ id: 'ghost', position: spec.before, hex: COLORS.primary, label: 'without the burn', labelDy: 18, availability: interval(burn.time, spec.end),
        hollow: true, dashed: true, trailTime: trail * 0.6 });
    } else {
      marker({ id: 'primary', position: spec.before, hex: COLORS.primary, label: spec.primary.name });
    }
    marker({ id: 'secondary', position: spec.secondaryTrack, hex: secHex, label: `${spec.secondary.name}${spec.synthetic ? ' · SYNTHETIC' : ''}`, labelDy: 18 });

    // live separations, only while the pair is near enough for a line to mean something (and not through the Earth)
    const near = 2.5e6;
    const sec = (time, out) => spec.secondaryTrack.getValue(time, out);
    const sepLine = (id, a, hex, dashLength, availability) => {
      const A = new C.Cartesian3(); const B = new C.Cartesian3();
      const positions = new C.CallbackProperty((time) => {
        const p = a(time, A); const q = sec(time, B);
        return p && q && C.Cartesian3.distance(p, q) < near ? [p, q] : [];
      }, false);
      add({ id: `enc-${id}`, availability, polyline: { positions, width: 1.6, arcType: C.ArcType.NONE,
        material: new C.PolylineDashMaterialProperty({ color: col(hex, 0.9), dashLength }) } });
      const M = new C.Cartesian3();
      const mid = new C.CallbackProperty((time) => {
        const p = a(time, A); const q = sec(time, B);
        return p && q && C.Cartesian3.distance(p, q) < near ? C.Cartesian3.midpoint(p, q, M) : undefined;
      }, false);
      return mid;
    };
    const A1 = new C.Cartesian3(); const B1 = new C.Cartesian3();
    const label = (position, textFn, hex, dy, availability) => add({ availability, position, label: { text: new C.CallbackProperty(textFn, false), font: FONT,
      fillColor: col(hex), showBackground: true, backgroundColor: bg, backgroundPadding: new C.Cartesian2(6, 4), pixelOffset: new C.Cartesian2(0, dy), scale: 0.95 } });
    const km = (p, q) => fmt.num(C.Cartesian3.distance(p, q) / 1000, C.Cartesian3.distance(p, q) < 1e4 ? 3 : 2);
    const midA = sepLine('sep', (t, o) => this.posActual(t, o), COLORS.sep, 12);
    label(midA, (time) => { const p = this.posActual(time, A1); const q = sec(time, B1); return p && q ? `${km(p, q)} km` : ''; }, COLORS.sep, -22);
    if (burn && spec.after) {
      const mid2 = sepLine('sep-ghost', (t, o) => spec.before.getValue(t, o), COLORS.primary, 6, interval(burn.time, spec.end));
      label(mid2, (time) => { const p = spec.before.getValue(time, A1); const q = sec(time, B1); return p && q ? `no burn · ${km(p, q)} km` : ''; },
        COLORS.primary, 24, interval(burn.time, spec.end));
    }

    // the closest approach, where the nominal track passes the other object
    const atTca = spec.before.getValue(spec.tca);
    if (atTca) {
      add({ position: atTca, billboard: { image: ringImage(COLORS.sep, 56, 2), scale: 0.6 },
        label: { text: 'closest approach', font: FONT, fillColor: col('#cfd6df'), showBackground: true, backgroundColor: bg,
          backgroundPadding: new C.Cartesian2(6, 4), pixelOffset: new C.Cartesian2(0, 30), scale: 0.9,
          distanceDisplayCondition: new C.DistanceDisplayCondition(0, 6e6) } });
    }
    // the burn: a quiet marker, and a ring that pulses for three seconds when the clock passes it
    if (burn) {
      const atBurn = spec.before.getValue(burn.time);
      if (atBurn) {
        const pulse = () => { const k = (performance.now() - this.pulseAt) / 3000; return k >= 0 && k <= 1 ? k : -1; };
        add({ position: atBurn,
          billboard: { image: ringImage(COLORS.burn, 96, 3), scale: new C.CallbackProperty(() => { const k = pulse(); return k < 0 ? 0.22 : 0.3 + k * 2.4; }, false),
            color: new C.CallbackProperty(() => { const k = pulse(); return C.Color.WHITE.withAlpha(k < 0 ? 0.85 : 1 - k); }, false) },
          label: { text: `burn · Δv ${fmt.num(burn.dv_mps, 3)} m/s ${burn.direction || ''}`.trim(), font: FONT, fillColor: col(COLORS.burn),
            showBackground: true, backgroundColor: bg, backgroundPadding: new C.Cartesian2(6, 4), pixelOffset: new C.Cartesian2(0, -26), scale: 0.9,
            distanceDisplayCondition: new C.DistanceDisplayCondition(0, 3e7) } });
      }
    }
  }

  // ---- interface -----------------------------------------------------------------------------
  buildUi() {
    const { spec } = this;
    const burn = spec.burn;
    const span = this.C.JulianDate.secondsDifference(spec.end, spec.start);
    const at = (t) => `${clamp(this.C.JulianDate.secondsDifference(t, spec.start) / span, 0, 1) * 100}%`;
    const kind = spec.kind === 'burn' ? 'Avoidance manoeuvre' : 'Close approach';
    const tags = `${spec.kind === 'burn' ? simTag() : riskBadge(spec.risk)}${spec.synthetic ? synTag() : ''}`;
    const sw = (hex, text, hollow = false) => `<span><i class="${hollow ? 'hollow' : ''}" style="${hollow ? 'border-color' : 'background'}:${hex}"></i>${esc(text)}</span>`;
    const legend = burn
      ? `${sw(COLORS.primary, 'on its own orbit')}${sw(COLORS.after, 'after the burn (simulated)')}${sw(COLORS.primary, 'where it would be without the burn', true)}${sw(spec.synthetic ? COLORS.synthetic : COLORS.secondary, 'the other object')}`
      : `${sw(COLORS.primary, spec.primary.name)}${sw(spec.synthetic ? COLORS.synthetic : COLORS.secondary, spec.secondary.name)}`;
    const miss = burn && spec.miss.after != null
      ? `${fmt.num(spec.miss.before, 3)} → <span class="status-ok">${fmt.num(spec.miss.after, 3)} km</span>` : `${fmt.num(spec.miss.before, 3)} km`;
    this.host.classList.add('replaying');
    const el = document.createElement('div');
    el.className = 'enc';
    el.innerHTML = `
      <div class="enc-card">
        <div class="enc-kind"><span>${kind}</span>${tags}</div>
        <div class="enc-pair"><a class="a" href="#/object/${esc(spec.primary.norad)}">${esc(spec.primary.name)}</a><i>×</i><span class="b" style="color:${spec.synthetic ? COLORS.synthetic : COLORS.secondary}">${esc(spec.secondary.name)}</span></div>
        <div class="enc-sep"><span class="k">Separation</span><b id="encSep">—</b></div>
        <div class="enc-phase" id="encPhase">—</div>
        <details class="enc-more"><summary>Details</summary>
          <dl class="enc-facts">
            <dt>Closest approach</dt><dd>${esc(fmt.dt(spec.tcaIso))}</dd>
            <dt>Miss distance</dt><dd>${miss}</dd>
            ${spec.rel_velocity != null ? `<dt>Relative speed</dt><dd>${fmt.num(spec.rel_velocity, 2)} km/s</dd>` : ''}
            ${spec.pc != null ? `<dt>Collision probability</dt><dd>${Number(spec.pc).toExponential(1)}</dd>` : ''}
            ${burn ? `<dt>Burn</dt><dd>${esc(fmt.dt(burn.iso))}</dd>` : ''}
          </dl>
          <div class="enc-legend">${legend}</div>
          ${burn ? '<p class="enc-note">Simulated: OrbitWatch has no command uplink, so nothing is sent to a spacecraft. Positions are SGP4 plus a linearised model of the burn.</p>' : ''}
        </details>
        <div class="enc-actions"><button class="btn sm" data-enc="close"><span class="long">← Back to the globe</span><span class="short">← Back</span></button>${spec.detailsHref ? `<a class="btn sm ghost" href="${spec.detailsHref}">Decision support →</a>` : ''}</div>
      </div>
      <div class="enc-dock" role="group" aria-label="Replay controls">
        <button class="enc-play" id="encPlay" data-enc="play" aria-label="Pause"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="pp" d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg></button>
        <div class="enc-timeline">
          <div class="enc-marks">${burn ? `<button data-jump="burn" style="left:${at(burn.time)}">Burn</button>` : ''}<button data-jump="tca" style="left:${at(spec.tca)}">Closest</button></div>
          <input type="range" id="encScrub" min="0" max="1000" value="0" aria-label="Replay time">
        </div>
        <span class="enc-time" id="encTime"></span>
        <div class="enc-seg" id="encSpeed" role="group" aria-label="Speed">
          <button data-sp="auto" title="Fast on the orbit, slow when the pair comes close">Auto</button>
          <button data-sp="real" title="Real time">Real time</button>
          <button data-sp="fast" title="Constant fast-forward">Fast</button></div>
        <div class="enc-seg" id="encView" role="group" aria-label="Camera">
          <button data-v="auto" title="Orbit view, then close-up on the encounter">Auto</button>
          <button data-v="orbit" title="The whole orbit">Orbit</button>
          <button data-v="close" title="Follow the pair">Close-up</button>
          <button data-v="free" title="Drag to look around">Free</button></div>
      </div>`;
    this.host.appendChild(el);
    this.ui = el;
    this.el = (id) => el.querySelector(id);
    this.syncSegments();

    const on = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); this.cleanups.push(() => target.removeEventListener(type, fn, opts)); };
    on(el, 'click', (e) => {
      const b = e.target.closest('[data-enc],[data-sp],[data-v],[data-jump]');
      if (!b) return;
      if (b.dataset.enc === 'close') { this.spec.onClose(); return; }
      if (b.dataset.enc === 'play') { this.toggle(); return; }
      if (b.dataset.sp) { this.speedMode = b.dataset.sp; this.syncSegments(); return; }
      if (b.dataset.v) { this.viewMode = b.dataset.v; if (this.viewMode !== 'free') this.entry = { t0: performance.now(), ...this.captureCamera() }; this.syncSegments(); return; }
      if (b.dataset.jump) {
        const t = b.dataset.jump === 'burn' ? spec.burn.time : spec.tca;
        this.viewer.clock.currentTime = this.C.JulianDate.addSeconds(t, b.dataset.jump === 'burn' ? -40 : -300, new this.C.JulianDate());
        this.viewer.clock.shouldAnimate = true;
        if (this.viewMode === 'free') this.viewMode = 'auto';
        this.syncSegments();
      }
    });
    const scrub = this.el('#encScrub');
    on(scrub, 'pointerdown', () => { this.dragging = true; this.wasPlaying = this.viewer.clock.shouldAnimate; this.viewer.clock.shouldAnimate = false; });
    on(window, 'pointerup', () => { if (this.dragging) { this.dragging = false; this.viewer.clock.shouldAnimate = this.wasPlaying; } });
    on(scrub, 'input', () => {
      const t = this.C.JulianDate.addSeconds(spec.start, (Number(scrub.value) / 1000) * span, new this.C.JulianDate());
      this.viewer.clock.currentTime = t;
    });
    on(window, 'keydown', (e) => { if (e.key === 'Escape') this.spec.onClose(); });
    // touching the globe hands the camera to the user
    const free = () => { if (this.viewMode !== 'free') { this.viewMode = 'free'; this.syncSegments(); } };
    const canvas = this.viewer.canvas;
    on(canvas, 'pointerdown', free);
    on(canvas, 'wheel', free, { passive: true });
    on(canvas, 'touchstart', free, { passive: true });
  }

  syncSegments() {
    const set = (id, key, value) => this.el(id).querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset[key] === value)));
    set('#encSpeed', 'sp', this.speedMode);
    set('#encView', 'v', this.viewMode);
  }

  toggle() { this.viewer.clock.shouldAnimate = !this.viewer.clock.shouldAnimate; }

  captureCamera() {
    const c = this.viewer.camera;
    return { pos: [c.positionWC.x, c.positionWC.y, c.positionWC.z], dir: [c.directionWC.x, c.directionWC.y, c.directionWC.z], up: [c.upWC.x, c.upWC.y, c.upWC.z] };
  }

  // ---- run -------------------------------------------------------------------------------------
  start() {
    const { C, viewer, spec } = this;
    this.savedCloud = null;
    spec.hideOthers(true);
    const clock = viewer.clock;
    Object.assign(clock, { startTime: spec.start.clone(), stopTime: spec.end.clone(), clockRange: C.ClockRange.LOOP_STOP,
      clockStep: C.ClockStep.SYSTEM_CLOCK_MULTIPLIER, multiplier: this.base, shouldAnimate: true });
    clock.currentTime = spec.start.clone();
    this.entry = { t0: performance.now(), ...this.captureCamera() };
    this.last = performance.now();
    this.remove = viewer.scene.preRender.addEventListener(() => this.frame());
  }

  paceFor(ewS, sKm, t) {
    const { C, spec } = this;
    if (this.speedMode === 'real') return 1;
    if (this.speedMode === 'fast') return this.base;
    let v = ewS > 400 ? this.base : clamp(0.08 * sKm, 0.7, this.base);
    if (spec.burn) v = Math.min(v, 10 + 0.8 * Math.abs(C.JulianDate.secondsDifference(t, spec.burn.time)));
    return v;
  }

  frame() {
    if (this.destroyed || this.viewer.isDestroyed()) return;
    const { C, viewer, spec } = this;
    const now = performance.now();
    const dt = Math.min((now - this.last) / 1000, 0.1);
    this.last = now;
    const clock = viewer.clock;
    const t = clock.currentTime;
    const A = this.posActual(t, this.s.a);
    const B = spec.secondaryTrack.getValue(t, this.s.b);
    if (!A || !B) return;
    const sM = C.Cartesian3.distance(A, B);
    const sKm = sM / 1000;
    this.sepKm = sKm;
    const ew = Math.abs(C.JulianDate.secondsDifference(t, spec.tca));
    if (clock.shouldAnimate) clock.multiplier = this.paceFor(ew, sKm, t);

    if (spec.burn) {
      const bd = C.JulianDate.secondsDifference(t, spec.burn.time);
      if (this.prevBurnDt !== null && this.prevBurnDt < 0 && bd >= 0 && bd < 120) this.pulseAt = now;
      this.prevBurnDt = bd;
    }

    // how much of the close-up: by time to the closest approach, so a pair that is merely near elsewhere in the
    // orbit does not pull the camera in
    const target = this.viewMode === 'close' ? 1 : this.viewMode === 'orbit' ? 0 : ew <= 200 ? 1 : ew >= 560 ? 0 : 1 - ease((ew - 200) / 360);
    this.w += (target - this.w) * (1 - Math.exp(-dt * 4));
    if (this.viewMode !== 'free') this.placeCamera(t, A, B, sM);
    if (now - this.lastUi > 90) { this.lastUi = now; this.updateUi(t, sKm, ew); }
  }

  placeCamera(t, A, B, sM) {
    const { C, viewer } = this;
    const th = gmst(C.JulianDate.toDate(t));
    const c = Math.cos(th); const s = Math.sin(th);
    // orbit pose: camera on the (inertially fixed) viewing direction, looking at the Earth's centre
    const D = [c * this.dTeme[0] + s * this.dTeme[1], -s * this.dTeme[0] + c * this.dTeme[1], this.dTeme[2]];
    const Po = scale(D, this.fitRange());
    const upO = orthoUp(scale(D, -1), [0, 0, 1]);
    const a = [A.x, A.y, A.z];
    const b = [B.x, B.y, B.z];
    // close-up pose: beside and above the pair, "up" away from the Earth so the flight reads left to right
    const centre = mixv(a, scale(addv(a, b), 0.5), clamp((6.0e6 - sM) / 2.0e6, 0, 1));   // the pair's midpoint once they are near
    const A2 = this.posActual(C.JulianDate.addSeconds(t, 3, new C.JulianDate()), this.s.a2) || this.posActual(C.JulianDate.addSeconds(t, -3, new C.JulianDate()), this.s.a2);
    const vHat = A2 ? unit(subv([A2.x, A2.y, A2.z], a)) : [1, 0, 0];
    const rHat = unit(a);
    const nHat = unit(cross(rHat, vHat));
    const off = unit(addv(addv(scale(rHat, 0.6), scale(nHat, 0.55)), scale(vHat, -0.3)));
    const range = clamp(2.3 * sM, 6000, 4.5e6);
    const upC = orthoUp(scale(off, -1), rHat);

    // blend as an orbit camera: the target moves to the pair early (so it stays centred), the range shrinks
    // logarithmically (constant zoom speed) and the direction turns from the orbit view to the close view
    const w = ease(clamp(this.w, 0, 1));
    const T = mixv([0, 0, 0], centre, ease(clamp(this.w * 1.6, 0, 1)));
    const R = Math.exp(lerp(Math.log(len(Po)), Math.log(range), w));
    const D2 = unit(mixv(unit(Po), off, w));
    const P = addv(T, scale(D2, R));
    let dir = scale(D2, -1);
    let up = orthoUp(dir, mixv(upO, upC, w));
    let pos = P;
    // glide from wherever the camera was when the replay (or the view) started
    const k = ease(clamp((performance.now() - this.entry.t0) / 1400, 0, 1));
    if (k < 1 && this.entry.pos) {
      pos = mixv(this.entry.pos, P, k);
      dir = unit(mixv(this.entry.dir, dir, k));
      up = orthoUp(dir, mixv(this.entry.up, up, k));
    }
    viewer.camera.setView({ destination: new C.Cartesian3(pos[0], pos[1], pos[2]),
      orientation: { direction: new C.Cartesian3(dir[0], dir[1], dir[2]), up: new C.Cartesian3(up[0], up[1], up[2]) } });
    // keep the Earth clear of the card on the left
    if (this.wide) viewer.camera.lookLeft(0.09 * (1 - 0.6 * w));
  }

  phase(t) {
    const { C, spec } = this;
    const dtca = C.JulianDate.secondsDifference(t, spec.tca);
    if (Math.abs(dtca) < 15) return 'Closest approach';
    if (spec.burn) {
      const db = C.JulianDate.secondsDifference(t, spec.burn.time);
      if (db < 0) return db > -90 ? 'Burn in a moment' : 'Before the burn';
      if (db < 20) return 'Burn';
      return dtca < 0 ? 'After the burn, approaching' : 'Past the other object';
    }
    return dtca < 0 ? 'Approaching' : 'Separating';
  }

  updateUi(t, sKm, ew) {
    const { C, spec } = this;
    const sepEl = this.el('#encSep');
    const decimals = sKm < 10 ? 3 : sKm < 100 ? 2 : sKm < 1000 ? 1 : 0;
    sepEl.textContent = `${fmt.num(sKm, decimals)} km`;
    sepEl.style.color = sKm <= 10 ? 'var(--r-high)' : sKm <= 100 ? 'var(--amber)' : '';
    this.el('#encPhase').textContent = this.phase(t);
    const span = C.JulianDate.secondsDifference(spec.end, spec.start);
    if (!this.dragging) this.el('#encScrub').value = String(Math.round((C.JulianDate.secondsDifference(t, spec.start) / span) * 1000));
    const d = C.JulianDate.toDate(t);
    const pad = (n) => String(n).padStart(2, '0');
    const ist = new Date(d.getTime() + 19800000);
    this.el('#encTime').innerHTML = `${pad(ist.getUTCHours())}:${pad(ist.getUTCMinutes())}:${pad(ist.getUTCSeconds())} <small>IST</small>`;
    const play = this.el('#encPlay');
    const playing = this.viewer.clock.shouldAnimate;
    if (play.dataset.state !== String(playing)) {
      play.dataset.state = String(playing);
      play.setAttribute('aria-label', playing ? 'Pause' : 'Play');
      play.querySelector('.pp').setAttribute('d', playing ? 'M7 5h3.5v14H7zM13.5 5H17v14h-3.5z' : 'M8 5l11 7-11 7z');
    }
    const rate = this.viewer.clock.multiplier;
    this.el('#encSpeed').dataset.rate = playing ? `×${rate >= 10 ? Math.round(rate) : fmt.num(rate, 1)}` : 'paused';
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    const { C, viewer, spec } = this;
    try { if (this.remove) this.remove(); } catch { /* viewer gone */ }
    this.cleanups.forEach((f) => f());
    if (!viewer.isDestroyed()) {
      this.entities.forEach((en) => viewer.entities.remove(en));
      const clock = viewer.clock;
      clock.clockRange = C.ClockRange.UNBOUNDED;
      clock.multiplier = 1;
      clock.currentTime = C.JulianDate.now();
      clock.shouldAnimate = true;
    }
    spec.hideOthers(false);
    this.host.classList.remove('replaying');
    if (this.ui) this.ui.remove();
  }
}
