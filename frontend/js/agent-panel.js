// AI decision support panel: runs the decision agent on one close approach (real or SYNTHETIC demo),
// shows its trace live, the recommendation, and the human review: approve (simulated execution)
// or reject with feedback and an optional immediate re-plan.
import { get, post } from './api.js';
import { term } from './drawers.js';
import { esc, fmt, h, modal, riskBadge, simTag, synTag, toast } from './ui.js';

const DECISION_LABEL = {
  MANEUVER_RECOMMENDED: 'Manoeuvre recommended', MONITOR: 'Monitor — no manoeuvre',
  NO_FEASIBLE_MANEUVER: 'No feasible manoeuvre', DATA_UNAVAILABLE: 'Data unavailable',
};
const pc = (v) => (v == null ? '—' : Number(v).toExponential(1));

// subject: an event id (number) or { eventId } / { demoEventId }
export async function agentPanel(container, subject, app, { assessmentId } = {}) {
  const s = typeof subject === 'object' ? subject : { eventId: subject };
  const synthetic = !!s.demoEventId;
  const base = synthetic ? `/demo/events/${s.demoEventId}` : `/conjunctions/${s.eventId}`;
  const el = h(`<section class="card agent bezel">
    <div class="card-head"><div><h2>AI decision support ${synthetic ? synTag() : ''}</h2>
      <div class="sub">An LLM agent chooses deterministic tools (SGP4, Foster ${term('pc', 'Pc')}, ${term('cw', 'Clohessy–Wiltshire')} optimiser, ground stations), reads the results and recommends what to do. Guardrails reject anything the physics does not support; every burn needs ${term('approval', 'human approval')}.</div></div>
      <div class="row"><select id="agHistory" class="hidden" aria-label="Earlier assessments" style="width:auto;height:28px"></select>
        <span class="tag plain" id="agEngine">—</span><button class="btn primary sm" id="agRun">Run AI assessment</button></div></div>
    <div id="agBody"><div class="muted small mono">No assessment yet for this event.</div></div></section>`);
  container.appendChild(el);
  const runBtn = el.querySelector('#agRun');
  const body = el.querySelector('#agBody');
  const hist = el.querySelector('#agHistory');
  let timer = null;
  let stopped = false;
  let current = null;

  try {
    const st = await get('/agent/status');
    el.querySelector('#agEngine').textContent = st.llm_enabled ? 'LLM · Groq' : 'Deterministic';
    if (!app.can('analyst')) {
      runBtn.disabled = true;
      runBtn.title = 'Running the agent needs the Analyst role';
      runBtn.textContent = app.user ? 'Analyst role needed' : 'Log in as an analyst';
    }
  } catch { /* status is informational */ }

  const loadHistory = async (selectId) => {
    try {
      const { items } = await get(`${base}/assessments`);
      if (items.length > 1) {
        hist.classList.remove('hidden');
        hist.innerHTML = items.map((a) => `<option value="${a.assessment_id}">#${a.assessment_id} · ${esc((a.decision || a.status).replace(/_/g, ' ').toLowerCase())} · ${fmt.dt(a.started_at).slice(5, 16)}</option>`).join('');
      }
      const id = selectId || items[0]?.assessment_id;
      if (id) { hist.value = String(id); show(id); }
    } catch { /* none yet */ }
  };
  hist.addEventListener('change', () => show(Number(hist.value)));

  async function show(id) {
    if (stopped) return;
    clearTimeout(timer);
    try {
      const a = await get(`/assessments/${id}`);
      current = a;
      render(body, a, app);
      el.querySelector('#agEngine').textContent = a.engine.startsWith('groq:') ? `LLM · ${a.engine.slice(5)}` : a.engine;
      if (a.status === 'running') { timer = setTimeout(() => show(id), 900); runBtn.disabled = true; } else if (app.can('analyst')) runBtn.disabled = false;
    } catch (err) { body.innerHTML = `<div class="error-box">${esc(err.message)}</div>`; }
  }

  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    body.innerHTML = '<div class="loading">Starting the agent…</div>';
    try {
      const r = await post(`${base}/assess`);
      await loadHistory(r.assessment_id);
    } catch (err) { toast(err.message, 'error'); runBtn.disabled = false; }
  });

  body.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-decide]');
    if (!b || !current) return;
    if (b.dataset.decide === 'approve') approveDialog(current, async () => { await loadHistory(current.assessment_id); });
    if (b.dataset.decide === 'reject') rejectDialog(current, async (res) => { await loadHistory(res.replan_assessment_id || current.assessment_id); });
  });

  await loadHistory(assessmentId);
  return () => { stopped = true; clearTimeout(timer); };
}

function decisionBox(a, app) {
  const d = a.decision_record;
  const m = a.maneuver;
  if (d) {
    const ok = d.status === 'APPROVED';
    const cand = d.candidate || m || {};
    return `<div class="decision-box ${ok ? 'approved' : 'rejected'}">
      <div class="db-head"><div class="row"><strong>${ok ? 'Approved' : 'Rejected'}</strong>${ok ? simTag() : ''}
        <span class="small muted">${esc(fmt.dt(d.decided_at))}${d.decided_by_name ? ` · by ${esc(d.decided_by_name)}` : ''}${d.origin === 'orbitalguard-archive' ? ' · OrbitalGuard archive' : ''}</span></div>
        ${ok && a.origin === 'orbitwatch' ? `<a class="btn sm accent" href="#/globe?assessment=${a.assessment_id}">View simulated burn in 3D</a>` : ''}</div>
      <div class="db-body">
        ${ok ? `<dl class="sim-compare">
          <div><dt>Δv</dt><dd>${fmt.num(d.delta_v_mps ?? cand.dv_mps, 3)} m/s</dd></div>
          <div><dt>Burn (IST · UTC)</dt><dd>${esc(fmt.dt(d.burn_time || cand.burn_time_utc || ''))}</dd></div>
          <div><dt>Miss before</dt><dd>${d.miss_before_km == null ? '—' : `${fmt.num(d.miss_before_km, 3)} km`}</dd></div>
          <div><dt>Miss after</dt><dd class="up">${d.miss_after_km == null ? '—' : `${fmt.num(d.miss_after_km, 3)} km`}</dd></div></dl>
          <p class="small muted" style="margin:0">OrbitWatch has no command uplink: this burn was executed in simulation only (SGP4 nominal orbit plus the linearised Clohessy–Wiltshire effect of the impulse).</p>`
          : `<p style="margin:0"><span class="muted small mono">REASON</span><br>${esc(d.reason || '—')}</p>`}
        ${a.replans?.length ? `<p class="small" style="margin:10px 0 0">Re-planned: ${a.replans.map((r) => `#${r.assessment_id} (${esc((r.decision || r.status).replace(/_/g, ' ').toLowerCase())})`).join(', ')}</p>` : ''}
      </div></div>`;
  }
  if (a.status !== 'complete' || a.decision !== 'MANEUVER_RECOMMENDED' || !m) return '';
  if (!a.can_decide) {
    return `<div class="decision-box"><div class="db-head"><strong>Awaiting human review</strong><span class="tag warn">pending</span></div>
      <div class="db-body small muted">${app.can('analyst') ? 'This assessment cannot be decided on.' : 'An analyst approves or rejects recommended manoeuvres.'}</div></div>`;
  }
  const burnPassed = m.burn_time_utc && new Date(m.burn_time_utc) <= new Date(Date.now() + 120000);
  return `<div class="decision-box"><div class="db-head"><div class="row"><strong>Human review</strong><span class="tag warn">awaiting decision</span></div>
      <span class="small muted">burn ${esc(fmt.rel(m.burn_time_utc))}</span></div>
    <div class="db-body"><p class="small" style="margin:0">Approve to execute the recommended burn <b>in simulation</b> (OrbitWatch has no uplink), or reject it with your reasons — optionally asking the agent to re-plan with a larger miss distance.</p>
      <div class="db-actions">
        <button class="btn approve" data-decide="approve" ${burnPassed ? 'disabled title="The burn time has passed: run a new assessment"' : ''}>Approve burn…</button>
        <button class="btn danger" data-decide="reject">Reject…</button></div></div></div>`;
}

function render(body, a, app) {
  const steps = a.steps || [];
  const gen = [...steps].reverse().find((s) => s.tool_name === 'generate_maneuver_candidates' && s.payload);
  const evals = {};
  steps.filter((s) => s.tool_name === 'evaluate_maneuver_constraints' && s.payload).forEach((s) => { evals[s.payload.candidate_id] = s.payload; });
  const m = a.maneuver;
  const review = a.feedback || a.min_miss_km ? `<div class="banner info"><svg class="icon"><use href="#i-info"/></svg><div>
      <strong>Re-plan after a rejection</strong> of assessment #${a.parent_assessment_id || '—'}${a.feedback ? `: “${esc(a.feedback)}”` : ''}${a.min_miss_km ? ` · required miss ≥ ${fmt.num(a.min_miss_km, 2)} km` : ''}</div></div>` : '';
  const syn = a.synthetic ? `<div class="banner syn"><svg class="icon"><use href="#i-flask"/></svg><div><strong>Synthetic demo event.</strong>
      The other object is injected synthetic debris, not a real catalogue object. Everything else (SGP4, Pc, optimiser, guardrails) is the real pipeline.</div></div>` : '';
  const decision = a.status === 'running' ? '' : `<div class="agent-decision ${a.decision === 'MANEUVER_RECOMMENDED' ? 'act' : ''}">
      <div class="spread"><div><div class="k">Recommendation · assessment #${a.assessment_id}</div><div class="d">${esc(DECISION_LABEL[a.decision] || a.status)}</div></div>
        <div class="row">${a.risk_tier ? riskBadge(a.risk_tier) : ''}${a.human_approval_required ? '<span class="tag accent">Human approval required</span>' : ''}</div></div>
      ${m ? `<dl class="agent-metrics">
        <div><dt>${term('dv', 'Δv')}</dt><dd>${fmt.num(m.dv_mps, 3)} m/s</dd></div><div><dt>Direction</dt><dd>${esc(m.direction)}</dd></div>
        <div><dt>Burn</dt><dd>${esc(fmt.dt(m.burn_time_utc || ''))}</dd></div><div><dt>Lead</dt><dd>T−${fmt.num(m.lead_time_min, 0)} min</dd></div>
        <div><dt>${term('pc', 'Pc')}</dt><dd>${pc(a.pc_before)} → ${pc(a.pc_after)}</dd></div><div><dt>${term('miss', 'Miss after')}</dt><dd>${m.miss_after_km == null ? '—' : `${fmt.num(m.miss_after_km, 2)} km`}</dd></div></dl>`
        : (a.pc_before != null ? `<dl class="agent-metrics"><div><dt>Pc</dt><dd>${pc(a.pc_before)}</dd></div></dl>` : '')}
      <p>${esc(a.explanation || '')}</p>
      <div class="small mono muted">${esc(a.engine)} · ${fmt.dt(a.finished_at || a.started_at)}</div></div>`;
  const cands = gen ? `<div class="table-wrap"><table class="data agent-cands"><thead><tr><th>Candidate</th><th class="num">Δv m/s</th><th>Direction</th>
      <th class="num">Lead</th><th class="num">Pc after</th><th class="num">Miss after</th><th class="num">${term('drift', 'Drift')} km</th><th>Constraints</th></tr></thead><tbody>
      ${gen.payload.candidates.map((c) => {
        const e = evals[c.candidate_id];
        const chosen = m && m.candidate_id === c.candidate_id;
        return `<tr class="${chosen ? 'chosen' : ''}"><td class="mono">${c.candidate_id}${chosen ? ' ◀' : ''}</td><td class="num">${fmt.num(c.dv_mps, 3)}</td>
          <td>${esc(c.direction)}</td><td class="num">T−${fmt.num(c.lead_time_min, 0)}m</td><td class="num">${pc(c.pc_after)}</td>
          <td class="num">${fmt.num(c.miss_after_km, 2)}</td><td class="num">${fmt.num(c.sma_drift_km, 2)}</td><td>${e ? (e.feasible ? '<span class="status-ok mono small">✓ feasible</span>'
            : `<span class="status-bad mono small">✗ ${esc(e.violations.join(', '))}</span>`) : '<span class="muted small">not evaluated</span>'}</td></tr>`;
      }).join('')}</tbody></table></div>` : '';
  body.innerHTML = `${syn}${review}${decision}${decisionBox(a, app)}${cands}
    <div class="agent-trace-head mono small"><span>Agent trace</span><span>${steps.length} steps${a.status === 'running' ? ' · running…' : ''}</span></div>
    <ol class="agent-trace">${steps.map((s) => `<li class="a-${s.actor}">
      <span class="n">${String(s.step_no).padStart(2, '0')}</span><span class="who">${s.actor}${s.tool_name ? ` · ${esc(s.tool_name)}` : ''}</span>
      <span class="what">${esc(s.summary)}</span></li>`).join('')}${a.status === 'running' ? '<li class="a-system pulse"><span class="n">··</span><span class="who">agent</span><span class="what">thinking…</span></li>' : ''}</ol>`;
}

async function approveDialog(a, done) {
  const m = a.maneuver;
  const subj = a.subject || {};
  const dlg = modal('Approve avoidance burn', `
    ${a.synthetic ? '<div class="banner syn" style="margin:0"><div><strong>Synthetic demo event.</strong> The other object is not real.</div></div>' : ''}
    <ul class="confirm-list">
      <li><span>Satellite</span><span>${esc(subj.primary_name || '')}</span></li>
      <li><span>Avoiding</span><span>${esc(subj.secondary_name || '')}</span></li>
      <li><span>Δv · direction</span><span>${fmt.num(m.dv_mps, 3)} m/s · ${esc(m.direction)}</span></li>
      <li><span>Burn time</span><span>${fmt.dt(m.burn_time_utc)} (${esc(fmt.rel(m.burn_time_utc))})</span></li>
      <li><span>Pc</span><span>${pc(a.pc_before)} → ${pc(m.pc_after)}</span></li>
      <li><span>Miss distance</span><span id="simMiss">computing simulation…</span></li>
      <li><span>Closest approach after burn</span><span id="simClosest">…</span></li></ul>
    <div class="banner info" style="margin:0"><div>${'<span class="tag sim">SIMULATED</span>'} OrbitWatch has no command uplink. Approval records the decision and executes the burn <b>in simulation</b>; nothing is sent to a spacecraft.</div></div>
    <label class="field"><span>Note (optional)</span><textarea name="reason" rows="2" maxlength="1000" placeholder="e.g. coordinated with the operator"></textarea></label>
    <label class="check"><input type="checkbox" name="confirm" required> I have reviewed the recommendation and approve this burn (simulated execution).</label>`,
  { submitLabel: 'Approve burn', onSubmit: async (fd) => {
    if (!fd.get('confirm')) throw new Error('Tick the confirmation box to approve.');
    await post(`/assessments/${a.assessment_id}/decision`, { action: 'approve', confirm: true, reason: fd.get('reason') || null });
    toast('Burn approved — simulated execution recorded');
    await done();
  } });
  dlg.querySelector('button[type=submit]').classList.replace('primary', 'approve');
  try {
    const sim = await get(`/assessments/${a.assessment_id}/simulation`);
    dlg.querySelector('#simMiss').textContent = `${fmt.num(sim.miss_before_km, 3)} → ${fmt.num(sim.miss_after_km, 3)} km at the original TCA`;
    dlg.querySelector('#simClosest').textContent = `${fmt.num(sim.closest_after.distance_km, 3)} km at ${fmt.dt(sim.closest_after.time)}`;
  } catch (err) {
    dlg.querySelector('#simMiss').textContent = `simulation unavailable: ${err.message}`;
  }
}

function rejectDialog(a, done) {
  const m = a.maneuver || {};
  modal('Reject the recommendation', `
    <p class="small muted" style="margin:0">The decision and your reasons are recorded in the audit log. You can ask the agent to re-plan straight away; it receives your feedback, and a minimum miss distance is enforced as a hard constraint.</p>
    <label class="field"><span>Reason (required)</span><textarea name="reason" rows="3" minlength="3" maxlength="1000" required placeholder="e.g. ${fmt.num(m.miss_after_km || 1, 1)} km is still too close; we need more margin"></textarea></label>
    <label class="check"><input type="checkbox" name="replan" checked> Re-plan now with this feedback</label>
    <label class="field"><span>Required miss distance after the burn (km, optional)</span>
      <input type="number" name="min_miss_km" min="0.05" max="50" step="0.05" placeholder="${fmt.num(Math.max(2, Math.ceil((m.miss_after_km || 1) * 2)), 0)}">
      <span class="hint">Becomes a constraint for the optimiser and the evaluator.</span></label>`,
  { submitLabel: 'Reject', onSubmit: async (fd) => {
    const reason = String(fd.get('reason') || '').trim();
    if (reason.length < 3) throw new Error('Say why you reject the recommendation.');
    const minMiss = fd.get('min_miss_km') ? Number(fd.get('min_miss_km')) : null;
    const res = await post(`/assessments/${a.assessment_id}/decision`, { action: 'reject', reason, replan: !!fd.get('replan'), min_miss_km: minMiss });
    toast(res.replan_assessment_id ? `Rejected — re-planning (assessment #${res.replan_assessment_id})` : 'Recommendation rejected');
    await done(res);
  } });
}
