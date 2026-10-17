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
 * Edits the short lines a satellite says when it hears the wake word,
 * before it listens (its `[wake] ack_mode` "greeting", the default).
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
  // Deletes confirm first (F-009) — the trash icon used to be the delete.
  const del = useDeleteConfirm((g) =>
    guard(() => apiDelete(`/api/greetings/${g.id}`), 'greeting removed'));
  const remove = (g) => del.request(g, {
    title: 'Delete this greeting?',
    body: <div>“{g.text}” leaves the {g.category} bank and satellites stop playing it. This can’t be undone.</div>,
  });

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
      {del.node}
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
 * upload a local Piper model (.onnx + .onnx.json), set the default,
 * rename, delete. Every mutation pings the server to re-render clips and
 * push them to satellites. (The Microsoft Edge cloud voices were dropped
 * on 2026-10-12.)
 *
 * Data:
 *   GET    /api/voices            · the registry (each row's `sample` and `problem`)
 *   POST   /api/voices/piper      · upload a Piper model (multipart)
 *   PATCH  /api/voices/{id}       · rename / set default
 *   DELETE /api/voices/{id}       · remove (refuses the default)
 */

// The Voices page's fields are the dashboard's own (`cal-inp`: the card
// colour, the theme's border, the brand focus ring, light and dark); these
// styles only lay them out.
const _voiceFieldLayout = { minWidth: 0, boxSizing: 'border-box', maxWidth: '100%' };

// A file picker in the dashboard's style, not the browser's raw "Choose
// File" button: a themed button opens the hidden input, and the chosen
// file's name (or "no file chosen") sits beside it.
const VoiceFilePick = ({ label, accept, file, onPick, disabled = false }) => {
  const ref = React.useRef(null);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', minWidth: 0 }}>
      <input ref={ref} type="file" accept={accept} hidden aria-label={label}
             onChange={(e) => {
               onPick((e.target.files && e.target.files[0]) || null);
               e.target.value = '';   // picking the same file again still counts
             }}/>
      <Button type="button" icon="file" disabled={disabled}
              onClick={() => ref.current && ref.current.click()}>{label}</Button>
      <span title={file ? file.name : undefined}
            style={{ fontSize: 12, color: file ? 'var(--fg)' : 'var(--fg-muted)', minWidth: 0, maxWidth: '100%',
                     overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: '1 1 120px' }}>
        {file ? file.name : 'no file chosen'}
      </span>
    </div>
  );
};

// A play button plays the voice it names, or is not shown (owner,
// 2026-10-12). The server says what each voice can play: "voice" (it
// speaks live), "clip" (a shipped voice not downloaded yet: its built-in
// Ogg Opus clip, which this browser must be able to play) or null.
const VOICE_CLIP_PLAYABLE = (() => {
  try { return !!new Audio().canPlayType('audio/ogg; codecs=opus'); } catch { return false; }
})();
const voiceSamplePlayable = (source) => source === 'voice' || (source === 'clip' && VOICE_CLIP_PLAYABLE);

const VoiceRow = ({ v, sampling, onPlay, onRename, onSetDefault, onDelete }) => {
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState(v.name);

  const save = () => {
    const t = name.trim();
    if (!t || t === v.name) { setEditing(false); setName(v.name); return; }
    onRename(v, t);
    setEditing(false);
  };

  // A voice that can't speak here keeps no play button (the system voice
  // would answer), and says why instead. A Microsoft Edge row left from an
  // older release goes when the server restarts: no "make default" for it.
  const playable = v.sample === undefined || voiceSamplePlayable(v.sample);
  const dropped = v.engine === 'edge';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
                  borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
      {playable
        ? <IconButton name={sampling ? 'loader' : 'play'}
                      title={sampling ? 'Synthesizing…' : 'Play a sample'}
                      disabled={sampling} onClick={() => onPlay(v)}/>
        : <span style={{ width: 28, flex: 'none' }} aria-hidden="true"/>}
      {editing ? (
        <input className="cal-inp" value={name} onChange={(e) => setName(e.target.value)} autoFocus
               aria-label="Voice name" onKeyDown={(e) => e.key === 'Enter' && save()}
               style={{ ..._voiceFieldLayout, flex: 1, minWidth: 120 }}/>
      ) : (
        <div style={{ flex: 1, minWidth: 120 }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>{v.name}</div>
          {v.problem && (
            <div style={{ fontSize: 12, color: 'var(--warn)' }}>
              Can’t speak here: {v.problem}. {dropped
                ? 'It goes when the server restarts; rooms set to it speak in the default voice.'
                : 'Rooms set to it speak with the system voice.'}
            </div>
          )}
        </div>
      )}
      <span style={{ fontSize: 12, color: 'var(--fg-muted)', maxWidth: 200,
                     overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {v.model_ref}
      </span>
      {v.is_default
        ? <Pill tone="live">default</Pill>
        : !dropped && <Button variant="ghost" onClick={() => onSetDefault(v)}>make default</Button>}
      {editing
        ? <Button variant="primary" onClick={save}>Save</Button>
        : <IconButton name="pencil" title="rename" onClick={() => setEditing(true)}/>}
      {!v.is_default && <IconButton name="trash-2" title="delete" onClick={() => onDelete(v)}/>}
    </div>
  );
};

const UploadPiper = ({ onUpload }) => {
  const [name, setName] = React.useState('');
  const [onnx, setOnnx] = React.useState(null);
  const [json, setJson] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  // "Upload voice" can only work with a name and both files: until then it
  // is greyed and says what is missing.
  const missing = [!name.trim() && 'a name', !onnx && 'the model', !json && 'the config'].filter(Boolean);

  const submit = async (e) => {
    e?.preventDefault?.();
    const n = name.trim();
    if (!n || !onnx || !json) return;
    setBusy(true);
    try {
      await onUpload(n, onnx, json);
      setName('');
      setOnnx(null);
      setJson(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}
          style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '10px 14px' }}>
      <input className="cal-inp" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Ryan)"
             aria-label="Name" style={{ ..._voiceFieldLayout, width: 240 }}/>
      <VoiceFilePick label="Model (.onnx)" accept=".onnx" file={onnx} onPick={setOnnx} disabled={busy}/>
      <VoiceFilePick label="Config (.onnx.json)" accept=".json" file={json} onPick={setJson} disabled={busy}/>
      <div>
        <Button variant="primary" icon="upload" type="submit" disabled={busy || missing.length > 0}
                title={missing.length ? `Choose ${missing.join(', ')} first` : undefined}>
          {busy ? 'Uploading…' : 'Upload voice'}
        </Button>
      </div>
    </form>
  );
};

/*
 * Domovoi's own voice (above the registry): which of the three public-
 * domain voices the server speaks with, and the "say it like" list that
 * tells the text-to-phoneme converter how to say a word. The phone reads
 * the same thing (GET /api/voice) so it sounds the same.
 *
 * Data:
 *   GET    /api/voice                          · voices, server_voice, pronunciations (device tier)
 *   GET    /api/voice/sample/{id}              · a sample in one voice (device tier)
 *   PUT    /api/voice/server                   · {voice} (admin)
 *   POST   /api/voice/pronunciations           · {word, say_like} (admin)
 *   DELETE /api/voice/pronunciations/{word}    · (admin)
 *   POST   /api/voice/pronunciations/defaults  · put the shipped entries back (admin)
 *   POST   /api/voice/pronunciations/preview   · {word, say_like, voice?} → WAV (device tier)
 */

const SAY_LIKE_HELP = 'Write it the way it sounds, in plain letters, the syllables split by hyphens and the '
  + 'stressed one in CAPITALS: Domovoi = DOH-moe-voy, Kamron = KAM-ren, Siobhan = shiv-AWN, Nguyen = win. '
  + 'The other syllables are said lightly. Sounds as dictionaries spell them: uh (but), ay (say), ee (see), '
  + 'oh (go), oo (too), ow (now), zh (measure). Advanced: the voice’s own phonemes between slashes, /wɪn/ '
  + '(hover a play button you have heard to see them).';

const VoiceSettingsCards = ({ fire }) => {
  const { data, loading, error, refresh } = useApiObject('/api/voice');
  // What each voice's play button can play (live, its clip, or nothing).
  const { data: sampleInfo } = useApiObject('/api/voice/samples', { quiet: true });
  const admin = useAdminSignedIn();
  const [current, setCurrent] = React.useState(null);
  const [busy, setBusy] = React.useState(null);      // what is playing / saving
  const [word, setWord] = React.useState('');
  const [sayLike, setSayLike] = React.useState('');
  // The voice's phonemes of each word heard here (lower-cased word -> the
  // string): advanced detail, on the play buttons' hover, not in the toast.
  const [heard, setHeard] = React.useState({});
  const audioRef = React.useRef(null);
  const view = current || data;

  React.useEffect(() => { setCurrent(null); }, [data]);

  const stopAudio = () => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
  };
  React.useEffect(() => stopAudio, []);

  // Play a WAV the server synthesizes; `key` marks which button is busy.
  // `say`: a "say it like" preview, toasted in plain words ("Kamron →
  // KAM-ren"), its phonemes kept for the button's hover.
  const playFrom = async (key, path, opts, say) => {
    stopAudio();
    setBusy(key);
    try {
      const r = await apiFetchRaw(path, opts || {});
      const phon = r.headers.get('X-Phonemes');
      if (say) {
        fire(`🔊 ${say.word} → ${say.sayLike}`);
        if (phon) setHeard((h) => ({ ...h, [say.word.toLowerCase()]: decodeURIComponent(phon) }));
      }
      const said = r.headers.get('X-Sample-Text');
      if (said) fire(`🔊 ${decodeURIComponent(said)}`);
      const url = URL.createObjectURL(await r.blob());
      const audio = new Audio(url);
      audioRef.current = audio;
      const done = () => { setBusy(null); URL.revokeObjectURL(url); };
      audio.onended = done;
      audio.onerror = () => { fire('playback failed'); done(); };
      await audio.play();
    } catch (e) {
      reportMutationFailure(fire, 'Play', e);
      setBusy(null);
    }
  };

  const save = async (key, fn, okMsg) => {
    setBusy(key);
    try {
      const next = await fn();
      if (next) setCurrent(next);
      if (okMsg) fire(okMsg);
      return true;
    } catch (e) {
      reportMutationFailure(fire, 'Save', e, { kept: true });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const pickVoice = (v) => save(`use:${v.id}`,
    () => apiFetch('/api/voice/server', { method: 'PUT', body: JSON.stringify({ voice: v.id }) }),
    `${v.name} is now the server's voice; the clips re-render in it`);
  const preview = (w, s) => playFrom(`say:${w}`, '/api/voice/pronunciations/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ word: w, say_like: s }),
  }, { word: w, sayLike: s });
  // "Hear it", with the voice's phonemes on hover once it has been heard.
  const hearTitle = (w, base) => {
    const ph = heard[(w || '').toLowerCase()];
    return ph ? `${base} (the voice’s phonemes: /${ph.replace(/[.!?]$/, '')}/)` : base;
  };
  const addEntry = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    const w = word.trim(); const s = sayLike.trim();
    if (!w || !s) return;
    const ok = await save('add', () => apiPost('/api/voice/pronunciations', { word: w, say_like: s }),
      `${w} = ${s} saved`);
    if (ok) { setWord(''); setSayLike(''); }
  };
  const removeEntry = (p) => save(`del:${p.word}`,
    () => apiDelete(`/api/voice/pronunciations/${encodeURIComponent(p.word)}`),
    `${p.word} is left to the converter again`);
  const restoreDefaults = () => save('defaults',
    () => apiPost('/api/voice/pronunciations/defaults', {}), 'the shipped entries are back');

  if (loading && !view) return null;
  if (!view) {
    return (
      <Card title="Domovoi's voice" sub="Could not load the voice settings.">
        <div style={{ padding: '10px 14px', fontSize: 13 }}>
          {error ? apiErrorText(error, 160) : 'No answer.'}{' '}
          <Button variant="ghost" onClick={refresh}>Try again</Button>
        </div>
      </Card>
    );
  }
  const lock = admin ? undefined : 'Sign in as an admin to change this';

  return (
    <React.Fragment>
      <Card title="Domovoi's voice"
            sub="The voice the server speaks in (rooms that picked their own keep it). All three are public domain and run on this server.">
        {view.voices.map((v) => {
          const inUse = v.id === view.server_voice;
          return (
            <div key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
                                     borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
              {voiceSamplePlayable(sampleInfo?.samples?.[v.id])
                ? <IconButton name={busy === `play:${v.id}` ? 'loader' : 'play'}
                              title={sampleInfo.samples[v.id] === 'clip'
                                ? 'Play a sample (a short built-in clip: the voice isn’t downloaded yet)'
                                : 'Play a sample'}
                              disabled={!!busy} onClick={() => playFrom(`play:${v.id}`, `/api/voice/sample/${v.id}`)}/>
                : <span style={{ width: 28, flex: 'none' }} aria-hidden="true"/>}
              <div style={{ flex: 1, minWidth: 140 }}>
                <div style={{ fontSize: 14, fontWeight: 500 }}>{v.name}</div>
                <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{v.sex} · {v.id}</div>
              </div>
              {inUse
                ? <Pill tone="live">in use</Pill>
                : <Button variant="ghost" disabled={!admin || !!busy} title={lock}
                          onClick={() => pickVoice(v)}>use this voice</Button>}
            </div>
          );
        })}
      </Card>

      <Card title="Say it like"
            sub="How Domovoi says the words it gets wrong: names, brands, the house's own words."
            action={admin && <Button variant="ghost" disabled={!!busy} onClick={restoreDefaults}
                                     title="Domovoi, Domo and Nguyen as shipped">put back the defaults</Button>}>
        {view.pronunciations.length === 0 && (
          <div style={{ padding: '10px 14px', fontSize: 13, color: 'var(--fg-muted)' }}>
            Nothing on the list: the converter decides every word.
          </div>
        )}
        {view.pronunciations.map((p) => (
          <div key={p.word} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
                                     borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
            <IconButton name={busy === `say:${p.word}` ? 'loader' : 'play'} title={hearTitle(p.word, 'Hear it')}
                        disabled={!!busy} onClick={() => preview(p.word, p.say_like)}/>
            <div style={{ flex: 1, minWidth: 100, fontSize: 14, fontWeight: 500 }}>{p.word}</div>
            <div style={{ flex: 1, minWidth: 100, fontSize: 14, fontFamily: 'var(--font-mono, monospace)' }}>{p.say_like}</div>
            {admin && <IconButton name="trash-2" title="take it off the list" disabled={!!busy}
                                  onClick={() => removeEntry(p)}/>}
          </div>
        ))}
        <form onSubmit={addEntry}
              style={{ display: 'flex', gap: 8, padding: '10px 14px', alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="cal-inp" value={word} onChange={(e) => setWord(e.target.value)} placeholder="Word (e.g. Kamron)"
                 aria-label="Word" maxLength={48} style={{ ..._voiceFieldLayout, flex: '1 1 140px' }}/>
          <input className="cal-inp" value={sayLike} onChange={(e) => setSayLike(e.target.value)}
                 placeholder="Say it like (e.g. KAM-ren)" aria-label="Say it like" maxLength={100}
                 style={{ ..._voiceFieldLayout, flex: '3 1 180px' }}/>
          {/* type="button": a plain button in this form would also submit
              it, saving the entry the owner only meant to hear. */}
          <IconButton type="button" name={busy === `say:${word.trim()}` ? 'loader' : 'play'}
                      title={hearTitle(word.trim(), 'Hear it before saving')}
                      disabled={!!busy || !word.trim() || !sayLike.trim()}
                      onClick={() => preview(word.trim(), sayLike.trim())}/>
          <Button variant="primary" type="submit" disabled={!admin || !!busy || !word.trim() || !sayLike.trim()}
                  title={lock}>Save</Button>
        </form>
        <div style={{ padding: '0 14px 12px', fontSize: 12, color: 'var(--fg-muted)' }}>{SAY_LIKE_HELP}</div>
      </Card>
    </React.Fragment>
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
      // With the browser's credential: the server's synthesis is on the
      // household device tier, and a bare fetch carries only the cookie,
      // which that tier refuses (every Piper sample came back 502).
      const r = await apiFetchRaw(`/api/voices/${v.id}/sample`);
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
      // The server's own sentence (why the voice can't speak right now);
      // quiet when the pair / sign-in prompt owns it.
      reportMutationFailure(fire, 'Play', e);
      setSamplingId(null);
    }
  };

  const guard = async (fn, okMsg) => {
    try {
      await fn();
      if (okMsg) fire(okMsg);
      await refresh();
    } catch (e) {
      fire(isInternetOffError(e) ? INTERNET_OFF_MESSAGE : `failed: ${e.message || e}`);
    }
  };

  const uploadPiper = (name, onnx, json) => {
    const fd = new FormData();
    fd.append('name', name);
    fd.append('onnx', onnx);
    fd.append('config', json);
    // Uploaded either way; one Domovoi can't speak says why, once, here.
    return guard(async () => {
      const v = await apiUpload('/api/voices/piper', fd);
      fire(v && v.problem
        ? `${v.name} is uploaded but can’t speak here: ${v.problem}. Rooms set to it speak with the system voice.`
        : 'voice uploaded');
      return v;
    }, null);
  };

  const rename = (v, name) =>
    guard(() => apiPatch(`/api/voices/${v.id}`, { name }), 'voice renamed');
  const setDefault = (v) =>
    guard(() => apiPatch(`/api/voices/${v.id}`, { set_default: true }), `${v.name} is now the default`);
  // Deletes confirm first (F-009): a Piper voice is an uploaded model file.
  const del = useDeleteConfirm((v) =>
    guard(() => apiDelete(`/api/voices/${v.id}`), 'voice removed'));
  const remove = (v) => del.request(v, {
    title: `Delete the voice “${v.name}”?`,
    body: <div>The uploaded Piper model and its config are removed — upload them again to get the voice back. Satellites using it fall back to the default voice.</div>,
  });

  return (
    <React.Fragment>
      <VoiceSettingsCards fire={fire}/>

      <Card title="Upload a local voice"
            sub="A Piper model (.onnx) and its config (.onnx.json), trained on espeak’s English phonemes: American (“en-us”, what most Piper voices people train use) or British (“en”). Its config says which; Domovoi’s converter writes both. Fully offline once uploaded.">
        <UploadPiper onUpload={uploadPiper}/>
      </Card>

      {loading ? null : items.length === 0 ? (
        <Empty title="No voices yet" sub="Upload a Piper model above."/>
      ) : (
        <Card title={`Registered (${items.length})`}
              sub="The default is used by any satellite that hasn't picked its own.">
          {items.map((v) => (
            <VoiceRow key={v.id} v={v} sampling={samplingId === v.id} onPlay={playSample}
                      onRename={rename} onSetDefault={setDefault} onDelete={remove}/>
          ))}
        </Card>
      )}
      {del.node}
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
 * release label ("1.0.0", or "1.0.0 + 14 (abc1234)" past that release) and
 * git SHA (with a "-dirty" suffix when its working tree has uncommitted
 * changes), the update channel, and an update check. The channel
 * (docs/LINUX_HOST.md, Release channels) is the branch the check and the
 * pull follow: stable (releases only) or main (every change). An admin can
 * switch it here; that never moves the code, it changes what "update
 * available" means: a newer release on stable, commits behind on main. A
 * channel root's update unit doesn't allow (DOMOVOI_UPSTREAM_BRANCH) isn't
 * offered. The panel offers exactly ONE update action
 * at a time, in the order an operator needs them: "Restart to apply
 * changes" when code is on disk but not loaded, else "Pull the latest" when
 * behind, else "Check for updates". The restart is replaced by the manual
 * command on a host without the sudoers grant (see restart_capable).
 *
 * Whenever no restart is waiting, an admin also gets "Restart Domovoi": the
 * same restart with nothing to load, for a setting changed by hand in
 * domovoi/.env, a restart-tier setting, or a server that needs a kick. Never
 * beside "Restart to apply changes" (that one restarts too), and on a host
 * that can't restart itself it is the manual command instead. With the
 * update unit it is that unit's quick plain restart, unless the unit would
 * run its full update (code pulled since the panel read the version, or
 * loaded outside the unit): then the press says so before anything happens
 * and the card says "Updating…" (restartDomovoiServer, components.jsx).
 *
 * On a Linux host with domovoi-update.service (restart_mode "update") the
 * restart also backs up, syncs dependencies, migrates and rolls back on
 * failure; the panel shows that unit's last result, and stops offering a
 * pull while the upstream is the commit it rolled back (bad_sha).
 *
 * While the server says a restart or update is under way
 * (restart_in_progress) that this card is not waiting on itself — pressed
 * before a reload, in another tab, or started by hand — the card offers
 * nothing: one greyed "Updating…" (or "Restarting…") and the underway
 * note, and it follows that run to its end (useServerRestartFollow,
 * components.jsx). The server refuses a second restart, and a pull,
 * meanwhile anyway.
 *
 * Data:
 *   GET   /api/config              · web build (web_version)
 *   GET   /api/config/version      · domovoi git SHA → { sha, restart_mode, last_update, bad_sha, … }
 *   POST  /api/config/version/check· git fetch + count → { behind, ahead, upstream, upstream_sha, error,
 *                                    channel, upstream_version, latest_release, tag_warning }
 *   POST  /api/config/version/pull · git pull --ff-only → { pulled, new_sha, error }
 *   POST  /api/config/version/channel · follow stable or main; HEAD never moves → { ok, changed, channel, error }
 *   POST  /api/config/version/restart · bounce core+web, or start the update unit → { ok, mode, units, error }
 *   GET   /api/config/core/restart · when the core doesn't answer: what "Restart the core" would do
 *   POST  /api/config/core/restart · the web ends a core that doesn't answer; systemd starts it again
 *
 * When the core doesn't answer at all (the version read comes back 502:
 * the web could not reach it), every action above is the core's own and
 * can't run, so the card says so and offers CoreRestart (components.jsx)
 * instead: incident 2026-10-14, a frozen core the old card could only show
 * as "—". `last_watchdog_restart` is the last time the core was ended for
 * freezing (its loop watchdog, its backstops, or that button), and why.
 *   GET   /api/config/editable     · the field set + current values
 *   PATCH /api/config/editable     · { changes } → { applied, rejected, restart_required }
 */

/* ---- Admin account ----------------------------------------------- */
/*
 * First-run setup + sign-in/out surface for the admin tier. The same
 * modal pops automatically whenever an admin-gated call returns
 * 401/403; this card is the discoverable path — fresh installs land
 * here looking for where the setup code goes.
 *
 * TWO kinds of signed in, and the card says which (CORE-6). A live
 * admin sign-in in this tab — the Bearer, held in JS memory only — does
 * everything the tier gates. The HttpOnly cookie a page reload keeps
 * only RENDERS: settings read with their secrets masked and without the
 * Advanced section, and any change asks for the password again. The card
 * used to say "signed in" for both and offer only "sign out", which is
 * how the owner signed out, signed in, reloaded, and never saw Advanced.
 *
 * Since 2026-10-14 a reload inside the reload window (default an hour
 * after the password was entered in this browser) takes the live sign-in
 * back by itself (auth.js resume), so the card says "signed in" and when a
 * reload stops keeping it ("until 14:32" — once, no countdown). Only past
 * the window is a reloaded tab "view only" (adminReloadState, components.jsx).
 *
 * On an unclaimed box, arriving here (the tab, or its address
 * #settings/configuration) opens the first-run admin setup by itself, once
 * per arrival, as soon as a fresh status read says the box is unclaimed:
 * the setup code field has the focus, so a new owner pastes and goes.
 * `setupAsked` is the installer's address, #settings/configuration/setup
 * (SETUP_ROUTE): on a box already claimed before it arrived it says so in
 * one line, and nothing opens. After a claim made here the marker leaves
 * the address (replaced in place), so a reload doesn't call the box
 * claimed "already".
 */
const AdminSection = ({ setupAsked = false }) => {
  const [, force] = React.useReducer((x) => x + 1, 0);
  // live / resumable / view only, and until when a reload keeps it.
  const reload = useAdminReloadState();
  // The first fresh answer this arrival got: 'unclaimed' | 'claimed' | null.
  const [arrived, setArrived] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    const un = Auth.subscribe(force);
    Promise.resolve(Auth.refreshStatus ? Auth.refreshStatus() : Auth.status).then((s) => {
      if (!alive || !s) return;
      const unclaimed = s.setup_complete === false;
      setArrived(unclaimed ? 'unclaimed' : 'claimed');
      if (unclaimed && !Auth.modalOpen) Auth.openModal();
    }, () => { /* unreachable: the card says "checking…" and offers the button */ });
    return () => { alive = false; un(); };
  }, []);
  const st = Auth.status;
  const needsSetup = st && st.setup_complete === false;
  const hostKind = useHostKind(!!needsSetup);
  // Signed in: the sign-in is in this tab, or this browser takes it back
  // on its own (inside the reload window). View only: past the window.
  const live = reload.signedIn;
  const viewOnly = reload.viewOnly;
  // Claimed here, on the installer's address: drop its marker in place.
  const claimedHere = arrived === 'unclaimed' && !!st && st.setup_complete !== false;
  React.useEffect(() => {
    if (!claimedHere || !setupAsked) return;
    try {
      if (routeParts(currentRoute()).join('/') === SETUP_ROUTE) {
        window.history.replaceState(null, '', '#settings/configuration');
      }
    } catch { /* the marker stays; it only adds a note on the next visit */ }
  }, [claimedHere, setupAsked]);
  const alreadySetUp = setupAsked && arrived === 'claimed' && !needsSetup;
  // Not returned: a view-only tab's sign-out waits on the password prompt.
  const signOut = () => { Auth.logout(); };
  // Why the last sign-out did not happen (the server never confirmed it).
  let signOutError = null;
  try { signOutError = Auth.signOutError || null; } catch { /* older Auth */ }
  return (
    <Card title="Admin"
          sub="Gates settings edits, plugin management, satellite upgrades, and deleting files. Saving a document, everyday playback and browsing never need it.">
      <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center',
                    gap: 12, flexWrap: 'wrap' }}>
        {st === null
          ? <Pill tone="idle">checking…</Pill>
          : needsSetup
            ? <Pill tone="warn">not set up yet</Pill>
            : live
              ? <Pill tone="ok">signed in</Pill>
              : viewOnly
                ? <Pill tone="warn">signed in (view only after reload)</Pill>
                : <Pill tone="idle">signed out</Pill>}
        {needsSetup && (
          <span className="setup-code-where" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            <SetupCodeWhere kind={hostKind}/>
          </span>
        )}
        {alreadySetUp && (
          <span className="admin-already-set-up" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            {live
              ? 'This Domovoi’s admin account is already set up, and you’re signed in.'
              : 'This Domovoi’s admin account is already set up: sign in with its password.'}
          </span>
        )}
        {live && signOutError && (
          // A sign-out the server never confirmed: the session and its
          // cookie are still alive there, so this browser IS still signed
          // in, and says so rather than "signed out" (auth.js logout).
          <span className="admin-sign-out-failed"
                style={{ fontSize: 12, color: 'var(--err)', flexBasis: '100%', order: 1 }}>
            Not signed out — {signOutError}, so this browser is still signed
            in.{' '}
            <button type="button" className="home-link" onClick={signOut}>try again</button>
          </span>
        )}
        {live && reload.until && (
          // When a reload stops keeping the sign-in: said once, in its own
          // row, gone when it passes (useAdminReloadState) — no countdown.
          <span className="admin-reload-until"
                style={{ fontSize: 12, color: 'var(--fg-muted)', flexBasis: '100%', order: 1 }}>
            Reloading this page, or opening Domovoi in another tab of this
            browser, keeps you signed in until {reload.until}.
          </span>
        )}
        {viewOnly && (
          // Its own row UNDER the pill and both buttons (order: 1): inline, the
          // long sentence pushed "sign out" onto a line of its own, away
          // from "sign in again".
          <span className="admin-view-only"
                style={{ fontSize: 12, color: 'var(--fg-muted)', flexBasis: '100%', order: 1 }}>
            {reload.windowWords
              ? `A page reload more than ${reload.windowWords} after you signed in keeps this browser signed in for viewing, but forgets`
              : 'A page reload keeps this browser signed in for viewing, but forgets'}
            {' '}the admin sign-in this tab needs to change settings or see
            Advanced. Sign in again to get it back.
          </span>
        )}
        <span style={{ flex: 1 }}/>
        {needsSetup
          ? <Button variant="primary" onClick={() => Auth.openModal()}>set up admin</Button>
          : live
            ? <Button onClick={signOut}>sign out</Button>
            : viewOnly
              ? <>
                  <Button variant="primary" icon="key" onClick={() => Auth.openModal()}>sign in again</Button>
                  <Button onClick={signOut}
                          title="asks for the password once: the server ends a session only for a tab that proves it holds one">
                    sign out
                  </Button>
                </>
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

// UPDATE_RESULT_PILL (the update unit's last result → pill) lives in
// components.jsx beside restartDomovoiServer, which reads it too.
const shortCommit = (s) => (s ? String(s).slice(0, 7) : '');

// The update unit's own warnings: steps that went ahead, but not the way the
// owner set them up. The core passes a `warn` step's detail only for the
// steps whose detail the script writes itself (git_version._WARN_DETAIL_STEPS).
// The one that matters most is a dependency lock the owner opted into
// (DOMOVOI_USE_LOCK=1) that the update could not use: the run still ends ok,
// so without this line the card would show a clean, hash-checked update.
const lastUpdateWarnings = (lastUpdate) => {
  const steps = lastUpdate && Array.isArray(lastUpdate.steps) ? lastUpdate.steps : [];
  const out = [];
  for (const s of steps) {
    if (!s || s.status !== 'warn' || typeof s.detail !== 'string' || !s.detail) continue;
    if (s.name === 'sync-deps' || s.name === 'rollback-deps') {
      out.push(s.detail.startsWith('lock not applied') ? s.detail : `lock not applied: ${s.detail}`);
    } else {
      out.push(s.detail);
    }
  }
  return out;
};

// The update channels (git_version.CHANNELS): what each one follows.
const CHANNEL_TEXT = {
  stable: 'releases only',
  main: 'every change, as it lands',
};

// What an update check found, in the channel's own terms: on stable "a
// newer release" (or that this server runs code newer than the latest
// one), on main how many commits behind. null when there is no answer.
const versionCheckWords = (res) => {
  if (!res || !res.upstream) return null;
  if (res.channel === 'stable') {
    if (res.behind > 0) return `Domovoi ${res.upstream_version || 'a newer release'} is available`;
    if (res.ahead > 0) return 'newer than the latest release';
    return 'the latest release';
  }
  return res.behind > 0 ? `${res.behind} commit${res.behind === 1 ? '' : 's'} behind` : 'up to date';
};

const LastUpdateWarnings = ({ lastUpdate }) => {
  const lines = lastUpdateWarnings(lastUpdate);
  if (!lines.length) return null;
  return (
    <div className="version-update-warnings mono"
         style={{ padding: '0 16px 12px', fontSize: 11, color: 'var(--warn)', overflowWrap: 'anywhere' }}>
      {lines.map((line, i) => <div key={i}>{line}</div>)}
    </div>
  );
};

const VersionSection = () => {
  const { data: cfg } = useApiObject('/api/config');
  // Checking and pulling talk to the git remote: greyed under "No"
  // (Settings → Internet). Restarting stays — it never leaves the box.
  const internetOff = internetOffIn(cfg);
  const { data: core, error: coreError, refresh: refreshCore } = useApiObject('/api/config/version');
  // The web answered for the core: it could not reach it (bridge_response's
  // 502), or the core itself said it can't serve (503/504).
  const coreDown = !core && !!coreError && [502, 503, 504].includes(coreError.status);
  const [fire, node] = useToast();
  const [checking, setChecking] = React.useState(false);
  const [pulling, setPulling] = React.useState(false);
  // The restart under way: 'apply' (Restart to apply changes) or 'plain'
  // (Restart Domovoi); null when there is none.
  const [restarting, setRestarting] = React.useState(null);
  // What it runs, as restartDomovoiServer tells it, in RestartUnderwayNote's
  // props ({ updating, plain }): what the operator said yes to (onStart),
  // then what the server took (onUnderway). A "Restart Domovoi" the update
  // unit runs as its full update is an update. Kept until the restart is
  // over, so the button's word holds while the card reads the version again.
  const [runs, setRuns] = React.useState(null);
  // The server took the restart and is away on purpose (onUnderway → onSettled).
  const [underway, setUnderway] = React.useState(false);
  const [status, setStatus] = React.useState(null);   // result of /version/check
  // An admin: signed in in this tab, or view-only after a reload, whose
  // press asks for the password once (data.js replays the restart). A
  // household member without the admin password never sees the restart.
  const admin = useAdminSignedIn();
  // A restart the server says is under way and this card isn't waiting on
  // itself (see the header): what it runs, or null.
  const serverRuns = useServerRestartFollow({
    core, fire, busy: !!restarting,
    onSettled: () => { setStatus(null); return refreshCore(); },
  });

  const check = async () => {
    setChecking(true); setStatus(null);
    try {
      const res = await apiPost('/api/config/version/check', {});
      setStatus(res);
      if (res && res.error) {
        fire(`update check failed: ${res.error}`);
      } else if (!res || !res.upstream) {
        fire(res && res.channel === 'stable' && !res.latest_release
          ? 'no release has been published on the stable channel yet'
          : 'no upstream configured — can’t check');
      } else {
        fire(versionCheckWords(res));
      }
    } catch (e) {
      reportMutationFailure(fire, 'update check', e);
    } finally {
      setChecking(false);
    }
  };

  // Follow the other channel. Nothing moves now: the server makes the
  // local branch at the code it runs and tracks origin/<channel>; the
  // check that follows says what is new there.
  const [switching, setSwitching] = React.useState(false);
  const switchChannel = async (to) => {
    if (!to || !core || to === core.channel) return;
    if (!window.confirm(to === 'stable'
      ? 'Follow the stable channel: Domovoi releases only?\n\nNothing changes now. ' +
        'The update check then offers each new release. If this server already ' +
        'runs newer code than the latest release, updates resume with the next ' +
        'release that includes it.'
      : 'Follow the main channel: every change, as it lands?\n\nNothing changes ' +
        'now. The update check then offers each new change on main, which is ' +
        'tested less than a release.'
    )) return;
    setSwitching(true);
    try {
      const res = await apiPost('/api/config/version/channel', { channel: to });
      if (res && res.ok) {
        fire(`now following ${to}`);
        setStatus(null);
        await refreshCore();
        await check();
      } else {
        fire(`channel not changed: ${(res && res.error) || 'unknown'}`);
      }
    } catch (e) {
      reportMutationFailure(fire, 'channel switch', e);
    } finally {
      setSwitching(false);
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
      reportMutationFailure(fire, 'pull', e);
    } finally {
      setPulling(false);
    }
  };

  // The restart itself (confirm, bounce, wait for the server to come back)
  // is restartDomovoiServer in components.jsx, shared with the Plugins
  // page's "restart to finish the upgrade". `plain`: Restart Domovoi, with
  // nothing waiting to load (which the update unit may still run as its
  // full update; restartDomovoiServer asks, and onUnderway says).
  const restart = (plain) => restartDomovoiServer({
    core, fire, plain,
    onStart: (how) => { setRestarting(plain ? 'plain' : 'apply'); setRuns(how || null); },
    onUnderway: (how) => { if (how) setRuns(how); setUnderway(true); },
    onSettled: () => { setUnderway(false); setStatus(null); return refreshCore(); },
  }).finally(() => { setUnderway(false); setRestarting(null); setRuns(null); });

  const webVer = cfg && cfg.web_version;
  // `sha` is the RUNNING code (captured at the core's boot), not whatever is
  // checked out right now — those diverge after a pull without a restart,
  // which is exactly when someone looks at this panel.
  const coreSha = core && core.sha;
  const checkoutSha = core && core.checkout_sha;
  // Its release label ("1.0.0", "1.0.0 + 14 (abc1234)"); the SHA beside it
  // only when the label doesn't already name it.
  const coreLabel = core && typeof core.version === 'string' ? core.version : null;
  const labelHasSha = (label, sha) => !!(label && sha && label.includes(String(sha).replace(/-dirty$/, '').slice(0, 7)));
  // The update channel: what the check and the pull follow, and the ones an
  // admin may switch to (root's DOMOVOI_UPSTREAM_BRANCH can narrow them).
  const channel = core && typeof core.channel === 'string' ? core.channel : null;
  const channelPin = core && Array.isArray(core.channel_pin) ? core.channel_pin : null;
  const channelChoices = (core && Array.isArray(core.channels) ? core.channels : [])
    .filter((c) => !channelPin || channelPin.includes(c));
  // Pulled code and staged plugin upgrades both wait on the same restart;
  // the panel says which (see pendingRestart in components.jsx).
  const pending = pendingRestart(core);
  const restartPending = pending.any;
  const codePending = pending.code;
  const behind = status && status.upstream ? status.behind : null;
  const restartCapable = !!(core && core.restart_capable);
  const updateUnit = !!(core && core.restart_mode === 'update');
  const lastUpdate = core && core.last_update;
  const lastPill = lastUpdate && (UPDATE_RESULT_PILL[lastUpdate.status]
                                  || { tone: 'idle', label: lastUpdate.status });
  // The update unit rolled this commit back. Pulling it again would only
  // repeat that, so while the upstream still points at it there is nothing
  // to offer but another check later.
  const badSha = core && core.bad_sha;
  const upstreamIsBad = !!(badSha && status && status.upstream_sha === badSha);
  // One action at a time, in the order the operator actually needs them:
  // code already on disk beats fetching more of it, and "check" is only
  // honest when we have no reason to think anything is pending.
  // 'busy' first: while a restart is under way nothing else may start.
  const mode = serverRuns ? 'busy' : restartPending ? 'restart'
    : ((behind || 0) > 0 ? (upstreamIsBad ? 'held' : 'pull') : 'check');
  // Restart Domovoi: an admin's, whenever no restart is waiting (that one
  // is the primary action, and it restarts too). On a host that can't
  // restart itself, the command to run instead.
  const plainRestart = admin && !!core && mode !== 'restart' && mode !== 'busy';
  const plainRunning = restarting === 'plain';
  // The switch is an admin's, and only where there is somewhere to go.
  // Not while the server can't read root's settings file: it can't tell
  // which channels the update unit would accept (channel_pin_problem).
  const pinUnknown = !!(core && core.channel_pin_problem);
  const canSwitch = admin && !!core && mode !== 'busy' && !pinUnknown
                    && channelChoices.some((c) => c !== channel);
  // On stable, past the latest release (switched from main): nothing to
  // pull until a release includes this code.
  const aheadOfRelease = !!(status && status.upstream && status.channel === 'stable'
                            && status.behind === 0 && status.ahead > 0);
  // Set up to follow stable before the first release (provision.sh then
  // follows main, and remembers what was asked for): once a release is out,
  // say so. Nothing switches by itself; the switch is above.
  const releaseOut = (core && core.latest_release) || (status && status.latest_release) || null;
  const stableWaiting = !!(core && core.channel_intended === 'stable' && channel && channel !== 'stable' && releaseOut);
  // Signed updates (docs/LINUX_HOST.md): whether the checked-out commit
  // carries a signature by a key in the server's root-owned allowed-signers
  // file. One badge: signed (verified); else what it is, and whether that
  // stops an update (enforced) or only means signing isn't set up yet.
  const sig = core && core.signature;
  const sigBadge = sig && sig.status && sig.status !== 'unknown' ? (
    sig.status === 'verified'
      ? { tone: 'ok', label: 'signed',
          title: `the checked-out commit is signed by ${sig.signer || 'an allowed key'} (${sig.allowed_signers})` }
      : { tone: sig.enforced ? 'err' : 'warn',
          label: sig.status === 'unsigned' ? 'unsigned' : 'signature not verified',
          title: sig.enforced
            ? `the checked-out commit ${sig.detail || sig.status}; signed updates are enforced on this server, so an update would refuse it`
            : `the checked-out commit is ${sig.detail || sig.status}; signed updates are not enforced on this server (no ${sig.allowed_signers})` }
  ) : null;

  return (
    <Card title="Version"
          sub="The Domovoi release this server runs, which updates it follows, and the update check.">
      <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '140px 1fr',
                    rowGap: 8, fontSize: 13, alignItems: 'center' }}>
        <div className="label">web build</div>
        <div className="mono" style={{ color: webVer ? 'var(--fg)' : 'var(--fg-faint)' }}>{webVer || '—'}</div>
        <div className="label">domovoi</div>
        <div className="mono" style={{ color: coreSha ? 'var(--fg)' : 'var(--fg-faint)', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {coreLabel && <span className="version-label" style={{ userSelect: 'all' }}>{coreLabel}</span>}
          {(!coreLabel || !labelHasSha(coreLabel, coreSha)) && (
            <span style={{ userSelect: 'all', color: coreLabel ? 'var(--fg-muted)' : undefined }}>{coreSha || '—'}</span>
          )}
          {coreSha && coreSha.endsWith('-dirty') &&
            <Pill tone="warn">uncommitted changes</Pill>}
          {sigBadge && <span className="version-signature" title={sigBadge.title}><Pill tone={sigBadge.tone}>{sigBadge.label}</Pill></span>}
          {core && core.uptime_sec != null && (
            <span style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
              running {fmtUptime(core.uptime_sec)}
            </span>
          )}
        </div>
        {codePending && (
          <React.Fragment>
            <div className="label">checked out</div>
            <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {core.checkout_version && <span>{core.checkout_version}</span>}
              {!labelHasSha(core.checkout_version, checkoutSha) && <span style={{ userSelect: 'all' }}>{checkoutSha}</span>}
              <Pill tone="warn">restart to load</Pill>
            </div>
          </React.Fragment>
        )}
        {core && (channel || canSwitch) && (
          <React.Fragment>
            <div className="label">updates</div>
            <div className="version-channel" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12 }}>
              {canSwitch ? (
                <select value={channel || ''} onChange={(e) => switchChannel(e.target.value)}
                        disabled={switching || internetOff || !!restarting || pulling}
                        aria-label="update channel"
                        title={internetOff ? NEEDS_INTERNET_TEXT : 'which updates this server follows; switching moves no code'}
                        style={{ font: 'inherit', fontSize: 12, height: 28, padding: '0 8px',
                                 borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                                 background: 'var(--card)', color: 'var(--fg)', maxWidth: 280 }}>
                  {!channelChoices.includes(channel) && (
                    <option value={channel || ''} disabled>{channel ? `${channel} (not a channel)` : 'no branch followed'}</option>
                  )}
                  {channelChoices.map((c) => (
                    <option key={c} value={c}>{CHANNEL_TEXT[c] ? `${c}: ${CHANNEL_TEXT[c]}` : c}</option>
                  ))}
                </select>
              ) : (
                <span>
                  <span className="mono">{channel}</span>
                  {CHANNEL_TEXT[channel] && <span style={{ color: 'var(--fg-muted)' }}>{`: ${CHANNEL_TEXT[channel]}`}</span>}
                </span>
              )}
              {switching && <span style={{ color: 'var(--fg-muted)' }}>switching…</span>}
              {admin && pinUnknown && (
                <span style={{ color: 'var(--fg-muted)' }}
                      title="the server can't read /etc/default/domovoi-update, where its update unit may pin a branch; making it readable (chmod 0644) brings the switch back">
                  can’t switch: the server can’t read its update settings
                </span>
              )}
              {admin && !canSwitch && !pinUnknown && channelPin && (
                <span style={{ color: 'var(--fg-muted)' }}
                      title="DOMOVOI_UPSTREAM_BRANCH in /etc/default/domovoi-update, which only an administrator on the server can change">
                  set on the server
                </span>
              )}
            </div>
          </React.Fragment>
        )}
        {core && core.last_watchdog_restart && core.last_watchdog_restart.at && (
          <React.Fragment>
            <div className="label">last freeze</div>
            <div className="version-last-freeze" style={{ fontSize: 12, color: 'var(--fg-muted)' }}
                 title={core.last_watchdog_restart.log ? `every thread's stack: ${core.last_watchdog_restart.log}` : undefined}>
              {new Date(core.last_watchdog_restart.at).toLocaleString()} · restarted · {core.last_watchdog_restart.reason}
            </div>
          </React.Fragment>
        )}
        {lastUpdate && (
          <React.Fragment>
            <div className="label">last update</div>
            <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Pill tone={lastPill.tone}>{lastPill.label}</Pill>
              <span>
                {lastUpdate.mode === 'update' && lastUpdate.from_sha !== lastUpdate.to_sha
                  ? `${shortCommit(lastUpdate.from_sha)} → ${shortCommit(lastUpdate.to_sha)}`
                  : `restart at ${shortCommit(lastUpdate.to_sha)}`}
              </span>
              {lastUpdate.finished_at && (
                <span style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
                  {new Date(lastUpdate.finished_at).toLocaleString()}
                </span>
              )}
            </div>
          </React.Fragment>
        )}
      </div>
      {coreDown && (
        <div className="version-core-down" style={{ padding: '0 16px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--err)' }}>
            The Domovoi core isn’t answering, so the satellites and the app can’t reach it.
          </div>
          <CoreRestart onBack={() => { setStatus(null); return refreshCore(); }}/>
        </div>
      )}
      {lastUpdate && lastUpdate.error && lastUpdate.status !== 'ok' && lastUpdate.status !== 'running' && (
        <div className="mono" style={{ padding: '0 16px 12px', fontSize: 11, color: 'var(--fg-muted)',
                                       overflowWrap: 'anywhere' }}>
          {lastUpdate.error}
        </div>
      )}
      {lastUpdate && <LastUpdateWarnings lastUpdate={lastUpdate}/>}
      {codePending && mode !== 'busy' && (
        <div style={{ padding: '0 16px 12px', fontSize: 12, color: 'var(--warn)' }}>
          New code is on disk but this process is still running the old
          modules{restartCapable ? ' — restart below to load it.'
                                 : ' — restart the Domovoi server for it to take effect.'}
        </div>
      )}
      {pending.plugins.length > 0 && mode !== 'busy' && (
        <div style={{ padding: '0 16px 12px', fontSize: 12, color: 'var(--warn)' }}>
          Plugin upgrade{pending.plugins.length === 1 ? '' : 's'} waiting for a restart:{' '}
          <span className="mono">{pending.plugins.map(pluginUpgradeLabel).join(', ')}</span>
          {restartCapable ? ' — restart below to finish.' : ' — restart the Domovoi server to finish.'}
        </div>
      )}
      <div style={{ padding: '0 16px 14px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {mode === 'busy' && (
          <Button variant="primary" icon="refresh-cw" disabled
                  title="a restart is under way — this card shows the version it runs when it’s done">
            {serverRuns.updating ? 'Updating…' : 'Restarting…'}
          </Button>
        )}
        {mode === 'restart' && restartCapable && (
          <Button variant="primary" icon="refresh-cw" onClick={() => restart(false)} disabled={!!restarting}>
            {restarting ? (updateUnit && !plainRunning ? 'Updating…' : 'Restarting…') : 'Restart to apply changes'}
          </Button>
        )}
        {mode === 'pull' && (
          <Button variant="primary" icon="download" onClick={pull} disabled={pulling || internetOff || !!restarting}
                  title={internetOff ? NEEDS_INTERNET_TEXT : undefined}>
            {pulling ? 'Pulling…' : 'Pull the latest'}
          </Button>
        )}
        {(mode === 'check' || mode === 'held') && (
          <Button variant="secondary" icon="refresh-cw" onClick={check} disabled={checking || internetOff || !!restarting}
                  title={internetOff ? NEEDS_INTERNET_TEXT : undefined}>
            {checking ? 'Checking…' : 'Check for updates'}
          </Button>
        )}
        {internetOff && mode !== 'restart' && mode !== 'busy' && <NeedsInternetNote compact/>}
        {mode !== 'restart' && mode !== 'busy' && behind != null && (
          <span className="version-check-result" style={{ fontSize: 12, color: behind > 0 ? 'var(--warn)' : 'var(--ok)' }}>
            {versionCheckWords(status)}
          </span>
        )}
        {mode !== 'restart' && mode !== 'busy' && behind != null && status.channel !== 'stable' && status.latest_release && (
          <span className="version-latest-release" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            latest release: {status.latest_release}
          </span>
        )}
        {plainRestart && restartCapable && (
          // Its own end of the row: a restart, not a step of the update.
          <span className="version-restart" style={{ marginLeft: 'auto' }}>
            <Button variant="secondary" icon="rotate-cw" onClick={() => restart(true)} disabled={!!restarting || pulling}
                    title="Restart the Domovoi services, so changes to domovoi/.env take effect">
              {plainRunning ? (runs && runs.updating ? 'Updating…' : 'Restarting…') : 'Restart Domovoi'}
            </Button>
          </span>
        )}
      </div>
      {underway && (
        <div style={{ padding: '0 16px 14px' }}>
          <RestartUnderwayNote updating={!!(runs && runs.updating)} plain={!!(runs && runs.plain)}/>
        </div>
      )}
      {serverRuns && (
        <div style={{ padding: '0 16px 14px' }}>
          <RestartUnderwayNote updating={serverRuns.updating} plain={serverRuns.plain}/>
        </div>
      )}
      {plainRestart && !restartCapable && (
        <RestartByHandHint version={core} style={{ padding: '0 16px 14px' }}/>
      )}
      {mode === 'pull' && (
        <div className="mono" style={{ padding: '0 16px 14px', fontSize: 11, color: 'var(--fg-faint)' }}>
          Pull updates the host files only — this panel will then offer the restart that loads them.
        </div>
      )}
      {status && status.tag_warning && mode !== 'busy' && (
        // A release tag that moved upstream (or tags that didn't arrive):
        // the labels may be off; the check and the pull work as ever.
        <div className="version-tag-warning" style={{ padding: '0 16px 12px', fontSize: 12, color: 'var(--warn)' }}>
          {status.tag_warning}
        </div>
      )}
      {stableWaiting && (
        <div className="version-stable-waiting" style={{ padding: '0 16px 14px', fontSize: 12, color: 'var(--fg-muted)' }}>
          This server was set up to follow stable, Domovoi’s releases only,
          before the first release was out, so it follows main for now.
          Domovoi {releaseOut} is out:{' '}
          {canSwitch ? 'switch to stable above to follow releases from now on.'
                     : 'an admin can switch it to stable here.'}
        </div>
      )}
      {aheadOfRelease && mode === 'check' && (
        <div className="version-ahead-of-release" style={{ padding: '0 16px 14px', fontSize: 12, color: 'var(--fg-muted)' }}>
          This server runs newer code than the latest release
          ({coreLabel || shortCommit(coreSha)}); updates resume with the next
          release that includes it.
        </div>
      )}
      {mode === 'held' && (
        <div style={{ padding: '0 16px 14px', fontSize: 12, color: 'var(--warn)' }}>
          The latest commit ({shortCommit(badSha)}) failed to apply on this server
          and was rolled back, so it isn’t offered again. The next commit
          upstream will be.
        </div>
      )}
      {mode === 'restart' && !restartCapable && (
        <RestartByHandHint version={core} style={{ padding: '0 16px 14px' }}/>
      )}
      {node}
    </Card>
  );
};

// Why the configuration card has nothing in it. Same three states the
// Models tab keeps apart (activeMessage): a 401 is not a connectivity
// problem and must not send anyone hunting for a stopped service; a 502
// from the web backend means the Domovoi server itself is down. Left
// unsaid, an unreachable server read as "this Domovoi has no settings"
// (F-012).
const configLoadMessage = (error) => {
  if (error.status === 401 || error.status === 403) {
    return 'admin login required — sign in to view and edit configuration';
  }
  if (error.status === 502) return 'domovoi unreachable — configuration unavailable';
  return `couldn't load configuration (${error.status || 'no response'}): ${apiErrorText(error)}`;
};

const ConfigPanel = ({ setupAsked = false }) => {
  // What comes back depends on WHO asks (CORE-6): the advanced section
  // and the unmasked secrets only for a live admin Bearer, the common
  // section with secrets masked for the cookie a reload keeps. So this
  // read re-reads on every sign-in and sign-out (refetchOnAuth, data.js)
  // — it used to succeed under the cookie and never look again, and
  // Advanced stayed hidden after a sign-in until the tab was switched.
  const { data, loading, error, refresh } = useApiObject('/api/config/editable', { refetchOnAuth: true });
  const fields = (data && data.fields) || [];
  // The answer itself has one home, its own tab: here it is one line. Fields
  // that need the internet are greyed under "No", read off that same row.
  const internetField = fields.find((f) => f.name === 'internet_access');
  const internetOff = !!(internetField && internetField.value === 'never');
  const [edits, setEdits] = React.useState({});
  const [saving, setSaving] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [advOpen, setAdvOpen] = React.useState(false);
  // "sign in" pressed on the withheld note: open Advanced when it lands.
  const [wantAdvanced, setWantAdvanced] = React.useState(false);
  const [retrying, setRetrying] = React.useState(false);
  const retry = async () => {
    setRetrying(true);
    try { await refresh(); } finally { setRetrying(false); }
  };
  // Inside the reload window this browser's sign-in comes back with no
  // prompt (auth.js resume); the login modal is for when it can't.
  const signIn = () => {
    try {
      if (Auth.canResume && Auth.canResume()) {
        Auth.resume().then((ok) => { if (!ok) Auth.openModal(); });
        return;
      }
      Auth.openModal();
    } catch { /* auth.js absent */ }
  };
  const signInForAdvanced = () => { setWantAdvanced(true); signIn(); };
  let signedInHere = false;
  try { signedInHere = Auth.isLoggedIn(); } catch { /* auth.js absent */ }
  // The reload window's length in words, for the note below ("an hour").
  const reloadWords = adminReloadState().windowWords;

  const setEdit = (name, v) => setEdits(prev => ({ ...prev, [name]: v }));
  const valueOf = (f) => (f.name in edits ? edits[f.name] : f.value);
  const dirtyCount = Object.keys(edits).length;
  const common = fields.filter(f => f.section !== 'advanced');
  const advanced = fields.filter(f => f.section === 'advanced');
  // CORE-6: the core sends the advanced block only to a caller holding an
  // admin bearer. Say so where the block would be — an absent Advanced
  // section otherwise reads as "this server has no infrastructure knobs".
  const advancedWithheld = !!data && data.advanced_available === false;
  React.useEffect(() => {
    if (wantAdvanced && advanced.length > 0) { setAdvOpen(true); setWantAdvanced(false); }
  }, [wantAdvanced, advanced.length]);

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
      // The server's own reason, and "cancelled" for a sign-in the
      // operator dismissed (a reloaded tab holds no admin sign-in, so a
      // save asks for one) — never a raw "403 Forbidden: {…}". Nothing
      // while the sign-in prompt owns the story.
      const msg = mutationErrorText(e, 'Save');
      if (msg) setResult({ error: msg });
    } finally {
      setSaving(false);
    }
  };

  const renderGroups = (list) => {
    const grouped = _groupBy(list.filter((f) => f.group !== 'Internet'));
    return Object.keys(grouped).map(group => (
      <div key={group} style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em',
                      color: 'var(--fg-muted)', fontWeight: 600, marginBottom: 2 }}>{group}</div>
        {grouped[group].map(f => <ConfigField key={f.name} f={f} value={valueOf(f)} internetOff={internetOff}
                                              onChange={v => setEdit(f.name, v)}/>)}
      </div>
    ));
  };
  const internetLine = internetField && (
    <div className="config-internet-line" style={{ fontSize: 13, padding: '6px 0 12px' }}>
      <span style={{ fontWeight: 500 }}>Internet: </span>
      <span>{(internetField.value && (internetField.choice_labels || {})[internetField.value])
             || internetField.value || 'not answered'}</span>
      <span style={{ color: 'var(--fg-muted)' }}> · change it in </span>
      <a href="#settings/internet" className="home-link">
        Settings → Internet
      </a>
    </div>
  );

  const rejectedCount = result && result.rejected ? Object.keys(result.rejected).length : 0;

  return (
    <React.Fragment>
    <AdminSection setupAsked={setupAsked}/>
    <VersionSection/>
    <Card title="Domovoi configuration"
          sub="Live-editable settings. Some changes apply instantly; those marked “restart” need a Domovoi server restart.">
      <div style={{ padding: '4px 14px 14px' }}>
        {loading && fields.length === 0
          ? <div style={{ padding: 30, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading settings…</div>
          : error && fields.length === 0
          ? <div style={{ padding: 30, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>
              <div>{configLoadMessage(error)}</div>
              <div style={{ marginTop: 10, display: 'flex', gap: 8, justifyContent: 'center' }}>
                {(error.status === 401 || error.status === 403) && (
                  <Button variant="primary" icon="key" onClick={signIn}>sign in</Button>
                )}
                <Button icon="refresh-cw" onClick={retry} disabled={retrying}>
                  {retrying ? 'retrying…' : 'retry'}
                </Button>
              </div>
            </div>
          : <>
              {internetLine}
              {renderGroups(common)}
              {advancedWithheld && (
                <div className="config-advanced-withheld"
                     style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 10,
                              fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
                  <div>
                    {signedInHere
                      ? 'The server withheld the advanced settings — database URL, ports, paths — from this tab’s sign-in. Signing in again asks for them once more.'
                      : 'Advanced settings — database URL, ports, paths — need an admin sign-in in this tab.'}
                  </div>
                  <div style={{ marginTop: 4 }}>
                    {reloadWords
                      ? `A page reload more than ${reloadWords} after you signed in keeps you signed in for viewing, but forgets the admin sign-in that Advanced and saving changes here need — sign in again to get it back.`
                      : 'A page reload keeps you signed in for viewing, but forgets the admin sign-in that Advanced and saving changes here need — sign in again after a reload.'}
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <Button variant="primary" icon="key" onClick={signInForAdvanced}>
                      {signedInHere ? 'sign in again' : 'sign in'}
                    </Button>
                  </div>
                </div>
              )}
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

        {/* "no changes / Save" under an empty card read as a live editor
            with nothing to edit; there is no footer until fields exist. */}
        {fields.length > 0 && (
        <div style={{ marginTop: 8, borderTop: '1px solid var(--border-soft)', paddingTop: 12 }}>
          {result && result.error &&
            <div style={{ fontSize: 12, color: 'var(--err)', marginBottom: 8 }}>{result.error}</div>}
          {rejectedCount > 0 &&
            <div style={{ fontSize: 12, color: 'var(--err)', marginBottom: 8 }}>
              rejected: {Object.entries(result.rejected).map(([k, v]) => `${k} (${v})`).join('; ')}
            </div>}
          {result && result.normalized && Object.keys(result.normalized).length > 0 &&
            <div style={{ fontSize: 12, color: 'var(--warn)', marginBottom: 8 }}>
              adjusted: {Object.entries(result.normalized).map(([k, v]) => `${k} (${v})`).join('; ')}
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
        )}
      </div>
    </Card>
    </React.Fragment>
  );
};

/* ============================================================ */
/* Internet tab                                                 */
/* ============================================================ */
/*
 * "Will this Domovoi have internet?" — the household's one answer
 * (INTERNET_ACCESS). It sets the DEFAULTS of the online extras; anything
 * set by hand wins, and "No" also closes every outbound path on the
 * server. The Windows installer asks the same question with the same
 * words; an unanswered box behaves exactly as it always has, and Home
 * shows an admin a row until someone answers.
 *
 * Data:
 *   GET   /api/config/internet  · the answer, the choices, connectivity and
 *                                 every setting that follows the answer
 *   PATCH /api/config/editable  · { changes: { internet_access } } saves the
 *                                 answer; { changes: {}, follow_internet: [name] }
 *                                 lets a hand-set setting follow it again
 */
const INTERNET_STATUS_TEXT = {
  connected: 'Connected',
  offline: 'Not connected right now',
  turned_off: 'Turned off for this box',
};
const INTERNET_ANSWER_LABEL = { always: 'Yes', sometimes: 'Sometimes', never: 'No' };
const internetOnOff = (v) => (v === true ? 'on' : v === false ? 'off' : String(v));
// Names in restart_required → words (a feature's own label, else these).
const INTERNET_RESTART_WORDS = { internet_access: 'the speech models’ download checks' };
// The search helper (SearXNG) behind web answers, as /v1/admin/internet
// reports it (domovoi/searxng_service.py status()).
const SEARCH_HELPER_TEXT = {
  unknown: 'not checked since the Domovoi server started',
  starting: 'starting (the first time it downloads a few hundred MB, which can take a few minutes)',
  stopping: 'stopping',
  running: 'running',
  stopped: 'stopped',
  failed: 'couldn’t start',
  left: 'left as it is',
};

const InternetFeatureRow = ({ f, following, onFollow }) => {
  const later = f.next_boot_value !== f.value;
  const values = f.answer_values || {};
  return (
    <div className="internet-feature" data-feature={f.name}
         style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', flexWrap: 'wrap',
                  borderTop: '1px solid var(--border-soft)' }}>
      <div style={{ flex: 1, minWidth: 220 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{f.label}</div>
        <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
          {['always', 'sometimes', 'never'].map((a) => `${INTERNET_ANSWER_LABEL[a]}: ${internetOnOff(values[a])}`).join(' · ')}
          {f.condition && !f.condition_met && <> · {f.condition}</>}
        </div>
      </div>
      <span className="mono" style={{ fontSize: 12 }}>{internetOnOff(f.value)}</span>
      {later && (
        <span className="internet-feature-later" style={{ fontSize: 12, color: 'var(--warn)' }}>
          → {internetOnOff(f.next_boot_value)} after a restart
        </span>
      )}
      {f.set_by === 'answer' && <Pill tone="ok">follows the answer</Pill>}
      {f.set_by === 'default' && <Pill>default</Pill>}
      {f.set_by === 'environment' && <Pill tone="warn">set in the server's environment</Pill>}
      {f.set_by === 'env_file' && (
        <>
          <span title="set by hand, or written there by an older .env template">
            <Pill tone="idle">set in domovoi/.env</Pill>
          </span>
          <Button variant="ghost" onClick={() => onFollow(f.name)} disabled={!!following}
                  title="comments out its line in domovoi/.env, so it takes the answer's default again">
            {following === f.name ? 'working…' : 'follow the answer again'}
          </Button>
        </>
      )}
    </div>
  );
};

const InternetPanel = () => {
  // What comes back is the same for the cookie and the Bearer (no secrets
  // in it), but a save needs the Bearer: re-read on every sign-in so the
  // page follows the session (refetchOnAuth, data.js).
  const { data, loading, error, refresh } = useApiObject('/api/config/internet', { refetchOnAuth: true });
  const [choice, setChoice] = React.useState(null);   // null = the saved answer
  const [saving, setSaving] = React.useState(false);
  const [following, setFollowing] = React.useState(null);
  const [result, setResult] = React.useState(null);
  const [retrying, setRetrying] = React.useState(false);
  const [helperBusy, setHelperBusy] = React.useState(false);
  const [helperError, setHelperError] = React.useState(null);
  const signIn = () => { try { Auth.openModal(); } catch { /* auth.js absent */ } };
  const retry = async () => {
    setRetrying(true);
    try { await refresh(); } finally { setRetrying(false); }
  };

  const answer = (data && data.answer) || '';
  const selected = choice != null ? choice : answer;
  const locked = !!(data && data.answer_locked);
  const dirty = choice != null && choice !== answer;
  const features = (data && data.features) || [];
  const labelOf = (name) => {
    const f = features.find((x) => x.name === name);
    return (f && f.label) || INTERNET_RESTART_WORDS[name] || name;
  };

  const save = async () => {
    if (!dirty || saving || locked) return;
    setSaving(true); setResult(null);
    try {
      const res = await apiPatch('/api/config/editable', { changes: { internet_access: choice } });
      setResult(res || {});
      const refused = res && res.rejected && res.rejected.internet_access;
      if (!refused) {
        announceInternetChange(choice);
        setChoice(null);
      }
      refresh();
    } catch (e) {
      const msg = mutationErrorText(e, 'Save');
      if (msg) setResult({ error: msg });
    } finally {
      setSaving(false);
    }
  };

  // "start it again": the core runs the search helper's start/stop for the
  // saved answer in the background; the page re-reads its state.
  const startHelper = async () => {
    if (helperBusy) return;
    setHelperBusy(true); setHelperError(null);
    try {
      await apiPost('/api/config/internet/search-helper', {});
      refresh();
      // A first start downloads the image: look again a little later.
      setTimeout(() => { refresh(); }, 5000);
    } catch (e) {
      const msg = mutationErrorText(e, 'Start the search helper', { kept: false });
      if (msg) setHelperError(msg);
    } finally {
      setHelperBusy(false);
    }
  };

  const followAgain = async (name) => {
    if (following) return;
    setFollowing(name); setResult(null);
    try {
      const res = await apiPatch('/api/config/editable', { changes: {}, follow_internet: [name] });
      setResult(res || {});
      refresh();
    } catch (e) {
      const msg = mutationErrorText(e, 'Follow the answer', { kept: false });
      if (msg) setResult({ error: msg });
    } finally {
      setFollowing(null);
    }
  };

  if (!data) {
    return (
      <Card title="Internet">
        <div style={{ padding: 30, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>
          {loading || !error ? 'loading…' : (
            <>
              <div>{configLoadMessage(error)}</div>
              <div style={{ marginTop: 10, display: 'flex', gap: 8, justifyContent: 'center' }}>
                {(error.status === 401 || error.status === 403) && (
                  <Button variant="primary" icon="key" onClick={signIn}>sign in</Button>
                )}
                <Button icon="refresh-cw" onClick={retry} disabled={retrying}>
                  {retrying ? 'retrying…' : 'retry'}
                </Button>
              </div>
            </>
          )}
        </div>
      </Card>
    );
  }

  const conn = data.connectivity || {};
  const statusText = !answer
    ? 'Not answered yet: Domovoi behaves as it always has'
    : (INTERNET_STATUS_TEXT[conn.reason] || INTERNET_STATUS_TEXT.connected);
  const statusTone = !answer ? 'warn' : conn.reason === 'connected' ? 'ok' : conn.reason === 'offline' ? 'warn' : 'idle';
  const restartNames = (result && result.restart_required && result.restart_required.length)
    ? result.restart_required : (data.restart_required || []);
  const refusedText = result && result.rejected ? Object.entries(result.rejected) : [];
  const notes = result && result.normalized ? Object.entries(result.normalized) : [];
  // The internet answer's own note already says what it is about: no label.
  const noteText = ([k, v]) => (k === 'internet_access' ? v : `${labelOf(k)}: ${v}`);
  const helper = data.search_helper || null;
  const helperWanted = answer === 'always' || answer === 'sometimes';
  const helperShown = helper && helper.managed !== false && (helperWanted || helper.state === 'failed');
  const helperCanRetry = helperWanted && !locked && helper
    && !['running', 'starting', 'stopping', 'unmanaged'].includes(helper.state);
  const ownNetPlugins = (data.network_plugins || []).filter((p) => !p.bundled);
  const warnings = data.warnings || [];

  return (
    <React.Fragment>
      <Card title="Will this Domovoi have internet?"
            sub="It sets the defaults of the online extras. Anything you set by hand wins.">
        <div style={{ padding: '10px 16px 14px' }}>
          <div className="internet-status" style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 10 }}>
            <Pill tone={statusTone}>{statusText}</Pill>
            {answer && conn.reason !== 'turned_off' && conn.target && (
              <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>checks {conn.target}</span>
            )}
          </div>
          {(data.choices || []).map((c) => (
            <label key={c.value} className={`internet-choice${selected === c.value ? ' on' : ''}`} data-choice={c.value}
                   style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '9px 0',
                            borderTop: '1px solid var(--border-soft)', cursor: locked ? 'default' : 'pointer',
                            opacity: locked && selected !== c.value ? 0.6 : 1 }}>
              <input type="radio" name="internet-answer" value={c.value} disabled={locked}
                     checked={selected === c.value} onChange={() => { if (!locked) setChoice(c.value); }}/>
              <span>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{c.label}</span>
                <span style={{ display: 'block', fontSize: 12, color: 'var(--fg-muted)' }}>{c.summary}</span>
                <span style={{ display: 'block', fontSize: 12, color: 'var(--fg-muted)', marginTop: 2 }}>{c.detail}</span>
              </span>
            </label>
          ))}
          {locked && (
            <div className="internet-locked" style={{ fontSize: 12, color: 'var(--warn)', marginTop: 8 }}>
              This answer is set in the server's environment (INTERNET_ACCESS), so it can't be changed
              here — change it where the Domovoi server is started, then restart it.
            </div>
          )}
          <div className="internet-privacy" style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 10, lineHeight: 1.5 }}>
            {data.privacy_note}
          </div>
          {(selected === 'always' || selected === 'sometimes') && (
            <div className="internet-searxng-hint" style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6, lineHeight: 1.5 }}>
              Web answers use the search helper (SearXNG). Domovoi starts it when you save Yes or
              Sometimes (the first time it downloads a few hundred MB); on a Linux appliance an
              update also starts it.
            </div>
          )}
          {helperShown && (
            <div className="internet-search-helper" data-state={helper.state}
                 style={{ fontSize: 12, marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ color: helper.state === 'failed' ? 'var(--err)' : 'var(--fg-muted)' }}>
                Search helper: {SEARCH_HELPER_TEXT[helper.state] || helper.state}
                {helper.state === 'failed' && helper.detail ? ` (${helper.detail})` : ''}
              </span>
              {helperCanRetry && (
                <Button variant="ghost" icon="refresh-cw" onClick={startHelper} disabled={helperBusy}
                        title="runs the same start Domovoi does when you save Yes or Sometimes">
                  {helperBusy ? 'starting…' : (helper.state === 'unknown' ? 'check it' : 'start it again')}
                </Button>
              )}
              {helperError && <span style={{ color: 'var(--err)' }}>{helperError}</span>}
            </div>
          )}
          {warnings.length > 0 && (
            <div className="internet-warnings" style={{ fontSize: 12, color: 'var(--warn)', marginTop: 8, lineHeight: 1.5 }}>
              {warnings.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}
          {ownNetPlugins.length > 0 && (
            <div className="internet-network-plugins" style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6, lineHeight: 1.5 }}>
              Add-ons that may reach the internet on their own, whatever you answer:{' '}
              {ownNetPlugins.map((p) => p.name).join(', ')}. Disable one on the Plugins page to keep it
              in the house.
            </div>
          )}
          {result && result.error && (
            <div style={{ fontSize: 12, color: 'var(--err)', marginTop: 8 }}>{result.error}</div>
          )}
          {refusedText.length > 0 && (
            <div className="internet-rejected" style={{ fontSize: 12, color: 'var(--err)', marginTop: 8 }}>
              not saved: {refusedText.map(([k, v]) => `${labelOf(k)} (${v})`).join('; ')}
            </div>
          )}
          {notes.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--warn)', marginTop: 8 }}>
              {notes.map(noteText).join('; ')}
            </div>
          )}
          {restartNames.length > 0 && (
            <div className="internet-restart" style={{ fontSize: 12, color: 'var(--warn)', marginTop: 8, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <Pill tone="warn">restart</Pill>
              <span>saved — restart the Domovoi server to apply: {restartNames.map(labelOf).join(', ')}</span>
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
              {dirty ? 'unsaved' : (answer ? `saved: ${INTERNET_ANSWER_LABEL[answer] || answer}` : 'not answered')}
            </span>
            <div style={{ marginLeft: 'auto' }}>
              <Button variant="primary" icon="check" onClick={save} disabled={!dirty || saving || locked}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </div>
      </Card>
      <Card title="What follows the answer"
            sub="Each of these takes its default from the answer unless you set it yourself.">
        <div style={{ padding: '4px 16px 12px' }}>
          {features.map((f) => (
            <InternetFeatureRow key={f.name} f={f} following={following} onFollow={followAgain}/>
          ))}
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
 *   GET    /api/wake-words/{id}/clips/{name}/audio · stream a clip WAV (variant=raw|trimmed) for playback (device read tier)
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
    // A clip is a recording of somebody's voice, served on the device READ
    // tier; an <audio> element can't set a header, so the household token
    // rides in the query (the dashboard cookie also passes). API_BASE keeps
    // the token on the server it belongs to when another one is selected.
    const url = withDeviceToken(
      `${API_BASE}/api/wake-words/${wid}/clips/${encodeURIComponent(name)}/audio?variant=${variant}`);
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
  // Deletes confirm first (F-009): a clip is a recording that can't be re-taken.
  const delConfirm = useDeleteConfirm((c) =>
    act(() => apiDelete(`/api/wake-words/${wid}/clips/${encodeURIComponent(c.name)}`), `deleted ${c.name}`));
  const del = (c) => delConfirm.request(c, {
    title: `Delete clip ${c.name}?`,
    body: <div>The recording is removed from disk and no longer counts toward training. Record another one on a room to replace it.</div>,
  });
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
      {delConfirm.node}
    </div>
  );
};

const WakeRow = ({ w, rooms, minClips = WAKE_MIN_CLIPS, fire, onRecordStart, onRecordStop, onTrain,
                   onRename, onThreshold, onSetDefault, onPush, onDelete, internetOff = false,
                   trainerOn = true, onTrainStop }) => {
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
  // What the chosen room's device declared (S0): a Pi takes both.
  const chosen = rooms.find((r) => r.room_id === roomFor);
  const canRecordHere = satCan(chosen, 'wake_clips');
  const canPushHere = satCan(chosen, 'wake_models');
  // A failed word trains again on the clips it has (no re-recording).
  const canTrain = (w.status === 'recording' || w.status === 'failed') && w.clip_count >= minClips;
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
                disabled={!haveRooms || w.status !== 'recording' || capturing || !canRecordHere}
                title={canRecordHere ? undefined : "this room's device doesn't record wake-word clips"}
                onClick={startRec}>Record</Button>
        <Button variant="ghost" icon="square" disabled={!haveRooms}
                onClick={stopRec}>Stop</Button>
        {/* Training downloads its data the first time: greyed under "No".
            Until training is turned on (the card above) a Train would only
            queue the word, so the button says why it waits. */}
        <Button variant="primary" icon="cpu" disabled={!canTrain || internetOff || !trainerOn}
                title={internetOff ? NEEDS_INTERNET_TEXT
                  : !trainerOn ? 'turn on wake-word training first (above)' : undefined}
                onClick={() => onTrain(w)}>Train</Button>
        {internetOff && <NeedsInternetNote compact/>}
        {w.status === 'training' && onTrainStop && (
          <Button variant="secondary" icon="square" onClick={() => onTrainStop(w)}
                  title="stop this training; the word keeps its clips and isn't queued again">
            Stop training</Button>
        )}
        {!internetOff && !trainerOn && canTrain && (
          <span style={{ fontSize: 11, color: 'var(--fg-muted)' }}>turn on training above first</span>
        )}
        <Button variant="secondary" icon="upload" disabled={!haveRooms || !canPush || !canPushHere}
                title={canPushHere ? undefined : "this room's device doesn't take wake-word models"}
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

/* ---- Turning training on (GET/POST /api/wake-words/trainer) ----------
 * Training needs a ~12 GB Docker image and ~18 GB of training data, so it
 * is off until an admin turns it on here. The card says what it needs and
 * what this server has, refuses with the reason instead of offering a
 * button that can't work (internet off, disk, memory, Docker), asks a
 * second yes on a CPU-only server (over an hour a word, voice slower
 * meanwhile), then shows the setup job's progress, or the error it
 * stopped on, which stays until the next try. The core does the work
 * (domovoi/wake_trainer_setup.py). */

const _TRAINER_STAGES = [
  ['image', 'Build the trainer'],
  ['gpu', 'Check the GPU'],
  ['data', 'Download the training data'],
  ['settings', 'Switch training on'],
];

const trainerEstimateText = (est) => {
  if (!est) return '';
  if (est.device === 'gpu') {
    const names = (est.gpu_names || []).join(', ');
    return `about ${est.minutes_per_word} minutes a wake word on the GPU${names ? ` (${names})` : ''}, `
      + `${est.first_minutes} the first time`;
  }
  return `about ${est.minutes_per_word} minutes a wake word on the CPU`
    + (est.cpus ? ` (${est.cpus} CPUs; ${est.measured})` : '');
};

const TrainerConfirmDialog = ({ title, busy, confirmLabel, onCancel, onConfirm, children,
                                confirmDisabled = false, icon = 'cpu' }) => (
  <div style={{ position: 'fixed', inset: 0, zIndex: 90, background: 'rgba(0,0,0,0.45)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
       onClick={busy ? undefined : onCancel}>
    <div role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
         style={{ width: 'min(500px, 100%)', background: 'var(--card)',
                  border: '1px solid var(--border)', borderRadius: 'var(--r-lg)',
                  boxShadow: 'var(--shadow-md), var(--inner-highlight)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '13px 16px',
                    borderBottom: '1px solid var(--border-soft)' }}>
        <Icon name={icon} size={16}/>
        <strong style={{ fontSize: 14 }}>{title}</strong>
      </div>
      <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8,
                    fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
        {children}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 16px',
                    borderTop: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
        <Button icon="x" disabled={busy} onClick={onCancel}>Cancel</Button>
        <Button variant="primary" icon="check" disabled={busy || confirmDisabled} onClick={onConfirm}>
          {busy ? 'Working…' : confirmLabel}
        </Button>
      </div>
    </div>
  </div>
);

const TrainerProgress = ({ job }) => {
  const at = _TRAINER_STAGES.findIndex(([id]) => id === job.stage);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ height: 8, borderRadius: 4, background: 'var(--border-soft)', overflow: 'hidden' }}>
        <div style={{ width: `${Math.max(2, job.percent || 0)}%`, height: '100%',
                      background: 'var(--brand)', transition: 'width .4s ease' }}/>
      </div>
      <div style={{ fontSize: 13 }}>{job.message || 'Working…'} <span className="mono"
        style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{job.percent || 0}%</span></div>
      <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--fg-muted)' }}>
        {_TRAINER_STAGES.map(([id, label], i) => (
          <li key={id} style={{ color: i < at ? 'var(--ok)' : i === at ? 'var(--fg)' : undefined,
                                fontWeight: i === at ? 600 : 400 }}>
            {label}{i < at ? ' ✓' : ''}
          </li>
        ))}
      </ol>
    </div>
  );
};

const WakeTrainerCard = ({ status, error, refresh, fire }) => {
  const admin = useAdminSignedIn();
  const [asking, setAsking] = React.useState(null);   // null | { cpu: bool }
  const [busy, setBusy] = React.useState(false);
  // The Windows install can't see C:'s free space: the admin ticks this.
  const [diskOk, setDiskOk] = React.useState(false);
  // "Turn off and free the space": what it removes, read when asked.
  const [offAsk, setOffAsk] = React.useState(null);   // null | { loading } | footprint
  const job = (status && status.job) || {};
  const running = job.state === 'running';

  // Poll while the job works; the page's other data doesn't move with it.
  React.useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => refresh(), 2000);
    return () => clearInterval(t);
  }, [running, refresh]);

  if (!status) {
    if (error && (error.status === 401 || error.status === 403)) {
      return (
        <Card title="Wake-word training" sub="An admin can turn training on here.">
          <div style={{ padding: '4px 14px 12px', fontSize: 13, color: 'var(--fg-muted)' }}>
            Sign in as the admin to see what training needs on this server.
          </div>
        </Card>
      );
    }
    return null;
  }
  const req = status.requirements || {};
  const est = req.estimate || {};
  const gpu = est.device === 'gpu';
  const refusals = req.refusals || [];
  const notes = req.notes || [];
  const disk = req.disk || {};
  const mem = req.memory || {};

  const start = async (cpuOk) => {
    setBusy(true);
    try {
      await apiPost('/api/wake-words/trainer/enable', { cpu_ok: !!cpuOk, disk_ok: diskOk });
      setAsking(null);
      fire('setting up wake-word training…');
    } catch (e) {
      fire(`couldn’t start: ${apiErrorText(e, 0)}`);
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const gb = (b) => (b == null ? 'size unknown' : `${(b / 1e9).toFixed(1)} GB`);
  const turnOff = async () => {
    setBusy(true);
    try {
      const r = await apiPost('/api/wake-words/trainer/disable', {});
      const errs = (r && r.errors) || [];
      fire(errs.length ? `turned off; not everything was removed: ${errs.join('; ')}`
                       : 'wake-word training turned off; the space is free');
      setOffAsk(null);
    } catch (e) {
      fire(`couldn’t turn it off: ${apiErrorText(e, 0)}`);
    } finally {
      setBusy(false);
      refresh();
    }
  };
  const askTurnOff = async () => {
    setOffAsk({ loading: true });
    try { setOffAsk(await apiGet('/api/wake-words/trainer/footprint')); }
    catch (e) { setOffAsk(null); fire(`couldn’t size it: ${apiErrorText(e, 0)}`); }
  };

  // On the Windows install the free space of C: can't be seen from inside
  // Domovoi: no number, a tick instead (owner rule: no misleading numbers).
  const needs = (
    <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
      {disk.confirm_gb ? (
        <li>Disk: about {disk.total_first_gb || 32} GB of the Windows drive (C:) the first time,
          and about half a gigabyte more for each wake word. Domovoi can’t see how much is
          free on C:.</li>
      ) : (
        <li>Disk: about {disk.total_first_gb || 32} GB the first time (the trainer and its
          training data; about half a gigabyte more for each wake word)
          {disk.free_gb != null ? ` · ${disk.free_gb} GB free here` : ''}.</li>
      )}
      <li>A one-time download of about {req.download_gb || 22} GB (the trainer and its training data).</li>
      {mem.pc_need_gb ? (
        <li>Memory: a PC with {mem.pc_need_gb} GB or more (Windows gives Domovoi the PC’s memory
          minus 8 GB, and training needs 24 GB of that){mem.total_gb != null ? ` · Domovoi has ${mem.total_gb} GB of it` : ''}.</li>
      ) : (
        <li>Memory: a 24 GB server{mem.total_gb != null ? ` · this one has ${mem.total_gb} GB` : ''}.</li>
      )}
      <li>Time: {trainerEstimateText(est)}.</li>
      <li>{gpu
        ? 'While a word trains it uses the GPU (the least busy card) and some CPU; voice keeps working.'
        : 'While a word trains it uses most of the CPU, so voice answers more slowly until it finishes.'}</li>
    </ul>
  );

  let body;
  let sub;
  if (running) {
    sub = 'Setting training up. You can leave this page; it carries on.';
    body = <TrainerProgress job={job}/>;
  } else if (status.enabled) {
    sub = gpu ? `On · trains on the GPU${status.gpu_name ? ` (${status.gpu_name})` : ''}`
              : 'On · trains on the CPU';
    const estText = trainerEstimateText(est);
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
        <div>{estText ? `${estText[0].toUpperCase()}${estText.slice(1)}. ` : ''}
          A training stops after {Math.round((status.timeout_sec || 0) / 60)} minutes
          {status.timeout_auto ? ' (sized to this server)' : ''}.</div>
        {!gpu && <div style={{ color: 'var(--warn)' }}>Voice answers more slowly while a word trains.</div>}
        {notes.map((n) => <div key={n} style={{ color: 'var(--fg-muted)' }}>{n}</div>)}
        {admin && !status.training_active && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button variant="ghost" icon="refresh-cw" disabled={busy}
                    onClick={() => setAsking({ cpu: !gpu, again: true })}>
              Check the GPU and trainer again
            </Button>
            <Button variant="ghost" icon="trash-2" disabled={busy} onClick={askTurnOff}>
              Turn off and free the space
            </Button>
          </div>
        )}
      </div>
    );
  } else {
    sub = 'Off. Training a wake word happens on this server, in a container; turn it on once.';
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
        <div style={{ color: 'var(--fg-muted)' }}>What it needs:</div>
        {needs}
        {notes.map((n) => <div key={n} style={{ color: 'var(--fg-muted)' }}>{n}</div>)}
        {refusals.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, color: 'var(--err)' }}>
            <strong>Training can’t be turned on here:</strong>
            {refusals.map((r) => <div key={r}>{r}</div>)}
          </div>
        ) : admin ? (
          <div>
            <Button variant="primary" icon="power" disabled={busy}
                    onClick={() => setAsking({ cpu: !gpu })}>Turn on wake-word training</Button>
          </div>
        ) : (
          <div style={{ color: 'var(--fg-muted)' }}>Sign in as the admin to turn it on.</div>
        )}
      </div>
    );
  }

  const failed = job.state === 'failed' && job.error;
  return (
    <Card title="Wake-word training" sub={sub}>
      <div style={{ padding: '4px 14px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {body}
        {failed && !running && (
          <div style={{ border: '1px solid var(--err)', borderRadius: 8, padding: '8px 12px',
                        display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
            <div style={{ color: 'var(--err)' }}>{job.error}</div>
            {(job.log_tail || []).length > 0 && (
              <details>
                <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--fg-muted)' }}>
                  what it printed last</summary>
                <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', margin: '6px 0 0',
                                              maxHeight: 220, overflow: 'auto' }}>
                  {(job.log_tail || []).join('\n')}</pre>
              </details>
            )}
            {admin && refusals.length === 0 && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <Button icon="rotate-ccw" disabled={busy}
                        onClick={() => setAsking({ cpu: !gpu })}>Try again</Button>
                {job.needs_cpu_confirm && (
                  <Button variant="secondary" icon="cpu" disabled={busy}
                          onClick={() => setAsking({ cpu: true })}>Use the CPU</Button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      {offAsk && (
        <TrainerConfirmDialog title="Turn off wake-word training and free the space?" icon="trash-2"
          busy={busy || !!offAsk.loading} confirmLabel="Turn off and remove"
          onCancel={() => setOffAsk(null)} onConfirm={turnOff}>
          {offAsk.loading ? <div>Asking Docker how much space it takes…</div> : (
            <React.Fragment>
              <div>This turns training off and removes:</div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                <li>the trainer image ({gb(offAsk.image_bytes)}) and Docker’s build cache for it;</li>
                <li>its training data and every wake word’s training files ({gb(offAsk.volume_bytes)}).</li>
              </ul>
              <div>Trained wake words and their recordings stay. Turning training on again
                downloads about {req.download_gb || 22} GB.</div>
            </React.Fragment>
          )}
        </TrainerConfirmDialog>
      )}
      {asking && (
        <TrainerConfirmDialog
          title={asking.cpu ? 'Train wake words on the CPU?' : 'Turn on wake-word training?'}
          busy={busy} confirmLabel={asking.cpu ? 'Use the CPU' : 'Turn it on'}
          confirmDisabled={!!disk.confirm_gb && !diskOk}
          onCancel={() => setAsking(null)} onConfirm={() => start(asking.cpu)}>
          <div>This builds the trainer and downloads its training data now (several minutes),
            then training is on for every wake word.</div>
          {needs}
          {asking.cpu && (
            <div style={{ color: 'var(--warn)' }}>
              No GPU here that training can use, so each wake word takes about{' '}
              {est.minutes_per_word || 75} minutes on the CPU, and voice answers more slowly
              while it runs.
            </div>
          )}
          {disk.confirm_gb ? (
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', color: 'var(--fg)' }}>
              <input type="checkbox" checked={diskOk} onChange={(e) => setDiskOk(e.target.checked)}/>
              <span>The Windows drive (C:) has at least {disk.confirm_gb} GB free. (Domovoi can’t
                check this itself: look in File Explorer, This PC.)</span>
            </label>
          ) : null}
        </TrainerConfirmDialog>
      )}
    </Card>
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
  // Training downloads its data the first time: greyed under "No".
  const internetOff = internetOffIn(cfg);
  const [fire, node] = useToast();
  // Is training on, and on what (the card above the list; the Train
  // buttons ask before a CPU training).
  const { data: trainer, error: trainerError, refresh: refreshTrainer } = useApiObject(
    '/api/wake-words/trainer', { quiet: true, refetchOnAuth: true });
  const trainerOn = !!(trainer && trainer.enabled);
  const trainerEst = trainer && trainer.requirements && trainer.requirements.estimate;
  const cpuTrainer = trainerOn && trainerEst && trainerEst.device === 'cpu';

  // A spare device that declared neither clip recording nor wake models
  // (S0, satCan) has nothing here; a Pi always qualifies.
  const rooms = (sats || []).filter((s) => s.status === 'online'
    && (satCan(s, 'wake_clips') || satCan(s, 'wake_models')));

  const guard = async (fn, okMsg) => {
    try {
      await fn();
      if (okMsg) fire(okMsg);
      await refresh();
    } catch (e) {
      fire(isInternetOffError(e) ? INTERNET_OFF_MESSAGE : `failed: ${e.message || e}`);
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
  const startTraining = (w) =>
    guard(() => apiPost(`/api/wake-words/${w.id}/train`, {}), `training “${w.name}”…`);
  // A CPU training takes over an hour and slows voice: ask first.
  const [cpuAskFor, setCpuAskFor] = React.useState(null);
  const train = (w) => {
    if (cpuTrainer) { setCpuAskFor(w); return undefined; }
    return startTraining(w);
  };
  const trainStop = (w) =>
    guard(() => apiPost(`/api/wake-words/${w.id}/train/stop`, {}), `stopping “${w.name}”…`);
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
  // Deletes confirm first (F-009): a wake word is recorded clips plus a
  // trained model — the most expensive thing on this page to lose.
  const del = useDeleteConfirm((w) =>
    guard(() => apiDelete(`/api/wake-words/${w.id}`), 'wake word removed'));
  const remove = (w) => del.request(w, {
    title: `Delete the wake word “${w.name}”?`,
    body: <div>Its recorded clips and trained model are deleted with it — they can’t be recovered. Satellites keep the model already pushed to them until another one is pushed.</div>,
  });

  return (
    <React.Fragment>
      <WakeTrainerCard status={trainer} error={trainerError} refresh={refreshTrainer} fire={fire}/>

      <Card title="Create a wake word"
            sub="Name it, then record positive clips on a satellite and train an openWakeWord model.">
        <AddWakeWord onAdd={add}/>
      </Card>

      <div style={{ padding: '0 2px 10px', fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
        ⚠ Record clips on the <strong>same mic board</strong> the satellite uses at runtime — the
        XVF3800's on-chip beamforming/AGC reshapes the signal, so a model trained from HAT-recorded
        clips can detect poorly on an XVF array. Training runs on this server once it is turned on
        above. A model trained here is for this household only: its training data's licences
        don't allow sharing it (see <code>scripts/wake_word/README.md</code>).
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
                     onPush={push} onDelete={remove} internetOff={internetOff}
                     trainerOn={trainerOn || !trainer} onTrainStop={trainStop}/>
          ))}
        </Card>
      )}
      {del.node}
      {cpuAskFor && (
        <TrainerConfirmDialog title={`Train “${cpuAskFor.name}” on the CPU?`} busy={false}
                              confirmLabel="Train" onCancel={() => setCpuAskFor(null)}
                              onConfirm={() => { const w = cpuAskFor; setCpuAskFor(null); startTraining(w); }}>
          <div>This server trains on the CPU: about {trainerEst.minutes_per_word} minutes,
            and voice answers more slowly until it finishes.</div>
        </TrainerConfirmDialog>
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

const ServerFingerprint = () => {
  const { data } = useApiObject('/api/config/server-identity');
  const fp = data && data.fingerprint;
  if (!fp) return null;
  return (
    <div style={{ marginTop: 4, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
      <div style={{ fontSize: 12, color: 'var(--fg)', marginBottom: 4 }}>
        This server's fingerprint
      </div>
      <div className="mono" style={{ fontSize: 12, wordBreak: 'break-all', color: 'var(--fg)' }}>
        {fp}
      </div>
      <div style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 4 }}>
        Satellites prepared from this dashboard are built with this string and
        will talk to no other server. It is printed on the card &mdash; if the two
        ever disagree, the satellite is being answered by something else.
      </div>
    </div>
  );
};

// docs/LICENSES.md as GitHub shows it: every model, voice and dataset a
// server downloads, with its licence (domovoi/tests/test_licences_doc.py).
const LICENCES_DOC_URL = 'https://github.com/coders-farm-official/domovoi/blob/main/docs/LICENSES.md';

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
        (web search, media-provider plugins) need the network, and they degrade gracefully instead of
        breaking.
      </div>
      <div>
        Domovoi's code is MIT. The models, voices and wake words this server downloads have their
        own licences; two defaults are not permissive: the wake word <code>hey_jarvis</code> (CC
        BY-NC-SA 4.0, non-commercial) and the answer model <code>llama3.2:3b</code> (the Llama 3.2
        Community License). Fine at home; the list says what each means for anyone passing them
        on: <a href={LICENCES_DOC_URL} target="_blank" rel="noopener noreferrer"
               style={{ color: 'var(--brand)' }}>licences of what Domovoi downloads</a>.
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
        Build / version identifiers live under Configuration → Version.
      </div>
      <ServerFingerprint/>
      <div style={{ marginTop: 4 }}>
        <Button variant="primary" icon="book-open"
                onClick={() => { window.location.hash = 'manual'; }}>
          Open the user manual
        </Button>
      </div>
    </div>
  </Card>
);

/* ---- Devices + queue access -------------------------------------- */
/*
 * Two jobs on one panel because they're the same subject from both ends:
 * what this browser calls itself, and which devices the household lets
 * touch a room's play queue.
 *
 * The device name is what a room queue's "added by" tag shows. It's seeded
 * from the user agent on first contact and editable here; renaming relabels
 * this device's existing queue entries (the queue read prefers the live
 * name), and can't be used to dodge a block, which matches the id too.
 *
 * `onRenamed` is DevicesPanel's refresh of the shared /api/devices list:
 * the Known-devices table and both access pickers read this device's name
 * from it, and nothing pushes the change (there is no devices channel on
 * the state bus), so the PATCH has to be followed by a re-fetch (F-008).
 */
const ThisDeviceCard = ({ fire, onRenamed }) => {
  const deviceId = DeviceIdentity.id();
  const [draft, setDraft] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [serverName, setServerName] = React.useState(DeviceIdentity.name());

  // Register (idempotent) so the row exists even if this is the first page
  // the user lands on, then show whatever name the server has.
  React.useEffect(() => {
    let alive = true;
    DeviceIdentity.register().then((row) => {
      if (alive && row && row.name) { setServerName(row.name); setDraft(row.name); }
    });
    return () => { alive = false; };
  }, []);
  React.useEffect(() => { if (serverName && !draft) setDraft(serverName); }, [serverName]);

  const save = async () => {
    const name = draft.trim();
    if (!name) { fire('name can’t be blank'); return; }
    setSaving(true);
    try {
      const row = await DeviceIdentity.rename(name);
      setServerName(row?.name || name);
      fire(`this device is now “${row?.name || name}”`);
      if (onRenamed) onRenamed();
    } catch (e) {
      fire(`rename failed: ${apiErrorText(e)}`);
    } finally { setSaving(false); }
  };

  const dirty = draft.trim() && draft.trim() !== (serverName || '');
  return (
    <Card title="This device"
          sub="The name a room queue shows next to anything you add from here.">
      <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center',
                    gap: 10, flexWrap: 'wrap' }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter' && dirty) save(); }}
               placeholder={DeviceIdentity.suggestedName()} maxLength={60}
               style={{ font: 'inherit', fontSize: 13, height: 30, flex: '1 1 240px',
                        padding: '0 10px', borderRadius: 'var(--r-sm)',
                        border: '1px solid var(--border)', background: 'var(--card)',
                        color: 'var(--fg)' }}/>
        <Button variant="primary" icon="check" disabled={!dirty || saving} onClick={save}>
          {saving ? 'saving…' : 'rename'}
        </Button>
      </div>
      <div style={{ padding: '0 16px 14px', fontSize: 11, color: 'var(--fg-faint)' }}>
        <span className="mono">{deviceId}</span>
        {' — this browser’s id, also used for podcast and video resume positions.'}
      </div>
    </Card>
  );
};

const QueueAccessCard = ({ fire, deviceList }) => {
  const { items: devices, loading: devicesLoading, refresh: refreshDevices } = deviceList;
  const { items: blocks, loading: blocksLoading, refresh: refreshBlocks } =
    useApiList('/api/music/queue-blocks');
  const { items: nowPlaying } = useApiList('/api/music/now-playing',
                                           { eventTypes: ['music.now_playing.changed'] });
  const rooms = nowPlaying.map((np) => np.room_id);

  const [pick, setPick] = React.useState('');     // device_id
  const [room, setRoom] = React.useState('');     // '' = every room
  const [note, setNote] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [sharing, setSharing] = React.useState(null);   // device_id mid-toggle

  /* Shared screen (V014): Home shows a device marked here without anyone's
   * personal content — calendar titles become "busy", reminders show their
   * room, problems shrink to one line. Admin-only on the server, so the
   * tablet itself can't switch it off. When the row is THIS browser, it
   * re-reads its own record so its Home follows at once. */
  const setShared = async (d, on) => {
    setSharing(d.device_id);
    try {
      await apiPatch(`/api/devices/${encodeURIComponent(d.device_id)}/shared-screen`,
                     { shared_screen: on });
      fire(on ? `${d.name} is now a shared screen` : `${d.name} is a personal device again`);
      refreshDevices();
      if (d.device_id === DeviceIdentity.id() && DeviceIdentity.refresh) DeviceIdentity.refresh();
    } catch (e) {
      reportMutationFailure(fire, 'shared screen', e);
    } finally { setSharing(null); }
  };

  const addBlock = async () => {
    const device = devices.find((d) => d.device_id === pick);
    if (!device) { fire('pick a device to block'); return; }
    setBusy(true);
    try {
      // Send BOTH id and name: the id survives a rename, the name survives a
      // reinstall. The server matches either.
      await apiPost('/api/music/queue-blocks', {
        device_id: device.device_id,
        device_name: device.name,
        room_id: room || null,
        note: note.trim() || null,
      });
      fire(`blocked ${device.name} from ${room || 'every room'}`);
      setPick(''); setNote('');
      refreshBlocks();
    } catch (e) {
      fire(e.status === 409
        ? 'that device is already blocked there'
        : `block failed: ${apiErrorText(e)}`);
    } finally { setBusy(false); }
  };

  const removeBlock = async (b) => {
    try {
      await apiDelete(`/api/music/queue-blocks/${b.id}`);
      fire('block removed');
      refreshBlocks();
    } catch (e) {
      fire(`remove failed: ${apiErrorText(e)}`);
    }
  };

  const selectStyle = {
    font: 'inherit', fontSize: 13, height: 30, padding: '0 8px',
    borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
    background: 'var(--card)', color: 'var(--fg)',
  };

  return (
    <>
      <Card title="Queue access"
            sub="Stop a device from editing a room's play queue — one room, or all of them.">
        {/* Said plainly, because the control is easy to over-trust: a
            block is applied where the dashboard and the app ask — it is
            household policy about those surfaces, not a boundary around
            the room's queue. ADD-3: the core's own queue routes now want
            a household credential, so a blocked device can no longer
            simply talk past the dashboard; it still can't be called a
            defence against someone determined on the LAN. */}
        <div style={{ padding: '10px 16px', display: 'flex', gap: 8, alignItems: 'flex-start',
                      borderBottom: '1px solid var(--border-soft)', background: 'var(--sunken)' }}>
          <Icon name="info" size={13}/>
          <span style={{ fontSize: 11, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
            Blocks apply to the dashboard and the app: those are the surfaces
            that ask who is editing, and a device tells the server who it is.
            This reliably keeps a known device out of a queue; it isn’t a
            defence against someone determined to get in. Changing blocks
            needs admin — so it can’t be undone from the device it was
            applied to.
          </span>
        </div>

        <div style={{ padding: '12px 16px', display: 'flex', gap: 8, alignItems: 'center',
                      flexWrap: 'wrap' }}>
          <select value={pick} onChange={(e) => setPick(e.target.value)} style={selectStyle}>
            <option value="">
              {devicesLoading ? 'loading devices…' : 'pick a device…'}
            </option>
            {devices.map((d) => (
              <option key={d.device_id} value={d.device_id}>
                {d.name} ({d.device_id})
              </option>
            ))}
          </select>
          <select value={room} onChange={(e) => setRoom(e.target.value)} style={selectStyle}>
            <option value="">every room</option>
            {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <input value={note} onChange={(e) => setNote(e.target.value)}
                 placeholder="why (optional)" maxLength={200}
                 style={{ ...selectStyle, flex: '1 1 160px' }}/>
          <Button variant="primary" icon="lock" disabled={busy || !pick} onClick={addBlock}>
            block
          </Button>
        </div>

        {blocksLoading && blocks.length === 0 ? (
          <div style={{ padding: 20, fontSize: 12, color: 'var(--fg-muted)' }}>loading blocks…</div>
        ) : blocks.length === 0 ? (
          <div style={{ padding: '4px 16px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
            No blocks — every device may edit every queue.
          </div>
        ) : (
          <table className="tbl">
            <thead><tr>
              <th>device</th><th>scope</th><th>why</th><th>added</th><th className="actions"></th>
            </tr></thead>
            <tbody>
              {blocks.map((b) => (
                <tr key={b.id}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{b.device_name || '—'}</div>
                    {b.device_id && (
                      <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>
                        {b.device_id}
                      </div>
                    )}
                  </td>
                  <td>
                    {b.room_id
                      ? <Pill tone="idle">{b.room_id}</Pill>
                      : <Pill tone="warn">every room</Pill>}
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{b.note || '—'}</td>
                  <td className="mono">{b.created_at ? relTime(b.created_at) : '—'}</td>
                  <td className="actions">
                    <Button icon="x" onClick={() => removeBlock(b)}>unblock</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title={`Known devices (${devices.length})`}
            sub="Browsers and phones that have introduced themselves, most recent first. Mark a tablet the whole house uses as a shared screen and Home and the page list leave personal content off it: a courtesy for the kitchen, not a lock (the same things are readable without a credential).">
        {devicesLoading && devices.length === 0 ? (
          <div style={{ padding: 20, fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>
        ) : devices.length === 0 ? (
          <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
            Nothing yet. A browser or phone registers itself the first time it
            loads the dashboard.
          </div>
        ) : (
          <table className="tbl">
            <thead><tr>
              <th>name</th><th>id</th><th>platform</th><th>last seen</th><th>shared screen</th>
            </tr></thead>
            <tbody>
              {devices.map((d) => (
                <tr key={d.device_id}>
                  <td style={{ fontWeight: 500 }}>
                    {d.name}
                    {d.device_id === DeviceIdentity.id() && (
                      <span style={{ marginLeft: 6 }}><Pill tone="ok">this one</Pill></span>
                    )}
                  </td>
                  <td className="mono" style={{ fontSize: 11 }}>{d.device_id}</td>
                  <td className="mono" style={{ fontSize: 11 }}>{d.platform || '—'}</td>
                  <td className="mono">{d.last_seen_at ? relTime(d.last_seen_at) : '—'}</td>
                  <td>
                    <input type="checkbox" name="shared-screen"
                           checked={!!d.shared_screen} disabled={sharing === d.device_id}
                           onChange={(e) => setShared(d, e.target.checked)}
                           title={d.shared_screen
                             ? 'shared screen — Home shows no personal content here'
                             : 'mark as a shared screen'}
                           aria-label={`${d.name}: shared screen`}
                           style={{ width: 16, height: 16, cursor: 'pointer' }}/>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div style={{ padding: '10px 16px', display: 'flex', justifyContent: 'flex-end' }}>
          <Button icon="refresh-cw" onClick={() => { refreshDevices(); refreshBlocks(); }}>
            refresh
          </Button>
        </div>
      </Card>
    </>
  );
};

/* Guests: phones and browsers with no household credential (owner decision
 * 2026-10-10). A guest may change the queue of a room that is playing and
 * pause / resume / skip / go back there, "until they get banned by someone
 * with admin privileges". Guests never register, so the server keeps its
 * own short list of the guests that did something (the core records each
 * guest action it lets through) and an admin bans from it. A ban covers
 * the guest's device id OR its address, every room, queue and transport;
 * it never touches a paired device or a signed-in admin. Every call here
 * is admin-only on the server. */
const GuestAccessCard = ({ fire }) => {
  const { items: guests, loading: guestsLoading, refresh: refreshGuests } =
    useApiList('/api/music/guests');
  const { items: bans, loading: bansLoading, refresh: refreshBans } =
    useApiList('/api/music/guest-bans');
  const [busy, setBusy] = React.useState(null);   // the row mid-request

  const refreshAll = () => { refreshGuests(); refreshBans(); };

  const ban = async (g) => {
    setBusy(`${g.device_id}|${g.source}`);
    try {
      await apiPost('/api/music/guest-bans', {
        device_id: g.device_id || null, source: g.source || null,
      });
      fire('guest banned — no queue changes or playback controls in any room');
      refreshAll();
    } catch (e) {
      if (e && e.status === 409) fire('that guest is already banned');
      else reportMutationFailure(fire, 'ban', e);
    } finally { setBusy(null); }
  };

  const unban = async (banId) => {
    setBusy(`ban-${banId}`);
    try {
      await apiDelete(`/api/music/guest-bans/${banId}`);
      fire('ban lifted');
      refreshAll();
    } catch (e) {
      reportMutationFailure(fire, 'unban', e);
    } finally { setBusy(null); }
  };

  const agent = (ua) => {
    if (!ua) return '—';
    const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox'
      : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : null;
    const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS'
      : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'macOS'
      : /Linux/.test(ua) ? 'Linux' : null;
    return browser || os ? [browser, os].filter(Boolean).join(' on ') : ua.slice(0, 40);
  };

  return (
    <Card title="Guests"
          sub="Phones and browsers that aren't paired can change the queue of a room that's playing, and pause, resume, skip or go back there. Ban one here.">
      {/* Said plainly, like the queue blocks above: a guest is known only
          by what its browser says and by its address on the Wi-Fi. */}
      <div style={{ padding: '10px 16px', display: 'flex', gap: 8, alignItems: 'flex-start',
                    borderBottom: '1px solid var(--border-soft)', background: 'var(--sunken)' }}>
        <Icon name="info" size={13}/>
        <span style={{ fontSize: 11, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
          A ban matches the id the guest’s browser sends or its address on your
          network, so it keeps a known phone out — a cleared browser is caught
          by its address for as long as the network gives it the same one. It
          isn’t a defence against someone determined: the household device
          token is the real key. Paired devices and admins are never affected.
          To turn guest access off altogether, use Configuration → Security.
        </span>
      </div>

      <div className="label" style={{ padding: '12px 16px 4px' }}>seen in the last week</div>
      {guestsLoading && guests.length === 0 ? (
        <div style={{ padding: '4px 16px 12px', fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>
      ) : guests.length === 0 ? (
        <div style={{ padding: '4px 16px 12px', fontSize: 12, color: 'var(--fg-muted)' }}>
          No guest has changed the music this week.
        </div>
      ) : (
        <table className="tbl">
          <thead><tr>
            <th>guest</th><th>browser</th><th>last did</th><th>seen</th><th className="actions"></th>
          </tr></thead>
          <tbody>
            {guests.map((g) => {
              const key = `${g.device_id}|${g.source}`;
              return (
                <tr key={key}>
                  <td>
                    <div className="mono" style={{ fontSize: 11 }}>{g.device_id || 'no id sent'}</div>
                    <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>{g.source}</div>
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--fg-muted)' }} title={g.user_agent || ''}>
                    {agent(g.user_agent)}
                  </td>
                  <td style={{ fontSize: 12 }}>
                    {g.last_action || '—'}{g.room_id ? <span style={{ color: 'var(--fg-muted)' }}> · {g.room_id}</span> : null}
                  </td>
                  <td className="mono">{g.last_seen ? relTime(g.last_seen) : '—'}</td>
                  <td className="actions">
                    {g.ban_id != null
                      ? <Button icon="undo-2" disabled={busy === `ban-${g.ban_id}`}
                                onClick={() => unban(g.ban_id)}>unban</Button>
                      : <Button icon="user-x" disabled={busy === key}
                                onClick={() => ban(g)}>ban</Button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div className="label" style={{ padding: '12px 16px 4px' }}>bans</div>
      {bansLoading && bans.length === 0 ? (
        <div style={{ padding: '4px 16px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>
      ) : bans.length === 0 ? (
        <div style={{ padding: '4px 16px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
          No guest is banned.
        </div>
      ) : (
        <table className="tbl">
          <thead><tr>
            <th>id</th><th>address</th><th>why</th><th>since</th><th className="actions"></th>
          </tr></thead>
          <tbody>
            {bans.map((b) => (
              <tr key={b.id}>
                <td className="mono" style={{ fontSize: 11 }}>{b.device_id || '—'}</td>
                <td className="mono" style={{ fontSize: 11 }}>{b.source || '—'}</td>
                <td style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{b.note || '—'}</td>
                <td className="mono">{b.created_at ? relTime(b.created_at) : '—'}</td>
                <td className="actions">
                  <Button icon="undo-2" disabled={busy === `ban-${b.id}`}
                          onClick={() => unban(b.id)}>unban</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
};

/* Files access — the Files surface is open on the LAN (browse, download,
 * upload, move, import; only delete needs admin), so this is the control
 * that takes WRITES away from one device without making every phone sign
 * in. Same device model and the same honesty note as the queue blocks. */
const FilesAccessCard = ({ fire, deviceList }) => {
  const { items: devices, loading: devicesLoading } = deviceList;
  const { items: blocks, loading: blocksLoading, refresh: refreshBlocks } =
    useApiList('/api/files/device-blocks');

  const [pick, setPick] = React.useState('');     // device_id
  const [note, setNote] = React.useState('');
  const [busy, setBusy] = React.useState(false);

  const addBlock = async () => {
    const device = devices.find((d) => d.device_id === pick);
    if (!device) { fire('pick a device to block'); return; }
    setBusy(true);
    try {
      // Both id and name, like the queue blocks: the id survives a rename,
      // the name survives a reinstall. The server matches either.
      await apiPost('/api/files/device-blocks', {
        device_id: device.device_id,
        device_name: device.name,
        note: note.trim() || null,
      });
      fire(`${device.name} can no longer change files`);
      setPick(''); setNote('');
      refreshBlocks();
    } catch (e) {
      fire(e.status === 409
        ? 'that device is already blocked'
        : `block failed: ${apiErrorText(e)}`);
    } finally { setBusy(false); }
  };

  const removeBlock = async (b) => {
    try {
      await apiDelete(`/api/files/device-blocks/${b.id}`);
      fire('block removed');
      refreshBlocks();
    } catch (e) {
      fire(`remove failed: ${apiErrorText(e)}`);
    }
  };

  const selectStyle = {
    font: 'inherit', fontSize: 13, height: 30, padding: '0 8px',
    borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
    background: 'var(--card)', color: 'var(--fg)',
  };

  // The subtitle is not a list of buttons. A list goes stale the moment
  // the block reaches one more of them, and this one did: saving a
  // document is refused too. It says what the block DOES.
  return (
    <Card title="Files access"
          sub="Stop a device from changing anything in the libraries — saving a document, uploading, moving, importing. It can still browse and download; deleting always needs admin.">
      <div style={{ padding: '10px 16px', display: 'flex', gap: 8, alignItems: 'flex-start',
                    borderBottom: '1px solid var(--border-soft)', background: 'var(--sunken)' }}>
        <Icon name="info" size={13}/>
        <span style={{ fontSize: 11, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
          Same caveat as queue access: blocks apply to the dashboard and the
          app, and a device tells the server who it is. This reliably keeps a
          known device from filling the libraries; it isn’t a defence against
          someone determined. Changing blocks needs admin.
        </span>
      </div>

      <div style={{ padding: '12px 16px', display: 'flex', gap: 8, alignItems: 'center',
                    flexWrap: 'wrap' }}>
        <select value={pick} onChange={(e) => setPick(e.target.value)} style={selectStyle}>
          <option value="">
            {devicesLoading ? 'loading devices…' : 'pick a device…'}
          </option>
          {devices.map((d) => (
            <option key={d.device_id} value={d.device_id}>
              {d.name} ({d.device_id})
            </option>
          ))}
        </select>
        <input value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="why (optional)" maxLength={200}
               style={{ ...selectStyle, flex: '1 1 160px' }}/>
        <Button variant="primary" icon="lock" disabled={busy || !pick} onClick={addBlock}>
          block
        </Button>
      </div>

      {blocksLoading && blocks.length === 0 ? (
        <div style={{ padding: 20, fontSize: 12, color: 'var(--fg-muted)' }}>loading blocks…</div>
      ) : blocks.length === 0 ? (
        <div style={{ padding: '4px 16px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
          No blocks — every device may upload, move and import.
        </div>
      ) : (
        <table className="tbl">
          <thead><tr>
            <th>device</th><th>why</th><th>added</th><th className="actions"></th>
          </tr></thead>
          <tbody>
            {blocks.map((b) => (
              <tr key={b.id}>
                <td>
                  <div style={{ fontWeight: 500 }}>{b.device_name || '—'}</div>
                  {b.device_id && (
                    <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>
                      {b.device_id}
                    </div>
                  )}
                </td>
                <td style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{b.note || '—'}</td>
                <td className="mono">{b.created_at ? relTime(b.created_at) : '—'}</td>
                <td className="actions">
                  <Button icon="x" onClick={() => removeBlock(b)}>unblock</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
};

/* Set a custom household token.
 *
 * NOT DeleteConfirmDialog: that component is a confirm, not a form — its
 * `busy` flag is the only way to disable the confirm button and it also
 * disables Cancel and the backdrop, its in-flight label is hard-coded
 * "Deleting…", and its confirm button is unconditionally danger-red. The
 * house idiom for "a modal with one field and a primary action" is the
 * cal-modal family in styles.css, of which PairModal (components.jsx) is
 * the twin. Zero new CSS. */
const SetTokenDialog = ({ open, onCancel, onSave, busy, error }) => {
  const [value, setValue] = React.useState('');
  // The value a server refusal belongs to. The error is the card's state
  // and nothing clears it until the next submit, so without this the red
  // line stays pinned under the field describing a token the person has
  // already typed over.
  const [rejected, setRejected] = React.useState(null);
  React.useEffect(() => { if (open) { setValue(''); setRejected(null); } }, [open]);
  if (!open) return null;
  const stored = value.trim();
  const tooShort = stored.length < 12;
  const submit = () => { if (!tooShort && !busy) { setRejected(stored); onSave(stored); } };
  /* WHICH RULE will match this token, live as it is typed.
   *
   * The server accepts the exact stored value always, and its canonical
   * form (normalize_device_token) only when the stored token IS its own
   * canonical form. `normalize` lowercases and collapses every run of
   * spaces/underscores/hyphens to one hyphen, so `$$bills_yall-market!!1999`
   * is NOT canonical: it is matched character for character, and the same
   * token with the underscore typed as a hyphen is refused with nothing on
   * screen to explain why. That refusal is correct and must not change —
   * what must not happen is springing it on the person who set it.
   *
   * Auth.normalizeDeviceToken is the same function as the server's
   * (domovoi/admin_auth.normalize_device_token) and the two are held to
   * each other character for character by
   * test_web_settings_household_token.py: a disagreement here would make
   * this line lie about how the token behaves, which is worse than saying
   * nothing. `null` = auth.js is not on the page at all (a JSX harness
   * loading components.jsx alone) — then say nothing rather than guess. */
  const forgiving = (() => {
    if (!stored) return null;
    try { return Auth.normalizeDeviceToken(stored) === stored; } catch { return null; }
  })();
  return (
    <div className="cal-modal-bg" onClick={busy ? undefined : onCancel}>
      <div className="cal-modal" role="dialog" aria-modal="true"
           aria-label="Set a custom household token" onClick={(e) => e.stopPropagation()}>
        <div className="cal-modal-head">
          <div className="ttl">Set a custom household token</div>
          <IconButton name="x" onClick={onCancel}/>
        </div>
        <div className="cal-modal-body">
          <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
            Replace the current household token with one of your own. At
            least 12 characters — letters, numbers, punctuation and spaces
            all work.
          </div>
          <div className="field">
            <label>new household token</label>
            <input className="cal-inp mono" value={value} autoFocus
                   placeholder="at least 12 characters"
                   data-testid="set-token-input"
                   onChange={(e) => setValue(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}/>
            {/* The counter counts the STORED form: outer whitespace is
                trimmed before it is saved, so it must be trimmed here too
                or the count lies about whether the button will unlock. */}
            <div className="hint">{stored.length} characters.</div>
            {/* Never blocks Save — it is what the token will DO, not a
                complaint about it. */}
            {forgiving !== null && (
              <div className="hint" data-testid="set-token-rule">
                {forgiving
                  ? 'Typing this back is forgiving — capitals, spaces and underscores all match.'
                  : 'This one must be typed exactly, character for character.'}
              </div>
            )}
          </div>
          {error && stored === rejected && <div className="err">{error}</div>}
          <div style={{ fontSize: 11, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
            A token you chose is easier to remember and easier to guess.
            Domovoi slows down repeated wrong guesses from the same device,
            but it will not stop you picking something short or obvious.
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
            Domovoi keeps this token in plain text so it can show it to you
            here, and it appears in the server's request log when the
            dashboard loads media. Do not reuse a password from anywhere else.
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
            Saving replaces the current token: every other phone and browser
            in the household must be given the new one before it can change
            anything. This browser is re-paired automatically. If Domovoi
            has been updated but not restarted, restart it first: only a
            server running this version understands a token you chose.
          </div>
        </div>
        <div className="cal-modal-foot">
          <Button onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="primary" icon="key" disabled={tooShort || busy} onClick={submit}>
            {busy ? 'saving…' : 'Save token'}
          </Button>
        </div>
      </div>
    </div>
  );
};

/* ---- Household token (the device tier) ----------------------------
 * The one secret every household device presents (X-Device-Token) for
 * ordinary actions. Shown ONLY to the admin sign-in in this tab (the
 * Bearer; the server refuses the dashboard cookie alone since 2026-10-14)
 * because it is what a new phone or browser is enrolled with. A view-only
 * tab (the cookie, past the reload window) gets the card with "sign in
 * again" and no fetch; a viewer who is not signed in sees nothing here,
 * and nothing is fetched for them (a 401 on this GET would otherwise pop
 * the login modal at everyone who opens the tab).
 *
 * Copy hands it to the clipboard; Set… replaces it with one the admin
 * chose (SetTokenDialog above); Rotate mints a generated phrase. Both
 * writes re-pair THIS browser and tell the admin that every other device
 * must pair again. */
const HouseholdTokenCard = ({ fire }) => {
  const [, force] = React.useReducer((n) => n + 1, 0);
  React.useEffect(() => {
    if (typeof Auth === 'undefined') return;
    try { return Auth.subscribe(force); } catch { /* auth.js absent */ }
  }, []);
  // Ask the server once when nobody has probed yet, so a cookie session
  // (a reload while logged in) renders the card too.
  React.useEffect(() => {
    try { if (typeof Auth !== 'undefined' && !Auth.status) Auth.refreshStatus(); } catch {}
  }, []);

  let admin = false;
  // The token is read with the admin sign-in in this tab only (the
  // Bearer): since 2026-10-14 the server refuses the cookie alone, which
  // every other service on the box receives too. A view-only tab — a
  // reload past the reload window — shows the card and asks for the
  // sign-in rather than reading.
  let live = false;
  try {
    live = typeof Auth !== 'undefined' && Auth.isLoggedIn();
    admin = typeof Auth !== 'undefined'
      && (live || !!(Auth.status && Auth.status.authenticated));
  } catch {}

  const { data, error, refresh } = useApiObject(live ? '/api/auth/device-token' : null);
  const [rotating, setRotating] = React.useState(false);
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [rotated, setRotated] = React.useState(false);
  const [setOpen, setSetOpen] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [setError, setSetError] = React.useState(null);

  if (!admin) return null;

  const token = data && data.token;
  const header = (data && data.header) || 'X-Device-Token';
  const thisPaired = (() => {
    try { return !!token && Auth.deviceToken && Auth.deviceToken() === token; } catch { return false; }
  })();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token);
      fire('household token copied');
    } catch {
      fire('copy failed — select the token and copy it by hand');
    }
  };

  const rotate = async () => {
    setConfirmOpen(false);
    setRotating(true);
    try {
      const out = await apiPost('/api/auth/device-token/rotate');
      if (out && out.token) {
        try { Auth.pair(out.token); } catch {}
        setRotated(true);
        fire('household token rotated — every other browser and phone must pair again');
      }
      refresh();
    } catch (e) {
      if (!isAuthFailure(e)) fire(`rotate failed: ${apiErrorText(e)}`);
    } finally { setRotating(false); }
  };

  const saveToken = async (value) => {
    setSetError(null);
    setSaving(true);
    try {
      const out = await apiPost('/api/auth/device-token', { token: value });
      if (out && out.token) {
        try { Auth.pair(out.token); } catch {}
        setRotated(true);
        setSetOpen(false);
        fire('household token set — every other browser and phone must pair again');
      }
      refresh();
    } catch (e) {
      // Inline, next to the field: the server's message is the actionable
      // one ("at least 12 characters"), and a toast would take it away
      // while the dialog is still standing.
      if (!isAuthFailure(e)) setSetError(apiErrorText(e));
    } finally { setSaving(false); }
  };

  return (
    <Card title="Household token"
          sub="What a phone or browser presents to prove it belongs to this household. Share it only with devices you own.">
      {!live ? (
        <div className="household-token-view-only"
             style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)',
                      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ flex: 1 }}>
            This tab is view only, and the household token is shown only to the
            admin sign-in. Sign in again to see or change it.
          </span>
          <Button variant="primary" icon="key"
                  onClick={() => { try { Auth.openModal(); } catch { /* auth.js absent */ } }}>
            sign in again
          </Button>
        </div>
      ) : error ? (
        <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
          {error.status === 501
            ? 'Available once first-run admin setup is complete.'
            : `couldn't load the token: ${apiErrorText(error)}`}
        </div>
      ) : !token ? (
        <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>
      ) : (
        <>
          <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center',
                        gap: 10, flexWrap: 'wrap' }}>
            <code className="mono" data-testid="household-token"
                  style={{ fontSize: 13, padding: '6px 10px', borderRadius: 'var(--r-sm)',
                           border: '1px solid var(--border)', background: 'var(--sunken)',
                           userSelect: 'all', overflowWrap: 'anywhere', flex: '1 1 320px' }}>
              {token}
            </code>
            <Button icon="copy" onClick={copy}>copy</Button>
            <Button icon="key" disabled={saving} onClick={() => { setSetError(null); setSetOpen(true); }}>
              set…
            </Button>
            <Button icon="refresh-cw" disabled={rotating} onClick={() => setConfirmOpen(true)}>
              {rotating ? 'rotating…' : 'rotate'}
            </Button>
          </div>
          <div style={{ padding: '0 16px 14px', fontSize: 11, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
            Sent as the <span className="mono">{header}</span> header. On a phone: Settings → Connection → Household token.
            {thisPaired
              ? ' This browser is paired with it.'
              : ' This browser is not paired with it yet — log in again or paste it into the pairing prompt.'}
            {' '}<span className="mono">set…</span> replaces it with one you choose (12+ characters);
            {' '}<span className="mono">rotate</span> generates a new phrase.
            {rotated && ' Changed: the previous token no longer works anywhere.'}
          </div>
        </>
      )}
      <DeleteConfirmDialog open={confirmOpen} title="Rotate the household token?" icon="refresh-cw"
                           confirmLabel="Rotate" onCancel={() => setConfirmOpen(false)} onConfirm={rotate}>
        <div>
          Every phone and browser in the household will need the new token
          before it can change things again. This browser is re-paired
          automatically.
        </div>
      </DeleteConfirmDialog>
      <SetTokenDialog open={setOpen} busy={saving} error={setError}
                      onCancel={() => { setSetOpen(false); setSetError(null); }}
                      onSave={saveToken}/>
    </Card>
  );
};

/* This device's phone bar: the tabs along the bottom when this browser is
 * phone-width (components.jsx PhoneBarStore / phoneBarRoutes). Kept in
 * this browser only, so it needs no sign-in and touches nothing on the
 * server — which is why it is a tab of its own (Settings → Phone bar)
 * rather than a card under Devices, whose other cards ask an admin to
 * sign in. Home's "everything" grid links here. Offers what a launcher
 * may show on this screen (navItemsFor): plugin pages too, and on a shared
 * screen never the personal pages, whatever was saved. */
const PhoneBarCard = ({ fire }) => {
  const shared = useSharedScreen();
  const manifest = window.DomovoiPluginManifest || { plugins: [] };
  const items = navItemsFor(manifest, { shared });
  const saved = usePhoneBar();
  const bar = phoneBarRoutes(items, saved);
  const byRoute = {};
  items.forEach((it) => { byRoute[it.route] = it; });
  const [unkept, setUnkept] = React.useState(false);
  const label = (r) => (byRoute[r] && byRoute[r].label) || r;
  const save = (next) => {
    if (next === bar) return;
    setUnkept(!PhoneBarStore.set(next));
  };
  const reset = () => {
    setUnkept(!PhoneBarStore.set(null));
    fire('phone bar back to home · music · videos · chat');
  };
  const glyph = (it) => (it && !it.core ? <PluginNavIcon src={it.iconSrc}/> : <Icon name={(it && it.icon) || 'puzzle'} size={16}/>);
  const spare = items.filter((it) => !bar.includes(it.route));
  const full = bar.length >= PHONE_BAR_MAX;
  return (
    <Card title="Phone bar"
          sub={`The tabs along the bottom of this browser at phone width, left to right. Home is always first; ${PHONE_BAR_MIN} to ${PHONE_BAR_MAX} tabs. Everything else is on Home's “everything” grid.`}>
      <div className="phone-bar-edit" style={{ padding: '8px 16px 4px' }}>
        {bar.map((r, i) => (
          <div key={r} className="phone-bar-row" data-route={r}
               style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 44,
                        borderTop: i ? '1px solid var(--border-soft)' : 'none' }}>
            <span className="mono" style={{ width: 16, fontSize: 11, color: 'var(--fg-faint)' }}>{i + 1}</span>
            {glyph(byRoute[r])}
            <span style={{ flex: 1, minWidth: 0, fontSize: 13 }}>{label(r)}</span>
            {r === 'home' ? (
              <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>always first</span>
            ) : (
              <>
                <IconButton name="chevron-up" title={`move ${label(r)} left`} disabled={i <= 1}
                            onClick={() => save(phoneBarEdit(bar, 'left', r))}/>
                <IconButton name="chevron-down" title={`move ${label(r)} right`} disabled={i >= bar.length - 1}
                            onClick={() => save(phoneBarEdit(bar, 'right', r))}/>
                <IconButton name="x" title={`take ${label(r)} off the bar`} disabled={bar.length <= PHONE_BAR_MIN}
                            onClick={() => save(phoneBarEdit(bar, 'remove', r))}/>
              </>
            )}
          </div>
        ))}
      </div>
      {spare.length > 0 && (
        <div style={{ padding: '6px 16px 12px' }}>
          <div className="label" style={{ marginBottom: 6 }}>
            {full ? `the bar is full (${PHONE_BAR_MAX}) · take one off to add another` : 'add to the bar'}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {spare.map((it) => (
              <Button key={it.route} variant="secondary" icon="plus" disabled={full}
                      title={`add ${it.label} to the bar`}
                      onClick={() => save(phoneBarEdit(bar, 'add', it.route))}>
                {it.label}
              </Button>
            ))}
          </div>
        </div>
      )}
      <div style={{ padding: '0 16px 14px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span className="mono phone-bar-preview" style={{ flex: 1, minWidth: 0, fontSize: 11, color: 'var(--fg-muted)' }}>
          {bar.map((r) => label(r).toLowerCase()).join(' · ')}
        </span>
        <Button variant="ghost" icon="rotate-ccw" disabled={saved == null} onClick={reset}>
          default
        </Button>
      </div>
      <div style={{ padding: '0 16px 14px', fontSize: 11, color: 'var(--fg-faint)' }}>
        {unkept
          ? 'This browser can’t keep it (private window or blocked storage): it lasts until you reload.'
          : 'Kept in this browser only — another phone keeps its own.'}
        {shared && ' A shared screen leaves people, chat, files and news off, whatever the bar says.'}
      </div>
    </Card>
  );
};

// Settings → Phone bar.
const PhoneBarPanel = () => {
  const [fire, toastNode] = useToast();
  return <><PhoneBarCard fire={fire}/>{toastNode}</>;
};

/* Get the Domovoi app (2026-10-15): this server hands its household's
 * Android phones the app (domovoi/android_app.py, the join page at
 * <server>/join). Scan the code with the phone's camera, or open the link
 * on it. Until a first release exists, the card says so and offers nothing;
 * until the server holds it, an admin downloads it here (AppOnServer). */
const GetTheAppCard = () => {
  const admin = useAdminSignedIn();
  const [app, setApp] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    let alive = true;
    apiGet('/api/devices/android-app', { quiet: true })
      .then(r => { if (alive) setApp(r); })
      .catch(e => { if (alive) setErr(apiErrorText(e)); });
    return () => { alive = false; };
  }, [tick]);
  const note = (t) => <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>{t}</div>;
  const sub = 'For this home’s Android phones and tablets (Android 8 or newer): from this server, no store.';
  if (err) return <Card title="Get the Domovoi app" sub={sub}>{note(`Couldn’t ask the server: ${err}`)}</Card>;
  if (!app) return <Card title="Get the Domovoi app" sub={sub}>{note('loading…')}</Card>;
  if (app.unreachable) return <Card title="Get the Domovoi app" sub={sub}>{note('The Domovoi server isn’t answering right now.')}</Card>;
  if (!app.release) {
    return <Card title="Get the Domovoi app" sub={sub}>{note('No app release yet. When there is one, this server offers it here.')}</Card>;
  }
  if (!app.ready) {
    // Downloaded only on an admin's press (owner decision 2026-10-15).
    return (
      <Card title="Get the Domovoi app" sub={sub}>
        {admin
          ? <div style={{ padding: '12px 16px' }}><AppOnServer onReady={() => setTick(t => t + 1)}/></div>
          : note(`The app isn’t on this server yet (Domovoi ${app.release.version_name}). Ask whoever runs Domovoi to press “Download the app to this server” here, signed in as admin.`)}
      </Card>
    );
  }
  return (
    <Card title="Get the Domovoi app" sub={sub}>
      <div className="get-app" style={{ padding: '12px 16px', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {app.join_url && <SpareQr text={app.join_url}/>}
        <div style={{ flex: '1 1 220px', minWidth: 0, fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.6 }}>
          {app.join_url ? (
            <>
              <div>Scan this with the phone’s camera, or open this link on the phone:</div>
              <div className="mono" style={{ wordBreak: 'break-all', color: 'var(--fg)', margin: '4px 0 8px' }}>{app.join_url}</div>
              <div>The page there downloads Domovoi {app.release.version_name} and shows how to install it. Then open the app: it finds this server on your Wi-Fi.</div>
            </>
          ) : (
            <div role="alert" style={{ color: 'var(--warn)' }}>{app.join_problem}</div>
          )}
        </div>
      </div>
    </Card>
  );
};

const DevicesPanel = () => {
  const [fire, toastNode] = useToast();
  // ONE devices list for the whole tab. Each card used to fetch its own,
  // so renaming this device updated the text box and nothing else until
  // the table's refresh button was pressed (F-008): the write landed, the
  // tab lied about it. Renames now refresh the list every card reads.
  const deviceList = useApiList('/api/devices');
  return (
    <>
      <ThisDeviceCard fire={fire} onRenamed={deviceList.refresh}/>
      <GetTheAppCard/>
      <HouseholdTokenCard fire={fire}/>
      <QueueAccessCard fire={fire} deviceList={deviceList}/>
      <GuestAccessCard fire={fire}/>
      <FilesAccessCard fire={fire} deviceList={deviceList}/>
      {toastNode}
    </>
  );
};

/* ============================================================ */
/* Recordings — opt-in command recordings, reviewed by an admin */
/* ============================================================ */
/* Rooms an admin opted in (Settings → Satellites, the room's drawer →
 * command recordings) keep each wake-word or follow-up command, audio
 * plus transcript, for tuning when Domovoi stops listening. Here an admin
 * plays them back, says whether the capture ended at the right moment,
 * and deletes them. Every call is the server's security tier (admin only,
 * 501 before setup). */

const CAPTURE_LABEL_ORDER = ['cut_off', 'fine', 'waited_too_long'];

const CaptureRow = ({ c, labels, playing, busy, onPlay, onLabel, onDelete }) => {
  const t = c.timings || {};
  const secs = c.duration_ms != null ? `${(c.duration_ms / 1000).toFixed(1)} s` : '—';
  return (
    <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)',
                  display: 'flex', alignItems: 'flex-start', gap: 10 }}>
      <IconButton name={playing ? 'square' : 'play'} title={playing ? 'stop' : 'play'}
                  onClick={() => onPlay(c)}/>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                      fontSize: 11, color: 'var(--fg-muted)' }}>
          <span className="room-chip">{c.room_id}</span>
          <span title={c.captured_at}>{relTime(c.captured_at)}</span>
          <span className="mono">
            {(c.trigger || '').replace(/_/g, ' ')} · {secs} · {c.end_reason ? c.end_reason.replace(/_/g, ' ') : 'end not reported'}
          </span>
          {t.total_ms != null && <span className="mono">· reply after {t.total_ms} ms</span>}
        </div>
        <div style={{ fontSize: 13, marginTop: 6, overflowWrap: 'anywhere' }}>{c.transcript}</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 2 }}>
          {c.matched_handler || '—'}{c.matched_path ? ` · ${c.matched_path}` : ''}{c.interrupted ? ' · interrupted' : ''}
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 8 }}>
          {CAPTURE_LABEL_ORDER.map((k) => (
            <Button key={k} variant={c.label === k ? 'primary' : 'ghost'} disabled={busy}
                    onClick={() => onLabel(c, c.label === k ? null : k)}>
              {labels[k] || k}
            </Button>
          ))}
        </div>
      </div>
      <IconButton name="trash-2" title="delete recording" onClick={() => onDelete(c)}/>
    </div>
  );
};

const CapturesPanel = () => {
  const [fire, toastNode] = useToast();
  const admin = useAdminSignedIn();
  // Read with the admin sign-in in this tab only: the server refuses the
  // cookie alone (2026-10-14), so a view-only tab asks for the sign-in.
  const live = adminReloadState().live;
  const { data, loading, error, refresh } = useApiObject(live ? '/api/captures' : null);
  const [room, setRoom] = React.useState('');
  const [only, setOnly] = React.useState('all');   // all | unlabelled | a label key
  const [playing, setPlaying] = React.useState(null);
  const [busy, setBusy] = React.useState('');
  const audioRef = React.useRef(null);

  const stopAudio = () => {
    const cur = audioRef.current;
    audioRef.current = null;
    if (cur) { cur.audio.pause(); URL.revokeObjectURL(cur.url); }
  };
  React.useEffect(() => stopAudio, []);

  // Fetched with the admin's credentials and played from memory: an <audio
  // src> can't carry a Bearer, and a recording must never ride a URL token.
  const play = async (c) => {
    stopAudio();
    if (playing === c.id) { setPlaying(null); return; }
    setPlaying(c.id);
    try {
      const r = await apiFetchRaw(
        `/api/captures/clips/${encodeURIComponent(c.room_id)}/${encodeURIComponent(c.id)}/audio`);
      const url = URL.createObjectURL(await r.blob());
      const audio = new Audio(url);
      audioRef.current = { audio, url };
      const done = () => {
        if (audioRef.current && audioRef.current.audio === audio) stopAudio();
        setPlaying((p) => (p === c.id ? null : p));
      };
      audio.onended = done;
      await audio.play();
    } catch (e) {
      if (!isAuthFailure(e)) fire('playback failed');
      stopAudio();
      setPlaying(null);
    }
  };

  const setLabel = async (c, label) => {
    setBusy(c.id);
    try {
      await apiPatch(`/api/captures/clips/${encodeURIComponent(c.room_id)}/${encodeURIComponent(c.id)}`, { label });
      await refresh();
    } catch (e) {
      reportMutationFailure(fire, 'label', e);
    } finally { setBusy(''); }
  };

  const delConfirm = useDeleteConfirm(async (c) => {
    if (playing === c.id) { stopAudio(); setPlaying(null); }
    try {
      await apiDelete(`/api/captures/clips/${encodeURIComponent(c.room_id)}/${encodeURIComponent(c.id)}`);
      fire('recording deleted');
      await refresh();
    } catch (e) {
      reportMutationFailure(fire, 'delete', e);
    }
  });

  const note = (text) => (
    <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>{text}</div>
  );
  const sub = 'What opted-in rooms said to Domovoi, kept to tune when it stops listening. '
    + 'Turn a room on or off in its drawer under Settings → Satellites.';

  if (!admin) {
    return <Card title="Command recordings" sub={sub}>{note('Admins only. Sign in to review recordings.')}</Card>;
  }
  if (!live) {
    return (
      <Card title="Command recordings" sub={sub}>
        <div className="captures-view-only"
             style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)',
                      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ flex: 1 }}>
            This tab is view only, and recordings are shown only to the admin
            sign-in. Sign in again to review them.
          </span>
          <Button variant="primary" icon="key"
                  onClick={() => { try { Auth.openModal(); } catch { /* auth.js absent */ } }}>
            sign in again
          </Button>
        </div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="Command recordings" sub={sub}>
        {note(error.status === 501
          ? 'Available once first-run admin setup is complete.'
          : `couldn't load recordings: ${apiErrorText(error)}`)}
      </Card>
    );
  }
  if (loading || !data) return <Card title="Command recordings" sub={sub}>{note('loading…')}</Card>;

  const labels = data.labels || {};
  const rooms = data.rooms || [];
  const all = data.captures || [];
  const shown = all.filter((c) => (!room || c.room_id === room)
    && (only === 'all' || (only === 'unlabelled' ? !c.label : c.label === only)));
  const count = `${data.count} recording${data.count === 1 ? '' : 's'}`;

  return (
    <>
      <Card title="Command recordings" sub={sub}
            action={<IconButton name="refresh-cw" title="refresh" onClick={() => refresh()}/>}>
        <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            {count} · {fmtBytes(data.bytes)} of {fmtBytes(data.cap_bytes)} · each kept {data.retention_days} days, oldest go first at the cap
          </div>
          {data.problem && (
            <div style={{ fontSize: 12, color: 'var(--warn)' }}>
              not recording anywhere: {data.problem}
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            {rooms.length === 0 && <span style={{ fontSize: 12, color: 'var(--fg-faint)' }}>no room is recording</span>}
            {rooms.map((r) => (
              <span key={r.room_id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                <span className="room-chip">{r.room_id}</span>
                {r.enabled ? <CaptureChip short/> : <span style={{ color: 'var(--fg-faint)' }}>off</span>}
                <span className="mono" style={{ color: 'var(--fg-muted)' }}>{r.count}</span>
              </span>
            ))}
          </div>
          {all.length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <select value={room} onChange={(e) => setRoom(e.target.value)} style={_wakeFieldStyle}>
                <option value="">every room</option>
                {rooms.map((r) => <option key={r.room_id} value={r.room_id}>{r.room_id}</option>)}
              </select>
              <select value={only} onChange={(e) => setOnly(e.target.value)} style={_wakeFieldStyle}>
                <option value="all">every label</option>
                <option value="unlabelled">not labelled yet</option>
                {CAPTURE_LABEL_ORDER.map((k) => <option key={k} value={k}>{labels[k] || k}</option>)}
              </select>
            </div>
          )}
        </div>
        {shown.length === 0 ? (
          <Empty title={all.length ? 'nothing matches' : 'nothing recorded yet'}
                 sub={all.length ? undefined : 'turn recording on for a room in its drawer, under Settings → Satellites'}/>
        ) : shown.map((c) => (
          <CaptureRow key={c.id} c={c} labels={labels} playing={playing === c.id}
                      busy={busy === c.id} onPlay={play} onLabel={setLabel}
                      onDelete={(x) => delConfirm.request(x, {
                        title: 'Delete this recording?',
                        body: <div>The audio and its transcript are removed from the server now.</div>,
                      })}/>
        ))}
      </Card>
      {delConfirm.node}
      {toastNode}
    </>
  );
};

/* ============================================================ */
/* Settings shell                                               */
/* ============================================================ */

// Each tab's address is #settings/<slug> (the id where there is no slug;
// the id opens the tab too). useRouteTab, components.jsx. The slugs are
// public: bookmarks, the docs and the Windows installer's "Open Domovoi"
// (#settings/configuration/setup) use them, so a slug is never renamed.
const SETTINGS_TABS = [
  { id: 'about', label: 'About' },            // informational intro / manual link
  { id: 'greetings', label: 'Greetings' },
  { id: 'voices', label: 'Voices' },
  { id: 'wakewords', slug: 'wake-words', label: 'Wake Words' },
  { id: 'recordings', label: 'Recordings' },  // opt-in command recordings (admins)
  { id: 'devices', label: 'Devices' },        // device names + queue / files access
  { id: 'phonebar', slug: 'phone-bar', label: 'Phone bar' },  // this browser's phone tabs (no sign-in: kept in the browser)
  { id: 'satellites', label: 'Satellites' },  // the rooms' Pis (was a nav route, #satellites still opens it)
  { id: 'models', label: 'Models' },          // model-management hub (was a nav route)
  { id: 'internet', label: 'Internet' },      // the INTERNET_ACCESS answer (its one home)
  { id: 'config', slug: 'configuration', label: 'Configuration' },   // last tab, by request
];
// A bare #settings opens About: what Domovoi is, and the way to the manual.
const SETTINGS_DEFAULT_TAB = 'about';

const SETTINGS_SUB = {
  about: 'What Domovoi is — and a link to the user manual.',
  greetings: 'Lines a satellite says when it hears the wake word, before it listens.',
  voices: 'Domovoi’s voice, how it says words, and the voice registry — each satellite speaks in one.',
  wakewords: 'Train + manage custom wake words; record clips on a satellite.',
  recordings: 'Commands kept from opted-in rooms to tune when Domovoi stops listening — admins only.',
  devices: 'Name this device, the household token, shared screens, and who may edit a room’s play queue.',
  phonebar: 'The tabs along the bottom of this browser at phone width — kept in this browser only.',
  satellites: 'The Pi in each room: status, approvals and adoption, announcements, and each room’s drawer.',
  models: "What's active in each role, install more, and the host hardware readout.",
  internet: 'Will this Domovoi have internet? It sets the defaults of the online extras.',
  config: 'Editable domovoi configuration.',
};

const SettingsPage = () => {
  // The tab is the address's (#settings/<tab>): clicking one writes it, a
  // link to one (the Configuration tab's "Settings → Internet", a "needs
  // internet" note, Home's rows) opens it, also while this page is on
  // screen, and Back goes to the tab before.
  const [tab, setTab, marks] = useRouteTab('settings', SETTINGS_TABS, SETTINGS_DEFAULT_TAB);

  return (
    <div className="page">
      <PageHeader title="Settings" sub={SETTINGS_SUB[tab]}/>
      <Tabs tabs={SETTINGS_TABS} value={tab} onChange={setTab}/>
      {tab === 'about' && <AboutPanel/>}
      {tab === 'greetings' && <GreetingsPanel/>}
      {tab === 'voices' && <VoicesPanel/>}
      {tab === 'wakewords' && <WakeWordsPanel/>}
      {tab === 'recordings' && <CapturesPanel/>}
      {tab === 'devices' && <DevicesPanel/>}
      {tab === 'phonebar' && <PhoneBarPanel/>}
      {tab === 'satellites' && <SatellitesPanel/>}
      {tab === 'models' && <ModelsPanel/>}
      {tab === 'internet' && <InternetPanel/>}
      {tab === 'config' && <ConfigPanel setupAsked={marks[0] === 'setup'}/>}
    </div>
  );
};

window.SettingsPage = SettingsPage;
