/* Settings → Household → Friend Houses (F2: friending).
 *
 * Serverless messaging and friend system between households. This
 * section turns it on or off (the friend_houses_enabled setting, off by
 * default), names this house, makes and shares friend files, adds a
 * friend from theirs, answers requests and keeps the friends list.
 * Messaging itself comes later: nothing here sends or shows a message.
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
 *   GET    /api/friend-houses/friends
 *   POST   /api/friend-houses/friends/{id}/verified
 *   PATCH  /api/friend-houses/friends/{id}          · rename
 *   DELETE /api/friend-houses/friends/{id}          · remove
 * Turning it on or off is the same PATCH /api/config/editable every other
 * switch uses.
 *
 * SECURITY (threat-model finding 1): every string that came from another
 * house (their name, a label, a fingerprint, an error, a detail) is
 * rendered as plain text through React's escaping. Nothing in this file
 * renders HTML or markdown, links text, or previews a link. A friend file
 * is a bearer secret: it lives only in this component's memory and in a
 * POST body, never in an address, browser storage or the console.
 *
 * Globals this file adds (one shared scope): FriendHousesPanel and the
 * Fh* / fh* / FH_* helpers. No object rest (`{ a, ...rest }`): see
 * printers.jsx.
 */

const FH_API = '/api/friend-houses';
const FH_DESCRIPTION = 'Serverless messaging and friend system between households';
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

/* ---- off ------------------------------------------------------------ */

const FhOffCard = ({ internetNever, detail, busy, onTurnOn }) => (
  <Card title="Friend Houses" sub={FH_DESCRIPTION}>
    <div style={{ ...fhStack, padding: '12px 16px' }}>
      <div style={{ fontSize: 13, lineHeight: 1.5 }}>
        Become friends with other Domovoi houses. Messages go house to house through the Tor network;
        no Coders Farm server is involved. Where using Tor is restricted, leave this off.
      </div>
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

  const blocked = preview && (preview.expired || preview.already_friends || preview.already_asked);
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

const FhOutgoing = ({ req }) => (
  <div style={fhItem}>
    <div style={{ fontSize: 13 }}>
      Asked <strong>{String(req.name || req.their_name || '')}</strong>… waiting for them to accept
    </div>
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

const FhRequestsCard = ({ requests, fire, onChanged }) => {
  const all = (requests && requests.requests) || [];
  const incoming = all.filter((r) => r.direction === 'in' && r.state === 'waiting');
  const outgoing = all.filter((r) => r.direction === 'out' && (r.state === 'unsent' || r.state === 'sent'));
  return (
    <Card title="Requests" sub="Houses that asked to be friends, and the ones you asked.">
      <div style={{ padding: '0 16px 4px' }}>
        {!incoming.length && !outgoing.length && <div style={{ ...fhMuted, padding: '12px 0' }}>no requests</div>}
        {incoming.map((r) => <FhIncoming key={r.id} req={r} fire={fire} onChanged={onChanged}/>)}
        {outgoing.map((r) => <FhOutgoing key={r.id} req={r}/>)}
      </div>
    </Card>
  );
};

/* ---- friends -------------------------------------------------------- */

const FhFriend = ({ friend, fire, onChanged }) => {
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState(String(friend.name || ''));
  const [busy, setBusy] = React.useState(false);
  const [confirm, setConfirm] = React.useState(false);
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
            {!friend.verified && <Pill tone="warn">not verified</Pill>}
            <IconButton name="pencil" title="rename" aria-label="rename" onClick={() => setEditing(true)}/>
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
      <div style={fhRow}>
        <span style={fhMuted}>fingerprint</span>
        <FhFingerprint value={friend.fingerprint} fire={fire}/>
      </div>
      <div style={fhRow}>
        <span style={fhMuted}>friends since {fhDay(friend.added_at)}</span>
        <Button icon="trash-2" disabled={busy} onClick={() => setConfirm(true)}>Remove</Button>
      </div>
      <DeleteConfirmDialog open={confirm} title={`Remove ${String(friend.name || 'this friend')}?`} confirmLabel="Remove"
                           busy={busy} onCancel={() => setConfirm(false)} onConfirm={remove}>
        <div>They won’t be able to reach this house any more.</div>
      </DeleteConfirmDialog>
    </div>
  );
};

const FhFriendsCard = ({ friends, fire, onChanged }) => {
  const list = (friends && friends.friends) || [];
  return (
    <Card title="Friends" sub="Houses this house is friends with.">
      <div style={{ padding: '0 16px 4px' }}>
        {!list.length && <div style={{ ...fhMuted, padding: '12px 0' }}>no friends yet</div>}
        {list.map((f) => <FhFriend key={f.id} friend={f} fire={fire} onChanged={onChanged}/>)}
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
  const refreshAll = () => { st.refresh(); inv.refresh(); req.refresh(); fr.refresh(); };
  useFhEvery(FH_POLL_MS, refreshAll, live);

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

  const head = (
    <div style={{ fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
      {FH_DESCRIPTION}. Messaging between friends comes in a later update.
    </div>
  );

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
        <FhOffCard internetNever={internetNever} detail={status.detail} busy={switching} onTurnOn={() => turn(true)}/>
      </div>
    );
  }

  return (
    <div className="friend-houses" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toastNode}
      {head}
      <FhHouseCard status={status} fire={fire} onSaved={st.refresh} busy={switching} onTurnOff={() => turn(false)}/>
      {workable && <FhInvitesCard status={status} invites={inv.data} fire={fire} onChanged={() => { inv.refresh(); st.refresh(); }}/>}
      {workable && <FhAddCard fire={fire} onSent={() => { req.refresh(); st.refresh(); }}/>}
      <FhRequestsCard requests={req.data} fire={fire} onChanged={() => { req.refresh(); fr.refresh(); st.refresh(); }}/>
      <FhFriendsCard friends={fr.data} fire={fire} onChanged={() => { fr.refresh(); st.refresh(); }}/>
    </div>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['friend-houses'] = { Panel: FriendHousesPanel };
