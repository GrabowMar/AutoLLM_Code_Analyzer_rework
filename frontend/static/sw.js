// Kill-switch service worker.
//
// This file exists ONLY to evict any leftover service worker from previous
// PWA-style builds. When a browser fetches /sw.js as part of its background
// SW update check, it gets THIS code, which:
//   1. unregisters itself,
//   2. deletes every cache entry it controls,
//   3. tells all open tabs to reload (so they pick up fresh assets).
//
// Returning a 404 here would NOT kill an existing SW — the browser keeps the
// old one. Returning this script does.

self.addEventListener('install', function (event) {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    (async function () {
      try {
        const keys = await caches.keys();
        await Promise.all(keys.map(function (k) { return caches.delete(k); }));
      } catch (_) {}
      try {
        await self.registration.unregister();
      } catch (_) {}
      try {
        const clients = await self.clients.matchAll({ type: 'window' });
        clients.forEach(function (c) {
          try { c.navigate(c.url); } catch (_) {}
        });
      } catch (_) {}
    })()
  );
});

self.addEventListener('fetch', function (event) {
  // Pass everything through to the network — never serve from any cache.
  event.respondWith(fetch(event.request));
});
