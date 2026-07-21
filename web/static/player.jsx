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

/* Client-side "recent" list — history is intentionally ephemeral (no
 * server-side play logging for browser playback). Kept in localStorage so
 * the offline auto-cache has something to prioritise. */
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
  const CLIENT_KEY = 'domovoi-client-id';
  const PERSON_KEY = 'domovoi-listener-person';

  const clientId = () => {
    let id = null;
    try { id = localStorage.getItem(CLIENT_KEY); } catch {}
    if (!id) {
      id = 'browser-' + Math.random().toString(36).slice(2, 12);
      try { localStorage.setItem(CLIENT_KEY, id); } catch {}
    }
    return id;
  };
  const getPerson = () => {
    try { const v = localStorage.getItem(PERSON_KEY); return v ? Number(v) : null; }
    catch { return null; }
  };
  const setPerson = (pid) => {
    try {
      if (pid == null) localStorage.removeItem(PERSON_KEY);
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
  const savePosition = async (item, positionSec, speed) => {
    const u = posUrl(item);
    if (!u) return;
    const body = { device_id: clientId(), position_sec: Math.round(positionSec) };
    const pid = getPerson();
    if (pid != null) body.person_id = pid;
    if (speed != null) body.speed = speed;
    try { await apiPost(u.post, body); } catch {}
  };

  return { clientId, getPerson, setPerson, fetchPosition, savePosition };
})();

/* Build a generic queue item from a downloaded podcast episode row. */
const itemFromEpisode = (ep, showTitle, artwork) => ({
  uid: `pod-${ep.id}-${Math.random().toString(36).slice(2, 7)}`,
  kind: 'podcast',
  itemId: ep.id,
  trackId: null,
  title: ep.title || 'episode',
  artist: showTitle || 'podcast',
  album: showTitle || '',
  src: absMedia(`/api/podcasts/episodes/${ep.id}/audio`),
  coverUrl: absMedia(artwork) || null,
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
  coverUrl: absMedia(book.artwork) || null,
  durationSec: book.duration_sec ?? null,
  seekable: true,
  cacheable: true,
  chapters: book.chapters || null,
  meta: { itemType: 'audiobook' },
});

/* ═══════════════════════════════════════════════════════════════════════
 * Playback context + provider.
 * ═══════════════════════════════════════════════════════════════════════ */

/* Safe default so pages that call usePlayback() before the provider is
 * mounted (e.g. during the pre-integration window, or a page rendered
 * outside the shell in a test) don't crash — they see `available:false`
 * and hide the browser-player affordances. */
const _noop = () => {};
const PlaybackContext = React.createContext({
  available: false,
  queue: [], index: -1, current: null, status: 'stopped',
  positionSec: 0, durationSec: 0, buffered: 0,
  volume: 1, muted: false,
  eqBands: EQ_BANDS.map(() => 0), eqEnabled: false,
  playbackRate: 1,
  target: { kind: 'browser' },
  sleepRemainingSec: null,
  recent: [],
  listenerPersonId: null, setListenerPersonId: _noop,
  spoken: SpokenAudio,
  playSpoken: _noop, jumpToChapter: _noop,
  itemFromEpisode: (x) => x, itemFromBook: (x) => x,
  playItems: _noop, enqueue: _noop, playNext: _noop,
  removeAt: _noop, moveItem: _noop, clearQueue: _noop, jumpTo: _noop,
  toggle: _noop, play: _noop, pause: _noop, stop: _noop,
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

const PlaybackProvider = ({ children }) => {
  // ── Queue + current item ───────────────────────────────────────────
  const [queue, setQueue] = React.useState([]);
  const [index, setIndex] = React.useState(-1);
  const [status, setStatus] = React.useState('stopped'); // playing|paused|loading|stopped
  const [positionSec, setPositionSec] = React.useState(0);
  const [durationSec, setDurationSec] = React.useState(0);
  const [buffered, setBuffered] = React.useState(0);
  const [volume, setVolumeState] = React.useState(() => {
    const v = Number(localStorage.getItem('domovoi-player-volume'));
    return Number.isFinite(v) && v >= 0 && v <= 1 ? v : 1;
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
  const [sleepRemainingSec, setSleepRemainingSec] = React.useState(null);
  const [recent, setRecent] = React.useState(() => {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; }
  });
  // "Listening as [person]" — browser has no voice ID, so spoken-audio resume
  // positions key off this (defaults to the dashboard user = null/anon).
  const [listenerPersonId, setListenerPersonIdState] = React.useState(() => SpokenAudio.getPerson());
  const setListenerPersonId = React.useCallback((pid) => {
    SpokenAudio.setPerson(pid); setListenerPersonIdState(pid);
  }, []);

  const current = index >= 0 && index < queue.length ? queue[index] : null;

  // ── Web Audio graph (lazily built on first play — needs a user gesture) ──
  const graphRef = React.useRef(null);   // { ctx, els:[A,B], gains, eq, analyser, master, active }
  const crossfadeSec = DEFAULT_CROSSFADE_SEC;
  const rafRef = React.useRef(0);
  const sleepTimerRef = React.useRef(null);
  const sleepEndOfTrackRef = React.useRef(false);
  const remotePollRef = React.useRef(null);
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

  /* ═══ Public actions ═══════════════════════════════════════════════ */
  const playItems = React.useCallback((items, startIndex = 0) => {
    if (!items || !items.length) return;
    setQueue(items);
    setIndex(startIndex);
    if (target.kind === 'browser') {
      loadAndPlay(items[startIndex]);
    } else {
      // Remote: re-cast the new queue.
      castRoomLoad(target.roomId, items);
    }
  }, [loadAndPlay, target]);

  /* Play a single spoken-audio item (podcast episode / audiobook), resuming
   * from a saved position + speed. The page fetches the position first and
   * passes it here; we set the queue, seek on load, and apply the remembered
   * playbackRate. Casting to a room hands off to the satellite (which resumes
   * from its own per-(device×person) position via the SpokenAudioHandler). */
  const playSpoken = React.useCallback((item, { resumeSec = 0, speed = 1 } = {}) => {
    setQueue([item]);
    setIndex(0);
    if (speed && speed !== playbackRate) setPlaybackRateState(Math.max(0.5, Math.min(3, speed)));
    if (target.kind === 'browser') {
      loadAndPlay(item, { resumeSec });
    } else {
      // Remote: the satellite resumes from its own saved position; just cast
      // the (library-castable) queue isn't possible for spoken audio yet, so
      // fall back to browser playback for now.
      loadAndPlay(item, { resumeSec });
    }
  }, [loadAndPlay, target, playbackRate]);

  const enqueue = React.useCallback((items) => {
    const add = Array.isArray(items) ? items : [items];
    setQueue((q) => {
      const nq = [...q, ...add];
      // If nothing is playing, start at the first appended item.
      if (index < 0 && target.kind === 'browser') {
        setIndex(q.length);
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
    setQueue((q) => {
      const nq = q.filter((_, idx) => idx !== i);
      setIndex((cur) => {
        if (i < cur) return cur - 1;
        if (i === cur) {
          // Removing the current item — advance to what slid into its slot.
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

  const clearQueue = React.useCallback(() => { stop(); setQueue([]); setIndex(-1); }, []);

  const jumpTo = React.useCallback((i) => {
    if (i < 0 || i >= queue.length) return;
    setIndex(i);
    if (target.kind === 'browser') loadAndPlay(queue[i]);
    else castRoomJump(target.roomId, i);
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
    setStatus('stopped');
    setPositionSec(0);
  }
  const toggle = () => { (status === 'playing') ? pause() : play(); };

  const next = React.useCallback(() => {
    if (target.kind === 'room') { apiPost(`/api/music/skip/${target.roomId}`).catch(() => {}); return; }
    const ni = index + 1;
    if (ni < queue.length) advanceTo(ni, { crossfade: false });
    else stop();
  }, [index, queue, advanceTo, target]);

  const prev = React.useCallback(() => {
    if (target.kind === 'room') { apiPost(`/api/music/skip/${target.roomId}`).catch(() => {}); return; }
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
      const artwork = item.coverUrl
        ? [{ src: item.coverUrl, sizes: '512x512', type: 'image/jpeg' }] : [];
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
  const castRoomLoad = React.useCallback(async (roomId, items) => {
    const trackIds = (items || []).filter((it) => it.kind === 'library' && it.trackId != null)
      .map((it) => it.trackId);
    if (!trackIds.length) throw new Error('only library tracks can be cast to a room');
    await apiPost('/api/music/play-tracks', { room_id: roomId, track_ids: trackIds });
  }, []);
  const castRoomJump = React.useCallback(async (roomId, i) => {
    // Re-cast from the chosen index onward so "play this queue item" works remotely.
    const items = queueRef.current.slice(i);
    await castRoomLoad(roomId, items);
  }, [castRoomLoad]);

  const castTo = React.useCallback(async (nextTarget) => {
    // browser → room: stop local, load queue into room, enter remote mode.
    if (nextTarget.kind === 'room') {
      const g = graphRef.current;
      const resumeAt = index;
      if (g) g.els.forEach((el) => { try { el.pause(); } catch {} });
      try {
        await castRoomLoad(nextTarget.roomId, queue.slice(Math.max(0, index)));
        setTarget(nextTarget);
        setStatus('playing');
      } catch (e) {
        console.warn('cast to room failed', e);
        throw e;
      }
    } else {
      // room → browser: resume local from the current queue position.
      if (target.kind === 'room' && target.roomId) {
        apiPost(`/api/music/pause/${target.roomId}`).catch(() => {});
      }
      setTarget({ kind: 'browser' });
      if (current) loadAndPlay(current, { resumeSec: 0 });
    }
  }, [queue, index, current, castRoomLoad, loadAndPlay, target]);

  // Remote-mode poller: mirror the room's now-playing into our state.
  React.useEffect(() => {
    if (target.kind !== 'room') {
      if (remotePollRef.current) { clearInterval(remotePollRef.current); remotePollRef.current = null; }
      return;
    }
    const poll = async () => {
      try {
        const all = await apiGet('/api/music/now-playing');
        const np = (all || []).find((r) => r.room_id === target.roomId);
        if (np) {
          setStatus(np.state === 'play' ? 'playing' : np.state === 'pause' ? 'paused' : 'stopped');
          setPositionSec(np.elapsed_sec || 0);
          setDurationSec(np.song?.duration_sec || 0);
        }
      } catch {}
    };
    poll();
    remotePollRef.current = setInterval(poll, 2000);
    return () => { if (remotePollRef.current) { clearInterval(remotePollRef.current); remotePollRef.current = null; } };
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
    target, sleepRemainingSec, recent,
    playItems, enqueue, playNext, removeAt, moveItem, clearQueue, jumpTo,
    toggle, play, pause, stop, next, prev, seek, seekBy,
    setVolume, toggleMute,
    setEqBand, resetEq, setEqEnabled, setPlaybackRate,
    setSleep, cancelSleep,
    castTo, getAnalyser,
    offline: OfflineCache,
    // Spoken audio (podcasts / audiobooks).
    playSpoken, jumpToChapter, spoken: SpokenAudio,
    listenerPersonId, setListenerPersonId,
    // Helper builders exposed so pages don't re-import them.
    itemFromTrack, itemFromSource, itemFromEpisode, itemFromBook,
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
 * ═══════════════════════════════════════════════════════════════════════ */
const MiniPlayer = () => {
  const p = usePlayback();
  const [showQueue, setShowQueue] = React.useState(false);
  const [showCast, setShowCast] = React.useState(false);
  if (!p.available || !p.current) return null;
  const it = p.current;
  const dur = p.durationSec || it.durationSec || 0;
  const pct = dur > 0 ? Math.min(100, (p.positionSec / dur) * 100) : 0;
  const remote = p.target.kind === 'room';

  return (
    <>
      {showQueue && <QueuePanel p={p} onClose={() => setShowQueue(false)}/>}
      {showCast && <CastMenu p={p} onClose={() => setShowCast(false)}/>}
      <div style={{
        position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 45,
        background: 'var(--card)', borderTop: '1px solid var(--border)',
        boxShadow: '0 -4px 16px oklch(0 0 0 / 0.10)',
        display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center',
        gap: 12, padding: '8px 14px', height: 64,
      }}>
        {/* left: cover + title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <CoverTile item={it} size={44}/>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.title}</div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {remote && <span style={{ color: 'var(--brand)' }}>◆ {p.target.roomId} · </span>}
              {it.artist || (it.seekable === false ? 'live stream' : '—')}
            </div>
          </div>
        </div>
        {/* center: transport + progress */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 280 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <IconButton name="skip-back" onClick={p.prev}/>
            <button className="btn btn-primary btn-icon" onClick={p.toggle}
                    style={{ width: 34, height: 34, borderRadius: '50%' }}>
              <Icon name={p.status === 'playing' ? 'pause' : 'play'} size={16}/>
            </button>
            <IconButton name="skip-forward" onClick={p.next}/>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%' }}>
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
        {/* right: volume + queue + cast + expand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end' }}>
          {p.sleepRemainingSec != null && (
            <span className="mono" title="sleep timer" style={{ fontSize: 10, color: 'var(--brand)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
              <Icon name="moon" size={11}/>{p.sleepRemainingSec < 0 ? 'end' : fmtDur(p.sleepRemainingSec)}
            </span>
          )}
          {!remote && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <IconButton name={p.muted || p.volume === 0 ? 'volume-x' : 'volume-2'} onClick={p.toggleMute}/>
              <input type="range" min={0} max={1} step={0.01} value={p.muted ? 0 : p.volume}
                     onChange={(e) => p.setVolume(Number(e.target.value))}
                     style={{ width: 70 }}/>
            </div>
          )}
          <IconButton name="list-music" onClick={() => setShowQueue((s) => !s)} title="queue"/>
          <IconButton name={remote ? 'cast' : 'monitor-speaker'} onClick={() => setShowCast((s) => !s)}
                      title="cast target"
                      style={remote ? { color: 'var(--brand)' } : undefined}/>
          <IconButton name="chevron-up" onClick={() => { window.location.hash = 'music'; }} title="open full player"/>
        </div>
      </div>
    </>
  );
};

/* Cover tile — real embedded art with graceful fallback to a gradient
 * tile keyed by title (the emoji/color-equivalent placeholder). */
const CoverTile = ({ item, size = 44, radius = 'var(--r-sm)' }) => {
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => { setFailed(false); }, [item && item.coverUrl]);
  const grad = 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))';
  if (item && item.coverUrl && !failed) {
    return <img src={item.coverUrl} alt="" onError={() => setFailed(true)}
                style={{ width: size, height: size, borderRadius: radius, objectFit: 'cover', border: '1px solid var(--border)', flexShrink: 0 }}/>;
  }
  return (
    <div style={{ width: size, height: size, borderRadius: radius, border: '1px solid var(--border)',
                  background: item && item.seekable === false ? 'var(--sunken)' : grad, flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Icon name={item && item.seekable === false ? 'radio' : 'music'} size={size * 0.4}
            className="" />
    </div>
  );
};

/* Queue panel — shows the queue with drag-reorder, remove, jump-to, and
 * "save as playlist". Floats above the mini-player. */
const QueuePanel = ({ p, onClose }) => {
  const dragFrom = React.useRef(null);
  const [saving, setSaving] = React.useState(false);
  const [name, setName] = React.useState('');
  const saveAsPlaylist = async () => {
    const trackIds = p.queue.filter((it) => it.kind === 'library' && it.trackId != null).map((it) => it.trackId);
    if (!trackIds.length || !name.trim()) return;
    setSaving(true);
    try {
      const pl = await apiPost('/api/playlists', { name: name.trim() });
      for (const tid of trackIds) {
        try { await apiPost(`/api/playlists/${pl.id}/tracks`, { track_id: tid }); } catch {}
      }
      setName(''); setSaving(false);
    } catch (e) { setSaving(false); console.warn('save queue failed', e); }
  };
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 46 }}/>
      <div style={{ position: 'fixed', right: 14, bottom: 76, width: 380, maxHeight: '60vh', zIndex: 47,
                    background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                    boxShadow: 'var(--shadow-md)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>queue · {p.queue.length}</div>
          <div style={{ display: 'flex', gap: 4 }}>
            <IconButton name="trash-2" onClick={p.clearQueue} title="clear queue"/>
            <IconButton name="x" onClick={onClose}/>
          </div>
        </div>
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {p.queue.map((it, i) => (
            <div key={it.uid}
                 draggable onDragStart={() => { dragFrom.current = i; }}
                 onDragOver={(e) => e.preventDefault()}
                 onDrop={() => { const f = dragFrom.current; dragFrom.current = null; if (f != null && f !== i) p.moveItem(f, i); }}
                 style={{ display: 'grid', gridTemplateColumns: '20px 1fr 22px', gap: 8, alignItems: 'center',
                          padding: '7px 12px', cursor: 'grab',
                          background: i === p.index ? 'var(--brand-soft)' : 'transparent',
                          borderBottom: '1px solid var(--border-soft)' }}>
              <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', textAlign: 'right' }}>
                {i === p.index ? <Icon name="volume-2" size={12}/> : i + 1}
              </div>
              <div onClick={() => p.jumpTo(i)} style={{ minWidth: 0, cursor: 'pointer' }}>
                <div style={{ fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.title}</div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.artist || '—'}</div>
              </div>
              <IconButton name="x" onClick={() => p.removeAt(i)}/>
            </div>
          ))}
          {p.queue.length === 0 && <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>queue is empty</div>}
        </div>
        <div style={{ padding: 10, borderTop: '1px solid var(--border)', display: 'flex', gap: 6 }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="save queue as playlist…"
                 onKeyDown={(e) => { if (e.key === 'Enter') saveAsPlaylist(); }}
                 style={{ flex: 1, font: 'inherit', fontSize: 12, height: 30, padding: '0 10px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}/>
          <Button variant="primary" icon="save" onClick={saveAsPlaylist} disabled={saving || !name.trim()}>save</Button>
        </div>
      </div>
    </>
  );
};

/* Cast menu — pick the playback target (this browser, or a room). */
const CastMenu = ({ p, onClose }) => {
  const { items: nowPlaying } = useApiList('/api/music/now-playing', { eventTypes: ['music.now_playing.changed'] });
  const rooms = (nowPlaying || []).map((np) => np.room_id);
  const [busy, setBusy] = React.useState(null);   // roomId currently being cast to
  const [err, setErr] = React.useState(null);
  const pick = async (t) => {
    setErr(null);
    setBusy(t.kind === 'room' ? t.roomId : 'browser');
    try { await p.castTo(t); onClose(); }
    catch (e) {
      // Surface the failure instead of swallowing it — a cast to a room whose
      // MPD instance isn't up returns 502 (domovoi: WinError 1225,
      // connection refused), and a silent catch made the button look dead.
      console.warn('cast failed', e);
      const msg = String((e && e.message) || e);
      setErr(t.kind === 'room'
        ? `Couldn't cast to ${t.roomId} — its speaker isn't reachable ` +
          `(is the ${t.roomId} satellite online?).`
        : `Couldn't switch playback here: ${msg.slice(0, 80)}`);
    } finally {
      setBusy(null);
    }
  };
  const remote = p.target.kind === 'room';
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 46 }}/>
      <div style={{ position: 'fixed', right: 14, bottom: 76, width: 260, zIndex: 47,
                    background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                    boxShadow: 'var(--shadow-md)', overflow: 'hidden' }}>
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 13, fontWeight: 600 }}>play on</div>
        <button onClick={() => pick({ kind: 'browser' })}
                style={_castRow(!remote)}>
          <Icon name="monitor" size={15}/> This browser {!remote && <Icon name="check" size={14}/>}
        </button>
        {rooms.map((r) => (
          <button key={r} onClick={() => pick({ kind: 'room', roomId: r })} disabled={busy != null}
                  style={_castRow(remote && p.target.roomId === r)}>
            <Icon name={busy === r ? 'loader' : 'speaker'} size={15}/> {r}
            {busy === r && <span style={{ fontSize: 11, color: 'var(--fg-muted)' }}>casting…</span>}
            {remote && p.target.roomId === r && <Icon name="check" size={14}/>}
          </button>
        ))}
        {rooms.length === 0 && <div style={{ padding: 12, fontSize: 11, color: 'var(--fg-muted)' }}>no rooms online — connect a satellite</div>}
        {err && <div style={{ padding: '10px 14px', fontSize: 11, color: 'var(--err)', borderTop: '1px solid var(--border-soft)' }}>{err}</div>}
      </div>
    </>
  );
};
const _castRow = (active) => ({
  font: 'inherit', width: '100%', display: 'flex', alignItems: 'center', gap: 10,
  padding: '10px 14px', border: 'none', borderBottom: '1px solid var(--border-soft)',
  background: active ? 'var(--brand-soft)' : 'var(--card)', color: 'var(--fg)',
  cursor: 'pointer', textAlign: 'left', fontSize: 13,
});

/* expose to other Babel scripts */
Object.assign(window, {
  PlaybackProvider, MiniPlayer, PlaybackContext, usePlayback,
  itemFromTrack, itemFromSource, itemFromEpisode, itemFromBook,
  resolveSourceStreamUrl,
  playerUrls, OfflineCache, SpokenAudio,
  CoverTile, EQ_BANDS, EQ_LABELS, EQ_MIN_DB, EQ_MAX_DB,
});
