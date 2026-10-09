/* Lyrics — the synced lyrics view (lyrics contract §12).
 *
 * What a player shows for the song it plays: the timed lines, the current
 * one highlighted, when the song has timed lyrics (a .lrc beside it, the
 * file's own tags, or LRCLIB's), and the words as plain text otherwise. It
 * follows THIS browser's playback (the player's own clock) and a ROOM's
 * (the room's now-playing elapsed time, carried forward on animation frames
 * between the player's 2 s polls, less a per-room timing nudge for the
 * speaker's stream buffer).
 *
 * Where it shows — each surface asks lyricsTrackIdFor(p) whether there is
 * a library song to show lyrics for (never for radio, podcasts, audiobooks
 * or plugin streams):
 *   * the Music page's Player tab (music_player_panel.jsx), open by default;
 *   * the phone's player sheet (player.jsx PlayerSheet), closed by default;
 *   * the desktop docked bar's "lyrics" button (player.jsx MiniPlayer) —
 *     LyricsFloat, floating above the bar like the queue;
 *   * each room card on the Music page (music.jsx NPCard) — LyricsRoomLine,
 *     the room's current timed line;
 *   * the Jobs tab (music.jsx JobsTab) — LyricsJobsCard, how far the house
 *     has got (counts only).
 *
 * The household tier only. The server answers the lyrics reads for a
 * paired device, an admin or the dashboard cookie and refuses anyone else;
 * after a refusal every surface here simply disappears for that viewer
 * (until the credential changes — a pairing, a sign-in). Lyrics never
 * appear on the kiosk display, on Home, in a toast, in the document title
 * or in the console: nothing in this file logs.
 *
 * One global scope with every other dashboard script: every top-level name
 * here starts with Lyrics, lyrics or _lyr (test_web_script_scope.py,
 * dupglobals.js), and there is no object rest/spread (the in-browser
 * compiler would emit a top-level `const _excluded`, already declared by
 * components.jsx). Hooks through `React.` (the shared-scope rule — see
 * player.jsx).
 */

/* ── constants ──────────────────────────────────────────────────────── */
const _lyrLeadMs = 150;            // a line shows this much early
const _lyrCacheMax = 30;           // docs kept, least recently used out
const _lyrCheckingTtlMs = 60000;   // a "still looking" doc is asked again after this
const _lyrChangedGapMs = 15000;    // at most one re-read per song per lyrics.changed burst
const _lyrHoldMs = 4000;           // a hand on the list stops following for this long
const _lyrNudgeStepMs = 250;
const _lyrNudgeMaxMs = 10000;
const _lyrScrollKeys = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' ']);

/* ── clocks ─────────────────────────────────────────────────────────── */
const _lyrNow = () => Date.now();
// The clock player.jsx stamps a room reading with (remoteNp.readAt).
const _lyrPerfNow = () => (typeof performance !== 'undefined' && performance
  && typeof performance.now === 'function' ? performance.now() : Date.now());

/* ── the doc store: one cache for every surface ─────────────────────── */
const _lyrStore = {
  entries: new Map(),     // String(trackId) → { state: 'ready'|'error', doc, at }
  inflight: new Map(),    // String(trackId) → the read in flight
  changedAt: new Map(),   // String(trackId) → when lyrics.changed last re-read it
  listeners: new Set(),
  denied: null,           // { cred }: refused (401/403) under this credential
  nudges: new Map(),      // roomId → ms, mirrored to localStorage
};

const _lyrCredential = () => {
  try { return typeof Auth !== 'undefined' && Auth ? Auth.credentialVersion : undefined; }
  catch { return undefined; }
};
const _lyrIsDenied = () => !!_lyrStore.denied && _lyrStore.denied.cred === _lyrCredential();

const _lyrNotify = () => {
  for (const fn of Array.from(_lyrStore.listeners)) {
    try { fn(); } catch { /* one surface's trouble is not the others' */ }
  }
};
const _lyrSubscribe = (fn) => {
  _lyrStore.listeners.add(fn);
  return () => { _lyrStore.listeners.delete(fn); };
};

const _lyrKey = (trackId) => String(trackId);

const _lyrPut = (key, entry) => {
  const m = _lyrStore.entries;
  m.delete(key);
  m.set(key, entry);
  while (m.size > _lyrCacheMax) m.delete(m.keys().next().value);
};
const _lyrPeek = (key) => {
  const m = _lyrStore.entries;
  const e = m.get(key);
  if (e) { m.delete(key); m.set(key, e); }   // used: to the back of the line
  return e || null;
};
// Missing, a failed read a minute old, or a "still looking" doc a minute old.
const _lyrStale = (entry) => !entry
  || (entry.state === 'error' && _lyrNow() - entry.at >= _lyrCheckingTtlMs)
  || (entry.state === 'ready' && !!(entry.doc && entry.doc.checking)
      && _lyrNow() - entry.at >= _lyrCheckingTtlMs);

/* Read one song's lyrics into the store. One read per song at a time; a
 * quiet read (a refusal opens no prompt — nobody asked for anything). */
const _lyrLoad = (trackId) => {
  const key = _lyrKey(trackId);
  if (_lyrStore.inflight.has(key)) return _lyrStore.inflight.get(key);
  if (_lyrIsDenied()) return Promise.resolve(null);
  const cred = _lyrCredential();
  const read = Promise.resolve()
    .then(() => apiGet(`/api/music/library/${encodeURIComponent(key)}/lyrics`, { quiet: true }))
    .then((doc) => { _lyrPut(key, { state: 'ready', doc: doc || null, at: _lyrNow() }); },
          (e) => {
            const status = e && e.status;
            if (status === 401 || status === 403) {
              // Not the household tier: show nothing anywhere, ask nothing
              // more, until a credential arrives.
              _lyrStore.denied = { cred };
              _lyrStore.entries.clear();
            } else {
              _lyrPut(key, { state: 'error', doc: null, at: _lyrNow() });
            }
          })
    .then(() => { _lyrStore.inflight.delete(key); _lyrNotify(); return null; });
  _lyrStore.inflight.set(key, read);
  _lyrNotify();
  return read;
};

// A credential that comes or goes (a pairing, a sign-in or -out, a rotated
// token) changes what this browser may read: forget what was read under
// the old one, and try again.
try {
  if (typeof Auth !== 'undefined' && Auth && typeof Auth.subscribe === 'function') {
    let lyrSeen = _lyrCredential();
    Auth.subscribe(() => {
      const now = _lyrCredential();
      if (now === lyrSeen) return;
      lyrSeen = now;
      _lyrStore.entries.clear();
      _lyrStore.denied = null;
      _lyrNotify();
    });
  }
} catch { /* auth.js absent — nothing to follow */ }

/* useLyrics(trackId) → { state: 'loading'|'ready'|'hidden'|'error', doc, refresh }.
 * 'hidden': no song, or this viewer is not the household tier. A doc that
 * says Domovoi is still looking (`checking`) is read again every minute
 * while shown, and on `lyrics.changed` at most once per 15 s. */
const useLyrics = (trackId) => {
  const key = trackId === null || trackId === undefined || trackId === '' ? null : _lyrKey(trackId);
  const [, setVersion] = React.useState(0);
  React.useEffect(() => _lyrSubscribe(() => setVersion((n) => n + 1)), []);
  const denied = _lyrIsDenied();
  const entry = key == null || denied ? null : _lyrPeek(key);
  const stale = key != null && !denied && _lyrStale(entry);
  React.useEffect(() => { if (stale) _lyrLoad(key); }, [key, stale]);

  const checking = !!(entry && entry.state === 'ready' && entry.doc && entry.doc.checking);
  React.useEffect(() => {
    if (key == null || !checking) return undefined;
    const t = setInterval(() => {
      if (_lyrStale(_lyrStore.entries.get(key) || null)) _lyrLoad(key);
    }, _lyrChangedGapMs);
    return () => clearInterval(t);
  }, [key, checking]);
  React.useEffect(() => {
    if (key == null || !checking || typeof stateBus === 'undefined' || !stateBus
        || typeof stateBus.subscribe !== 'function') return undefined;
    return stateBus.subscribe((ev) => {
      if (!ev || ev.type !== 'lyrics.changed') return;
      const last = _lyrStore.changedAt.get(key) || 0;
      if (_lyrNow() - last < _lyrChangedGapMs) return;
      _lyrStore.changedAt.set(key, _lyrNow());
      _lyrLoad(key);
    });
  }, [key, checking]);

  const refresh = React.useCallback(() => (key == null ? Promise.resolve(null) : _lyrLoad(key)), [key]);
  let state;
  if (key == null || denied) state = 'hidden';
  else if (!entry || (entry.state === 'error' && _lyrStore.inflight.has(key))) state = 'loading';
  else state = entry.state === 'error' ? 'error' : 'ready';
  return { state, doc: state === 'ready' ? entry.doc : null, refresh };
};

/* ── the pure parts ─────────────────────────────────────────────────── */

/* The library song a player's lyrics belong to, or null (no lyrics UI at
 * all): a room's song while casting — the ROOM's, from its own reading,
 * since this browser's queue can lag what the room plays — else this
 * browser's current item when it is a library track. Null too once this
 * viewer has been refused (not the household tier). */
const lyricsTrackIdFor = (p) => {
  if (!p || _lyrIsDenied()) return null;
  const t = p.target || {};
  if (t.kind === 'room') {
    const np = p.remoteNp;
    return np && np.track_id != null ? np.track_id : null;
  }
  const c = p.current;
  return c && c.kind === 'library' && c.trackId != null ? c.trackId : null;
};

/* The line to highlight at `ms`: the last whose time is at most ms + 150
 * (shown a hair early), -1 before the first. Lines are in time order. */
const lyricsActiveIndex = (lines, ms) => {
  if (!Array.isArray(lines) || lines.length === 0) return -1;
  const at = Number(ms) + _lyrLeadMs;
  if (Number.isNaN(at)) return -1;
  let lo = 0;
  let hi = lines.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (Number(lines[mid].t) <= at) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
};

/* Where a room's song is now, in ms: its reported elapsed time, plus the
 * time since that reading while it plays, less the room's nudge (positive =
 * lyrics later), kept inside the song. `np` is the player's remoteNp
 * ({elapsed_sec, state, duration_sec, readAt}); `nowPerf` the same clock
 * as readAt. */
const lyricsRoomPositionMs = (np, nowPerf, offsetMs) => {
  if (!np) return 0;
  const elapsed = (Number(np.elapsed_sec) || 0) * 1000;
  const since = np.state === 'play' ? (Number(nowPerf) || 0) - (Number(np.readAt) || 0) : 0;
  let ms = elapsed + since - (Number(offsetMs) || 0);
  const dur = Number(np.duration_sec);
  if (Number.isFinite(dur) && dur > 0) ms = Math.min(ms, dur * 1000);
  return Math.max(0, ms);
};

/* ── the room nudge: per room, remembered in this browser ───────────── */
const _lyrNudgeKey = (roomId) => `domovoi-lyrics-nudge:${roomId}`;
const _lyrClampNudge = (ms) => {
  const n = Math.round(Number(ms) || 0);
  return Math.max(-_lyrNudgeMaxMs, Math.min(_lyrNudgeMaxMs, n));
};
const _lyrNudgeGet = (roomId) => {
  if (!roomId) return 0;
  if (_lyrStore.nudges.has(roomId)) return _lyrStore.nudges.get(roomId);
  let ms = 0;
  try {
    const raw = localStorage.getItem(_lyrNudgeKey(roomId));
    ms = raw == null ? 0 : _lyrClampNudge(raw);
  } catch { ms = 0; }
  _lyrStore.nudges.set(roomId, ms);
  return ms;
};
const _lyrNudgeSet = (roomId, ms) => {
  if (!roomId) return 0;
  const v = _lyrClampNudge(ms);
  _lyrStore.nudges.set(roomId, v);
  try {
    if (v === 0) localStorage.removeItem(_lyrNudgeKey(roomId));
    else localStorage.setItem(_lyrNudgeKey(roomId), String(v));
  } catch { /* storage refused: kept for this page */ }
  _lyrNotify();
  return v;
};
const _lyrNudgeText = (ms) => (ms ? `${ms > 0 ? '+' : '−'}${(Math.abs(ms) / 1000).toFixed(2)} s` : '');

const _lyrReducedMotion = () => {
  try {
    return !!(typeof window !== 'undefined' && window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch { return false; }
};

/* ── one line ───────────────────────────────────────────────────────── */
// Memoised on what a line shows, so a frame re-renders only the lines whose
// state changed — the one that stopped being current and the one that is.
const _lyrRowSame = (a, b) => a.text === b.text && a.state === b.state && a.t === b.t && a.seek === b.seek;

// A gap (an instrumental break) is a muted music note — the design system's
// icon, not a Unicode glyph (docs/design/README.md: no glyphs as icons).
const LyricsLine = React.memo(function LyricsLine({ text, state, t, seek }) {
  const cls = `lyr-line lyr-${state}${text ? '' : ' lyr-gap'}`;
  const current = state === 'now' ? 'true' : undefined;
  const words = text || <span className="lyr-gap-note" aria-hidden="true"><Icon name="music" size={13}/></span>;
  if (seek) {
    return (
      <button type="button" className={cls} aria-current={current}
              aria-label={text ? undefined : 'instrumental break'}
              onClick={() => seek(t)}>
        {words}
      </button>
    );
  }
  return <div className={cls} aria-current={current}>{words}</div>;
}, _lyrRowSame);

/* ── LyricsView ─────────────────────────────────────────────────────── */
/* trackId: the library song. follow: {kind: 'local'} (this browser's
 * playback) or {kind: 'room', roomId}. height: px (CSS may override, see
 * styles.css "Lyrics"). compact: the smaller type of the float and the
 * phone sheet. */
const LyricsView = ({ trackId, follow, height = 240, compact = false }) => {
  const p = usePlayback();
  const { state, doc, refresh } = useLyrics(trackId);
  const kind = follow && follow.kind === 'room' ? 'room' : 'local';
  const roomId = kind === 'room' ? (follow.roomId || null) : null;
  // (A nudge from any surface notifies the store, which useLyrics follows.)
  const offset = _lyrNudgeGet(roomId);
  const lines = state === 'ready' && doc && doc.status === 'synced' && Array.isArray(doc.lines)
    && doc.lines.length ? doc.lines : null;

  // Where the song is. Local: the player's own clock. Room: its reading,
  // carried forward on animation frames while it plays (re-rendering only
  // when the line changes); the 2 s poll re-anchors it.
  const np = roomId && p.remoteNp && p.remoteNp.room_id === roomId ? p.remoteNp : null;
  const [, setFrameLine] = React.useState(-1);
  React.useEffect(() => {
    if (kind !== 'room' || !lines || !np || np.state !== 'play'
        || typeof requestAnimationFrame !== 'function') return undefined;
    let raf = 0;
    let last = null;
    const frame = () => {
      const i = lyricsActiveIndex(lines, lyricsRoomPositionMs(np, _lyrPerfNow(), offset));
      if (i !== last) { last = i; setFrameLine(i); }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => { if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(raf); };
  }, [kind, lines, np, offset]);
  let active = -1;
  if (lines && kind === 'room') active = lyricsActiveIndex(lines, lyricsRoomPositionMs(np, _lyrPerfNow(), offset));
  else if (lines) active = lyricsActiveIndex(lines, (Number(p.positionSec) || 0) * 1000);

  // A hand on the list (wheel, touch, a drag, the keys) stops following for
  // 4 s and offers "follow"; a tap on a line or "follow" resumes at once.
  const [held, setHeld] = React.useState(false);
  const holdTimer = React.useRef(null);
  const holdFollow = React.useCallback(() => {
    setHeld(true);
    if (holdTimer.current) clearTimeout(holdTimer.current);
    holdTimer.current = setTimeout(() => { holdTimer.current = null; setHeld(false); }, _lyrHoldMs);
  }, []);
  const resumeFollow = React.useCallback(() => {
    if (holdTimer.current) { clearTimeout(holdTimer.current); holdTimer.current = null; }
    setHeld(false);
  }, []);
  React.useEffect(() => () => { if (holdTimer.current) clearTimeout(holdTimer.current); }, []);
  const pointerDown = React.useRef(false);
  React.useEffect(() => {
    if (typeof window === 'undefined' || !window.addEventListener) return undefined;
    const up = () => { pointerDown.current = false; };
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    return () => {
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
    };
  }, []);

  // A line seeks only in this browser's playback of a seekable item; a room
  // cannot seek. The callback is stable, so the memoised lines hold.
  const canSeek = kind === 'local' && !!(p.current && p.current.seekable !== false)
    && typeof p.seek === 'function';
  const seekRef = React.useRef(null);
  seekRef.current = canSeek ? p.seek : null;
  const seekTo = React.useCallback((t) => {
    const s = seekRef.current;
    if (s) s(t / 1000);
    resumeFollow();
  }, [resumeFollow]);
  const seek = canSeek ? seekTo : null;

  // Keep the current line at 35% of the list's height; the first placement
  // of a song is instant, the rest glide (instant under reduced motion).
  const scrollRef = React.useRef(null);
  const placedRef = React.useRef(false);
  React.useEffect(() => { placedRef.current = false; }, [trackId]);
  React.useEffect(() => {
    if (!lines || held) return;
    const sc = scrollRef.current;
    if (!sc || typeof sc.querySelector !== 'function') return;
    const row = sc.querySelector('[aria-current="true"]');
    const top = Math.max(0, row ? row.offsetTop + row.offsetHeight / 2 - sc.clientHeight * 0.35 : 0);
    const instant = !placedRef.current || _lyrReducedMotion();
    placedRef.current = true;
    try { sc.scrollTo({ top, behavior: instant ? 'auto' : 'smooth' }); }
    catch { sc.scrollTop = top; }
  }, [active, held, lines]);

  if (state === 'hidden') return null;

  let body;
  if (state === 'loading') {
    body = <div className="lyr-skel" aria-hidden="true"><span/><span/><span/></div>;
  } else if (state === 'error') {
    body = (
      <div className="lyr-empty">
        <div>couldn't load the lyrics</div>
        <Button icon="refresh-cw" onClick={refresh}>retry</Button>
      </div>
    );
  } else if (lines) {
    body = lines.map((l, i) => (
      <LyricsLine key={i} text={l.text} t={l.t} seek={seek}
                  state={i < active ? 'past' : i === active ? 'now' : 'next'}/>
    ));
  } else if (doc && doc.text && (doc.status === 'plain' || doc.status === 'synced')) {
    body = <div className="lyr-plain">{doc.text}</div>;
  } else if (doc && doc.status === 'instrumental') {
    body = <div className="lyr-empty">instrumental — no words to show</div>;
  } else {
    body = <div className="lyr-empty">{doc && doc.checking ? 'looking for lyrics…' : 'no lyrics for this song'}</div>;
  }

  const label = state === 'ready' && doc ? doc.source_label : null;
  const nudge = roomId && lines ? (
    <span className="lyr-nudge">
      <span>timing{offset ? ` ${_lyrNudgeText(offset)}` : ''}</span>
      <button type="button" aria-label="lyrics earlier"
              onClick={() => _lyrNudgeSet(roomId, offset - _lyrNudgeStepMs)}>{'−¼ s'}</button>
      <span aria-hidden="true">·</span>
      <button type="button" aria-label="lyrics later"
              onClick={() => _lyrNudgeSet(roomId, offset + _lyrNudgeStepMs)}>{'+¼ s'}</button>
      <span aria-hidden="true">·</span>
      <button type="button" aria-label="reset lyrics timing" disabled={!offset}
              onClick={() => _lyrNudgeSet(roomId, 0)}>reset</button>
    </span>
  ) : null;
  const followBtn = held && lines ? (
    <button type="button" className="lyr-follow" onClick={resumeFollow}>follow</button>
  ) : null;

  return (
    <div className={`lyr-view${compact ? ' lyr-compact' : ''}`} style={{ '--lyr-h': `${height}px` }}>
      <div className="lyr-scroll" ref={scrollRef} role="region" aria-label="lyrics" tabIndex={0}
           onWheel={lines ? holdFollow : undefined}
           onTouchMove={lines ? holdFollow : undefined}
           onPointerDown={() => { pointerDown.current = true; }}
           onScroll={() => { if (lines && pointerDown.current) holdFollow(); }}
           onKeyDown={lines ? (e) => { if (e && _lyrScrollKeys.has(e.key)) holdFollow(); } : undefined}>
        {body}
      </div>
      {(label || nudge || followBtn) && (
        <div className="lyr-foot">
          {label && <span className="lyr-source">{label}</span>}
          {nudge}
          {followBtn}
        </div>
      )}
    </div>
  );
};

/* ── the desktop bar's floating panel ───────────────────────────────── */
/* Built like player.jsx's QueuePanel: fixed above the docked bar, over a
 * click-away layer. The bar keeps it, the queue and the cast menu
 * mutually exclusive. */
const LyricsFloat = ({ trackId, follow, onClose }) => (
  <>
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 46 }}/>
    <div className="lyr-float"
         style={{ position: 'fixed', right: 14, bottom: 'calc(var(--dock-bottom, 0px) + 76px)', width: 380,
                  height: 'min(60vh, 460px)', zIndex: 47, background: 'var(--card)',
                  border: '1px solid var(--border)', borderRadius: 'var(--r-md)', boxShadow: 'var(--shadow-md)',
                  display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex',
                    alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>lyrics</div>
        <IconButton name="x" onClick={onClose} title="close lyrics" aria-label="close lyrics"/>
      </div>
      <LyricsView trackId={trackId} follow={follow} height={380} compact/>
    </div>
  </>
);

/* ── a room card's current line (music.jsx NPCard) ──────────────────── */
/* The room's current TIMED line, from its reported elapsed time plus the
 * card's own second ticker (`tick`) while it plays, less the room's nudge.
 * Nothing for plain lyrics, none, a stream, or a viewer outside the
 * household tier. The line keeps its height through a gap. */
const LyricsRoomLine = ({ np, tick }) => {
  const playing = !!(np && np.state === 'play' && np.song);
  const paused = !!(np && np.state === 'pause' && np.song);
  const trackId = (playing || paused) && np.track_id != null ? np.track_id : null;
  const { state, doc } = useLyrics(trackId);
  if (trackId == null || state !== 'ready' || !doc || doc.status !== 'synced'
      || !Array.isArray(doc.lines) || !doc.lines.length) return null;
  const sec = (Number(np.elapsed_sec) || 0) + (playing ? (Number(tick) || 0) : 0);
  const i = lyricsActiveIndex(doc.lines, sec * 1000 - _lyrNudgeGet(np.room_id));
  const line = i >= 0 ? doc.lines[i].text : '';
  return <div className="lyr-room-line">{line || ' '}</div>;
};

/* ── the Jobs tab's card ────────────────────────────────────────────── */
const _lyrNum = (n) => (typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('en-US') : '—');
const _lyrSongs = (n) => (n === 1 ? 'song' : 'songs');
const _lyrDay = (iso) => {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return null;
  try { return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); }
  catch { return String(iso).slice(0, 10); }
};

/* What the workers' short codes mean, said for the household (the review of
 * 2026-10-06: "unavailable:network" is no sentence). The code itself stays
 * on the line as its title, for whoever helps with the box. */
const _lyrLrclibWhy = (code) => {
  const c = String(code || '');
  if (!c) return 'it did not answer';
  if (c === 'unavailable:network' || c === 'unavailable:timeout') {
    return "LRCLIB didn't answer; trying again in a few minutes";
  }
  if (c.startsWith('unavailable:')) return 'LRCLIB is having trouble; trying again in a few minutes';
  if (c === 'rate_limited') return 'LRCLIB asked to slow down';
  if (c.startsWith('rejected:')) return 'LRCLIB turned some songs down';
  if (c === 'save:too_large') return 'some lyrics were too long to keep';
  return 'something went wrong on this server';   // save:<error>, an error's name
};
const _lyrLrcWhy = (code) => ({
  permission: 'no permission to write in the music folder',
  outside_music_dir: 'the song is outside the music folder',
  io: 'a disk error',
  name: "a file name it can't use",
}[String(code || '')] || 'something went wrong');

/* The card's lines from GET /api/music/lyrics/status, each {text, title}
 * (title: the worker's own code behind the words, or null) — pure, so
 * every line is pinned by a test. */
const _lyrJobsRows = (s) => {
  if (!s) return [];
  const bySource = s.by_source || {};
  const scan = s.scan || {};
  const lr = s.lrclib || {};
  const lrc = s.lrc_files || {};
  const index = s.index || {};
  const tracks = Number(s.tracks) || 0;
  const out = [];
  const add = (text, title) => out.push({ text, title: title || null });
  add(`lyrics · ${_lyrNum(s.with_lyrics)} of ${_lyrNum(tracks)} ${_lyrSongs(tracks)} · ${_lyrNum(s.synced)} timed`);
  const unscanned = Number(scan.unscanned) || 0;
  add(unscanned > 0
    ? `reading your files — ${_lyrNum(Math.max(0, tracks - unscanned))} of ${_lyrNum(tracks)}`
    : `in your files: ${_lyrNum(bySource.sidecar || 0)} .lrc · ${_lyrNum(bySource.embedded || 0)} in the songs' tags`);
  const counts = `${_lyrNum(lr.found || 0)} found · ${_lyrNum(lr.not_found || 0)} not found`;
  switch (lr.state) {
    case 'off':
      add('LRCLIB: off — Settings → Configuration → Library'); break;
    case 'internet_off':
      add('LRCLIB: off — this Domovoi stays off the internet (Settings → Internet)'); break;
    case 'offline':
      add('LRCLIB: paused — offline'); break;
    case 'rate_limited':
      add('LRCLIB: paused — LRCLIB asked to slow down'); break;
    case 'paused':
    case 'error':
      add(`LRCLIB: paused — ${_lyrLrclibWhy(lr.last_error)}`, lr.last_error); break;
    case 'running':
      add(`LRCLIB: asking — ${_lyrNum(lr.due)} ${_lyrSongs(lr.due)} to go · ${counts}`); break;
    case 'idle':
    case 'done': {
      const again = lr.next_retry_at ? _lyrDay(lr.next_retry_at) : null;
      add(`LRCLIB: done — ${counts}${again ? ` · asks again from ${again}` : ''}`); break;
    }
    default:
      add('LRCLIB: status unknown');
  }
  if (lrc.enabled) {
    let line = `.lrc files: ${_lyrNum(lrc.written || 0)} saved`;
    if (lrc.exists > 0) line += ` · ${_lyrNum(lrc.exists)} ${_lyrSongs(lrc.exists)} already had one`;
    const failed = lrc.failed > 0;
    if (failed) line += ` · ${_lyrNum(lrc.failed)} couldn't be saved${lrc.last_error ? ` (${_lyrLrcWhy(lrc.last_error)})` : ''}`;
    add(line, failed ? lrc.last_error : null);
  }
  if (index.pending > 0) add(`making lyrics searchable — ${_lyrNum(index.pending)} to go`);
  if (s.search_enabled === false) add('finding songs by their words: off — Settings → Configuration → Library');
  return out;
};
const lyricsJobsLines = (s) => _lyrJobsRows(s).map((r) => r.text);

/* How far the house has got with its lyrics. Nothing at all for a viewer
 * outside the household tier, or while the library is empty. */
const LyricsJobsCard = () => {
  const { data, error, refresh } = useApiObject('/api/music/lyrics/status',
    { quiet: true, eventTypes: ['lyrics.changed'] });
  // Refused: not the household tier — show nothing, and stop asking.
  const refused = !!(error && (error.status === 401 || error.status === 403));
  React.useEffect(() => {
    if (refused) return undefined;
    const t = setInterval(() => { refresh(); }, 10000);
    return () => clearInterval(t);
  }, [refresh, refused]);
  if (refused) return null;
  if (!data || !(Number(data.tracks) > 0)) return null;
  const rows = _lyrJobsRows(data);
  return (
    <div className="lyr-jobs">
      <div className="lyr-jobs-head"><Icon name="mic-vocal" size={14}/><span>{rows[0].text}</span></div>
      {rows.slice(1).map((r, i) => (
        <div key={i} className="lyr-jobs-line" title={r.title || undefined}>{r.text}</div>
      ))}
    </div>
  );
};

/* expose to other Babel scripts */
Object.assign(window, {
  LyricsView, LyricsFloat, LyricsRoomLine, LyricsJobsCard,
  useLyrics, lyricsActiveIndex, lyricsJobsLines, lyricsTrackIdFor, lyricsRoomPositionMs,
});
