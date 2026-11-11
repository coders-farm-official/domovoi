/* Settings → Household → Media folders (owner, 2026-11-11).
 *
 * Where Domovoi keeps the household's music, videos, pictures, documents,
 * audiobooks and podcasts, as a person can find them, and what can be done
 * about them from here (domovoi/media_folders.py):
 *
 *   • Windows install: each folder's Windows path (C:\Users\Public\Domovoi\
 *     Music, not the /srv/media/music mount inside Domovoi's private Linux
 *     system). From a browser on the Domovoi PC, "open on the Domovoi PC"
 *     (the tray opens File Explorer there); from any other device the path
 *     with a copy button and a line saying where it is. Moving the folders
 *     (a new folder on that PC; Domovoi restarts; files stay put).
 *   • Linux server: the path itself, copy only (a server opens no
 *     windows), and Music and Videos change in place as before, checked by
 *     the server (the folder must be there and readable).
 *
 * A control that can't work here is not shown; the reason is.
 *
 * Data:
 *   GET  /api/config/media-folders                  · the folders, host kind, on_this_pc
 *   POST /api/config/media-folders/open             · { kind } → the tray opens it
 *   POST /api/config/media-folders/location         · { root } → the PC moves them
 *   POST /api/config/media-folders/location/cancel  · withdraw a waiting move
 *   PATCH /api/config/editable                      · { changes: { music_dir } } (Linux)
 */

const MEDIA_MOVE_WORDS = {
  waiting: 'waiting for the Domovoi PC (it looks every 30 seconds, and waits 10 minutes after any restart)',
  busy: 'waiting: an update or restart of Domovoi is running',
  applying: 'moving: the Domovoi PC is making the folders, then Domovoi restarts (a couple of minutes offline)',
  done: 'moved',
  refused: 'not moved',
  failed: 'not moved',
  stale: 'not moved: the request waited too long; ask again',
};

const mediaFoldersFieldStyle = {
  font: 'inherit', fontFamily: 'var(--ff-mono)', fontSize: 12, padding: '7px 10px', borderRadius: 'var(--r-sm)',
  border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--fg)', minWidth: 0, flex: '1 1 220px',
};

/* One kind's row: where it is and what can be done from here. */
const MediaFolderRow = ({ row, data, fire, onOpen, onSaved }) => {
  const [editing, setEditing] = React.useState(false);
  const [value, setValue] = React.useState(row.location || '');
  const [problem, setProblem] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const windows = data.host_kind !== 'linux';
  const canOpen = windows && row.where === 'host' && data.open && data.open.available && data.on_this_pc;
  const copy = async () => {
    const ok = await copyToClipboard(row.location);
    fire(ok ? `copied ${row.location}` : 'copy failed: select the path and copy it yourself', { tone: ok ? 'ok' : 'warn' });
  };
  const save = async () => {
    setSaving(true); setProblem(null);
    try {
      const res = await apiPatch('/api/config/editable', { changes: { [row.field]: value.trim() } });
      const why = res && res.rejected && res.rejected[row.field];
      if (why) { setProblem(why); return; }
      setEditing(false);
      fire(`saved: ${row.label.toLowerCase()} is ${value.trim()} after a restart (Settings → About & updates → restart)`, { tone: 'ok' });
      onSaved();
    } catch (e) {
      const msg = mutationErrorText(e, 'Save');
      if (msg) setProblem(msg);
    } finally {
      setSaving(false);
    }
  };
  let state = null;
  if (row.where === 'internal') state = <span style={{ color: 'var(--fg-muted)' }}>kept inside Domovoi, not in a folder on the PC</span>;
  else if (windows && !row.connected) state = <Pill tone="warn">not connected</Pill>;
  else if (!row.exists) state = <Pill tone="warn">missing</Pill>;
  else if (!row.readable) state = <Pill tone="warn">not readable</Pill>;
  return (
    <div className="media-folder-row" data-config-field={row.editable ? row.field : undefined} data-media-kind={row.kind}
         style={{ padding: '10px 0', borderBottom: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 500, minWidth: 92 }}>{row.label}</span>
        {row.location && !editing && (
          <span className="mono" style={{ fontFamily: 'var(--ff-mono)', fontSize: 12, wordBreak: 'break-all', flex: '1 1 220px' }}>
            {row.location}
          </span>
        )}
        {state}
        {!editing && (
          <span style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
            {canOpen && <Button icon="folder-open" onClick={() => onOpen(row)}>open on the Domovoi PC</Button>}
            {row.location && <Button icon="copy" onClick={copy}>copy path</Button>}
            {row.editable && <Button icon="pencil" onClick={() => { setValue(row.location || ''); setProblem(null); setEditing(true); }}>change</Button>}
          </span>
        )}
      </div>
      {editing && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input value={value} onChange={(e) => setValue(e.target.value)} style={mediaFoldersFieldStyle}
                   aria-label={`${row.label} folder`} spellCheck={false} autoFocus/>
            <Button variant="primary" icon="check" onClick={save} disabled={saving || !value.trim()}>
              {saving ? 'saving…' : 'save'}
            </Button>
            <Button onClick={() => setEditing(false)} disabled={saving}>cancel</Button>
          </div>
          {problem && <div role="alert" style={{ fontSize: 12, color: 'var(--err-ink)', marginTop: 6 }}>{problem}</div>}
          <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6 }}>
            A folder on the Domovoi server that Domovoi’s service user can read. Takes effect after a restart;
            the rooms’ music players then use the new folder. Files aren’t moved.
          </div>
        </div>
      )}
    </div>
  );
};

/* Windows: move the media root to another folder on the PC. */
const MediaFoldersMove = ({ data, fire, refresh }) => {
  const change = data.change || {};
  const [value, setValue] = React.useState('');
  const [confirming, setConfirming] = React.useState(false);
  const [problem, setProblem] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  if (data.host_kind === 'linux') return null;
  if (!change.available) {
    return (
      <div className="media-folders-move" style={{ fontSize: 12, color: 'var(--fg-muted)', padding: '12px 0 0' }}>
        Moving the folders from here isn’t possible yet: {change.why}.
      </div>
    );
  }
  const underway = ['busy', 'applying'].includes(change.state) || (change.state === 'waiting' && !change.waiting_for_host);
  const target = value.trim();
  const ask = async () => {
    setBusy(true); setProblem(null);
    try {
      await apiPost('/api/config/media-folders/location', { root: target });
      setConfirming(false); setValue('');
      fire(`asked the Domovoi PC to move the media folders to ${target}`, { tone: 'ok' });
      refresh();
    } catch (e) {
      const msg = mutationErrorText(e, 'Move', { kept: false });
      if (msg) setProblem(msg.replace(/^Move failed: /, ''));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };
  const withdraw = async () => {
    try { await apiPost('/api/config/media-folders/location/cancel', {}); refresh(); }
    catch (e) { const msg = mutationErrorText(e, 'Cancel', { kept: false }); if (msg) fire(msg, { tone: 'err' }); }
  };
  const words = MEDIA_MOVE_WORDS[change.state];
  return (
    <div className="media-folders-move" style={{ paddingTop: 14 }}>
      <div style={{ fontWeight: 500, marginBottom: 4 }}>Move the media folders</div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 8, lineHeight: 1.5 }}>
        Pick another folder on the Domovoi PC, for example on a bigger drive. Domovoi makes Music, Pictures,
        Videos, Documents and Audiobooks in it, restarts (a couple of minutes offline) and uses them from then on.
        Your files stay where they are: move them across in File Explorer when you like.
      </div>
      {words && change.state !== 'none' && (
        <div className="media-folders-move-state" role="status"
             style={{ fontSize: 12, marginBottom: 8, color: ['refused', 'failed', 'stale'].includes(change.state) ? 'var(--err-ink)' : 'var(--fg)' }}>
          {change.requested_root && <span className="mono" style={{ fontFamily: 'var(--ff-mono)' }}>{change.requested_root}: </span>}
          {words}{change.problem ? ` (${change.problem})` : ''}
          {change.state === 'done' && change.previous_root && (
            <span style={{ color: 'var(--fg-muted)' }}> · the files in {change.previous_root} are still there</span>
          )}
          {change.waiting_for_host && <span> · <a href="#" className="home-link" onClick={(e) => { e.preventDefault(); withdraw(); }}>withdraw</a></span>}
        </div>
      )}
      {!underway && !confirming && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input value={value} onChange={(e) => { setValue(e.target.value); setProblem(null); }} style={mediaFoldersFieldStyle}
                 placeholder="D:\Media" aria-label="new folder for the media folders" spellCheck={false}/>
          <Button icon="folder-input" disabled={!target} onClick={() => setConfirming(true)}>move…</Button>
        </div>
      )}
      {confirming && (
        <div className="media-folders-confirm" style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', padding: 12 }}>
          <div style={{ fontSize: 13, marginBottom: 8, lineHeight: 1.5 }}>
            Move the media folders to <span className="mono" style={{ fontFamily: 'var(--ff-mono)' }}>{target}</span>?
            Domovoi restarts once to use them. The files in{' '}
            <span className="mono" style={{ fontFamily: 'var(--ff-mono)' }}>{data.root || 'the old folders'}</span> stay there.
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button variant="primary" icon="check" onClick={ask} disabled={busy}>{busy ? 'asking…' : 'move and restart'}</Button>
            <Button onClick={() => setConfirming(false)} disabled={busy}>cancel</Button>
          </div>
        </div>
      )}
      {problem && <div role="alert" style={{ fontSize: 12, color: 'var(--err-ink)', marginTop: 6 }}>{problem}</div>}
    </div>
  );
};

const MediaFoldersPanel = () => {
  const { data, loading, error, refresh } = useApiObject('/api/config/media-folders', { refetchOnAuth: true });
  const [fire, toastNode] = useToast();
  const change = (data && data.change) || {};
  const pending = ['waiting', 'busy', 'applying'].includes(change.state);
  React.useEffect(() => {
    if (!pending) return undefined;
    const t = setInterval(() => refresh(), 5000);
    return () => clearInterval(t);
  }, [pending, refresh]);

  const open = async (row) => {
    let id = null;
    try {
      const res = await apiPost('/api/config/media-folders/open', { kind: row.kind });
      id = res && res.id;
      fire(`opening ${row.label} on the Domovoi PC…`, { key: 'media-open' });
    } catch (e) {
      reportMutationFailure(fire, 'Open', e);
      return;
    }
    // The tray looks every few seconds; say how it went.
    for (let i = 0; i < 8 && id; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      let s = null;
      try { s = await apiGet('/api/config/media-folders', { quiet: true }); } catch { s = null; }
      const last = s && s.open && s.open.last;
      if (last && last.id === id) {
        const ok = last.outcome === 'opened';
        fire(ok ? `opened ${row.label} in File Explorer on the Domovoi PC` : `${row.label} wasn’t opened: ${last.detail || last.outcome}`,
             { key: 'media-open', tone: ok ? 'ok' : 'warn' });
        return;
      }
    }
    fire('the Domovoi PC didn’t answer: is Domovoi’s icon running there?', { key: 'media-open', tone: 'warn' });
  };

  if (!data) {
    return (
      <Card title="Media folders">
        <div style={{ padding: 24, fontSize: 12, color: 'var(--fg-muted)', textAlign: 'center' }}>
          {loading ? 'loading…' : (error && (error.status === 401 || error.status === 403))
            ? 'sign in as the admin to see where the media folders are'
            : `can’t read the media folders: ${error ? apiErrorText(error) : 'no answer'}`}
        </div>
      </Card>
    );
  }
  const windows = data.host_kind !== 'linux';
  const sub = windows
    ? `The folders on ${data.place} where Domovoi keeps your media. Copy files into them in File Explorer.`
    : 'The folders on the Domovoi server that Domovoi reads your media from.';
  let where = null;
  if (windows && !data.on_this_pc) {
    where = `These folders are on ${data.place}, not on this device. Open them there in File Explorer, or open this page in a browser on that PC to get an “open” button.`;
  } else if (windows && data.open && !data.open.available) {
    where = `Opening them from here: ${data.open.why}.`;
  }
  return (
    <React.Fragment>
      {toastNode}
      <Card title="Where they are" sub={sub}>
        <div style={{ padding: '4px 16px 14px' }}>
          {where && <div className="media-folders-where" style={{ fontSize: 12, color: 'var(--fg-muted)', padding: '6px 0 4px', lineHeight: 1.5 }}>{where}</div>}
          {(data.kinds || []).map((row) => (
            <MediaFolderRow key={row.kind} row={row} data={data} fire={fire} onOpen={open} onSaved={refresh}/>
          ))}
          <MediaFoldersMove data={data} fire={fire} refresh={refresh}/>
        </div>
      </Card>
    </React.Fragment>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['media-folders'] = { Panel: MediaFoldersPanel };
