import { csvUrl, get, qs } from '../api.js';
import { altitudeBar } from '../diagrams.js';
import { debounce, empty, errorBox, esc, fmt, h, hashQuery, loading, pager, setHashQuery, table, typeTag, TYPE_SLUG, TYPES } from '../ui.js';

const CENTERS = { SU: 'heliocentric', MO: 'lunar', EM: 'Earth–Moon', MA: 'Mars', VE: 'Venus', JU: 'Jupiter', SS: 'Solar System escape',
  EL1: 'Sun–Earth L1', EL2: 'Sun–Earth L2' };
const SORTS = { norad: 'NORAD number', name: 'name', launch: 'newest launch', perigee: 'perigee' };
const STATUS = { onorbit: 'in Earth orbit', beyond: 'beyond Earth orbit', decayed: 're-entered', all: 'all objects' };

export async function render(root, { app }) {
  const state = { q: '', type: '', country: '', org: '', region: '', status: 'onorbit', has_orbit: '', sort: 'norad',
    page: 1, ...hashQuery() };
  state.page = Number(state.page) || 1;

  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Object catalogue</div><h1>Satellites, rocket bodies and debris</h1>
    <p>Every catalogued object with its launch, owner and orbital region. Search by name, NORAD number or COSPAR designator.</p></div>
    <div class="row" id="exportBox"></div></div>`));
  const filters = h(`<form class="filters" autocomplete="off">
    <input type="hidden" name="type">
    <label class="field wide"><span>Search</span><input type="search" name="q" placeholder="ISS, 25544, 2019-081…"></label>
    <label class="field"><span>Country</span><select name="country"><option value="">All countries</option></select></label>
    <label class="field"><span>Organisation</span><select name="org"><option value="">All organisations</option></select></label>
    <label class="field"><span>Orbital region</span><select name="region"><option value="">All regions</option></select></label>
    <label class="field"><span>Status</span><select name="status"><option value="onorbit">In Earth orbit</option><option value="beyond">Beyond Earth orbit</option><option value="decayed">Re-entered</option><option value="all">All</option></select></label>
    <label class="field"><span>Sort</span><select name="sort"><option value="norad">NORAD number</option><option value="name">Name</option><option value="launch">Newest launch</option><option value="perigee">Perigee</option></select></label>
    <label class="check"><input type="checkbox" name="has_orbit" value="1"> With current orbit only</label>
  </form>`);
  root.appendChild(filters);
  // the type is a row of chips with live counts (the counts follow every other filter); the form keeps it in a hidden field
  const typeChips = h(`<div class="chips type-chips" role="group" aria-label="Object type">${['', ...TYPES].map((t) => `<button type="button" class="chip" data-type="${esc(t)}" aria-pressed="false">${
    t ? `<i class="tdot t-${TYPE_SLUG[t]}"></i>` : ''}${esc(t || 'All types')}<span class="n"></span></button>`).join('')}</div>`);
  root.appendChild(typeChips);
  const bar = h(`<div class="result-bar"><div class="result-count" id="count" aria-live="polite">Loading…</div><div class="active-filters" id="active"></div></div>`);
  root.appendChild(bar);
  const results = h(`<div class="table-card">${loading()}</div>`);
  root.appendChild(results);

  const syncForm = () => {
    for (const [k, v] of Object.entries(state)) {
      const input = filters.elements[k];
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = v === '1'; else input.value = v;
    }
    typeChips.querySelectorAll('.chip').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.type === state.type)));
  };
  syncForm();

  app.lookups().then((lk) => {
    const fill = (name, items, label) => {
      const sel = filters.elements[name];
      sel.insertAdjacentHTML('beforeend', items.map((i) => `<option value="${esc(i[0])}">${esc(label(i))}</option>`).join(''));
      sel.value = state[name] || '';
    };
    fill('country', lk.countries.map((c) => [c.country_code, c.name, c.objects]), (i) => `${i[1]} (${fmt.int(i[2])})`);
    fill('org', lk.organisations.map((o) => [o.org_id, o.name, o.objects]), (i) => `${i[1]} (${fmt.int(i[2])})`);
    fill('region', lk.regions.map((r) => [r.region_id, r.name]), (i) => i[1]);
    paintActive();
  });

  if (app.can('analyst')) {
    const box = root.querySelector('#exportBox');
    box.innerHTML = '<a class="btn" id="exportBtn">Export CSV</a>';
  }

  const params = () => ({ q: state.q, type: state.type, country: state.country, org: state.org, region: state.region,
    status: state.status, has_orbit: state.has_orbit, sort: state.sort });

  // ---- the line above the table: how many, in what order, and the filters in force (each one clears with a click) ----
  const chosen = (name) => (filters.elements[name].selectedOptions[0]?.textContent || state[name]).split(' (')[0];
  function paintActive(total) {
    if (total !== undefined) {
      bar.querySelector('#count').innerHTML = `<b>${fmt.int(total)}</b>${total === 1 ? 'object' : 'objects'}<span class="muted"> · ${STATUS[state.status]} · by ${SORTS[state.sort]}</span>`;
    }
    const active = [];
    if (state.q) active.push(['q', `“${state.q}”`]);
    if (state.type) active.push(['type', state.type]);
    if (state.country) active.push(['country', chosen('country')]);
    if (state.org) active.push(['org', chosen('org')]);
    if (state.region) active.push(['region', chosen('region')]);
    if (state.status !== 'onorbit') active.push(['status', STATUS[state.status]]);
    if (state.has_orbit) active.push(['has_orbit', 'with a current orbit']);
    bar.querySelector('#active').innerHTML = active.map(([k, label]) => `<button type="button" class="fchip" data-clear="${k}" title="Remove this filter">${esc(label)}<span aria-hidden="true">×</span></button>`).join('')
      + (active.length > 1 ? '<button type="button" class="fchip clear" data-clear="*">Clear all</button>' : '');
  }
  bar.addEventListener('click', (e) => {
    const key = e.target.closest('[data-clear]')?.dataset.clear;
    if (!key) return;
    const reset = { q: '', type: '', country: '', org: '', region: '', status: 'onorbit', has_orbit: '' };
    if (key === '*') Object.assign(state, reset); else state[key] = reset[key];
    state.page = 1;
    syncForm();
    load();
  });
  typeChips.addEventListener('click', (e) => {
    const b = e.target.closest('.chip');
    if (!b) return;
    state.type = state.type === b.dataset.type ? '' : b.dataset.type;      // a second click on the chosen chip shows every type again
    state.page = 1;
    syncForm();
    load();
  });
  const paintFacets = (facets) => {
    if (!facets?.type) return;
    const all = Object.values(facets.type).reduce((a, b) => a + b, 0);
    typeChips.querySelectorAll('.chip').forEach((b) => {
      const n = b.dataset.type ? facets.type[b.dataset.type] || 0 : all;
      b.querySelector('.n').textContent = fmt.int(n);
      b.classList.toggle('none', n === 0 && b.dataset.type !== state.type);
    });
  };

  let seq = 0;
  async function load() {
    const mine = ++seq;
    setHashQuery({ ...params(), page: state.page > 1 ? state.page : '' });
    const exp = root.querySelector('#exportBtn');
    if (exp) exp.href = csvUrl('/objects', params());
    results.innerHTML = loading();
    paintActive();
    try {
      // the counts on the type chips are a GROUP BY over every row: they are asked for beside the table, which never waits for them
      get('/objects/facets' + qs(params())).then((f) => { if (mine === seq) paintFacets(f); }).catch(() => {});
      const data = await get('/objects' + qs({ ...params(), page: state.page, page_size: 50 }));
      if (mine !== seq) return;
      paintActive(data.total);
      results.innerHTML = '';
      if (!data.items.length) {
        results.innerHTML = empty('No objects match these filters', ' Try removing one of the filters above.');
        return;
      }
      results.appendChild(table([
        { label: 'NORAD', cls: 'idcell', sort: 'norad', render: (r) => r.norad_id },
        { label: 'Name', cls: 'name-cell', sort: 'name', render: (r) => `${esc(r.name)}<div class="small muted mono">${esc(r.intl_designator || '')}</div>` },
        { label: 'Type', render: (r) => typeTag(r.object_type) },
        { label: 'Owner', render: (r) => (r.org_name ? `${esc(r.org_name)}<div class="small muted">${esc(r.country_name || '')}</div>` : '<span class="muted">—</span>') },
        { label: 'Launched', sort: 'launch', sortDir: 'descending', render: (r) => fmt.date(r.launch_date) },
        { label: 'Region', render: (r) => esc(r.region_name || (r.decay_date ? `Re-entered ${r.decay_date}`
          : r.in_earth_orbit === 0 || r.in_earth_orbit === false ? `Beyond Earth orbit (${CENTERS[r.orbit_center] || r.orbit_center || '?'})` : '—')) },
        { label: 'Perigee × apogee', num: true, sort: 'perigee', render: (r) => (r.perigee_km == null ? '—'
          : `${fmt.int(r.perigee_km)} × ${fmt.int(r.apogee_km)} km${altitudeBar(r.perigee_km, r.apogee_km)}`) },
        { label: 'Status', render: (r) => esc(r.status) },
        { label: '', cls: 'rowgo-cell', render: (r) => (r.perigee_km == null ? '' : `<a class="rowgo" href="#/globe?norad=${r.norad_id}" title="Show on the globe" aria-label="Show ${esc(r.name)} on the globe"><svg aria-hidden="true"><use href="#i-globe"/></svg></a>`) },
      ], data.items, { sort: state.sort, onSort: (key) => { state.sort = key; state.page = 1; syncForm(); load(); }, onRow: (r) => { location.hash = `#/object/${r.norad_id}`; } }));
      results.appendChild(pager(data.total, data.page, data.page_size, (p) => { state.page = p; load(); window.scrollTo(0, 0); }));
    } catch (err) {
      if (mine === seq) results.innerHTML = errorBox(err);
    }
  }

  const onChange = () => {
    for (const k of ['q', 'country', 'org', 'region', 'status', 'sort']) state[k] = filters.elements[k].value;
    state.has_orbit = filters.elements.has_orbit.checked ? '1' : '';
    state.page = 1;
    load();
  };
  filters.addEventListener('change', onChange);
  filters.elements.q.addEventListener('input', debounce(onChange, 300));
  filters.addEventListener('submit', (e) => e.preventDefault());
  load();
}
