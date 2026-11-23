/* Home — the dashboard's default page (#home): the house at a glance.
 *
 * "What is the house doing right now, and does anything need me?",
 * answered in about five seconds on a phone (design-notes HOME-PLAN.md,
 * approved 2026-09-26). On a phone, top to bottom:
 *
 *   status line      household name, date, "3 online · 1 offline · 2 playing
 *                    · 1 timer", the live dot, "Connect this browser"
 *   needs attention  one line per problem, collapsed by kind
 *   timers           every timer and reminder in the house, counting down
 *   rooms            one row per room: pause / resume / stop / play
 *   announce         say something in every room
 *   today            today's and tomorrow's events
 *   photos chip      "Understanding your photos: 3,210 of 20,000" while the
 *                    photos-and-videos backlog runs (household, read-only;
 *                    links to Settings → Chat & AI → Photos and videos)
 *   everything       every page that is not on this phone's bar (and a
 *                    link to pick the bar's tabs)
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
 * Settings → Phones & browsers) also hides calendar titles, reminder text and the
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
const HOME_PHONE_ROWS = 3;                 // attention rows and today rows a phone shows
const HOME_TODAY_ROWS = 6;                 // ...and a desktop
const HOME_PHONE_TIMERS = 2;               // timers a phone shows, so the rooms start above the fold
const HOME_FRESH_MS = 5 * 1000;            // a read this young needs no re-read on (re)connect
const HOME_MANUAL_MS = 30 * 60 * 1000;     // the manual's words hardly change
const HOME_SOON_SEC = 600;                 // countdowns turn warn under 10 min
const HOME_ROOM_EVENTS = [
  'satellites.presence.changed', 'satellites.wifi.changed', 'satellites.dropins.changed',
  'satellites.display.changed', 'satellites.pending.changed', 'music.now_playing.changed', 'room_battery.changed',
];
const HOME_WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
const HOME_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
// The first-run hint is the user manual's Start here (manual-content.json,
// the first "try saying" phrase there); this when the manual can't be read.
// Both work on a fresh install: a timer, by its fast path, offline.
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
  // (health.jsx healthViewer: Health asks the same question.)
  const whoIsLooking = () => healthViewer();

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
   * ones that just went off. "done · kitchen" comes from the server's
   * fire history (`fires`, V018) — never from a row vanishing, which is
   * what a cancel looks like too — and lingers a minute after the fire.
   * No `fires` (an older server, V018 not applied): no done lines.
   * `cancelledRef` holds the rows this page has cancelled; they leave the
   * countdown before the re-read confirms it. */
  const useTimers = (data, cancelledRef) => {
    const list = (data && Array.isArray(data.timers)) ? data.timers : [];
    const fires = (data && Array.isArray(data.fires)) ? data.fires : [];
    const offset = React.useMemo(() => {
      const s = data && Date.parse(data.server_now);
      return Number.isFinite(s) ? s - Date.now() : 0;
    }, [data]);
    const now = Date.now() + offset;
    const active = list.filter((t) => Date.parse(t.expires_at) > now && !cancelledRef.current.has(t.id));
    const done = fires
      .filter((f) => {
        const at = Date.parse(f.fired_at);
        return Number.isFinite(at) && now - at < HOME_DONE_MS;
      })
      .sort((a, b) => Date.parse(b.fired_at) - Date.parse(a.fired_at) || b.id - a.id);
    return { active, done, now };
  };

  return { useViewer, useLive, useInterval, useOnFocus, useDebounced, useTick, useIsPhone, useTimers,
           useCachedObject, tabHidden };
})();

/* ---- needs attention: the rules ----------------------------------- */

/* Every problem Home can name, ranked err → warn: the problems among
 * Health's checks (health.jsx healthChecks — ONE set of checks for both
 * pages, since 2026-10-21). Each row links to its part of Settings →
 * Health, which links on to the page that fixes it; Home never fixes
 * anything itself. `scope` says who may see a row (health.jsx). */
const HomeAttentionRows = (inputs) => healthProblems(healthChecks(inputs));

/* Who sees which of those rows (HOME_PROBLEMS_VISIBILITY, admin-set; the
 * rule is health.jsx healthAudience, Health's too):
 *   everyone  household members see the rows they can notice, an admin all
 *   summary   household members see one neutral line
 *   admins    household members see nothing
 * A shared screen shows the neutral line at most, whatever the setting —
 * even with an admin signed in on it. */
const HomeAttentionView = ({ rows, viewer, visibility, shared }) => {
  const audience = healthAudience({ viewer, visibility, shared });
  const own = audience === 'all' ? rows : rows.filter((r) => r.scope !== 'admin');
  if (audience === 'summary') return own.length ? { mode: 'summary', count: own.length } : { mode: 'none' };
  if (audience === 'none') return { mode: 'none' };
  return { mode: 'rows', rows: own };
};

/* Friend Houses' unread messages: one row, not a problem (Health never
 * lists it), for whoever may read the inbox (the device tier: a paired
 * phone or browser, or an admin). It links to the Friend Houses page's
 * Messages section and goes once they're read. */
const HOME_FH_INBOX = '/api/friend-houses/unread';   // a count only: no names, no text
const HomeFriendHousesRow = (inbox) => {
  const n = Number(inbox && inbox.unread) || 0;
  if (n <= 0) return null;
  return { key: 'fh-unread', tone: 'brand', scope: 'household',
           text: HomePlural(n, 'unread Friend Houses message'),
           href: '#settings/friend-houses/messages' };
};
/* That row added to whatever the problems show: after the rows, below a
 * summary line, or on its own. */
const HomeWithRow = (view, row) => {
  if (!row) return view;
  if (view.mode === 'rows') return { ...view, rows: [...view.rows, row] };
  if (view.mode === 'summary') return { ...view, extra: [...(view.extra || []), row] };
  return { mode: 'rows', rows: [row] };
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
          // A clear button, not a faint link (owner, 2026-11-04): it opens
          // the connect prompt (components.jsx ConnectPrompt) again after
          // its "Not now". "This browser": a Mac's is not a phone.
          <span className="home-connect">
            <Button variant="primary" icon="link" onClick={() => { onPair(); }}>Connect this browser</Button>
            <span className="home-connect-why">for live updates, the calendar and every control</span>
          </span>
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
          {(view.extra || []).map((r) => (
            <a key={r.key} className="home-att-row" data-key={r.key} href={r.href}>
              <StatusDot tone={r.tone}/>
              <span className="txt">{r.text}</span>
            </a>
          ))}
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
          {' · '}<a href="#settings/health" className="home-link">every check</a>
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
          if (r.coreDown && viewer.isAdmin && !shared) {
            return (
              <div key={r.key} className={`${cls} home-att-claim`} data-key={r.key}>
                {body}
                <span className="home-att-acts">
                  <a href={r.href} className="home-link">health</a>
                  <CoreRestart/>
                </span>
              </div>
            );
          }
          // A row's href is its part of Settings → Health
          // (#settings/health/rooms): a plain link opens it.
          return (
            <a key={r.key} className={cls} data-key={r.key} href={r.href}>
              {body}
            </a>
          );
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

/* A fire (GET /api/timers `fires`) in the shape HomeTimerTitle reads. */
const HomeFireAsTimer = (f) => ({
  is_reminder: f.is_reminder, label: f.label, message: f.message, room_id: f.room_id,
  expires_at: f.due_at, created_at: f.created_at,
});
// Heard somewhere → ok; a room still waiting its turn → warn; nowhere → err.
// A browser with no household credential gets no per-room rows (rule F1:
// `deliveries` is []), so its summary is what says a fire is on its way.
const HomeFireTone = (f) => {
  if ((f.heard_in || []).length) return 'ok';
  const rows = f.deliveries || [];
  if (rows.some((d) => d.outcome === 'pending' || d.outcome === 'sending')) return 'warn';
  if (!rows.length && String(f.summary || '').startsWith('announcing')) return 'warn';
  return 'err';
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
        {done.map((f) => (
          <div key={`done-${f.id}`} className="home-timer-done" data-done={f.id}>
            <StatusDot tone={HomeFireTone(f)}/>
            <span>done · {f.room_id || 'no room'}</span>
            {!(shared && f.is_reminder) && <span className="meta">{HomeTimerTitle(HomeFireAsTimer(f), shared)}</span>}
            {f.summary && <span className="meta home-timer-heard">{f.summary}</span>}
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

/* What a room is playing, or null. Only a room that is playing or paused
 * has a song: a STOPPED room's player keeps the last song loaded, and the
 * now-playing read still names it (state "stop", song set), which made a
 * room nobody was listening to look like it was playing that song at 0:00
 * here while Music and Settings → Rooms said nothing (2026-10-11).
 * A stopped room is quiet, as everywhere else. */
const HomeRoomSong = (s) => {
  const np = s && s.now_playing;
  if (!s || s.status !== 'online' || !np || !np.song) return null;
  return np.state === 'play' || np.state === 'pause' ? np.song : null;
};

/* Groups (V060): rooms sharing one queue. What a room's card offers:
 * other rooms (or a named group, or every room) hear what plays here; this
 * room hears what plays in another; it leaves the group it is in. Built from
 * the rooms read and /api/music/groups; the server says no (409) when the
 * music stopped meanwhile. A quiet room only gets the joins. */
const HomeGroupChoices = (s, rooms, groups) => {
  const np = s.now_playing || {};
  const g = np.group || null;
  const inMine = new Set(g ? g.rooms : [s.room_id]);
  const playingHere = !!HomeRoomSong(s);
  const others = rooms.filter((r) => r.room_id !== s.room_id && r.status === 'online');
  const out = [];
  if (playingHere) {
    (groups || []).forEach((gr) => {
      const missing = gr.rooms.filter((r) => !inMine.has(r) && others.some((o) => o.room_id === r));
      if (missing.length) {
        out.push({ key: `group:${gr.id}`, icon: 'speaker', label: `play in ${gr.name} too`,
                   body: { host_room: s.room_id, group_id: gr.id }, path: 'shared' });
      }
    });
    if (others.some((o) => !inMine.has(o.room_id))) {
      out.push({ key: 'everywhere', icon: 'speaker', label: 'play everywhere',
                 hint: 'every connected room hears it',
                 body: { host_room: s.room_id, everywhere: true }, path: 'shared' });
    }
    others.filter((o) => !inMine.has(o.room_id)).forEach((o) => {
      out.push({ key: `share:${o.room_id}`, icon: 'plus', label: `play in ${o.room_id} too`,
                 body: { host_room: s.room_id, rooms: [o.room_id] }, path: 'shared' });
    });
  }
  others.filter((o) => !inMine.has(o.room_id) && HomeRoomSong(o)).forEach((o) => {
    out.push({ key: `join:${o.room_id}`, icon: 'log-in', label: `join ${o.room_id}`,
               hint: `hear what plays in ${o.room_id}`,
               body: { room_id: s.room_id, host_room: o.room_id }, path: 'shared/join' });
  });
  if (g) {
    out.push({ key: 'leave', icon: 'log-out', label: 'leave the group',
               hint: 'this room stops, the others play on',
               body: { room_id: s.room_id }, path: 'shared/leave' });
  }
  return out;
};

const HomeGroupMenu = ({ room, items, disabled, onPick }) => {
  const [open, setOpen] = React.useState(false);
  const wrapRef = React.useRef(null);
  React.useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  if (!items.length) return null;
  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <IconButton name="users" title={`${room}: other rooms`} aria-label={`play ${room}'s music in other rooms, or join another room`}
                  aria-haspopup="menu" aria-expanded={open} disabled={disabled}
                  onClick={() => setOpen((o) => !o)}/>
      {open && (
        <div role="menu" aria-label={`${room} and other rooms`} className="home-group-menu"
             style={{ position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 50,
                      minWidth: 220, maxWidth: 'calc(100vw - 32px)', background: 'var(--card)',
                      border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                      boxShadow: 'var(--shadow-md), var(--inner-highlight)', padding: 6,
                      display: 'flex', flexDirection: 'column', gap: 2 }}>
          {items.map((it) => (
            <button key={it.key} role="menuitem" type="button"
                    onClick={() => { setOpen(false); onPick(it); }}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 10px',
                             minHeight: 40, background: 'transparent', border: 'none',
                             borderRadius: 'var(--r-sm)', cursor: 'pointer', textAlign: 'left',
                             font: 'inherit', color: 'var(--fg)' }}>
              <Icon name={it.icon} size={16}/>
              <span style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{it.label}</span>
                {it.hint && <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{it.hint}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

/* The chip on a room in a group: the group's name, or the rooms it plays
 * with. */
const HomeGroupChipText = (s) => {
  const g = s.now_playing && s.now_playing.group;
  if (!g) return null;
  const others = g.rooms.filter((r) => r !== s.room_id);
  const withText = others.length ? `with ${others.join(', ')}` : '';
  return g.name ? `${g.name}${withText ? ` · ${withText}` : ''}` : withText;
};

/* The stop button's title: in a group a stop stops every room of it, as
 * pause pauses them (2026-11-15); "leave the group" in the rooms menu
 * stops this room alone. */
const HomeStopTitle = (s) => {
  const g = s.now_playing && s.now_playing.group;
  if (!g || !g.rooms || g.rooms.length < 2) return `stop ${s.room_id}`;
  return `stop ${g.name || 'the group'}: ${g.rooms.join(', ')}`;
};

/* One room. Not SatCard: that is a single <button>, and a button can't
 * hold the transport buttons. The left part is a link to Settings →
 * Satellites (#settings/rooms, which opens that tab); the buttons sit apart on
 * the right edge, in thumb reach on a phone, while something is playing or
 * paused. A quiet room has none: starting something in a room belongs to
 * the Music page, which picks the room and the track together. */
const HomeRoomRow = ({ s, stale, sinceFetchSec, nextTimerLeft, busy, onAct,
                       rooms = [], groups = null, onGroup = null }) => {
  const online = s.status === 'online';
  const np = s.now_playing;
  const song = HomeRoomSong(s);
  const playing = !!song && np.state === 'play';
  const paused = !!song && np.state === 'pause';
  const dur = song && song.duration_sec ? song.duration_sec : 0;
  // A stale read (the core is down) is where it was, not where it would be.
  // Never past the song's end: between a song ending and the read that
  // brings the next one, the count used to run on ("0:14 / 0:13").
  const ran = song ? (np.elapsed_sec || 0) + (playing && !stale ? sinceFetchSec : 0) : 0;
  const elapsed = dur ? Math.min(ran, dur) : ran;
  const progress = dur ? Math.min(100, (elapsed / dur) * 100) : 0;
  // satellites.jsx owns the rule: by signal (dBm) when the satellite
  // reports one, by rate for an older one; an idle link's beacon rate is
  // never "weak" on its own.
  const weakWifi = online && !!s.wifi && !!window.wifiTone && window.wifiTone(s.wifi) === 'err';
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
  // A room in a group hears one shared queue with other rooms (V060).
  const groupText = online ? HomeGroupChipText(s) : null;
  if (groupText) {
    chips.push(<span key="group" className="home-chip" title="these rooms play one shared queue">
      <Icon name="speaker" size={12}/>{groupText}</span>);
  }
  const groupItems = onGroup && online && !stale ? HomeGroupChoices(s, rooms, groups) : [];
  const groupMenu = groupItems.length > 0 && (
    <HomeGroupMenu room={s.room_id} items={groupItems} disabled={!canAct}
                   onPick={(it) => onGroup(s.room_id, it)}/>
  );

  let actions = null;
  if (online && !stale) {
    if (playing) {
      actions = (
        <>
          <IconButton name="pause" title={`pause ${s.room_id}`} disabled={!canAct} onClick={() => { onAct(s.room_id, 'pause'); }}/>
          <IconButton name="square" title={HomeStopTitle(s)} disabled={!canAct} onClick={() => { onAct(s.room_id, 'stop'); }}/>
          <IconButton name="list-music" title={`${s.room_id}'s queue`} aria-label={`open ${s.room_id}'s queue`}
                      onClick={() => { HomeOpenQueue(s.room_id); }}/>
          {groupMenu}
        </>
      );
    } else if (paused) {
      actions = (
        <>
          <IconButton name="play" title={`resume ${s.room_id}`} disabled={!canAct} onClick={() => { onAct(s.room_id, 'resume'); }}/>
          <IconButton name="square" title={HomeStopTitle(s)} disabled={!canAct} onClick={() => { onAct(s.room_id, 'stop'); }}/>
          <IconButton name="list-music" title={`${s.room_id}'s queue`} aria-label={`open ${s.room_id}'s queue`}
                      onClick={() => { HomeOpenQueue(s.room_id); }}/>
          {groupMenu}
        </>
      );
    } else if (groupMenu) {
      // A quiet room: only the joins ("hear what plays in the kitchen").
      actions = groupMenu;
    }
  }

  return (
    <div className={`home-room${online && !stale ? '' : ' dim'}`} data-room={s.room_id}>
      <a className="home-room-main" href="#settings/rooms" title={`${s.room_id} in Settings → Rooms`}>
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
                     onAct, onStopAll, groups = null, onGroup = null }) => {
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
               action={<a href="#settings/rooms" className="home-link">set up a room</a>}/>
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
                       busy={!!busy[s.room_id]} onAct={onAct}
                       rooms={rooms} groups={groups} onGroup={onGroup}/>
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

/* A GUEST's rooms (data.js isGuestViewer: no household credential). The
 * rooms read (/api/satellites) is a household read, so a friend's phone
 * used to see "couldn't load rooms" here. It gets what it may act on
 * instead (owner decision 2026-10-10): the rooms that are PLAYING, from
 * the open now-playing read, each with pause / resume and a link to that
 * room's queue — the guest may change a playing room's queue. Nothing
 * else: no offline rooms, no timers, no stop. */
// What a guest is told when the server refused a press (the room stopped
// meanwhile, an admin's ban): words in a toast, never the pairing prompt.
const HomeGuestRefusal = (verb, e) => {
  if (e && e.status === 429) return `${verb}: too many changes from this phone — try again in a minute`;
  const said = e && e.detail && typeof e.detail.detail === 'string' ? e.detail.detail : null;
  if ((e && e.status === 401) || (said && /x-device-token|admin session required/i.test(said))) {
    return `${verb}: guests can only change a room that's playing — connect this browser to do more`;
  }
  return `${verb}: ${said || apiErrorText(e)}`;
};

// The queue panel aimed at that room (player.jsx openQueueFor, P5); with
// nothing in this browser's player, data.js's openRoomQueue: Music, on
// that room's queue tab.
const HomeOpenQueue = (room) => {
  if (typeof openQueueFor === 'function') openQueueFor(room);
  else if (typeof openRoomQueue === 'function') openRoomQueue(room);
  else window.location.hash = 'music';
};

const HomeGuestRooms = ({ np, busy, onAct }) => {
  const list = Array.isArray(np.data) ? np.data : [];
  const live = list.filter((r) => r && r.song && (r.state === 'play' || r.state === 'pause'))
    .sort((a, b) => (a.state === b.state ? String(a.room_id).localeCompare(String(b.room_id))
      : a.state === 'play' ? -1 : 1));
  let content;
  if (!HomeAnswered(np)) {
    content = <div className="home-rooms"><div className="home-room home-room-skel" aria-hidden="true"/></div>;
  } else if (!HomeOk(np)) {
    content = <Card><div className="home-att-quiet">couldn't see what's playing</div></Card>;
  } else if (!live.length) {
    content = <Card><div className="home-att-quiet">nothing is playing right now</div></Card>;
  } else {
    content = (
      <div className="home-rooms">
        {live.map((r) => {
          const playing = r.state === 'play';
          const verb = playing ? 'pause' : 'resume';
          return (
            <div key={r.room_id} className="home-room" data-room={r.room_id}>
              <a className="home-room-main" href="#music" title={`${r.room_id}'s queue`}
                 onClick={(e) => { e.preventDefault(); HomeOpenQueue(r.room_id); }}>
                <div className="home-room-top">
                  <StatusDot tone="ok" live={playing}/>
                  <span className="home-room-name">{r.room_id}</span>
                  {playing ? <Pill tone="live" live>playing</Pill> : <Pill tone="idle">paused</Pill>}
                </div>
                <div className="home-room-np">
                  <span className="t">{HomeSongTitle(r.song)}</span>
                  {r.song.artist && <span> · {r.song.artist}</span>}
                </div>
              </a>
              <div className="home-room-actions">
                <IconButton name={playing ? 'pause' : 'play'} title={`${verb} ${r.room_id}`}
                            disabled={!!busy[r.room_id]} onClick={() => { onAct(r.room_id, verb); }}/>
                <IconButton name="list-music" title={`${r.room_id}'s queue`}
                            onClick={() => { HomeOpenQueue(r.room_id); }}/>
              </div>
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <div className="home-sec home-sec-rooms">
      <div className="home-sec-head"><span className="label">playing now</span></div>
      {content}
    </div>
  );
};

/* ---- today --------------------------------------------------------- */

/* Today / next: what is still on today, then the next few events of the
 * coming week, across every calendar the house shows (its own, the ones
 * added by link, the ones phones share): time ("all day" for an all-day
 * one), title, place, the calendar's colour and name for one from
 * elsewhere, a "now" pill while one is running. Never the description. A
 * shared screen gets the times and "busy" only (no calendar names either).
 * Read from local midnight: an event under way must still show. */
const HomeToday = ({ events, answered, failed, now, shared, guest = false }) => {
  const list = Array.isArray(events) ? events : [];
  const d1 = HomeDayStart(now, 1);
  const d2 = HomeDayStart(now, 2);
  const horizon = HomeDayStart(now, 7);
  const endOf = (e) => (e.ends_at ? Date.parse(e.ends_at) : Date.parse(e.starts_at) + 3600 * 1000);
  const sorted = [...list].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at));
  const today = sorted.filter((e) => Date.parse(e.starts_at) < d1 && endOf(e) >= now);
  const later = sorted.filter((e) => Date.parse(e.starts_at) >= d1 && Date.parse(e.starts_at) < horizon);
  const shown = [...today, ...later].slice(0, HOME_TODAY_ROWS);
  const titleOf = (e) => (shared ? 'busy' : e.title);
  const dayLabel = (ms) => {
    if (ms < d1) return 'today';
    if (ms < d2) return 'tomorrow';
    return window.fmtDayLabel ? window.fmtDayLabel(new Date(ms)) : HomeFmtDate(ms);
  };

  let content;
  if (!answered) {
    content = <div className="home-att-quiet">checking…</div>;
  } else if (failed && guest) {
    // A guest's read is refused, not broken: say what opens it.
    content = <div className="home-att-quiet">connect this browser to see the calendar</div>;
  } else if (failed) {
    content = <div className="home-att-quiet">calendar unavailable</div>;
  } else if (!shown.length) {
    const earlier = sorted.some((e) => Date.parse(e.starts_at) < d1 && endOf(e) < now);
    content = (
      <div className="home-att-quiet">
        {earlier ? 'nothing more today, and nothing on the calendar this week' : 'nothing on the calendar this week'}
      </div>
    );
  } else {
    const groups = [];
    for (const e of shown) {
      // Anything begun before today and still on (an all-day trip) is today's.
      const label = dayLabel(Math.max(Date.parse(e.starts_at), HomeDayStart(now)));
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.rows.push(e);
      else groups.push({ label, rows: [e] });
    }
    const earlier = sorted.some((e) => Date.parse(e.starts_at) < d1 && endOf(e) < now);
    let n = 0;
    content = (
      <div className="home-today">
        {!today.length && (
          <div className="home-att-quiet">{earlier ? 'nothing more today' : 'nothing on today'}</div>
        )}
        {groups.map((g) => (
          <div key={g.label} className="home-today-day">
            <div className="home-group-label">{g.label}</div>
            {g.rows.map((e) => {
              const idx = n++;
              const running = Date.parse(e.starts_at) <= now && now < endOf(e);
              const ext = !!e.read_only;
              return (
                <a key={e.id} href="#calendar" data-event={e.id}
                   className={`cal-mlist-row home-today-row${idx >= HOME_PHONE_ROWS ? ' home-phone-extra' : ''}`}>
                  <div className="time mono">
                    <div>{e.all_day ? 'all day' : HomeClock(e.starts_at)}</div>
                    {e.ends_at && !e.all_day && <div className="end">{HomeClock(e.ends_at)}</div>}
                  </div>
                  <div className="body">
                    <div className="ti">{titleOf(e)}</div>
                    {(ext || (!shared && e.location)) && (
                      <div className="meta">
                        {ext && (
                          <span className="cal-src-tag src-ext"
                                style={e.calendar_color ? { '--cal-c': e.calendar_color } : undefined}>
                            <span className="dot"/>{shared ? '' : (e.calendar_name || '')}
                          </span>
                        )}
                        {!shared && e.location && <span><Icon name="map-pin" size={11}/> {e.location}</span>}
                      </div>
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
      <Card title="today / next" action={<a href="#calendar" className="home-link">calendar</a>}>{content}</Card>
    </div>
  );
};

/* ---- first run ----------------------------------------------------- */

// The first phrase Start here offers (the manual's content file, the one
// the #manual page shows): never a plugin's phrase, never one that needs a
// room, the library or the internet (domovoi/tests/test_manual_content.py).
const HomeHintPhrase = (manual) => {
  const blocks = manual && manual.start && Array.isArray(manual.start.blocks) ? manual.start.blocks : [];
  const say = blocks.find((b) => b && b.type === 'say' && Array.isArray(b.phrases) && b.phrases.length);
  const first = say && say.phrases.find((p) => p && typeof p.text === 'string' && !p.pending);
  return first ? first.text : HOME_HINT_FALLBACK;
};

const HomeFirstRun = ({ manual }) => (
  <div className="home-firstrun">
    <span className="lab">try saying</span>
    <span className="mono">“{HomeHintPhrase(manual)}”</span>
    <a href="#manual" className="home-link">start here</a>
  </div>
);

/* ---- everything (phones) -------------------------------------------- */

/* Every page that is not on this phone's bar (components.jsx
 * phoneBarRoutes) — plugin pages included, with their badges — plus
 * Settings and the manual. The phone's "more" menu, and the way to the
 * bar's editor (Settings → App layout). `counts` and `badges` are
 * App's one useSidebarCounts and usePluginBadges results, handed down
 * rather than fetched again (a grid rendered without `badges` polls for
 * itself). A shared screen leaves the same pages off as the sidebar and the
 * strip (navItemsFor). On a desktop the sidebar already lists everything
 * and this is not mounted. */
const HomeEverything = ({ counts, badges: given, shared }) => {
  const manifest = window.DomovoiPluginManifest || { plugins: [] };
  const own = usePluginBadges(given ? null : manifest);
  const badges = given || own;
  const c = counts || {};
  const ai = useAiFeatures();
  useFacesPage();   // Faces comes and goes with the switch (navItemsFor reads it)
  const items = navItemsFor(manifest, { shared, ai });
  const onBar = new Set(phoneBarRoutes(items, usePhoneBar()));
  // The sidebar's groups (resolveNavTree, components.jsx) over what the bar
  // leaves: a group with one tile left is a plain tile, like the sidebar's
  // one-page group. Runs of plain tiles share a heading-less grid.
  // The household's menu layout (Settings → App layout), as the sidebar.
  const tree = resolveNavTree(items.filter((it) => !onBar.has(it.route)), useNavLayout());
  const blocks = [];
  const plain = (it) => {
    const last = blocks[blocks.length - 1];
    if (last && !last.label) last.tiles.push(it);
    else blocks.push({ key: `run-${blocks.length}`, label: null, tiles: [it] });
  };
  tree.forEach((e) => {
    if (e.kind === 'group') blocks.push({ key: e.id, label: e.label, tiles: e.items });
    else plain(e.item);
  });
  HOME_EXTRA_TILES.forEach(plain);
  const tile = (it) => {
    const badge = it.core ? (it.countKey ? c[it.countKey] : null) : badges[it.route];
    return (
      <a key={it.route} href={`#${it.route}`} className="home-tile">
        {it.core ? <Icon name={it.icon} size={18}/> : <PluginNavIcon src={it.iconSrc} srcDark={it.iconSrcDark} size={18}/>}
        <span className="lab">{it.label}</span>
        {badge != null && <span className="home-tile-badge">{badge}</span>}
      </a>
    );
  };
  const edit = (
    <a href="#settings/app-layout" className="home-link home-bar-edit">
      edit the bar
    </a>
  );
  return (
    <div className="home-sec home-sec-everything">
      <Card title="everything" action={edit}>
        {blocks.map((b) => (
          <div key={b.key} className="home-grid-block" role={b.label ? 'group' : undefined} aria-label={b.label ? b.label : undefined}>
            {b.label && <div className="home-grid-head">{b.label.toLowerCase()}</div>}
            <div className="home-grid">{b.tiles.map(tile)}</div>
          </div>
        ))}
      </Card>
    </div>
  );
};

/* ---- photos and videos: the backlog chip ------------------------------ */

/* "Understanding your photos: 3,210 of 20,000" (1.5.0, design
 * MEDIA-UNDERSTANDING §5): read-only, only while understanding is on and
 * a backlog is left, and never for a guest or on a shared screen (the
 * read itself is the household tier). Home has no summary endpoint of its
 * own, so this is the Settings card's read (/api/media-understanding),
 * issued only on Home and only for the household; its progress event
 * re-reads it (the core sends one at most every 10 s while it works).
 * The chip links to the card, where Pause and the estimate are. */
const HOME_MEDIA_HREF = '#settings/photos-and-videos';

const HomeMediaChip = ({ data }) => {
  const s = (data && data.settings) || {};
  const p = (data && data.progress) || {};
  if (!s.enabled) return null;
  const ph = p.photos || {};
  const vi = p.videos || {};
  const left = (ph.left || 0) + (vi.left || 0);
  if (left <= 0) return null;
  const total = (ph.total || 0) + (vi.total || 0);
  const done = total - left;
  const what = vi.total ? 'photos and videos' : 'photos';
  const n = (x) => Number(x || 0).toLocaleString();
  return (
    <div className="home-sec home-sec-media">
      <a className="home-chip home-media-chip" href={HOME_MEDIA_HREF}
         title={s.paused ? 'paused · open Photos and videos in Settings' : 'open Photos and videos in Settings'}>
        <Icon name="image" size={12}/>
        <span>Understanding your {what}: <span className="mono">{n(done)}</span> of <span className="mono">{n(total)}</span>{s.paused ? ' · paused' : ''}</span>
      </a>
    </div>
  );
};

/* ---- the page ------------------------------------------------------- */

/* Plugin cards (SDK 1.14, [[web.home_cards]]). A plugin's panel bundle
 * registers `window.DomovoiPlugins.<slug>.homeCards.<Component>`; the
 * manifest (enabled plugins only: a disabled or uninstalled one is not in
 * it) says which cards exist. The slot is QUIET: a card whose script never
 * loaded, that isn't registered, that throws, or that renders nothing
 * (no data) leaves no trace on Home — no error box, no empty frame, and
 * nothing in "needs attention". The card draws its own <Card> and reads
 * its data quietly (apiFetch with noPrompt: true), so an unpaired guest is
 * never prompted. `default_on = false` cards are not shown (Home has no
 * per-household card settings yet). */
const HomePluginCardBoundary = (typeof React.Component === 'function') ? class extends React.Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error) {
    try { console.warn(`home card ${this.props.slug}/${this.props.id} failed:`, error); } catch {}
  }
  render() { return this.state.failed ? null : this.props.children; }
} : null;   // the render-test harness has no class components

const HomePluginCard = ({ slug, id, title, guest, Comp }) => {
  const props = { quiet: true, title, guest: !!guest };
  if (!HomePluginCardBoundary) {
    try { return Comp(props); } catch (e) { return null; }
  }
  return (
    <HomePluginCardBoundary slug={slug} id={id}>
      <Comp {...props}/>
    </HomePluginCardBoundary>
  );
};

const HomePluginCardList = (manifest) => {
  const out = [];
  const registry = window.DomovoiPlugins || {};
  ((manifest && manifest.plugins) || []).forEach((p) => {
    if (!p || p.status === 'uninstalled' || p.status === 'disabled') return;
    const reg = registry[p.slug];
    const comps = reg && reg.homeCards;
    if (!comps || typeof comps !== 'object') return;
    (p.home_cards || []).forEach((c) => {
      const Comp = comps[c.component];
      if (typeof Comp !== 'function' || c.default_on === false) return;
      out.push({ slug: p.slug, id: c.id, title: c.title, order: c.order == null ? 50 : c.order, Comp });
    });
  });
  return out.sort((a, b) => (a.order - b.order) || a.slug.localeCompare(b.slug) || a.id.localeCompare(b.id));
};

const HomePluginCards = ({ manifest, guest }) => {
  const cards = HomePluginCardList(manifest || window.DomovoiPluginManifest);
  if (!cards.length) return null;
  return (
    <div className="home-sec home-plugin-cards">
      {cards.map(({ slug, id, title, Comp }) => (
        <HomePluginCard key={`${slug}/${id}`} slug={slug} id={id} title={title} guest={guest} Comp={Comp}/>
      ))}
    </div>
  );
};

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
  // A guest (no household credential, box set up) can't read the rooms;
  // it gets the rooms that are playing from the open now-playing read
  // instead (HomeGuestRooms). Never issued for anyone else.
  const guest = viewer.known && !viewer.unclaimed && !viewer.paired && !viewer.isAdmin;
  const guestNp = useApiObject(guest ? '/api/music/now-playing' : null,
                               { eventTypes: ['music.now_playing.changed'], quiet: true });
  // `timer_fires.changed` too: a fire's "done · kitchen" line comes from
  // the same read, and its "heard in …" moves as each room announces it.
  // A reminder's words and the fire details answer to a household
  // credential (rules M1/F1), so pairing (or signing in) re-reads it.
  const timers = useApiObject('/api/timers', { eventTypes: ['timers.changed', 'timer_fires.changed'], quiet: true,
                                               refetchOnAuth: true });
  const plugins = useApiObject('/api/plugins', { eventTypes: ['plugins.changed'], quiet: true });
  // The photos-and-videos backlog chip: a household read, issued only once
  // this browser is paired (or an admin's), never on a shared screen.
  const mediaHousehold = (viewer.paired || viewer.isAdmin) && !shared;
  const mediaUnderstanding = useApiObject(mediaHousehold ? '/api/media-understanding' : null,
                                          { eventTypes: ['media_understanding.changed'], quiet: true,
                                            refetchOnAuth: true });
  // Named groups (V060) for the rooms' "play in upstairs too": a household
  // read, never issued for a guest.
  const musicGroups = useApiObject(guest ? null : '/api/music/groups',
                                   { eventTypes: ['music.now_playing.changed'], quiet: true });
  const acq = useApiObject('/api/acquisitions?status=pending&limit=100',
                           { eventTypes: ['acquisitions.changed'], quiet: true });
  // Friend Houses' unread count: the inbox's own tier (device), so only a
  // paired browser or an admin asks.
  const fhInbox = useApiObject(viewer.paired || admin ? HOME_FH_INBOX : null, { quiet: true, refetchOnAuth: true });
  const dayStart = HomeDayStart(Date.now());
  const calPath = React.useMemo(() => {
    const start = new Date(dayStart).toISOString();
    const end = new Date(HomeDayStart(dayStart, 7)).toISOString();
    return `/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&limit=50`;
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
  // Whether the phone app is on this server yet (a reminder row until it is).
  const phoneApp = HomeHooks.useCachedObject(admin ? '/api/satellites/android-app' : null, HOME_APPROVALS_MS);

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
  HomeHooks.useInterval(() => { health.refresh(); cfg.refresh(); fhInbox.refresh(); }, HOME_HEALTH_MS);
  HomeHooks.useInterval(() => approvals.refresh(), HOME_APPROVALS_MS, admin);
  HomeHooks.useInterval(() => hardware.refresh(), HOME_HARDWARE_MS, admin);
  const versionAt = React.useRef(0);
  React.useEffect(() => { if (admin) versionAt.current = Date.now(); }, [admin]);
  HomeHooks.useOnFocus(() => {
    cfg.refresh(); health.refresh(); cal.refresh(); fhInbox.refresh();
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

  // The container guard's open incidents (components.jsx: one shared read
  // for the whole page, the banner's too).
  const guard = useContainerGuard();

  // ── derived ──
  const coreDown = !!(health.data && health.data.domovoi_reachable === false);
  const dbDown = !!(health.data && health.data.db_reachable === false);
  // A Wi-Fi push newer than the last read wins for rx/tx; nothing else.
  const wifiNow = wifiPush && wifiPush.at >= satsAt ? wifiPush.map : null;
  const rooms = (Array.isArray(sats.data) ? sats.data : []).map((s) => {
    const w = wifiNow && wifiNow[s.room_id];
    if (!w || typeof w !== 'object') return s;
    return { ...s, wifi: { ...(s.wifi || {}), rx_mbits: w.rx_mbits, tx_mbits: w.tx_mbits,
                           signal_dbm: w.signal_dbm, mode: w.mode } };
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
    // A guest's rooms come from now-playing (HomeGuestRooms); its refused
    // rooms read is not a problem to report.
    ['server', health], ...(guest ? [] : [['rooms', sats]]), ['plugins', plugins], ['media requests', acq],
    ...(admin ? [['approvals', approvals], ['adoption', pending], ['updates', version], ['disk', hardware],
                 ['phone app', phoneApp]]
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
    version: version.data, hardware: hardware.data, config: cfg.data, phoneApp: phoneApp.data,
    guard,
  });
  const visibility = (cfg.data && cfg.data.home_problems_visibility) || 'everyone';
  // A household member's rows wait for the setting that decides them, so
  // an "admins only" house never flashes them while /api/config loads.
  const view = HomeWithRow((!admin && !HomeAnswered(cfg)) ? { mode: 'none' }
    : HomeAttentionView({ rows, viewer, visibility, shared }), HomeFriendHousesRow(fhInbox.data));
  const [checkedAt, setCheckedAt] = React.useState(null);
  React.useEffect(() => { if (!checking) setCheckedAt(Date.now()); },
    [checking, health.data, sats.data, plugins.data, acq.data, approvals.data, pending.data,
     version.data, hardware.data, phoneApp.data]);

  // First run: a house with nothing in it yet gets one hint, taken from
  // the manual. Only then is the manual asked for.
  const firstRun = HomeOk(sats) && !rooms.length && HomeOk(timers) && !active.length
    && HomeOk(cal) && !(cal.data || []).length;
  const manual = HomeHooks.useCachedObject(firstRun ? '/manual-content.json' : null, HOME_MANUAL_MS);

  // ── actions (device tier; data.js prompts and replays when unpaired) ──
  const [busy, setBusy] = React.useState({});
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

  // Groups (V060): another room hears this one, this one joins another, or
  // it leaves its group. Every room it touches is busy meanwhile.
  const onGroup = async (room, it) => {
    const touched = [room, ...((it.body && it.body.rooms) || [])];
    setRoomsBusy(touched, true);
    try {
      await apiPost(`/api/music/groups/${it.path}`, it.body);
      fire(it.key === 'leave' ? `${room} left the group` : `${it.label} · done`);
    } catch (e) {
      reportMutationFailure(fire, it.key === 'leave' ? 'leave the group' : 'share', e);
    } finally {
      setRoomsBusy(touched, false);
      musicGroups.refresh();
      refetchRooms();
    }
  };

  // A guest's pause / resume (HomeGuestRooms): sent without the pairing
  // prompt — the server decides, and a refusal is said in words.
  const onGuestAct = async (room, verb) => {
    setRoomBusy(room, true);
    try {
      await apiFetch(`/api/music/${verb}/${encodeURIComponent(room)}`,
                     { method: 'POST', body: '{}', noPrompt: true });
    } catch (e) {
      fire(HomeGuestRefusal(verb, e));
    } finally {
      setRoomBusy(room, false);
      guestNp.refresh();
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
  // one and toast "that one already finished" over "cancelled". The row
  // shows "cancelling…" while the DELETE is out, and leaves the countdown
  // once the server has said yes (useTimers skips `cancelled`).
  const onCancel = async (t) => {
    if (cancellingRef.current.has(t.id)) return;
    cancellingRef.current.add(t.id);
    setCancelling((c) => new Set(c).add(t.id));
    try {
      await apiDelete(`/api/timers/${t.id}`);
      cancelled.current.add(t.id);
      fire(`cancelled ${HomeTimerNoun(t, shared)}`);
    } catch (e) {
      if (e && e.status === 404) fire('that one already finished');
      else reportMutationFailure(fire, 'cancel', e);
    } finally {
      cancellingRef.current.delete(t.id);
      setCancelling((c) => { const next = new Set(c); next.delete(t.id); return next; });
      timers.refresh();
    }
  };

  // The connect prompt (components.jsx ConnectAsk), past its "Not now";
  // the bare pair prompt where it isn't loaded.
  const onPair = () => {
    try { if (typeof ConnectAsk !== 'undefined') { ConnectAsk.open(); return; } } catch { /* below */ }
    try { Auth.requestPairing(); } catch {}
  };
  const onSignIn = () => { try { Auth.requestLogin(); } catch {} };
  // "claim it": the first-run welcome again (components.jsx FirstRun),
  // which holds the claim form, even after its "Not now".
  const onClaim = () => {
    try { if (typeof FirstRun !== 'undefined') { FirstRun.open(); return; } } catch { /* below */ }
    onSignIn();
  };

  const name = (cfg.data && cfg.data.bot_name) || 'domovoi';
  const Broadcast = window.Broadcast;

  return (
    <div className="page home">
      <HomeHeader name={name} nowMs={now} line={line} lineReady={HomeAnswered(sats)} live={live}
                  viewer={viewer} onPair={onPair}/>
      {firstRun && HomeAnswered(manual) && <HomeFirstRun manual={manual.data}/>}
      <div className="home-cols">
        <div className="home-col">
          {guest ? (
            <HomeGuestRooms np={guestNp} busy={busy} onAct={onGuestAct}/>
          ) : (
            <HomeRooms rooms={rooms} answered={HomeAnswered(sats)} failed={HomeAnswered(sats) && !HomeOk(sats)}
                       dbDown={dbDown} stale={coreDown} fetchedAt={satsAt} timerLeftByRoom={timerLeftByRoom}
                       busy={busy} stoppingAll={stoppingAll}
                       onAct={onAct} onStopAll={onStopAll}
                       groups={musicGroups.data ? musicGroups.data.groups : null} onGroup={onGroup}/>
          )}
          <div className="home-pair">
            <HomeTimers active={active} done={done} now={now} shared={shared}
                        onlineRooms={onlineRooms} cancelling={cancelling} onCancel={onCancel}/>
            <HomeToday events={cal.data} answered={HomeAnswered(cal)}
                       failed={HomeAnswered(cal) && !HomeOk(cal)} now={now} shared={shared} guest={guest}/>
          </div>
        </div>
        <div className="home-col">
          <HomeAttention view={view} viewer={viewer} shared={shared} checking={checking}
                         failed={failed} refused={refused} checkedAt={checkedAt}
                         onClaim={onClaim} onSignIn={onSignIn}/>
          {Broadcast && (
            <div className="home-sec home-sec-announce">
              <Broadcast compact onlineCount={coreDown ? 0 : online.length} fire={fire}/>
            </div>
          )}
          {mediaHousehold && <HomeMediaChip data={mediaUnderstanding.data}/>}
          <HomePluginCards guest={guest}/>
          {isPhone !== false && <HomeEverything counts={counts} badges={badges} shared={shared}/>}
        </div>
      </div>
      {toastNode}
    </div>
  );
};

window.HomePage = HomePage;
