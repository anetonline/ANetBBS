// Pulse deliberately never caches authenticated pages or status responses.
// The worker exists for PWA installability and may cache only public assets.
const CACHE = 'anetbbs-pulse-shell-v2';
const ASSETS = ['/static/pulse/pulse.css', '/static/pulse/pulse.js', '/static/pulse/icon.svg', '/static/pulse/apple-touch-icon.png'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/admin/')) return;
  if (ASSETS.includes(url.pathname)) event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
});

