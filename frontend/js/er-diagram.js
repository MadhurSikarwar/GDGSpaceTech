// The project's entity-relationship diagram (Chen notation) drawn as live SVG: entities carry their current row
// counts, and clicking one selects the matching table on the Database page.
// Entity -> table: a few conceptual entities are named differently from their tables.
import { esc, fmt } from './ui.js';

const S = 1.25;                                    // layout is drawn on a 750-wide grid and scaled up
let X0 = 30;                                       // left margin: wider when attribute fans point left
const P = (x, y) => [x * S + X0, y * S];

// id: [x, y, table, attribute fan direction (deg, 0 = right, 90 = down), attributes (key first, marked *)]
const ENTITIES = {
  LAUNCH_SITE: [147, 93, 'launch_site', -90, ['*site_id', 'name', 'country', 'latitude', 'longitude']],
  LAUNCH: [340, 93, 'launch', -90, ['*launch_id', 'launch_date', 'outcome']],
  LAUNCH_VEHICLE: [524, 93, 'launch_vehicle', -90, ['*vehicle_id', 'name']],
  ORGANISATION: [698, 93, 'organisation', -90, ['*org_id', 'name', 'org_type', 'country']],
  SPACE_OBJECT: [340, 267, 'space_object', 165, ['*norad_id', 'intl_designator', 'name', 'object_type', 'status', 'decay_date']],
  MISSION: [547, 267, 'mission', 90, ['*mission_id', 'name', 'purpose']],
  CURRENT_ORBIT: [340, 413, 'current_orbit', 90, ['*epoch', 'perigee_km', 'apogee_km', 'inclination', 'period_min']],
  ORBIT_REGION: [627, 413, 'orbit_region', 90, ['*region_id', 'name', 'min_altitude_km', 'max_altitude_km']],
  CONJUNCTION_EVENT: [180, 428, 'conjunction_event', 170, ['*event_id', 'risk_level', 'relative_velocity', 'time_of_closest_approach', 'miss_distance_km']],
  ALERT: [180, 550, 'alert', 90, ['*alert_id', 'sent_on', 'acknowledged']],
  OBJECT_OWNERSHIP: [699, 229, 'object_ownership', 90, ['from_date', 'to_date'], true],   // associative entity
};

const RELS = {
  HOSTS: [244, 93], USED_IN: [434, 93], BUILDS: [611, 93], DEPLOYS: [340, 180], BREAKS_FROM: [248, 200],
  OWNS: [698, 180], SERVES: [448, 267], HAS_LATEST_ORBIT: [340, 343], LIES_IN: [491, 413],
  INVOLVES: [274, 428], TRIGGERS: [180, 492],
};

// [from, to, label at from, label at to, total participation, anchor offset on from, anchor offset on to]
const LINKS = [
  ['LAUNCH_SITE', 'HOSTS', '1', ''], ['HOSTS', 'LAUNCH', '', 'N'],
  ['LAUNCH', 'USED_IN', 'N', ''], ['USED_IN', 'LAUNCH_VEHICLE', '', '1'],
  ['LAUNCH_VEHICLE', 'BUILDS', 'N', ''], ['BUILDS', 'ORGANISATION', '', '1'],
  ['LAUNCH', 'DEPLOYS', '1', ''], ['DEPLOYS', 'SPACE_OBJECT', '', 'N', true],
  ['SPACE_OBJECT', 'BREAKS_FROM', '1', '', false, [-34, -8]], ['SPACE_OBJECT', 'BREAKS_FROM', 'N', '', false, [-52, -2]],
  ['ORGANISATION', 'OWNS', 'M', ''], ['OWNS', 'OBJECT_OWNERSHIP', '', ''],
  ['OWNS', 'SPACE_OBJECT', '', 'M', false, null, [62, -6]],
  ['SPACE_OBJECT', 'SERVES', 'N', ''], ['SERVES', 'MISSION', '', 'M'],
  ['SPACE_OBJECT', 'HAS_LATEST_ORBIT', '1', '', true], ['HAS_LATEST_ORBIT', 'CURRENT_ORBIT', '', '1', true],
  ['CURRENT_ORBIT', 'LIES_IN', 'N', ''], ['LIES_IN', 'ORBIT_REGION', '', '1'],
  ['CONJUNCTION_EVENT', 'INVOLVES', 'N', ''], ['INVOLVES', 'CURRENT_ORBIT', '', 'N'],
  ['INVOLVES', 'SPACE_OBJECT', '', 'M', false, null, [-52, 14]],
  ['CONJUNCTION_EVENT', 'TRIGGERS', '1', ''], ['TRIGGERS', 'ALERT', '', 'N', true],
];

const pretty = (id) => id.replace(/_/g, ' ');
const entW = (id) => Math.max(104, id.length * 7.6 + 22);
const ENT_H = 46;
const relW = (id) => Math.max(46, id.length * 3.9 + 14);

function pos(id) {
  const e = ENTITIES[id] || RELS[id];
  return P(e[0], e[1]);
}

export function drawEr(el, byName, state, select) {
  X0 = state.erAll ? 100 : 30;
  const W = state.erAll ? 1150 : 1010; const H = 800;
  const hw = (id) => (ENTITIES[id] ? entW(id) / 2 : relW(id));
  const hh = (id) => (ENTITIES[id] ? ENT_H / 2 : 17);
  const cardinality = (a, b, text, off) => {
    if (!text) return '';
    const [ax, ay] = pos(a); const [bx, by] = pos(b);
    const dx = bx - ax; const dy = by - ay; const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len; const uy = dy / len;
    // the label sits just outside the node at the end it describes
    const reach = Math.min(hw(a) / Math.max(Math.abs(ux), 0.01), hh(a) / Math.max(Math.abs(uy), 0.01)) + 13;
    return `<text class="card" x="${(ax + (off?.[0] || 0) + ux * reach - uy * 9).toFixed(1)}" y="${(ay + (off?.[1] || 0) + uy * reach + ux * 9 + 4).toFixed(1)}" text-anchor="middle">${esc(text)}</text>`;
  };

  let lines = ''; let labels = '';
  LINKS.forEach(([a, b, la, lb, total, offA, offB]) => {
    const [ax, ay] = pos(a); const [bx, by] = pos(b);
    const x1 = ax + (offA?.[0] || 0); const y1 = ay + (offA?.[1] || 0);
    const x2 = bx + (offB?.[0] || 0); const y2 = by + (offB?.[1] || 0);
    lines += `<line class="l${total ? ' total' : ''}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`;
    if (total) lines += `<line class="l-in" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`;
    // cardinality is written next to the entity end of the line
    if (la) labels += cardinality(a, b, la, offA);
    if (lb) labels += cardinality(b, a, lb, offB);
  });

  let attrs = ''; let nodes = '';
  Object.entries(ENTITIES).forEach(([id, [, , table, dir, list, assoc]]) => {
    const [cx, cy] = pos(id);
    const showAttrs = state.erAll;
    const n = showAttrs ? list.length : 0;
    const sideways = Math.abs(Math.cos((dir * Math.PI) / 180)) > 0.6;
    const spread = Math.min(sideways ? 110 : 160, 38 * (n - 1) + 20);
    (showAttrs ? list : []).forEach((raw, i) => {
      const key = raw.startsWith('*');
      const name = key ? raw.slice(1) : raw;
      const a = ((dir + (n === 1 ? 0 : -spread / 2 + (spread * (sideways ? n - 1 - i : i)) / (n - 1))) * Math.PI) / 180;
      const rx = name.length * 3.5 + 10;
      const horizontal = Math.abs(Math.cos(a)) > 0.6;
      const R = horizontal ? entW(id) / 2 + rx + 26 + (i % 2) * 16 : 58 + (i % 2) * 24;
      const ax = cx + Math.cos(a) * R; const ay = cy + Math.sin(a) * (horizontal ? R * 0.9 : R);
      attrs += `<line class="l a" x1="${cx.toFixed(1)}" y1="${cy.toFixed(1)}" x2="${ax.toFixed(1)}" y2="${ay.toFixed(1)}"/>
        <g class="at${key ? ' key' : ''}"><ellipse cx="${ax.toFixed(1)}" cy="${ay.toFixed(1)}" rx="${rx.toFixed(1)}" ry="10"/>
        <text x="${ax.toFixed(1)}" y="${(ay + 3.4).toFixed(1)}" text-anchor="middle">${esc(name)}</text></g>`;
    });
    const t = byName.get(table);
    const w = entW(id);
    const sel = state.sel === table;
    nodes += `<g class="en${assoc ? ' assoc' : ''}${sel ? ' sel' : ''}" tabindex="0" role="button" data-t="${esc(table)}"
        aria-label="${esc(pretty(id))}, table ${esc(table)}${t ? `, ${fmt.int(t.rows)} rows` : ''}"${sel ? ' aria-current="true"' : ''}>
      <rect x="${(cx - w / 2).toFixed(1)}" y="${(cy - ENT_H / 2).toFixed(1)}" width="${w.toFixed(1)}" height="${ENT_H}"/>
      ${assoc ? `<rect class="in" x="${(cx - w / 2 + 3).toFixed(1)}" y="${(cy - ENT_H / 2 + 3).toFixed(1)}" width="${(w - 6).toFixed(1)}" height="${ENT_H - 6}"/>` : ''}
      <text x="${cx.toFixed(1)}" y="${(cy - 6).toFixed(1)}" text-anchor="middle" class="nm">${esc(pretty(id))}</text>
      <text x="${cx.toFixed(1)}" y="${(cy + 7).toFixed(1)}" text-anchor="middle" class="ct">${t ? fmt.int(t.rows) : '—'} rows</text>
      ${list.find((a) => a.startsWith('*')) ? `<text x="${cx.toFixed(1)}" y="${(cy + 18).toFixed(1)}" text-anchor="middle" class="pk">PK ${esc(list.find((a) => a.startsWith('*')).slice(1))}</text>` : ''}</g>`;
  });
  Object.entries(RELS).forEach(([id]) => {
    const [cx, cy] = pos(id);
    const w = relW(id);
    nodes += `<g class="rl"><polygon points="${cx - w},${cy} ${cx},${cy - 17} ${cx + w},${cy} ${cx},${cy + 17}"/>
      <text x="${cx}" y="${cy + 3.2}" text-anchor="middle">${esc(pretty(id))}</text></g>`;
  });

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Entity-relationship diagram">${lines}${attrs}${labels}${nodes}
    <text class="note" x="${P(248, 169)[0]}" y="${P(248, 169)[1]}" text-anchor="middle">1 = parent, N = debris</text>
    <g class="key" transform="translate(${W - 238} ${H - 150})">
      <text class="kh" x="0" y="0">Key</text>
      <rect class="en-k" x="0" y="10" width="30" height="14"/><text x="42" y="21">Entity</text>
      <rect class="en-k assoc-k" x="0" y="32" width="30" height="14"/><text x="42" y="43">Associative entity</text>
      <polygon class="rl-k" points="0,62 15,54 30,62 15,70"/><text x="42" y="65">Relationship</text>
      ${state.erAll ? `<ellipse class="at-k" cx="15" cy="84" rx="15" ry="7"/><text x="42" y="87">Attribute</text>
      <ellipse class="at-k key" cx="15" cy="104" rx="15" ry="7"/><text x="42" y="107" class="u">Key attribute</text>` : ''}
      <line class="l total" x1="0" y1="124" x2="30" y2="124"/><line class="l-in" x1="0" y1="124" x2="30" y2="124"/><text x="42" y="127">Total participation</text><text x="0" y="146" class="u2">PK = primary key</text></g></svg>`;
  const act = (node) => node && select(node.dataset.t);
  const svg = el.querySelector('svg');
  svg.addEventListener('click', (e) => act(e.target.closest('[data-t]')));
  svg.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(e.target.closest('[data-t]')); }
  });
}
