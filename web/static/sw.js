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
 *
 * THE SHELL IS NETWORK-FIRST TOO, and that is the whole point of this file
 * now. A service worker sits in front of the HTTP cache, so a worker that
 * answers from its own cache without asking can hand a browser a stale
 * bundle no matter what the server's headers say — which is exactly what
 * this one used to do: a deployed fix sat unread on the server while the
 * operator reloaded and reloaded. The network answer wins whenever there is
 * one; the cache is the offline fallback.
 *
 * The server half of the same promise is web/backend/static_cache.py, which
 * stamps every asset URL in the page with that file's own token and serves
 * the page itself `no-cache`. BOTH halves are needed: this file cannot reach
 * a browser where the worker never registers (a first load, a failed
 * registration, or — the ordinary case on a LAN install reached over plain
 * http:// — an origin that is not a secure context, where registration is
 * impossible), and that file cannot reach a browser whose worker answers
 * before the network is consulted.
 *
 * The two halves also settle what a fetch costs here, which matters on a
 * Pi-class box: a URL carrying a build token cannot change meaning, so it is
 * fetched normally and the HTTP cache answers it for nothing. Only the
 * UNVERSIONED names — the page, the precache list, anything hand-typed — are
 * re-fetched with `cache: 'no-cache'`, which is a 304 with no body. So the
 * bill for never shipping an invisible fix is one conditional request for
 * the page, not one per asset.
 */

// The cache this worker installs the shell into.
//
// This name USED to be the only thing standing between a deploy and a
// browser that never sees it: same-origin static was served cache-first
// with no revalidation, and the install handler that repopulates the
// cache only fires when this file's own bytes change — so a warm browser
// kept the auth.js and data.js it installed with, forever, until somebody
// remembered to move the name. Nobody remembers. Two branches changed
// static assets in one evening and neither touched this line, and the
// result was a dashboard serving the pre-fix drawings.jsx out of cache
// while the fixed file sat on the server.
//
// So the name is no longer load-bearing: the shell is network-first and
// the server versions the asset URLs, so a deploy arrives whether or not
// anybody touched this line, and this cache is the OFFLINE fallback rather
// than the source of truth. Bump it when a shell asset's
// CONTRACT with the server changes and you want warm browsers to throw the
// old copies away at once — v4 does exactly that for the browsers that
// installed v3 before the revalidation fix existed. Forgetting to bump it
// now costs nothing.
const SHELL_CACHE = 'domovoi-shell-v4';
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
      //
      // `cache: 'reload'`, NOT a bare cache.add(u): a bare add is a
      // default-mode fetch, so a browser that opened the dashboard recently
      // could answer the install fetch out of its own HTTP cache — and a
      // cache named for the NEW shell would fill up with the OLD auth.js.
      // The server now answers these plain, unversioned names `no-cache`
      // (web/backend/static_cache.py), which closes that on its own, but
      // 'reload' is kept: an install is the one fetch that must not depend
      // on the server's headers being right, because what it stores is
      // what a browser will live on when the network is gone.
      Promise.allSettled(SHELL_ASSETS.map((u) => cache.add(new Request(u, { cache: 'reload' }))))
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
  //
  // fetchFresh, not a bare fetch(req). "Network-first" in a worker only
  // means the worker asks the network before it reads its OWN cache — the
  // browser's HTTP cache still sits between the two, and a plugin asset
  // has no version in its URL to keep it honest, so a default-mode fetch
  // was answered out of that cache and the upgraded panel never ran. The
  // server now sends these no-cache (web/backend/plugin_host.py); this
  // holds anyway, because a worker outlives the box it first spoke to.
  if (isPluginAsset(url)) {
    event.respondWith(
      fetchFresh(req).then((resp) => {
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

  // Same-origin static shell: NETWORK-FIRST, with the cache as the offline
  // fallback. See the header — cache-first here is what let a deployed fix
  // sit unread on the server while the operator reloaded and reloaded.
  if (url.origin === self.location.origin) {
    event.respondWith(shellNetworkFirst(req));
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

// How long the shell waits for the box before it falls back to the copy it
// already has. A healthy LAN answers a conditional GET in single-digit
// milliseconds, so this only ever fires when the server is unreachable or
// the Wi-Fi has gone soft — the case the offline cache exists for. The
// network request is NOT abandoned when it fires: it keeps running and
// refreshes the cache, so the next load is current even if this one was not.
const SHELL_NETWORK_TIMEOUT_MS = 4000;

/* A fetch that cannot come back stale.
 *
 * A URL carrying a build token (`data.js?v=…`) is SAFE to take from the
 * HTTP cache: the token is a digest of that file's bytes, so a deploy that
 * changes the file changes the URL, and a cache hit on the old URL is by
 * definition the file that URL names. Those are fetched normally and cost
 * nothing on a warm browser.
 *
 * Everything else gets `cache: 'no-cache'`, which sends the conditional
 * headers and takes a 304 for an answer — a round trip, not a re-download.
 * Deliberately independent of the response headers there: a worker that
 * trusted the server's Cache-Control on an unversioned name would be as
 * stale as the oldest server it ever spoke to, and this worker outlives
 * deploys by design.
 *
 * Built from the URL rather than from `req` because a navigation request
 * cannot be copied by the Request constructor without changing its mode;
 * nothing a static GET carries beyond its URL and its credentials matters
 * to a file server. */
function fetchFresh(req) {
  const versioned = /[?&]v=[0-9a-f]+(&|$)/.test(req.url);
  return fetch(new Request(req.url, {
    cache: versioned ? 'default' : 'no-cache',
    credentials: 'same-origin',
    redirect: 'follow',
  }));
}

/* Network-first for the app shell, cache as the fallback.
 *
 * Order matters: the network answer WINS whenever there is one, so a deploy
 * reaches a browser on its next ordinary reload — no hard refresh, no
 * cache clearing, nothing for anyone to be told to do. The cached copy is
 * served only when the network fails outright or is still silent after
 * SHELL_NETWORK_TIMEOUT_MS, which is what keeps the dashboard opening with
 * the box switched off. A NAVIGATION with neither falls back to the cached
 * index.html so the SPA still boots — a subresource must not, or an offline
 * script tag gets served a page of HTML.
 *
 * `ignoreSearch` on the lookup, because the server stamps the shell's asset
 * URLs with a build token (`data.js?v=…`, see web/backend/static_cache.py).
 * Offline, ANY copy of data.js beats none, and the precache above stores the
 * unversioned names; online the network wins anyway, so matching loosely
 * costs nothing and stops the cache fragmenting one file per deploy. */
function shellNetworkFirst(req) {
  return caches.open(SHELL_CACHE).then((cache) =>
    cache.match(req, { ignoreSearch: true }).then((hit) => {
      const network = fetchFresh(req).then((resp) => {
        // Only cache a real answer: a 404 or a 500 stored here would be
        // served for as long as the box stays offline.
        if (resp && resp.ok) {
          cache.put(req, resp.clone())
            .then(() => dropOlderCopies(cache, req))
            .catch(() => {});
        }
        return resp;
      });
      if (!hit) {
        return network.catch(() =>
          req.mode === 'navigate' ? caches.match('/index.html') : Response.error()
        );
      }
      return new Promise((resolve) => {
        const timer = setTimeout(() => resolve(hit), SHELL_NETWORK_TIMEOUT_MS);
        network.then(
          (resp) => { clearTimeout(timer); resolve(resp); },
          () => { clearTimeout(timer); resolve(hit); },
        );
      });
    })
  );
}

/* One entry per shell file, not one per file per deploy.
 *
 * The URLs the server stamps (`data.js?v=…`) are new on every release that
 * touches that file, so `cache.put` was ADDING a key each time and the
 * activate handler only ever deletes whole caches by NAME. A phone that
 * lives through a year of deploys would carry every copy of every file it
 * ever fetched, and nothing would read the old ones: the lookup is
 * ignoreSearch, so it takes whichever copy it finds first, and the network
 * wins whenever the box is up.
 *
 * So after a successful put, the other copies of that same PATH go. Same
 * pathname, different query = a previous release's copy of this file.
 * Best-effort and deliberately unawaited by the response: a failed prune
 * costs disk, a prune that blocked the page would cost the page. */
function dropOlderCopies(cache, req) {
  const keep = new URL(req.url);
  return cache.keys().then((keys) => Promise.all(
    keys.filter((k) => {
      const u = new URL(k.url);
      return u.pathname === keep.pathname && u.search !== keep.search;
    }).map((k) => cache.delete(k))
  )).catch(() => {});
}

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
