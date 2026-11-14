/* Music → Tools (admin): Duplicates, Needs review, Cover art, Trash (V058).
 *
 * Every change here is behind one confirmation and goes to
 * /api/music/library-tools/* (web/backend/api/library_tools.py), which
 * takes the admin sign-in. The tab is only shown to a signed-in admin
 * (music.jsx), so nothing here is a control that can't do what it says.
 */

const LT_SECTIONS = [
  { id: 'duplicates', label: 'Duplicates' },
  { id: 'review', label: 'Needs review' },
  { id: 'cover', label: 'Cover art' },
  { id: 'trash', label: 'Trash' },
];

const ltLen = (s) => (s == null ? '—' : fmtDur(s));

/* One confirmation dialog for every action on this tab: `title` says what
 * is about to happen, `children` what it means, `confirmLabel` names the
 * action (never "Delete": nothing here deletes a file). */
const LtConfirm = ({ ask, busy, onCancel, onConfirm }) => {
  if (!ask) return null;
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 90, background: 'rgba(0,0,0,0.45)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
         onClick={busy ? undefined : onCancel}>
      <div role="dialog" aria-modal="true" aria-label={ask.title} onClick={(e) => e.stopPropagation()}
           style={{ width: 'min(480px, 100%)', background: 'var(--card)',
                    border: '1px solid var(--border)', borderRadius: 'var(--r-lg)',
                    boxShadow: 'var(--shadow-md), var(--inner-highlight)', overflow: 'hidden' }}>
        <div style={{ padding: '13px 16px', borderBottom: '1px solid var(--border-soft)' }}>
          <strong style={{ fontSize: 14 }}>{ask.title}</strong>
        </div>
        <div style={{ padding: '14px 16px', fontSize: 13, color: 'var(--fg-muted)',
                      display: 'flex', flexDirection: 'column', gap: 8 }}>
          {ask.body}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 16px',
                      borderTop: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
          <Button icon="x" disabled={busy} onClick={onCancel}>Cancel</Button>
          <Button variant="primary" icon={ask.icon || 'check'} disabled={busy} onClick={onConfirm}>
            {busy ? 'Working…' : ask.confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
};

/* The pending-confirmation state: `ask({title, body, confirmLabel, run})`. */
const useLtConfirm = () => {
  const [pending, setPending] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    try { await pending.run(); } finally { setBusy(false); setPending(null); }
  };
  const node = <LtConfirm ask={pending} busy={busy}
                          onCancel={() => { if (!busy) setPending(null); }} onConfirm={confirm}/>;
  return { ask: setPending, node };
};

const useLtLoad = (path) => {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState(null);
  const load = React.useCallback(async () => {
    try { setData(await apiGet(path)); setError(null); }
    catch (e) { setError(e); }
  }, [path]);
  React.useEffect(() => { load(); }, [load]);
  return { data, error, reload: load };
};

const LtState = ({ q, empty }) => {
  if (q.error) return <Empty glyph="sleeping" title="couldn't load" sub={apiErrorText(q.error)}
                             action={<Button icon="refresh-cw" onClick={q.reload}>retry</Button>}/>;
  if (!q.data) return <div style={{ padding: 16, color: 'var(--fg-muted)' }}>Loading…</div>;
  return empty || null;
};

/* ---- Duplicates ------------------------------------------- */

const LtDupGroup = ({ g, onMove, onKeepBoth }) => {
  const [keep, setKeep] = React.useState(g.best_id);
  const others = g.members.filter(m => m.id !== keep).length;
  return (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)' }}>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 6 }}>{g.label}</div>
      {g.members.map(m => (
        <label key={m.id} style={{ display: 'grid', gridTemplateColumns: 'auto 40px 1fr', gap: 10,
                                   alignItems: 'center', padding: '6px 0', cursor: 'pointer' }}>
          <input type="radio" name={`keep-${g.key}`} checked={keep === m.id}
                 onChange={() => setKeep(m.id)} aria-label={`keep ${m.title || m.path}`}/>
          <CoverArt src={libraryCoverUrl(m.id)} size={40}/>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {m.title || '(no title)'}{m.artist ? ` — ${m.artist}` : ''}
              {m.id === g.best_id && <span style={{ marginLeft: 6 }}><Pill tone="ok">best copy</Pill></span>}
            </div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>
              {ltLen(m.duration_sec)} · {m.bitrate_kbps ? `${m.bitrate_kbps} kbps` : 'bitrate unknown'}
              {' · '}{m.identity}{m.has_art ? ' · has art' : ''}
              {m.playlists ? ` · in ${m.playlists} playlist${m.playlists === 1 ? '' : 's'}` : ''}
              {m.missing ? ' · file missing' : ''}
              <div>{m.path}</div>
            </div>
          </div>
        </label>
      ))}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
        <Button variant="primary" icon="archive" onClick={() => onMove(g, keep)}>
          Move {others} other cop{others === 1 ? 'y' : 'ies'} to trash
        </Button>
        <Button icon="copy" onClick={() => onKeepBoth(g)}>Keep {g.members.length === 2 ? 'both' : 'all'}</Button>
      </div>
    </div>
  );
};

const LtDuplicates = ({ fire, confirm }) => {
  const q = useLtLoad('/api/music/library-tools/duplicates');
  const groups = (q.data && q.data.groups) || [];
  const onMove = (g, keep) => {
    const kept = g.members.find(m => m.id === keep);
    const n = g.members.length - 1;
    confirm.ask({
      title: `Move ${n} other cop${n === 1 ? 'y' : 'ies'} to the trash?`,
      confirmLabel: 'Move to trash', icon: 'archive',
      body: (<>
        <div>Keeping “{kept.title || kept.path}” ({ltLen(kept.duration_sec)}).</div>
        <div>The other files move to the trash folder inside your music folder. Their playlist
          places, favourite and listening history move to the copy you keep. Nothing is deleted:
          Tools → Trash puts a copy back.</div>
      </>),
      run: async () => {
        try {
          const r = await apiPost('/api/music/library-tools/duplicates/resolve',
            { keep_id: keep, member_ids: g.members.map(m => m.id) });
          fire(r.failed && r.failed.length
            ? `moved ${r.moved.length}; ${r.failed.length} not moved: ${r.failed[0].reason}`
            : `moved ${r.moved.length} to the trash`);
        } catch (e) { reportMutationFailure(fire, 'move to trash', e); }
        q.reload();
      },
    });
  };
  const onKeepBoth = (g) => confirm.ask({
    title: 'Keep every copy?', confirmLabel: 'Keep them', icon: 'copy',
    body: <div>This group won't be shown again, unless a copy is added to it or leaves it.</div>,
    run: async () => {
      try { await apiPost('/api/music/library-tools/duplicates/keep-both', { member_ids: g.members.map(m => m.id) }); }
      catch (e) { reportMutationFailure(fire, 'keep both', e); }
      q.reload();
    },
  });
  if (!q.data || q.error) return <LtState q={q}/>;
  if (!groups.length) return <Empty glyph="sleeping" title="no duplicates"
                                    sub={q.data.kept ? `${q.data.kept} group${q.data.kept === 1 ? '' : 's'} you chose to keep` : 'every song is in the library once'}/>;
  return (
    <div>
      <div style={{ padding: '10px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
        {q.data.total} group{q.data.total === 1 ? '' : 's'} of songs the library has more than once
        (same MusicBrainz recording, or same artist and title). The best copy is chosen for you:
        the highest bitrate, then identified over tagged over untagged, then the one with cover art.
      </div>
      {groups.map(g => <LtDupGroup key={g.key} g={g} onMove={onMove} onKeepBoth={onKeepBoth}/>)}
    </div>
  );
};

/* ---- Needs review ----------------------------------------- */

const LT_REASON = {
  disagreed: 'Song recognition found a song whose names don’t match this file’s, so it kept the file’s names.',
  low_confidence: 'Song recognition wasn’t sure enough to rename it.',
};

const LtReview = ({ fire, confirm }) => {
  const q = useLtLoad('/api/music/library-tools/review');
  const items = (q.data && q.data.items) || [];
  const apply = (it, i) => {
    const c = it.candidates[i];
    confirm.ask({
      title: `Rename to “${c.title || '?'}”?`, confirmLabel: 'Apply',
      body: (<>
        <div>“{it.title || it.file}”{it.artist ? ` by ${it.artist}` : ''} becomes “{c.title}”
          {c.artist ? ` by ${c.artist}` : ''}{c.album ? `, on ${c.album}` : ''}.</div>
        <div>Like an edit by hand: song recognition won't change it again. Its year and genre
          are then looked up for the new song.</div>
      </>),
      run: async () => {
        try { await apiPost(`/api/music/library-tools/review/${it.track_id}/apply`, { index: i }); fire('renamed'); }
        catch (e) { reportMutationFailure(fire, 'apply', e); }
        q.reload();
      },
    });
  };
  const skip = (it) => confirm.ask({
    title: 'Keep the file’s own names?', confirmLabel: 'Skip', icon: 'x',
    body: <div>“{it.title || it.file}” keeps its names and leaves this list.</div>,
    run: async () => {
      try { await apiPost(`/api/music/library-tools/review/${it.track_id}/skip`); }
      catch (e) { reportMutationFailure(fire, 'skip', e); }
      q.reload();
    },
  });
  if (!q.data || q.error) return <LtState q={q}/>;
  if (!items.length) return <Empty glyph="sleeping" title="nothing to review"
                                   sub="songs recognition wasn't sure about show up here"/>;
  return (
    <div>
      {items.map(it => (
        <div key={it.track_id} style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)' }}>
          <div style={{ fontWeight: 500, overflowWrap: 'anywhere' }}>
            {it.title || it.file}{it.artist ? ` — ${it.artist}` : ''}
          </div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>
            {ltLen(it.duration_sec)}{it.album ? ` · ${it.album}` : ''} · {it.file}
          </div>
          <div style={{ fontSize: 12, color: 'var(--fg-muted)', margin: '4px 0 8px' }}>{LT_REASON[it.reason] || ''}</div>
          {it.candidates.map((c, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'center',
                                  padding: '6px 0', borderTop: '1px dashed var(--border-soft)' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ overflowWrap: 'anywhere' }}>{c.title || '?'}{c.artist ? ` — ${c.artist}` : ''}</div>
                <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                  {c.album ? `${c.album} · ` : ''}{ltLen(c.length_sec)}
                  {c.score != null ? ` · score ${Math.round(c.score * 100)}%` : ''}
                  {c.sources != null ? ` · ${c.sources} submission${c.sources === 1 ? '' : 's'}` : ''}
                  {c.source === 'shazam' ? ' · from Shazam' : ''}
                </div>
              </div>
              <Button icon="check" onClick={() => apply(it, i)}>Apply</Button>
            </div>
          ))}
          <div style={{ marginTop: 6 }}><Button icon="x" onClick={() => skip(it)}>Skip</Button></div>
        </div>
      ))}
    </div>
  );
};

/* ---- Cover art -------------------------------------------- */

const LT_COVER_STATE = {
  written: 'written into the file', cached: 'shown from Domovoi’s copy', skipped: 'not written',
};

const LtCover = ({ fire, confirm }) => {
  const q = useLtLoad('/api/music/library-tools/cover-art');
  const d = q.data;
  const undo = (it) => confirm.ask({
    title: 'Undo this cover?', confirmLabel: 'Undo', icon: 'rotate-ccw',
    body: <div>{it.state === 'written'
      ? (it.had_art ? 'The file gets its own pictures back, as they were before Domovoi changed it.'
                    : 'The picture Domovoi added is taken out of the file again.')
      : 'Domovoi stops showing its copy of the cover.'} Domovoi won't add a cover to this song again.</div>,
    run: async () => {
      try { await apiPost(`/api/music/library-tools/cover-art/${it.track_id}/undo`); fire('cover undone'); }
      catch (e) { reportMutationFailure(fire, 'undo', e); }
      q.reload();
    },
  });
  if (!d || q.error) return <LtState q={q}/>;
  const c = d.counts || {};
  return (
    <div>
      <div style={{ padding: '10px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
        {d.enabled
          ? `Covers come from the Cover Art Archive for songs Domovoi has identified (${d.mode === 'add_missing' ? 'only files with no art' : 'replacing the art files have'}). MP3 and FLAC files get the picture written in; other kinds of file show Domovoi's own copy. Settings → Advanced → Library changes this.`
          : 'Cover art from the Cover Art Archive is off (Settings → Advanced → Library).'}
        <div className="mono" style={{ marginTop: 4 }}>
          {c.written || 0} written · {c.cached || 0} Domovoi's copy · {c.same || 0} already right
          · {c.no_art || 0} no art available · {c.skipped || 0} not written · {c.undone || 0} undone
        </div>
      </div>
      {(d.items || []).map(it => (
        <div key={it.track_id} style={{ display: 'grid', gridTemplateColumns: '40px 1fr auto', gap: 10,
                                        alignItems: 'center', padding: '8px 16px', borderTop: '1px solid var(--border-soft)' }}>
          <CoverArt src={libraryCoverUrl(it.track_id, it.cover_v)} size={40}/>
          <div style={{ minWidth: 0 }}>
            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {it.title || '?'}{it.artist ? ` — ${it.artist}` : ''}
            </div>
            <div style={{ fontSize: 11, color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>
              {LT_COVER_STATE[it.state] || it.state}{it.note && it.state === 'skipped' ? `: ${it.note}` : ''}
            </div>
          </div>
          {it.state !== 'skipped' ? <Button icon="rotate-ccw" onClick={() => undo(it)}>Undo</Button> : <span/>}
        </div>
      ))}
    </div>
  );
};

/* ---- Trash ------------------------------------------------ */

const LtTrash = ({ fire, confirm }) => {
  const q = useLtLoad('/api/music/library-tools/trash');
  const items = (q.data && q.data.items) || [];
  const restore = (it) => confirm.ask({
    title: 'Put this copy back?', confirmLabel: 'Restore', icon: 'rotate-ccw',
    body: <div>“{it.title || it.path}” moves back to {it.path} and into the library again
      (not into the playlists it was in).</div>,
    run: async () => {
      try { await apiPost(`/api/music/library-tools/trash/${it.id}/restore`); fire('restored'); }
      catch (e) { reportMutationFailure(fire, 'restore', e); }
      q.reload();
    },
  });
  if (!q.data || q.error) return <LtState q={q}/>;
  if (!items.length) return <Empty glyph="sleeping" title="the trash is empty"
                                   sub="copies the duplicates finder moves wait here"/>;
  return (
    <div>
      <div style={{ padding: '10px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>
        The files are in the “{q.data.folder}” folder inside your music folder. Empty it with
        a file manager when you're sure.
      </div>
      {items.map(it => (
        <div key={it.id} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'center',
                                  padding: '8px 16px', borderTop: '1px solid var(--border-soft)' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ overflowWrap: 'anywhere' }}>{it.title || it.path}{it.artist ? ` — ${it.artist}` : ''}</div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>
              {it.path}{it.moved_at ? ` · moved ${relTime(it.moved_at)}` : ''}{it.in_trash ? '' : ' · no longer in the trash'}
            </div>
          </div>
          {it.in_trash ? <Button icon="rotate-ccw" onClick={() => restore(it)}>Restore</Button> : <span/>}
        </div>
      ))}
    </div>
  );
};

const LibraryToolsTab = ({ fire }) => {
  const [section, setSection] = React.useState('duplicates');
  const confirm = useLtConfirm();
  return (
    <div>
      <div style={{ padding: '0 8px' }}><Tabs tabs={LT_SECTIONS} value={section} onChange={setSection}/></div>
      {section === 'duplicates' && <LtDuplicates fire={fire} confirm={confirm}/>}
      {section === 'review' && <LtReview fire={fire} confirm={confirm}/>}
      {section === 'cover' && <LtCover fire={fire} confirm={confirm}/>}
      {section === 'trash' && <LtTrash fire={fire} confirm={confirm}/>}
      {confirm.node}
    </div>
  );
};

window.LibraryToolsTab = LibraryToolsTab;
