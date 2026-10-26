/* Co-editing for Documents: window.DomovoiCollab.
 *
 *   const collab = await DomovoiCollab.open('Shopping list.html');
 *   // → { ydoc, provider, user, close(), ... }   (a Promise: open() asks the
 *   //   server for the document's id first, or reads it from the cache
 *   //   when offline)
 *
 * Hand `collab` to the editor (builder A: DomovoiDocEditor.create(el,
 * {collab})). The editor binds Tiptap's Collaboration extension to
 * `collab.ydoc` (XmlFragment "default") and CollaborationCursor to
 * `collab.provider` with `collab.user`.
 *
 * What the editor has to do besides binding (web/backend/collab/__init__.py
 * has the why):
 *   * stay read-only until `collab.ready` (await `collab.whenReady`), and
 *     stay read-only when `collab.readonly`;
 *   * SEED: `collab.onSeed(async (ydoc) => editor.commands.setContent(...))`.
 *     The helper runs it once, after sync, only on the one client the
 *     server picked, then reports it (markSeeded). The file's content goes
 *     into the shared document exactly once. (An editor that seeds on its
 *     own when `collab.seed` is true must call `collab.markSeeded()` after.)
 *   * SAVE: `collab.setSaver(async ({ headers }) => { ... })`. The saver
 *     serializes the editor and saves through the ordinary Documents route
 *     with `headers` added (X-Domovoi-Collab). It is called when the server
 *     asks (this client edited last and typing stopped) and on close();
 *   * RESET: on the 'reset' event (the file was changed outside the editor
 *     and the shared document rebuilt from it), close and open again.
 *
 * Offline: y-indexeddb keeps a copy of the shared document in this browser,
 * so a phone that loses the network keeps editing, and its edits merge when
 * it reconnects. The provider retries on its own.
 *
 * Plain script, not Babel: it reads data.js's helpers (API_BASE, apiFetch,
 * deviceToken, wsDeviceSubprotocols, DeviceIdentity) only when open() runs.
 */
(function () {
  'use strict';

  var CONTROL = 100;
  var YJS_BUNDLE = '/vendor/yjs/yjs.min.js';
  var CACHE_KEY = 'domovoi-collab-docs';
  var SAVE_ON_CLOSE_WAIT_MS = 4000;
  var HELLO_WAIT_MS = 4000;

  var loading = {};
  function loadScript(src) {
    if (loading[src]) return loading[src];
    loading[src] = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src;
      s.async = false;
      s.onload = function () { resolve(); };
      s.onerror = function () { delete loading[src]; reject(new Error('could not load ' + src)); };
      document.head.appendChild(s);
    });
    return loading[src];
  }

  /* One Yjs in the page: the editor's bundle (web/static/vendor/yjs, builder
   * A) sets window.Y and, sharing it, window.YKit (y-websocket's client
   * provider, y-indexeddb, y-protocols, lib0). Co-editing uses exactly
   * those; it carries no Yjs of its own. */
  function ensureLibs() {
    if (window.Y && window.YKit) return Promise.resolve();
    var ed = window.DomovoiDocEditor;
    if (ed && typeof ed.loadYjs === 'function') return Promise.resolve(ed.loadYjs());
    return loadScript(YJS_BUNDLE);
  }

  function g(name) {
    try {
      // data.js's top-level consts live in the shared global scope, not on window.
      // eslint-disable-next-line no-new-func
      return Function('return typeof ' + name + " !== 'undefined' ? " + name + ' : undefined')();
    } catch (e) { return undefined; }
  }

  function apiBase() { return g('API_BASE') || ''; }

  function serverKey() {
    var b = apiBase();
    if (!b) return window.location.host;
    try { return new URL(b).host; } catch (e) { return b; }
  }

  function wsBase() {
    var http = apiBase() || (window.location.protocol + '//' + window.location.host);
    return http.replace(/^http/, 'ws') + '/api/collab';
  }

  function readCache() {
    try { return JSON.parse(localStorage.getItem(CACHE_KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function writeCache(path, meta) {
    try {
      var all = readCache();
      all[serverKey() + '|' + path] = {
        doc_id: meta.doc_id, epoch: meta.epoch, user: meta.user, readonly: meta.readonly,
        at: Date.now(),
      };
      // A phone opens a few dozen documents, not thousands; keep it small.
      var keys = Object.keys(all).sort(function (a, b) { return all[b].at - all[a].at; });
      keys.slice(200).forEach(function (k) { delete all[k]; });
      localStorage.setItem(CACHE_KEY, JSON.stringify(all));
    } catch (e) { /* private mode: no offline open, nothing else lost */ }
  }
  function cached(path) { return readCache()[serverKey() + '|' + path] || null; }

  function dbName(docId, epoch) {
    return 'domovoi-collab:' + serverKey() + ':' + docId + ':' + epoch;
  }

  /* Which offline copies this browser holds, by name, for browsers without
   * indexedDB.databases(): every copy opened is noted here and dropped when
   * it is deleted. */
  var DBS_KEY = 'domovoi-collab-dbs';
  function knownDbs() {
    try { return JSON.parse(localStorage.getItem(DBS_KEY) || '[]') || []; } catch (e) { return []; }
  }
  function noteDb(name, keep) {
    try {
      var all = knownDbs().filter(function (n) { return n !== name; });
      if (keep) all.push(name);
      localStorage.setItem(DBS_KEY, JSON.stringify(all.slice(-500)));
    } catch (e) { /* private mode */ }
  }

  function socketOptions(epoch) {
    var params = { epoch: String(epoch) };
    try {
      var DI = g('DeviceIdentity');
      var id = DI && DI.id && DI.id();
      if (id) params.device_id = id;
    } catch (e) { /* no device id: named "Someone" */ }
    var protocols = [];
    try {
      var tok = g('deviceToken') && g('deviceToken')();
      var mk = g('wsDeviceSubprotocols');
      protocols = (tok && mk && mk(tok)) || [];
    } catch (e) { protocols = []; }
    return { params: params, protocols: protocols };
  }

  var RESCUE_WAIT_MS = 8000;

  /* A copy of this document from an OLDER epoch is still in this browser:
   * the phone edited offline, the file was changed outside the editor
   * meanwhile, and the tab was closed before it came back online. Before
   * opening the current document, each such copy connects once with its
   * own epoch; the server keeps its edits as an "offline copy" version
   * (they cannot be merged into the rebuilt document) and answers "reset".
   * Only then is the copy deleted. Without an answer (offline, a refusal)
   * it stays for next time. */
  function rescueOldCopies(meta) {
    if (typeof indexedDB === 'undefined' || meta.offline) return Promise.resolve();
    var prefix = 'domovoi-collab:' + serverKey() + ':' + meta.doc_id + ':';
    var listed = (indexedDB.databases ? Promise.resolve(indexedDB.databases()).then(function (dbs) {
      return (dbs || []).map(function (d) { return d && d.name; });
    }) : Promise.resolve([])).catch(function () { return []; });
    return listed.then(function (names) {
      var seen = {};
      var old = names.concat(knownDbs()).filter(function (n) {
        if (!n || seen[n] || n.indexOf(prefix) !== 0) return false;
        seen[n] = true;
        var ep = parseInt(n.slice(prefix.length), 10);
        return ep >= 1 && ep < meta.epoch;
      });
      return old.reduce(function (p, name) {
        return p.then(function () {
          return rescueOne(name, parseInt(name.slice(prefix.length), 10), meta)
            .catch(function () { /* kept for next time */ });
        });
      }, Promise.resolve());
    });
  }

  function rescueOne(name, epoch, meta) {
    var Y = window.Y;
    var YC = window.YKit;
    var ydoc = new Y.Doc();
    var idb = new YC.IndexeddbPersistence(name, ydoc);
    var provider = null;
    var finish = function () {
      try { if (provider) provider.destroy(); } catch (e) {}
      try { idb.destroy(); } catch (e) {}
      try { ydoc.destroy(); } catch (e) {}
    };
    return idb.whenSynced.then(function () {
      var empty = Y.encodeStateVector(ydoc).length <= 1;
      if (empty) return true;
      var o = socketOptions(epoch);
      provider = new YC.WebsocketProvider(wsBase(), meta.doc_id, ydoc, {
        params: o.params, protocols: o.protocols, disableBc: true, connect: false,
      });
      provider.awareness.setLocalState(null);   // nobody needs to see this one
      return new Promise(function (resolve) {
        var t = setTimeout(function () { resolve(false); }, RESCUE_WAIT_MS);
        provider.messageHandlers[CONTROL] = function (_e, decoder) {
          var msg;
          try { msg = JSON.parse(YC.decoding.readVarString(decoder)); } catch (e) { return; }
          if (msg && msg.t === 'reset') { clearTimeout(t); resolve(true); }
        };
        provider.on('closed', function () { clearTimeout(t); resolve(false); });
        provider.connect();
      });
    }).then(function (answered) {
      if (!answered) { finish(); return; }
      return Promise.resolve(idb.clearData()).catch(function () {}).then(function () {
        noteDb(name, false);
        finish();
      });
    }, function (err) { finish(); throw err; });
  }

  function fetchMeta(path) {
    var apiFetch = g('apiFetch');
    var url = '/api/collab/doc?path=' + encodeURIComponent(path);
    if (apiFetch) return apiFetch(url, { quiet: true });
    return fetch(apiBase() + url, { credentials: 'include' }).then(function (r) {
      if (!r.ok) { var e = new Error(r.status + ' ' + r.statusText); e.status = r.status; throw e; }
      return r.json();
    });
  }

  function makeEmitter() {
    var handlers = {};
    return {
      on: function (evt, fn) { (handlers[evt] = handlers[evt] || []).push(fn); return fn; },
      off: function (evt, fn) { handlers[evt] = (handlers[evt] || []).filter(function (h) { return h !== fn; }); },
      emit: function (evt, arg) {
        (handlers[evt] || []).slice().forEach(function (h) {
          try { h(arg); } catch (e) { console.warn('collab listener failed', e); }
        });
      },
      has: function (evt) { return !!(handlers[evt] && handlers[evt].length); },
    };
  }

  function sendControl(provider, payload) {
    var ws = provider.ws;
    if (!ws || ws.readyState !== 1) return false;
    var enc = window.YKit.encoding;
    var e = enc.createEncoder();
    enc.writeVarUint(e, CONTROL);
    enc.writeVarString(e, JSON.stringify(payload));
    ws.send(enc.toUint8Array(e));
    return true;
  }

  /* What a person reads in the status line. */
  function statusText(c) {
    if (c.closedReason) return c.closedReason;
    if (c.status === 'connected' && c.synced) return c.readonly ? 'View only' : 'Saved';
    if (c.status === 'connected') return 'Connecting…';
    return 'Offline: changes will sync';
  }

  var CLOSE_TEXT = {
    4401: 'This device needs pairing again to edit',
    4403: 'Not allowed from this page',
    4404: 'This document is no longer there',
    4409: 'The document changed outside the editor',
    4413: 'This document is too large to edit together',
    4410: 'This document was moved or renamed',
    4415: 'This kind of document can’t be edited together',
    4429: 'Too many wrong passwords from here: wait a few minutes',
  };

  function open(docPath, opts) {
    opts = opts || {};
    return ensureLibs().then(function () {
      return fetchMeta(docPath).then(function (meta) {
        writeCache(docPath, meta);
        return meta;
      }, function (err) {
        // Offline (or the server restarting): open from this browser's own
        // copy when there is one. Refusals (401/403/404) are not offline.
        var c = cached(docPath);
        if (c && !(err && err.status && err.status < 500)) return Object.assign({ offline: true }, c);
        throw err;
      });
    }).then(function (meta) {
      return rescueOldCopies(meta).catch(function () {}).then(function () {
        return start(docPath, meta, opts);
      });
    });
  }

  function start(docPath, meta, opts) {
    var Y = window.Y;
    var YC = window.YKit;
    var ev = makeEmitter();
    var ydoc = new Y.Doc();
    var idb = null;
    // No IndexedDB (some private windows, a WebView without it): online
    // only. A store that refuses later (quota, a locked profile) costs the
    // offline copy, never the editing.
    if (typeof indexedDB !== 'undefined') {
      try {
        idb = new YC.IndexeddbPersistence(dbName(meta.doc_id, meta.epoch), ydoc);
        noteDb(dbName(meta.doc_id, meta.epoch), true);
        if (idb._db && idb._db.catch) idb._db.catch(function () {});
        if (idb.whenSynced && idb.whenSynced.catch) idb.whenSynced.catch(function () {});
      } catch (e) { idb = null; }
    }

    var so = socketOptions(meta.epoch);
    var params = so.params;
    var protocols = so.protocols;

    var provider = new YC.WebsocketProvider(wsBase(), meta.doc_id, ydoc, {
      params: params,
      protocols: protocols,
      disableBc: true,          // tabs of one browser sync through the server, never around it
      maxBackoffTime: 10000,
      connect: false,
    });

    var user = meta.user || { name: 'Someone', color: '#1971c2' };
    provider.awareness.setLocalStateField('user', { name: user.name, color: user.color });

    var resolveSynced, resolveReady;
    var collab = {
      ydoc: ydoc,
      provider: provider,
      user: user,
      path: docPath,
      docId: meta.doc_id,
      epoch: meta.epoch,
      readonly: !!meta.readonly,
      ready: false,
      mustSeed: false,
      synced: false,
      status: 'connecting',
      closedReason: null,
      offlineOpen: !!meta.offline,
      whenSynced: new Promise(function (r) { resolveSynced = r; }),
      whenReady: new Promise(function (r) { resolveReady = r; }),
      whenLocal: idb ? idb.whenSynced.then(function () { return true; }, function () { return false; })
                     : Promise.resolve(false),
      on: ev.on, off: ev.off,
      statusText: function () { return statusText(collab); },
      presence: function () { return presenceList(); },
      saveHeaders: function (rev) {
        return { 'X-Domovoi-Collab': collab.docId + ';' + collab.epoch + ';' + (rev == null ? 0 : rev) };
      },
      get seed() { return collab.mustSeed; },
      markSeeded: function () {
        collab.mustSeed = false;
        sendControl(provider, { t: 'seeded' });
      },
      /* The safe way to seed: fn(ydoc, collab) runs ONCE, after this client
       * has synced, only if the server picked this client; when it resolves
       * the helper calls markSeeded(). If it throws, the server hands the
       * job to another editor. Prefer this to reading mustSeed yourself. */
      onSeed: function (fn) { seeder = fn || null; maybeSeed(); },
      setSaver: function (fn) {
        saver = fn || null;
        sendControl(provider, { t: 'saver', on: !!saver });
      },
      close: close,
    };
    var saver = null;
    var saving = null;
    var closed = false;
    var seeder = null;
    var seeding = false;

    function maybeSeed() {
      if (!seeder || seeding || !collab.mustSeed || !collab.synced || collab.readonly) return;
      seeding = true;
      Promise.resolve().then(function () { return seeder(ydoc, collab); }).then(function () {
        collab.markSeeded();
      }, function (err) {
        console.warn('collab: seeding failed', err && err.message ? err.message : err);
        collab.mustSeed = false;
        sendControl(provider, { t: 'seed-failed' });
      }).then(function () { seeding = false; });
    }

    function presenceList() {
      var out = [];
      provider.awareness.getStates().forEach(function (state, clientId) {
        if (!state || !state.user) return;
        out.push({ clientId: clientId, name: String(state.user.name || 'Someone').slice(0, 60),
                   color: state.user.color || '#888', self: clientId === ydoc.clientID });
      });
      out.sort(function (a, b) { return (b.self ? 1 : 0) - (a.self ? 1 : 0) || a.name.localeCompare(b.name); });
      return out;
    }

    function setStatus(s) {
      if (collab.status === s) return;
      collab.status = s;
      ev.emit('status', { status: s, text: statusText(collab) });
    }

    function runSave(rev, leaving) {
      if (!saver || collab.readonly) return Promise.resolve(false);
      if (saving) return saving;
      saving = collab.whenSynced.then(function () {
        return saver({ rev: rev, headers: collab.saveHeaders(rev), leaving: !!leaving, collab: collab });
      }).then(function () { return true; }, function (err) {
        console.warn('collab: save failed', err && err.message ? err.message : err);
        sendControl(provider, { t: 'save-failed' });
        ev.emit('save-failed', err);
        return false;
      }).then(function (ok) { saving = null; return ok; });
      return saving;
    }

    var onControl = function (msg) {
      switch (msg.t) {
        case 'hello':
          collab.readonly = !!msg.readonly || !!msg.stale;
          collab.epoch = msg.epoch;
          if (msg.user) {
            collab.user = msg.user;
            provider.awareness.setLocalStateField('user', { name: msg.user.name, color: msg.user.color });
          }
          if (msg.seed) { collab.mustSeed = true; ev.emit('seed', collab); maybeSeed(); }
          if (msg.ready && !collab.ready) { collab.ready = true; resolveReady(collab); ev.emit('ready', collab); }
          if (saver && !collab.readonly) sendControl(provider, { t: 'saver', on: true });
          ev.emit('hello', msg);
          break;
        case 'seed':
          collab.mustSeed = true; ev.emit('seed', collab); maybeSeed(); break;
        case 'ready':
          if (!collab.ready) { collab.ready = true; resolveReady(collab); ev.emit('ready', collab); }
          break;
        case 'save':
          runSave(msg.rev, false); break;
        case 'saved':
          ev.emit('saved', msg); break;
        case 'restored':
          ev.emit('restored', msg); break;
        case 'limit':
          ev.emit('limit', msg); break;
        case 'gone':
          collab.closedReason = CLOSE_TEXT[4404]; ev.emit('gone', msg); break;
        case 'moved':
          // Renamed or moved in Documents: same document, new path. The
          // server closes this socket; the editor reopens it by the new one.
          collab.closedReason = CLOSE_TEXT[4410];
          var was = cached(docPath);
          if (was && typeof msg.path === 'string') writeCache(msg.path, was);
          ev.emit('moved', { path: msg.path, from: docPath });
          break;
        case 'reset':
          noteDb(dbName(collab.docId, collab.epoch), false);
          // The shared document was rebuilt from the file. This browser's
          // copy belongs to the old one: the server kept any offline edits
          // as a version (msg.kept), so the copy can go.
          collab.closedReason = CLOSE_TEXT[4409];
          var oldEpoch = collab.epoch;
          var c = cached(docPath);
          if (c) writeCache(docPath, Object.assign({}, c, { epoch: msg.epoch }));
          provider.disconnect();
          var drop = idb ? idb.clearData() : Promise.resolve();
          Promise.resolve(drop).catch(function () {}).then(function () {
            ev.emit('reset', { epoch: msg.epoch, from: oldEpoch, kept: !!msg.kept });
          });
          break;
        default:
          break;
      }
    };

    provider.messageHandlers[CONTROL] = function (_encoder, decoder) {
      var msg;
      try { msg = JSON.parse(YC.decoding.readVarString(decoder)); } catch (e) { return; }
      if (msg && typeof msg.t === 'string') onControl(msg);
    };

    provider.on('status', function (e) {
      setStatus(e.status === 'connected' ? 'connected' : e.status === 'connecting' ? 'connecting' : 'offline');
      if (e.status !== 'connected') { collab.synced = false; }
    });
    provider.on('sync', function (isSynced) {
      collab.synced = !!isSynced;
      if (isSynced) { resolveSynced(collab); maybeSeed(); }
      ev.emit('status', { status: collab.status, text: statusText(collab) });
    });
    provider.on('closed', function (e) {
      collab.closedReason = CLOSE_TEXT[e.code] || 'Disconnected';
      setStatus('closed');
      ev.emit('status', { status: 'closed', text: statusText(collab), code: e.code });
    });
    provider.awareness.on('change', function () { ev.emit('presence', presenceList()); });

    function onPageHide() {
      // Best effort: ask to save now while the page is still here.
      if (saver && !collab.readonly) sendControl(provider, { t: 'save-now' });
    }
    window.addEventListener('pagehide', onPageHide);

    function close() {
      if (closed) return Promise.resolve();
      closed = true;
      window.removeEventListener('pagehide', onPageHide);
      var done;
      if (saver && !collab.readonly && provider.wsconnected) {
        // Saved on leave: the server answers save-now with either a save
        // request (the file is behind) or "saved" (it isn't).
        done = new Promise(function (resolve) {
          var timer = setTimeout(resolve, SAVE_ON_CLOSE_WAIT_MS);
          var finish = function () { clearTimeout(timer); resolve(); };
          ev.on('saved', function (m) { if (m && m.reply === 'save-now') finish(); });
          var orig = onControl;
          onControl = function (msg) {
            if (msg.t === 'save') { runSave(msg.rev, true).then(finish); return; }
            orig(msg);
          };
          if (!sendControl(provider, { t: 'save-now' })) finish();
        });
      } else {
        done = Promise.resolve();
      }
      return done.then(function () {
        try { provider.destroy(); } catch (e) {}
        try { if (idb) idb.destroy(); } catch (e) {}
        try { ydoc.destroy(); } catch (e) {}
        ev.emit('closed', {});
      });
    }

    provider.connect();
    // Resolve once the server has said hello (so readonly, ready and
    // mustSeed are known), or straight away when opening offline, or after
    // a few seconds either way: an editor can open on its local copy and
    // pick the rest up from the events.
    if (meta.offline) return collab;
    return new Promise(function (resolve) {
      var t = setTimeout(function () { resolve(collab); }, HELLO_WAIT_MS);
      var done = function () { clearTimeout(t); resolve(collab); };
      ev.on('hello', done);
      provider.on('closed', done);
    });
  }

  window.DomovoiCollab = {
    open: open,
    ensureLibs: ensureLibs,
    /* For the history preview: a Y.Doc holding one version (base64 update). */
    docFromUpdate: function (b64) {
      return ensureLibs().then(function () {
        var bin = atob(b64);
        var bytes = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        var d = new window.Y.Doc();
        window.Y.applyUpdate(d, bytes);
        return d;
      });
    },
    CLOSE_TEXT: CLOSE_TEXT,
  };
})();
