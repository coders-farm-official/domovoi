/* Co-editing UI pieces for the document editors (builder A's editor and
 * builder D's sheets put them in their toolbars):
 *
 *   <CollabPresence collab={collab}/>
 *       who has the document open (coloured dots + names) and the
 *       connection state ("Offline: changes will sync").
 *   <CollabHistory path={rel_path} collab={collab} onClose={...} fire={fire}
 *                  renderPreview={(el, ydoc) => destroyFn}/>
 *       version history: a list (when, who), a preview, and Restore.
 *       Without renderPreview the preview is the version's plain text; with
 *       it, the editor draws the version itself, read-only, from a Y.Doc.
 *
 * `collab` is what window.DomovoiCollab.open() resolved to (collab.js).
 */

const _collabTimeLabel = (iso) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const sameDay = d.toDateString() === now.toDateString();
  const yesterday = new Date(now.getTime() - 86400000).toDateString() === d.toDateString();
  if (sameDay) return `Today ${time}`;
  if (yesterday) return `Yesterday ${time}`;
  const opts = { month: 'short', day: 'numeric' };
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
  return `${d.toLocaleDateString([], opts)}, ${time}`;
};

const _collabNames = (names) => {
  const list = (names || []).filter(Boolean);
  if (!list.length) return '';
  if (list.length <= 2) return list.join(' and ');
  return `${list.slice(0, 2).join(', ')} and ${list.length - 2} more`;
};

const CollabPresence = ({ collab }) => {
  const [people, setPeople] = React.useState(() => (collab ? collab.presence() : []));
  const [status, setStatus] = React.useState(() => (collab ? collab.statusText() : ''));
  React.useEffect(() => {
    if (!collab) return undefined;
    const onPresence = (list) => setPeople(list);
    const onStatus = () => setStatus(collab.statusText());
    collab.on('presence', onPresence);
    collab.on('status', onStatus);
    collab.on('hello', onStatus);
    setPeople(collab.presence());
    setStatus(collab.statusText());
    return () => {
      collab.off('presence', onPresence);
      collab.off('status', onStatus);
      collab.off('hello', onStatus);
    };
  }, [collab]);
  if (!collab) return null;
  const offline = collab.status !== 'connected';
  const shown = people.slice(0, 5);
  const more = people.length - shown.length;
  return (
    <div className="collab-presence" role="status" aria-live="polite"
         style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flexWrap: 'wrap' }}>
      {shown.map((p) => (
        <span key={p.clientId} title={p.self ? `${p.name} (you)` : p.name}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12,
                       color: 'var(--fg-muted)', maxWidth: 140, minWidth: 0 }}>
          <span aria-hidden="true" style={{ width: 9, height: 9, borderRadius: '50%',
                                            background: p.color, flexShrink: 0,
                                            boxShadow: '0 0 0 1px var(--card, #fff)' }}/>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {p.self ? 'You' : p.name}
          </span>
        </span>
      ))}
      {more > 0 && <span style={{ fontSize: 12, color: 'var(--fg-faint)' }}>+{more}</span>}
      <span style={{ fontSize: 11, color: offline ? 'var(--warn-ink, var(--warn))' : 'var(--fg-faint)',
                     whiteSpace: 'nowrap' }}>
        {status}
      </span>
    </div>
  );
};

const CollabHistory = ({ path, collab, onClose, fire, renderPreview }) => {
  const [list, setList] = React.useState({ status: 'loading', versions: [] });
  const [picked, setPicked] = React.useState(null);
  const [preview, setPreview] = React.useState({ status: 'idle' });
  const [restoring, setRestoring] = React.useState(false);
  const richRef = React.useRef(null);
  const canRestore = !!collab && !collab.readonly;

  const load = React.useCallback(async () => {
    try {
      const j = await apiFetch(`/api/collab/history?path=${encodeURIComponent(path)}`, { quiet: true });
      setList({ status: 'ready', versions: j.versions || [] });
    } catch (e) {
      setList({ status: 'error', versions: [], message: String((e && e.message) || e) });
    }
  }, [path]);

  React.useEffect(() => { load(); }, [load]);

  React.useEffect(() => {
    if (!collab) return undefined;
    const refresh = () => load();
    collab.on('restored', refresh);
    return () => collab.off('restored', refresh);
  }, [collab, load]);

  React.useEffect(() => {
    if (!picked) return undefined;
    let cancelled = false;
    let destroy = null;
    setPreview({ status: 'loading' });
    (async () => {
      try {
        const v = await apiFetch(
          `/api/collab/history/${picked.id}?path=${encodeURIComponent(path)}`, { quiet: true });
        if (cancelled) return;
        setPreview({ status: 'ready', text: v.text || '', update: v.update });
        if (renderPreview && richRef.current && window.DomovoiCollab) {
          const ydoc = await window.DomovoiCollab.docFromUpdate(v.update);
          if (cancelled) { ydoc.destroy(); return; }
          const undo = renderPreview(richRef.current, ydoc);
          destroy = () => { try { if (typeof undo === 'function') undo(); } catch {} ydoc.destroy(); };
        }
      } catch (e) {
        if (!cancelled) setPreview({ status: 'error', message: String((e && e.message) || e) });
      }
    })();
    return () => { cancelled = true; if (destroy) destroy(); };
  }, [picked, path, renderPreview]);

  const onRestore = async () => {
    if (!picked) return;
    if (!window.confirm(
      `Restore the version from ${_collabTimeLabel(picked.at)}? `
      + 'What the document says now is kept in the history too.')) return;
    setRestoring(true);
    try {
      await apiFetch('/api/collab/restore', {
        method: 'POST', body: JSON.stringify({ path, version_id: picked.id }),
      });
      if (fire) fire('Restored');
      setPicked(null);
      await load();
    } catch (e) {
      const why = typeof deviceBlockReason === 'function' ? deviceBlockReason(e) : null;
      const msg = why || (typeof mutationErrorText === 'function' ? mutationErrorText(e, 'Restore') : String(e));
      if (msg && fire) fire(msg);
    } finally { setRestoring(false); }
  };

  return (
    <div role="dialog" aria-label="Version history"
         style={{ position: 'fixed', inset: 0, zIndex: 90, background: 'var(--bg)',
                  display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                    borderBottom: '1px solid var(--border)', background: 'var(--card)' }}>
        <Icon name="history" size={16}/>
        <strong style={{ fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis',
                         whiteSpace: 'nowrap', minWidth: 0 }}>Version history</strong>
        <span style={{ flex: 1 }}/>
        {canRestore && picked && (
          <Button variant="primary" icon="rotate-ccw" disabled={restoring} onClick={onRestore}>
            {restoring ? 'Restoring…' : 'Restore'}
          </Button>
        )}
        <Button icon="x" onClick={onClose}>Close</Button>
      </div>
      <div className="collab-history-body"
           style={{ flex: 1, minHeight: 0, display: 'flex', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px', maxWidth: '100%', minHeight: 0, overflowY: 'auto',
                      borderRight: '1px solid var(--border-soft)', maxHeight: '100%' }}>
          {list.status === 'loading' && (
            <div style={{ padding: 16, fontSize: 13, color: 'var(--fg-faint)' }}>Loading…</div>
          )}
          {list.status === 'error' && (
            <div style={{ padding: 16, fontSize: 13, color: 'var(--err-ink)' }}>
              Couldn’t load the history: {list.message}
            </div>
          )}
          {list.status === 'ready' && !list.versions.length && (
            <div style={{ padding: 16, fontSize: 13, color: 'var(--fg-faint)' }}>
              No earlier versions yet. A version is kept every few minutes while someone edits,
              and when the last person closes the document.
            </div>
          )}
          {list.versions.map((v) => {
            const on = picked && picked.id === v.id;
            const who = _collabNames(v.editors);
            return (
              <button key={v.id} type="button" onClick={() => setPicked(v)}
                      aria-pressed={!!on}
                      style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 14px',
                               border: 'none', borderBottom: '1px solid var(--border-soft)',
                               background: on ? 'var(--brand-soft)' : 'transparent',
                               color: 'var(--fg)', cursor: 'pointer', font: 'inherit' }}>
                <div style={{ fontSize: 13, fontWeight: on ? 600 : 500 }}>{_collabTimeLabel(v.at)}</div>
                <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                  {v.label}{who ? ` · ${who}` : ''}
                </div>
              </button>
            );
          })}
        </div>
        <div style={{ flex: '3 1 320px', minWidth: 0, minHeight: 0, overflowY: 'auto',
                      padding: '16px 20px' }}>
          {!picked && list.status === 'ready' && list.versions.length > 0 && (
            <div style={{ fontSize: 13, color: 'var(--fg-faint)' }}>Pick a version to see it.</div>
          )}
          {picked && preview.status === 'loading' && (
            <div style={{ fontSize: 13, color: 'var(--fg-faint)' }}>Loading…</div>
          )}
          {picked && preview.status === 'error' && (
            <div style={{ fontSize: 13, color: 'var(--err-ink)' }}>Couldn’t load it: {preview.message}</div>
          )}
          <div ref={richRef} style={{ display: renderPreview && preview.status === 'ready' ? 'block' : 'none' }}/>
          {picked && preview.status === 'ready' && !renderPreview && (
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.6, maxWidth: 780,
                          overflowWrap: 'anywhere' }}>
              {preview.text || <span style={{ color: 'var(--fg-faint)' }}>This version is empty.</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

window.CollabPresence = CollabPresence;
window.CollabHistory = CollabHistory;
