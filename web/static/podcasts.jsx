/* Podcasts page — subscriptions, episodes, browser playback via the shared
 * mini-player (player.jsx). Extends the music player rather than forking it:
 * an episode becomes a generic queue item (kind='podcast') with chapters +
 * a per-(device × person) resume position.
 *
 * Data sources:
 *   * GET  /api/podcasts/subscriptions            — subscribed shows + counts
 *   * GET  /api/podcasts/subscriptions/{id}/episodes
 *     Both load one page at a time as they scroll (data.js usePagedList);
 *     the searches are the server's, over every show / episode.
 *   * POST /api/podcasts/subscriptions            — subscribe (feed URL / name)
 *   * DELETE /api/podcasts/subscriptions/{id}
 *   * GET  /api/podcasts/discover?q=              — iTunes discovery (network)
 *   * POST /api/podcasts/poll                     — manual feed poll now
 *   * /ws/state · podcasts.changed                — refresh subs/episodes
 *   * /ws/state · podcast_positions.changed       — refresh resume positions
 *
 * Artwork (fix B11): every `artwork` the API returns is a SERVER path
 * (/api/podcasts/subscriptions/{id}/artwork, /api/podcasts/discover/artwork/
 * {key}) or null — the server fetched the image. PodArt draws only such a
 * path; anything else gets the placeholder, so this page never makes the
 * browser contact a publisher.
 *
 * Internet answer "never" (components.jsx useInternetPolicy): search, poll
 * now and the directory are greyed with the "needs internet" note;
 * subscribing by RSS URL stays.
 */

// The internet answer and its refusal text (components.jsx, internet-build
// 5.10). Guarded so the page still renders on a dashboard without them.
const usePodcastsInternet = () => (
  typeof useInternetPolicy === 'function' ? useInternetPolicy() : { off: false }
);
const podcastsFailText = (e, fallback) => (
  typeof isInternetOffError === 'function' && isInternetOffError(e) ? INTERNET_OFF_MESSAGE : fallback
);
/* The "poll now" toast: the counts, then any show whose feed couldn't be
   read, by name, with the server's plain-words reason (POST
   /api/podcasts/poll `failures`; an older server sends none). */
const podcastsPollText = (r) => {
  const head = `Polled — ${(r && r.downloaded) || 0} downloaded, ${(r && r.new) || 0} new`;
  const failures = (r && Array.isArray(r.failures)) ? r.failures : [];
  if (!failures.length) return head;
  const n = failures.length;
  const named = failures.slice(0, 3)
    .map((f) => `${(f && f.title) || 'a show'} — ${(f && f.message) || "couldn't read the feed"}`)
    .join('; ');
  const more = n > 3 ? `; and ${n - 3} more` : '';
  return `${head}. ${n} ${n === 1 ? 'show' : 'shows'} couldn't be read: ${named}${more}`;
};
const podcastsNeedsInternetTitle = (off) => (
  off ? (typeof NEEDS_INTERNET_TEXT === 'string' ? NEEDS_INTERNET_TEXT : 'needs internet') : undefined
);
const PodcastsNeedsInternet = () => (
  typeof NeedsInternetNote === 'function'
    ? <NeedsInternetNote compact/>
    : <span style={{ fontSize: 11, color: 'var(--fg-muted)' }}>needs internet</span>
);

/* A podcast artwork value the page may load: only a server path (/api/…),
 * resolved against the selected server (API_BASE). A remote URL — an old
 * server's answer — or a protocol-relative //host/… is never handed to the
 * browser. */
const podcastArtSrc = (url) => (
  typeof url === 'string' && url.startsWith('/api/')
    ? `${typeof API_BASE === 'string' ? API_BASE : ''}${url}`
    : null
);

const PodcastsPage = () => {
  const p = usePlayback();
  const net = usePodcastsInternet();
  const [fire, toastNode] = useToast();
  const [search, setSearch] = React.useState('');
  const debSearch = useDebouncedValue(search, 250);
  const needle = debSearch.trim();
  const list = usePagedList(
    '/api/podcasts/subscriptions' + (needle ? `?q=${encodeURIComponent(needle)}` : ''),
    { eventTypes: ['podcasts.changed'] });
  const subs = list.items;
  const refresh = list.reload;
  const [selected, setSelected] = React.useState(null);   // subscription row
  const [showAdd, setShowAdd] = React.useState(false);
  const [resumePrompt, setResumePrompt] = React.useState(null); // {item, pos}
  const [polling, setPolling] = React.useState(false);

  const poll = async () => {
    setPolling(true);
    try {
      const r = await apiPost('/api/podcasts/poll', {});
      fire(podcastsPollText(r));
      refresh();
    } catch (e) { fire(podcastsFailText(e, 'Poll failed (offline?)')); }
    setPolling(false);
  };

  return (
    <>
      <PageHeader title="Podcasts" sub="subscriptions + episodes, played here or cast to a room"
                  actions={
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <input type="text" placeholder="search shows…" value={search}
                             onChange={(e) => setSearch(e.target.value)}
                             style={{ font: 'inherit', fontSize: 13, height: 30, padding: '0 10px',
                                      borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                                      background: 'var(--card)', color: 'var(--fg)', width: 160 }}/>
                      <ListeningAsSelector/>
                      <Button icon="refresh-cw" onClick={poll} disabled={polling || net.off}
                              title={podcastsNeedsInternetTitle(net.off)}>{polling ? 'polling…' : 'poll now'}</Button>
                      {net.off && <PodcastsNeedsInternet/>}
                      <Button variant="primary" icon="plus" onClick={() => setShowAdd(true)}>subscribe</Button>
                    </div>
                  }/>

      {(!subs || subs.length === 0) && !list.loading && !list.error ? (
        needle
          ? <Empty glyph="headphones" title="no shows match" sub={`search = “${needle}”`}
                   action={<Button icon="x" onClick={() => setSearch('')}>clear search</Button>}/>
          : <Empty glyph="headphones" title="no subscriptions yet"
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
      <PagedListFooter list={list} noun="shows"/>

      {selected && (
        <EpisodeDrawer sub={selected} onClose={() => setSelected(null)}
                       onPlay={(ep) => playEpisode(p, selected, ep, setResumePrompt)}
                       onUnsub={async () => {
                         await apiDelete(`/api/podcasts/subscriptions/${selected.id}`);
                         setSelected(null); refresh();
                       }}/>
      )}
      {showAdd && <SubscribeModal off={net.off} onClose={() => setShowAdd(false)} onDone={() => { setShowAdd(false); refresh(); }}/>}
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

const PodArt = ({ url, size = 48 }) => {
  const src = podcastArtSrc(url);
  return src
    ? <img src={src} alt="" style={{ width: size, height: size, borderRadius: 'var(--r-sm)', objectFit: 'cover', border: '1px solid var(--border)', flexShrink: 0 }}/>
    : <div style={{ width: size, height: size, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                    background: 'var(--sunken)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <Icon name="podcast" size={size * 0.45}/>
      </div>;
};

/* ---- Episode details (formats are pinned in test_web_podcasts_page.py) ----
 * Every helper takes `now` so a test can fix the clock. Text only: a feed's
 * description is HTML, and it is reduced to plain text here and drawn as a
 * React text node, never injected. Local time, like the rest of the page. */
const POD_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const POD_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const podDayNumber = (d) => Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000);
/* "today" · "yesterday" · "3 days ago" (to 6) · "12 Oct" · "12 Oct 2025"
   (the year only when it is not this year). '' for no / unreadable date. */
const podPublished = (iso, now = new Date()) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const ago = podDayNumber(now) - podDayNumber(d);
  if (ago === 0) return 'today';
  if (ago === 1) return 'yesterday';
  if (ago >= 2 && ago <= 6) return `${ago} days ago`;
  const plain = `${d.getDate()} ${POD_MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? plain : `${plain} ${d.getFullYear()}`;
};
/* The tooltip: "Tue 12 Oct 2026, 14:05". */
const podPublishedFull = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${POD_DAYS[d.getDay()]} ${d.getDate()} ${POD_MONTHS[d.getMonth()]} ${d.getFullYear()}, ${hh}:${mm}`;
};
/* "1 h 12 min" · "1 h" · "38 min" · "<1 min" · '' when unknown. */
const podLength = (sec) => {
  if (!(sec > 0)) return '';
  const total = Math.round(sec / 60);
  if (total < 1) return '<1 min';
  const h = Math.floor(total / 60); const m = total % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
};
/* The listener's place in an episode. `kind`: 'unknown' (an older server, or
   no listener sent: nothing shown), 'new' (never started), 'played',
   'partial' (with `label` "24 min left" and `pct`), 'resumed' (a place but no
   length: "at 24 min"). Within 30 s of the end counts as played. */
const podProgress = (ep) => {
  if (!ep || ep.position_sec === undefined) return { kind: 'unknown' };
  const pos = Number(ep.position_sec);
  if (ep.position_sec === null || !(pos > 0)) return { kind: 'new' };
  const dur = Number(ep.duration_sec);
  if (!(dur > 0)) return { kind: 'resumed', label: `at ${podLength(pos) || '<1 min'}` };
  if (dur - pos <= 30) return { kind: 'played', label: 'played' };
  return { kind: 'partial', label: `${podLength(dur - pos)} left`,
           pct: Math.max(2, Math.min(98, Math.round((pos / dur) * 100))) };
};
const podDownload = (ep) => {
  if (ep.has_file) return 'downloaded';
  if (ep.download_status === 'downloading') return 'downloading';
  if (ep.download_status === 'failed') return 'download failed';
  return 'not downloaded';
};
const POD_ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', hellip: '…', ndash: '–', mdash: '—', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“' };
/* HTML → plain text: block ends become line breaks, tags are dropped, the
   common entities decoded. Nothing here ever reaches the DOM as markup. */
const podPlainText = (html) => {
  if (!html || typeof html !== 'string') return '';
  return html
    .replace(/<\s*(script|style)[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi, ' ')
    .replace(/<\s*br\s*\/?\s*>|<\s*\/\s*(p|div|li|h[1-6]|ul|ol|blockquote)\s*>/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
      if (e[0] === '#') {
        const n = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        return n > 0 && n < 0x110000 ? String.fromCodePoint(n) : m;
      }
      return Object.prototype.hasOwnProperty.call(POD_ENTITIES, e.toLowerCase()) ? POD_ENTITIES[e.toLowerCase()] : m;
    })
    .replace(/[ \t ]+/g, ' ')
    .replace(/ ?\n ?/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
};
const podSubInfo = (sub, total) => {
  const n = sub.episode_count != null ? sub.episode_count : total;
  const bits = [];
  if (n != null) bits.push(`${n} ${n === 1 ? 'episode' : 'episodes'}`);
  bits.push(sub.last_polled_at ? `updated ${relTime(sub.last_polled_at)}` : 'not checked yet');
  return bits.join(' · ');
};

/* Click (or Enter / Space) toggles two lines against the whole text. */
const PodText = ({ text, style }) => {
  const [open, setOpen] = React.useState(false);
  return (
    <div role="button" tabIndex={0} aria-expanded={open} title={open ? 'show less' : 'show more'}
         onClick={() => setOpen(!open)}
         onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(!open); } }}
         style={{ fontSize: 12, color: 'var(--fg-muted)', cursor: 'pointer', overflowWrap: 'anywhere',
                  whiteSpace: open ? 'pre-wrap' : 'normal', ...style,
                  ...(open ? { maxHeight: '40vh', overflowY: 'auto' }
                           : { overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }) }}>
      {text}
    </div>
  );
};

const EpisodeRow = ({ ep, onPlay, now }) => {
  const when = podPublished(ep.published_at, now);
  const len = podLength(ep.duration_sec);
  const prog = podProgress(ep);
  const desc = podPlainText(ep.description);
  const chapters = ep.chapters && ep.chapters.length ? ep.chapters.length : 0;
  const meta = [
    when && <span key="d" title={podPublishedFull(ep.published_at)}>{when}</span>,
    len && <span key="l">{len}</span>,
    (prog.kind === 'played' || prog.kind === 'partial' || prog.kind === 'resumed')
      && <span key="p" style={prog.kind !== 'played' ? { color: 'var(--brand)' } : undefined}>{prog.label}</span>,
    chapters > 0 && <span key="c">{`${chapters} ${chapters === 1 ? 'chapter' : 'chapters'}`}</span>,
    <span key="s">{podDownload(ep)}</span>,
  ].filter(Boolean);
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 16px',
                  borderBottom: '1px solid var(--border-soft)' }}>
      <button className="btn btn-primary btn-icon" disabled={!ep.has_file}
              onClick={() => onPlay(ep)}
              style={{ width: 32, height: 32, borderRadius: '50%', opacity: ep.has_file ? 1 : 0.4, flexShrink: 0 }}>
        <Icon name="play" size={14}/>
      </button>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {prog.kind === 'new' && (
            <span title="not played yet" aria-label="not played yet"
                  style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--brand)', flexShrink: 0 }}/>
          )}
          <div style={{ fontSize: 13, fontWeight: prog.kind === 'new' ? 600 : 500, minWidth: 0, overflow: 'hidden',
                        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflowWrap: 'anywhere' }}>{ep.title}</div>
        </div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)', display: 'flex', flexWrap: 'wrap', gap: '0 6px', marginTop: 2 }}>
          {meta.map((m, i) => <React.Fragment key={i}>{i > 0 && <span aria-hidden="true">·</span>}{m}</React.Fragment>)}
        </div>
        {prog.kind === 'partial' && (
          <div style={{ height: 3, borderRadius: 2, background: 'var(--sunken)', marginTop: 4, overflow: 'hidden' }}>
            <div style={{ width: `${prog.pct}%`, height: '100%', background: 'var(--brand)' }}/>
          </div>
        )}
        {desc && <PodText text={desc} style={{ marginTop: 4 }}/>}
      </div>
      {ep.has_file && (
        <IconButton name="download" title="save to this device"
                    onClick={() => deviceDownload(`/api/podcasts/episodes/${ep.id}/audio?download=1`)}/>
      )}
    </div>
  );
};

const EpisodeDrawer = ({ sub, onClose, onPlay, onUnsub }) => {
  const [search, setSearch] = React.useState('');
  const debSearch = useDebouncedValue(search, 250);
  const needle = debSearch.trim();
  // The listening-as person's place in each episode comes with the rows
  // (device_id + person_id); changing the person re-reads them.
  const pb = typeof usePlayback === 'function' ? usePlayback() : null;
  const deviceId = pb && pb.spoken && pb.spoken.clientId ? pb.spoken.clientId() : null;
  const personId = pb && pb.listenerPersonId != null ? pb.listenerPersonId : null;
  const qs = [];
  if (needle) qs.push(`q=${encodeURIComponent(needle)}`);
  if (deviceId) {
    qs.push(`device_id=${encodeURIComponent(deviceId)}`);
    if (personId != null) qs.push(`person_id=${personId}`);
  }
  // A long back catalogue arrives a page at a time as the drawer scrolls;
  // the search runs on the server over every episode of the show.
  const list = usePagedList(
    `/api/podcasts/subscriptions/${sub.id}/episodes` + (qs.length ? `?${qs.join('&')}` : ''),
    { eventTypes: ['podcasts.changed', 'podcast_positions.changed'] });
  const episodes = list.items;
  const now = new Date();
  const showText = podPlainText(sub.description);
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
        <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--border-soft)' }}>
          <div className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)', marginBottom: 4 }}>
            {podSubInfo(sub, list.total)}
          </div>
          {showText && <PodText text={showText} style={{ marginBottom: 8 }}/>}
          <input type="text" placeholder="search episodes…" value={search}
                 onChange={(e) => setSearch(e.target.value)} style={{ ..._inp, width: '100%', height: 30 }}/>
        </div>
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {(episodes || []).map((ep) => <EpisodeRow key={ep.id} ep={ep} onPlay={onPlay} now={now}/>)}
          {(episodes || []).length === 0 && !list.loading && !list.error && (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-muted)', fontSize: 12 }}>
              {needle ? `no episodes match “${needle}”` : 'no episodes yet — try "poll now"'}
            </div>
          )}
          <PagedListFooter list={list} noun="episodes" compact/>
        </div>
      </div>
    </>
  );
};

const SubscribeModal = ({ off = false, onClose, onDone }) => {
  // Searching the directory needs the internet; an RSS URL is only stored.
  const [tab, setTab] = React.useState(off ? 'url' : 'search');
  const [q, setQ] = React.useState('');
  const [results, setResults] = React.useState([]);
  const [feedUrl, setFeedUrl] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [fire, toastNode] = useToast();

  const search = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try { setResults(await apiGet(`/api/podcasts/discover?q=${encodeURIComponent(q.trim())}`)); }
    catch (e) { fire(podcastsFailText(e, 'Discovery needs internet')); }
    setBusy(false);
  };
  const subscribe = async (body) => {
    setBusy(true);
    try { await apiPost('/api/podcasts/subscriptions', body); fire('Subscribed'); onDone(); }
    catch (e) { fire(podcastsFailText(e, 'Subscribe failed')); }
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
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="show name…" autoFocus
                     disabled={off} title={podcastsNeedsInternetTitle(off)}
                     onKeyDown={(e) => e.key === 'Enter' && !off && search()} style={_inp}/>
              <Button variant="primary" icon="search" onClick={search} disabled={busy || off}
                      title={podcastsNeedsInternetTitle(off)}>search</Button>
            </div>
            {off && <div style={{ marginTop: 8 }}><PodcastsNeedsInternet/></div>}
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
