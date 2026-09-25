// Drive web/static/sw.js's FETCH handler for real, against a fake Cache
// Storage and a fake network, and report what it actually did.
//
// The fetch handler is the delivery mechanism for every front-end change in
// this product: a worker sits in FRONT of the browser's HTTP cache, so a
// worker that answers from its own cache without asking makes the server's
// headers irrelevant. Until 2026-09-25 that is what this one did, and a
// deployed fix sat unread on the server while the operator reloaded.
//
// Asserting that by grepping sw.js for identifiers was the wrong trade: a
// rename goes red for nothing, and inverting the logic inside the same
// identifiers stays green. So this DISPATCHES events and looks at outcomes.
//
// Usage: node sw_fetch_harness.js <repo-root>
// Out:   { cases: {...} }
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const listeners = {};
const fetches = [];      // every request the worker put on the wire

class FakeRequest {
  constructor(input, init) {
    init = init || {};
    const raw = typeof input === 'string' ? input : input.url;
    // The real Request resolves against the worker's scope; these tests
    // hand it both shapes, and a bare path must not become a URL error.
    this.url = raw.startsWith('/') ? 'http://test' + raw : raw;
    this.method = init.method || (typeof input === 'object' && input.method) || 'GET';
    this.mode = init.mode || (typeof input === 'object' && input.mode) || 'no-cors';
    this.cache = init.cache || (typeof input === 'object' && input.cache) || 'default';
  }
}

class FakeResponse {
  constructor(body, init) {
    init = init || {};
    this.body = body;
    this.status = init.status === undefined ? 200 : init.status;
    this.ok = this.status >= 200 && this.status < 300;
    this.tag = init.tag || body;
  }
  clone() { return new FakeResponse(this.body, { status: this.status, tag: this.tag }); }
}
FakeResponse.error = () => new FakeResponse('network-error', { status: 0, tag: 'ERROR' });

// A Cache that behaves like the real one on the two things sw.js leans on:
// keys() returns Requests, and match(req, {ignoreSearch}) can hit a copy
// stored under a different query string.
function makeCache(name) {
  const entries = [];   // [{req, resp}]
  return {
    name,
    entries,
    add(req) {
      const r = typeof req === 'string' ? new FakeRequest(req) : req;
      entries.push({ req: r, resp: new FakeResponse('installed:' + r.url) });
      return Promise.resolve();
    },
    put(req, resp) {
      const i = entries.findIndex((e) => e.req.url === req.url);
      if (i >= 0) entries.splice(i, 1);
      entries.push({ req, resp });
      return Promise.resolve();
    },
    delete(req) {
      const i = entries.findIndex((e) => e.req.url === req.url);
      if (i >= 0) { entries.splice(i, 1); return Promise.resolve(true); }
      return Promise.resolve(false);
    },
    keys() { return Promise.resolve(entries.map((e) => e.req)); },
    match(req, opts) {
      const want = new URL(typeof req === 'string' ? req : req.url);
      const hit = entries.find((e) => {
        const got = new URL(e.req.url);
        if (got.pathname !== want.pathname) return false;
        return (opts && opts.ignoreSearch) ? true : got.search === want.search;
      });
      return Promise.resolve(hit ? hit.resp : undefined);
    },
  };
}

const store = {};
let networkDown = false;

const sandbox = {
  console, setTimeout, clearTimeout, URL, Promise,
  Request: FakeRequest,
  Response: FakeResponse,
  caches: {
    open: (n) => Promise.resolve(store[n] || (store[n] = makeCache(n))),
    keys: () => Promise.resolve(Object.keys(store)),
    delete: (n) => { delete store[n]; return Promise.resolve(true); },
    match: (u) => {
      for (const n of Object.keys(store)) {
        const hit = store[n].entries.find((e) => e.req.url === u
          || e.req.url === 'http://test' + u);
        if (hit) return Promise.resolve(hit.resp);
      }
      return Promise.resolve(undefined);
    },
  },
  fetch: (input, init) => {
    const r = input instanceof FakeRequest ? input : new FakeRequest(input, init);
    fetches.push({ url: r.url, cache: r.cache });
    if (networkDown) return Promise.reject(new Error('offline'));
    return Promise.resolve(new FakeResponse('NETWORK ' + r.url));
  },
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

const SHELL = vm.runInContext('SHELL_CACHE', sandbox);

function dispatch(req) {
  let answer;
  const event = { request: req, respondWith: (p) => { answer = p; } };
  for (const fn of (listeners.fetch || [])) fn(event);
  return answer;
}

function req(url, extra) {
  return new FakeRequest('http://test' + url, extra || {});
}

const settle = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  const cases = {};
  const shell = await sandbox.caches.open(SHELL);

  // 1. An unversioned shell name must be revalidated, not trusted.
  fetches.length = 0;
  let resp = await dispatch(req('/data.js'));
  cases.unversioned = { body: resp && resp.body, fetched: fetches.slice() };

  // 2. A versioned URL costs nothing extra: the token IS the identity.
  fetches.length = 0;
  resp = await dispatch(req('/files.jsx?v=aaaa1111'));
  cases.versioned = { body: resp && resp.body, fetched: fetches.slice() };

  // 3. THE CACHE MUST NOT GROW ONE ENTRY PER DEPLOY. The browser already
  //    holds last release's copy; this release changed the file.
  await shell.put(req('/drawings.jsx?v=0d0000000000'),
                  new FakeResponse('OLD drawings'));
  const before = (await shell.keys()).map((r) => r.url);
  fetches.length = 0;
  resp = await dispatch(req('/drawings.jsx?v=e011111111aa'));
  await settle(); await settle();
  const after = (await shell.keys()).map((r) => r.url);
  cases.deploy_growth = {
    body: resp && resp.body,
    before_drawings: before.filter((u) => /drawings/.test(u)),
    after_drawings: after.filter((u) => /drawings/.test(u)),
    fetched: fetches.slice(),
  };

  // 4. The network answer WINS over a cached one. Cache-first here is what
  //    let a deployed fix sit unread while the operator reloaded.
  await shell.put(req('/settings.jsx'), new FakeResponse('STALE settings'));
  fetches.length = 0;
  resp = await dispatch(req('/settings.jsx'));
  cases.network_beats_cache = { body: resp && resp.body, fetched: fetches.slice() };

  // 5. A plugin asset has no token in its URL, so it must be revalidated
  //    too — a default-mode fetch there is answered by the HTTP cache and
  //    the upgraded panel never runs.
  fetches.length = 0;
  resp = await dispatch(req('/plugins/demo/static/panel.js'));
  cases.plugin_asset = { body: resp && resp.body, fetched: fetches.slice() };

  // 6. /api is never intercepted: state must stay live.
  cases.api_not_intercepted = dispatch(req('/api/music/state')) === undefined;

  // 7. Offline: a navigation still boots; a subresource must NOT be handed
  //    a page of HTML where a script tag was expected.
  networkDown = true;
  await shell.add('/index.html');
  fetches.length = 0;
  resp = await dispatch(req('/', { mode: 'navigate' }));
  cases.offline_navigation = { body: resp && resp.body };
  const sub = await dispatch(req('/nothing-cached.js'));
  cases.offline_subresource = { body: sub && sub.body, tag: sub && sub.tag };
  // A versioned URL offline still finds the copy stored under the plain name.
  networkDown = true;
  await shell.add('/people.jsx');
  resp = await dispatch(req('/people.jsx?v=cafe00000000'));
  cases.offline_versioned_finds_plain = { body: resp && resp.body };

  process.stdout.write(JSON.stringify({ shellCache: SHELL, cases }));
})().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exitCode = 1;
});
