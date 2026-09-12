// Shared formatting & math helpers.

export function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}

export function fmtKm(v, digits = 1) {
  return `${fmtNum(v, digits)} km`;
}

export function fmtUTC(date) {
  const d = date instanceof Date ? date : new Date(date);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
}

export function fmtClock(date) {
  const d = date instanceof Date ? date : new Date(date);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

export function relativeAge(hours) {
  if (hours === null || hours === undefined) return { text: '—', cls: 'age-stale' };
  if (hours < 1) return { text: `${Math.round(hours * 60)}m ago`, cls: 'age-fresh' };
  if (hours < 24) return { text: `${hours.toFixed(1)}h ago`, cls: hours < 6 ? 'age-fresh' : 'age-mid' };
  return { text: `${Math.round(hours / 24)}d ago`, cls: 'age-stale' };
}

export function minutesUntil(isoTime, now = new Date()) {
  const t = new Date(isoTime).getTime();
  return (t - now.getTime()) / 60000;
}

export function fmtCountdown(minutes) {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return '—';
  const past = minutes < 0;
  const abs = Math.abs(minutes);
  const h = Math.floor(abs / 60);
  const m = Math.floor(abs % 60);
  const s = Math.floor((abs * 60) % 60);
  const str = h > 0 ? `${h}h ${m}m` : `${m}m ${s}s`;
  return past ? `T+${str}` : `T-${str}`;
}

export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

export function lerp(a, b, t) {
  return a + (b - a) * t;
}

export function lerpVec3(a, b, t) {
  return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), z: lerp(a.z, b.z, t) };
}

export function debounce(fn, ms) {
  let h;
  return (...args) => {
    clearTimeout(h);
    h = setTimeout(() => fn(...args), ms);
  };
}

export function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

export function shortId(id, len = 10) {
  if (!id) return '—';
  return id.length > len ? id.slice(0, len) + '…' : id;
}

export function riskTierColorVar(tier) {
  switch ((tier || '').toUpperCase()) {
    case 'CRITICAL': return '--red';
    case 'HIGH': return '--amber';
    case 'MEDIUM': return '--cyan';
    case 'LOW': return '--green';
    default: return '--ink-2';
  }
}

let seedState = 42;
export function seededRandom() {
  seedState = (seedState * 1664525 + 1013904223) >>> 0;
  return seedState / 4294967296;
}
export function seedRandom(seed) {
  seedState = seed >>> 0;
}
