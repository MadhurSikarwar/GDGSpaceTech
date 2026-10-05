import { csvUrl, get, qs } from '../api.js';
import { debounce, empty, errorBox, esc, fmt, h, hashQuery, loading, pager, setHashQuery, table, typeTag, TYPES } from '../ui.js';

const CENTERS = { SU: 'heliocentric', MO: 'lunar', EM: 'Earth–Moon', MA: 'Mars', VE: 'Venus', JU: 'Jupiter', SS: 'Solar System escape',
  EL1: 'Sun–Earth L1', EL2: 'Sun–Earth L2' };

export async function render(root, { app }) {
  const state = { q: '', type: '', country: '', org: '', region: '', status: 'onorbit', has_orbit: '', sort: 'norad',
    page: 1, ...hashQuery() };
  state.page = Number(state.page) || 1;

  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Object catalogue</div><h1>Satellites, rocket bodies and debris</h1>
    <p>Every catalogued object with its launch, owner and orbital region. Search by name, NORAD number or COSPAR designator.</p></div>
    <div class="row" id="exportBox"></div></div>`));
  const filters = h(`<form class="filters" autocomplete="off">
    <label class="field wide"><span>Search</span><input type="search" name="q" placeholder="ISS, 25544, 2019-081…"></label>
    <label class="field"><span>Type</span><select name="type"><option value="">All types</option>${TYPES.map((t) => `<option>${t}</option>`).join('')}</select></label>
    <label class="field"><span>Country</span><select name="country"><option value="">All countries</option></select></label>
    <label class="field"><span>Organisation</span><select name="org"><option value="">All organisations</option></select></label>
    <label class="field"><span>Orbital region</span><select name="region"><option value="">All regions</option></select></label>
    <label class="field"><span>Status</span><select name="status"><option value="onorbit">In Earth orbit</option><option value="beyond">Beyond Earth orbit</option><option value="decayed">Re-entered</option><option value="all">All</option></select></label>
    <label class="field"><span>Sort</span><select name="sort"><option value="norad">NORAD number</option><option value="name">Name</option><option value="launch">Newest launch</option><option value="perigee">Perigee</option></select></label>
    <label class="check"><input type="checkbox" name="has_orbit" value="1"> With current orbit only</label>
  </form>`);
  root.appendChild(filters);
  const results = h(`<div class="table-card">${loading()}</div>`);
  root.appendChild(results);

  for (const [k, v] of Object.entries(state)) {
    const input = filters.elements[k];
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = v === '1'; else input.value = v;
  }

  app.lookups().then((lk) => {
    const fill = (name, items, label) => {
      const sel = filters.elements[name];
      sel.insertAdjacentHTML('beforeend', items.map((i) => `<option value="${esc(i[0])}">${esc(label(i))}</option>`).join(''));
      sel.value = state[name] || '';
    };
    fill('country', lk.countries.map((c) => [c.country_code, c.name, c.objects]), (i) => `${i[1]} (${fmt.int(i[2])})`);
    fill('org', lk.organisations.map((o) => [o.org_id, o.name, o.objects]), (i) => `${i[1]} (${fmt.int(i[2])})`);
    fill('region', lk.regions.map((r) => [r.region_id, r.name]), (i) => i[1]);
  });

  if (app.can('analyst')) {
    const box = root.querySelector('#exportBox');
    box.innerHTML = '<a class="btn" id="exportBtn">Export CSV</a>';
  }

  const params = () => ({ q: state.q, type: state.type, country: state.country, org: state.org, region: state.region,
    status: state.status, has_orbit: state.has_orbit, sort: state.sort });

  let seq = 0;
  async function load() {
    const mine = ++seq;
    setHashQuery({ ...params(), page: state.page > 1 ? state.page : '' });
    const exp = root.querySelector('#exportBtn');
    if (exp) exp.href = csvUrl('/objects', params());
    results.innerHTML = loading();
    try {
      const data = await get('/objects' + qs({ ...params(), page: state.page, page_size: 50 }));
      if (mine !== seq) return;
      results.innerHTML = '';
      if (!data.items.length) { results.innerHTML = empty('No objects match these filters'); return; }
      results.appendChild(table([
        { label: 'NORAD', cls: 'idcell', render: (r) => r.norad_id },
        { label: 'Name', cls: 'name-cell', render: (r) => `${esc(r.name)}<div class="small muted mono">${esc(r.intl_designator || '')}</div>` },
        { label: 'Type', render: (r) => typeTag(r.object_type) },
        { label: 'Owner', render: (r) => (r.org_name ? `${esc(r.org_name)}<div class="small muted">${esc(r.country_name || '')}</div>` : '<span class="muted">—</span>') },
        { label: 'Launched', render: (r) => fmt.date(r.launch_date) },
        { label: 'Region', render: (r) => esc(r.region_name || (r.decay_date ? `Re-entered ${r.decay_date}`
          : r.in_earth_orbit === 0 || r.in_earth_orbit === false ? `Beyond Earth orbit (${CENTERS[r.orbit_center] || r.orbit_center || '?'})` : '—')) },
        { label: 'Perigee × apogee', num: true, render: (r) => (r.perigee_km == null ? '—' : `${fmt.int(r.perigee_km)} × ${fmt.int(r.apogee_km)} km`) },
        { label: 'Status', render: (r) => esc(r.status) },
      ], data.items, { onRow: (r) => { location.hash = `#/object/${r.norad_id}`; } }));
      results.appendChild(pager(data.total, data.page, data.page_size, (p) => { state.page = p; load(); window.scrollTo(0, 0); }));
    } catch (err) {
      if (mine === seq) results.innerHTML = errorBox(err);
    }
  }

  const onChange = () => {
    for (const k of ['q', 'type', 'country', 'org', 'region', 'status', 'sort']) state[k] = filters.elements[k].value;
    state.has_orbit = filters.elements.has_orbit.checked ? '1' : '';
    state.page = 1;
    load();
  };
  filters.addEventListener('change', onChange);
  filters.elements.q.addEventListener('input', debounce(onChange, 300));
  filters.addEventListener('submit', (e) => e.preventDefault());
  load();
}
