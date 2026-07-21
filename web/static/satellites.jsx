/* Satellites page — per-room Pi grid + drill-in detail drawer + broadcast composer.
 *
 * Data sources:
 *   * GET /api/satellites                                     — grid roster
 *   * GET /api/satellites/{room}/sessions                     — drawer · sessions tab
 *   * GET /api/satellites/{room}/conversations                — drawer · conversations tab
 *   * GET /api/satellites/{room}/notes                        — drawer · notes tab
 *   * GET /api/satellites/{room}/timers                       — drawer · timers tab
 *   * GET /api/satellites/{room}/recently-played              — drawer · recently-played tab
 *   * POST /api/music/add-by-url|query                        — drawer · recently-played "+ add" (generic acquisition)
 *   * POST /api/satellites/{room}/volume                      — drawer · overview · volume slider (master output)
 *   * POST /api/satellites/{room}/restart                     — drawer · overview · Restart satellite
 *   * POST /api/satellites/{room}/upgrade                     — drawer · overview · Upgrade satellite (code sync + self-restart)
 *   * POST /api/satellites/{room}/pairing/reset               — drawer · overview · Reset pairing (admin-gated, V002 WS auth)
 *   * GET  /api/config/version                                — domovoi git SHA → "needs upgrade" comparison
 *   * POST /api/satellites/{room}/dropin/start                — drawer · overview · Drop in (→ target_room)
 *   * POST /api/satellites/{room}/dropin/end                  — drawer · overview · Hang up
 *   * GET  /api/satellites/{room}/config                      — drawer · settings tab (load)
 *   * PATCH /api/satellites/{room}/config                     — drawer · settings tab (save → Pi rewrites config.toml + restarts)
 *   * /ws/state · `satellites.presence.changed`               — refresh roster
 *   * /ws/state · `satellites.wifi.changed`                   — refresh roster
 *   * /ws/state · `satellites.dropins.changed`                — refresh roster (live drop-in pairings)
 *
 * Note: the backend's Session shape has `person_id`, not `person_name`,
 * so we don't try to render a person label in the row — the People page
 * is the right surface for that linkage. Same for ConversationTurn.
 */

/* ---- helpers ---------------------------------------------- */
const wifiTone = (rx) => rx == null ? 'idle' : rx < 5 ? 'err' : rx < 15 ? 'warn' : 'ok';
const wifiColor = (tone) => ({ ok: 'var(--ok)', warn: 'var(--warn)', err: 'var(--err)', idle: 'var(--fg-faint)' }[tone]);

const fmtRemaining = (sec) => {
  if (sec == null) return '—';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s.toString().padStart(2,'0')}s`;
  return `${s}s`;
};

const remainingFromExpiresAt = (iso) => {
  if (!iso) return null;
  const ms = new Date(iso) - new Date();
  return Math.max(0, Math.round(ms / 1000));
};

/* ---- Satellite card --------------------------------------- */
const SatCard = ({ s, onOpen, tick }) => {
  const online = s.status === 'online';
  const np = s.now_playing;
  const playing = np?.state === 'play' && np.song;
  const songDur = np?.song?.duration_sec ?? 0;
  const elapsed = playing ? (np.elapsed_sec ?? 0) + tick : 0;
  const progress = playing && songDur ? Math.min(100, (elapsed / songDur) * 100) : 0;
  const wTone = wifiTone(s.wifi?.rx_mbits);
  const wCol  = wifiColor(wTone);

  return (
    <button onClick={() => onOpen(s)}
            className="sat-card"
            data-online={online}
            style={{ font: 'inherit', textAlign: 'left', cursor: 'pointer',
                     background: 'var(--card)', border: '1px solid var(--border)',
                     borderRadius: 'var(--r-md)', boxShadow: 'var(--inner-highlight)',
                     padding: 0, overflow: 'hidden',
                     opacity: online ? 1 : 0.78,
                     transition: 'transform .18s ease, box-shadow .18s ease, opacity .2s ease' }}
            onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.boxShadow = 'var(--shadow-md), var(--inner-highlight)'; }}
            onMouseLeave={e => { e.currentTarget.style.transform = ''; e.currentTarget.style.boxShadow = 'var(--inner-highlight)'; }}>
      <div style={{ padding: '14px 16px 10px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <StatusDot tone={online ? 'ok' : 'idle'} live={online}/>
        <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em', flex: 1, minWidth: 0,
                      color: online ? 'var(--fg)' : 'var(--fg-muted)' }}>
          {s.room_id}
        </div>
        <Pill tone={online ? 'live' : 'idle'} live={online}>{online ? 'online' : 'offline'}</Pill>
      </div>

      <div style={{ padding: '0 16px 12px', minHeight: 56 }}>
        {playing ? (
          <>
            <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {np.song.title || np.song.file?.split('/').pop() || 'unknown'}
              {np.song.artist && <span style={{ color: 'var(--fg-muted)', fontWeight: 400 }}> · {np.song.artist}</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
              <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--sunken)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${progress}%`, background: 'var(--brand)' }}/>
              </div>
              <span className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)' }}>
                {fmtDur(elapsed)}{songDur ? ` / ${fmtDur(songDur)}` : ''}
              </span>
            </div>
          </>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>no music</div>
        )}
      </div>

      <div style={{ borderTop: '1px solid var(--border-soft)', padding: '10px 16px',
                    display: 'flex', alignItems: 'center', gap: 10, background: 'var(--sunken)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: wCol }}>
          <Icon name="wifi" size={13}/>
          <span className="mono" style={{ fontSize: 12, color: 'var(--fg)' }}>
            {s.wifi?.rx_mbits != null ? `${s.wifi.rx_mbits.toFixed(0)} Mbit/s` : '—'}
          </span>
        </div>
        <span style={{ flex: 1 }}/>
        <span className="mono" style={{ fontSize: 11, color: online ? 'var(--ok)' : 'var(--fg-faint)' }}>
          {online ? 'active now' : relTime(s.last_connected_at)}
        </span>
      </div>
    </button>
  );
};

/* ---- Drawer scoped session/conversation rows -------------- */
const SatSessionRow = ({ session, turns }) => {
  const [open, setOpen] = React.useState(false);
  const dur = session.last_activity && session.started_at
    ? (new Date(session.last_activity) - new Date(session.started_at)) / 1000
    : 0;
  return (
    <div style={{ borderBottom: '1px solid var(--border-soft)' }}>
      <div onClick={() => setOpen(o => !o)}
           style={{ padding: '12px 16px', display: 'grid',
                    gridTemplateColumns: '20px 1fr auto auto', gap: 12, alignItems: 'center', cursor: 'pointer' }}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14}/>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>{relTime(session.started_at)}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
            {(session.id || '').slice(0, 8)} · {fmtDur(Math.round(dur))}
            {session.person_id != null && ` · person #${session.person_id}`}
          </div>
        </div>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{session.intent_count} turn{session.intent_count === 1 ? '' : 's'}</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', minWidth: 80, textAlign: 'right' }}>last {relTime(session.last_activity)}</span>
      </div>
      {open && (
        <div style={{ padding: '4px 16px 14px 48px', background: 'var(--sunken)' }}>
          {turns.length === 0
            ? <div style={{ fontSize: 12, color: 'var(--fg-faint)', padding: '12px 0' }}>no turns recorded</div>
            : turns.map(c => (
                <div key={c.id} style={{ padding: '10px 0', borderTop: '1px solid var(--border-soft)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{relTime(c.at)}</span>
                    <Pill tone={c.matched_handler ? 'live' : 'idle'}>{c.matched_handler || 'qa'}</Pill>
                    <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>{c.matched_path}</span>
                  </div>
                  <div style={{ fontSize: 13 }}>“{c.user_text}”</div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 3, display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                    <DomovoiGlyph size={12}/><span>{c.assistant_text}</span>
                  </div>
                </div>
              ))}
        </div>
      )}
    </div>
  );
};

const SatConversationTurn = ({ c }) => {
  const long = (c.assistant_text || '').length > 140;
  const [more, setMore] = React.useState(false);
  const txt = c.assistant_text || '';
  const shown = long && !more ? txt.slice(0, 130).trimEnd() + '…' : txt;
  return (
    <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{relTime(c.at)}</span>
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

/* ---- Volume control (overview tab) ------------------------ */
// Master output volume for a room's satellite. Drives the Pi's hardware mixer
// (scales BOTH speech and music) via POST /api/satellites/{room}/volume, which
// the server relays as a `set_volume` frame. The slider debounces so a
// drag fires one request on settle; the Pi re-reports its real level via
// volume_status, which flows back through the snapshot as `s.volume`.
const VolumeControl = ({ s, fire }) => {
  const online = s.status === 'online';
  const known = s.volume != null;
  const [vol, setVol] = React.useState(known ? s.volume : 50);
  const [touched, setTouched] = React.useState(false);
  const dirtyRef = React.useRef(false);   // true while a local edit awaits commit
  const timerRef = React.useRef(null);

  // Follow the server-reported level (e.g. a spoken "turn it up", or another
  // dashboard) whenever the user isn't mid-adjustment.
  React.useEffect(() => {
    if (s.volume != null && !dirtyRef.current) setVol(s.volume);
  }, [s.volume]);
  // Clean up a pending debounce on unmount / room switch.
  React.useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const commit = async (level) => {
    try {
      await apiPost(`/api/satellites/${s.room_id}/volume`, { level });
      fire(`${s.room_id} volume set to ${level}%`);
    } catch (e) {
      fire(`volume failed: ${e.message}`);
    } finally {
      dirtyRef.current = false;
    }
  };

  const onSlide = (v) => {
    setVol(v); setTouched(true); dirtyRef.current = true;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => commit(v), 300);
  };

  const nudge = (delta) => {
    const v = Math.max(0, Math.min(100, vol + delta));
    setVol(v); setTouched(true); dirtyRef.current = true;
    if (timerRef.current) clearTimeout(timerRef.current);
    commit(v);
  };

  return (
    <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
      <div className="label" style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
        volume
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 12,
                                        color: (known || touched) ? 'var(--fg)' : 'var(--fg-faint)' }}>
          {(known || touched) ? `${vol}%` : 'unknown'}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <IconButton name="volume-1" onClick={() => nudge(-5)} disabled={!online} title="quieter"/>
        <input type="range" min={0} max={100} step={1} value={vol}
               disabled={!online}
               onChange={e => onSlide(Number(e.target.value))}
               style={{ flex: 1, accentColor: 'var(--brand)',
                        cursor: online ? 'pointer' : 'not-allowed' }}/>
        <IconButton name="volume-2" onClick={() => nudge(5)} disabled={!online} title="louder"/>
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 6 }}>
        master output — scales speech and music on {s.room_id}
        {!known && online && ' · move to set (this board may not report its level)'}
      </div>
    </div>
  );
};

/* ---- Drawer body tabs ------------------------------------- */
const OverviewBody = ({ s, sats, fire }) => {
  const [msg, setMsg] = React.useState('');
  const [peer, setPeer] = React.useState('');
  const wTone = wifiTone(s.wifi?.rx_mbits);
  const np = s.now_playing;
  const playing = np?.state === 'play' && np.song;
  const songDur = np?.song?.duration_sec ?? 0;

  // The server's current git SHA. A satellite whose last-synced SHA
  // (s.version) differs is running older code and can be upgraded in place.
  // A null s.version (a Pi that has never reported a synced SHA — e.g. one
  // updated by hand, predating its first UI upgrade) is treated as UNKNOWN,
  // not behind, so we don't nag with a false "needs upgrade" pill.
  const { data: coreVer } = useApiObject('/api/config/version');
  const coreSha = coreVer && coreVer.sha;
  const needsUpgrade = !!(coreSha && s.version && s.version !== coreSha);
  // The Upgrade button is enabled whenever the room is online and NOT known to
  // be up to date — i.e. unknown-version (bootstrap the first UI upgrade so the
  // Pi starts reporting its synced SHA) OR a confirmed mismatch. Only a
  // known-equal version disables it. Without the bootstrap case, a hand-updated
  // Pi (which reports no version yet) could never trigger its first UI upgrade.
  const upToDate = !!(coreSha && s.version && s.version === coreSha);

  const sendAnnounce = async () => {
    if (!msg.trim()) {
      fire('type a message first');
      return;
    }
    if (s.status !== 'online') {
      fire(`${s.room_id} is offline`);
      return;
    }
    try {
      // The server returns a 200 with `announced_to: []` when
      // the WS to this room is dead under the active-sessions map.
      // Inspect that to give an honest toast.
      const result = await apiPost(`/api/satellites/${s.room_id}/announce`, { message: msg });
      const delivered = Array.isArray(result?.announced_to) ? result.announced_to : [];
      if (delivered.includes(s.room_id)) {
        fire(`announced to ${s.room_id}`);
      } else {
        fire(`${s.room_id} accepted the request but didn't play — connection may be dead`);
      }
      setMsg('');
    } catch (e) {
      fire(`announce failed: ${e.message}`);
    }
  };

  const restartSat = async () => {
    if (s.status !== 'online') { fire(`${s.room_id} is offline`); return; }
    if (!window.confirm(`Restart the ${s.room_id} satellite? It'll drop offline for a few seconds while the service bounces.`)) return;
    try {
      await apiPost(`/api/satellites/${s.room_id}/restart`, {});
      fire(`restarting ${s.room_id}…`);
    } catch (e) {
      fire(`restart failed: ${e.message}`);
    }
  };

  const upgradeSat = async () => {
    if (s.status !== 'online') { fire(`${s.room_id} is offline`); return; }
    if (!window.confirm(
      `Upgrade the ${s.room_id} satellite to ${coreSha || 'the latest domovoi code'}?\n\n` +
      `It pulls the new satellite code from the Domovoi server, backs up its current ` +
      `tree, and restarts — dropping offline for a few seconds. If the new code won't ` +
      `start, it rolls back automatically.`
    )) return;
    try {
      await apiPost(`/api/satellites/${s.room_id}/upgrade`, {});
      fire(`upgrading ${s.room_id}…`);
    } catch (e) {
      fire(`upgrade failed: ${e.message}`);
    }
  };

  // Reset pairing (V002): delete this room's pairing row so the next
  // connect re-pairs trust-on-first-use. Needed after re-flashing a Pi or
  // moving a room to a new device (the old token no longer matches). Works
  // whether or not the room is online — the pairing lives in the DB.
  const resetPairing = async () => {
    if (!window.confirm(
      `Reset pairing for ${s.room_id}?\n\n` +
      `The next device that connects as this room will re-pair and take it ` +
      `over. Do this after re-flashing the Pi or moving the room to a new ` +
      `device — otherwise leave it alone, since it lets a new device claim ` +
      `the room.`
    )) return;
    try {
      const r = await apiPost(`/api/satellites/${s.room_id}/pairing/reset`, {});
      fire(r && r.reset ? `pairing reset for ${s.room_id}` : `${s.room_id} had no pairing to reset`);
    } catch (e) {
      fire(`reset pairing failed: ${e.message}`);
    }
  };

  // Drop-in: eligible peers are OTHER rooms that are online, AEC-capable
  // (full_duplex), and not already in a call. The server re-checks all
  // of this and refuses with a clear reason, but pre-filtering keeps the
  // dropdown honest.
  const peers = (sats || []).filter(
    p => p.room_id !== s.room_id && p.status === 'online' && p.full_duplex && !p.in_call_with
  );
  const startDropIn = async () => {
    if (!peer) { fire('pick a room to drop in on'); return; }
    if (s.status !== 'online') { fire(`${s.room_id} is offline`); return; }
    try {
      await apiPost(`/api/satellites/${s.room_id}/dropin/start`, { target_room: peer });
      fire(`dropping in: ${s.room_id} → ${peer}`);
      setPeer('');
    } catch (e) {
      fire(`drop-in failed: ${e.message}`);
    }
  };
  const hangUp = async () => {
    try {
      await apiPost(`/api/satellites/${s.room_id}/dropin/end`, {});
      fire(`hung up ${s.room_id}`);
    } catch (e) {
      fire(`hang up failed: ${e.message}`);
    }
  };

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--border-soft)' }}>
        <div style={{ padding: '14px 16px', borderRight: '1px solid var(--border-soft)' }}>
          <div className="label">last connected</div>
          <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{s.status === 'online' ? 'active now' : relTime(s.last_connected_at)}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
            {s.last_connected_at ? s.last_connected_at.replace('T',' ').slice(0,16) + ' UTC' : '—'}
          </div>
        </div>
        <div style={{ padding: '14px 16px' }}>
          <div className="label">wi-fi</div>
          {s.wifi && s.wifi.rx_mbits != null ? (
            <>
              <div className="mono" style={{ fontSize: 16, fontWeight: 600, marginTop: 2, color: wifiColor(wTone) }}>
                {s.wifi.rx_mbits.toFixed(1)}
                {s.wifi.tx_mbits != null && (
                  <span style={{ fontSize: 11, color: 'var(--fg-muted)', fontWeight: 400 }}> / {s.wifi.tx_mbits.toFixed(1)} Mbit/s</span>
                )}
              </div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>{s.wifi.ssid || '—'}</div>
            </>
          ) : (
            <div className="mono" style={{ fontSize: 13, color: 'var(--fg-faint)', marginTop: 4 }}>no signal</div>
          )}
        </div>
      </div>

      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6 }}>now playing</div>
        {playing ? (
          <div style={{ fontSize: 13 }}>
            <strong style={{ fontWeight: 500 }}>{np.song.title || np.song.file?.split('/').pop() || 'unknown'}</strong>
            {np.song.artist && <span style={{ color: 'var(--fg-muted)' }}> · {np.song.artist}</span>}
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginLeft: 8 }}>
              {fmtDur(np.elapsed_sec)}{songDur ? ` / ${fmtDur(songDur)}` : ''}
            </span>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>nothing playing</div>
        )}
      </div>

      <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 8, fontSize: 12, borderBottom: '1px solid var(--border-soft)' }}>
        <div className="label">room id</div>
        <div className="mono" style={{ userSelect: 'all' }}>{s.room_id}</div>
        <div className="label">voice</div>
        <div className="mono" style={{ color: s.voice ? 'var(--fg)' : 'var(--fg-faint)' }}>{s.voice || 'registry default'}</div>
        <div className="label">version</div>
        <div className="mono" style={{ color: s.version ? 'var(--fg)' : 'var(--fg-faint)' }}>{s.version || '—'}</div>
        <div className="label">pairing</div>
        <div style={{ fontSize: 12 }}>
          {s.pairing && s.pairing.paired ? (
            <span style={{ color: 'var(--ok)' }}>
              paired
              {s.pairing.paired_at && (
                <span className="mono" style={{ color: 'var(--fg-muted)', fontWeight: 400 }}>
                  {' '}since {s.pairing.paired_at.replace('T', ' ').slice(0, 16)} UTC
                </span>
              )}
              {s.pairing.last_seen_at && (
                <span className="mono" style={{ color: 'var(--fg-faint)', fontWeight: 400 }}>
                  {' '}· last seen {relTime(s.pairing.last_seen_at)}
                </span>
              )}
            </span>
          ) : (
            <span style={{ color: 'var(--fg-faint)' }}>unpaired</span>
          )}
        </div>
        <div className="label">mpd ports</div>
        <div className="mono">control :{s.mpd_ports.control} · http :{s.mpd_ports.http}</div>
        <div className="label">stream</div>
        <div className="mono" style={{ color: 'var(--fg-muted)' }}>{np?.stream_url || `(http :${s.mpd_ports.http})`}</div>
      </div>

      <VolumeControl s={s} fire={fire}/>

      <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6 }}>announce to {s.room_id}</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={msg} onChange={e => setMsg(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') sendAnnounce(); }}
                 placeholder={`speak through the ${s.room_id} satellite…`}
                 disabled={s.status !== 'online'}
                 style={{ flex: 1, font: 'inherit', fontSize: 13, height: 32, padding: '0 10px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <Button variant="primary" icon="send" disabled={!msg.trim() || s.status !== 'online'} onClick={sendAnnounce}>
            send
          </Button>
        </div>
      </div>

      <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6 }}>drop in</div>
        {s.in_call_with ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Pill tone="live" live>in call with {s.in_call_with}</Pill>
            <Button icon="x" onClick={hangUp}>Hang up</Button>
          </div>
        ) : !s.full_duplex ? (
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
            this room's mic can't do drop-in — it needs an echo-cancelling array (XVF3800)
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 8 }}>
            <select value={peer} onChange={e => setPeer(e.target.value)}
                    disabled={s.status !== 'online' || peers.length === 0}
                    style={{ flex: 1, font: 'inherit', fontSize: 13, height: 32, padding: '0 8px',
                             borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                             background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}>
              <option value="">{peers.length ? 'choose a room…' : 'no eligible rooms'}</option>
              {peers.map(p => <option key={p.room_id} value={p.room_id}>{p.room_id}</option>)}
            </select>
            <Button variant="primary" icon="phone" disabled={!peer || s.status !== 'online'} onClick={startDropIn}>
              Start
            </Button>
          </div>
        )}
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 6 }}>
          live two-way audio — say "hang up" or use the button to end
        </div>
      </div>

      <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
          maintenance
          {needsUpgrade && <Pill tone="warn">needs upgrade</Pill>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button icon="refresh-cw" disabled={s.status !== 'online'} onClick={restartSat}>
            Restart satellite
          </Button>
          <Button variant={needsUpgrade ? 'primary' : 'secondary'} icon="download-cloud"
                  disabled={s.status !== 'online' || upToDate} onClick={upgradeSat}>
            Upgrade satellite
          </Button>
          <Button icon="shield-off" disabled={!(s.pairing && s.pairing.paired)} onClick={resetPairing}>
            Reset pairing
          </Button>
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 6 }}>
          restart bounces domovoi-satellite.service · upgrade syncs satellite code to {coreSha || 'the Domovoi server'} then restarts · reset pairing lets a re-flashed / new device re-pair as this room
        </div>
      </div>
    </>
  );
};

const RoomSessionsBody = ({ room }) => {
  const { items: sessions, loading } = useApiList(`/api/satellites/${room}/sessions?limit=100`);
  const { items: convos } = useApiList(`/api/satellites/${room}/conversations?limit=300`);
  if (loading && sessions.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading sessions…</div>;
  if (sessions.length === 0) return <Empty glyph="sleeping" title="no sessions in this room"/>;
  return <div>{sessions.map(s => (
    <SatSessionRow key={s.id} session={s} turns={convos.filter(c => c.session_id === s.id)}/>
  ))}</div>;
};

const RoomConversationsBody = ({ room }) => {
  const [q, setQ] = React.useState('');
  const { items: all, loading } = useApiList(`/api/satellites/${room}/conversations?limit=300`);
  const filtered = all.filter(c => !q || ((c.user_text || '') + ' ' + (c.assistant_text || '')).toLowerCase().includes(q.toLowerCase()));
  if (loading && all.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading conversations…</div>;
  return (
    <>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ position: 'relative', flex: 1, maxWidth: 320 }}>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="search this room…"
                 style={{ font: 'inherit', fontSize: 13, width: '100%', height: 30, padding: '0 10px 0 28px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <span style={{ position: 'absolute', left: 9, top: 8, color: 'var(--fg-subtle)', pointerEvents: 'none' }}>
            <Icon name="search" size={13}/>
          </span>
        </div>
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-faint)' }}>
          {filtered.length} of {all.length}
        </span>
      </div>
      {filtered.length === 0
        ? <Empty glyph="sleeping" title="nothing said in this room" sub={q ? `q = “${q}”` : 'satellite has been quiet'}/>
        : <div>{filtered.map(c => <SatConversationTurn key={c.id} c={c}/>)}</div>}
    </>
  );
};

const RoomNotesBody = ({ room }) => {
  const { items: notes, loading } = useApiList(`/api/satellites/${room}/notes`);
  if (loading && notes.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading notes…</div>;
  if (notes.length === 0)
    return <Empty glyph="sleeping" title="no notes from this room" sub="say: domovoi, jot this down — …"/>;
  return (
    <div>
      {notes.map(n => (
        <div key={n.id} style={{ padding: '14px 16px', borderTop: '1px solid var(--border-soft)' }}>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginBottom: 4 }}>
            {relTime(n.captured_at)} · #{n.id}
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.55 }}>{n.body}</div>
        </div>
      ))}
    </div>
  );
};

const RoomTimersBody = ({ room, fire }) => {
  const { items: rows, loading, refresh } = useApiList(`/api/satellites/${room}/timers`);
  // Tick state so the remaining-time column counts down between fetches.
  const [, setTick] = React.useState(0);
  React.useEffect(() => {
    const i = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(i);
  }, []);

  const onCancel = async (t) => {
    try {
      await apiDelete(`/api/satellites/${room}/timers/${t.id}`);
      fire(`cancelled ${t.is_reminder ? 'reminder' : 'timer'} #${t.id}`);
      refresh();
    } catch (e) {
      fire(`cancel failed: ${e.message}`);
    }
  };

  if (loading && rows.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading timers…</div>;
  if (rows.length === 0)
    return <Empty glyph="sleeping" title="no active timers or reminders"/>;
  return (
    <table className="tbl">
      <thead><tr>
        <th>kind</th><th>label</th><th>fires</th><th className="num">remaining</th><th className="actions"></th>
      </tr></thead>
      <tbody>
        {rows.map(t => {
          const remaining = remainingFromExpiresAt(t.expires_at);
          const kind = t.is_reminder ? 'reminder' : 'timer';
          return (
            <tr key={t.id}>
              <td><Pill tone={kind === 'timer' ? 'live' : 'idle'}>{kind}</Pill></td>
              <td style={{ fontWeight: 500 }}>{t.label || (t.is_reminder ? t.message : '—')}</td>
              <td className="mono">{relTime(t.expires_at)}</td>
              <td className="num mono" style={{ color: remaining != null && remaining < 600 ? 'var(--warn)' : 'var(--fg)' }}>
                {fmtRemaining(remaining)}
              </td>
              <td className="actions">
                <Button icon="x" onClick={() => onCancel(t)}>cancel</Button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};

// Source → pill tone for the recently-played table, served by
// /api/capabilities (design §8): handler/source names map to tone slugs
// (media/device/info/comms/neutral) which render as pill tones here.
// Unknown sources fall back to neutral — never an error.
const _TONE_TO_PILL = { media: 'warn', device: 'ok', info: 'idle', comms: 'live', neutral: 'idle' };
const useSourceTones = () => {
  const { data } = useApiObject('/api/capabilities');
  return React.useMemo(() => {
    const map = { library: 'live', playlist: 'idle' };
    if (data) {
      Object.entries(data.source_tones || {}).forEach(([k, tone]) => {
        map[k] = map[k] || _TONE_TO_PILL[tone] || 'idle';
      });
      (data.handler_display || []).forEach((h) => {
        if (!map[h.name]) map[h.name] = _TONE_TO_PILL[h.tone] || 'idle';
      });
    }
    return map;
  }, [data]);
};

const RoomRecentlyPlayedBody = ({ room, fire }) => {
  const { items: rows, loading, refresh } = useApiList(`/api/satellites/${room}/recently-played?limit=100`);
  const sourceTones = useSourceTones();
  // Fulfiller availability gates the "+ add" affordance (design §10.1:
  // the button appears while a media-acquisition-fulfiller capability
  // is registered; null = core unreachable — leave the button on and
  // let the enqueue answer honestly).
  const { data: acqAvail } = useApiObject('/api/acquisitions?limit=1');
  const fulfillerKnownAbsent = acqAvail?.can_fulfill_query === false;
  // Rows added this session — flip the button to "queued" optimistically,
  // since the row only truly drops off once the async acquisition lands.
  const [queued, setQueued] = React.useState(() => new Set());

  const onAdd = async (r) => {
    try {
      // Exact add when the play stored a URL (dedup-keyed so repeat
      // clicks don't queue duplicates); title/artist query otherwise.
      const res = r.url
        ? await apiPost('/api/music/add-by-url', {
            room_id: room, url: r.url, title: r.title,
            dedup_key: r.external_id ? `${r.source}:${r.external_id}` : null,
          })
        : await apiPost('/api/music/add-by-query', {
            room_id: room,
            query: [r.title, r.artist || r.channel].filter(Boolean).join(' '),
            artist: r.artist || null,
          });
      if (res?.already_in_library) fire(`already in library: ${r.title || 'track'}`);
      else if (res?.already_downloading) fire(`already queued: ${r.title || 'track'}`);
      else fire(res?.message || `queued for the library: ${r.title || 'track'}`);
      setQueued(prev => new Set(prev).add(r.id));
      refresh();
    } catch (e) {
      fire(`add failed: ${e.message}`);
    }
  };

  if (loading && rows.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading history…</div>;
  if (rows.length === 0)
    return <Empty glyph="sleeping" title="nothing played in this room yet" sub="play some music and it'll show up here"/>;
  return (
    <table className="tbl">
      <thead><tr>
        <th>when</th><th>source</th><th>track</th><th className="actions"></th>
      </tr></thead>
      <tbody>
        {rows.map(r => {
          const sub = r.artist || r.channel;
          return (
            <tr key={r.id}>
              <td className="mono" style={{ whiteSpace: 'nowrap', color: 'var(--fg-muted)' }}>{relTime(r.started_at)}</td>
              <td><Pill tone={sourceTones[r.source] || 'idle'}>{r.source}</Pill></td>
              <td>
                <div style={{ fontWeight: 500 }}>{r.title || '—'}</div>
                {sub && <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{sub}</div>}
              </td>
              <td className="actions">
                {r.can_add && !fulfillerKnownAbsent && (
                  queued.has(r.id)
                    ? <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>queued</span>
                    : <Button icon="plus" onClick={() => onAdd(r)}>add</Button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};

// Per-satellite config editor. Reuses the global ConfigField + _groupBy
// from components.jsx (same field UI as the server gear). Saving
// pushes edits to the Pi, which rewrites config.toml and restarts.
const RoomSettingsBody = ({ room, fire }) => {
  const { data, loading } = useApiObject(`/api/satellites/${room}/config`);
  const fields = (data && data.fields) || [];
  const reported = data && data.reported;
  const [edits, setEdits] = React.useState({});
  const [saving, setSaving] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [advOpen, setAdvOpen] = React.useState(false);

  const setEdit = (name, v) => setEdits(prev => ({ ...prev, [name]: v }));
  const valueOf = (f) => (f.name in edits ? edits[f.name] : f.value);
  const dirtyCount = Object.keys(edits).length;
  const common = fields.filter(f => f.section !== 'advanced');
  const advanced = fields.filter(f => f.section === 'advanced');
  const rejectedCount = result && result.rejected ? Object.keys(result.rejected).length : 0;

  const save = async () => {
    if (dirtyCount === 0) return;
    if (!window.confirm(`Save and restart the ${room} satellite? It'll drop offline for a few seconds while it applies the change.`)) return;
    setSaving(true); setResult(null);
    try {
      const res = await apiPatch(`/api/satellites/${room}/config`, { changes: edits });
      setResult(res);
      const rejected = (res && res.rejected) || {};
      setEdits(prev => { const n = {}; Object.keys(prev).forEach(k => { if (k in rejected) n[k] = prev[k]; }); return n; });
      if (res && res.restarting) fire(`${room} restarting to apply…`);
    } catch (e) {
      setResult({ error: e.message });
    } finally { setSaving(false); }
  };

  const renderGroups = (list) => {
    const grouped = _groupBy(list);
    return Object.keys(grouped).map(group => (
      <div key={group} style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--fg-muted)', fontWeight: 600, marginBottom: 2 }}>{group}</div>
        {grouped[group].map(f => <ConfigField key={f.name} f={f} value={valueOf(f)} onChange={v => setEdit(f.name, v)}/>)}
      </div>
    ));
  };

  if (loading && fields.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading settings…</div>;
  if (fields.length === 0)
    return <Empty glyph="sleeping" title="settings unavailable" sub="the satellite must be online to edit its config"/>;

  return (
    <div style={{ padding: '12px 16px' }}>
      <div style={{ margin: '0 0 12px', padding: '8px 12px', borderRadius: 'var(--r-sm)', background: 'var(--sunken)', border: '1px solid var(--border-soft)', fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
        Saving rewrites the Pi's <code>config.toml</code> and <strong>restarts the satellite</strong> to apply — it drops offline for a few seconds.
        {!reported && <div style={{ color: 'var(--warn)', marginTop: 4 }}>waiting for the satellite to report its current config…</div>}
      </div>
      {renderGroups(common)}
      {advanced.length > 0 && (
        <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <button onClick={() => setAdvOpen(o => !o)}
                  style={{ font: 'inherit', fontSize: 13, fontWeight: 600, background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--fg)', display: 'flex', alignItems: 'center', gap: 6, padding: 0 }}>
            <Icon name={advOpen ? 'chevron-down' : 'chevron-right'} size={14}/> Advanced
          </button>
          {advOpen && <>
            <div style={{ margin: '10px 0', padding: '10px 12px', borderRadius: 'var(--r-sm)', background: 'oklch(0.8 0.16 60 / 0.12)', border: '1px solid var(--warn)', fontSize: 12, color: 'var(--fg)', lineHeight: 1.5 }}>
              <strong>⚠ These can take the satellite offline.</strong> A wrong audio device, LED, or mic-gain value can leave it without mic or sound until you SSH in and fix <code>~/.domovoi/config.toml</code> (a <code>.bak</code> is saved on every change).
            </div>
            {renderGroups(advanced)}
          </>}
        </div>
      )}
      <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border-soft)' }}>
        {result && result.error && <div style={{ fontSize: 12, color: 'var(--err)', marginBottom: 8 }}>save failed: {result.error}</div>}
        {rejectedCount > 0 && <div style={{ fontSize: 12, color: 'var(--err)', marginBottom: 8 }}>rejected: {Object.entries(result.rejected).map(([k, v]) => `${k} (${v})`).join('; ')}</div>}
        {result && result.restarting && <div style={{ fontSize: 12, color: 'var(--warn)', marginBottom: 8 }}>saved — {room} is restarting to apply. It'll reconnect shortly; reopen to confirm.</div>}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{dirtyCount > 0 ? `${dirtyCount} unsaved` : 'no changes'}</span>
          <div style={{ marginLeft: 'auto' }}>
            <Button variant="primary" icon="check" onClick={save} disabled={saving || dirtyCount === 0}>{saving ? 'Saving…' : 'Save & restart'}</Button>
          </div>
        </div>
      </div>
    </div>
  );
};

/* ---- Drawer ----------------------------------------------- */
const SatDrawer = ({ s, sats, onClose, fire }) => {
  const [tab, setTab] = React.useState('overview');
  React.useEffect(() => { setTab('overview'); }, [s?.room_id]);
  React.useEffect(() => {
    if (!s) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [s, onClose]);
  if (!s) return null;

  // Icon-only tabs (label shows as a hover tooltip) — 7 text labels
  // overflowed the 560px drawer and clipped "Settings".
  const tabs = [
    { id: 'overview',      label: 'Overview',        icon: 'info' },
    { id: 'sessions',      label: 'Sessions',        icon: 'history' },
    { id: 'conversations', label: 'Conversations',   icon: 'message-square' },
    { id: 'recently',      label: 'Recently played', icon: 'music' },
    { id: 'notes',         label: 'Notes',           icon: 'sticky-note' },
    { id: 'timers',        label: 'Timers',          icon: 'timer' },
    { id: 'settings',      label: 'Settings',        icon: 'settings' },
  ];

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.18)', backdropFilter: 'blur(2px)', zIndex: 60 }}/>
      <aside style={{ position: 'fixed', top: 0, right: 0, height: '100vh', width: 560, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 61,
                      display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-soft)',
                      display: 'flex', alignItems: 'center', gap: 10 }}>
          <StatusDot tone={s.status === 'online' ? 'ok' : 'idle'} live={s.status === 'online'}/>
          <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em' }}>{s.room_id}</div>
          <Pill tone={s.status === 'online' ? 'live' : 'idle'} live={s.status === 'online'}>{s.status}</Pill>
          <div style={{ marginLeft: 'auto' }}><IconButton name="x" onClick={onClose}/></div>
        </div>
        <Tabs tabs={tabs} value={tab} onChange={setTab} padX={16}/>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {tab === 'overview'      && <OverviewBody s={s} sats={sats} fire={fire}/>}
          {tab === 'sessions'      && <RoomSessionsBody room={s.room_id}/>}
          {tab === 'conversations' && <RoomConversationsBody room={s.room_id}/>}
          {tab === 'recently'      && <RoomRecentlyPlayedBody room={s.room_id} fire={fire}/>}
          {tab === 'notes'         && <RoomNotesBody room={s.room_id}/>}
          {tab === 'timers'        && <RoomTimersBody room={s.room_id} fire={fire}/>}
          {tab === 'settings'      && <RoomSettingsBody room={s.room_id} fire={fire}/>}
        </div>
      </aside>
    </>
  );
};

/* ---- Broadcast composer ----------------------------------- */
const Broadcast = ({ onlineCount, fire }) => {
  const [msg, setMsg] = React.useState('');
  const send = async () => {
    // Explicit feedback on the no-op branches. The button is also
    // styled `:disabled` for these cases, but Enter-key submission +
    // DevTools-tampering can still get here, and a silent bail leaves
    // the user thinking the button is broken.
    if (!msg.trim()) {
      fire('type a message first');
      return;
    }
    if (onlineCount === 0) {
      fire('no satellites connected — nothing to broadcast to');
      return;
    }
    try {
      // Inspect the server's reported `announced_to` rather than
      // assuming everything reached the speakers. The core returns a
      // 200 even when some rooms had dead WSes underneath the active-
      // sessions map; the truth is in this list.
      const result = await apiPost('/api/satellites/announce-all', { message: msg });
      const delivered = Array.isArray(result?.announced_to) ? result.announced_to : [];
      if (delivered.length === 0) {
        fire('broadcast queued but no satellites accepted it (dead connections?)');
      } else if (delivered.length < onlineCount) {
        fire(`broadcast partial — ${delivered.length}/${onlineCount} reached (${delivered.join(', ')})`);
      } else {
        fire(`broadcasted to ${delivered.length} satellite${delivered.length === 1 ? '' : 's'}`);
      }
      setMsg('');
    } catch (e) {
      fire(`broadcast failed: ${e.message}`);
    }
  };
  return (
    <Card>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', alignItems: 'center', gap: 10 }}>
        <Icon name="megaphone" size={15}/>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Broadcast</div>
        <Pill tone={onlineCount > 0 ? 'live' : 'idle'} live={onlineCount > 0}>
          {onlineCount} online
        </Pill>
      </div>
      <div style={{ padding: 16, background: 'var(--sunken)' }}>
        <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 10 }}>
          Speaks through every connected satellite simultaneously.
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={msg} onChange={e => setMsg(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') send(); }}
                 placeholder="dinner's ready" disabled={onlineCount === 0}
                 style={{ flex: 1, font: 'inherit', fontSize: 14, height: 38, padding: '0 12px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <Button variant="primary" icon="megaphone" disabled={!msg.trim() || onlineCount === 0} onClick={send}>
            broadcast
          </Button>
        </div>
      </div>
    </Card>
  );
};

/* ---- Page ------------------------------------------------- */
const SatellitesPage = () => {
  const [openRoom, setOpenRoom] = React.useState(null);
  const [tick, setTick] = React.useState(0);
  const [fire, toastNode] = useToast();

  const { items: sats, loading } = useApiList('/api/satellites', {
    eventTypes: ['satellites.presence.changed', 'satellites.wifi.changed', 'satellites.dropins.changed', 'music.now_playing.changed'],
  });

  React.useEffect(() => {
    const i = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(i);
  }, []);
  // Reset tick on payload change so the locally-extrapolated elapsed
  // time follows the freshly-fetched canonical elapsed_sec.
  React.useEffect(() => { setTick(0); }, [sats.length, JSON.stringify(sats.map(s => s.now_playing?.elapsed_sec))]);

  const open = sats.find(s => s.room_id === openRoom) || null;
  const onlineCount = sats.filter(s => s.status === 'online').length;
  const offlineCount = sats.filter(s => s.status === 'offline').length;

  return (
    <div className="page">
      <PageHeader
        title="Satellites"
        sub={`${onlineCount} online · ${offlineCount} offline · ${sats.length} provisioned`}
      />

      {loading && sats.length === 0 ? (
        <Card><div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading satellites…</div></Card>
      ) : sats.length === 0 ? (
        <Card><Empty glyph="sleeping" title="no satellites provisioned yet" sub="connect a Pi to bring a room online"/></Card>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14 }}>
          {sats.map(s => <SatCard key={s.room_id} s={s} onOpen={(s) => setOpenRoom(s.room_id)} tick={tick}/>)}
        </div>
      )}

      <Broadcast onlineCount={onlineCount} fire={fire}/>

      <SatDrawer s={open} sats={sats} onClose={() => setOpenRoom(null)} fire={fire}/>
      {toastNode}
    </div>
  );
};

window.SatellitesPage = SatellitesPage;
