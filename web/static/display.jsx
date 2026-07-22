/* Fullscreen now-playing display for VIDEO satellites (the kiosk browser
 * on a Radxa Zero 3W or similar renders this page — see
 * satellite/VIDEO_SATELLITE.md). Standalone on purpose (display.html):
 * no app shell, no service worker, no icon library — minimal RAM.
 *
 * Data path mirrors the satellites-page cards exactly:
 *   * initial GET /api/music/now-playing (retried every 5 s until it
 *     lands, so a server reboot self-heals);
 *   * refetch on `music.now_playing.changed` over /ws/state (the event
 *     deliberately carries no elapsed_sec — the refetch reads it fresh
 *     from MPD via the web backend);
 *   * a 1 s local tick extrapolates elapsed between refetches;
 *   * `_status` events from the shared stateBus drive a reconnect
 *     overlay (the bus already reconnects with backoff).
 *
 * Everything rides the daily-tier LAN surface — no auth, same posture as
 * every dashboard read (docs/SECURITY_PRIVACY.md).
 *
 * URL: /display.html?room=<room_id>[&theme=light]
 */

const dpParams = new URLSearchParams(window.location.search);
const DP_ROOM = (dpParams.get('room') || '').trim();
if (dpParams.get('theme') === 'light') {
  document.documentElement.dataset.theme = 'light';
}

const dpFmtDur = (s) => {
  if (s == null || !isFinite(s)) return '0:00';
  const t = Math.max(0, Math.round(s));
  const m = Math.floor(t / 60);
  const sec = String(t % 60).padStart(2, '0');
  return `${m}:${sec}`;
};

/* The same gradient the dashboard's cards fall back to when a track has
 * no embedded art (or the source is a stream). */
const DP_GRADIENT = 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))';

const DpCover = ({ trackId, size }) => {
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [trackId]);
  const style = {
    width: size, height: size, borderRadius: 'var(--r-md)',
    border: '1px solid var(--border)', flexShrink: 0,
    background: DP_GRADIENT, objectFit: 'cover',
  };
  if (trackId == null || failed) return <div style={style}/>;
  return (
    <img
      src={`${API_BASE}/api/music/library/${trackId}/cover`}
      alt=""
      style={style}
      onError={() => setFailed(true)}
    />
  );
};

/* Inline stroke icons (no icon library on this page). */
const DpIcon = ({ d, size = 26, filled = false }) => (
  <svg width={size} height={size} viewBox="0 0 24 24"
       fill={filled ? 'currentColor' : 'none'} stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    {d}
  </svg>
);
const dpIcons = {
  play:  <polygon points="6 3 20 12 6 21 6 3"/>,
  pause: <>{[7, 15].map(x => <rect key={x} x={x} y="4" width="3.5" height="16" rx="1"/>)}</>,
  skip:  <><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></>,
  stop:  <rect x="5" y="5" width="14" height="14" rx="2"/>,
};

const DpButton = ({ icon, onClick, disabled, primary }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    style={{
      width: 64, height: 64, borderRadius: 'var(--r-full)',
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      font: 'inherit', cursor: 'pointer',
      background: primary ? 'var(--brand)' : 'var(--card)',
      color: primary ? 'oklch(0.2 0.02 75)' : 'var(--fg)',
      border: primary ? '1px solid transparent' : '1px solid var(--border)',
      opacity: disabled ? 0.45 : 1,
      transition: 'transform .12s ease, opacity .12s ease',
    }}
  >
    <DpIcon d={icon} filled={primary}/>
  </button>
);

const DpTransport = ({ np, busy, act }) => {
  const playing = np.state === 'play';
  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
      <DpButton
        primary
        icon={playing ? dpIcons.pause : dpIcons.play}
        disabled={busy}
        onClick={() => act(playing ? 'pause' : 'resume')}
      />
      <DpButton icon={dpIcons.skip} disabled={busy} onClick={() => act('skip')}/>
      <DpButton icon={dpIcons.stop} disabled={busy} onClick={() => act('stop')}/>
    </div>
  );
};

const DpProgress = ({ elapsed, duration }) => {
  const pct = duration ? Math.min(100, (elapsed / duration) * 100) : 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, width: '100%' }}>
      <span className="mono" style={{ fontSize: 15, color: 'var(--fg-muted)', minWidth: 52, textAlign: 'right' }}>
        {dpFmtDur(elapsed)}
      </span>
      <div style={{ flex: 1, height: 5, borderRadius: 3, background: 'var(--sunken)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: 'var(--brand)',
                      transition: 'width .9s linear' }}/>
      </div>
      <span className="mono" style={{ fontSize: 15, color: 'var(--fg-muted)', minWidth: 52 }}>
        {duration ? dpFmtDur(duration) : '–:––'}
      </span>
    </div>
  );
};

const DpClock = ({ dim }) => {
  const [now, setNow] = React.useState(() => new Date());
  React.useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const hh = now.getHours();
  const mm = String(now.getMinutes()).padStart(2, '0');
  const date = now.toLocaleDateString(undefined, {
    weekday: 'long', month: 'long', day: 'numeric',
  });
  return (
    <div style={{ textAlign: 'center', opacity: dim ? 0.55 : 1 }}>
      <div className="mono" style={{ fontSize: 128, fontWeight: 300, letterSpacing: '-0.02em',
                                     color: 'var(--fg)', lineHeight: 1 }}>
        {hh}:{mm}
      </div>
      <div style={{ fontSize: 22, color: 'var(--fg-muted)', marginTop: 16 }}>{date}</div>
    </div>
  );
};

const DpIdle = ({ mode, lastTrackId, roomName }) => {
  if (mode === 'blank') return <div style={{ width: '100%', height: '100%', background: 'oklch(0.08 0 0)' }}/>;
  if (mode === 'art' && lastTrackId != null) {
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'center',
                    justifyContent: 'center', opacity: 0.5 }}>
        <DpCover trackId={lastTrackId} size="min(62vh, 62vw)"/>
      </div>
    );
  }
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 40,
                  alignItems: 'center', justifyContent: 'center' }}>
      <DpClock dim/>
      <div style={{ fontSize: 16, color: 'var(--fg-faint)' }}>{roomName}</div>
    </div>
  );
};

/* Slow ±8 px drift so static chrome never burns into the panel. */
const DpBurnInShifter = ({ children }) => {
  const [offset, setOffset] = React.useState([0, 0]);
  React.useEffect(() => {
    let i = 0;
    const spots = [[0, 0], [8, 4], [0, 8], [-8, 4], [-4, -6], [6, -8]];
    const t = setInterval(() => {
      i = (i + 1) % spots.length;
      setOffset(spots[i]);
    }, 60000);
    return () => clearInterval(t);
  }, []);
  return (
    <div style={{ height: '100%',
                  transform: `translate(${offset[0]}px, ${offset[1]}px)`,
                  transition: 'transform 2s ease' }}>
      {children}
    </div>
  );
};

const DpReconnect = ({ show }) => show ? (
  <div style={{ position: 'fixed', top: 20, left: '50%', transform: 'translateX(-50%)',
                background: 'var(--overlay, var(--card))', border: '1px solid var(--border)',
                borderRadius: 'var(--r-full)', padding: '8px 18px',
                display: 'flex', alignItems: 'center', gap: 10, zIndex: 10 }}>
    <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--warn)' }}/>
    <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>reconnecting</span>
  </div>
) : null;

const DisplayApp = () => {
  if (!DP_ROOM) {
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'center',
                    justifyContent: 'center', padding: 40 }}>
        <div style={{ maxWidth: 520, background: 'var(--card)', border: '1px solid var(--border)',
                      borderRadius: 'var(--r-md)', padding: 28 }}>
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 10 }}>no room set</div>
          <div style={{ fontSize: 14, color: 'var(--fg-muted)', lineHeight: 1.6 }}>
            open this page as <span className="mono">/display.html?room=&lt;room_id&gt;</span> —
            the video satellite's kiosk launcher builds this URL from its config
            (see <span className="mono">satellite/VIDEO_SATELLITE.md</span>).
          </div>
        </div>
      </div>
    );
  }

  const [np, setNp] = React.useState(null);          // this room's NowPlaying
  const [fetchedAt, setFetchedAt] = React.useState(0);
  const [haveData, setHaveData] = React.useState(false);
  const [wsUp, setWsUp] = React.useState(true);
  const [tickNow, setTickNow] = React.useState(() => Date.now());
  const [busy, setBusy] = React.useState(false);
  const lastTrackIdRef = React.useRef(null);

  // Room metadata (label + configured idle mode) — refreshed on display
  // config changes; tolerant of the room being offline (defaults apply).
  const { data: sat } = useApiObject(`/api/satellites/${DP_ROOM}`, {
    eventTypes: ['satellites.display.changed', 'satellites.presence.changed'],
  });
  const idleMode = sat?.display?.idle_mode || 'clock';
  const roomName = sat?.room_label || DP_ROOM;

  const refresh = React.useCallback(async () => {
    try {
      const rooms = await apiGet('/api/music/now-playing');
      const mine = (rooms || []).find((r) => r.room_id === DP_ROOM) || null;
      setNp(mine);
      setFetchedAt(Date.now());
      setHaveData(true);
      if (mine?.track_id != null) lastTrackIdRef.current = mine.track_id;
    } catch (e) {
      console.warn('now-playing fetch failed:', e);
      setHaveData(false);   // re-arms the 5 s retry loop below
    }
  }, []);

  // Initial fetch + 5 s retry until the backend answers (server reboot
  // self-heal); afterwards events drive the refetches.
  React.useEffect(() => { refresh(); }, [refresh]);
  React.useEffect(() => {
    if (haveData) return;
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [haveData, refresh]);

  useStateEvents(['music.now_playing.changed'], () => refresh());
  useStateEvents(['_status'], (ev) => {
    setWsUp(!!ev.connected);
    if (ev.connected) refresh();
  });

  // 1 s tick — extrapolates elapsed between refetches while playing.
  React.useEffect(() => {
    const t = setInterval(() => setTickNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const act = async (verb) => {
    setBusy(true);
    try { await apiPost(`/api/music/${verb}/${DP_ROOM}`); }
    catch (e) { console.warn(`${verb} failed:`, e); }
    finally { setBusy(false); }
  };

  const active = np && np.state !== 'stop' && np.song;
  const elapsed = active
    ? (np.elapsed_sec ?? 0) + (np.state === 'play' ? (tickNow - fetchedAt) / 1000 : 0)
    : 0;
  const duration = active ? (np.song.duration_sec ?? 0) : 0;

  return (
    <DpBurnInShifter>
      <DpReconnect show={!wsUp || !haveData}/>
      {!active ? (
        <DpIdle mode={idleMode} lastTrackId={lastTrackIdRef.current} roomName={roomName}/>
      ) : (
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column',
                      padding: 'min(6vh, 48px) min(7vw, 64px)', boxSizing: 'border-box' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%',
                           background: np.state === 'play' ? 'var(--ok)' : 'var(--idle)' }}/>
            <span style={{ fontSize: 16, color: 'var(--fg-muted)' }}>{roomName}</span>
            {np.source && (
              <span style={{ fontSize: 12, color: 'var(--fg-faint)',
                             border: '1px solid var(--border)', borderRadius: 'var(--r-full)',
                             padding: '2px 10px' }}>{np.source}</span>
            )}
            <span style={{ flex: 1 }}/>
            <span style={{ fontSize: 14, color: 'var(--fg-faint)' }}>
              {np.state === 'play' ? 'now playing' : 'paused'}
            </span>
          </div>

          <div style={{ flex: 1, display: 'flex', alignItems: 'center',
                        gap: 'min(6vw, 56px)', minHeight: 0, paddingTop: 24 }}>
            <DpCover trackId={np.track_id} size="min(52vh, 38vw)"/>
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 18 }}>
              <div style={{ fontSize: 'min(6vh, 44px)', fontWeight: 650, letterSpacing: '-0.015em',
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {np.song.title || np.song.file?.split('/').pop() || 'unknown'}
              </div>
              {np.song.artist && (
                <div style={{ fontSize: 'min(3.4vh, 26px)', color: 'var(--fg-muted)',
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {np.song.artist}
                </div>
              )}
              {np.song.album && (
                <div style={{ fontSize: 'min(2.6vh, 19px)', color: 'var(--fg-faint)',
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {np.song.album}
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 26, paddingTop: 18 }}>
            <DpProgress elapsed={elapsed} duration={duration}/>
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <DpTransport np={np} busy={busy} act={act}/>
            </div>
          </div>
        </div>
      )}
    </DpBurnInShifter>
  );
};

ReactDOM.createRoot(document.getElementById('root')).render(<DisplayApp/>);
