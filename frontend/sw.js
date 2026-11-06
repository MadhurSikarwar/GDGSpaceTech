// OrbitWatch service worker: shows push notifications (close-approach alerts) even when no OrbitWatch tab is open.
// It deliberately has no fetch handler: it never caches or intercepts the site's requests, so pages load exactly as
// without it.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch { msg = { body: event.data ? event.data.text() : '' }; }
  event.waitUntil(self.registration.showNotification(msg.title || 'OrbitWatch', {
    body: msg.body || '',
    tag: msg.tag || undefined,
    icon: '/favicon.svg',
    badge: '/favicon.svg',
    data: { url: msg.url || '/' },
  }));
});

// A click opens the close approach: in an OrbitWatch tab that is already open, otherwise in a new one.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/', self.location.origin).href;
  event.waitUntil((async () => {
    const tabs = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const tab = tabs.find((c) => new URL(c.url).origin === self.location.origin);
    // A message works for every OrbitWatch tab; navigate() only for tabs this worker already controls.
    if (tab) { await tab.focus(); tab.postMessage({ type: 'ow-open', url: target }); return undefined; }
    return self.clients.openWindow(target);
  })());
});
