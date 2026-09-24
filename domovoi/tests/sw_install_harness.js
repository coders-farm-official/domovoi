// Run web/static/sw.js's install handler for real, against a fake Cache
// Storage, and report the cache mode of every request it made.
//
// The service worker's install handler is the ONE moment the shell bundle
// is fetched, and SHELL_CACHE's name is the only thing that makes it fire
// again. If those fetches are answered out of the browser's own HTTP cache,
// a cache named for the new shell fills with the old bundle and the name
// bump buys nothing — and the name will not change again.
//
// Usage: node sw_install_harness.js <repo-root>
// Out:   { installHandlers, shellCache, added: [{url, cache}, ...] }
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const listeners = {};
const added = [];

// Only as much of Request as sw.js uses. A bare string handed to
// cache.add() is recorded as such, because that is the shape of the bug:
// Cache.add(string) is a default-mode fetch.
class FakeRequest {
  constructor(input, init) {
    this.url = typeof input === 'string' ? input : input.url;
    this.cache = (init && init.cache) || (typeof input === 'object' && input.cache) || 'default';
  }
}

const cache = {
  add(req) {
    added.push(typeof req === 'string'
      ? { url: req, cache: 'default (bare string)' }
      : { url: req.url, cache: req.cache });
    return Promise.resolve();
  },
  put: () => Promise.resolve(),
  match: () => Promise.resolve(undefined),
};

const sandbox = {
  console, setTimeout, clearTimeout, URL, Promise,
  Request: FakeRequest,
  caches: {
    open: () => Promise.resolve(cache),
    keys: () => Promise.resolve([]),
    delete: () => Promise.resolve(true),
    match: () => Promise.resolve(undefined),
  },
  fetch: () => Promise.reject(new Error('no network in the harness')),
};
sandbox.self = {
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  skipWaiting: () => Promise.resolve(),
  clients: { claim: () => Promise.resolve() },
  location: { origin: 'http://test' },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'web/static/sw.js'), 'utf8'),
                sandbox, { filename: 'web/static/sw.js' });

(async () => {
  const waits = [];
  for (const fn of (listeners.install || [])) fn({ waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  process.stdout.write(JSON.stringify({
    installHandlers: (listeners.install || []).length,
    shellCache: vm.runInContext('SHELL_CACHE', sandbox),
    added,
  }));
})().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
});
