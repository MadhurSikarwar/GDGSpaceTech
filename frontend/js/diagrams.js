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
  return `<svg class="rd" viewBox="0 0 ${SIZE} ${SIZE}" role="img" aria-label="Radar of upcoming close approaches: time to closest approach clockwise from the top, miss distance from the centre">`
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
