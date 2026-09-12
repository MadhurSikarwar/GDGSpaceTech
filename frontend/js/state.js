// Minimal observable store — no framework, just direct subscriptions.

const listeners = new Set();

export const state = {
  view: 'orbit',

  objects: [],              // OrbitalObject[]
  objectsById: new Map(),
  objectsLoaded: false,

  trajectories: new Map(),  // objectId -> Trajectory
  trajectoryRequests: new Set(),

  conjunctions: [],         // ConjunctionCandidate[]
  conjunctionsLoaded: false,

  selectedObjectId: null,
  activeConjunctionId: null,

  // Explore Mode: entered from an already-selected object via the EXPLORE
  // action. `conjunctionId` is set when the explored object participates in
  // a conjunction candidate, driving the conjunction-analysis presentation.
  explore: { active: false, objectId: null, conjunctionId: null },

  risk: new Map(),          // conjunctionId -> { data: RiskAssessment, live: bool }
  maneuvers: new Map(),     // conjunctionId -> { data: ManeuverCandidates, live: bool }
  decisions: new Map(),     // conjunctionId -> { data: ManeuverDecision, live: bool }
  approvals: new Map(),     // conjunctionId -> { maneuverId, at }
  rejections: new Map(),    // conjunctionId -> { at }
  mitigations: new Map(),   // conjunctionId -> { status, preMiss, postMiss, preRisk, postRisk, deltaV, maneuver, computedAt }
  selectedManeuver: new Map(), // conjunctionId -> maneuverId (user override before approval)

  serviceStatus: {
    tracking: 'unknown',
    risk: 'unknown',
    maneuver: 'unknown',
    optimizer: 'unknown',
  },

  filterType: 'ALL',
  searchQuery: '',

  simMinutes: 0,
  playing: false,
  playSpeed: 1,

  log: [],
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function notify(topic) {
  for (const fn of listeners) fn(topic);
}

export function pushLog(message, kind = 'info') {
  const entry = { t: new Date(), message, kind };
  state.log.unshift(entry);
  if (state.log.length > 200) state.log.length = 200;
  notify('log');
}

// NOTE: keyed by catalog_id (NORAD number / SYNTHETIC-99999), not the opaque
// DB-generated object_id (a UUID). ConjunctionCandidate.primary_object /
// secondary_object and the /objects/{id}/trajectory route both resolve by
// catalog_id, so catalog_id is the one universal join key across the API.

export function setObjects(objects) {
  state.objects = objects;
  state.objectsById = new Map(objects.map((o) => [o.catalog_id, o]));
  state.objectsLoaded = true;
  notify('objects');
}

export function upsertObject(obj) {
  const idx = state.objects.findIndex((o) => o.catalog_id === obj.catalog_id);
  if (idx >= 0) state.objects[idx] = obj;
  else state.objects.push(obj);
  state.objectsById.set(obj.catalog_id, obj);
  notify('objects');
}

export function setTrajectory(objectId, trajectory) {
  state.trajectories.set(objectId, trajectory);
  notify('trajectory:' + objectId);
}

export function setConjunctions(list) {
  state.conjunctions = list;
  state.conjunctionsLoaded = true;
  notify('conjunctions');
}

export function addConjunctions(list) {
  const ids = new Set(state.conjunctions.map((c) => c.conjunction_id));
  for (const c of list) {
    if (!ids.has(c.conjunction_id)) state.conjunctions.push(c);
  }
  state.conjunctionsLoaded = true;
  notify('conjunctions');
}

export function selectObject(objectId) {
  state.selectedObjectId = objectId;
  notify('selection');
}

export function setActiveConjunction(conjId) {
  state.activeConjunctionId = conjId;
  notify('activeConjunction');
}

export function enterExplore(objectId, conjunctionId = null) {
  state.explore = { active: true, objectId, conjunctionId };
  notify('explore');
}

export function exitExplore() {
  state.explore = { active: false, objectId: null, conjunctionId: null };
  notify('explore');
}

export function setExploreConjunction(conjunctionId) {
  state.explore.conjunctionId = conjunctionId;
  notify('explore');
}

export function setServiceStatus(key, val) {
  if (state.serviceStatus[key] === val) return;
  state.serviceStatus[key] = val;
  notify('serviceStatus');
}

export function setView(view) {
  state.view = view;
  notify('view');
}

export function setRisk(conjId, entry) {
  state.risk.set(conjId, entry);
  notify('risk');
}

export function setManeuvers(conjId, entry) {
  state.maneuvers.set(conjId, entry);
  notify('maneuvers');
}

export function setDecision(conjId, entry) {
  state.decisions.set(conjId, entry);
  notify('decisions');
}

export function setApproval(conjId, maneuverId) {
  state.approvals.set(conjId, { maneuverId, at: new Date() });
  state.rejections.delete(conjId); // clear any previous rejection
  notify('approvals');
}

export function setRejection(conjId) {
  state.rejections.set(conjId, { at: new Date() });
  notify('rejections');
}

export function setMitigation(conjId, result) {
  state.mitigations.set(conjId, result);
  notify('mitigations');
}

export function setSelectedManeuver(conjId, maneuverId) {
  state.selectedManeuver.set(conjId, maneuverId);
  state.rejections.delete(conjId); // clear rejection if they select a new candidate
  notify('selectedManeuver');
}
