// Live updates over Server-Sent Events (/api/stream), with a polling fallback.
//
// The stream pushes event-log rows visible to the user's role and the user's
// unacknowledged alert count. EventSource reconnects by itself and resumes from
// the last event id; if the stream keeps failing (a proxy that buffers, a
// network that drops long requests) the client switches to polling /api/events
// every 30 s and keeps trying the stream in the background.
import { get } from './api.js';

const listeners = new Set();
export const live = {
  state: 'connecting',          // connecting | live | polling | offline
  lastId: 0,
  alerts: null,
  recent: [],                   // newest first, capped
};

const emit = (type, data) => { for (const fn of listeners) { try { fn(type, data); } catch { /* listener errors stay local */ } } };
export function onLive(fn) { listeners.add(fn); return () => listeners.delete(fn); }

let es = null;
let failures = 0;
let pollTimer = null;
let retryTimer = null;

function setState(s) {
  if (live.state !== s) { live.state = s; emit('state', s); }
}

function push(row) {
  if (row.log_id <= live.lastId && live.recent.some((r) => r.log_id === row.log_id)) return;
  live.lastId = Math.max(live.lastId, row.log_id);
  live.recent.unshift(row);
  if (live.recent.length > 200) live.recent.length = 200;
  emit('log', row);
}

function startPolling() {
  setState('polling');
  clearInterval(pollTimer);
  const poll = async () => {
    try {
      const { items } = await get(`/events?after=${live.lastId}&limit=50`);
      items.forEach(push);
      if (live.state === 'offline') setState('polling');
    } catch { setState('offline'); }
  };
  poll();
  pollTimer = setInterval(poll, 30000);
  // keep trying the stream now and then
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, 120000);
}

export function connect() {
  if (!window.EventSource) { startPolling(); return; }
  if (es) es.close();
  setState(live.state === 'polling' ? 'polling' : 'connecting');
  es = new EventSource(`/api/stream${live.lastId ? `?after=${live.lastId}` : ''}`);
  es.addEventListener('hello', (e) => {
    failures = 0;
    clearInterval(pollTimer);
    clearTimeout(retryTimer);
    try { const d = JSON.parse(e.data); if (!live.lastId) live.lastId = d.after || 0; } catch { /* ignore */ }
    setState('live');
  });
  es.addEventListener('log', (e) => { try { push(JSON.parse(e.data)); } catch { /* ignore */ } });
  es.addEventListener('alerts', (e) => {
    try { live.alerts = JSON.parse(e.data).unacknowledged; emit('alerts', live.alerts); } catch { /* ignore */ }
  });
  es.onerror = () => {
    failures += 1;
    if (es && es.readyState === EventSource.CLOSED || failures >= 4) {
      if (es) es.close();
      es = null;
      startPolling();
    } else {
      setState('connecting');
    }
  };
}

// A new login/logout changes what the stream may show: reconnect with the new session.
export function reconnect() {
  live.lastId = 0;
  live.recent = [];
  live.alerts = null;
  emit('reset');
  connect();
}

export async function loadRecent(limit = 60) {
  try {
    const { items } = await get(`/events?limit=${limit}`);
    const known = new Set(live.recent.map((r) => r.log_id));
    for (const r of items.reverse()) if (!known.has(r.log_id)) { live.recent.push(r); live.lastId = Math.max(live.lastId, r.log_id); }
    live.recent.sort((a, b) => b.log_id - a.log_id);
    emit('loaded');
  } catch { /* the drawer shows what it has */ }
}
