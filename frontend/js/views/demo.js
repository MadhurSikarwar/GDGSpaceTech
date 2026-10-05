// Synthetic-debris demo lab (restored from OrbitalGuard's "inject synthetic debris" demo).
// Creates a guaranteed close approach against a real satellite so the whole pipeline — Pc, the
// decision agent, human approval and the simulated burn — can be demonstrated on demand.
// Synthetic objects live only in the demo tables and are labelled SYNTHETIC everywhere.
import { del, get, post, qs } from '../api.js';
import { agentPanel } from '../agent-panel.js';
import { term } from '../drawers.js';
import { onLive } from '../live.js';
import { debounce, empty, errorBox, esc, fmt, h, hashQuery, loading, riskBadge, setHashQuery, synTag, toast } from '../ui.js';

export async function render(root, { app }) {
  const q = hashQuery();
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Demonstration</div><h1>Synthetic-debris demo lab</h1>
    <p>Inject a piece of ${term('synthetic', 'synthetic debris')} on a crossing orbit to create a guaranteed close approach with a real satellite,
    then run the decision agent, review its recommendation and approve or reject the burn — the full pipeline, on demand.</p></div>
    <div class="row"><button class="btn danger" id="resetAll" disabled>Reset demo</button></div></div>`));
  root.appendChild(h(`<div class="banner syn"><svg class="icon"><use href="#i-flask"/></svg><div>
    <strong>Synthetic data, kept apart.</strong> Synthetic objects carry <span class="mono">SYN-</span> designations instead of NORAD numbers and are stored only in
    the demo tables. They never appear in the catalogue, statistics, reports, alerts or exports. Their orbits are real SGP4 element sets fitted to the planned
    encounter, so screening, ${term('pc', 'Pc')}, the agent and the burn simulation run unchanged.</div></div>`));
  const top = h(`<div class="demo-hero">
    <section class="card bezel"><div class="card-head"><h2>Inject synthetic debris</h2><span class="sub">analyst role</span></div>
      <form id="injForm" class="stack" autocomplete="off">
        <label class="field"><span>Target satellite (real catalogue object)</span>
          <input type="search" name="target" placeholder="ISS, CARTOSAT, 25544…" value="ISS (ZARYA) · 25544">
          <input type="hidden" name="target_norad" value="25544"><div class="search-results" id="tgtResults"></div></label>
        <div class="grid cols-3">
          <label class="field"><span>Encounter in (hours)</span><input type="number" name="lead_h" min="1" max="24" step="0.5" value="6"></label>
          <label class="field"><span>${term('miss', 'Miss distance')} (km)</span><input type="number" name="miss_km" min="0.02" max="5" step="0.01" value="0.25"></label>
          <label class="field"><span>Crossing angle (°)</span><input type="number" name="crossing_deg" min="10" max="170" step="5" value="70"></label>
        </div>
        <p class="small muted" style="margin:0">Six hours leaves room for burns one to six half-orbits before the encounter and for an uplink pass before them.</p>
        <div class="form-error"></div>
        <div class="row"><button class="btn primary" type="submit" id="injBtn">Inject synthetic debris</button></div>
      </form></section>
    <section class="card"><div class="card-head"><h2>How the demo works</h2></div>
      <ol class="small" style="margin:0;padding-left:18px;line-height:1.75;color:var(--ink-2)">
        <li>The target's real current orbit is propagated with SGP4 to the encounter time.</li>
        <li>A debris state is placed there: the target's velocity turned by the crossing angle, offset radially by the miss distance.</li>
        <li>SGP4 mean elements are fitted iteratively until SGP4 itself reproduces that state to under a metre.</li>
        <li>Screening refinement verifies the encounter; Pc uses the same covariance model as real events.</li>
        <li>Run the AI assessment, then approve (simulated execution) or reject with feedback and re-plan.</li>
        <li>Replay it on the globe, including the burn and the before/after orbits.</li></ol></section></div>`);
  root.appendChild(top);
  const listCard = h(`<section class="card" style="margin-top:14px"><div class="card-head"><h2>Active scenarios</h2>
      <label class="check"><input type="checkbox" id="showArchived"> show archived OrbitalGuard demo data</label></div>
    <div id="scList">${loading()}</div></section>`);
  root.appendChild(listCard);
  const detail = h('<div style="margin-top:14px"></div>');
  root.appendChild(detail);
  const $ = (s) => root.querySelector(s);

  const canEdit = app.can('analyst');
  if (!canEdit) {
    $('#injBtn').disabled = true;
    $('#injBtn').textContent = app.user ? 'Analyst role needed' : 'Log in as an analyst';
  }

  // ---- target search ------------------------------------------------------
  const form = $('#injForm');
  const results = $('#tgtResults');
  form.elements.target.addEventListener('input', debounce(async () => {
    const s = form.elements.target.value.trim();
    if (s.length < 2) { results.innerHTML = ''; return; }
    try {
      const { items } = await get(`/objects${qs({ q: s, status: 'onorbit', has_orbit: '1', page_size: 8 })}`);
      results.innerHTML = items.map((o) => `<button type="button" data-id="${o.norad_id}" data-name="${esc(o.name)}">${esc(o.name)}
        <span class="muted mono">${o.norad_id} · ${fmt.int(o.perigee_km)}×${fmt.int(o.apogee_km)} km</span></button>`).join('')
        || '<div class="small muted" style="padding:6px 8px">No object in Earth orbit with a current orbit matches.</div>';
    } catch { results.innerHTML = ''; }
  }, 250));
  results.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-id]');
    if (!b) return;
    form.elements.target_norad.value = b.dataset.id;
    form.elements.target.value = `${b.dataset.name} · ${b.dataset.id}`;
    results.innerHTML = '';
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = form.querySelector('.form-error');
    err.textContent = '';
    $('#injBtn').disabled = true;
    $('#injBtn').textContent = 'Fitting SGP4 elements…';
    try {
      const sc = await post('/demo', {
        target_norad: Number(form.elements.target_norad.value), lead_min: Number(form.elements.lead_h.value) * 60,
        miss_km: Number(form.elements.miss_km.value), crossing_deg: Number(form.elements.crossing_deg.value),
      });
      toast(`Synthetic debris ${sc.objects[0].designation} injected`);
      await load();
      if (sc.events[0]) openEvent(sc.events[0].demo_event_id);
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      $('#injBtn').disabled = !canEdit;
      $('#injBtn').textContent = 'Inject synthetic debris';
    }
  });

  // ---- scenarios -------------------------------------------------------------
  let stopAgent = null;
  async function openEvent(demoEventId, assessmentId) {
    if (stopAgent) stopAgent();
    setHashQuery({ event: demoEventId });
    detail.innerHTML = '';
    stopAgent = await agentPanel(detail, { demoEventId }, app, { assessmentId });
    detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function load() {
    const el = $('#scList');
    try {
      const { items } = await get(`/demo${$('#showArchived').checked ? '?archived=1' : ''}`);
      const active = items.filter((s) => s.status === 'active');
      $('#resetAll').disabled = !canEdit || !active.length;
      if (!items.length) { el.innerHTML = empty('No active scenario', ' Inject synthetic debris to create one.'); return; }
      el.innerHTML = items.map((s) => s.status === 'active' ? `<div class="scenario active">
          <div class="sc-head"><div class="row">${synTag()}<strong>${esc(s.objects?.[0]?.designation || '')}</strong>
            <span class="muted">crossing</span><a href="#/object/${s.target_norad}">${esc(s.target_name)}</a></div>
            <div class="row">${canEdit ? `<button class="btn sm danger" data-clear="${s.scenario_id}">Clear</button>` : ''}</div></div>
          ${(s.events || []).map((ev) => `<div class="spread" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--hair)">
            <div class="row small"><span class="mono">TCA ${fmt.dt(ev.time_of_closest_approach)}</span><span class="muted">${fmt.rel(ev.time_of_closest_approach)}</span>
              <span class="mono">${fmt.num(ev.miss_distance_km, 3)} km</span><span class="mono">${fmt.num(ev.relative_velocity, 2)} km/s</span>
              <span class="mono">Pc ${ev.probability_of_collision == null ? '—' : Number(ev.probability_of_collision).toExponential(1)}</span>${riskBadge(ev.risk_level)}</div>
            <div class="row"><a class="btn sm" href="#/globe?demo_event=${ev.demo_event_id}">Replay in 3D</a>
              <button class="btn sm primary" data-open="${ev.demo_event_id}">Decision support →</button></div></div>`).join('')}
          <p class="small muted" style="margin:8px 0 0">${esc(s.notes || '')} · created ${fmt.rel(s.created_at)}</p></div>`
        : `<div class="scenario"><div class="sc-head"><div class="row">${synTag()}<span class="tag plain">archived</span>
            <span>${esc(s.target_name)}</span><span class="muted small">${fmt.int(s.events)} synthetic events · ${fmt.dt(s.created_at)}</span></div></div>
            <p class="small muted" style="margin:6px 0 0">${esc(s.notes || '')}</p></div>`).join('');
    } catch (err) { el.innerHTML = errorBox(err); }
  }

  $('#scList').addEventListener('click', async (e) => {
    const open = e.target.closest('[data-open]');
    if (open) { openEvent(Number(open.dataset.open)); return; }
    const clear = e.target.closest('[data-clear]');
    if (clear && confirm('Clear this scenario? Its synthetic objects and events are deleted.')) {
      try { await del(`/demo/${clear.dataset.clear}`); toast('Scenario cleared'); detail.innerHTML = ''; await load(); } catch (err) { toast(err.message, 'error'); }
    }
  });
  $('#showArchived').addEventListener('change', load);
  $('#resetAll').addEventListener('click', async () => {
    if (!confirm('Reset the demo? Every active synthetic object and event is deleted.')) return;
    try { const r = await post('/demo/clear'); toast(`${r.cleared} scenario${r.cleared === 1 ? '' : 's'} cleared`); detail.innerHTML = ''; await load(); } catch (err) { toast(err.message, 'error'); }
  });

  await load();
  if (q.assessment) {
    try {
      const a = await get(`/assessments/${Number(q.assessment)}`);
      if (a.demo_event_id) openEvent(a.demo_event_id, a.assessment_id);
    } catch (err) { detail.innerHTML = errorBox(err); }
  } else if (q.event) openEvent(Number(q.event));
  const off = onLive((type, d) => { if (type === 'log' && d.category === 'demo') load(); });
  return () => { off(); if (stopAgent) stopAgent(); };
}
