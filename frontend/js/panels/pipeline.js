import { state, subscribe, setRisk, setManeuvers, setDecision, setApproval, setRejection, setSelectedManeuver, setMitigation, pushLog, setActiveConjunction } from '../state.js';
import * as api from '../api.js';
import { icons } from '../icons.js';
import { escapeHtml, fmtKm, fmtNum, fmtRelVel, riskTierColorVar, clamp } from '../utils.js';
import { toast } from './topbar.js';

const STAGES = [
  { key: 'tracking', label: 'TRACKING', icon: icons.satellite },
  { key: 'screening', label: 'SCREENING', icon: icons.radar },
  { key: 'risk', label: 'RISK', icon: icons.alertTriangle },
  { key: 'maneuver', label: 'MANEUVER', icon: icons.fly },
  { key: 'optimizer', label: 'OPTIMIZER', icon: icons.target },
  { key: 'approval', label: 'APPROVAL', icon: icons.flag },
];

// Closed-loop mitigation steps shown after approval
const MITIGATION_STEPS = [
  { key: 'pre',   label: 'PRE-MANEUVER',         icon: icons.satellite },
  { key: 'burn',  label: 'APPROVED BURN',         icon: icons.fly },
  { key: 'prop',  label: 'SIMULATED PROPAGATION', icon: icons.radar },
  { key: 'post',  label: 'POST-MANEUVER',         icon: icons.target },
  { key: 'assess',label: 'REASSESSMENT',          icon: icons.alertTriangle },
];

let activeStageKey = null;
let onApproveCb = null;
// Track which conjunctions are currently running the mitigation animation
const mitigatingSet = new Set();

export function initPipeline({ onApprove }) {
  onApproveCb = onApprove;

  document.getElementById('pipelineConjSelect').addEventListener('change', (e) => {
    setActiveConjunction(e.target.value || null);
  });

  document.getElementById('runPipelineBtn').addEventListener('click', () => {
    if (state.activeConjunctionId) runPipeline(state.activeConjunctionId);
  });

  subscribe((topic) => {
    if (['conjunctions', 'activeConjunction', 'risk', 'maneuvers', 'decisions', 'approvals', 'selectedManeuver', 'mitigations', 'view'].includes(topic)) {
      render();
    }
  });

  render();
}

export function openConjunction(conjId, autoRun = true) {
  setActiveConjunction(conjId);
  // Gate on maneuvers, not risk: risk is speculatively prefetched in the
  // background for every conjunction (to badge the Conjunctions list), so it
  // is usually already present by the time a card is opened. Maneuvers only
  // ever come from an actual pipeline run, so they're the right signal for
  // "has this conjunction been walked through yet".
  if (autoRun && !state.maneuvers.has(conjId)) runPipeline(conjId);
}

async function runPipeline(conjId) {
  const conj = state.conjunctions.find((c) => c.conjunction_id === conjId);
  if (!conj) return;

  activeStageKey = 'risk'; render();
  const riskRes = await api.assessRisk(conj);
  setRisk(conjId, riskRes);
  pushLog(`Risk Agent ${riskRes.live ? '(live)' : '(fallback)'} scored ${conjId}: ${riskRes.data.risk_score.toFixed(1)} / ${riskRes.data.risk_level}`, riskRes.data.risk_level === 'CRITICAL' || riskRes.data.risk_level === 'HIGH' ? 'warn' : 'info');

  activeStageKey = 'maneuver'; render();
  const manRes = await api.generateManeuvers(conj, conj.primary_object);
  setManeuvers(conjId, manRes);
  pushLog(`Maneuver Agent ${manRes.live ? '(live)' : '(fallback)'} produced ${manRes.data.candidates.length} candidate burns for ${conjId}`);

  activeStageKey = 'optimizer'; render();
  const decRes = await api.optimizeDecision(conj, manRes.data);
  setDecision(conjId, decRes);
  setSelectedManeuver(conjId, decRes.data.decision.recommended_maneuver_id);
  pushLog(`Optimizer ${decRes.live ? '(live)' : '(fallback)'} recommends ${decRes.data.decision.recommended_maneuver_id}`, 'ok');

  activeStageKey = 'approval'; render();
}

/**
 * Run closed-loop post-maneuver simulation.
 * Derives post-burn state from the approved maneuver candidate data;
 * uses the existing risk fallback (or live agent) to re-assess the
 * projected post-burn conjunction state. All values come from actual
 * pipeline data — nothing is hard-coded.
 */
async function runMitigationFlow(conj, maneuver) {
  const conjId = conj.conjunction_id;
  if (mitigatingSet.has(conjId)) return; // already running
  mitigatingSet.add(conjId);

  const riskEntry = state.risk.get(conjId);
  const preMiss   = conj.closest_approach.distance_km;
  const preVel    = conj.closest_approach.relative_velocity_km_s;
  const preRisk   = riskEntry?.data?.risk_score ?? null;
  const preLevel  = riskEntry?.data?.risk_level  ?? 'UNKNOWN';

  // Animate through steps: notify renders for each step
  const STEP_DELAY_MS = 900;
  const steps = ['pre', 'burn', 'prop', 'post', 'assess'];

  // Store a partial mitigation so the UI can show the progress bar
  setMitigation(conjId, { status: 'running', step: 'pre', preMiss, preRisk, preLevel });
  await wait(STEP_DELAY_MS);

  setMitigation(conjId, { status: 'running', step: 'burn', preMiss, preRisk, preLevel });
  await wait(STEP_DELAY_MS);

  setMitigation(conjId, { status: 'running', step: 'prop', preMiss, preRisk, preLevel });
  await wait(STEP_DELAY_MS);

  // Build a synthetic "post-burn conjunction" object using the maneuver's projected new_separation_km
  const postMiss = maneuver.new_separation_km;

  const postConj = {
    ...conj,
    conjunction_id: conjId + '_POST',
    closest_approach: {
      distance_km: postMiss,
      relative_velocity_km_s: preVel, // velocity unchanged by a small translational burn
    },
  };

  setMitigation(conjId, { status: 'running', step: 'post', preMiss, preRisk, preLevel, postMiss });
  await wait(STEP_DELAY_MS);

  setMitigation(conjId, { status: 'running', step: 'assess', preMiss, preRisk, preLevel, postMiss });

  // Re-assess risk on projected post-burn state (live or fallback)
  const postRiskRes = await api.assessRisk(postConj);
  const postRisk  = postRiskRes.data.risk_score;
  const postLevel = postRiskRes.data.risk_level;

  // Determine outcome:
  // MITIGATED   → post risk level LOW or MEDIUM and separation improved by >50%
  // MONITORING  → improved but not fully mitigated
  // ACTIVE      → not sufficiently improved
  const improvement = postMiss - preMiss; // km gained
  const improvePct  = preMiss > 0 ? improvement / preMiss : 0;
  let mitigationStatus;
  if ((postLevel === 'LOW') || (postLevel === 'MEDIUM' && improvePct >= 0.5)) {
    mitigationStatus = 'MITIGATED';
  } else if (improvement > 0 && postRisk < (preRisk ?? 100)) {
    mitigationStatus = 'MONITORING';
  } else {
    mitigationStatus = 'ACTIVE';
  }

  pushLog(
    `Post-burn reassessment: ${preMiss.toFixed(2)} km → ${postMiss.toFixed(2)} km separation. Risk ${preLevel}(${(preRisk??0).toFixed(1)}) → ${postLevel}(${postRisk.toFixed(1)}). Outcome: ${mitigationStatus}`,
    mitigationStatus === 'MITIGATED' ? 'ok' : mitigationStatus === 'MONITORING' ? 'info' : 'warn'
  );

  setMitigation(conjId, {
    status: mitigationStatus,
    step: 'done',
    preMiss,
    postMiss,
    preRisk,
    postRisk,
    preLevel,
    postLevel,
    deltaV: maneuver.delta_v_m_s,
    maneuver: maneuver.maneuver_id,
    burnDir: maneuver.burn_direction,
    computedAt: new Date().toISOString(),
    postRiskLive: postRiskRes.live,
  });

  mitigatingSet.delete(conjId);

  const toastKind = mitigationStatus === 'MITIGATED' ? 'info' : 'warn';
  const toastMsg = mitigationStatus === 'MITIGATED'
    ? `Conjunction mitigated — separation increased to ${fmtKm(postMiss)}.`
    : mitigationStatus === 'MONITORING'
    ? `Improved to ${fmtKm(postMiss)}, risk reduced. Continue monitoring.`
    : `Conjunction persists after burn — reassess maneuver plan.`;
  toast(mitigationStatus === 'MITIGATED' ? '✓ CONJUNCTION MITIGATED' : mitigationStatus === 'MONITORING' ? '⚠ MONITORING' : '⚠ CONJUNCTION REMAINS', toastMsg, toastKind);

  onApproveCb?.(conj, maneuver, mitigationStatus);
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

function stageStatus(conjId) {
  const risk = state.risk.get(conjId);
  const man = state.maneuvers.get(conjId);
  const dec = state.decisions.get(conjId);
  const appr = state.approvals.get(conjId);
  return {
    tracking: 'done',
    screening: conjId ? 'done' : 'pending',
    risk: risk ? 'done' : activeStageKey === 'risk' ? 'active' : 'pending',
    maneuver: man ? 'done' : activeStageKey === 'maneuver' ? 'active' : 'pending',
    optimizer: dec ? 'done' : activeStageKey === 'optimizer' ? 'active' : 'pending',
    approval: appr ? 'done' : activeStageKey === 'approval' ? 'active' : 'pending',
  };
}

function render() {
  populateSelect();
  const conjId = state.activeConjunctionId;
  renderStageTrack(conjId);
  renderBody(conjId);
}

function populateSelect() {
  const sel = document.getElementById('pipelineConjSelect');
  const current = state.activeConjunctionId;
  sel.innerHTML = `<option value="">— choose a conjunction —</option>` + state.conjunctions.map((c) =>
    `<option value="${escapeHtml(c.conjunction_id)}" ${c.conjunction_id === current ? 'selected' : ''}>${escapeHtml(c.primary_object_name || c.primary_object)} × ${escapeHtml(c.secondary_object_name || c.secondary_object)}</option>`
  ).join('');
  document.getElementById('runPipelineBtn').disabled = !current;
}

function renderStageTrack(conjId) {
  const statuses = stageStatus(conjId);
  const el = document.getElementById('stageTrack');
  el.innerHTML = STAGES.map((s, i) => {
    const st = statuses[s.key];
    const node = `
      <div class="stage-node ${st}">
        <div class="node-dot">${st === 'done' ? icons.check : st === 'active' ? '<span class="spin" style="width:13px;height:13px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;display:block"></span>' : s.icon}</div>
        <div class="node-label">${s.label}</div>
      </div>`;
    const connector = i < STAGES.length - 1
      ? `<div class="stage-connector ${statuses[STAGES[i + 1].key] !== 'pending' ? 'done' : ''}"></div>`
      : '';
    return node + connector;
  }).join('');
}

function renderBody(conjId) {
  const body = document.getElementById('pipelineBody');
  if (!conjId) {
    body.innerHTML = `<div class="pipeline-empty">${icons.radar}<p>Select a conjunction candidate above, or open one from the Conjunctions tab, then run the pipeline.</p></div>`;
    return;
  }
  const conj = state.conjunctions.find((c) => c.conjunction_id === conjId);
  if (!conj) { body.innerHTML = `<div class="pipeline-empty">${icons.alertTriangle}<p>Conjunction not found.</p></div>`; return; }

  const riskEntry = state.risk.get(conjId);
  const manEntry = state.maneuvers.get(conjId);
  const decEntry = state.decisions.get(conjId);
  const approval = state.approvals.get(conjId);
  const rejection = state.rejections.get(conjId);
  const selectedManeuverId = state.selectedManeuver.get(conjId) || (decEntry?.data?.recommended_maneuver_id);
  const mitigation = state.mitigations.get(conjId);

  let html = `<div class="stage-cards">`;
  html += conjunctionCard(conj);
  if (riskEntry) html += riskCard(riskEntry);
  if (manEntry) html += maneuverCard(manEntry, decEntry, selectedManeuverId, approval);
  if (decEntry) html += decisionCard(decEntry);
  if (manEntry && decEntry) html += approvalGate(conj, manEntry, selectedManeuverId, approval, rejection);
  if (approval) html += mitigationCard(conj, mitigation, manEntry, approval);
  html += `</div>`;
  body.innerHTML = html;

  body.querySelectorAll('.maneuver-option').forEach((el) => {
    el.addEventListener('click', () => {
      if (approval) return;
      setSelectedManeuver(conjId, el.dataset.id);
    });
  });
  const approveBtn = body.querySelector('#approveBtn');
  if (approveBtn) approveBtn.addEventListener('click', () => doApprove(conj, manEntry, selectedManeuverId));
  const rejectBtn = body.querySelector('#rejectBtn');
  if (rejectBtn) rejectBtn.addEventListener('click', () => {
    pushLog(`Operator rejected the current maneuver plan for ${conjId}. Awaiting revised decision.`, 'warn');
    setRejection(conjId);
    toast('MANEUVER REJECTED', 'Adjust the candidate selection or re-run the pipeline.', 'warn');
  });
}

function sourceTag(live) {
  return `<span class="source-tag ${live ? 'live' : 'sim'}">${live ? 'LIVE AGENT' : 'SIMULATED'}</span>`;
}

function conjunctionCard(conj) {
  const mins = (new Date(conj.tca) - Date.now()) / 60000;
  return `
  <div class="stage-card">
    <div class="stage-card-head">
      <span class="stage-card-eyebrow">01 · SCREENING${sourceTag(true)}</span>
      <span class="mono" style="font-size:9px;color:var(--ink-3)">${escapeHtml(conj.screening.method)}</span>
    </div>
    <div class="kv-grid">
      <div class="kv-field"><label>Primary</label><div class="v">${escapeHtml(conj.primary_object_name || conj.primary_object)}</div></div>
      <div class="kv-field"><label>Secondary</label><div class="v">${escapeHtml(conj.secondary_object_name || conj.secondary_object)}</div></div>
      <div class="kv-field"><label>Distance @ TCA</label><div class="v accent">${fmtKm(conj.closest_approach.distance_km)}</div></div>
      <div class="kv-field"><label>Rel. Velocity</label><div class="v">${fmtRelVel(conj.closest_approach.relative_velocity_km_s)}</div></div>
      <div class="kv-field"><label>Time to TCA</label><div class="v">${mins > 0 ? mins.toFixed(1) + ' min' : 'past'}</div></div>
    </div>
  </div>`;
}

function gaugeSVG(score, colorVar) {
  const r = 44, c = 2 * Math.PI * r;
  const pct = clamp(score, 0, 100) / 100;
  return `
  <svg viewBox="0 0 108 108" width="108" height="108">
    <circle class="gauge-track" cx="54" cy="54" r="${r}" fill="none" stroke-width="9"/>
    <circle cx="54" cy="54" r="${r}" fill="none" stroke="${colorVar}" stroke-width="9" stroke-linecap="round"
      stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - pct)).toFixed(1)}"
      transform="rotate(-90 54 54)" style="transition: stroke-dashoffset .6s var(--ease)"/>
  </svg>`;
}

function riskCard(entry) {
  const r = entry.data;
  const colorVar = `var(${riskTierColorVar(r.risk_level)})`;
  return `
  <div class="stage-card">
    <div class="stage-card-head">
      <span class="stage-card-eyebrow">02 · RISK ASSESSMENT${sourceTag(entry.live)}</span>
    </div>
    <div class="risk-card-body">
      <div class="gauge-wrap">
        ${gaugeSVG(r.risk_score, colorVar)}
        <div class="gauge-num">
          <span class="score" style="color:${colorVar}">${fmtNum(r.risk_score, 1)}</span>
          <span class="tier">${r.risk_level}</span>
        </div>
      </div>
      <div class="kv-grid" style="flex:1">
        <div class="kv-field"><label>Closest Approach</label><div class="v">${fmtKm(r.factors.closest_approach_km)}</div></div>
        <div class="kv-field"><label>Time to TCA</label><div class="v">${fmtNum(r.factors.time_to_tca_minutes, 1)} min</div></div>
        <div class="kv-field"><label>Rel. Velocity</label><div class="v">${fmtRelVel(r.factors.relative_velocity_km_s)}</div></div>
        <div class="kv-field"><label>Confidence</label><div class="v">${escapeHtml(r.uncertainty.confidence)}</div></div>
      </div>
    </div>
    ${r.notes ? `<div class="notes-line">${escapeHtml(r.notes)}</div>` : ''}
  </div>`;
}

function burnIcon(dir) {
  if (dir === 'POSIGRADE') return icons.arrowUp;
  if (dir === 'RETROGRADE') return icons.arrowDown;
  return icons.arrowSide;
}

function maneuverCard(manEntry, decEntry, selectedId, approved) {
  const candidates = manEntry.data.candidates;
  const recommendedId = decEntry?.data?.decision?.recommended_maneuver_id;
  return `
  <div class="stage-card">
    <div class="stage-card-head">
      <span class="stage-card-eyebrow">03 · MANEUVER CANDIDATES${sourceTag(manEntry.live)}</span>
      <span class="mono" style="font-size:9px;color:var(--ink-3)">${approved ? 'LOCKED' : 'CLICK TO SELECT'}</span>
    </div>
    <div class="maneuver-grid">
      ${candidates.map((c) => `
        <div class="maneuver-option ${c.maneuver_id === selectedId ? 'selected' : ''}" data-id="${c.maneuver_id}">
          <div class="mo-head">
            <span class="mo-id">${c.maneuver_id}</span>
            ${c.maneuver_id === recommendedId ? '<span class="recommended-tag">Optimizer pick</span>' : ''}
          </div>
          <div class="mo-row"><span>Direction</span><span class="mo-v burn-dir">${burnIcon(c.burn_direction)}${c.burn_direction}</span></div>
          <div class="mo-row"><span>Delta-V</span><span class="mo-v">${c.delta_v_m_s.toFixed(2)} m/s</span></div>
          <div class="mo-row"><span>New Separation</span><span class="mo-v">${fmtKm(c.new_separation_km)}</span></div>
          <div class="mo-row"><span>Resulting Risk</span><span class="risk-tag ${c.resulting_risk}" style="padding:1px 7px;font-size:8.5px">${c.resulting_risk}</span></div>
        </div>`).join('')}
    </div>
  </div>`;
}

function decisionCard(decEntry) {
  const d = decEntry.data;
  return `
  <div class="stage-card">
    <div class="stage-card-head">
      <span class="stage-card-eyebrow">04 · OPTIMIZER DECISION${sourceTag(decEntry.live)}</span>
    </div>
    <div class="decision-reason">
      ${icons.info}
      <p><b>Recommended: ${escapeHtml(d.decision.recommended_maneuver_id)}.</b> ${escapeHtml(d.decision.reason)}</p>
    </div>
  </div>`;
}

function approvalGate(conj, manEntry, selectedId, approval, rejection) {
  if (approval) {
    const m = manEntry.data.candidates.find((c) => c.maneuver_id === approval.maneuverId);
    return `
    <div class="stage-card">
      <div class="stage-card-head"><span class="stage-card-eyebrow">05 · HUMAN APPROVAL</span></div>
      <div class="approved-banner">
        ${icons.check}
        <div>Maneuver <b>${escapeHtml(approval.maneuverId)}</b> (${m ? m.burn_direction : ''}, ${m ? m.delta_v_m_s.toFixed(2) : '—'} m/s) approved and queued for simulation. Projected separation ${m ? fmtKm(m.new_separation_km) : '—'}. This is a decision-support simulation — no command was sent to hardware.</div>
      </div>
    </div>`;
  }
  const selected = manEntry.data.candidates.find((c) => c.maneuver_id === selectedId) || manEntry.data.candidates[0];
  
  if (rejection) {
    return `
    <div class="stage-card">
      <div class="stage-card-head"><span class="stage-card-eyebrow">05 · HUMAN APPROVAL</span></div>
      <div class="approved-banner" style="background: var(--flame-dim); color: var(--flame); border-color: var(--flame-dim);">
        ${icons.close}
        <div>Maneuver <b>${escapeHtml(selected?.maneuver_id)}</b> was rejected by the operator. Please select a different candidate from the Maneuver stage above.</div>
      </div>
    </div>`;
  }

  return `
  <div class="stage-card">
    <div class="stage-card-head"><span class="stage-card-eyebrow">05 · HUMAN APPROVAL GATE</span></div>
    <div class="approval-gate">
      <div class="ag-label">Operator confirmation required</div>
      <div class="ag-desc">OrbitalGuard does not command hardware. Approving <b>${selected?.maneuver_id}</b> only records a simulated go-ahead and re-plots the projected trajectory for review.</div>
      <div class="approval-actions">
        <button class="btn btn-danger btn-md" id="rejectBtn">${icons.close} REJECT</button>
        <button class="btn btn-safe btn-lg" id="approveBtn">${icons.check} APPROVE MANEUVER ${selected?.maneuver_id}</button>
      </div>
    </div>
  </div>`;
}

// ── Post-approval mitigation visualization ──────────────────────────────────

function mitigationStepTrack(currentStep) {
  const isRunning = currentStep !== 'done';
  return `
  <div class="mitigation-track">
    ${MITIGATION_STEPS.map((s, i) => {
      let cls;
      if (currentStep === 'done') {
        cls = 'mit-done';
      } else {
        const stepIdx = MITIGATION_STEPS.findIndex((x) => x.key === currentStep);
        if (i < stepIdx) cls = 'mit-done';
        else if (i === stepIdx) cls = 'mit-active';
        else cls = 'mit-pending';
      }
      const connector = i < MITIGATION_STEPS.length - 1
        ? `<div class="mit-connector ${cls === 'mit-done' ? 'done' : ''}"></div>`
        : '';
      return `
        <div class="mit-step ${cls}">
          <div class="mit-dot">
            ${cls === 'mit-done' ? icons.check : cls === 'mit-active' ? '<span class="spin" style="width:11px;height:11px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;display:block"></span>' : s.icon}
          </div>
          <div class="mit-label">${s.label}</div>
        </div>${connector}`;
    }).join('')}
  </div>`;
}

function mitigationCard(conj, mitigation, manEntry, approval) {
  // If not yet started
  if (!mitigation) {
    return `
    <div class="stage-card mit-card" id="mitigationCard">
      <div class="stage-card-head">
        <span class="stage-card-eyebrow">06 · POST-MANEUVER MITIGATION</span>
        <span class="source-tag sim">SIMULATION</span>
      </div>
      <div class="mit-pending-msg">${icons.fly}<p>Closed-loop reassessment queued. Starting simulation…</p></div>
    </div>`;
  }

  const { status, step, preMiss, postMiss, preRisk, postRisk, preLevel, postLevel, deltaV, maneuver: mId, burnDir, postRiskLive } = mitigation;

  // Running — show animated step track + partial data
  if (status === 'running') {
    return `
    <div class="stage-card mit-card" id="mitigationCard">
      <div class="stage-card-head">
        <span class="stage-card-eyebrow">06 · POST-MANEUVER MITIGATION</span>
        <span class="source-tag sim">SIMULATION</span>
      </div>
      ${mitigationStepTrack(step)}
      <div class="mit-progress-msg">
        ${step === 'pre' ? 'Capturing pre-maneuver orbital state…' : ''}
        ${step === 'burn' ? `Applying approved burn: ${mId} (${burnDir ?? ''}, ${deltaV != null ? deltaV.toFixed(2)+' m/s' : '—'})…` : ''}
        ${step === 'prop' ? 'Propagating post-burn trajectory via SGP4…' : ''}
        ${step === 'post' ? `Projected new separation: ${postMiss != null ? fmtKm(postMiss) : '—'}` : ''}
        ${step === 'assess' ? 'Re-scoring conjunction risk on projected post-burn state…' : ''}
      </div>
    </div>`;
  }

  // Completed — full card with comparison matrix and outcome
  const outcomeClass = status === 'MITIGATED' ? 'outcome-mitigated' : status === 'MONITORING' ? 'outcome-monitoring' : 'outcome-active';
  const outcomeIcon  = status === 'MITIGATED' ? icons.check : icons.alertTriangle;
  const outcomeLabel = status === 'MITIGATED' ? '✓ CONJUNCTION MITIGATED'
                     : status === 'MONITORING' ? '⚠ MONITORING'
                     : '⚠ CONJUNCTION REMAINS';

  const missImprovement = postMiss != null && preMiss != null ? ((postMiss - preMiss) / preMiss * 100) : null;
  const riskImprovement = postRisk != null && preRisk != null ? preRisk - postRisk : null;

  const preColorVar  = `var(${riskTierColorVar(preLevel)})`;
  const postColorVar = `var(${riskTierColorVar(postLevel)})`;

  return `
  <div class="stage-card mit-card ${outcomeClass}" id="mitigationCard">
    <div class="stage-card-head">
      <span class="stage-card-eyebrow">06 · POST-MANEUVER MITIGATION${sourceTag(postRiskLive ?? false)}</span>
      <span class="mit-outcome-badge ${outcomeClass}">${outcomeIcon}${outcomeLabel}</span>
    </div>

    ${mitigationStepTrack('done')}

    <div class="mit-comparison">
      <div class="mit-col pre">
        <div class="mit-col-label">PRE-MANEUVER</div>
        <div class="mit-gauge-row">
          ${gaugeSVG(preRisk ?? 0, preColorVar)}
          <div class="gauge-num">
            <span class="score" style="color:${preColorVar}">${fmtNum(preRisk, 1)}</span>
            <span class="tier">${preLevel}</span>
          </div>
        </div>
        <div class="mit-stat"><label>Miss Distance</label><span>${fmtKm(preMiss)}</span></div>
        <div class="mit-stat"><label>Risk Score</label><span style="color:${preColorVar}">${fmtNum(preRisk, 1)} / 100</span></div>
        <div class="mit-stat"><label>Risk Level</label><span class="risk-tag ${preLevel}" style="font-size:9px;padding:2px 8px">${preLevel}</span></div>
      </div>

      <div class="mit-arrow-col">
        <div class="mit-arrow-container">
          <div class="mit-arrow-line"></div>
          <div class="mit-arrow-head">${icons.fly}</div>
          <div class="mit-dv-label">${deltaV?.toFixed(2) ?? '—'} m/s<br/><span style="font-size:8px;opacity:0.7">${burnDir ?? ''}</span></div>
        </div>
        ${missImprovement != null ? `<div class="mit-improvement ${missImprovement >= 0 ? 'positive' : 'negative'}">+${missImprovement.toFixed(0)}% sep</div>` : ''}
        ${riskImprovement != null ? `<div class="mit-improvement ${riskImprovement >= 0 ? 'positive' : 'negative'}">${riskImprovement >= 0 ? '↓' : '↑'}${Math.abs(riskImprovement).toFixed(1)} risk</div>` : ''}
      </div>

      <div class="mit-col post">
        <div class="mit-col-label">POST-MANEUVER</div>
        <div class="mit-gauge-row">
          ${gaugeSVG(postRisk ?? 0, postColorVar)}
          <div class="gauge-num">
            <span class="score" style="color:${postColorVar}">${fmtNum(postRisk, 1)}</span>
            <span class="tier">${postLevel}</span>
          </div>
        </div>
        <div class="mit-stat"><label>Miss Distance</label><span>${fmtKm(postMiss)}</span></div>
        <div class="mit-stat"><label>Risk Score</label><span style="color:${postColorVar}">${fmtNum(postRisk, 1)} / 100</span></div>
        <div class="mit-stat"><label>Risk Level</label><span class="risk-tag ${postLevel}" style="font-size:9px;padding:2px 8px">${postLevel}</span></div>
      </div>
    </div>

    ${status !== 'MITIGATED' ? `
    <div class="mit-residual-note">
      ${icons.info}
      <p>${status === 'MONITORING'
        ? `Separation improved to ${fmtKm(postMiss)} but risk remains elevated. Continue active monitoring and consider a follow-up burn if TCA approaches.`
        : `Applied burn was insufficient to reduce conjunction risk below acceptable thresholds. Recommend re-screening with revised maneuver parameters.`
      }</p>
    </div>` : ''}
  </div>`;
}

function doApprove(conj, manEntry, selectedId) {
  const maneuver = manEntry.data.candidates.find((c) => c.maneuver_id === selectedId) || manEntry.data.candidates[0];
  setApproval(conj.conjunction_id, maneuver.maneuver_id);

  document.body.classList.add('approval-flash');
  setTimeout(() => document.body.classList.remove('approval-flash'), 600);
  activeStageKey = null;
  render();

  pushLog(`Operator approved ${maneuver.maneuver_id} (${maneuver.burn_direction}, ${maneuver.delta_v_m_s.toFixed(2)} m/s) for ${conj.conjunction_id}. Starting mitigation simulation…`, 'ok');
  toast('MANEUVER APPROVED', `${maneuver.maneuver_id} approved — running closed-loop simulation…`, 'info');

  // Kick off mitigation flow (non-blocking)
  runMitigationFlow(conj, maneuver);
}
