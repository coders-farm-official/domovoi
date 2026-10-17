/* People page — household roster + per-person profile / sessions / conversations.
 *
 * Data sources:
 *   * GET /api/people                          — roster.
 *   * GET /api/people/{id}/sessions            — sessions for the selected person.
 *   * GET /api/people/{id}/conversations       — conversation_log rows for them.
 *   * GET /api/people/{id}/memories|favorites|preferences — the Memory tab.
 *   * GET /api/people/{id}/profiles            — their voice samples, satellite
 *                                                and phone sets (Profile tab).
 *   * GET /api/denylist                        — opted-out voice count.
 *   * GET /api/voice-history                   — "Keep voice history", what is
 *                                                kept, the latest cleanup/delete.
 *   * /ws/state · `people.last_seen.changed`   — push refresh of the roster.
 *
 * Sessions and conversations are fetched per-person on selection; we
 * don't preload them all because conversation_log can be large.
 *
 * Conversations, memories, favorites and preferences are household speech
 * and personal content: the server reads them on the DEVICE tier, so an
 * unpaired browser is asked to pair (apiGet opens the prompt) and the
 * per-person fetch re-runs once it has (useRetryAfterCredential).
 */

const isLive = (iso) => {
  if (!iso) return false;
  return (new Date() - new Date(iso)) / 1000 < 300;
};

// How many of a person's turns the page reads (the Conversations tab).
const PERSON_TURNS_FETCHED = 200;

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
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Forget {person.name}{person.namesakes > 0 ? ` (#${person.id})` : ''}?
          </div>
        </div>
        <div style={{ padding: 16, fontSize: 13, color: 'var(--fg)' }}>
          <p style={{ margin: 0, marginBottom: 10 }}>This is a cascade delete. Domovoi will lose:</p>
          <ul style={{ margin: 0, marginBottom: 8, paddingLeft: 18, color: 'var(--fg-muted)', fontSize: 12, lineHeight: 1.8 }}>
            <li><span className="mono" style={{ color: 'var(--fg)' }}>{person.voice_profile_count}</span> voice profile{person.voice_profile_count === 1 ? '' : 's'}</li>
            <li>what they said: <span className="mono" style={{ color: 'var(--fg)' }}>{turnCount}{turnCount >= PERSON_TURNS_FETCHED ? '+' : ''}</span> conversation turn{turnCount === 1 ? '' : 's'}{sessionCount > 0 && <> in <span className="mono" style={{ color: 'var(--fg)' }}>{sessionCount}</span> session{sessionCount === 1 ? '' : 's'}</>}, with Domovoi's answers</li>
            <li>their memories, favorites and preferences</li>
          </ul>
          <p style={{ margin: 0, marginBottom: 12, fontSize: 12, color: 'var(--fg-muted)' }}>
            Turns where someone else said their name stay: Domovoi goes by who was speaking.
          </p>
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

/* ---- Voice samples (Profile tab) -------------------------- */
/*
 * A person's voice samples in their two sets (V026): satellite samples,
 * heard by a room's satellite (what satellites recognize them by), and
 * phone samples, said into the app's chat mic (what a phone turn
 * recognizes them by first; satellites never use them). Metadata only,
 * never an embedding. Deleting one is admin-tier on the server.
 */
const PERSON_VOICE_SAMPLE_SETS = [
  { key: 'satellite', label: 'satellite samples',
    hint: 'heard by a room’s satellite — what satellites recognize this voice by' },
  { key: 'phone', label: 'phone samples',
    hint: 'said into the app’s chat mic — what the phone recognizes this voice by first; satellites never use them' },
];

/*
 * A claimed sample (V029): added because somebody said this person's name
 * and "yes", or learned by a room from a voice recognized only through such
 * a sample. Matching uses it; "forget me" from a voice that doesn't match
 * the person's own removes only these. Where and by whom reach an admin
 * only (the server leaves them out for everyone else, with a phone
 * sample's room, which names the phone).
 */
const personVoiceSampleClaim = (s) => {
  if (!s.claimed) return null;
  if (s.claim_of) return `learned through the name claim #${s.claim_of}`;
  const where = s.claim_room ? ` in ${s.claim_room}` : '';
  const who = s.claimed_by_name ? ` by a voice recognized as ${s.claimed_by_name}` : '';
  return `added by saying the name${where}${who}`;
};

const PersonVoiceSampleRow = ({ sample, onDelete }) => (
  <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border-soft)',
                display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'center' }}>
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 13, overflowWrap: 'anywhere' }}>
        {sample.room_id || (sample.source === 'phone' ? 'a phone' : 'no room')}
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
        #{sample.id} · added {sample.enrolled_at ? new Date(sample.enrolled_at).toLocaleString() : '—'} ({relTime(sample.enrolled_at)})
      </div>
      {sample.claimed && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--warn, var(--fg-muted))', marginTop: 2 }}>
          {personVoiceSampleClaim(sample)}
        </div>
      )}
    </div>
    <Button icon="trash-2" onClick={() => onDelete(sample)}
            style={{ background: 'var(--err-soft)', color: 'var(--err)', borderColor: 'transparent' }}>
      delete
    </Button>
  </div>
);

const PersonVoiceSamples = ({ samples, onDelete }) => (
  <div style={{ borderBottom: '1px solid var(--border-soft)' }}>
    {PERSON_VOICE_SAMPLE_SETS.map(set => {
      const rows = samples.filter(s => (s.source || 'satellite') === set.key);
      return (
        <div key={set.key}>
          <div style={{ padding: '12px 16px 2px', display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <div className="label">{set.label}</div>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{rows.length}</span>
          </div>
          <div style={{ padding: '0 16px 8px', fontSize: 12, color: 'var(--fg-muted)' }}>{set.hint}</div>
          {rows.length === 0
            ? <div style={{ padding: '0 16px 12px', fontSize: 12, color: 'var(--fg-faint)' }}>none</div>
            : rows.map(s => <PersonVoiceSampleRow key={s.id} sample={s} onDelete={onDelete}/>)}
        </div>
      );
    })}
  </div>
);

/* ---- Same name as somebody else ---------------------------- */
/*
 * One name, one person (handlers/voice_profile.py): voice enrolment makes
 * a second person with a name only when told to (a "no" to "I already know
 * someone called X. Is that you?"), and the rename below refuses one
 * someone else has (409). People who share a name (that "no", an
 * enrolment from before the rule, a parent and child) are flagged here, because when
 * the name is said Domovoi won't guess which of them is meant — "my name
 * is …" and "who am I" both send the speaker to this page.
 */
const PersonNamesakes = ({ person }) => (
  <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)',
                background: 'var(--warn-soft, var(--sunken))', fontSize: 13, lineHeight: 1.5 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600 }}>
      <Icon name="users" size={14}/>
      {person.namesakes + 1} people are called {person.name}
    </div>
    <div style={{ color: 'var(--fg-muted)', marginTop: 4 }}>
      When someone says “my name is {person.name}”, Domovoi won’t guess which one they are.
      If these are the same person, forget the extra one (below); they can then say
      “my name is {person.name}” wherever Domovoi doesn’t know their voice yet.
      If they are different people, rename one of them.
    </div>
  </div>
);

/* ---- Profile tab ------------------------------------------ */
const ProfileTab = ({ person, sessionCount, turnCount, samples, onDeleteSample, onForget, onRenamed, fire }) => {
  // Rename: PATCH /api/people/{id}, admin tier. A name somebody else has
  // is refused (409) with the server's reason, and the box stays open.
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(person.name);
  const [saving, setSaving] = React.useState(false);
  React.useEffect(() => { setDraft(person.name); setEditing(false); }, [person.id]);

  const save = async () => {
    const name = draft.trim().replace(/\s+/g, ' ');
    if (!name || name === person.name) { setEditing(false); return; }
    setSaving(true);
    try {
      await apiPatch(`/api/people/${person.id}`, { name });
      fire(`renamed to ${name}`);
      setEditing(false);
      onRenamed();
    } catch (e) {
      reportMutationFailure(fire, 'rename', e, { kept: true });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div style={{ padding: '20px 16px', display: 'flex', alignItems: 'center', gap: 16, borderBottom: '1px solid var(--border-soft)' }}>
        <Avatar name={person.name} size="xl"/>
        <div style={{ minWidth: 0, flex: 1 }}>
          {editing ? (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input autoFocus value={draft} maxLength={60} disabled={saving}
                     aria-label="new name"
                     onChange={e => setDraft(e.target.value)}
                     onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setDraft(person.name); setEditing(false); } }}
                     style={{ font: 'inherit', fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em',
                              padding: '4px 8px', borderRadius: 'var(--r-sm)', minWidth: 0,
                              border: '1px solid var(--brand)', background: 'var(--card)', color: 'var(--fg)' }}/>
              <Button icon="check" variant="primary" onClick={save} disabled={saving}>save</Button>
              <Button icon="x" onClick={() => { setDraft(person.name); setEditing(false); }}>cancel</Button>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <h2 style={{ margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em' }}>{person.name}</h2>
              {isLive(person.last_seen_at) && <Pill tone="live" live>live</Pill>}
              {person.namesakes > 0 && <Pill tone="warn">same name</Pill>}
              <Button icon="pencil" onClick={() => setEditing(true)}>rename</Button>
            </div>
          )}
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 6 }}>
            person · #{person.id} · added {relTime(person.created_at)}
          </div>
        </div>
      </div>

      {person.namesakes > 0 && <PersonNamesakes person={person}/>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: '1px solid var(--border-soft)' }}>
        <div style={{ padding: '14px 16px', borderRight: '1px solid var(--border-soft)' }}>
          <div className="label">last heard</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 2 }}>{relTime(person.last_seen_at)}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>presence: {person.presence_tier || '—'}</div>
        </div>
        <div style={{ padding: '14px 16px', borderRight: '1px solid var(--border-soft)' }}>
          <div className="label">voice profiles</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 2 }}>{person.voice_profile_count}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
            {samples.filter(s => s.source === 'phone').length} from the phone
          </div>
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

      <PersonVoiceSamples samples={samples} onDelete={onDeleteSample}/>

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

const MemoryTab = ({ person, memories, favorites, preferences, loading, refused, onRefresh, fire }) => {
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
    } catch (e) { reportMutationFailure(fire, 'save', e); }
  };
  const setMemoryStatus = async (mem, status) => {
    try {
      await apiPatch(`/api/people/${person.id}/memories/${mem.id}`, { status });
      fire(status === 'active' ? 'approved' : 'rejected');
      onRefresh();
    } catch (e) { reportMutationFailure(fire, status === 'active' ? 'approve' : 'reject', e); }
  };
  const deleteMemory = async (mem) => {
    if (!window.confirm(`Forget "${mem.body}"?`)) return;
    try {
      await apiDelete(`/api/people/${person.id}/memories/${mem.id}`);
      fire('deleted');
      onRefresh();
    } catch (e) {
      // The login modal owns a 401 (data.js) — no raw toast behind it (F-006).
      reportMutationFailure(fire, 'delete', e);
    }
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
    } catch (e) { reportMutationFailure(fire, 'save', e); }
  };
  const deleteFavorite = async (fav) => {
    if (!window.confirm(`Forget favorite ${fav.kind} = ${fav.value}?`)) return;
    try {
      await apiDelete(`/api/people/${person.id}/favorites/${fav.id}`);
      fire('deleted');
      onRefresh();
    } catch (e) {
      reportMutationFailure(fire, 'delete', e);
    }
  };

  const favsByKind = {};
  for (const f of favorites) (favsByKind[f.kind] = favsByKind[f.kind] || []).push(f);

  const activeMemories   = memories.filter(m => m.status === 'active');
  const pendingMemories  = memories.filter(m => m.status === 'pending');
  const rejectedMemories = memories.filter(m => m.status === 'rejected');

  if (loading && memories.length === 0 && favorites.length === 0 && Object.keys(preferences || {}).length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading memory…</div>;
  if (refused && memories.length === 0 && favorites.length === 0)
    return <PairedOnlyEmpty what={`what the house keeps about ${person.name}`}/>;

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

const ConversationsTab = ({ person, conversations, loading, refused }) => {
  const [q, setQ] = React.useState('');
  const filtered = conversations.filter(c => {
    if (!q) return true;
    const hay = ((c.user_text || '') + ' ' + (c.assistant_text || '')).toLowerCase();
    return hay.includes(q.toLowerCase());
  });
  if (loading && conversations.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading conversations…</div>;
  if (refused && conversations.length === 0)
    return <PairedOnlyEmpty what={`what ${person.name} said`}/>;
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

/* ---- Voice history sub-view (owner, 2026-10-17) ----------- */
/*
 * How long Domovoi keeps what people say to it, and deleting all of it
 * (domovoi/voice_history.py):
 *   GET   /api/voice-history             — the setting, what is kept and
 *                                          since when, the latest cleanup and
 *                                          delete (counts and dates only)
 *   PATCH /api/config/editable           — { changes: { voice_history_keep } }
 *                                          (admin; also Settings → Configuration
 *                                          → Privacy)
 *   POST  /api/voice-history/delete-all  — admin; runs as a job on the server,
 *                                          its progress is read back here
 */
const VOICE_HISTORY_GOES = [
  'what everyone said to Domovoi, and what it answered, in every room and on every phone: the Conversations and Sessions tabs',
  'the routing log behind them',
  'each room’s short-term conversation (what “say that again” and a follow-up question use)',
  'the satellites’ logs kept on the server, which quote what they heard',
];
const VOICE_HISTORY_STAYS =
  'People, voice samples, memories, notes, timers, the music played, chat threads and command recordings stay.';

const voiceHistoryDays = (keep) => (keep === 'forever' ? null : Number(keep));

const VoiceHistoryJobLine = ({ job }) => {
  if (!job) return null;
  const d = job.deleted || {};
  const turns = d.conversation_log || 0;
  if (job.state === 'queued' || job.state === 'running')
    return <span>Deleting all voice history… {turns} turn{turns === 1 ? '' : 's'} so far.</span>;
  if (job.state === 'failed')
    return <span style={{ color: 'var(--err)' }}>The delete stopped ({job.error}). The server tries again within the hour.</span>;
  return (
    <span>
      Deleted everything said before {new Date(job.cutoff).toLocaleString()} ({turns} turn{turns === 1 ? '' : 's'}), {relTime(job.finished_at)}.
    </span>
  );
};

const VoiceHistoryView = ({ vh, onBack, fire }) => {
  const { data, error, refresh } = vh;
  const [pendingKeep, setPendingKeep] = React.useState(null);   // a shorter keep, awaiting confirm
  const [askDelete, setAskDelete] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const job = data && data.delete_all;
  const running = !!job && (job.state === 'queued' || job.state === 'running');
  React.useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [running, refresh]);

  const refused = error && (error.status === 401 || error.status === 403);
  const keep = data ? data.keep : null;
  const choices = (data && data.choices) || [];
  const labelOf = (v) => (choices.find(c => c.value === v) || {}).label || v;

  const saveKeep = async (value) => {
    setBusy(true);
    try {
      const res = await apiPatch('/api/config/editable', { changes: { voice_history_keep: value } });
      const why = res && res.rejected && res.rejected.voice_history_keep;
      if (why) fire(`not saved: ${why}`);
      else fire(value === 'forever' ? 'voice history is kept forever now'
                                    : `voice history is kept for ${labelOf(value)} now`);
      // The server deletes what is now too old right after the save; read
      // the counts again once it has.
      refresh();
      setTimeout(refresh, 2000);
    } catch (e) {
      reportMutationFailure(fire, 'save', e);
    } finally {
      setBusy(false);
      setPendingKeep(null);
    }
  };
  const onKeepChange = (value) => {
    if (value === keep) return;
    const now = voiceHistoryDays(keep), next = voiceHistoryDays(value);
    // Shorter deletes at once: ask first. Longer, or forever, deletes nothing.
    if (next !== null && (now === null || next < now)) setPendingKeep(value);
    else saveKeep(value);
  };
  const deleteAll = async () => {
    setBusy(true);
    try {
      await apiPost('/api/voice-history/delete-all', {});
      fire('deleting all voice history');
      refresh();
    } catch (e) {
      reportMutationFailure(fire, 'delete', e);
    } finally {
      setBusy(false);
      setAskDelete(false);
    }
  };

  const cleanup = data && data.last_cleanup;
  const cleaned = cleanup && cleanup.deleted ? (cleanup.deleted.conversation_log || 0) : 0;

  return (
    <Card>
      <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid var(--border-soft)' }}>
        <Button icon="chevron-left" onClick={onBack}>back</Button>
        <div style={{ fontSize: 14, fontWeight: 600, marginLeft: 4 }}>Voice history</div>
        {data && <Pill tone="idle" style={{ marginLeft: 'auto' }}>{data.keep === 'forever' ? 'kept forever' : `kept ${data.keep_label}`}</Pill>}
      </div>
      {refused && !data ? <PairedOnlyEmpty what="how long the house keeps what was said"/> : (
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16, fontSize: 13, lineHeight: 1.55 }}>
          <div style={{ color: 'var(--fg-muted)', maxWidth: 560 }}>
            Domovoi keeps what people say to it, and what it answered, so the Conversations and Sessions
            tabs can show them. Anything older than you choose here is deleted every day.
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <label htmlFor="voice-history-keep" style={{ fontWeight: 500 }}>Keep voice history</label>
            <select id="voice-history-keep" value={keep || ''} disabled={!data || busy}
                    onChange={e => onKeepChange(e.target.value)}
                    style={{ ..._cfgInput, minWidth: 150 }}>
              {!keep && <option value="" disabled>…</option>}
              {choices.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
            <span style={{ fontSize: 12, color: 'var(--fg-faint)' }}>an admin changes this</span>
          </div>

          <div className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span>
              {!data ? 'reading…'
                : data.turns === 0 ? 'nothing kept yet'
                : `${data.turns} turn${data.turns === 1 ? '' : 's'} kept · the oldest from ${new Date(data.oldest_at).toLocaleDateString()}`}
            </span>
            {data && data.keep === 'forever' && <span>Forever: nothing is deleted on its own.</span>}
            {data && data.keep !== 'forever' && cleanup && cleanup.finished_at && (
              cleanup.state === 'failed'
                ? <span style={{ color: 'var(--err)' }}>the last cleanup, {relTime(cleanup.finished_at)}, stopped ({cleanup.error}); the server tries again within the hour</span>
                : <span>last cleanup {relTime(cleanup.finished_at)}: {cleaned} turn{cleaned === 1 ? '' : 's'} deleted</span>
            )}
            {job && <VoiceHistoryJobLine job={job}/>}
          </div>

          <div style={{ paddingTop: 14, borderTop: '1px solid var(--border-soft)', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <Button icon="trash-2" disabled={!data || busy || running} onClick={() => setAskDelete(true)}
                    style={{ background: 'var(--err-soft)', color: 'var(--err)', borderColor: 'transparent' }}>
              Delete all voice history
            </Button>
            <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
              Forgetting one person (their Profile tab) deletes only what they said.
            </span>
          </div>
        </div>
      )}

      {pendingKeep && (
        <DeleteConfirmDialog title={`Keep voice history for ${labelOf(pendingKeep)}?`} busy={busy}
                             confirmLabel={`Keep ${labelOf(pendingKeep)}`}
                             onCancel={() => setPendingKeep(null)} onConfirm={() => saveKeep(pendingKeep)}>
          <div>Everything said more than {labelOf(pendingKeep)} ago is deleted now, and every day from then on. This can’t be undone.</div>
        </DeleteConfirmDialog>
      )}
      {askDelete && (
        <DeleteConfirmDialog title="Delete all voice history?" busy={busy}
                             confirmLabel="Delete all voice history"
                             onCancel={() => setAskDelete(false)} onConfirm={deleteAll}>
          <div>This deletes, for everyone:</div>
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
            {VOICE_HISTORY_GOES.map(t => <li key={t}>{t}</li>)}
          </ul>
          <div>{VOICE_HISTORY_STAYS}</div>
          <div>This can’t be undone. Backups the server made before an update keep their copy until they’re replaced.</div>
        </DeleteConfirmDialog>
      )}
    </Card>
  );
};

/* ---- Page ------------------------------------------------- */
const PeoplePage = () => {
  const [selectedId, setSelectedId] = React.useState(null);
  const [view, setView] = React.useState('roster'); // roster | denylist | history
  const [tab, setTab] = React.useState('profile');
  const [search, setSearch] = React.useState('');
  const [confirmFor, setConfirmFor] = React.useState(null);
  const [fire, toastNode] = useToast();

  const { items: people, refresh: refreshPeople } =
    useApiList('/api/people', { eventTypes: ['people.last_seen.changed'] });
  const { items: denylist } = useApiList('/api/denylist');
  const denylistCount = denylist.length;
  // "Keep voice history", for the rail's line and the Voice history view.
  // Quiet: an unpaired browser isn't asked to pair just for the rail.
  const voiceHistory = useApiObject('/api/voice-history', { quiet: true, refetchOnAuth: true });

  // Per-person fetches; refetch when the selection changes OR when
  // a memory/favorite mutation bumps the tick.
  const [sessions, setSessions] = React.useState([]);
  const [conversations, setConversations] = React.useState([]);
  const [memories, setMemories] = React.useState([]);
  const [favorites, setFavorites] = React.useState([]);
  const [preferences, setPreferences] = React.useState({});
  const [samples, setSamples] = React.useState([]);
  const [perPersonLoading, setPerPersonLoading] = React.useState(false);
  const [refreshTick, setRefreshTick] = React.useState(0);
  const refreshProfile = React.useCallback(() => setRefreshTick(t => t + 1), []);
  // What a person said and what the house keeps about them (conversations,
  // memories, favorites, preferences) is read on the device tier, so an
  // unpaired browser's first fetch is refused: apiGet opens the pair modal,
  // the tab renders empty, and this refusal re-runs the fetch once the
  // browser is paired (or an admin signs in).
  const [personRefusal, setPersonRefusal] = React.useState(null);
  useRetryAfterCredential(personRefusal, refreshProfile);
  const personPairRefused = !!(personRefusal && personRefusal.deviceTokenRequired);

  React.useEffect(() => {
    if (selectedId == null) {
      setSessions([]); setConversations([]);
      setMemories([]); setFavorites([]); setPreferences({}); setSamples([]);
      setPersonRefusal(null);
      return;
    }
    let cancelled = false;
    let refusal = null;
    const read = (path, empty) => apiGet(path).catch((e) => {
      if (e && (e.status === 401 || e.status === 403) && !refusal) refusal = e;
      return empty;
    });
    setPerPersonLoading(true);
    (async () => {
      const [ss, cc, mm, ff, pp, vs] = await Promise.all([
        read(`/api/people/${selectedId}/sessions?limit=50`, []),
        read(`/api/people/${selectedId}/conversations?limit=${PERSON_TURNS_FETCHED}`, []),
        read(`/api/people/${selectedId}/memories`, []),
        read(`/api/people/${selectedId}/favorites`, []),
        read(`/api/people/${selectedId}/preferences`, {}),
        read(`/api/people/${selectedId}/profiles`, []),
      ]);
      if (cancelled) return;
      setSessions(ss || []);
      setConversations(cc || []);
      setMemories(mm || []);
      setFavorites(ff || []);
      setPreferences(pp || {});
      setSamples(vs || []);
      setPersonRefusal(refusal);
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
      reportMutationFailure(fire, 'forget', e);
    }
  };

  // One voice sample, either set (admin-tier on the server).
  const onDeleteSample = async (sample) => {
    const where = sample.source === 'phone' ? 'phone sample' : 'satellite sample';
    if (!window.confirm(`Delete this ${where} (#${sample.id}) from ${selected ? selected.name : 'this person'}? This can't be undone.`)) return;
    try {
      await apiDelete(`/api/people/${sample.person_id}/profiles/${sample.id}`);
      fire(`deleted ${where} #${sample.id}`);
      refreshProfile();
      refreshPeople();
    } catch (e) {
      reportMutationFailure(fire, 'delete', e);
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
                            {p.namesakes > 0 && <span title="someone else has this name too"><StatusDot tone="warn"/></span>}
                          </div>
                          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                            {p.namesakes > 0 && <>#{p.id} · same name · </>}
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

          <button onClick={() => { setView('history'); setSelectedId(null); }}
                  style={{ font: 'inherit', cursor: 'pointer', textAlign: 'left',
                           padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10,
                           background: view === 'history' ? 'var(--brand-soft)' : 'var(--card)',
                           border: '1px solid var(--border)',
                           borderRadius: 'var(--r-md)',
                           color: view === 'history' ? 'var(--brand-press)' : 'var(--fg)' }}>
            <Icon name="history" size={14}/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 500 }}>Voice history</div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                {voiceHistory.data
                  ? (voiceHistory.data.keep === 'forever' ? 'kept forever' : `kept ${voiceHistory.data.keep_label}`)
                  : 'how long what was said is kept'}
              </div>
            </div>
          </button>
        </div>

        {/* Right pane */}
        {view === 'denylist' ? (
          <DenylistView count={denylistCount} onBack={() => setView('roster')}/>
        ) : view === 'history' ? (
          <VoiceHistoryView vh={voiceHistory} fire={fire} onBack={() => setView('roster')}/>
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
                          samples={samples}
                          onDeleteSample={onDeleteSample}
                          onForget={() => setConfirmFor(selected)}
                          onRenamed={refreshPeople}
                          fire={fire}/>
            )}
            {tab === 'memory' && (
              <MemoryTab person={selected}
                         memories={memories}
                         favorites={favorites}
                         preferences={preferences}
                         loading={perPersonLoading}
                         refused={personPairRefused}
                         onRefresh={refreshProfile}
                         fire={fire}/>
            )}
            {tab === 'sessions' && (
              <SessionsTab person={selected} sessions={sessions}
                           conversations={conversations} loading={perPersonLoading}/>
            )}
            {tab === 'conversations' && (
              <ConversationsTab person={selected} conversations={conversations}
                                loading={perPersonLoading} refused={personPairRefused}/>
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
