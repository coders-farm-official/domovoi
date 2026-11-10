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

/* A size in plain units: "31 GB", "9.4 GB", "640 MB". */
const healthSize = (bytes) => {
  const b = Number(bytes) || 0;
  const gb = b / (1024 ** 3);
  if (gb >= 10) return `${Math.round(gb)} GB`;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.max(1, Math.round(b / (1024 ** 2)))} MB`;
};

// Programs have less than this share of the memory free: worth a warning.
const HEALTH_MEM_LOW = 0.1;

/* The memory row's words (core /v1/admin/memory, domovoi/host_memory.py).
 * File cache "dominates" when it is at least what programs use and at
 * least a quarter of the total: that is when people read the box as full. */
const healthMemoryWords = (mem) => {
  const total = mem.total_bytes || 0;
  const programs = mem.programs_bytes || 0;
  const cache = mem.cache_bytes || 0;
  const low = total > 0 && (mem.available_bytes || 0) < total * HEALTH_MEM_LOW;
  return {
    low,
    cacheHeavy: total > 0 && cache >= programs && cache >= total / 4,
    text: low
      ? `running low · ${healthSize(mem.available_bytes || 0)} of ${healthSize(total)} left for programs`
      : `using ${healthSize(programs)} for programs · ${healthSize(cache)} file cache (handed back when needed)`,
  };
};

/* The file-cache trim's state under the memory row (core
 * /v1/admin/memory `cache_trim`: scripts/linux/cache-trim.py's last run,
 * every 2 minutes). null when it has never run here (an install the next
 * update hasn't reached yet): nothing is said. */
const healthTrimWords = (trim) => {
  if (!trim || !trim.checked_at) return null;
  if (!trim.enabled) {
    return trim.why_enabled === 'operator'
      ? 'automatic file-cache trimming is turned off on this server'
      : 'file cache isn’t trimmed here: this server hands it to programs as they need it';
  }
  const limit = trim.limit_bytes ? ` above ${healthSize(trim.limit_bytes)}` : '';
  const last = trim.last_trim;
  return `file cache is trimmed automatically${limit} · ${last
    ? `last trimmed ${relTime(last.at)} (freed ${healthSize(last.freed_bytes)})`
    : 'nothing to trim so far'}`;
};

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
                        approvals, pending, version, hardware, config, internet, guard, memory }) => {
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
            text: "this Domovoi isn't claimed yet · anyone on the network can change settings" });
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
    // The turn watchdog (core /v1/health `speech`, 2026-11-09): a speech
    // worker stuck on one request (only a restart frees it), or a request
    // that timed out lately.
    const speech = health && health.speech && typeof health.speech === 'object' ? health.speech : null;
    const lastTimeout = speech && speech.last_timeout;
    if (!health) add(unread(c));
    else if (waits) add(wait(c));
    else if (speech && speech.state === 'stalled') {
      const mins = Math.max(1, Math.round((speech.stuck_for_s || 0) / 60));
      add({ ...c, tone: 'err', fix: '#settings/about', fixLabel: 'restart',
            text: `speech recognition is stuck (for ${mins} min) · the rooms can't understand anyone until Domovoi restarts` });
    } else if (stt === 'unavailable') {
      add({ ...c, tone: 'err', text: "speech recognition is off · the rooms can't understand anyone" });
    } else if (stt === 'fallback') {
      add({ ...c, tone: 'warn', text: 'speech recognition is on its slower fallback model' });
    } else if (speech && speech.state === 'slow' && lastTimeout) {
      add({ ...c, tone: 'warn', at: lastTimeout.at, fix: '#settings/about', fixLabel: 'restart',
            text: `a spoken request timed out while ${lastTimeout.stage || 'being answered'} · if it keeps happening, restart Domovoi` });
    } else add({ ...c, tone: 'ok', text: stt ? 'ready' : 'not reported by this server' });
    // A request that timed out later, while its answer was being worked
    // out: a slow or loading model, most likely — not speech recognition,
    // and nothing a restart fixes. Shown only while recent (15 minutes).
    const reply = speech && speech.last_reply_timeout;
    if (health && !waits && reply) {
      add({ key: 'answers', part: 'server', label: 'answers', scope: 'open', order: 4.5,
            tone: 'warn', at: reply.at,
            text: 'a spoken request got no answer in time · the language model may be slow or still loading' });
    }
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
  {
    // Where the memory goes (an admin read; Health only, Home never asks).
    // Information more than a check: amber only when programs are short.
    const c = { key: 'memory', part: 'server', label: 'memory', scope: 'admin', order: 16 };
    if (waits) add(wait(c));
    else if (!memory) add(unread(c));
    else if (memory.available === false) {
      add({ ...c, tone: 'idle', text: memory.why || 'not available on this system' });
    } else {
      const w = healthMemoryWords(memory);
      add({ ...c, tone: w.low ? 'warn' : 'ok', text: w.text, memory,
            sub: healthTrimWords(memory.cache_trim) });
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
                  text: off[0].battery && off[0].battery.probably_out
                    ? `${off[0].room_id} is offline · probably out of battery`
                    : `${off[0].room_id} is offline · it can't hear anyone right now` }
              : { tone: 'warn',
                  text: off.every((s) => s.battery && s.battery.probably_out)
                    ? `${off.length} rooms offline · probably out of battery`
                    : `${off.length} rooms offline` }),
            'none');
  // A phone room's battery (V040): only when the house has one, so a house
  // of Pis never sees a "battery" row. `low` is the server's: a warning at
  // 20 % or 10 % is open, the phone isn't on its charger and the room's
  // "Notify when the battery is low" is on.
  if (list && list.some((s) => s.board === 'android_room')) {
    roomCheck({ key: 'battery', part: 'rooms', label: 'battery', scope: 'open', order: 4,
                fix: '#settings/rooms', fixLabel: 'rooms' },
              (xs) => xs.filter((s) => s.status === 'online' && s.battery && s.battery.low),
              (low) => {
                const pct = (s) => (s.battery.percent == null ? '' : ` (${Math.round(s.battery.percent)}%)`);
                const worst = low.some((s) => s.battery.percent != null && s.battery.percent <= 10);
                return { tone: worst ? 'err' : 'warn',
                         text: low.length === 1
                           ? `${low[0].room_id}'s battery is low${pct(low[0])} · plug it in`
                           : `${low.length} rooms' batteries are low · plug them in` };
              },
              'no phone room is low');
  }
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
              : `${approvals.length} rooms are waiting for approval` });
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
              ? 'a room device is plugged in and ready to adopt'
              : `${pending.length} room devices are plugged in and ready to adopt` });
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
  'satellites.presence.changed', 'satellites.display.changed', 'satellites.pending.changed', 'room_battery.changed',
];

const HEALTH_MEM_KIND = { service: 'Domovoi service', container: 'container', process: 'program' };
const HEALTH_MEM_SOURCE = { services: 'Domovoi services', containers: 'containers', processes: 'other programs' };

/* The memory row unfolded: what the total is made of, then what uses it,
 * largest first. Plain words; the honest part is the file cache. */
const HealthMemoryDetail = ({ mem }) => {
  const w = healthMemoryWords(mem);
  const wsl = mem.host === 'wsl';
  const rows = [...(mem.breakdown || [])].sort((a, b) => (b.bytes || 0) - (a.bytes || 0));
  const facts = [`${healthSize(mem.total_bytes)} in all`, `${healthSize(mem.free_bytes)} unused`];
  if ((mem.shared_bytes || 0) >= 256 * 1024 * 1024) facts.push(`${healthSize(mem.shared_bytes)} shared`);
  if ((mem.swap_used_bytes || 0) >= 64 * 1024 * 1024) facts.push(`${healthSize(mem.swap_used_bytes)} moved to swap`);
  return (
    <div className="health-mem" data-health-memory="detail">
      <div className="health-mem-facts">{facts.join(' · ')}</div>
      {wsl && (
        <p className="health-mem-note" data-mem-note="limit">
          Domovoi may use up to about {healthSize(mem.total_bytes)} of this PC’s memory: the share
          its installer gave it.
        </p>
      )}
      {w.cacheHeavy && (
        <p className="health-mem-note" data-mem-note="cache">
          Most of it is file cache: files read lately, kept in memory so they open fast. That’s normal,
          and programs get it back the moment they need it.
          {wsl && (mem.cache_trim && mem.cache_trim.enabled
            ? ` Domovoi hands what is over ${healthSize(mem.cache_trim.limit_bytes || 0)} back to Windows every few minutes.`
            : ' Windows gets it back once Domovoi has been idle for a little while.')}
        </p>
      )}
      {rows.length > 0 ? (
        <ul className="health-mem-list">
          {rows.map((r, i) => (
            <li key={`${r.kind}-${r.name}-${i}`} className="health-mem-item" data-kind={r.kind}>
              <span className="health-mem-name">
                {r.label}
                {r.kind === 'process' && r.count > 1 && <span className="health-mem-sub"> · {r.count} running</span>}
                {r.kind === 'container' && r.label === 'other container' && (
                  <span className="health-mem-sub mono"> · {r.name}</span>
                )}
              </span>
              <span className="health-mem-kind">{HEALTH_MEM_KIND[r.kind] || r.kind}</span>
              <span className="health-mem-size mono">{healthSize(r.bytes)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="health-mem-facts">nothing to break down</div>
      )}
      {rows.some((r) => r.kind === 'process') && (
        <div className="health-mem-facts">
          programs that share memory count it once each, so their sizes can add up to more than they use
        </div>
      )}
      {(mem.missing || []).map((m) => (
        <div key={m.source} className="health-mem-missing" data-mem-missing={m.source}>
          couldn’t check {HEALTH_MEM_SOURCE[m.source] || m.source}: {m.why}
        </div>
      ))}
    </div>
  );
};

/* "This PC": how much of the PC Domovoi may use, on the Windows install
 * (core /v1/admin/host-limits, domovoi/host_limits.py; DomovoiHost writes
 * it into .wslconfig). Shown only where DomovoiHost reports the PC. Each
 * limit is Automatic (the installer's share) or a slider inside the PC's
 * real bounds; it applies when Domovoi next restarts its Linux system. */
const healthLimitsWords = (lim, draft) => {
  const mem = draft.memory_gb;
  const notes = [];
  if (mem != null && mem < lim.warn_below_gb) {
    notes.push({ key: 'low', tone: 'warn',
                 text: `Under ${lim.warn_below_gb} GB the AI models, speech recognition and the rooms’ music may not all fit, and Domovoi gets slow.` });
  }
  const effective = mem != null ? mem : lim.memory_gb.default;
  if (effective != null && effective < lim.training_gb) {
    notes.push({ key: 'training', tone: 'idle',
                 text: `Training wake words needs ${lim.training_gb} GB.` });
  }
  return notes;
};

const HealthLimitSlider = ({ label, unit, bounds, auto, autoText, value, onAuto, onValue, id }) => (
  <div className="health-limit" data-limit={id}>
    <div className="health-limit-head">
      <span className="health-limit-label">{label}</span>
      <label className="health-limit-auto">
        <input type="checkbox" checked={auto} onChange={(e) => onAuto(e.target.checked)}/>
        {' '}automatic{autoText ? ` (${autoText})` : ''}
      </label>
    </div>
    {!auto && (
      <div className="config-slider">
        <div className="config-slider-track">
          <input type="range" min={bounds.min} max={bounds.max} step={1} value={value}
                 aria-label={label} aria-valuetext={`${value} ${unit}`}
                 onChange={(e) => onValue(Number(e.target.value))}/>
          <div className="config-slider-ends">
            <span>{bounds.min} {unit}</span>
            <span>{bounds.max} {unit}</span>
          </div>
        </div>
        <span className="health-limit-value mono">{value} {unit}</span>
      </div>
    )}
  </div>
);

const HealthLimitsCard = () => {
  const lim = useApiObject('/api/models/host-limits', HEALTH_QUIET);
  const d = lim.data;
  const [draft, setDraft] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState(null);
  const [confirm, setConfirm] = React.useState(false);
  // What this page last saved: DomovoiHost reports it as `chosen` only once
  // it has read the request (within 30 s), and the core's own copy
  // (`requested`) until then, so a Restart right after a Save never sends
  // the old values back.
  const [sent, setSent] = React.useState(null);
  if (!d || !d.available) return null;
  const saved = sent || d.requested || { memory_gb: d.memory_gb.chosen, processors: d.processors.chosen };
  const cur = draft || saved;
  const changed = cur.memory_gb !== saved.memory_gb || cur.processors !== saved.processors;
  const send = async (body, restart) => {
    setBusy(true); setMsg(null);
    try {
      const limits = { memory_gb: body.memory_gb, processors: body.processors };
      await apiPost('/api/models/host-limits', { ...limits, restart_now: !!restart });
      setSent(limits); setDraft(null); setConfirm(false);
      setMsg(restart ? 'Restarting Domovoi so it starts with its new limits · back in a couple of minutes'
        : 'Saved · it applies when Domovoi next restarts');
      lim.refresh();
    } catch (e) {
      setMsg(`Not saved: ${(e && e.message) || 'the server refused it'}`);
    } finally { setBusy(false); }
  };
  const memDefault = d.memory_gb.default;
  const running = d.running || {};
  const notes = healthLimitsWords(d, cur);
  return (
    <div data-health-part="pc" data-settings-mark="pc">
      <Card title="This PC">
        <div className="health-limits" data-health-limits="card">
          <p className="health-mem-note">
            This PC has {d.ram_gb} GB of memory and {d.logical_processors} processor cores. Domovoi
            may use up to:
          </p>
          <HealthLimitSlider id="memory" label="Memory" unit="GB" bounds={d.memory_gb}
            auto={cur.memory_gb == null} autoText={memDefault != null ? `${memDefault} GB` : ''}
            value={cur.memory_gb != null ? cur.memory_gb : (memDefault || d.memory_gb.min)}
            onAuto={(a) => setDraft({ ...cur, memory_gb: a ? null : (memDefault || d.memory_gb.min) })}
            onValue={(v) => setDraft({ ...cur, memory_gb: v })}/>
          <HealthLimitSlider id="processors" label="Processor cores" unit="cores" bounds={d.processors}
            auto={cur.processors == null} autoText={`all ${d.logical_processors}`}
            value={cur.processors != null ? cur.processors : d.processors.max}
            onAuto={(a) => setDraft({ ...cur, processors: a ? null : d.processors.max })}
            onValue={(v) => setDraft({ ...cur, processors: v })}/>
          {notes.map((n) => (
            <div key={n.key} className={n.tone === 'warn' ? 'health-mem-missing' : 'health-mem-facts'}
                 data-limit-note={n.key}>{n.text}</div>
          ))}
          <div className="health-mem-facts">
            The new limits apply when Domovoi next restarts: when this PC restarts, or now with
            “Restart Domovoi now” (Domovoi is offline for a couple of minutes).
          </div>
          {d.pending && (
            <div className="health-mem-facts" data-limit-state="pending">
              Domovoi still runs on {running.memory_gb != null ? `${running.memory_gb} GB` : 'its old memory'}
              {running.processors ? ` and ${running.processors} cores` : ''} until it restarts.
            </div>
          )}
          {d.waiting_for_host && (
            <div className="health-mem-facts" data-limit-state="waiting">
              Saved · Domovoi’s Windows side picks it up within a minute.
            </div>
          )}
          {d.request === 'invalid' && (
            <div className="health-mem-missing" data-limit-state="refused">
              Domovoi’s Windows side refused the last change{d.request_problem ? `: ${d.request_problem}` : ''}.
            </div>
          )}
          {d.restart === 'failed' && (
            <div className="health-mem-missing" data-limit-state="restart-failed">
              The last restart for new limits didn’t finish · Save logs from the tray icon says why.
            </div>
          )}
          {msg && <div className="health-mem-facts" data-limit-state="msg">{msg}</div>}
          <div className="health-limits-actions">
            <Button variant="primary" type="button" disabled={!changed || busy} data-limit-action="save"
                    onClick={() => send(cur, false)}>Save</Button>
            {(d.pending || changed) && !confirm && (
              <Button type="button" disabled={busy} data-limit-action="restart"
                      onClick={() => setConfirm(true)}>Restart Domovoi now</Button>
            )}
            {confirm && (
              <>
                <Button variant="primary" type="button" disabled={busy} data-limit-action="confirm"
                        onClick={() => send(cur, true)}>Yes, restart (offline a couple of minutes)</Button>
                <Button type="button" disabled={busy} data-limit-action="cancel"
                        onClick={() => setConfirm(false)}>Cancel</Button>
              </>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
};

const HealthRow = ({ c }) => {
  const problem = c.tone === 'err' || c.tone === 'warn';
  const [open, setOpen] = React.useState(false);
  return (
    <div className={`health-row${problem ? ' health-row-problem' : ''}`} data-check={c.key} data-tone={c.tone}>
      <StatusDot tone={c.tone}/>
      <span className="health-label">{c.label}</span>
      <span className="health-text">
        {c.text}
        {c.at && <span className="health-when mono"> · {relTime(c.at)}</span>}
        {c.sub && <span className="health-row-sub" data-health-sub={c.key}>{c.sub}</span>}
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
      {c.memory && (
        <button type="button" className="home-link health-fix health-mem-toggle" aria-expanded={open}
                onClick={() => setOpen((o) => !o)}>
          {open ? 'hide details' : 'what uses it'}
        </button>
      )}
      {c.memory && open && <HealthMemoryDetail mem={c.memory}/>}
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
  // Read once per visit, not polled: the core looks at Docker and every
  // process for it (bounded, cached 15 s there).
  const memory = useApiObject(admin ? '/api/models/memory' : null, HEALTH_QUIET);
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
    guard: m.s, memory: memory.data,
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
                 ['internet', internet], ['memory', memory]] : [])];
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
          sign in as the admin to see updates, disk space, memory and rooms waiting for approval ·{' '}
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
      {admin && <HealthLimitsCard/>}
      {m.toastNode}
    </div>
  );
};

// Settings shows a section for every panel registered here (settings.jsx
// settingsExtraSections): Health is System → Health.
window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections.health = { Panel: HealthPanel };

Object.assign(window, { healthChecks, healthProblems, healthAudience, healthViewer, healthSize, healthTrimWords,
  healthLimitsWords, HealthPanel });
