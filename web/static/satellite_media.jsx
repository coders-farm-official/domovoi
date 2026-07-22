/* Prepare-satellite-media card (Satellites page, below Broadcast).
 * Flow: flash stock OS with any tool → insert the card into THIS machine
 * (or pick the zip download) → prepare → boot the device → plug it into
 * this machine's USB → adopt. Build progress rides the satellites.media
 * realtime channel (satellite_media_jobs, V004). */

const smInput = {
  font: 'inherit', fontSize: 13, height: 32, padding: '0 8px',
  borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
  background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)',
};

const smBytes = (n) => {
  if (n == null) return '—';
  if (n > 1024 * 1024 * 1024) return `${(n / (1024 ** 3)).toFixed(1)} GiB`;
  if (n > 1024 * 1024) return `${(n / (1024 ** 2)).toFixed(0)} MiB`;
  return `${Math.max(1, Math.round(n / 1024))} KiB`;
};

const MediaJobRow = ({ j }) => {
  const live = j.status === 'running' || j.status === 'pending';
  return (
    <div style={{ padding: '10px 0', borderTop: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Pill tone={j.status === 'done' ? 'ok' : j.status === 'failed' ? 'err' : live ? 'live' : 'idle'} live={live}>
          {j.status}
        </Pill>
        <span className="mono" style={{ fontSize: 12 }}>
          {j.board} · {j.mic_profile} · {j.target_kind === 'zip' ? 'zip download' : `drive ${j.target_ref}:`}
        </span>
        <span style={{ flex: 1 }}/>
        {j.status === 'done' && j.has_artifact && (
          <a className="mono" style={{ fontSize: 12, color: 'var(--brand)' }}
             href={`${API_BASE}/api/satellites/media/jobs/${j.id}/download`}>
            download zip
          </a>
        )}
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{relTime(j.requested_at)}</span>
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
        <div className="mono" style={{ fontSize: 11, color: 'var(--err)', marginTop: 4 }}>{j.error}</div>
      )}
      {j.status === 'done' && j.target_kind === 'drive' && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 4 }}>
          eject the card, boot the device, then plug it into this machine to adopt
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
  const [busy, setBusy] = React.useState(false);
  const [open, setOpen] = React.useState(false);

  // Re-scan drives while the section is open (a just-inserted card should
  // appear without a manual refresh).
  React.useEffect(() => {
    if (!open) return;
    const t = setInterval(refreshTargets, 4000);
    return () => clearInterval(t);
  }, [open, refreshTargets]);

  const boards = status?.boards || [];
  const plugins = status?.plugins || [];
  const cache = status?.cache || {};
  const bootTargets = targets.filter(t => t.looks_like_pi_boot);

  const prepare = async () => {
    setBusy(true);
    try {
      const body = {
        board, mic_profile: mic,
        target: target === 'zip' ? { kind: 'zip' } : { kind: 'drive', token: target },
        offline: true,
      };
      const r = await apiPost('/api/satellites/media/prepare', body);
      fire(r.attached ? 'attached to the running build' : 'build started');
    } catch (e) {
      fire(`prepare failed: ${e.message}`);
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
      fire(`cache refresh failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: 16, background: 'var(--card)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-md)', boxShadow: 'var(--inner-highlight)' }}>
      <button onClick={() => setOpen(o => !o)}
              style={{ font: 'inherit', width: '100%', textAlign: 'left', cursor: 'pointer',
                       background: 'none', border: 'none', padding: '14px 16px',
                       display: 'flex', alignItems: 'center', gap: 10 }}>
        <Icon name="disc-3" size={16}/>
        <span style={{ fontSize: 14, fontWeight: 600, flex: 1 }}>prepare satellite media</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
          flash stock OS · prepare here · boot · plug in to adopt
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
              {(status?.mic_profiles || []).map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <select value={target} onChange={e => setTarget(e.target.value)} style={smInput}>
              <option value="zip">download overlay zip</option>
              {bootTargets.map(t => (
                <option key={t.token} value={t.token}>
                  write to {t.token}: {t.label ? `(${t.label})` : ''} · {smBytes(t.free_bytes)} free
                </option>
              ))}
            </select>
            <Button variant="primary" icon="hammer" disabled={busy} onClick={prepare}>Prepare</Button>
            <Button icon="refresh-cw" disabled={busy} onClick={refreshCache}>Refresh caches</Button>
          </div>

          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 8,
                                         display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {['wheels', 'debs', 'dtbo', 'oww_models'].map(k => (
              <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <StatusDot tone={cache[k]?.ok ? 'ok' : 'idle'}/>
                {k} {cache[k]?.ok ? `(${cache[k].files})` : '(empty)'}
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
                          style={{ color: 'var(--warn)', display: 'inline-flex',
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
              {jobs.slice(0, 5).map(j => <MediaJobRow key={j.id} j={j}/>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

window.PrepareMediaCard = PrepareMediaCard;
