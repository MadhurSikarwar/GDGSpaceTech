// Administration (SRS 3.7): users & roles, reference data, watchlist, configuration,
// job status and logs, database cluster.
import { del, get, patch, post, put, qs } from '../api.js';
import { debounce, empty, errorBox, esc, fmt, h, loading, modal, objLink, pager, table, toast, typeTag } from '../ui.js';

const TABS = [['users', 'Users & roles'], ['ref', 'Reference data'], ['watchlist', 'Watchlist'], ['config', 'Configuration'],
  ['jobs', 'Jobs'], ['logs', 'Logs'], ['cluster', 'Database cluster']];
const ROLES = [['viewer', 'Public Viewer'], ['analyst', 'Analyst'], ['admin', 'Administrator']];

export async function render(root, { app }) {
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Administrator</div><h1>Administration</h1>
    <p>Every action here runs on the ow_admin MySQL account: data changes on all tables, no schema changes.</p></div></div>`));
  const tabs = h(`<div class="tabs">${TABS.map(([k, l]) => `<button data-k="${k}">${l}</button>`).join('')}</div>`);
  const body = h('<div></div>');
  root.append(tabs, body);
  let stop = null;
  const show = (k) => {
    if (stop) { stop(); stop = null; }
    tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.k === k));
    history.replaceState(null, '', `#/admin?tab=${k}`);
    // Each tab renders into its own holder, so a slow tab that finishes after the user moved on
    // draws into a detached element instead of over the current tab.
    const holder = h(`<div>${loading()}</div>`);
    body.replaceChildren(holder);
    Promise.resolve(PANELS[k](holder, app)).then((s) => {
      if (holder.isConnected) stop = s || null; else if (s) s();
    }).catch((err) => { holder.innerHTML = errorBox(err); });
  };
  tabs.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) show(b.dataset.k); });
  const initial = new URLSearchParams(location.hash.split('?')[1] || '').get('tab');
  show(TABS.some(([k]) => k === initial) ? initial : 'users');
  return () => { if (stop) stop(); };
}

const PANELS = {
  async users(body, app) {
    const draw = async (q = '') => {
      const { items } = await get('/admin/users' + qs({ q }));
      const list = body.querySelector('.list');
      list.innerHTML = '';
      if (!items.length) { list.innerHTML = empty('No users found'); return; }
      list.appendChild(table([
        { label: 'Name', render: (u) => `<strong>${esc(u.name)}</strong><div class="small muted">${esc(u.email)}</div>` },
        { label: 'Role', render: (u) => `<select data-role="${u.user_id}" ${u.user_id === app.user.user_id ? 'disabled' : ''}>${
          ROLES.map(([v, l]) => `<option value="${v}" ${u.role === v ? 'selected' : ''}>${l}</option>`).join('')}</select>` },
        { label: 'Active', render: (u) => `<label class="check"><input type="checkbox" data-active="${u.user_id}" ${u.is_active ? 'checked' : ''} ${u.user_id === app.user.user_id ? 'disabled' : ''}> ${u.is_active ? 'Active' : 'Disabled'}</label>` },
        { label: 'Subscriptions', num: true, render: (u) => fmt.int(u.subscriptions) },
        { label: 'Open alerts', num: true, render: (u) => fmt.int(u.open_alerts) },
        { label: 'Joined', render: (u) => fmt.date(u.created_at) },
        { label: '', render: (u) => (u.user_id === app.user.user_id ? '<span class="small muted">you</span>' : `<button class="btn sm danger" data-del="${u.user_id}">Delete</button>`) },
      ], items));
    };
    body.innerHTML = `<section class="card flush"><div class="card-head"><h2>Users</h2><div class="row">
      <input type="search" placeholder="Search name or email" style="width:220px" class="q"><button class="btn sm primary" id="newUser">New user</button></div></div>
      <div class="list">${loading()}</div></section>`;
    body.querySelector('.q').addEventListener('input', debounce((e) => draw(e.target.value), 300));
    body.addEventListener('change', async (e) => {
      const r = e.target.dataset.role;
      const a = e.target.dataset.active;
      try {
        if (r) { await patch(`/admin/users/${r}`, { role: e.target.value }); toast('Role updated'); }
        if (a) { await patch(`/admin/users/${a}`, { is_active: e.target.checked }); toast(e.target.checked ? 'User enabled' : 'User disabled'); draw(body.querySelector('.q').value); }
      } catch (err) { toast(err.message, 'error'); draw(body.querySelector('.q').value); }
    });
    body.addEventListener('click', async (e) => {
      const d = e.target.closest('[data-del]');
      if (d && confirm('Delete this user and their subscriptions and alerts?')) {
        try { await del(`/admin/users/${d.dataset.del}`); toast('User deleted'); draw(); } catch (err) { toast(err.message, 'error'); }
      }
      if (e.target.id === 'newUser') {
        modal('New user', `<div class="form-grid">
          <label class="field"><span>Name</span><input name="name" required></label>
          <label class="field"><span>Email</span><input name="email" type="email" required></label>
          <label class="field"><span>Initial password</span><input name="password" type="password" minlength="8" required autocomplete="new-password"></label>
          <label class="field"><span>Role</span><select name="role">${ROLES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label></div>`,
        { submitLabel: 'Create user', onSubmit: async (fd) => { await post('/admin/users', Object.fromEntries(fd)); toast('User created'); draw(); } });
      }
    });
    await draw();
  },

  async ref(body) {
    const TABLES = [['country', 'Countries'], ['organisation', 'Organisations'], ['launch_site', 'Launch sites'],
      ['launch_vehicle', 'Launch vehicles'], ['mission', 'Missions'], ['orbit_region', 'Orbital regions']];
    body.innerHTML = `<div class="filters"><label class="field"><span>Table</span><select class="tbl">${TABLES.map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select></label>
      <label class="field wide"><span>Search</span><input type="search" class="q"></label>
      <div class="field"><span>&nbsp;</span><button class="btn primary" id="addRow">Add row</button></div></div>
      <div class="table-card list">${loading()}</div>`;
    let page = 1;
    let meta = null;
    const tblSel = body.querySelector('.tbl');
    const draw = async () => {
      const data = await get(`/admin/ref/${tblSel.value}` + qs({ q: body.querySelector('.q').value, page }));
      meta = data;
      const list = body.querySelector('.list');
      list.innerHTML = '';
      if (!data.items.length) { list.innerHTML = empty('No rows'); return; }
      list.appendChild(table([...data.columns.map((c) => ({ label: c.replace(/_/g, ' '), render: (r) => esc(r[c] ?? '—') })),
        { label: '', render: (r) => `<button class="btn sm" data-edit="${esc(r[data.pk])}">Edit</button> <button class="btn sm danger" data-del="${esc(r[data.pk])}">Delete</button>` }],
      data.items));
      list.appendChild(pager(data.total, data.page, data.page_size, (p) => { page = p; draw(); }));
    };
    const form = (row = {}) => `<div class="form-grid">${meta.columns.filter((c) => !(c === meta.pk && ['launch_vehicle', 'mission', 'orbit_region'].includes(meta.table)))
      .map((c) => `<label class="field"><span>${c.replace(/_/g, ' ')}</span>${c === 'org_type'
        ? `<select name="${c}">${['Government', 'Private', 'Academic'].map((t) => `<option ${row[c] === t ? 'selected' : ''}>${t}</option>`).join('')}</select>`
        : `<input name="${c}" value="${esc(row[c] ?? '')}" ${row[c] !== undefined && c === meta.pk ? 'readonly' : ''}>`}</label>`).join('')}</div>`;
    tblSel.addEventListener('change', () => { page = 1; draw(); });
    body.querySelector('.q').addEventListener('input', debounce(() => { page = 1; draw(); }, 300));
    body.addEventListener('click', async (e) => {
      if (e.target.id === 'addRow') {
        modal(`Add to ${meta.table}`, form(), { submitLabel: 'Add', onSubmit: async (fd) => { await post(`/admin/ref/${meta.table}`, Object.fromEntries(fd)); toast('Row added'); draw(); } });
      }
      const ed = e.target.closest('[data-edit]');
      if (ed) {
        const row = meta.items.find((r) => String(r[meta.pk]) === ed.dataset.edit);
        modal(`Edit ${meta.table}`, form(row), { onSubmit: async (fd) => {
          const vals = Object.fromEntries(fd);
          delete vals[meta.pk];
          await put(`/admin/ref/${meta.table}/${encodeURIComponent(ed.dataset.edit)}`, vals);
          toast('Saved'); draw();
        } });
      }
      const dl = e.target.closest('[data-del]');
      if (dl && confirm('Delete this row? Rows still referenced elsewhere are protected by foreign keys.')) {
        try { await del(`/admin/ref/${meta.table}/${encodeURIComponent(dl.dataset.del)}`); toast('Deleted'); draw(); } catch (err) { toast(err.message, 'error'); }
      }
    });
    await draw();
  },

  async watchlist(body) {
    const draw = async () => {
      const { items } = await get('/admin/watchlist');
      const list = body.querySelector('.list');
      body.querySelector('.count').textContent = `${fmt.int(items.length)} objects`;
      list.innerHTML = '';
      if (!items.length) { list.innerHTML = empty('The watchlist is empty', ' Screening needs at least one primary object.'); return; }
      list.appendChild(table([
        { label: 'Object', render: (r) => `${objLink(r.norad_id, r.name)}<div class="small mono muted">NORAD ${r.norad_id}</div>` },
        { label: 'Type', render: (r) => typeTag(r.object_type) },
        { label: 'Country', render: (r) => esc(r.country_name || '—') },
        { label: 'Region', render: (r) => esc(r.region_name || '—') },
        { label: 'Element epoch', render: (r) => (r.epoch ? fmt.dt(r.epoch) : '<span class="muted">no current orbit</span>') },
        { label: 'Reason', render: (r) => esc(r.reason || '—') },
        { label: '', render: (r) => `<button class="btn sm danger" data-rm="${r.norad_id}">Remove</button>` },
      ], items));
    };
    body.innerHTML = `<section class="card flush"><div class="card-head"><div><h2>Screening watchlist</h2><div class="sub count"></div></div>
      <form class="row" id="addWatch"><input type="number" name="norad" min="1" placeholder="NORAD" style="width:120px" required>
      <input name="reason" placeholder="Reason (optional)" style="width:220px"><button class="btn sm primary">Add</button></form></div>
      <div class="list">${loading()}</div></section>`;
    body.querySelector('#addWatch').addEventListener('submit', async (e) => {
      e.preventDefault();
      try { await post('/admin/watchlist', { norad_id: Number(e.target.norad.value), reason: e.target.reason.value }); e.target.reset(); toast('Added to the watchlist'); draw(); } catch (err) { toast(err.message, 'error'); }
    });
    body.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-rm]');
      if (b) { try { await del(`/admin/watchlist/${b.dataset.rm}`); toast('Removed'); draw(); } catch (err) { toast(err.message, 'error'); } }
    });
    await draw();
  },

  async config(body) {
    const { items } = await get('/admin/config');
    body.innerHTML = `<section class="card"><div class="card-head"><div><h2>Configuration</h2>
      <div class="sub">Read by the jobs at run time; schedule changes are picked up by the scheduler within 10 minutes.</div></div></div>
      <form class="stack">${items.map((c) => `<label class="field"><span><span class="mono">${esc(c.config_key)}</span> — ${esc(c.description || '')}</span>
        <input name="${esc(c.config_key)}" value="${esc(c.config_value)}"><span class="small muted">last changed ${fmt.dt(c.updated_on)}${c.updated_by ? ` by ${esc(c.updated_by)}` : ''}</span></label>`).join('')}
      <div><button class="btn primary">Save changes</button></div></form></section>`;
    const form = body.querySelector('form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const changed = {};
      for (const c of items) if (form.elements[c.config_key].value !== c.config_value) changed[c.config_key] = form.elements[c.config_key].value;
      if (!Object.keys(changed).length) { toast('Nothing changed'); return; }
      try { await put('/admin/config', changed); toast('Configuration saved'); PANELS.config(body); } catch (err) { toast(err.message, 'error'); }
    });
  },

  async jobs(body) {
    const DESCR = { catalog: 'SATCAT + GCAT reference data', ingest: 'CelesTrak element sets', screening: 'close-approach screening',
      aggregation: 'MapReduce / aggregation summaries', reentry_train: 'train re-entry model', reentry_predict: 'refresh re-entry predictions',
      backup: 'MySQL + MongoDB backup', spacetrack_import: 'Space-Track history import' };
    let timer = null;
    const draw = async () => {
      const data = await get('/admin/jobs');
      const latest = Object.fromEntries(data.latest.map((j) => [j.job_name, j]));
      body.innerHTML = `<section class="card flush"><div class="card-head"><h2>Jobs</h2><span class="sub">The scheduler runs these on their own; “Run now” starts one in the web server.</span></div><div class="j"></div></section>
        <section class="card flush" style="margin-top:16px"><div class="card-head"><h2>Recent runs</h2></div><div class="r"></div></section>`;
      body.querySelector('.j').appendChild(table([
        { label: 'Job', render: (j) => `<strong class="mono">${j}</strong><div class="small muted">${DESCR[j] || ''}</div>` },
        { label: 'Last run', render: (j) => (latest[j] ? `${fmt.dt(latest[j].started_at)}<div class="small muted">${fmt.rel(latest[j].started_at)}</div>` : '<span class="muted">never</span>') },
        { label: 'Status', render: (j) => (data.running[j] ? '<span class="tag accent">running…</span>' : latest[j] ? statusTag(latest[j].status) : '—') },
        { label: 'Result', render: (j) => `<div class="small" style="max-width:520px">${esc((latest[j]?.message || '').split('\n')[0])}</div>` },
        { label: '', render: (j) => (j === 'spacetrack_import'
          ? `<select class="stmode" style="width:120px"><option value="watchlist">watchlist</option><option value="decayed">decayed</option></select> `
          : '') + `<button class="btn sm" data-run="${j}" ${data.running[j] ? 'disabled' : ''}>Run now</button>` },
      ], data.jobs));
      body.querySelector('.r').appendChild(table([
        { label: 'Run', render: (r) => `<span class="mono">#${r.run_id}</span>` }, { label: 'Job', render: (r) => `<span class="mono">${esc(r.job_name)}</span>` },
        { label: 'Trigger', render: (r) => esc(r.triggered_by) + (r.attempt > 1 ? ` · attempt ${r.attempt}` : '') },
        { label: 'Started', render: (r) => fmt.dt(r.started_at) },
        { label: 'Duration', num: true, render: (r) => (r.finished_at ? `${fmt.num((new Date(r.finished_at) - new Date(r.started_at)) / 1000, 1)} s` : '…') },
        { label: 'Status', render: (r) => statusTag(r.status) },
        { label: 'Records', num: true, render: (r) => fmt.int(r.records_processed) },
        { label: 'Message', render: (r) => `<div class="small" style="max-width:420px;white-space:pre-wrap">${esc((r.message || '').slice(0, 400))}</div>` },
      ], data.recent));
      clearTimeout(timer);
      if (Object.values(data.running).some(Boolean)) timer = setTimeout(() => draw().catch(() => {}), 4000);
    };
    body.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-run]');
      if (!b) return;
      b.disabled = true;
      const payload = b.dataset.run === 'spacetrack_import' ? { mode: body.querySelector('.stmode').value } : {};
      try { await post(`/admin/jobs/${b.dataset.run}/run`, payload); toast(`Started ${b.dataset.run}`); setTimeout(() => draw(), 800); } catch (err) { toast(err.message, 'error'); b.disabled = false; }
    });
    await draw();
    return () => clearTimeout(timer);
  },

  async logs(body) {
    body.innerHTML = `<div class="filters"><label class="field"><span>Source</span><select class="src">
      <option value="download">Download log (MongoDB)</option><option value="scheduler">Scheduler log</option>
      <option value="web">Web server log</option><option value="cli">Command-line log</option></select></label>
      <div class="field"><span>&nbsp;</span><button class="btn" id="reload">Refresh</button></div></div><div class="out">${loading()}</div>`;
    const draw = async () => {
      const src = body.querySelector('.src').value;
      const out = body.querySelector('.out');
      out.innerHTML = loading();
      const data = await get(`/admin/logs?source=${src}&limit=300`);
      out.innerHTML = '';
      if (src === 'download') {
        if (!data.items.length) { out.innerHTML = empty('No downloads logged yet'); return; }
        const card = h('<div class="table-card"></div>');
        card.appendChild(table([
          { label: 'Started', render: (d) => fmt.dt(d.started_at) }, { label: 'Source', render: (d) => `<span class="mono">${esc(d.source)}</span>` },
          { label: 'Status', render: (d) => statusTag(d.status === 'ok' || d.status === 'cache' ? 'success' : 'failed', d.status) },
          { label: 'HTTP', num: true, render: (d) => esc(d.http_status ?? '—') },
          { label: 'Bytes', num: true, render: (d) => fmt.int(d.bytes) }, { label: 'Records', num: true, render: (d) => fmt.int(d.records) },
          { label: 'Attempt', num: true, render: (d) => fmt.int(d.attempt) },
          { label: 'Error', render: (d) => `<span class="small">${esc(d.error || '')}</span>` }], data.items));
        out.appendChild(card);
      } else {
        out.appendChild(h(`<pre class="log">${esc(data.lines.join('')) || 'Empty log'}</pre>`));
      }
    };
    body.querySelector('.src').addEventListener('change', draw);
    body.querySelector('#reload').addEventListener('click', draw);
    await draw();
  },

  async cluster(body) {
    const s = await get('/admin/mongo-status');
    const sh = typeof s.sharding === 'object' && s.sharding ? s.sharding : null;
    body.innerHTML = `<div class="grid cols-2">
      <section class="card"><div class="card-head"><h2>Processes</h2><span class="sub">local sharded cluster</span></div>
        <dl class="facts">${Object.entries(s.processes).map(([k, up]) => `<dt class="mono">${esc(k)}</dt><dd class="${up ? 'status-ok' : 'status-bad'}">${up ? '● running' : '○ down'}</dd>`).join('')}</dl></section>
      <section class="card"><div class="card-head"><h2>Replica sets</h2></div>
        ${Object.entries(s.replica_sets).map(([rs, m]) => `<h3 style="margin:8px 0 6px">${esc(rs)}</h3>${typeof m === 'string' ? `<p class="small">${esc(m)}</p>`
          : `<dl class="facts">${Object.entries(m).map(([host, st]) => `<dt class="mono">${esc(host)}</dt><dd>${st === 'PRIMARY' ? '<strong>PRIMARY</strong>' : esc(st)}</dd>`).join('')}</dl>`}`).join('')}</section>
      <section class="card"><div class="card-head"><h2>Sharding</h2></div>
        ${sh ? `<dl class="facts"><dt>Collection</dt><dd class="mono">${esc(sh.collection)}</dd><dt>Shard key</dt><dd class="mono">${esc(JSON.stringify(sh.shard_key))}</dd>
          ${Object.entries(sh.documents).map(([k, n]) => `<dt>${esc(k)}</dt><dd>${fmt.int(n)} documents · ${fmt.int(sh.chunks[k] || 0)} chunk(s)</dd>`).join('')}</dl>`
          : `<p class="small">${esc(s.sharding || 'unavailable')}</p>`}
        <p class="small muted" style="margin-top:12px">Stop a shard's primary (e.g. <span class="mono">mongosh --port 27101</span>, then <span class="mono">db.adminCommand({shutdown:1})</span>)
        and refresh: a secondary is elected and the application keeps working.</p></section></div>`;
  },
};

function statusTag(status, label) {
  const cls = { success: 'risk-LOW', skipped: '', failed: 'risk-CRITICAL', running: 'accent' }[status] ?? '';
  return `<span class="badge ${cls}"><i></i>${esc(label || status)}</span>`;
}
