/* Band Manager Service Worker — App-Shell cache-first, /api/* niemals cachen. */
const VERSION = 'bandmanager-v2';
const SHELL = [
  '/', '/index.html', '/style.css', '/app.js', '/manifest.webmanifest',
  '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;                 // Schreibzugriffe immer ans Netz
  if (url.origin !== self.location.origin) return;        // Fonts etc. – Browser-Cache reicht
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/mcp')) return; // network-only

  // App-Shell: cache-first, im Hintergrund auffrischen
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const fresh = fetch(e.request).then((res) => {
        if (res.ok) caches.open(VERSION).then((c) => c.put(e.request, res.clone()));
        return res;
      }).catch(() => cached);
      return cached || fresh;
    })
  );
});
