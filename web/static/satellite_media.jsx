/* Prepare-satellite-media card (Settings → Rooms, below Broadcast).
 * Flow (portal — the shipped path): flash stock OS, prepare here, boot the
 * device; it raises its own `Domovoi-Setup-<id>` network, the customer sets
 * it up from a phone and approves it on the dashboard. USB adoption instead
 * has the device present itself as a drive to this machine. The job row
 * doesn't record which transport built it, so its completion note stays
 * neutral between the two.
 *
 * Legacy note: flash stock OS with any tool → insert the card into THIS machine
 * (or pick the zip download) → prepare → boot the device → plug it into
 * this machine's USB → adopt. Build progress rides the satellites.media
 * realtime channel (satellite_media_jobs, V004).
 *
 * Inside WSL (a Windows install) the server sees no removable drives, so
 * /status answers drive_targets: false: the card drops the USB transport
 * and the drive targets, stops polling for cards, and leads with the
 * Windows tray icon's card helper (windows/tray, "Prepare a satellite
 * card"), which writes the zip onto the card and fetches the setup
 * network's password itself from this server (the zip's one-time pass,
 * POST /api/satellites/media/claim); the person still reads it here for
 * their phone. The zip's README keeps the steps by hand.
 * The tray links here as #settings/rooms/media, which opens this
 * card. */

const smInput = {
  font: 'inherit', fontSize: 13, height: 32, padding: '0 8px',
  borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
  background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)',
};

/* The mic boards in household words; the value sent stays the id. */
const SM_MIC_LABELS = {
  respeaker_2mic_hat_v2: 'ReSpeaker 2-Mics HAT v2',
  respeaker_2mic_hat_v1: 'ReSpeaker 2-Mics HAT v1',
  xvf3800_usb: 'ReSpeaker XVF3800 (USB, 4 mics)',
  none: 'no microphone yet',
};
const smMicLabel = (id) => SM_MIC_LABELS[id] || id;

/* A HAT needs its device-tree overlay on the Pi. Stage 1 copies a prebuilt
 * one from the payload's dtbo cache when the cache has one, and otherwise
 * only warns (firstrun.sh, step 6): nothing builds it for you. */
const smNeedsOverlay = (id) => /^respeaker_2mic_hat/.test(id || '');
const SM_OVERLAY_DOC_URL = 'https://github.com/coders-farm-official/domovoi/blob/main/satellite/PROVISIONING.md#4-respeaker-driver--device-tree-overlay';

const smBytes = (n) => {
  if (n == null) return '—';
  if (n > 1024 * 1024 * 1024) return `${(n / (1024 ** 3)).toFixed(1)} GiB`;
  if (n > 1024 * 1024) return `${(n / (1024 ** 2)).toFixed(0)} MiB`;
  return `${Math.max(1, Math.round(n / 1024))} KiB`;
};

/* The cache row under Prepare, one entry per bucket: "(empty)" only where
 * "Refresh caches" fills it. The wheels and the wake-word models are what
 * it downloads; the debs need Docker (without it the refresh skips them,
 * and the row says so). The mic-board overlays have no fetcher at all
 * (domovoi/satellite_media/fetchers.py): the XVF3800 needs none, and with
 * no prebuilt one here a 2-Mics HAT card leaves it to the Pi, whose first
 * boot points at the by-hand compile (satellite/PROVISIONING.md §4). That
 * row is a fact, not a status: no dot (tone null). */
const smCacheRows = (cache, dockerAvailable) => {
  const files = (k) => (cache[k] && cache[k].ok ? cache[k].files : 0);
  const bucket = (k, emptyText) => (files(k)
    ? { key: k, tone: 'ok', text: `${k} (${files(k)})` }
    : { key: k, tone: 'idle', text: emptyText || `${k} (empty)` });
  return [
    bucket('wheels'),
    bucket('debs', dockerAvailable === false ? 'debs (needs docker)' : null),
    files('dtbo')
      ? { key: 'dtbo', tone: 'ok', text: `mic-board overlay (${files('dtbo')})` }
      : { key: 'dtbo', tone: null,
          text: 'mic-board overlay: XVF3800 needs none · 2-Mics HAT: built on the Pi by hand (PROVISIONING §4)' },
    bucket('oww_models'),
  ];
};

/* ---- Setup credentials -----------------------------------------------
 *
 * What the person holding the card needs in order to connect to the
 * satellite they just prepared: the setup network's key, and the console
 * login.
 *
 * For a card written straight to a DRIVE both are also on the card
 * (domovoi/ap.json and domovoi/console.json) — stage 1 reads the first,
 * and anyone holding the card can read either. The downloadable ZIP
 * carries NEITHER (WEB-1): an artifact that sits on the server and is
 * fetched over HTTP is the wrong place for a plaintext password, so this
 * modal is the only place they appear for a zip build. Its copy says so.
 *
 * They live in the web process's memory and nowhere else, so a restart
 * loses them.
 */
const CredentialsModal = ({ creds, zip, cardHelper, onClose }) => {
  const ap = creds.ap;
  const con = creds.console;
  const row = (label, value) => (
    <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: 10,
                  alignItems: 'baseline', marginBottom: 8 }}>
      <div className="label">{label}</div>
      <div className="mono" style={{ userSelect: 'all', fontSize: 14 }}>{value}</div>
    </div>
  );
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex',
                  alignItems: 'center', justifyContent: 'center',
                  background: 'oklch(0.1 0 0 / 0.5)' }}
         onClick={onClose}>
      <div onClick={e => e.stopPropagation()}
           style={{ background: 'var(--card)', border: '1px solid var(--border)',
                    borderRadius: 'var(--r-lg)', boxShadow: 'var(--shadow-md)',
                    padding: '20px 22px', width: 520, maxWidth: '92vw' }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
          setup details for this card
        </div>
        <div style={{ fontSize: 13, color: 'var(--fg-muted)', marginBottom: 18 }}>
          {ap
            ? 'This satellite will create its own Wi-Fi network. Connect to it with this password and the setup page should open by itself — if it does not, browse to the address below.'
            : 'This card is adopted over USB, so it has no setup network.'}
        </div>

        {ap && row('wi-fi network', ap.ssid)}
        {ap && row('wi-fi password', ap.psk)}
        {ap && row('setup page', 'http://192.168.4.1')}
        {con && row('console login', `${con.username} / ${con.password}`)}

        <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 16,
                      paddingTop: 14, borderTop: '1px solid var(--border-soft)' }}>
          {zip && cardHelper ? (
            <>
              {ap && (
                <>The desktop app (Domovoi's tray icon → <b>Prepare a satellite
                  card</b>) fills in the wi-fi password by itself when it
                  writes the card: you don't type it there. You still need it
                  on your phone, to join the setup network, so keep this open or
                  write it down.{' '}</>
              )}
              {creds.reissued
                ? 'These were made again after Domovoi restarted, when the tray icon signed in as admin to write the card: the ones shown before no longer apply to this card. '
                : 'This is the only place it is shown. '}
              The downloaded zip contains no passwords, and these are held in
              memory here until the dashboard restarts.
            </>
          ) : zip ? (
            <>
              Write these down now — this is the only place they are shown. The
              downloaded zip deliberately contains no passwords, and they are held
              in memory here until the dashboard restarts.
              {ap && (
                <> To use the setup network, create{' '}
                  <span className="mono">domovoi/ap.json</span> on the card's boot
                  partition with this password (the zip's README has the exact
                  line).</>
              )}
            </>
          ) : (
            <>
              Write these down now. They are shown from memory and disappear when the
              dashboard restarts — after that, read <span className="mono">domovoi/ap.json</span> and{' '}
              <span className="mono">domovoi/console.json</span> from the card itself.
            </>
          )}
        </div>
        <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
          <Button variant="primary" onClick={onClose}>done</Button>
        </div>
      </div>
    </div>
  );
};

const MediaJobRow = ({ j, onShowCredentials, cardHelper }) => {
  const live = j.status === 'running' || j.status === 'pending';
  return (
    <div style={{ padding: '10px 0', borderTop: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Pill tone={j.status === 'done' ? 'ok' : j.status === 'failed' ? 'err' : live ? 'live' : 'idle'} live={live}>
          {j.status}
        </Pill>
        <span className="mono" style={{ fontSize: 12 }}>
          #{j.id} · {j.board} · {j.mic_profile} · {j.target_kind === 'zip' ? 'zip download' : `drive ${j.target_ref}:`}
        </span>
        <span style={{ flex: 1 }}/>
        {j.status === 'done' && j.has_artifact && (
          <a className="mono" style={{ fontSize: 12, color: 'var(--brand-ink)' }}
             href={`${API_BASE}/api/satellites/media/jobs/${j.id}/download`}>
            {cardHelper ? 'download for the card helper' : 'download zip'}
          </a>
        )}
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}
              title={j.requested_at ? new Date(j.requested_at).toLocaleString() : undefined}>{relTime(j.requested_at)}</span>
      </div>
      {live && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
          <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--sunken)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${j.pct || 0}%`, background: 'var(--brand)' }}/>
          </div>
          <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{j.status_text || j.phase || '…'}</span>
        </div>
      )}
      {j.status === 'failed' && j.error && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--err-ink)', marginTop: 4 }}>{j.error}</div>
      )}
      {j.status === 'done' && j.target_kind === 'zip' && cardHelper && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 4 }}>
          then Domovoi's tray icon → Prepare a satellite card
        </div>
      )}
      {j.status === 'done' && j.target_kind === 'drive' && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 4 }}>
          eject the card, then boot the device to finish setup
        </div>
      )}
      {/* Zip builds too: their passwords are in no file at all (WEB-1), so
          this is the only place a portal card's setup key can be read —
          and inside WSL the zip is the only way to build a card. */}
      {/* Held in the server's memory only, so a restart clears them: then
          the row says so rather than offering a button that opens nothing
          (owner rule: no misleading controls). A server too old to say
          (no has_credentials) keeps the button. */}
      {j.status === 'done' && onShowCredentials && j.has_credentials !== false && (
        <div style={{ marginTop: 6 }}>
          <Button variant="ghost" icon="key" onClick={() => onShowCredentials(j)}>
            show setup details
          </Button>
        </div>
      )}
      {j.status === 'done' && j.has_credentials === false && (
        <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6 }}>
          {j.target_kind === 'zip'
            ? 'Setup details were cleared when Domovoi restarted. Write this card with the tray icon (it can sign in as admin to make a new password).'
            : 'Setup details were cleared when Domovoi restarted. The card has them in domovoi/ap.json and domovoi/console.json.'}
        </div>
      )}
    </div>
  );
};

const PrepareMediaCard = ({ fire }) => {
  const { data: status, refresh: refreshStatus } = useApiObject('/api/satellites/media/status');
  const { items: targets, refresh: refreshTargets } = useApiList('/api/satellites/media/targets');
  const { items: jobs } = useApiList('/api/satellites/media/jobs', {
    eventTypes: ['satellites.media.changed'],
  });
  const [board, setBoard] = React.useState('pi02w');
  const [mic, setMic] = React.useState('respeaker_2mic_hat_v2');
  const [target, setTarget] = React.useState('zip');
  const [transport, setTransport] = React.useState('portal');
  // Remembered per browser: a shop building batches for one market shouldn't
  // retype it, but it is never defaulted silently — the legal channel set
  // differs by country and a wrong domain is a compliance problem.
  const [country, setCountry] = React.useState(() => {
    try { return localStorage.getItem('domovoi-wifi-country') || 'US'; }
    catch (e) { return 'US'; }
  });
  const [busy, setBusy] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const [creds, setCreds] = React.useState(null);
  // The Windows tray icon's card helper links to #settings/rooms/media:
  // open this card and bring it into view.
  const cardRef = React.useRef(null);
  const linkedRef = React.useRef(false);
  React.useEffect(() => {
    const parts = routeParts(currentRoute());
    // Settings → Rooms since 2026-10-21; the tray's old words still land here.
    if (parts[0] !== 'settings' || !['rooms', 'satellites'].includes(parts[1]) || parts[2] !== 'media') return;
    linkedRef.current = true;
    setOpen(true);
  }, []);
  // Refreshing the caches downloads (PyPI, Docker Hub, Debian, GitHub):
  // greyed while the box is set to stay off the internet. Prepare uses
  // what is already cached and stays available.
  // The internet answer, read once for the page (the GET /api/config the
  // dashboard's shell reads too) and handed down as a prop; one read per
  // page, never one per row.
  const internetCfg = useApiObject('/api/config', { quiet: true });
  const internetOff = !!(internetCfg.data && internetCfg.data.internet_access === 'never');

  const showCredentials = async (job) => {
    try {
      const c = await apiGet(`/api/satellites/media/jobs/${job.id}/credentials`);
      // A zip build's passwords exist nowhere else, so the modal says so.
      setCreds({ ...c, zip: job.target_kind === 'zip' });
    } catch (e) {
      // 404 is the normal case after a dashboard restart, not a fault. A
      // card written from here still carries the files; a zip never did.
      fire(e.status !== 404
        ? `couldn't load setup details: ${e.message}`
        : job.target_kind === 'zip'
          ? 'setup details are no longer in memory, and the zip never had them — prepare the zip again'
          : 'setup details are no longer in memory — read domovoi/ap.json from the card');
    }
  };

  // False only inside WSL, where no drive ever appears. Unknown (status
  // still loading, or an older server) counts as visible, which is how
  // the card has always behaved.
  const drivesVisible = status?.drive_targets !== false;

  // Re-scan drives while the section is open (a just-inserted card should
  // appear without a manual refresh).
  React.useEffect(() => {
    if (!open || !drivesVisible) return;
    const t = setInterval(refreshTargets, 4000);
    return () => clearInterval(t);
  }, [open, drivesVisible, refreshTargets]);

  // Nothing but the portal can finish setup here; don't send a stale pick.
  React.useEffect(() => {
    if (!drivesVisible) { setTransport('portal'); setTarget('zip'); }
  }, [drivesVisible]);

  // Scrolled to once the page above it has its height (the satellites and
  // this card's status loaded), so it lands in view.
  React.useEffect(() => {
    if (!linkedRef.current || !status) return;
    linkedRef.current = false;
    const t = setTimeout(() => {
      try { if (cardRef.current) cardRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
      catch (e) { /* an old browser: the card is open anyway */ }
    }, 400);
    return () => clearTimeout(t);
  }, [status]);

  const boards = status?.boards || [];
  const plugins = status?.plugins || [];
  const cache = status?.cache || {};
  const bootTargets = targets.filter(t => t.looks_like_pi_boot);

  const prepare = async () => {
    setBusy(true);
    try {
      const body = {
        board, mic_profile: mic,
        setup_transport: transport,
        wifi_country: country,
        target: target === 'zip' ? { kind: 'zip' } : { kind: 'drive', token: target },
        offline: true,
      };
      if (!/^[A-Z]{2}$/.test(country)) {
        fire('set a two-letter wi-fi country first — a blocked radio can never be set up');
        return;
      }
      const r = await apiPost('/api/satellites/media/prepare', body);
      fire(r.attached ? 'attached to the running build' : 'build started');
    } catch (e) {
      reportMutationFailure(fire, 'prepare', e);
    } finally {
      setBusy(false);
    }
  };

  const refreshCache = async () => {
    setBusy(true);
    fire('refreshing artifact caches — the wheel fetch can take minutes');
    try {
      const r = await apiPost('/api/satellites/media/cache/refresh', {});
      const bad = Object.entries(r).filter(([, v]) => !v.ok);
      fire(bad.length ? `cache refresh: ${bad.map(([k, v]) => `${k}: ${v.message}`).join(' · ')}` : 'caches refreshed');
      refreshStatus();
    } catch (e) {
      if (isInternetOffError(e)) fire(INTERNET_OFF_MESSAGE);
      else reportMutationFailure(fire, 'cache refresh', e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={cardRef}
         style={{ background: 'var(--card)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-md)', boxShadow: 'var(--inner-highlight)' }}>
      <button onClick={() => setOpen(o => !o)}
              style={{ font: 'inherit', width: '100%', textAlign: 'left', cursor: 'pointer',
                       background: 'none', border: 'none', padding: '14px 16px',
                       display: 'flex', alignItems: 'center', gap: 10 }}>
        <Icon name="disc-3" size={16}/>
        <span style={{ fontSize: 14, fontWeight: 600, flex: 1 }}>prepare satellite media</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
          {!drivesVisible
            ? 'flash stock OS · build here · tray icon writes the card · boot · set up from a phone'
            : transport === 'portal'
              ? 'flash stock OS · prepare here · boot · set up from a phone'
              : 'flash stock OS · prepare here · boot · plug in to adopt'}
        </span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14}/>
      </button>

      {open && (
        <div style={{ padding: '0 16px 14px' }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <select value={board} onChange={e => setBoard(e.target.value)} style={smInput}>
              {boards.map(b => (
                <option key={b.id} value={b.id} disabled={!b.supported}>
                  {b.label}{b.supported ? '' : ' (manual setup only)'}
                </option>
              ))}
            </select>
            <select value={mic} onChange={e => setMic(e.target.value)} style={smInput}>
              {(status?.mic_profiles || []).map(m => <option key={m} value={m}>{smMicLabel(m)}</option>)}
            </select>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
                            color: 'var(--fg-muted)' }}>
              wi-fi country
              <input value={country} maxLength={2} size={2} style={smInput}
                     onChange={e => {
                       const v = e.target.value.toUpperCase().replace(/[^A-Z]/g, '');
                       setCountry(v);
                       try { localStorage.setItem('domovoi-wifi-country', v); }
                       catch (err) { /* private window — session only */ }
                     }}/>
            </label>
            {!drivesVisible && (
              <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}
                    title="this server can't see SD cards, so the card is set up from a phone over its own Wi-Fi">
                wi-fi setup portal
              </span>
            )}
            {drivesVisible && (
              <select value={transport} onChange={e => setTransport(e.target.value)} style={smInput}>
                <option value="portal">wi-fi setup portal</option>
                <option value="usb">usb adoption (plug into this server)</option>
              </select>
            )}
            <select value={target} onChange={e => setTarget(e.target.value)} style={smInput}>
              <option value="zip">{drivesVisible ? 'download overlay zip' : 'download for the card helper'}</option>
              {bootTargets.map(t => (
                <option key={t.token} value={t.token}>
                  write to {t.token}: {t.label ? `(${t.label})` : ''} · {smBytes(t.free_bytes)} free
                </option>
              ))}
            </select>
            <Button variant="primary" icon="hammer" disabled={busy} onClick={prepare}>Prepare</Button>
            <Button icon="refresh-cw" disabled={busy || internetOff} onClick={refreshCache}
                    title={internetOff ? NEEDS_INTERNET_TEXT : undefined}>Refresh caches</Button>
            {internetOff && <NeedsInternetNote compact/>}
          </div>

          {smNeedsOverlay(mic) && !cache.dtbo?.ok && (
            <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 6 }}>
              {smMicLabel(mic)} needs a one-time overlay step on the Pi:{' '}
              <a href={SM_OVERLAY_DOC_URL} target="_blank" rel="noopener noreferrer"
                 style={{ color: 'var(--brand-ink)' }}>PROVISIONING §4</a>
            </div>
          )}

          {!drivesVisible && (
            <div style={{ marginTop: 10, padding: '8px 10px', display: 'flex', gap: 8,
                          alignItems: 'flex-start', borderRadius: 'var(--r-sm)',
                          background: 'var(--sunken)' }}>
              <Icon name="info" size={13}/>
              <span style={{ fontSize: 11, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
                Domovoi is running inside WSL on Windows, which can't see SD
                cards. Build the card files here, then use Domovoi's tray icon
                (beside the clock) → <b>Prepare a satellite card</b>: it writes
                them onto the card and fills in the setup network password by
                itself. You'll still need that password for your phone: it's
                shown here when the build finishes ("show setup details").
                Then set the satellite up from your phone over its own Wi-Fi.
                By hand instead: the zip's README.txt has the steps.
              </span>
            </div>
          )}

          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 8,
                                         display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {smCacheRows(cache, status?.docker_available).map(r => (
              <span key={r.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                {r.tone ? <StatusDot tone={r.tone}/> : <Icon name="info" size={11}/>}
                {r.text}
              </span>
            ))}
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <StatusDot tone={status?.docker_available ? 'ok' : 'warn'}/>
              docker {status?.docker_available ? 'available' : 'unavailable — deb cache skipped'}
            </span>
          </div>

          {plugins.length > 0 && (
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 6 }}>
              plugin payloads: {plugins.map(p => (
                <span key={p.slug} style={{ marginRight: 10 }}>
                  {p.slug} {smBytes(p.payload_bytes)}
                  {p.satellite_root && (
                    <span title="this plugin runs root steps on satellites"
                          style={{ color: 'var(--warn-ink)', display: 'inline-flex',
                                   alignItems: 'center', gap: 2 }}>
                      {' '}<Icon name="shield-alert" size={10}/> root
                    </span>
                  )}
                </span>
              ))}
            </div>
          )}

          {jobs.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {jobs.slice(0, 5).map(j => (
                <MediaJobRow key={j.id} j={j} onShowCredentials={showCredentials}
                             cardHelper={!drivesVisible}/>
              ))}
            </div>
          )}
        </div>
      )}
      {creds && (
        <CredentialsModal creds={creds} zip={!!creds.zip} cardHelper={!drivesVisible}
                          onClose={() => setCreds(null)}/>
      )}
    </div>
  );
};

window.PrepareMediaCard = PrepareMediaCard;
