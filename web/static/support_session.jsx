/* Satellite support sessions (1.5.0): an admin's shell on one satellite.
 *
 * docs/SECURITY_PRIVACY.md, "Support sessions", says what it is and why it
 * is safe. This file draws:
 *
 *  - SupportSessionBanner: on EVERY page, for every viewer (it is their
 *    room): "Support session open on Kitchen, 23 min left", with End (any
 *    paired household device may end one). Live through
 *    `support_sessions.changed` on the state socket.
 *  - SatSupportControl: the satellite drawer's Settings tab (admin): the
 *    "Allow support sessions" switch (off by default; turning it on needs
 *    a password sign-in within 5 minutes) and Open support session.
 *  - The terminal: the Open dialog asks for the password AGAIN, then the
 *    socket opens with the single-use ticket as a subprotocol (never in
 *    the URL). xterm.js draws the output; it never becomes HTML. Warnings
 *    (5 and 1 minutes left, 9 minutes idle) are shown in the frame, never
 *    typed into the shell. xterm is loaded on demand, here only, with the
 *    fit add-on and no other.
 *  - Settings → System → Support sessions: the audit list, the transcript
 *    as plain text (React text, never HTML), its download and deletes.
 *
 * One function scope: the dashboard's scripts share one global scope, so
 * only the names put on window at the end leave it.
 */
(() => {
  const XTERM = {
    css: '/vendor/xterm/xterm.css',
    js: '/vendor/xterm/xterm.min.js',
    fit: '/vendor/xterm/addon-fit.min.js',
  };
  const MAGENTA = '#FF00C8';
  const EVENT = 'support_sessions.changed';
  const INPUT_CHUNK = 3000;       // bytes per input frame (the server takes 4 KB)

  /* ── loading xterm, once, on demand ───────────────────────────── */
  const pending = {};
  const loadScript = (src) => {
    if (pending[src]) return pending[src];
    pending[src] = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.async = false;
      s.onload = () => resolve();
      s.onerror = () => { delete pending[src]; reject(new Error(`couldn't load ${src}`)); };
      document.head.appendChild(s);
    });
    return pending[src];
  };
  const loadXterm = () => {
    if (!document.querySelector(`link[href="${XTERM.css}"]`)) {
      const l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = XTERM.css;
      document.head.appendChild(l);
    }
    return loadScript(XTERM.js).then(() => loadScript(XTERM.fit))
      .then(() => ({ Terminal: window.Terminal, FitAddon: window.FitAddon && window.FitAddon.FitAddon }));
  };

  /* ── bytes ⇄ base64 ───────────────────────────────────────────── */
  const bytesToB64 = (bytes) => {
    let bin = '';
    for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  };
  const b64ToBytes = (b64) => {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
    return out;
  };

  const wsUrl = (path) => {
    let base = '';
    try { base = API_BASE || ''; } catch { base = ''; }
    const http = base || `${window.location.protocol}//${window.location.host}`;
    return http.replace(/^http/, 'ws') + path;
  };

  const minutesLeft = (endsAt) => {
    if (!endsAt) return null;
    return Math.max(0, Math.ceil((endsAt * 1000 - Date.now()) / 60000));
  };

  /* ── the open sessions: one shared read ───────────────────────── */
  const Store = (() => {
    let state = { loaded: false, sessions: [] };
    const subs = new Set();
    let started = false;
    const set = (sessions) => {
      state = { loaded: true, sessions: Array.isArray(sessions) ? sessions : [] };
      subs.forEach((fn) => { try { fn(state); } catch { /* one bad subscriber */ } });
    };
    const refresh = async () => {
      try {
        const d = await apiGet('/api/support-sessions/active', { quiet: true });
        set(d && d.sessions);
      } catch { /* not paired, or an older server: nothing to show */ }
    };
    const start = () => {
      if (started) return;
      started = true;
      refresh();
      try {
        stateBus.subscribe((ev) => {
          if (ev.type === EVENT && Array.isArray(ev.data)) set(ev.data);
          else if (ev.type === '_status' && ev.connected) refresh();
        });
      } catch { /* no socket: the poll covers it */ }
      setInterval(() => {
        let live = false;
        try { live = !!stateBus.connected; } catch { live = false; }
        if (!live && !document.hidden) refresh();
      }, 30000);
    };
    return {
      get: () => state,
      refresh,
      subscribe(fn) { subs.add(fn); start(); return () => subs.delete(fn); },
    };
  })();

  const useSupportSessions = () => {
    const [s, setS] = React.useState(() => Store.get());
    React.useEffect(() => Store.subscribe(setS), []);
    return s;
  };

  const useTick = (ms) => {
    const [, force] = React.useReducer((n) => n + 1, 0);
    React.useEffect(() => {
      const t = setInterval(force, ms);
      return () => clearInterval(t);
    }, [ms]);
  };

  /* ── the banner, every page, every viewer ─────────────────────── */
  const SupportSessionBanner = () => {
    const s = useSupportSessions();
    const [fire, toastNode] = useToast();
    const [busy, setBusy] = React.useState(null);
    useTick(15000);
    const open = (s.sessions || []).filter((x) => x.state === 'open' || x.state === 'opening');
    if (open.length === 0) return toastNode || null;
    const end = async (id) => {
      setBusy(id);
      try {
        await apiPost(`/api/support-sessions/${encodeURIComponent(id)}/end`, {});
        await Store.refresh();
      } catch (e) {
        reportMutationFailure(fire, 'end the support session', e);
      } finally { setBusy(null); }
    };
    return (
      <>
        {open.map((x) => {
          const left = minutesLeft(x.ends_at);
          return (
            <div key={x.id} className="guard-banner support-banner" role="alert">
              <StatusDot tone="err" live/>
              <span className="guard-banner-text">
                Support session open on {x.room_id || x.satellite_id}
                {left != null ? `, ${left} min left` : ''}
              </span>
              <span className="guard-banner-actions">
                <button type="button" className="btn btn-ghost guard-banner-ack mono"
                        disabled={busy === x.id} onClick={() => end(x.id)}>
                  {busy === x.id ? 'ending…' : 'End'}
                </button>
              </span>
            </div>
          );
        })}
        {toastNode}
      </>
    );
  };

  /* ── the terminal ─────────────────────────────────────────────── */
  const SupportTerminal = ({ satellite, onClose }) => {
    const [step, setStep] = React.useState('password');  // password | opening | open | ended
    const [password, setPassword] = React.useState('');
    const [error, setError] = React.useState(null);
    const [stale, setStale] = React.useState(false);
    const [warning, setWarning] = React.useState(null);
    const [ended, setEnded] = React.useState(null);
    const holder = React.useRef(null);
    const live = React.useRef({ ws: null, term: null, fit: null });

    React.useEffect(() => () => {
      const l = live.current;
      try { if (l.ws) l.ws.close(); } catch { /* gone */ }
      try { if (l.term) l.term.dispose(); } catch { /* gone */ }
      window.removeEventListener('resize', l.onResize || (() => {}));
    }, []);

    const send = (msg) => {
      const ws = live.current.ws;
      if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
    };

    const attach = async (opened) => {
      const { Terminal, FitAddon } = await loadXterm();
      if (!Terminal || !FitAddon) throw new Error("the terminal didn't load");
      // No link or clipboard add-ons; windowOptions stays all-off (the default).
      const term = new Terminal({
        cursorBlink: true, fontSize: 13, scrollback: 2000,
        fontFamily: '"JetBrains Mono", ui-monospace, monospace',
        theme: { background: '#111111' },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(holder.current);
      try { fit.fit(); } catch { /* sized later */ }
      live.current.term = term;
      live.current.id = opened.session_id;
      live.current.fit = fit;
      const ws = new WebSocket(
        wsUrl(`/api/support-sessions/${encodeURIComponent(opened.session_id)}/terminal`),
        ['domovoi-support', `ticket.${opened.ticket}`],
      );
      live.current.ws = ws;
      ws.onopen = () => send({ t: 'resize', cols: term.cols, rows: term.rows });
      ws.onmessage = (ev) => {
        let msg = null;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (!msg || typeof msg !== 'object') return;
        if (msg.t === 'out' && typeof msg.d === 'string') term.write(b64ToBytes(msg.d));
        else if (msg.t === 'opened') { setStep('open'); term.focus(); }
        else if (msg.t === 'warn' && typeof msg.text === 'string') setWarning(msg.text);
        else if (msg.t === 'end') {
          setEnded(String(msg.text || msg.reason || 'ended'));
          setStep('ended');
        }
      };
      ws.onclose = () => {
        setStep((cur) => (cur === 'ended' ? cur : 'ended'));
        setEnded((cur) => cur || 'the connection closed');
        Store.refresh();
      };
      const enc = new TextEncoder();
      const input = (bytes) => {
        for (let i = 0; i < bytes.length; i += INPUT_CHUNK) {
          send({ t: 'in', d: bytesToB64(bytes.subarray(i, i + INPUT_CHUNK)) });
        }
      };
      term.onData((d) => input(enc.encode(d)));
      term.onBinary((d) => {
        const b = new Uint8Array(d.length);
        for (let i = 0; i < d.length; i += 1) b[i] = d.charCodeAt(i) & 0xff;
        input(b);
      });
      term.onResize(({ cols, rows }) => send({ t: 'resize', cols, rows }));
      live.current.onResize = () => { try { fit.fit(); } catch { /* closed */ } };
      window.addEventListener('resize', live.current.onResize);
    };

    const open = async () => {
      if (!password) return;
      setStep('opening'); setError(null); setStale(false);
      try {
        const opened = await apiPost(
          `/api/satellites/${encodeURIComponent(satellite.room_id)}/support/open`,
          { password, cols: 100, rows: 30 },
        );
        setPassword('');
        await attach(opened);
        Store.refresh();
      } catch (e) {
        const text = apiErrorText(e, 0);
        setStale(/sign in with the password again/.test(text));
        setError(text);
        setStep('password');
      }
    };

    const endNow = async () => {
      try {
        if (live.current.id) await apiPost(`/api/support-sessions/${encodeURIComponent(live.current.id)}/end`, {});
      } catch { /* the socket close below ends it anyway */ }
      try { if (live.current.ws) live.current.ws.close(); } catch { /* gone */ }
    };

    return (
      <div style={{ position: 'fixed', inset: 0, zIndex: 95, background: 'rgba(0,0,0,0.5)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
        <div role="dialog" aria-modal="true" aria-label="Support session"
             style={{ width: step === 'password' ? 'min(480px, 100%)' : 'min(980px, 100%)',
                      background: 'var(--card)', border: `1px solid ${MAGENTA}`,
                      borderRadius: 'var(--r-lg)', boxShadow: 'var(--shadow-md)', overflow: 'hidden',
                      display: 'flex', flexDirection: 'column', maxHeight: '100%' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px',
                        borderBottom: '1px solid var(--border-soft)' }}>
            <Icon name="terminal" size={16}/>
            <strong style={{ fontSize: 14 }}>Support session · {satellite.room_id}</strong>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              {step === 'open' && <Button icon="square" onClick={endNow}>End</Button>}
              {step !== 'open' && step !== 'opening' && <IconButton name="x" onClick={onClose}/>}
            </div>
          </div>
          {step === 'password' || step === 'opening' ? (
            <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
              <div style={{ color: 'var(--fg-muted)' }}>
                Opens a shell on this satellite as its service account (not root), for at most
                30 minutes, closing after 10 minutes without typing. The room hears that it
                started, its light breathes magenta, every dashboard shows a banner, and
                everything is recorded. Anyone in the house can end it.
              </div>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span>Admin password</span>
                <input type="password" className="input" autoComplete="current-password" value={password}
                       autoFocus onChange={(e) => setPassword(e.target.value)}
                       onKeyDown={(e) => { if (e.key === 'Enter') open(); }}/>
              </label>
              {error && <div style={{ color: 'var(--err-ink)' }}>{error}</div>}
              {stale && (
                <div>
                  <button type="button" className="home-link"
                          onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>
                    sign in again
                  </button>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <Button icon="x" onClick={onClose} disabled={step === 'opening'}>Cancel</Button>
                <Button variant="primary" icon="terminal" disabled={!password || step === 'opening'}
                        onClick={open}>{step === 'opening' ? 'Opening…' : 'Open'}</Button>
              </div>
            </div>
          ) : null}
          {(warning || ended) && (
            <div role="status" style={{ padding: '8px 16px', fontSize: 13,
                                        background: ended ? 'var(--sunken)' : 'var(--warn-soft)' }}>
              {ended ? `Session ended: ${ended}` : warning}
            </div>
          )}
          <div ref={holder} style={{ display: step === 'password' ? 'none' : 'block', height: '60vh',
                                     minHeight: 240, background: '#111111', padding: 4 }}/>
        </div>
      </div>
    );
  };

  /* ── the satellite drawer's Settings tab ──────────────────────── */
  const SatSupportControl = ({ s, fire, refresh }) => {
    const admin = useAdminSignedIn();
    const [busy, setBusy] = React.useState(false);
    const [error, setError] = React.useState(null);
    const [terminal, setTerminal] = React.useState(false);
    if (!admin || !s) return null;
    const online = s.status === 'online';
    const toggle = async (on) => {
      setBusy(true); setError(null);
      try {
        await apiPost(`/api/satellites/${encodeURIComponent(s.room_id)}/support/${on ? 'allow' : 'disallow'}`, {});
        if (refresh) refresh();
      } catch (e) {
        setError(apiErrorText(e, 0));
      } finally { setBusy(false); }
    };
    return (
      <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)',
                    display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
        <div style={{ fontWeight: 600 }}>Support sessions</div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={!!s.support_allowed} disabled={busy}
                 onChange={(e) => toggle(e.target.checked)}/>
          <span>Allow support sessions</span>
        </label>
        <div style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
          Lets an admin open a shell on this satellite for a fault the diagnostics can’t fix.
          Turning it on needs a password sign-in in the last 5 minutes.
          {!s.support_shell && online && ' This device doesn’t offer support sessions (only a Raspberry Pi on a prepared card whose account can’t use sudo does).'}
        </div>
        {error && <div style={{ color: 'var(--err-ink)', fontSize: 12 }}>{error}</div>}
        {s.support_allowed && s.support_shell && online && (
          <div><Button icon="terminal" onClick={() => setTerminal(true)}>Open support session</Button></div>
        )}
        <a className="home-link" href="#settings/support-sessions" style={{ fontSize: 12 }}>past support sessions</a>
        {terminal && <SupportTerminal satellite={s} onClose={() => { setTerminal(false); if (refresh) refresh(); }}/>}
      </div>
    );
  };

  /* ── Settings → System → Support sessions (admin) ─────────────── */
  const stamp = (iso) => (iso ? new Date(iso).toLocaleString() : '');

  const SupportSessionsList = () => {
    const [rows, setRows] = React.useState(null);
    const [error, setError] = React.useState(null);
    const [viewing, setViewing] = React.useState(null);   // {id, text}
    const [fire, toastNode] = useToast();
    const load = async () => {
      setError(null);
      try {
        const d = await apiGet('/api/support-sessions');
        setRows((d && d.sessions) || []);
      } catch (e) { setError(apiErrorText(e, 0)); }
    };
    React.useEffect(() => { load(); }, []);
    useStateEvents([EVENT], () => { load(); });
    const view = async (id) => {
      try {
        const d = await apiGet(`/api/support-sessions/${encodeURIComponent(id)}/transcript`);
        setViewing({ id, text: String((d && d.text) || '') });
      } catch (e) { reportMutationFailure(fire, 'read the transcript', e); }
    };
    const download = async (id) => {
      try {
        const r = await apiFetchRaw(`/api/support-sessions/${encodeURIComponent(id)}/transcript.cast`);
        const blob = new Blob([await r.text()], { type: 'text/plain' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `support-session-${id}.cast`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      } catch (e) { reportMutationFailure(fire, 'download the transcript', e); }
    };
    const remove = async (id) => {
      try {
        await apiDelete(`/api/support-sessions/${encodeURIComponent(id)}`);
        if (viewing && viewing.id === id) setViewing(null);
        load();
      } catch (e) { reportMutationFailure(fire, 'delete', e); }
    };
    const removeAll = async () => {
      try { await apiDelete('/api/support-sessions'); setViewing(null); load(); } catch (e) { reportMutationFailure(fire, 'delete', e); }
    };
    return (
      <Card title="Support sessions"
            sub="Every shell an admin opened on a satellite: who, when, how it ended. Transcripts are kept 30 days (200 MB in all); secrets in them are blanked where Domovoi can tell, so they stay admin-only."
            action={rows && rows.some((r) => r.closed_at) ? <Button icon="trash-2" onClick={removeAll}>Delete all</Button> : null}>
        {error && <div style={{ padding: '12px 16px', color: 'var(--err-ink)', fontSize: 13 }}>{error}</div>}
        {rows && rows.length === 0 && <Empty glyph="sleeping" title="no support sessions yet"/>}
        {rows && rows.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {rows.map((r) => (
              <div key={r.id} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10,
                                       padding: '10px 16px', borderTop: '1px solid var(--border-soft)', fontSize: 13 }}>
                <div style={{ fontWeight: 600 }}>{r.room_id || r.satellite_id}</div>
                <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{stamp(r.opened_at)}</span>
                <span style={{ color: 'var(--fg-muted)' }}>
                  {r.closed_at ? (r.end_text || r.end_reason) : 'open'}
                  {r.opener_label ? ` · by ${r.opener_label}` : ''}
                  {r.truncated ? ' · log full' : ''}
                </span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                  {r.has_transcript && <Button icon="file-text" onClick={() => view(r.id)}>View</Button>}
                  {r.has_transcript && <Button icon="download" onClick={() => download(r.id)}>Download</Button>}
                  {r.closed_at && <IconButton name="trash-2" onClick={() => remove(r.id)}/>}
                </div>
              </div>
            ))}
          </div>
        )}
        {viewing && (
          // Plain text only: React sets it as a text node, never as HTML.
          <pre className="mono" style={{ margin: 0, padding: '10px 16px', background: 'var(--sunken)',
                                         fontSize: 11, lineHeight: 1.5, whiteSpace: 'pre-wrap',
                                         wordBreak: 'break-word', maxHeight: 420, overflow: 'auto',
                                         borderTop: '1px solid var(--border-soft)' }}>{viewing.text}</pre>
        )}
        {toastNode}
      </Card>
    );
  };

  const SupportSessionsPanel = () => {
    const admin = useAdminSignedIn();
    if (!admin) {
      return (
        <Card title="Support sessions" sub="Shells an admin opened on a satellite.">
          <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--fg-muted)' }}>
            Admins only.{' '}
            <button type="button" className="home-link"
                    onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</button>
          </div>
        </Card>
      );
    }
    return <SupportSessionsList/>;
  };

  window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
  window.DomovoiSettingsSections['support-sessions'] = { Panel: SupportSessionsPanel };
  Object.assign(window, { SupportSessionBanner, SatSupportControl });
})();
