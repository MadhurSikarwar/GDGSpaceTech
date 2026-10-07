// SVG diagrams drawn from real numbers, as plain strings (no DOM, so they are easy to test):
//   * the close-approach radar: where each upcoming close approach falls in time (the angle, clockwise from the top: now,
//     then onwards) and how close it will be (the distance from the centre: the nearer the centre, the closer the miss);
//   * an object's orbit to scale: the Earth, the ellipse through perigee and apogee, and the regime boundaries.
import { esc, fmt } from './ui.js';

const SIZE = 420;
const C = SIZE / 2;
const R = 168;
const RISK_FILL = { CRITICAL: 'var(--r-critical)', HIGH: 'var(--r-high)', MEDIUM: 'var(--r-medium)', LOW: 'var(--r-low)' };
const RISK_ORDER = { LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3 };

// The point for an event `hours` from now that will miss by `missKm`. Distance grows with the square root of the miss, so
// that the many misses of a few kilometres do not crowd the middle.
export function radarPoint(hours, missKm, spanH = 72, maxKm = 10) {
  const theta = (Math.min(Math.max(hours, 0), spanH) / spanH) * 2 * Math.PI;
  const r = R * Math.sqrt(Math.min(Math.max(missKm, 0), maxKm) / maxKm);
  return { x: C + r * Math.sin(theta), y: C - r * Math.cos(theta), r, theta };
}

// The events still ahead, soonest first.
export function nextEvents(markers, now = Date.now(), n = 8) {
  return markers.filter((m) => Date.parse(m.tca) >= now).sort((a, b) => Date.parse(a.tca) - Date.parse(b.tca)).slice(0, n);
}

export function radarSvg(markers, { now = Date.now(), spanH = 72, maxKm = 10 } = {}) {
  const rings = [0.1, 0.2, 0.5, 1].map((f) => f * maxKm);
  const grid = rings.map((k) => `<circle cx="${C}" cy="${C}" r="${(R * Math.sqrt(k / maxKm)).toFixed(1)}"/>`).join('');
  const spokes = Array.from({ length: 12 }, (_, i) => {
    const t = (i / 12) * 2 * Math.PI;
    return `<line x1="${C}" y1="${C}" x2="${(C + R * Math.sin(t)).toFixed(1)}" y2="${(C - R * Math.cos(t)).toFixed(1)}"/>`;
  }).join('');
  const kmLabels = rings.map((k) => {
    const r = R * Math.sqrt(k / maxKm);
    const t = (135 * Math.PI) / 180;
    return `<text x="${(C + r * Math.sin(t) + 3).toFixed(1)}" y="${(C - r * Math.cos(t) + 11).toFixed(1)}">${Number(k.toFixed(1))} km</text>`;
  }).join('');
  const hourLabels = [['NOW', 0, -1, 'middle'], [`+${spanH / 4} H`, 1, 0, 'start'], [`+${spanH / 2} H`, 0, 1, 'middle'], [`+${(3 * spanH) / 4} H`, -1, 0, 'end']]
    .map(([text, dx, dy, anchor]) => `<text x="${C + dx * (R + 12)}" y="${C + dy * (R + 12) + (dy ? (dy > 0 ? 9 : -2) : 3)}" text-anchor="${anchor}">${text}</text>`).join('');
  const inSpan = markers
    .map((m) => ({ m, hours: (Date.parse(m.tca) - now) / 3600000 }))
    .filter(({ hours }) => hours >= 0 && hours <= spanH)
    .sort((a, b) => (RISK_ORDER[a.m.risk] ?? 0) - (RISK_ORDER[b.m.risk] ?? 0));
  // The many MEDIUM events are one path of little circles (one element, not hundreds: the radar stays light to draw);
  // the HIGH and CRITICAL ones are links, each with a tooltip, because those are the ones a reader will want to open.
  const medium = inSpan.filter(({ m }) => !(m.risk === 'HIGH' || m.risk === 'CRITICAL'));
  const lowDots = medium.length
    ? `<path d="${medium.map(({ m, hours }) => {
      const p = radarPoint(hours, m.miss_km, spanH, maxKm);
      return `M${(p.x - 2.4).toFixed(1)} ${p.y.toFixed(1)}a2.4 2.4 0 1 0 4.8 0a2.4 2.4 0 1 0-4.8 0`;
    }).join('')}" fill="${RISK_FILL.MEDIUM}" opacity="0.6"/>` : '';
  const dots = lowDots + inSpan
    .filter(({ m }) => m.risk === 'HIGH' || m.risk === 'CRITICAL')
    .map(({ m, hours }) => {
      const p = radarPoint(hours, m.miss_km, spanH, maxKm);
      const rad = m.risk === 'CRITICAL' ? 5.2 : 3.4;
      const fill = RISK_FILL[m.risk];
      const tip = `${m.primary.name} × ${m.secondary.name} · ${m.risk} · miss ${fmt.num(m.miss_km, 2)} km · in ${fmt.num(hours, 1)} h`;
      return `<a class="rd-dot" href="#/conjunctions?event=${m.event_id}">${m.risk === 'CRITICAL' ? `<circle class="rd-ring" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${rad}" stroke="${fill}"/>` : ''}`
        + `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${rad}" fill="${fill}" opacity="${m.risk === 'HIGH' ? 0.85 : 1}"/><title>${esc(tip)}</title></a>`;
    }).join('');
  return `<svg class="rd" viewBox="0 0 ${SIZE} ${SIZE}" role="group" aria-label="Radar of upcoming close approaches: time to closest approach clockwise from the top, miss distance from the centre">`
    + `<g class="rd-grid">${grid}${spokes}<path d="M${C - 6} ${C}h12M${C} ${C - 6}v12"/></g>${dots}<g class="rd-labels">${kmLabels}${hourLabels}</g></svg>`;
}

// ---- an object's orbit, to scale -------------------------------------------------------------------------------
const EARTH_KM = 6378.137;
const GEO_KM = 35786;
const LEO_TOP_KM = 2000;

// The ellipse through perigee (right) and apogee (left) with the Earth at its focus, scaled to fit the box.
export function orbitGeometry({ perigee_km: pe, apogee_km: ap }, W = 420, H = 230, pad = 30) {
  const rp = EARTH_KM + pe;
  const ra = EARTH_KM + ap;
  const a = (ra + rp) / 2;
  const c = (ra - rp) / 2;
  const b = Math.sqrt(ra * rp);
  const s = Math.min((W / 2 - pad) / a, (H / 2 - pad) / b);
  return { s, W, H, cx: W / 2, cy: H / 2, A: a * s, B: b * s, ex: W / 2 + c * s, earthR: EARTH_KM * s };
}

export function orbitSvg(orbit, W = 420, H = 230) {
  const g = orbitGeometry(orbit, W, H);
  const ring = (km) => (EARTH_KM + km) * g.s;
  const refs = [[LEO_TOP_KM, 'LEO ends 2,000 km'], [GEO_KM, 'GEO 35,786 km']]
    .filter(([km]) => ring(km) > g.earthR + 6 && ring(km) < 1.9 * Math.max(g.A, g.B))
    .map(([km, label]) => `<circle cx="${g.ex.toFixed(1)}" cy="${g.cy}" r="${ring(km).toFixed(1)}"/>`
      + `<text x="${(g.ex + ring(km) * 0.7071 + 4).toFixed(1)}" y="${(g.cy - ring(km) * 0.7071).toFixed(1)}">${label}</text>`).join('');
  const peX = g.cx + g.A;
  const apX = g.cx - g.A;
  return `<svg class="od" viewBox="0 0 ${W} ${H + 26}" role="img" aria-label="The orbit drawn to scale: perigee ${fmt.int(orbit.perigee_km)} km, apogee ${fmt.int(orbit.apogee_km)} km above the surface">`
    + `<defs><radialGradient id="odEarth" cx="50%" cy="42%" r="60%"><stop offset="0" stop-color="#1b4a73"/><stop offset="1" stop-color="#06121f"/></radialGradient></defs>`
    + `<g class="od-ref">${refs}</g>`
    + `<ellipse class="od-orbit" cx="${g.cx}" cy="${g.cy}" rx="${g.A.toFixed(1)}" ry="${g.B.toFixed(1)}"/>`
    + `<circle class="od-earth" cx="${g.ex.toFixed(1)}" cy="${g.cy}" r="${Math.max(g.earthR, 2).toFixed(1)}"/>`
    + `<circle class="od-pt" cx="${peX.toFixed(1)}" cy="${g.cy}" r="3.2"/><circle class="od-pt" cx="${apX.toFixed(1)}" cy="${g.cy}" r="3.2"/>`
    + `<text class="od-lab" x="2" y="12">INCLINATION ${fmt.num(orbit.inclination, 2)}°</text>`
    + `<text class="od-lab" x="${W - 2}" y="12" text-anchor="end">PERIOD ${fmt.num(orbit.period_min, 1)} MIN</text>`
    + `<text class="od-lab" x="2" y="${H + 20}">APOGEE ${fmt.int(orbit.apogee_km)} KM</text>`
    + `<text class="od-lab" x="${W - 2}" y="${H + 20}" text-anchor="end">PERIGEE ${fmt.int(orbit.perigee_km)} KM</text></svg>`;
}

// ---- an altitude range as a tiny bar (the catalogue's rows): log scale from 100 km to 600,000 km, ticks at the LEO limit and GEO ----
const ALT_LOG_MIN = 2;
const ALT_LOG_MAX = Math.log10(600000);
export const altPos = (km) => Math.min(Math.max((Math.log10(Math.max(Number(km) || 0, 100)) - ALT_LOG_MIN) / (ALT_LOG_MAX - ALT_LOG_MIN), 0), 1);

export function altitudeBar(perigeeKm, apogeeKm) {
  const a = altPos(perigeeKm);
  const b = altPos(apogeeKm);
  return `<span class="altbar" aria-hidden="true"><b style="left:${(altPos(LEO_TOP_KM) * 100).toFixed(1)}%"></b><b style="left:${(altPos(GEO_KM) * 100).toFixed(1)}%"></b>`
    + `<i style="left:${(Math.min(a, b) * 100).toFixed(1)}%;width:${(Math.abs(b - a) * 100).toFixed(1)}%"></i></span>`;
}

// ---- the demo lab's preview: two tracks crossing, drawn from the numbers in the form (a schematic, not to scale) ----------
const MU_KM3 = 398600.4418;
export const circularSpeed = (altKm) => Math.sqrt(MU_KM3 / (EARTH_KM + altKm));
// the synthetic debris has the target's own speed turned by the crossing angle, so the two speeds are equal
export const closingSpeed = (speed, crossingDeg) => 2 * speed * Math.sin((crossingDeg * Math.PI) / 360);

export function encounterSvg({ missKm, crossingDeg, leadH, periodMin = null, altKm = null }) {
  const W = 420;
  const H = 236;
  const miss = Math.min(Math.max(Number.isFinite(missKm) ? missKm : 0.25, 0.02), 5);
  const deg = Math.min(Math.max(Number.isFinite(crossingDeg) ? crossingDeg : 70, 10), 170);
  const lead = Math.min(Math.max(Number.isFinite(leadH) ? leadH : 6, 1), 24);
  const cx = 150;
  const cy = 88;
  const d = 10 + 50 * Math.sqrt(miss / 5);                       // the miss in pixels, on a square-root scale: a schematic
  const th = (deg * Math.PI) / 180;
  const ux = Math.cos(th);
  const uy = -Math.sin(th);                                      // the debris track; screen y points down
  const dx = cx;
  const dy = cy - d;                                             // the debris, offset from the target at the closest approach
  const s = d / uy;                                              // where the two tracks cross
  const xx = dx + s * ux;
  const line = (px, py, vx, vy, len) => `M${(px - vx * len).toFixed(1)} ${(py - vy * len).toFixed(1)}L${(px + vx * len).toFixed(1)} ${(py + vy * len).toFixed(1)}`;
  const arrow = (px, py, vx, vy) => `M${(px - vy * 5 - vx * 9).toFixed(1)} ${(py + vx * 5 - vy * 9).toFixed(1)}L${px.toFixed(1)} ${py.toFixed(1)}L${(px + vy * 5 - vx * 9).toFixed(1)} ${(py - vx * 5 - vy * 9).toFixed(1)}`;
  const ax = xx + 30;
  const bx = xx + 30 * Math.cos(th);
  const by = cy - 30 * Math.sin(th);
  const mid = th / 2;
  const speed = altKm == null ? null : circularSpeed(altKm);
  const facts = speed == null ? '' : `<text class="en-fact" x="2" y="12">TARGET ${fmt.num(speed, 2)} KM/S</text><text class="en-fact" x="2" y="26">CLOSING ${fmt.num(closingSpeed(speed, deg), 2)} KM/S</text>`;
  // the lead time as a rule with a tick for every half orbit (a burn is planned at a half-orbit before the encounter)
  const y0 = H - 30;
  const half = periodMin ? periodMin / 2 : null;
  const n = half ? Math.floor((lead * 60) / half) : 0;
  const ticks = Array.from({ length: n }, (_, i) => `<line x1="${(20 + ((i + 1) * half) / (lead * 60) * (W - 40)).toFixed(1)}" x2="${(20 + ((i + 1) * half) / (lead * 60) * (W - 40)).toFixed(1)}" y1="${y0 - 5}" y2="${y0 + 5}"/>`).join('');
  const note = half ? `${n} half-orbits of ${fmt.num(half, 1)} min before the encounter` : 'orbit unavailable: half-orbits not shown';
  return `<svg class="enc" viewBox="0 0 ${W} ${H}" role="img" aria-label="Encounter preview: crossing angle ${Math.round(deg)} degrees, miss distance ${fmt.num(miss, 2)} km, ${fmt.num(lead, 1)} hours ahead">`
    + facts
    + `<path class="en-t" d="${line(cx, cy, 1, 0, 130)}"/><path class="en-t a" d="${arrow(cx + 130, cy, 1, 0)}"/>`
    + `<path class="en-d" d="${line(dx, dy, ux, uy, 120)}"/><path class="en-d a" d="${arrow(dx + ux * 120, dy + uy * 120, ux, uy)}"/>`
    + `<path class="en-arc" d="M${ax.toFixed(1)} ${cy}A30 30 0 0 0 ${bx.toFixed(1)} ${by.toFixed(1)}"/>`
    + `<text class="en-lab" x="${(xx + 44 * Math.cos(mid)).toFixed(1)}" y="${(cy - 44 * Math.sin(mid) + 3).toFixed(1)}">${Math.round(deg)}°</text>`
    + `<path class="en-miss" d="M${cx} ${cy}L${dx} ${dy.toFixed(1)}"/>`
    + `<text class="en-lab" x="${cx + 8}" y="${(cy - d / 2 + 3).toFixed(1)}">MISS ${fmt.num(miss, 2)} KM</text>`
    + `<circle class="en-pt t" cx="${cx}" cy="${cy}" r="5"/><circle class="en-pt d" cx="${dx}" cy="${dy.toFixed(1)}" r="4"/>`
    + `<text class="en-lab t" x="${cx - 8}" y="${cy + 20}" text-anchor="end">TARGET (REAL ORBIT)</text>`
    + `<text class="en-lab d" x="${dx - 10}" y="${(dy - 8).toFixed(1)}" text-anchor="end">SYNTHETIC DEBRIS</text>`
    + `<line class="en-rule" x1="20" x2="${W - 20}" y1="${y0}" y2="${y0}"/><g class="en-ticks">${ticks}</g>`
    + `<text class="en-lab" x="20" y="${y0 + 20}">NOW</text><text class="en-lab" x="${W - 20}" y="${y0 + 20}" text-anchor="end">ENCOUNTER · +${fmt.num(lead, 1)} H</text>`
    + `<text class="en-fact" x="${W / 2}" y="${y0 - 12}" text-anchor="middle">${note.toUpperCase()}</text></svg>`;
}

// ---- decoration for the log-in pages: the Earth with three orbits and satellites riding them -------------------------------
// The motion is SMIL (animateMotion along each ellipse), which the browser runs without any script; the stars are a fixed scatter.
export function orbitScene() {
  const cx = 280;
  const cy = 262;
  const rings = [{ rx: 118, ry: 66, rot: -28, dur: 17, dots: 2, cls: 'a' }, { rx: 188, ry: 118, rot: 31, dur: 29, dots: 3, cls: 'b' },
    { rx: 246, ry: 236, rot: 8, dur: 54, dots: 2, cls: 'c' }];
  const track = (rx, ry) => `M${-rx} 0A${rx} ${ry} 0 1 1 ${rx} 0A${rx} ${ry} 0 1 1 ${-rx} 0`;
  const orbits = rings.map((r) => `<g transform="translate(${cx} ${cy}) rotate(${r.rot})"><ellipse class="sc-ring ${r.cls}" rx="${r.rx}" ry="${r.ry}"/>`
    + Array.from({ length: r.dots }, (_, i) => `<circle class="sc-sat ${r.cls}" r="${r.cls === 'a' ? 3.4 : 2.7}"><animateMotion dur="${r.dur}s" begin="${(-(i * r.dur) / r.dots).toFixed(1)}s" repeatCount="indefinite" path="${track(r.rx, r.ry)}"/></circle>`).join('')
    + '</g>').join('');
  let seed = 7;
  const rand = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const stars = Array.from({ length: 70 }, () => `<circle cx="${(rand() * 560).toFixed(0)}" cy="${(rand() * 520).toFixed(0)}" r="${(0.4 + rand() * 0.9).toFixed(2)}" opacity="${(0.25 + rand() * 0.6).toFixed(2)}"/>`).join('');
  return `<svg class="scene" viewBox="0 0 560 524" aria-hidden="true" focusable="false"><defs>`
    + `<radialGradient id="scEarth" cx="36%" cy="32%" r="75%"><stop offset="0" stop-color="#2f78b4"/><stop offset="0.5" stop-color="#0d3050"/><stop offset="1" stop-color="#02060b"/></radialGradient>`
    + `<radialGradient id="scGlow" cx="50%" cy="50%" r="50%"><stop offset="0.72" stop-color="#8ad8ea" stop-opacity="0"/><stop offset="0.9" stop-color="#8ad8ea" stop-opacity="0.22"/><stop offset="1" stop-color="#8ad8ea" stop-opacity="0"/></radialGradient></defs>`
    + `<g class="sc-stars">${stars}</g>${orbits}`
    + `<circle cx="${cx}" cy="${cy}" r="92" fill="url(#scGlow)"/><circle class="sc-earth" cx="${cx}" cy="${cy}" r="64"/><circle class="sc-pulse" cx="${cx}" cy="${cy}" r="64"/></svg>`;
}
