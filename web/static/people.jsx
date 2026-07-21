/* People page — household roster + per-person profile / sessions / conversations.
 *
 * Data sources:
 *   * GET /api/people                          — roster.
 *   * GET /api/people/{id}/sessions            — sessions for the selected person.
 *   * GET /api/people/{id}/conversations       — conversation_log rows for them.
 *   * GET /api/denylist                        — opted-out voice count.
 *   * /ws/state · `people.last_seen.changed`   — push refresh of the roster.
 *
 * Sessions and conversations are fetched per-person on selection; we
 * don't preload them all because conversation_log can be large.
 */

const isLive = (iso) => {
  if (!iso) return false;
  return (new Date() - new Date(iso)) / 1000 < 300;
};

/* ---- Confirm modal (cascade delete) ----------------------- */
const ConfirmForget = ({ person, sessionCount, turnCount, open, onClose, onConfirm }) => {
  const [text, setText] = React.useState('');
  React.useEffect(() => { if (open) setText(''); }, [open]);
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open || !person) return null;
  const ok = text.trim() === person.name;
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.18)', backdropFilter: 'blur(2px)', zIndex: 60 }}/>
      <div style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                    width: 480, maxWidth: 'calc(100vw - 32px)',
                    background: 'var(--card)', border: '1px solid var(--border)',
                    borderRadius: 'var(--r-md)', boxShadow: 'var(--shadow-md), var(--inner-highlight)', zIndex: 61 }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-soft)',
                      display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--err-soft)',
                        display: 'grid', placeItems: 'center', color: 'var(--err)' }}>
            <Icon name="alert-triangle" size={14}/>
          </div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Forget {person.name}?</div>
        </div>
        <div style={{ padding: 16, fontSize: 13, color: 'var(--fg)' }}>
          <p style={{ margin: 0, marginBottom: 10 }}>This is a cascade delete. Domovoi will lose:</p>
          <ul style={{ margin: 0, marginBottom: 12, paddingLeft: 18, color: 'var(--fg-muted)', fontSize: 12, lineHeight: 1.8 }}>
            <li><span className="mono" style={{ color: 'var(--fg)' }}>{person.voice_profile_count}</span> voice profile{person.voice_profile_count === 1 ? '' : 's'}</li>
            <li><span className="mono" style={{ color: 'var(--fg)' }}>{sessionCount}</span> session{sessionCount === 1 ? '' : 's'}</li>
            <li><span className="mono" style={{ color: 'var(--fg)' }}>{turnCount}</span> conversation turn{turnCount === 1 ? '' : 's'}</li>
          </ul>
          <p style={{ margin: 0, marginBottom: 8, fontSize: 12, color: 'var(--fg-muted)' }}>
            Type <span className="mono" style={{ color: 'var(--fg)' }}>{person.name}</span> to confirm.
          </p>
          <input value={text} onChange={e => setText(e.target.value)} autoFocus
                 style={{ font: 'inherit', fontFamily: 'var(--ff-mono)', fontSize: 13, width: '100%', height: 32,
                          padding: '0 10px', borderRadius: 'var(--r-sm)',
                          border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)',
                          boxShadow: 'var(--inner-highlight)' }}/>
        </div>
        <div style={{ padding: 12, borderTop: '1px solid var(--border-soft)',
                      display: 'flex', gap: 8, justifyContent: 'flex-end', background: 'var(--sunken)' }}>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon="trash-2" disabled={!ok} onClick={() => onConfirm(person)}
                  style={{ background: ok ? 'var(--err)' : 'var(--err-soft)',
                           color: ok ? 'white' : 'var(--err)',
                           borderColor: 'transparent' }}>
            Forget {person.name}
          </Button>
        </div>
      </div>
    </>
  );
};

/* ---- Profile tab ------------------------------------------ */
const ProfileTab = ({ person, sessionCount, turnCount, onForget, fire }) => {
  // Rename via PATCH not yet on the people API; surface it as a toast
  // until the endpoint lands. Kept the input for visual continuity.
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(person.name);
  React.useEffect(() => { setDraft(person.name); setEditing(false); }, [person.id]);

  const save = () => {
    if (draft.trim() && draft.trim() !== person.name) {
      fire('rename: PATCH /api/people/{id} not implemented yet');
    }
    setEditing(false);
  };

  return (
    <>
      <div style={{ padding: '20px 16px', display: 'flex', alignItems: 'center', gap: 16, borderBottom: '1px solid var(--border-soft)' }}>
        <Avatar name={person.name} size="xl"/>
        <div style={{ minWidth: 0, flex: 1 }}>
          {editing ? (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input autoFocus value={draft} onChange={e => setDraft(e.target.value)}
                     onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setDraft(person.name); setEditing(false); } }}
                     style={{ font: 'inherit', fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em',
                              padding: '4px 8px', borderRadius: 'var(--r-sm)',
                              border: '1px solid var(--brand)', background: 'var(--card)', color: 'var(--fg)' }}/>
              <Button icon="check" variant="primary" onClick={save}>save</Button>
              <Button icon="x" onClick={() => { setDraft(person.name); setEditing(false); }}>cancel</Button>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <h2 style={{ margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em' }}>{person.name}</h2>
              {isLive(person.last_seen_at) && <Pill tone="live" live>live</Pill>}
              <Button icon="pencil" onClick={() => setEditing(true)}>rename</Button>
            </div>
          )}
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 6 }}>
            person · #{person.id} · added {relTime(person.created_at)}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: '1px solid var(--border-soft)' }}>
        <div style={{ padding: '14px 16px', borderRight: '1px solid var(--border-soft)' }}>
          <div className="label">last heard</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 2 }}>{relTime(person.last_seen_at)}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>presence: {person.presence_tier || '—'}</div>
        </div>
        <div style={{ padding: '14px 16px', borderRight: '1px solid var(--border-soft)' }}>
          <div className="label">voice profiles</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 2 }}>{person.voice_profile_count}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>enrolled</div>
        </div>
        <div style={{ padding: '14px 16px', borderRight: '1px solid var(--border-soft)' }}>
          <div className="label">sessions</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 2 }}>{sessionCount}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>recent</div>
        </div>
        <div style={{ padding: '14px 16px' }}>
          <div className="label">turns</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 2 }}>{turnCount}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>conversation_log rows</div>
        </div>
      </div>

      <div style={{ padding: 16, borderBottom: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6 }}>notes</div>
        <div style={{ fontSize: 13, color: person.notes ? 'var(--fg)' : 'var(--fg-faint)' }}>
          {person.notes || 'no notes yet'}
        </div>
      </div>

      <div style={{ padding: 16, display: 'flex', alignItems: 'center', gap: 8, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
        <div style={{ marginLeft: 'auto' }}>
          <Button icon="trash-2" onClick={onForget}
                  style={{ background: 'var(--err-soft)', color: 'var(--err)', borderColor: 'transparent' }}>
            forget person
          </Button>
        </div>
      </div>
    </>
  );
};

/* ---- Memory tab (memories / favorites / preferences) ----- */
/*
 * Memory tab surfaces the three personalization stores for this
 * person. Memories with status='pending' come from the implicit
 * extractor worker — the voice flow handles them via a yes/no offer,
 * the UI offers approve / reject buttons here too. Rejected rows are
 * collapsed by default so the active list stays focused.
 */
const MemoryRow = ({ m, dimmed, onApprove, onReject, onDelete }) => (
  <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border-soft)',
                display: 'grid', gridTemplateColumns: '1fr auto auto auto',
                gap: 10, alignItems: 'center', opacity: dimmed ? 0.55 : 1 }}>
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 13 }}>{m.body}</div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
        {m.source} · {m.status}{m.topic ? ` · ${m.topic}` : ''} · {relTime(m.created_at)}
      </div>
    </div>
    {onApprove && <Button icon="check" variant="primary" onClick={onApprove}>approve</Button>}
    {onReject && <Button icon="x" onClick={onReject}>reject</Button>}
    <Button icon="trash-2" onClick={onDelete}
            style={{ background: 'var(--err-soft)', color: 'var(--err)', borderColor: 'transparent' }}>
      forget
    </Button>
  </div>
);

const MemoryTab = ({ person, memories, favorites, preferences, loading, onRefresh, fire }) => {
  const [newMemBody, setNewMemBody] = React.useState('');
  const [newFavKind, setNewFavKind] = React.useState('');
  const [newFavValue, setNewFavValue] = React.useState('');

  const submitMemory = async () => {
    const body = newMemBody.trim();
    if (!body) return;
    try {
      await apiPost(`/api/people/${person.id}/memories`, { body });
      setNewMemBody('');
      fire('saved memory');
      onRefresh();
    } catch (e) { fire(`save failed: ${e.message}`); }
  };
  const setMemoryStatus = async (mem, status) => {
    try {
      await apiPatch(`/api/people/${person.id}/memories/${mem.id}`, { status });
      fire(status === 'active' ? 'approved' : 'rejected');
      onRefresh();
    } catch (e) { fire(`patch failed: ${e.message}`); }
  };
  const deleteMemory = async (mem) => {
    if (!window.confirm(`Forget "${mem.body}"?`)) return;
    try {
      await apiDelete(`/api/people/${person.id}/memories/${mem.id}`);
      fire('deleted');
      onRefresh();
    } catch (e) { fire(`delete failed: ${e.message}`); }
  };
  const submitFavorite = async () => {
    const kind = newFavKind.trim().toLowerCase();
    const value = newFavValue.trim();
    if (!kind || !value) return;
    try {
      await apiPost(`/api/people/${person.id}/favorites`, { kind, value, rank: 0 });
      setNewFavKind(''); setNewFavValue('');
      fire('saved favorite');
      onRefresh();
    } catch (e) { fire(`save failed: ${e.message}`); }
  };
  const deleteFavorite = async (fav) => {
    if (!window.confirm(`Forget favorite ${fav.kind} = ${fav.value}?`)) return;
    try {
      await apiDelete(`/api/people/${person.id}/favorites/${fav.id}`);
      fire('deleted');
      onRefresh();
    } catch (e) { fire(`delete failed: ${e.message}`); }
  };

  const favsByKind = {};
  for (const f of favorites) (favsByKind[f.kind] = favsByKind[f.kind] || []).push(f);

  const activeMemories   = memories.filter(m => m.status === 'active');
  const pendingMemories  = memories.filter(m => m.status === 'pending');
  const rejectedMemories = memories.filter(m => m.status === 'rejected');

  if (loading && memories.length === 0 && favorites.length === 0 && Object.keys(preferences || {}).length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading memory…</div>;

  return (
    <div>
      {/* Memories */}
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Memories</div>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
          {activeMemories.length} active{pendingMemories.length ? ` · ${pendingMemories.length} pending` : ''}
        </span>
      </div>

      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', gap: 8, alignItems: 'center' }}>
        <input value={newMemBody} onChange={e => setNewMemBody(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') submitMemory(); }}
               placeholder="add a memory…  e.g. allergic to peanuts"
               style={{ font: 'inherit', fontSize: 13, flex: 1, height: 30, padding: '0 10px',
                        borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                        background: 'var(--card)', color: 'var(--fg)',
                        boxShadow: 'var(--inner-highlight)' }}/>
        <Button icon="plus" variant="primary" disabled={!newMemBody.trim()} onClick={submitMemory}>save</Button>
      </div>

      {pendingMemories.length > 0 && (
        <div style={{ borderBottom: '1px solid var(--border-soft)', background: 'var(--brand-soft)' }}>
          <div className="label" style={{ padding: '10px 16px 4px' }}>pending — extracted from conversation</div>
          {pendingMemories.map(m => (
            <MemoryRow key={m.id} m={m}
                       onApprove={() => setMemoryStatus(m, 'active')}
                       onReject={() => setMemoryStatus(m, 'rejected')}
                       onDelete={() => deleteMemory(m)}/>
          ))}
        </div>
      )}

      {activeMemories.length === 0 && pendingMemories.length === 0
        ? <div style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--fg-faint)' }}>
            no memories yet · say "remember that ___" or use the input above
          </div>
        : activeMemories.map(m => (
            <MemoryRow key={m.id} m={m} onDelete={() => deleteMemory(m)}/>
          ))}

      {rejectedMemories.length > 0 && (
        <details style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)' }}>
          <summary style={{ fontSize: 12, color: 'var(--fg-muted)', cursor: 'pointer' }}>
            {rejectedMemories.length} rejected (hidden)
          </summary>
          <div style={{ marginTop: 8 }}>
            {rejectedMemories.map(m => (
              <MemoryRow key={m.id} m={m} dimmed onDelete={() => deleteMemory(m)}/>
            ))}
          </div>
        </details>
      )}

      {/* Favorites */}
      <div style={{ padding: '14px 16px', borderTop: '1px solid var(--border-soft)',
                    borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Favorites</div>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
          {favorites.length} saved · {Object.keys(favsByKind).length} kind{Object.keys(favsByKind).length === 1 ? '' : 's'}
        </span>
      </div>

      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', gap: 8, alignItems: 'center' }}>
        <input value={newFavKind} onChange={e => setNewFavKind(e.target.value)}
               placeholder="kind  e.g. team"
               style={{ font: 'inherit', fontSize: 13, width: 110, height: 30, padding: '0 10px',
                        borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                        background: 'var(--card)', color: 'var(--fg)',
                        boxShadow: 'var(--inner-highlight)' }}/>
        <input value={newFavValue} onChange={e => setNewFavValue(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') submitFavorite(); }}
               placeholder="value  e.g. Mariners"
               style={{ font: 'inherit', fontSize: 13, flex: 1, height: 30, padding: '0 10px',
                        borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                        background: 'var(--card)', color: 'var(--fg)',
                        boxShadow: 'var(--inner-highlight)' }}/>
        <Button icon="plus" variant="primary"
                disabled={!newFavKind.trim() || !newFavValue.trim()}
                onClick={submitFavorite}>save</Button>
      </div>

      {favorites.length === 0
        ? <div style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--fg-faint)' }}>
            no favorites yet · say "my favorite ___ is ___" or use the inputs above
          </div>
        : Object.entries(favsByKind).map(([kind, items]) => (
            <div key={kind} style={{ padding: '10px 16px', borderBottom: '1px solid var(--border-soft)',
                                      display: 'grid', gridTemplateColumns: '110px 1fr',
                                      gap: 12, alignItems: 'center' }}>
              <span className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{kind}</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {items.map(f => (
                  <span key={f.id}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                                 padding: '4px 10px', borderRadius: '999px',
                                 background: 'var(--sunken)', border: '1px solid var(--border)',
                                 fontSize: 12 }}>
                    {f.value}
                    <button onClick={() => deleteFavorite(f)} title="forget"
                            style={{ font: 'inherit', cursor: 'pointer', background: 'transparent',
                                     border: 'none', padding: 0, color: 'var(--fg-faint)',
                                     display: 'inline-flex', alignItems: 'center' }}>
                      <Icon name="x" size={11}/>
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}

      {/* Preferences (read-only for v1 — JSONB edits via voice or backend) */}
      <div style={{ padding: '14px 16px', borderTop: '1px solid var(--border-soft)',
                    borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Preferences</div>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
          {Object.keys(preferences || {}).length} key{Object.keys(preferences || {}).length === 1 ? '' : 's'}
        </span>
      </div>

      {Object.keys(preferences || {}).length === 0
        ? <div style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--fg-faint)' }}>
            no preferences set yet
          </div>
        : <div>
            {Object.entries(preferences || {}).map(([key, value]) => (
              <div key={key} style={{ padding: '8px 16px', borderBottom: '1px solid var(--border-soft)',
                                       display: 'grid', gridTemplateColumns: '140px 1fr', gap: 12 }}>
                <span className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{key}</span>
                <span style={{ fontSize: 13 }}>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>
              </div>
            ))}
          </div>}
    </div>
  );
};

/* ---- Sessions tab ----------------------------------------- */
const SessionRow = ({ session, turns }) => {
  const [open, setOpen] = React.useState(false);
  const dur = session.last_activity && session.started_at
    ? (new Date(session.last_activity) - new Date(session.started_at)) / 1000
    : 0;
  return (
    <div style={{ borderBottom: '1px solid var(--border-soft)' }}>
      <div onClick={() => setOpen(o => !o)}
           style={{ padding: '12px 16px', display: 'grid',
                    gridTemplateColumns: '24px 1fr auto auto auto', gap: 12, alignItems: 'center', cursor: 'pointer' }}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14}/>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>{relTime(session.started_at)}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
            {(session.id || '').slice(0, 8) || '—'} · {fmtDur(Math.round(dur))}
          </div>
        </div>
        <RoomChip name={session.room_id} online/>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{session.intent_count} turn{session.intent_count === 1 ? '' : 's'}</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', minWidth: 80, textAlign: 'right' }}>
          last {relTime(session.last_activity)}
        </span>
      </div>
      {open && (
        <div style={{ padding: '4px 16px 14px 52px', background: 'var(--sunken)' }}>
          {turns.length === 0
            ? <div style={{ fontSize: 12, color: 'var(--fg-faint)', padding: '12px 0' }}>no conversation rows in this session</div>
            : turns.map(c => (
                <div key={c.id} style={{ padding: '10px 0', borderTop: '1px solid var(--border-soft)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{relTime(c.at)}</span>
                    <Pill tone={c.matched_handler ? 'live' : 'idle'}>{c.matched_handler || 'qa'}</Pill>
                    <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>{c.matched_path}</span>
                  </div>
                  <div style={{ fontSize: 13 }}>“{c.user_text}”</div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 3, display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                    <DomovoiGlyph size={12}/>
                    <span>{c.assistant_text}</span>
                  </div>
                </div>
              ))}
        </div>
      )}
    </div>
  );
};

const SessionsTab = ({ person, sessions, conversations, loading }) => {
  if (loading && sessions.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading sessions…</div>;
  if (sessions.length === 0)
    return <Empty glyph="sleeping" title={`${person.name} hasn't had a session yet`}
                  sub="sessions appear once they've spoken at least once"/>;
  return (
    <div>
      {sessions.map(s => (
        <SessionRow key={s.id} session={s}
                    turns={conversations.filter(c => c.session_id === s.id)}/>
      ))}
    </div>
  );
};

/* ---- Conversations tab ------------------------------------ */
const ConversationTurn = ({ c }) => {
  const long = (c.assistant_text || '').length > 140;
  const [more, setMore] = React.useState(false);
  const txt = c.assistant_text || '';
  const shown = long && !more ? txt.slice(0, 130).trimEnd() + '…' : txt;
  return (
    <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{relTime(c.at)}</span>
        <RoomChip name={c.room_id} online/>
        <Pill tone={c.matched_handler ? 'live' : 'idle'}>{c.matched_handler || 'qa'}</Pill>
        <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', marginLeft: 'auto' }}>#{c.id}</span>
      </div>
      <div style={{ fontSize: 13, marginBottom: 4 }}>“{c.user_text}”</div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', display: 'flex', alignItems: 'flex-start', gap: 6 }}>
        <DomovoiGlyph size={12}/>
        <span>{shown}{long && (
          <button onClick={() => setMore(m => !m)}
                  style={{ font: 'inherit', fontSize: 12, marginLeft: 4, background: 'none', border: 'none',
                           color: 'var(--brand)', cursor: 'pointer', padding: 0 }}>
            {more ? 'less' : 'more'}
          </button>
        )}</span>
      </div>
    </div>
  );
};

const ConversationsTab = ({ person, conversations, loading }) => {
  const [q, setQ] = React.useState('');
  const filtered = conversations.filter(c => {
    if (!q) return true;
    const hay = ((c.user_text || '') + ' ' + (c.assistant_text || '')).toLowerCase();
    return hay.includes(q.toLowerCase());
  });
  if (loading && conversations.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading conversations…</div>;
  return (
    <>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ position: 'relative', flex: 1, maxWidth: 360 }}>
          <input value={q} onChange={e => setQ(e.target.value)}
                 placeholder="search what they said or what domovoi said…"
                 style={{ font: 'inherit', fontSize: 13, width: '100%', height: 30,
                          padding: '0 10px 0 28px', borderRadius: 'var(--r-sm)',
                          border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)',
                          boxShadow: 'var(--inner-highlight)' }}/>
          <span style={{ position: 'absolute', left: 9, top: 8, color: 'var(--fg-subtle)', pointerEvents: 'none' }}>
            <Icon name="search" size={13}/>
          </span>
        </div>
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-faint)' }}>
          {filtered.length} of {conversations.length}
        </span>
      </div>
      {filtered.length === 0
        ? <Empty glyph="sleeping" title="nothing matches" sub={q ? `q = “${q}”` : `${person.name} hasn't said anything yet`}/>
        : <div>{filtered.map(c => <ConversationTurn key={c.id} c={c}/>)}</div>}
    </>
  );
};

/* ---- Denylist sub-view ------------------------------------ */
const DenylistView = ({ count, onBack }) => (
  <Card>
    <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid var(--border-soft)' }}>
      <Button icon="chevron-left" onClick={onBack}>back</Button>
      <div style={{ fontSize: 14, fontWeight: 600, marginLeft: 4 }}>Voice denylist</div>
      <Pill tone="idle" style={{ marginLeft: 'auto' }}>{count} opted out</Pill>
    </div>
    <div style={{ padding: 24, textAlign: 'center' }}>
      <div style={{ fontSize: 13, color: 'var(--fg-muted)', maxWidth: 440, margin: '0 auto', lineHeight: 1.55 }}>
        Opted-out voices are never persisted, never named, and never appear in this list. Domovoi hashes their
        embedding so it can ignore them on future utterances — but the hash isn't reversible to anything human-readable.
      </div>
      <div style={{ marginTop: 18, display: 'inline-flex', alignItems: 'center', gap: 12,
                    padding: '14px 18px', background: 'var(--sunken)', border: '1px solid var(--border-soft)',
                    borderRadius: 'var(--r-md)' }}>
        <div style={{ width: 32, height: 32, borderRadius: '50%', background: 'var(--card)',
                      border: '1px solid var(--border)', display: 'grid', placeItems: 'center', color: 'var(--fg-muted)' }}>
          <Icon name="mic-off" size={14}/>
        </div>
        <div style={{ textAlign: 'left' }}>
          <div style={{ fontSize: 22, fontWeight: 600 }}>{count}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>hashes on file</div>
        </div>
      </div>
    </div>
  </Card>
);

/* ---- Page ------------------------------------------------- */
const PeoplePage = () => {
  const [selectedId, setSelectedId] = React.useState(null);
  const [view, setView] = React.useState('roster'); // roster | denylist
  const [tab, setTab] = React.useState('profile');
  const [search, setSearch] = React.useState('');
  const [confirmFor, setConfirmFor] = React.useState(null);
  const [fire, toastNode] = useToast();

  const { items: people, refresh: refreshPeople } =
    useApiList('/api/people', { eventTypes: ['people.last_seen.changed'] });
  const { items: denylist } = useApiList('/api/denylist');
  const denylistCount = denylist.length;

  // Per-person fetches; refetch when the selection changes OR when
  // a memory/favorite mutation bumps the tick.
  const [sessions, setSessions] = React.useState([]);
  const [conversations, setConversations] = React.useState([]);
  const [memories, setMemories] = React.useState([]);
  const [favorites, setFavorites] = React.useState([]);
  const [preferences, setPreferences] = React.useState({});
  const [perPersonLoading, setPerPersonLoading] = React.useState(false);
  const [refreshTick, setRefreshTick] = React.useState(0);
  const refreshProfile = React.useCallback(() => setRefreshTick(t => t + 1), []);

  React.useEffect(() => {
    if (selectedId == null) {
      setSessions([]); setConversations([]);
      setMemories([]); setFavorites([]); setPreferences({});
      return;
    }
    let cancelled = false;
    setPerPersonLoading(true);
    (async () => {
      const [ss, cc, mm, ff, pp] = await Promise.all([
        apiGet(`/api/people/${selectedId}/sessions?limit=50`).catch(() => []),
        apiGet(`/api/people/${selectedId}/conversations?limit=200`).catch(() => []),
        apiGet(`/api/people/${selectedId}/memories`).catch(() => []),
        apiGet(`/api/people/${selectedId}/favorites`).catch(() => []),
        apiGet(`/api/people/${selectedId}/preferences`).catch(() => ({})),
      ]);
      if (cancelled) return;
      setSessions(ss || []);
      setConversations(cc || []);
      setMemories(mm || []);
      setFavorites(ff || []);
      setPreferences(pp || {});
      setPerPersonLoading(false);
    })();
    return () => { cancelled = true; };
  }, [selectedId, refreshTick]);

  const selected = people.find(p => p.id === selectedId) || null;
  const filtered = people.filter(p => !search || (p.name || '').toLowerCase().includes(search.toLowerCase()));
  const liveCount = people.filter(p => isLive(p.last_seen_at)).length;

  const onForget = async (person) => {
    setConfirmFor(null);
    try {
      await apiDelete(`/api/people/${person.id}`);
      fire(`forgot ${person.name} · cascade complete`);
      setSelectedId(null);
      refreshPeople();
    } catch (e) {
      fire(`forget failed: ${e.message}`);
    }
  };

  const tabs = [
    { id: 'profile',       label: 'Profile' },
    { id: 'memory',        label: 'Memory',
      count: memories.length + favorites.length },
    { id: 'sessions',      label: 'Sessions',      count: sessions.length },
    { id: 'conversations', label: 'Conversations', count: conversations.length },
  ];

  return (
    <div className="page">
      <PageHeader
        title="People"
        sub={`${people.length} enrolled · ${liveCount} heard in the last 5 min`}
      />

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16, alignItems: 'stretch' }}>
        {/* Left rail */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Card>
            <div style={{ padding: '12px', borderBottom: '1px solid var(--border-soft)' }}>
              <div style={{ position: 'relative' }}>
                <input value={search} onChange={e => setSearch(e.target.value)}
                       placeholder="filter by name…"
                       style={{ font: 'inherit', fontSize: 13, width: '100%', height: 30,
                                padding: '0 10px 0 28px', borderRadius: 'var(--r-sm)',
                                border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)',
                                boxShadow: 'var(--inner-highlight)' }}/>
                <span style={{ position: 'absolute', left: 9, top: 8, color: 'var(--fg-subtle)', pointerEvents: 'none' }}>
                  <Icon name="search" size={13}/>
                </span>
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {filtered.length === 0
                ? <div style={{ padding: 18, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>
                    {people.length === 0 ? 'nobody enrolled yet' : 'no match'}
                  </div>
                : filtered.map(p => {
                    const active = view === 'roster' && selectedId === p.id;
                    const live = isLive(p.last_seen_at);
                    return (
                      <button key={p.id}
                              onClick={() => { setView('roster'); setSelectedId(p.id); setTab('profile'); }}
                              style={{ font: 'inherit', textAlign: 'left',
                                       display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 10, alignItems: 'center',
                                       padding: '10px 14px', cursor: 'pointer',
                                       background: active ? 'var(--brand-soft)' : 'transparent',
                                       border: 'none', borderTop: '1px solid var(--border-soft)',
                                       borderLeftWidth: 3, borderLeftStyle: 'solid',
                                       borderLeftColor: active ? 'var(--brand)' : 'transparent' }}>
                        <Avatar name={p.name} size="lg"/>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ fontSize: 14, fontWeight: 500,
                                           color: active ? 'var(--brand-press)' : 'var(--fg)' }}>{p.name}</span>
                            {live && <StatusDot tone="ok" live/>}
                          </div>
                          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                            {relTime(p.last_seen_at)} · {p.presence_tier || 'high'}
                          </div>
                        </div>
                        <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>
                          {p.voice_profile_count}p
                        </span>
                      </button>
                    );
                  })}
            </div>
          </Card>

          <button onClick={() => { setView('denylist'); setSelectedId(null); }}
                  style={{ font: 'inherit', cursor: 'pointer', textAlign: 'left',
                           padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10,
                           background: view === 'denylist' ? 'var(--brand-soft)' : 'var(--card)',
                           border: '1px solid var(--border)',
                           borderRadius: 'var(--r-md)',
                           color: view === 'denylist' ? 'var(--brand-press)' : 'var(--fg)' }}>
            <Icon name="mic-off" size={14}/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 500 }}>Voice denylist</div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>opted-out voices · names redacted</div>
            </div>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{denylistCount}</span>
          </button>
        </div>

        {/* Right pane */}
        {view === 'denylist' ? (
          <DenylistView count={denylistCount} onBack={() => setView('roster')}/>
        ) : !selected ? (
          <Card>
            <div style={{ padding: '64px 24px', textAlign: 'center' }}>
              <div style={{ display: 'inline-block', color: 'var(--fg-subtle)', marginBottom: 12 }}>
                <SleepingDomovoi size={120}/>
              </div>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg)' }}>Pick someone from the list</div>
              <div style={{ fontSize: 13, color: 'var(--fg-muted)', marginTop: 6, maxWidth: 360, margin: '6px auto 0' }}>
                Profiles, sessions, and the full conversation log live here. Domovoi's napping until you choose.
              </div>
            </div>
          </Card>
        ) : (
          <Card>
            <Tabs tabs={tabs} value={tab} onChange={setTab} padX={16}/>
            {tab === 'profile' && (
              <ProfileTab person={selected}
                          sessionCount={sessions.length}
                          turnCount={conversations.length}
                          onForget={() => setConfirmFor(selected)}
                          fire={fire}/>
            )}
            {tab === 'memory' && (
              <MemoryTab person={selected}
                         memories={memories}
                         favorites={favorites}
                         preferences={preferences}
                         loading={perPersonLoading}
                         onRefresh={refreshProfile}
                         fire={fire}/>
            )}
            {tab === 'sessions' && (
              <SessionsTab person={selected} sessions={sessions}
                           conversations={conversations} loading={perPersonLoading}/>
            )}
            {tab === 'conversations' && (
              <ConversationsTab person={selected} conversations={conversations}
                                loading={perPersonLoading}/>
            )}
          </Card>
        )}
      </div>

      <ConfirmForget person={confirmFor}
                     sessionCount={sessions.length}
                     turnCount={conversations.length}
                     open={!!confirmFor}
                     onClose={() => setConfirmFor(null)} onConfirm={onForget}/>
      {toastNode}
    </div>
  );
};

window.PeoplePage = PeoplePage;
