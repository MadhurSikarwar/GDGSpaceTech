import { state, subscribe } from '../state.js';
import { icons } from '../icons.js';
import { escapeHtml, relativeAge, fmtNum } from '../utils.js';

let onSelect = null;
let onSync = null;

export function initCatalog({ onSelectObject, onSyncCatalog }) {
  onSelect = onSelectObject;
  onSync = onSyncCatalog;

  document.getElementById('filterPills').addEventListener('click', (e) => {
    const pill = e.target.closest('.pill');
    if (!pill) return;
    state.filterType = pill.dataset.type;
    document.querySelectorAll('.pill').forEach((p) => p.classList.toggle('active', p === pill));
    renderList();
  });

  document.getElementById('searchInput').addEventListener('input', (e) => {
    state.searchQuery = e.target.value.trim().toLowerCase();
    renderList();
  });

  document.getElementById('catalogList').addEventListener('click', (e) => {
    const row = e.target.closest('.cat-row');
    if (row) onSelect?.(row.dataset.id);
  });

  subscribe((topic) => {
    if (topic === 'objects') renderList();
    if (topic === 'selection') highlightSelection();
  });
}

function filteredObjects() {
  return state.objects.filter((o) => {
    if (state.filterType !== 'ALL' && o.object_type !== state.filterType) return false;
    if (state.searchQuery) {
      const hay = `${o.name} ${o.catalog_id}`.toLowerCase();
      if (!hay.includes(state.searchQuery)) return false;
    }
    return true;
  });
}

const RENDER_LIMIT = 200;

function renderList() {
  const container = document.getElementById('catalogList');
  document.getElementById('catalogCount').textContent = `${state.objects.length} OBJECT${state.objects.length === 1 ? '' : 'S'}`;

  if (!state.objectsLoaded) return;

  if (state.objects.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        ${icons.inbox}
        <p>No objects tracked yet. Ingest live TLEs from CelesTrak, or load the offline cache.</p>
        <div class="empty-actions">
          <button class="btn btn-ghost btn-sm" data-sync="stations">LOAD ISS + STATIONS</button>
          <button class="btn btn-primary btn-sm" data-sync="active">SYNC ACTIVE CATALOG</button>
        </div>
      </div>`;
    container.querySelectorAll('[data-sync]').forEach((b) => b.addEventListener('click', () => onSync?.(b.dataset.sync)));
    return;
  }

  const items = filteredObjects();
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state">${icons.search}<p>No objects match this filter.</p></div>`;
    return;
  }

  // Only the first RENDER_LIMIT matches are put in the DOM. The live catalog
  // is ~10.9k objects, and building that many rows costs ~1.5s of blocked
  // main thread -- paid again on every keystroke, since input re-renders the
  // list. Filtering and search still run across the whole catalog; this caps
  // only what is materialised, and the footer says so when it truncates.
  const shown = items.length > RENDER_LIMIT ? items.slice(0, RENDER_LIMIT) : items;

  container.innerHTML = shown.map((o) => {
    const age = relativeAge(o.data_quality?.data_age_hours);
    const alt = o.state?.altitude_km !== undefined ? fmtNum(o.state.altitude_km, 0) : '—';
    const selected = o.catalog_id === state.selectedObjectId;
    return `
      <div class="cat-row ${selected ? 'selected' : ''}" data-id="${escapeHtml(o.catalog_id)}">
        <div class="r-name">
          <span class="nm">${escapeHtml(o.name)}</span>
          <span class="id">${escapeHtml(o.catalog_id)} · <span class="type-badge ${o.object_type}">${o.object_type.replace('_', ' ')}</span></span>
        </div>
        <span class="r-alt">${alt}</span>
        <span class="r-age ${age.cls}">${age.text}</span>
      </div>`;
  }).join('') + (items.length > RENDER_LIMIT
    ? `<div class="cat-more">Showing ${RENDER_LIMIT} of ${items.length} matches — refine the search to narrow it down.</div>`
    : '');

  highlightSelection();
}

function highlightSelection() {
  document.querySelectorAll('.cat-row').forEach((r) => r.classList.toggle('selected', r.dataset.id === state.selectedObjectId));
}

export { renderList as renderCatalogList };
