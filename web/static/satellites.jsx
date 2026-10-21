/* Satellites — per-room Pi grid + drill-in detail drawer + broadcast composer.
 * Since 2026-10-11 a Settings tab (Settings → Rooms, SatellitesPanel);
 * the old #satellites opens that tab (components.jsx settingsTabRoute).
 *
 * Data sources:
 *   * GET /api/satellites                                     — grid roster
 *   * GET /api/satellites/{room}/sessions                     — drawer · sessions tab
 *   * GET /api/satellites/{room}/conversations                — drawer · conversations tab (paired devices only)
 *   * GET /api/satellites/{room}/notes                        — drawer · notes tab (paired devices only)
 *   * GET /api/satellites/{room}/timers                       — drawer · timers tab
 *   * GET /api/timers/fires?room_id={room}                    — drawer · timers tab · "recently fired here"
 *   * PUT /api/satellites/{room}/timer-announcements          — drawer · overview · "Only reminders for this device"
 *   * GET /api/satellites/{room}/recently-played              — drawer · recently-played tab
 *   * POST /api/music/add-by-url|query                        — drawer · recently-played "+ add" (generic acquisition)
 *   * POST /api/satellites/{room}/volume                      — drawer · overview · volume slider (master output)
 *   * POST /api/satellites/{room}/restart                     — drawer · overview · Restart satellite
 *   * POST /api/satellites/{room}/upgrade                     — drawer · overview · Upgrade satellite (code sync + self-restart)
 *   * POST /api/satellites/{room}/pairing/reset               — drawer · overview · Reset pairing (admin-gated, V002 WS auth)
 *   * POST /api/satellites/{room}/voice                       — drawer · overview · the room's voice (admin; a Pi confirms aloud)
 *   * GET  /api/config/version                                — domovoi git SHA → "needs upgrade" comparison
 *   * POST /api/satellites/{room}/dropin/start                — drawer · overview · Drop in (→ target_room)
 *   * POST /api/satellites/{room}/dropin/end                  — drawer · overview · Hang up
 *   * GET  /api/satellites/{room}/config                      — drawer · settings tab (load)
 *   * PATCH /api/satellites/{room}/config                     — drawer · settings tab (save → Pi rewrites config.toml + restarts)
 *   * GET  /api/satellites/{room}/logs                        — drawer · logs tab (lazy: fetched only when the tab is opened)
 *   * /ws/state · `satellites.presence.changed`               — refresh roster
 *   * /ws/state · `satellites.wifi.changed`                   — refresh roster
 *   * /ws/state · `satellites.dropins.changed`                — refresh roster (live drop-in pairings)
 *
 * Note: the backend's Session shape has `person_id`, not `person_name`,
 * so we don't try to render a person label in the row — the People page
 * is the right surface for that linkage. Same for ConversationTurn.
 */

/* ---- helpers ---------------------------------------------- */
// Wi-Fi, by SIGNAL since 2026-10-12: the received signal at the satellite
// (dBm) is what says whether its link is good. At or above -60 dBm good,
// -60 to -70 fair, below -70 weak (a 2.4 GHz link wants about -67 for
// steady real-time audio). Same bands as the voice answer
// (domovoi/handlers/wifi.py SIGNAL_GOOD_DBM / SIGNAL_FAIR_DBM).
const WIFI_SIGNAL_GOOD_DBM = -60;
const WIFI_SIGNAL_FAIR_DBM = -70;
const wifiSignalTone = (dbm) => dbm == null ? 'idle' : dbm >= WIFI_SIGNAL_GOOD_DBM ? 'ok' : dbm >= WIFI_SIGNAL_FAIR_DBM ? 'warn' : 'err';
const wifiSignalWord = (dbm) => ({ ok: 'good', warn: 'fair', err: 'weak', idle: '' }[wifiSignalTone(dbm)]);
// The rate is `iw`'s LAST frame, and an idle link's last frame is a beacon
// at 1 Mbit/s: by itself it is never a verdict. It colours only a
// satellite too old to report a signal, exactly as before.
const wifiRateTone = (rx) => rx == null ? 'idle' : rx < 5 ? 'err' : rx < 15 ? 'warn' : 'ok';
// The tone of a link: given the room's `wifi` object, by signal when it has
// one (and red while the radio hosts the setup hotspot), else by rate. A
// bare number is a rate (the older call form, kept for window.wifiTone).
const wifiTone = (w) => {
  if (w != null && typeof w === 'object') {
    if (w.mode === 'ap') return 'err';
    if (w.signal_dbm != null) return wifiSignalTone(w.signal_dbm);
    return wifiRateTone(w.rx_mbits);
  }
  return wifiRateTone(w);
};
// What the link reads as, main + secondary: "-52 dBm" + "72 Mbit/s" for a
// satellite that reports a signal, the rate alone for one that does not.
const wifiLinkText = (w) => {
  if (!w) return { main: '—', sub: null };
  if (w.mode === 'ap') return { main: 'setup hotspot', sub: 'not on the house Wi-Fi' };
  const rate = w.rx_mbits != null ? `${w.rx_mbits.toFixed(0)} Mbit/s` : null;
  if (w.signal_dbm != null) return { main: `${Math.round(w.signal_dbm)} dBm`, sub: rate };
  return { main: rate || '—', sub: null };
};
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

/* When a room was last heard from: its newest health sample, paired hello
 * or MPD row time (the server's last_seen_at), else the MPD row's own time
 * from a server before 2026-10-15. last_connected_at alone is NOT that:
 * it changes only when the room's MPD row is (re)allocated, which is how
 * every card of a frozen core read "offline · 19d ago" (incident 2026-10-14). */
const satLastSeen = (s) => s.last_seen_at || s.last_connected_at || null;

/* ---- Satellite card --------------------------------------- */
/* `coreDown`: the server's core isn't answering (/api/health), so nothing
 * here is live: no room can connect, and "offline" would say the
 * satellite is the problem. The card says "can't connect" and when the
 * room was last heard from instead. */
const SatCard = ({ s, onOpen, tick, coreDown = false }) => {
  const online = s.status === 'online' && !coreDown;
  const np = s.now_playing;
  const playing = np?.state === 'play' && np.song;
  const songDur = np?.song?.duration_sec ?? 0;
  const elapsed = playing ? (np.elapsed_sec ?? 0) + tick : 0;
  const progress = playing && songDur ? Math.min(100, (elapsed / songDur) * 100) : 0;
  const wTone = wifiTone(s.wifi);
  const wCol  = wifiColor(wTone);
  const wText = wifiLinkText(s.wifi);

  return (
    <button onClick={() => onOpen(s)}
            className="sat-card"
            data-online={online}
            style={{ font: 'inherit', textAlign: 'left', cursor: 'pointer',
                     background: 'var(--card)', border: '1px solid var(--border)',
                     borderRadius: 'var(--r-md)', boxShadow: 'var(--inner-highlight)',
                     padding: 0, overflow: 'hidden',
                     transition: 'transform .18s ease, box-shadow .18s ease' }}
            onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.boxShadow = 'var(--shadow-md), var(--inner-highlight)'; }}
            onMouseLeave={e => { e.currentTarget.style.transform = ''; e.currentTarget.style.boxShadow = 'var(--inner-highlight)'; }}>
      <div style={{ padding: '14px 16px 10px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <StatusDot tone={online ? 'ok' : 'idle'} live={online}/>
        <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em', flex: 1, minWidth: 0,
                      color: online ? 'var(--fg)' : 'var(--fg-muted)' }}>
          {s.room_id}
        </div>
        {satBoardLabel(s) && (
          <span className="sat-board-badge" title={`board: ${s.board}`}>
            <Icon name={s.board === 'android_room' ? 'smartphone' : 'laptop'} size={12}/> {satBoardLabel(s)}
          </span>
        )}
        {s.sat_type === 'video' && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4,
                         fontSize: 11, color: 'var(--fg-muted)', background: 'var(--sunken)',
                         border: '1px solid var(--border)', borderRadius: 'var(--r-sm)',
                         padding: '2px 7px' }}>
            <Icon name="monitor" size={12}/> video
          </span>
        )}
        {s.sat_type === 'video' && online && s.display && s.display.kiosk_alive === false && (
          <Pill tone="warn">kiosk dead</Pill>
        )}
        {online && Array.isArray(s.settings_ignored) && s.settings_ignored.length > 0 && (
          <Pill tone="warn" title={s.settings_ignored.join('\n') + '\n(it uses its board\u2019s default instead)'}>
            {s.settings_ignored.length === 1 ? 'a setting ignored' : `${s.settings_ignored.length} settings ignored`}
          </Pill>
        )}
        <span title={coreDown ? 'the server’s core isn’t answering, so no satellite can connect' : undefined}>
          <Pill tone={online ? 'live' : coreDown ? 'warn' : 'idle'} live={online}>
            {online ? 'online' : coreDown ? 'can’t connect' : s.status === 'waiting' ? 'waiting' : 'offline'}
          </Pill>
        </span>
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
        {s.capture_commands && (
          <div style={{ marginTop: 8 }}><CaptureChip/></div>
        )}
      </div>

      <div style={{ borderTop: '1px solid var(--border-soft)', padding: '10px 16px',
                    display: 'flex', alignItems: 'center', gap: 10, background: 'var(--sunken)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: wCol, minWidth: 0 }}
             title={s.wifi && s.wifi.signal_dbm != null ? `signal ${wifiSignalWord(s.wifi.signal_dbm)} (good at -60 dBm or more, weak below -70)` : undefined}>
          <Icon name="wifi" size={13}/>
          <span className="mono" style={{ fontSize: 12, color: s.wifi && s.wifi.mode === 'ap' ? wCol : 'var(--fg)' }}>
            {wText.main}
          </span>
          {wText.sub && (
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', whiteSpace: 'nowrap' }}>· {wText.sub}</span>
          )}
        </div>
        <span style={{ flex: 1 }}/>
        <span className="mono sat-card-seen" style={{ fontSize: 11, color: online ? 'var(--ok-ink)' : 'var(--fg-faint)' }}>
          {online ? 'active now' : satLastSeen(s) ? `last heard ${relTime(satLastSeen(s))}` : 'never heard'}
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

/* How a turn's mic was opened. "wake_word" is the ordinary case and would
 * just be noise on every row, so only the exceptions get a pill: a barge-in
 * (which on a board with imperfect AEC can be the satellite interrupting
 * ITSELF) and a follow-up (no wake word required, so an unexpected one
 * explains a turn nobody meant to start). Null on rows older than V011. */
const TRIGGER_PILL = {
  barge_in: { label: 'barge-in', tone: 'warn' },
  followup: { label: 'follow-up', tone: 'idle' },
  push_to_talk: { label: 'push-to-talk', tone: 'idle' },
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
        {TRIGGER_PILL[c.utterance_trigger] && (
          <Pill tone={TRIGGER_PILL[c.utterance_trigger].tone}>
            {TRIGGER_PILL[c.utterance_trigger].label}
          </Pill>
        )}
        <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', marginLeft: 'auto' }}>#{c.id}</span>
      </div>
      <div style={{ fontSize: 13, marginBottom: 4 }}>“{c.user_text}”</div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', display: 'flex', alignItems: 'flex-start', gap: 6 }}>
        <DomovoiGlyph size={12}/>
        <span>{shown}{long && (
          <button onClick={() => setMore(m => !m)}
                  style={{ font: 'inherit', fontSize: 12, marginLeft: 4, background: 'none', border: 'none',
                           color: 'var(--brand-ink)', cursor: 'pointer', padding: 0 }}>
            {more ? 'less' : 'more'}
          </button>
        )}</span>
      </div>
    </div>
  );
};

/* ---- Health card + last outage (overview tab) -------------- */
// GET /api/satellites/{room}/health: the latest `health` sample the Pi
// sent (one a minute; kept by the core across disconnects, so an offline
// room still shows its last minute), the record its previous process life
// left behind (`prev_health` in its hello), and a 24 h summary. Fetched on
// mount and re-read every minute while the drawer is open.
const satHealthAgeTone = (sec) => sec == null ? 'idle' : sec < 150 ? 'ok' : sec < 900 ? 'warn' : 'err';
const satHealthTempTone = (c) => c == null ? 'idle' : c < 70 ? 'ok' : c < 80 ? 'warn' : 'err';
const satHealthThrottle = (flags) => (flags || []).filter(f => !f.endsWith('_occurred'));
const satHealthMb = (kb) => kb == null ? '—' : `${(kb / 1024).toFixed(0)} MB`;
// A spare device's battery, `{percent, charging, on_power}` (laptops and
// phones send the same object); null on a device without one (every Pi).
// onCharger: true / false / null.
const satBattery = (s) => {
  const b = s && s.battery;
  if (!b) return null;
  const onCharger = b.charging ? true : b.on_power == null ? null : !!b.on_power;
  return { percent: b.percent == null ? null : b.percent, onCharger, charging: !!b.charging };
};
const satBatteryView = (b) => {
  const pct = b.percent;
  const value = pct == null ? '—' : `${Math.round(pct)}%`;
  const plugged = b.charging ? 'charging' : 'on the charger';
  const sub = b.onCharger === true
    ? (pct != null && pct >= 98 ? `${plugged} · set a charge limit if it stays here for weeks` : plugged)
    : b.onCharger === false ? 'on battery' : 'power unknown';
  const tone = pct == null ? 'idle'
    : b.onCharger === false ? (pct < 20 ? 'err' : 'warn')
    : (b.onCharger === true && pct >= 98) ? 'warn' : 'ok';
  return { value, sub, tone };
};
const satHealthUptime = (sec) => {
  if (sec == null) return '—';
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
};
const satHealthWhen = (iso) => iso ? `${relTime(iso)} · ${iso.replace('T', ' ').slice(0, 16)} UTC` : '—';

const SatHealthStat = ({ label, value, sub, tone }) => (
  <div style={{ minWidth: 0 }}>
    <div className="label">{label}</div>
    <div className="mono" style={{ fontSize: 15, fontWeight: 600, marginTop: 2, color: tone ? wifiColor(tone) : 'inherit' }}>{value}</div>
    {sub && <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-muted)', marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{sub}</div>}
  </div>
);

const SatLastOutage = ({ outage }) => {
  if (!outage || !outage.report) return null;
  const r = outage.report;
  const diag = Array.isArray(r.offline_diag) ? r.offline_diag.slice(-8) : [];
  const actions = Array.isArray(r.actions) ? r.actions.slice(-3) : [];
  const quiet = typeof r.last_exit_reason === 'string' && r.last_exit_reason.startsWith('shutdown:');
  return (
    <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <div className="label">last outage</div>
        <Pill tone={quiet ? 'idle' : 'warn'}>{quiet ? 'restart on request' : 'the previous life died'}</Pill>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>reported {satHealthWhen(outage.reported_at)}</span>
      </div>
      <div style={{ fontSize: 12, marginTop: 6 }}>
        <span style={{ color: 'var(--fg-muted)' }}>exit reason: </span>
        <span className="mono">{r.last_exit_reason || 'unknown (no reason was written — a power cut, an OOM kill, or a crash the process never saw)'}</span>
      </div>
      {actions.length > 0 && (
        <div style={{ fontSize: 11.5, marginTop: 4, color: 'var(--fg-muted)' }}>
          self-checks: {actions.map((a, i) => <span key={i} className="mono">{i ? ' · ' : ''}{a.action}: {a.reason}</span>)}
        </div>
      )}
      {diag.length > 0 && (
        <table className="tbl" style={{ marginTop: 8, fontSize: 11 }}>
          <thead><tr><th>when</th><th>failure</th><th>gateway</th><th>core</th><th>link</th><th>action</th></tr></thead>
          <tbody>
            {diag.map((e, i) => (
              <tr key={i}>
                <td className="mono">{e.ts ? new Date(e.ts * 1000).toISOString().replace('T', ' ').slice(5, 16) : '—'}</td>
                <td className="mono">{e.failure || '—'}</td>
                <td className="mono">{e.gateway_ok == null ? '?' : e.gateway_ok ? 'answers' : 'silent'}</td>
                <td className="mono">{e.core_ok == null ? '?' : e.core_ok ? 'up' : 'down'}</td>
                <td className="mono">{e.nm_state && e.nm_state.state ? e.nm_state.state : (e.wifi ? wifiLinkText(e.wifi).main : '—')}</td>
                <td className="mono">{e.action || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {r.truncated && <div style={{ fontSize: 10.5, color: 'var(--fg-faint)', marginTop: 4 }}>the record was larger than the core keeps; the oldest entries were dropped</div>}
    </div>
  );
};

// What root's boot-time network step did on this boot (satellite/net_settings.py
// `assess`: the setup hotspot profile gone, Wi-Fi power saving off, the host
// name domovoi-<room> set before the Wi-Fi joins), with the reason, and the
// host name the router should be showing. A satellite older than this sends
// neither and the card reads exactly as before.
const SAT_NET_STEP = {
  applied: { tone: 'ok', word: 'applied' },
  unchanged: { tone: 'ok', word: 'nothing to change' },
  skipped: { tone: 'warn', word: 'skipped' },
  failed: { tone: 'err', word: 'failed' },
  not_run: { tone: 'warn', word: 'not run' },
  not_installed: { tone: 'idle', word: 'not installed' },
  running: { tone: 'idle', word: 'running' },
};
const SatNetStep = ({ step, hostname }) => {
  const m = (step && SAT_NET_STEP[step.state]) || null;
  return (
    <div className="sat-net-step" style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginTop: 10, fontSize: 12 }}>
      <div className="label">{m ? 'boot network step' : 'network'}</div>
      {m && <Pill tone={m.tone}>{m.word}</Pill>}
      {hostname && <span className="mono" style={{ fontSize: 11.5 }}>host name {hostname}</span>}
      {m && step.detail && <span style={{ color: 'var(--fg-muted)', flexBasis: '100%', overflowWrap: 'anywhere' }}>{step.detail}</span>}
      {step && step.early && <span className="sat-net-step-early" style={{ color: 'var(--fg-muted)', flexBasis: '100%', overflowWrap: 'anywhere' }}>{step.early}</span>}
    </div>
  );
};

/* A computer set up from a room stick (2026-10-16, domovoi/usb_stick/):
 * what its firmware said about Secure Boot at this boot (root's boot-check
 * on the computer writes it; the satellite reports it). The stick freezes
 * the boot loader it installed (owner decision 2026-10-15), so no update
 * can swap in a shim this firmware won't start. When Secure Boot is on and
 * the firmware lacks Microsoft's 2023 certificate the card says so plainly,
 * with how to update the firmware by hand. Any other satellite sends no
 * `boot` and shows nothing here. */
const satNeeds2023 = (b) => !!(b && b.firmware === 'uefi' && b.secure_boot === true && b.ca_2023 === false);
const SatBootSecurity = ({ boot }) => {
  if (!boot || !boot.stick) return null;
  const uefi = boot.firmware === 'uefi';
  const words = [
    uefi ? 'UEFI' : 'BIOS',
    uefi ? (boot.secure_boot === true ? 'Secure Boot on' : boot.secure_boot === false ? 'Secure Boot off' : 'no Secure Boot setting') : null,
    uefi && boot.ca_2023 != null ? (boot.ca_2023 ? 'firmware has the 2023 certificate' : 'no 2023 certificate') : null,
    uefi && boot.loader_held ? 'boot loader frozen' : null,
  ].filter(Boolean).join(' · ');
  return (
    <div className="sat-boot-security" style={{ marginTop: 10, fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div className="label">boot</div>
        <span className="mono" style={{ fontSize: 11.5 }}>{words}</span>
      </div>
      {uefi && boot.secure_boot == null && (
        <div className="sat-boot-nosb" style={{ color: 'var(--fg-muted)', lineHeight: 1.5, marginTop: 4 }}>
          This computer's firmware doesn't say whether Secure Boot is on (older firmware often has no
          Secure Boot at all). Nothing to do: the room starts the same either way.
        </div>
      )}
      {satNeeds2023(boot) && (
        <div role="note" className="sat-boot-2023" style={{ color: 'var(--warn-ink)', lineHeight: 1.5, marginTop: 4 }}>
          This computer's firmware doesn't have Microsoft's 2023 Secure Boot certificate. It keeps
          starting as it is: its boot loader is frozen at the one the USB stick installed. Once Debian
          ships a boot loader signed with the 2023 certificate only, a stick made after that will stop
          starting on it while Secure Boot is on. To fix it by hand: install the maker's latest BIOS/UEFI
          update for this model from their support site (many have a bootable USB updater), or turn
          Secure Boot off in its firmware settings.
        </div>
      )}
    </div>
  );
};

const SatHealthCard = ({ room, online }) => {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await apiGet(`/api/satellites/${room}/health`);
        if (alive) { setData(d); setError(null); }
      } catch (e) {
        if (alive) setError(e.message || String(e));
      }
    })();
    const timer = setInterval(() => setTick(t => t + 1), 60_000);
    return () => { alive = false; clearInterval(timer); };
  }, [room, tick]);

  const latest = data && data.latest;
  const s = latest && latest.sample;
  const ageSec = latest && latest.received_at ? Math.max(0, (Date.now() - Date.parse(latest.received_at)) / 1000) : null;
  const hist = data && data.history && data.history.samples ? data.history : null;
  const throttle = s ? satHealthThrottle(s.throttled_flags) : [];
  return (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <div className="label">health</div>
        {s && <Pill tone={satHealthAgeTone(ageSec)}>{ageSec == null ? 'sample' : ageSec < 90 ? 'live' : `last sample ${relTime(latest.received_at)}`}</Pill>}
        {s && !online && <Pill tone="idle">kept from before it went offline</Pill>}
        {s && s.reboot_needed &&
          <Pill tone="warn" title={s.reboot_needed}>reboot needed</Pill>}
        {s && Array.isArray(s.settings_ignored) && s.settings_ignored.length > 0 &&
          <Pill tone="warn" title={s.settings_ignored.join('\n')}>settings ignored</Pill>}
        {s && s.wifi && s.wifi.mode === 'ap' &&
          <Pill tone="err" title="the radio is hosting the setup hotspot instead of being on the house Wi-Fi; the satellite is bringing the house network back">setup hotspot on</Pill>}
        {s && satNeeds2023(s.boot) &&
          <Pill tone="warn" title="Secure Boot is on and the firmware lacks Microsoft's 2023 certificate (below)">firmware needs the 2023 certificate</Pill>}
        {s && s.mic_blocked === true &&
          <Pill tone="err" title="the device's microphone is switched off in its own settings (a phone's mic privacy toggle): it hears only silence">mic blocked</Pill>}
        {throttle.length > 0 && <Pill tone="err">{throttle.join(', ').replace(/_/g, ' ')}</Pill>}
        {s && s.throttled_flags && s.throttled_flags.length > throttle.length && throttle.length === 0 &&
          <Pill tone="warn" title={s.throttled_flags.join(', ')}>throttled earlier this boot</Pill>}
        {hist && <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-faint)' }}>{hist.samples} samples in 24 h</span>}
      </div>
      {s && Array.isArray(s.settings_ignored) && s.settings_ignored.length > 0 && (
        <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6 }}>
          {s.settings_ignored.join('; ')}. It uses its board’s default instead; a new value from the Settings tab replaces the stored one.
        </div>
      )}
      {s && s.reboot_needed && (
        <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6 }}>
          {s.net_step && s.net_step.state === 'failed'
            ? 'Its network settings did not all apply at this boot (below); a reboot tries again.'
            : `Upgraded, but not rebooted yet: its network settings (no setup hotspot, Wi-Fi power saving off, host name domovoi-${room} set before the Wi-Fi joins) are applied at boot.`}
          {' '}Unplug it for a few seconds and plug it back in.
        </div>
      )}
      {error && !s && <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6 }}>couldn’t read health: {error}</div>}
      {!error && !s && data && (
        <div style={{ fontSize: 12, color: 'var(--fg-faint)', marginTop: 6 }}>
          no health sample yet — the satellite sends one a minute once it runs code from 2026-10-09 on (upgrade it from this tab)
        </div>
      )}
      {!data && !error && <div style={{ fontSize: 12, color: 'var(--fg-faint)', marginTop: 6 }}>loading…</div>}
      {s && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px 12px', marginTop: 8 }}>
          <SatHealthStat label="memory" value={satHealthMb(s.rss_kb)}
                         sub={`peak ${satHealthMb(s.hwm_kb)}${hist && hist.rss_kb && hist.rss_kb.max != null ? ` · 24 h max ${satHealthMb(hist.rss_kb.max)}` : ''}`}
                         tone={s.rss_kb != null ? (s.rss_kb > 250 * 1024 ? 'err' : s.rss_kb > 180 * 1024 ? 'warn' : 'ok') : 'idle'}/>
          <SatHealthStat label="cpu" value={s.cpu_pct == null ? '—' : `${s.cpu_pct.toFixed(0)}%`}
                         sub={`system ${s.sys_cpu_pct == null ? '—' : s.sys_cpu_pct.toFixed(0) + '%'} · load ${s.load1 == null ? '—' : s.load1.toFixed(2)}`}/>
          <SatHealthStat label="temperature" value={s.soc_temp_c == null ? '—' : `${s.soc_temp_c.toFixed(1)}°C`}
                         sub={s.throttled == null ? 'no throttle data' : s.throttled === 0 ? 'never throttled this boot' : `throttled 0x${s.throttled.toString(16)}`}
                         tone={satHealthTempTone(s.soc_temp_c)}/>
          <SatHealthStat label="uptime" value={satHealthUptime(s.uptime_s)}
                         sub={`process ${satHealthUptime(s.proc_uptime_s)} · ${s.reconnects == null ? '—' : s.reconnects} reconnects`}/>
          <SatHealthStat label="wi-fi" value={wifiLinkText(s.wifi).main}
                         sub={[s.wifi && s.wifi.signal_dbm != null ? wifiLinkText(s.wifi).sub : null,
                               s.nm_state && s.nm_state.state ? `NetworkManager: ${s.nm_state.state}` : (s.wifi && s.wifi.ssid ? s.wifi.ssid : 'no link data')]
                               .filter(Boolean).join(' · ')}
                         tone={s.wifi ? wifiTone(s.wifi) : 'idle'}/>
          <SatHealthStat label="microphone" value={s.mic_expected === false ? 'off' : s.mic_fps == null ? '—' : `${s.mic_fps.toFixed(1)} fps`}
                         sub={s.wake_loop_age_s == null ? 'wake loop not armed' : `wake loop ${s.wake_loop_age_s.toFixed(1)}s ago · queue ${s.mic_q == null ? '—' : s.mic_q}`}
                         tone={s.mic_expected === false ? 'idle' : s.mic_fps == null ? 'idle' : s.mic_fps > 20 ? 'ok' : s.mic_fps > 0 ? 'warn' : 'err'}/>
          <SatHealthStat label="reachability" value={s.gateway_ok == null ? '—' : s.gateway_ok ? 'gateway answers' : 'gateway silent'}
                         sub={`core ${s.core_ok == null ? '?' : s.core_ok ? 'reachable' : 'unreachable'} · ws ${s.ws || '—'}${s.ws_down_for_s ? ` for ${satHealthUptime(s.ws_down_for_s)}` : ''}`}
                         tone={s.gateway_ok == null ? 'idle' : s.gateway_ok ? 'ok' : 'err'}/>
          <SatHealthStat label="handles" value={s.fds == null ? '—' : `${s.fds} fds`}
                         sub={`${s.threads == null ? '—' : s.threads} threads · ${s.mem_avail_kb == null ? '—' : satHealthMb(s.mem_avail_kb)} free`}
                         tone={s.fds == null ? 'idle' : s.fds > 400 ? 'warn' : 'ok'}/>
          {satBattery(s) && (() => {
            const v = satBatteryView(satBattery(s));
            return <SatHealthStat label="battery" value={v.value} sub={v.sub} tone={v.tone}/>;
          })()}
        </div>
      )}
      {/* A room made from a USB stick has no Pi's boot-time network step:
          its "not installed" (and the Pi's command to run it) would only
          mislead. Its host name still shows. */}
      {s && (s.net_step || s.hostname) && (
        <SatNetStep step={s.boot && s.boot.stick && s.net_step && s.net_step.state === 'not_installed' ? null : s.net_step}
                    hostname={s.hostname}/>
      )}
      {s && s.boot && <SatBootSecurity boot={s.boot}/>}
      {s && (
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-faint)', marginTop: 8 }}>
          sampled {satHealthWhen(latest.received_at)}
        </div>
      )}
      <SatLastOutage outage={data && data.last_outage}/>
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
      reportMutationFailure(fire, 'volume', e);
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
/* ---- Video-satellite display controls (type-gated drawer block) ----
 * Rendered only for sat_type === 'video'. Screen on/off + kiosk restart
 * proxy through /api/satellites/{room}/display → the core's set_display
 * frame; state pills come from the display_status the device reports.
 * Brightness is read-only in v1 (the frame has no brightness action). */
const VideoControls = ({ s, fire }) => {
  const d = s.display || {};
  const online = s.status === 'online';
  const sendDisplay = async (action) => {
    try {
      await apiPost(`/api/satellites/${s.room_id}/display`, { action });
      fire(action === 'restart_kiosk'
        ? `restarting kiosk on ${s.room_id}…`
        : `screen ${action} sent to ${s.room_id}`);
    } catch (e) {
      reportMutationFailure(fire, 'display action', e);
    }
  };
  const restartKiosk = () => {
    if (!window.confirm(
      `Restart the kiosk browser on ${s.room_id}?\n\n` +
      `The screen goes blank for a few seconds while it relaunches.`
    )) return;
    sendDisplay('restart_kiosk');
  };
  return (
    <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
      <div className="label" style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
        display
        {online && d.kiosk_alive === true && <Pill tone="live" live>kiosk running</Pill>}
        {online && d.kiosk_alive === false && <Pill tone="warn">kiosk dead</Pill>}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <Button icon={d.on === false ? 'monitor' : 'monitor-off'} disabled={!online}
                onClick={() => sendDisplay(d.on === false ? 'on' : 'off')}>
          {d.on === false ? 'Screen on' : 'Screen off'}
        </Button>
        <Button icon="rotate-cw" disabled={!online} onClick={restartKiosk}>
          Restart kiosk
        </Button>
        {d.brightness != null && (
          <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
            backlight {d.brightness}%
          </span>
        )}
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 6 }}>
        idle screen: {d.idle_mode || 'clock'} — change it (and the power mechanism) in the Settings tab
      </div>
    </div>
  );
};

/* ---- Command recordings for tuning (opt-in per room) -------
 * Off for every room until an admin turns it on here. While on, each
 * wake-word or follow-up command said to this satellite is kept on the
 * Domovoi server (audio + transcript) for tuning when Domovoi stops
 * listening; Settings → Voice history plays and labels them. Turning it off
 * deletes what was kept, so both directions confirm first. The server's
 * security tier decides every call; the admin check here only picks
 * which controls to show. */
const SatCaptureControl = ({ s, fire, refresh }) => {
  const admin = useAdminSignedIn();
  const [busy, setBusy] = React.useState(false);
  // The kept-count read is Bearer-only (the cookie alone is refused since
  // 2026-10-14): asked only with the admin sign-in in this tab.
  const live = adminReloadState().live;
  const path = live ? `/api/captures?room_id=${encodeURIComponent(s.room_id)}` : null;
  const { data, refresh: refreshKept } = useApiObject(path, { quiet: true });
  // What the last toggle here set, shown until the roster (a slower read:
  // it asks every room's player) catches up — so the block never says
  // "on" right after it was turned off.
  const [justSet, setJustSet] = React.useState(null);
  React.useEffect(() => { setJustSet(null); }, [s.room_id, s.capture_commands]);
  const on = justSet !== null ? justSet : !!s.capture_commands;
  const room = data && (data.rooms || []).find((r) => r.room_id === s.room_id);
  const kept = room ? room.count : 0;
  const days = (data && data.retention_days) || 14;
  const plural = (n) => `${n} recording${n === 1 ? '' : 's'}`;

  const turnOn = async () => {
    if (!window.confirm(
      `Record commands in ${s.room_id}?\n\n` +
      `Each command said to this satellite (after the wake word, or answering a ` +
      `follow-up question) is kept on the Domovoi server for ${days} days, the audio ` +
      `and what it heard, so admins can tune when Domovoi stops listening. ` +
      `Wake-word training, drop-in calls and chat are never kept.\n\n` +
      `Let the people who use this room know.`
    )) return;
    setBusy(true);
    try {
      await apiFetch(`/api/captures/rooms/${encodeURIComponent(s.room_id)}`, { method: 'PUT' });
      setJustSet(true);
      fire(`recording commands in ${s.room_id}`);
      refresh && refresh();
      refreshKept();
    } catch (e) {
      reportMutationFailure(fire, 'turn on recording', e);
    } finally { setBusy(false); }
  };

  const turnOff = async () => {
    if (!window.confirm(
      `Stop recording in ${s.room_id}?\n\n` +
      (kept ? `The ${plural(kept)} kept so far will be deleted now. This can't be undone.`
            : 'Nothing kept here yet, so nothing to delete.')
    )) return;
    setBusy(true);
    try {
      const r = await apiDelete(`/api/captures/rooms/${encodeURIComponent(s.room_id)}`);
      const gone = (r && r.deleted) || 0;
      setJustSet(false);
      fire(`stopped recording in ${s.room_id}${gone ? ` · ${plural(gone)} deleted` : ''}`);
      refresh && refresh();
      refreshKept();
    } catch (e) {
      reportMutationFailure(fire, 'stop recording', e);
    } finally { setBusy(false); }
  };

  return (
    <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
      <div className="label" style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
        command recordings
        {on && <CaptureChip/>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 180px', fontSize: 12, color: on ? 'var(--fg)' : 'var(--fg-muted)' }}>
          {on
            ? <>on since <span className="mono">{s.capture_since ? relTime(s.capture_since) : 'just now'}</span>
                {admin && room && (
                  <span style={{ color: 'var(--fg-muted)' }}>
                    {' · '}{kept ? `${plural(kept)} · ${fmtBytes(room.bytes)}` : 'nothing kept yet'}
                  </span>
                )}</>
            : 'off · nothing said here is kept'}
        </div>
        {admin && (on ? (
          <Button icon="mic-off" disabled={busy} onClick={turnOff}>Stop and delete</Button>
        ) : (
          <Button icon="mic" disabled={busy} onClick={turnOn}>Record commands</Button>
        ))}
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 6 }}>
        {admin
          ? `for tuning when domovoi stops listening · kept ${days} days · review in settings → recordings`
          : 'admins turn this on or off · sign in to change it'}
      </div>
    </div>
  );
};

/* "Only reminders for this device" — which timers and reminders this
 * satellite announces. Off (the default): every room's, each named by the
 * room it was set in ("From the garage: your 10 minute timer is done").
 * On: only the ones set on this satellite. The room a timer was set in
 * always announces it. A household setting (device tier, like volume):
 * no admin check, and it works while the room is offline — it is a row
 * in the database the core reads each time something goes off. */
const SatTimerScopeControl = ({ s, fire, refresh }) => {
  const [busy, setBusy] = React.useState(false);
  // What the last change here set, until the roster (a slower read) agrees.
  const [justSet, setJustSet] = React.useState(null);
  React.useEffect(() => { setJustSet(null); }, [s.room_id, s.timers_own_only]);
  const on = justSet !== null ? justSet : !!s.timers_own_only;

  const onChange = async (e) => {
    if (busy) return;
    const next = !!(e && e.target && e.target.checked);
    setBusy(true);
    try {
      await apiFetch(`/api/satellites/${encodeURIComponent(s.room_id)}/timer-announcements`, {
        method: 'PUT', body: JSON.stringify({ own_only: next }),
      });
      setJustSet(next);
      fire(next
        ? `${s.room_id} now announces only its own timers and reminders`
        : `${s.room_id} now announces timers and reminders from every room`);
      refresh && refresh();
    } catch (err) {
      reportMutationFailure(fire, 'change timer announcements', err);
    } finally { setBusy(false); }
  };

  return (
    <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
      <div className="label" style={{ marginBottom: 6 }}>timers &amp; reminders</div>
      {/* .sat-timer-scope: a 44px target on a phone (styles.css). */}
      <label className="sat-timer-scope" style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13,
                      cursor: busy ? 'default' : 'pointer' }}>
        <input type="checkbox" checked={on} disabled={busy} onChange={onChange}/>
        Only reminders for this device
      </label>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 6 }}>
        Covers timers and reminders. Off: this satellite also announces the ones set in other rooms and says which room they came from. On: it announces only the ones set on this satellite. The room a timer or reminder was set in always announces it.
      </div>
    </div>
  );
};

/* ---- A satellite upgrade under way -------------------------------
 * The Domovoi server keeps a record per room from an upgrade request until
 * the satellite says hello again (domovoi/satellite_upgrades.py, the list
 * row's `upgrade`): `running`, then `done` (back on the new code),
 * `rolled_back` (back on its old code: the new code didn't stay up) or
 * `no_news` (never came back with an answer; a sync that fails before the
 * restart leaves it online on its old code without a word). The Upgrade
 * button locks on `running` the way Settings → About & updates locks on
 * restart_in_progress: across a reload and in every tab, and the server
 * refuses a second request meanwhile (409).
 *
 * satUpgradePresses holds THIS page's presses, room → { at, expected,
 * serverStartedMs }, kept while the drawer is closed and opened again: the
 * button locks the moment the server says yes, before any list carries the
 * record. A record started at or after the press's own is the press's. A
 * server that keeps no records (an older core) ends the press when the
 * satellite reports the expected version, or after SAT_UPGRADE_WAIT_MS. */
const satUpgradePresses = new Map();
const SAT_UPGRADE_WAIT_MS = 6 * 60 * 1000;
const SAT_UPGRADE_FINISHED = ['done', 'rolled_back', 'no_news'];

const satUpgradeView = (s, press, now = Date.now()) => {
  const rec = s && s.upgrade && typeof s.upgrade === 'object' && typeof s.upgrade.state === 'string'
    ? s.upgrade : null;
  const recMs = rec && rec.started_at ? Date.parse(rec.started_at) : NaN;
  const of = (state, r, expected, startedMs) => ({ state, running: state === 'running', rec: r, expected, startedMs });
  if (press) {
    const ours = !!rec && Number.isFinite(recMs) && press.serverStartedMs != null
      && recMs >= press.serverStartedMs - 1000;
    if (ours) return of(rec.state, rec, rec.expected_sha || press.expected, recMs);
    if (press.outcome) {
      return rec && rec.state === 'running'
        ? of('running', rec, rec.expected_sha, Number.isFinite(recMs) ? recMs : null)
        : of(press.outcome, null, press.expected, press.at);
    }
    // No record to wait for (a server that keeps none): the satellite's own
    // version says it.
    if (press.serverStartedMs == null && press.expected
        && s.status === 'online' && s.version === press.expected) {
      return of('done', null, press.expected, press.at);
    }
    // Never a word, and no record (or one long gone): stop waiting.
    if (now - press.at > SAT_UPGRADE_WAIT_MS) return of('no_news', null, press.expected, press.at);
    return of('running', null, press.expected, press.at);
  }
  if (rec) return of(rec.state, rec, rec.expected_sha, Number.isFinite(recMs) ? recMs : null);
  return of(null, null, null, null);
};

// "1:05"
const satUpgradeClock = (ms) => {
  const sec = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
};

// How an upgrade ended, in a sentence (the toast) or a line (the drawer).
const satUpgradeOutcome = (room, v, { line = false } = {}) => {
  const to = v.expected || 'the new code';
  if (v.state === 'done') return line ? `upgraded to ${to}` : `${room} upgraded — now on ${to}`;
  const back = v.rec && v.rec.back_sha;
  if (v.state === 'rolled_back') {
    return `${line ? 'came back' : `${room} came back`} on its old code${back ? ` (${back})` : ''}: the new code didn’t stay up, so it rolled back — its Logs tab says why`;
  }
  if (v.state === 'no_news') {
    return `${line ? 'didn’t' : `${room} didn’t`} come back on ${to} in time — if it still shows its old version, the sync failed before the restart; its Logs tab says why`;
  }
  return '';
};

/* ---- The room's voice (drawer · overview) ------------------------ */
/* What "switch to Norman's voice" said in the room does, from here
 * (POST /api/satellites/{room}/voice, admin): a Pi is sent the change, says
 * the confirmation in the new voice and reports it back (online only: it
 * keeps the choice itself); a phone in room mode has the server keep it
 * (`voice_on_server`), offline too, and may go back to the server's own
 * voice. A voice that isn't downloaded is listed, marked as Settings →
 * Voices marks it, and can't be picked: the room would speak in the
 * fallback voice under its name. */
const RoomVoicePicker = ({ s, fire, refresh }) => {
  const admin = useAdminSignedIn();
  // refetchOnAuth: a list read before the sign-in landed came back empty.
  const { items: registry } = useApiList('/api/voices', { quiet: true, refetchOnAuth: true });
  const { data: shipped } = useApiObject('/api/voice', { quiet: true, refetchOnAuth: true });
  const { data: sampleInfo } = useApiObject('/api/voice/samples', { quiet: true, refetchOnAuth: true });
  const [busy, setBusy] = React.useState(false);

  const onServer = !!s.voice_on_server;
  const online = s.status === 'online';
  const changeable = onServer || (online && satCan(s, 'voice'));
  const rows = Array.isArray(registry) ? registry : [];
  const defaultName = (rows.find(v => v.is_default) || {}).name;
  const options = rows.map(v => ({
    name: v.name,
    ready: v.sample === 'voice' && !v.problem,
    why: v.problem ? 'can’t speak here' : 'not downloaded yet',
  }));
  // The voices Domovoi ships that the registry hasn't got yet.
  for (const v of (shipped && Array.isArray(shipped.voices) ? shipped.voices : [])) {
    if (rows.some(r => r.model_ref === v.id) || options.some(o => o.name === v.name)) continue;
    options.push({ name: v.name, ready: sampleInfo?.samples?.[v.id] === 'voice', why: 'not downloaded yet' });
  }
  const current = s.voice || '';
  if (current && !options.some(o => o.name === current)) options.push({ name: current, ready: false, why: 'not in the registry' });
  const serverLabel = `the server’s voice${defaultName ? ` (${defaultName})` : ''}`;

  const label = (
    <span className="mono" style={{ color: s.voice ? 'var(--fg)' : 'var(--fg-faint)' }}>{s.voice || serverLabel}</span>
  );
  // No registry (not loaded, or unreadable): nothing to offer honestly.
  if (!changeable || !admin || rows.length === 0) {
    const why = !admin ? 'sign in as an admin to change it here, or say “switch to <name>’s voice” in the room'
      : !changeable ? 'its satellite keeps its own voice: change it while the room is online'
      : 'say “switch to <name>’s voice” in the room to change it';
    return <div title={why}>{label}</div>;
  }

  const pick = async (value) => {
    if (value === current) return;
    setBusy(true);
    try {
      const res = await apiPost(`/api/satellites/${s.room_id}/voice`, { voice: value || null });
      const spoken = res?.voice || serverLabel;
      fire(res?.kept_on === 'device'
        ? `${s.room_id} switches to ${spoken}: it says so in the new voice`
        : `${s.room_id} now speaks as ${spoken}${online ? '' : ' (from its next connection)'}`);
      if (refresh) refresh();
    } catch (e) {
      reportMutationFailure(fire, 'change the voice', e);
    } finally { setBusy(false); }
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
      <select aria-label={`${s.room_id}’s voice`} value={current} disabled={busy}
              onChange={e => pick(e.target.value)}
              title={onServer ? 'kept on the server for this room: it applies offline too'
                              : 'the satellite keeps it: it says the change in the new voice'}
              style={{ flex: 1, minWidth: 0, maxWidth: 260, font: 'inherit', fontSize: 12, height: 28, padding: '0 6px',
                       borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                       background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}>
        {(onServer || !current) && <option value="" disabled={!onServer}>{serverLabel}</option>}
        {options.map(o => (
          <option key={o.name} value={o.name} disabled={!o.ready && o.name !== current}>
            {o.ready ? o.name : `${o.name} · ${o.why}`}
          </option>
        ))}
      </select>
      {busy && <Icon name="loader" size={14}/>}
    </div>
  );
};

const OverviewBody = ({ s, sats, fire, onClose, refresh }) => {
  const waiting = s.status === 'waiting';
  const [msg, setMsg] = React.useState('');
  const [peer, setPeer] = React.useState('');
  const wTone = wifiTone(s.wifi);
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
  // What this device declared (S0). A Pi sends no list: all true, as before.
  const canRestart = satCan(s, 'restart');
  const canUpgrade = satCan(s, 'code_sync');
  const canAnnounce = satCan(s, 'announce_after_session_end');
  const notOffered = satBoardLabel(s) ? `this ${satBoardLabel(s)}` : 'this device';

  // The upgrade under way, if any (satUpgradeView above): this page's
  // press, or the server's record — what a reload or another tab sees.
  const [press, setPress] = React.useState(() => satUpgradePresses.get(s.room_id) || null);
  React.useEffect(() => { setPress(satUpgradePresses.get(s.room_id) || null); }, [s.room_id]);
  // The press in flight: the confirm said yes, the server hasn't answered
  // (a sign-in prompt may stand in between).
  const [asking, setAsking] = React.useState(false);
  const [upgradeErr, setUpgradeErr] = React.useState(null);
  const [nowMs, setNowMs] = React.useState(() => Date.now());
  const upView = satUpgradeView(s, press, nowMs);
  const upgrading = upView.running;
  // While it runs: the clock, and a fresh list every few seconds — the
  // satellite drops off and comes back, and that answer ends the wait.
  const refreshRef = React.useRef(refresh);
  refreshRef.current = refresh;
  React.useEffect(() => {
    if (!upgrading) return undefined;
    const tick = setInterval(() => setNowMs(Date.now()), 1000);
    const poll = setInterval(() => {
      try { if (refreshRef.current) refreshRef.current(); } catch { /* the next tick */ }
    }, 3000);
    return () => { clearInterval(tick); clearInterval(poll); };
  }, [upgrading]);
  // The wait is over: say how it went, once, and forget the press (the
  // server's record, when there is one, keeps saying it for a while).
  const lastUpState = React.useRef(upView.state);
  React.useEffect(() => {
    const was = lastUpState.current;
    lastUpState.current = upView.state;
    if (was !== 'running' || !SAT_UPGRADE_FINISHED.includes(upView.state)) return;
    fire(satUpgradeOutcome(s.room_id, upView));
    if (!press) return;
    if (upView.rec) {
      satUpgradePresses.delete(s.room_id);
      setPress(null);
    } else {
      // No record says it (an older server): the press keeps the outcome.
      const settled = { ...press, outcome: upView.state };
      satUpgradePresses.set(s.room_id, settled);
      setPress(settled);
    }
  }, [upView.state]);

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
      reportMutationFailure(fire, 'announce', e);
    }
  };

  const restartSat = async () => {
    if (upgrading) { fire(`${s.room_id} is upgrading — it restarts itself when the new code is in`); return; }
    if (s.status !== 'online') { fire(`${s.room_id} is offline`); return; }
    if (!window.confirm(`Restart the ${s.room_id} satellite? It'll drop offline for a few seconds while the service bounces.`)) return;
    try {
      await apiPost(`/api/satellites/${s.room_id}/restart`, {});
      fire(`restarting ${s.room_id}…`);
    } catch (e) {
      reportMutationFailure(fire, 'restart', e);
    }
  };

  const upgradeSat = async () => {
    if (upgrading || asking) return;
    if (s.status !== 'online') { fire(`${s.room_id} is offline`); return; }
    if (!window.confirm(
      `Upgrade the ${s.room_id} satellite to ${coreSha || 'the latest domovoi code'}?\n\n` +
      `It pulls the new satellite code from the Domovoi server, backs up its current ` +
      `tree, and restarts — dropping offline for a few seconds. If the new code won't ` +
      `start, it rolls back automatically.\n\n` +
      `Some updates finish at the next boot: if its health card then says ` +
      `"reboot needed", unplug it for a few seconds and plug it back in.`
    )) return;
    setUpgradeErr(null);
    setAsking(true);
    try {
      const res = await apiPost(`/api/satellites/${s.room_id}/upgrade`, {});
      // Locked from here until the satellite is back (satUpgradeView).
      const rec = res && res.upgrade;
      const startedMs = rec && rec.started_at ? Date.parse(rec.started_at) : NaN;
      const p = { at: Date.now(), expected: (res && res.expected_sha) || coreSha || null,
                  serverStartedMs: Number.isFinite(startedMs) ? startedMs : null };
      satUpgradePresses.set(s.room_id, p);
      setPress(p);
      setNowMs(Date.now());
      fire(`upgrading ${s.room_id}…`);
      try { refresh && refresh(); } catch { /* the poll catches up */ }
    } catch (e) {
      // Refused, failed or cancelled: the button stays offered, and says why.
      const msg = reportMutationFailure(fire, 'upgrade', e);
      if (msg) setUpgradeErr(msg);
      // 409: one is already under way (another tab, the app): the list says so.
      if (e && e.status === 409) { try { refresh && refresh(); } catch { /* next poll */ } }
    } finally {
      setAsking(false);
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
      reportMutationFailure(fire, 'reset pairing', e);
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
      reportMutationFailure(fire, 'drop-in', e);
    }
  };
  const hangUp = async () => {
    try {
      await apiPost(`/api/satellites/${s.room_id}/dropin/end`, {});
      fire(`hung up ${s.room_id}`);
    } catch (e) {
      reportMutationFailure(fire, 'hang up', e);
    }
  };

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--border-soft)' }}>
        <div style={{ padding: '14px 16px', borderRight: '1px solid var(--border-soft)' }}>
          <div className="label">last heard from</div>
          <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{s.status === 'online' ? 'active now' : satLastSeen(s) ? relTime(satLastSeen(s)) : 'never'}</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
            {satLastSeen(s) ? new Date(satLastSeen(s)).toISOString().replace('T',' ').slice(0,16) + ' UTC' : '—'}
          </div>
        </div>
        <div style={{ padding: '14px 16px' }}>
          <div className="label">wi-fi</div>
          {s.wifi && s.wifi.mode === 'ap' ? (
            <>
              <div className="mono" style={{ fontSize: 16, fontWeight: 600, marginTop: 2, color: wifiColor('err') }}>setup hotspot</div>
              <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>hosting its setup network, not on the house Wi-Fi</div>
            </>
          ) : s.wifi && s.wifi.signal_dbm != null ? (
            <>
              <div className="mono" style={{ fontSize: 16, fontWeight: 600, marginTop: 2, color: wifiColor(wTone) }}>
                {Math.round(s.wifi.signal_dbm)} dBm
                <span style={{ fontSize: 11, fontWeight: 400 }}> {wifiSignalWord(s.wifi.signal_dbm)}</span>
              </div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
                {s.wifi.ssid || '—'}
                {s.wifi.rx_mbits != null && ` · ${s.wifi.rx_mbits.toFixed(1)}${s.wifi.tx_mbits != null ? ` / ${s.wifi.tx_mbits.toFixed(1)}` : ''} Mbit/s`}
              </div>
            </>
          ) : s.wifi && s.wifi.rx_mbits != null ? (
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
        {playing || (np?.state === 'pause' && np.song) ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontSize: 13, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <strong style={{ fontWeight: 500 }}>{np.song.title || np.song.file?.split('/').pop() || 'unknown'}</strong>
              {np.song.artist && <span style={{ color: 'var(--fg-muted)' }}> · {np.song.artist}</span>}
              <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginLeft: 8 }}>
                {fmtDur(np.elapsed_sec)}{songDur ? ` / ${fmtDur(songDur)}` : ''}
              </span>
            </div>
            {s.sat_type === 'video' && (
              <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
                <IconButton name={np.state === 'play' ? 'pause' : 'play'}
                            title={np.state === 'play' ? 'pause' : 'resume'}
                            onClick={async () => {
                              try { await apiPost(`/api/music/${np.state === 'play' ? 'pause' : 'resume'}/${s.room_id}`); }
                              catch (e) { reportMutationFailure(fire, np.state === 'play' ? 'pause' : 'resume', e); }
                            }}/>
                <IconButton name="skip-forward" title="skip"
                            onClick={async () => {
                              try { await apiPost(`/api/music/skip/${s.room_id}`); }
                              catch (e) { reportMutationFailure(fire, 'skip', e); }
                            }}/>
                <IconButton name="square" title="stop"
                            onClick={async () => {
                              try { await apiPost(`/api/music/stop/${s.room_id}`); }
                              catch (e) { reportMutationFailure(fire, 'stop', e); }
                            }}/>
              </div>
            )}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>nothing playing</div>
        )}
      </div>

      <SatHealthCard room={s.room_id} online={s.status === 'online'}/>

      <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 8, fontSize: 12, borderBottom: '1px solid var(--border-soft)' }}>
        <div className="label">room id</div>
        <div className="mono" style={{ userSelect: 'all' }}>{s.room_id}</div>
        <div className="label">type</div>
        <div className="mono">
          {s.sat_type}
          {!s.mic_enabled && <span style={{ color: 'var(--fg-faint)' }}> · voice input off</span>}
        </div>
        {s.hardware && (
          <>
            <div className="label">hardware</div>
            <div className="mono" style={{ color: 'var(--fg-muted)' }}>{s.hardware}</div>
          </>
        )}
        {satBoardLabel(s) && (
          <>
            <div className="label">board</div>
            <div className="mono" style={{ color: 'var(--fg-muted)' }}>
              {satBoardLabel(s)}{satBoardLabel(s) !== s.board ? ` · ${s.board}` : ''}
            </div>
          </>
        )}
        <div className="label">voice</div>
        <RoomVoicePicker s={s} fire={fire} refresh={refresh}/>
        <div className="label">version</div>
        <div className="mono" style={{ color: s.version ? 'var(--fg)' : 'var(--fg-faint)' }}>{s.version || '—'}</div>
        <div className="label">pairing</div>
        <div style={{ fontSize: 12 }}>
          {s.pairing && s.pairing.paired ? (
            <span style={{ color: 'var(--ok-ink)' }}>
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
        <div className="label">room</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="mono" style={{ color: s.room_label ? 'var(--fg)' : 'var(--fg-faint)' }}>
            {s.room_label || 'ungrouped'}
          </span>
          <IconButton name="pencil" title="edit room label" onClick={async () => {
            const v = window.prompt('Room label (empty to clear):', s.room_label || '');
            if (v === null) return;
            try {
              await apiPatch(`/api/satellites/${s.room_id}`, { room_label: v.trim() || null });
              fire(v.trim() ? `labeled ${s.room_id} as "${v.trim()}"` : `cleared ${s.room_id}'s room label`);
              refresh && refresh();
            } catch (e) {
              reportMutationFailure(fire, 'label update', e);
            }
          }}/>
        </div>
        <div className="label">mpd ports</div>
        <div className="mono">{s.mpd_ports ? <>control :{s.mpd_ports.control} · http :{s.mpd_ports.http}</> : <span style={{ color: 'var(--fg-faint)' }}>not provisioned yet</span>}</div>
        <div className="label">stream</div>
        <div className="mono" style={{ color: 'var(--fg-muted)' }}>{np?.stream_url || (s.mpd_ports ? `(http :${s.mpd_ports.http})` : '—')}</div>
      </div>

      <VolumeControl s={s} fire={fire}/>

      {s.sat_type === 'video' && satCan(s, 'display') && <VideoControls s={s} fire={fire}/>}

      <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6 }}>announce to {s.room_id}</div>
        {!canAnnounce && (
          <div className="mono sat-not-offered">{notOffered} doesn't take announcements — timers and reminders aren't spoken there</div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={msg} onChange={e => setMsg(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') sendAnnounce(); }}
                 placeholder={`speak through the ${s.room_id} satellite…`}
                 disabled={s.status !== 'online' || !canAnnounce}
                 style={{ flex: 1, font: 'inherit', fontSize: 13, height: 32, padding: '0 10px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <Button variant="primary" icon="send" disabled={!msg.trim() || s.status !== 'online' || !canAnnounce} onClick={sendAnnounce}>
            send
          </Button>
        </div>
      </div>

      <SatTimerScopeControl s={s} fire={fire} refresh={refresh}/>

      <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6 }}>drop in</div>
        {s.in_call_with ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Pill tone="live" live>in call with {s.in_call_with}</Pill>
            <Button icon="x" onClick={hangUp}>Hang up</Button>
          </div>
        ) : !s.mic_enabled ? (
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
            no microphone on this device — voice input is off
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

      <SatCaptureControl s={s} fire={fire} refresh={refresh}/>

      <div style={{ padding: 16, background: 'var(--sunken)', borderTop: '1px solid var(--border-soft)' }}>
        <div className="label" style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
          maintenance
          {needsUpgrade && canUpgrade && <Pill tone="warn">needs upgrade</Pill>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {/* Nothing else restarts it mid-upgrade: a restart during the sync
              would leave a half-copied tree with no rollback armed. */}
          <Button icon="refresh-cw" disabled={upgrading || s.status !== 'online' || !canRestart} onClick={restartSat}
                  title={upgrading ? 'it restarts itself when the new code is in' : undefined}>
            Restart satellite
          </Button>
          <Button variant={needsUpgrade && canUpgrade ? 'primary' : 'secondary'} icon="download-cloud"
                  disabled={upgrading || asking || s.status !== 'online' || upToDate || !canUpgrade}
                  title={upgrading ? 'an upgrade is under way — this unlocks when the satellite is back' : undefined}
                  onClick={upgradeSat}>
            {upgrading || asking ? 'Upgrading…' : 'Upgrade satellite'}
          </Button>
          <Button icon="shield-off" disabled={!(s.pairing && s.pairing.paired)} onClick={resetPairing}>
            Reset pairing
          </Button>
          {waiting && (
            <Button icon="trash-2" onClick={async () => {
              if (!window.confirm(
                `Remove ${s.room_id}?\n\nThis deletes the adopted-but-never-connected room and its ` +
                `preseeded pairing. Do this if the device was unplugged mid-setup or adopted by mistake.`
              )) return;
              try {
                await apiDelete(`/api/satellites/${s.room_id}`);
                fire(`removed ${s.room_id}`);
                onClose && onClose();
                refresh && refresh();
              } catch (e) {
                reportMutationFailure(fire, 'remove', e);
              }
            }}>
              Remove
            </Button>
          )}
          {/* A room that HAS connected. Retiring it is what frees the name:
              until every one of these rows is gone, the setup portal and the
              adopt flow both reject the name as taken, and a decommissioned
              satellite holds its room forever. Offline only - the server
              refuses to retire a room out from under a live device. */}
          {!waiting && s.status !== 'online' && (
            <Button icon="trash-2" onClick={async () => {
              if (!window.confirm(
                `Retire ${s.room_id}?\n\n` +
                `Frees the name so a new satellite can use it. Removes this room's ` +
                `pairing, any pending approval, and its music instance.\n\n` +
                `Saved playlists for the room and conversation history are kept.`
              )) return;
              try {
                await apiDelete(`/api/satellites/${s.room_id}?purge=true`);
                fire(`retired ${s.room_id} - the name is free again`);
                onClose && onClose();
                refresh && refresh();
              } catch (e) {
                reportMutationFailure(fire, 'retire', e);
              }
            }}>
              Retire room
            </Button>
          )}
        </div>
        {upgrading && (
          <div className="sat-upgrade-progress" style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 8 }}>
            {s.status === 'online'
              ? `Syncing its code${upView.expected ? ` to ${upView.expected}` : ''} from the Domovoi server…`
              : 'Restarting into the new code…'}
            {upView.startedMs != null && (
              <span className="mono" style={{ marginLeft: 6, color: 'var(--fg-faint)' }}>
                {satUpgradeClock(nowMs - upView.startedMs)}
              </span>
            )}
            <div style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 2 }}>
              It drops offline while it restarts. This unlocks when it is back on the new
              code, usually within a couple of minutes.
            </div>
          </div>
        )}
        {!upgrading && SAT_UPGRADE_FINISHED.includes(upView.state) && (
          <div className="sat-upgrade-result" data-state={upView.state}
               style={{ fontSize: 12, marginTop: 8,
                        color: upView.state === 'done' ? 'var(--ok-ink)' : 'var(--warn-ink)' }}>
            {satUpgradeOutcome(s.room_id, upView, { line: true })}
          </div>
        )}
        {upgradeErr && !upgrading && (
          <div className="sat-upgrade-error" style={{ fontSize: 12, color: 'var(--err-ink)', marginTop: 8 }}>
            {upgradeErr}
          </div>
        )}
        {canRestart && canUpgrade ? (
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 6 }}>
            restart bounces domovoi-satellite.service · upgrade syncs satellite code to {coreSha || 'the Domovoi server'} then restarts · reset pairing lets a re-flashed / new device re-pair as this room
          </div>
        ) : (
          <div className="mono sat-not-offered" style={{ marginTop: 6 }}>
            {notOffered} {!canRestart && !canUpgrade ? 'restarts and updates itself' : !canRestart ? 'restarts itself' : 'updates itself'} — it didn't offer {[!canRestart && 'restart', !canUpgrade && 'code sync'].filter(Boolean).join(' or ')} · reset pairing lets a new device re-pair as this room
          </div>
        )}
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
  const { items: all, loading, error } = useApiList(`/api/satellites/${room}/conversations?limit=300`);
  const filtered = all.filter(c => !q || ((c.user_text || '') + ' ' + (c.assistant_text || '')).toLowerCase().includes(q.toLowerCase()));
  if (loading && all.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading conversations…</div>;
  if (error && error.deviceTokenRequired && all.length === 0)
    return <PairedOnlyEmpty what="what was said in this room"/>;
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
  const { items: notes, loading, error } = useApiList(`/api/satellites/${room}/notes`);
  if (loading && notes.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading notes…</div>;
  if (error && error.deviceTokenRequired && notes.length === 0)
    return <PairedOnlyEmpty what="the notes taken in this room"/>;
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

/* A reminder whose words the server held back: this browser holds no
 * household credential (rule M1). Pair it, or sign in, to read them. */
const SatTimerWordsHidden = 'reminder (words hidden)';

// What a fired timer or reminder is called in the "recently fired" list.
// A reminder's words stay off a shared screen (the kitchen tablet), as on
// Home and the alert cards.
const SatTimerFireTitle = (f, shared) => {
  if (f.is_reminder) {
    if (shared) return 'reminder';
    return f.masked ? SatTimerWordsHidden : (f.message || 'reminder');
  }
  if (f.label) return `${f.label} timer`;
  const total = (Date.parse(f.due_at) - Date.parse(f.created_at)) / 1000;
  if (!(total > 0)) return 'timer';
  return total < 90 ? `${Math.round(total)}s timer` : `${Math.round(total / 60)} min timer`;
};

/* What went off that was SET in this room lately (fire history, V018),
 * and where it was heard. Nothing when the server keeps no history. A
 * browser with no household credential is served only the last 10
 * minutes (rule F1), so on an unpaired screen the list is short. */
const SatTimerFiredList = ({ room }) => {
  const shared = useSharedScreen();
  // Masked and cut to 10 minutes without a household credential (rules
  // M1/F1): re-read when one arrives or goes.
  const { data } = useApiObject(`/api/timers/fires?room_id=${encodeURIComponent(room)}&limit=10`,
                                { eventTypes: ['timer_fires.changed'], quiet: true, refetchOnAuth: true });
  const fires = (data && Array.isArray(data.fires)) ? data.fires : [];
  if (!fires.length) return null;
  return (
    <div className="sat-timer-fired" style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)' }}>
      <div className="label" style={{ marginBottom: 6 }}>recently fired here</div>
      {fires.map((f) => (
        <div key={f.id} className="sat-timer-fired-row mono" data-fire={f.id}
             style={{ fontSize: 12, color: 'var(--fg-muted)', padding: '3px 0', overflowWrap: 'anywhere' }}>
          {`fired ${relTime(f.fired_at)} · ${SatTimerFireTitle(f, shared)} · ${f.summary || ''}`}
        </div>
      ))}
    </div>
  );
};

// A running timer's label cell. A reminder's label holds its words, so on
// a shared screen it reads "reminder", like everywhere else there.
const SatTimerRowLabel = (t, shared) => {
  if (t.masked) return SatTimerWordsHidden;
  if (t.is_reminder && shared) return 'reminder';
  return t.label || (t.is_reminder ? t.message : '—');
};

const RoomTimersBody = ({ room, fire }) => {
  const shared = useSharedScreen();
  // A reminder's words answer to a household credential (rule M1):
  // re-read when one arrives or goes.
  const { items: rows, loading, refresh } = useApiList(`/api/satellites/${room}/timers`,
                                                       { refetchOnAuth: true });
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
      reportMutationFailure(fire, `cancel #${t.id}`, e);
    }
  };

  if (loading && rows.length === 0)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading timers…</div>;
  if (rows.length === 0)
    return (
      <>
        <Empty glyph="sleeping" title="no active timers or reminders"/>
        <SatTimerFiredList room={room}/>
      </>
    );
  return (
    <>
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
              <td style={{ fontWeight: 500 }}>
                {SatTimerRowLabel(t, shared)}
              </td>
              <td className="mono">{relTime(t.expires_at)}</td>
              <td className="num mono" style={{ color: remaining != null && remaining < 600 ? 'var(--warn-ink)' : 'var(--fg)' }}>
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
    <SatTimerFiredList room={room}/>
    </>
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
      reportMutationFailure(fire, 'add', e);
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

// A setting this satellite's code predates (`supported: false` — missing
// from the report it sent): shown, but not editable. Saved, an older Pi
// would write it to config.toml and restart, then ignore it.
const SatelliteSettingNeedsUpgrade = ({ f }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderTop: '1px solid var(--border-soft)' }}>
    <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-muted)' }}>{f.label}</span>
      <span title={f.help} style={{ color: 'var(--fg-subtle)', cursor: 'help', display: 'inline-flex' }}>
        <Icon name="info" size={13}/>
      </span>
    </div>
    <span title="This satellite's software doesn't have this setting yet. Upgrade it from its Overview tab." style={{ flexShrink: 0 }}>
      <Pill tone="warn">needs a satellite upgrade</Pill>
    </span>
  </div>
);

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
        {grouped[group].map(f => f.supported === false
          ? <SatelliteSettingNeedsUpgrade key={f.name} f={f}/>
          : <ConfigField key={f.name} f={f} value={valueOf(f)} onChange={v => setEdit(f.name, v)}/>)}
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
        {!reported && <div style={{ color: 'var(--warn-ink)', marginTop: 4 }}>waiting for the satellite to report its current config…</div>}
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
        {result && result.error && <div style={{ fontSize: 12, color: 'var(--err-ink)', marginBottom: 8 }}>save failed: {result.error}</div>}
        {rejectedCount > 0 && <div style={{ fontSize: 12, color: 'var(--err-ink)', marginBottom: 8 }}>rejected: {Object.entries(result.rejected).map(([k, v]) => `${k} (${v})`).join('; ')}</div>}
        {result && result.restarting && <div style={{ fontSize: 12, color: 'var(--warn-ink)', marginBottom: 8 }}>saved — {room} is restarting to apply. It'll reconnect shortly; reopen to confirm.</div>}
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

/* ---- Logs tab --------------------------------------------- */
/* How much of the satellite's 10 MB ring we render. The whole ring is
 * reachable from the API (?max_bytes=), but a browser <pre> of 10 MB of
 * text janks the drawer on a Pi-class client for no benefit — the last
 * megabyte is thousands of lines, which is what anyone actually reads. */
const LOG_VIEW_BYTES = 1024 * 1024;

// `fmtBytes` comes from components.jsx (the shared kit). Don't redeclare it
// here: page scripts share one global scope, and a second top-level `const`
// of the same name is a SyntaxError that kills the next script to load.

const RoomLogsBody = ({ room, online }) => {
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [data, setData] = React.useState(null);
  // Which of the two logs is shown: the server's retained file, or the
  // Pi's live ring. Declared with the other hooks (before any early
  // return) — hooks must run in the same order on every render.
  const [view, setView] = React.useState('retained');
  const preRef = React.useRef(null);

  // A refused read (the logs are an admin read) runs again once the
  // sign-in it prompted for lands (data.js useRetryAfterCredential), like
  // the Files page's libraries (F-052).
  const [refusal, setRefusal] = React.useState(null);
  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setData(await apiGet(`/api/satellites/${room}/logs?max_bytes=${LOG_VIEW_BYTES}`));
      setRefusal(null);
    } catch (e) {
      setError(e.message || String(e));
      setRefusal(e && (e.status === 401 || e.status === 403
        || (e.status === 429 && e.deviceTokenWait)) ? e : null);
      setData(null);
    } finally { setLoading(false); }
  }, [room]);
  useRetryAfterCredential(refusal, load);

  // Fetch on mount only. The drawer renders this component solely while its
  // tab is selected, so mounting IS the visit — nothing is pulled off the
  // satellite until someone actually looks, and switching back re-reads.
  React.useEffect(() => { load(); }, [load]);

  // Land on the newest line, the way `tail` does. The buffer is oldest→newest
  // and the interesting part is always the bottom.
  React.useEffect(() => {
    if (data && preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight;
  }, [data]);

  if (loading)
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading logs…</div>;

  if (error)
    return <Empty glyph="sleeping" title="couldn't read the log"
                  sub={online ? error : 'nothing is retained for this room yet, and the live ring needs the satellite online'}
                  action={<Button icon="rotate-cw" onClick={load}>Try again</Button>}/>;

  // Two logs since 2026-10-09: what the core RETAINS on disk for this room
  // (the lines the satellite pushed — including what it spooled while it
  // was disconnected, marked [offline] — survives the Pi dying) and the
  // live ring in the Pi's memory (the old pull; null while it is offline).
  // A core from before the change answers the old flat shape: `live` and
  // `retained` are then undefined and the ring is rendered as before.
  const live = data && (data.live !== undefined ? data.live : { text: data.text, bytes: data.bytes,
                                                                buffered_bytes: data.buffered_bytes, truncated: data.truncated });
  const retained = data && data.retained;
  // A connected device that declared no live log (S0) answers like an
  // offline one, with `live_supported: false`.
  const liveOffered = !(data && data.live_supported === false);
  const showRetained = view === 'retained' && !!retained;
  const text = showRetained ? (retained.text || '') : ((live && live.text) || '');
  const preStyle = {
    flex: 1, margin: 0, padding: '12px 16px', overflow: 'auto',
    background: 'var(--sunken)', color: 'var(--fg-muted)',
    fontSize: 11, lineHeight: 1.55, whiteSpace: 'pre',
    // pre-wrap would reflow long tracebacks into an unreadable
    // block; a horizontal scrollbar keeps one record on one line.
    tabSize: 4,
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border-soft)',
                    display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Button icon="rotate-cw" onClick={load}>Refresh</Button>
        <Tabs padX={0} value={showRetained ? 'retained' : 'live'} onChange={setView} tabs={[
          { id: 'retained', label: `retained on the server${retained ? ` · ${fmtBytes(retained.bytes)}` : ' · none'}` },
          { id: 'live', label: `live ring${live ? ` · ${fmtBytes(live.bytes)}` : liveOffered ? (online ? '' : ' · offline') : ' · not offered'}` },
        ]}/>
        {showRetained && retained.truncated &&
          <Pill tone="idle">last {fmtBytes(retained.bytes)} of {fmtBytes(retained.file_bytes)} on disk</Pill>}
        {!showRetained && live && live.truncated &&
          <Pill tone="idle">showing last {fmtBytes(live.bytes)} of {fmtBytes(live.buffered_bytes)}</Pill>}
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-faint)' }}>
          {showRetained
            ? `on the server (last 1 MB) · [offline] = spooled while disconnected${retained.updated_at ? ` · ${relTime(retained.updated_at)}` : ''}`
            : 'in memory on the Pi · lost on restart'}
        </span>
      </div>
      {!showRetained && !live && !liveOffered
        ? <Empty glyph="sleeping" title="this device doesn't offer a live log"
                 sub="it didn't declare one in its hello — the retained log is what it pushed"/>
      : !showRetained && !live
        ? <Empty glyph="sleeping" title="the live ring needs the satellite online"
                 sub={retained ? 'the retained log above is what it pushed before it went offline' : 'the buffer lives in its own process'}/>
        : text === ''
          ? <Empty glyph="sleeping" title={showRetained ? 'nothing retained yet' : 'log is empty'}
                   sub={showRetained ? 'the satellite pushes its lines once it runs code from 2026-10-09 on' : 'the satellite has not written anything since it started'}/>
          : <pre ref={preRef} className="mono" style={preStyle}>{text}</pre>}
    </div>
  );
};

/* ---- Drawer ----------------------------------------------- */
const SatDrawer = ({ s, sats, onClose, fire, refresh }) => {
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
    { id: 'logs',          label: 'Logs',            icon: 'terminal' },
    { id: 'settings',      label: 'Settings',        icon: 'settings' },
  ];

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.18)', backdropFilter: 'blur(2px)', zIndex: 60 }}/>
      <aside style={{ position: 'fixed', top: 0, right: 0, height: 'var(--app-h)', width: 560, maxWidth: '100%',
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
          {tab === 'overview'      && <OverviewBody s={s} sats={sats} fire={fire} onClose={onClose} refresh={refresh}/>}
          {tab === 'sessions'      && <RoomSessionsBody room={s.room_id}/>}
          {tab === 'conversations' && <RoomConversationsBody room={s.room_id}/>}
          {tab === 'recently'      && <RoomRecentlyPlayedBody room={s.room_id} fire={fire}/>}
          {tab === 'notes'         && <RoomNotesBody room={s.room_id}/>}
          {tab === 'timers'        && <RoomTimersBody room={s.room_id} fire={fire}/>}
          {tab === 'logs'          && <RoomLogsBody room={s.room_id} online={s.status === 'online'}/>}
          {tab === 'settings'      && (satCan(s, 'config')
            ? <RoomSettingsBody room={s.room_id} fire={fire}/>
            : <Empty glyph="sleeping" title="settings stay on the device"
                     sub={`${satBoardLabel(s) ? `this ${satBoardLabel(s)}` : 'this device'} didn't offer remote settings — change them on the device itself`}/>)}
        </div>
      </aside>
    </>
  );
};

/* ---- Broadcast composer ----------------------------------- */
/* `compact` is Home's one-line "say something in every room": the same
 * send, the same partial-delivery toast, in a row that fits a phone. */
const Broadcast = ({ onlineCount, fire, compact = false }) => {
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
      reportMutationFailure(fire, 'broadcast', e);
    }
  };
  if (compact) {
    const none = onlineCount === 0;
    return (
      <Card title="announce"
            action={<Pill tone={none ? 'idle' : 'live'} live={!none}>{none ? 'no rooms online' : `${onlineCount} online`}</Pill>}>
        {/* Heights live in styles.css (.broadcast-compact), not inline,
            so Home's phone block can make both 44px touch targets; on a
            phone the label hides and the megaphone button is the send. */}
        <div className="broadcast-compact" style={{ display: 'flex', gap: 8, padding: '6px 16px 10px' }}>
          <input value={msg} onChange={e => setMsg(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') send(); }}
                 aria-label="announcement"
                 placeholder="say something in every room" disabled={none}
                 style={{ flex: 1, minWidth: 0, font: 'inherit', fontSize: 13, padding: '0 12px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <Button variant="primary" icon="megaphone" title="announce in every room"
                  aria-label="announce in every room"
                  disabled={!msg.trim() || none} onClick={() => { send(); }}>
            <span className="broadcast-send-label">send</span>
          </Button>
        </div>
      </Card>
    );
  }
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
                 style={{ flex: 1, minWidth: 0, font: 'inherit', fontSize: 14, height: 38, padding: '0 12px',
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
/* ---- USB adoption: pending devices + adopt modal -------------------
 * An unprovisioned satellite plugged into the server's USB port shows up
 * here (satellites.pending.changed). Adopt = name it + hand it Wi-Fi; the
 * backend preseeds pairing and writes the provision file to the device.
 * The PSK lives only in modal state and is cleared on close. */

const adoptInputStyle = {
  width: '100%', font: 'inherit', fontSize: 13, height: 32, padding: '0 10px',
  borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
  background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)',
  boxSizing: 'border-box',
};

const AdoptModal = ({ pending, sats, force, onClose, onAdopted, fire }) => {
  const [roomId, setRoomId] = React.useState(force && pending.already_adopted_as ? pending.already_adopted_as : '');
  const [roomLabel, setRoomLabel] = React.useState('');
  const [ssid, setSsid] = React.useState('');
  const [psk, setPsk] = React.useState('');
  const [showPsk, setShowPsk] = React.useState(false);
  const [country, setCountry] = React.useState('');
  const profiles = pending.profiles_supported && pending.profiles_supported.length
    ? pending.profiles_supported
    : ['respeaker_2mic_hat', 'xvf3800_usb', 'radxa_zero3w_video'];
  const [profile, setProfile] = React.useState(
    pending.sat_type === 'video' && profiles.includes('radxa_zero3w_video')
      ? 'radxa_zero3w_video' : profiles[0]
  );
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);

  const idOk = /^[a-z][a-z0-9_]{0,31}$/.test(roomId);
  const collision = !force && sats.some(s => s.room_id === roomId);
  const canSubmit = idOk && !collision && ssid.trim() && psk.length >= 8 &&
    (!country || /^[A-Z]{2}$/.test(country)) && !busy;

  const close = () => { setPsk(''); onClose(); };

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      await apiPost(`/api/satellites/pending/${pending.pending_id}/adopt`, {
        room_id: roomId,
        room_label: roomLabel.trim() || null,
        wifi_ssid: ssid.trim(),
        wifi_psk: psk,
        wifi_country: country || null,
        device_profile: profile,
        force: !!force,
      });
      fire(`provisioned ${roomId} — safe to unplug once the drive disappears`);
      setPsk('');
      onAdopted(pending.pending_id, roomId);
    } catch (e) {
      if (e.status === 409) setErr(e.message.includes('re-announced') ? 'device re-announced itself — close and retry' : `room "${roomId}" already exists`);
      else if (e.status === 410) setErr('device was unplugged');
      else setErr(`adopt failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const row = (label, node) => (
    <div style={{ marginBottom: 12 }}>
      <div className="label" style={{ marginBottom: 4 }}>{label}</div>
      {node}
    </div>
  );

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex',
                  alignItems: 'center', justifyContent: 'center',
                  background: 'oklch(0.1 0 0 / 0.5)' }}
         onClick={close}>
      <div onClick={e => e.stopPropagation()}
           style={{ width: 420, maxWidth: 'calc(100vw - 32px)', maxHeight: '90vh', overflowY: 'auto',
                    background: 'var(--card)', border: '1px solid var(--border)',
                    borderRadius: 'var(--r-lg)', boxShadow: 'var(--shadow-md), var(--inner-highlight)',
                    padding: 20 }}>
        <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>
          {force ? 're-provision satellite' : 'adopt satellite'}
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginBottom: 16 }}>
          {pending.model || pending.board || 'unknown board'}
          {pending.mac && ` · ${pending.mac.slice(-8)}`}
          {' · '}{pending.sat_type}
        </div>

        {row('satellite name', (
          <>
            <input value={roomId} onChange={e => setRoomId(e.target.value)} style={adoptInputStyle}
                   placeholder="kitchen, livingroom_tv, …" autoFocus disabled={force && !!pending.already_adopted_as}/>
            {roomId && !idOk && <div className="mono" style={{ fontSize: 11, color: 'var(--err-ink)', marginTop: 4 }}>lowercase letters, digits, _ — must start with a letter</div>}
            {collision && <div className="mono" style={{ fontSize: 11, color: 'var(--err-ink)', marginTop: 4 }}>that name is taken</div>}
          </>
        ))}
        {row('room label (optional — groups satellites in one room)', (
          <input value={roomLabel} onChange={e => setRoomLabel(e.target.value)} style={adoptInputStyle}
                 placeholder="Living Room"/>
        ))}
        {row('wi-fi network', (
          <input value={ssid} onChange={e => setSsid(e.target.value)} style={adoptInputStyle}
                 placeholder="SSID"/>
        ))}
        {row('wi-fi password', (
          <div style={{ display: 'flex', gap: 6 }}>
            <input value={psk} onChange={e => setPsk(e.target.value)} style={{ ...adoptInputStyle, flex: 1 }}
                   type={showPsk ? 'text' : 'password'} placeholder="8+ characters"/>
            <IconButton name={showPsk ? 'eye-off' : 'eye'} title={showPsk ? 'hide' : 'show'}
                        onClick={() => setShowPsk(v => !v)}/>
          </div>
        ))}
        {row('wi-fi country (optional, 2-letter)', (
          <input value={country} onChange={e => setCountry(e.target.value.toUpperCase())}
                 style={{ ...adoptInputStyle, width: 90 }} placeholder="US" maxLength={2}/>
        ))}
        {row('mic board profile', (
          <select value={profile} onChange={e => setProfile(e.target.value)}
                  style={{ ...adoptInputStyle, height: 34 }}>
            {profiles.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        ))}

        {err && <div className="mono" style={{ fontSize: 12, color: 'var(--err-ink)', marginBottom: 10 }}>{err}</div>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 6 }}>
          <Button onClick={close}>Cancel</Button>
          <Button variant="primary" icon="plug" disabled={!canSubmit} onClick={submit}>
            {busy ? 'provisioning…' : force ? 'Re-provision' : 'Adopt'}
          </Button>
        </div>
      </div>
    </div>
  );
};

/* ---- Wi-Fi onboarding: satellites waiting for a human ---------------
 *
 * A satellite set up through its own Wi-Fi portal can't be preseeded — the
 * server was never part of that exchange — so it parks here instead of
 * claiming its room on trust. It shows a six-digit code and says it out
 * loud; typing that code in is what makes this a decision about the device
 * in the room rather than about whoever connected first.
 *
 * The code is deliberately NOT in this payload. A code printed next to the
 * approve button would be a label again — the whole point is that only
 * someone who can see or hear the device knows it. The server compares it
 * and counts the attempts.
 */
const APPROVAL_CODE_LEN = 6;

/* Which refusals belong BESIDE THE CODE BOX rather than in a toast.
 *
 * The approve route is admin-gated at both hops, so data.js's generic
 * auth retry treats any 401/403 on it as "sign in again" — it opens the
 * admin-password modal and replays once a bearer exists. That is right
 * for a refusal about WHO is asking and wrong for a refusal about WHAT
 * was typed: one mistyped digit used to throw Kamron out of the approval
 * he was standing in front of and into the password prompt, and the
 * first refusal arrived as `approve failed: 403 Forbidden:
 * {"detail":"…"}` — a status line and a JSON blob — in a toast (F-050).
 *
 * The core answers every refusal about the request itself on a status
 * that is not about credentials — 422 wrong code, 400 no code or
 * non-digits, 409 nothing pending / no code on file, 429 the room's
 * budget for the moment. Those land here, at the field, in the server's
 * own words. Anything else — an unreachable core, a real 401 — is not
 * this field's business and keeps the toast (and, for a genuine sign-in
 * problem, the modal data.js opens for it).
 *
 * `tone` is the difference between "that did not work" and "not just
 * yet": a 429 is a WAIT. Nothing was lost, nothing is banned, and the
 * satellite is still asking — so it must not be painted as a failure.
 *
 * `marksTheCode` is narrower still, and only true when the DIGITS are
 * what was wrong (422, 400). A 409 says the request is no longer there
 * — someone else approved it, or it was rejected — and a 429 says not
 * yet; reddening the box in either case would accuse the operator of a
 * typo he did not make.
 */
const APPROVAL_REFUSALS = {
  400: { tone: 'err', marksTheCode: true },    // nothing typed, or not digits
  422: { tone: 'err', marksTheCode: true },    // wrong code
  409: { tone: 'err', marksTheCode: false },   // nothing pending / no code on file
  429: { tone: 'wait', marksTheCode: false },  // the room's budget, for now
};
/* NOT CLIPPED, and that is the whole point of the second argument.
 *
 * This text lands in a card that wraps, next to the box the operator is
 * typing into; there is no width it has to fit. It used to be cut at
 * 400 characters, which was longer than the refusals of the day and
 * shorter than the throttle sentence that replaced them — so the 429
 * arrived ending "…and it is the code column of th": mid-word, with the
 * third of the three recovery routes gone, reading like a broken
 * dashboard rather than like advice. A cap chosen to be "big enough"
 * is a cap somebody's next sentence walks past without telling anyone.
 *
 * `apiErrorText`'s clipping is fixed too (it now stops at a word and
 * says it was cut), because a toast still has a size. This one has
 * none, so it asks for none. */
const approvalFieldError = (e) => {
  const shape = APPROVAL_REFUSALS[(e && e.status) || 0];
  if (!shape) return null;
  return { ...shape, text: apiErrorText(e, 0) };
};

const ApprovalCard = ({ a, busy, error, onApprove, onReject, onClearError }) => {
  const [code, setCode] = React.useState('');
  const ready = code.length >= 4 && !busy;
  const submit = () => { if (ready) onApprove(a, code); };
  // Typing is the operator answering the message, so it goes. The digits
  // stay: fixing the one that was wrong beats retyping all six, and the
  // budget is five tries.
  const retype = (v) => {
    setCode(v);
    if (error && onClearError) onClearError();
  };
  return (
    <Card>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px',
                    flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontWeight: 600 }}>{a.room_id}</div>
          <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            {(a.board && (SAT_BOARD_LABELS[a.board] || a.board)) || 'unknown board'}
            {a.mac ? ` · ${a.mac.slice(-8)}` : ''}
            {' · '}{a.sat_type || 'voice'}
            {/* How many times the DEVICE has re-announced itself, which is
              * not a budget and not the approval-code attempt counter
              * (that one is the server's, 5 per room per 5 minutes, and
              * lives nowhere on this page). "N attempts" next to a code
              * box read as a limit running down — F-049. */}
            {a.attempts > 1
              ? <span title="times this satellite has asked to be approved — not a limit">
                  {' · '}asked {a.attempts} times
                </span>
              : null}
          </div>
        </div>
        <input value={code}
               onChange={e => retype(e.target.value.replace(/\D/g, '').slice(0, 10))}
               onKeyDown={e => { if (e.key === 'Enter') submit(); }}
               inputMode="numeric" autoComplete="off" spellCheck={false}
               aria-label={`approval code for ${a.room_id}`}
               aria-invalid={error && error.marksTheCode ? true : undefined}
               placeholder={'0'.repeat(APPROVAL_CODE_LEN)} disabled={busy}
               className="mono"
               style={{ width: 130, fontSize: 20, letterSpacing: '0.16em', height: 38,
                        textAlign: 'center', padding: '0 10px',
                        borderRadius: 'var(--r-sm)',
                        border: `1px solid ${error && error.marksTheCode
                                   ? 'var(--err)' : 'var(--border)'}`,
                        background: 'var(--card)', color: 'var(--fg)',
                        boxShadow: 'var(--inner-highlight)' }}/>
        <Button variant="primary" icon="check" disabled={!ready}
                onClick={submit}>approve</Button>
        <Button variant="ghost" icon="x" disabled={busy}
                onClick={() => onReject(a)}>reject</Button>
      </div>
      {error && (
        <div role="alert"
             style={{ padding: '0 16px 10px', fontSize: 12, lineHeight: 1.5,
                      color: error.tone === 'wait' ? 'var(--warn-ink)' : 'var(--err-ink)' }}>
          {error.text}
        </div>
      )}
      <div style={{ padding: '0 16px 12px', fontSize: 12, color: 'var(--fg-muted)' }}>
        {a.has_code === false
          ? `This request arrived without a code — power-cycle the satellite so it asks again with one.`
          : `Type the ${APPROVAL_CODE_LEN} digits the satellite is showing and saying. Reading them off the device is what proves this request is the unit in front of you. Missed them? The code never changes — the satellite says it again every time it retries, and it is on the Pi in ~/.domovoi/approval_code.`}
      </div>
    </Card>
  );
};

const PendingSatCard = ({ p, adopted, onAdopt }) => {
  const failed = p.status === 'wifi_failed';
  const readopt = !!p.already_adopted_as;
  return (
    <div style={{ background: 'var(--card)', border: '1px solid var(--brand)',
                  borderRadius: 'var(--r-md)', boxShadow: 'var(--inner-highlight)',
                  padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
      <Icon name="usb" size={18}/>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>
          {p.parse_error ? 'unreadable setup drive' : (p.model || p.board || 'new satellite')}
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
          {p.parse_error
            ? p.parse_error
            : [
                p.mac && p.mac.slice(-8),
                p.sat_type,
                readopt && `already adopted as ${p.already_adopted_as}`,
              ].filter(Boolean).join(' · ')}
        </div>
        {failed && p.error && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--err-ink)', marginTop: 2 }}>{p.error}</div>
        )}
      </div>
      {adopted ? (
        <Pill tone="live" live>provisioned — waiting for device</Pill>
      ) : p.parse_error ? (
        <Pill tone="warn">unreadable</Pill>
      ) : failed ? (
        <Button variant="primary" icon="wifi" onClick={() => onAdopt(p, true)}>Re-enter Wi-Fi</Button>
      ) : readopt ? (
        <Button icon="rotate-cw" onClick={() => {
          if (window.confirm(`Re-provision this device as "${p.already_adopted_as}"? Its pairing token is rotated — the previous device for that room stops matching.`)) onAdopt(p, true);
        }}>Re-provision</Button>
      ) : (
        <Button variant="primary" icon="plug" onClick={() => onAdopt(p, false)}>Adopt</Button>
      )}
    </div>
  );
};

/* ---- Add a spare device (S1, 2026-10-13) ------------------------------
 * A Linux laptop, a mini PC or a phone becomes a room. The admin names the
 * room; the server answers with one enrolment payload
 * (`domovoi-room:1?core=…&fp=…&room=…`, docs/uml/satellite-protocol.md)
 * shown two ways: inside a one-line install command for Linux, and as a
 * QR code for a phone's room mode. The payload carries no secret: the new
 * device still shows up above, waiting for its approval code. Admin only
 * (the route is too). The QR library loads only when a code is drawn. */
const _spareQrScript = { p: null };
const spareLoadQr = () => {
  if (window.qrcode) return Promise.resolve(window.qrcode);
  if (!_spareQrScript.p) {
    _spareQrScript.p = new Promise((resolve, reject) => {
      const el = document.createElement('script');
      el.src = '/vendor/qrcode/qrcode.js';
      el.onload = () => (window.qrcode ? resolve(window.qrcode) : reject(new Error('no qrcode')));
      el.onerror = () => { _spareQrScript.p = null; reject(new Error('the QR library did not load')); };
      document.head.appendChild(el);
    });
  }
  return _spareQrScript.p;
};

// The QR's modules, drawn as SVG squares (no innerHTML).
const SpareQr = ({ text }) => {
  const [matrix, setMatrix] = React.useState(null);
  const [err, setErr] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    setMatrix(null); setErr(null);
    spareLoadQr().then((qrcode) => {
      const qr = qrcode(0, 'M');
      qr.addData(text);
      qr.make();
      const n = qr.getModuleCount();
      const dark = [];
      for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) dark.push([r, c]);
      if (alive) setMatrix({ n, dark });
    }).catch((e) => { if (alive) setErr(e.message || String(e)); });
    return () => { alive = false; };
  }, [text]);
  if (err) return <div style={{ fontSize: 12, color: 'var(--err-ink)' }}>couldn’t draw the QR code: {err}</div>;
  if (!matrix) return <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>drawing the QR code…</div>;
  const q = 4;   // the quiet zone, in modules
  const size = matrix.n + 2 * q;
  return (
    <svg className="spare-qr" role="img" aria-label="QR code of the enrolment payload"
         viewBox={`0 0 ${size} ${size}`} width="216" height="216" shapeRendering="crispEdges"
         style={{ background: '#fff', borderRadius: 'var(--r-sm)', flex: 'none' }}>
      <rect width={size} height={size} fill="#fff"/>
      {matrix.dark.map(([r, c]) => <rect key={`${r}-${c}`} x={c + q} y={r + q} width="1" height="1" fill="#000"/>)}
    </svg>
  );
};

/* The Windows laptop's command (2026-10-13). The same trust model as the
 * Linux one: the bundle's SHA-256 is checked before anything runs, the
 * server proves its fingerprint before anything is written. It installs for
 * the signed-in account only, with no administrator rights; the Python and
 * libraries it needs come from this server's cache when an admin has
 * fetched them (here), else straight from python.org and PyPI — the same
 * pinned files either way. Unsigned for now: said plainly. */
const spareMb = (b) => b == null ? '—' : `${Math.round(b / 1e6)} MB`;
const SpareWindows = ({ res, fire }) => {
  const [rt, setRt] = React.useState(res.windows_runtime || null);
  const [fetching, setFetching] = React.useState(false);
  React.useEffect(() => { setRt(res.windows_runtime || null); }, [res]);
  React.useEffect(() => {
    if (!rt || !rt.fetch || !rt.fetch.running) return undefined;
    const t = setInterval(async () => {
      try { setRt(await apiGet('/api/satellites/windows-runtime')); } catch (e) { /* keep the last */ }
    }, 3000);
    return () => clearInterval(t);
  }, [rt && rt.fetch && rt.fetch.running]);
  const getFiles = async () => {
    setFetching(true);
    try {
      setRt(await apiPost('/api/satellites/windows-runtime/fetch', {}));
    } catch (e) {
      fire(apiErrorText(e, 0));
    } finally {
      setFetching(false);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(res.command_windows);
      fire('Windows install command copied');
    } catch (e) {
      fire('select the command and copy it by hand (the browser refused the clipboard)');
    }
  };
  const running = !!(rt && rt.fetch && rt.fetch.running);
  return (
    <div className="spare-windows" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div className="label">windows laptop or pc (windows 10 or 11, 64-bit)</div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
        On the PC, signed in as the person whose account it will run in (a normal window, not
        "Run as administrator"), open Windows PowerShell or Terminal and paste this. It installs
        for that account only, needs no administrator rights, and listens while that account is
        signed in. Not on the PC that runs this Domovoi server: it refuses there.
      </div>
      <pre className="mono spare-command spare-command-windows" tabIndex={0}
           style={{ margin: 0, padding: 10, fontSize: 11.5, lineHeight: 1.45,
                    whiteSpace: 'pre-wrap', wordBreak: 'break-all', userSelect: 'all',
                    background: 'var(--bg-sunken, var(--bg))', border: '1px solid var(--border)',
                    borderRadius: 'var(--r-sm)', maxHeight: 160, overflow: 'auto' }}>{res.command_windows}</pre>
      <div><Button icon="copy" onClick={copy}>copy the Windows command</Button></div>
      <div role="note" className="spare-windows-unsigned"
           style={{ fontSize: 11.5, color: 'var(--warn-ink)', lineHeight: 1.5 }}>
        Not signed yet: the satellite's own code carries no Coders Farm signature (Python itself is
        signed by the Python Software Foundation). Windows Security or another antivirus may warn
        about a command that downloads and starts programs; Windows shows its microphone-in-use
        icon the whole time the satellite listens for the wake word.
      </div>
      {rt && (
        <div className="spare-windows-runtime" style={{ fontSize: 11.5, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
          {rt.ready
            ? <>This server holds the Windows files (Python {rt.python} and its libraries,
                {' '}{spareMb(rt.bytes_total)}): the PC gets them from here.</>
            : <>This server doesn't hold the Windows files yet (Python {rt.python} and its libraries,
                {' '}{spareMb(rt.bytes_total)}{rt.bytes_cached ? `, ${spareMb(rt.bytes_cached)} so far` : ''}).
                Get them now, or the PC downloads the same pinned files from python.org and PyPI
                itself.{' '}
                {rt.fetch && rt.fetch.ok === false && rt.fetch.message
                  ? <span style={{ color: 'var(--err-ink)' }}>Last try: {rt.fetch.message}</span> : null}</>}
          {!rt.ready && (
            <div style={{ marginTop: 6 }}>
              <Button icon="download" disabled={running || fetching} onClick={getFiles}>
                {running ? 'getting the Windows files…' : 'get the Windows files'}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/* The Android app on this server (2026-10-15, domovoi/android_app.py), the
 * admin's side, shared by Settings → Rooms → add a spare device and
 * Settings → Phones & browsers → get the Domovoi app. The release android/release.json
 * pins comes from the project's GitHub release ONLY when an admin presses
 * "Download the app to this server" (owner decision 2026-10-15: never by
 * itself), or as an APK an admin uploads (internet access off). The server
 * keeps a file only when it is one the release names, signed by the release
 * key. Progress while it downloads; a failure stays on the card until the
 * next try. No release yet: said plainly, no buttons. `app` is the admin
 * status (GET /api/satellites/android-app) when the caller has it; without
 * it the component reads it. `onReady` runs once every file is here. */
const appOnServerMessage = (e) => {
  const d = e && e.detail && e.detail.detail;
  return (d && typeof d === 'object' && typeof d.message === 'string') ? d.message : apiErrorText(e, 300);
};
const AppOnServer = ({ app: initial, onReady }) => {
  const [app, setApp] = React.useState(initial || null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [kept, setKept] = React.useState(null);
  const fileRef = React.useRef(null);
  React.useEffect(() => { if (initial) setApp(initial); }, [initial]);
  React.useEffect(() => {
    if (initial) return undefined;
    let alive = true;
    apiGet('/api/satellites/android-app').then(r => { if (alive) setApp(r); })
      .catch(e => { if (alive) setError(appOnServerMessage(e)); });
    return () => { alive = false; };
  }, []);
  const running = !!(app && app.fetch && app.fetch.running);
  React.useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(async () => {
      try { setApp(await apiGet('/api/satellites/android-app')); } catch (e) { /* keep the last */ }
    }, 2000);
    return () => clearInterval(t);
  }, [running]);
  const ready = !!(app && app.ready);
  React.useEffect(() => { if (ready && onReady) onReady(); }, [ready]);
  const download = async () => {
    setBusy(true); setError(null); setKept(null);
    try { setApp(await apiPost('/api/satellites/android-app/fetch', {})); }
    catch (e) { setError(appOnServerMessage(e)); }
    finally { setBusy(false); }
  };
  const upload = async (file) => {
    if (!file) return;
    setBusy(true); setError(null); setKept(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await apiUpload('/api/satellites/android-app/upload', fd);
      setApp(r);
      setKept(r.accepted);
    } catch (e) {
      setError(appOnServerMessage(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };
  const note = { fontSize: 11.5, color: 'var(--fg-faint)', lineHeight: 1.5 };
  if (!app) {
    return <div className="spare-app" style={note}>{error ? `Couldn’t ask the server about the Android app: ${error}` : 'asking the server about the Android app…'}</div>;
  }
  if (!app.release) {
    return (
      <div className="spare-app spare-app-none" role="note" style={note}>
        No app release yet: the page tells the phone so. A phone that already has the Domovoi app
        can still use this code in Domovoi Room.
      </div>
    );
  }
  const v = app.release.version_name;
  const sizes = (app.apks || []).map(a => `${a.abi === 'armeabi-v7a' ? '32-bit' : '64-bit'} ${spareMb(a.size)}`).join(', ');
  if (app.ready) {
    return (
      <div className="spare-app spare-app-ready" style={note}>
        {kept ? <>Kept {kept}. </> : null}This server hands out Domovoi {v} ({sizes}).
      </div>
    );
  }
  const f = app.fetch || {};
  const remaining = Math.max(0, (app.bytes_total || 0) - (app.bytes_cached || 0));
  const failed = !running && f.ok === false && f.message;
  const pct = f.bytes_todo ? Math.min(100, Math.round(100 * (f.bytes_done || 0) / f.bytes_todo)) : 0;
  return (
    <div className="spare-app spare-app-missing" style={{ ...note, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {running ? (
        <div className="spare-app-progress">
          <div>Downloading Domovoi {v} to this server: {spareMb(f.bytes_done || 0)} of {spareMb(f.bytes_todo || remaining)}…</div>
          <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
               style={{ height: 4, borderRadius: 2, background: 'var(--border)', marginTop: 4, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: 'var(--brand)' }}/>
          </div>
        </div>
      ) : app.internet_off ? (
        <div>Internet access is off here, so this server can’t download Domovoi {v}. Upload its APKs
          ({(app.missing || []).join(', ')}) from the release on GitHub instead.</div>
      ) : (
        <div>The app isn’t on this server yet (Domovoi {v}: {sizes}); until it is, the page tells the
          phone to ask you.</div>
      )}
      {failed && <div role="alert" className="spare-app-error" style={{ color: 'var(--err-ink)' }}>The download failed: {f.message}</div>}
      {error && <div role="alert" className="spare-app-error" style={{ color: 'var(--err-ink)' }}>{error}</div>}
      {kept && <div>Kept {kept}.</div>}
      {!running && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          {!app.internet_off && (
            <Button icon="download" variant="primary" disabled={busy} onClick={download}>
              {`Download the app to this server (about ${spareMb(remaining)})`}
            </Button>
          )}
          <Button icon="upload" disabled={busy} onClick={() => fileRef.current && fileRef.current.click()}>
            {busy ? 'checking…' : 'upload an APK'}
          </Button>
          <input ref={fileRef} type="file" accept=".apk,application/vnd.android.package-archive" hidden
                 aria-label="upload an APK of the release"
                 onChange={e => upload(e.target.files && e.target.files[0])}/>
        </div>
      )}
    </div>
  );
};

/* The Linux laptop's own Python (2026-10-15). A Debian or Ubuntu new enough
 * (Python 3.11+) uses its own; older ones (Ubuntu 22.04, Mint 21, Debian 11)
 * and Fedora, openSUSE and Arch get a pinned CPython, from this server's
 * cache when an admin has fetched it (here), else straight from GitHub —
 * the same pinned file either way, checked by its SHA-256 on the laptop. */
const SpareLinuxPython = ({ res, fire }) => {
  const [rt, setRt] = React.useState(res.linux_runtime || null);
  const [fetching, setFetching] = React.useState(false);
  React.useEffect(() => { setRt(res.linux_runtime || null); }, [res]);
  React.useEffect(() => {
    if (!rt || !rt.fetch || !rt.fetch.running) return undefined;
    const t = setInterval(async () => {
      try { setRt(await apiGet('/api/satellites/linux-runtime')); } catch (e) { /* keep the last */ }
    }, 3000);
    return () => clearInterval(t);
  }, [rt && rt.fetch && rt.fetch.running]);
  if (!rt) return null;
  const getIt = async () => {
    setFetching(true);
    try {
      setRt(await apiPost('/api/satellites/linux-runtime/fetch', {}));
    } catch (e) {
      fire(apiErrorText(e, 0));
    } finally {
      setFetching(false);
    }
  };
  const running = !!(rt.fetch && rt.fetch.running);
  const versions = (rt.pythons || []).map((p) => p.version).filter((v, i, all) => all.indexOf(v) === i).join(' / ');
  return (
    <div className="spare-linux-python" style={{ fontSize: 11.5, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
      Older Linux (Ubuntu 22.04, Mint 21, Debian 11) and Fedora, openSUSE or Arch get the
      satellite's own Python ({versions}).{' '}
      {rt.ready
        ? <>This server holds it ({spareMb(rt.bytes_total)}): the laptop gets it from here.</>
        : <>This server doesn't hold it yet ({spareMb(rt.bytes_total)}{rt.bytes_cached ? `, ${spareMb(rt.bytes_cached)} so far` : ''}).
            Get it now, or the laptop downloads the same pinned file from GitHub itself.{' '}
            {rt.fetch && rt.fetch.ok === false && rt.fetch.message
              ? <span style={{ color: 'var(--err-ink)' }}>Last try: {rt.fetch.message}</span> : null}</>}
      {!rt.ready && (
        <div style={{ marginTop: 6 }}>
          <Button icon="download" disabled={running || fetching} onClick={getIt}>
            {running ? 'getting the Linux Python…' : 'get the Linux Python'}
          </Button>
        </div>
      )}
    </div>
  );
};

/* Old computer (USB stick), 2026-10-16 (domovoi/usb_stick/ on the core).
 * An old 64-bit PC or laptop, or an Intel Mac, becomes this room from a USB
 * stick the server makes: "Prepare the stick" downloads Debian's netinst
 * from Debian the first time (pinned by size and SHA-256), then re-packs it
 * with this room's code. The stick asks one question on the old computer
 * (type ERASE, naming its disk) and nothing is erased before that. Written
 * with Raspberry Pi Imager (its custom-list link opens it with this stick
 * listed, nothing sent to Raspberry Pi) or USBImager. Refused up front with
 * the reason (internet "never" without Debian's image here, the disk short,
 * no xorriso on the server); a failure stays here until the next try. */
const SPARE_STICK_BOOT_KEYS = [
  ['Dell', 'F12'], ['HP', 'Esc, then F9'], ['Lenovo', 'F12 (some: the small Novo button)'],
  ['Acer', 'F12 (turn on "F12 Boot Menu" in the firmware first)'], ['ASUS', 'Esc (desktops: F8)'],
  ['Toshiba / Dynabook', 'F12'], ['MSI', 'F11'], ['Samsung', 'Esc or F10'], ['Sony VAIO', 'Assist button, or F11'],
  ['Fujitsu', 'F12'], ['Gigabyte / ASRock', 'F12 / F11'], ['Intel NUC', 'F10'],
  ['Intel Mac (2012-2017)', 'hold Option (Alt) from the start chime'],
];
const SpareStickStep = ({ n, icon, children }) => (
  <li style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
    <span aria-hidden style={{ flex: 'none', width: 32, height: 32, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                               display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-muted)' }}>
      <Icon name={icon} size={18}/>
    </span>
    <span style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--fg-muted)' }}>
      <span className="mono" style={{ color: 'var(--fg)', marginRight: 6 }}>{n}</span>{children}
    </span>
  </li>
);
const spareStickWhen = (epoch) => epoch ? relTime(new Date(epoch * 1000).toISOString()) : '—';
const SpareUsbStick = ({ res, body, fire }) => {
  const [st, setSt] = React.useState(res.usb_stick || null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);
  React.useEffect(() => { setSt(res.usb_stick || null); setError(null); }, [res]);
  const refresh = async () => {
    try { setSt(await apiGet('/api/satellites/usb-stick')); } catch (e) { setError(appOnServerMessage(e)); }
  };
  React.useEffect(() => { if (!res.usb_stick) refresh(); }, [res]);
  const job = (st && st.job) || {};
  const running = !!job.running;
  React.useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(async () => {
      try { setSt(await apiGet('/api/satellites/usb-stick')); } catch (e) { /* keep the last */ }
    }, 2000);
    return () => clearInterval(t);
  }, [running]);
  const prepare = async () => {
    setBusy(true); setError(null);
    try { setSt(await apiPost('/api/satellites/usb-stick', body)); }
    catch (e) { setError(appOnServerMessage(e)); }
    finally { setBusy(false); }
  };
  const remove = async (id) => {
    try { setSt(await apiDelete(`/api/satellites/usb-stick/${id}`)); fire('the stick’s image is gone from this server'); }
    catch (e) { setError(appOnServerMessage(e)); }
  };
  const note = { fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 };
  const deb = st && st.debian;
  const builds = (st && st.builds) || [];
  const mine = builds.find(b => b.room_id === res.room_id) || null;
  const others = builds.filter(b => b.room_id !== res.room_id);
  const failed = !running && job.ok === false && job.message && job.room_id === res.room_id;
  const refusal = st && st.refusal && st.refusal.reason !== 'busy' ? st.refusal : null;
  const pct = job.bytes_todo ? Math.min(100, Math.round(100 * (job.bytes_done || 0) / job.bytes_todo)) : 0;
  const imagerLink = mine ? `rpi-imager://?repo=${encodeURIComponent(mine.os_list_url)}` : null;
  return (
    <div className="spare-usb-stick" style={{ display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border-soft)', paddingTop: 12 }}>
      <div className="spare-stick-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div className="label">old computer (usb stick)</div>
        <PreviewTag/>
      </div>
      <div style={note}>
        An old 64-bit PC or laptop, or an Intel Mac from 2012 to 2017, becomes this room: the stick
        installs a fresh system on it (Debian) and the satellite, with no terminal and no password. It
        <b> erases that computer’s disk</b>, and asks first, on the computer itself, naming the disk.
        Like the other spare devices, the stick is a preview: so far it has been tried in virtual
        machines; real computers are next.
      </div>
      <ul className="spare-stick-needs" style={{ ...note, margin: 0, paddingLeft: 18 }}>
        <li>A processor from about 2009 or newer (Intel Core i3/i5/i7 or later, AMD from about 2011). An older one is refused before anything is erased.</li>
        <li>A network cable from your router to that computer while it sets up (or a USB network adapter). Wi-Fi on the stick comes later.</li>
        <li>A USB stick of 2 GB or more (everything on it is replaced), and a disk of 8 GB or more in the computer.</li>
      </ul>
      {!st && !error && <div style={note}>asking the server about USB sticks…</div>}
      {refusal && !running && <div role="alert" className="spare-stick-refusal" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{refusal.message}</div>}
      {running ? (
        <div className="spare-stick-progress" style={note}>
          <div>{job.stage === 'download'
            ? `Downloading Debian ${deb ? deb.version : ''} from Debian: ${spareMb(job.bytes_done || 0)} of ${spareMb(job.bytes_todo)}…`
            : `Making the stick for ${job.room || 'the room'}…`}</div>
          <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.stage === 'download' ? pct : undefined}
               style={{ height: 4, borderRadius: 2, background: 'var(--border)', marginTop: 4, overflow: 'hidden' }}>
            <div style={{ width: job.stage === 'download' ? `${pct}%` : '100%', height: '100%', background: 'var(--brand)', opacity: job.stage === 'download' ? 1 : 0.5 }}/>
          </div>
        </div>
      ) : st && !refusal && (
        <div>
          <Button icon="usb" variant={mine ? 'secondary' : 'primary'} disabled={busy} onClick={prepare}>
            {busy ? 'starting…' : mine ? 'prepare it again' : 'Prepare the stick'}
          </Button>
          {deb && !deb.cached && (
            <span style={{ ...note, marginLeft: 8 }}>
              the first time, this server downloads Debian {deb.version} ({spareMb(deb.size)}) from Debian
            </span>
          )}
        </div>
      )}
      {failed && <div role="alert" className="spare-stick-error" style={{ fontSize: 12, color: 'var(--err-ink)' }}>Preparing the stick failed: {job.message}</div>}
      {error && <div role="alert" className="spare-stick-error" style={{ fontSize: 12, color: 'var(--err-ink)' }}>{error}</div>}
      {mine && (
        <div className="spare-stick-ready" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={note}>
            The stick for <b>{mine.room}</b> is ready ({spareMb(mine.size)}, made {spareStickWhen(mine.made)}).
            {' '}SHA-256 <span className="mono" style={{ wordBreak: 'break-all' }}>{mine.sha256.slice(0, 16)}…</span>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <a className="btn btn-primary" href={imagerLink} style={{ textDecoration: 'none' }}><Icon name="external-link" size={14}/>Open in Raspberry Pi Imager</a>
            <a className="btn btn-secondary" href={mine.manifest_url} download style={{ textDecoration: 'none' }}><Icon name="file-down" size={14}/>the Imager file</a>
            <a className="btn btn-secondary" href={mine.image_url} download style={{ textDecoration: 'none' }}><Icon name="download" size={14}/>download the image</a>
            <Button icon="trash-2" variant="ghost" onClick={() => remove(mine.id)}>remove it from this server</Button>
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
            Raspberry Pi Imager 2.0.3 or newer opens with this stick listed (“A computer”, then “Domovoi room
            stick”) and checks every byte while it writes; nothing is sent to Raspberry Pi for a list like this
            one. Don’t have it? <a href="https://www.raspberrypi.com/software/" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--fg-muted)' }}>Get Raspberry Pi Imager</a>,
            then double-click the Imager file. Or write the image with{' '}
            <a href="https://bztsrc.gitlab.io/usbimager/" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--fg-muted)' }}>USBImager</a>.
          </div>
          <ol className="spare-stick-steps" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <SpareStickStep n="1" icon="usb">Write it to a USB stick: in Raspberry Pi Imager choose “A computer”, “Domovoi room stick”, then your stick.</SpareStickStep>
            <SpareStickStep n="2" icon="power">Plug the stick into the old computer and switch it on, holding its boot-menu key (below); pick the USB stick.</SpareStickStep>
            <SpareStickStep n="3" icon="keyboard">It asks one question, naming the disk: type ERASE to make that computer “{mine.room}”. Anything else leaves it as it was.</SpareStickStep>
            <SpareStickStep n="4" icon="monitor">About 10 to 30 minutes later its screen shows a six-digit code in large digits, and says it if a speaker works: type it above, under waiting for approval.</SpareStickStep>
          </ol>
        </div>
      )}
      <details className="spare-stick-keys">
        <summary style={{ fontSize: 12, cursor: 'pointer' }}>boot-menu keys by maker</summary>
        <table className="tbl" style={{ marginTop: 6, fontSize: 11.5 }}>
          <tbody>{SPARE_STICK_BOOT_KEYS.map(([maker, key]) => <tr key={maker}><td>{maker}</td><td className="mono">{key}</td></tr>)}</tbody>
        </table>
        <div style={{ fontSize: 11.5, color: 'var(--fg-faint)', marginTop: 4 }}>Common keys; a model can differ. If none works, the computer’s firmware settings (often F2 or Del) can put USB first, or turn off “Fast Boot”.</div>
      </details>
      <details className="spare-stick-secure-boot">
        <summary style={{ fontSize: 12, cursor: 'pointer' }}>Secure Boot</summary>
        <div style={{ ...note, marginTop: 6, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div>The stick starts with Secure Boot on: its boot loader is Debian’s, signed by Microsoft with both
            its 2011 and its 2023 certificates{deb && deb.shim_signatures && deb.shim_signatures.length ? ` (${deb.shim_signatures.join(', ')})` : ''}.</div>
          <div>If the computer refuses it (“Access Denied”, “Secure Boot violation”), its firmware has
            Microsoft’s third-party certificate switched off (some Lenovo and Surface models since 2022): turn on
            “Allow Microsoft 3rd Party UEFI CA” in its firmware settings, or turn Secure Boot off.</div>
          <div style={{ color: 'var(--warn-ink)' }}>Some older PCs’ firmware only has Microsoft’s 2011 certificate,
            and Debian’s newer boot loaders are signed with the 2023 one only. So the room keeps the boot loader
            the stick installed (it is frozen, never updated), and this page warns on that room’s health card
            when its firmware lacks the 2023 certificate. To update the firmware by hand: install the maker’s
            latest BIOS/UEFI update for that model from their support site (do it before wiping, from Windows,
            if you can: Windows Update may also bring the certificate), or turn Secure Boot off.</div>
        </div>
      </details>
      <div className="spare-stick-power" style={{ fontSize: 11.5, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
        A room runs day and night. An old desktop tower draws about 30 to 80 W: $50 to $130 a year at US
        prices, more than a new Pi satellite costs. An old laptop (6 to 15 W, $10 to $25 a year) or a mini PC
        is the better room if you have the choice.
      </div>
      {others.length > 0 && (
        <div className="spare-stick-others" style={{ fontSize: 11.5, color: 'var(--fg-faint)', display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div>Other sticks on this server (about {spareMb(others[0].size)} each):</div>
          {others.map(b => (
            <div key={b.id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span>{b.room} · made {spareStickWhen(b.made)}</span>
              <Button icon="trash-2" variant="ghost" onClick={() => remove(b.id)}>remove</Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const SpareDeviceCard = ({ fire }) => {
  const admin = useAdminSignedIn();
  const [open, setOpen] = React.useState(false);
  const [room, setRoom] = React.useState('');
  const [core, setCore] = React.useState('');
  const [editCore, setEditCore] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [res, setRes] = React.useState(null);
  const [err, setErr] = React.useState(null);
  if (!admin) return null;

  const make = async () => {
    if (!room.trim() || busy) return;
    setBusy(true); setErr(null);
    try {
      const body = { room: room.trim() };
      if (editCore && core.trim()) body.core = core.trim();
      const r = await apiPost('/api/satellites/enrolment', body);
      setRes(r);
      if (!editCore) setCore(r.core);
    } catch (e) {
      setRes(null);
      setErr(apiErrorText(e, 0));
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(res.command);
      fire('install command copied');
    } catch (e) {
      fire('select the command and copy it by hand (the browser refused the clipboard)');
    }
  };

  return (
    <div className="spare-device-card"
         style={{ background: 'var(--card)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-md)', boxShadow: 'var(--inner-highlight)' }}>
      <button onClick={() => setOpen(o => !o)}
              style={{ font: 'inherit', width: '100%', textAlign: 'left', cursor: 'pointer',
                       background: 'none', border: 'none', padding: '14px 16px',
                       display: 'flex', alignItems: 'center', gap: 10, color: 'var(--fg)' }}>
        <Icon name="laptop" size={16}/>
        <span style={{ fontSize: 14, fontWeight: 600 }}>add a spare device</span>
        <PreviewTag/>
        <span style={{ flex: 1 }}/>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
          a laptop or PC (Linux, Windows, or an old one from a USB stick), or a phone
        </span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14}/>
      </button>
      {open && (
        <div style={{ padding: '0 16px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
            Name the room the device will be. It joins like a Pi: it waits above for its
            approval code, and nothing in this command or code is a password. Spare computers
            (old ones from a USB stick too) and phones as rooms are a preview: they work, but
            have been tried on fewer devices than a Pi.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input value={room} onChange={e => setRoom(e.target.value)} maxLength={64}
                   onKeyDown={e => { if (e.key === 'Enter') make(); }}
                   placeholder="room name, e.g. Kid's Room" aria-label="room name"
                   style={{ ...smInput, minWidth: 220 }}/>
            <Button variant="primary" icon="qr-code" disabled={!room.trim() || busy} onClick={make}>
              {busy ? 'making…' : 'make the code'}
            </Button>
            <label style={{ fontSize: 12, color: 'var(--fg-muted)', display: 'flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={editCore} onChange={e => setEditCore(e.target.checked)}/>
              a different server address
            </label>
            {editCore && (
              <input value={core} onChange={e => setCore(e.target.value)} className="mono"
                     placeholder="http://192.168.1.20:6370" aria-label="server address"
                     style={{ ...smInput, minWidth: 220 }}/>
            )}
          </div>
          {err && (
            <div role="alert" style={{ fontSize: 12, color: 'var(--err-ink)' }}>
              {err}{/home network/.test(err) && !editCore
                ? ' — tick “a different server address” and give its address on your network.' : ''}
            </div>
          )}
          {res && (
            <>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                It connects as <span className="mono">{res.room_id}</span> to{' '}
                <span className="mono">{res.core}</span>.
              </div>
              {res.conflict && <div role="alert" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{res.conflict}</div>}
              <div className="spare-device-body" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                <div style={{ flex: '1 1 320px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div className="label">linux laptop or mini pc</div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
                    On the device, signed in as the account it will run as, open a terminal and
                    paste this. It installs the satellite and shows the approval code.
                  </div>
                  <pre className="mono spare-command" tabIndex={0}
                       style={{ margin: 0, padding: 10, fontSize: 11.5, lineHeight: 1.45,
                                whiteSpace: 'pre-wrap', wordBreak: 'break-all', userSelect: 'all',
                                background: 'var(--bg-sunken, var(--bg))', border: '1px solid var(--border)',
                                borderRadius: 'var(--r-sm)', maxHeight: 160, overflow: 'auto' }}>{res.command}</pre>
                  <div><Button icon="copy" onClick={copy}>copy the command</Button></div>
                  <div style={{ fontSize: 11.5, color: 'var(--fg-faint)', lineHeight: 1.5 }}>
                    It runs the download only if it is exactly the bundle this server serves now
                    (SHA-256 <span className="mono">{res.bundle_sha256.slice(0, 16)}…</span>), then checks
                    the server is the one with fingerprint{' '}
                    <span className="mono" style={{ wordBreak: 'break-all' }}>{res.fingerprint}</span>{' '}
                    (Settings → About shows it) before it writes anything. Make a new command after
                    updating Domovoi.
                  </div>
                  <SpareLinuxPython res={res} fire={fire}/>
                </div>
                <div className="spare-phone" style={{ flex: '0 1 260px', minWidth: 216, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div className="label">phone or tablet (android 8 or newer)</div>
                  <SpareQr text={res.join_url || res.payload}/>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
                    Scan it with the phone's camera: it opens a page on this server that installs the
                    Domovoi app and starts it as this room. A phone that already has the app: open
                    Domovoi Room and scan it there.
                  </div>
                  <AppOnServer app={res.android_app}/>
                </div>
              </div>
              {res.command_windows && <SpareWindows res={res} fire={fire}/>}
              <SpareUsbStick res={res} fire={fire}
                             body={editCore && core.trim() ? { room: res.room, core: core.trim() } : { room: res.room }}/>
            </>
          )}
        </div>
      )}
    </div>
  );
};

/* The satellites view. `embedded` is how Settings → Rooms shows it
 * (SatellitesPanel): under the Settings header, with the roster's counts
 * as its first line. Without it, the same view as a page of its own, which
 * the shell no longer routes to (#satellites opens Settings → Rooms). Every
 * read, action and auth tier is the same either way. */
// How often the page asks whether the core answers (the web pings it).
const SAT_HEALTH_POLL_MS = 10000;

const SatellitesPage = ({ embedded = false } = {}) => {
  const [openRoom, setOpenRoom] = React.useState(null);
  const [tick, setTick] = React.useState(0);
  const [fire, toastNode] = useToast();

  const { items: sats, loading, refresh } = useApiList('/api/satellites', {
    eventTypes: ['satellites.presence.changed', 'satellites.wifi.changed', 'satellites.dropins.changed', 'satellites.display.changed', 'satellites.pending.changed', 'music.now_playing.changed'],
  });
  // Whether the server's core answers (the web's /api/health pings it):
  // while it doesn't, no satellite can connect, and the page says that
  // rather than showing every room "offline" (incident 2026-10-14).
  const { data: health, refresh: refreshHealth } = useApiObject('/api/health', { quiet: true });
  React.useEffect(() => {
    const t = setInterval(refreshHealth, SAT_HEALTH_POLL_MS);
    return () => clearInterval(t);
  }, [refreshHealth]);
  const coreDown = !!health && health.domovoi_reachable === false;
  // Why the core can't serve: its database (the web's own ping fails too),
  // or a core that runs but can't answer (its loop answers /v1/alive), or a
  // core that is stopped or frozen. Only the last one is the core's to fix
  // by restarting.
  const downWhy = !coreDown ? null
    : health.db_reachable === false ? 'db'
      : health.core_alive === true ? 'sick' : 'frozen';
  const wasDown = React.useRef(false);
  React.useEffect(() => {
    if (wasDown.current && !coreDown) refresh();   // back: the live state again
    wasDown.current = coreDown;
  }, [coreDown, refresh]);
  const { items: approvals, refresh: refreshApprovals } = useApiList('/api/satellites/approvals');
  const [approvalBusy, setApprovalBusy] = React.useState('');
  // {room_id, tone, text} — the refusal that belongs beside THAT room's
  // code box. One at a time: the operator is standing at one satellite.
  const [approvalError, setApprovalError] = React.useState(null);

  // The satellite retries on its own, so this list settles without a push
  // channel; poll while the page is open.
  React.useEffect(() => {
    const t = setInterval(refreshApprovals, 5000);
    return () => clearInterval(t);
  }, [refreshApprovals]);

  const decide = async (a, action, code) => {
    if (action === 'reject' && !window.confirm(
      `Reject ${a.room_id}?\n\nThe satellite keeps retrying until it is ` +
      `approved or powered off — this clears the request, it doesn't ban the device.`
    )) return;
    setApprovalBusy(a.room_id);
    setApprovalError(null);
    try {
      // The code rides with the approval: the server compares it before it
      // binds the room to that device, so an approve without one is refused.
      await apiPost(`/api/satellites/approvals/${a.room_id}/${action}`,
                    action === 'approve' ? { code } : {});
      fire(action === 'approve' ? `${a.room_id} approved` : `${a.room_id} rejected`);
      refreshApprovals();
    } catch (e) {
      // A refusal about the code stays on the card the operator is
      // looking at, in the server's own words (F-050). Everything else
      // is a page-level problem and keeps the toast — and a genuine
      // sign-in refusal says nothing here at all, because data.js has
      // already put the password modal on screen for it (F-006).
      const field = action === 'approve' ? approvalFieldError(e) : null;
      if (field) setApprovalError({ room_id: a.room_id, ...field });
      else {
        const said = mutationErrorText(e, action, { kept: false });
        if (said) fire(said);
      }
    } finally {
      setApprovalBusy('');
    }
  };

  const { items: pending } = useApiList('/api/satellites/pending', {
    eventTypes: ['satellites.pending.changed'],
  });
  // Inside WSL (a Windows install) the server can't see USB drives, so
  // nothing ever turns up under "plugged in"; the empty state points at
  // the Wi-Fi setup route instead of the USB port.
  const { data: caps } = useApiObject('/api/capabilities');
  const onWsl = caps?.host_kind === 'wsl';
  const [adoptTarget, setAdoptTarget] = React.useState(null);   // {pending, force}
  const [adoptedIds, setAdoptedIds] = React.useState({});       // pending_id → room

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
  const waitingCount = sats.filter(s => s.status === 'waiting').length;

  // Group cards under room-label headings once any label exists;
  // unlabeled satellites stay in a flat grid below.
  const labels = [...new Set(sats.map(s => s.room_label).filter(Boolean))].sort();
  const unlabeled = sats.filter(s => !s.room_label);
  const grid = (list) => (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14 }}>
      {list.map(s => <SatCard key={s.room_id} s={s} onOpen={(s) => setOpenRoom(s.room_id)} tick={tick} coreDown={coreDown}/>)}
    </div>
  );

  const summary = coreDown
    ? `the core isn’t answering · ${sats.length} known`
    : `${onlineCount} online · ${offlineCount} offline${waitingCount ? ` · ${waitingCount} waiting` : ''} · ${sats.length} known`;
  const body = (
    <>
      {coreDown && (
        <Card>
          <div className="sat-core-down" style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <StatusDot tone="err"/>
              <span style={{ fontSize: 14, fontWeight: 600 }}>
                {downWhy === 'db'
                  ? 'The database isn’t answering, so satellites can’t connect.'
                  : downWhy === 'sick'
                    ? 'The server’s core is running but can’t answer, so satellites can’t connect.'
                    : 'The server’s core isn’t answering, so satellites can’t connect.'}
              </span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
              {downWhy === 'db'
                ? 'The core needs it for every room. Restarting the core won’t help; the database has to come back first. '
                : downWhy === 'sick'
                  ? 'It is most likely waiting on its database. Restarting it won’t help while that lasts. '
                  : ''}
              The rooms below are as they were last heard from. They reconnect by themselves once the core is back.
            </div>
            {downWhy === 'frozen' && <CoreRestart onBack={() => { refreshHealth(); refresh(); }}/>}
          </div>
        </Card>
      )}
      {approvals.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="label">waiting for approval</div>
          {approvals.map(a => (
            <ApprovalCard key={a.room_id} a={a} busy={approvalBusy === a.room_id}
                          error={approvalError && approvalError.room_id === a.room_id
                                 ? approvalError : null}
                          onClearError={() => setApprovalError(null)}
                          onApprove={(x, code) => decide(x, 'approve', code)}
                          onReject={x => decide(x, 'reject')}/>
          ))}
        </div>
      )}

      {pending.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="label">plugged in — ready to adopt</div>
          {pending.map(p => (
            <PendingSatCard key={p.pending_id} p={p}
                            adopted={!!adoptedIds[p.pending_id]}
                            onAdopt={(pnd, force) => setAdoptTarget({ pending: pnd, force })}/>
          ))}
        </div>
      )}

      {loading && sats.length === 0 ? (
        <Card><div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading satellites…</div></Card>
      ) : sats.length === 0 ? (
        <Card><Empty glyph="sleeping" title="no satellites provisioned yet"
                     sub={onWsl
                       ? "build the card's files below, write them with the tray icon's card helper, then set the satellite up from a phone — no USB adoption while Domovoi runs inside WSL on Windows"
                       : "plug a satellite into this machine's USB port to adopt it — or provision one manually"}/></Card>
      ) : labels.length === 0 ? (
        grid(sats)
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {labels.map(label => (
            <div key={label}>
              <div className="label" style={{ marginBottom: 8 }}>{label}</div>
              {grid(sats.filter(s => s.room_label === label))}
            </div>
          ))}
          {unlabeled.length > 0 && (
            <div>
              {labels.length > 0 && <div className="label" style={{ marginBottom: 8 }}>ungrouped</div>}
              {grid(unlabeled)}
            </div>
          )}
        </div>
      )}

      <Broadcast onlineCount={onlineCount} fire={fire}/>

      <PrepareMediaCard fire={fire}/>

      <SpareDeviceCard fire={fire}/>

      <SatDrawer s={open} sats={sats} onClose={() => setOpenRoom(null)} fire={fire} refresh={refresh}/>
      {adoptTarget && (
        <AdoptModal pending={adoptTarget.pending} sats={sats} force={adoptTarget.force}
                    onClose={() => setAdoptTarget(null)}
                    onAdopted={(pendingId, room) => {
                      setAdoptedIds(a => ({ ...a, [pendingId]: room }));
                      setAdoptTarget(null);
                      refresh();
                    }}
                    fire={fire}/>
      )}
      {toastNode}
    </>
  );
  if (embedded) {
    return (
      <div className="sat-panel">
        <div className="sat-panel-summary mono">{summary}</div>
        {body}
      </div>
    );
  }
  return (
    <div className="page">
      <PageHeader title="Satellites" sub={summary}/>
      {body}
    </div>
  );
};

// Settings → Rooms (settings.jsx).
const SatellitesPanel = () => <SatellitesPage embedded/>;

window.SatellitesPage = SatellitesPage;
window.SatellitesPanel = SatellitesPanel;
// Home (home.jsx) shows the same rooms, timers and broadcast. It reads
// these from window instead of declaring its own: every page script
// shares one Babel scope, and a second top-level `const wifiTone` would
// be a SyntaxError that kills whichever file loads later.
Object.assign(window, { wifiTone, fmtRemaining, remainingFromExpiresAt, Broadcast });
