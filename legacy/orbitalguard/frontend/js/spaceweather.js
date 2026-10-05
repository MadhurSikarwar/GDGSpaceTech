// Live NOAA space-weather HUD badge in the topbar. Reuses the LIVE/SIMULATED
// honesty convention from api.js: the badge shows "SIM" whenever either the
// tracking service itself was unreachable (outer `live`) or the tracking
// service's own NOAA fetch fell back server-side (data.live) -- either way
// the displayed numbers are the quiet-sun default, not a real reading.

import * as api from './api.js';

const REFRESH_MS = 60000; // client poll cadence; the server itself caches NOAA reads for 10 min

export function initSpaceWeather() {
  refresh();
  setInterval(refresh, REFRESH_MS);
}

async function refresh() {
  const el = document.getElementById('spaceWeatherBadge');
  if (!el) return;
  try {
    const { data, live } = await api.getSpaceWeather();
    render(el, data, live);
  } catch {
    render(el, api.localQuietSunSpaceWeather(), false);
  }
}

function render(el, sw, live) {
  const simulated = !live || !sw.live;
  el.title = simulated
    ? 'NOAA space weather unreachable -- showing quiet-sun baseline'
    : `NOAA SWPC · fetched ${new Date(sw.fetched_at).toISOString()}`;
  el.innerHTML = `
    <span class="sw-dot ${sw.activity_level}"></span>
    <span>F10.7 ${sw.f107_sfu.toFixed(0)} sfu</span>
    <span class="sw-sep">·</span>
    <span>Kp ${sw.kp_index.toFixed(1)}</span>
    ${simulated ? '<span class="sw-sim">SIM</span>' : ''}
  `;
}
