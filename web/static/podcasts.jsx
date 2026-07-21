/* Podcasts page — subscriptions, episodes, browser playback via the shared
 * mini-player (player.jsx). Extends the music player rather than forking it:
 * an episode becomes a generic queue item (kind='podcast') with chapters +
 * a per-(device × person) resume position.
 *
 * Data sources:
 *   * GET  /api/podcasts/subscriptions            — subscribed shows + counts
 *   * GET  /api/podcasts/subscriptions/{id}/episodes
 *   * POST /api/podcasts/subscriptions            — subscribe (feed URL / name)
 *   * DELETE /api/podcasts/subscriptions/{id}
 *   * GET  /api/podcasts/discover?q=              — iTunes discovery (network)
 *   * POST /api/podcasts/poll                     — manual feed poll now
 *   * /ws/state · podcasts.changed                — refetch subs/episodes
 *   * /ws/state · podcast_positions.changed       — refetch resume positions
 */

const PodcastsPage = () => {
  const p = usePlayback();
  const [fire, toastNode] = useToast();
  const { items: subs, refetch } = useApiList('/api/podcasts/subscriptions', {
    eventTypes: ['podcasts.changed'],
  });
  const [selected, setSelected] = React.useState(null);   // subscription row
  const [showAdd, setShowAdd] = React.useState(false);
  const [resumePrompt, setResumePrompt] = React.useState(null); // {item, pos}
  const [polling, setPolling] = React.useState(false);

  const poll = async () => {
    setPolling(true);
    try {
      const r = await apiPost('/api/podcasts/poll', {});
      fire(`Polled — ${r.downloaded || 0} downloaded, ${r.new || 0} new`);
      refetch();
    } catch { fire('Poll failed (offline?)'); }
    setPolling(false);
  };

  return (
    <>
      <PageHeader title="Podcasts" sub="subscriptions + episodes, played here or cast to a room"
                  actions={
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <ListeningAsSelector/>
                      <Button icon="refresh-cw" onClick={poll} disabled={polling}>{polling ? 'polling…' : 'poll now'}</Button>
                      <Button variant="primary" icon="plus" onClick={() => setShowAdd(true)}>subscribe</Button>
                    </div>
                  }/>

      {(!subs || subs.length === 0) ? (
        <Empty glyph="headphones" title="no subscriptions yet"
               sub="subscribe to a podcast by RSS URL or search by name"
               action={<Button variant="primary" icon="plus" onClick={() => setShowAdd(true)}>subscribe</Button>}/>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
          {subs.map((sub) => (
            <div key={sub.id} onClick={() => setSelected(sub)}
                 style={{ cursor: 'pointer', background: 'var(--card)', border: '1px solid var(--border)',
                          borderRadius: 'var(--r-md)', padding: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
              <PodArt url={sub.artwork} size={48}/>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {sub.title || sub.feed_url}
                </div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)' }}>
                  {sub.downloaded_count}/{sub.episode_count} downloaded
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {selected && (
        <EpisodeDrawer sub={selected} onClose={() => setSelected(null)}
                       onPlay={(ep) => playEpisode(p, selected, ep, setResumePrompt)}
                       onUnsub={async () => {
                         await apiDelete(`/api/podcasts/subscriptions/${selected.id}`);
                         setSelected(null); refetch();
                       }}/>
      )}
      {showAdd && <SubscribeModal onClose={() => setShowAdd(false)} onDone={() => { setShowAdd(false); refetch(); }}/>}
      {resumePrompt && (
        <ResumePrompt prompt={resumePrompt} onClose={() => setResumePrompt(null)}
                      onResume={() => { p.playSpoken(resumePrompt.item, { resumeSec: resumePrompt.pos.position_sec, speed: resumePrompt.pos.speed }); setResumePrompt(null); }}
                      onRestart={() => { p.playSpoken(resumePrompt.item, { resumeSec: 0, speed: resumePrompt.pos.speed }); setResumePrompt(null); }}/>
      )}
      {toastNode}
    </>
  );
};

async function playEpisode(p, sub, ep, setResumePrompt) {
  if (!ep.has_file) return;
  const item = p.itemFromEpisode(ep, sub.title, sub.artwork);
  const pos = await p.spoken.fetchPosition(item);
  if (pos && pos.position_sec > 5) setResumePrompt({ item, pos });
  else p.playSpoken(item, { resumeSec: 0, speed: (pos && pos.speed) || 1 });
}

const PodArt = ({ url, size = 48 }) => (
  url
    ? <img src={url} alt="" style={{ width: size, height: size, borderRadius: 'var(--r-sm)', objectFit: 'cover', border: '1px solid var(--border)', flexShrink: 0 }}/>
    : <div style={{ width: size, height: size, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                    background: 'var(--sunken)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <Icon name="podcast" size={size * 0.45}/>
      </div>
);

const EpisodeDrawer = ({ sub, onClose, onPlay, onUnsub }) => {
  const { items: episodes } = useApiList(`/api/podcasts/subscriptions/${sub.id}/episodes`, {
    eventTypes: ['podcasts.changed', 'podcast_positions.changed'],
  });
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.35)', zIndex: 50 }}/>
      <div style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: 'min(520px, 92vw)', zIndex: 51,
                    background: 'var(--bg)', borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 16, borderBottom: '1px solid var(--border)', display: 'flex', gap: 12, alignItems: 'center' }}>
          <PodArt url={sub.artwork} size={56}/>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{sub.title || sub.feed_url}</div>
            {sub.author && <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{sub.author}</div>}
          </div>
          <IconButton name="trash-2" onClick={onUnsub} title="unsubscribe"/>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {(episodes || []).map((ep) => (
            <div key={ep.id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '10px 16px',
                                      borderBottom: '1px solid var(--border-soft)' }}>
              <button className="btn btn-primary btn-icon" disabled={!ep.has_file}
                      onClick={() => onPlay(ep)}
                      style={{ width: 32, height: 32, borderRadius: '50%', opacity: ep.has_file ? 1 : 0.4 }}>
                <Icon name="play" size={14}/>
              </button>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ep.title}</div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)' }}>
                  {ep.duration_sec ? fmtDur(ep.duration_sec) : '—'}
                  {ep.chapters && ep.chapters.length ? ` · ${ep.chapters.length} chapters` : ''}
                  {!ep.has_file ? ` · ${ep.download_status}` : ''}
                </div>
              </div>
              {ep.has_file && (
                <IconButton name="download" title="save to this device"
                            onClick={() => deviceDownload(`/api/podcasts/episodes/${ep.id}/audio?download=1`)}/>
              )}
            </div>
          ))}
          {(episodes || []).length === 0 && <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-muted)', fontSize: 12 }}>no episodes yet — try "poll now"</div>}
        </div>
      </div>
    </>
  );
};

const SubscribeModal = ({ onClose, onDone }) => {
  const [tab, setTab] = React.useState('search');
  const [q, setQ] = React.useState('');
  const [results, setResults] = React.useState([]);
  const [feedUrl, setFeedUrl] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [fire, toastNode] = useToast();

  const search = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try { setResults(await apiGet(`/api/podcasts/discover?q=${encodeURIComponent(q.trim())}`)); }
    catch { fire('Discovery needs internet'); }
    setBusy(false);
  };
  const subscribe = async (body) => {
    setBusy(true);
    try { await apiPost('/api/podcasts/subscriptions', body); fire('Subscribed'); onDone(); }
    catch (e) { fire('Subscribe failed'); }
    setBusy(false);
  };

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.35)', zIndex: 60 }}/>
      <div style={{ position: 'fixed', top: '10vh', left: '50%', transform: 'translateX(-50%)', width: 'min(560px, 92vw)',
                    zIndex: 61, background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                    boxShadow: 'var(--shadow-md)', padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>Subscribe to a podcast</div>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
          <button onClick={() => setTab('search')} style={_pill(tab === 'search')}>search</button>
          <button onClick={() => setTab('url')} style={_pill(tab === 'url')}>by RSS URL</button>
        </div>
        {tab === 'search' ? (
          <>
            <div style={{ display: 'flex', gap: 6 }}>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="show name…" autoFocus
                     onKeyDown={(e) => e.key === 'Enter' && search()} style={_inp}/>
              <Button variant="primary" icon="search" onClick={search} disabled={busy}>search</Button>
            </div>
            <div style={{ marginTop: 12, maxHeight: '40vh', overflowY: 'auto' }}>
              {results.map((r, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 4px', borderBottom: '1px solid var(--border-soft)' }}>
                  <PodArt url={r.artwork} size={40}/>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.title}</div>
                    <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{r.author}</div>
                  </div>
                  <Button icon="plus" onClick={() => subscribe({ feed_url: r.feed_url })} disabled={busy}>add</Button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div style={{ display: 'flex', gap: 6 }}>
            <input value={feedUrl} onChange={(e) => setFeedUrl(e.target.value)} placeholder="https://…/feed.xml" autoFocus style={_inp}/>
            <Button variant="primary" icon="plus" onClick={() => subscribe({ feed_url: feedUrl.trim() })} disabled={busy || !feedUrl.trim()}>subscribe</Button>
          </div>
        )}
        {toastNode}
      </div>
    </>
  );
};

const ResumePrompt = ({ prompt, onClose, onResume, onRestart }) => (
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

const _pill = (active) => ({
  font: 'inherit', fontSize: 12, padding: '4px 12px', borderRadius: 'var(--r-full)',
  border: '1px solid var(--border)', cursor: 'pointer',
  background: active ? 'var(--brand-soft)' : 'var(--card)', color: 'var(--fg)',
});
const _inp = {
  flex: 1, font: 'inherit', fontSize: 13, height: 34, padding: '0 12px',
  borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)',
};

window.PodcastsPage = PodcastsPage;
