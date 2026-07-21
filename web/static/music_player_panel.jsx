/* Expanded "now playing" view — the Music page's big-player surface, bound
 * to the same app-level PlaybackContext the docked mini-player uses. Adds
 * the visualizer, the 10-band EQ, the sleep timer, the cast target, and the
 * offline-pin control. Rendered by music.jsx as its "Player" tab.
 *
 * Loaded via Babel; uses React.* hooks (shared-scope rule — see player.jsx).
 */

/* ── Frequency-bar visualizer (taps the graph's AnalyserNode) ─────────── */
const Visualizer = ({ p, height = 120 }) => {
  const canvasRef = React.useRef(null);
  const rafRef = React.useRef(0);
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const draw = () => {
      const analyser = p.getAnalyser && p.getAnalyser();
      const w = canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1);
      const h = canvas.height = height * (window.devicePixelRatio || 1);
      ctx.clearRect(0, 0, w, h);
      if (analyser) {
        const bins = analyser.frequencyBinCount;
        const data = new Uint8Array(bins);
        analyser.getByteFrequencyData(data);
        const bars = 48;
        const step = Math.floor(bins / bars);
        const bw = w / bars;
        for (let i = 0; i < bars; i++) {
          let sum = 0;
          for (let j = 0; j < step; j++) sum += data[i * step + j];
          const v = (sum / step) / 255;
          const bh = Math.max(2, v * h);
          const hue = 60 - v * 30;
          ctx.fillStyle = `oklch(${0.55 + v * 0.2} ${0.12 + v * 0.06} ${hue})`;
          ctx.fillRect(i * bw + bw * 0.15, h - bh, bw * 0.7, bh);
        }
      } else {
        ctx.fillStyle = 'var(--fg-faint)';
      }
      rafRef.current = requestAnimationFrame(draw);
    };
    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, [p, height]);
  return <canvas ref={canvasRef} style={{ width: '100%', height, display: 'block' }}/>;
};

/* ── 10-band graphic EQ ───────────────────────────────────────────────── */
const EqPanel = ({ p }) => {
  const presets = {
    flat: EQ_BANDS.map(() => 0),
    bass: [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    vocal: [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
    treble: [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
  };
  const applyPreset = (name) => presets[name].forEach((g, i) => p.setEqBand(i, g));
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
          <input type="checkbox" checked={p.eqEnabled} onChange={(e) => p.setEqEnabled(e.target.checked)}/>
          equalizer
        </label>
        <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
          {Object.keys(presets).map((name) => (
            <button key={name} onClick={() => applyPreset(name)}
                    style={{ font: 'inherit', fontSize: 11, padding: '3px 9px', borderRadius: 'var(--r-full)',
                             border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg-muted)', cursor: 'pointer' }}>
              {name}
            </button>
          ))}
          <Button icon="rotate-ccw" onClick={p.resetEq}>reset</Button>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${EQ_BANDS.length}, 1fr)`, gap: 6, opacity: p.eqEnabled ? 1 : 0.45 }}>
        {EQ_BANDS.map((f, i) => (
          <div key={f} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
            <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>
              {p.eqBands[i] > 0 ? '+' : ''}{p.eqBands[i]}
            </span>
            <input type="range" min={EQ_MIN_DB} max={EQ_MAX_DB} step={1}
                   value={p.eqBands[i]} disabled={!p.eqEnabled}
                   onChange={(e) => p.setEqBand(i, Number(e.target.value))}
                   className="eq-slider"
                   style={{ writingMode: 'vertical-lr', direction: 'rtl', width: 20, height: 110 }}/>
            <span className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)' }}>{EQ_LABELS[i]}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

/* ── Sleep timer control ──────────────────────────────────────────────── */
const SleepTimerControl = ({ p }) => {
  const opts = [15, 30, 45, 60];
  const active = p.sleepRemainingSec != null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <Icon name="moon" size={14}/>
      <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>sleep</span>
      {opts.map((m) => (
        <button key={m} onClick={() => p.setSleep(m)}
                style={{ font: 'inherit', fontSize: 11, padding: '3px 9px', borderRadius: 'var(--r-full)',
                         border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg-muted)', cursor: 'pointer' }}>
          {m}m
        </button>
      ))}
      <button onClick={() => p.setSleep('end-of-track')}
              style={{ font: 'inherit', fontSize: 11, padding: '3px 9px', borderRadius: 'var(--r-full)',
                       border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg-muted)', cursor: 'pointer' }}>
        end of track
      </button>
      {p.current && p.current.meta && p.current.meta.itemType && (
        <button onClick={() => p.setSleep('end-of-chapter')}
                style={{ font: 'inherit', fontSize: 11, padding: '3px 9px', borderRadius: 'var(--r-full)',
                         border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg-muted)', cursor: 'pointer' }}>
          end of chapter
        </button>
      )}
      {active && (
        <span className="mono" style={{ fontSize: 11, color: 'var(--brand)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {p.sleepRemainingSec < 0 ? 'ends after track' : fmtDur(p.sleepRemainingSec)}
          <IconButton name="x" onClick={p.cancelSleep}/>
        </span>
      )}
    </div>
  );
};

/* ── Offline / PWA controls for the current track ─────────────────────── */
const OfflineControl = ({ p }) => {
  const it = p.current;
  const [, force] = React.useState(0);
  if (!it) return null;
  if (!p.offline.supported()) {
    return <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>offline cache unavailable</span>;
  }
  if (!it.cacheable) {
    return <span style={{ fontSize: 11, color: 'var(--fg-faint)' }} title="live streams can't be cached">offline: n/a (live)</span>;
  }
  const pinned = p.offline.isPinned(it.trackId);
  const toggle = async () => {
    if (pinned) await p.offline.unpin(it.trackId);
    else await p.offline.pin(it);
    force((n) => n + 1);
  };
  const usedMb = Math.round(p.offline.usage() / (1024 * 1024));
  const budgetMb = Math.round(p.offline.budget() / (1024 * 1024));
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <Button icon={pinned ? 'download' : 'download-cloud'} onClick={toggle}
              style={pinned ? { color: 'var(--brand)' } : undefined}>
        {pinned ? 'pinned offline' : 'download for offline'}
      </Button>
      <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>{usedMb} / {budgetMb} MB</span>
    </div>
  );
};

/* ── Spoken-audio transport: ±15/30s skip + speed ─────────────────────── */
const SpokenControls = ({ p }) => {
  const it = p.current;
  if (!it || !(it.meta && it.meta.itemType)) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 20px', flexWrap: 'wrap',
                  borderTop: '1px solid var(--border-soft)' }}>
      <Button icon="rotate-ccw" onClick={() => p.seekBy(-30)}>30s</Button>
      <Button icon="rotate-ccw" onClick={() => p.seekBy(-15)}>15s</Button>
      <Button icon="rotate-cw" onClick={() => p.seekBy(15)}>15s</Button>
      <Button icon="rotate-cw" onClick={() => p.seekBy(30)}>30s</Button>
      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--fg-muted)', marginLeft: 'auto' }}>
        speed
        <select value={p.playbackRate} onChange={(e) => p.setPlaybackRate(Number(e.target.value))}
                style={{ font: 'inherit', fontSize: 12, height: 26, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
          {[0.75, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3].map((r) => <option key={r} value={r}>{r}×</option>)}
        </select>
      </label>
    </div>
  );
};

/* ── Chapter list + jump (podcasts / audiobooks) ──────────────────────── */
const ChapterList = ({ p }) => {
  const it = p.current;
  const chapters = it && Array.isArray(it.chapters) ? it.chapters : [];
  if (!chapters.length) return null;
  // Which chapter is current, from the live position.
  let cur = 0;
  chapters.forEach((c, i) => { if ((p.positionSec || 0) >= (c.start_sec || 0)) cur = i; });
  return (
    <div style={{ borderTop: '1px solid var(--border-soft)', maxHeight: 240, overflowY: 'auto' }}>
      <div style={{ padding: '8px 20px', fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)' }}>
        chapters · {chapters.length}
      </div>
      {chapters.map((c, i) => (
        <button key={i} onClick={() => p.jumpToChapter(i)}
                style={{ font: 'inherit', width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                         padding: '8px 20px', border: 'none', borderTop: '1px solid var(--border-soft)',
                         background: i === cur ? 'var(--brand-soft)' : 'transparent', color: 'var(--fg)',
                         cursor: 'pointer', textAlign: 'left', fontSize: 12 }}>
          <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', width: 44, textAlign: 'right' }}>
            {fmtDur(c.start_sec || 0)}
          </span>
          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {c.title || `Chapter ${i + 1}`}
          </span>
          {i === cur && <Icon name="volume-2" size={12}/>}
        </button>
      ))}
    </div>
  );
};

/* ── "Listening as [person]" selector — browser has no voice ID ───────── */
const ListeningAsSelector = () => {
  const p = usePlayback();
  const { items: people } = useApiList('/api/people', { eventTypes: ['people.last_seen.changed'] });
  if (!p.available) return null;
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--fg-muted)' }}>
      <Icon name="user" size={13}/> listening as
      <select value={p.listenerPersonId ?? ''}
              onChange={(e) => p.setListenerPersonId(e.target.value === '' ? null : Number(e.target.value))}
              style={{ font: 'inherit', fontSize: 12, height: 28, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
        <option value="">me (this browser)</option>
        {(people || []).map((pers) => <option key={pers.id} value={pers.id}>{pers.name}</option>)}
      </select>
    </label>
  );
};

/* ── The full expanded now-playing panel ──────────────────────────────── */
const NowPlayingPanel = () => {
  const p = usePlayback();
  if (!p.available) {
    return <Empty glyph="headphones" title="player unavailable"
                  sub="the playback provider isn't mounted — see INTEGRATION_music.md"/>;
  }
  const it = p.current;
  if (!it) {
    return <Empty glyph="headphones" title="nothing queued"
                  sub="play a library track in the browser from the Library tab to start"/>;
  }
  const dur = p.durationSec || it.durationSec || 0;
  const pct = dur > 0 ? Math.min(100, (p.positionSec / dur) * 100) : 0;
  const remote = p.target.kind === 'room';
  return (
    <div style={{ padding: 0 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 20, padding: 20, alignItems: 'center' }}>
        <CoverTile item={it} size={220} radius="var(--r-md)"/>
        <div style={{ minWidth: 0 }}>
          {remote && <Pill tone="live" live>casting to {p.target.roomId}</Pill>}
          <h2 className="h2" style={{ margin: '6px 0 2px' }}>{it.title}</h2>
          <div style={{ fontSize: 15, color: 'var(--fg-muted)' }}>{it.artist || '—'}</div>
          {it.album && <div style={{ fontSize: 13, color: 'var(--fg-faint)' }}>{it.album}</div>}

          {/* progress */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '18px 0 10px' }}>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', width: 40, textAlign: 'right' }}>{fmtDur(p.positionSec)}</span>
            <div onClick={(e) => { if (!it.seekable) return; const r = e.currentTarget.getBoundingClientRect(); p.seek(((e.clientX - r.left) / r.width) * dur); }}
                 style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--sunken)', overflow: 'hidden', cursor: it.seekable ? 'pointer' : 'default' }}>
              <div style={{ height: '100%', width: `${pct}%`, background: 'var(--brand)' }}/>
            </div>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', width: 40 }}>{it.seekable ? fmtDur(dur) : 'live'}</span>
          </div>

          {/* transport */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <IconButton name="skip-back" onClick={p.prev}/>
            <button className="btn btn-primary btn-icon" onClick={p.toggle} style={{ width: 44, height: 44, borderRadius: '50%' }}>
              <Icon name={p.status === 'playing' ? 'pause' : 'play'} size={20}/>
            </button>
            <IconButton name="skip-forward" onClick={p.next}/>
            <IconButton name="square" onClick={p.stop}/>
            {!remote && (
              <>
                <IconButton name={p.muted || p.volume === 0 ? 'volume-x' : 'volume-2'} onClick={p.toggleMute}/>
                <input type="range" min={0} max={1} step={0.01} value={p.muted ? 0 : p.volume}
                       onChange={(e) => p.setVolume(Number(e.target.value))} style={{ width: 110 }}/>
              </>
            )}
            {!remote && (
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--fg-muted)', marginLeft: 8 }}>
                speed
                <select value={p.playbackRate} onChange={(e) => p.setPlaybackRate(Number(e.target.value))}
                        style={{ font: 'inherit', fontSize: 12, height: 26, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
                  {[0.75, 1, 1.25, 1.5, 2].map((r) => <option key={r} value={r}>{r}×</option>)}
                </select>
              </label>
            )}
          </div>
        </div>
      </div>

      {/* visualizer */}
      {!remote && (
        <div style={{ padding: '0 20px 12px' }}>
          <Visualizer p={p} height={120}/>
        </div>
      )}

      {/* spoken-audio skip + speed */}
      {!remote && <SpokenControls p={p}/>}

      {/* controls row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 20px', borderTop: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
        <SleepTimerControl p={p}/>
        <div style={{ marginLeft: 'auto' }}><OfflineControl p={p}/></div>
      </div>

      {/* chapter list (podcasts / audiobooks) */}
      {!remote && <ChapterList p={p}/>}

      {/* EQ */}
      {!remote && (
        <div style={{ borderTop: '1px solid var(--border-soft)' }}>
          <EqPanel p={p}/>
        </div>
      )}
    </div>
  );
};

Object.assign(window, {
  NowPlayingPanel, Visualizer, EqPanel, SleepTimerControl, OfflineControl,
  SpokenControls, ChapterList, ListeningAsSelector,
});
