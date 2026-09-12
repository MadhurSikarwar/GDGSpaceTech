import { state, subscribe } from '../state.js';
import { icons } from '../icons.js';
import { escapeHtml, fmtKm, fmtCountdown, minutesUntil } from '../utils.js';

let onOpen = null;

export function initConjunctions({ onOpenConjunction, onRunScreen }) {
  onOpen = onOpenConjunction;

  document.getElementById('conjList').addEventListener('click', (e) => {
    const card = e.target.closest('.conj-card');
    if (card) onOpen?.(card.dataset.id);
  });

  document.getElementById('runScreenBtn').addEventListener('click', onRunScreen);

  subscribe((topic) => {
    if (topic === 'conjunctions' || topic === 'risk') renderConjunctions();
  });
}

function renderConjunctions() {
  const container = document.getElementById('conjList');
  if (!state.conjunctionsLoaded) {
    container.innerHTML = Array(3).fill('<div class="skeleton-card"></div>').join('');
    return;
  }

  if (state.conjunctions.length === 0) {
    container.innerHTML = `
      <div class="empty-state" style="padding:60px 20px">
        ${icons.radar}
        <p>No conjunction candidates flagged. Run screening on the current catalog, or inject a synthetic debris object for a guaranteed demo scenario.</p>
      </div>`;
    updateConjCount();
    return;
  }

  const sorted = [...state.conjunctions].sort((a, b) => new Date(a.tca) - new Date(b.tca));

  container.innerHTML = sorted.map((c) => {
    const riskEntry = state.risk.get(c.conjunction_id);
    const riskLevel = riskEntry?.data?.risk_level || 'PENDING';
    const mins = minutesUntil(c.tca);
    const distCls = c.closest_approach.distance_km < 10 ? 'crit' : c.closest_approach.distance_km < 25 ? 'warn' : '';
    return `
      <div class="conj-card risk-${riskLevel}" data-id="${escapeHtml(c.conjunction_id)}">
        <div class="conj-pair">
          <div class="p1">${escapeHtml(c.primary_object_name || c.primary_object)}</div>
          <div class="p2"><span class="vs">×</span> ${escapeHtml(c.secondary_object_name || c.secondary_object)}</div>
          <div class="conj-id">${escapeHtml(c.conjunction_id)}</div>
        </div>
        <div class="conj-metric"><label>TCA</label><span class="v">${fmtCountdown(mins)}</span></div>
        <div class="conj-metric"><label>Distance</label><span class="v ${distCls}">${fmtKm(c.closest_approach.distance_km)}</span></div>
        <div class="conj-metric"><label>Rel. Velocity</label><span class="v">${c.closest_approach.relative_velocity_km_s.toFixed(2)} km/s</span></div>
        <span class="risk-tag ${riskLevel}">${riskLevel}${riskEntry && !riskEntry.live ? ' ·SIM' : ''}</span>
        <span class="conj-arrow">${icons.chevronRight}</span>
      </div>`;
  }).join('');

  updateConjCount();
}

function updateConjCount() {
  const count = state.conjunctions.length;
  const countEl = document.getElementById('conjCount');
  if (countEl) {
    countEl.textContent = count;
    countEl.classList.toggle('has-alert', count > 0);
  }
}

export { renderConjunctions };
