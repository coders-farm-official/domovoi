/* Videos page — every video across the Files media libraries, played in a
 * full-screen overlay <video> with per-(device × person) resume positions.
 *
 * Data sources:
 *   * GET  /api/videos/list        — bounded walk of every library
 *   * GET  /api/videos/recent      — newest resume rows for this device
 *   * GET/POST/DELETE /api/videos/position — the resume store
 *   * /ws/state · video_positions.changed — refetch the recent strip
 *
 * Identity rides the same per-client id + "listening as" person the spoken
 * audio store uses (player.jsx SpokenAudio) so podcasts/audiobooks/videos
 * share one notion of "this device, this person".
 *
 * MKV note: streams are served with their native container MIME; Chromium
 * demuxes Matroska (h264/aac plays fine). A browser that can't fires the
 * <video> error event and the overlay falls back to save-to-device copy.
 */

const vidFmtBytes = (n) => {
  if (n == null) return '—';
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const vidStreamUrl = (v, download) =>
  `${API_BASE}/api/videos/stream?library_id=${encodeURIComponent(v.library_id)}`
  + `&path=${encodeURIComponent(v.rel)}` + (download ? '&download=1' : '');
const vidPosterUrl = (v) =>
  `${API_BASE}/api/videos/poster?library_id=${encodeURIComponent(v.library_id)}`
  + `&path=${encodeURIComponent(v.rel)}`;

const vidPositionBody = (v) => {
  const body = { library_id: v.library_id, path: v.rel, device_id: SpokenAudio.clientId() };
  const pid = SpokenAudio.getPerson();
  if (pid != null) body.person_id = pid;
  return body;
};

/* Poster tile: 16:9, poster image when the server extracted one (a 204 /
 * failed load falls back to the film glyph). Progress bar along the bottom
 * edge when `progress` (0..1) is passed (recent strip). */
const VideoPosterTile = ({ video, progress, onClick }) => {
  const [failed, setFailed] = React.useState(false);
  return (
    <div onClick={onClick}
         style={{ position: 'relative', aspectRatio: '16 / 9', cursor: 'pointer',
                  borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                  background: 'var(--sunken)', overflow: 'hidden',
                  display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      {!failed
        ? <img src={vidPosterUrl(video)} alt="" loading="lazy"
               onError={() => setFailed(true)}
               style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
                        objectFit: 'cover' }}/>
        : <Icon name="film" size={26}/>}
      <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                    justifyContent: 'center', opacity: 0, transition: 'opacity 120ms',
                    background: 'oklch(0 0 0 / 0.25)' }}
           onMouseEnter={(e) => { e.currentTarget.style.opacity = 1; }}
           onMouseLeave={(e) => { e.currentTarget.style.opacity = 0; }}>
        <div style={{ width: 40, height: 40, borderRadius: 9999, background: 'var(--brand)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      color: 'oklch(0.2 0.02 75)' }}>
          <Icon name="play" size={18}/>
        </div>
      </div>
      {progress != null && progress > 0 && (
        <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 3,
                      background: 'oklch(0 0 0 / 0.4)' }}>
          <div style={{ width: `${Math.min(100, progress * 100)}%`, height: '100%',
                        background: 'var(--brand)' }}/>
        </div>
      )}
    </div>
  );
};

/* Full-screen playback overlay. Auto-resumes at `resumeSec`, saves the
 * position every 5 s while playing plus on pause/close/end (end saves 0 so
 * a finished video restarts next time). Esc closes. */
const VideoOverlay = ({ video, resumeSec, onClose }) => {
  const p = usePlayback();
  const ref = React.useRef(null);
  const lastSave = React.useRef(0);
  const [errored, setErrored] = React.useState(false);

  const save = (positionSec) => {
    const el = ref.current;
    const body = {
      ...vidPositionBody(video),
      position_sec: Math.max(0, Math.round(positionSec)),
      title: video.title || video.name,
    };
    if (el && Number.isFinite(el.duration) && el.duration > 0)
      body.duration_sec = Math.round(el.duration);
    apiPost('/api/videos/position', body).catch(() => {});
  };

  React.useEffect(() => {
    // One video at a time in the house^W browser — pause the music player.
    try { if (p && p.status === 'playing') p.pause(); } catch {}
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const onTimeUpdate = () => {
    const el = ref.current;
    if (!el || el.paused) return;
    const now = Date.now();
    if (now - lastSave.current >= 5000) {
      lastSave.current = now;
      save(el.currentTime);
    }
  };

  const close = () => {
    const el = ref.current;
    if (el && !errored && el.currentTime > 0 && !el.ended) save(el.currentTime);
    onClose();
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 90, background: 'oklch(0.12 0.005 80)',
                  display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
                    color: 'oklch(0.95 0.005 80)' }}>
        <div style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 600,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {video.title || video.name}
          <span className="mono" style={{ fontSize: 11, marginLeft: 10, opacity: 0.55 }}>
            {video.library_label || video.library_id}
          </span>
        </div>
        <Button icon="rotate-ccw" title="start over"
                onClick={() => { const el = ref.current; if (el) { el.currentTime = 0; el.play().catch(() => {}); } }}>
          start over
        </Button>
        <Button icon="download" title="save to this device"
                onClick={() => deviceDownload(vidStreamUrl(video, true))}>save</Button>
        <IconButton name="x" title="close" onClick={close}/>
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center',
                    justifyContent: 'center', padding: '0 16px 16px' }}>
        {errored ? (
          <div style={{ textAlign: 'center', color: 'oklch(0.85 0.005 80)', maxWidth: 420 }}>
            <Icon name="film" size={34}/>
            <div style={{ fontSize: 14, fontWeight: 600, margin: '12px 0 6px' }}>
              this browser can't play this file
            </div>
            <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 16 }}>
              the container/codec isn't supported here — save it to this device
              and play it locally, or use the Android app
            </div>
            <Button variant="primary" icon="download"
                    onClick={() => deviceDownload(vidStreamUrl(video, true))}>save to device</Button>
          </div>
        ) : (
          <video ref={ref} src={vidStreamUrl(video)} poster={vidPosterUrl(video)}
                 controls autoPlay
                 onLoadedMetadata={() => {
                   const el = ref.current;
                   if (el && resumeSec > 5 && (!el.duration || resumeSec < el.duration * 0.95))
                     el.currentTime = resumeSec;
                 }}
                 onTimeUpdate={onTimeUpdate}
                 onPause={() => { const el = ref.current; if (el && !el.ended) save(el.currentTime); }}
                 onEnded={() => save(0)}
                 onError={() => setErrored(true)}
                 style={{ maxWidth: '100%', maxHeight: '100%', width: '100%',
                          borderRadius: 'var(--r-sm)', outline: 'none', background: 'black' }}/>
        )}
      </div>
    </div>
  );
};

const VideosPage = () => {
  const [filter, setFilter] = React.useState('');
  const debFilter = useDebouncedValue(filter, 200);
  const [playing, setPlaying] = React.useState(null); // { video, resumeSec }

  const { items: videos, loading } =
    useApiList('/api/videos/list', { pickItems: (x) => x.videos });

  const pid = SpokenAudio.getPerson();
  const recentPath = `/api/videos/recent?device_id=${encodeURIComponent(SpokenAudio.clientId())}`
    + (pid != null ? `&person_id=${pid}` : '');
  const { items: recent, refresh: refreshRecent } =
    useApiList(recentPath, { pickItems: (x) => x.recent, eventTypes: ['video_positions.changed'] });

  const play = async (video, resumeOverride) => {
    let resumeSec = resumeOverride;
    if (resumeSec == null) {
      try {
        const q = vidPositionBody(video);
        const pos = await apiGet(
          `/api/videos/position?library_id=${encodeURIComponent(q.library_id)}`
          + `&path=${encodeURIComponent(q.path)}&device_id=${encodeURIComponent(q.device_id)}`
          + (q.person_id != null ? `&person_id=${q.person_id}` : ''));
        resumeSec = (pos && pos.position_sec) || 0;
      } catch { resumeSec = 0; }
    }
    setPlaying({ video, resumeSec });
  };

  const removeRecent = async (r) => {
    try { await apiDelete('/api/videos/position', vidPositionBody(r)); } catch {}
    refreshRecent();
  };

  const q = debFilter.trim().toLowerCase();
  const filtered = q
    ? videos.filter((v) => v.name.toLowerCase().includes(q) || v.rel.toLowerCase().includes(q))
    : videos;

  // Group by library for section headers (registry order is stable).
  const byLibrary = [];
  const seen = {};
  filtered.forEach((v) => {
    let g = seen[v.library_id];
    if (!g) { g = { id: v.library_id, label: v.library_label, videos: [] }; seen[v.library_id] = g; byLibrary.push(g); }
    g.videos.push(v);
  });

  return (
    <>
      <PageHeader title="Videos" sub="videos across your files libraries · resume where you left off"
                  actions={
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input type="text" placeholder="filter…" value={filter}
                             onChange={(e) => setFilter(e.target.value)}
                             style={{ font: 'inherit', fontSize: 13, height: 30, padding: '0 10px',
                                      borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                                      background: 'var(--card)', color: 'var(--fg)', width: 180 }}/>
                    </div>
                  }/>

      {recent && recent.length > 0 && !q && (
        <div style={{ marginBottom: 24 }}>
          <div className="nav-section" style={{ padding: 0, marginBottom: 8 }}>recently played</div>
          <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 4 }}>
            {recent.map((r) => (
              <div key={`${r.library_id}:${r.rel}`} style={{ width: 220, flexShrink: 0 }}>
                <VideoPosterTile video={r}
                                 progress={r.duration_sec ? r.position_sec / r.duration_sec : null}
                                 onClick={() => play(r, r.position_sec)}/>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 500, overflow: 'hidden',
                                  textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.title || r.name}
                    </div>
                    <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>
                      {fmtDur(r.position_sec)}{r.duration_sec ? ` / ${fmtDur(r.duration_sec)}` : ''}
                      {' · '}{liveRelTime(r.updated_at)}
                    </div>
                  </div>
                  <IconButton name="x" title="remove from recently played"
                              onClick={() => removeRecent(r)}/>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {(!videos || videos.length === 0) && !loading ? (
        <Empty glyph="sleeping" title="no videos found"
               sub="drop video files (mp4 · webm · mkv · mov) into any files library"/>
      ) : (
        byLibrary.map((g) => (
          <div key={g.id} style={{ marginBottom: 24 }}>
            <div className="nav-section" style={{ padding: 0, marginBottom: 8 }}>
              {g.label} <span className="mono" style={{ fontSize: 10 }}>· {g.videos.length}</span>
            </div>
            <div style={{ display: 'grid', gap: 12,
                          gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
              {g.videos.map((v) => (
                <div key={`${v.library_id}:${v.rel}`}>
                  <VideoPosterTile video={v} onClick={() => play(v)}/>
                  <div style={{ fontSize: 12, fontWeight: 500, marginTop: 6, overflow: 'hidden',
                                textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={v.rel}>
                    {v.name}
                  </div>
                  <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>
                    {vidFmtBytes(v.size)} · {liveRelTime(new Date(v.mtime * 1000).toISOString())}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))
      )}

      {playing && (
        <VideoOverlay video={playing.video} resumeSec={playing.resumeSec}
                      onClose={() => { setPlaying(null); refreshRecent(); }}/>
      )}
    </>
  );
};

window.VideosPage = VideosPage;
