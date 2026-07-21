/* Domovoi Music — service worker.
 *
 * Two jobs:
 *   1. App-shell offline: cache the static bundle (index.html, css, jsx, the
 *      data/component/player scripts) so the dashboard opens with no network.
 *      CDN deps (React/Babel/Lucide) are cached opportunistically as they're
 *      fetched (opaque responses) so a warm cache also boots offline.
 *   2. Audio offline: serve library-track audio from the `domovoi-audio-v1`
 *      cache the in-page OfflineCache manager fills (manual pins + auto-cache
 *      of recent/favorites). Audio requests are cache-first so a pinned track
 *      plays with the network off; a miss falls through to the network (and
 *      the range request streams from Domovoi as usual).
 *
 * Deliberately conservative: never caches /api/* JSON (state must stay live),
 * treats /plugins/<slug>/static/* as network-first (plugin assets change on
 * install/upgrade without a filename bump), and always lets the network win
 * for anything it doesn't recognise.
 */

const SHELL_CACHE = 'domovoi-shell-v2';
const AUDIO_CACHE = 'domovoi-audio-v1';   // shared with player.jsx OfflineCache
const RUNTIME_CACHE = 'domovoi-runtime-v1';

const SHELL_ASSETS = [
  '/',
  '/index.html',
  '/colors_and_type.css',
  '/styles.css',
  '/auth.js',
  '/data.js',
  '/components.jsx',
  '/player.jsx',
  '/music_player_panel.jsx',
  '/music.jsx',
  '/people.jsx',
  '/satellites.jsx',
  '/calendar.jsx',
  '/plugins.jsx',
  '/settings.jsx',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      // Best-effort: a missing optional asset shouldn't fail the whole install.
      Promise.allSettled(SHELL_ASSETS.map((u) => cache.add(u)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => ![SHELL_CACHE, AUDIO_CACHE, RUNTIME_CACHE].includes(k))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

const isAudio = (url) => /\/api\/music\/library\/\d+\/audio$/.test(url.pathname);
const isCover = (url) => /\/api\/music\/library\/\d+\/cover$/.test(url.pathname);
const isApi = (url) => url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws');
const isPluginAsset = (url) => url.pathname.startsWith('/plugins/');

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Plugin static assets: network-first (they change on install/upgrade
  // without a filename bump); cache fallback keeps offline boots working.
  if (isPluginAsset(url)) {
    event.respondWith(
      fetch(req).then((resp) => {
        const copy = resp.clone();
        caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return resp;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Save-to-device requests (?download=1): never intercept. The cached
  // playback response lacks the attachment Content-Disposition, and
  // ignoreSearch below would happily serve it.
  if (url.searchParams.has('download')) return;

  // Audio: cache-first from the shared audio cache; else network.
  if (isAudio(url)) {
    event.respondWith(
      caches.open(AUDIO_CACHE).then((cache) =>
        cache.match(req, { ignoreVary: true, ignoreSearch: true }).then((hit) => hit || fetch(req))
      )
    );
    return;
  }

  // Cover art: stale-while-revalidate in the runtime cache.
  if (isCover(url)) {
    event.respondWith(staleWhileRevalidate(req, RUNTIME_CACHE));
    return;
  }

  // Other API / WS traffic: always network (live state).
  if (isApi(url)) return;

  // Same-origin static shell: cache-first, fall back to network then cache.
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
        const copy = resp.clone();
        caches.open(SHELL_CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return resp;
      }).catch(() => caches.match('/index.html')))
    );
    return;
  }

  // Cross-origin CDN deps: cache opportunistically (opaque is fine).
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
      const copy = resp.clone();
      caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return resp;
    }).catch(() => hit))
  );
});

function staleWhileRevalidate(req, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(req).then((hit) => {
      const fetched = fetch(req).then((resp) => {
        cache.put(req, resp.clone()).catch(() => {});
        return resp;
      }).catch(() => hit);
      return hit || fetched;
    })
  );
}
