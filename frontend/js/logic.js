// Pure helpers behind the newer pages (no DOM, no network): the account page's device list, the watchlist timeline,
// the stale-data banner and the assessment comparison. Tested with Node in tests/test_frontend_logic.py.
import { fmt } from './ui.js';

export const RISK_ORDER = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];
export const riskRank = (r) => RISK_ORDER.indexOf(r);
export const worstRisk = (risks) => risks.reduce((w, r) => (riskRank(r) > riskRank(w) ? r : w), null);

// "Chrome on Windows" from a user-agent string. Order matters: Edge and Opera also say "Chrome", Chrome also "Safari".
export function deviceName(ua = '') {
  const browser = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS'
    : /Mac OS X/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} on ${os}` : browser;
}

// Watchlist timeline: close approaches that would be drawn within `px` of each other on a strip `width` wide (spanning
// `from`..`to`, epoch ms) become one cluster, drawn as a single dot with a count. A busy satellite (over a hundred
// approaches in a few hours) then reads as a few labelled clusters instead of an unreadable blob.
export function clusterEvents(events, { from, to, width, px = 12 }) {
  const x = (t) => ((t - from) / (to - from)) * width;
  const sorted = events.map((e) => ({ ...e, t: Date.parse(e.tca) })).filter((e) => e.t >= from && e.t <= to)
    .sort((a, b) => a.t - b.t);
  const summarise = (evs) => {
    const t0 = evs[0].t;
    const t1 = evs[evs.length - 1].t;
    const closest = evs.reduce((m, e) => (Number(e.miss_km) < Number(m.miss_km) ? e : m));
    return { events: evs, t0, t1, x: x((t0 + t1) / 2), count: evs.length, risk: worstRisk(evs.map((e) => e.risk)),
      closest, minMiss: Number(closest.miss_km) };
  };
  let out = sorted.map((e) => summarise([e]));
  // Merge the closest pair of neighbours until every dot is at least px from the next. A group's dot sits at the
  // middle of its time span, so merging can move it: hence the loop rather than a single pass.
  for (;;) {
    let best = -1;
    for (let i = 1; i < out.length; i++) {
      if (out[i].x - out[i - 1].x < px && (best < 0 || out[i].x - out[i - 1].x < out[best].x - out[best - 1].x)) best = i;
    }
    if (best < 0) return out;
    out = [...out.slice(0, best - 1), summarise([...out[best - 1].events, ...out[best].events]), ...out.slice(best + 1)];
  }
}

// Stale-data banner: stale when the newest element-set download OR the last successful screening is older than the
// threshold (hours). Missing timestamps count as infinitely old.
export function staleness({ newestDownload, lastScreening, thresholdH = 8, now = Date.now() }) {
  const ageH = (iso) => (iso ? (now - Date.parse(iso)) / 3600000 : Infinity);
  const download = ageH(newestDownload);
  const screening = ageH(lastScreening);
  const what = download > thresholdH ? 'download' : screening > thresholdH ? 'screening' : null;
  return { stale: !!what, what, downloadAgeH: download, screeningAgeH: screening };
}

// One cell of the assessment comparison, as text.
const isIso = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v);
export function cmpValue(field, v, decisionLabels = {}) {
  if (v == null || v === '') return '—';
  if (field === 'pc_before' || field === 'pc_after') return Number(v).toExponential(1);
  if (field === 'assessment_id' || field === 'parent_assessment_id') return `#${v}`;
  if (field === 'decision') return decisionLabels[v] || String(v);
  if (isIso(v)) return fmt.dt(v);
  if (typeof v === 'number') return fmt.num(v, Number.isInteger(v) ? 0 : 3);
  return String(v);
}

// Login check (see orbitwatch/challenge.py): find a nonce so that SHA-256("<salt>:<nonce>") starts with `bits` zero
// bits. Digests are computed in parallel batches (Web Crypto is asynchronous), which keeps the wait to about a second.
const zeroBits = (bytes) => {
  let n = 0;
  for (const b of bytes) {
    if (b === 0) { n += 8; continue; }
    return n + Math.clz32(b) - 24;
  }
  return n;
};
export async function solveChallenge(ch, { batch = 512, subtle = globalThis.crypto.subtle } = {}) {
  const enc = new TextEncoder();
  for (let start = 0; ; start += batch) {
    const nonces = Array.from({ length: batch }, (_, i) => start + i);
    const digests = await Promise.all(nonces.map((n) => subtle.digest('SHA-256', enc.encode(`${ch.salt}:${n}`))));
    const hit = digests.findIndex((d) => zeroBits(new Uint8Array(d)) >= ch.bits);
    if (hit >= 0) return { ...ch, nonce: nonces[hit] };
  }
}
