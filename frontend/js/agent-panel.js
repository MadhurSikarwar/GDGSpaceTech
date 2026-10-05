// AI decision support panel: runs the decision agent on one close approach and
// shows its trace live (each tool it chose, what the tool found, guardrails)
// and the final recommendation.
import { get, post } from './api.js';
import { esc, fmt, h, riskBadge, toast } from './ui.js';

const DECISION_LABEL = {
  MANEUVER_RECOMMENDED: 'Manoeuvre recommended', MONITOR: 'Monitor — no manoeuvre',
  NO_FEASIBLE_MANEUVER: 'No feasible manoeuvre', DATA_UNAVAILABLE: 'Data unavailable',
};
const pc = (v) => (v == null ? '—' : Number(v).toExponential(1));

export async function agentPanel(container, eventId, app) {
  const el = h(`<section class="card agent">
    <div class="card-head"><div><h2>AI decision support</h2>
      <div class="sub">An LLM agent chooses deterministic tools (SGP4, Foster Pc, Clohessy–Wiltshire optimiser, ground stations), reads the results and recommends what to do. Guardrails reject anything the physics does not support.</div></div>
      <div class="row"><span class="tag plain" id="agEngine">—</span><button class="btn primary sm" id="agRun">Run AI assessment</button></div></div>
    <div id="agBody"><div class="muted small mono">No assessment yet for this event.</div></div></section>`);
  container.appendChild(el);
  const runBtn = el.querySelector('#agRun');
  const body = el.querySelector('#agBody');
  let timer = null;
  let stopped = false;

  try {
    const st = await get('/agent/status');
    el.querySelector('#agEngine').textContent = st.llm_enabled ? `LLM · Groq` : 'Deterministic';
    if (!app.can('analyst')) {
      runBtn.disabled = true;
      runBtn.title = 'Running the agent needs the Analyst role';
      runBtn.textContent = app.user ? 'Analyst role needed' : 'Log in as an analyst';
    }
  } catch { /* status is informational */ }

  const show = async (id) => {
    if (stopped) return;
    try {
      const a = await get(`/assessments/${id}`);
      render(body, a);
      el.querySelector('#agEngine').textContent = a.engine.startsWith('groq:') ? `LLM · ${a.engine.slice(5)}` : a.engine;
      if (a.status === 'running') { timer = setTimeout(() => show(id), 900); runBtn.disabled = true; } else if (app.can('analyst')) runBtn.disabled = false;
    } catch (err) { body.innerHTML = `<div class="error-box">${esc(err.message)}</div>`; }
  };

  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    body.innerHTML = '<div class="loading">Starting the agent…</div>';
    try {
      const r = await post(`/conjunctions/${eventId}/assess`);
      show(r.assessment_id);
    } catch (err) { toast(err.message, 'error'); runBtn.disabled = false; }
  });

  try {
    const { items } = await get(`/conjunctions/${eventId}/assessments`);
    if (items.length) show(items[0].assessment_id);
  } catch { /* none yet */ }
  return () => { stopped = true; clearTimeout(timer); };
}

function render(body, a) {
  const steps = a.steps || [];
  const gen = [...steps].reverse().find((s) => s.tool_name === 'generate_maneuver_candidates' && s.payload);
  const evals = {};
  steps.filter((s) => s.tool_name === 'evaluate_maneuver_constraints' && s.payload).forEach((s) => { evals[s.payload.candidate_id] = s.payload; });
  const m = a.maneuver;
  const decision = a.status === 'running' ? '' : `<div class="agent-decision ${a.decision === 'MANEUVER_RECOMMENDED' ? 'act' : ''}">
      <div class="spread"><div><div class="k">Recommendation</div><div class="d">${esc(DECISION_LABEL[a.decision] || a.status)}</div></div>
        <div class="row">${a.risk_tier ? riskBadge(a.risk_tier) : ''}${a.human_approval_required ? '<span class="tag accent">Human approval required</span>' : ''}</div></div>
      ${m ? `<dl class="agent-metrics">
        <div><dt>Δv</dt><dd>${fmt.num(m.dv_mps, 3)} m/s</dd></div><div><dt>Direction</dt><dd>${esc(m.direction)}</dd></div>
        <div><dt>Burn</dt><dd>${esc((m.burn_time_utc || '').slice(11, 19))} UTC</dd></div><div><dt>Lead</dt><dd>T−${fmt.num(m.lead_time_min, 0)} min</dd></div>
        <div><dt>Pc</dt><dd>${pc(a.pc_before)} → ${pc(a.pc_after)}</dd></div><div><dt>Slot drift</dt><dd>${fmt.num(m.sma_drift_km, 2)} km</dd></div></dl>`
        : (a.pc_before != null ? `<dl class="agent-metrics"><div><dt>Pc</dt><dd>${pc(a.pc_before)}</dd></div></dl>` : '')}
      <p>${esc(a.explanation || '')}</p>
      <div class="small mono muted">${esc(a.engine)} · ${fmt.dt(a.finished_at || a.started_at)}</div></div>`;
  const cands = gen ? `<table class="data agent-cands"><thead><tr><th>Candidate</th><th class="num">Δv m/s</th><th>Direction</th>
      <th class="num">Lead</th><th class="num">Pc after</th><th class="num">Drift km</th><th>Constraints</th></tr></thead><tbody>
      ${gen.payload.candidates.map((c) => {
        const e = evals[c.candidate_id];
        const chosen = m && m.candidate_id === c.candidate_id;
        return `<tr class="${chosen ? 'chosen' : ''}"><td class="mono">${c.candidate_id}${chosen ? ' ◀' : ''}</td><td class="num">${fmt.num(c.dv_mps, 3)}</td>
          <td>${esc(c.direction)}</td><td class="num">T−${fmt.num(c.lead_time_min, 0)}m</td><td class="num">${pc(c.pc_after)}</td>
          <td class="num">${fmt.num(c.sma_drift_km, 2)}</td><td>${e ? (e.feasible ? '<span class="status-ok mono small">✓ feasible</span>'
            : `<span class="status-bad mono small">✗ ${esc(e.violations.join(', '))}</span>`) : '<span class="muted small">not evaluated</span>'}</td></tr>`;
      }).join('')}</tbody></table>` : '';
  body.innerHTML = `${decision}${cands}
    <div class="agent-trace-head mono small"><span>Agent trace</span><span>${steps.length} steps${a.status === 'running' ? ' · running…' : ''}</span></div>
    <ol class="agent-trace">${steps.map((s) => `<li class="a-${s.actor}">
      <span class="n">${String(s.step_no).padStart(2, '0')}</span><span class="who">${s.actor}${s.tool_name ? ` · ${esc(s.tool_name)}` : ''}</span>
      <span class="what">${esc(s.summary)}</span></li>`).join('')}${a.status === 'running' ? '<li class="a-system pulse"><span class="n">··</span><span class="who">agent</span><span class="what">thinking…</span></li>' : ''}</ol>`;
}
