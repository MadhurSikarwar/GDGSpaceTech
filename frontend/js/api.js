// API client for the OrbitalGuard multi-agent backend.
//
// Design note: Risk / Maneuver / Optimizer are teammate-owned services that
// may not be running (or may not have CORS configured) during development or
// judging. Every call to those three tries the live service first, and falls
// back to a local heuristic that matches the exact contract shape from
// shared/schemas/*.py. Every result carries `live: true|false` so the UI can
// badge it honestly (LIVE vs SIMULATED) instead of pretending a fallback is
// real agent output.

export const BASES = {
  tracking: 'http://localhost:8000/api/v1',
  risk: 'http://localhost:8001',
  maneuver: 'http://localhost:8002',
  optimizer: 'http://localhost:8003',
};

const FIXTURES = {
  objects: './data/sample_objects.json',
  conjunctions: './data/sample_conjunctions.json',
};

async function fetchJSON(url, opts = {}, timeoutMs = 5000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...opts, signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function checkHealth(base, timeoutMs = 2500) {
  try {
    await fetchJSON(`${base}/health`, {}, timeoutMs);
    return true;
  } catch {
    return false;
  }
}

// ---- Tracking + Screening (platform baseline, expected live) ----

export async function getObjects(objectType = null) {
  const qs = objectType ? `?object_type=${encodeURIComponent(objectType)}` : '';
  try {
    return { data: await fetchJSON(`${BASES.tracking}/objects${qs}`, {}, 30000), live: true };
  } catch (err) {
    console.warn('[api] tracking /objects unreachable, using bundled fixture', err);
    const data = await fetchJSON(FIXTURES.objects);
    return { data, live: false };
  }
}

export async function getTrajectory(catalogId, horizon = 90, step = 1.0) {
  const url = `${BASES.tracking}/objects/${encodeURIComponent(catalogId)}/trajectory?horizon=${horizon}&step=${step}`;
  return fetchJSON(url, {}, 8000);
}

export async function getConjunctions() {
  try {
    return { data: await fetchJSON(`${BASES.tracking}/conjunctions`), live: true };
  } catch (err) {
    console.warn('[api] tracking /conjunctions unreachable, using bundled fixture', err);
    const data = await fetchJSON(FIXTURES.conjunctions);
    return { data, live: false };
  }
}

export async function runIngest(group = 'active') {
  return fetchJSON(`${BASES.tracking}/ingest?group=${encodeURIComponent(group)}`, { method: 'POST' }, 20000);
}

export async function runScreen(horizon = 90, thresholdKm = 50.0) {
  return fetchJSON(`${BASES.tracking}/screen?horizon=${horizon}&threshold_km=${thresholdKm}`, { method: 'POST' }, 20000);
}

export async function injectSyntheticDebris(targetCatalogId = '25544') {
  return fetchJSON(`${BASES.tracking}/demo/inject-synthetic?target_catalog_id=${encodeURIComponent(targetCatalogId)}`, { method: 'POST' }, 20000);
}

// ---- Risk / Maneuver / Optimizer — live-with-fallback ----

export async function assessRisk(conj) {
  try {
    const data = await fetchJSON(
      `${BASES.risk}/assess-risk?conjunction_id=${encodeURIComponent(conj.conjunction_id)}`,
      { method: 'POST' },
      3000
    );
    return { data, live: true };
  } catch (err) {
    return { data: localRiskAssessment(conj), live: false };
  }
}

export async function generateManeuvers(conj, satelliteId) {
  try {
    const data = await fetchJSON(
      `${BASES.maneuver}/generate-maneuvers?conjunction_id=${encodeURIComponent(conj.conjunction_id)}&satellite_id=${encodeURIComponent(satelliteId)}`,
      { method: 'POST' },
      3000
    );
    return { data, live: true };
  } catch (err) {
    return { data: localManeuverCandidates(conj, satelliteId), live: false };
  }
}

export async function optimizeDecision(conj, maneuverCandidates) {
  try {
    const data = await fetchJSON(
      `${BASES.optimizer}/optimize-decision?conjunction_id=${encodeURIComponent(conj.conjunction_id)}`,
      { method: 'POST' },
      3000
    );
    return { data, live: true };
  } catch (err) {
    return { data: localOptimizerDecision(conj, maneuverCandidates), live: false };
  }
}

// ---- Local fallback heuristics (clearly-labeled SIMULATED estimates) ----
// Mirror the documented business rules in INTEGRATION_CONTRACT.md so the
// fallback tells the same story a live agent would, just without claiming
// to be one.

function classifyRisk(distanceKm, relVelKmS) {
  const distanceFactor = clamp01(100 - distanceKm * 2) ;
  const velocityFactor = clamp01(relVelKmS * 6);
  const score = Math.round((0.6 * distanceFactor + 0.4 * velocityFactor) * 10) / 10;
  let level = 'LOW';
  if (score >= 85) level = 'CRITICAL';
  else if (score >= 65) level = 'HIGH';
  else if (score >= 35) level = 'MEDIUM';
  return { score, level };
}

function clamp01(v) {
  return Math.min(100, Math.max(0, v));
}

export function localRiskAssessment(conj) {
  const distanceKm = conj.closest_approach.distance_km;
  const relVel = conj.closest_approach.relative_velocity_km_s;
  const timeToTca = (new Date(conj.tca).getTime() - Date.now()) / 60000;
  const { score, level } = classifyRisk(distanceKm, relVel);
  return {
    conjunction_id: conj.conjunction_id,
    risk_score: score,
    risk_level: level,
    factors: {
      closest_approach_km: distanceKm,
      time_to_tca_minutes: Math.round(timeToTca * 10) / 10,
      relative_velocity_km_s: relVel,
    },
    uncertainty: { model: 'LOCAL_HEURISTIC_FALLBACK', confidence: 'LOW' },
    notes: `Simulated estimate — Risk Agent unreachable. Derived from ${distanceKm.toFixed(2)} km separation and ${relVel.toFixed(2)} km/s relative velocity.`,
  };
}

export function localManeuverCandidates(conj, satelliteId) {
  const distanceKm = conj.closest_approach.distance_km;
  const relVel = conj.closest_approach.relative_velocity_km_s;
  const timeToTca = Math.max(5, (new Date(conj.tca).getTime() - Date.now()) / 60000);

  const plans = [
    { id: 'M1', dv: 0.6, dir: 'POSIGRADE' },
    { id: 'M2', dv: 1.3, dir: 'RETROGRADE' },
    { id: 'M3', dv: 2.1, dir: 'NORMAL' },
  ];

  const candidates = plans.map((p) => {
    const newSeparation = Math.round((distanceKm + p.dv * timeToTca * 0.35) * 10) / 10;
    const { level } = classifyRisk(newSeparation, relVel);
    return {
      maneuver_id: p.id,
      delta_v_m_s: p.dv,
      burn_direction: p.dir,
      new_separation_km: newSeparation,
      resulting_risk: level,
    };
  });

  return {
    conjunction_id: conj.conjunction_id,
    primary_object: satelliteId,
    candidates,
  };
}

const RISK_RANK = { LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3 };

export function localOptimizerDecision(conj, maneuverCandidates) {
  const candidates = maneuverCandidates?.candidates?.length
    ? maneuverCandidates.candidates
    : localManeuverCandidates(conj, maneuverCandidates?.primary_object || conj.primary_object).candidates;

  const lowRisk = candidates.filter((c) => c.resulting_risk === 'LOW');
  const pool = lowRisk.length ? lowRisk : candidates;
  const best = [...pool].sort((a, b) => {
    const rankDiff = RISK_RANK[a.resulting_risk] - RISK_RANK[b.resulting_risk];
    if (rankDiff !== 0) return rankDiff;
    return a.delta_v_m_s - b.delta_v_m_s;
  })[0];

  const reason = lowRisk.length
    ? `${best.maneuver_id} (${best.burn_direction.toLowerCase()}) reaches ${best.new_separation_km.toFixed(1)} km separation for ${best.delta_v_m_s.toFixed(2)} m/s of delta-v — the lowest fuel cost among candidates that drive resulting risk to LOW.`
    : `${best.maneuver_id} (${best.burn_direction.toLowerCase()}) offers the best available risk reduction (${best.resulting_risk}) though no evaluated candidate reaches LOW; recommend re-screening after this burn.`;

  return {
    conjunction_id: conj.conjunction_id,
    decision: {
      recommended_maneuver_id: best.maneuver_id,
      reason,
    },
    human_approval_required: true,
    simulation: {
      status: 'PENDING',
      new_tca_distance_km: best.new_separation_km,
    },
  };
}
