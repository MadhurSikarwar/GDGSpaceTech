// Time zones. Everything in OrbitWatch is stored and computed in UTC; people reading the site in India read
// India Standard Time (UTC+05:30, no daylight saving), so every instant is shown in IST first with UTC beside it.
export const IST_OFFSET_MS = 5.5 * 3600 * 1000;

const p2 = (n) => String(n).padStart(2, '0');

// A Date from a Date, an epoch in ms, or an API string. API datetimes are UTC; those without a zone get "Z".
export function asDate(v) {
  if (v instanceof Date) return v;
  if (typeof v === 'number') return new Date(v);
  if (!v) return new Date(NaN);
  const s = String(v);
  return new Date(/[zZ]$|[+-]\d\d:?\d\d$/.test(s) ? s : `${s.replace(' ', 'T')}Z`);
}

// Calendar fields of an instant in one zone, built from a shifted UTC clock so the browser's own zone never matters.
function fields(v, zone) {
  const d = asDate(v);
  if (Number.isNaN(d.getTime())) return null;
  const t = new Date(d.getTime() + (zone === 'ist' ? IST_OFFSET_MS : 0));
  const iso = t.toISOString();
  return { date: iso.slice(0, 10), md: iso.slice(5, 10), hm: iso.slice(11, 16), hms: iso.slice(11, 19) };
}

export const utcHMS = (v) => fields(v, 'utc')?.hms ?? '—';
export const istHMS = (v) => fields(v, 'ist')?.hms ?? '—';
export const utcHM = (v) => fields(v, 'utc')?.hm ?? '—';
export const istHM = (v) => fields(v, 'ist')?.hm ?? '—';
export const utcDate = (v) => fields(v, 'utc')?.date ?? '—';
export const istDate = (v) => fields(v, 'ist')?.date ?? '—';
export const utcDateTime = (v) => { const f = fields(v, 'utc'); return f ? `${f.date} ${f.hms} UTC` : '—'; };
export const istDateTime = (v) => { const f = fields(v, 'ist'); return f ? `${f.date} ${f.hms} IST` : '—'; };

// "2026-10-06 19:31:19 IST · 14:01:19 UTC" (the UTC part carries its date only when it differs from IST's).
export function bothDateTime(v) {
  const i = fields(v, 'ist');
  const u = fields(v, 'utc');
  if (!i) return '—';
  return `${i.date} ${i.hms} IST · ${u.date === i.date ? '' : `${u.md} `}${u.hms} UTC`;
}

// "19:31:19 IST · 14:01:19 UTC": the clock face used in the header, on the globe and on the landing page.
export function bothClock(v = new Date()) {
  return `${istHMS(v)} IST · ${utcHMS(v)} UTC`;
}

// Hour-minute form used in chart axes and tight spaces.
export function bothHM(v) {
  const i = fields(v, 'ist');
  const u = fields(v, 'utc');
  return i ? `${i.hm} IST · ${u.hm} UTC` : '—';
}
