/* Settings page — the server's management surface, split into tabs.
 *
 * Consolidates what used to be three separate places:
 *   • Greetings    — the wake-word greeting bank (client_greetings)
 *   • Voices       — the TTS voice registry (voices)
 *   • Configuration — editable domovoi config (was the gear modal)
 *
 * Configuration is intentionally the LAST tab — it's the heaviest / most
 * dangerous surface, so the everyday tabs (greetings, voices) come first.
 * The config panel reuses ConfigField / _groupBy / _cfgInput from
 * components.jsx; the old modal shell is gone.
 */

/* ============================================================ */
/* Greetings tab                                                */
/* ============================================================ */
/*
 * Edits the short lines a satellite plays the instant the wake word fires.
 * Every mutation pings the server to re-render the clips and push
 * them live to connected satellites, so a change is audible within seconds
 * — no restart. `{name}` is replaced with the bot's name at render time.
 *
 * Data:
 *   GET    /api/greetings        · the bank
 *   POST   /api/greetings        · add
 *   PATCH  /api/greetings/{id}   · edit text / category / enabled
 *   DELETE /api/greetings/{id}   · remove
 */

const GREETING_CATEGORIES = ['generic', 'funny'];

const _greetingFieldStyle = {
  font: 'inherit', fontSize: 13, padding: '7px 10px', borderRadius: 'var(--r-sm)',
  border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--fg)',
};

const GreetingRow = ({ g, onSave, onToggle, onDelete }) => {
  const [editing, setEditing] = React.useState(false);
  const [text, setText] = React.useState(g.text);
  const [category, setCategory] = React.useState(g.category);

  const save = () => {
    const t = text.trim();
    if (!t) return;
    onSave(g, { text: t, category });
    setEditing(false);
  };

  if (editing) {
    return (
      <div style={{ display: 'flex', gap: 8, padding: '8px 14px', alignItems: 'center',
                    borderBottom: '1px solid var(--border-soft)' }}>
        <input value={text} onChange={(e) => setText(e.target.value)} autoFocus
               style={{ ..._greetingFieldStyle, flex: 1 }}/>
        <select value={category} onChange={(e) => setCategory(e.target.value)} style={_greetingFieldStyle}>
          {GREETING_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <Button variant="primary" onClick={save}>Save</Button>
        <Button variant="ghost"
                onClick={() => { setEditing(false); setText(g.text); setCategory(g.category); }}>
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
                  borderBottom: '1px solid var(--border-soft)', opacity: g.enabled ? 1 : 0.45 }}>
      <div style={{ flex: 1, fontSize: 13 }}>{g.text}</div>
      <Pill tone={g.category === 'funny' ? 'live' : 'idle'}>{g.category}</Pill>
      <Button variant="ghost" onClick={() => onToggle(g)}>{g.enabled ? 'on' : 'off'}</Button>
      <IconButton name="pencil" title="edit" onClick={() => setEditing(true)}/>
      <IconButton name="trash-2" title="delete" onClick={() => onDelete(g)}/>
    </div>
  );
};

const AddGreeting = ({ onAdd }) => {
  const [text, setText] = React.useState('');
  const [category, setCategory] = React.useState('generic');
  const submit = (e) => {
    e?.preventDefault?.();
    const t = text.trim();
    if (!t) return;
    onAdd(t, category);
    setText('');
  };
  return (
    <form onSubmit={submit}
          style={{ display: 'flex', gap: 8, padding: '10px 14px', alignItems: 'center', flexWrap: 'wrap' }}>
      <input value={text} onChange={(e) => setText(e.target.value)}
             placeholder="New greeting…  (use {name} for the bot's name)"
             style={{ ..._greetingFieldStyle, flex: 1, minWidth: 220 }}/>
      <select value={category} onChange={(e) => setCategory(e.target.value)} style={_greetingFieldStyle}>
        {GREETING_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <Button variant="primary" icon="plus" type="submit">Add</Button>
    </form>
  );
};

const GreetingsPanel = () => {
  const { items, loading, refresh } = useApiList('/api/greetings');
  const [fire, node] = useToast();

  const guard = async (fn, okMsg) => {
    try {
      await fn();
      if (okMsg) fire(okMsg);
      await refresh();
    } catch (e) {
      fire(`failed: ${e.message || e}`);
    }
  };

  const add = (text, category) =>
    guard(() => apiPost('/api/greetings', { text, category }), 'greeting added');
  const save = (g, patch) =>
    guard(() => apiPatch(`/api/greetings/${g.id}`, patch), 'greeting updated');
  const toggle = (g) =>
    guard(() => apiPatch(`/api/greetings/${g.id}`, { enabled: !g.enabled }));
  const remove = (g) =>
    guard(() => apiDelete(`/api/greetings/${g.id}`), 'greeting removed');

  const generic = items.filter((g) => g.category === 'generic');
  const funny = items.filter((g) => g.category === 'funny');

  return (
    <React.Fragment>
      <Card title="Add a greeting"
            sub="Changes re-render and reach connected satellites within seconds. Use {name} for the bot's name.">
        <AddGreeting onAdd={add}/>
      </Card>

      {loading ? null : items.length === 0 ? (
        <Empty title="No greetings yet" sub="Add one above to get started."/>
      ) : (
        <React.Fragment>
          <Card title={`Generic (${generic.length})`} sub="The everyday ones — picked most often.">
            {generic.map((g) => (
              <GreetingRow key={g.id} g={g} onSave={save} onToggle={toggle} onDelete={remove}/>
            ))}
          </Card>
          <Card title={`Funny (${funny.length})`} sub="Sprinkled in occasionally.">
            {funny.map((g) => (
              <GreetingRow key={g.id} g={g} onSave={save} onToggle={toggle} onDelete={remove}/>
            ))}
          </Card>
        </React.Fragment>
      )}
      {node}
    </React.Fragment>
  );
};

/* ============================================================ */
/* Voices tab                                                   */
/* ============================================================ */
/*
 * Each satellite speaks in one registered voice; the server renders
 * that room's responses + greeting clips in it. Manage the registry here:
 * register a cloud (Edge) voice by id, upload a local Piper model
 * (.onnx + .onnx.json), set the default, rename, delete. Every mutation
 * pings the server to re-render clips and push them to satellites.
 *
 * Data:
 *   GET    /api/voices            · the registry
 *   POST   /api/voices/edge       · register an Edge voice {name, voice_id, set_default}
 *   POST   /api/voices/piper      · upload a Piper model (multipart)
 *   PATCH  /api/voices/{id}       · rename / set default
 *   DELETE /api/voices/{id}       · remove (refuses the default)
 */

const _voiceFieldStyle = {
  font: 'inherit', fontSize: 13, padding: '7px 10px', borderRadius: 'var(--r-sm)',
  border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--fg)',
};

const VoiceRow = ({ v, sampling, onPlay, onRename, onSetDefault, onDelete }) => {
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState(v.name);

  const save = () => {
    const t = name.trim();
    if (!t || t === v.name) { setEditing(false); setName(v.name); return; }
    onRename(v, t);
    setEditing(false);
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
                  borderBottom: '1px solid var(--border-soft)' }}>
      <IconButton name={sampling ? 'loader' : 'play'}
                  title={sampling ? 'Synthesizing…' : 'Play a sample'}
                  disabled={sampling} onClick={() => onPlay(v)}/>
      {editing ? (
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
               onKeyDown={(e) => e.key === 'Enter' && save()}
               style={{ ..._voiceFieldStyle, flex: 1 }}/>
      ) : (
        <div style={{ flex: 1, fontSize: 14, fontWeight: 500 }}>{v.name}</div>
      )}
      <Pill tone={v.engine === 'piper' ? 'idle' : 'live'}>
        {v.engine === 'piper' ? 'local' : 'cloud'}
      </Pill>
      <span style={{ fontSize: 12, color: 'var(--fg-muted)', maxWidth: 200,
                     overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {v.model_ref}
      </span>
      {v.is_default
        ? <Pill tone="live">default</Pill>
        : <Button variant="ghost" onClick={() => onSetDefault(v)}>make default</Button>}
      {editing
        ? <Button variant="primary" onClick={save}>Save</Button>
        : <IconButton name="pencil" title="rename" onClick={() => setEditing(true)}/>}
      {!v.is_default && <IconButton name="trash-2" title="delete" onClick={() => onDelete(v)}/>}
    </div>
  );
};

const RegisterEdge = ({ onAdd }) => {
  const [name, setName] = React.useState('');
  const [voiceId, setVoiceId] = React.useState('');
  const submit = (e) => {
    e?.preventDefault?.();
    const n = name.trim(); const id = voiceId.trim();
    if (!n || !id) return;
    onAdd(n, id);
    setName(''); setVoiceId('');
  };
  return (
    <form onSubmit={submit}
          style={{ display: 'flex', gap: 8, padding: '10px 14px', alignItems: 'center', flexWrap: 'wrap' }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Aria)"
             style={{ ..._voiceFieldStyle, width: 160 }}/>
      <input value={voiceId} onChange={(e) => setVoiceId(e.target.value)}
             placeholder="Edge voice id (e.g. en-US-AriaNeural)"
             style={{ ..._voiceFieldStyle, flex: 1, minWidth: 220 }}/>
      <Button variant="primary" icon="cloud" type="submit">Register</Button>
    </form>
  );
};

const UploadPiper = ({ onUpload }) => {
  const [name, setName] = React.useState('');
  const onnxRef = React.useRef(null);
  const jsonRef = React.useRef(null);
  const [busy, setBusy] = React.useState(false);

  const submit = async (e) => {
    e?.preventDefault?.();
    const n = name.trim();
    const onnx = onnxRef.current?.files?.[0];
    const json = jsonRef.current?.files?.[0];
    if (!n || !onnx || !json) return;
    setBusy(true);
    try {
      await onUpload(n, onnx, json);
      setName('');
      if (onnxRef.current) onnxRef.current.value = '';
      if (jsonRef.current) jsonRef.current.value = '';
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}
          style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 14px' }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Ryan)"
             style={{ ..._voiceFieldStyle, width: 200 }}/>
      <label style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
        Model (.onnx)
        <input ref={onnxRef} type="file" accept=".onnx" style={{ display: 'block', marginTop: 4 }}/>
      </label>
      <label style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
        Config (.onnx.json)
        <input ref={jsonRef} type="file" accept=".json" style={{ display: 'block', marginTop: 4 }}/>
      </label>
      <div>
        <Button variant="primary" icon="upload" type="submit" disabled={busy}>
          {busy ? 'Uploading…' : 'Upload voice'}
        </Button>
      </div>
    </form>
  );
};

const VoicesPanel = () => {
  const { items, loading, refresh } = useApiList('/api/voices');
  const [fire, node] = useToast();
  const [samplingId, setSamplingId] = React.useState(null);
  const audioRef = React.useRef(null);

  // Fetch a freshly-synthesized sample (intro + fun fact) and play it.
  // The button stays "busy" through synthesis + playback, cleared on end.
  const playSample = async (v) => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    setSamplingId(v.id);
    try {
      const r = await fetch(`/api/voices/${v.id}/sample`);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const said = r.headers.get('X-Sample-Text');
      if (said) fire(`🔊 ${decodeURIComponent(said)}`);
      const url = URL.createObjectURL(await r.blob());
      const audio = new Audio(url);
      audioRef.current = audio;
      const done = () => { setSamplingId(null); URL.revokeObjectURL(url); };
      audio.onended = done;
      audio.onerror = () => { fire('sample playback failed'); done(); };
      await audio.play();
    } catch (e) {
      fire(`sample failed: ${e.message || e}`);
      setSamplingId(null);
    }
  };

  const guard = async (fn, okMsg) => {
    try {
      await fn();
      if (okMsg) fire(okMsg);
      await refresh();
    } catch (e) {
      fire(`failed: ${e.message || e}`);
    }
  };

  const addEdge = (name, voiceId) =>
    guard(() => apiPost('/api/voices/edge', { name, voice_id: voiceId }), 'voice registered');

  const uploadPiper = (name, onnx, json) => {
    const fd = new FormData();
    fd.append('name', name);
    fd.append('onnx', onnx);
    fd.append('config', json);
    return guard(() => apiUpload('/api/voices/piper', fd), 'voice uploaded');
  };

  const rename = (v, name) =>
    guard(() => apiPatch(`/api/voices/${v.id}`, { name }), 'voice renamed');
  const setDefault = (v) =>
    guard(() => apiPatch(`/api/voices/${v.id}`, { set_default: true }), `${v.name} is now the default`);
  const remove = (v) =>
    guard(() => apiDelete(`/api/voices/${v.id}`), 'voice removed');

  return (
    <React.Fragment>
      <Card title="Add a cloud voice"
            sub="Register a Microsoft Edge neural voice by its id. Needs network to speak.">
        <RegisterEdge onAdd={addEdge}/>
      </Card>

      <Card title="Upload a local voice"
            sub="A Piper model (.onnx) and its config (.onnx.json). Fully offline once uploaded.">
        <UploadPiper onUpload={uploadPiper}/>
      </Card>

      {loading ? null : items.length === 0 ? (
        <Empty title="No voices yet" sub="Add a cloud voice or upload a Piper model above."/>
      ) : (
        <Card title={`Registered (${items.length})`}
              sub="The default is used by any satellite that hasn't picked its own.">
          {items.map((v) => (
            <VoiceRow key={v.id} v={v} sampling={samplingId === v.id} onPlay={playSample}
                      onRename={rename} onSetDefault={setDefault} onDelete={remove}/>
          ))}
        </Card>
      )}
      {node}
    </React.Fragment>
  );
};

/* ============================================================ */
/* Configuration tab                                            */
/* ============================================================ */
/*
 * Editable domovoi config — formerly the gear modal in the topbar.
 * Reuses ConfigField / _groupBy from components.jsx. Common settings show
 * up front; "Advanced" stays collapsed behind a warning because wrong
 * values (DB URL, ports, STT device) can stop the server booting.
 *
 * Above the editor sits a Version section: the web build, the server's
 * git SHA (with a "-dirty" suffix when its working tree has uncommitted
 * changes), and an update check. The panel offers exactly ONE action at a
 * time, in the order an operator needs them: "Restart to apply changes"
 * when code is on disk but not loaded, else "Pull the latest" when behind,
 * else "Check for updates". The restart is replaced by the manual command
 * on a host without the sudoers grant (see restart_capable).
 *
 * Data:
 *   GET   /api/config              · web build (web_version)
 *   GET   /api/config/version      · domovoi git SHA → { sha }
 *   POST  /api/config/version/check· git fetch + count → { behind, ahead, upstream, error }
 *   POST  /api/config/version/pull · git pull --ff-only → { pulled, new_sha, error }
 *   POST  /api/config/version/restart · bounce core+web → { ok, units, error }
 *   GET   /api/config/editable     · the field set + current values
 *   PATCH /api/config/editable     · { changes } → { applied, rejected, restart_required }
 */

/* ---- Admin account ----------------------------------------------- */
/*
 * First-run setup + sign-in/out surface for the admin tier. The same
 * modal pops automatically whenever an admin-gated call returns
 * 401/403; this card is the discoverable path — fresh installs land
 * here looking for where the setup code goes.
 */
const AdminSection = () => {
  const [, force] = React.useReducer((x) => x + 1, 0);
  React.useEffect(() => {
    const un = Auth.subscribe(force);
    Auth.refreshStatus();
    return un;
  }, []);
  const st = Auth.status;
  const needsSetup = st && st.setup_complete === false;
  const signedIn = Auth.isLoggedIn() || !!(st && st.authenticated);
  return (
    <Card title="Admin"
          sub="Gates settings edits, plugin management, satellite upgrades, and file writes. Everyday playback and browsing never need it.">
      <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center',
                    gap: 12, flexWrap: 'wrap' }}>
        {st === null
          ? <Pill tone="idle">checking…</Pill>
          : needsSetup
            ? <Pill tone="warn">not set up yet</Pill>
            : signedIn
              ? <Pill tone="ok">signed in</Pill>
              : <Pill tone="idle">signed out</Pill>}
        {needsSetup && (
          <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            Enter the setup code from the Domovoi server console (also in{' '}
            <code>~/.domovoi/setup-code.txt</code>) and choose an admin password.
          </span>
        )}
        <span style={{ flex: 1 }}/>
        {needsSetup
          ? <Button variant="primary" onClick={() => Auth.openModal()}>set up admin</Button>
          : signedIn
            ? <Button onClick={() => Auth.logout()}>sign out</Button>
            : <Button variant="primary" onClick={() => Auth.openModal()}>sign in</Button>}
      </div>
    </Card>
  );
};

// "running 2h 14m" — coarse on purpose: this answers "did it restart when I
// thought it did?", not "how long exactly".
const fmtUptime = (sec) => {
  if (sec == null || !isFinite(sec) || sec < 0) return '';
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
};

const VersionSection = () => {
  const { data: cfg } = useApiObject('/api/config');
  const { data: core, refresh: refreshCore } = useApiObject('/api/config/version');
  const [fire, node] = useToast();
  const [checking, setChecking] = React.useState(false);
  const [pulling, setPulling] = React.useState(false);
  const [restarting, setRestarting] = React.useState(false);
  const [status, setStatus] = React.useState(null);   // result of /version/check

  const check = async () => {
    setChecking(true); setStatus(null);
    try {
      const res = await apiPost('/api/config/version/check', {});
      setStatus(res);
      if (res && res.error) {
        fire(`update check failed: ${res.error}`);
      } else if (!res || !res.upstream) {
        fire('no upstream configured — can’t check');
      } else if (res.behind > 0) {
        fire(`${res.behind} commit${res.behind === 1 ? '' : 's'} behind`);
      } else {
        fire('up to date');
      }
    } catch (e) {
      fire(`update check failed: ${e.message || e}`);
    } finally {
      setChecking(false);
    }
  };

  const pull = async () => {
    if (!window.confirm(
      'Pull the latest domovoi code (git pull --ff-only)?\n\n' +
      'This updates the files on the Domovoi host but does not load them — ' +
      'this panel will then offer the restart that does. Satellites can be ' +
      'upgraded individually afterwards.'
    )) return;
    setPulling(true);
    try {
      const res = await apiPost('/api/config/version/pull', {});
      if (res && res.pulled) {
        fire(`pulled${res.new_sha ? ` — now ${res.new_sha}` : ''}. Restart to apply.`);
        setStatus(null);
        refreshCore();
      } else {
        fire(`pull failed: ${(res && res.error) || 'unknown'}`);
      }
    } catch (e) {
      fire(`pull failed: ${e.message || e}`);
    } finally {
      setPulling(false);
    }
  };

  // After the bounce the server is briefly gone; a failed poll is the
  // expected middle of a successful restart, not an error to report.
  const waitForServer = async () => {
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const v = await apiGet('/api/config/version');
        if (v && !v.restart_required) {
          refreshCore();
          setStatus(null);
          fire(`restarted — now running ${v.running_sha || v.sha || 'new code'}`);
          return true;
        }
      } catch (e) { /* still down — keep waiting */ }
    }
    fire('restart is taking longer than expected — check the service by hand');
    refreshCore();
    return false;
  };

  const restart = async () => {
    if (!window.confirm(
      'Restart the Domovoi services to load the pulled code?\n\n' +
      'This bounces domovoi-core and domovoi-web. Voice is unavailable for ' +
      'a few seconds and connected satellites reconnect on their own.'
    )) return;
    setRestarting(true);
    try {
      const res = await apiPost('/api/config/version/restart', {});
      if (res && res.ok) {
        fire('restarting…');
        await waitForServer();
      } else {
        fire(`restart failed: ${(res && res.error) || 'unknown'}`);
      }
    } catch (e) {
      // The response should beat the bounce, but if the connection dropped
      // first the restart probably still fired — verify instead of crying.
      fire('restarting…');
      await waitForServer();
    } finally {
      setRestarting(false);
    }
  };

  const webVer = cfg && cfg.web_version;
  // `sha` is the RUNNING code (captured at the core's boot), not whatever is
  // checked out right now — those diverge after a pull without a restart,
  // which is exactly when someone looks at this panel.
  const coreSha = core && core.sha;
  const checkoutSha = core && core.checkout_sha;
  const restartPending = !!(core && core.restart_required);
  const behind = status && status.upstream ? status.behind : null;
  const restartCapable = !!(core && core.restart_capable);
  const restartHint = core && core.restart_hint;
  // One action at a time, in the order the operator actually needs them:
  // code already on disk beats fetching more of it, and "check" is only
  // honest when we have no reason to think anything is pending.
  const mode = restartPending ? 'restart' : ((behind || 0) > 0 ? 'pull' : 'check');

  return (
    <Card title="Version"
          sub="Build identifiers for the web dashboard and the Domovoi server, plus a git update check.">
      <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '140px 1fr',
                    rowGap: 8, fontSize: 13, alignItems: 'center' }}>
        <div className="label">web build</div>
        <div className="mono" style={{ color: webVer ? 'var(--fg)' : 'var(--fg-faint)' }}>{webVer || '—'}</div>
        <div className="label">domovoi</div>
        <div className="mono" style={{ color: coreSha ? 'var(--fg)' : 'var(--fg-faint)', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ userSelect: 'all' }}>{coreSha || '—'}</span>
          {coreSha && coreSha.endsWith('-dirty') &&
            <Pill tone="warn">uncommitted changes</Pill>}
          {core && core.uptime_sec != null && (
            <span style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
              running {fmtUptime(core.uptime_sec)}
            </span>
          )}
        </div>
        {restartPending && (
          <React.Fragment>
            <div className="label">checked out</div>
            <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ userSelect: 'all' }}>{checkoutSha}</span>
              <Pill tone="warn">restart to load</Pill>
            </div>
          </React.Fragment>
        )}
      </div>
      {restartPending && (
        <div style={{ padding: '0 16px 12px', fontSize: 12, color: 'var(--warn)' }}>
          New code is on disk but this process is still running the old
          modules{restartCapable ? ' — restart below to load it.'
                                 : ' — restart the Domovoi server for it to take effect.'}
        </div>
      )}
      <div style={{ padding: '0 16px 14px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {mode === 'restart' && restartCapable && (
          <Button variant="primary" icon="refresh-cw" onClick={restart} disabled={restarting}>
            {restarting ? 'Restarting…' : 'Restart to apply changes'}
          </Button>
        )}
        {mode === 'pull' && (
          <Button variant="primary" icon="download" onClick={pull} disabled={pulling}>
            {pulling ? 'Pulling…' : 'Pull the latest'}
          </Button>
        )}
        {mode === 'check' && (
          <Button variant="secondary" icon="refresh-cw" onClick={check} disabled={checking}>
            {checking ? 'Checking…' : 'Check for updates'}
          </Button>
        )}
        {mode !== 'restart' && behind != null && (
          <span style={{ fontSize: 12, color: behind > 0 ? 'var(--warn)' : 'var(--ok)' }}>
            {behind > 0
              ? `${behind} commit${behind === 1 ? '' : 's'} behind`
              : 'up to date'}
          </span>
        )}
      </div>
      {mode === 'pull' && (
        <div className="mono" style={{ padding: '0 16px 14px', fontSize: 11, color: 'var(--fg-faint)' }}>
          Pull updates the host files only — this panel will then offer the restart that loads them.
        </div>
      )}
      {mode === 'restart' && !restartCapable && (
        <div className="mono" style={{ padding: '0 16px 14px', fontSize: 11, color: 'var(--fg-faint)' }}>
          {restartHint || 'This host can’t restart itself.'} Run by hand:
          <div style={{ userSelect: 'all', color: 'var(--fg-muted)', marginTop: 4 }}>
            sudo systemctl restart domovoi-core domovoi-web
          </div>
        </div>
      )}
      {node}
    </Card>
  );
};

const ConfigPanel = () => {
  const { data, loading, refresh } = useApiObject('/api/config/editable');
  const fields = (data && data.fields) || [];
  const [edits, setEdits] = React.useState({});
  const [saving, setSaving] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [advOpen, setAdvOpen] = React.useState(false);

  const setEdit = (name, v) => setEdits(prev => ({ ...prev, [name]: v }));
  const valueOf = (f) => (f.name in edits ? edits[f.name] : f.value);
  const dirtyCount = Object.keys(edits).length;
  const common = fields.filter(f => f.section !== 'advanced');
  const advanced = fields.filter(f => f.section === 'advanced');

  const save = async () => {
    if (dirtyCount === 0) return;
    setSaving(true); setResult(null);
    try {
      const res = await apiPatch('/api/config/editable', { changes: edits });
      setResult(res);
      const rejected = (res && res.rejected) || {};
      setEdits(prev => {                       // keep only still-rejected edits
        const next = {};
        Object.keys(prev).forEach(k => { if (k in rejected) next[k] = prev[k]; });
        return next;
      });
      refresh();
    } catch (e) {
      setResult({ error: e.message });
    } finally {
      setSaving(false);
    }
  };

  const renderGroups = (list) => {
    const grouped = _groupBy(list);
    return Object.keys(grouped).map(group => (
      <div key={group} style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em',
                      color: 'var(--fg-muted)', fontWeight: 600, marginBottom: 2 }}>{group}</div>
        {grouped[group].map(f => <ConfigField key={f.name} f={f} value={valueOf(f)} onChange={v => setEdit(f.name, v)}/>)}
      </div>
    ));
  };

  const rejectedCount = result && result.rejected ? Object.keys(result.rejected).length : 0;

  return (
    <React.Fragment>
    <AdminSection/>
    <VersionSection/>
    <Card title="Domovoi configuration"
          sub="Live-editable settings. Some changes apply instantly; those marked “restart” need a Domovoi server restart.">
      <div style={{ padding: '4px 14px 14px' }}>
        {loading && fields.length === 0
          ? <div style={{ padding: 30, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading settings…</div>
          : <>
              {renderGroups(common)}
              {advanced.length > 0 && (
                <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                  <button onClick={() => setAdvOpen(o => !o)}
                          style={{ font: 'inherit', fontSize: 13, fontWeight: 600, background: 'transparent', border: 'none',
                                   cursor: 'pointer', color: 'var(--fg)', display: 'flex', alignItems: 'center', gap: 6, padding: 0 }}>
                    <Icon name={advOpen ? 'chevron-down' : 'chevron-right'} size={14}/>
                    Advanced
                  </button>
                  {advOpen && <>
                    <div style={{ margin: '10px 0', padding: '10px 12px', borderRadius: 'var(--r-sm)',
                                  background: 'oklch(0.8 0.16 60 / 0.12)', border: '1px solid var(--warn)',
                                  fontSize: 12, color: 'var(--fg)', lineHeight: 1.5 }}>
                      <strong>⚠ These can break the Domovoi server.</strong> Wrong values — database URL, ports,
                      paths, the STT device — can stop it from starting or reaching its services, and may need
                      you to edit <code>domovoi/.env</code> by hand to recover. Change only if you know what
                      you're doing.
                    </div>
                    {renderGroups(advanced)}
                  </>}
                </div>
              )}
            </>}

        <div style={{ marginTop: 8, borderTop: '1px solid var(--border-soft)', paddingTop: 12 }}>
          {result && result.error &&
            <div style={{ fontSize: 12, color: 'var(--err)', marginBottom: 8 }}>save failed: {result.error}</div>}
          {rejectedCount > 0 &&
            <div style={{ fontSize: 12, color: 'var(--err)', marginBottom: 8 }}>
              rejected: {Object.entries(result.rejected).map(([k, v]) => `${k} (${v})`).join('; ')}
            </div>}
          {result && result.restart_required && result.restart_required.length > 0 &&
            <div style={{ fontSize: 12, color: 'var(--warn)', marginBottom: 8 }}>
              saved — restart the Domovoi server to apply: {result.restart_required.join(', ')}
            </div>}
          {result && !result.error && rejectedCount === 0 && (!result.restart_required || !result.restart_required.length) &&
           result.applied && result.applied.length > 0 &&
            <div style={{ fontSize: 12, color: 'var(--ok)', marginBottom: 8 }}>saved ✓</div>}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
              {dirtyCount > 0 ? `${dirtyCount} unsaved` : 'no changes'}
            </span>
            <div style={{ marginLeft: 'auto' }}>
              <Button variant="primary" icon="check" onClick={save} disabled={saving || dirtyCount === 0}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Card>
    </React.Fragment>
  );
};

/* ============================================================ */
/* Wake Words tab                                               */
/* ============================================================ */
/*
 * Train + manage custom wake words. The lifecycle:
 *   1. Create a wake word (name + the phrase to say) — starts in `recording`.
 *   2. Record positive clips ON a satellite. The server tells the chosen Pi
 *      to suspend its wake loop and stream short clips back; each clip bumps
 *      the live count (pushed over the state bus → `wake_words.changed`).
 *   3. Once enough clips are banked, Train builds an openWakeWord .onnx on
 *      Domovoi (status `training` → `ready` / `failed`). Training is OFF by
 *      default and Linux-only — see scripts/wake_word/README.md.
 *   4. Push the ready model to a room (set_default / per-room control frame),
 *      exactly like Voices — the satellite picks it up via a sidecar override.
 *
 * Clips MUST be recorded on the SAME mic board the satellite uses at runtime:
 * the XVF3800's on-chip beamforming/AGC reshapes the signal, so a model
 * trained from HAT-recorded clips can detect poorly when pushed to an XVF
 * array (and vice versa).
 *
 * Data:
 *   GET    /api/wake-words                  · the registry (live clip-count/status)
 *   POST   /api/wake-words                  · create {name, phrase, threshold?, source_room_id?}
 *   POST   /api/wake-words/{id}/record/start· tell a room to start streaming clips {room_id}
 *   POST   /api/wake-words/{id}/record/stop · stop streaming {room_id}
 *   POST   /api/wake-words/{id}/train       · mark for training (needs enough clips)
 *   GET    /api/wake-words/{id}/clips              · per-clip quality/trim/selection (WakeClipList)
 *   GET    /api/wake-words/{id}/clips/{name}/audio · stream a clip WAV (variant=raw|trimmed) for playback
 *   PATCH  /api/wake-words/{id}/clips/{name}       · include/exclude one clip from training {selected}
 *   POST   /api/wake-words/{id}/clips/selection    · bulk select {selected, names?, only_verdict?}
 *   POST   /api/wake-words/{id}/clips/reanalyze    · force-recompute quality + trim
 *   DELETE /api/wake-words/{id}/clips/{name}       · drop one clip (+ its analysis artifacts)
 *   POST   /api/wake-words/{id}/score              · offline max-over-clip scores (needs a ready model)
 *   PATCH  /api/wake-words/{id}                    · rename / threshold / set_default
 *   POST   /api/wake-words/{id}/push               · push the ready model to a room {room_id}
 *   DELETE /api/wake-words/{id}                    · remove (refuses the default)
 */

const WAKE_MIN_CLIPS = 15;   // mirrors settings.wake_word_min_clips (default)

const _wakeFieldStyle = {
  font: 'inherit', fontSize: 13, padding: '7px 10px', borderRadius: 'var(--r-sm)',
  border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--fg)',
};

const _WAKE_STATUS_TONE = {
  recording: 'idle', training: 'warn', ready: 'live', failed: 'err',
};

// A room <select> fed by the satellite roster, filtered to online rooms.
const RoomSelect = ({ value, onChange, rooms }) => (
  <select value={value} onChange={(e) => onChange(e.target.value)} style={_wakeFieldStyle}>
    {rooms.length === 0 && <option value="">no online satellites</option>}
    {rooms.map((r) => <option key={r.room_id} value={r.room_id}>{r.room_id}</option>)}
  </select>
);

// Quality verdict → Pill tone / sparkline colour.
const _CLIP_TONE = { good: 'ok', fair: 'warn', poor: 'err' };
const _CLIP_COLOR = { good: 'var(--ok)', fair: 'var(--warn)', poor: 'var(--err)' };

// A compact energy sparkline built from the clip's downsampled RMS envelope —
// the "container reflects quality" visual (a clean phrase has a clear hump;
// silence/gated clips look flat or spiky).
const ClipSparkline = ({ env, color }) => (
  <div style={{ display: 'flex', alignItems: 'flex-end', gap: 1, height: 26, width: '100%' }}>
    {(env || []).map((v, i) => (
      <div key={i} style={{ flex: 1, minWidth: 1, height: `${Math.max(4, v * 100)}%`,
                            background: color, opacity: 0.45 + 0.55 * v, borderRadius: 1 }}/>
    ))}
  </div>
);

// One recorded clip: quality pill, sparkline, key metrics, offline score (once
// scored), raw/trimmed playback, an include-in-training checkbox, and delete.
const WakeClipCard = ({ c, playing, onPlay, onToggle, onDelete }) => {
  const tone = _CLIP_TONE[c.verdict] || 'idle';
  const color = _CLIP_COLOR[c.verdict] || 'var(--fg-muted)';
  const m = c.metrics || {};
  const isPlaying = (v) => playing && playing.name === c.name && playing.variant === v;
  const idx = (c.name.match(/(\d+)/) || [])[1] || c.name;
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', padding: 10,
                  background: 'var(--card)', display: 'flex', flexDirection: 'column', gap: 8,
                  opacity: c.selected ? 1 : 0.5, transition: 'opacity .15s ease' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <input type="checkbox" checked={c.selected} title="include in training"
               onChange={(e) => onToggle(c, e.target.checked)}/>
        <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>#{idx}</span>
        <Pill tone={tone}>{c.verdict}</Pill>
        {typeof c.score === 'number' && (
          <span className="mono" style={{ fontSize: 11, color: c.score >= 0.5 ? 'var(--ok)' : 'var(--err)' }}>
            {c.score.toFixed(2)}
          </span>
        )}
        <div style={{ marginLeft: 'auto' }}>
          <IconButton name="trash-2" title="delete clip" onClick={() => onDelete(c)}/>
        </div>
      </div>
      <ClipSparkline env={c.envelope} color={color}/>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)',
                                     display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <span>SNR {m.snr_db}dB</span>
        <span>{c.raw_duration_ms}ms{c.has_trimmed ? ` → ${c.trimmed_duration_ms}` : ''}</span>
        {m.clipping_pct > 0 && <span style={{ color: 'var(--err)' }}>clip {m.clipping_pct}%</span>}
        {(c.issues || []).filter((i) => i !== 'clipping').map((i) => (
          <span key={i} style={{ color: 'var(--warn)' }}>{i.replace(/_/g, ' ')}</span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <Button variant={isPlaying('raw') ? 'primary' : 'secondary'}
                icon={isPlaying('raw') ? 'pause' : 'play'}
                onClick={() => onPlay(c.name, 'raw')}>raw</Button>
        <Button variant={isPlaying('trimmed') ? 'primary' : 'secondary'}
                icon={isPlaying('trimmed') ? 'pause' : 'play'}
                disabled={!c.has_trimmed} onClick={() => onPlay(c.name, 'trimmed')}>trimmed</Button>
      </div>
    </div>
  );
};

// The per-wake-word clip grid: header curation controls (select all/none,
// deselect poor, re-analyze, score) + a responsive grid of clip cards. One
// <audio> plays at a time. Refreshes live on `wake_words.changed` (a new clip
// bumps the count) and after any local mutation.
const WakeClipGrid = ({ wid, canScore, fire }) => {
  const { data, loading, refresh } = useApiObject(`/api/wake-words/${wid}/clips`,
    { eventTypes: ['wake_words.changed'] });
  const clips = (data && data.clips) || [];
  const selectedCount = (data && data.selected_count) || 0;
  const minClips = (data && data.min_clips) || WAKE_MIN_CLIPS;
  const [busy, setBusy] = React.useState(false);
  const [playing, setPlaying] = React.useState(null);   // { name, variant }
  const audioRef = React.useRef(null);

  React.useEffect(() => () => { if (audioRef.current) { audioRef.current.pause(); } }, []);

  const play = (name, variant) => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    if (playing && playing.name === name && playing.variant === variant) { setPlaying(null); return; }
    const url = `/api/wake-words/${wid}/clips/${encodeURIComponent(name)}/audio?variant=${variant}`;
    const audio = new Audio(url);
    audioRef.current = audio;
    setPlaying({ name, variant });
    const done = () => { setPlaying(null); if (audioRef.current === audio) audioRef.current = null; };
    audio.onended = done;
    audio.onerror = () => { fire('clip playback failed'); done(); };
    audio.play().catch(() => { fire('clip playback failed'); done(); });
  };

  const act = async (fn, okMsg) => {
    setBusy(true);
    try { await fn(); if (okMsg) fire(okMsg); await refresh(); }
    catch (e) { fire(`failed: ${e.message || e}`); }
    finally { setBusy(false); }
  };
  const toggle = (c, selected) =>
    act(() => apiPatch(`/api/wake-words/${wid}/clips/${encodeURIComponent(c.name)}`, { selected }));
  const selectAll = (selected) =>
    act(() => apiPost(`/api/wake-words/${wid}/clips/selection`, { selected }),
        selected ? 'all clips selected' : 'all clips deselected');
  const deselectPoor = () =>
    act(() => apiPost(`/api/wake-words/${wid}/clips/selection`, { selected: false, only_verdict: 'poor' }),
        'poor clips deselected');
  const reanalyze = () =>
    act(() => apiPost(`/api/wake-words/${wid}/clips/reanalyze`, {}), 're-analyzed clips');
  const del = (c) =>
    act(() => apiDelete(`/api/wake-words/${wid}/clips/${encodeURIComponent(c.name)}`), `deleted ${c.name}`);
  const score = () =>
    act(async () => {
      const res = await apiPost(`/api/wake-words/${wid}/score`, {});
      const s = res && res.summary;
      if (s) fire(`scored: real recall ${Math.round((s.raw_recall || 0) * 100)}% · silence ${s.silence_score}`);
    });

  if (loading && clips.length === 0)
    return <div style={{ padding: 12, fontSize: 12, color: 'var(--fg-muted)' }}>loading clips…</div>;
  if (clips.length === 0)
    return <div style={{ padding: 12, fontSize: 12, color: 'var(--fg-faint)' }}>No clips recorded yet — hit Record on a room.</div>;

  return (
    <div style={{ padding: '2px 2px 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 500,
                       color: selectedCount >= minClips ? 'var(--ok)' : 'var(--fg-muted)' }}>
          {selectedCount} / {minClips} selected for training
        </span>
        <span style={{ marginLeft: 'auto' }}/>
        <Button variant="ghost" onClick={() => selectAll(true)} disabled={busy}>Select all</Button>
        <Button variant="ghost" onClick={() => selectAll(false)} disabled={busy}>None</Button>
        <Button variant="ghost" onClick={deselectPoor} disabled={busy}>Deselect poor</Button>
        <Button variant="ghost" icon="refresh-cw" onClick={reanalyze} disabled={busy}>Re-analyze</Button>
        {canScore && <Button variant="secondary" icon="activity" onClick={score} disabled={busy}>Score clips</Button>}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 }}>
        {clips.map((c) => (
          <WakeClipCard key={c.name} c={c} playing={playing}
                        onPlay={play} onToggle={toggle} onDelete={del}/>
        ))}
      </div>
    </div>
  );
};

const WakeRow = ({ w, rooms, minClips = WAKE_MIN_CLIPS, fire, onRecordStart, onRecordStop, onTrain,
                   onRename, onThreshold, onSetDefault, onPush, onDelete }) => {
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState(w.name);
  const [room, setRoom] = React.useState('');
  const [thr, setThr] = React.useState(w.threshold);
  const [showClips, setShowClips] = React.useState(false);

  // Live-recording UX. `recActive` flips the moment the operator clicks Record
  // (so the banner shows before the first clip lands) and clears on Stop or when
  // the row leaves `recording`. `capLive` is driven purely by clip_count bumps
  // arriving on the `wake_words.changed` channel, so an in-progress take is still
  // surfaced after a page reload or from another browser; it lapses a few seconds
  // after clips stop arriving. `bumped` briefly flashes the counter on each clip.
  const [recActive, setRecActive] = React.useState(false);
  const [capLive, setCapLive] = React.useState(false);
  const [bumped, setBumped] = React.useState(false);
  const prevCount = React.useRef(w.clip_count);
  const flashTimer = React.useRef(null);
  const capTimer = React.useRef(null);

  // Keep the local threshold input in sync if the row refreshes underneath us.
  React.useEffect(() => { setThr(w.threshold); }, [w.threshold]);

  // A new clip landed: flash the counter and refresh the "capturing" heartbeat.
  React.useEffect(() => {
    if (w.clip_count > prevCount.current) {
      setBumped(true);
      clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setBumped(false), 900);
      setCapLive(true);
      clearTimeout(capTimer.current);
      capTimer.current = setTimeout(() => setCapLive(false), 6000);
    }
    prevCount.current = w.clip_count;
  }, [w.clip_count]);
  React.useEffect(() => () => {
    clearTimeout(flashTimer.current); clearTimeout(capTimer.current);
  }, []);
  // A take only lives in the `recording` phase; once it trains / fails / goes
  // ready, drop the active indicators regardless of local state.
  React.useEffect(() => {
    if (w.status !== 'recording') { setRecActive(false); setCapLive(false); }
  }, [w.status]);

  const saveName = () => {
    const t = name.trim();
    if (!t || t === w.name) { setEditing(false); setName(w.name); return; }
    onRename(w, t);
    setEditing(false);
  };

  const commitThreshold = () => {
    const v = parseFloat(thr);
    if (!isFinite(v) || v === w.threshold) { setThr(w.threshold); return; }
    onThreshold(w, v);
  };

  const tone = _WAKE_STATUS_TONE[w.status] || 'idle';
  // The chosen room defaults to the first online one if none picked yet.
  const roomFor = room || (rooms[0] && rooms[0].room_id) || '';
  const haveRooms = rooms.length > 0;
  const canTrain = w.status === 'recording' && w.clip_count >= minClips;
  const canPush = w.status === 'ready';
  const capturing = (recActive || capLive) && w.status === 'recording';
  const startRec = () => { setRecActive(true); onRecordStart(w, roomFor); };
  const stopRec = () => { setRecActive(false); setCapLive(false); onRecordStop(w, roomFor); };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 14px',
                  borderBottom: '1px solid var(--border-soft)' }}>
      {/* Top line — name, phrase, status, clip count, threshold */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {editing ? (
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
                 onKeyDown={(e) => e.key === 'Enter' && saveName()}
                 style={{ ..._wakeFieldStyle, width: 180 }}/>
        ) : (
          <div style={{ fontSize: 14, fontWeight: 500 }}>{w.name}</div>
        )}
        <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>“{w.phrase}”</span>
        <Pill tone={tone} live={capturing}>{w.status}</Pill>
        <span style={{ fontSize: 13, fontWeight: bumped ? 700 : 600,
                       color: bumped ? 'var(--brand)'
                                     : (w.clip_count >= minClips ? 'var(--ok)' : 'var(--fg-muted)'),
                       transition: 'color .25s ease' }}>
          {w.clip_count} / {minClips} clips
        </span>
        {w.is_default && <Pill tone="live">default</Pill>}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>threshold</span>
          <input type="number" step="0.05" min="0" max="1" value={thr}
                 onChange={(e) => setThr(e.target.value)} onBlur={commitThreshold}
                 onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
                 style={{ ..._wakeFieldStyle, width: 72 }}/>
          {editing
            ? <Button variant="primary" onClick={saveName}>Save</Button>
            : <IconButton name="pencil" title="rename" onClick={() => setEditing(true)}/>}
          {!w.is_default && <IconButton name="trash-2" title="delete" onClick={() => onDelete(w)}/>}
        </div>
      </div>

      {w.status === 'failed' && w.error && (
        <div style={{ fontSize: 12, color: 'var(--err)' }}>{w.error}</div>
      )}

      {/* Live recording banner — prominent while a take is capturing so a long
          manual session (runs until Stop) is easy to monitor at a glance. */}
      {capturing && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px',
                      borderRadius: 8, border: '1px solid var(--err)',
                      background: 'oklch(0.62 0.21 25 / 0.10)' }}>
          <Pill tone="err" live>Recording</Pill>
          <span style={{ fontSize: 15, fontWeight: 700,
                         color: bumped ? 'var(--brand)' : 'var(--fg)',
                         transition: 'color .25s ease' }}>
            {w.clip_count} clip{w.clip_count === 1 ? '' : 's'} captured
          </span>
          <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            {w.clip_count >= minClips
              ? 'enough to train — keep going or click Stop'
              : `${minClips - w.clip_count} more to reach the train minimum`}
          </span>
          <Button variant="secondary" icon="square" style={{ marginLeft: 'auto' }}
                  onClick={stopRec}>Stop recording</Button>
        </div>
      )}

      {/* Action line — record on a room, train, push, set default */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <RoomSelect value={roomFor} onChange={setRoom} rooms={rooms}/>
        <Button variant="secondary" icon="mic"
                disabled={!haveRooms || w.status !== 'recording' || capturing}
                onClick={startRec}>Record</Button>
        <Button variant="ghost" icon="square" disabled={!haveRooms}
                onClick={stopRec}>Stop</Button>
        <Button variant="primary" icon="cpu" disabled={!canTrain}
                onClick={() => onTrain(w)}>Train</Button>
        <Button variant="secondary" icon="upload" disabled={!haveRooms || !canPush}
                onClick={() => onPush(w, roomFor)}>Push to room</Button>
        {canPush && !w.is_default &&
          <Button variant="ghost" onClick={() => onSetDefault(w)}>make default</Button>}
        <Button variant="ghost" icon={showClips ? 'chevron-down' : 'chevron-right'}
                onClick={() => setShowClips((s) => !s)}>
          {showClips ? 'hide clips' : `clips (${w.clip_count})`}
        </Button>
      </div>

      {/* Expandable per-clip review: quality, auto-trim, playback, curation */}
      {showClips && (
        <div style={{ borderTop: '1px dashed var(--border-soft)', marginTop: 2 }}>
          <WakeClipGrid wid={w.id} canScore={w.status === 'ready'} fire={fire}/>
        </div>
      )}
    </div>
  );
};

const AddWakeWord = ({ onAdd }) => {
  const [name, setName] = React.useState('');
  const [phrase, setPhrase] = React.useState('');
  const submit = (e) => {
    e?.preventDefault?.();
    const n = name.trim(); const p = phrase.trim();
    if (!n || !p) return;
    onAdd(n, p);
    setName(''); setPhrase('');
  };
  return (
    <form onSubmit={submit}
          style={{ display: 'flex', gap: 8, padding: '10px 14px', alignItems: 'center', flexWrap: 'wrap' }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Hey Domovoi)"
             style={{ ..._wakeFieldStyle, width: 200 }}/>
      <input value={phrase} onChange={(e) => setPhrase(e.target.value)}
             placeholder="Spoken phrase (e.g. hey domovoi)"
             style={{ ..._wakeFieldStyle, flex: 1, minWidth: 220 }}/>
      <Button variant="primary" icon="plus" type="submit">Create</Button>
    </form>
  );
};

const WakeWordsPanel = () => {
  // Live clip-count/status updates ride the net-new `wake_words.changed`
  // channel (the streaming clip-write, the trainer, and these CRUD routes all
  // pg_notify it). Unlike Voices, this panel does have realtime.
  const { items, loading, refresh } = useApiList('/api/wake-words',
    { eventTypes: ['wake_words.changed'] });
  // The satellite roster drives the Record / Push room pickers; only online
  // rooms can take a control frame, so filter to those.
  const { items: sats } = useApiList('/api/satellites',
    { eventTypes: ['satellites.presence.changed'] });
  // The train-gate threshold is server config (settings.wake_word_min_clips);
  // read it so the UI gate/label track the real value instead of a guess.
  // The server's /train still 409s authoritatively below it.
  const { data: cfg } = useApiObject('/api/config');
  const minClips = (cfg && cfg.wake_word_min_clips) || WAKE_MIN_CLIPS;
  const [fire, node] = useToast();

  const rooms = (sats || []).filter((s) => s.status === 'online');

  const guard = async (fn, okMsg) => {
    try {
      await fn();
      if (okMsg) fire(okMsg);
      await refresh();
    } catch (e) {
      fire(`failed: ${e.message || e}`);
    }
  };

  const add = (name, phrase) =>
    guard(() => apiPost('/api/wake-words', { name, phrase }), 'wake word created');
  const recordStart = (w, room) => {
    if (!room) { fire('no online satellite to record on'); return; }
    return guard(() => apiPost(`/api/wake-words/${w.id}/record/start`, { room_id: room }),
      `recording “${w.phrase}” on ${room}…`);
  };
  const recordStop = (w, room) => {
    if (!room) { fire('pick a room'); return; }
    return guard(() => apiPost(`/api/wake-words/${w.id}/record/stop`, { room_id: room }),
      'recording stopped');
  };
  const train = (w) =>
    guard(() => apiPost(`/api/wake-words/${w.id}/train`, {}), `training “${w.name}”…`);
  const rename = (w, name) =>
    guard(() => apiPatch(`/api/wake-words/${w.id}`, { name }), 'wake word renamed');
  const threshold = (w, value) =>
    guard(() => apiPatch(`/api/wake-words/${w.id}`, { threshold: value }), 'threshold updated');
  const setDefault = (w) =>
    guard(() => apiPatch(`/api/wake-words/${w.id}`, { set_default: true }), `${w.name} is now the default`);
  const push = (w, room) => {
    if (!room) { fire('no online satellite to push to'); return; }
    return guard(() => apiPost(`/api/wake-words/${w.id}/push`, { room_id: room }),
      `pushed “${w.name}” to ${room}`);
  };
  const remove = (w) =>
    guard(() => apiDelete(`/api/wake-words/${w.id}`), 'wake word removed');

  return (
    <React.Fragment>
      <Card title="Create a wake word"
            sub="Name it, then record positive clips on a satellite and train an openWakeWord model.">
        <AddWakeWord onAdd={add}/>
      </Card>

      <div style={{ padding: '0 2px 10px', fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
        ⚠ Record clips on the <strong>same mic board</strong> the satellite uses at runtime — the
        XVF3800's on-chip beamforming/AGC reshapes the signal, so a model trained from HAT-recorded
        clips can detect poorly on an XVF array. Training runs on Domovoi and is Linux-only / off by
        default (see <code>scripts/wake_word/README.md</code>).
      </div>

      {loading ? null : items.length === 0 ? (
        <Empty title="No wake words yet" sub="Create one above to start recording clips."/>
      ) : (
        <Card title={`Wake words (${items.length})`}
              sub="Record clips, train, then push a ready model to a room.">
          {items.map((w) => (
            <WakeRow key={w.id} w={w} rooms={rooms} minClips={minClips} fire={fire}
                     onRecordStart={recordStart} onRecordStop={recordStop} onTrain={train}
                     onRename={rename} onThreshold={threshold} onSetDefault={setDefault}
                     onPush={push} onDelete={remove}/>
          ))}
        </Card>
      )}
      {node}
    </React.Fragment>
  );
};

/* ============================================================ */
/* About tab                                                    */
/* ============================================================ */
/*
 * A light, informational panel — what Domovoi / Domovoi is, in a few
 * one-liners, plus a primary link into the User Manual (the interactive
 * "how domovoi works" topology page, route "#manual"). It leads the tabs as
 * the friendly intro; Configuration stays intentionally last. Pure frontend —
 * no data fetch. Version / build identifiers live under Configuration → Version.
 */

const AboutPanel = () => (
  <Card title="About Domovoi"
        sub="The local-first home voice assistant that runs entirely on your own hardware.">
    <div style={{ padding: '6px 16px 16px', display: 'flex', flexDirection: 'column', gap: 10,
                  fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.55, maxWidth: 680 }}>
      <div>
        <strong style={{ color: 'var(--fg)' }}>Domovoi</strong> is a household guardian spirit from
        Slavic folklore, often taking the shape of a cat — hence the mascot. The assistant's spoken
        name is configurable; this dashboard manages the Domovoi server it runs on.
      </div>
      <div>
        A Pi in each room hears you, Domovoi does the thinking — speech-to-text, understanding, voice —
        and the answer plays back through that room's speakers.
      </div>
      <div>
        It's <strong style={{ color: 'var(--fg)' }}>local-first</strong>: speech, understanding, local
        voices, the music library, timers and intercom all work with no internet. Only a few features
        (web search, media-provider plugins, cloud voices) need the network, and they degrade gracefully instead of
        breaking.
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
        Build / version identifiers live under Configuration → Version.
      </div>
      <div style={{ marginTop: 4 }}>
        <Button variant="primary" icon="book-open"
                onClick={() => { window.location.hash = 'manual'; }}>
          Open the user manual
        </Button>
      </div>
    </div>
  </Card>
);

/* ============================================================ */
/* Settings shell                                               */
/* ============================================================ */

const SETTINGS_TABS = [
  { id: 'about', label: 'About' },            // informational intro / manual link
  { id: 'greetings', label: 'Greetings' },
  { id: 'voices', label: 'Voices' },
  { id: 'wakewords', label: 'Wake Words' },
  { id: 'models', label: 'Models' },          // model-management hub (was a nav route)
  { id: 'config', label: 'Configuration' },   // last tab, by request
];

const SETTINGS_SUB = {
  about: 'What Domovoi is — and a link to the user manual.',
  greetings: 'Lines a satellite plays the instant the wake word fires.',
  voices: 'The TTS voice registry — each satellite speaks in one.',
  wakewords: 'Train + manage custom wake words; record clips on a satellite.',
  models: "What's active in each role, install more, and the host hardware readout.",
  config: 'Editable domovoi configuration.',
};

const SettingsPage = () => {
  const [tab, setTab] = React.useState('greetings');

  return (
    <div className="page">
      <PageHeader title="Settings" sub={SETTINGS_SUB[tab]}/>
      <Tabs tabs={SETTINGS_TABS} value={tab} onChange={setTab}/>
      {tab === 'about' && <AboutPanel/>}
      {tab === 'greetings' && <GreetingsPanel/>}
      {tab === 'voices' && <VoicesPanel/>}
      {tab === 'wakewords' && <WakeWordsPanel/>}
      {tab === 'models' && <ModelsPanel/>}
      {tab === 'config' && <ConfigPanel/>}
    </div>
  );
};

window.SettingsPage = SettingsPage;
