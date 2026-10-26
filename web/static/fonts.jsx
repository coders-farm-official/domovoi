/* ============================================================
   fonts.jsx — the editors' fonts: window.DomovoiFonts (the registry),
   window.DomovoiFontPicker (the toolbar picker) and Settings → Documents →
   Fonts (the admin's "More fonts").

   The house has two kinds of font family (web/backend/api/fonts.py):
     * bundled — about 70 open-licence families served from /fonts/<slug>/
       (web/static/fonts, written by scripts/vendor/fonts.py);
     * fetched — families an admin added for the whole house from the
       google/fonts collection (OFL or Apache only), stored on the server.
   Both are served by this server, so they work with no internet. A face
   downloads only when text in it is drawn (or ensure() asks for it).

   Everything lives inside one IIFE so nothing here can collide with
   another page script's top-level names (all scripts share one scope).
   ============================================================ */

const DomovoiFontsModule = (() => {
  const CATEGORIES = ['sans', 'serif', 'mono', 'display', 'handwriting'];
  const CATEGORY_LABELS = { sans: 'sans', serif: 'serif', mono: 'mono', display: 'display', handwriting: 'handwriting' };
  const CATEGORY_GENERIC = { sans: 'sans-serif', serif: 'serif', mono: 'monospace', display: 'sans-serif', handwriting: 'cursive' };
  const GENERIC = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui',
    'ui-serif', 'ui-sans-serif', 'ui-monospace', 'ui-rounded', 'emoji', 'math', 'fangsong',
    '-apple-system', 'blinkmacsystemfont', 'inherit', 'initial', 'unset', 'revert']);
  /* Office fonts a document from Word often names. The house can't ship
   * them (they are not open-licence), so each is drawn in a face with the
   * same letter widths, and a line wraps where it did in Word. The device's
   * own copy wins when it has one (local()). */
  const SUBSTITUTES = {
    'arial': 'Liberation Sans', 'helvetica': 'Liberation Sans', 'helvetica neue': 'Liberation Sans',
    'times new roman': 'Liberation Serif', 'times': 'Liberation Serif',
    'courier new': 'Liberation Mono', 'courier': 'Liberation Mono',
    'calibri': 'Carlito', 'cambria': 'Caladea',
  };
  const RECENT_KEY = 'domovoi.fonts.recent';
  const RECENT_MAX = 8;
  const REFRESH_MS = 10 * 60 * 1000;

  const base = () => (typeof API_BASE === 'string' ? API_BASE : '');
  const norm = (name) => String(name || '').trim().replace(/^['"]|['"]$/g, '').trim().toLowerCase();
  const cssQuote = (name) => '"' + String(name).replace(/["\\]/g, '\\$&').replace(/[\u0000-\u001f<>]/g, '') + '"';

  let families = [];          // [{family, slug, category, source, weights, italic, licence, faces}]
  let byName = new Map();     // lower-case name -> entry
  let loaded = false;
  let inflight = null;
  let lastSig = '';
  const subs = new Set();
  const injected = new Set(); // slugs whose @font-face rules are in the page
  let cssLinked = false;
  let aliasesInjected = false;

  const signature = (list) => list.map((f) => `${f.source}:${f.slug}`).join('|');

  const notify = () => {
    const snap = list();
    subs.forEach((fn) => { try { fn(snap); } catch { /* one bad subscriber */ } });
  };

  const setFamilies = (next) => {
    families = next;
    byName = new Map(next.map((f) => [norm(f.family), f]));
    loaded = true;
    const sig = signature(next);
    if (sig !== lastSig) {
      const first = lastSig === '';
      lastSig = sig;
      if (!first || subs.size) notify();
    }
  };

  /* Read the list: /api/fonts (bundled + added), else the bundled
   * manifest (a browser that isn't paired still gets the bundled set). */
  const load = (force) => {
    if (loaded && !force) return Promise.resolve(list());
    if (inflight) return inflight;
    inflight = (async () => {
      try {
        const body = await apiGet('/api/fonts', { quiet: true });
        setFamilies((body && body.families) || []);
      } catch {
        try {
          const r = await fetch(`${base()}/fonts/manifest.json`, { credentials: 'include' });
          if (!r.ok) throw new Error(`manifest ${r.status}`);
          const m = await r.json();
          setFamilies((m.families || []).map((f) => ({
            family: f.family, slug: f.slug, category: f.category, source: 'bundled',
            licence: f.licence, weights: f.weights, italic: !!f.italic,
            faces: (f.faces || []).map((x) => ({ url: `/fonts/${f.slug}/${x.file}`, style: x.style,
              weight: x.weight, format: x.format, unicode_range: x.unicode_range })),
          })));
        } catch { if (!loaded) setFamilies([]); }
      } finally { inflight = null; }
      return list();
    })();
    return inflight;
  };

  /* list(): the families as the editors' interface states them. Empty
   * until the first load() finishes; onChange fires when it does. */
  function list() {
    return families.map((f) => ({
      family: f.family, category: f.category, source: f.source,
      weights: (f.weights || []).slice(), italic: !!f.italic, licence: f.licence, slug: f.slug,
    }));
  }

  const linkBundledCss = () => {
    if (cssLinked || typeof document === 'undefined') return;
    cssLinked = true;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `${base()}/fonts/fonts.css`;
    link.setAttribute('data-domovoi-fonts', 'bundled');
    document.head.appendChild(link);
  };

  const fmt = { woff2: 'woff2', woff: 'woff', ttf: 'truetype', otf: 'opentype' };

  const faceRules = (f) => (f.faces || []).map((x) => {
    const w = Array.isArray(x.weight) ? x.weight : [400, 400];
    const weight = w[0] === w[1] ? `${w[0]}` : `${w[0]} ${w[1]}`;
    return `@font-face{font-family:${cssQuote(f.family)};font-style:${x.style === 'italic' ? 'italic' : 'normal'};`
      + `font-weight:${weight};font-display:swap;src:url('${base()}${x.url}') format('${fmt[x.format] || 'truetype'}');`
      + (x.unicode_range ? `unicode-range:${x.unicode_range};` : '') + '}';
  }).join('\n');

  const injectFaces = (f) => {
    if (!f || injected.has(f.slug) || typeof document === 'undefined') return;
    injected.add(f.slug);
    if (f.source === 'bundled') { linkBundledCss(); return; }
    const style = document.createElement('style');
    style.setAttribute('data-domovoi-fonts', f.slug);
    style.textContent = faceRules(f);
    document.head.appendChild(style);
  };

  /* The office-font stand-ins, as @font-face rules named after the office
   * font: local() first, then our file with the same widths. */
  const injectAliases = () => {
    if (aliasesInjected || typeof document === 'undefined') return;
    const rules = [];
    Object.keys(SUBSTITUTES).forEach((alias) => {
      const target = byName.get(norm(SUBSTITUTES[alias]));
      if (!target) return;
      const local = alias.replace(/\b\w/g, (c) => c.toUpperCase());
      (target.faces || []).forEach((x) => {
        const w = Array.isArray(x.weight) ? x.weight : [400, 400];
        rules.push(`@font-face{font-family:${cssQuote(local)};font-style:${x.style};font-weight:${w[0] === w[1] ? w[0] : `${w[0]} ${w[1]}`};`
          + `font-display:swap;src:local(${cssQuote(local)}),url('${base()}${x.url}') format('${fmt[x.format] || 'truetype'}');}`);
      });
    });
    if (!rules.length) return;
    aliasesInjected = true;
    const style = document.createElement('style');
    style.setAttribute('data-domovoi-fonts', 'office-substitutes');
    style.textContent = rules.join('\n');
    document.head.appendChild(style);
  };

  const get = (name) => byName.get(norm(name)) || null;

  /* 'installed' | 'substitute' (an office font drawn in its stand-in) |
   * 'generic' (serif, sans-serif…) | 'missing' (not in the house). */
  const status = (name) => {
    const n = norm(name);
    if (!n) return 'generic';
    if (GENERIC.has(n)) return 'generic';
    if (byName.has(n)) return 'installed';
    if (SUBSTITUTES[n] && byName.has(norm(SUBSTITUTES[n]))) return 'substitute';
    return 'missing';
  };
  const substituteFor = (name) => SUBSTITUTES[norm(name)] || null;

  /* ensure(family): put the family's @font-face rules in the page and load
   * its regular face (plus bold/italic when asked: { weights, italic }).
   * Resolves true when the house has it, false otherwise (the text stays in
   * its fallback). Never rejects. */
  const ensure = async (name, opts) => {
    if (!loaded) await load();
    const n = norm(name);
    let f = byName.get(n);
    if (!f && SUBSTITUTES[n]) { injectAliases(); f = byName.get(norm(SUBSTITUTES[n])); }
    if (!f) return false;
    injectFaces(f);
    if (typeof document === 'undefined' || !document.fonts || !document.fonts.load) return true;
    const o = opts || {};
    const weights = (o.weights && o.weights.length ? o.weights : [400]);
    const loads = [];
    weights.forEach((w) => {
      loads.push(document.fonts.load(`${w} 1em ${cssQuote(f.family)}`));
      if (o.italic) loads.push(document.fonts.load(`italic ${w} 1em ${cssQuote(f.family)}`));
    });
    try { await Promise.all(loads); } catch { /* a face that failed stays in the fallback */ }
    return true;
  };

  /* A CSS font-family value: the family, then its group's generic. */
  const stack = (name) => {
    const f = get(name) || get(substituteFor(name) || '');
    const generic = CATEGORY_GENERIC[(f && f.category) || 'sans'] || 'sans-serif';
    return `${cssQuote(f ? f.family : String(name || '').trim())}, ${generic}`;
  };

  /* The first family of a CSS font-family value ("'Lora', serif" -> Lora). */
  const firstFamily = (value) => {
    const parts = String(value || '').split(',');
    for (const p of parts) {
      const n = p.trim().replace(/^['"]|['"]$/g, '').trim();
      if (n) return n;
    }
    return '';
  };

  /* Families a document names: style="font-family: …" and <font face>.
   * The first named family of each, de-duplicated, as written. */
  const familiesInHTML = (html) => {
    const out = new Map();
    const add = (v) => { const n = firstFamily(v); if (n && !GENERIC.has(norm(n))) out.set(norm(n), n); };
    const decode = (t) => String(t || '').replace(/&quot;/g, '"').replace(/&#0*39;|&apos;/g, "'")
      .replace(/&#0*34;/g, '"').replace(/&amp;/g, '&');
    const text = String(html || '');
    const attrRe = /\b(style|face)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi;
    let m;
    while ((m = attrRe.exec(text))) {
      const value = decode(m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : m[4]));
      if (m[1].toLowerCase() === 'face') { add(value); continue; }
      const declRe = /font-family\s*:\s*([^;]+)/gi;
      let d;
      while ((d = declRe.exec(value))) add(d[1]);
    }
    return Array.from(out.values());
  };
  const missingInHTML = (html) => familiesInHTML(html).filter((n) => status(n) === 'missing');

  const recent = () => {
    try {
      const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, RECENT_MAX) : [];
    } catch { return []; }
  };
  const markUsed = (name) => {
    if (!name) return;
    try {
      const own = get(name);
      const label = own ? own.family : String(name);
      const next = [label].concat(recent().filter((x) => norm(x) !== norm(label))).slice(0, RECENT_MAX);
      localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    } catch { /* storage off: no recents, nothing else changes */ }
  };

  const onChange = (cb) => {
    subs.add(cb);
    return () => { subs.delete(cb); };
  };

  /* Admin: add a family for the whole house; resolves with its entry. */
  const add = async (family) => {
    const r = await apiPost('/api/fonts/fetch', { family });
    await load(true);
    return r && r.family;
  };
  const remove = async (slug) => {
    await apiDelete(`/api/fonts/${encodeURIComponent(slug)}`);
    await load(true);
  };

  /* Admin: which of these missing families could be added (the open-licence
   * collection has them)? { name: {available, family?, licence?} }. Kept for
   * the page's life; a refusal (internet off, not an admin) answers {}. */
  const known = new Map();
  const lookup = async (names) => {
    const ask = (names || []).filter((n) => n && !known.has(norm(n))).slice(0, 12);
    if (ask.length) {
      try {
        const q = ask.map((n) => `family=${encodeURIComponent(n)}`).join('&');
        const body = await apiGet(`/api/fonts/lookup?${q}`, { quiet: true });
        const got = (body && body.families) || {};
        ask.forEach((n) => { if (got[n]) known.set(norm(n), got[n]); });
      } catch { /* unknown stays unknown: no add button */ }
    }
    const out = {};
    (names || []).forEach((n) => { if (known.has(norm(n))) out[n] = known.get(norm(n)); });
    return out;
  };

  // Keep the list current: the realtime channel when a page has the
  // socket, a slow re-read otherwise, and a re-read when the tab returns.
  try {
    if (typeof stateBus !== 'undefined' && stateBus.subscribe) {
      stateBus.subscribe((ev) => { if (ev && ev.type === 'fonts.changed' && loaded) load(true); });
    }
  } catch { /* no realtime here */ }
  try {
    setInterval(() => { if (loaded && subs.size) load(true); }, REFRESH_MS);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && loaded && subs.size) load(true);
    });
  } catch { /* no document (a test sandbox) */ }

  const api = {
    CATEGORIES, CATEGORY_LABELS, list, load, ready: () => load(), ensure, onChange, status,
    has: (n) => status(n) === 'installed', get: (n) => { const f = get(n); return f ? list().find((x) => x.slug === f.slug) : null; },
    substituteFor, stack, firstFamily, familiesInHTML, missingInHTML, recent, markUsed, add, remove, lookup,
    isLoaded: () => loaded,
  };
  return api;
})();

window.DomovoiFonts = DomovoiFontsModule;

/* ---- the picker -------------------------------------------------------
 * <DomovoiFontPicker value onChange disabled disabledNote documentFamilies/>
 *   value             the current family (any string, installed or not)
 *   onChange(family)  a family was picked (installed ones only)
 *   disabled          greys the button; disabledNote says why (the
 *                     Markdown note: "save as a formatted document to use
 *                     fonts")
 *   documentFamilies  families the open document names; the ones the
 *                     house lacks are listed first, marked "not installed",
 *                     with "add for the house" for an admin
 * The button shows the value in its own face; the list previews every
 * name in its own face, each loading as it scrolls into view. */
const DomovoiFontPickerUI = (() => {
  const F = DomovoiFontsModule;

  const useFontList = () => {
    const [items, setItems] = React.useState(F.list());
    React.useEffect(() => {
      const off = F.onChange(setItems);
      F.load().then(setItems);
      return off;
    }, []);
    return items;
  };

  /* A name drawn in its own face once the row is on screen. */
  const FaceName = ({ family, children, eager }) => {
    const ref = React.useRef(null);
    const [ready, setReady] = React.useState(false);
    React.useEffect(() => {
      let dead = false;
      const go = () => F.ensure(family).then((ok) => { if (!dead && ok) setReady(true); });
      if (eager || typeof IntersectionObserver === 'undefined' || !ref.current) { go(); return () => { dead = true; }; }
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { io.disconnect(); go(); }
      }, { rootMargin: '120px' });
      io.observe(ref.current);
      return () => { dead = true; io.disconnect(); };
    }, [family, eager]);
    return (
      <span ref={ref} className="font-face-name"
            style={ready ? { fontFamily: F.stack(family) } : undefined}>
        {children || family}
      </span>
    );
  };

  const Picker = ({ value, onChange, disabled = false, disabledNote = '', documentFamilies = null,
                    label = 'font', compact = false }) => {
    const items = useFontList();
    const admin = useAdminSignedIn();
    const net = useInternetPolicy();
    const [open, setOpen] = React.useState(false);
    const [q, setQ] = React.useState('');
    const [cat, setCat] = React.useState('all');
    const [adding, setAdding] = React.useState('');
    const [err, setErr] = React.useState('');
    const [avail, setAvail] = React.useState({});
    const wrapRef = React.useRef(null);
    const searchRef = React.useRef(null);

    React.useEffect(() => {
      if (!open) return undefined;
      const onDoc = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
      const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
      document.addEventListener('mousedown', onDoc);
      document.addEventListener('keydown', onKey);
      setTimeout(() => { try { searchRef.current && searchRef.current.focus(); } catch { /* gone */ } }, 0);
      return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
    }, [open]);

    const missingKey = (documentFamilies || []).join('|');
    React.useEffect(() => {
      if (!open || !admin || net.off) return undefined;
      let dead = false;
      const names = (documentFamilies || []).concat(value ? [F.firstFamily(value)] : [])
        .filter((n) => F.status(n) === 'missing' && !F.substituteFor(n));
      if (names.length) F.lookup(names).then((got) => { if (!dead) setAvail(got); });
      return () => { dead = true; };
    }, [open, admin, net.off, missingKey, value]);

    const current = value ? F.firstFamily(value) : '';
    const currentStatus = current ? F.status(current) : 'generic';
    const words = q.trim().toLowerCase();
    const matches = (name, category) => (!words || name.toLowerCase().includes(words))
      && (cat === 'all' || category === cat);

    const recents = F.recent().map((n) => items.find((f) => f.family.toLowerCase() === n.toLowerCase()))
      .filter(Boolean).filter((f) => matches(f.family, f.category));
    const missing = (documentFamilies || []).concat(current && currentStatus === 'missing' ? [current] : [])
      .filter((n, i, arr) => F.status(n) === 'missing' && arr.findIndex((m) => m.toLowerCase() === n.toLowerCase()) === i)
      .filter((n) => !words || n.toLowerCase().includes(words));
    const rest = items.filter((f) => matches(f.family, f.category));

    const pick = (family) => {
      F.markUsed(family);
      setOpen(false);
      setQ('');
      if (onChange) onChange(family);
    };

    const addForHouse = async (family) => {
      setErr('');
      setAdding(family);
      try {
        await F.add(family);
      } catch (e) {
        const msg = (typeof isInternetOffError === 'function' && isInternetOffError(e))
          ? INTERNET_OFF_MESSAGE
          : ((e && e.detail && typeof e.detail.detail === 'string' && e.detail.detail) || 'Couldn’t add that font.');
        setErr(msg);
      } finally { setAdding(''); }
    };

    const row = (f, key) => (
      <button key={key} type="button" role="option" aria-selected={current.toLowerCase() === f.family.toLowerCase()}
              className={`font-pick-row${current.toLowerCase() === f.family.toLowerCase() ? ' on' : ''}`}
              onClick={() => pick(f.family)}>
        <FaceName family={f.family}/>
        <span className="font-pick-meta">{f.source === 'fetched' ? 'added' : F.CATEGORY_LABELS[f.category]}</span>
      </button>
    );

    const buttonLabel = current || label;
    return (
      <div className={`font-picker${compact ? ' compact' : ''}`} ref={wrapRef}>
        <button type="button" className="btn btn-secondary font-picker-button" disabled={disabled}
                title={disabled ? disabledNote : (currentStatus === 'missing' ? `${current} · not installed` : buttonLabel)}
                aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
          {current && currentStatus !== 'missing' ? <FaceName family={current} eager/> : <span>{buttonLabel}</span>}
          {currentStatus === 'missing' && <span className="font-pick-missing">not installed</span>}
          <Icon name="chevron-down" size={12}/>
        </button>
        {disabled && disabledNote && <span className="font-picker-note">{disabledNote}</span>}
        {open && !disabled && (
          <div className="font-pick-pop" role="listbox" aria-label="fonts">
            <input ref={searchRef} type="search" className="font-pick-search" placeholder="search fonts"
                   aria-label="search fonts" value={q} onChange={(e) => setQ(e.target.value)}/>
            <div className="font-pick-cats" role="tablist">
              {['all'].concat(F.CATEGORIES).map((c) => (
                <button key={c} type="button" role="tab" aria-selected={cat === c}
                        className={`font-pick-cat${cat === c ? ' on' : ''}`} onClick={() => setCat(c)}>
                  {c === 'all' ? 'all' : F.CATEGORY_LABELS[c]}
                </button>
              ))}
            </div>
            <div className="font-pick-list">
              {missing.length > 0 && <div className="font-pick-head">in this document, not installed</div>}
              {missing.map((n) => {
                const a = avail[n];
                let side;
                if (F.substituteFor(n)) side = <span className="font-pick-meta">shown in {F.substituteFor(n)}</span>;
                else if (admin && net.off) side = <NeedsInternetNote compact/>;
                else if (admin && a && a.available) {
                  side = (
                    <Button disabled={!!adding} onClick={() => addForHouse(a.family || n)}>
                      {adding === (a.family || n) ? 'adding…' : 'add for the house'}
                    </Button>
                  );
                } else if (admin && a) side = <span className="font-pick-meta">not in the free collection</span>;
                else side = <span className="font-pick-meta">shown in a similar font</span>;
                return (
                  <div key={`m-${n}`} className="font-pick-row missing">
                    <span className="font-face-name">{n}</span>
                    {side}
                  </div>
                );
              })}
              {err && <div className="font-pick-err" role="alert">{err}</div>}
              {recents.length > 0 && <div className="font-pick-head">recently used</div>}
              {recents.map((f) => row(f, `r-${f.slug}`))}
              {recents.length > 0 && rest.length > 0 && <div className="font-pick-head">all fonts</div>}
              {rest.map((f) => row(f, `a-${f.slug}`))}
              {!rest.length && !recents.length && !missing.length && (
                <div className="font-pick-none">{F.isLoaded() ? 'no fonts match' : 'loading fonts…'}</div>
              )}
            </div>
            {admin && <a className="font-pick-more" href="#settings/fonts" onClick={() => setOpen(false)}>more fonts…</a>}
          </div>
        )}
      </div>
    );
  };

  return { Picker, FaceName, useFontList };
})();

window.DomovoiFontPicker = DomovoiFontPickerUI.Picker;
window.DomovoiFonts.Picker = DomovoiFontPickerUI.Picker;
window.DomovoiFonts.FaceName = DomovoiFontPickerUI.FaceName;

/* ---- Settings → Documents → Fonts --------------------------------------
 * Everyone sees how many fonts the house has; an admin sees the families
 * added for the house (with Remove) and "More fonts", the open-licence
 * collection to add from. Adding needs the internet; with it turned off
 * the button is greyed and says why. */
const DomovoiFontsSettings = (() => {
  const F = DomovoiFontsModule;
  const { FaceName, useFontList } = DomovoiFontPickerUI;

  const mb = (n) => `${(Number(n || 0) / 1000000).toFixed(1)} MB`;
  const errText = (e, fallback) => {
    if (typeof isInternetOffError === 'function' && isInternetOffError(e)) return INTERNET_OFF_MESSAGE;
    const d = e && e.detail && e.detail.detail;
    return (typeof d === 'string' && d) || fallback;
  };

  const Catalogue = ({ onAdded }) => {
    const [state, setState] = React.useState({ loading: true, error: '', families: [] });
    const [q, setQ] = React.useState('');
    const [cat, setCat] = React.useState('all');
    const [busy, setBusy] = React.useState('');
    const [note, setNote] = React.useState('');
    const [shown, setShown] = React.useState(60);

    React.useEffect(() => {
      let dead = false;
      apiGet('/api/fonts/catalogue')
        .then((b) => { if (!dead) setState({ loading: false, error: '', families: (b && b.families) || [] }); })
        .catch((e) => { if (!dead) setState({ loading: false, error: errText(e, 'Couldn’t get the list of fonts.'), families: [] }); });
      return () => { dead = true; };
    }, []);

    const words = q.trim().toLowerCase();
    const hits = state.families.filter((f) => (!words || f.family.toLowerCase().includes(words))
      && (cat === 'all' || f.category === cat));

    const add = async (f) => {
      setBusy(f.family);
      setNote('');
      try {
        await F.add(f.family);
        setState((s) => Object.assign({}, s, { families: s.families.map((x) => (x.slug === f.slug ? Object.assign({}, x, { installed: true }) : x)) }));
        setNote(`${f.family} added for the whole house.`);
        if (onAdded) onAdded();
      } catch (e) {
        setNote(errText(e, `Couldn’t add ${f.family}.`));
      } finally { setBusy(''); }
    };

    if (state.loading) {
      return <div className="fonts-note">getting the list of fonts… the first time takes up to a minute</div>;
    }
    if (state.error) return <div className="fonts-note" role="alert">{state.error}</div>;
    return (
      <div className="fonts-catalogue">
        <input type="search" className="font-pick-search" placeholder={`search ${state.families.length} fonts`}
               aria-label="search fonts" value={q} onChange={(e) => { setQ(e.target.value); setShown(60); }}/>
        <div className="font-pick-cats">
          {['all'].concat(F.CATEGORIES).map((c) => (
            <button key={c} type="button" className={`font-pick-cat${cat === c ? ' on' : ''}`}
                    aria-pressed={cat === c} onClick={() => { setCat(c); setShown(60); }}>{c}</button>
          ))}
        </div>
        {note && <div className="fonts-note" role="status">{note}</div>}
        <div className="fonts-cat-list">
          {hits.slice(0, shown).map((f) => (
            <div key={f.slug} className="fonts-row">
              <span className="fonts-row-name">{f.family}</span>
              <span className="fonts-row-meta">{f.category} · {f.licence}</span>
              {f.installed
                ? <span className="fonts-row-meta">in the house</span>
                : <Button disabled={!!busy} onClick={() => add(f)}>{busy === f.family ? 'adding…' : 'add'}</Button>}
            </div>
          ))}
          {!hits.length && <div className="fonts-note">no fonts match</div>}
        </div>
        {hits.length > shown && <Button onClick={() => setShown(shown + 120)}>show more</Button>}
        <div className="fonts-note">
          Only fonts under the SIL Open Font License or the Apache licence are listed, from the
          open-source Google Fonts collection. Each one’s licence is checked before it is added,
          and kept beside it.
        </div>
      </div>
    );
  };

  const Panel = () => {
    const items = useFontList();
    const admin = useAdminSignedIn();
    const net = useInternetPolicy();
    const [browsing, setBrowsing] = React.useState(false);
    const [busy, setBusy] = React.useState('');
    const [note, setNote] = React.useState('');
    const added = items.filter((f) => f.source === 'fetched');
    const bundled = items.length - added.length;

    const remove = async (f) => {
      setBusy(f.slug);
      setNote('');
      try { await F.remove(f.slug); setNote(`${f.family} removed. Documents that use it show it in a similar font.`); }
      catch (e) { setNote(errText(e, `Couldn’t remove ${f.family}.`)); }
      finally { setBusy(''); }
    };

    return (
      <div className="fonts-panel">
        <Card title="fonts for documents" sub={F.isLoaded()
          ? `${bundled} come with Domovoi${added.length ? `, ${added.length} added for this house` : ''}. They work without internet.`
          : 'loading…'}>
          <div className="fonts-groups">
            {F.CATEGORIES.map((c) => {
              const inCat = items.filter((f) => f.category === c);
              return (
                <div key={c} className="fonts-group">
                  <div className="fonts-group-head">{c} · {inCat.length}</div>
                  <div className="fonts-group-names">
                    {inCat.slice(0, 6).map((f) => <FaceName key={f.slug} family={f.family}/>)}
                    {inCat.length > 6 && <span className="fonts-row-meta">and {inCat.length - 6} more</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
        <Card title="added for this house" sub={admin ? 'Fonts an admin added. Removing one doesn’t change any document.' : 'Only an admin can add or remove fonts.'}>
          {added.length === 0 && <div className="fonts-note">none yet</div>}
          {added.map((f) => (
            <div key={f.slug} className="fonts-row">
              <span className="fonts-row-name"><FaceName family={f.family}/></span>
              <span className="fonts-row-meta">{f.category} · {f.licence}</span>
              {admin && <Button variant="secondary" disabled={!!busy} onClick={() => remove(f)}>
                {busy === f.slug ? 'removing…' : 'remove'}
              </Button>}
            </div>
          ))}
          {note && <div className="fonts-note" role="status">{note}</div>}
        </Card>
        {admin && (
          <Card title="more fonts" sub="Add any open-licence font from the Google Fonts collection for everyone in the house."
                action={browsing ? null : (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <Button variant="primary" icon="type" disabled={net.off}
                            title={net.off ? NEEDS_INTERNET_TEXT : undefined}
                            onClick={() => setBrowsing(true)}>browse fonts</Button>
                    {net.off && <NeedsInternetNote compact/>}
                  </span>
                )}>
            {browsing && !net.off && <Catalogue/>}
            {!browsing && !net.off && (
              <div className="fonts-note">
                About two thousand families. One you add is ready in documents and spreadsheets on every
                device in the house, and works without internet from then on.
              </div>
            )}
            {net.off && <div className="fonts-note">{INTERNET_OFF_MESSAGE}: adding fonts needs it. The fonts already here keep working.</div>}
          </Card>
        )}
        <div className="fonts-note">
          Every font here is free to use and share: SIL Open Font License or Apache licence,
          with each font’s licence kept beside it.
        </div>
      </div>
    );
  };

  return { Panel };
})();

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections.fonts = { Panel: DomovoiFontsSettings.Panel };
