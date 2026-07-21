/* Audiobooks page — local .m4b / chaptered books, played in the browser via
 * the shared mini-player (player.jsx). A book becomes a generic queue item
 * (kind='audiobook') carrying chapters + a per-(device × person) resume
 * position. Single-file books stream directly; folder books stream the
 * current chapter file (the player seeks within it).
 *
 * Data sources:
 *   * GET  /api/audiobooks              — indexed books
 *   * POST /api/audiobooks/reindex      — re-walk audiobooks_dir
 *   * /ws/state · podcasts.changed      — refetch on (re)index
 *   * /ws/state · podcast_positions.changed — refetch resume positions
 */

const AudiobooksPage = () => {
  const p = usePlayback();
  const [fire, toastNode] = useToast();
  const { items: books, refetch } = useApiList('/api/audiobooks', { eventTypes: ['podcasts.changed'] });
  const [busy, setBusy] = React.useState(false);
  const [resumePrompt, setResumePrompt] = React.useState(null);

  const reindex = async () => {
    setBusy(true);
    try { const r = await apiPost('/api/audiobooks/reindex', {}); fire(`Indexed ${r.scanned || 0} book(s)`); refetch(); }
    catch { fire('Reindex failed'); }
    setBusy(false);
  };

  const save = (book) => {
    // Single-file books save as the file; folder books arrive zipped —
    // the server may take a moment to build the archive first.
    if (book.is_folder) fire('Zipping chapters — download will start shortly');
    deviceDownload(`/api/audiobooks/${book.id}/download`);
  };

  const play = async (book) => {
    const item = p.itemFromBook(book);
    const pos = await p.spoken.fetchPosition(item);
    if (pos && pos.position_sec > 5) setResumePrompt({ item, pos });
    else p.playSpoken(item, { resumeSec: 0, speed: (pos && pos.speed) || 1 });
  };

  return (
    <>
      <PageHeader title="Audiobooks" sub="local books with chapters + resume, played here or cast to a room"
                  actions={
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <ListeningAsSelector/>
                      <Button icon="refresh-cw" onClick={reindex} disabled={busy}>{busy ? 'indexing…' : 'reindex'}</Button>
                    </div>
                  }/>

      {(!books || books.length === 0) ? (
        <Empty glyph="headphones" title="no audiobooks yet"
               sub="drop .m4b files or per-chapter folders into your audiobooks dir, then reindex"
               action={<Button variant="primary" icon="refresh-cw" onClick={reindex}>reindex</Button>}/>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
          {books.map((b) => (
            <div key={b.id} style={{ background: 'var(--card)', border: '1px solid var(--border)',
                                     borderRadius: 'var(--r-md)', padding: 14, display: 'flex', gap: 12 }}>
              <div style={{ width: 56, height: 56, borderRadius: 'var(--r-sm)', flexShrink: 0,
                            border: '1px solid var(--border)',
                            background: b.artwork ? `center/cover url(${b.artwork})` : 'var(--sunken)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {!b.artwork && <Icon name="book-open" size={26}/>}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.title}</div>
                {b.author && <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{b.author}</div>}
                <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', marginTop: 2 }}>
                  {b.duration_sec ? fmtDur(b.duration_sec) : '—'}
                  {b.chapters && b.chapters.length ? ` · ${b.chapters.length} chapters` : ''}
                </div>
                <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
                  <Button variant="primary" icon="play" onClick={() => play(b)}>play</Button>
                  <Button icon="download" title={b.is_folder ? 'save all chapters to this device (zip)' : 'save to this device'}
                          onClick={() => save(b)}>save</Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {resumePrompt && (
        <AbResumePrompt prompt={resumePrompt} onClose={() => setResumePrompt(null)}
                        onResume={() => { p.playSpoken(resumePrompt.item, { resumeSec: resumePrompt.pos.position_sec, speed: resumePrompt.pos.speed }); setResumePrompt(null); }}
                        onRestart={() => { p.playSpoken(resumePrompt.item, { resumeSec: 0, speed: resumePrompt.pos.speed }); setResumePrompt(null); }}/>
      )}
      {toastNode}
    </>
  );
};

/* Self-contained resume prompt (doesn't depend on podcasts.jsx load order). */
const AbResumePrompt = ({ prompt, onClose, onResume, onRestart }) => (
  <>
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.35)', zIndex: 70 }}/>
    <div style={{ position: 'fixed', top: '35vh', left: '50%', transform: 'translateX(-50%)', width: 'min(380px, 92vw)',
                  zIndex: 71, background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                  boxShadow: 'var(--shadow-md)', padding: 20, textAlign: 'center' }}>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>{prompt.item.title}</div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 16 }}>
        You were at {fmtDur(prompt.pos.position_sec)}.
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
        <Button variant="primary" icon="play" onClick={onResume}>resume</Button>
        <Button icon="rotate-ccw" onClick={onRestart}>start over</Button>
      </div>
    </div>
  </>
);

window.AudiobooksPage = AudiobooksPage;
