/* Shared primitives for the Domovoi Web UI kit. Loaded via Babel. */

const { useState, useEffect, useRef } = React;

/* ---- Lucide icon helper ------------------------------------- */
/*
 * Lucide's runtime works by scanning the DOM for ``<i data-lucide="…">``
 * placeholders and swapping them for inline SVG. The App-level
 * ``createIcons`` effect only fires on route / theme changes — not
 * when a child page mutates its own state. So icons that mount
 * inside dynamically-loaded content (search result rows, paginated
 * pages, async-fetched lists) stayed invisible. Trigger a debounced
 * rescan after every Icon mount so any newly-attached placeholder
 * gets converted regardless of where it appeared from. Already-
 * converted icons short-circuit inside lucide, so the cost is just
 * a single querySelectorAll per debounce window.
 */
let _lucideScanPending = false;
const _scheduleLucideScan = () => {
  if (_lucideScanPending) return;
  _lucideScanPending = true;
  setTimeout(() => {
    _lucideScanPending = false;
    if (window.lucide) {
      window.lucide.createIcons({ attrs: { 'stroke-width': 1.5 } });
    }
  }, 0);
};

/*
 * Icon renders a lucide glyph, but through a wrapper the way it does for a
 * reason: lucide converts a placeholder by REPLACING the <i data-lucide> node
 * with a fresh <svg> (parentNode.replaceChild). If React owns that <i>, the
 * swap desyncs React's tree — so a later `name` change (play⇄pause, mute,
 * chevrons) patches the now-detached <i> and the visible <svg> never updates,
 * leaving the glyph frozen on whatever it first rendered.
 *
 * The fix: React owns only the stable <span>. On mount and on every `name`
 * change we imperatively drop a fresh <i data-lucide> inside it and let lucide
 * swap THAT node — it's ours, not React's, so recreating it is safe and the
 * icon actually updates. The class/size/stroke live on the inner <i> (lucide
 * copies them onto the <svg>), so descendant CSS like `.nav-item .ico` and
 * `.btn .ic` matches exactly as before.
 */
const Icon = ({ name, size = 16, stroke = 1.5, className = '' }) => {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const host = ref.current;
    if (!host) return;
    host.textContent = '';
    const i = document.createElement('i');
    i.setAttribute('data-lucide', name);
    i.setAttribute('data-stroke-width', stroke);
    if (className) i.className = className;
    i.style.cssText = `width:${size}px;height:${size}px;display:inline-flex;flex-shrink:0`;
    host.appendChild(i);
    _scheduleLucideScan();
  }, [name, size, stroke, className]);
  return <span ref={ref} style={{ display: 'inline-flex', flexShrink: 0, lineHeight: 0 }}/>;
};

/* ---- Cat glyph (the only custom mark in chrome) ------------- */
const DomovoiGlyph = ({ size = 22, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
    <path d="M5 13 C5 9.4 7.5 7.5 12 7.5 C16.5 7.5 19 9.4 19 13 L19 16.2 C19 18 17.4 19.4 15.6 19.4 L8.4 19.4 C6.6 19.4 5 18 5 16.2 Z"/>
    <path d="M5.6 9.5 L4.4 5.6 L8.5 7.6"/>
    <path d="M18.4 9.5 L19.6 5.6 L15.5 7.6"/>
    <circle cx="9.6"  cy="13.4" r="0.55" fill="currentColor" stroke="none"/>
    <circle cx="14.4" cy="13.4" r="0.55" fill="currentColor" stroke="none"/>
    <path d="M11.3 15.4 L12 16.1 L12.7 15.4"/>
    <path d="M12 16.1 L12 17"/>
    <path d="M7.6 15.4 L9.4 15.7 M7.6 16.4 L9.4 16.3"/>
    <path d="M16.4 15.4 L14.6 15.7 M16.4 16.4 L14.6 16.3"/>
  </svg>
);

const SleepingDomovoi = ({ size = 80 }) => (
  <svg width={size} height={size * 0.5} viewBox="0 0 64 32" fill="none"
       stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M6 22 C6 14 14 10 24 11 C36 12 46 14 56 16 C58 16.4 58.6 18.4 57 19.6 C50 24.4 40 26 30 25.6 C18 25 6 26 6 22 Z"/>
    <path d="M50 16.6 C54 14 56 11.4 54 9 C52 7 49 8 48 11"/>
    <path d="M11.5 13 L10 8.4 L15 10.6"/>
    <path d="M18 11.4 L18 6.6 L22 9.4"/>
    <path d="M12 18 Q14 19.4 16 18"/>
    <path d="M9 19.6 L11 19.4 M9 21 L11 20.6"/>
    <path d="M28 7 L34 7 L28 12 L34 12" strokeWidth="1.1" opacity="0.5"/>
    <path d="M37 4 L41 4 L37 8 L41 8" strokeWidth="1" opacity="0.35"/>
  </svg>
);

/* Domovoi with headphones — for music page empty states */
const HeadphonesDomovoi = ({ size = 96 }) => (
  <svg width={size} height={size * 0.78} viewBox="0 0 80 62" fill="none"
       stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {/* sitting body */}
    <path d="M22 52 C22 38 30 30 40 30 C50 30 58 38 58 52 Z"/>
    {/* head */}
    <ellipse cx="40" cy="26" rx="13" ry="11"/>
    {/* ears */}
    <path d="M30 18 L28 10 L34 14"/>
    <path d="M50 18 L52 10 L46 14"/>
    {/* face */}
    <path d="M36 26 Q37.5 27.2 39 26" strokeWidth="1.2"/>
    <path d="M44 26 Q42.5 27.2 41 26" strokeWidth="1.2"/>
    <path d="M40 28.6 L40 30 M37 30 L43 30" strokeWidth="1.1"/>
    {/* whiskers */}
    <path d="M28 28 L34 29 M28 30.5 L34 30" strokeWidth="0.9" opacity="0.55"/>
    <path d="M52 28 L46 29 M52 30.5 L46 30" strokeWidth="0.9" opacity="0.55"/>
    {/* tail curling forward */}
    <path d="M58 50 C66 48 68 42 64 38" opacity="0.8"/>
    {/* headphones — band over head + cups */}
    <path d="M28 22 C28 12 52 12 52 22" strokeWidth="1.6"/>
    <rect x="24" y="20" width="6" height="9" rx="2" fill="currentColor" opacity="0.18"/>
    <rect x="24" y="20" width="6" height="9" rx="2" strokeWidth="1.5"/>
    <rect x="50" y="20" width="6" height="9" rx="2" fill="currentColor" opacity="0.18"/>
    <rect x="50" y="20" width="6" height="9" rx="2" strokeWidth="1.5"/>
    {/* music notes drifting up */}
    <path d="M18 18 L18 8 L23 6" strokeWidth="1.1" opacity="0.55"/>
    <ellipse cx="16.5" cy="18.5" rx="2.4" ry="1.6" strokeWidth="1.1" opacity="0.55"/>
    <path d="M64 14 L64 6 L68 4" strokeWidth="1" opacity="0.4"/>
    <ellipse cx="62.6" cy="14.5" rx="2" ry="1.4" strokeWidth="1" opacity="0.4"/>
  </svg>
);

/* ---- Status dot (with optional live pulse) ------------------ */
const StatusDot = ({ tone = 'idle', live = false }) => {
  const colorVar = {
    ok: 'var(--ok)', warn: 'var(--warn)', err: 'var(--err)',
    idle: 'var(--idle)', brand: 'var(--brand)'
  }[tone] || 'var(--idle)';
  const haloVar = {
    ok:    'oklch(0.72 0.17 145 / 0.30)',
    brand: 'var(--brand-halo)',
    warn:  'oklch(0.80 0.16 60 / 0.30)',
    err:   'oklch(0.62 0.21 25 / 0.30)',
  }[tone] || 'transparent';
  if (!live) return <span className="dot" style={{ background: colorVar }}/>;
  return (
    <span className="dot-wrap">
      <span className="halo" style={{ background: haloVar }}/>
      <span className="core" style={{ background: colorVar }}/>
    </span>
  );
};

/* ---- Pill (inline status) ----------------------------------- */
const Pill = ({ tone = 'idle', live = false, children }) => (
  <span className={`pill ${tone}`}>
    <StatusDot tone={tone === 'live' ? 'brand' : tone} live={live}/>
    {children}
  </span>
);

/* ---- Room chip ---------------------------------------------- */
const RoomChip = ({ name, online }) => (
  <span className="room-chip">
    <StatusDot tone={online ? 'ok' : 'idle'} live={online}/>
    {name}
  </span>
);

/* ---- Avatar (deterministic colour from initial) ------------- */
const avaPalette = {
  K: ['oklch(0.86 0.05 75)',  'oklch(0.72 0.12 60)'],
  S: ['oklch(0.86 0.05 200)', 'oklch(0.62 0.14 220)'],
  A: ['oklch(0.86 0.05 145)', 'oklch(0.62 0.14 160)'],
  R: ['oklch(0.86 0.05 30)',  'oklch(0.62 0.14 30)'],
  default: ['oklch(0.86 0.04 80)', 'oklch(0.66 0.04 80)']
};
const Avatar = ({ name = '', size }) => {
  const initial = (name[0] || '?').toUpperCase();
  const [a, b] = avaPalette[initial] || avaPalette.default;
  return (
    <span className={`ava ${size === 'lg' ? 'lg' : ''}`}
          style={{ background: `linear-gradient(135deg, ${a}, ${b})` }}>
      {initial}
    </span>
  );
};

/* ---- Card --------------------------------------------------- */
const Card = ({ title, sub, action, padded = false, children }) => (
  <section className="card">
    {(title || action) && (
      <div className="card-head">
        <div>
          {title && <div className="t">{title}</div>}
          {sub   && <div className="s">{sub}</div>}
        </div>
        {action}
      </div>
    )}
    <div className={`card-body ${padded ? 'padded' : ''}`}>{children}</div>
  </section>
);

/* ---- Empty state -------------------------------------------- */
const Empty = ({ title, sub, glyph = 'sleeping', action }) => {
  const Glyph = glyph === 'headphones' ? HeadphonesDomovoi : SleepingDomovoi;
  const size = glyph === 'headphones' ? 104 : 92;
  return (
    <div className="empty">
      <div className="ill"><Glyph size={size}/></div>
      <div className="t">{title}</div>
      {sub && <div className="s">{sub}</div>}
      {action && <div style={{ marginTop: 12 }}>{action}</div>}
    </div>
  );
};

/* ---- Buttons ------------------------------------------------ */
const Button = ({ variant = 'secondary', icon, children, ...rest }) => (
  <button className={`btn btn-${variant}`} {...rest}>
    {icon && <Icon name={icon} size={14}/>}
    {children}
  </button>
);
const IconButton = ({ name, ...rest }) => (
  <button className="btn btn-ghost btn-icon" {...rest}>
    <Icon name={name} size={14}/>
  </button>
);

/* ---- Time helpers ------------------------------------------- */
/* Use wall-clock NOW so relative timestamps tick as data flows in.
 * The skill's demo bundle pinned NOW to a fixed sample date so its
 * static fixtures rendered deterministic "2m ago" labels; the wired
 * UI takes timestamps from the live DB and needs the real clock. */
const relTime = (iso) => {
  if (!iso) return '—';
  const t = new Date(iso);
  const s = (new Date() - t) / 1000;
  if (s < 30) return 'just now';
  if (s < 90) return '1m ago';
  if (s < 3600) return `${Math.round(s/60)}m ago`;
  if (s < 86400) return `${Math.round(s/3600)}h ago`;
  return `${Math.round(s/86400)}d ago`;
};
const fmtDur = (sec) => {
  if (sec == null) return '—';
  const m = Math.floor(sec / 60); const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2,'0')}`;
};

/* ---- Toast (page-level helper) ----------------------------- */
/*
 * Stacked: every fire() gets its OWN toast box with its OWN removal
 * timer. The old single-slot version let a burst of messages truncate
 * each other — toast A's cleanup timer would clear replacement toast B
 * after a few hundred ms, so long error details vanished unread.
 * Longer messages linger longer (up to 12 s); click a toast to dismiss.
 */
const useToast = () => {
  const [items, setItems] = React.useState([]);
  const nextId = React.useRef(0);
  const dismiss = (id) => setItems((cur) => cur.filter((t) => t.id !== id));
  const fire = (msg) => {
    const id = ++nextId.current;
    const text = String(msg);
    setItems((cur) => [...cur, { id, text }]);
    const ttl = Math.min(12000, 2400 + text.length * 35);
    setTimeout(() => dismiss(id), ttl);
  };
  const node = items.length > 0 && (
    <div style={{ position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
                  display: 'flex', flexDirection: 'column-reverse', alignItems: 'center',
                  gap: 8, zIndex: 60, maxWidth: 'min(90vw, 560px)' }}>
      {items.map((t) => (
        <div key={t.id} onClick={() => dismiss(t.id)} title="dismiss"
             style={{ background: 'var(--overlay)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                      boxShadow: 'var(--shadow-md), var(--inner-highlight)', padding: '10px 14px',
                      fontSize: 13, color: 'var(--fg)', display: 'flex', alignItems: 'center', gap: 8,
                      cursor: 'pointer' }}>
          <StatusDot tone="brand" live/>
          <span style={{ overflowWrap: 'anywhere' }}>{t.text}</span>
        </div>
      ))}
    </div>
  );
  return [fire, node];
};

/* ---- Tabs (page-level helper) ------------------------------ */
const Tabs = ({ tabs, value, onChange, padX = 0 }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 0, borderBottom: '1px solid var(--border)',
                padding: `0 ${padX}px` }}>
    {tabs.map(t => {
      const active = t.id === value;
      return (
        <button key={t.id} onClick={() => onChange(t.id)}
          // Icon-only mode (set `icon` on a tab) keeps a row of many tabs from
          // overflowing — the label rides as a native hover tooltip. Tabs
          // without `icon` render their text label exactly as before.
          title={t.icon ? t.label : undefined}
          aria-label={t.icon ? t.label : undefined}
          style={{ font: 'inherit', fontSize: 13, fontWeight: 500,
                   background: 'transparent', border: 'none', cursor: 'pointer',
                   padding: t.icon ? '12px 13px' : '12px 14px', color: active ? 'var(--fg)' : 'var(--fg-muted)',
                   borderBottom: `2px solid ${active ? 'var(--brand)' : 'transparent'}`,
                   marginBottom: '-1px', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {t.icon ? <Icon name={t.icon} size={17}/> : t.label}
          {t.count != null && <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>{t.count}</span>}
        </button>
      );
    })}
  </div>
);

/* ---- Sidebar ------------------------------------------------ */
const NavItem = ({ icon, label, badge, active, onClick }) => (
  <div className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}>
    <Icon name={icon} className="ico" size={16}/>
    <span>{label}</span>
    {badge != null && <span className="badge">{badge}</span>}
  </div>
);

/* Core nav items. `order` values are the published core_nav numbers
 * (design §5.2, echoed in /api/plugins/manifest) so plugin authors can
 * slot pages deliberately. `countKey` reads useSidebarCounts. */
const CORE_NAV_ITEMS = [
  { route: 'chat',       icon: 'message-square', label: 'Chat',    order: 8 },
  { route: 'music',      icon: 'music',       label: 'Music',      order: 10, countKey: 'music' },
  { route: 'podcasts',   icon: 'podcast',     label: 'Podcasts',   order: 12 },
  { route: 'audiobooks', icon: 'book-open',   label: 'Audiobooks', order: 14 },
  { route: 'videos',     icon: 'film',        label: 'Videos',     order: 15 },
  // order 16 is deliberately free — the Image Generation plugin's Images
  // page slots there (nav_order 16 in its manifest) when installed.
  { route: 'news',       icon: 'newspaper',   label: 'News',       order: 18 },
  { route: 'people',     icon: 'users',       label: 'People',     order: 20, countKey: 'people' },
  { route: 'satellites', icon: 'radio-tower', label: 'Satellites', order: 30, countKey: 'satellites' },
  { route: 'calendar',   icon: 'calendar',    label: 'Calendar',   order: 40, countKey: 'calendar' },
  { route: 'files',      icon: 'folder',      label: 'Files',      order: 45 },
  { route: 'plugins',    icon: 'blocks',      label: 'Plugins',    order: 95 },
];

/* Poll each plugin page's declared badge endpoint on a shared, jittered
 * 30 s cadence (design §5.3 badge contract): open GET returning JSON;
 * render body[key] when it's a positive int; non-200 / non-JSON /
 * missing key / 0 ⇒ no badge, never an error surface. */
const usePluginBadges = (manifest) => {
  const [badges, setBadges] = React.useState({});   // route → int
  React.useEffect(() => {
    const pages = [];
    (manifest?.plugins || []).forEach((p) => (p.pages || []).forEach((pg) => {
      if (pg.badge && pg.badge.endpoint && pg.badge.key) pages.push(pg);
    }));
    if (!pages.length) return;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      const next = {};
      await Promise.all(pages.map(async (pg) => {
        try {
          const body = await apiGet(pg.badge.endpoint);
          const v = body && body[pg.badge.key];
          if (typeof v === 'number' && v > 0) next[pg.route] = v;
        } catch { /* no badge — by contract */ }
      }));
      if (cancelled) return;
      setBadges(next);
      timer = setTimeout(poll, 30000 + Math.random() * 3000);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [manifest]);
  return badges;
};

/* Small wrapper for plugin nav icons: manifest-supplied SVG URL when
 * present, generic puzzle-piece glyph otherwise. */
const PluginNavIcon = ({ src }) => src
  ? <img src={src} alt="" className="ico"
         style={{ width: 16, height: 16, flexShrink: 0 }}/>
  : <Icon name="puzzle" className="ico" size={16}/>;

const Sidebar = ({ route, setRoute, counts, manifest }) => {
  const badges = usePluginBadges(manifest);
  // Interleave core + plugin items by nav order; equal values sort
  // core-first, then plugin slug (design §5.2).
  const items = CORE_NAV_ITEMS.map((it) => ({ ...it, core: true, slug: '' }));
  (manifest?.plugins || []).forEach((p) => (p.pages || []).forEach((pg) => {
    items.push({
      route: pg.route, label: pg.nav_label || pg.route,
      iconSrc: pg.nav_icon, order: pg.nav_order ?? 50,
      core: false, slug: p.slug,
    });
  }));
  items.sort((a, b) => (a.order - b.order)
    || (a.core === b.core ? 0 : (a.core ? -1 : 1))
    || String(a.slug).localeCompare(String(b.slug))
    || String(a.route).localeCompare(String(b.route)));
  return (
    <aside className="sidebar">
      <div className="brand-row">
        <DomovoiGlyph size={22} className="glyph"/>
        <div className="word">domovoi</div>
        <div className="ver">/ 1.0</div>
      </div>

      <div>
        <div className="nav-section">workspace</div>
        <nav className="nav">
          {items.map((it) => (
            <div key={it.route}
                 className={`nav-item ${route === it.route ? 'active' : ''}`}
                 onClick={() => setRoute(it.route)}>
              {it.core
                ? <Icon name={it.icon} className="ico" size={16}/>
                : <PluginNavIcon src={it.iconSrc}/>}
              <span>{it.label}</span>
              {it.core && it.countKey && counts[it.countKey] != null
                && <span className="badge">{counts[it.countKey]}</span>}
              {!it.core && badges[it.route] != null
                && <span className="badge">{badges[it.route]}</span>}
            </div>
          ))}
        </nav>
      </div>

      <SidebarFooter/>
    </aside>
  );
};

// Who you are and which server you're pointed at.
//
//   * host — ServerStore.currentLabel(), the same origin the Topbar shows.
//   * name — Domovoi has no per-user login, only an admin gate. So report
//     the auth state honestly rather than inventing a person: the sidebar
//     doubles as the answer to "why is that page asking me to sign in?".
const SidebarFooter = () => {
  const [, force] = React.useReducer((n) => n + 1, 0);

  // Auth lives in JS memory and changes on login/logout — re-render with it.
  React.useEffect(() => {
    if (typeof Auth === 'undefined') return;
    try { return Auth.subscribe(force); } catch { /* auth.js absent */ }
  }, []);

  let signedIn = false;
  try { signedIn = typeof Auth !== 'undefined' && Auth.isLoggedIn(); } catch {}

  const host = (() => {
    try { return ServerStore.currentLabel(); } catch { return window.location.host; }
  })();

  return (
    <div className="footer">
      <div className="who">
        <Avatar name={signedIn ? 'Admin' : 'Guest'}/>
        <div>
          <div className="name">{signedIn ? 'Admin' : 'Not signed in'}</div>
          <div className="host" title={host}>{host}</div>
        </div>
      </div>
    </div>
  );
};

/* ---- Domovoi config fields (rendered by the Settings page) ----
 * These primitives back the Configuration tab in settings.jsx (ConfigPanel).
 * They used to feed a gear-icon modal in the Topbar; that modal is gone —
 * the config surface now lives as the last tab on the Settings page. */
const _cfgInput = {
  font: 'inherit', fontSize: 13, height: 30, padding: '0 10px',
  borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
  background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)',
};

const ConfigField = ({ f, value, onChange }) => {
  let input;
  if (f.type === 'bool')
    input = <input type="checkbox" checked={!!value} onChange={e => onChange(e.target.checked)}
                   style={{ width: 16, height: 16, cursor: 'pointer' }}/>;
  else if (f.type === 'choice')
    input = <select value={value ?? ''} onChange={e => onChange(e.target.value)} style={{ ..._cfgInput, minWidth: 150 }}>
      {(f.choices || []).map(c => <option key={c} value={c}>{c}</option>)}
    </select>;
  else if (f.type === 'int' || f.type === 'float')
    input = <input type="number" value={value ?? ''} min={f.min ?? undefined} max={f.max ?? undefined}
                   step={f.type === 'int' ? 1 : 'any'}
                   onChange={e => onChange(e.target.value === '' ? '' : Number(e.target.value))}
                   style={{ ..._cfgInput, width: 110, textAlign: 'right' }}/>;
  else
    input = <input type="text" value={value ?? ''} onChange={e => onChange(e.target.value)}
                   style={{ ..._cfgInput, minWidth: 240 }}/>;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderTop: '1px solid var(--border-soft)' }}>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>{f.label}</span>
        <span title={f.help} style={{ color: 'var(--fg-subtle)', cursor: 'help', display: 'inline-flex' }}>
          <Icon name="info" size={13}/>
        </span>
        {f.tier === 'restart' && <Pill tone="warn">restart</Pill>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        {input}
        {f.unit && <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', width: 34 }}>{f.unit}</span>}
      </div>
    </div>
  );
};

const _groupBy = (list) => {
  const g = {};
  list.forEach(f => { (g[f.group] = g[f.group] || []).push(f); });
  return g;
};

/* ---- Topbar ------------------------------------------------- */
const Topbar = ({ route, setRoute, theme, setTheme }) => {
  const labels = { music: 'Music', podcasts: 'Podcasts', audiobooks: 'Audiobooks', news: 'News', people: 'People', satellites: 'Satellites', calendar: 'Calendar', plugins: 'Plugins', settings: 'Settings', files: 'Files', manual: 'User Manual' };
  // Plugin routes take their crumb label from the manifest.
  if (!labels[route]) {
    const manifest = window.DomovoiPluginManifest || { plugins: [] };
    for (const p of manifest.plugins || []) {
      for (const pg of p.pages || []) {
        if (pg.route === route) labels[route] = pg.nav_label || route;
      }
    }
  }
  const [showServers, setShowServers] = React.useState(false);
  return (
    <header className="topbar">
      <div className="crumbs">
        <span>domovoi</span>
        <span className="sep">/</span>
        <strong>{labels[route]}</strong>
      </div>
      <div className="spacer"/>
      <div className="cmdk" role="button" tabIndex={0}>
        <Icon name="search" size={13}/>
        <span>search anything</span>
        <span className="key">⌘K</span>
      </div>
      <button className="theme-toggle" onClick={() => setShowServers(true)}
              title="switch domovoi">
        <Icon name="server" size={14} className="ic"/>
        <span style={{ fontSize: 12 }} className="mono">{ServerStore.currentLabel()}</span>
      </button>
      <button className={`theme-toggle ${route === 'settings' ? 'active' : ''}`}
              onClick={() => setRoute('settings')} title="settings">
        <Icon name="settings" size={14} className="ic"/>
      </button>
      <button className="theme-toggle" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              title="toggle theme">
        <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={14} className="ic"/>
        <span style={{ fontSize: 12 }}>{theme === 'dark' ? 'light' : 'dark'}</span>
      </button>
      {showServers && <ServerSwitcher onClose={() => setShowServers(false)}/>}
    </header>
  );
};

/* ---- Server switcher (multi-domovoi homes) --------------- */
/* Pick which backend this dashboard talks to: same-origin (default),
 * a saved server, a scan hit, or a manually typed ip:port. Selection
 * lives in localStorage (ServerStore, data.js) and switching reloads. */
const ServerSwitcher = ({ onClose }) => {
  const [saved, setSaved] = React.useState(ServerStore.list());
  const [found, setFound] = React.useState([]);
  const [scanning, setScanning] = React.useState(false);
  const [progress, setProgress] = React.useState(null);
  const [manual, setManual] = React.useState('');
  const [manualBusy, setManualBusy] = React.useState(false);
  const [manualErr, setManualErr] = React.useState(null);
  const current = ServerStore.current();
  const canScan = !!ServerStore.scanPrefix();

  const scan = async () => {
    setScanning(true); setFound([]); setProgress([0, 254, 0]);
    const hits = await ServerStore.scan((done, total, n) => setProgress([done, total, n]));
    setFound(hits || []);
    setScanning(false);
  };

  const pick = (url, name) => {
    if (url) ServerStore.upsert(url, name);
    ServerStore.select(url); // reloads
  };

  const addManual = async () => {
    setManualBusy(true); setManualErr(null);
    let url = manual.trim().replace(/\/+$/, '');
    if (!url) { setManualBusy(false); return; }
    if (!/^https?:\/\//.test(url)) url = `http://${url}`;
    if (!/:\d+$/.test(url.replace(/^https?:\/\//, ''))) url = `${url}:6369`;
    const hit = await ServerStore.probe(url, 3000);
    setManualBusy(false);
    if (hit) pick(hit.url, hit.name);
    else setManualErr("couldn't reach a dashboard there");
  };

  const Row = ({ url, name, removable }) => {
    const active = (url || '') === current;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px',
                    borderRadius: 6, background: active ? 'var(--brand-soft)' : 'transparent' }}>
        <StatusDot tone={active ? 'brand' : 'idle'} live={active}/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 500 }}>{name || (url ? 'domovoi' : 'this host')}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
            {url || window.location.host}
          </div>
        </div>
        {!active && <Button onClick={() => pick(url, name)}>use</Button>}
        {removable && !active && (
          <IconButton name="x" title="forget" onClick={() => {
            ServerStore.remove(url); setSaved(ServerStore.list());
          }}/>
        )}
      </div>
    );
  };

  const savedUrls = new Set(saved.map((s) => s.url));
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 70, background: 'oklch(0 0 0 / 0.4)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center' }}
         onClick={onClose}>
      <div className="card" style={{ width: 420, maxWidth: '92vw', maxHeight: '80vh',
                                     overflow: 'auto', padding: 16 }}
           onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
          <div className="eyebrow">domovois</div>
          <div style={{ flex: 1 }}/>
          <IconButton name="x" title="close" onClick={onClose}/>
        </div>

        <Row url="" name={null}/>
        {saved.map((s) => <Row key={s.url} url={s.url} name={s.name} removable/>)}
        {found.filter((f) => !savedUrls.has(f.url)).map((f) => (
          <Row key={f.url} url={f.url} name={f.name}/>
        ))}

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
          <Button onClick={scan} disabled={scanning || !canScan}>
            {scanning ? `scanning… ${progress ? progress[0] : 0}/254` : 'scan network'}
          </Button>
          {scanning && progress && (
            <span className="meta">{progress[2]} found</span>
          )}
          {!canScan && (
            <span className="meta">open the dashboard by IP address to enable scanning</span>
          )}
          {!scanning && progress && !found.length && (
            <span className="meta">no domovois found</span>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <input placeholder="192.168.1.30:6369" value={manual}
                 style={{ flex: 1, font: 'inherit', fontSize: 13, height: 32,
                          padding: '0 10px', borderRadius: 'var(--r-sm)',
                          border: '1px solid var(--border)', background: 'var(--card)',
                          color: 'var(--fg)' }}
                 onChange={(e) => { setManual(e.target.value); setManualErr(null); }}
                 onKeyDown={(e) => { if (e.key === 'Enter') addManual(); }}/>
          <Button onClick={addManual} disabled={manualBusy || !manual.trim()}>
            {manualBusy ? 'checking…' : 'add'}
          </Button>
        </div>
        {manualErr && <div className="meta" style={{ color: 'var(--err)', marginTop: 6 }}>{manualErr}</div>}
        <div className="meta" style={{ marginTop: 10 }}>
          switching reloads the page pointed at the selected backend
        </div>
      </div>
    </div>
  );
};

/* ---- Page header (used by every route) ---------------------- */
const PageHeader = ({ title, sub, actions }) => (
  <div className="page-header">
    <div className="l">
      <h1 className="h2">{title}</h1>
      {sub && <div className="meta" style={{ color: 'var(--fg-muted)', fontSize: 12 }}>{sub}</div>}
    </div>
    {actions && <div className="actions">{actions}</div>}
  </div>
);

/* ---- Stat (summary card) ------------------------------------ */
const Stat = ({ label, value, sub }) => (
  <div className="stat">
    <div className="lab">{label}</div>
    <div className="val num-tab">{value}</div>
    {sub && <div className="sub">{sub}</div>}
  </div>
);

/* ---- Admin login / first-run setup modal (design §7.2/§7.3) --
 * Minimal v1 wiring: pops on any 401/403 (data.js calls
 * Auth.requestLogin()) or on demand from the Settings page. The
 * bearer token lives in JS memory only (auth.js); the cookie the
 * login endpoint sets just renders GET state after a reload. */
const LoginModal = ({ onClose }) => {
  const [status, setStatus] = useState(Auth.status);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!status) Auth.refreshStatus().then(setStatus);
  }, []);

  const needsSetup = status && status.setup_complete === false;

  const submit = async () => {
    setErr(null);
    if (needsSetup) {
      if (password.length < 10) { setErr('password must be at least 10 characters'); return; }
      if (password !== confirm) { setErr('passwords do not match'); return; }
    }
    setBusy(true);
    try {
      if (needsSetup) await Auth.setup(code.trim(), password);
      else await Auth.login(password);
      onClose();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cal-modal-bg" onClick={onClose}>
      <div className="cal-modal" onClick={(e) => e.stopPropagation()}>
        <div className="cal-modal-head">
          <div className="ttl">{needsSetup ? 'first-run admin setup' : 'admin login'}</div>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div className="cal-modal-body">
          {needsSetup ? (
            <>
              <div className="hint">
                Enter the setup code from the Domovoi server console (also in
                <code>~/.domovoi/setup-code.txt</code>) and choose an admin password.
              </div>
              <div className="field">
                <label>setup code</label>
                <input className="cal-inp" value={code} autoFocus
                       placeholder="eight-words-separated-by-dashes"
                       onChange={(e) => setCode(e.target.value)}/>
              </div>
              <div className="field">
                <label>admin password (min 10 chars)</label>
                <input className="cal-inp" type="password" value={password}
                       onChange={(e) => setPassword(e.target.value)}/>
              </div>
              <div className="field">
                <label>confirm password</label>
                <input className="cal-inp" type="password" value={confirm}
                       onChange={(e) => setConfirm(e.target.value)}
                       onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}/>
              </div>
            </>
          ) : (
            <div className="field">
              <label>admin password</label>
              <input className="cal-inp" type="password" value={password} autoFocus
                     onChange={(e) => setPassword(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}/>
            </div>
          )}
          {err && <div className="err">{err}</div>}
          <div className="hint">
            Admin actions (settings, plugins, satellite upgrades) need this;
            everyday playback and browsing never do.
          </div>
        </div>
        <div className="cal-modal-foot">
          <Button onClick={onClose}>cancel</Button>
          <Button variant="primary" onClick={submit} disabled={busy || !password}>
            {busy ? 'working…' : (needsSetup ? 'set password' : 'log in')}
          </Button>
        </div>
      </div>
    </div>
  );
};

/* Mounted once in the App shell — re-renders on Auth store changes. */
const AuthModalHost = () => {
  const [, force] = React.useReducer((x) => x + 1, 0);
  useEffect(() => Auth.subscribe(force), []);
  if (!Auth.modalOpen) return null;
  return <LoginModal onClose={() => Auth.closeModal()}/>;
};

/* expose to other Babel scripts */
Object.assign(window, {
  Icon, DomovoiGlyph, SleepingDomovoi, HeadphonesDomovoi, StatusDot, Pill, RoomChip, Avatar,
  Card, Empty, Button, IconButton, Sidebar, Topbar, PageHeader, Stat, useToast, Tabs,
  relTime, fmtDur, LoginModal, AuthModalHost,
});
