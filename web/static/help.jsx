/* Help — "Domovoi as its own help desk" (part 9) on the dashboard.
 *
 * One panel, opened from three places:
 *   * the "?" in the top bar, on every page (Topbar, components.jsx), with
 *     that page's name;
 *   * "ask Domovoi about this" on an error toast (useToast, components.jsx),
 *     prefilled with the error's code and the page;
 *   * window.DomovoiHelp.open({ query, page, code, message }) from anywhere.
 *
 * The panel searches Domovoi's own help (GET /api/help/search: the manual,
 * the FAQ, the troubleshooting guide, the error codes' entries, installed
 * plugins' help files). No model: with the household's AI features off this
 * search IS help. With AI on, "ask Domovoi" starts a home-server chat titled
 * "Help: <page>" with the question typed in, and Chat answers it from the
 * same help, the live facts and the system's own records (chat.py), reading
 * a pasted or attached screenshot with the vision model.
 *
 * "report a problem" builds the redacted summary (POST /api/help/report),
 * shows it whole, and offers copy and save. Nothing is sent anywhere.
 */

const HELP_DISCUSSIONS_URL = 'https://github.com/coders-farm-official/domovoi/discussions';

/* Who is open, for HelpHost. A plain store: the top bar, toasts and pages
 * are in different trees. */
const HelpStore = (() => {
  let state = null;
  const subs = new Set();
  return {
    get: () => state,
    open: (opts) => { state = { ...(opts || {}), at: Date.now() }; subs.forEach((f) => f(state)); },
    close: () => { state = null; subs.forEach((f) => f(state)); },
    subscribe: (f) => { subs.add(f); return () => subs.delete(f); },
  };
})();

/* The chat a help panel started, handed to the Chat page once (chat.jsx
 * takes it when it shows). */
const HelpHandoff = (() => {
  let pending = null;
  return {
    set: (h) => { pending = h; },
    take: () => { const h = pending; pending = null; return h; },
  };
})();

/* The current page's name, the way the top bar says it. An old address
 * (#satellites, #containers) is the Settings section it became
 * (resolveRoute), and a Settings section is named by its label
 * ("Settings → Phones & browsers"). */
const helpPageName = () => {
  const raw = String(window.location.hash || '#home').slice(1);
  const parts = String(typeof resolveRoute === 'function' ? resolveRoute(raw) : raw).split('/');
  const route = parts[0] || 'home';
  const labels = { home: 'Home', chat: 'Chat', music: 'Music', podcasts: 'Podcasts', audiobooks: 'Audiobooks',
    videos: 'Videos', news: 'News', people: 'People', calendar: 'Calendar',
    plugins: 'Plugins', settings: 'Settings', files: 'Files', manual: 'User Manual' };
  if (labels[route]) {
    const tab = parts[1];
    if (route !== 'settings' || !tab) return labels[route];
    const all = [...(window.SETTINGS_SECTIONS || []), ...(window.SETTINGS_GROUPS || [])];
    const known = all.find((s) => s.id === tab || (s.aliases || []).includes(tab));
    return `Settings → ${known ? known.label : tab.replace(/-/g, ' ')}`;
  }
  const manifest = window.DomovoiPluginManifest || { plugins: [] };
  for (const p of manifest.plugins || []) {
    for (const pg of p.pages || []) if (pg.route === route) return pg.nav_label || route;
  }
  return route;
};

/* What "ask Domovoi" types into the new chat. */
const helpDraft = ({ query, page, code, message }) => {
  if (message || code) {
    const what = message ? `"${String(message).slice(0, 300)}"` : 'an error';
    return `I got ${what}${code ? ` (code ${code})` : ''} on the ${page || 'dashboard'} page. `
      + 'What does it mean, and how do I fix it?';
  }
  return (query || '').trim();
};

const HelpResult = ({ r }) => {
  const external = /^https?:/.test(r.link || '');
  return (
    <div className="help-result">
      <div className="help-result-head">
        <span className="help-result-section">{r.section}</span>
        {r.link && (
          <a href={r.link} className="home-link" target={external ? '_blank' : undefined}
             rel={external ? 'noopener noreferrer' : undefined}
             onClick={() => { if (!external) HelpStore.close(); }}>open</a>
        )}
      </div>
      <div className="help-result-snippet">{r.snippet}</div>
    </div>
  );
};

const HelpReport = ({ onBack }) => {
  const [what, setWhat] = React.useState('');
  const [names, setNames] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [text, setText] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [copied, setCopied] = React.useState(false);
  const build = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await apiPost('/api/help/report', { what_happened: what, include_room_names: names });
      setText(r.text);
    } catch (e) {
      setErr(mutationErrorText(e, 'Building the summary', { kept: true }));
    }
    setBusy(false);
  };
  const copy = async () => {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch { /* fall through */ }
    if (!ok) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        ok = document.execCommand('copy'); ta.remove();
      } catch { ok = false; }
    }
    setCopied(ok ? 'copied' : 'copy failed: select the text and copy it');
  };
  const save = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'domovoi-problem-report.md';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="help-report">
      <div className="help-note">
        Domovoi builds a summary of its own state for you to post where people can help: version,
        rooms, plugins and what its checks show. It leaves out passwords, codes, addresses and names.
        <strong> Nothing is sent.</strong> Read it, then copy or save it and post it yourself, on{' '}
        <a href={HELP_DISCUSSIONS_URL} target="_blank" rel="noopener noreferrer" className="home-link">GitHub Discussions</a>{' '}
        or Discord.
      </div>
      {text == null ? (
        <React.Fragment>
          <label className="help-label" htmlFor="help-what">what happened? (optional)</label>
          <textarea id="help-what" className="help-textarea" rows={3} value={what}
                    onChange={(e) => setWhat(e.target.value)}
                    placeholder="what you did, what you expected, what you saw"/>
          <label className="help-check">
            <input type="checkbox" checked={names} onChange={(e) => setNames(e.target.checked)}/>
            include room names (otherwise they read room 1, room 2…)
          </label>
          {err && <div className="help-error">{err}</div>}
          <div className="help-actions">
            <Button onClick={onBack}>back</Button>
            <Button variant="primary" icon="file-text" onClick={build} disabled={busy}>
              {busy ? 'building…' : 'build the summary'}
            </Button>
          </div>
        </React.Fragment>
      ) : (
        <React.Fragment>
          <textarea className="help-textarea mono" rows={12} value={text} readOnly aria-label="the summary"/>
          {copied && <div className="help-note">{copied}</div>}
          <div className="help-actions">
            <Button onClick={() => { setText(null); setCopied(false); }}>edit</Button>
            <Button icon="download" onClick={save}>save as file</Button>
            <Button variant="primary" icon="copy" onClick={copy}>copy</Button>
          </div>
        </React.Fragment>
      )}
    </div>
  );
};

const HelpPanel = ({ opts, onClose }) => {
  const ai = useAiFeatures();
  const page = opts.page || helpPageName();
  const initial = opts.query || [opts.code, opts.message].filter(Boolean).join(' ');
  // With nothing typed, the help shown is this page's (its name searched).
  const pageQuery = page && page !== 'Home' ? page : '';
  const [q, setQ] = React.useState(initial);
  const [res, setRes] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [view, setView] = React.useState('search');
  const [starting, setStarting] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const inputRef = React.useRef(null);

  React.useEffect(() => { if (inputRef.current) inputRef.current.focus(); }, []);
  React.useEffect(() => {
    const query = q.trim() || pageQuery;
    if (!query) { setRes(null); return undefined; }
    let gone = false;
    setBusy(true);
    const t = setTimeout(async () => {
      try {
        const r = await apiGet(`/api/help/search?q=${encodeURIComponent(query)}&limit=5`, { quiet: true });
        if (!gone) setRes(r);
      } catch {
        if (!gone) setRes({ error: true, results: [] });
      }
      if (!gone) setBusy(false);
    }, 250);
    return () => { gone = true; clearTimeout(t); };
  }, [q, pageQuery]);
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const ask = async () => {
    setStarting(true); setErr(null);
    try {
      const title = `Help: ${page}`.slice(0, 60);
      const t = await apiPost('/api/chat/threads', { title });
      HelpHandoff.set({ threadId: t.id, draft: helpDraft({ ...opts, query: q, page }) });
      onClose();
      if ((window.location.hash || '').split('/')[0] === '#chat') {
        window.dispatchEvent(new CustomEvent('domovoi-help-handoff'));
      } else {
        window.location.hash = '#chat';
      }
    } catch (e) {
      setErr(mutationErrorText(e, 'Starting the chat', { kept: false }));
    }
    setStarting(false);
  };

  return (
    <div className="help-backdrop" onClick={onClose}>
      <div className="card help-panel" role="dialog" aria-modal="true" aria-label="help"
           onClick={(e) => e.stopPropagation()}>
        <div className="help-head">
          <div className="eyebrow">{view === 'report' ? 'report a problem' : 'help'}</div>
          <span className="help-page">{page}</span>
          {opts.code && <span className="mono help-code">{opts.code}</span>}
          <div style={{ flex: 1 }}/>
          <IconButton name="x" title="close" aria-label="close" onClick={onClose}/>
        </div>
        {view === 'report' ? <HelpReport onBack={() => setView('search')}/> : (
          <React.Fragment>
            <input ref={inputRef} className="input help-search" value={q} aria-label="search the help"
                   placeholder="ask or search: how do I add a room?"
                   onChange={(e) => setQ(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter' && ai && q.trim()) ask(); }}/>
            <div className="help-results" aria-live="polite">
              {busy && !res && <div className="help-note">searching…</div>}
              {res && res.error && <div className="help-note">couldn't search the help right now</div>}
              {res && !res.error && !res.covered && (
                <div className="help-note">
                  {res.not_covered || "The manual doesn't cover that."} Try other words
                  {ai ? ', or ask Domovoi' : ''}.
                </div>
              )}
              {res && (res.results || []).filter((r) => res.covered ? r.confident !== false : true)
                .slice(0, res.covered ? 5 : 0).map((r) => <HelpResult key={r.id} r={r}/>)}
              {!res && !busy && (
                <div className="help-note">
                  Search Domovoi's manual, its FAQ and troubleshooting guide, and your plugins' help.
                </div>
              )}
            </div>
            {!ai && (
              <div className="help-note">
                AI chat and questions are off for this home, so help is a search of the manual.
              </div>
            )}
            {ai && (
              <div className="help-note">
                Domovoi answers only from its help and its own records. About something on screen?
                Paste or attach a screenshot in the chat: it's read on your server and stays home.
              </div>
            )}
            {err && <div className="help-error">{err}</div>}
            <div className="help-actions">
              <Button icon="flag" onClick={() => setView('report')}>report a problem</Button>
              <div style={{ flex: 1 }}/>
              <a href="#manual" className="home-link" onClick={onClose}>the manual</a>
              {ai && (
                <Button variant="primary" icon="message-circle" onClick={ask} disabled={starting}>
                  {starting ? 'starting…' : 'ask Domovoi'}
                </Button>
              )}
            </div>
          </React.Fragment>
        )}
      </div>
    </div>
  );
};

/* Rendered once by the App shell (index.html). */
const HelpHost = () => {
  const [opts, setOpts] = React.useState(HelpStore.get());
  React.useEffect(() => HelpStore.subscribe(setOpts), []);
  if (!opts) return null;
  return <HelpPanel key={opts.at} opts={opts} onClose={HelpStore.close}/>;
};

window.DomovoiHelp = { open: (o) => HelpStore.open(o), close: () => HelpStore.close() };
window.DomovoiHelpHandoff = HelpHandoff;
window.HelpHost = HelpHost;
