// WebSocket client for the tracking service's discrete catalog-change
// telemetry (see connection_manager.py / main.py for the server side).
// Replaces HTTP polling for the tracking service specifically -- risk/
// maneuver/optimizer stay on lightweight health polling in api.js/main.js,
// since they aren't high-frequency telemetry sources and standing up a
// socket on all four services would be disproportionate.
//
// Handles reconnect with exponential backoff and a ping/pong keepalive so a
// connection silently dropped by an idle-timing-out intermediary is
// detected and recovered from, not just left looking alive forever.

const WS_URL = 'ws://localhost:8000/api/v1/ws/telemetry';
const PING_INTERVAL_MS = 20000;
const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 20000;

let socket = null;
let pingTimer = null;
let reconnectTimer = null;
let backoffMs = MIN_BACKOFF_MS;
let onMessageCb = null;
let onStatusCb = null;
let stopped = true;

function setStatus(status) {
  if (onStatusCb) onStatusCb(status); // 'connecting' | 'open' | 'closed'
}

function connect() {
  clearTimeout(reconnectTimer);
  setStatus('connecting');

  socket = new WebSocket(WS_URL);

  socket.addEventListener('open', () => {
    backoffMs = MIN_BACKOFF_MS;
    setStatus('open');
    clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send('ping');
    }, PING_INTERVAL_MS);
  });

  socket.addEventListener('message', (e) => {
    if (e.data === 'pong') return;
    try {
      const msg = JSON.parse(e.data);
      if (onMessageCb) onMessageCb(msg);
    } catch (err) {
      console.warn('[ws] malformed telemetry message', e.data, err);
    }
  });

  socket.addEventListener('close', () => {
    clearInterval(pingTimer);
    setStatus('closed');
    if (!stopped) scheduleReconnect();
  });

  socket.addEventListener('error', () => {
    // The subsequent 'close' event (fired automatically after 'error' on a
    // WebSocket) is what actually schedules the reconnect -- this handler
    // only exists to make sure the socket doesn't hang half-open.
    if (socket) socket.close();
  });
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, backoffMs);
  backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
}

export function initTelemetryWebSocket({ onMessage, onStatus }) {
  onMessageCb = onMessage;
  onStatusCb = onStatus;
  stopped = false;
  backoffMs = MIN_BACKOFF_MS;
  connect();
}

export function stopTelemetryWebSocket() {
  stopped = true;
  clearTimeout(reconnectTimer);
  clearInterval(pingTimer);
  if (socket) socket.close();
}
