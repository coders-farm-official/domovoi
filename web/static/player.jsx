/* Browser Music Player — shared playback foundation.
 *
 * This file is the architectural heart of the browser music player. It
 * introduces an APP-LEVEL playback context that lives ABOVE the page
 * router (see index.html integration), so audio keeps playing while the
 * user navigates between pages. Everything the player needs — the Web
 * Audio graph, the queue, the current item, position, EQ, visualizer,
 * sleep timer, Media Session wiring, keyboard shortcuts, the offline
 * cache, and the cast target — lives here.
 *
 * It is deliberately FEATURE-AGNOSTIC. The queue holds generic "items"
 * (see the item shape below), not library-specific rows, so a later
 * Podcasts/Audiobooks feature can push podcast episodes / audiobook
 * chapters through the same graph and mini-player. Podcast-only concerns
 * (playbackRate memory, chapters, resume-from-position) are already
 * threaded through as generic hooks (`playbackRate`, `item.meta`,
 * `item.chapters`) that this feature simply doesn't populate yet.
 *
 * Loaded via Babel-in-browser like the rest of the bundle. It uses the
 * `React.` prefix for hooks (matching data.js) rather than destructuring,
 * because Babel-in-browser shares one top-level scope across every
 * <script type="text/babel"> tag and components.jsx already declares
 * `const { useState, useEffect, useRef } = React`.
 *
 * ── Generic queue item shape ───────────────────────────────────────────
 *   {
 *     uid:        string   // stable unique id within the queue
 *     kind:       'library' | 'radio'      // (future: 'podcast','audiobook')
 *     trackId:    number | null            // library_tracks.id when kind==='library'
 *     title, artist, album: string
 *     src:        string   // audio URL (Range endpoint / radio proxy)
 *     coverUrl:   string | null
 *     durationSec:number | null
 *     seekable:   boolean  // false for live radio
 *     cacheable:  boolean  // false for live radio
 *     meta:       object   // free-form (podcast chapters / resume, later)
 *   }
 */

/* ── EQ band definitions (10-band graphic EQ) ─────────────────────────── */
/* 8 kHz is written 8e3 so port-number greps never
 * false-positive on an audio frequency. */
const EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8e3, 16000];
const EQ_LABELS = ['31', '62', '125', '250', '500', '1k', '2k', '4k', '8k', '16k'];
const EQ_MIN_DB = -12;
const EQ_MAX_DB = 12;

/* Crossfade duration in seconds. 0 = gapless hard-cut (start next the
 * instant the current ends). Non-zero fades the outgoing/incoming pair. */
const DEFAULT_CROSSFADE_SEC = 0;

/* Client-side "recent" list, kept in localStorage so the offline
 * auto-cache has something to prioritise. It is not the listening history:
 * since 2026-11-12 (V051) each library song this browser plays is also
 * logged to the server when it ends (ListenLog, below), and that is what
 * Up next learns from. */
const RECENT_KEY = 'domovoi-player-recent';
const RECENT_MAX = 60;

/* ── URL builders (single source of truth for the new endpoints) ──────── */
/* Media srcs must honor the selected server (ServerStore / API_BASE in
 * data.js) — a relative src would stream from the origin that served the
 * page, not the backend the dashboard is pointed at. */
const absMedia = (p) => (p && p.startsWith('/') ? `${API_BASE}${p}` : p);
const playerUrls = {
  audio: (trackId) => absMedia(`/api/music/library/${trackId}/audio`),
  cover: (trackId) => absMedia(`/api/music/library/${trackId}/cover`),
};

/* ── Plugin player-source registry (design §5.2 step 4) ────────────────
 * Seeded by the app bootstrap from /api/plugins/manifest
 * `player_sources` entries: kind → { plugin, stream_url_template }.
 * A plugin page builds live-stream queue items via itemFromSource();
 * an unknown kind resolves to null and the item is skipped with a
 * console warn — never a crash. */
window.DomovoiPlayerSources = window.DomovoiPlayerSources || {};

const resolveSourceStreamUrl = (kind, id) => {
  const src = window.DomovoiPlayerSources[kind];
  if (!src || !src.stream_url_template) return null;
  return absMedia(src.stream_url_template.replace('{id}', encodeURIComponent(id)));
};

/* Build a generic live-stream queue item for a registered plugin
 * player-source kind. `row` needs {id, title}; `subtitle` renders in
 * the artist slot. Returns null (with a warning) for unknown kinds. */
const itemFromSource = (kind, row, { subtitle = '' } = {}) => {
  const src = resolveSourceStreamUrl(kind, row.id);
  if (!src) {
    console.warn(`player: unknown source kind "${kind}" — is its plugin enabled?`);
    return null;
  }
  return {
    uid: `${kind}-${row.id}-${Math.random().toString(36).slice(2, 7)}`,
    kind,
    trackId: null,
    itemId: row.id,
    title: row.title || row.name || kind,
    artist: subtitle || row.subtitle || '',
    album: '',
    src,
    coverUrl: row.coverUrl || null,
    durationSec: null,
    seekable: false,   // live streams
    cacheable: false,
    meta: {},
  };
};

/* Build a generic queue item from a library `Track` row (schemas.Track). */
const itemFromTrack = (t) => ({
  uid: `lib-${t.id}-${Math.random().toString(36).slice(2, 7)}`,
  kind: 'library',
  trackId: t.id,
  title: t.title || (t.file_path || '').split(/[\\/]/).pop() || 'unknown',
  artist: t.artist || '',
  album: t.album || '',
  src: playerUrls.audio(t.id),
  coverUrl: playerUrls.cover(t.id),
  durationSec: t.duration_sec ?? null,
  seekable: true,
  cacheable: true,
  meta: {},
});

/* ═══════════════════════════════════════════════════════════════════════
 * Up next (V053, phase P3, 2026-11-12): when a library song plays here, the
 * server keeps a session that hands out songs like it
 * (/api/music/up-next, web/backend/api/up_next.py). Its picks join the
 * queue as AUTO items, after every song somebody asked for:
 *   origin       'explicit' (asked for) | 'list' (a whole list played on
 *                purpose) | 'auto' (an Up next pick)
 *   upNextSeq    the pick's number in its session
 *   reasonLabel  why it was picked ("same artist", "90s", "from road trip")
 *   sessionId    the session the song belongs to (the listening history
 *                carries it back, which is how skips and finishes feed it)
 * The queue panels show auto items in their own "up next" section
 * (PlayerUpNext) with the Autoplay switch. Autoplay is remembered per
 * browser; off, nothing more is added and the queue stops at its end
 * (a song clicked inside a list still plays the rest of that list).
 * ═══════════════════════════════════════════════════════════════════════ */
const UP_NEXT_REFILL_AT = 5;          // ask for more when this few songs are left
const UP_NEXT_AUTOPLAY_KEY = 'domovoi-upnext-autoplay';
const UP_NEXT_IDS_CAP = 500;
const upNextAutoplayPref = () => {
  try { return localStorage.getItem(UP_NEXT_AUTOPLAY_KEY) !== '0'; } catch { return true; }
};
const _upNextSetAutoplayPref = (on) => {
  try { localStorage.setItem(UP_NEXT_AUTOPLAY_KEY, on ? '1' : '0'); } catch {}
};
/* A pick as a queue item. */
const itemFromPick = (pick, sessionId) => Object.assign(
  itemFromTrack({ id: pick.track_id, title: pick.title, artist: pick.artist, album: pick.album,
                  duration_sec: pick.duration_sec }),
  { origin: 'auto', upNextSeq: pick.seq, reasonLabel: pick.reason_label || '', sessionId });
/* Up next's calls that nobody pressed a button for (the start that rides
 * on a play, a refill, a removed pick): `noPrompt`, so a refused sign-in
 * never opens a prompt over the music. */
const _upNextCall = (method, path, body) => apiFetch(path, {
  method, body: JSON.stringify(body || {}), noPrompt: true });
/* This browser may ask for Up next: it is paired, or signed in (and the
 * page has its data layer: a test harness without one asks nothing). */
const _upNextPaired = () => {
  if (typeof apiFetch !== 'function') return false;
  try { return typeof Auth === 'undefined' || !!(Auth.isPaired ? Auth.isPaired() : false) || !!(Auth.isLoggedIn && Auth.isLoggedIn()); }
  catch { return false; }
};
/* A session from the server, as the provider keeps it. */
const _upNextFrom = (s) => ({
  id: s.id, mode: s.mode, autoplay: !!s.autoplay, person: s.person || null, seed: s.seed || null,
  haveThrough: s.have_through_seq || 0, ended: !!s.ended, loading: false, offline: false,
});
/* Whose taste a new mix follows, as a start or a cast sends it: the
 * "listening as" person picked here, "household" picked on purpose (or a
 * shared screen with no pick), else nothing (the server uses this
 * browser's claimed person). */
const upNextListenerFields = () => {
  let shared = false;
  try { shared = !!(DeviceIdentity.sharedScreen && DeviceIdentity.sharedScreen() === true); } catch {}
  let picked;
  try { picked = SpokenAudio.explicitPerson(); } catch { picked = undefined; }
  if (picked === 'none') return { as_household: true };
  if (picked !== undefined && picked !== null) return { listener_person_id: picked };
  return shared ? { as_household: true } : {};
};

/* Where an explicit "add to queue" lands: after the last song somebody
 * asked for, before the first Up next pick still to come. */
const _upNextAddAt = (q, index) => {
  for (let i = Math.max(0, index + 1); i < q.length; i++) if (q[i] && q[i].origin === 'auto') return i;
  return q.length;
};

/* ═══════════════════════════════════════════════════════════════════════
 * Offline cache manager (PWA). Stores audio blobs in Cache Storage under a
 * named cache, and pin/LRU metadata in localStorage. Manual pins never
 * evict; auto-cached (recent/favorite) entries evict LRU under a budget.
 * Radio is never cacheable (live). Best-effort — every method degrades to a
 * no-op if the browser lacks Cache Storage.
 * ═══════════════════════════════════════════════════════════════════════ */
const OfflineCache = (() => {
  const CACHE_NAME = 'domovoi-audio-v1';
  const META_KEY = 'domovoi-offline-meta';
  const DEFAULT_BUDGET_BYTES = 500 * 1024 * 1024; // 500 MB

  const supported = () => typeof caches !== 'undefined';

  const loadMeta = () => {
    try { return JSON.parse(localStorage.getItem(META_KEY) || '{}'); }
    catch { return {}; }
  };
  const saveMeta = (m) => {
    try { localStorage.setItem(META_KEY, JSON.stringify(m)); } catch {}
  };

  // meta shape: { [trackId]: { pinned:bool, bytes:num, at:ms, title, artist } }
  const isCached = (trackId) => {
    const m = loadMeta();
    return !!m[trackId];
  };
  const isPinned = (trackId) => {
    const m = loadMeta();
    return !!(m[trackId] && m[trackId].pinned);
  };

  const _touch = (trackId) => {
    const m = loadMeta();
    if (m[trackId]) { m[trackId].at = Date.now(); saveMeta(m); }
  };

  const _budget = () => {
    const raw = Number(localStorage.getItem('domovoi-offline-budget'));
    return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_BUDGET_BYTES;
  };
  const setBudget = (bytes) => {
    try { localStorage.setItem('domovoi-offline-budget', String(bytes)); } catch {}
  };

  const usage = () => {
    const m = loadMeta();
    return Object.values(m).reduce((a, e) => a + (e.bytes || 0), 0);
  };

  const _evictIfNeeded = async (incomingBytes) => {
    const m = loadMeta();
    let used = usage();
    if (used + incomingBytes <= _budget()) return;
    // Evict least-recently-used unpinned entries until we fit.
    const candidates = Object.entries(m)
      .filter(([, e]) => !e.pinned)
      .sort((a, b) => (a[1].at || 0) - (b[1].at || 0));
    const cache = await caches.open(CACHE_NAME);
    for (const [tid, e] of candidates) {
      if (used + incomingBytes <= _budget()) break;
      await cache.delete(playerUrls.audio(tid));
      used -= (e.bytes || 0);
      delete m[tid];
    }
    saveMeta(m);
  };

  /* Download a track's audio into the cache. `pinned` marks a manual pin
   * (never auto-evicted). Returns true on success. */
  const cache = async (item, { pinned = false } = {}) => {
    if (!supported() || !item || !item.cacheable || item.trackId == null) return false;
    const trackId = item.trackId;
    const url = playerUrls.audio(trackId);
    try {
      const resp = await fetch(url);
      if (!resp.ok) return false;
      const blob = await resp.clone().blob();
      await _evictIfNeeded(blob.size);
      const store = await caches.open(CACHE_NAME);
      await store.put(url, resp);
      const m = loadMeta();
      m[trackId] = {
        pinned: pinned || !!(m[trackId] && m[trackId].pinned),
        bytes: blob.size, at: Date.now(),
        title: item.title, artist: item.artist,
      };
      saveMeta(m);
      return true;
    } catch (e) {
      console.warn('offline cache failed', e);
      return false;
    }
  };

  const pin = (item) => cache(item, { pinned: true });
  const autoCache = (item) => cache(item, { pinned: false });

  const unpin = async (trackId) => {
    if (!supported()) return;
    try {
      const store = await caches.open(CACHE_NAME);
      await store.delete(playerUrls.audio(trackId));
    } catch {}
    const m = loadMeta();
    delete m[trackId];
    saveMeta(m);
  };

  const list = () => loadMeta();

  return { supported, isCached, isPinned, pin, unpin, autoCache, cache,
           list, usage, setBudget, budget: _budget, touch: _touch };
})();

/* ═══════════════════════════════════════════════════════════════════════
 * Spoken audio (podcasts + audiobooks) — resume positions, chapters, speed,
 * and the "listening as [person]" identity. Extends the generic player
 * WITHOUT forking it: spoken items are ordinary queue items with
 * kind='podcast'|'audiobook' and a populated `meta`/`chapters`. The browser
 * has no voice ID, so a lightweight person selector (persisted per client)
 * feeds `person_id`; `device_id` is a stable per-client id.
 * ═══════════════════════════════════════════════════════════════════════ */
const SpokenAudio = (() => {
  const PERSON_KEY = 'domovoi-listener-person';

  // One device id for the whole dashboard — DeviceIdentity (data.js) owns
  // the same 'domovoi-client-id' key this used to mint itself. Keeping them
  // unified is what makes "added by <device>" and the resume positions refer
  // to the same thing, and what makes an admin device block cover both.
  const clientId = () => DeviceIdentity.id();
  /* What this browser was told to listen as: a person id, 'none' (nobody
   * in particular, on purpose), or undefined (nothing picked: follow the
   * device). */
  const explicitPerson = () => {
    try {
      const v = localStorage.getItem(PERSON_KEY);
      if (v === 'none') return 'none';
      return v ? Number(v) : undefined;
    } catch { return undefined; }
  };
  /* The person whose this browser's listening is: the one picked in
   * "listening as", else the person this browser is claimed by ("this is
   * me" on the People page), unless it is a shared screen. null = the
   * household. */
  const devicePerson = () => {
    try {
      if (DeviceIdentity.sharedScreen && DeviceIdentity.sharedScreen() === true) return null;
      const o = DeviceIdentity.owner ? DeviceIdentity.owner() : null;
      return o ? o.id : null;
    } catch { return null; }
  };
  const getPerson = () => {
    const v = explicitPerson();
    if (v === 'none') return null;
    return v !== undefined ? v : devicePerson();
  };
  /* Picking the device's own person (or nobody, on a browser nobody
   * claimed) goes back to following the device; "nobody" on a claimed
   * browser is remembered as such. */
  const setPerson = (pid) => {
    try {
      const dev = devicePerson();
      if (pid == null) {
        if (dev == null) localStorage.removeItem(PERSON_KEY);
        else localStorage.setItem(PERSON_KEY, 'none');
      } else if (Number(pid) === dev) localStorage.removeItem(PERSON_KEY);
      else localStorage.setItem(PERSON_KEY, String(pid));
    } catch {}
  };

  // ── Endpoint helpers (podcast episode vs audiobook differ in path) ──
  const posUrl = (item) => {
    const pid = getPerson();
    const q = `device_id=${encodeURIComponent(clientId())}` + (pid != null ? `&person_id=${pid}` : '');
    if (item.kind === 'podcast') return { get: `/api/podcasts/positions/${item.itemId}?${q}`, post: `/api/podcasts/positions/${item.itemId}` };
    if (item.kind === 'audiobook') return { get: `/api/audiobooks/${item.itemId}/position?${q}`, post: `/api/audiobooks/${item.itemId}/position` };
    return null;
  };

  const fetchPosition = async (item) => {
    const u = posUrl(item);
    if (!u) return null;
    try { return await apiGet(u.get); } catch { return null; }
  };
  // `positionSec` is the place in the ITEM; a folder book's file items
  // carry their start in the book (meta.offsetSec), and the server keeps
  // one place per book.
  const savePosition = async (item, positionSec, speed) => {
    const u = posUrl(item);
    if (!u) return;
    const offset = (item.meta && item.meta.offsetSec) || 0;
    const body = { device_id: clientId(), position_sec: Math.round(offset + positionSec) };
    const pid = getPerson();
    if (pid != null) body.person_id = pid;
    if (speed != null) body.speed = speed;
    try { await apiPost(u.post, body); } catch {}
  };

  // Forget the "listening as" pick (a device marked a shared screen).
  const forgetPick = () => { try { localStorage.removeItem(PERSON_KEY); } catch {} };

  return { clientId, getPerson, setPerson, explicitPerson, devicePerson, forgetPick,
           fetchPosition, savePosition };
})();

/* ═══════════════════════════════════════════════════════════════════════
 * Listening history (V051, Up next P1, 2026-11-12). Each LIBRARY song this
 * browser plays is one row on the server, written when it ends:
 *   track_id, this browser's id, when it started, how long it really
 *   played (wall clock while playing; a pause or a seek adds nothing, and
 *   a song that runs to its end is NOT padded up to its length: owner
 *   decision 2026-11-14, count real time), its length, and how it ended:
 *   `finished` when it ran to its own end or at 90 % or more, else `skipped` (next,
 *   or a queue row picked) or `stopped` (stop, a new play, casting to a
 *   room, the page closed).
 * Whose it is: the "listening as" person when one was picked, "nobody" as
 * picked, else the server's own answer (the person this browser is
 * claimed by, unless it is a shared screen). The same rule as the rooms
 * (domovoi/music_listens.py outcome_for).
 *
 * Rows wait in a small outbox in localStorage and go to POST
 * /api/music/listens in batches: a few seconds after a song ends, when the
 * browser comes back online, and on pagehide (a keepalive fetch, which can
 * carry the household token; sendBeacon can't set a header). An unsent
 * row survives a reload; the server drops rows more than 7 days old. Only
 * a refused BATCH SHAPE (a 4xx other than 401/403/408/429) drops rows here.
 * Streams, podcasts, audiobooks and files outside the library are never
 * logged. Nothing is sent from a browser that isn't paired.
 * ═══════════════════════════════════════════════════════════════════════ */
const ListenLog = (() => {
  const OUTBOX_KEY = 'domovoi-listen-outbox';
  const OUTBOX_MAX = 500;
  const BATCH = 200;
  const PAGEHIDE_BATCH = 100;   // a keepalive body is capped at 64 KB
  const FINISHED_SHARE = 0.9;
  const KEPT_ERRORS = [401, 403, 408, 429];
  let cur = null;   // { uid, trackId, startedAt, played, since, duration, skipped }
  let timer = null;
  let sending = false;

  const load = () => {
    try { const v = JSON.parse(localStorage.getItem(OUTBOX_KEY) || '[]'); return Array.isArray(v) ? v : []; }
    catch { return []; }
  };
  const save = (rows) => {
    try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(rows.slice(-OUTBOX_MAX))); } catch {}
  };
  // Remove exactly the rows a flush sent, by their uid: a song that ended
  // while that flush was on its way is not dropped with them.
  const dropSent = (uids) => {
    const gone = new Set(uids);
    save(load().filter((r) => !gone.has(r.uid)));
  };
  const newUid = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const outcome = (played, duration, skipped, ended) => {
    if (ended) return 'finished';
    if (duration > 0 && played >= FINISHED_SHARE * duration) return 'finished';
    return skipped ? 'skipped' : 'stopped';
  };
  const playedNow = () => {
    if (!cur) return 0;
    return cur.played + (cur.since != null ? (Date.now() - cur.since) / 1000 : 0);
  };

  /* The song now playing here: `item` (a library one, or anything else),
   * and whether it is playing. Called on every change of item or status;
   * the same item again only moves the clock. */
  const follow = (item, playing, durationSec) => {
    const lib = item && item.kind === 'library' && item.trackId != null ? item : null;
    if (cur && (!lib || lib.uid !== cur.uid)) close();
    if (!lib) return;
    if (!cur) {
      cur = { uid: lib.uid, trackId: lib.trackId, startedAt: new Date().toISOString(),
              played: 0, since: null, duration: lib.durationSec || null, skipped: false,
              origin: lib.origin || 'explicit', sessionId: lib.sessionId || null };
    }
    // Up next: the session can arrive after the song started.
    if (lib.sessionId && !cur.sessionId) cur.sessionId = lib.sessionId;
    if (durationSec > 0 && Number.isFinite(durationSec)) cur.duration = durationSec;
    if (playing && cur.since == null) cur.since = Date.now();
    if (!playing && cur.since != null) { cur.played = playedNow(); cur.since = null; }
  };
  // The song now playing was skipped (next, a queue row picked).
  const skip = () => { if (cur) cur.skipped = true; };
  // The song now playing has ended, however. `natural`: it ran to its end.
  const close = (natural = false) => {
    if (!cur) return;
    // The time really heard: a natural end doesn't pad it up to the length
    // (a seek to the last seconds isn't listening).
    let played = playedNow();
    const duration = cur.duration || 0;
    if (duration > 0) played = Math.min(played, duration);
    const row = {
      uid: newUid(),
      track_id: cur.trackId, device_id: DeviceIdentity.id(), started_at: cur.startedAt,
      played_sec: Math.round(played), outcome: outcome(played, duration, cur.skipped && !natural, natural),
    };
    if (duration > 0) row.duration_sec = Math.round(duration);
    // An Up next session's song: the server feeds it back to the session.
    if (cur.sessionId) row.session_id = cur.sessionId;
    if (cur.origin === 'auto' || cur.origin === 'list') row.origin = cur.origin;
    // A shared screen's songs are the household's (the server says so too).
    let shared = false;
    try { shared = !!(DeviceIdentity.sharedScreen && DeviceIdentity.sharedScreen() === true); } catch {}
    const picked = shared ? undefined : SpokenAudio.explicitPerson();
    if (picked === 'none') row.as_household = true;
    else if (picked !== undefined) row.listener_person_id = picked;
    cur = null;
    if (played < 1) return;
    const rows = load();
    rows.push(row);
    save(rows);
    clearTimeout(timer);
    timer = setTimeout(() => flush(), 5000);
  };

  const paired = () => {
    try { return typeof Auth === 'undefined' || !!(Auth.isPaired() || Auth.isLoggedIn()); }
    catch { return false; }
  };
  /* Send what waits, through apiFetch with `noPrompt` (nobody pressed
   * anything, so a refusal never opens a prompt). `keepalive` (the page
   * is being hidden or closed): one smaller batch, a fetch the browser
   * finishes after the page has gone; the answer, if it comes, clears
   * what it took. */
  const flush = async ({ keepalive = false } = {}) => {
    if (sending || !paired()) return;
    const rows = load();
    if (!rows.length) return;
    const batch = rows.slice(0, keepalive ? PAGEHIDE_BATCH : BATCH);
    const uids = batch.map((r) => r.uid);
    // The uid is this outbox's own; the server isn't sent it.
    const body = JSON.stringify({ listens: batch.map((r) => { const o = Object.assign({}, r); delete o.uid; return o; }) });
    sending = !keepalive;
    try {
      await apiFetch('/api/music/listens', { method: 'POST', body, noPrompt: true, keepalive });
      dropSent(uids);
      if (!keepalive && load().length) setTimeout(() => flush(), 1000);
    } catch (e) {
      const st = e && e.status;
      // A batch the server can't take as it is would fail forever: drop it.
      if (st >= 400 && st < 500 && !KEPT_ERRORS.includes(st)) dropSent(uids);
    } finally { if (!keepalive) sending = false; }
  };

  /* Listen for the page going away and the network coming back once this
   * browser has played (or has left unsent) a library song: not at load,
   * so a page that never plays music adds no handlers. */
  let armed = false;
  const arm = () => {
    if (armed) return;
    armed = true;
    try {
      window.addEventListener('online', () => flush());
      window.addEventListener('pagehide', () => { close(); flush({ keepalive: true }); });
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') flush({ keepalive: true });
      });
    } catch { /* no window (a test harness) */ }
  };
  // What an earlier visit left behind goes out a little after load.
  try {
    setTimeout(() => { if (load().length) { arm(); flush(); } }, 8000);
  } catch { /* no timers */ }

  return { follow: (...a) => { if (a[0] && a[0].kind === 'library') arm(); return follow(...a); },
           skip, close, flush, outbox: load };
})();

/* Build a generic queue item from a downloaded podcast episode row.
 * `artwork` is the server path of the show's stored artwork (fix B11); a
 * remote URL is dropped, so the media session never makes the browser
 * fetch a publisher's image. */
const itemFromEpisode = (ep, showTitle, artwork) => ({
  uid: `pod-${ep.id}-${Math.random().toString(36).slice(2, 7)}`,
  kind: 'podcast',
  itemId: ep.id,
  trackId: null,
  title: ep.title || 'episode',
  artist: showTitle || 'podcast',
  album: showTitle || '',
  src: absMedia(`/api/podcasts/episodes/${ep.id}/audio`),
  coverUrl: (typeof artwork === 'string' && artwork.startsWith('/api/')) ? absMedia(artwork) : null,
  durationSec: ep.duration_sec ?? null,
  seekable: true,
  cacheable: true,
  chapters: ep.chapters || null,
  meta: { itemType: 'podcast_episode' },
});

/* Build a generic queue item from an audiobook row (single-file books). */
const itemFromBook = (book) => ({
  uid: `book-${book.id}-${Math.random().toString(36).slice(2, 7)}`,
  kind: 'audiobook',
  itemId: book.id,
  trackId: null,
  title: book.title || 'audiobook',
  artist: book.author || '',
  album: book.narrator || '',
  src: absMedia(`/api/audiobooks/${book.id}/audio`),
  coverUrl: absMedia(book.cover_url || book.artwork) || null,
  durationSec: book.duration_sec ?? null,
  seekable: true,
  cacheable: true,
  chapters: book.chapters || null,
  meta: { itemType: 'audiobook' },
});

/* The queue items an audiobook plays as: a per-chapter FOLDER book is one
 * item per file (the audio route serves it file by file, `?file=`), in
 * order, each knowing where it starts in the book (meta.offsetSec) so the
 * book keeps ONE resume position; a single-file book is itemFromBook.
 * Before 2026-10-20 a folder book was one item with no `?file=` and the
 * server refused it (400): it never played. */
const itemsFromBook = (book) => {
  const files = book && book.is_folder && Array.isArray(book.files)
    ? book.files.filter((f) => f && f.file) : [];
  if (!files.length) return [itemFromBook(book)];
  const tag = Math.random().toString(36).slice(2, 7);
  return files.map((f, i) => ({
    uid: `book-${book.id}-${i}-${tag}`,
    kind: 'audiobook',
    itemId: book.id,
    trackId: null,
    title: f.title || f.file,
    artist: book.author || '',
    album: book.title || '',
    src: absMedia(`/api/audiobooks/${book.id}/audio?file=${encodeURIComponent(f.file)}`),
    coverUrl: absMedia(book.cover_url || book.artwork) || null,
    durationSec: f.duration_sec ?? null,
    seekable: true,
    cacheable: true,
    chapters: null,
    meta: { itemType: 'audiobook', offsetSec: f.start_sec || 0 },
  }));
};

/* ═══════════════════════════════════════════════════════════════════════
 * Playback context + provider.
 * ═══════════════════════════════════════════════════════════════════════ */

/* Safe default so pages that call usePlayback() before the provider is
 * mounted (e.g. during the pre-integration window, or a page rendered
 * outside the shell in a test) don't crash — they see `available:false`
 * and hide the browser-player affordances. */
const _noop = () => {};

/* Casting to a room hands the server library track ids; a room plays from the
 * domovoi's own library, so podcasts and audiobooks (no library id) stay in
 * the browser. The room starts on the FIRST id sent, so callers pass the
 * queue from the current item on, never the whole queue. An error carrying
 * `castRefused` says why nothing went, in words for the person. */
const _castTrackIds = (items) => (items || [])
  .filter((it) => it && it.kind === 'library' && it.trackId != null)
  .map((it) => it.trackId);
const _castRefused = () => {
  const e = new Error('only library songs can be cast to a room — nothing from here on is in the library');
  e.castRefused = true;
  return e;
};
/* Where a room playing this queue has got to: from `index` on, the first
 * library item whose title is the room's now-playing title (`np` is the
 * room's /api/music/now-playing row), at the room's elapsed time when the
 * room is on that very item. Without a usable reading: `index`, from its top
 * (`known: false`). The Android app's CastPlanner.followRoom / handBack. */
const _castFollowRoom = (queue, index, np) => {
  const q = queue || [];
  const from = Math.max(0, Math.min(index, q.length - 1));
  const title = np && np.song && np.song.title;
  if (title) {
    for (let i = from; i < q.length; i++) {
      const it = q[i];
      if (it && it.kind === 'library' && it.title === title) {
        const sec = Number(np.elapsed_sec);
        return { at: i, sec: Number.isFinite(sec) && sec > 0 ? sec : 0, known: true };
      }
    }
  }
  return { at: from, sec: 0, known: false };
};
/* A room's transport control (pause / resume / skip / previous), waited
 * for: resolves when the room did it, rejects when it didn't. The core
 * answers a control MPD never got with 502 / 409 / 503 since 2026-10-01
 * (before, a 200 carrying "I couldn't reach the music player.", which every
 * hand-off took for "paused"); a 2xx saying `ok: false` is refused too. */
const _roomControl = async (verb, roomId) => {
  const r = await apiPost(`/api/music/${verb}/${roomId}`);
  if (r && r.ok === false) throw new Error(`${verb} didn't happen in ${roomId}`);
  return r;
};
const PlaybackContext = React.createContext({
  available: false,
  queue: [], index: -1, current: null, status: 'stopped',
  positionSec: 0, durationSec: 0, buffered: 0,
  volume: 1, muted: false,
  eqBands: EQ_BANDS.map(() => 0), eqEnabled: false,
  playbackRate: 1,
  target: { kind: 'browser' },
  // The cast room's last now-playing reading, while casting (lyrics.jsx
  // follows a room by it): {room_id, track_id, state, elapsed_sec,
  // duration_sec, readAt}; null when the target is this browser.
  remoteNp: null,
  sleepRemainingSec: null,
  recent: [],
  listenerPersonId: null, setListenerPersonId: _noop,
  spoken: SpokenAudio,
  playSpoken: _noop, jumpToChapter: _noop,
  itemFromEpisode: (x) => x, itemFromBook: (x) => x, itemsFromBook: (x) => [x],
  playItems: _noop, enqueue: _noop, playNext: _noop,
  upNext: null, autoplay: true, playFromList: _noop, playList: _noop,
  setAutoplay: _noop, lessLikeThis: _noop, setUpNextListener: _noop,
  removeAt: _noop, moveItem: _noop, clearQueue: _noop, jumpTo: _noop,
  toggle: _noop, play: _noop, pause: _noop, stop: _noop, closePlayer: _noop,
  next: _noop, prev: _noop, seek: _noop, seekBy: _noop,
  setVolume: _noop, toggleMute: _noop,
  setEqBand: _noop, resetEq: _noop, setEqEnabled: _noop,
  setPlaybackRate: _noop,
  setSleep: _noop, cancelSleep: _noop,
  castTo: _noop,
  getAnalyser: () => null,
  offline: OfflineCache,
});

const usePlayback = () => React.useContext(PlaybackContext);

/* The volume a browser starts at, from what it stored last time (a string,
 * or null when it never stored one). Nothing stored is full volume:
 * `Number(null)` is 0, which used to start every new browser silent — and
 * the effect below then stored that 0, so it stayed silent, with the
 * volume only reachable from the desktop bar or the phone's player sheet.
 * A stored 0 is read the same way: it is what that bug left behind, and
 * starting muted-by-volume is never what someone opening the player
 * wants (mute itself is not remembered either). */
const _playerStartVolume = (raw) => {
  const v = raw == null || String(raw).trim() === '' ? NaN : Number(raw);
  return Number.isFinite(v) && v > 0 && v <= 1 ? v : 1;
};

const PlaybackProvider = ({ children }) => {
  // ── Queue + current item ───────────────────────────────────────────
  const [queue, setQueue] = React.useState([]);
  const [index, setIndex] = React.useState(-1);
  const [status, setStatus] = React.useState('stopped'); // playing|paused|loading|stopped
  const [positionSec, setPositionSec] = React.useState(0);
  const [durationSec, setDurationSec] = React.useState(0);
  const [buffered, setBuffered] = React.useState(0);
  const [volume, setVolumeState] = React.useState(() => {
    try { return _playerStartVolume(localStorage.getItem('domovoi-player-volume')); }
    catch { return 1; }
  });
  const [muted, setMuted] = React.useState(false);
  const [eqBands, setEqBands] = React.useState(() => {
    try { const s = JSON.parse(localStorage.getItem('domovoi-player-eq') || 'null');
          if (Array.isArray(s) && s.length === EQ_BANDS.length) return s; } catch {}
    return EQ_BANDS.map(() => 0);
  });
  const [eqEnabled, setEqEnabledState] = React.useState(
    () => localStorage.getItem('domovoi-player-eq-on') === '1');
  const [playbackRate, setPlaybackRateState] = React.useState(1);
  const [target, setTarget] = React.useState({ kind: 'browser' });
  // The target as of the last change, read at once. A cast picked while
  // another is still on its way runs after it (castTo) and has to start from
  // where that one left things, which this render's `target` doesn't show.
  // Every change of target goes through moveTarget.
  const targetRef = React.useRef(target);
  const moveTarget = (t) => { targetRef.current = t; setTarget(t); };
  // Counts the "play here"s (playItems / playSpoken). A cast picked before
  // one and still on its way when it comes has lost: it does nothing more
  // (castToNow), and a room that already took its queue is paused again.
  // Before 2026-10-01 the cast landed after the play here and silenced it.
  const playHereGenRef = React.useRef(0);
  const statusRef = React.useRef(status); statusRef.current = status;
  const [sleepRemainingSec, setSleepRemainingSec] = React.useState(null);
  const [recent, setRecent] = React.useState(() => {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; }
  });
  // "Listening as [person]" — browser has no voice ID, so spoken-audio resume
  // positions key off this (defaults to the dashboard user = null/anon).
  const [listenerPersonId, setListenerPersonIdState] = React.useState(() => SpokenAudio.getPerson());
  const setListenerPersonId = React.useCallback((pid) => {
    SpokenAudio.setPerson(pid); setListenerPersonIdState(SpokenAudio.getPerson());
  }, []);
  // Nothing picked here follows the device: when the server says whose
  // this browser is ("this is me", People page) or that it is a shared
  // screen, "listening as" moves with it.
  React.useEffect(() => {
    const sync = () => {
      // Marked a shared screen: a "listening as" pick saved here goes.
      try { if (DeviceIdentity.sharedScreen && DeviceIdentity.sharedScreen() === true) SpokenAudio.forgetPick(); } catch {}
      setListenerPersonIdState(SpokenAudio.getPerson());
    };
    const offs = [];
    try {
      if (DeviceIdentity.subscribeOwner) offs.push(DeviceIdentity.subscribeOwner(sync));
      if (DeviceIdentity.subscribeShared) offs.push(DeviceIdentity.subscribeShared(sync));
    } catch { /* data.js absent */ }
    sync();
    return () => offs.forEach((off) => { try { off(); } catch {} });
  }, []);

  const current = index >= 0 && index < queue.length ? queue[index] : null;

  // ── Up next (the session this browser's queue is following) ─────────
  // null: none. Else { id, mode, autoplay, person, seed, haveThrough,
  // ended, loading, offline, failedAt }. The ref is read by callbacks made
  // once; every change goes through setUpNext. upNextGenRef counts the
  // plays that started (or dropped) a session: an answer for an older one
  // is ignored.
  const [upNext, setUpNextState] = React.useState(null);
  const upNextRef = React.useRef(null);
  const setUpNext = (v) => {
    const nv = typeof v === 'function' ? v(upNextRef.current) : v;
    upNextRef.current = nv;
    setUpNextState(nv);
  };
  const upNextGenRef = React.useRef(0);
  const refillingRef = React.useRef(false);
  const [autoplayPref, setAutoplayPrefState] = React.useState(upNextAutoplayPref);
  const autoplayPrefRef = React.useRef(autoplayPref); autoplayPrefRef.current = autoplayPref;
  const dropUpNext = () => { upNextGenRef.current += 1; refillingRef.current = false; setUpNext(null); };

  // ── Web Audio graph (lazily built on first play — needs a user gesture) ──
  const graphRef = React.useRef(null);   // { ctx, els:[A,B], gains, eq, analyser, master, active }
  const crossfadeSec = DEFAULT_CROSSFADE_SEC;
  const rafRef = React.useRef(0);
  const sleepTimerRef = React.useRef(null);
  const sleepEndOfTrackRef = React.useRef(false);
  const remotePollRef = React.useRef(null);
  const remoteNpRef = React.useRef(null);       // the cast room's last now-playing row
  // The same reading for whoever renders (lyrics.jsx follows a room by it):
  // the room's song id, state, elapsed and length, and when it was read
  // (performance.now(), so a view can carry the position forward).
  const [remoteNp, setRemoteNp] = React.useState(null);
  const heldRef = React.useRef(null);           // { uid, sec }: where this browser stopped to cast
  const lastPosSaveRef = React.useRef(0);      // throttle spoken-audio position saves
  const sleepEndOfChapterRef = React.useRef(false);

  // Best-effort resume-position save for the current spoken-audio item.
  const saveSpokenPosition = React.useCallback((flush = false) => {
    const g = graphRef.current;
    const it = queueRef.current[indexRef.current];
    if (!g || !it || !(it.meta && it.meta.itemType)) return;
    const now = Date.now();
    if (!flush && now - lastPosSaveRef.current < 10000) return;
    lastPosSaveRef.current = now;
    const el = g.els[g.active];
    if (el) SpokenAudio.savePosition(it, el.currentTime || 0, playbackRate);
  }, [playbackRate]);

  const buildGraph = React.useCallback(() => {
    if (graphRef.current) return graphRef.current;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    const ctx = new AC();
    const els = [new Audio(), new Audio()];
    els.forEach((el) => { el.crossOrigin = 'anonymous'; el.preload = 'auto'; });
    const sources = els.map((el) => ctx.createMediaElementSource(el));
    const gains = sources.map(() => ctx.createGain());
    sources.forEach((s, i) => { s.connect(gains[i]); gains[i].gain.value = i === 0 ? 1 : 0; });
    // 10-band EQ in series.
    const eq = EQ_BANDS.map((f, i) => {
      const b = ctx.createBiquadFilter();
      b.type = i === 0 ? 'lowshelf' : i === EQ_BANDS.length - 1 ? 'highshelf' : 'peaking';
      b.frequency.value = f;
      b.Q.value = 1.0;
      b.gain.value = eqEnabled ? eqBands[i] : 0;
      return b;
    });
    // gains → eq[0] → eq[1] → … → analyser → master → destination
    gains.forEach((g) => g.connect(eq[0]));
    for (let i = 0; i < eq.length - 1; i++) eq[i].connect(eq[i + 1]);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.82;
    eq[eq.length - 1].connect(analyser);
    const master = ctx.createGain();
    master.gain.value = muted ? 0 : volume;
    analyser.connect(master);
    master.connect(ctx.destination);
    // Wire per-element event handlers.
    els.forEach((el) => {
      el.addEventListener('ended', () => onElementEnded(el));
      el.addEventListener('progress', () => updateBuffered(el));
    });
    graphRef.current = { ctx, els, sources, gains, eq, analyser, master, active: 0 };
    return graphRef.current;
  }, []); // eslint-disable-line

  const activeEl = () => (graphRef.current ? graphRef.current.els[graphRef.current.active] : null);
  const idleEl = () => (graphRef.current ? graphRef.current.els[graphRef.current.active ^ 1] : null);

  const updateBuffered = (el) => {
    try {
      if (el.buffered && el.buffered.length && el.duration) {
        setBuffered(el.buffered.end(el.buffered.length - 1) / el.duration);
      }
    } catch {}
  };

  // ── Master volume / mute ───────────────────────────────────────────
  React.useEffect(() => {
    if (graphRef.current) {
      graphRef.current.master.gain.value = muted ? 0 : volume;
    }
    localStorage.setItem('domovoi-player-volume', String(volume));
  }, [volume, muted]);

  // ── EQ live update ─────────────────────────────────────────────────
  React.useEffect(() => {
    if (graphRef.current) {
      graphRef.current.eq.forEach((b, i) => { b.gain.value = eqEnabled ? eqBands[i] : 0; });
    }
    localStorage.setItem('domovoi-player-eq', JSON.stringify(eqBands));
    localStorage.setItem('domovoi-player-eq-on', eqEnabled ? '1' : '0');
  }, [eqBands, eqEnabled]);

  // ── playbackRate live update (podcasts will drive this) ────────────
  React.useEffect(() => {
    const g = graphRef.current;
    if (g) g.els.forEach((el) => { el.playbackRate = playbackRate; });
  }, [playbackRate]);

  // ── Recent list persistence ────────────────────────────────────────
  const pushRecent = React.useCallback((item) => {
    if (!item || item.trackId == null) return;
    setRecent((prev) => {
      const next = [{ trackId: item.trackId, title: item.title, artist: item.artist, at: Date.now() },
        ...prev.filter((r) => r.trackId !== item.trackId)].slice(0, RECENT_MAX);
      try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch {}
      return next;
    });
    // Auto-cache recent items in the background (best-effort, respects budget).
    if (item.cacheable && OfflineCache.supported() && navigator.onLine) {
      OfflineCache.autoCache(item);
    }
  }, []);

  /* ── Core: load an item into the active element and play ──────────── */
  const loadAndPlay = React.useCallback(async (item, { resumeSec = 0 } = {}) => {
    const g = buildGraph();
    if (!g || !item) return;
    if (g.ctx.state === 'suspended') { try { await g.ctx.resume(); } catch {} }
    const el = activeEl();
    // Prefer an offline-cached blob when present (Cache Storage / SW serves it).
    el.src = item.src;
    el.playbackRate = playbackRate;
    g.gains[g.active].gain.cancelScheduledValues(g.ctx.currentTime);
    g.gains[g.active].gain.value = 1;
    g.gains[g.active ^ 1].gain.value = 0;
    setStatus('loading');
    try {
      await el.play();
      if (resumeSec > 0 && item.seekable) { try { el.currentTime = resumeSec; } catch {} }
      setStatus('playing');
      pushRecent(item);
      updateMediaSession(item);
    } catch (e) {
      console.warn('play failed', e);
      setStatus('paused');
    }
  }, [buildGraph, playbackRate, pushRecent]);

  /* Preload the next item into the idle element (gapless / crossfade). */
  const preloadNext = React.useCallback(() => {
    const g = graphRef.current;
    if (!g) return;
    const nextItem = queue[index + 1];
    const el = idleEl();
    if (nextItem && el && el.src !== nextItem.src) {
      try { el.src = nextItem.src; el.load(); } catch {}
    }
  }, [queue, index]);

  /* Crossfade / gapless advance to a specific queue index. */
  const advanceTo = React.useCallback((nextIndex, { crossfade = false } = {}) => {
    const g = graphRef.current;
    if (nextIndex < 0 || nextIndex >= queue.length) { stop(); return; }
    const nextItem = queue[nextIndex];
    if (!g) { setIndex(nextIndex); loadAndPlay(nextItem); return; }

    if (crossfade && crossfadeSec > 0) {
      const other = g.active ^ 1;
      const el = g.els[other];
      if (el.src !== nextItem.src) { el.src = nextItem.src; }
      el.playbackRate = playbackRate;
      el.currentTime = 0;
      el.play().then(() => {
        const now = g.ctx.currentTime;
        g.gains[g.active].gain.cancelScheduledValues(now);
        g.gains[other].gain.cancelScheduledValues(now);
        g.gains[g.active].gain.setValueAtTime(1, now);
        g.gains[g.active].gain.linearRampToValueAtTime(0, now + crossfadeSec);
        g.gains[other].gain.setValueAtTime(0, now);
        g.gains[other].gain.linearRampToValueAtTime(1, now + crossfadeSec);
        g.active = other;
        setIndex(nextIndex);
        setStatus('playing');
        pushRecent(nextItem);
        updateMediaSession(nextItem);
      }).catch((e) => { console.warn('crossfade play failed', e); });
    } else {
      // Gapless hard cut: reuse the idle element if it was preloaded.
      const other = g.active ^ 1;
      const el = g.els[other];
      if (el.src === nextItem.src && el.readyState >= 2) {
        g.gains[other].gain.value = 1;
        g.gains[g.active].gain.value = 0;
        el.currentTime = 0;
        el.play().catch(() => {});
        g.active = other;
        setIndex(nextIndex);
        setStatus('playing');
        pushRecent(nextItem);
        updateMediaSession(nextItem);
      } else {
        setIndex(nextIndex);
        loadAndPlay(nextItem);
      }
    }
  }, [queue, loadAndPlay, playbackRate, pushRecent]);

  const onElementEnded = (el) => {
    // Only react to the currently-active element ending.
    const g = graphRef.current;
    if (!g || el !== g.els[g.active]) return;
    // The listening history: this song ran to its end.
    ListenLog.close(true);
    if (sleepEndOfTrackRef.current) {
      sleepEndOfTrackRef.current = false;
      setSleepRemainingSec(null);
      stop();
      return;
    }
    // Use the functional index to avoid a stale closure.
    setIndex((i) => {
      const ni = i + 1;
      if (ni < queueRef.current.length) { advanceRef.current(ni, { crossfade: false }); return i; }
      // End of queue.
      setStatus('stopped');
      return i;
    });
  };

  // Keep refs fresh for event handlers created once.
  const queueRef = React.useRef(queue); queueRef.current = queue;
  const indexRef = React.useRef(index); indexRef.current = index;
  const advanceRef = React.useRef(advanceTo); advanceRef.current = advanceTo;

  // The listening history (ListenLog): the library song playing in THIS
  // browser, and whether it is playing. A new item closes the one before
  // (as stopped, or skipped when next / a queue row ended it); casting to
  // a room closes it too (the room's own logger takes over there).
  const currentUid = current ? current.uid : null;
  React.useEffect(() => {
    if (target.kind !== 'browser') { ListenLog.follow(null, false); return; }
    ListenLog.follow(current, status === 'playing', durationSec);
  }, [currentUid, current ? current.sessionId : null, status, target.kind, durationSec]); // eslint-disable-line

  /* ── position ticker (rAF) + crossfade trigger ────────────────────── */
  React.useEffect(() => {
    const tick = () => {
      const g = graphRef.current;
      if (g && target.kind === 'browser') {
        const el = g.els[g.active];
        if (el) {
          setPositionSec(el.currentTime || 0);
          if (el.duration && Number.isFinite(el.duration)) setDurationSec(el.duration);
          // Spoken-audio: throttled resume-position save + end-of-chapter stop.
          const it = queueRef.current[indexRef.current];
          if (status === 'playing' && it && it.meta && it.meta.itemType) {
            saveSpokenPosition(false);
            if (sleepEndOfChapterRef.current && Array.isArray(it.chapters) && it.chapters.length) {
              const starts = it.chapters.map((c) => c.start_sec || 0);
              let nextStart = null;
              for (const s of starts) { if (s > el.currentTime) { nextStart = s; break; } }
              const boundary = nextStart != null ? nextStart : el.duration;
              if (boundary && el.currentTime >= boundary - 0.5) {
                sleepEndOfChapterRef.current = false;
                setSleepRemainingSec(null);
                pause();
              }
            }
          }
          // Crossfade trigger.
          if (crossfadeSec > 0 && status === 'playing' && el.duration) {
            const remaining = el.duration - el.currentTime;
            const nextI = index + 1;
            if (remaining <= crossfadeSec && nextI < queue.length && !g._fading) {
              g._fading = true;
              advanceTo(nextI, { crossfade: true });
              setTimeout(() => { if (graphRef.current) graphRef.current._fading = false; }, crossfadeSec * 1000 + 200);
            }
          }
          // Preload the next when we cross the 60%-through mark.
          if (el.duration && el.currentTime / el.duration > 0.6) preloadNext();
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [status, index, queue, target, advanceTo, preloadNext, saveSpokenPosition]);

  /* Load `item` PAUSED at `sec`: a hand-back from a room that wasn't playing
   * waits here where the room was, and play() starts it there. An element
   * with no metadata yet keeps the time as its default playback start
   * position (HTMLMediaElement), which play() honours. */
  const cueAt = React.useCallback((item, sec) => {
    const g = buildGraph();
    if (!g || !item) return;
    const el = activeEl();
    try { el.pause(); } catch {}
    el.src = item.src;
    el.playbackRate = playbackRate;
    if (sec > 0 && item.seekable) { try { el.currentTime = sec; } catch {} }
    setPositionSec(sec > 0 ? sec : 0);
    setStatus('paused');
    updateMediaSession(item);
  }, [buildGraph, playbackRate]);

  /* ── Leaving a room ────────────────────────────────────────────────────
   * The hand-off rules (2026-10-01), the same in the Android app
   * (PlayerController): a room that is LEFT — for this browser, for another
   * room, or by a "play here" while casting — is paused, never left playing
   * unheard. Paused rather than stopped: a person's pause holds against every
   * automatic restart (a voice turn's auto-resume, an announcement's restart:
   * domovoi/music_pause.py), so it cannot come back on by itself; the room
   * keeps its queue and place for someone there to resume; a later cast back
   * to it is a new start, which clears the hold. Stop would throw the place
   * away and tear down the satellite's music stream for nothing. */
  /* `onLeft(room, paused)` hears how the room's pause went — `paused` false
   * when it didn't happen (the core's 502 for a player that never answered),
   * so the caller can say "couldn't pause office" instead of "paused". */
  const leaveRoom = React.useCallback((onLeft) => {
    const t = targetRef.current;
    if (t.kind !== 'room' || !t.roomId) return null;
    const room = t.roomId;
    _roomControl('pause', room).then(
      () => { if (onLeft) onLeft(room, true); },
      (e) => { console.warn(`pausing ${room} failed`, e); if (onLeft) onLeft(room, false); });
    remoteNpRef.current = null;
    setRemoteNp(null);
    moveTarget({ kind: 'browser' });
    return room;
  }, []);

  /* ═══ Public actions ═══════════════════════════════════════════════ */
  /* Replace the queue and play it IN THIS BROWSER. Every caller is a "play
   * here" (a library row's "play in this browser", a station, a podcast or
   * an audiobook), so while casting this ends the cast — the room is paused
   * and no longer watched (leaveRoom) — rather than sending the list to the
   * room. It used to re-cast: the toast said "playing in this browser" over
   * a room, and a station (nothing a room can play) replaced the queue and
   * played nowhere. Starting a room is the cast picker's job; a queue row
   * picked while casting (jumpTo) still re-casts. Same rule as the Android
   * app's PlayerController.playItems. Returns the room it left, if any;
   * `onLeft(room, paused)` hears whether that room's pause happened.
   *
   * A play here wins over a cast still on its way (playHereGenRef): that
   * cast stops where it is, or pauses the room again if it took the queue. */
  const playItems = React.useCallback((items, startIndex = 0, { onLeft } = {}) => {
    if (!items || !items.length) return null;
    const at = Math.max(0, Math.min(startIndex, items.length - 1));
    playHereGenRef.current += 1;
    const left = leaveRoom(onLeft);
    dropUpNext();
    setQueue(items);
    setIndex(at);
    loadAndPlay(items[at]);
    return left;
  }, [loadAndPlay, leaveRoom]);

  /* Play a single spoken-audio item (podcast episode / audiobook), resuming
   * from a saved position + speed. The page fetches the position first and
   * passes it here; we set the queue, seek on load, and apply the remembered
   * playbackRate. A room can't play spoken audio, so while casting this
   * ends the cast like playItems — it used to play here while the room
   * played on. A folder book comes as its list of file items
   * (itemsFromBook): `resumeSec` is the place in the BOOK, so it starts in
   * the file that holds it, that far into the file. */
  const playSpoken = React.useCallback((itemOrItems, { resumeSec = 0, speed = 1 } = {}) => {
    const items = (Array.isArray(itemOrItems) ? itemOrItems : [itemOrItems]).filter(Boolean);
    if (!items.length) return;
    const offsetOf = (it) => (it.meta && it.meta.offsetSec) || 0;
    let at = 0;
    items.forEach((it, i) => { if (resumeSec >= offsetOf(it)) at = i; });
    playHereGenRef.current += 1;
    leaveRoom();
    dropUpNext();
    setQueue(items);
    setIndex(at);
    if (speed && speed !== playbackRate) setPlaybackRateState(Math.max(0.5, Math.min(3, speed)));
    loadAndPlay(items[at], { resumeSec: Math.max(0, resumeSec - offsetOf(items[at])) });
  }, [loadAndPlay, leaveRoom, playbackRate]);

  const enqueue = React.useCallback((items) => {
    const add = Array.isArray(items) ? items : [items];
    setQueue((q) => {
      // After the songs somebody asked for, before Up next's picks.
      const at = _upNextAddAt(q, index);
      const nq = [...q.slice(0, at), ...add, ...q.slice(at)];
      // If nothing is playing, start at the first appended item.
      if (index < 0 && target.kind === 'browser') {
        setIndex(at);
        loadAndPlay(add[0]);
      }
      return nq;
    });
  }, [index, loadAndPlay, target]);

  const playNext = React.useCallback((item) => {
    setQueue((q) => {
      const at = index < 0 ? q.length : index + 1;
      const nq = [...q.slice(0, at), item, ...q.slice(at)];
      if (index < 0 && target.kind === 'browser') { setIndex(at); loadAndPlay(item); }
      return nq;
    });
  }, [index, loadAndPlay, target]);

  const removeAt = React.useCallback((i) => {
    // An Up next pick taken out: the session hears it (best-effort).
    const gone = queueRef.current[i];
    const un = upNextRef.current;
    if (gone && gone.origin === 'auto' && un && un.id && gone.sessionId === un.id && gone.upNextSeq) {
      _upNextCall('POST', `/api/music/up-next/sessions/${un.id}/feedback`, { seq: gone.upNextSeq, kind: 'remove' })
        .catch(() => {});
    }
    setQueue((q) => {
      const nq = q.filter((_, idx) => idx !== i);
      setIndex((cur) => {
        if (i < cur) return cur - 1;
        if (i === cur) {
          // Removing the current item — advance to what slid into its slot.
          // For the listening history that is a skip of it.
          if (target.kind === 'browser') ListenLog.skip();
          if (nq.length === 0) { stop(); return -1; }
          const ni = Math.min(cur, nq.length - 1);
          if (target.kind === 'browser') loadAndPlay(nq[ni]);
          return ni;
        }
        return cur;
      });
      return nq;
    });
  }, [loadAndPlay, target]);

  const moveItem = React.useCallback((from, to) => {
    setQueue((q) => {
      if (from === to || from < 0 || to < 0 || from >= q.length || to >= q.length) return q;
      const nq = [...q];
      const [m] = nq.splice(from, 1);
      nq.splice(to, 0, m);
      setIndex((cur) => {
        if (cur === from) return to;
        if (from < cur && to >= cur) return cur - 1;
        if (from > cur && to <= cur) return cur + 1;
        return cur;
      });
      return nq;
    });
  }, []);

  const clearQueue = React.useCallback(() => { stop(); dropUpNext(); setQueue([]); setIndex(-1); }, []);

  /* ── Up next: starting, refilling, the switch, "less like this" ──────
   * `start` is the body's start ({track_id, context?} or {context,
   * whole_list}); the queue is already playing. The first batch joins it
   * as auto items when the server answers; a play made meanwhile wins. */
  const startUpNext = (start) => {
    if (!_upNextPaired()) return null;
    const gen = upNextGenRef.current;
    setUpNext({ loading: true, autoplay: autoplayPrefRef.current, mode: null, seed: null });
    const body = Object.assign({ target: { kind: 'device', device_id: DeviceIdentity.id() }, start,
                                 autoplay: autoplayPrefRef.current }, upNextListenerFields());
    return _upNextCall('POST', '/api/music/up-next/sessions', body).then((s) => {
      if (gen !== upNextGenRef.current || !s || !s.id) return;
      setUpNext(_upNextFrom(s));
      const picks = Array.isArray(s.picks) ? s.picks : [];
      setQueue((q) => [
        // The songs this start played are the session's too (listens feed it).
        ...q.map((it) => (it && it.upNextGen === gen ? Object.assign({}, it, { sessionId: s.id }) : it)),
        ...picks.map((pk) => itemFromPick(pk, s.id)),
      ]);
    }).catch((e) => {
      if (gen !== upNextGenRef.current) return;
      // Refused (a song the library lost, a sign-in): no Up next for this
      // play, quietly. Unreachable or failing: say Up next needs the home
      // server, and try again when the song changes.
      const st = e && e.status;
      if (st && st < 500) setUpNext({ refused: true, autoplay: autoplayPrefRef.current });
      else {
        // failedAt is filled in by the refill effect, on the song playing then.
        setUpNext({ loading: false, offline: true, autoplay: autoplayPrefRef.current, start,
                    failedAt: null });
      }
    });
  };

  /* Items for a start, tagged with the generation the start's playItems
   * makes, so the session id can be put on them when it comes. */
  const _startItems = (tracks, origin) => {
    const gen = upNextGenRef.current + 1;
    return tracks.map((t) => Object.assign(itemFromTrack(t), { origin, upNextGen: gen }));
  };
  /* A song clicked inside a list (`tracks`, `at`): it plays here, and Up
   * next goes on with the rest of the list (with autoplay on: round again
   * when the list has 50 songs or more, else similar songs from the
   * library once it has played). `context` names the list: {kind, ref?, name}; an `ids` list
   * takes its songs from `tracks`. No context: the song on its own,
   * similar songs from the whole library. */
  const playFromList = (tracks, at = 0, context = null, opts = {}) => {
    const list = (tracks || []).filter(Boolean);
    const track = list[Math.max(0, Math.min(at, list.length - 1))];
    if (!track) return null;
    const left = playItems(_startItems([track], 'explicit'), 0, opts);
    const start = { track_id: track.id };
    if (context) {
      start.context = Object.assign({}, context);
      if (context.kind === 'ids') {
        start.context.track_ids = list.map((t) => t.id).filter((x) => x != null).slice(0, UP_NEXT_IDS_CAP);
      }
    }
    startUpNext(start);
    return left;
  };
  /* A whole list played (or shuffled) on purpose: the list is the queue,
   * as it is; Up next carries on with similar songs when it is nearly
   * over, while autoplay is on. */
  const playList = (tracks, context = null, opts = {}) => {
    const list = (tracks || []).filter(Boolean);
    if (!list.length) return null;
    const left = playItems(_startItems(list, 'list'), 0, opts);
    const ctx = context ? Object.assign({}, context) : { kind: 'ids', name: 'this list' };
    if (ctx.kind === 'ids') ctx.track_ids = list.map((t) => t.id).filter((x) => x != null).slice(-UP_NEXT_IDS_CAP);
    startUpNext({ context: ctx, whole_list: true, track_id: list[0].id });
    return left;
  };

  /* Ask for more once this few songs are left after the one playing. The
   * same call again (have_through_seq) returns the same batch, so a retry
   * never skips one. Picks the session took back ("less like this", two
   * early skips of one artist) leave the queue. A queue of library songs
   * with no session (added from search, handed back from a room) gets one
   * when it is nearly over, built from the queue itself, while autoplay is
   * on. Only while playing HERE: a room keeps its own queue. */
  const refill = () => {
    const u = upNextRef.current;
    const gen = upNextGenRef.current;
    refillingRef.current = true;
    const at = indexRef.current;
    return _upNextCall('POST', `/api/music/up-next/sessions/${u.id}/next`, { have_through_seq: u.haveThrough })
      .then((r) => {
        if (gen !== upNextGenRef.current || !r) return;
        const gone = new Set(r.removed || []);
        const picks = (r.picks || []).filter((pk) => pk.seq > u.haveThrough && !gone.has(pk.seq));
        const top = (r.picks || []).reduce((m, pk) => Math.max(m, pk.seq), u.haveThrough);
        setQueue((q) => {
          const cur = indexRef.current;
          const kept = q.filter((it, i) => !(i > cur && it && it.origin === 'auto'
            && it.sessionId === u.id && gone.has(it.upNextSeq)));
          return [...kept, ...picks.map((pk) => itemFromPick(pk, u.id))];
        });
        // Nothing new and not over: ask again when the song changes, not in a loop.
        const idle = !picks.length && !r.ended;
        // A short list played out goes on in the library: the mode moves on
        // ('list' -> 'continue'), and the header with it ("more like …").
        setUpNext((x) => (x && x.id === u.id
          ? Object.assign({}, x, { haveThrough: top, ended: !!r.ended, offline: false,
                                   failedAt: idle ? at : null, mode: r.mode || x.mode }) : x));
      })
      .catch(() => {
        if (gen !== upNextGenRef.current) return;
        // Tried again when the song changes.
        setUpNext((x) => (x && x.id === u.id ? Object.assign({}, x, { offline: true, failedAt: at }) : x));
      })
      .finally(() => { if (gen === upNextGenRef.current) refillingRef.current = false; });
  };
  React.useEffect(() => {
    if (target.kind !== 'browser' || index < 0 || refillingRef.current) return;
    const left = queue.length - 1 - index;
    if (left > UP_NEXT_REFILL_AT) return;
    const u = upNextRef.current;
    if (!u) {
      if (!autoplayPref || !_upNextPaired()) return;
      const lib = queue.filter((it) => it && it.kind === 'library' && it.trackId != null);
      if (!lib.length) return;
      const gen = upNextGenRef.current + 1;
      upNextGenRef.current = gen;
      setQueue((q) => q.map((it) => (it && it.kind === 'library' ? Object.assign({}, it, { upNextGen: gen }) : it)));
      startUpNext({ context: { kind: 'ids', name: 'this queue',
                               track_ids: lib.map((it) => it.trackId).slice(-UP_NEXT_IDS_CAP) },
                    whole_list: true, track_id: lib[0].trackId });
      return;
    }
    if (u.failedAt === index) return;
    if (!u.id) {
      if (u.offline && u.start && !u.loading) {
        if (u.failedAt == null) setUpNext(Object.assign({}, u, { failedAt: index }));
        else startUpNext(u.start);
      }
      return;
    }
    if (u.loading || u.ended) return;
    if (!u.autoplay && u.mode !== 'list') return;
    refill();
  }, [index, queue.length, upNext, target.kind, autoplayPref]); // eslint-disable-line

  /* The Autoplay switch. Off: the waiting picks of a mix leave the queue
   * and nothing more is added (a list keeps the rest of itself, then
   * stops). Remembered for this browser. Rejects when the server didn't
   * take it (the switch still says what this browser does). */
  const setAutoplay = async (on) => {
    _upNextSetAutoplayPref(!!on);
    setAutoplayPrefState(!!on);
    autoplayPrefRef.current = !!on;
    const u = upNextRef.current;
    if (!u) return;
    setUpNext(Object.assign({}, u, { autoplay: !!on, ended: on ? false : u.ended, failedAt: null }));
    if (!on && u.mode !== 'list') {
      setQueue((q) => q.filter((it, i) => !(i > indexRef.current && it && it.origin === 'auto')));
    }
    if (u.id) await apiPatch(`/api/music/up-next/sessions/${u.id}`, { autoplay: !!on });
  };
  /* "Less like this" on the pick at queue row `i`: that artist is out of
   * the mix for the rest of the session, their waiting picks with it. */
  const lessLikeThis = async (i) => {
    const it = queueRef.current[i];
    const u = upNextRef.current;
    if (!it || it.origin !== 'auto' || !u || !u.id || it.sessionId !== u.id) return [];
    const r = await apiPost(`/api/music/up-next/sessions/${u.id}/feedback`,
      { seq: it.upNextSeq, kind: 'less_like_this' });
    const gone = new Set((r && r.removed) || []);
    gone.add(it.upNextSeq);
    setQueue((q) => q.filter((x, j) => !(j > indexRef.current && x && x.origin === 'auto'
      && x.sessionId === u.id && gone.has(x.upNextSeq))));
    return [...gone];
  };
  /* Whose taste the mix follows (the "for" selector): the same "listening
   * as" the rest of the player uses; the session follows it from its next
   * batch. */
  const setUpNextListener = async (pid) => {
    setListenerPersonId(pid);
    const u = upNextRef.current;
    if (!u || !u.id) return;
    const s = await apiPatch(`/api/music/up-next/sessions/${u.id}`, { listener_person_id: pid == null ? null : pid });
    if (s && upNextRef.current && upNextRef.current.id === u.id) {
      setUpNext(Object.assign({}, upNextRef.current, { person: s.person || null }));
    }
  };

  const jumpTo = React.useCallback((i) => {
    if (i < 0 || i >= queue.length) return;
    if (target.kind === 'browser') { ListenLog.skip(); setIndex(i); loadAndPlay(queue[i]); return; }
    // While casting: the room plays that row now — a real jump within the
    // room's own queue (POST /api/music/queue/{room}/play, Up next P4),
    // found by its library song; a row the room's queue doesn't hold any
    // more is cast again from there, as before. The highlight moves only
    // once the room has moved (a row a room can't play moves nothing).
    // In line with the casts (castTo): it waits for one still on its way,
    // and a "play here" that comes meanwhile wins — the jump is not sent,
    // or the room that took it is paused again.
    const gen = playHereGenRef.current;
    const run = castChainRef.current.then(async () => {
      // No longer casting by its turn (a play here, a hand-back): nothing to do.
      const t = targetRef.current;
      if (t.kind !== 'room') return;
      if (!(await castRoomPlayEntry(t.roomId, queueRef.current[i]))) await castRoomJump(t.roomId, i);
      if (gen !== playHereGenRef.current) {
        await _roomControl('pause', t.roomId).catch((e) => console.warn(`pausing ${t.roomId} failed`, e));
        return;
      }
      setIndex(i);
    });
    castChainRef.current = run.catch(() => {});
    run.catch((e) => console.warn('re-cast to room failed', e));
  }, [queue, loadAndPlay, target]);

  function play() {
    if (target.kind === 'room') { apiPost(`/api/music/resume/${target.roomId}`).catch(() => {}); return; }
    const el = activeEl();
    if (el) { el.play().then(() => setStatus('playing')).catch(() => {}); }
    else if (current) loadAndPlay(current);
  }
  function pause() {
    if (target.kind === 'room') { apiPost(`/api/music/pause/${target.roomId}`).catch(() => {}); return; }
    const el = activeEl();
    if (el) { el.pause(); setStatus('paused'); saveSpokenPosition(true); }
  }
  function stop() {
    if (target.kind === 'room') { apiPost(`/api/music/stop/${target.roomId}`).catch(() => {}); setStatus('stopped'); return; }
    const g = graphRef.current;
    if (g) g.els.forEach((el) => { try { el.pause(); } catch {} });
    ListenLog.close();
    setStatus('stopped');
    setPositionSec(0);
  }
  /* The bar's "Stop and close": the spoken-audio resume position is saved
   * first (before anything is paused or cleared), a cast room is really
   * stopped (a refusal rejects, and the caller keeps the bar), then the
   * queue is emptied — which is what removes the bar — and the browser's
   * media controls are cleared. Nothing of the queue is kept in storage,
   * so a reload does not bring the bar back. */
  const closePlayer = async () => {
    saveSpokenPosition(true);
    if (target.kind === 'room') await _roomControl('stop', target.roomId);
    stop();
    setQueue([]);
    setIndex(-1);
    if ('mediaSession' in navigator) {
      try { navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = 'none'; } catch {}
    }
  };
  const toggle = () => { (status === 'playing') ? pause() : play(); };

  const next = React.useCallback(() => {
    // While casting: the room's next song. The core follows the queue the
    // room was cast (since 2026-10-01; before, skip swapped it for one
    // random library track).
    if (target.kind === 'room') { apiPost(`/api/music/skip/${target.roomId}`).catch(() => {}); return; }
    ListenLog.skip();
    const ni = index + 1;
    if (ni < queue.length) advanceTo(ni, { crossfade: false });
    else stop();
  }, [index, queue, advanceTo, target]);

  const prev = React.useCallback(() => {
    // While casting: back one song in the room's queue (/api/music/previous,
    // the core's MPD previous; on the queue's first song it starts again).
    // The buttons, the p / shift+left keys and the OS media controls'
    // previoustrack all land here. It used to post skip — previous moved the
    // room FORWARD — and then did nothing while there was no previous
    // route. Same as the Android app (PlayerController.prev).
    if (target.kind === 'room') { apiPost(`/api/music/previous/${target.roomId}`).catch(() => {}); return; }
    // Restart current if >3s in, else go to previous.
    const el = activeEl();
    if (el && el.currentTime > 3) { el.currentTime = 0; return; }
    const pi = index - 1;
    if (pi >= 0) advanceTo(pi, { crossfade: false });
    else if (el) el.currentTime = 0;
  }, [index, advanceTo, target]);

  const seek = React.useCallback((sec) => {
    if (target.kind === 'room' || !current || !current.seekable) return;
    const el = activeEl();
    if (el && Number.isFinite(el.duration)) {
      el.currentTime = Math.max(0, Math.min(sec, el.duration));
      setPositionSec(el.currentTime);
    }
  }, [current, target]);
  const seekBy = React.useCallback((delta) => { seek(positionSec + delta); }, [seek, positionSec]);

  const setVolume = React.useCallback((v) => { setVolumeState(Math.max(0, Math.min(1, v))); setMuted(false); }, []);
  const toggleMute = React.useCallback(() => setMuted((m) => !m), []);

  const setEqBand = React.useCallback((i, gain) => {
    setEqBands((b) => { const nb = [...b]; nb[i] = Math.max(EQ_MIN_DB, Math.min(EQ_MAX_DB, gain)); return nb; });
  }, []);
  const resetEq = React.useCallback(() => setEqBands(EQ_BANDS.map(() => 0)), []);
  const setEqEnabled = React.useCallback((b) => setEqEnabledState(!!b), []);
  const setPlaybackRate = React.useCallback((r) => {
    const clamped = Math.max(0.5, Math.min(3, r));
    setPlaybackRateState(clamped);
    // Persist per-item speed memory for spoken audio (podcasts/audiobooks).
    const it = queueRef.current[indexRef.current];
    if (it && it.meta && it.meta.itemType) SpokenAudio.savePosition(it, positionSec, clamped);
  }, [positionSec]);

  /* Jump to a chapter by index (spoken audio). */
  const jumpToChapter = React.useCallback((chIdx) => {
    const it = queueRef.current[indexRef.current];
    if (!it || !Array.isArray(it.chapters) || !it.chapters[chIdx]) return;
    seek(it.chapters[chIdx].start_sec || 0);
  }, [seek]);

  // ── Sleep timer ────────────────────────────────────────────────────
  const cancelSleep = React.useCallback(() => {
    if (sleepTimerRef.current) { clearInterval(sleepTimerRef.current); sleepTimerRef.current = null; }
    sleepEndOfTrackRef.current = false;
    sleepEndOfChapterRef.current = false;
    setSleepRemainingSec(null);
  }, []);
  const setSleep = React.useCallback((arg) => {
    cancelSleep();
    if (arg === 'end-of-chapter') { sleepEndOfChapterRef.current = true; setSleepRemainingSec(-1); return; }
    if (arg === 'end-of-track') { sleepEndOfTrackRef.current = true; setSleepRemainingSec(-1); return; }
    const mins = Number(arg);
    if (!Number.isFinite(mins) || mins <= 0) return;
    let remaining = Math.round(mins * 60);
    setSleepRemainingSec(remaining);
    sleepTimerRef.current = setInterval(() => {
      remaining -= 1;
      setSleepRemainingSec(remaining);
      if (remaining <= 0) { cancelSleep(); pause(); }
    }, 1000);
  }, [cancelSleep]);

  // ── Media Session API ──────────────────────────────────────────────
  const updateMediaSession = (item) => {
    if (!('mediaSession' in navigator) || !item) return;
    try {
      // No `type`: a library cover is whatever the file carries (JPEG,
      // PNG, WebP). A cover the page already knows is missing isn't offered.
      const artwork = item.coverUrl && !coverMisses.has(item.coverUrl)
        ? [{ src: item.coverUrl, sizes: '512x512' }] : [];
      navigator.mediaSession.metadata = new window.MediaMetadata({
        title: item.title || 'unknown',
        artist: item.artist || '',
        album: item.album || 'Domovoi',
        artwork,
      });
    } catch {}
  };
  React.useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    const ms = navigator.mediaSession;
    const set = (a, h) => { try { ms.setActionHandler(a, h); } catch {} };
    set('play', play); set('pause', pause);
    set('previoustrack', prev); set('nexttrack', next);
    set('seekbackward', () => seekBy(-10));
    set('seekforward', () => seekBy(10));
    set('seekto', (d) => { if (d.seekTime != null) seek(d.seekTime); });
    set('stop', stop);
    return () => ['play','pause','previoustrack','nexttrack','seekbackward','seekforward','seekto','stop']
      .forEach((a) => set(a, null));
  }, [next, prev, seek, seekBy]);

  React.useEffect(() => {
    if ('mediaSession' in navigator) {
      navigator.mediaSession.playbackState =
        status === 'playing' ? 'playing' : status === 'paused' ? 'paused' : 'none';
    }
  }, [status]);

  // ── Keyboard shortcuts (ignore when typing in a field) ─────────────
  React.useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      // Space on a focused button would both click it and toggle playback —
      // let the button handle its own key.
      if (e.key === ' ' && t && t.tagName === 'BUTTON') return;
      switch (e.key) {
        case ' ': e.preventDefault(); toggle(); break;
        case 'ArrowRight': if (e.shiftKey) next(); else seekBy(5); break;
        case 'ArrowLeft': if (e.shiftKey) prev(); else seekBy(-5); break;
        case 'ArrowUp': e.preventDefault(); setVolume(volume + 0.05); break;
        case 'ArrowDown': e.preventDefault(); setVolume(volume - 0.05); break;
        case 'n': next(); break;
        case 'p': prev(); break;
        default: break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggle, next, prev, seekBy, setVolume, volume]);

  // ── Casting (Spotify-Connect-style hand-off) ───────────────────────
  // `startSec`: how far into items[0] the room should start (the listener's
  // place in the current song). Sent only when items[0] is itself the first
  // id, i.e. a library track: otherwise it belongs to a song not sent.
  // `paused`: the room takes the queue and waits there, paused, until play
  // (`start_paused`) — a cast from a player that was paused. A core from
  // before 2026-10-01 ignores it and plays, as every cast used to.
  const castRoomLoad = React.useCallback(async (roomId, items, startSec = 0, { paused = false } = {}) => {
    const trackIds = _castTrackIds(items);
    if (!trackIds.length) throw _castRefused();
    const body = { room_id: roomId, track_ids: trackIds };
    const first = (items || [])[0];
    const at = Math.floor(Number(startSec) || 0);
    if (at >= 2 && first && first.kind === 'library' && first.trackId === trackIds[0]) body.start_sec = at;
    if (paused) body.start_paused = true;
    // Up next goes with the queue (P4): this browser's session carries on
    // in the room (the room's server refills it from there); without one
    // the room arms its own for what was cast.
    const u = upNextRef.current;
    if (u && u.id && !u.ended) body.up_next_session_id = u.id;
    if (!autoplayPrefRef.current) body.autoplay = false;
    Object.assign(body, upNextListenerFields());
    await apiPost('/api/music/play-tracks', body);
  }, []);
  /* The room plays `item` now, if its queue holds that song: the entry
   * after the one playing first, else the nearest before it. True when it
   * jumped. */
  const castRoomPlayEntry = async (roomId, item) => {
    if (!item || item.trackId == null) return false;
    let q = null;
    try { q = await apiGet(`${roomQueuePath(roomId)}?device_id=${encodeURIComponent(DeviceIdentity.id())}`); }
    catch { return false; }
    const items = (q && q.items) || [];
    const cur = items.findIndex((x) => x && x.playing);
    const hits = items.map((x, j) => ({ x, j })).filter(({ x }) => x && x.track_id === item.trackId);
    const hit = hits.find(({ j }) => j > cur) || hits[hits.length - 1];
    if (!hit) return false;
    await roomPlayEntry(roomId, hit.x.song_id);
    return true;
  };
  const castRoomJump = React.useCallback(async (roomId, i) => {
    // Re-cast from the chosen index onward so "play this queue item" works remotely.
    const items = queueRef.current.slice(i);
    await castRoomLoad(roomId, items);
  }, [castRoomLoad]);

  /* A room's now-playing row, read now; null when it can't be read. */
  const readRoom = async (roomId) => {
    try {
      const all = await apiGet('/api/music/now-playing');
      return (all || []).find((r) => r && r.room_id === roomId) || null;
    } catch { return null; }
  };

  /* Where playback goes: a room, or back to this browser. Resolves to what
   * happened — { kind, roomId?, startIndex, startSec, left, leftPaused,
   * playing?, paused? } — so the picker can say when a room it left would
   * not pause.
   *
   *   browser → room   the room starts on the current track at this
   *                    browser's position; it is told first, and this
   *                    browser goes quiet only once it has taken the queue.
   *   room → room      the new room starts where the old one had got to
   *                    (_castFollowRoom on a fresh reading), then the old
   *                    room is paused.
   *   room → browser   the room is paused first, then this browser picks up
   *                    on the room's track at the room's time — playing only
   *                    if the room was playing and took the pause, else
   *                    waiting there, paused.
   * The room starts the way the player it takes over from was: playing, or
   * PAUSED at that place (`paused: true`, the core's start_paused) when
   * this browser or the old room was paused — Spotify Connect's hand-off.
   * Before 2026-10-01 a cast from a paused browser started the room playing.
   * A queue with nothing a room can play is refused before anything
   * changes, with an error carrying `castRefused`.
   *
   * One change of target at a time: castTo below queues this behind any
   * cast still on its way, and it reads where playback is from targetRef.
   * A "play here" made after the pick (`gen` is the play-here count at the
   * pick) wins: what is left of this is not done, and a room that already
   * took the queue is paused again — { kind: 'superseded', roomId, sent,
   * undone }. */
  const castToNow = React.useCallback(async (nextTarget, gen = playHereGenRef.current) => {
    const superseded = () => gen !== playHereGenRef.current;
    if (superseded()) return { kind: 'superseded', roomId: nextTarget.roomId || null, sent: false };
    const target = targetRef.current;
    if (nextTarget.kind === 'room') {
      const from = target.kind === 'room' ? target.roomId : null;
      let at = Math.max(0, index);
      let startSec = 0;
      let sourcePlaying = true;
      const liveEl = from ? null : activeEl();
      if (from) {
        const np = (await readRoom(from)) || remoteNpRef.current;
        ({ at, sec: startSec } = _castFollowRoom(queue, index, np));
        // A room that can't be read at all is taken to be playing, as before.
        if (np) sourcePlaying = np.state === 'play';
        if (superseded()) return { kind: 'superseded', roomId: nextTarget.roomId, sent: false };
      } else {
        if (liveEl) startSec = liveEl.currentTime || 0;
        sourcePlaying = liveEl ? !liveEl.paused : statusRef.current === 'playing';
      }
      const items = queue.slice(at);
      // Nothing a room can play: say so before touching playback, and never
      // switch the target to a room that was sent nothing.
      if (!_castTrackIds(items).length) throw _castRefused();
      const paused = !sourcePlaying;
      try {
        await castRoomLoad(nextTarget.roomId, items, startSec, { paused });
      } catch (e) {
        // The room refused: this browser (or the old room) plays on.
        console.warn('cast to room failed', e);
        throw e;
      }
      if (superseded()) {
        // A play here came while the room was taking the queue. It has
        // already paused the room it left and is playing in this browser;
        // the room that took this queue must not start it.
        let undone = true;
        try { await _roomControl('pause', nextTarget.roomId); }
        catch (e) { undone = false; console.warn(`pausing ${nextTarget.roomId} failed`, e); }
        return { kind: 'superseded', roomId: nextTarget.roomId, sent: true, undone };
      }
      // Only now that the room has it: silence this browser.
      const g = graphRef.current;
      if (g) g.els.forEach((el) => { try { el.pause(); } catch {} });
      if (liveEl && queue[at]) heldRef.current = { uid: queue[at].uid, sec: startSec };
      remoteNpRef.current = null;
      setRemoteNp(null);
      setIndex(at);
      moveTarget(nextTarget);
      setStatus(paused ? 'paused' : 'playing');
      const left = from && from !== nextTarget.roomId ? from : null;
      let leftPaused = false;
      if (left) {
        try { await _roomControl('pause', left); leftPaused = true; }
        catch (e) { console.warn(`pausing ${left} failed`, e); }
      }
      return { kind: 'room', roomId: nextTarget.roomId, startIndex: at, startSec, left, leftPaused, paused };
    }
    if (target.kind !== 'room' || !target.roomId) {
      return { kind: 'browser', left: null, playing: status === 'playing' };
    }
    const room = target.roomId;
    // Where the room is (the last poll's row if it can't be read now), and
    // whether it was playing; then pause it BEFORE this browser makes a sound.
    const np = (await readRoom(room)) || remoteNpRef.current;
    const wasPlaying = !!(np && np.state === 'play');
    let leftPaused = true;
    try { await _roomControl('pause', room); }
    catch (e) { leftPaused = false; console.warn(`pausing ${room} failed`, e); }
    // A play here came meanwhile: it paused the room and plays here already.
    if (superseded()) return { kind: 'superseded', roomId: null, sent: false, left: room, leftPaused };
    const follow = _castFollowRoom(queue, index, np);
    const item = queue[follow.at] || null;
    // The room's track unknown (stopped, or playing something not in this
    // queue): where this browser was when it cast, if it is that item.
    const held = heldRef.current;
    const sec = follow.known ? follow.sec : (held && item && held.uid === item.uid ? held.sec : 0);
    remoteNpRef.current = null;
    setRemoteNp(null);
    moveTarget({ kind: 'browser' });
    // Up next comes home with the music: the session the room carried on
    // moves back to this browser (P4). One the room no longer has (another
    // play replaced it) is dropped here; the queue starts a new one when
    // it is nearly over.
    const un = upNextRef.current;
    if (un && un.id) {
      _upNextCall('POST', `/api/music/up-next/sessions/${un.id}/move`,
        { target: { kind: 'device', device_id: DeviceIdentity.id() } })
        .catch(() => { if (upNextRef.current && upNextRef.current.id === un.id) dropUpNext(); });
    }
    const playing = !!(item && wasPlaying && leftPaused);
    if (item) {
      setIndex(follow.at);
      if (playing) loadAndPlay(item, { resumeSec: sec });
      else cueAt(item, sec);
    } else {
      setStatus('stopped');
    }
    return { kind: 'browser', left: room, leftPaused, leftWasPlaying: wasPlaying, playing,
             startIndex: follow.at, startSec: sec };
  }, [queue, index, status, castRoomLoad, loadAndPlay, cueAt]);

  /* A cast takes seconds (the room readies its stream first), and the
   * picker can be closed and opened again meanwhile, so a second pick can
   * come while the first is on its way. Before 2026-10-01 both started from
   * the target as it was: office then den from this browser left BOTH rooms
   * playing, office never paused or watched again; office then "This
   * browser" did nothing and the cast to office landed after. Now each pick
   * waits for the one before it and starts from where it left things. Same
   * rule as the Android app's PlayerController.castTo. The play-here count
   * is read at the PICK: a play here after it beats it, even while it
   * waits its turn. */
  const castChainRef = React.useRef(Promise.resolve());
  const castTo = React.useCallback((nextTarget) => {
    const gen = playHereGenRef.current;
    const run = castChainRef.current.then(() => castToNow(nextTarget, gen));
    castChainRef.current = run.catch(() => {});
    return run;
  }, [castToNow]);

  // Remote-mode poller: mirror the room's now-playing into our state.
  React.useEffect(() => {
    if (target.kind !== 'room') {
      if (remotePollRef.current) { clearInterval(remotePollRef.current); remotePollRef.current = null; }
      setRemoteNp(null);
      return;
    }
    // A reading that lands after the target moved on (back here, or to
    // another room) is dropped: it would mark this browser paused while it
    // plays, or hand the next cast the old room's place.
    let live = true;
    const poll = async () => {
      try {
        const all = await apiGet('/api/music/now-playing');
        const np = (all || []).find((r) => r.room_id === target.roomId);
        if (np && live) {
          remoteNpRef.current = np;
          setStatus(np.state === 'play' ? 'playing' : np.state === 'pause' ? 'paused' : 'stopped');
          setPositionSec(np.elapsed_sec || 0);
          setDurationSec(np.song?.duration_sec || 0);
          setRemoteNp({
            room_id: np.room_id, track_id: np.track_id ?? null, state: np.state,
            elapsed_sec: Number(np.elapsed_sec) || 0,
            duration_sec: (np.song && np.song.duration_sec) || null,
            // performance.now() — Date.now() only where there is none (the
            // node harnesses): lyrics.jsx reads the same clock back.
            readAt: typeof performance !== 'undefined' && performance && performance.now
              ? performance.now() : Date.now(),
          });
        }
      } catch {}
    };
    poll();
    remotePollRef.current = setInterval(poll, 2000);
    return () => {
      live = false;
      if (remotePollRef.current) { clearInterval(remotePollRef.current); remotePollRef.current = null; }
    };
  }, [target]);

  const getAnalyser = React.useCallback(() => (graphRef.current ? graphRef.current.analyser : null), []);

  // Cleanup on unmount.
  React.useEffect(() => () => {
    cancelAnimationFrame(rafRef.current);
    if (sleepTimerRef.current) clearInterval(sleepTimerRef.current);
    if (remotePollRef.current) clearInterval(remotePollRef.current);
    const g = graphRef.current;
    if (g) { try { g.ctx.close(); } catch {} }
  }, []);

  // Register the PWA service worker once (best-effort). The manifest link
  // is added by the integration in index.html; SW registration lives here
  // so it ships with the player.
  React.useEffect(() => {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch((e) => console.debug('sw register skipped', e));
    }
  }, []);

  const value = {
    available: true,
    queue, index, current, status,
    positionSec, durationSec, buffered,
    volume, muted, eqBands, eqEnabled, playbackRate,
    target, remoteNp, sleepRemainingSec, recent,
    playItems, enqueue, playNext, removeAt, moveItem, clearQueue, jumpTo,
    // Up next.
    upNext, autoplay: autoplayPref, playFromList, playList, setAutoplay, lessLikeThis, setUpNextListener,
    toggle, play, pause, stop, closePlayer, next, prev, seek, seekBy,
    setVolume, toggleMute,
    setEqBand, resetEq, setEqEnabled, setPlaybackRate,
    setSleep, cancelSleep,
    castTo, getAnalyser,
    offline: OfflineCache,
    // Spoken audio (podcasts / audiobooks).
    playSpoken, jumpToChapter, spoken: SpokenAudio,
    listenerPersonId, setListenerPersonId,
    // Helper builders exposed so pages don't re-import them.
    itemFromTrack, itemFromSource, itemFromEpisode, itemFromBook, itemsFromBook,
  };

  return (
    <PlaybackContext.Provider value={value}>
      {children}
      <MiniPlayer/>
    </PlaybackContext.Provider>
  );
};

/* ═══════════════════════════════════════════════════════════════════════
 * Global docked mini-player. Rendered by the provider so it appears on
 * every page. Collapses to nothing when the queue is empty.
 *
 * One bar, two layouts (styles.css, "Docked player"):
 *   desktop  cover and title | transport over a seek bar | sleep timer,
 *            volume, queue, cast target, and "open full player" (the
 *            expanded player, PlayerSheet). The queue and the cast target
 *            float above the bar.
 *   phone    (760px and below) one 64px row of what a thumb needs: cover,
 *            title/artist, play/pause, next, and "open player" — which
 *            opens the same PlayerSheet, where everything else went. A 2px
 *            line on the bar's top edge keeps the position visible.
 * The cover and title are a button too: it opens PlayerSheet at any width.
 * `mp-desk` marks what only a desktop shows and `mp-phone` what only a
 * phone shows, so the phone never gets a squeezed copy of the desktop row.
 * ═══════════════════════════════════════════════════════════════════════ */
// styles.css's phone breakpoint, for the few things CSS can't do alone.
const _MP_PHONE = '(max-width: 760px)';
// Listen to a MediaQueryList (null: nothing to listen to); returns the
// unsubscribe. addListener is the pre-2020 Safari spelling.
const _mpOnMediaChange = (mq, fn) => {
  if (!mq) return () => {};
  if (mq.addEventListener) { mq.addEventListener('change', fn); return () => mq.removeEventListener('change', fn); }
  if (mq.addListener) { mq.addListener(fn); return () => mq.removeListener(fn); }
  return () => {};
};
/* The expanded player's address. The Music page's Player tab was
 * #music/player until 2026-11-12; the player is now a sheet over any page,
 * and that address (a bookmark, the manual, an old link) opens it over
 * Music. The address is rewritten to #music in place before anything reads
 * it — at load, and first among the hashchange listeners (this file's is
 * added before the shell's and the pages') — so the Music page never sees
 * a tab it doesn't have, and the sheet's own history entry (pushed by
 * MiniPlayer) sits on top of #music: Back closes the sheet, then leaves. */
const PLAYER_SHEET_ROUTE = 'music/player';
const _mpSheetHub = { openers: new Set(), atLoad: false };
/* Open the expanded player from anywhere (a link, another page). False
 * when there is nothing to show (nothing queued) or no player mounted. */
const openPlayerSheet = () => {
  let opened = false;
  _mpSheetHub.openers.forEach((fn) => { if (fn()) opened = true; });
  return opened;
};
const _mpTakeSheetRoute = () => {
  let route = '';
  try { route = String(window.location.hash || '').replace(/^#/, '').toLowerCase().replace(/\/+$/, ''); } catch { return false; }
  if (route !== PLAYER_SHEET_ROUTE) return false;
  try { window.history.replaceState(window.history.state, '', '#music'); } catch { /* the old address stays */ }
  return true;
};
try {
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    _mpSheetHub.atLoad = _mpTakeSheetRoute();
    window.addEventListener('hashchange', () => { if (_mpTakeSheetRoute()) openPlayerSheet(); });
  }
} catch { /* no window: nothing to route */ }

const MiniPlayer = () => {
  const p = usePlayback();
  const [showQueue, setShowQueue] = React.useState(false);
  const [showCast, setShowCast] = React.useState(false);
  const [showLyrics, setShowLyrics] = React.useState(false);
  // 'closed' | 'open' | 'closing' (the shrink back into the bar is running).
  const [sheet, setSheet] = React.useState('closed');
  const sheetOpen = sheet !== 'closed';
  const hasItem = !!(p.available && p.current);
  // A failed "Stop and close" is said here, in the page's usual toast.
  const [fire, toastNode] = (typeof useToast === 'function' ? useToast : () => [null, null])();
  const closeBar = async () => {
    try { await p.closePlayer(); }
    catch (e) {
      if (typeof fire !== 'function') return;
      if (typeof reportMutationFailure === 'function') reportMutationFailure(fire, 'stop', e);
      else fire("couldn't stop the room — the player is still open", { tone: 'err' });
    }
  };
  // The library song whose lyrics the bar offers (lyrics.jsx, loaded after
  // this file); none for radio, podcasts, audiobooks and plugin streams. A
  // lyrics.jsx that failed to load must not take the player down with it.
  const lyrId = hasItem && typeof lyricsTrackIdFor === 'function' ? lyricsTrackIdFor(p) : null;
  // Read the song's lyrics as it starts — one quiet read, shared with every
  // lyrics surface (useLyrics): the float and the sheet open on them at
  // once, and a viewer the server refuses loses the button as the song
  // starts, not when it is pressed. (Which function runs is fixed for the
  // life of the page, so the hooks inside always run in the same order.)
  (typeof useLyrics === 'function' ? useLyrics : () => null)(lyrId);
  React.useEffect(() => { if (lyrId == null) setShowLyrics(false); }, [lyrId]);

  // The expanded player (PlayerSheet) is a history entry of its own, so
  // Back (the phone's gesture, the browser's button) closes it instead of
  // leaving the page it was opened over. Closing it any other way pops that
  // entry again — unless something else (a tab in the strip, a link) has
  // already pushed past it, which closes the sheet too. Closing it in place
  // (chevron, Escape, Back, a swipe down) hands focus back to the bar
  // control that opened it; a close that came with a new page leaves focus
  // to that page. Opening and closing animate in PlayerSheet: it grows out
  // of the bar (barRef) and shrinks back into it, then says onClosed.
  const barRef = React.useRef(null);
  const infoRef = React.useRef(null);     // the cover and title (every width)
  const deskOpenRef = React.useRef(null); // the desktop's chevron
  const expandRef = React.useRef(null);   // the phone's "open player"
  const openerRef = React.useRef('expand');
  const refocusRef = React.useRef(false);
  const openHashRef = React.useRef('');
  const sheetRef = React.useRef('closed');
  sheetRef.current = sheet;
  const openSheet = (from) => {
    if (sheetRef.current !== 'closed') return;
    setShowQueue(false);
    setShowCast(false);
    setShowLyrics(false);
    openerRef.current = from || 'expand';
    openHashRef.current = window.location.hash;
    try { window.history.pushState({ domovoiPlayerSheet: true }, ''); } catch {}
    sheetRef.current = 'open';
    setSheet('open');
  };
  // `instant`: no shrink (the bar itself is going away).
  const closeSheet = React.useCallback((refocus, instant) => {
    if (sheetRef.current === 'closed') return;
    refocusRef.current = refocus !== false;
    sheetRef.current = instant ? 'closed' : 'closing';
    setSheet(instant ? 'closed' : 'closing');
    try {
      const st = window.history.state;
      if (st && st.domovoiPlayerSheet) window.history.back();
    } catch {}
  }, []);
  // A step in history already happened: just close (no back of our own).
  const sheetGone = React.useCallback((refocus) => {
    if (sheetRef.current !== 'open') return;
    refocusRef.current = refocus;
    sheetRef.current = 'closing';
    setSheet('closing');
  }, []);
  const sheetClosed = React.useCallback(() => {
    sheetRef.current = 'closed';
    setSheet('closed');
  }, []);
  // openPlayerSheet() and #music/player reach this bar through the hub.
  const openRef = React.useRef(null);
  openRef.current = () => {
    if (!hasItem) return false;
    if (sheetRef.current === 'closed') openSheet('info');
    return true;
  };
  React.useEffect(() => {
    const fn = () => (openRef.current ? openRef.current() : false);
    _mpSheetHub.openers.add(fn);
    // An address that asked for the player as the page loaded: open it if
    // something is already queued, else Music is where it lands.
    if (_mpSheetHub.atLoad) { _mpSheetHub.atLoad = false; fn(); }
    return () => { _mpSheetHub.openers.delete(fn); };
  }, []);
  React.useEffect(() => {
    if (!sheetOpen) return undefined;
    // Any history step while it is open leaves the entry it pushed — the
    // back gesture, or a tab's new hash. (Not only a step to a state-less
    // entry: after a tab and back again, the entry behind this sheet's can
    // be an older sheet's, and back must still close this one.)
    const onPop = () => sheetGone(window.location.hash === openHashRef.current);
    // A new address closes it — except #music/player rewritten to the
    // #music it was opened over (the link asked for what is showing).
    const onHash = () => {
      if (window.location.hash === openHashRef.current) return;
      sheetGone(false);
    };
    // Escape: unless something inside took it (the visualizer's preset
    // list), or the visualizer is full screen (Escape leaves that first).
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      try { if (document.fullscreenElement || document.webkitFullscreenElement) return; } catch {}
      closeSheet();
    };
    // A page in the sidebar or the phone's strip closes it — the one
    // already showing too, which changes no hash. React has handled the tap
    // at its root before it bubbles up to the document, so a page that
    // navigated has already pushed past the sheet's entry and closeSheet
    // doesn't go back.
    const onTap = (e) => {
      const t = e && e.target;
      if (t && t.closest && t.closest('.sidebar .nav-item')) closeSheet(false);
    };
    window.addEventListener('popstate', onPop);
    window.addEventListener('hashchange', onHash);
    window.addEventListener('keydown', onKey);
    document.addEventListener('click', onTap);
    return () => {
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener('keydown', onKey);
      document.removeEventListener('click', onTap);
    };
  }, [sheetOpen, closeSheet, sheetGone]);
  React.useEffect(() => {
    if (sheetOpen || !refocusRef.current) return;
    refocusRef.current = false;
    // Back to what opened it; a control the width now hides can't take
    // focus, so the cover-and-title button (shown at every width) does.
    const want = { info: infoRef, desk: deskOpenRef, expand: expandRef }[openerRef.current] || expandRef;
    const shown = (r) => r.current && r.current.focus && (r.current.offsetParent !== null || r.current.offsetParent === undefined);
    const b = shown(want) ? want.current : (infoRef.current || want.current);
    if (b && b.focus) { try { b.focus({ preventScroll: true }); } catch {} }
  }, [sheetOpen]);
  // Clearing the queue from the sheet empties the player out from under it.
  React.useEffect(() => { if (sheetOpen && !hasItem) closeSheet(false, true); }, [sheetOpen, hasItem, closeSheet]);
  // While the bar shows, <html> carries .mp-docked: styles.css turns it
  // into --player-h, which toasts and notices add to their offset so they
  // float above the bar instead of over its play button.
  React.useEffect(() => {
    const root = document.documentElement;
    if (!hasItem || !root || !root.classList) return undefined;
    root.classList.add('mp-docked');
    return () => root.classList.remove('mp-docked');
  }, [hasItem]);
  // The other way round: a desktop window narrowed to a phone's width
  // hides the buttons that toggle the floating queue, cast target and
  // lyrics (and the 380px queue is wider than the screen), so close them.
  const panelOpen = showQueue || showCast || showLyrics;
  React.useEffect(() => {
    if (!panelOpen || typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia(_MP_PHONE);
    return _mpOnMediaChange(mq, () => {
      if (mq.matches) { setShowQueue(false); setShowCast(false); setShowLyrics(false); }
    });
  }, [panelOpen]);

  if (!hasItem) return null;
  const it = p.current;
  const dur = p.durationSec || it.durationSec || 0;
  const pct = dur > 0 ? Math.min(100, (p.positionSec / dur) * 100) : 0;
  const remote = p.target.kind === 'room';
  const playing = p.status === 'playing';

  return (
    <>
      {showQueue && <QueuePanel p={p} onClose={() => setShowQueue(false)}/>}
      {showCast && <CastMenu p={p} onClose={() => setShowCast(false)}/>}
      {showLyrics && lyrId != null && typeof LyricsFloat === 'function' && (
        <LyricsFloat trackId={lyrId}
                     follow={remote ? { kind: 'room', roomId: p.target.roomId } : { kind: 'local' }}
                     onClose={() => setShowLyrics(false)}/>
      )}
      {sheetOpen && <PlayerSheet p={p} closing={sheet === 'closing'} originRef={barRef}
                                 onClose={() => closeSheet()} onClosed={sheetClosed}/>}
      {toastNode}
      {/* `--dock-bottom` is 0 on a desktop and the phone strip's height at
          760px and below (styles.css), so on a phone the player docks ON
          TOP of the five tabs instead of covering them. */}
      {/* Hidden (not removed) while the expanded player covers it: on a
          desktop its left end, under the sidebar, would show beside the
          sheet. It is back as the sheet shrinks into it. */}
      <div className={`mini-player${sheet === 'open' ? ' under-sheet' : ''}`} ref={barRef}>
        <div className="mp-line mp-phone" aria-hidden="true"><span style={{ width: `${pct}%` }}/></div>
        {/* left: cover + title — a button: click, Enter or Space opens the
            expanded player, at every width */}
        <button type="button" className="mp-info" ref={infoRef} onClick={() => openSheet('info')}
                title="open player" aria-haspopup="dialog" aria-expanded={sheet === 'open'}>
          <CoverTile item={it} size={44}/>
          <span className="mp-text">
            <span className="mp-title">{it.title}</span>
            <span className="mp-sub mono">
              {remote && <span style={{ color: 'var(--brand-ink)' }}>◆ {p.target.roomId} · </span>}
              {it.artist || (it.seekable === false ? 'live stream' : '—')}
            </span>
          </span>
        </button>
        {/* center: transport + progress */}
        <div className="mp-center">
          <div className="mp-transport">
            <button className="btn btn-ghost btn-icon mp-desk" onClick={p.prev} title="previous" aria-label="previous">
              <Icon name="skip-back" size={14}/>
            </button>
            <button className="btn btn-primary btn-icon mp-play" onClick={p.toggle}
                    title={playing ? 'pause' : 'play'} aria-label={playing ? 'pause' : 'play'}>
              <Icon name={playing ? 'pause' : 'play'} size={16}/>
            </button>
            <button className="btn btn-ghost btn-icon" onClick={p.next} title="next" aria-label="next">
              <Icon name="skip-forward" size={14}/>
            </button>
          </div>
          <div className="mp-seek mp-desk">
            <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', width: 34, textAlign: 'right' }}>{fmtDur(p.positionSec)}</span>
            <div onClick={(e) => {
                   if (!it.seekable) return;
                   const r = e.currentTarget.getBoundingClientRect();
                   p.seek(((e.clientX - r.left) / r.width) * dur);
                 }}
                 style={{ flex: 1, height: 4, borderRadius: 2, background: 'var(--sunken)', overflow: 'hidden', cursor: it.seekable ? 'pointer' : 'default' }}>
              <div style={{ height: '100%', width: `${pct}%`, background: 'var(--brand)' }}/>
            </div>
            <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', width: 34 }}>{it.seekable ? fmtDur(dur) : 'live'}</span>
          </div>
        </div>
        {/* right: volume + queue + cast + expand (desktop) / open player (phone) */}
        <div className="mp-side">
          <div className="mp-side-desk mp-desk">
            {p.sleepRemainingSec != null && (
              <span className="mono mp-sleep" title="sleep timer">
                <Icon name="moon" size={11}/>{p.sleepRemainingSec < 0 ? 'end' : fmtDur(p.sleepRemainingSec)}
              </span>
            )}
            {!remote && (
              <div className="mp-vol">
                <IconButton name={p.muted || p.volume === 0 ? 'volume-x' : 'volume-2'} onClick={p.toggleMute}/>
                <input type="range" min={0} max={1} step={0.01} value={p.muted ? 0 : p.volume}
                       onChange={(e) => p.setVolume(Number(e.target.value))}
                       style={{ width: 70 }}/>
              </div>
            )}
            {/* lyrics: one floating panel at a time with the queue and the cast target */}
            {lyrId != null && (
              <IconButton name="mic-vocal" title="lyrics" aria-label="lyrics" aria-pressed={showLyrics}
                          onClick={() => { setShowQueue(false); setShowCast(false); setShowLyrics((s) => !s); }}
                          style={showLyrics ? { color: 'var(--brand-ink)' } : undefined}/>
            )}
            <IconButton name="list-music" onClick={() => { setShowLyrics(false); setShowQueue((s) => !s); }} title="queue"/>
            <IconButton name={remote ? 'cast' : 'monitor-speaker'}
                        onClick={() => { setShowLyrics(false); setShowCast((s) => !s); }}
                        title="cast target"
                        style={remote ? { color: 'var(--brand-ink)' } : undefined}/>
            {/* The expanded player, over this page (2026-11-12: it was the
                Music page's Player tab). */}
            <button ref={deskOpenRef} type="button" className="btn btn-ghost btn-icon"
                    onClick={() => openSheet('desk')} title="open full player" aria-label="open full player"
                    aria-haspopup="dialog" aria-expanded={sheet === 'open'}>
              <Icon name="chevron-up" size={14}/>
            </button>
          </div>
          <button ref={expandRef} className="btn btn-ghost btn-icon mp-phone mp-expand" onClick={() => openSheet('expand')}
                  title="open player" aria-label="open player"
                  aria-haspopup="dialog" aria-expanded={sheet === 'open'}>
            <Icon name="chevron-up" size={16}/>
          </button>
          <button className="btn btn-ghost btn-icon mp-close" onClick={closeBar}
                  title="Stop and close" aria-label="Stop and close">
            <Icon name="x" size={16}/>
          </button>
        </div>
      </div>
    </>
  );
};

/* ═══════════════════════════════════════════════════════════════════════
 * The expanded player (2026-11-12; it was the Music page's Player tab and,
 * on a phone, a sheet of its own). One component for every width, opened
 * from the docked bar — its cover and title, the desktop's chevron, the
 * phone's "open player", or #music/player — and grown out of it.
 *
 * Where it sits (styles.css, "The expanded player"): over everything but
 * the navigation. On a desktop that is the column right of the sidebar,
 * top to bottom, the topbar and the docked bar included; on a phone the
 * screen above the tab strip. The sidebar stays live so another page is
 * still one click away (a page there closes the sheet).
 *
 * What it carries: the cover, title and artist, the seek bar, previous /
 * play / next, volume and speed, where it plays, the sleep timer, offline
 * download, the spoken-audio skips and chapters — and a stage: the MilkDrop
 * visualizer (visualizer.jsx) drawn BEHIND a panel with three tabs, Lyrics
 * · Equalizer · Queue, plus Hide (no panel: the picture alone). The panel
 * is see-through by the amount the visualizer's own bar sets. With the
 * visualizer off, unsupported or casting, the panel is a plain card.
 *
 * Motion: opening animates the sheet's clip from the bar's box to its own
 * (280 ms) while the content fades up; closing runs it back (240 ms) and
 * unmounts after. The house ease, cubic-bezier(0.2, 0, 0, 1). With
 * prefers-reduced-motion: a 120 ms fade, nothing grows.
 *
 * A dialog (aria-modal): focus moves to its close chevron, Tab stays
 * inside, and closing hands focus back to the bar (MiniPlayer). Closes
 * with the chevron, Escape, Back, a swipe down on its header (touch), or a
 * page in the sidebar / strip.
 * ═══════════════════════════════════════════════════════════════════════ */
const _MP_OPEN_MS = 280;
const _MP_CLOSE_MS = 240;
const _MP_FADE_MS = 120;
const _MP_EASE = 'cubic-bezier(0.2, 0, 0, 1)';
const _MP_CLIP_FULL = 'inset(0px 0px 0px 0px)';
const _mpReducedMotion = () => {
  try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  catch { return false; }
};
const _mpRect = (el) => {
  try { return el && typeof el.getBoundingClientRect === 'function' ? el.getBoundingClientRect() : null; }
  catch { return null; }
};
// The clip that leaves only `f` (the bar's box) of `s` (the sheet's box).
const _mpClipTo = (s, f) => `inset(${Math.max(0, f.top - s.top)}px ${Math.max(0, s.right - f.right)}px `
  + `${Math.max(0, s.bottom - f.bottom)}px ${Math.max(0, f.left - s.left)}px)`;

/* The panel the person picked last, kept for this browser tab's session
 * (sessionStorage, and in memory when that is refused). */
const _MP_TAB_KEY = 'domovoi-player-tab';
const _mpTabMem = { v: null };
const _mpTabGet = () => {
  try { const v = sessionStorage.getItem(_MP_TAB_KEY); if (v) return v; } catch { /* memory below */ }
  return _mpTabMem.v;
};
const _mpTabSet = (v) => {
  _mpTabMem.v = v;
  try { sessionStorage.setItem(_MP_TAB_KEY, v); } catch { /* kept in memory */ }
};

/* Which panel shows. `here`: the tab picked for THIS song (it stays, even
 * Lyrics on a song with none found: the person asked to look). Else the
 * session's pick while it still applies — Hide and Queue always do, the
 * Equalizer while this browser plays (not casting), Lyrics while the song
 * has some (or they are still loading) — else the default: Lyrics when
 * the song has them, otherwise the Equalizer, otherwise the Queue. */
const playerSheetTab = ({ here, picked, lyricsTab, lyricsFound, lyricsLoading, eqTab }) => {
  const usable = (t) => t === 'hide' || t === 'queue' || (t === 'eq' && eqTab) || (t === 'lyrics' && lyricsTab);
  if (here && usable(here)) return here;
  const withLyrics = lyricsTab && (lyricsFound || lyricsLoading);
  if (picked === 'hide' || picked === 'queue') return picked;
  if (picked === 'eq' && eqTab) return 'eq';
  if (picked === 'lyrics' && withLyrics) return 'lyrics';
  if (withLyrics) return 'lyrics';
  return eqTab ? 'eq' : 'queue';
};

const _MP_TAB_LABELS = { lyrics: 'Lyrics', eq: 'Equalizer', queue: 'Queue', hide: 'Hide' };

const PlayerSheet = ({ p, closing = false, originRef, onClose, onClosed }) => {
  const sheetRef = React.useRef(null);
  const innerRef = React.useRef(null);
  const closeRef = React.useRef(null);
  const drag = React.useRef(null);
  const onClosedRef = React.useRef(onClosed);
  onClosedRef.current = onClosed;

  // Focus moves in, to the close chevron.
  React.useEffect(() => {
    const b = closeRef.current;
    if (b && b.focus) { try { b.focus({ preventScroll: true }); } catch {} }
  }, []);

  // Grow out of the bar. (No Web Animations — an old browser, the test
  // harness — : it just appears.)
  React.useLayoutEffect(() => {
    const el = sheetRef.current;
    if (!el || typeof el.animate !== 'function') return;
    const inner = innerRef.current;
    const fade = (ms) => el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: ms, easing: 'linear' });
    if (_mpReducedMotion()) { fade(_MP_FADE_MS); return; }
    const s = _mpRect(el);
    const f = _mpRect(originRef && originRef.current);
    if (!s || !f || !f.width || !f.height) { fade(_MP_FADE_MS); return; }
    el.animate([{ clipPath: _mpClipTo(s, f) }, { clipPath: _MP_CLIP_FULL }],
               { duration: _MP_OPEN_MS, easing: _MP_EASE });
    if (inner && typeof inner.animate === 'function') {
      inner.animate([{ opacity: 0, transform: 'translateY(24px)' }, { opacity: 1, transform: 'none' }],
                    { duration: _MP_OPEN_MS, easing: _MP_EASE });
    }
  }, []);

  // Shrink back into the bar, then let MiniPlayer unmount it.
  React.useEffect(() => {
    if (!closing) return undefined;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      if (onClosedRef.current) onClosedRef.current();
    };
    const el = sheetRef.current;
    if (!el || typeof el.animate !== 'function') { finish(); return undefined; }
    try { if (el.getAnimations) el.getAnimations({ subtree: true }).forEach((a) => a.cancel()); } catch {}
    const inner = innerRef.current;
    const d = drag.current;
    let anim;
    if (_mpReducedMotion()) {
      anim = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: _MP_FADE_MS, easing: 'linear', fill: 'forwards' });
    } else if (d && d.dy > 0) {
      // A swipe down carries on down, off the screen.
      anim = el.animate([{ transform: `translateY(${d.dy}px)` }, { transform: 'translateY(100%)' }],
                        { duration: 200, easing: _MP_EASE, fill: 'forwards' });
    } else {
      const s = _mpRect(el);
      const f = _mpRect(originRef && originRef.current);
      if (s && f && f.width && f.height) {
        anim = el.animate([{ clipPath: _MP_CLIP_FULL }, { clipPath: _mpClipTo(s, f) }],
                          { duration: _MP_CLOSE_MS, easing: _MP_EASE, fill: 'forwards' });
        if (inner && typeof inner.animate === 'function') {
          inner.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(24px)' }],
                        { duration: _MP_CLOSE_MS, easing: _MP_EASE, fill: 'forwards' });
        }
      } else {
        anim = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: _MP_FADE_MS, easing: 'linear', fill: 'forwards' });
      }
    }
    anim.onfinish = finish;
    anim.oncancel = finish;
    // A hidden browser tab may never run it: close all the same.
    const t = setTimeout(finish, _MP_CLOSE_MS + 400);
    return () => clearTimeout(t);
  }, [closing]);

  // A swipe down on the header (touch) closes it; a short one springs back.
  const onTouchStart = (e) => {
    const t = e.touches && e.touches[0];
    if (!t || closing) return;
    drag.current = { y0: t.clientY, t0: Date.now(), dy: 0 };
  };
  const onTouchMove = (e) => {
    const d = drag.current;
    const t = e.touches && e.touches[0];
    const el = sheetRef.current;
    if (!d || !t || !el || closing) return;
    d.dy = Math.max(0, t.clientY - d.y0);
    el.style.transform = d.dy ? `translateY(${d.dy}px)` : '';
  };
  const onTouchEnd = () => {
    const d = drag.current;
    const el = sheetRef.current;
    if (!d || !el || closing) return;
    const fast = d.dy > 32 && d.dy / Math.max(1, Date.now() - d.t0) > 0.6;
    if (d.dy > 96 || fast) { onClose(); return; }
    drag.current = null;
    const from = el.style.transform;
    el.style.transform = '';
    if (from && typeof el.animate === 'function' && !_mpReducedMotion()) {
      el.animate([{ transform: from }, { transform: 'none' }], { duration: 180, easing: _MP_EASE });
    }
  };

  // Tab and Shift+Tab stay inside.
  const onKeyDown = (e) => {
    if (e.key !== 'Tab') return;
    const el = sheetRef.current;
    if (!el || typeof el.querySelectorAll !== 'function') return;
    const list = Array.from(el.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), '
      + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((x) => x.offsetParent !== null);
    if (!list.length) return;
    const a = document.activeElement;
    const first = list[0];
    const last = list[list.length - 1];
    if (e.shiftKey && (a === first || !el.contains(a))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (a === last || !el.contains(a))) { e.preventDefault(); first.focus(); }
  };

  const it = p.current;
  const dur = p.durationSec || it.durationSec || 0;
  const pct = dur > 0 ? Math.min(100, (p.positionSec / dur) * 100) : 0;
  const remote = p.target.kind === 'room';
  const playing = p.status === 'playing';
  const spoken = !!(it.meta && it.meta.itemType);
  const seekAt = (e) => {
    if (!it.seekable) return;
    const r = e.currentTarget.getBoundingClientRect();
    p.seek(((e.clientX - r.left) / r.width) * dur);
  };

  // ── the panel's tabs ───────────────────────────────────────────────
  // lyrics.jsx, music_player_panel.jsx and visualizer.jsx load after this
  // file; one that failed to load takes its part away, not the player.
  // (Which of them exist is fixed for the life of the page, so the hooks
  // below always run in the same order.)
  const lyrId = typeof lyricsTrackIdFor === 'function' && typeof LyricsView === 'function'
    ? lyricsTrackIdFor(p) : null;
  const lyr = (typeof useLyrics === 'function' ? useLyrics : () => null)(lyrId);
  const lyrDoc = lyr && lyr.state === 'ready' ? lyr.doc : null;
  const lyricsFound = !!(lyrDoc && ((lyrDoc.status === 'synced' && Array.isArray(lyrDoc.lines) && lyrDoc.lines.length)
    || (lyrDoc.text && (lyrDoc.status === 'plain' || lyrDoc.status === 'synced'))));
  const eqTab = !remote && typeof EqPanel === 'function';
  const songKey = remote && p.remoteNp ? `room:${p.remoteNp.track_id ?? ''}:${it.uid}` : it.uid;
  const [here, setHere] = React.useState(null);           // { song, tab }
  const [picked, setPicked] = React.useState(_mpTabGet);
  const tab = playerSheetTab({
    here: here && here.song === songKey ? here.tab : null, picked,
    lyricsTab: lyrId != null, lyricsFound, lyricsLoading: !!(lyr && lyr.state === 'loading'), eqTab,
  });
  const tabs = [lyrId != null && 'lyrics', eqTab && 'eq', 'queue', 'hide'].filter(Boolean);
  const pickTab = (t) => {
    setHere({ song: songKey, tab: t });
    setPicked(t);
    _mpTabSet(t);
  };
  const tabKey = (e) => {
    const i = tabs.indexOf(tab);
    const to = e.key === 'ArrowRight' ? tabs[(i + 1) % tabs.length]
      : e.key === 'ArrowLeft' ? tabs[(i - 1 + tabs.length) % tabs.length]
      : e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs[tabs.length - 1] : null;
    if (!to) return;
    if (e.preventDefault) e.preventDefault();
    pickTab(to);
    try {
      const b = typeof document.getElementById === 'function' ? document.getElementById(`mp-tab-${to}`) : null;
      if (b && b.focus) b.focus();
    } catch {}
  };
  const tabStrip = (
    <div className="mp-tabs" role="tablist" aria-label="player panel" onKeyDown={tabKey}>
      {tabs.map((t) => (
        <button key={t} id={`mp-tab-${t}`} type="button" role="tab"
                className={`mp-tab${t === tab ? ' on' : ''}`} aria-selected={t === tab}
                aria-controls={t === 'hide' ? undefined : 'mp-tabpanel'} tabIndex={t === tab ? 0 : -1}
                title={t === 'hide' ? 'hide the panel' : undefined}
                onClick={() => pickTab(t)}>
          {_MP_TAB_LABELS[t]}
        </button>
      ))}
    </div>
  );
  const panel = tab === 'hide' ? null : (
    <div className={`mp-panel mp-panel-${tab}`} id="mp-tabpanel" role="tabpanel"
         aria-labelledby={`mp-tab-${tab}`}>
      {tab === 'lyrics' && (
        <LyricsView trackId={lyrId} height={480}
                    follow={remote ? { kind: 'room', roomId: p.target.roomId } : { kind: 'local' }}/>
      )}
      {tab === 'eq' && <EqPanel p={p}/>}
      {tab === 'queue' && (
        <>
          <div className="mp-sheet-sec-head">
            <span>queue · {playerQueueCount(p.queue, p.index)}</span>
            <IconButton name="trash-2" onClick={p.clearQueue} title="clear queue" aria-label="clear queue"/>
          </div>
          <div className="mp-q-list">
            <PlayerQueueRows queue={p.queue} index={p.index}
                             jumpTo={p.jumpTo} removeAt={p.removeAt} moveItem={p.moveItem}/>
          </div>
          <PlayerUpNext p={p}/>
          <PlayerQueueAdd p={p}/>
        </>
      )}
    </div>
  );
  const stage = typeof NowPlayingVisualizer === 'function'
    ? <NowPlayingVisualizer p={p} remote={remote} head={tabStrip} panel={panel}/>
    : (
      <div className="mp-stage-solid">
        <div className="viz-head">{tabStrip}</div>
        {panel}
      </div>
    );

  return (
    <div ref={sheetRef} className="mp-sheet" role="dialog" aria-modal="true" aria-label="now playing"
         onKeyDown={onKeyDown}>
      <div ref={innerRef} className="mp-sheet-inner">
        <div className="mp-sheet-head" onTouchStart={onTouchStart} onTouchMove={onTouchMove}
             onTouchEnd={onTouchEnd} onTouchCancel={onTouchEnd}>
          <button ref={closeRef} className="btn btn-ghost btn-icon" onClick={onClose}
                  title="close" aria-label="close player">
            <Icon name="chevron-down" size={18}/>
          </button>
          <span className="mp-sheet-title">now playing</span>
          <span className="mp-sheet-grip" aria-hidden="true"/>
        </div>
        <div className="mp-sheet-body">
          <div className="mp-sheet-grid">
            <div className="mp-sheet-now">
              <CoverTile item={it} size={200} radius="var(--r-md)"/>
              <div className="mp-sheet-meta">
                {remote && <Pill tone="live" live>casting to {p.target.roomId}</Pill>}
                <div className="mp-sheet-name">{it.title}</div>
                <div className="mp-sheet-by">{it.artist || (it.seekable === false ? 'live stream' : '—')}</div>
                {it.album && <div className="mp-sheet-album">{it.album}</div>}
              </div>
              <div className="mp-sheet-seek">
                <span className="mono">{fmtDur(p.positionSec)}</span>
                <div className="mp-sheet-track" onClick={seekAt} style={{ cursor: it.seekable ? 'pointer' : 'default' }}>
                  <div><span style={{ width: `${pct}%` }}/></div>
                </div>
                <span className="mono">{it.seekable ? fmtDur(dur) : 'live'}</span>
              </div>
              <div className="mp-sheet-transport">
                <button className="btn btn-ghost btn-icon" onClick={p.prev} title="previous" aria-label="previous">
                  <Icon name="skip-back" size={20}/>
                </button>
                <button className="btn btn-primary btn-icon mp-sheet-play" onClick={p.toggle}
                        title={playing ? 'pause' : 'play'} aria-label={playing ? 'pause' : 'play'}>
                  <Icon name={playing ? 'pause' : 'play'} size={20}/>
                </button>
                <button className="btn btn-ghost btn-icon" onClick={p.next} title="next" aria-label="next">
                  <Icon name="skip-forward" size={20}/>
                </button>
              </div>
              {!remote && (
                <div className="mp-sheet-vol">
                  <button className="btn btn-ghost btn-icon" onClick={p.toggleMute}
                          title={p.muted ? 'unmute' : 'mute'} aria-label={p.muted ? 'unmute' : 'mute'}>
                    <Icon name={p.muted || p.volume === 0 ? 'volume-x' : 'volume-2'} size={16}/>
                  </button>
                  <input type="range" min={0} max={1} step={0.01} value={p.muted ? 0 : p.volume}
                         aria-label="volume" onChange={(e) => p.setVolume(Number(e.target.value))}/>
                  {/* Spoken audio has its own speeds in SpokenControls. */}
                  {!spoken && typeof p.setPlaybackRate === 'function' && (
                    <select className="mp-sheet-speed" value={p.playbackRate ?? 1} aria-label="speed"
                            title="speed" onChange={(e) => p.setPlaybackRate(Number(e.target.value))}>
                      {[0.75, 1, 1.25, 1.5, 2].map((r) => <option key={r} value={r}>{r}×</option>)}
                    </select>
                  )}
                </div>
              )}
            </div>
            <div className="mp-sheet-stage">{stage}</div>
            {(typeof SleepTimerControl === 'function' || typeof OfflineControl === 'function'
              || (!remote && spoken)) && (
              <section className="mp-sheet-sec mp-sheet-more" aria-label="more">
                {!remote && typeof SpokenControls === 'function' && <SpokenControls p={p} bare/>}
                {typeof SleepTimerControl === 'function' && <SleepTimerControl p={p}/>}
                {typeof OfflineControl === 'function' && <OfflineControl p={p}/>}
                {!remote && typeof ChapterList === 'function' && <ChapterList p={p} bare/>}
              </section>
            )}
            <section className="mp-sheet-sec mp-sheet-on" aria-label="play on">
              <div className="mp-sheet-sec-head">play on</div>
              <PlayerCastTargets p={p} big/>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
};

/* Cover tile — the item's cover (a library track's comes from the file
 * itself, see CoverArt in components.jsx) over the player's own fallback:
 * the warm gradient for a track, a sunken tile with a radio glyph for a
 * live stream. A cover that 404s stays on the fallback, quietly. */
const CoverTile = ({ item, size = 44, radius = 'var(--r-sm)' }) => {
  const live = !!item && item.seekable === false;
  const grad = 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))';
  return <CoverArt src={(item && item.coverUrl) || null} size={size} radius={radius}
                   icon={live ? 'radio' : 'music'} background={live ? 'var(--sunken)' : grad}
                   iconColor="inherit"/>;
};

/* The queue's rows — drag to reorder, tap to jump, x to remove — shared by
 * the desktop's floating QueuePanel and the phone sheet (styles.css sizes
 * them: 48px rows with a 44px remove button in the sheet). Memoised on
 * the queue and its actions, which are stable between frames, so the
 * position ticker re-rendering the player doesn't re-render every row. */
const PlayerQueueRows = React.memo(function PlayerQueueRows({ queue, index, jumpTo, removeAt, moveItem }) {
  const dragFrom = React.useRef(null);
  // Up next's picks still to come have their own section (PlayerUpNext).
  const shown = queue.filter((it, i) => !_upNextWaiting(it, i, index));
  return (
    <>
      {queue.map((it, i) => (_upNextWaiting(it, i, index) ? null : (
        <div key={it.uid} className={`mp-q-row${i === index ? ' cur' : ''}`}
             draggable onDragStart={() => { dragFrom.current = i; }}
             onDragOver={(e) => e.preventDefault()}
             onDrop={() => { const f = dragFrom.current; dragFrom.current = null; if (f != null && f !== i) moveItem(f, i); }}>
          <div className="mp-q-num mono">
            {i === index ? <Icon name="volume-2" size={12}/> : i + 1}
          </div>
          <div className="mp-q-main" onClick={() => jumpTo(i)}>
            <div className="mp-q-title">{it.title}</div>
            <div className="mp-q-sub mono">{it.artist || '—'}</div>
          </div>
          <IconButton name="x" onClick={() => removeAt(i)} title="remove" aria-label={`remove ${it.title}`}/>
        </div>
      )))}
      {shown.length === 0 && <div className="mp-q-empty">queue is empty</div>}
    </>
  );
});

/* An Up next pick still to come (after the song playing). */
const _upNextWaiting = (it, i, index) => !!(it && it.origin === 'auto' && i > index);
/* The queue's count: what plays and what somebody asked for, not the picks. */
const playerQueueCount = (queue, index) => (queue || []).filter((it, i) => !_upNextWaiting(it, i, index)).length;

/* ── The queue's "up next" section (both queue panels) ──────────────────
 * Below the songs somebody asked for: what Up next will play after them,
 * each with why it was picked, a "less like this" (that artist is out of
 * the mix for the rest of the session) and remove. Its head says what the
 * mix is built from ("based on <song>", "shuffled from <list>") and holds
 * the Autoplay switch; "for <name>" picks whose taste it follows when the
 * household has two people or more. Tap a row to play it now.
 *
 * No misleading controls: nothing at all when the queue has no library
 * song (streams, podcasts, books), for a browser that isn't paired (a
 * guest), or when the server refused this play's Up next. "up next needs
 * the home server" when it couldn't be reached, instead of picks that
 * won't come. */
const PlayerUpNext = ({ p }) => {
  // While casting, Up next is the room's: the picks its server adds as it
  // plays, read from the room's own queue (P4).
  if (p.target && p.target.kind === 'room' && p.target.roomId) return <RoomUpNext roomId={p.target.roomId}/>;
  return <PlayerUpNextHere p={p}/>;
};

const PlayerUpNextHere = ({ p }) => {
  const [note, setNote] = React.useState('');
  const [busy, setBusy] = React.useState(null);      // the row whose "less like this" is on its way
  const u = p.upNext || null;
  const paired = _upNextPaired();
  const library = (p.queue || []).some((it) => it && it.kind === 'library' && it.trackId != null);
  const show = !!(p.available && paired && library && !(u && u.refused));
  // The roster is a household read; quiet, so it never opens a prompt.
  const { items: people } = useApiList(show ? '/api/people' : null,
    { eventTypes: ['people.last_seen.changed'], quiet: true });
  if (!show) return null;
  const on = u ? !!u.autoplay : !!p.autoplay;
  const listMode = !!(u && u.mode === 'list');
  const listName = u && u.seed && u.seed.list ? u.seed.list.name : null;
  const rows = [];
  (p.queue || []).forEach((it, i) => { if (_upNextWaiting(it, i, p.index)) rows.push({ it, i }); });
  const basis = upNextBasis(u);
  const roster = (people || []).filter((x) => x && x.id != null);
  const showFor = roster.length >= 2 && !!(u && u.id);
  const forValue = u && u.person ? String(u.person.id) : '';
  const toggle = async (next) => {
    setNote('');
    try { await p.setAutoplay(next); }
    catch (e) { if (!isAuthFailure(e)) setNote(`Couldn't tell the home server: ${apiErrorText(e)}`); }
  };
  const lessLike = async (row) => {
    setNote(''); setBusy(row.it.uid);
    try {
      const gone = await p.lessLikeThis(row.i);
      const n = Math.max(0, (gone || []).length - 1);
      setNote(`No more ${row.it.artist || 'of that'} in this mix${n ? ` (${n} more taken out)` : ''}.`);
    } catch (e) {
      if (!isAuthFailure(e)) setNote(`Couldn't change the mix: ${apiErrorText(e)}`);
    } finally { setBusy(null); }
  };
  const pickFor = async (v) => {
    setNote('');
    try { await p.setUpNextListener(v === '' ? null : Number(v)); }
    catch (e) { if (!isAuthFailure(e)) setNote(`Couldn't change whose mix this is: ${apiErrorText(e)}`); }
  };
  let line = null;
  if (u && u.offline) line = 'up next needs the home server';
  else if (!on) {
    // A list keeps the rest of itself: say where it stops.
    line = listMode && rows.length ? `autoplay is off — stops after the last song of ${listName || 'this list'}`
      : 'autoplay is off — the queue stops at the end';
  }
  else if (u && u.loading) line = 'finding songs like this one…';
  else if (!rows.length && on) line = 'similar songs join here when the queue is nearly over';
  return (
    <section className="mp-un" aria-label="up next">
      <div className="mp-un-head">
        <div className="mp-un-what">
          <span className="mp-un-name">up next</span>
          {basis && <span className="mp-un-basis">{basis}</span>}
        </div>
        <label className="mp-un-auto" title="keep playing similar songs when the queue ends">
          <input type="checkbox" role="switch" checked={on} aria-checked={on}
                 aria-label="autoplay: keep playing similar songs when the queue ends"
                 onChange={(e) => toggle(!!e.target.checked)}/>
          <span>autoplay</span>
        </label>
      </div>
      {showFor && (
        <label className="mp-un-for">
          <span>for</span>
          <select value={forValue} aria-label="whose taste this mix follows"
                  onChange={(e) => pickFor(e.target.value)}>
            <option value="">household</option>
            {roster.map((x) => <option key={x.id} value={String(x.id)}>{x.name}</option>)}
          </select>
        </label>
      )}
      {line && <div className="mp-un-line" role="status">{line}</div>}
      {rows.length > 0 && (
        <div className="mp-q-list mp-un-list">
          {rows.map((row) => (
            <div key={row.it.uid} className="mp-q-row mp-un-row">
              <div className="mp-q-num mono" aria-hidden="true"><Icon name="sparkles" size={12}/></div>
              <div className="mp-q-main" role="button" tabIndex={0} title="play now"
                   onClick={() => p.jumpTo(row.i)}
                   onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { if (e.preventDefault) e.preventDefault(); p.jumpTo(row.i); } }}>
                <div className="mp-q-title">{row.it.title}</div>
                <div className="mp-un-sub">
                  <span className="mp-q-sub mono">{row.it.artist || '—'}</span>
                  {row.it.reasonLabel && <span className="mp-un-why">{row.it.reasonLabel}</span>}
                </div>
              </div>
              <div className="mp-un-acts">
                <IconButton name="thumbs-down" onClick={() => lessLike(row)} disabled={busy === row.it.uid}
                            title={`less like this: no more ${row.it.artist || 'of this artist'} in this mix`}
                            aria-label={`less like this: ${row.it.artist || row.it.title}`}/>
                <IconButton name="x" onClick={() => p.removeAt(row.i)} title="remove"
                            aria-label={`remove ${row.it.title}`}/>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="mp-q-note" role="status" aria-live="polite">{note}</div>
    </section>
  );
};

/* ── A room's "up next" section (P4) ────────────────────────────────────
 * What the room's own Up next will play after what somebody asked for,
 * read from the room's queue: each pick with why it was picked; tap one to
 * play it now (a real jump); "less like this" (that artist is out of the
 * mix) and remove. The head says what the mix is built from and holds the
 * Autoplay switch. The player's queue while casting and a room's drawer
 * (`limit`: its first few, with "open queue") show it; the Music page's
 * Room queue tab draws the same rows inside its own list.
 *
 * No misleading controls: nothing at all when the room has no Up next;
 * no switch and no "less like this" for a guest (a guest may remove, and
 * jump, on a playing room). The queue is re-read when the room's song
 * changes, every 10 s while shown (the server adds picks as it plays), and
 * after every press. */
const ROOM_UP_NEXT_POLL_MS = 10000;
const RoomUpNext = ({ roomId, limit = null, onOpenQueue = null, send = null, className = '' }) => {
  const [note, setNote] = React.useState('');
  const [busy, setBusy] = React.useState(null);
  const [gone, setGone] = React.useState(() => new Set());
  const deviceId = DeviceIdentity.id();
  const { data: q, refresh } = useApiObject(
    roomId ? `${roomQueuePath(roomId)}?device_id=${encodeURIComponent(deviceId)}` : null,
    { eventTypes: ['music.now_playing.changed'], quiet: true });
  React.useEffect(() => {
    if (!roomId) return undefined;
    const t = setInterval(() => {
      try { if (typeof document !== 'undefined' && document.hidden) return; } catch {}
      refresh();
    }, ROOM_UP_NEXT_POLL_MS);
    return () => clearInterval(t);
  }, [roomId]); // eslint-disable-line
  // Songs taken out here leave the list at once; a fresh read of the queue
  // (its entries changed) is the truth again.
  const qKey = ((q && q.items) || []).map((x) => x && x.song_id).join(',');
  React.useEffect(() => { setGone(new Set()); }, [qKey]);
  if (!q || !q.session_id) return null;
  const guest = !!q.guest;
  const editable = q.editable !== false;
  const post = send || apiPost;
  const all = roomUpNextRows(q.items).filter(({ it }) => !gone.has(it.song_id));
  const rows = limit ? all.slice(0, limit) : all;
  const basis = roomUpNextBasis(q);
  const line = roomUpNextLine(q, all);
  const act = async (key, verb, fn) => {
    setNote(''); setBusy(key);
    try { await fn(); refresh(); }
    catch (e) { if (!isAuthFailure(e)) setNote(`Couldn't ${verb}: ${apiErrorText(e)}`); }
    finally { setBusy(null); }
  };
  const toggle = (on) => act('autoplay', 'change autoplay', () =>
    apiPatch(`/api/music/up-next/sessions/${q.session_id}`, { autoplay: !!on }));
  const playNow = (it) => act(it.song_id, 'play that', () => roomPlayEntry(roomId, it.song_id, post));
  const remove = (it) => act(it.song_id, 'remove that', async () => {
    await post(roomQueuePath(roomId, '/remove'), { song_ids: [it.song_id], device_id: deviceId });
    setGone((g) => new Set([...g, it.song_id]));
  });
  const lessLike = (it) => act(it.song_id, 'change the mix', async () => {
    const r = await apiPost(`/api/music/up-next/sessions/${q.session_id}/feedback`,
      { seq: it.up_next_seq, kind: 'less_like_this' });
    // The server takes that artist's picks out of the room's queue on its
    // next look; they leave this list now.
    const seqs = new Set((r && r.removed) || []);
    const out = (q.items || []).filter((x) => x && seqs.has(x.up_next_seq) && !x.playing).map((x) => x.song_id);
    setGone((g) => new Set([...g, ...out, it.song_id]));
    const n = Math.max(0, out.length - 1);
    setNote(`No more ${it.artist || 'of that'} in this mix${n ? ` (${n} more taken out)` : ''}.`);
  });
  return (
    <section className={`mp-un${className ? ` ${className}` : ''}`} aria-label={`up next in ${roomId}`}>
      <div className="mp-un-head">
        <div className="mp-un-what">
          <span className="mp-un-name">up next</span>
          {basis && <span className="mp-un-basis">{basis}</span>}
        </div>
        {!guest && editable && (
          <label className="mp-un-auto" title="keep playing similar songs when the queue ends">
            <input type="checkbox" role="switch" checked={!!q.autoplay} aria-checked={!!q.autoplay}
                   disabled={busy === 'autoplay'}
                   aria-label={`autoplay in ${roomId}: keep playing similar songs when the queue ends`}
                   onChange={(e) => toggle(!!e.target.checked)}/>
            <span>autoplay</span>
          </label>
        )}
      </div>
      {line && <div className="mp-un-line" role="status">{line}</div>}
      {rows.length > 0 && (
        <div className="mp-q-list mp-un-list">
          {rows.map(({ it }) => (
            <div key={it.song_id} className="mp-q-row mp-un-row">
              <div className="mp-q-num mono" aria-hidden="true"><Icon name="sparkles" size={12}/></div>
              <div className="mp-q-main" role="button" tabIndex={0} title={`play now in ${roomId}`}
                   onClick={() => editable && playNow(it)}
                   onKeyDown={(e) => { if (editable && (e.key === 'Enter' || e.key === ' ')) { if (e.preventDefault) e.preventDefault(); playNow(it); } }}>
                <div className="mp-q-title">{it.title || 'unknown'}</div>
                <div className="mp-un-sub">
                  <span className="mp-q-sub mono">{it.artist || '—'}</span>
                  {it.reason_label && <span className="mp-un-why">{it.reason_label}</span>}
                </div>
              </div>
              {editable && (
                <div className="mp-un-acts">
                  {!guest && (
                    <IconButton name="thumbs-down" onClick={() => lessLike(it)} disabled={busy === it.song_id}
                                title={`less like this: no more ${it.artist || 'of this artist'} in this mix`}
                                aria-label={`less like this: ${it.artist || it.title}`}/>
                  )}
                  <IconButton name="x" onClick={() => remove(it)} disabled={busy === it.song_id}
                              title="remove" aria-label={`remove ${it.title || 'this song'}`}/>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {onOpenQueue && (
        <div className="mp-un-line">
          <a href="#music/queue" onClick={(e) => { if (e.preventDefault) e.preventDefault(); onOpenQueue(); }}>
            open {roomId}'s queue{all.length > rows.length ? ` · ${all.length - rows.length} more` : ''}
          </a>
        </div>
      )}
      <div className="mp-q-note" role="status" aria-live="polite">{note}</div>
    </section>
  );
};

/* "Save queue as playlist" — the library tracks in the queue, in order.
 * A secondary control under the queue's search: a button that opens a small
 * name prompt (2026-11-12; it used to take the place of the search box). */
const PlayerQueueSave = ({ queue, roomId = null }) => {
  const [open, setOpen] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [name, setName] = React.useState('');
  const [note, setNote] = React.useState('');
  const trackIds = queue.filter((it) => it.kind === 'library' && it.trackId != null).map((it) => it.trackId);
  const saveAsPlaylist = async () => {
    if ((!roomId && !trackIds.length) || !name.trim() || saving) return;
    setSaving(true); setNote('');
    try {
      if (roomId) {
        // While casting: the room's own queue, as the room holds it (P4).
        const r = await apiPost(roomQueuePath(roomId, '/save-playlist'),
          { name: name.trim(), device_id: DeviceIdentity.id() });
        setNote(roomSaveNote(r, name.trim()));
        setName(''); setSaving(false); setOpen(false);
        return;
      }
      const pl = await apiPost('/api/playlists', { name: name.trim() });
      for (const tid of trackIds) {
        try { await apiPost(`/api/playlists/${pl.id}/tracks`, { track_id: tid }); } catch {}
      }
      setNote(`Saved as "${name.trim()}".`);
      setName(''); setSaving(false); setOpen(false);
    } catch (e) {
      setSaving(false);
      console.warn('save queue failed', e);
      // A refused sign-in already has its own prompt on screen.
      if (!isAuthFailure(e)) setNote(`Couldn't save the playlist: ${apiErrorText(e)}`);
    }
  };
  if (!open) {
    return (
      <div className="mp-q-saverow">
        <Button icon="save" onClick={() => { setOpen(true); setNote(''); }}
                disabled={!roomId && !trackIds.length}
                title={roomId ? `save ${roomId}'s queue as a playlist`
                  : trackIds.length ? 'save this queue as a playlist' : 'no library songs in the queue to save'}>
          save as playlist
        </Button>
        {note && <span className="mp-q-note" role="status">{note}</span>}
      </div>
    );
  }
  return (
    <div className="mp-q-saverow" role="group" aria-label="save queue as playlist">
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="name your playlist…"
             aria-label="playlist name" autoFocus
             onKeyDown={(e) => {
               if (e.key === 'Enter') saveAsPlaylist();
               if (e.key === 'Escape') { if (e.stopPropagation) e.stopPropagation(); e.preventDefault(); setOpen(false); }
             }}/>
      <Button variant="primary" icon="save" onClick={saveAsPlaylist} disabled={saving || !name.trim()}>save</Button>
      <Button onClick={() => setOpen(false)}>cancel</Button>
      {note && <span className="mp-q-note" role="status">{note}</span>}
    </div>
  );
};

/* What the queue's search box asks, one picked at random each time the panel
 * opens (and kept while typing). Warm and light; no brands. */
const _QUEUE_ASK_PROMPTS = [
  "What's next? Name a song, artist or vibe…",
  'Feed me a song…',
  'Who should we hear from next?',
  'Something for the mood…',
  'Got a song stuck in your head?',
  'What are we in the mood for?',
  'Add a little something…',
  'Name a tune, an album, a feeling…',
  "Who's up next on the stage?",
  'Whisper a song title…',
  'Hungry for a genre? Say which…',
  'Keep it going: what else?',
  'An old favorite, or something new?',
  'Tell the house what to play next…',
  'Dig up a song, any song…',
];
const _queueAskPrompt = () => _QUEUE_ASK_PROMPTS[Math.floor(Math.random() * _QUEUE_ASK_PROMPTS.length)];
const _QUEUE_SEARCH_MIN = 2;
const _QUEUE_GENRE_TAKE = 25;   // songs a genre row adds, shuffled

/* Search the library and add what is found, to a queue: by title, artist
 * or album (GET /api/music/library?q=, the library search) and by genre
 * (GET /api/music/genres?q=; a genre row adds up to 25 of its songs,
 * shuffled). Only library songs. Combobox + listbox; Enter adds the
 * highlighted row, Shift+Enter plays it next, Up/Down move, Esc clears.
 *
 * Shared by the player's queue (PlayerQueueAdd, below) and the Music
 * page's Room queue tab (music.jsx QueueTab): each says where an add goes
 * through `onAdd(tracks, next, label)`, which resolves to the line to show
 * (or throws: "Couldn't add …", said unless a sign-in prompt has it).
 * `unavailable`: a sentence said instead of adding, when nothing can take
 * the songs. `idBase` keeps the listbox's ids apart when both are on
 * screen. `where` names the room in the buttons' titles. */
const QueueSearchAdd = ({ onAdd, unavailable = null, idBase = 'mp-q', where = null,
                          ariaLabel = 'search the library to add to the queue' }) => {
  const [prompt] = React.useState(_queueAskPrompt);
  const [q, setQ] = React.useState('');
  const dq = useDebouncedValue(q.trim(), 250);
  const [rows, setRows] = React.useState(null);        // null = nothing asked yet
  const [busy, setBusy] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const [status, setStatus] = React.useState('');
  const asking = dq.length >= _QUEUE_SEARCH_MIN && dq === q.trim();
  React.useEffect(() => {
    if (dq.length < _QUEUE_SEARCH_MIN) { setRows(null); setBusy(false); return undefined; }
    let live = true;
    setBusy(true);
    const enc = encodeURIComponent(dq);
    (async () => {
      try {
        const [songs, genres] = await Promise.all([
          apiGet(`/api/music/library?q=${enc}&limit=8`, { quiet: true }),
          apiGet(`/api/music/genres?q=${enc}&limit=2`, { quiet: true }).catch(() => null),
        ]);
        if (!live) return;
        const out = [];
        for (const t of ((songs && songs.items) || [])) out.push({ kind: 'song', key: `s${t.id}`, track: t });
        for (const g of ((genres && genres.items) || [])) {
          if (g && g.key) out.push({ kind: 'genre', key: `g${g.key}`, genre: g });
        }
        setRows(out); setActive(0); setStatus('');
      } catch (e) {
        if (!live) return;
        setRows([]);
        if (!isAuthFailure(e)) setStatus(`Couldn't search the library: ${apiErrorText(e)}`);
      } finally { if (live) setBusy(false); }
    })();
    return () => { live = false; };
  }, [dq]);

  const tracksOf = async (row) => {
    if (row.kind === 'song') return [row.track];
    const r = await apiGet(`/api/music/library?genre=${encodeURIComponent(row.genre.key)}&limit=200`);
    const all = ((r && r.items) || []).slice();
    for (let i = all.length - 1; i > 0; i--) {      // shuffled: a genre is a mood, not an order
      const j = Math.floor(Math.random() * (i + 1));
      [all[i], all[j]] = [all[j], all[i]];
    }
    return all.slice(0, _QUEUE_GENRE_TAKE);
  };
  const label = (row) => (row.kind === 'song' ? (row.track.title || 'that song') : `${row.genre.name} songs`);
  const add = async (row, next) => {
    if (unavailable) { setStatus(unavailable); return; }
    try {
      const tracks = await tracksOf(row);
      if (!tracks.length) { setStatus(`Nothing to add from ${label(row)}.`); return; }
      const said = await onAdd(tracks, next, label(row));
      setStatus(said || '');
    } catch (e) {
      if (!isAuthFailure(e)) setStatus(`Couldn't add ${label(row)}: ${apiErrorText(e)}`);
    }
  };
  const list = rows || [];
  const onKey = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!list.length) return;
      e.preventDefault();
      setActive((a) => (e.key === 'ArrowDown' ? (a + 1) % list.length : (a - 1 + list.length) % list.length));
    } else if (e.key === 'Enter') {
      const row = list[active];
      if (row) { e.preventDefault(); add(row, !!e.shiftKey); }
    } else if (e.key === 'Escape' && q) {
      if (e.stopPropagation) e.stopPropagation();
      e.preventDefault();
      setQ(''); setStatus('');
    }
  };
  const showList = rows !== null && asking;
  const listId = `${idBase}-results`;
  return (
    <>
      <input type="search" className="mp-q-search" value={q} placeholder={prompt}
             onChange={(e) => { setQ(e.target.value); setStatus(''); }} onKeyDown={onKey}
             role="combobox" aria-label={ariaLabel}
             aria-autocomplete="list" aria-expanded={showList && list.length > 0}
             aria-controls={listId}
             aria-activedescendant={showList && list[active] ? `${idBase}-opt-${active}` : undefined}
             autoComplete="off"/>
      {showList && (
        <div className="mp-q-results" id={listId} role="listbox" aria-label="songs to add">
          {list.map((row, i) => (
            <div key={row.key} id={`${idBase}-opt-${i}`} role="option" aria-selected={i === active}
                 className={`mp-q-res${i === active ? ' on' : ''}`}
                 onMouseEnter={() => setActive(i)}>
              <div className="mp-q-res-main" onClick={() => add(row, false)}>
                {row.kind === 'song' ? (
                  <>
                    <div className="mp-q-title">{row.track.title || 'unknown'}{row.track.artist ? ` · ${row.track.artist}` : ''}</div>
                    <div className="mp-q-sub mono">{row.track.album || '—'}</div>
                  </>
                ) : (
                  <>
                    <div className="mp-q-title">{row.genre.name}</div>
                    <div className="mp-q-sub mono">genre · {row.genre.track_count} song{row.genre.track_count === 1 ? '' : 's'}, shuffled</div>
                  </>
                )}
              </div>
              <IconButton name="corner-down-right" tabIndex={-1} onClick={() => add(row, true)}
                          title={where ? `play next in ${where}` : 'play next'}
                          aria-label={`play ${label(row)} next`}/>
              <IconButton name="list-plus" tabIndex={-1} onClick={() => add(row, false)}
                          title={where ? `add to ${where}'s queue` : 'add to queue'}
                          aria-label={`add ${label(row)} to the queue`}/>
            </div>
          ))}
          {list.length === 0 && !busy && (
            <div className="mp-q-empty" role="status">Nothing matches "{dq}". Try an artist, an album or a genre.</div>
          )}
        </div>
      )}
      <div className="mp-q-note" role="status" aria-live="polite">
        {status || (busy && asking ? 'searching…' : '')}
      </div>
    </>
  );
};

/* The player's queue search (QueueSearchAdd). While casting, the panel
 * still shows the queue this browser keeps for the room, and an add goes
 * to both it and the room's own queue (POST /api/music/queue/{room}/add):
 * "add to queue" lands after what was asked for and before the room's
 * Up next picks, "play next" right after the song playing there
 * (`where`, P4). */
const PlayerQueueAdd = ({ p }) => {
  const casting = !!(p.target && p.target.kind === 'room');
  const onAdd = async (tracks, next, label) => {
    const items = tracks.map(itemFromTrack);
    if (next) {
      for (let i = items.length - 1; i >= 0; i--) p.playNext(items[i]);
    } else {
      p.enqueue(items);
    }
    if (casting) {
      try {
        await apiPost(roomQueuePath(p.target.roomId, '/add'),
          { track_ids: tracks.map((t) => t.id), device_id: DeviceIdentity.id(), where: next ? 'next' : 'end' });
      } catch (e) {
        if (isAuthFailure(e)) return '';
        return `Added "${label}" here, but ${p.target.roomId} didn't take it: ${apiErrorText(e)}`;
      }
    }
    const n = items.length;
    const where = casting ? ` in ${p.target.roomId}` : '';
    return `${next ? 'Playing next' : 'Added'}${where}: ${label}${n > 1 ? ` (${n} songs)` : ''}`;
  };
  return (
    <div className="mp-q-save">
      <QueueSearchAdd onAdd={onAdd} where={casting ? p.target.roomId : null}
                      unavailable={p.available ? null : "The browser player isn't available here."}/>
      <PlayerQueueSave queue={p.queue} roomId={casting ? p.target.roomId : null}/>
    </div>
  );
};

/* Queue panel — the desktop bar's queue, floating above it. */
const QueuePanel = ({ p, onClose }) => (
  <>
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 46 }}/>
    <div style={{ position: 'fixed', right: 14, bottom: 'calc(var(--dock-bottom, 0px) + 76px)', width: 380, maxHeight: '60vh', zIndex: 47,
                  background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                  boxShadow: 'var(--shadow-md)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>queue · {playerQueueCount(p.queue, p.index)}</div>
        <div style={{ display: 'flex', gap: 4 }}>
          <IconButton name="trash-2" onClick={p.clearQueue} title="clear queue"/>
          <IconButton name="x" onClick={onClose}/>
        </div>
      </div>
      <div className="mp-q-list" style={{ overflowY: 'auto', flex: 1 }}>
        <PlayerQueueRows queue={p.queue} index={p.index}
                         jumpTo={p.jumpTo} removeAt={p.removeAt} moveItem={p.moveItem}/>
        <PlayerUpNext p={p}/>
      </div>
      <PlayerQueueAdd p={p}/>
    </div>
  </>
);

/* A cast a room didn't take, in words for the person. The core names the
 * part that failed (`failed` in the error body, 2026-10-01): the room's
 * music player, which runs on the domovoi server, or — no satellite has
 * ever connected — the satellite. Every failure used to read "its speaker
 * isn't reachable (is the office satellite online?)", also when office's
 * satellite was connected and the server's music player had frozen (ft).
 * The app says the same (CastPlanner.failureNote). A refusal
 * (`castRefused`) already says why, as itself. */
const _castFailureNote = (e, roomId) => {
  if (e && e.castRefused) return String(e.message || e);
  const status = (e && e.status) || 0;
  const body = e && e.detail && typeof e.detail === 'object' ? e.detail : null;
  const part = body ? body.failed : null;
  const head = `Couldn't cast to ${roomId}`;
  if (part === 'music_player') {
    return `${head} — its music player on the domovoi server isn't answering. ` +
      'Try again in a minute; if it keeps failing, restart the domovoi.';
  }
  if (part === 'satellite') {
    return `${head} — no room has connected to the domovoi yet, so there is no speaker to play on.`;
  }
  if (e && e.deviceTokenRequired) return `${head} — this browser needs pairing with the domovoi again.`;
  if (status === 404) return `${head} — none of these songs are in the library now (try a library rescan).`;
  if (status >= 500) return `${head} — the domovoi couldn't start it (it said ${status}).`;
  if (status) return `${head} (the domovoi said ${status}).`;
  return `${head} — the domovoi can't be reached (offline?).`;
};

/* Where the player plays — this browser, or a room. The desktop bar's
 * CastMenu floats it above the bar; the phone sheet lists it inline with
 * 48px rows (`big`). `onPicked` runs after a switch succeeds. */
const PlayerCastTargets = ({ p, onPicked, big = false }) => {
  const { items: nowPlaying, loading } = useApiList('/api/music/now-playing', { eventTypes: ['music.now_playing.changed'] });
  const rooms = (nowPlaying || []).map((np) => np.room_id);
  const [busy, setBusy] = React.useState(null);   // roomId currently being cast to
  const [err, setErr] = React.useState(null);
  const pick = async (t) => {
    setErr(null);
    setBusy(t.kind === 'room' ? t.roomId : 'browser');
    try {
      const r = await p.castTo(t);
      // The room this left would not pause: it may still be playing, so say
      // so (and stay open) instead of closing as if all went well.
      if (r && r.left && !r.leftPaused) {
        setErr(`Couldn't pause ${r.left} — it may still be playing.`);
        return;
      }
      // A "play here" beat this pick. The room took the queue before it did
      // and wouldn't pause again: it may be playing.
      if (r && r.kind === 'superseded' && r.sent && !r.undone) {
        setErr(`Played here instead, but ${r.roomId} couldn't be paused — it may be playing.`);
        return;
      }
      if (onPicked) onPicked();
    }
    catch (e) {
      // Surface the failure instead of swallowing it — a silent catch made
      // the button look dead. In words (_castFailureNote), never the
      // server's reply.
      console.warn('cast failed', e);
      const msg = String((e && e.message) || e);
      setErr(t.kind === 'room' ? _castFailureNote(e, t.roomId)
        : `Couldn't switch playback here: ${msg.slice(0, 80)}`);
    } finally {
      setBusy(null);
    }
  };
  const remote = p.target.kind === 'room';
  return (
    <>
      <button onClick={() => pick({ kind: 'browser' })}
              style={_castRow(!remote, big)}>
        <Icon name="monitor" size={15}/> This browser {!remote && <Icon name="check" size={14}/>}
      </button>
      {rooms.map((r) => (
        <button key={r} onClick={() => pick({ kind: 'room', roomId: r })} disabled={busy != null}
                style={_castRow(remote && p.target.roomId === r, big)}>
          <Icon name={busy === r ? 'loader' : 'speaker'} size={15}/> {r}
          {busy === r && <span style={{ fontSize: 11, color: 'var(--fg-muted)' }}>casting…</span>}
          {remote && p.target.roomId === r && <Icon name="check" size={14}/>}
        </button>
      ))}
      {/* The rooms read takes a moment (it asks every room's MPD), and
          the phone sheet mounts this list afresh on every open: until
          it answers, say so rather than "no rooms online". */}
      {rooms.length === 0 && (
        <div style={{ padding: 12, fontSize: 11, color: 'var(--fg-muted)' }}>
          {loading ? 'looking for rooms…' : 'no rooms online — set up a room'}
        </div>
      )}
      {err && <div style={{ padding: '10px 14px', fontSize: 11, color: 'var(--err-ink)', borderTop: '1px solid var(--border-soft)' }}>{err}</div>}
    </>
  );
};

/* Cast menu — the desktop bar's playback target, floating above it. */
const CastMenu = ({ p, onClose }) => (
  <>
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 46 }}/>
    <div style={{ position: 'fixed', right: 14, bottom: 'calc(var(--dock-bottom, 0px) + 76px)', width: 260, zIndex: 47,
                  background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                  boxShadow: 'var(--shadow-md)', overflow: 'hidden' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 13, fontWeight: 600 }}>play on</div>
      <PlayerCastTargets p={p} onPicked={onClose}/>
    </div>
  </>
);
const _castRow = (active, big = false) => ({
  font: 'inherit', width: '100%', display: 'flex', alignItems: 'center', gap: 10,
  padding: big ? '0 14px' : '10px 14px', minHeight: big ? 48 : undefined,
  border: 'none', borderBottom: '1px solid var(--border-soft)',
  background: active ? 'var(--brand-soft)' : 'var(--card)', color: 'var(--fg)',
  cursor: 'pointer', textAlign: 'left', fontSize: 13,
});

/* expose to other Babel scripts */
Object.assign(window, {
  PlaybackProvider, MiniPlayer, PlaybackContext, usePlayback, openPlayerSheet, playerSheetTab,
  itemFromTrack, itemFromSource, itemFromEpisode, itemFromBook, itemsFromBook,
  resolveSourceStreamUrl,
  playerUrls, OfflineCache, SpokenAudio,
  CoverTile, EQ_BANDS, EQ_LABELS, EQ_MIN_DB, EQ_MAX_DB,
  ListenLog, PlayerUpNext, playerQueueCount,
  // A room's queue and its Up next (P4): music.jsx, satellites.jsx.
  RoomUpNext, PlayerQueueSave, upNextListenerFields,
});
