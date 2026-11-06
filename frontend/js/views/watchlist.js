// My watchlist: every satellite the user follows, each with a risk timeline of its close approaches from N days ago to
// N days ahead (N = 3, 7 or 14). Approaches drawn closer than a few pixels apart are grouped into one dot with a count
// (a busy satellite can have a hundred in a few hours); dot colour = worst risk, size = risk level, past ones faded.
// A single dot opens that close approach, a group opens the close-approach list filtered to that object and those
// days. The page refreshes itself when a screening finishes, and new satellites can be followed from here.
import { del, get, post } from '../api.js';
import { onLive } from '../live.js';
import { clusterEvents, RISK_ORDER, worstRisk } from '../logic.js';
import { exportSvg } from '../svg-export.js';
import { countUpIn, empty, errorBox, esc, fmt, h, hashQuery, loading, objLink, riskBadge, setHashQuery, toast, typeTag } from '../ui.js';

const WINDOWS = [3, 7, 14];
const RISK_R = { LOW: 3.2, MEDIUM: 4.2, HIGH: 5.4, CRITICAL: 6.6 };
const W = 640;
const H = 46;
const PAD = 8;

export async function render(root) {
  const q = hashQuery();
  let days = WINDOWS.includes(Number(q.days)) ? Number(q.days) : 7;
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Alerts &amp; subscriptions</div><h1>My watchlist</h1>
    <p>Every satellite you follow, with its close approaches before and after now. A dot is one close approach (a number on it:
    several close together); its colour and size show the risk level. Click a dot to open it.</p></div>
    <div class="row"><a class="btn" href="#/alerts">My alerts</a><a class="btn" href="#/catalog">Browse the catalogue</a></div></div>`));
  const bar = h(`<div class="wl-bar">
    <form class="row wl-follow" autocomplete="off"><input name="obj" placeholder="NORAD number or name, e.g. 25544 or CARTOSAT-3" aria-label="Satellite to follow" required>
      <button class="btn primary" type="submit">Follow</button></form>
    <div class="view-toggle" role="group" aria-label="Time window">${WINDOWS.map((d) => `<button type="button" data-days="${d}" class="${d === days ? 'active' : ''}">±${d} days</button>`).join('')}</div>
  </div>`);
  root.appendChild(bar);
  const box = h(`<div class="stack">${loading('Reading your watchlist')}</div>`);
  root.appendChild(box);

  async function load() {
    let data;
    try { data = await get(`/me/watchlist?days=${days}`); } catch (err) { box.innerHTML = errorBox(err); return; }
    if (!box.isConnected) return;
    paint(box, data.items, days);
  }

  bar.querySelector('.view-toggle').addEventListener('click', (e) => {
    const d = Number(e.target.closest('[data-days]')?.dataset.days);
    if (!d || d === days) return;
    days = d;
    bar.querySelectorAll('[data-days]').forEach((b) => b.classList.toggle('active', Number(b.dataset.days) === d));
    setHashQuery({ days: d === 7 ? '' : d });
    load();
  });
  bar.querySelector('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = e.target.obj.value.trim();
    try {
      let norad = /^\d+$/.test(text) ? Number(text) : null;
      let name = text;
      if (!norad) {
        const r = await get(`/objects?${new URLSearchParams({ q: text, status: 'all', page_size: '1', sort: 'relevance' })}`);
        if (!r.items.length) throw new Error(`No object matches “${text}”.`);
        ({ norad_id: norad, name } = r.items[0]);
      }
      await post('/me/subscriptions', { norad_id: norad });
      e.target.reset();
      toast(`Following ${name} (NORAD ${norad})`);
      load();
    } catch (err) { toast(err.message, 'error'); }
  });
  box.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-unsub]');
    if (b) {
      b.disabled = true;
      try { await del(`/me/subscriptions/${b.dataset.unsub}`); toast('Unfollowed'); load(); } catch (err) { b.disabled = false; toast(err.message, 'error'); }
      return;
    }
    if (e.target.closest('[data-save]')) {
      const svg = buildSvgForExport(box);
      if (svg) exportSvg(svg, `orbitwatch-watchlist-${days}d`);
    }
  });
  // A finished screening (or new data) may add close approaches: redraw, without the loading flash.
  let pending = null;
  const off = onLive((type, d) => {
    if (type !== 'log' || !['screening', 'data'].includes(d.category)) return;
    clearTimeout(pending);
    pending = setTimeout(load, 1500);
  });
  await load();
  return () => { off(); clearTimeout(pending); };
}

function paint(box, items, days) {
  const now = Date.now();
  const rows = items.map((o) => ({ ...o, ahead: o.events.filter((e) => Date.parse(e.tca) >= now) }));
  if (!rows.length) {
    box.innerHTML = `<section class="card">${empty('You do not follow any satellite yet',
      ' Type a NORAD number or a name above (or open an object and choose “Subscribe to alerts”); it appears here with its risk timeline.')}</section>`;
    return;
  }
  const ahead = rows.flatMap((o) => o.ahead);
  const top = worstRisk(ahead.map((e) => e.risk));
  const topN = ahead.filter((e) => e.risk === top).length;
  const closest = ahead.reduce((m, e) => (m == null || Number(e.miss_km) < Number(m.miss_km) ? e : m), null);
  box.innerHTML = `<div class="tiles quiet" aria-label="Watchlist totals">
      <div class="tile"><div class="label">Satellites followed</div><div class="value">${fmt.int(rows.length)}</div>
        <div class="foot">${fmt.int(rows.filter((o) => o.ahead.length).length)} with a close approach ahead</div></div>
      <div class="tile"><div class="label">Approaches ahead · ${days} days</div><div class="value">${fmt.int(ahead.length)}</div>
        <div class="foot">${fmt.int(rows.reduce((s, o) => s + o.events.length - o.ahead.length, 0))} in the past ${days} days</div></div>
      <div class="tile"><div class="label">Highest risk ahead</div><div class="value" style="font-size:22px">${top ? riskBadge(top) : '—'}</div>
        <div class="foot">${top ? `${fmt.int(topN)} event${topN === 1 ? '' : 's'}` : 'nothing predicted'}</div></div>
      <div class="tile"><div class="label">Closest miss ahead</div><div class="value">${closest ? fmt.km(closest.miss_km, 2) : '—'}</div>
        <div class="foot">${closest ? `${esc(closest.other_name)} · ${esc(fmt.rel(closest.tca))}` : 'nothing predicted'}</div></div>
    </div>
    <section class="card flush"><div class="card-head"><h2>Risk timeline</h2>
      <div class="row"><span class="sub">${days} days back · now · ${days} days ahead</span>
        <button class="btn sm" type="button" data-save title="Save these timelines as an SVG image">Save SVG</button></div></div>
      <div class="wl-legend small muted">${RISK_ORDER.map((r) => `<span><i class="wl-dot risk-fill-${r}"></i>${r}</span>`).join('')}<span><i class="wl-dot past"></i>past</span><span><b class="wl-count-key">3</b> grouped approaches</span></div>
      <div class="wl-list">${rows.map((o) => row(o, now, days)).join('')}</div></section>`;
  countUpIn(box);
}

function row(o, now, days) {
  const next = o.ahead[0];
  const top = worstRisk(o.ahead.map((e) => e.risk));
  const orbit = o.perigee_km != null ? `${fmt.int(o.perigee_km)} × ${fmt.int(o.apogee_km)} km · ${fmt.num(o.inclination, 1)}°` : 'no current orbit';
  return `<div class="wl-row" data-norad="${o.norad_id}">
    <div class="wl-obj">${objLink(o.norad_id, o.name)} ${typeTag(o.object_type)}
      <div class="small muted mono">NORAD ${o.norad_id} · ${esc(orbit)}</div></div>
    <div class="wl-line">${timeline(o, now, days)}</div>
    <div class="wl-next">${next
      ? `${riskBadge(top)}<div class="small">next ${esc(fmt.rel(next.tca))} · ${fmt.km(next.miss_km, 2)}</div>`
      : '<span class="small muted">none ahead</span>'}
      <button class="btn sm ghost" data-unsub="${o.norad_id}" title="Stop following ${esc(o.name)}">Unfollow</button></div>
  </div>`;
}

// One SVG strip: x = time (−days … +days), y = miss distance of the closest approach in the group (lower = closer).
function timeline(o, now, days) {
  const span = days * 86400000;
  const from = now - span;
  const to = now + span;
  const x = (t) => PAD + ((t - from) / (to - from)) * (W - 2 * PAD);
  // Grouped wider than the biggest dot (radius up to ~10 with a count), so neither dots nor their counts overlap.
  const clusters = clusterEvents(o.events, { from, to, width: W - 2 * PAD, px: 22 });
  const maxMiss = Math.max(10, ...clusters.map((c) => c.minMiss));
  const y = (km) => 9 + (Math.min(km, maxMiss) / maxMiss) * (H - 20);
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="wl-svg" role="img" aria-label="${esc(o.name)}: ${o.events.length} close approaches in ±${days} days">`;
  const step = days > 7 ? 2 : 1;
  for (let d = -days; d <= days; d += step) {
    const xx = x(now + d * 86400000).toFixed(1);
    svg += `<line class="${d === 0 ? 'now' : 'tick'}" x1="${xx}" x2="${xx}" y1="2" y2="${H - 8}"/>`;
    if (d === 0 || d === -days || d === days) svg += `<text x="${xx}" y="${H - 0.5}" text-anchor="middle">${d === 0 ? 'now' : `${d > 0 ? '+' : '−'}${Math.abs(d)} d`}</text>`;
  }
  // Highest risk drawn last, so it sits on top.
  [...clusters].sort((a, b) => RISK_ORDER.indexOf(a.risk) - RISK_ORDER.indexOf(b.risk)).forEach((c) => {
    const past = c.t1 < now;
    const r = (RISK_R[c.risk] || 3) + (c.count > 1 ? Math.min(4, Math.log2(c.count)) : 0);
    const cx = (PAD + c.x).toFixed(1);
    const cy = y(c.minMiss).toFixed(1);
    const e = c.closest;
    const tip = c.count === 1
      ? `${e.risk} · vs ${e.other_name} (${e.other_type || 'object'}) · miss ${fmt.num(e.miss_km, 3)} km · ${fmt.dt(e.tca)}`
      : `${c.count} close approaches, worst ${c.risk} · closest ${fmt.num(c.minMiss, 3)} km (vs ${e.other_name}) · ${fmt.dt(new Date(c.t0).toISOString())} to ${fmt.dt(new Date(c.t1).toISOString())}`;
    const href = c.count === 1 ? `#/conjunctions?event=${Number(e.event_id)}`
      : `#/conjunctions?norad=${Number(o.norad_id)}&when=all&sort=miss&from=${new Date(c.t0).toISOString().slice(0, 10)}&to=${new Date(c.t1).toISOString().slice(0, 10)}`;
    svg += `<a href="${href}"><circle class="risk-fill-${esc(c.risk)}${past ? ' past' : ''}" cx="${cx}" cy="${cy}" r="${r.toFixed(1)}"><title>${esc(tip)}</title></circle>`
      + (c.count > 1 ? `<text class="cnt" x="${cx}" y="${(Number(cy) + 3).toFixed(1)}" text-anchor="middle">${c.count > 99 ? '99+' : c.count}</text>` : '')
      + '</a>';
  });
  if (!o.events.length) svg += `<text class="none" x="${W / 2}" y="${H / 2}" text-anchor="middle">no close approach in this window</text>`;
  return `${svg}</svg>`;
}

// All the timelines stacked into one SVG (with the object names) for "Save SVG".
function buildSvgForExport(box) {
  const rows = [...box.querySelectorAll('.wl-row')];
  if (!rows.length) return null;
  const LABEL = 220;
  const ROW = H + 14;
  const ns = 'http://www.w3.org/2000/svg';
  const out = document.createElementNS(ns, 'svg');
  out.setAttribute('viewBox', `0 0 ${LABEL + W} ${rows.length * ROW + 6}`);
  rows.forEach((r, i) => {
    const g = document.createElementNS(ns, 'g');
    g.setAttribute('transform', `translate(0 ${i * ROW + 6})`);
    const label = document.createElementNS(ns, 'text');
    label.setAttribute('x', '4');
    label.setAttribute('y', String(H / 2));
    label.setAttribute('class', 'wl-export-label');
    label.textContent = `${r.querySelector('.wl-obj a').textContent} (${r.dataset.norad})`;
    g.appendChild(label);
    const strip = r.querySelector('svg').cloneNode(true);
    strip.setAttribute('x', String(LABEL));
    strip.setAttribute('width', String(W));
    strip.setAttribute('height', String(H));
    g.appendChild(strip);
    out.appendChild(g);
  });
  // Computed styles are read from the live page, so the stack is attached (hidden) while it is serialised.
  out.style.position = 'absolute';
  out.style.left = '-99999px';
  box.querySelector('.wl-list').appendChild(out);
  setTimeout(() => out.remove(), 0);
  return out;
}
