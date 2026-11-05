// The Database page's map views besides the ER diagram and the table ring: domain columns, table spotlight,
// size treemap and data flow. Each draws into `el` from the same context:
//   { tables, byName, rels, mysql, mongo, state, select }
// and every table it draws is a button that selects that table (data-t), so the detail panel stays in step.
import { esc, fmt } from './ui.js';

export const GROUPS = [
  ['catalogue', 'Catalogue', ['country', 'organisation', 'launch_site', 'launch_vehicle', 'launch', 'space_object', 'mission', 'object_mission', 'object_ownership']],
  ['orbits', 'Orbits and environment', ['orbit_region', 'current_orbit', 'space_weather', 'ground_station']],
  ['screening', 'Screening and alerts', ['conjunction_event', 'watchlist', 'subscription', 'alert', 'reentry_prediction', 'reentry_observation', 'archive_risk_assessment']],
  ['decisions', 'Decision support', ['agent_assessment', 'agent_step', 'maneuver_decision', 'ml_model']],
  ['demo', 'Demo lab', ['demo_scenario', 'demo_object', 'demo_event']],
  ['system', 'Operations', []],
];
export const groupOf = (name) => (GROUPS.find(([, , t]) => t.includes(name)) || GROUPS[GROUPS.length - 1])[0];
export const groupLabel = (id) => GROUPS.find(([g]) => g === id)?.[1] || id;
export const bytes = (n) => (n >= 1048576 ? `${fmt.num(n / 1048576, 1)} MB` : `${fmt.num(n / 1024, 0)} KB`);

const rowsOf = (t) => (t ? fmt.int(t.rows) : '—');

// One click/keyboard handler for every [data-t] in the svg.
function wire(el, select) {
  const svg = el.querySelector('svg');
  const act = (node) => node && select(node.dataset.t);
  svg.addEventListener('click', (e) => act(e.target.closest('[data-t]')));
  svg.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(e.target.closest('[data-t]')); }
  });
}
const neighbours = (rels, name) => {
  const set = new Set([name]);
  rels.forEach((r) => { if (r.from === name) set.add(r.to); if (r.to === name) set.add(r.from); });
  return set;
};

// ------------------------------------------------------------------ domains: one column per area
export function drawDomains(el, { tables, rels, state, select }) {
  const cols = GROUPS.filter(([id]) => tables.some((t) => t.group === id));
  const CW = 178; const GAP = 44; const BH = 38; const BG = 8; const TOP = 44;
  const W = cols.length * CW + (cols.length - 1) * GAP;
  const maxN = Math.max(...cols.map(([id]) => tables.filter((t) => t.group === id).length));
  const H = TOP + maxN * (BH + BG) + 10;
  const box = new Map();
  cols.forEach(([id], ci) => {
    tables.filter((t) => t.group === id).sort((a, b) => b.rows - a.rows)
      .forEach((t, i) => box.set(t.name, { x: ci * (CW + GAP), y: TOP + i * (BH + BG), ci, t }));
  });
  const near = neighbours(rels, state.sel);
  let edges = ''; let nodes = '';
  rels.forEach((r) => {
    const a = box.get(r.from); const b = box.get(r.to);
    if (!a || !b || r.from === r.to) return;
    let d;
    if (a.ci === b.ci) {
      const x = a.x + CW; const y1 = a.y + BH / 2; const y2 = b.y + BH / 2;
      d = `M${x} ${y1} C${x + 34} ${y1} ${x + 34} ${y2} ${x} ${y2}`;
    } else {
      const fwd = a.ci < b.ci;
      const sx = fwd ? a.x + CW : a.x; const ex = fwd ? b.x : b.x + CW;
      const k = Math.max(30, Math.abs(ex - sx) / 2) * (fwd ? 1 : -1);
      d = `M${sx} ${a.y + BH / 2} C${sx + k} ${a.y + BH / 2} ${ex - k} ${b.y + BH / 2} ${ex} ${b.y + BH / 2}`;
    }
    edges += `<path class="e${r.from === state.sel || r.to === state.sel ? ' hot' : ''}" d="${d}"/>`;
  });
  cols.forEach(([id, label], ci) => {
    nodes += `<text class="ch" x="${ci * (CW + GAP)}" y="14">${esc(label)}</text><line class="chl" x1="${ci * (CW + GAP)}" x2="${ci * (CW + GAP) + CW}" y1="24" y2="24"/>`;
  });
  const top = Math.max(...tables.map((t) => t.rows), 10);
  box.forEach(({ x, y, t }) => {
    const w = Math.max(t.rows ? (Math.log10(t.rows + 1) / Math.log10(top + 1)) * (CW - 20) : 0, t.rows ? 3 : 0);
    nodes += `<g class="bx${t.name === state.sel ? ' sel' : ''}${near.has(t.name) && t.name !== state.sel ? ' near' : ''}" tabindex="0" role="button" data-t="${esc(t.name)}"
      aria-label="${esc(t.name)}, ${rowsOf(t)} rows"><rect x="${x}" y="${y}" width="${CW}" height="${BH}"/>
      <text class="nm" x="${x + 10}" y="${y + 16}">${esc(t.name)}</text><text class="ct" x="${x + CW - 10}" y="${y + 16}" text-anchor="end">${rowsOf(t)}</text>
      <rect class="bar" x="${x + 10}" y="${y + 25}" width="${w.toFixed(1)}" height="3"/></g>`;
  });
  el.innerHTML = `<svg viewBox="-6 0 ${W + 12} ${H}" role="group" aria-label="Tables grouped by area, foreign keys between them">${edges}${nodes}</svg>`;
  wire(el, select);
}

// ------------------------------------------------------------------ spotlight: one table and what is around it
export function drawSpotlight(el, { byName, rels, state, select }) {
  const W = 1040; const H = 880; const cx = W / 2; const cy = H / 2; const R1 = 170; const R2 = 330;
  const hops = state.hops || 2;
  const c = state.sel;
  const hop1 = [...new Set(rels.filter((r) => r.from !== r.to && (r.from === c || r.to === c)).map((r) => (r.from === c ? r.to : r.from)))].sort();
  const seen = new Set([c, ...hop1]);
  const hop2 = [];
  if (hops > 1) {
    rels.forEach((r) => {
      if (r.from === r.to) return;
      [[r.from, r.to], [r.to, r.from]].forEach(([a, b]) => { if (hop1.includes(a) && !seen.has(b)) { seen.add(b); hop2.push(b); } });
    });
  }
  const ang = new Map();
  hop1.forEach((n, i) => ang.set(n, (i / hop1.length) * Math.PI * 2 - Math.PI / 2));
  const parentAngle = (n) => {
    const ps = rels.filter((r) => (r.from === n && hop1.includes(r.to)) || (r.to === n && hop1.includes(r.from)))
      .map((r) => ang.get(r.from === n ? r.to : r.from));
    // mean of directions, not of angles, so two parents either side of the top do not average to the bottom
    const sx = ps.reduce((s, a) => s + Math.cos(a), 0); const sy = ps.reduce((s, a) => s + Math.sin(a), 0);
    return Math.atan2(sy, sx);
  };
  const ordered = hop2.map((n) => [n, parentAngle(n)]).sort((a, b) => a[1] - b[1]);
  const gap = (Math.PI * 2) / Math.max(ordered.length, 16);
  ordered.forEach(([n, a], i) => { ang.set(n, i && a - ang.get(ordered[i - 1][0]) < gap ? ang.get(ordered[i - 1][0]) + gap : a); });
  const at = new Map([[c, { x: cx, y: cy, a: 0, r: 22 }]]);
  const place = (n, R) => {
    const a = ang.get(n);
    const t = byName.get(n);
    at.set(n, { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), a, r: 5 + Math.min(11, Math.log10((t?.rows || 0) + 1) * 2.3) });
  };
  hop1.forEach((n) => place(n, R1));
  ordered.forEach(([n]) => place(n, R2));
  const maxLog = Math.log10(Math.max(...[...byName.values()].map((t) => t.rows), 10));
  at.get(c).r = 14 + (Math.log10((byName.get(c)?.rows || 0) + 1) / maxLog) * 10;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="${esc(c)} and the tables linked to it"><circle class="ring" cx="${cx}" cy="${cy}" r="${R1}"/>${hops > 1 ? `<circle class="ring" cx="${cx}" cy="${cy}" r="${R2}"/>` : ''}`;
  // Edges first; the foreign-key columns of a link are written along its line (not at a point), one line of text
  // per column, so labels on different links never meet.
  const pairs = new Map();
  rels.forEach((r) => {
    const a = at.get(r.from); const b = at.get(r.to);
    if (!a || !b || r.from === r.to) return;
    const hot = r.from === c || r.to === c;
    svg += `<line class="e${hot ? ' hot' : ''}" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"/>`;
    if (hot) {
      const other = r.from === c ? r.to : r.from;
      if (!pairs.has(other)) pairs.set(other, []);
      pairs.get(other).push(r.column);
    }
  });
  pairs.forEach((cols, other) => {
    const p = at.get(other);
    const right = Math.cos(p.a) >= 0;
    const deg = (p.a * 180) / Math.PI + (right ? 0 : 180);
    const mx = cx + (p.x - cx) * 0.5; const my = cy + (p.y - cy) * 0.5;
    [...new Set(cols)].forEach((col, k, all) => {
      svg += `<text class="fk" transform="translate(${mx.toFixed(1)} ${my.toFixed(1)}) rotate(${deg.toFixed(1)})" y="${(-4 + (k - (all.length - 1) / 2) * 12 * (right ? 1 : -1)).toFixed(1)}" text-anchor="middle">${esc(col)}</text>`;
    });
  });
  at.forEach((p, n) => {
    const t = byName.get(n);
    const isC = n === c;
    const right = Math.cos(p.a) >= 0;
    const deg = (p.a * 180) / Math.PI + (right ? 0 : 180);
    const lx = p.x + Math.cos(p.a) * (p.r + 7); const ly = p.y + Math.sin(p.a) * (p.r + 7);
    svg += `<g class="n${isC ? ' sel' : ' near'}${hop2.includes(n) ? ' far' : ''}" tabindex="0" role="button" data-t="${esc(n)}" aria-label="${esc(n)}, ${rowsOf(t)} rows">
      <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${p.r.toFixed(1)}"/>
      ${isC ? `<text class="big" x="${p.x}" y="${p.y - p.r - 12}" text-anchor="middle">${esc(n)}</text><text class="sm" x="${p.x}" y="${p.y + p.r + 18}" text-anchor="middle">${rowsOf(t)} rows</text>`
        : `<text transform="translate(${lx.toFixed(1)} ${ly.toFixed(1)}) rotate(${deg.toFixed(1)})" y="3.6" text-anchor="${right ? 'start' : 'end'}">${esc(n)}</text>`}</g>`;
  });
  el.innerHTML = `${svg}</svg>`;
  wire(el, select);
}

// ------------------------------------------------------------------ treemap: area = rows or size on disk
function squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((s, i) => s + i.v, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest = items.map((i) => ({ ...i, a: i.v * scale })).sort((p, q) => q.a - p.a);
  let rx = x; let ry = y; let rw = w; let rh = h;
  const worst = (row, side) => {
    const s = row.reduce((t, i) => t + i.a, 0);
    const mx = Math.max(...row.map((i) => i.a)); const mn = Math.min(...row.map((i) => i.a));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  while (rest.length) {
    const side = Math.min(rw, rh);
    let row = [rest[0]];
    let k = 1;
    while (k < rest.length && worst([...row, rest[k]], side) <= worst(row, side)) { row.push(rest[k]); k += 1; }
    rest = rest.slice(row.length);
    const s = row.reduce((t, i) => t + i.a, 0);
    if (rw >= rh) {                                  // lay the row down the left edge
      const cw = s / rh; let yy = ry;
      row.forEach((i) => { const hh = i.a / cw; out.push({ ...i, x: rx, y: yy, w: cw, h: hh }); yy += hh; });
      rx += cw; rw -= cw;
    } else {                                         // lay the row along the top edge
      const rhh = s / rw; let xx = rx;
      row.forEach((i) => { const ww = i.a / rhh; out.push({ ...i, x: xx, y: ry, w: ww, h: rhh }); xx += ww; });
      ry += rhh; rh -= rhh;
    }
    row = null;
  }
  return out;
}

export function drawTreemap(el, { tables, state, select }) {
  const W = 1040; const H = 640;
  const metric = state.metric === 'bytes' ? 'bytes' : 'rows';
  const val = (t) => t[metric];
  const floor = Math.max(...tables.map(val), 1) * 0.004;       // the smallest tables stay big enough to click
  const groups = GROUPS.map(([id, label]) => {
    const ts = tables.filter((t) => t.group === id);
    return { id, label, ts, v: ts.reduce((s, t) => s + Math.max(val(t), floor), 0) };
  }).filter((g) => g.ts.length);
  const top = Math.log10(Math.max(...tables.map(val), 10));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Tables sized by ${metric === 'rows' ? 'row count' : 'size on disk'}">`;
  squarify(groups.map((g) => ({ ...g })), 0, 0, W, H).forEach((g) => {
    svg += `<g class="tg"><rect x="${g.x.toFixed(1)}" y="${g.y.toFixed(1)}" width="${g.w.toFixed(1)}" height="${g.h.toFixed(1)}"/>
      <text class="gh" x="${(g.x + 8).toFixed(1)}" y="${(g.y + 15).toFixed(1)}">${esc(g.label.length * 7.2 > g.w - 14 ? `${g.label.slice(0, Math.max(Math.floor((g.w - 14) / 7.2) - 1, 0))}…` : g.label)}</text></g>`;
    squarify(g.ts.map((t) => ({ t, v: Math.max(val(t), floor) })), g.x + 3, g.y + 22, g.w - 6, g.h - 25).forEach(({ t, x, y, w, h }) => {
      const alpha = 0.1 + (Math.log10(val(t) + 1) / top) * 0.55;
      const label = w > 74 && h > 30;
      svg += `<g class="tm${t.name === state.sel ? ' sel' : ''}" tabindex="0" role="button" data-t="${esc(t.name)}"
        aria-label="${esc(t.name)}, ${rowsOf(t)} rows, ${bytes(t.bytes)}"><title>${esc(t.name)}: ${rowsOf(t)} rows, ${bytes(t.bytes)}</title>
        <rect x="${(x + 1).toFixed(1)}" y="${(y + 1).toFixed(1)}" width="${Math.max(w - 2, 0).toFixed(1)}" height="${Math.max(h - 2, 0).toFixed(1)}" style="fill:rgba(138,216,234,${alpha.toFixed(3)})"/>
        ${label ? `<text class="nm" x="${(x + 8).toFixed(1)}" y="${(y + 17).toFixed(1)}">${esc(t.name.length * 6.6 > w - 14 ? `${t.name.slice(0, Math.floor((w - 14) / 6.6) - 1)}…` : t.name)}</text>
        <text class="ct" x="${(x + 8).toFixed(1)}" y="${(y + 31).toFixed(1)}">${metric === 'rows' ? `${rowsOf(t)} rows` : bytes(t.bytes)}</text>` : ''}</g>`;
    });
  });
  el.innerHTML = `${svg}</svg>`;
  wire(el, select);
}

// ------------------------------------------------------------------ data flow: from source to page
// Stages follow the scheduler's jobs: ingest_and_screen, catalog, space_weather, aggregation, and the pages that read the results.
export function drawFlow(el, { byName, mongo, select }) {
  const mg = new Map(mongo.collections.map((c) => [c.name, c]));
  const N = (id, col, row, label, sub, kind, table) => ({ id, col, row, label, sub, kind, table });
  const tbl = (n) => `${rowsOf(byName.get(n))} rows`;
  const doc = (n) => (mg.get(n) ? `${fmt.int(mg.get(n).documents)} docs` : 'MongoDB');
  const nodes = [
    N('celestrak', 0, 0, 'CelesTrak GP', 'element sets, every 4 h', 'src'),
    N('spacetrack', 0, 1, 'Space-Track', 'element sets + history', 'src'),
    N('catalog', 0, 2, 'SATCAT + GCAT', 'object reference, weekly', 'src'),
    N('noaa', 0, 3, 'NOAA SWPC', 'Kp, Ap, F10.7', 'src'),
    N('dl', 1, 0, 'download_log', doc('download_log'), 'mongo'),
    N('hist', 1, 1, 'orbit_history', doc('orbit_history'), 'mongo'),
    N('raw', 1, 2, 'raw_catalog', doc('raw_catalog'), 'mongo'),
    N('mr', 1, 3, 'mr_monthly_altitude', doc('mr_monthly_altitude'), 'mongo'),
    N('cur', 2, 0, 'current_orbit', tbl('current_orbit'), 'sql', 'current_orbit'),
    N('obj', 2, 1, 'space_object', tbl('space_object'), 'sql', 'space_object'),
    N('wx', 2, 2, 'space_weather', tbl('space_weather'), 'sql', 'space_weather'),
    N('wl', 3, 0, 'watchlist', tbl('watchlist'), 'sql', 'watchlist'),
    N('ev', 3, 1, 'conjunction_event', tbl('conjunction_event'), 'sql', 'conjunction_event'),
    N('al', 4, 0, 'alert', tbl('alert'), 'sql', 'alert'),
    N('ag', 4, 1, 'agent_assessment', tbl('agent_assessment'), 'sql', 'agent_assessment'),
    N('md', 4, 2, 'maneuver_decision', tbl('maneuver_decision'), 'sql', 'maneuver_decision'),
    N('globe', 5, 0, 'Globe', 'live positions', 'page'),
    N('dash', 5, 1, 'Dashboard', 'health and events', 'page'),
    N('cat', 5, 2, 'Catalogue', 'every object', 'page'),
    N('conj', 5, 3, 'Close approaches', 'events and decisions', 'page'),
  ];
  const edges = [['celestrak', 'dl'], ['spacetrack', 'dl'], ['dl', 'hist'], ['catalog', 'raw'], ['noaa', 'wx'], ['hist', 'cur'], ['hist', 'mr'],
    ['raw', 'obj'], ['cur', 'obj'], ['cur', 'ev'], ['wl', 'ev'], ['ev', 'al'], ['ev', 'ag'], ['ag', 'md'],
    ['cur', 'globe'], ['ev', 'dash'], ['obj', 'cat'], ['ev', 'conj'], ['md', 'conj']];
  const stages = ['Sources', 'MongoDB history', 'MySQL catalogue', 'SGP4 screening', 'Alerts and AI', 'Pages'];
  const CW = 150; const GAP = 58; const RH = 74; const TOP = 50; const BH = 52;
  const W = stages.length * CW + (stages.length - 1) * GAP;
  const H = TOP + 4 * RH;
  const by = new Map(nodes.map((n) => [n.id, { ...n, x: n.col * (CW + GAP), y: TOP + n.row * RH }]));
  let svg = `<svg viewBox="-6 0 ${W + 12} ${H}" role="group" aria-label="How data moves from public sources to the pages">
    <defs><marker id="dbArr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z" class="arr"/></marker></defs>`;
  stages.forEach((s, i) => { svg += `<text class="ch" x="${i * (CW + GAP)}" y="14">${String(i + 1).padStart(2, '0')} · ${esc(s)}</text><line class="chl" x1="${i * (CW + GAP)}" x2="${i * (CW + GAP) + CW}" y1="24" y2="24"/>`; });
  edges.forEach(([a, b]) => {
    const p = by.get(a); const q = by.get(b);
    const sx = p.x + CW; const sy = p.y + BH / 2; const ex = q.x; const ey = q.y + BH / 2;
    const k = (ex - sx) / 2;
    svg += `<path class="e" d="M${sx} ${sy} C${sx + k} ${sy} ${ex - k} ${ey} ${ex - 1} ${ey}" marker-end="url(#dbArr)"/>`;
  });
  by.forEach((n) => {
    const inner = `<rect x="${n.x}" y="${n.y}" width="${CW}" height="${BH}"/>
      <text class="nm" x="${n.x + 10}" y="${n.y + 21}">${esc(n.label)}</text><text class="ct" x="${n.x + 10}" y="${n.y + 38}">${esc(n.sub)}</text>`;
    svg += n.table
      ? `<g class="fx ${n.kind}" tabindex="0" role="button" data-t="${esc(n.table)}" aria-label="${esc(n.label)}, ${esc(n.sub)}">${inner}</g>`
      : `<g class="fx ${n.kind}">${inner}</g>`;
  });
  el.innerHTML = `${svg}</svg>`;
  wire(el, select);
}
