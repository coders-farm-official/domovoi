/* Health — Settings → System → Health (#settings/health), and the ONE set
 * of checks Home's "needs attention" list is made from (2026-10-21).
 *
 * healthChecks() is pure: from what the page has read, it returns every
 * check this dashboard knows, green or not, each in its part of Health
 * (server, updates, rooms, plugins, internet, containers). Home keeps only
 * the problems (healthProblems), in the order it always showed them, and
 * links each one to its part of Health; Health shows them all. A check
 * whose read hasn't answered is grey ("not checked yet"), never green.
 *
 * `scope` says who may see a check: 'setup' (the claim row — everyone,
 * since anyone on the network can change settings until the box is
 * claimed), 'open' (explains what a person notices) or 'admin' (only an
 * admin can act; those reads are never even made for anyone else). When
 * the core or the database is down that is the only story: every check
 * that reads through them would just repeat it, so they wait. Neither
 * page fixes anything itself — each problem links to the page that does.
 *
 * The container guard's details fold in here too (containers.jsx): its
 * open incidents at the top of the page, the rest in the Containers part.
 *
 * Every top-level name here starts with health/Health (all page scripts
 * share one global scope, index.html).
 */

const HEALTH_DISK_WARN = 90;
const HEALTH_DISK_ERR = 95;
const HEALTH_POLL_MS = 60 * 1000;

// The parts of the page, in order, and what each is called.
const HEALTH_PARTS = [
  { id: 'server', label: 'Server' },
  { id: 'updates', label: 'Updates' },
  { id: 'rooms', label: 'Rooms' },
  { id: 'plugins', label: 'Plugins' },
  { id: 'internet', label: 'Internet' },
  { id: 'containers', label: 'Containers' },
];

const HEALTH_INTERNET_WORDS = { always: 'yes', sometimes: 'sometimes', never: 'no' };
const HEALTH_UPDATE_WORDS = {
  ok: 'worked', running: 'in progress', rolled_back: 'rolled back', rollback_failed: 'rollback failed',
  failed: 'failed', aborted: 'not applied', refused: 'not applied',
};

/* Who is looking. `isAdmin` also covers a cookie session that survived a
 * reload (status.authenticated — isLoggedIn() alone misses it) and the
 * pre-setup grace, where every read, the admin ones included, is open. */
const healthViewer = () => {
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

const healthPlural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;

/* The container guard's check: from the shared store (components.jsx),
 * the banner's own words. The words follow the reader's tier: a browser
 * with no household credential gets only "something needs an admin's
 * attention". */
const healthGuardCheck = (guard) => {
  const base = { key: 'containers', part: 'containers', label: 'Domovoi containers', scope: 'open', order: 0 };
  if (!guard || !guard.loaded || !guard.data) {
    return { ...base, tone: 'idle', text: 'not checked yet' };
  }
  const b = (typeof containerGuardBanner === 'function') ? containerGuardBanner(guard) : null;
  if (!b || !b.show) {
    return { ...base, tone: 'ok', text: 'every container Domovoi expects looks right' };
  }
  const tone = b.tone === 'warn' ? 'warn' : 'err';
  let text = "something needs an admin's attention";
  if (guard.tier === 'household') {
    if (b.tone === 'tamper') {
      text = 'signs of tampering in a Domovoi container · risky changes are paused';
    } else if (b.ids.length > 1) {
      text = `${b.ids.length} Domovoi containers need attention`;
    } else {
      const only = (guard.data.incidents || []).find((i) => i.id === b.ids[0]);
      const summary = String((only && only.summary) || 'a Domovoi container needs attention').replace(/\.$/, '');
      text = summary.charAt(0).toLowerCase() + summary.slice(1);
    }
  }
  return { ...base, tone, text };
};

/* Every check, in Health's order. `order` is where Home always listed the
 * problem (it ranks err → warn, then by it). `fix` is the page that fixes
 * it; `items` the rooms or plugins a row is about. */
const healthChecks = ({ viewer, health, rooms, plugins, pluginErrors, acq,
                        approvals, pending, version, hardware, config, internet, guard }) => {
  const v = viewer || {};
  const out = [];
  const add = (c) => out.push(c);
  const dbDown = !!health && health.db_reachable === false;
  const coreDown = !!health && health.domovoi_reachable === false;
  const waits = dbDown || coreDown;
  const wait = (c) => ({ ...c, tone: 'idle', text: 'waiting for the Domovoi server' });
  const unread = (c) => ({ ...c, tone: 'idle', text: 'not checked yet' });

  // ── server ──
  {
    const c = { key: 'claim', part: 'server', label: 'admin account', scope: 'setup', order: 1 };
    if (v.unclaimed) {
      add({ ...c, tone: 'err', claim: true,
            text: "this box isn't claimed yet · anyone on the network can change settings" });
    } else if (v.known) {
      add({ ...c, tone: 'ok', text: 'set up · changes need the admin password' });
    } else {
      add(unread(c));
    }
  }
  {
    const c = { key: 'core', part: 'server', label: 'Domovoi server', scope: 'open', order: 3 };
    if (!health) add(unread(c));
    else if (coreDown) {
      // A core that runs but can't serve (its /v1/alive answers) is told
      // apart: restarting it won't help.
      const sick = health.core_alive === true;
      add({ ...c, tone: 'err', coreDown: !sick,
            text: sick
              ? "the Domovoi server is running but can't answer · most likely its database"
              : "the Domovoi server isn't answering · rooms show the last known state" });
    } else add({ ...c, tone: 'ok', text: 'answering' });
  }
  {
    const c = { key: 'db', part: 'server', label: 'database', scope: 'open', order: 2 };
    if (!health) add(unread(c));
    else if (dbDown) add({ ...c, tone: 'err', text: "the database isn't answering · nothing new can be saved" });
    else add({ ...c, tone: 'ok', text: 'answering' });
  }
  {
    const c = { key: 'stt', part: 'server', label: 'speech recognition', scope: 'open', order: 4,
                fix: '#settings/models', fixLabel: 'models' };
    const stt = health && health.stt;
    if (!health) add(unread(c));
    else if (waits) add(wait(c));
    else if (stt === 'unavailable') {
      add({ ...c, tone: 'err', text: "speech recognition is off · the rooms can't understand anyone" });
    } else if (stt === 'fallback') {
      add({ ...c, tone: 'warn', text: 'speech recognition is on its slower fallback model' });
    } else add({ ...c, tone: 'ok', text: stt ? 'ready' : 'not reported by this server' });
  }
  {
    const c = { key: 'disk', part: 'server', label: 'disk space', scope: 'admin', order: 15,
                fix: '#settings/models', fixLabel: 'models' };
    const disk = hardware && hardware.disk;
    if (waits) add(wait(c));
    else if (!hardware) add(unread(c));
    else if (!disk || typeof disk.percent !== 'number') add({ ...c, tone: 'idle', text: 'not reported by this server' });
    else {
      // The core measures $HOME, so it is the home disk, not "the" disk.
      const pct = Math.round(disk.percent);
      if (disk.percent >= HEALTH_DISK_WARN) {
        add({ ...c, tone: disk.percent >= HEALTH_DISK_ERR ? 'err' : 'warn', text: `home disk is ${pct}% full` });
      } else add({ ...c, tone: 'ok', text: `home disk is ${pct}% full` });
    }
  }

  // ── updates (admin) ──
  {
    const c = { key: 'version', part: 'updates', label: 'version', scope: 'admin', order: 90,
                fix: '#settings/about', fixLabel: 'about & updates' };
    if (waits) add(wait(c));
    else if (!version) add(unread(c));
    else {
      const sha = version.sha ? String(version.sha).slice(0, 7) : null;
      const label = typeof version.version === 'string' ? version.version : sha;
      const channel = typeof version.channel === 'string' ? ` · ${version.channel} channel` : '';
      add({ ...c, tone: 'ok', text: `${label ? `running ${label}` : 'running'}${channel}` });
    }
  }
  {
    const c = { key: 'update', part: 'updates', label: 'last update', scope: 'admin', order: 12,
                fix: '#settings/about', fixLabel: 'about & updates' };
    if (waits) add(wait(c));
    else if (!version) add(unread(c));
    else {
      const last = version.last_update;
      const st = last && last.status;
      if (st === 'failed' || st === 'rollback_failed') {
        add({ ...c, tone: 'err', at: last.finished_at, text: 'the last update failed' });
      } else if (st === 'rolled_back' || (version.bad_sha && st !== 'ok')) {
        add({ ...c, tone: 'warn', at: last && last.finished_at, text: 'the last update was rolled back' });
      } else if (st) {
        add({ ...c, tone: st === 'ok' ? 'ok' : 'idle', at: last.finished_at, text: HEALTH_UPDATE_WORDS[st] || st });
      } else add({ ...c, tone: 'idle', text: 'none on record' });
    }
  }
  {
    const c = { key: 'restart', part: 'updates', label: 'restart', scope: 'admin', order: 13,
                fix: '#settings/about', fixLabel: 'about & updates' };
    if (waits) add(wait(c));
    else if (!version) add(unread(c));
    else {
      const r = pendingRestart(version);
      if (r.code) {
        add({ ...c, tone: 'warn', text: "a restart is pending · the pulled code isn't running yet" });
      } else if (r.plugins.length) {
        add({ ...c, tone: 'warn', fix: '#plugins', fixLabel: 'plugins',
              items: r.plugins.map((p) => ({ name: p.slug, href: '#plugins' })),
              text: r.plugins.length === 1
                ? `a restart is pending · the ${r.plugins[0].slug} upgrade isn't running yet`
                : `a restart is pending · ${r.plugins.length} plugin upgrades aren't running yet` });
      } else add({ ...c, tone: 'ok', text: 'not needed' });
    }
  }

  // ── rooms ──
  const list = Array.isArray(rooms) ? rooms : null;
  const roomItems = (xs) => xs.map((s) => ({ name: s.room_id, href: '#settings/rooms' }));
  const roomCheck = (c, pick, problem, okText) => {
    if (waits) return add(wait(c));
    if (!list) return add(unread(c));
    const hits = pick(list);
    if (!hits.length) return add({ ...c, tone: 'ok', text: okText });
    return add({ ...c, ...problem(hits), items: roomItems(hits) });
  };
  {
    const c = { key: 'rooms', part: 'rooms', label: 'rooms online', scope: 'open', order: 91,
                fix: '#settings/rooms', fixLabel: 'rooms' };
    if (waits) add(wait(c));
    else if (!list) add(unread(c));
    else if (!list.length) add({ ...c, tone: 'idle', text: 'no rooms yet' });
    else {
      const on = list.filter((s) => s.status === 'online').length;
      add({ ...c, tone: on ? 'ok' : 'idle', text: `${on} of ${list.length} online` });
    }
  }
  roomCheck({ key: 'offline', part: 'rooms', label: 'offline', scope: 'open', order: 5,
              fix: '#settings/rooms', fixLabel: 'rooms' },
            (xs) => xs.filter((s) => s.status === 'offline'),
            (off) => (off.length === 1
              ? { tone: 'warn', at: off[0].last_connected_at,
                  text: `${off[0].room_id} is offline · it can't hear anyone right now` }
              : { tone: 'warn', text: `${off.length} rooms offline` }),
            'none');
  roomCheck({ key: 'waiting', part: 'rooms', label: 'set up, not connected yet', scope: 'open', order: 6,
              fix: '#settings/rooms', fixLabel: 'rooms' },
            (xs) => xs.filter((s) => s.status === 'waiting'),
            (w) => ({ tone: 'warn',
                      text: w.length === 1
                        ? `${w[0].room_id} was set up but hasn't connected yet`
                        : `${w.length} rooms were set up but haven't connected yet` }),
            'none');
  roomCheck({ key: 'kiosk', part: 'rooms', label: 'screens', scope: 'open', order: 7,
              fix: '#settings/rooms', fixLabel: 'rooms' },
            (xs) => xs.filter((s) => s.sat_type === 'video' && s.status === 'online'
              && s.display && s.display.kiosk_alive === false),
            (dead) => ({ tone: 'warn',
                         text: dead.length === 1
                           ? `the ${dead[0].room_id} screen stopped showing anything`
                           : `${dead.length} screens stopped showing anything` }),
            'every screen is showing');
  {
    const c = { key: 'approvals', part: 'rooms', label: 'waiting for approval', scope: 'admin', order: 10,
                fix: '#settings/rooms', fixLabel: 'rooms' };
    if (waits) add(wait(c));
    else if (!Array.isArray(approvals)) add(unread(c));
    else if (!approvals.length) add({ ...c, tone: 'ok', text: 'none' });
    else {
      add({ ...c, tone: 'warn', items: roomItems(approvals),
            text: approvals.length === 1
              ? `${approvals[0].room_id} is waiting for approval`
              : `${approvals.length} satellites are waiting for approval` });
    }
  }
  {
    const c = { key: 'adopt', part: 'rooms', label: 'ready to adopt', scope: 'admin', order: 11,
                fix: '#settings/rooms', fixLabel: 'rooms' };
    if (waits) add(wait(c));
    else if (!Array.isArray(pending)) add(unread(c));
    else if (!pending.length) add({ ...c, tone: 'ok', text: 'none' });
    else {
      add({ ...c, tone: 'warn',
            text: pending.length === 1
              ? 'a satellite is plugged in and ready to adopt'
              : `${pending.length} satellites are plugged in and ready to adopt` });
    }
  }

  // ── plugins ──
  {
    // The server's view (GET /api/plugins, open so that errors show for
    // everyone) plus what only this browser saw — a page script that
    // failed to load or threw while rendering.
    const c = { key: 'plugins', part: 'plugins', label: 'plugins', scope: 'open', order: 8,
                fix: '#plugins', fixLabel: 'plugins' };
    if (waits) add(wait(c));
    else if (!plugins) add(unread(c));
    else {
      const all = (plugins.plugins || []).filter((p) => p.status !== 'uninstalled');
      const bad = new Map();   // slug → {name, tone}
      all.forEach((p) => {
        const broken = p.status === 'load_error' || (p.enabled !== false
          && (!!p.web_load_error || (p.page_errors || []).length > 0));
        if (broken) bad.set(p.slug, { name: p.name || p.slug, tone: 'err' });
        else if (p.enabled !== false && p.status === 'degraded') bad.set(p.slug, { name: p.name || p.slug, tone: 'warn' });
      });
      Object.keys(pluginErrors || {}).forEach((slug) => {
        if ((pluginErrors[slug] || []).length) {
          const known = all.find((p) => p.slug === slug);
          bad.set(slug, { name: (known && known.name) || slug, tone: 'err' });
        }
      });
      const items = [...bad.entries()].map(([slug, b]) => ({ name: b.name, href: `#plugins/${slug}` }));
      if (bad.size === 1) {
        const [only] = [...bad.values()];
        add({ ...c, tone: only.tone, items,
              text: only.tone === 'err' ? `the ${only.name} plugin failed to load`
                : `the ${only.name} plugin is degraded` });
      } else if (bad.size > 1) {
        add({ ...c, tone: [...bad.values()].some((b) => b.tone === 'err') ? 'err' : 'warn', items,
              text: `${bad.size} plugins have problems` });
      } else {
        add({ ...c, tone: 'ok', text: all.length ? `${healthPlural(all.length, 'plugin')} · all working` : 'none installed' });
      }
    }
  }
  {
    // A media request is waiting and no provider plugin can fill it
    // (docs/FAQ.md). Never the request's own text.
    const c = { key: 'acq', part: 'plugins', label: 'media requests', scope: 'open', order: 9,
                fix: '#music', fixLabel: 'music' };
    if (waits) add(wait(c));
    else if (!acq) add(unread(c));
    else if (!acq.core_reachable) add({ ...c, tone: 'idle', text: 'not checked · the Domovoi server is away' });
    else {
      const stuck = (acq.acquisitions || []).filter((a) => a.status === 'pending'
        && (a.kind === 'url' ? acq.can_fulfill_url === false : acq.can_fulfill_query === false));
      if (stuck.length) {
        add({ ...c, tone: 'warn',
              text: stuck.length === 1
                ? 'a media request is waiting · no provider plugin can fill it'
                : `${stuck.length} media requests are waiting · no provider plugin can fill them` });
      } else add({ ...c, tone: 'ok', text: 'none stuck' });
    }
  }

  // ── internet ──
  {
    // Nobody has said whether this box has internet (INTERNET_ACCESS):
    // until an admin answers, it behaves as it always has. '' is the
    // unanswered answer; a server from before the question sends nothing.
    const c = { key: 'internet', part: 'internet', label: 'internet setting', scope: 'open', order: 14,
                fix: '#settings/internet', fixLabel: 'internet' };
    if (waits) add(wait(c));
    else if (!config) add(unread(c));
    else if (config.internet_access === '') {
      add({ ...c, scope: 'admin', tone: 'warn', text: 'tell Domovoi whether this box has internet' });
    } else if (config.internet_access === undefined) {
      add({ ...c, tone: 'idle', text: 'not asked by this server' });
    } else {
      add({ ...c, tone: 'ok', text: `has internet: ${HEALTH_INTERNET_WORDS[config.internet_access] || config.internet_access}` });
    }
  }
  {
    // Whether it works right now (GET /api/config/internet, an admin read).
    // Only a box meant to be always online calls "not connected" a problem.
    const c = { key: 'connection', part: 'internet', label: 'connection', scope: 'admin', order: 92,
                fix: '#settings/internet', fixLabel: 'internet' };
    const conn = internet && internet.connectivity;
    const answer = (internet && internet.answer) || (config && config.internet_access) || '';
    if (waits) add(wait(c));
    else if (!conn) add(unread(c));
    else if (conn.reason === 'connected') add({ ...c, tone: 'ok', text: 'working' });
    else if (conn.reason === 'turned_off') add({ ...c, tone: 'idle', text: 'turned off for this box' });
    else if (conn.reason === 'offline') {
      add({ ...c, tone: answer === 'always' ? 'warn' : 'idle', text: 'not connected right now' });
    } else add({ ...c, tone: 'idle', text: String(conn.reason || 'unknown') });
  }

  // ── containers ──
  add(healthGuardCheck(guard));
  return out;
};

/* The problems only, as Home lists them: err before warn, then in the
 * order Home always had. Each links to its part of Health. */
const healthProblems = (checks) => {
  const rank = { err: 0, warn: 1 };
  return checks
    .filter((c) => c.tone === 'err' || c.tone === 'warn')
    .map((c) => ({ ...c, href: c.claim ? undefined : `#settings/health/${c.part}` }))
    .sort((a, b) => (rank[a.tone] - rank[b.tone]) || (a.order - b.order));
};

/* Who sees which problems (HOME_PROBLEMS_VISIBILITY, admin-set):
 *   everyone  household members see the ones they can notice, an admin all
 *   summary   household members see one neutral line
 *   admins    household members see nothing
 * A shared screen shows the neutral line at most, whatever the setting —
 * even with an admin signed in on it. 'all' | 'open' | 'summary' | 'none'. */
const healthAudience = ({ viewer, visibility, shared }) => {
  const v = viewer || {};
  if (shared) return (v.isAdmin || visibility !== 'admins') ? 'summary' : 'none';
  if (v.isAdmin) return 'all';
  if (visibility === 'admins') return 'none';
  if (visibility === 'summary') return 'summary';
  return 'open';
};

/* ---- the page ------------------------------------------------------- */

const HEALTH_QUIET = { quiet: true };
const HEALTH_ROOM_EVENTS = [
  'satellites.presence.changed', 'satellites.display.changed', 'satellites.pending.changed',
];

const HealthRow = ({ c }) => {
  const problem = c.tone === 'err' || c.tone === 'warn';
  return (
    <div className={`health-row${problem ? ' health-row-problem' : ''}`} data-check={c.key} data-tone={c.tone}>
      <StatusDot tone={c.tone}/>
      <span className="health-label">{c.label}</span>
      <span className="health-text">
        {c.text}
        {c.at && <span className="health-when mono"> · {relTime(c.at)}</span>}
        {problem && c.items && c.items.length > 1 && (
          <span className="health-items">
            {c.items.map((it, i) => (
              <React.Fragment key={`${it.name}-${i}`}>
                {i > 0 && ', '}<a href={it.href} className="home-link">{it.name}</a>
              </React.Fragment>
            ))}
          </span>
        )}
      </span>
      {problem && c.fix && (
        <a className="home-link health-fix" href={c.fix}>open {c.fixLabel || 'it'}</a>
      )}
      {c.coreDown && <span className="health-fix"><CoreRestart/></span>}
    </div>
  );
};

const HealthPanel = ({ marks = [] }) => {
  const [, force] = React.useReducer((n) => n + 1, 0);
  React.useEffect(() => {
    if (typeof Auth === 'undefined') return undefined;
    try {
      const off = Auth.subscribe(force);
      if (!Auth.status && Auth.refreshStatus) Auth.refreshStatus();
      return off;
    } catch { return undefined; }
  }, []);
  const viewer = healthViewer();
  const admin = viewer.isAdmin;
  const shared = useSharedScreen();

  const cfg = useApiObject('/api/config', HEALTH_QUIET);
  const health = useApiObject('/api/health', HEALTH_QUIET);
  const guest = viewer.known && !viewer.unclaimed && !viewer.paired && !viewer.isAdmin;
  const sats = useApiObject(guest ? null : '/api/satellites', { eventTypes: HEALTH_ROOM_EVENTS, quiet: true });
  const plugins = useApiObject('/api/plugins', { eventTypes: ['plugins.changed'], quiet: true });
  const acq = useApiObject('/api/acquisitions?status=pending&limit=100',
                           { eventTypes: ['acquisitions.changed'], quiet: true });
  const approvals = useApiObject(admin ? '/api/satellites/approvals' : null, HEALTH_QUIET);
  const pending = useApiObject(admin ? '/api/satellites/pending' : null,
                               { eventTypes: ['satellites.pending.changed'], quiet: true });
  const version = useApiObject(admin ? '/api/config/version' : null, HEALTH_QUIET);
  const hardware = useApiObject(admin ? '/api/models/hardware' : null, HEALTH_QUIET);
  const internet = useApiObject(admin ? '/api/config/internet' : null, HEALTH_QUIET);
  const m = useCtrModel();

  // Browser-side plugin errors (index.html's pipeline): re-render on news.
  React.useEffect(() => {
    const pe = window.DomovoiPluginErrors;
    if (!pe || !pe.subscribe) return undefined;
    return pe.subscribe(force);
  }, []);
  React.useEffect(() => {
    const t = setInterval(() => {
      try { if (document.hidden) return; } catch { /* no document */ }
      health.refresh(); cfg.refresh();
      if (admin) { approvals.refresh(); version.refresh(); }
    }, HEALTH_POLL_MS);
    return () => clearInterval(t);
  }, [admin]);

  const checks = healthChecks({
    viewer, health: health.data, rooms: sats.data, plugins: plugins.data,
    pluginErrors: (window.DomovoiPluginErrors && window.DomovoiPluginErrors._bySlug) || {},
    acq: acq.data, approvals: approvals.data, pending: pending.data,
    version: version.data, hardware: hardware.data, config: cfg.data, internet: internet.data,
    guard: m.s,
  });
  const visibility = (cfg.data && cfg.data.home_problems_visibility) || 'everyone';
  const audience = (!admin && !cfg.data && !cfg.error) ? 'none' : healthAudience({ viewer, visibility, shared });
  const own = checks.filter((c) => audience === 'all' || c.scope !== 'admin');
  const problems = own.filter((c) => c.tone === 'err' || c.tone === 'warn');

  // #settings/health/<part>: that part on screen, once per arrival.
  const mark = marks[0] || null;
  const markDone = React.useRef(null);
  React.useEffect(() => {
    if (!mark) { markDone.current = null; return; }
    if (markDone.current === mark) return;
    const raf = window.requestAnimationFrame || ((fn) => setTimeout(fn, 0));
    raf(() => {
      try {
        const want = mark === 'containers' && m.open.length ? 'incidents' : mark;
        const el = document.querySelector(`[data-settings-mark="${want}"]`)
          || document.querySelector(`[data-health-part="${mark}"]`);
        if (!el) return;
        markDone.current = mark;
        el.scrollIntoView({ block: 'start' });
      } catch { /* the part is still there; only the scroll is missing */ }
    });
  });

  // Reads that failed: say so, so a green row never hides a check that
  // couldn't run.
  const sources = [['server', health], ['rooms', guest ? null : sats], ['plugins', plugins], ['media requests', acq],
    ...(admin ? [['approvals', approvals], ['adoption', pending], ['updates', version], ['disk', hardware],
                 ['internet', internet]] : [])];
  const failed = sources.filter(([, r]) => r && !r.loading && r.data == null && r.error != null).map(([n]) => n);

  if (audience === 'none' || audience === 'summary') {
    return (
      <Card title="health" padded>
        <div className="health-summary" data-audience={audience}>
          {audience === 'summary' && problems.length
            ? `${healthPlural(problems.length, 'thing needs', 'things need')} the admin's attention`
            : audience === 'summary' ? 'nothing needs attention' : 'the admin sees the house’s health'}
        </div>
        <div className="ctr-hint">
          {shared ? 'this is a shared screen · an admin sees the details on their own device'
            : 'an admin sees the details when signed in'}
        </div>
      </Card>
    );
  }

  return (
    <div className="health-page">
      <CtrOpenPart m={m}/>
      <div className="health-summary-line" data-problems={problems.length}>
        <StatusDot tone={problems.some((c) => c.tone === 'err') ? 'err' : problems.length ? 'warn' : 'ok'}/>
        <span>
          {problems.length ? `${healthPlural(problems.length, 'thing needs', 'things need')} attention`
            : 'everything checked looks fine'}
        </span>
        {failed.length > 0 && <span className="health-failed"> · couldn’t check {failed.join(', ')}</span>}
      </div>
      {!admin && viewer.known && (
        <div className="ctr-hint">
          sign in as the admin to see updates, disk space and rooms waiting for approval ·{' '}
          <button type="button" className="home-link"
                  onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</button>
        </div>
      )}
      {HEALTH_PARTS.map((part) => {
        const rows = own.filter((c) => c.part === part.id);
        if (!rows.length && part.id !== 'containers') return null;
        return (
          <div key={part.id} data-health-part={part.id} data-settings-mark={part.id}>
            <Card title={part.label}>
              <div className="health-rows">
                {rows.map((c) => <HealthRow key={c.key} c={c}/>)}
              </div>
              {part.id === 'containers' && <CtrContainersPart m={m}/>}
            </Card>
            {part.id === 'containers' && <CtrBehindPart m={m}/>}
          </div>
        );
      })}
      {m.toastNode}
    </div>
  );
};

// Settings shows a section for every panel registered here (settings.jsx
// settingsExtraSections): Health is System → Health.
window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections.health = { Panel: HealthPanel };

Object.assign(window, { healthChecks, healthProblems, healthAudience, healthViewer, HealthPanel });
