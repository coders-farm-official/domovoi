/* Home — the dashboard's default page (#home): the house at a glance.
 *
 * "What is the house doing right now, and does anything need me?",
 * answered in about five seconds on a phone (design-notes HOME-PLAN.md,
 * approved 2026-09-26). On a phone, top to bottom:
 *
 *   status line      household name, date, "3 online · 1 offline · 2 playing
 *                    · 1 timer", the live dot, "pair this phone"
 *   needs attention  one line per problem, collapsed by kind
 *   timers           every timer and reminder in the house, counting down
 *   rooms            one row per room: pause / resume / stop / play
 *   announce         say something in every room
 *   today            today's and tomorrow's events
 *   everything       every page that is not one of the strip's five tabs
 *
 * On a desktop the same sections sit in two columns (styles.css, "Home").
 *
 * No home endpoint: the page composes reads the dashboard already has.
 * Every one of them is OPEN and quiet (`quiet: true`, data.js), because
 * this is the page a guest's phone lands on and it must never open a
 * sign-in or pair prompt by itself. The admin-only checks (approvals,
 * adoption, version, disk) are not even asked for until this browser is
 * an admin's. A press (pause, cancel, announce) still prompts and
 * replays, as it does everywhere.
 *
 * Never on Home: transcripts, voice notes, memories, people's names, chat
 * titles, Wi-Fi names. A shared screen (an admin marks the device in
 * Settings → Devices) also hides calendar titles, reminder text and the
 * problem rows (one neutral line at most), and the personal pages drop off
 * every launcher (components.jsx SHARED_SCREEN_HIDDEN). That is
 * presentational, not a boundary: everything it hides is an open read, a
 * browser that can't register (a private window on the tablet itself) is
 * never masked, and the tablet still holds the household token.
 *
 * Every top-level name starts with Home or HOME_: all the dashboard's
 * scripts share one Babel scope, and a second `const Foo` anywhere is a
 * SyntaxError that kills whichever file loads later. Helpers another page
 * owns (fmtRemaining, wifiTone, Broadcast, fmtClock) are read from window.
 */

/* ---- constants ---------------------------------------------------- */

const HOME_QUIET = { quiet: true };
const HOME_LIVE_POLL_MS = 30 * 1000;       // rooms + timers, only while the socket is down
const HOME_HEALTH_MS = 60 * 1000;
const HOME_APPROVALS_MS = 30 * 1000;
const HOME_HARDWARE_MS = 5 * 60 * 1000;
const HOME_VERSION_GAP_MS = 10 * 60 * 1000;
const HOME_FOCUS_GAP_MS = 15 * 1000;       // focus + visibilitychange arrive together
const HOME_DONE_MS = 60 * 1000;            // "done · kitchen" lingers this long
const HOME_ROOM_DEBOUNCE_MS = 300;         // every /api/satellites read opens an MPD connection per room
const HOME_STOP_ALL_ARM_MS = 4000;
const HOME_DISK_WARN = 90;
const HOME_DISK_ERR = 95;
const HOME_PHONE_ROWS = 3;                 // attention rows and today rows a phone shows
const HOME_TODAY_ROWS = 6;                 // ...and a desktop
const HOME_PHONE_TIMERS = 2;               // timers a phone shows, so the rooms start above the fold
const HOME_FRESH_MS = 5 * 1000;            // a read this young needs no re-read on (re)connect
const HOME_MANUAL_MS = 30 * 60 * 1000;     // the manual's example phrases hardly change
const HOME_SOON_SEC = 600;                 // countdowns turn warn under 10 min
const HOME_ROOM_EVENTS = [
  'satellites.presence.changed', 'satellites.wifi.changed', 'satellites.dropins.changed',
  'satellites.display.changed', 'satellites.pending.changed', 'music.now_playing.changed',
];
const HOME_WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
const HOME_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
// The first-run hint prefers these handlers' example phrases, in order.
const HOME_HINT_HANDLERS = ['timer', 'reminder', 'clock', 'music'];
const HOME_HINT_FALLBACK = 'set a timer for 10 minutes';

/* Answers that outlive one mount of the page, for the reads whose cost is
 * the point (useCachedObject below). On a phone Home is also the "more"
 * menu, so it mounts every time someone passes through it on the way to
 * Podcasts or Settings; the hardware probe alone runs nvidia-smi and a CPU
 * sample on the core. path → { at, data }. */
const HOME_CACHE = new Map();

/* Pages the "everything" grid lists besides the nav items: Settings has
 * no nav row (the topbar gear opens it) and the manual is reached from
 * Settings → About, so a phone would otherwise have no way to either. */
const HOME_EXTRA_TILES = [
  { route: 'settings', icon: 'settings', label: 'Settings', core: true },
  { route: 'manual', icon: 'book', label: 'User Manual', core: true },
];

/* ---- small helpers ------------------------------------------------ */

// "Now" is always Date.now() here — never a bare `new Date()` — so one
// clock drives every countdown, progress bar and "today".
const HomeFmtDate = (ms) => {
  const d = new Date(ms);
  return `${HOME_WEEKDAYS[d.getDay()]} ${d.getDate()} ${HOME_MONTHS[d.getMonth()]}`;
};

const HomeDayStart = (ms, plusDays = 0) => {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  if (plusDays) d.setDate(d.getDate() + plusDays);
  return d.getTime();
};

const HomeFmtLeft = (sec) => {
  if (sec >= 86400) return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h`;
  return (window.fmtRemaining || fmtDur)(sec);
};

const HomeClock = (iso) => (window.fmtClock
  ? window.fmtClock(iso)
  : new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));

const HomePlural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;

// Answered once: the read came back, with data or with an error. A hook
// that has not answered yet is why the page says "checking…".
const HomeAnswered = (r) => !!r && !r.loading && (r.data != null || r.error != null);
const HomeOk = (r) => !!r && r.data != null;

/* What a timer is called. A reminder's text is household content and
 * shows — except on a shared screen, where it reads "reminder · office".
 * A timer's own label ("pasta") is low-risk and always shows. */
const HomeTimerTitle = (t, shared) => {
  if (t.is_reminder) {
    return shared ? `reminder · ${t.room_id || 'no room'}` : (t.message || t.label || 'reminder');
  }
  if (t.label) return t.label;
  const total = (Date.parse(t.expires_at) - Date.parse(t.created_at)) / 1000;
  if (!(total > 0)) return 'timer';
  return total < 90 ? `${Math.round(total)}s timer` : `${Math.round(total / 60)} min timer`;
};
// ...and what a toast calls it: "cancelled pasta timer".
const HomeTimerNoun = (t, shared) => {
  if (t.is_reminder) return 'reminder';
  return t.label ? `${t.label} timer` : HomeTimerTitle(t, shared);
};

const HomeSongTitle = (song) => (song && (song.title || (song.file || '').split('/').pop())) || 'unknown';

/* ---- hooks -------------------------------------------------------- */

/* The page's hooks, in one namespace: every top-level name here must
 * start with Home (the shared-scope rule above), and a hook reads best
 * as useSomething. */
const HomeHooks = (() => {
  /* Who is looking. `isAdmin` also covers a cookie session that survived a
   * reload (status.authenticated — isLoggedIn() alone misses it) and the
   * pre-setup grace, where every read, the admin ones included, is open. */
  const whoIsLooking = () => {
    let st = null; let loggedIn = false; let paired = false;
    try {
      if (typeof Auth !== 'undefined') {
        st = Auth.status || null;
        loggedIn = !!(Auth.isLoggedIn && Auth.isLoggedIn());
        paired = !!(Auth.isPaired && Auth.isPaired());
      }
    } catch { /* auth.js absent */ }
    const unclaimed = !!st && st.setup_complete === false;
    return {
      known: !!st,
      unclaimed,
      paired,
      isAdmin: loggedIn || !!(st && st.authenticated) || unclaimed,
    };
  };

  const useViewer = () => {
    const [, force] = React.useReducer((n) => n + 1, 0);
    React.useEffect(() => {
      if (typeof Auth === 'undefined') return undefined;
      try {
        const off = Auth.subscribe(force);
        // Nobody has asked the server who this is yet: ask once, so an
        // unclaimed box and a cookie session show up without a sign-in.
        if (!Auth.status && Auth.refreshStatus) Auth.refreshStatus();
        return off;
      } catch { return undefined; }
    }, []);
    return whoIsLooking();
  };

  // The socket's own status events (data.js StateBus `_status`).
  const useLive = () => {
    const [live, setLive] = React.useState(() => {
      try { return typeof stateBus !== 'undefined' && !!stateBus.connected; } catch { return false; }
    });
    useStateEvents(['_status'], (ev) => setLive(!!ev.connected));
    return live;
  };

  // Every poll here skips while the tab is hidden.
  const tabHidden = () => {
    try { return typeof document !== 'undefined' && !!document.hidden; } catch { return false; }
  };

  const useInterval = (fn, ms, enabled = true) => {
    const ref = React.useRef(fn);
    ref.current = fn;
    React.useEffect(() => {
      if (!enabled || !ms) return undefined;
      const t = setInterval(() => { if (!tabHidden()) ref.current(); }, ms);
      return () => clearInterval(t);
    }, [ms, enabled]);
  };

  // Back on the page (focus, or the tab shown again): re-read what may
  // have moved while nobody was looking. Both events fire together.
  const useOnFocus = (fn) => {
    const ref = React.useRef(fn);
    ref.current = fn;
    React.useEffect(() => {
      let last = 0;
      const on = () => {
        if (tabHidden() || Date.now() - last < HOME_FOCUS_GAP_MS) return;
        last = Date.now();
        ref.current();
      };
      window.addEventListener('focus', on);
      const doc = (typeof document !== 'undefined' && document.addEventListener) ? document : null;
      if (doc) doc.addEventListener('visibilitychange', on);
      return () => {
        window.removeEventListener('focus', on);
        if (doc) doc.removeEventListener('visibilitychange', on);
      };
    }, []);
  };

  const useDebounced = (fn, ms) => {
    const ref = React.useRef(fn);
    ref.current = fn;
    const timer = React.useRef(null);
    React.useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
    return React.useCallback(() => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => { timer.current = null; ref.current(); }, ms);
    }, [ms]);
  };

  // A 1 s re-render while something on the page is counting.
  const useTick = (active) => {
    const [, setN] = React.useState(0);
    useInterval(() => setN((n) => n + 1), 1000, active);
  };

  /* useApiObject's shape ({data, error, loading, refresh}) for a read an
   * answer younger than `ttl` can stand in for: reused from HOME_CACHE on
   * mount instead of asked for again. refresh() always asks. Quiet, like
   * every read on this page. */
  const useCachedObject = (path, ttl) => {
    const fresh = () => {
      const hit = path ? HOME_CACHE.get(path) : null;
      return hit && Date.now() - hit.at < ttl ? hit : null;
    };
    const [state, setState] = React.useState(() => {
      const hit = fresh();
      return hit ? { data: hit.data, error: null, loading: false }
        : { data: null, error: null, loading: !!path };
    });
    const alive = React.useRef(true);
    React.useEffect(() => () => { alive.current = false; }, []);
    const load = React.useCallback(async () => {
      if (!path) { setState({ data: null, error: null, loading: false }); return; }
      try {
        const data = await apiGet(path, HOME_QUIET);
        HOME_CACHE.set(path, { at: Date.now(), data });
        if (alive.current) setState({ data, error: null, loading: false });
      } catch (error) {
        // Like useApiObject: a failed re-read keeps what was last known.
        if (alive.current) setState((st) => ({ data: st.data, error, loading: false }));
      }
    }, [path]);
    React.useEffect(() => {
      const hit = fresh();
      if (hit) setState({ data: hit.data, error: null, loading: false });
      else load();
    }, [load]);
    return { ...state, refresh: load };
  };

  // true on a phone, false on a desktop, null when the browser can't say
  // (the CSS then decides alone).
  const useIsPhone = () => {
    const mq = React.useMemo(() => {
      try { return window.matchMedia ? window.matchMedia('(max-width: 760px)') : null; } catch { return null; }
    }, []);
    const [phone, setPhone] = React.useState(() => (mq ? !!mq.matches : null));
    React.useEffect(() => {
      if (!mq) return undefined;
      const on = () => setPhone(!!mq.matches);
      if (mq.addEventListener) mq.addEventListener('change', on);
      else if (mq.addListener) mq.addListener(on);
      return () => {
        if (mq.removeEventListener) mq.removeEventListener('change', on);
        else if (mq.removeListener) mq.removeListener(on);
      };
    }, [mq]);
    return phone;
  };

  /* The house's timers against the SERVER's clock (server_now), plus the
   * ones that just fired. The server deletes a timer the moment it fires,
   * so this page is the only place that remembers it for the minute of
   * "done · kitchen". A timer that vanishes before its time was cancelled,
   * not fired, and leaves no line. */
  const useTimers = (data, cancelledRef) => {
    const list = (data && Array.isArray(data.timers)) ? data.timers : [];
    const offset = React.useMemo(() => {
      const s = data && Date.parse(data.server_now);
      return Number.isFinite(s) ? s - Date.now() : 0;
    }, [data]);
    const prevRef = React.useRef(new Map());
    const firedRef = React.useRef(new Map());
    React.useEffect(() => {
      const now = Date.now() + offset;
      const next = new Map(list.map((t) => [t.id, t]));
      for (const [id, t] of prevRef.current) {
        if (next.has(id) || cancelledRef.current.has(id)) continue;
        const at = Date.parse(t.expires_at);
        if (at <= now + 2000) firedRef.current.set(id, { ...t, doneAt: at });
      }
      prevRef.current = next;
    }, [data]);

    const now = Date.now() + offset;
    const active = [];
    const done = new Map();
    for (const t of list) {
      const at = Date.parse(t.expires_at);
      if (at > now) active.push(t);
      else if (!cancelledRef.current.has(t.id)) done.set(t.id, { ...t, doneAt: at });
    }
    for (const [id, d] of firedRef.current) {
      if (now - d.doneAt >= HOME_DONE_MS) firedRef.current.delete(id);
      else if (!done.has(id)) done.set(id, d);
    }
    const doneList = [...done.values()]
      .filter((d) => now - d.doneAt < HOME_DONE_MS)
      .sort((a, b) => b.doneAt - a.doneAt);
    return { active, done: doneList, now };
  };

  return { useViewer, useLive, useInterval, useOnFocus, useDebounced, useTick, useIsPhone, useTimers,
           useCachedObject, tabHidden };
})();

/* ---- needs attention: the rules ----------------------------------- */

/* Every problem Home can name, ranked err → warn. `scope` says who may
 * see it: 'setup' (the claim row — everyone, since anyone on the network
 * can change settings until the box is claimed), 'open' (explains what a
 * person notices) or 'admin' (only an admin can act; those reads are
 * never even made for anyone else). When the core or the database is
 * down that is the only story: every rule that reads through them would
 * just repeat it, so they wait. Home never fixes anything itself — each
 * row links to the page that does. */
const HomeAttentionRows = ({ viewer, health, rooms, plugins, pluginErrors, acq,
                             approvals, pending, version, hardware }) => {
  const rows = [];
  const add = (r) => rows.push(r);
  if (viewer.unclaimed) {
    add({ key: 'claim', tone: 'err', scope: 'setup', claim: true,
          text: "this box isn't claimed yet · anyone on the network can change settings" });
  }
  const dbDown = !!health && health.db_reachable === false;
  const coreDown = !!health && health.domovoi_reachable === false;
  if (dbDown) {
    add({ key: 'db', tone: 'err', scope: 'open', href: '#settings',
          text: "the database isn't answering · nothing new can be saved" });
  }
  if (coreDown) {
    add({ key: 'core', tone: 'err', scope: 'open', href: '#satellites',
          text: "the Domovoi server isn't answering · rooms show the last known state" });
  }

  if (!dbDown && !coreDown) {
    const stt = health && health.stt;
    if (stt === 'unavailable') {
      add({ key: 'stt', tone: 'err', scope: 'open', href: '#settings',
            text: "speech recognition is off · the rooms can't understand anyone" });
    } else if (stt === 'fallback') {
      add({ key: 'stt', tone: 'warn', scope: 'open', href: '#settings',
            text: 'speech recognition is on its slower fallback model' });
    }

    const list = Array.isArray(rooms) ? rooms : [];
    const offline = list.filter((s) => s.status === 'offline');
    if (offline.length === 1) {
      add({ key: 'offline', tone: 'warn', scope: 'open', href: '#satellites',
            at: offline[0].last_connected_at,
            text: `${offline[0].room_id} is offline · it can't hear anyone right now` });
    } else if (offline.length > 1) {
      add({ key: 'offline', tone: 'warn', scope: 'open', href: '#satellites',
            text: `${offline.length} rooms offline` });
    }
    const waiting = list.filter((s) => s.status === 'waiting');
    if (waiting.length) {
      add({ key: 'waiting', tone: 'warn', scope: 'open', href: '#satellites',
            text: waiting.length === 1
              ? `${waiting[0].room_id} was set up but hasn't connected yet`
              : `${waiting.length} rooms were set up but haven't connected yet` });
    }
    const dead = list.filter((s) => s.sat_type === 'video' && s.status === 'online'
      && s.display && s.display.kiosk_alive === false);
    if (dead.length) {
      add({ key: 'kiosk', tone: 'warn', scope: 'open', href: '#satellites',
            text: dead.length === 1
              ? `the ${dead[0].room_id} screen stopped showing anything`
              : `${dead.length} screens stopped showing anything` });
    }

    // Plugins: the server's view (GET /api/plugins, open so that errors
    // show for everyone) plus what only this browser saw — a page script
    // that failed to load or threw while rendering.
    const bad = new Map();   // slug → {name, tone}
    ((plugins && plugins.plugins) || []).forEach((p) => {
      if (p.status === 'uninstalled') return;
      const broken = p.status === 'load_error' || (p.enabled !== false
        && (!!p.web_load_error || (p.page_errors || []).length > 0));
      if (broken) bad.set(p.slug, { name: p.name || p.slug, tone: 'err' });
      else if (p.enabled !== false && p.status === 'degraded') bad.set(p.slug, { name: p.name || p.slug, tone: 'warn' });
    });
    Object.keys(pluginErrors || {}).forEach((slug) => {
      if ((pluginErrors[slug] || []).length) {
        const known = ((plugins && plugins.plugins) || []).find((p) => p.slug === slug);
        bad.set(slug, { name: (known && known.name) || slug, tone: 'err' });
      }
    });
    if (bad.size === 1) {
      const [only] = [...bad.values()];
      add({ key: 'plugins', tone: only.tone, scope: 'open', href: '#plugins',
            text: only.tone === 'err' ? `the ${only.name} plugin failed to load`
              : `the ${only.name} plugin is degraded` });
    } else if (bad.size > 1) {
      add({ key: 'plugins', tone: [...bad.values()].some((b) => b.tone === 'err') ? 'err' : 'warn',
            scope: 'open', href: '#plugins', text: `${bad.size} plugins have problems` });
    }

    // A media request is waiting and no provider plugin can fill it
    // (docs/FAQ.md). Never the request's own text.
    if (acq && acq.core_reachable) {
      const stuck = (acq.acquisitions || []).filter((a) => a.status === 'pending'
        && (a.kind === 'url' ? acq.can_fulfill_url === false : acq.can_fulfill_query === false));
      if (stuck.length) {
        add({ key: 'acq', tone: 'warn', scope: 'open', href: '#music',
              text: stuck.length === 1
                ? 'a media request is waiting · no provider plugin can fill it'
                : `${stuck.length} media requests are waiting · no provider plugin can fill them` });
      }
    }

    // Admin-only. These arrays/objects are null for anyone else: the
    // reads behind them were never made.
    if (Array.isArray(approvals) && approvals.length) {
      add({ key: 'approvals', tone: 'warn', scope: 'admin', href: '#satellites',
            text: approvals.length === 1
              ? `${approvals[0].room_id} is waiting for approval`
              : `${approvals.length} satellites are waiting for approval` });
    }
    if (Array.isArray(pending) && pending.length) {
      add({ key: 'adopt', tone: 'warn', scope: 'admin', href: '#satellites',
            text: pending.length === 1
              ? 'a satellite is plugged in and ready to adopt'
              : `${pending.length} satellites are plugged in and ready to adopt` });
    }
    if (version) {
      const last = version.last_update;
      const st = last && last.status;
      if (st === 'failed' || st === 'rollback_failed') {
        add({ key: 'update', tone: 'err', scope: 'admin', href: '#settings', at: last.finished_at,
              text: 'the last update failed' });
      } else if (st === 'rolled_back' || (version.bad_sha && st !== 'ok')) {
        add({ key: 'update', tone: 'warn', scope: 'admin', href: '#settings', at: last && last.finished_at,
              text: 'the last update was rolled back' });
      }
      const restartWaiting = pendingRestart(version);
      if (restartWaiting.code) {
        add({ key: 'restart', tone: 'warn', scope: 'admin', href: '#settings',
              text: "a restart is pending · the pulled code isn't running yet" });
      } else if (restartWaiting.plugins.length) {
        add({ key: 'restart', tone: 'warn', scope: 'admin', href: '#plugins',
              text: restartWaiting.plugins.length === 1
                ? `a restart is pending · the ${restartWaiting.plugins[0].slug} upgrade isn't running yet`
                : `a restart is pending · ${restartWaiting.plugins.length} plugin upgrades aren't running yet` });
      }
    }
    const disk = hardware && hardware.disk;
    if (disk && typeof disk.percent === 'number' && disk.percent >= HOME_DISK_WARN) {
      // The core measures $HOME, so it is the home disk, not "the" disk.
      add({ key: 'disk', tone: disk.percent >= HOME_DISK_ERR ? 'err' : 'warn', scope: 'admin',
            href: '#settings', text: `home disk is ${Math.round(disk.percent)}% full` });
    }
  }

  const rank = { err: 0, warn: 1 };
  return rows
    .map((r, i) => ({ ...r, i }))
    .sort((a, b) => (rank[a.tone] - rank[b.tone]) || (a.i - b.i));
};

/* Who sees which of those rows (HOME_PROBLEMS_VISIBILITY, admin-set):
 *   everyone  household members see the rows they can notice, an admin all
 *   summary   household members see one neutral line
 *   admins    household members see nothing
 * A shared screen shows the neutral line at most, whatever the setting —
 * even with an admin signed in on it. */
const HomeAttentionView = ({ rows, viewer, visibility, shared }) => {
  const own = viewer.isAdmin ? rows : rows.filter((r) => r.scope !== 'admin');
  const summary = own.length ? { mode: 'summary', count: own.length } : { mode: 'none' };
  if (shared) return (viewer.isAdmin || visibility !== 'admins') ? summary : { mode: 'none' };
  if (viewer.isAdmin) return { mode: 'rows', rows };
  if (visibility === 'admins') return { mode: 'none' };
  if (visibility === 'summary') return summary;
  return { mode: 'rows', rows: own };
};

/* ---- status line --------------------------------------------------- */

const HomeHeader = ({ name, nowMs, line, lineReady, live, viewer, onPair }) => {
  const [explain, setExplain] = React.useState(false);
  const unpaired = viewer.known && !viewer.paired && !viewer.isAdmin;
  const why = live
    ? 'live · changes show up the moment they happen'
    : unpaired
      ? "this browser isn't paired, so the server doesn't stream to it · the page re-reads every 30s"
      : 'the live connection is down · reconnecting, and re-reading every 30s meanwhile';
  return (
    <div className="page-header home-header">
      <div className="home-status">
        <h1 className="h2">{name}</h1>
        <div className="home-status-line">
          <span className="mono">{HomeFmtDate(nowMs)}</span>
          {lineReady
            ? line.map((part, i) => (
                <React.Fragment key={i}><span className="sep">·</span><span>{part}</span></React.Fragment>
              ))
            : <span className="home-skel" aria-hidden="true"/>}
          {/* On a phone the live dot ends the count line (the plan's place
              for it) instead of taking a row of its own above the fold. */}
          <button type="button" className="home-live-dot home-phone-only" title={why}
                  aria-label={live ? 'live' : 'not live'} aria-expanded={explain}
                  onClick={() => setExplain((x) => !x)}>
            <StatusDot tone={live ? 'brand' : 'idle'} live={live}/>
          </button>
        </div>
        {unpaired && (
          <button type="button" className="home-link" onClick={() => { onPair(); }}>
            pair this phone for live updates and controls
          </button>
        )}
        {explain && <div className="home-explain">{why}</div>}
      </div>
      <div className="actions home-desktop-only">
        <button type="button" className="home-live" title={why} aria-expanded={explain}
                onClick={() => setExplain((x) => !x)}>
          <StatusDot tone={live ? 'brand' : 'idle'} live={live}/>
          <span>{live ? 'live' : 'not live · updates every 30s'}</span>
        </button>
      </div>
    </div>
  );
};

/* ---- needs attention ----------------------------------------------- */

const HomeAttention = ({ view, viewer, shared, checking, failed, refused, checkedAt, onClaim, onSignIn }) => {
  const [expanded, setExpanded] = React.useState(false);
  // An admin read refused (401/403): the in-memory sign-in is gone or
  // stale, and every admin check behind it is failing quietly.
  const signInAgain = refused && (
    <> · <button type="button" className="home-link" onClick={() => { onSignIn(); }}>sign in again</button></>
  );
  const couldnt = failed.length > 0 && (
    <div className="home-att-quiet">couldn't check {failed.join(', ')}{signInAgain}</div>
  );
  const signInHint = !viewer.isAdmin && !shared && viewer.known && (
    <div className="home-att-foot home-desktop-only">
      <button type="button" className="home-link" onClick={() => { onSignIn(); }}>
        sign in for approvals and system checks
      </button>
    </div>
  );

  if (view.mode === 'summary') {
    return (
      <div className="home-sec home-sec-attention">
        <Card title="needs attention">
          <div className="home-att-row">
            <StatusDot tone="idle"/>
            <span className="txt">something needs the admin's attention</span>
            <span className="when">{view.count}</span>
          </div>
          {signInHint}
        </Card>
      </div>
    );
  }
  if (view.mode !== 'rows') return null;

  const rows = view.rows;
  if (!rows.length) {
    // The household view hides when empty: the header counts already say
    // it. Only an admin gets the explicit all-clear — and never a false
    // one: "checking…" until every source has answered once.
    if (!viewer.isAdmin || shared) return null;
    let quiet = null;
    if (checking) quiet = <div className="home-att-quiet">checking…</div>;
    else if (failed.length) quiet = couldnt;
    else {
      quiet = (
        <div className="home-att-quiet">
          nothing needs you · checked {relTime(new Date(checkedAt || Date.now()).toISOString())}
        </div>
      );
    }
    return (
      <div className="home-sec home-sec-attention">
        <Card title="needs attention">{quiet}</Card>
      </div>
    );
  }

  const extra = rows.length - HOME_PHONE_ROWS;
  // "+N more" rides the card header, not a footer row of its own: on a
  // phone every row above the rooms is a row the rooms start below.
  const more = extra > 0 && !expanded && (
    <button type="button" className="home-link home-phone-only" onClick={() => setExpanded(true)}>+{extra} more</button>
  );
  return (
    <div className="home-sec home-sec-attention">
      <Card title="needs attention" action={more}>
        {rows.map((r, i) => {
          const cls = `home-att-row${i >= HOME_PHONE_ROWS && !expanded ? ' home-phone-extra' : ''}`;
          const body = (
            <>
              <StatusDot tone={r.tone}/>
              <span className="txt">{r.text}</span>
              {r.at && <span className="when">{relTime(r.at)}</span>}
            </>
          );
          if (r.claim) {
            return (
              <div key={r.key} className={`${cls} home-att-claim`} data-key={r.key}>
                {body}
                <span className="home-att-acts">
                  <a href="#manual" className="home-link">how domovoi works</a>
                  <Button variant="primary" icon="key" onClick={() => { onClaim(); }}>claim it</Button>
                </span>
              </div>
            );
          }
          return <a key={r.key} className={cls} data-key={r.key} href={r.href}>{body}</a>;
        })}
        {checking && <div className="home-att-quiet">checking…</div>}
        {/* A problem row must not hide that a check behind it failed: a
            disk filling up says nothing while the hardware read is down. */}
        {!checking && viewer.isAdmin && !shared && couldnt}
        {signInHint}
      </Card>
    </div>
  );
};

/* ---- timers -------------------------------------------------------- */

const HomeTimerRow = ({ t, now, shared, roomOnline, busy, extra, onCancel }) => {
  const at = Date.parse(t.expires_at);
  const created = Date.parse(t.created_at);
  const left = Math.max(0, Math.round((at - now) / 1000));
  const span = at - created;
  const pct = span > 0 ? Math.min(100, Math.max(0, ((now - created) / span) * 100)) : 0;
  const title = HomeTimerTitle(t, shared);
  return (
    <div className={`home-timer${extra ? ' home-phone-extra' : ''}`} data-timer={t.id}>
      <div className="home-timer-main">
        {t.room_id ? <RoomChip name={t.room_id} online={roomOnline}/> : <span className="room-chip">no room</span>}
        <span className="home-timer-title">{title}</span>
      </div>
      <span className={`home-timer-left${left < HOME_SOON_SEC ? ' soon' : ''}`}>{HomeFmtLeft(left)}</span>
      <Button icon="x" title={`cancel ${HomeTimerNoun(t, shared)}`} disabled={busy}
              onClick={() => { onCancel(t); }}>{busy ? 'cancelling…' : 'cancel'}</Button>
      <div className="home-bar" aria-hidden="true"><span style={{ width: `${pct}%` }}/></div>
    </div>
  );
};

const HomeTimers = ({ active, done, now, shared, onlineRooms, cancelling, onCancel }) => {
  const [expanded, setExpanded] = React.useState(false);
  if (!active.length && !done.length) return null;
  const extra = active.length - HOME_PHONE_TIMERS;
  const more = extra > 0 && !expanded && (
    <button type="button" className="home-link home-phone-only" onClick={() => setExpanded(true)}>+{extra} more</button>
  );
  return (
    <div className="home-sec home-sec-timers">
      <Card title="timers" action={more}>
        {active.map((t, i) => (
          <HomeTimerRow key={t.id} t={t} now={now} shared={shared}
                        roomOnline={onlineRooms.has(t.room_id)} busy={cancelling.has(t.id)}
                        extra={i >= HOME_PHONE_TIMERS && !expanded} onCancel={onCancel}/>
        ))}
        {done.map((d) => (
          <div key={`done-${d.id}`} className="home-timer-done" data-done={d.id}>
            <StatusDot tone="ok"/>
            <span>done · {d.room_id || 'no room'}</span>
            {!(shared && d.is_reminder) && <span className="meta">{HomeTimerTitle(d, shared)}</span>}
          </div>
        ))}
      </Card>
    </div>
  );
};

/* ---- rooms --------------------------------------------------------- */

// Playing, then paused, then online and quiet, then waiting, then offline.
const HomeRoomRank = (s) => {
  const np = s.now_playing;
  if (s.status === 'online' && np && np.song && np.state === 'play') return 0;
  if (s.status === 'online' && np && np.song && np.state === 'pause') return 1;
  if (s.status === 'online') return 2;
  if (s.status === 'waiting') return 3;
  return 4;
};

/* One room. Not SatCard: that is a single <button>, and a button can't
 * hold the transport buttons. The left part is a link to Satellites; the
 * buttons sit apart on the right edge, in thumb reach on a phone. */
const HomeRoomRow = ({ s, stale, sinceFetchSec, nextTimerLeft, busy, onAct, onPlay }) => {
  const online = s.status === 'online';
  const np = s.now_playing;
  const song = online && np && np.song ? np.song : null;
  const playing = !!song && np.state === 'play';
  const paused = !!song && np.state === 'pause';
  const dur = song && song.duration_sec ? song.duration_sec : 0;
  // A stale read (the core is down) is where it was, not where it would be.
  // Never past the song's end: between a song ending and the read that
  // brings the next one, the count used to run on ("0:14 / 0:13").
  const ran = song ? (np.elapsed_sec || 0) + (playing && !stale ? sinceFetchSec : 0) : 0;
  const elapsed = dur ? Math.min(ran, dur) : ran;
  const progress = dur ? Math.min(100, (elapsed / dur) * 100) : 0;
  const rx = s.wifi && s.wifi.rx_mbits;
  const weakWifi = online && rx != null && window.wifiTone && window.wifiTone(rx) === 'err';
  const kioskDead = online && s.sat_type === 'video' && s.display && s.display.kiosk_alive === false;
  const canAct = online && !stale && !busy;

  let pill = null;
  if (stale) pill = <Pill tone="idle">last known</Pill>;
  else if (playing) pill = <Pill tone="live" live>playing</Pill>;
  else if (paused) pill = <Pill tone="idle">paused</Pill>;
  else if (s.status === 'waiting') pill = <Pill tone="warn">waiting</Pill>;
  else if (!online) pill = <Pill tone="idle">offline</Pill>;

  let body;
  if (song) {
    body = (
      <>
        <div className="home-room-np">
          <span className="t">{HomeSongTitle(song)}</span>
          {song.artist && <span> · {song.artist}</span>}
        </div>
        <div className="home-room-prog">
          <div className="bar"><span style={{ width: `${progress}%` }}/></div>
          <span className="mono">{fmtDur(elapsed)}{dur ? ` / ${fmtDur(dur)}` : ''}</span>
        </div>
      </>
    );
  } else if (online) {
    body = <div className="home-room-np faint">quiet</div>;
  } else if (s.status === 'waiting') {
    body = <div className="home-room-np faint">set up, not connected yet</div>;
  } else {
    body = (
      <div className="home-room-np faint">
        {s.last_connected_at ? `last seen ${relTime(s.last_connected_at)}` : 'never connected'}
      </div>
    );
  }

  const chips = [];
  if (nextTimerLeft != null) {
    chips.push(<span key="timer" className="home-chip"><Icon name="timer" size={12}/>{HomeFmtLeft(nextTimerLeft)}</span>);
  }
  if (online && s.in_call_with) {
    chips.push(<span key="call" className="home-chip"><Icon name="phone" size={12}/>in call with {s.in_call_with}</span>);
  }
  if (kioskDead) chips.push(<Pill key="kiosk" tone="warn">screen stopped</Pill>);
  if (weakWifi) chips.push(<span key="wifi" className="home-chip"><Icon name="wifi-off" size={12}/>weak wi-fi</span>);
  // Opt-in command recording (V016): shown to everyone, on every screen —
  // a room that records says so where people look at it.
  if (s.capture_commands) chips.push(<CaptureChip key="capture"/>);

  let actions = null;
  if (online && !stale) {
    if (playing) {
      actions = (
        <>
          <IconButton name="pause" title={`pause ${s.room_id}`} disabled={!canAct} onClick={() => { onAct(s.room_id, 'pause'); }}/>
          <IconButton name="square" title={`stop ${s.room_id}`} disabled={!canAct} onClick={() => { onAct(s.room_id, 'stop'); }}/>
        </>
      );
    } else if (paused) {
      actions = (
        <>
          <IconButton name="play" title={`resume ${s.room_id}`} disabled={!canAct} onClick={() => { onAct(s.room_id, 'resume'); }}/>
          <IconButton name="square" title={`stop ${s.room_id}`} disabled={!canAct} onClick={() => { onAct(s.room_id, 'stop'); }}/>
        </>
      );
    } else {
      actions = <Button icon="play" title={`play in ${s.room_id}`} disabled={!canAct} onClick={() => { onPlay(s.room_id); }}>play</Button>;
    }
  }

  return (
    <div className={`home-room${online && !stale ? '' : ' dim'}`} data-room={s.room_id}>
      <a className="home-room-main" href="#satellites" title={`${s.room_id} on the satellites page`}>
        <div className="home-room-top">
          <StatusDot tone={online ? 'ok' : s.status === 'waiting' ? 'warn' : 'idle'} live={online && !stale}/>
          <span className="home-room-name">{s.room_id}</span>
          {pill}
        </div>
        {body}
        {chips.length > 0 && <div className="home-room-chips">{chips}</div>}
      </a>
      {actions && <div className="home-room-actions">{actions}</div>}
    </div>
  );
};

// "stop all" touches other people's rooms, so it always takes a second tap.
const HomeStopAll = ({ count, busy, onConfirm }) => {
  const [armed, setArmed] = React.useState(false);
  React.useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), HOME_STOP_ALL_ARM_MS);
    return () => clearTimeout(t);
  }, [armed]);
  if (busy) return <Button variant="secondary" icon="square" disabled>stopping…</Button>;
  return (
    <Button variant={armed ? 'primary' : 'secondary'} icon="square"
            onClick={() => {
              if (!armed) { setArmed(true); return; }
              setArmed(false);
              onConfirm();
            }}>
      {armed ? `stop ${count} rooms?` : 'stop all'}
    </Button>
  );
};

const HomeRooms = ({ rooms, answered, failed, dbDown, stale, fetchedAt, timerLeftByRoom, busy, stoppingAll,
                     onAct, onPlay, onStopAll }) => {
  const playing = stale ? [] : rooms.filter((s) => HomeRoomRank(s) === 0);
  const head = (
    <div className="home-sec-head">
      <span className="label">rooms</span>
      {(playing.length >= 2 || stoppingAll) && (
        <HomeStopAll count={playing.length} busy={stoppingAll}
                     onConfirm={() => onStopAll(playing.map((s) => s.room_id))}/>
      )}
    </div>
  );
  let content;
  if (!answered) {
    content = <div className="home-rooms"><div className="home-room home-room-skel" aria-hidden="true"/></div>;
  } else if (failed) {
    // Say the cause when health knows it; otherwise just that it failed.
    content = (
      <Card>
        <div className="home-att-quiet">
          {dbDown ? "rooms unavailable · the database isn't answering" : "couldn't load rooms"}
        </div>
      </Card>
    );
  } else if (!rooms.length) {
    content = (
      <Card>
        <Empty glyph="sleeping" title="no rooms yet"
               action={<a href="#satellites" className="home-link">add a satellite</a>}/>
      </Card>
    );
  } else {
    const sorted = [...rooms].sort((a, b) => (HomeRoomRank(a) - HomeRoomRank(b))
      || String(a.room_id).localeCompare(String(b.room_id)));
    const labels = [...new Set(sorted.map((s) => s.room_label).filter(Boolean))].sort();
    // Local clock on both sides: elapsed_sec was true when THIS browser
    // received it, whatever the server's clock says.
    const since = Math.max(0, (Date.now() - fetchedAt) / 1000);
    const grid = (list) => (
      <div className="home-rooms">
        {list.map((s) => (
          <HomeRoomRow key={s.room_id} s={s} stale={stale} sinceFetchSec={since}
                       nextTimerLeft={timerLeftByRoom[s.room_id]}
                       busy={!!busy[s.room_id]} onAct={onAct} onPlay={onPlay}/>
        ))}
      </div>
    );
    content = labels.length === 0 ? grid(sorted) : (
      <>
        {labels.map((label) => (
          <div key={label} className="home-room-group">
            <div className="home-group-label">{label}</div>
            {grid(sorted.filter((s) => s.room_label === label))}
          </div>
        ))}
        {sorted.some((s) => !s.room_label) && (
          <div className="home-room-group">
            <div className="home-group-label">ungrouped</div>
            {grid(sorted.filter((s) => !s.room_label))}
          </div>
        )}
      </>
    );
  }
  return <div className="home-sec home-sec-rooms">{head}{content}</div>;
};

/* A quiet room's "play": favorites, shuffled (playlist 0 is the virtual
 * Favorites list), or a way to Music to pick something. A bottom sheet on
 * a phone, a small dialog on a desktop. */
const HomePlaySheet = ({ room, onClose, onFavorites }) => (
  <div className="home-sheet-bg" onClick={onClose}>
    <div className="home-sheet" role="dialog" aria-label={`play in ${room}`} onClick={(e) => e.stopPropagation()}>
      <div className="home-sheet-head">
        <span>play in {room}</span>
        <IconButton name="x" title="close" onClick={onClose}/>
      </div>
      <button type="button" className="home-sheet-opt" onClick={() => { onFavorites(room); }}>
        <Icon name="shuffle" size={16}/><span>favorites · shuffle</span>
      </button>
      <a className="home-sheet-opt" href="#music" onClick={onClose}>
        <Icon name="music" size={16}/><span>pick something in music</span>
      </a>
    </div>
  </div>
);

/* ---- today --------------------------------------------------------- */

/* Today's and tomorrow's events: time, title, place, a "now" pill while
 * one is running. Never the description. A shared screen gets the times
 * and "busy" only. Read from local midnight, because the server filters
 * on starts_at and an event already under way must still show. */
const HomeToday = ({ events, answered, failed, now, shared }) => {
  const list = Array.isArray(events) ? events : [];
  const d1 = HomeDayStart(now, 1);
  const d2 = HomeDayStart(now, 2);
  const endOf = (e) => (e.ends_at ? Date.parse(e.ends_at) : Date.parse(e.starts_at) + 3600 * 1000);
  const sorted = [...list].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at));
  const soon = sorted.filter((e) => Date.parse(e.starts_at) < d2 && endOf(e) >= now);
  const shown = soon.slice(0, HOME_TODAY_ROWS);
  const titleOf = (e) => (shared ? 'busy' : e.title);

  let content;
  if (!answered) {
    content = <div className="home-att-quiet">checking…</div>;
  } else if (failed) {
    content = <div className="home-att-quiet">calendar unavailable</div>;
  } else if (!shown.length) {
    const next = sorted.find((e) => Date.parse(e.starts_at) >= d2);
    const earlier = sorted.some((e) => Date.parse(e.starts_at) < d1 && endOf(e) < now);
    const lead = earlier ? 'nothing more today' : 'nothing on today';
    content = (
      <div className="home-att-quiet">
        {next
          ? `${lead} · next: ${(window.fmtDayLabel ? window.fmtDayLabel(new Date(Date.parse(next.starts_at))) : HomeFmtDate(Date.parse(next.starts_at)))} ${HomeClock(next.starts_at)} ${titleOf(next)}`
          : lead}
      </div>
    );
  } else {
    const days = [
      { key: 'today', label: 'today', rows: shown.filter((e) => Date.parse(e.starts_at) < d1) },
      { key: 'tomorrow', label: 'tomorrow', rows: shown.filter((e) => Date.parse(e.starts_at) >= d1) },
    ].filter((d) => d.rows.length);
    let n = 0;
    content = (
      <div className="home-today">
        {days.map((d) => (
          <div key={d.key} className="home-today-day">
            <div className="home-group-label">{d.label}</div>
            {d.rows.map((e) => {
              const idx = n++;
              const running = Date.parse(e.starts_at) <= now && now < endOf(e);
              return (
                <a key={e.id} href="#calendar" data-event={e.id}
                   className={`cal-mlist-row home-today-row${idx >= HOME_PHONE_ROWS ? ' home-phone-extra' : ''}`}>
                  <div className="time mono">
                    <div>{HomeClock(e.starts_at)}</div>
                    {e.ends_at && <div className="end">{HomeClock(e.ends_at)}</div>}
                  </div>
                  <div className="body">
                    <div className="ti">{titleOf(e)}</div>
                    {!shared && e.location && (
                      <div className="meta"><span><Icon name="map-pin" size={11}/> {e.location}</span></div>
                    )}
                  </div>
                  {running ? <Pill tone="live" live>now</Pill> : <span/>}
                </a>
              );
            })}
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="home-sec home-sec-today">
      <Card title="today" action={<a href="#calendar" className="home-link">calendar</a>}>{content}</Card>
    </div>
  );
};

/* ---- first run ----------------------------------------------------- */

// An example phrase from the user manual's feature table
// (/api/capabilities/manual), the timer's first.
const HomeHintPhrase = (manual) => {
  const handlers = (manual && Array.isArray(manual.handlers)) ? manual.handlers : [];
  for (const name of HOME_HINT_HANDLERS) {
    const h = handlers.find((x) => x.name === name);
    if (h && (h.example_phrases || []).length) return h.example_phrases[0];
  }
  const any = handlers.find((x) => (x.example_phrases || []).length);
  return any ? any.example_phrases[0] : HOME_HINT_FALLBACK;
};

const HomeFirstRun = ({ manual }) => (
  <div className="home-firstrun">
    <span className="lab">try saying</span>
    <span className="mono">“{HomeHintPhrase(manual)}”</span>
    <a href="#manual" className="home-link">how domovoi works</a>
  </div>
);

/* ---- everything (phones) -------------------------------------------- */

/* Every page that is not one of the phone strip's five tabs — plugin
 * pages included, with their badges — plus Settings and the manual. The
 * phone's "more" menu. `counts` and `badges` are App's one
 * useSidebarCounts and usePluginBadges results, handed down rather than
 * fetched again (a grid rendered without `badges` polls for itself). A
 * shared screen leaves the same pages off as the sidebar and the strip
 * (navItemsFor). On a desktop the sidebar already lists everything and
 * this is not mounted. */
const HomeEverything = ({ counts, badges: given, shared }) => {
  const manifest = window.DomovoiPluginManifest || { plugins: [] };
  const own = usePluginBadges(given ? null : manifest);
  const badges = given || own;
  const c = counts || {};
  const tiles = navItemsFor(manifest, { shared })
    .filter((it) => !it.primary)
    .concat(HOME_EXTRA_TILES);
  return (
    <div className="home-sec home-sec-everything">
      <Card title="everything">
        <div className="home-grid">
          {tiles.map((it) => {
            const badge = it.core ? (it.countKey ? c[it.countKey] : null) : badges[it.route];
            return (
              <a key={it.route} href={`#${it.route}`} className="home-tile">
                {it.core ? <Icon name={it.icon} size={18}/> : <PluginNavIcon src={it.iconSrc}/>}
                <span className="lab">{it.label}</span>
                {badge != null && <span className="home-tile-badge">{badge}</span>}
              </a>
            );
          })}
        </div>
      </Card>
    </div>
  );
};

/* ---- the page ------------------------------------------------------- */

const HomePage = ({ counts, badges }) => {
  const [fire, toastNode] = useToast();
  const viewer = HomeHooks.useViewer();
  const live = HomeHooks.useLive();
  const shared = useSharedScreen();
  const isPhone = HomeHooks.useIsPhone();
  const admin = viewer.isAdmin;

  // Open reads, all quiet.
  const cfg = useApiObject('/api/config', HOME_QUIET);
  const health = useApiObject('/api/health', HOME_QUIET);
  const sats = useApiObject('/api/satellites', HOME_QUIET);
  const timers = useApiObject('/api/timers', { eventTypes: ['timers.changed'], quiet: true });
  const plugins = useApiObject('/api/plugins', { eventTypes: ['plugins.changed'], quiet: true });
  const acq = useApiObject('/api/acquisitions?status=pending&limit=100',
                           { eventTypes: ['acquisitions.changed'], quiet: true });
  const dayStart = HomeDayStart(Date.now());
  const calPath = React.useMemo(() => {
    const start = new Date(dayStart).toISOString();
    const end = new Date(HomeDayStart(dayStart, 7)).toISOString();
    return `/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&limit=20`;
  }, [dayStart]);
  // Not the WS payload: its window starts at now(), so an event already
  // under way drops out of it. The push only says "re-read".
  const cal = useApiObject(calPath, { eventTypes: ['calendar.events.changed'], quiet: true });

  // Admin-only reads: never issued for anyone else (a refused GET used to
  // pop the login modal on landing; quiet is the second belt).
  // The costly ones (and the manual, below) survive a re-mount for as
  // long as their own poll would have waited (HOME_CACHE).
  const approvals = HomeHooks.useCachedObject(admin ? '/api/satellites/approvals' : null, HOME_APPROVALS_MS);
  const pending = useApiObject(admin ? '/api/satellites/pending' : null,
                               { eventTypes: ['satellites.pending.changed'], quiet: true });
  const version = HomeHooks.useCachedObject(admin ? '/api/config/version' : null, HOME_VERSION_GAP_MS);
  const hardware = HomeHooks.useCachedObject(admin ? '/api/models/hardware' : null, HOME_HARDWARE_MS);

  // When the rooms and the timers were last read (each read is a new object).
  const satsAt = React.useMemo(() => Date.now(), [sats.data]);
  const timersAt = React.useMemo(() => Date.now(), [timers.data]);

  // Rooms: a push re-reads, debounced; a dead socket polls instead. A
  // push while the tab is hidden only marks them stale — the focus handler
  // re-reads once — because every /api/satellites read opens an MPD
  // connection per room. A Wi-Fi report carries the whole new state, and
  // every room sends one a minute, so it is merged in, never re-read.
  const refetchRooms = HomeHooks.useDebounced(() => sats.refresh(), HOME_ROOM_DEBOUNCE_MS);
  const roomsDirty = React.useRef(false);
  const [wifiPush, setWifiPush] = React.useState(null);   // { at, map: {room: wifi} }
  useStateEvents(HOME_ROOM_EVENTS, (ev) => {
    if (ev.type === 'satellites.wifi.changed') {
      if (ev.data && typeof ev.data === 'object') setWifiPush({ at: Date.now(), map: ev.data });
      return;
    }
    if (HomeHooks.tabHidden()) { roomsDirty.current = true; return; }
    refetchRooms();
  });
  // Back from a real gap, re-read what the pushes would have said. Not on
  // the socket's FIRST open, a moment after the mount's own reads: what
  // was read under HOME_FRESH_MS ago needs no second fan-out.
  const wasLive = React.useRef(live);
  React.useEffect(() => {
    if (live && !wasLive.current) {
      if (Date.now() - satsAt >= HOME_FRESH_MS) sats.refresh();
      if (Date.now() - timersAt >= HOME_FRESH_MS) timers.refresh();
    }
    wasLive.current = live;
  }, [live]);
  HomeHooks.useInterval(() => { sats.refresh(); timers.refresh(); }, HOME_LIVE_POLL_MS, !live);
  // The problem-rows setting rides the health tick: a wall tablet that
  // never loses focus still picks up an admin's change within a minute.
  // (Whether this device is a shared screen, DeviceIdentity.boot re-asks.)
  HomeHooks.useInterval(() => { health.refresh(); cfg.refresh(); }, HOME_HEALTH_MS);
  HomeHooks.useInterval(() => approvals.refresh(), HOME_APPROVALS_MS, admin);
  HomeHooks.useInterval(() => hardware.refresh(), HOME_HARDWARE_MS, admin);
  const versionAt = React.useRef(0);
  React.useEffect(() => { if (admin) versionAt.current = Date.now(); }, [admin]);
  HomeHooks.useOnFocus(() => {
    cfg.refresh(); health.refresh(); cal.refresh();
    if (!live) { roomsDirty.current = false; sats.refresh(); timers.refresh(); }
    else if (roomsDirty.current) { roomsDirty.current = false; refetchRooms(); }
    if (admin && Date.now() - versionAt.current > HOME_VERSION_GAP_MS) {
      versionAt.current = Date.now();
      version.refresh();
    }
  });

  // Browser-side plugin errors (index.html's pipeline): re-render on news.
  const [, bumpPluginErrors] = React.useReducer((n) => n + 1, 0);
  React.useEffect(() => {
    const pe = window.DomovoiPluginErrors;
    if (!pe || !pe.subscribe) return undefined;
    return pe.subscribe(bumpPluginErrors);
  }, []);

  // ── derived ──
  const coreDown = !!(health.data && health.data.domovoi_reachable === false);
  const dbDown = !!(health.data && health.data.db_reachable === false);
  // A Wi-Fi push newer than the last read wins for rx/tx; nothing else.
  const wifiNow = wifiPush && wifiPush.at >= satsAt ? wifiPush.map : null;
  const rooms = (Array.isArray(sats.data) ? sats.data : []).map((s) => {
    const w = wifiNow && wifiNow[s.room_id];
    if (!w || typeof w !== 'object') return s;
    return { ...s, wifi: { ...(s.wifi || {}), rx_mbits: w.rx_mbits, tx_mbits: w.tx_mbits } };
  });
  const cancelled = React.useRef(new Set());
  const { active, done, now } = HomeHooks.useTimers(timers.data, cancelled);
  const online = rooms.filter((s) => s.status === 'online');
  const playingCount = coreDown ? 0 : rooms.filter((s) => HomeRoomRank(s) === 0).length;
  HomeHooks.useTick(active.length > 0 || done.length > 0 || playingCount > 0);
  const onlineRooms = new Set(online.map((s) => s.room_id));
  const timerLeftByRoom = {};
  active.forEach((t) => {
    if (t.room_id && timerLeftByRoom[t.room_id] == null) {
      timerLeftByRoom[t.room_id] = Math.max(0, Math.round((Date.parse(t.expires_at) - now) / 1000));
    }
  });

  // The status line's counts, from the sections below — no extra fetch.
  const line = [];
  if (HomeOk(sats)) {
    if (!rooms.length) line.push('no rooms yet');
    else {
      // With the core down these are what it said last, not what is true.
      if (coreDown) line.push('last known');
      line.push(`${online.length} online`);
      const off = rooms.filter((s) => s.status === 'offline').length;
      const wait = rooms.filter((s) => s.status === 'waiting').length;
      if (off) line.push(`${off} offline`);
      if (wait) line.push(`${wait} waiting`);
      if (playingCount) line.push(`${playingCount} playing`);
    }
  }
  // The database answers these, not the rooms read: they count on their own.
  const nTimers = active.filter((t) => !t.is_reminder).length;
  const nReminders = active.length - nTimers;
  if (nTimers) line.push(HomePlural(nTimers, 'timer'));
  if (nReminders) line.push(HomePlural(nReminders, 'reminder'));

  // Needs attention.
  const sources = [
    ['server', health], ['rooms', sats], ['plugins', plugins], ['media requests', acq],
    ...(admin ? [['approvals', approvals], ['adoption', pending], ['updates', version], ['disk', hardware]]
              : [['settings', cfg]]),
  ];
  const checking = sources.some(([, r]) => !HomeAnswered(r));
  const failed = sources.filter(([, r]) => HomeAnswered(r) && !HomeOk(r)).map(([n]) => n);
  const refused = admin && sources.some(([, r]) => !HomeOk(r) && r.error
    && (r.error.status === 401 || r.error.status === 403));
  const rows = HomeAttentionRows({
    viewer, health: health.data, rooms, plugins: plugins.data,
    pluginErrors: (window.DomovoiPluginErrors && window.DomovoiPluginErrors._bySlug) || {},
    acq: acq.data, approvals: approvals.data, pending: pending.data,
    version: version.data, hardware: hardware.data,
  });
  const visibility = (cfg.data && cfg.data.home_problems_visibility) || 'everyone';
  // A household member's rows wait for the setting that decides them, so
  // an "admins only" house never flashes them while /api/config loads.
  const view = (!admin && !HomeAnswered(cfg)) ? { mode: 'none' }
    : HomeAttentionView({ rows, viewer, visibility, shared });
  const [checkedAt, setCheckedAt] = React.useState(null);
  React.useEffect(() => { if (!checking) setCheckedAt(Date.now()); },
    [checking, health.data, sats.data, plugins.data, acq.data, approvals.data, pending.data,
     version.data, hardware.data]);

  // First run: a house with nothing in it yet gets one hint, taken from
  // the manual. Only then is the manual asked for.
  const firstRun = HomeOk(sats) && !rooms.length && HomeOk(timers) && !active.length
    && HomeOk(cal) && !(cal.data || []).length;
  const manual = HomeHooks.useCachedObject(firstRun ? '/api/capabilities/manual' : null, HOME_MANUAL_MS);

  // ── actions (device tier; data.js prompts and replays when unpaired) ──
  const [busy, setBusy] = React.useState({});
  const [sheetRoom, setSheetRoom] = React.useState(null);
  const [stoppingAll, setStoppingAll] = React.useState(false);
  const [cancelling, setCancelling] = React.useState(() => new Set());
  // The guards read refs, not render state: two taps inside one batch of
  // events would both see the state from before either of them.
  const stoppingRef = React.useRef(false);
  const cancellingRef = React.useRef(new Set());
  const setRoomBusy = (room, on) => setBusy((b) => ({ ...b, [room]: on }));
  const setRoomsBusy = (list, on) => setBusy((b) => {
    const next = { ...b };
    list.forEach((room) => { next[room] = on; });
    return next;
  });

  const onAct = async (room, verb) => {
    setRoomBusy(room, true);
    try {
      await apiPost(`/api/music/${verb}/${encodeURIComponent(room)}`);
    } catch (e) {
      reportMutationFailure(fire, verb, e);
    } finally {
      setRoomBusy(room, false);
      refetchRooms();
    }
  };

  const onFavorites = async (room) => {
    setSheetRoom(null);
    setRoomBusy(room, true);
    try {
      await apiPost('/api/music/play-playlist', { room_id: room, playlist_id: 0, shuffle: true });
      fire(`playing favorites in ${room}`);
    } catch (e) {
      reportMutationFailure(fire, 'play', e);
    } finally {
      setRoomBusy(room, false);
      refetchRooms();
    }
  };

  // Every room in the batch is busy until the whole batch settles, so no
  // pause or stop can land in the middle of it and "stop all" can't fire twice.
  const onStopAll = async (list) => {
    if (stoppingRef.current) return;
    stoppingRef.current = true;
    setStoppingAll(true);
    setRoomsBusy(list, true);
    try {
      const results = await Promise.allSettled(
        list.map((room) => apiPost(`/api/music/stop/${encodeURIComponent(room)}`)));
      const bad = results.filter((r) => r.status === 'rejected');
      if (!bad.length) fire(`stopped ${HomePlural(list.length, 'room')}`);
      else {
        const said = mutationErrorText(bad[0].reason, 'stop', { kept: false });
        if (bad.length < list.length) fire(`stopped ${list.length - bad.length} of ${list.length} rooms${said ? ` · ${said}` : ''}`);
        else if (said) fire(said);
      }
    } finally {
      setRoomsBusy(list, false);
      stoppingRef.current = false;
      setStoppingAll(false);
      refetchRooms();
    }
  };

  // One DELETE per timer at a time: a double tap must not send a second
  // one and toast "that one already finished" over "cancelled".
  const onCancel = async (t) => {
    if (cancellingRef.current.has(t.id)) return;
    cancellingRef.current.add(t.id);
    setCancelling((c) => new Set(c).add(t.id));
    cancelled.current.add(t.id);
    try {
      await apiDelete(`/api/timers/${t.id}`);
      fire(`cancelled ${HomeTimerNoun(t, shared)}`);
    } catch (e) {
      cancelled.current.delete(t.id);
      if (e && e.status === 404) fire('that one already finished');
      else reportMutationFailure(fire, 'cancel', e);
    } finally {
      cancellingRef.current.delete(t.id);
      setCancelling((c) => { const next = new Set(c); next.delete(t.id); return next; });
      timers.refresh();
    }
  };

  const onPair = () => { try { Auth.requestPairing(); } catch {} };
  const onSignIn = () => { try { Auth.requestLogin(); } catch {} };

  const name = (cfg.data && cfg.data.bot_name) || 'domovoi';
  const Broadcast = window.Broadcast;

  return (
    <div className="page home">
      <HomeHeader name={name} nowMs={now} line={line} lineReady={HomeAnswered(sats)} live={live}
                  viewer={viewer} onPair={onPair}/>
      {firstRun && HomeAnswered(manual) && <HomeFirstRun manual={manual.data}/>}
      <div className="home-cols">
        <div className="home-col">
          <HomeRooms rooms={rooms} answered={HomeAnswered(sats)} failed={HomeAnswered(sats) && !HomeOk(sats)}
                     dbDown={dbDown} stale={coreDown} fetchedAt={satsAt} timerLeftByRoom={timerLeftByRoom}
                     busy={busy} stoppingAll={stoppingAll}
                     onAct={onAct} onPlay={setSheetRoom} onStopAll={onStopAll}/>
          <div className="home-pair">
            <HomeTimers active={active} done={done} now={now} shared={shared}
                        onlineRooms={onlineRooms} cancelling={cancelling} onCancel={onCancel}/>
            <HomeToday events={cal.data} answered={HomeAnswered(cal)}
                       failed={HomeAnswered(cal) && !HomeOk(cal)} now={now} shared={shared}/>
          </div>
        </div>
        <div className="home-col">
          <HomeAttention view={view} viewer={viewer} shared={shared} checking={checking}
                         failed={failed} refused={refused} checkedAt={checkedAt}
                         onClaim={onSignIn} onSignIn={onSignIn}/>
          {Broadcast && (
            <div className="home-sec home-sec-announce">
              <Broadcast compact onlineCount={coreDown ? 0 : online.length} fire={fire}/>
            </div>
          )}
          {isPhone !== false && <HomeEverything counts={counts} badges={badges} shared={shared}/>}
        </div>
      </div>
      {sheetRoom && <HomePlaySheet room={sheetRoom} onClose={() => setSheetRoom(null)} onFavorites={onFavorites}/>}
      {toastNode}
    </div>
  );
};

window.HomePage = HomePage;
