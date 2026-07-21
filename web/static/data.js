/* Tiny data layer — API fetch helpers + a single shared WebSocket
 * subscription to /ws/state, exposed as React hooks that page
 * components consume. Loaded via Babel-in-browser like the rest of
 * the bundle (so it can use JSX / hooks freely).
 *
 * Design constraints:
 *   * One WebSocket per page-load, multiplexed across hooks. The
 *     server already supports per-channel filtering; we subscribe
 *     to every channel we know about and let each hook filter what
 *     it cares about.
 *   * Hooks are fire-and-forget — `useApiList('/api/music/library')`
 *     returns `{ items, loading, error, refresh }`, and the page
 *     just renders against `items`.
 *   * Server pushes don't require a refetch. The poll loop on the
 *     backend already publishes the new state in the event payload
 *     (`data` field). Hooks merge those pushes in place.
 *   * Errors degrade silently in the UI — the page renders empty
 *     states / dashes rather than red banners. Console gets the
 *     details for debugging.
 */

// ─── Server selection ───────────────────────────────────────────────
// Multi-domovoi homes: the dashboard can point at a different
// backend than the one that served it. '' = same-origin (default).
// Selection + the saved server list persist in localStorage; switching
// reloads the page so every hook and the WebSocket re-init cleanly.

const SERVER_KEY = 'domovoi-server';
const SERVERS_KEY = 'domovoi-servers'; // [{url, name}]

const API_BASE = (() => {
  try { return localStorage.getItem(SERVER_KEY) || ''; } catch { return ''; }
})();
const WS_PATH = '/ws/state';

const ServerStore = {
  current: () => API_BASE, // '' = same-origin
  currentLabel() {
    if (!API_BASE) return window.location.host;
    try { return new URL(API_BASE).host; } catch { return API_BASE; }
  },
  list() {
    try { return JSON.parse(localStorage.getItem(SERVERS_KEY) || '[]'); } catch { return []; }
  },
  _save(list) {
    try { localStorage.setItem(SERVERS_KEY, JSON.stringify(list)); } catch {}
  },
  upsert(url, name) {
    const clean = url.replace(/\/+$/, '');
    const list = this.list().filter((s) => s.url !== clean);
    list.push({ url: clean, name: name || null });
    this._save(list);
  },
  remove(url) {
    this._save(this.list().filter((s) => s.url !== url));
  },
  select(url) {
    try {
      if (url) localStorage.setItem(SERVER_KEY, url.replace(/\/+$/, ''));
      else localStorage.removeItem(SERVER_KEY);
    } catch {}
    window.location.reload();
  },
  // Probe one base URL for a live web backend; resolves {url, name} or null.
  async probe(base, timeoutMs = 1200) {
    const clean = base.replace(/\/+$/, '');
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(`${clean}/api/health`, { signal: ctrl.signal });
      if (!r.ok) return null;
      let name = null;
      try {
        const cfg = await fetch(`${clean}/api/config`).then((x) => x.json());
        name = cfg.bot_name || null;
      } catch {}
      return { url: clean, name };
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  },
  // Scan the /24 around the current host for backends on :6369. Only
  // possible when the page was loaded by IPv4 address (the browser
  // can't learn its own LAN address); callers fall back to manual add.
  scanPrefix() {
    let host = window.location.hostname;
    if (API_BASE) { try { host = new URL(API_BASE).hostname; } catch {} }
    const m = host.match(/^(\d+\.\d+\.\d+)\.\d+$/);
    return m ? m[1] : null;
  },
  async scan(onProgress) {
    const prefix = this.scanPrefix();
    if (!prefix) return null; // caller shows the "load by IP to scan" hint
    const found = [];
    let done = 0;
    const hosts = Array.from({ length: 254 }, (_, i) => i + 1);
    const CONC = 24;
    const worker = async () => {
      while (hosts.length) {
        const n = hosts.shift();
        const hit = await this.probe(`http://${prefix}.${n}:6369`, 900);
        if (hit) found.push(hit);
        done += 1;
        if (onProgress) onProgress(done, 254, found.length);
      }
    };
    await Promise.all(Array.from({ length: CONC }, worker));
    return found;
  },
};

// Admin auth (auth.js, loaded first): the in-memory bearer token rides
// along on every API call; a 401/403 from an admin-gated endpoint pops
// the login modal so the user can authenticate and retry their action.
const _authHeaders = () => {
  try { return (typeof Auth !== 'undefined' && Auth.headers()) || {}; }
  catch { return {}; }
};
const _maybeRequestLogin = (status) => {
  if (status !== 401 && status !== 403) return;
  try { if (typeof Auth !== 'undefined') Auth.requestLogin(); } catch {}
};

const apiFetch = async (path, opts = {}) => {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const r = await fetch(url, {
    credentials: 'include',
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ..._authHeaders(),
      ...(opts.headers || {}),
    },
  });
  if (!r.ok) {
    _maybeRequestLogin(r.status);
    const text = await r.text().catch(() => '');
    const err = new Error(`${r.status} ${r.statusText}: ${text.slice(0, 200)}`);
    err.status = r.status;   // callers branch on auth failures (retry-after-login)
    throw err;
  }
  if (r.status === 204) return null;
  return r.json();
};

const apiGet = (path) => apiFetch(path);
const apiPost = (path, body) => apiFetch(path, { method: 'POST', body: JSON.stringify(body || {}) });
const apiPatch = (path, body) => apiFetch(path, { method: 'PATCH', body: JSON.stringify(body || {}) });
const apiDelete = (path, body) => apiFetch(path, {
  method: 'DELETE',
  body: body !== undefined ? JSON.stringify(body) : undefined,
});

// Save a server-side media file to THIS device (music track, podcast
// episode, audiobook). The endpoints serve Content-Disposition: attachment
// (the ?download=1 variants and /download routes), so the browser saves the
// file — even when API_BASE points at a cross-origin Domovoi server (the
// selected server), where the <a download> attribute alone would be ignored.
// The server also picks the filename, so no name is set here.
const deviceDownload = (path) => {
  const a = document.createElement('a');
  a.href = path.startsWith('http') ? path : `${API_BASE}${path}`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
};

// Multipart upload (file + fields). Doesn't set Content-Type — the
// browser fills in the multipart boundary. Used by the Voices page to
// upload Piper .onnx models.
const apiUpload = async (path, formData) => {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const r = await fetch(url, {
    method: 'POST',
    body: formData,
    credentials: 'include',
    headers: _authHeaders(),
  });
  if (!r.ok) {
    _maybeRequestLogin(r.status);
    const text = await r.text().catch(() => '');
    const err = new Error(`${r.status} ${r.statusText}: ${text.slice(0, 200)}`);
    err.status = r.status;   // callers branch on auth failures (retry-after-login)
    try { err.detail = JSON.parse(text); } catch { /* non-JSON body */ }
    throw err;
  }
  if (r.status === 204) return null;
  return r.json();
};

// ─── Shared WebSocket bus ───────────────────────────────────────────
// One socket per page-load; subscribers get every event and filter
// client-side. The protocol's server-side filter is a perf
// optimization not a correctness gate, so subscribing-to-all and
// distributing locally is fine at homelab scale.

class StateBus {
  constructor() {
    this.subscribers = new Set();
    this.ws = null;
    this.connected = false;
    this.reconnectDelayMs = 1000;
    this.shouldRun = false;
  }

  start() {
    if (this.shouldRun) return;
    this.shouldRun = true;
    this._connect();
  }

  _connect() {
    if (!this.shouldRun) return;
    const httpBase = API_BASE
      || `${window.location.protocol}//${window.location.host}`;
    const url = httpBase.replace(/^http/, 'ws') + WS_PATH;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      console.warn('ws connect failed:', e);
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.addEventListener('open', () => {
      this.connected = true;
      this.reconnectDelayMs = 1000;
      // Empty subscribe = subscribe to all channels (server contract).
      try { ws.send(JSON.stringify({ subscribe: [] })); } catch {}
      this._notifyAll({ type: '_status', connected: true });
    });

    ws.addEventListener('message', (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      this._notifyAll(msg);
    });

    ws.addEventListener('close', () => {
      this.connected = false;
      this._notifyAll({ type: '_status', connected: false });
      this._scheduleReconnect();
    });

    ws.addEventListener('error', () => {
      // 'error' precedes 'close' in browsers; the close handler does
      // the reconnect bookkeeping. Just log here.
      // (Don't spam — most "errors" on close are normal.)
    });
  }

  _scheduleReconnect() {
    if (!this.shouldRun) return;
    const delay = this.reconnectDelayMs;
    this.reconnectDelayMs = Math.min(delay * 1.6, 15000);
    setTimeout(() => this._connect(), delay);
  }

  _notifyAll(event) {
    for (const cb of this.subscribers) {
      try { cb(event); } catch (e) { console.warn('ws subscriber threw:', e); }
    }
  }

  subscribe(cb) {
    this.subscribers.add(cb);
    if (!this.shouldRun) this.start();
    return () => this.subscribers.delete(cb);
  }
}

const stateBus = new StateBus();

// ─── React hooks ────────────────────────────────────────────────────
//
// We reach the hooks via the `React.` prefix here rather than
// destructuring `const { useState, useEffect } = React;`. Babel-in-
// browser shares top-level scope across every
// `<script type="text/babel">` tag, so a destructure in data.js plus
// the same destructure in components.jsx would collide with a
// SyntaxError and kill the whole bundle. The verbose `React.X` form
// sidesteps that without forcing components.jsx (which is the
// canonical first-declared place for these names) to change.

// One-shot list fetch with refresh. `eventTypes` is a list of WS
// event types that should trigger a refetch (server doesn't always
// embed the full new payload, so a refetch is the safest move).
const useApiList = (path, { eventTypes = [], pickItems = (x) => x } = {}) => {
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  const refresh = React.useCallback(async () => {
    try {
      const data = await apiGet(path);
      setItems(pickItems(data) || []);
      setError(null);
    } catch (e) {
      console.warn(`fetch ${path}:`, e);
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [path]);

  React.useEffect(() => { refresh(); }, [refresh]);

  React.useEffect(() => {
    if (!eventTypes || eventTypes.length === 0) return;
    return stateBus.subscribe((ev) => {
      if (eventTypes.includes(ev.type)) refresh();
    });
  }, [refresh, eventTypes.join(',')]);

  return { items, loading, error, refresh, setItems };
};

// One-shot single-resource fetch (e.g. /api/config). Same shape as
// useApiList minus the array-ness — `data` instead of `items`.
const useApiObject = (path, { eventTypes = [] } = {}) => {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  const refresh = React.useCallback(async () => {
    // Skip when path is null/empty (drawers fetch conditionally
    // and pass null when they're closed). Without this guard,
    // `apiGet(null)` would issue a request against the literal
    // string 'null'.
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    try {
      setData(await apiGet(path));
      setError(null);
    } catch (e) {
      console.warn(`fetch ${path}:`, e);
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [path]);

  React.useEffect(() => { refresh(); }, [refresh]);
  React.useEffect(() => {
    if (!eventTypes || eventTypes.length === 0) return;
    return stateBus.subscribe((ev) => {
      if (eventTypes.includes(ev.type)) refresh();
    });
  }, [refresh, eventTypes.join(',')]);

  return { data, loading, error, refresh };
};

// Debounce any value. Tail-edge: returns the latest value after it
// stops changing for `delay` ms. Used to avoid refetching on every
// keystroke when a filter input drives a server-side query.
const useDebouncedValue = (value, delay = 250) => {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
};

// Subscribe to specific WS event types and feed every event to a
// callback. Useful for streaming updates (now-playing tick, satellite
// presence) where we don't want to refetch on every event.
const useStateEvents = (eventTypes, onEvent) => {
  const cbRef = React.useRef(onEvent);
  cbRef.current = onEvent;
  React.useEffect(() => {
    return stateBus.subscribe((ev) => {
      if (!eventTypes || eventTypes.length === 0 || eventTypes.includes(ev.type)) {
        cbRef.current?.(ev);
      }
    });
  }, [eventTypes.join(',')]);
};

// Sidebar count badges. Computed from the same lists each page uses;
// kept here so the App shell can render counts without each page
// having to lift state up.
const useSidebarCounts = () => {
  const [counts, setCounts] = React.useState({
    music: null, people: null, satellites: null, calendar: null,
  });

  React.useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const [music, people, sats, cal] = await Promise.all([
        // Library endpoint returns {total, items}; ask for 1 item so the
        // network payload stays tiny — we only want the count.
        apiGet('/api/music/library?limit=1').catch(() => null),
        apiGet('/api/people').catch(() => null),
        apiGet('/api/satellites').catch(() => null),
        apiGet('/api/calendar/events').catch(() => null),
        // Plugin pages declare their own sidebar badges in the plugin
        // manifest (usePluginBadges in components.jsx) — nothing
        // plugin-specific is fetched here.
      ]);
      if (cancelled) return;
      const lenOf = (x) => Array.isArray(x) ? x.length : (x?.total ?? x?.items?.length ?? null);
      setCounts({
        music: typeof music?.total === 'number' ? music.total : null,
        people: lenOf(people),
        satellites: lenOf(sats),
        calendar: lenOf(cal),
      });
    };
    refresh();
    // Only refetch on events that actually CHANGE counts. Earlier
    // version subscribed to `people.last_seen.changed` and
    // `satellites.presence.changed` too — but those don't add or
    // remove rows from their tables (`last_seen` is a timestamp
    // update; presence is online/offline flag, not row creation), and
    // they fire frequently enough during normal operation to flood
    // the network with 5× parallel sidebar GETs each time. The
    // events listed here are the ones whose underlying tables
    // actually gain/lose rows.
    const off = stateBus.subscribe((ev) => {
      if (
        ev.type === 'library.indexer.changed'
        || ev.type === 'calendar.events.changed'
      ) refresh();
    });
    return () => { cancelled = true; off(); };
  }, []);

  return counts;
};

// ─── Time helpers (page-local NOW vs reference NOW) ─────────────────
// The skill's components.jsx exposes a frozen NOW for sample data.
// The wired pages need wall-clock NOW so relative times tick.
const liveNow = () => new Date();
const liveRelTime = (iso) => {
  if (!iso) return '—';
  const t = new Date(iso);
  const s = (liveNow() - t) / 1000;
  if (s < 30) return 'just now';
  if (s < 90) return '1m ago';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

// Expose to other Babel scripts (mirrors components.jsx's pattern).
Object.assign(window, {
  apiGet, apiPost, apiPatch, apiDelete, deviceDownload,
  stateBus, ServerStore,
  useApiList, useApiObject, useStateEvents, useSidebarCounts,
  useDebouncedValue,
  liveNow, liveRelTime,
});
