/* Settings → Household → Friend Houses (F2: friending; F3: messages; F4: shared lists).
 *
 * Serverless messaging and friend system between households. This
 * section turns it on or off (the friend_houses_enabled setting, off by
 * default), names this house, makes and shares friend files, adds a
 * friend from theirs, answers requests and keeps the friends list.
 * F3 adds the Messages card (conversations, compose, save a copy, delete)
 * and each friend's alert choice (what a new message does in the rooms).
 *
 * Data (admin, security tier at both hops; web/backend/api/friend_houses.py
 * proxies to the core's /v1/admin/friend-houses):
 *   GET    /api/friend-houses                       · status, this house
 *   PUT    /api/friend-houses/settings              · house name, default length
 *   GET    /api/friend-houses/invites               · open friend files
 *   POST   /api/friend-houses/invites               · make one (file may be null: poll)
 *   GET    /api/friend-houses/invites/{id}          · one, with its file once ready
 *   DELETE /api/friend-houses/invites/{id}          · cancel
 *   POST   /api/friend-houses/preview               · what their file says
 *   POST   /api/friend-houses/requests              · send a request
 *   GET    /api/friend-houses/requests              · in and out
 *   POST   /api/friend-houses/requests/{id}/accept|decline
 *   DELETE /api/friend-houses/requests/{id}            (take back one this house sent)
 *   GET    /api/friend-houses/friends
 *   POST   /api/friend-houses/friends/{id}/verified
 *   PATCH  /api/friend-houses/friends/{id}          · rename
 *   DELETE /api/friend-houses/friends/{id}          · remove
 *   PUT    /api/friend-houses/friends/{id}/alert    · inbox | sender | read
 *   GET    /api/friend-houses/conversations         · the inbox, unread total
 *   GET    /api/friend-houses/conversations/{house}?before=&limit=  · oldest first
 *   POST   /api/friend-houses/conversations/{house}/read
 *   GET    /api/friend-houses/conversations/{house}/export  · saved as a file
 *   DELETE /api/friend-houses/conversations/{house}
 *   POST   /api/friend-houses/messages              · {friend_id, text}
 *   GET    /api/friend-houses/shared-lists          · shared both ways, offers waiting
 *   POST   /api/friend-houses/shared-lists          · {list_id, friend_id}
 *   POST   /api/friend-houses/shared-lists/{id}/accept|decline
 *   DELETE /api/friend-houses/shared-lists/{id}     · stop sharing (both keep a copy)
 * The household's lists for the share picker: GET /api/lists (lists.jsx).
 * Turning it on or off is the same PATCH /api/config/editable every other
 * switch uses.
 *
 * SECURITY (threat-model finding 1): every string that came from another
 * house (their name, a message, a label, a fingerprint, an error, a detail) is
 * rendered as plain text through React's escaping. Nothing in this file
 * renders HTML or markdown, links text, or previews a link. A friend file
 * is a bearer secret: it lives only in this component's memory and in a
 * POST body, never in an address, browser storage or the console; so is
 * a message's text.
 *
 * Globals this file adds (one shared scope): FriendHousesPanel and the
 * Fh* / fh* / FH_* helpers. No object rest (`{ a, ...rest }`): see
 * printers.jsx.
 */

const FH_API = '/api/friend-houses';
const FH_DESCRIPTION = 'Message and share lists with friends’ houses, house to house, with no company server in between';
const FH_POLL_MS = 15000;
const FH_FILE_POLL_MS = 5000;
const FH_NAME_MAX = 40;
const FH_LABEL_MAX = 60;
const FH_FILE_MAX_CHARS = 16000;

const fhDay = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch { return '—'; }
};
const fhDays = (n) => `${n} ${n === 1 ? 'day' : 'days'}`;
const fhRange = (lo, hi) => {
  const out = [];
  for (let i = lo; i <= hi; i += 1) out.push(i);
  return out;
};
// The server's own sentence for a refusal, as written (detail as-is).
const fhErr = (e) => apiErrorText(e, 0);
const fhFail = (fire, e) => {
  if (isAuthFailure(e)) return;
  fire(fhErr(e), { tone: 'warn' });
};
// A file name safe to hand the browser: no folders, our extension.
const fhFileName = (name) => {
  const base = String(name || '').split(/[\\/]/).pop().replace(/[^A-Za-z0-9._ -]/g, '').trim();
  const stem = base.replace(/\.domovoi-friend$/i, '') || 'friend-file';
  return `${stem}.domovoi-friend`;
};

const fhFieldStyle = {
  font: 'inherit', fontSize: 13, padding: '7px 10px', borderRadius: 'var(--r-sm)',
  border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)', minWidth: 0,
};
const fhMuted = { fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 };
const fhRow = { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' };
const fhStack = { display: 'flex', flexDirection: 'column', gap: 12 };
const fhItem = { padding: '12px 0', borderTop: '1px solid var(--border-soft)', display: 'flex',
                 flexDirection: 'column', gap: 8 };

const FhNote = ({ children }) => (
  <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>{children}</div>
);

// The 4-digit code, large, one digit per cell (plain text).
const FhCode = ({ code }) => (
  <span className="mono" aria-label={`code ${String(code || '').split('').join(' ')}`}
        style={{ fontSize: 28, fontWeight: 600, letterSpacing: '0.3em', color: 'var(--fg)' }}>
    {String(code || '')}
  </span>
);

// A fingerprint: small, mono, copyable.
const FhFingerprint = ({ value, fire }) => {
  if (!value) return <span style={fhMuted}>—</span>;
  const copy = async () => {
    const ok = await copyToClipboard(String(value));
    fire(ok ? 'fingerprint copied' : 'copy failed — select it and copy by hand', { tone: ok ? 'ok' : 'warn' });
  };
  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', minWidth: 0, maxWidth: '100%' }}>
      <code className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>{String(value)}</code>
      <IconButton name="copy" title="copy the fingerprint" aria-label="copy the fingerprint" onClick={copy}/>
    </span>
  );
};

/* Poll `fn` every `ms` while the page is visible (and once on coming back). */
const useFhEvery = (ms, fn, on) => {
  const ref = React.useRef(fn);
  ref.current = fn;
  React.useEffect(() => {
    if (!on) return undefined;
    const tick = () => { if (!document.hidden) ref.current(); };
    const t = setInterval(tick, ms);
    document.addEventListener('visibilitychange', tick);
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', tick); };
  }, [ms, on]);
};

/* ---- status --------------------------------------------------------- */

const FH_STATUS = {
  on: { tone: 'ok', word: 'on' },
  starting: { tone: 'warn', word: 'starting', live: true },
  stopped: { tone: 'idle', word: 'stopped' },
  unavailable: { tone: 'idle', word: 'unavailable' },
  error: { tone: 'err', word: 'not working' },
  off: { tone: 'idle', word: 'off' },
};

const FhStatusLine = ({ status }) => {
  const s = FH_STATUS[status && status.status] || FH_STATUS.off;
  return (
    <div style={{ ...fhRow, fontSize: 13 }} role="status">
      <StatusDot tone={s.tone} live={!!s.live}/>
      <span>Private connection: <strong>{s.word}</strong></span>
      {status && status.detail && <span style={fhMuted}>· {String(status.detail)}</span>}
    </div>
  );
};

/* The installer's firewall for the Tor container (status.firewall:
   in_place | missing | unknown). Without it the container could reach this
   server's own ports and the home network, so say so plainly. */
const FhFirewallNote = ({ status }) => (
  status && status.firewall === 'missing' ? (
    <div className="chat-note" role="note">
      This server doesn’t have the firewall rules that keep Friend Houses’ private connection away from
      the rest of your home network. Update Domovoi (or run its installer again) before using Friend Houses.
    </div>
  ) : null
);

/* What changed who this house trusts in the last week (status.changes):
   new friend houses and lists shared with one. Said aloud too, but quiet
   hours hold that back, so it stays written here (audit R6 FH-8). */
const FhChangesNote = ({ status }) => {
  const changes = (status && Array.isArray(status.changes)) ? status.changes : [];
  if (!changes.length) return null;
  return (
    <div className="chat-note" role="note">
      <div><strong>This week</strong></div>
      {changes.map((c, i) => (
        <div key={i}>
          {c.kind === 'friend'
            ? <>{String(c.friend || 'A house')} became a friend house</>
            : <>“{String(c.list || 'A list')}” was shared with {String(c.friend || 'a friend house')}</>}
          {' '}<span style={fhMuted}>{fhDay(c.at)}</span>
        </div>
      ))}
      <div style={fhMuted}>Not you? Remove or block the house below, and change the admin password.</div>
    </div>
  );
};

/* A notice from core (status.notice), e.g. a friend file it closed after
   repeated failed tries. Stays until dismissed or a new file is made. */
const FhNoticeNote = ({ status, fire, onDismissed }) => {
  const n = status && status.notice;
  const [busy, setBusy] = React.useState(false);
  if (!n || !n.text) return null;
  const dismiss = async () => {
    setBusy(true);
    try { await apiDelete(`${FH_API}/notice`); onDismissed(); } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  return (
    <div className="chat-note" role="note" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
      <span style={{ flex: 1 }}>{String(n.text)} <span style={fhMuted}>{fhDay(n.at)}</span></span>
      <Button variant="ghost" disabled={busy} onClick={dismiss}>Dismiss</Button>
    </div>
  );
};

/* ---- off ------------------------------------------------------------ */

const FhOffCard = ({ internetNever, detail, busy, onTurnOn, status }) => (
  <Card title="Friend Houses" sub={FH_DESCRIPTION}>
    <div style={{ ...fhStack, padding: '12px 16px' }}>
      <div style={{ fontSize: 13, lineHeight: 1.5 }}>
        Become friends with other Domovoi houses. Messages go house to house through the Tor network;
        no Coders Farm server is involved. Where using Tor is restricted, leave this off.
      </div>
      <FhFirewallNote status={status}/>
      {internetNever ? (
        <div className="chat-note info" role="note">
          {detail ? String(detail) : 'This Domovoi stays off the internet, so Friend Houses can’t run.'}
          {' '}It can be turned on once <a href="#settings/internet">Settings → Internet</a> allows the internet.
        </div>
      ) : (
        <div>
          <Button variant="primary" icon="power" disabled={busy} onClick={onTurnOn}>
            {busy ? 'Turning on…' : 'Turn on'}
          </Button>
        </div>
      )}
    </div>
  </Card>
);

/* ---- this house ----------------------------------------------------- */

const FhHouseCard = ({ status, fire, onSaved, onTurnOff, busy }) => {
  const [name, setName] = React.useState(status.house_name || '');
  const [saving, setSaving] = React.useState(false);
  React.useEffect(() => { setName(status.house_name || ''); }, [status.house_name]);
  const limits = status.limits || { min_days: 1, max_days: 30 };
  const save = async (body, said) => {
    setSaving(true);
    try {
      await apiPut(`${FH_API}/settings`, body);
      fire(said, { tone: 'ok' });
      onSaved();
    } catch (e) { fhFail(fire, e); } finally { setSaving(false); }
  };
  const trimmed = name.trim();
  const nameChanged = trimmed !== (status.house_name || '') && trimmed !== '';
  return (
    <Card title="This house" sub="What friends see, and the friend files it makes."
          action={<Button icon="power" disabled={busy} onClick={onTurnOff}>{busy ? 'Turning off…' : 'Turn off'}</Button>}>
      <div style={{ ...fhStack, padding: '12px 16px' }}>
        <FhStatusLine status={status}/>
        <FhFirewallNote status={status}/>
        <FhNoticeNote status={status} fire={fire} onDismissed={onSaved}/>
        <FhChangesNote status={status}/>
        <div style={fhRow}>
          <label htmlFor="fh-house-name" style={{ fontSize: 13 }}><strong>The name friends see</strong></label>
          <input id="fh-house-name" value={name} maxLength={FH_NAME_MAX} disabled={saving}
                 autoComplete="off" style={{ ...fhFieldStyle, flex: '1 1 200px' }}
                 onChange={(e) => setName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Enter' && nameChanged) save({ house_name: trimmed }, 'name saved'); }}/>
          {nameChanged && (
            <Button variant="primary" disabled={saving} onClick={() => save({ house_name: trimmed }, 'name saved')}>Save</Button>
          )}
        </div>
        <div style={fhRow}>
          <span style={{ fontSize: 13 }}><strong>Fingerprint</strong></span>
          {status.fingerprint
            ? <FhFingerprint value={status.fingerprint} fire={fire}/>
            : <span style={fhMuted}>made when the private connection first starts</span>}
        </div>
        <div style={fhRow}>
          <label htmlFor="fh-default-days" style={{ fontSize: 13 }}><strong>A friend file lasts</strong></label>
          <select id="fh-default-days" value={String(status.invite_days || 7)} disabled={saving}
                  style={fhFieldStyle}
                  onChange={(e) => { const n = Number(e.target.value); save({ invite_days: n }, `friend files last ${fhDays(n)}`); }}>
            {fhRange(limits.min_days || 1, limits.max_days || 30).map((n) => (
              <option key={n} value={String(n)}>{fhDays(n)}</option>
            ))}
          </select>
          <span style={fhMuted}>the household default; each file can change it</span>
        </div>
      </div>
    </Card>
  );
};

/* ---- make a friend file --------------------------------------------- */

const fhDownload = (file) => {
  const blob = new Blob([String(file.text)], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fhFileName(file.filename);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const FhMadeFile = ({ made, fire, onDone }) => {
  const copy = async () => {
    const ok = await copyToClipboard(String(made.file.text));
    fire(ok ? 'friend file copied' : 'copy failed — use Download instead', { tone: ok ? 'ok' : 'warn' });
  };
  return (
    <div className="chat-note info" role="note" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {made.file ? (
        <>
          <div><strong>Your friend file is ready.</strong>{made.label ? <> ({String(made.label)})</> : null} It works until {fhDay(made.expires_at)}.</div>
          <div>Anyone who gets this file can ask to be your friend. Nothing happens unless you accept.</div>
          <div style={fhRow}>
            <Button variant="primary" icon="download" onClick={() => fhDownload(made.file)}>Download</Button>
            <Button icon="copy" onClick={copy}>Copy as text</Button>
            <Button variant="ghost" onClick={onDone}>Done</Button>
          </div>
        </>
      ) : made.gone ? (
        <>
          <div>{String(made.gone)}</div>
          <div><Button variant="ghost" onClick={onDone}>Done</Button></div>
        </>
      ) : (
        <div style={fhRow}>
          <StatusDot tone="warn" live/>
          <span>Getting the private connection ready… this can take a few minutes the first time.</span>
        </div>
      )}
    </div>
  );
};

const FhInvitesCard = ({ status, invites, fire, onChanged }) => {
  const limits = status.limits || { min_days: 1, max_days: 30, max_open_files: 10 };
  const [days, setDays] = React.useState('');
  const [label, setLabel] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [made, setMade] = React.useState(null);   // {id, label, expires_at, file, gone}
  const list = (invites && invites.invites) || [];
  const full = list.length >= (limits.max_open_files || 10);

  // While the file is still being made: ask again every 5 s.
  const waitingId = made && !made.file && !made.gone ? made.id : null;
  React.useEffect(() => {
    if (!waitingId) return undefined;
    let stop = false;
    const look = async () => {
      try {
        const r = await apiGet(`${FH_API}/invites/${encodeURIComponent(waitingId)}`, { quiet: true });
        if (!stop && r && r.file) setMade((m) => (m && m.id === waitingId ? Object.assign({}, m, { file: r.file }) : m));
      } catch (e) {
        if (!stop && e && e.status === 404) {
          setMade((m) => (m && m.id === waitingId ? Object.assign({}, m, { gone: fhErr(e) }) : m));
        }
      }
    };
    const t = setInterval(look, FH_FILE_POLL_MS);
    return () => { stop = true; clearInterval(t); };
  }, [waitingId]);

  const make = async () => {
    setBusy(true);
    try {
      const body = {};
      if (days) body.days = Number(days);
      if (label.trim()) body.label = label.trim();
      const r = await apiPost(`${FH_API}/invites`, body);
      setMade({ id: r.id, label: r.label, expires_at: r.expires_at, file: r.file || null, gone: null });
      setLabel('');
      setDays('');
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  // An open file's text again (left the page while it was being made, say).
  const reopen = async (inv) => {
    try {
      const r = await apiGet(`${FH_API}/invites/${encodeURIComponent(inv.id)}`, { quiet: true });
      setMade({ id: r.id, label: r.label, expires_at: r.expires_at, file: r.file || null, gone: null });
    } catch (e) { fhFail(fire, e); onChanged(); }
  };
  const cancel = async (inv) => {
    try {
      await apiDelete(`${FH_API}/invites/${encodeURIComponent(inv.id)}`);
      if (made && made.id === inv.id) setMade(null);
      fire('friend file cancelled', { tone: 'ok' });
      onChanged();
    } catch (e) { fhFail(fire, e); }
  };

  return (
    <Card title="Make a friend file" sub="Give it to a house you want to be friends with, by any means you trust.">
      <div style={{ ...fhStack, padding: '12px 16px' }}>
        {made && <FhMadeFile made={made} fire={fire} onDone={() => setMade(null)}/>}
        {full ? (
          <div style={fhMuted}>
            {`${limits.max_open_files || 10} friend files are open, the most there can be. Cancel one below, or wait for one to be used or to expire, to make another.`}
          </div>
        ) : (
          <>
            <div style={fhRow}>
              <label htmlFor="fh-invite-days" style={{ fontSize: 13 }}>Lasts</label>
              <select id="fh-invite-days" value={days} disabled={busy} style={fhFieldStyle}
                      onChange={(e) => setDays(e.target.value)}>
                <option value="">{`the house default (${fhDays(status.invite_days || 7)})`}</option>
                {fhRange(limits.min_days || 1, limits.max_days || 30).map((n) => (
                  <option key={n} value={String(n)}>{fhDays(n)}</option>
                ))}
              </select>
              <label htmlFor="fh-invite-label" style={{ fontSize: 13 }}>Note</label>
              <input id="fh-invite-label" value={label} maxLength={FH_LABEL_MAX} disabled={busy}
                     placeholder="for the Smiths (optional)" autoComplete="off"
                     style={{ ...fhFieldStyle, flex: '1 1 180px' }}
                     onChange={(e) => setLabel(e.target.value)}/>
              <Button variant="primary" icon="file-plus" disabled={busy} onClick={make}>{busy ? 'Making…' : 'Make'}</Button>
            </div>
            <div style={fhMuted}>Anyone who gets this file can ask to be your friend. Nothing happens unless you accept.</div>
          </>
        )}
        {list.length > 0 && (
          <div>
            <div className="label" style={{ marginBottom: 4 }}>open friend files</div>
            {list.map((inv) => (
              <div key={inv.id} style={{ ...fhRow, padding: '8px 0', borderTop: '1px solid var(--border-soft)', fontSize: 13 }}>
                <span style={{ flex: '1 1 160px', minWidth: 0, overflowWrap: 'anywhere' }}>
                  {inv.label ? String(inv.label) : 'no note'}
                </span>
                <span style={fhMuted}>works until {fhDay(inv.expires_at)}</span>
                {!(made && made.id === inv.id) && <Button icon="file-text" onClick={() => reopen(inv)}>Show file</Button>}
                <Button icon="x" onClick={() => cancel(inv)}>Cancel</Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
};

/* ---- add a friend from their file ----------------------------------- */

const FhAddCard = ({ fire, onSent }) => {
  const [text, setText] = React.useState('');
  const [preview, setPreview] = React.useState(null);
  const [name, setName] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [problem, setProblem] = React.useState(null);
  const fileRef = React.useRef(null);

  const reset = () => {
    setText(''); setPreview(null); setName(''); setProblem(null);
    if (fileRef.current) fileRef.current.value = '';
  };
  const onText = (v) => { setText(v); setPreview(null); setProblem(null); };
  const onFile = (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    if (f.size > FH_FILE_MAX_CHARS) { setProblem('That file is far too big to be a friend file.'); return; }
    const reader = new FileReader();
    reader.onload = () => onText(String(reader.result || ''));
    reader.onerror = () => setProblem('That file couldn’t be read.');
    reader.readAsText(f);
  };
  const look = async () => {
    setBusy(true); setProblem(null);
    try {
      const r = await apiPost(`${FH_API}/preview`, { file: text });
      setPreview(r);
      setName(String((r && r.name) || '').slice(0, FH_NAME_MAX));
    } catch (e) {
      if (!isAuthFailure(e)) setProblem(fhErr(e));
    } finally { setBusy(false); }
  };
  const send = async () => {
    setBusy(true); setProblem(null);
    try {
      await apiPost(`${FH_API}/requests`, { file: text, name: name.trim() });
      fire('request sent — it shows under Requests until they accept', { tone: 'ok' });
      reset();
      onSent();
    } catch (e) {
      if (!isAuthFailure(e)) setProblem(fhErr(e));
    } finally { setBusy(false); }
  };

  const blocked = preview && (preview.expired || preview.already_friends || preview.already_asked || preview.blocked);
  return (
    <Card title="Add a friend from their file" sub="Open the friend file another house gave you, or paste its text.">
      <div style={{ ...fhStack, padding: '12px 16px' }}>
        <div style={fhRow}>
          <label htmlFor="fh-file" style={{ fontSize: 13 }}>Friend file</label>
          <input id="fh-file" ref={fileRef} type="file" accept=".domovoi-friend,text/plain" disabled={busy}
                 onChange={onFile} style={{ fontSize: 13, maxWidth: '100%' }}/>
        </div>
        <textarea className="cal-inp mono" rows={4} value={text} disabled={busy}
                  aria-label="or paste the friend file’s text" placeholder="or paste the friend file’s text here"
                  autoComplete="off" spellCheck={false} style={{ fontSize: 11, width: '100%', boxSizing: 'border-box' }}
                  onChange={(e) => onText(e.target.value)}/>
        {!preview && (
          <div style={fhRow}>
            <Button variant="primary" icon="eye" disabled={busy || !text.trim()} onClick={look}>
              {busy ? 'Reading…' : 'Preview'}
            </Button>
            {text && <Button variant="ghost" disabled={busy} onClick={reset}>Clear</Button>}
          </div>
        )}
        {problem && <div role="alert" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{String(problem)}</div>}
        {preview && (
          <div style={{ ...fhStack, paddingTop: 4 }}>
            <div style={{ fontSize: 13 }}>
              A house that calls itself <strong>“{String(preview.name || '')}”</strong>
            </div>
            <div style={fhRow}>
              <span style={fhMuted}>fingerprint</span>
              <FhFingerprint value={preview.fingerprint} fire={fire}/>
            </div>
            {preview.expired && <div className="chat-note" role="note">This friend file has expired. Ask them for a new one.</div>}
            {preview.already_friends && <div className="chat-note info" role="note">You’re already friends with this house.</div>}
            {preview.already_asked && <div className="chat-note info" role="note">You’ve already asked this house; the request is under Requests.</div>}
            {preview.blocked && <div className="chat-note" role="note">You blocked this house. Unblock it first if you want to be friends again.</div>}
            {!blocked && (
              <div style={fhRow}>
                <label htmlFor="fh-their-name" style={{ fontSize: 13 }}><strong>What do you call them?</strong></label>
                <input id="fh-their-name" value={name} maxLength={FH_NAME_MAX} disabled={busy} autoComplete="off"
                       style={{ ...fhFieldStyle, flex: '1 1 180px' }} onChange={(e) => setName(e.target.value)}/>
              </div>
            )}
            <div style={fhRow}>
              {!blocked && (
                <Button variant="primary" icon="send" disabled={busy || !name.trim()} onClick={send}>
                  {busy ? 'Sending…' : 'Send request'}
                </Button>
              )}
              <Button variant="ghost" disabled={busy} onClick={reset}>{blocked ? 'Done' : 'Cancel'}</Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
};

/* ---- requests ------------------------------------------------------- */

const FhIncoming = ({ req, fire, onChanged }) => {
  const [name, setName] = React.useState(String(req.their_name || '').slice(0, FH_NAME_MAX));
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
  const accept = async () => {
    setBusy(true);
    try {
      await apiPost(`${FH_API}/requests/${encodeURIComponent(req.id)}/accept`, { name: name.trim() });
      fire('friends now', { tone: 'ok' });
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  const decline = async () => {
    setBusy(true);
    try {
      await apiPost(`${FH_API}/requests/${encodeURIComponent(req.id)}/decline`, {});
      fire('request declined', { tone: 'ok' });
      setConfirm(false);
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  return (
    <div style={fhItem}>
      <div style={{ fontSize: 13 }}>
        <strong>{String(req.their_name || '')}</strong> <span style={fhMuted}>(calls itself)</span> wants to be friends
      </div>
      {req.code && (
        <div style={fhRow}>
          <FhCode code={req.code}/>
          <span style={fhMuted}>Optional: call them and check they see the same code.</span>
        </div>
      )}
      <div style={fhRow}>
        <span style={fhMuted}>fingerprint</span>
        <FhFingerprint value={req.fingerprint} fire={fire}/>
      </div>
      <div style={fhRow}>
        <label htmlFor={`fh-accept-${req.id}`} style={{ fontSize: 13 }}>What do you call them?</label>
        <input id={`fh-accept-${req.id}`} value={name} maxLength={FH_NAME_MAX} disabled={busy} autoComplete="off"
               style={{ ...fhFieldStyle, flex: '1 1 160px' }} onChange={(e) => setName(e.target.value)}/>
        <Button variant="primary" icon="check" disabled={busy || !name.trim()} onClick={accept}>Accept</Button>
        <Button icon="x" disabled={busy} onClick={() => setConfirm(true)}>Decline</Button>
      </div>
      <DeleteConfirmDialog open={confirm} title="Decline this request?" icon="x" confirmLabel="Decline" busy={busy}
                           onCancel={() => setConfirm(false)} onConfirm={decline}>
        <div>{`${String(req.their_name || 'This house')} won’t become a friend of this house.`}</div>
      </DeleteConfirmDialog>
    </div>
  );
};

const FhOutgoing = ({ req, fire, onChanged }) => {
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
  const withdraw = async () => {
    setBusy(true);
    try {
      await apiDelete(`${FH_API}/requests/${encodeURIComponent(req.id)}`);
      fire('request taken back', { tone: 'ok' });
      setConfirm(false);
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  return (
  <div style={fhItem}>
    <div style={{ ...fhRow, justifyContent: 'space-between' }}>
      <div style={{ fontSize: 13 }}>
        Asked <strong>{String(req.name || req.their_name || '')}</strong>… waiting for them to accept
      </div>
      <Button icon="x" disabled={busy} onClick={() => setConfirm(true)}>Take back</Button>
    </div>
    <DeleteConfirmDialog open={confirm} title="Take back this request?" icon="x" confirmLabel="Take back" busy={busy}
                         onCancel={() => setConfirm(false)} onConfirm={withdraw}>
      <div>{`${String(req.name || req.their_name || 'That house')} can’t accept it any more, and loses the way in it gave them. You can ask again with a new friend file.`}</div>
    </DeleteConfirmDialog>
    {req.code && (
      <div style={fhRow}>
        <FhCode code={req.code}/>
        <span style={fhMuted}>Optional: call them and check they see the same code.</span>
      </div>
    )}
    {req.trying && (
      <div style={fhMuted}>
        Still trying to reach them{req.last_error ? <>: {String(req.last_error)}</> : '.'}
      </div>
    )}
    {req.expires_at && <div style={fhMuted}>the request ends {fhDay(req.expires_at)}</div>}
  </div>
  );
};

const FhRequestsCard = ({ requests, fire, onChanged }) => {
  const all = (requests && requests.requests) || [];
  const incoming = all.filter((r) => r.direction === 'in' && r.state === 'waiting');
  const outgoing = all.filter((r) => r.direction === 'out' && (r.state === 'unsent' || r.state === 'sent'));
  return (
    <Card title="Requests" sub="Houses that asked to be friends, and the ones you asked.">
      <div style={{ padding: '0 16px 4px' }}>
        {!incoming.length && !outgoing.length && <div style={{ ...fhMuted, padding: '12px 0' }}>no requests</div>}
        {incoming.map((r) => <FhIncoming key={r.id} req={r} fire={fire} onChanged={onChanged}/>)}
        {outgoing.map((r) => <FhOutgoing key={r.id} req={r} fire={fire} onChanged={onChanged}/>)}
      </div>
    </Card>
  );
};

/* ---- messages: shared bits ----------------------------------------- */

// What a new message from a friend does, least intrusive first (owner
// decision: the least intrusive is the default).
const FH_ALERTS = [
  { id: 'inbox', label: 'Only in the inbox' },
  { id: 'sender', label: 'Say who it’s from in the rooms' },
  { id: 'read', label: 'Read new messages aloud in the rooms',
    note: 'Wake words are taken out, and long messages are cut short.' },
];
const FH_MESSAGE_MAX = 2000;
const FH_CONVERSATION_POLL_MS = 10000;
const FH_PAGE = 50;
const FH_CLOCK_SKEW_MS = 5 * 60 * 1000;

const fhWhen = (ms) => {
  if (!Number.isFinite(ms)) return '—';
  try {
    return new Date(ms).toLocaleString(undefined,
      { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch { return '—'; }
};
const fhIsoMs = (iso) => { const t = Date.parse(iso || ''); return Number.isFinite(t) ? t : NaN; };
// A file name of safe characters only.
const fhSafe = (s) => String(s || '').replace(/[^A-Za-z0-9 ._-]/g, '').replace(/\s+/g, ' ').trim().slice(0, 60);
const fhExportName = (name) => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `Friend Houses - ${fhSafe(name) || 'friend'} - ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}.json`;
};
const fhSaveJson = (data, filename) => {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
// A conversation is addressed by the other house's public key (64 hex),
// never by anything a message says.
const fhHouseOk = (h) => /^[0-9a-f]{64}$/.test(String(h || ''));

/* ---- friends -------------------------------------------------------- */

const FhAlertChoice = ({ friend, fire, onChanged }) => {
  const [busy, setBusy] = React.useState(false);
  const current = FH_ALERTS.some((a) => a.id === friend.alert) ? friend.alert : 'inbox';
  const choose = async (alert) => {
    if (alert === current) return;
    setBusy(true);
    try {
      await apiPut(`${FH_API}/friends/${encodeURIComponent(friend.id)}/alert`, { alert });
      fire('saved', { tone: 'ok' });
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  return (
    <fieldset style={{ border: 0, padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
      <legend style={{ ...fhMuted, padding: 0, marginBottom: 2 }}>when a message comes from them</legend>
      {FH_ALERTS.map((a) => (
        <label key={a.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, lineHeight: 1.4 }}>
          <input type="radio" name={`fh-alert-${friend.id}`} value={a.id} checked={current === a.id}
                 disabled={busy} onChange={() => choose(a.id)}/>
          <span>
            {a.label}
            {a.note && <span style={{ display: 'block', ...fhMuted }}>{a.note}</span>}
          </span>
        </label>
      ))}
      <span style={fhMuted}>Every choice keeps to each room’s quiet hours.</span>
    </fieldset>
  );
};

/* "5m ago", "3h ago", "2d ago": how long since a friend's house was heard. */
const fhAgo = (iso) => {
  const t = fhIsoMs(iso);
  if (!Number.isFinite(t)) return null;
  const min = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const h = Math.round(min / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

// Only whether the friend's HOUSE is online (owner decision): nothing
// about the people in it.
const FhPresence = ({ friend }) => {
  if (friend.online) return <Pill tone="ok">Online</Pill>;
  const ago = fhAgo(friend.last_seen_at);
  return <span style={fhMuted}>{ago ? `Last seen ${ago}` : 'Not reached yet'}</span>;
};

const FhBlockDialog = ({ friend, open, onCancel, onBlocked, fire }) => {
  const [keep, setKeep] = React.useState(true);
  const [move, setMove] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => { if (open) { setKeep(true); setMove(true); } }, [open]);
  const name = String(friend.name || 'this house');
  const saveCopy = async () => {
    try {
      const data = await apiGet(`${FH_API}/conversations/${friend.house}/export`);
      fhSaveJson(data, fhExportName(friend.name));
    } catch (e) { fhFail(fire, e); }
  };
  const block = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await apiPost(`${FH_API}/friends/${encodeURIComponent(friend.id)}/block`,
                              { keep_messages: keep, new_address: move });
      fire(r && r.new_address
        ? `${name} is blocked · this house is moving to a new private address`
        : `${name} is blocked`, { tone: 'ok' });
      onBlocked();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  const box = { display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, lineHeight: 1.45, color: 'var(--fg)' };
  return (
    <DeleteConfirmDialog open={open} title={`Block ${name}?`} icon="ban" confirmLabel={busy ? 'Blocking…' : 'Block'}
                         onCancel={busy ? undefined : onCancel} onConfirm={block}>
      <div>{`Block ${name}? They’re removed and can’t ask to be friends again unless you unblock them.`}</div>
      <label style={box}>
        <input type="checkbox" checked={keep} disabled={busy} onChange={(e) => setKeep(e.target.checked)}/>
        <span>Keep our messages with them</span>
      </label>
      {!keep && fhHouseOk(friend.house) && (
        <div style={{ paddingLeft: 24 }}>
          <Button icon="download" disabled={busy} onClick={saveCopy}>Save a copy first</Button>
        </div>
      )}
      <label style={box}>
        <input type="checkbox" checked={move} disabled={busy} onChange={(e) => setMove(e.target.checked)}/>
        <span>
          Move this house to a new private address (recommended): the blocked house can’t even reach the door.
          Your other friends are told the new address automatically; messages may take a few minutes to get
          through while it changes.
        </span>
      </label>
    </DeleteConfirmDialog>
  );
};

const FhFriend = ({ friend, fire, onChanged, onMessage, onShare }) => {
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState(String(friend.name || ''));
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
  const [blocking, setBlocking] = React.useState(false);
  const act = async (fn, said) => {
    setBusy(true);
    try { await fn(); if (said) fire(said, { tone: 'ok' }); onChanged(); return true; }
    catch (e) { fhFail(fire, e); return false; }
    finally { setBusy(false); }
  };
  const rename = async () => {
    const ok = await act(() => apiPatch(`${FH_API}/friends/${encodeURIComponent(friend.id)}`, { name: name.trim() }), 'renamed');
    if (ok) setEditing(false);
  };
  const verified = () => act(() => apiPost(`${FH_API}/friends/${encodeURIComponent(friend.id)}/verified`, {}), 'marked as verified');
  const remove = async () => {
    const ok = await act(() => apiDelete(`${FH_API}/friends/${encodeURIComponent(friend.id)}`), 'friend removed');
    if (ok) setConfirm(false);
  };
  return (
    <div style={fhItem}>
      <div style={fhRow}>
        {editing ? (
          <>
            <input value={name} maxLength={FH_NAME_MAX} disabled={busy} autoComplete="off" autoFocus
                   aria-label="their name" style={{ ...fhFieldStyle, flex: '1 1 160px' }}
                   onChange={(e) => setName(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) rename(); if (e.key === 'Escape') setEditing(false); }}/>
            <Button variant="primary" disabled={busy || !name.trim()} onClick={rename}>Save</Button>
            <Button variant="ghost" disabled={busy} onClick={() => { setEditing(false); setName(String(friend.name || '')); }}>Cancel</Button>
          </>
        ) : (
          <>
            <strong style={{ fontSize: 14, overflowWrap: 'anywhere' }}>{String(friend.name || '')}</strong>
            <FhPresence friend={friend}/>
            {!friend.verified && <Pill tone="warn">not verified</Pill>}
            <IconButton name="pencil" title="rename" aria-label="rename" onClick={() => setEditing(true)}/>
            {fhHouseOk(friend.house) && (
              <Button icon="message-square" onClick={() => onMessage(friend.house)}>Message</Button>
            )}
            {onShare && <Button icon="share-2" onClick={() => onShare(friend)}>Share a list</Button>}
          </>
        )}
      </div>
      {!friend.verified && friend.code && (
        <div style={fhRow}>
          <FhCode code={friend.code}/>
          <span style={fhMuted}>Optional: call them and check they see the same code.</span>
          <Button icon="check" disabled={busy} onClick={verified}>We compared the codes</Button>
        </div>
      )}
      <FhAlertChoice friend={friend} fire={fire} onChanged={onChanged}/>
      <div style={fhRow}>
        <span style={fhMuted}>fingerprint</span>
        <FhFingerprint value={friend.fingerprint} fire={fire}/>
      </div>
      <div style={fhRow}>
        <span style={fhMuted}>friends since {fhDay(friend.added_at)}</span>
        <Button icon="trash-2" disabled={busy} onClick={() => setConfirm(true)}>Remove</Button>
        <Button icon="ban" disabled={busy} onClick={() => setBlocking(true)}>Block</Button>
      </div>
      <DeleteConfirmDialog open={confirm} title={`Remove ${String(friend.name || 'this friend')}?`} confirmLabel="Remove"
                           busy={busy} onCancel={() => setConfirm(false)} onConfirm={remove}>
        <div>They won’t be able to reach this house any more. (Block also stops them asking again.)</div>
      </DeleteConfirmDialog>
      <FhBlockDialog friend={friend} open={blocking} fire={fire} onCancel={() => setBlocking(false)}
                     onBlocked={() => { setBlocking(false); onChanged(); }}/>
    </div>
  );
};

const FhFriendsCard = ({ friends, fire, onChanged, onMessage, onShare }) => {
  const list = (friends && friends.friends) || [];
  return (
    <Card title="Friends" sub="Houses this house is friends with.">
      <div style={{ padding: '0 16px 4px' }}>
        {!list.length && <div style={{ ...fhMuted, padding: '12px 0' }}>no friends yet</div>}
        {list.map((f) => <FhFriend key={f.id} friend={f} fire={fire} onChanged={onChanged} onMessage={onMessage} onShare={onShare}/>)}
      </div>
    </Card>
  );
};

/* ---- blocked houses ------------------------------------------------- */

const FhBlockedHouse = ({ house, fire, onChanged }) => {
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
  const name = String(house.name || 'this house');
  const unblock = async () => {
    setBusy(true);
    try {
      await apiDelete(`${FH_API}/blocked/${house.house}`);
      fire(`${name} is unblocked`, { tone: 'ok' });
      setConfirm(false);
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  return (
    <div style={fhItem}>
      <div style={fhRow}>
        <strong style={{ fontSize: 14, overflowWrap: 'anywhere' }}>{name}</strong>
        <span style={fhMuted}>blocked {fhDay(house.blocked_at)}</span>
      </div>
      <div style={fhRow}>
        <span style={fhMuted}>fingerprint</span>
        <FhFingerprint value={house.fingerprint} fire={fire}/>
      </div>
      <div><Button icon="undo-2" disabled={busy} onClick={() => setConfirm(true)}>Unblock</Button></div>
      <DeleteConfirmDialog open={confirm} title={`Unblock ${name}?`} icon="undo-2" confirmLabel="Unblock" busy={busy}
                           onCancel={() => setConfirm(false)} onConfirm={unblock}>
        <div>{`Unblock ${name}? They aren’t friends again; they can ask with a new friend file.`}</div>
      </DeleteConfirmDialog>
    </div>
  );
};

// Hidden when nobody is blocked.
const FhBlockedCard = ({ blocked, fire, onChanged }) => {
  const list = ((blocked && blocked.blocked) || []).filter((b) => fhHouseOk(b.house));
  if (!list.length) return null;
  return (
    <Card title="Blocked houses" sub="Houses that can’t ask to be friends with this house.">
      <div style={{ padding: '0 16px 4px' }}>
        {list.map((b) => <FhBlockedHouse key={b.house} house={b} fire={fire} onChanged={onChanged}/>)}
      </div>
    </Card>
  );
};

/* ---- messages ------------------------------------------------------- */
/* A message body is another house's words: plain text through React's
 * escaping, line breaks kept by white-space: pre-wrap. Nothing in it is
 * linked, fetched or previewed. */

// An outgoing message's state, in plain words.
const fhOutState = (m) => {
  if (m.state === 'sent') return { tone: 'ok', text: 'Delivered' };
  if (m.state === 'failed') return { tone: 'err', text: m.last_error ? `Not delivered: ${String(m.last_error)}` : 'Not delivered' };
  if (m.last_error) return { tone: 'warn', text: `Still trying: ${String(m.last_error)}` };
  return { tone: 'idle', text: 'Waiting to send' };
};

const FhMessage = ({ m }) => {
  const out = m.direction === 'out';
  const here = fhIsoMs(m.created_at);
  const theirs = Number(m.sent_at) * 1000;
  const skewed = !out && Number.isFinite(theirs) && Number.isFinite(here) && Math.abs(theirs - here) > FH_CLOCK_SKEW_MS;
  const st = out ? fhOutState(m) : null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: out ? 'flex-end' : 'flex-start', gap: 3 }}>
      <div className="fh-message-body"
           style={{ maxWidth: 'min(520px, 92%)', padding: '8px 11px', borderRadius: 'var(--r-md)', fontSize: 13,
                    lineHeight: 1.45, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
                    background: out ? 'var(--sunken)' : 'var(--card)', border: '1px solid var(--border)',
                    color: 'var(--fg)' }}>
        {String(m.body == null ? '' : m.body)}
      </div>
      <div style={{ ...fhMuted, fontSize: 11, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <span>{fhWhen(here)}</span>
        {skewed && <span>· sent {fhWhen(theirs)} (their clock)</span>}
        {st && <span style={{ display: 'inline-flex', gap: 5, alignItems: 'center' }}>· <StatusDot tone={st.tone}/>{st.text}</span>}
      </div>
    </div>
  );
};

const FhConversation = ({ conv, fire, onChanged, onClose }) => {
  const house = conv.house;
  const [msgs, setMsgs] = React.useState([]);     // oldest first, by id
  const [more, setMore] = React.useState(false);  // earlier ones to load
  const [loaded, setLoaded] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [text, setText] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
  const changed = React.useRef(onChanged);
  changed.current = onChanged;

  const merge = (rows) => setMsgs((cur) => {
    const byId = new Map(cur.map((m) => [m.id, m]));
    rows.forEach((m) => byId.set(m.id, m));
    return Array.from(byId.values()).sort((a, b) => a.id - b.id);
  });
  const markRead = async () => {
    try { await apiPost(`${FH_API}/conversations/${house}/read`, {}); changed.current(); } catch { /* the next poll tries again */ }
  };
  const latest = async (first) => {
    try {
      const r = await apiGet(`${FH_API}/conversations/${house}?limit=${FH_PAGE}`, { quiet: true });
      const rows = (r && r.messages) || [];
      merge(rows);
      if (first) setMore(rows.length >= FH_PAGE);
      setError(null);
      setLoaded(true);
      if (!first && rows.some((m) => m.direction === 'in' && m.state === 'unread')) markRead();
    } catch (e) { setError(e); setLoaded(true); }
  };
  // Opening it reads it, and marks it read.
  React.useEffect(() => {
    latest(true);
    markRead();
  }, [house]);
  useFhEvery(FH_CONVERSATION_POLL_MS, () => latest(false), true);

  const earlier = async () => {
    if (!msgs.length) return;
    try {
      const r = await apiGet(`${FH_API}/conversations/${house}?limit=${FH_PAGE}&before=${Number(msgs[0].id)}`, { quiet: true });
      const rows = (r && r.messages) || [];
      merge(rows);
      setMore(rows.length >= FH_PAGE);
    } catch (e) { fhFail(fire, e); }
  };
  const send = async () => {
    const body = text;
    if (!body.trim() || body.length > FH_MESSAGE_MAX || !conv.friend_id) return;
    setBusy(true);
    try {
      await apiPost(`${FH_API}/messages`, { friend_id: conv.friend_id, text: body });
      setText('');
      await latest(false);
      changed.current();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  const save = async () => {
    try {
      const data = await apiGet(`${FH_API}/conversations/${house}/export`);
      fhSaveJson(data, fhExportName(conv.name));
    } catch (e) { fhFail(fire, e); }
  };
  const remove = async () => {
    setBusy(true);
    try {
      await apiDelete(`${FH_API}/conversations/${house}`);
      fire('messages deleted', { tone: 'ok' });
      setConfirm(false);
      changed.current();
      onClose();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };

  const name = String(conv.name || 'this house');
  const former = !conv.friend_id;
  const over = text.length > FH_MESSAGE_MAX;
  return (
    <div style={{ ...fhStack, paddingTop: 4 }}>
      <div style={fhRow}>
        <IconButton name="chevron-left" title="all conversations" aria-label="all conversations" onClick={onClose}/>
        <strong style={{ fontSize: 14, overflowWrap: 'anywhere', flex: '1 1 auto' }}>
          {name}{former ? ' (no longer a friend)' : ''}
        </strong>
        {msgs.length > 0 && <Button icon="download" onClick={save}>Save a copy (with proof of what was sent)</Button>}
        {msgs.length > 0 && <Button icon="trash-2" disabled={busy} onClick={() => setConfirm(true)}>Delete</Button>}
      </div>
      {more && <div><Button variant="ghost" onClick={earlier}>Load earlier</Button></div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }} aria-live="polite">
        {!loaded && <div style={fhMuted}>loading…</div>}
        {loaded && error && !msgs.length && <div style={fhMuted}>{`couldn’t load: ${fhErr(error)}`}</div>}
        {loaded && !error && !msgs.length && <div style={fhMuted}>no messages yet</div>}
        {msgs.map((m) => <FhMessage key={m.id} m={m}/>)}
      </div>
      {!former && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <textarea className="cal-inp" rows={3} value={text} disabled={busy} maxLength={FH_MESSAGE_MAX}
                    aria-label={`message to ${name}`} placeholder={`Message ${name}…`} autoComplete="off"
                    style={{ width: '100%', boxSizing: 'border-box' }}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send(); }}/>
          <div style={fhRow}>
            <span style={{ ...fhMuted, color: over ? 'var(--warn-ink)' : 'var(--fg-muted)', flex: '1 1 auto' }}>
              {`${text.length} / ${FH_MESSAGE_MAX}`}
            </span>
            <Button variant="primary" icon="send" disabled={busy || !text.trim() || over} onClick={send}>
              {busy ? 'Sending…' : 'Send'}
            </Button>
          </div>
        </div>
      )}
      <DeleteConfirmDialog open={confirm} title="Delete these messages?" busy={busy}
                           onCancel={() => setConfirm(false)} onConfirm={remove}>
        <div>{`Delete every message with ${name}? This can’t be undone. Save a copy first if you might need it.`}</div>
      </DeleteConfirmDialog>
    </div>
  );
};

const FhInboxCard = ({ conversations, friends, open, setOpen, fire, onChanged }) => {
  const list = (conversations && conversations.conversations) || [];
  const friendList = (friends && friends.friends) || [];
  // The open conversation: from the inbox, else a friend with no messages yet.
  let conv = null;
  if (open) {
    conv = list.find((c) => c.house === open) || null;
    if (!conv) {
      const f = friendList.find((x) => x.house === open);
      if (f) conv = { house: f.house, name: f.name, friend_id: f.id, unread: 0, not_delivered: 0 };
    }
  }
  return (
    <Card title="Messages" sub="Messages with your friend houses. New ones arrive here.">
      <div style={{ padding: '4px 16px 12px' }}>
        {conv ? (
          <FhConversation key={conv.house} conv={conv} fire={fire} onChanged={onChanged} onClose={() => setOpen(null)}/>
        ) : (
          <>
            {!list.length && <div style={{ ...fhMuted, padding: '8px 0' }}>no messages yet · press Message on a friend to write one</div>}
            {list.filter((c) => fhHouseOk(c.house)).map((c) => (
              <button key={c.house} type="button" className="fh-conversation-row"
                      onClick={() => setOpen(c.house)}
                      style={{ display: 'flex', gap: 10, alignItems: 'center', width: '100%', textAlign: 'left',
                               padding: '10px 0', background: 'none', border: 0,
                               borderTop: '1px solid var(--border-soft)', color: 'var(--fg)', font: 'inherit',
                               fontSize: 13, cursor: 'pointer' }}>
                <span style={{ flex: '1 1 auto', minWidth: 0, overflowWrap: 'anywhere', fontWeight: c.unread ? 600 : 400 }}>
                  {String(c.name || '')}{c.friend_id ? '' : ' (no longer a friend)'}
                </span>
                {c.not_delivered > 0 && <Pill tone="err">{`${c.not_delivered} not delivered`}</Pill>}
                {c.unread > 0 && <Pill tone="live">{`${c.unread} unread`}</Pill>}
                <Icon name="chevron-right" size={14}/>
              </button>
            ))}
          </>
        )}
      </div>
    </Card>
  );
};

/* ---- shared lists (F4) ---------------------------------------------- */
/* A list shared with a friend house: changes either house makes show up in
 * both. Its title, the friend's name and the list's name are plain text;
 * a shared list is addressed only by its 32-hex id. */

const FH_SHARE_EXPLAIN = 'Changes either house makes show up in both, usually within a minute. Each house keeps its own copy and its own name for it.';
const fhSpaceOk = (id) => /^[0-9a-f]{32}$/.test(String(id || ''));
const fhFriendName = (s) => (s.friend ? String(s.friend) : 'a former friend');
const fhChanges = (n) => `${n} ${n === 1 ? 'change' : 'changes'} waiting to send`;

// Open a list on the Lists page (lists.jsx remembers which list is open).
const fhOpenList = (listId) => {
  try { if (typeof listsSaveSelected === 'function') listsSaveSelected(Number(listId)); } catch { /* a convenience */ }
};

const FhListOffer = ({ share, fire, onChanged }) => {
  const [naming, setNaming] = React.useState(false);
  const [name, setName] = React.useState(String(share.title || ''));
  const [problem, setProblem] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
  const accept = async () => {
    setBusy(true); setProblem(null);
    try {
      const body = naming && name.trim() ? { name: name.trim() } : {};
      const r = await apiPost(`${FH_API}/shared-lists/${share.id}/accept`, body);
      fire(`added to Lists as ${String((r && r.name) || share.title || 'a new list')}`, { tone: 'ok' });
      onChanged();
    } catch (e) {
      if (isAuthFailure(e)) return;
      // A name taken here: say so, and offer a name to try instead.
      if (e && e.status === 400) {
        setNaming(true);
        setProblem(fhErr(e));
      } else fhFail(fire, e);
    } finally { setBusy(false); }
  };
  const decline = async () => {
    setBusy(true);
    try {
      await apiPost(`${FH_API}/shared-lists/${share.id}/decline`, {});
      fire('list declined', { tone: 'ok' });
      setConfirm(false);
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  return (
    <div style={fhItem}>
      <div style={{ fontSize: 13, overflowWrap: 'anywhere' }}>
        <strong>{fhFriendName(share)}</strong> shared a list: <strong>“{String(share.title || '')}”</strong>
      </div>
      {naming ? (
        <div style={fhRow}>
          <label htmlFor={`fh-list-name-${share.id}`} style={{ fontSize: 13 }}>Call it here</label>
          <input id={`fh-list-name-${share.id}`} value={name} disabled={busy} autoComplete="off" maxLength={120}
                 style={{ ...fhFieldStyle, flex: '1 1 180px' }}
                 onChange={(e) => { setName(e.target.value); setProblem(null); }}
                 onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) accept(); }}/>
        </div>
      ) : (
        <div>
          <button type="button" className="home-link" style={{ fontSize: 12 }} onClick={() => setNaming(true)}>
            give it another name here
          </button>
        </div>
      )}
      {problem && <div role="alert" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{String(problem)}</div>}
      <div style={fhRow}>
        <Button variant="primary" icon="check" disabled={busy || (naming && !name.trim())} onClick={accept}>Accept</Button>
        <Button icon="x" disabled={busy} onClick={() => setConfirm(true)}>Decline</Button>
      </div>
      <DeleteConfirmDialog open={confirm} title="Decline this list?" icon="x" confirmLabel="Decline" busy={busy}
                           onCancel={() => setConfirm(false)} onConfirm={decline}>
        <div>{`“${String(share.title || 'This list')}” won’t be added here. ${fhFriendName(share)} isn’t told.`}</div>
      </DeleteConfirmDialog>
    </div>
  );
};

const FhSharedList = ({ share, fire, onChanged }) => {
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
  const listName = String(share.list_name || share.title || 'this list');
  const who = fhFriendName(share);
  const stop = async () => {
    setBusy(true);
    try {
      await apiDelete(`${FH_API}/shared-lists/${share.id}`);
      fire('stopped sharing — you both keep your own copy', { tone: 'ok' });
      setConfirm(false);
      onChanged();
    } catch (e) { fhFail(fire, e); } finally { setBusy(false); }
  };
  const waiting = Number(share.waiting_to_send) || 0;
  const offered = share.state === 'invited';   // ours, not accepted yet
  return (
    <div style={fhItem}>
      <div style={{ fontSize: 13, overflowWrap: 'anywhere' }}>
        <strong>{listName}</strong>
        {share.origin === 'us' ? ` — shared with ${who}` : ` — from ${who}`}
        {offered && <span style={fhMuted}> · waiting for them to accept</span>}
      </div>
      {share.origin === 'them' && share.title && String(share.title) !== listName && (
        <div style={fhMuted}>{`they call it “${String(share.title)}”`}</div>
      )}
      {waiting > 0 && <div style={fhMuted}>{fhChanges(waiting)}</div>}
      <div style={fhRow}>
        {Number.isInteger(share.list_id) && share.list_id > 0 && (
          <a href="#lists" className="home-link" style={{ fontSize: 12 }} onClick={() => fhOpenList(share.list_id)}>open in Lists</a>
        )}
        <Button icon="x" disabled={busy} onClick={() => setConfirm(true)}>Stop sharing</Button>
      </div>
      <DeleteConfirmDialog open={confirm} title="Stop sharing?" icon="x" confirmLabel="Stop sharing" busy={busy}
                           onCancel={() => setConfirm(false)} onConfirm={stop}>
        <div>{`Stop sharing ${listName} with ${who}? You both keep your own copy.`}</div>
      </DeleteConfirmDialog>
    </div>
  );
};

const FhShareList = ({ lists, friends, shares, shareFor, fire, onChanged }) => {
  const [listId, setListId] = React.useState('');
  const [friendId, setFriendId] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [problem, setProblem] = React.useState(null);
  // A friend row's "Share a list" picks that friend here.
  React.useEffect(() => { if (shareFor && shareFor.id) { setFriendId(shareFor.id); setProblem(null); } }, [shareFor]);
  const taken = new Set(shares.map((s) => s.list_id).filter((id) => id != null));
  const ours = (Array.isArray(lists) ? lists : []).filter((l) => l && Number.isInteger(l.id) && !taken.has(l.id));
  const friendList = (friends && friends.friends) || [];
  const share = async () => {
    setBusy(true); setProblem(null);
    try {
      await apiPost(`${FH_API}/shared-lists`, { list_id: Number(listId), friend_id: friendId });
      fire('list shared — it shows up for them once they accept', { tone: 'ok' });
      setListId('');
      onChanged();
    } catch (e) {
      if (!isAuthFailure(e)) setProblem(fhErr(e));
    } finally { setBusy(false); }
  };
  if (!friendList.length) return <div style={fhMuted}>Make a friend first to share a list with them.</div>;
  if (!ours.length) {
    return <div style={fhMuted}>Every list here is already shared, or there are none yet. <a href="#lists" className="home-link">Lists</a></div>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div className="label">share a list</div>
      <div style={fhRow}>
        <select aria-label="the list to share" value={listId} disabled={busy} style={fhFieldStyle}
                onChange={(e) => { setListId(e.target.value); setProblem(null); }}>
          <option value="">choose a list…</option>
          {ours.map((l) => <option key={l.id} value={String(l.id)}>{String(l.name || '')}</option>)}
        </select>
        <span style={fhMuted}>with</span>
        <select aria-label="the friend to share it with" value={friendId} disabled={busy} style={fhFieldStyle}
                onChange={(e) => { setFriendId(e.target.value); setProblem(null); }}>
          <option value="">choose a friend…</option>
          {friendList.map((f) => <option key={f.id} value={String(f.id)}>{String(f.name || '')}</option>)}
        </select>
        <Button variant="primary" icon="share-2" disabled={busy || !listId || !friendId} onClick={share}>
          {busy ? 'Sharing…' : 'Share'}
        </Button>
      </div>
      {problem && <div role="alert" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{String(problem)}</div>}
    </div>
  );
};

const FhSharedListsCard = ({ shared, lists, friends, shareFor, fire, onChanged }) => {
  const all = ((shared && shared.shared_lists) || []).filter((s) => fhSpaceOk(s.id));
  const offers = all.filter((s) => s.origin === 'them' && s.state === 'invited');
  const current = all.filter((s) => !(s.origin === 'them' && s.state === 'invited'));
  return (
    <Card title="Shared lists" sub="Lists this house shares with friend houses.">
      <div style={{ ...fhStack, padding: '12px 16px' }}>
        <div style={fhMuted}>{FH_SHARE_EXPLAIN}</div>
        {offers.length > 0 && (
          <div>
            <div className="label" style={{ marginBottom: 4 }}>offered to this house</div>
            {offers.map((s) => <FhListOffer key={s.id} share={s} fire={fire} onChanged={onChanged}/>)}
          </div>
        )}
        {current.length > 0 && (
          <div>
            <div className="label" style={{ marginBottom: 4 }}>shared now</div>
            {current.map((s) => <FhSharedList key={s.id} share={s} fire={fire} onChanged={onChanged}/>)}
          </div>
        )}
        {!all.length && <div style={fhMuted}>no shared lists yet</div>}
        <FhShareList lists={lists} friends={friends} shares={all} shareFor={shareFor} fire={fire} onChanged={onChanged}/>
      </div>
    </Card>
  );
};

/* ---- the section ---------------------------------------------------- */

const FriendHousesPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  // Every read is the security tier: the admin sign-in in this tab only.
  const live = adminReloadState().live;
  const opts = { quiet: true, refetchOnAuth: true };
  const st = useApiObject(live ? FH_API : null, opts);
  const cfg = useApiObject(live ? '/api/config/editable' : null, opts);
  const { data: pub } = useApiObject('/api/config', { quiet: true });
  const [switching, setSwitching] = React.useState(false);
  const [open, setOpen] = React.useState(null);   // the open conversation's house key
  const inboxRef = React.useRef(null);
  const listsRef = React.useRef(null);
  const [shareFor, setShareFor] = React.useState(null);   // {id} from a friend row

  const field = ((cfg.data && cfg.data.fields) || []).find((f) => f.name === 'friend_houses_enabled');
  const status = st.data;
  const enabled = field ? !!field.value : !!(status && status.status && status.status !== 'off');
  const internetNever = !!(pub && pub.internet_access === 'never');
  const state = status && status.status;
  // What can't work is hidden: no making or adding while the private
  // connection can't run here.
  const workable = enabled && !internetNever && state !== 'unavailable';

  const inv = useApiObject(live && enabled ? `${FH_API}/invites` : null, opts);
  const req = useApiObject(live && enabled ? `${FH_API}/requests` : null, opts);
  const fr = useApiObject(live && enabled ? `${FH_API}/friends` : null, opts);
  const convs = useApiObject(live && enabled ? `${FH_API}/conversations` : null, opts);
  const shared = useApiObject(live && enabled ? `${FH_API}/shared-lists` : null, opts);
  const lists = useApiObject(live && enabled ? '/api/lists' : null, opts);
  const blockedHouses = useApiObject(live && enabled ? `${FH_API}/blocked` : null, opts);
  const refreshAll = () => {
    st.refresh(); inv.refresh(); req.refresh(); fr.refresh(); convs.refresh(); shared.refresh(); lists.refresh();
    blockedHouses.refresh();
  };
  useFhEvery(FH_POLL_MS, refreshAll, live);

  const toInbox = () => {
    setTimeout(() => {
      try { if (inboxRef.current) inboxRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch { /* gone */ }
    }, 0);
  };
  const shareWith = (friend) => {
    setShareFor({ id: friend.id, at: Date.now() });
    setTimeout(() => {
      try { if (listsRef.current) listsRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch { /* gone */ }
    }, 0);
  };
  const openConversation = (house) => {
    if (!fhHouseOk(house)) return;
    setOpen(house);
    toInbox();
  };

  const turn = async (on) => {
    setSwitching(true);
    try {
      const res = await apiPatch('/api/config/editable', { changes: { friend_houses_enabled: on } });
      const why = res && res.rejected && res.rejected.friend_houses_enabled;
      if (why) fire(String(why), { tone: 'warn' });
      else fire(on ? 'Friend Houses is on' : 'Friend Houses is off', { tone: 'ok' });
      cfg.refresh();
      refreshAll();
    } catch (e) { fhFail(fire, e); } finally { setSwitching(false); }
  };

  // The unread total: the inbox's own count, else the status's.
  const unreadRaw = convs.data && convs.data.unread != null ? convs.data.unread : (status && status.unread);
  const unread = Number(unreadRaw) || 0;
  // The section's subtitle already says what Friend Houses is: the head
  // holds only the unread count.
  const head = unread > 0 ? (
    <div style={{ ...fhRow, justifyContent: 'flex-end' }}>
      <button type="button" className="home-link" aria-label={`${unread} unread messages`}
              onClick={() => { setOpen(null); toInbox(); }}>
        <Pill tone="live">{`${unread} unread`}</Pill>
      </button>
    </div>
  ) : null;

  if (!admin) {
    return (
      <Card title="Friend Houses" sub={FH_DESCRIPTION}>
        <FhNote>
          Admins only.{' '}
          <button type="button" className="home-link"
                  onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</button>
        </FhNote>
      </Card>
    );
  }
  if (!live) {
    return (
      <Card title="Friend Houses" sub={FH_DESCRIPTION}>
        <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)', display: 'flex',
                      alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ flex: 1 }}>This tab is view only, and Friend Houses is shown only to the admin sign-in. Sign in again to manage it.</span>
          <Button variant="primary" icon="key"
                  onClick={() => { try { Auth.openModal(); } catch { /* auth.js absent */ } }}>sign in again</Button>
        </div>
      </Card>
    );
  }
  if (st.error && !status) {
    return (
      <Card title="Friend Houses" sub={FH_DESCRIPTION}>
        <FhNote>{st.error.status === 501 ? 'Available once first-run admin setup is complete.' : `couldn’t load: ${fhErr(st.error)}`}</FhNote>
      </Card>
    );
  }
  if (!status) return <div style={fhMuted}>loading…</div>;

  if (!enabled) {
    return (
      <div className="friend-houses" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {toastNode}
        <FhOffCard internetNever={internetNever} detail={status.detail} busy={switching} onTurnOn={() => turn(true)} status={status}/>
      </div>
    );
  }

  return (
    <div className="friend-houses" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toastNode}
      {head}
      <FhHouseCard status={status} fire={fire} onSaved={st.refresh} busy={switching} onTurnOff={() => turn(false)}/>
      <div ref={inboxRef}>
        <FhInboxCard conversations={convs.data} friends={fr.data} open={open} setOpen={setOpen} fire={fire}
                     onChanged={() => { convs.refresh(); st.refresh(); }}/>
      </div>
      <div ref={listsRef}>
        <FhSharedListsCard shared={shared.data} lists={lists.data} friends={fr.data} shareFor={shareFor} fire={fire}
                           onChanged={() => { shared.refresh(); lists.refresh(); }}/>
      </div>
      {workable && <FhInvitesCard status={status} invites={inv.data} fire={fire} onChanged={() => { inv.refresh(); st.refresh(); }}/>}
      {workable && <FhAddCard fire={fire} onSent={() => { req.refresh(); st.refresh(); }}/>}
      <FhRequestsCard requests={req.data} fire={fire} onChanged={() => { req.refresh(); fr.refresh(); st.refresh(); }}/>
      <FhFriendsCard friends={fr.data} fire={fire}
                     onChanged={() => { fr.refresh(); st.refresh(); convs.refresh(); shared.refresh(); blockedHouses.refresh(); }}
                     onMessage={openConversation} onShare={shareWith}/>
      <FhBlockedCard blocked={blockedHouses.data} fire={fire} onChanged={() => { blockedHouses.refresh(); }}/>
    </div>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['friend-houses'] = { Panel: FriendHousesPanel };
