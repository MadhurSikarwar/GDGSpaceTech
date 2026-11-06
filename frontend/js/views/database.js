// Database page: what the project stores. A relationship map of the MySQL tables (foreign keys as lines, size as
// dot area), row counts per table, and the MongoDB collections. Everything is read from /api/database, which
// only lists what a public visitor may query.
import { get } from '../api.js';
import { bytes, drawDomains, drawFlow, drawSpotlight, drawTreemap, GROUPS, groupOf } from '../db-views.js';
import { drawEr } from '../er-diagram.js';
import { exportPng, exportSvg } from '../svg-export.js';
import { countUpIn, empty, errorBox, esc, fmt, h, loading, toast } from '../ui.js';

const VIEWS = [
  ['er', 'ER diagram', 'The conceptual design: entities, relationships and cardinality (1, N, M), with the live row count of each entity. Click an entity to see its attributes and its table.'],
  ['ring', 'Table ring', 'Every table in a ring. Dot area = rows; a line = foreign key. Pick a table to light up what it links to.'],
  ['domains', 'Domains', 'Tables in columns by area, largest first, with the foreign keys drawn between them.'],
  ['spotlight', 'Spotlight', 'One table in the centre, the tables it links to around it, and the foreign-key column on each link. Click any table to move the spotlight.'],
  ['treemap', 'Treemap', 'Area shows how much each table holds. The smallest tables are drawn slightly larger than true scale so they can still be clicked.'],
  ['flow', 'Data flow', 'How data moves: public sources, MongoDB history, the MySQL catalogue, screening, alerts and AI, then the pages. Counts are live.'],
];

export async function render(root) {
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Data</div><h1>The database</h1>
    <p>Every table, how they connect, and how much each holds. MySQL keeps the catalogue and the screening results; MongoDB keeps the history of every orbit. Click a table to see its columns and links.</p></div>
    <div class="row"><a class="btn" href="#/catalog">Catalogue</a><a class="btn primary" href="#/insights">How it works <span class="arrow">→</span></a></div></div>`));
  const box = h('<div class="dbv"></div>');
  root.appendChild(box);
  box.appendChild(h(loading('Reading the schema')));
  let data;
  try { data = await get('/database'); } catch (err) { box.innerHTML = ''; box.appendChild(h(errorBox(err))); return undefined; }
  if (!box.isConnected) return undefined;      // left while the schema loaded
  box.innerHTML = '';
  const { mysql, mongo } = data;
  const tables = mysql.tables.map((t) => ({ ...t, group: groupOf(t.name) }));
  const byName = new Map(tables.map((t) => [t.name, t]));
  const rels = mysql.relations.filter((r) => r.from !== r.to);
  const totalRows = tables.reduce((s, t) => s + t.rows, 0);
  const mongoDocs = mongo.collections.reduce((s, c) => s + c.documents, 0);

  box.appendChild(h(`<div class="tiles quiet" aria-label="Database totals">
    ${tile('MySQL tables', tables.length, `${mysql.views.length} views on top`)}
    ${tile('MySQL rows', totalRows, 'across all tables')}
    ${tile('Relationships', mysql.relations.length, 'foreign keys')}
    ${tile('Mongo collections', mongo.available ? mongo.collections.length : null, mongo.available ? mongo.database : 'MongoDB is not reachable')}
    ${tile('Mongo documents', mongo.available ? mongoDocs : null, 'orbit history, raw catalogue, logs')}
  </div>`));

  const layout = h(`<div class="dbv-grid">
    <section class="card bezel" aria-label="Database maps"><div class="card-head"><h2>Database map</h2>
      <div class="dbv-tabs" role="tablist" aria-label="Map view" id="dbTabs">
        ${VIEWS.map(([id, label], i) => `<button type="button" role="tab" data-v="${id}" aria-selected="${i === 0}">${label}</button>`).join('')}</div>
      <div class="row dbv-export" role="group" aria-label="Save this map"><span class="small muted">Save</span>
        <button type="button" class="btn sm" data-exp="svg" title="Save this map as a vector image (SVG)">SVG</button>
        <button type="button" class="btn sm" data-exp="png" title="Save this map as a picture (PNG, 2× size)">PNG</button></div></div>
      <p class="dbv-sub" id="dbSub"></p>
      <div class="dbv-legend" id="dbOpts"></div><div id="dbMap" class="dbv-map"></div></section>
    <aside class="card" id="dbDetail" aria-live="polite"></aside>
  </div>`);
  box.appendChild(layout);

  const state = { sel: byName.has('space_object') ? 'space_object' : tables[0]?.name, group: null, view: 'er', erAll: false, hops: 2, metric: 'rows' };
  const select = (name) => { state.sel = name; paint(); };
  const ctx = { tables, byName, rels, mysql, mongo, state, select };
  const opts = layout.querySelector('#dbOpts');
  const chip = (attr, val, label, on) => `<button type="button" class="chip" ${attr}="${val}" aria-pressed="${on}">${label}</button>`;
  const present = GROUPS.filter(([id]) => tables.some((t) => t.group === id));

  function paintOptions() {
    const v = state.view;
    opts.hidden = !['er', 'ring', 'spotlight', 'treemap'].includes(v);
    if (v === 'er') opts.innerHTML = chip('data-o', 'erAll', 'Show all attributes', state.erAll);
    else if (v === 'ring') {
      opts.innerHTML = present.map(([id, label]) => `<button type="button" class="chip" data-g="${id}" aria-pressed="${state.group === id}">${esc(label)} <b>${tables.filter((t) => t.group === id).length}</b></button>`).join('');
    } else if (v === 'spotlight') opts.innerHTML = chip('data-hops', 1, '1 hop', state.hops === 1) + chip('data-hops', 2, '2 hops', state.hops === 2);
    else if (v === 'treemap') opts.innerHTML = chip('data-metric', 'rows', 'Rows', state.metric === 'rows') + chip('data-metric', 'bytes', 'Size on disk', state.metric === 'bytes');
    else opts.innerHTML = '';
  }
  function paint() {
    const map = layout.querySelector('#dbMap');
    const v = state.view;
    if (v === 'er') drawEr(map, byName, state, select);
    else if (v === 'ring') drawMap(map, tables, rels, state, select);
    else if (v === 'domains') drawDomains(map, ctx);
    else if (v === 'spotlight') drawSpotlight(map, ctx);
    else if (v === 'treemap') drawTreemap(map, ctx);
    else drawFlow(map, ctx);
    paintOptions();
    layout.querySelector('#dbSub').textContent = VIEWS.find(([id]) => id === v)[2];
    layout.querySelectorAll('#dbTabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.v === v)));
    drawDetail(layout.querySelector('#dbDetail'), byName.get(state.sel), mysql, select);
  }
  opts.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.o === 'erAll') state.erAll = !state.erAll;
    else if (b.dataset.g) state.group = state.group === b.dataset.g ? null : b.dataset.g;
    else if (b.dataset.hops) state.hops = Number(b.dataset.hops);
    else if (b.dataset.metric) state.metric = b.dataset.metric;
    paint();
  });
  layout.querySelector('.dbv-export').addEventListener('click', async (e) => {
    const kind = e.target.closest('[data-exp]')?.dataset.exp;
    const svg = layout.querySelector('#dbMap svg');
    if (!kind || !svg) return;
    const name = `orbitwatch-${state.view === 'er' ? 'er-diagram' : `database-${state.view}`}`;
    try {
      if (kind === 'svg') await exportSvg(svg, name);
      else await exportPng(svg, name);
      toast(`Saved ${name}.${kind}`);
    } catch (err) { toast(err.message, 'error'); }
  });
  layout.querySelector('#dbTabs').addEventListener('click', (e) => {
    const v = e.target.closest('[data-v]')?.dataset.v;
    if (v && v !== state.view) { state.view = v; paint(); }
  });
  paint();

  // Rows per table, log scale, grouped like the map.
  const sorted = [...tables].sort((a, b) => b.rows - a.rows);
  const max = Math.log10(Math.max(sorted[0]?.rows || 1, 10));
  box.appendChild(h(`<section class="card"><div class="card-head"><h2>Rows per table</h2><span class="sub">log scale · click a row to open it in the map</span></div>
    <div class="dbv-bars" id="dbBars">${sorted.map((t) => `<button type="button" class="dbv-bar" data-t="${esc(t.name)}">
      <span class="nm">${esc(t.name)}</span>
      <span class="trk"><i style="width:${Math.max(t.rows ? (Math.log10(t.rows + 1) / max) * 100 : 0, t.rows ? 1.5 : 0)}%"></i></span>
      <span class="n">${fmt.int(t.rows)}</span><span class="sz">${bytes(t.bytes)}</span></button>`).join('')}</div></section>`));
  box.querySelector('#dbBars').addEventListener('click', (e) => {
    const t = e.target.closest('[data-t]')?.dataset.t;
    if (!t) return;
    select(t);
    layout.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  box.appendChild(mongoCard(mongo));
  if (mysql.views.length) {
    box.appendChild(h(`<section class="card"><div class="card-head"><h2>Views</h2><span class="sub">saved queries the pages read from</span></div>
      <div class="dbv-views">${mysql.views.map((v) => `<span class="chip static" title="${esc(v.columns.map((c) => c.name).join(', '))}">${esc(v.name)} <b>${v.columns.length}</b></span>`).join('')}</div></section>`));
  }
  countUpIn(box);
  return undefined;
}

function tile(label, value, foot) {
  return `<div class="tile"><div class="label">${esc(label)}</div>
    <div class="value">${value == null ? '—' : fmt.int(value)}</div><div class="foot">${esc(foot)}</div></div>`;
}

// ---------------------------------------------------------------- relationship map
function drawMap(el, tables, rels, state, select) {
  const W = 780; const H = 780; const cx = W / 2; const cy = H / 2; const R = 205;
  const order = [...tables].sort((a, b) => GROUPS.findIndex(([g]) => g === a.group) - GROUPS.findIndex(([g]) => g === b.group)
    || a.name.localeCompare(b.name));
  const maxLog = Math.log10(Math.max(...tables.map((t) => t.rows), 10));
  const pos = new Map();
  order.forEach((t, i) => {
    const a = (i / order.length) * Math.PI * 2 - Math.PI / 2;
    pos.set(t.name, { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), a, r: 4 + (Math.log10(t.rows + 1) / maxLog) * 11 });
  });
  const near = new Set([state.sel]);
  rels.forEach((r) => { if (r.from === state.sel) near.add(r.to); if (r.to === state.sel) near.add(r.from); });
  const dim = (t) => (state.group ? t.group !== state.group : !near.has(t.name));

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Tables arranged in a ring, foreign keys drawn between them">`;
  // Edges bend toward the centre so they do not run along the ring.
  rels.forEach((r) => {
    const a = pos.get(r.from); const b = pos.get(r.to);
    if (!a || !b) return;
    const hot = r.from === state.sel || r.to === state.sel;
    svg += `<path class="e${hot ? ' hot' : ''}" d="M${a.x.toFixed(1)} ${a.y.toFixed(1)} Q${cx} ${cy} ${b.x.toFixed(1)} ${b.y.toFixed(1)}"/>`;
  });
  order.forEach((t) => {
    const p = pos.get(t.name);
    // Labels run along the radius so neighbours never overlap; on the left half they are flipped to stay readable.
    const right = Math.cos(p.a) >= 0;
    const deg = (p.a * 180) / Math.PI + (right ? 0 : 180);
    const lx = p.x + Math.cos(p.a) * (p.r + 7);
    const ly = p.y + Math.sin(p.a) * (p.r + 7);
    const cls = `n${t.name === state.sel ? ' sel' : ''}${near.has(t.name) && t.name !== state.sel ? ' near' : ''}${dim(t) ? ' dim' : ''}`;
    svg += `<g class="${cls}" tabindex="0" role="button" data-t="${esc(t.name)}" aria-label="${esc(t.name)}, ${fmt.int(t.rows)} rows"${t.name === state.sel ? ' aria-current="true"' : ''}>
      <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${p.r.toFixed(1)}"/>
      <text transform="translate(${lx.toFixed(1)} ${ly.toFixed(1)}) rotate(${deg.toFixed(1)})" y="3.6" text-anchor="${right ? 'start' : 'end'}">${esc(t.name)}</text></g>`;
  });
  svg += `<text class="mid" x="${cx}" y="${cy - 4}" text-anchor="middle">${tables.length} tables</text>
    <text class="mid sub" x="${cx}" y="${cy + 14}" text-anchor="middle">${rels.length} links</text></svg>`;
  el.innerHTML = svg;
  const act = (node) => node && select(node.dataset.t);
  el.querySelector('svg').addEventListener('click', (e) => act(e.target.closest('[data-t]')));
  el.querySelector('svg').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(e.target.closest('[data-t]')); }
  });
}

function drawDetail(el, t, mysql, select) {
  if (!t) { el.innerHTML = empty('No table selected'); return; }
  const out = mysql.relations.filter((r) => r.from === t.name);
  const inn = mysql.relations.filter((r) => r.to === t.name && r.from !== t.name);
  const link = (n) => `<button type="button" class="dbv-link" data-t="${esc(n)}">${esc(n)}</button>`;
  el.innerHTML = `<div class="card-head"><h2>${esc(t.name)}</h2><span class="sub">${esc(GROUPS.find(([g]) => g === t.group)[1])}</span></div>
    <dl class="telemetry"><dt>Rows</dt><dd>${fmt.int(t.rows)}</dd><dt>Size on disk</dt><dd>${bytes(t.bytes)}</dd>
      <dt>Columns</dt><dd>${t.columns.length}</dd></dl>
    <h3 class="dbv-h">Columns</h3>
    <ul class="dbv-cols">${t.columns.map((c) => `<li><span class="cn">${esc(c.name)}</span><span class="ct">${esc(c.type)}</span>
      ${c.key === 'PRI' ? '<span class="tag"><i></i>PK</span>' : ''}${out.some((r) => r.column === c.name) ? '<span class="tag"><i></i>FK</span>' : ''}</li>`).join('')}</ul>
    <h3 class="dbv-h">References</h3>
    <p class="dbv-rel">${out.length ? out.map((r) => `${esc(r.column)} → ${link(r.to)}`).join('<br>') : '<span class="muted">none</span>'}</p>
    <h3 class="dbv-h">Referenced by</h3>
    <p class="dbv-rel">${inn.length ? [...new Set(inn.map((r) => r.from))].map(link).join(' ') : '<span class="muted">none</span>'}</p>`;
  el.querySelectorAll('.dbv-link').forEach((b) => b.addEventListener('click', () => select(b.dataset.t)));
}

// ---------------------------------------------------------------- MongoDB
function mongoCard(mongo) {
  if (!mongo.available) {
    return h(`<section class="card"><div class="card-head"><h2>MongoDB</h2></div>${empty('MongoDB is not reachable', 'Start it with ow mongo-start.')}</section>`);
  }
  const top = Math.max(...mongo.collections.map((c) => c.documents), 1);
  return h(`<section class="card bezel"><div class="card-head"><h2>MongoDB · ${esc(mongo.database)}</h2><span class="sub">document store: orbit history, raw catalogue, download log</span></div>
    <div class="dbv-mongo">${mongo.collections.map((c) => `<div class="dbv-coll">
      <div class="spread"><b class="mono">${esc(c.name)}</b><span class="num">${fmt.int(c.documents)} <small class="muted">documents</small></span></div>
      <div class="dbv-track"><i style="width:${Math.max((c.documents / top) * 100, 1.5)}%"></i></div>
      <div class="dbv-fields">${c.fields.map((f) => `<span>${esc(f)}</span>`).join('')}</div></div>`).join('')}</div></section>`);
}
