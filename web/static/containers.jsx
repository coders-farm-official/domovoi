/* Containers — the container guard's details, a part of Settings →
 * System → Health since 2026-10-21 (health.jsx; the old #containers
 * address lands on #settings/health/containers).
 *
 * Reached from the banner every page shows while an incident is open,
 * from Home's problems row and from a phone's notification. Health shows
 * the open incidents at its top (CtrOpenPart) and the rest in its
 * Containers part (CtrContainersPart): the last day's history and, folded
 * under "behind the scenes", what the guard watches and the music player
 * image's rebuild. What it shows depends on who is looking
 * (docs/CONTAINER_GUARD.md):
 *
 *   * no household credential: only that something needs an admin;
 *   * a paired device: every open incident and the last day's history in
 *     plain words, and what the freeze is holding;
 *   * an admin: the same with what was expected and found, acknowledge
 *     (a failure) and clear-after-review (tampering), and the guard's last
 *     check — what it watches, what Docker showed, what it holds and why.
 *
 * Every name here is prefixed (Ctr…) — all page scripts share one global
 * scope (index.html), so a bare helper name would collide.
 */

const CTR_FREEZE_LIST = [
  'plugin installs, upgrades and enabling a plugin',
  'satellite code upgrades and the plugin payloads they carry',
  'satellite media preparation',
  'pulling and applying Domovoi updates',
];

const CtrKindWords = {
  missing: 'missing', stopped: 'stopped', restarting: 'restarting', unhealthy: 'unhealthy',
  oneshot_failed: 'failed', daemon: 'docker down', image: 'different image',
  unknown: 'unknown container', config: 'risky settings', outdated: 'older image',
};

const ctrTone = (inc) => {
  if (!inc.open) return 'idle';
  if (inc.class === 'tamper' || inc.severity === 'error') return 'err';
  return 'warn';
};

const CtrDetails = ({ title, value }) => {
  if (!value || typeof value !== 'object' || Object.keys(value).length === 0) return null;
  const rows = Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!rows.length) return null;
  return (
    <div className="ctr-details">
      <div className="ctr-details-title mono">{title}</div>
      {rows.map(([k, v]) => (
        <div key={k} className="ctr-details-row">
          <span className="mono ctr-details-key">{k}</span>
          <span className="mono ctr-details-val">{Array.isArray(v) ? (v.join(', ') || '—') : (typeof v === 'object' ? JSON.stringify(v) : String(v))}</span>
        </div>
      ))}
    </div>
  );
};

const CtrIncident = ({ inc, admin, onChanged, fire }) => {
  const [note, setNote] = React.useState('');
  const [clearing, setClearing] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const act = async (verb, path, body) => {
    setBusy(true);
    try {
      await apiPost(path, body || {});
      fire(verb === 'clear' ? 'cleared' : 'acknowledged');
      setClearing(false);
      await onChanged();
    } catch (e) {
      reportMutationFailure(fire, verb, e, { kept: verb === 'clear' });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`ctr-incident ${inc.open ? '' : 'ctr-incident-closed'}`} data-incident={inc.id}>
      <div className="ctr-incident-head">
        <StatusDot tone={ctrTone(inc)} live={inc.open}/>
        <span className="ctr-incident-summary">{inc.summary}</span>
      </div>
      <div className="ctr-incident-meta mono">
        <span>#{inc.id}</span>
        <span>{inc.class === 'tamper' ? 'tampering' : 'failure'}</span>
        <span>{CtrKindWords[inc.kind] || inc.kind}</span>
        {inc.container && <span>{inc.container}</span>}
        <span>opened {relTime(inc.opened_at)}</span>
        {inc.open && inc.last_seen_at && <span>last seen {relTime(inc.last_seen_at)}</span>}
        {inc.acknowledged && inc.open && <span>acknowledged</span>}
        {!inc.open && <span>{inc.resolution === 'cleared' ? 'cleared' : 'resolved'} {relTime(inc.resolved_at)}</span>}
        {inc.freezes && <span className="ctr-freezes">pauses risky changes</span>}
      </div>
      {admin && (
        <div className="ctr-incident-body">
          <CtrDetails title="expected" value={inc.expected}/>
          <CtrDetails title="found" value={inc.found}/>
          {(inc.acknowledged_by || inc.cleared_by) && (
            <div className="ctr-incident-who mono">
              {inc.acknowledged_by && <span>acknowledged by {inc.acknowledged_by}</span>}
              {inc.cleared_by && <span>cleared by {inc.cleared_by}{inc.clear_note ? ` · ${inc.clear_note}` : ''}</span>}
            </div>
          )}
        </div>
      )}
      {admin && inc.can_acknowledge && (
        <div className="ctr-incident-actions">
          <Button disabled={busy} onClick={() => act('acknowledge', `/api/containers/incidents/${inc.id}/acknowledge`)}>
            acknowledge
          </Button>
          <span className="ctr-hint">hides the banner until something new happens; it still resolves by itself</span>
        </div>
      )}
      {admin && inc.can_clear && !clearing && (
        <div className="ctr-incident-actions">
          <Button variant="primary" disabled={busy} onClick={() => setClearing(true)}>clear after review</Button>
          <span className="ctr-hint">risky changes stay paused until every tampering incident is cleared</span>
        </div>
      )}
      {admin && inc.can_clear && clearing && (
        <div className="ctr-clear">
          <div className="ctr-hint">
            Clear this only once you know why it happened: you swapped the image yourself, or
            you removed what shouldn't be there. If it is still there it stays accepted;
            anything different opens a new incident.
          </div>
          <input maxLength={500} placeholder="what you checked (optional)"
                 value={note} onChange={(e) => setNote(e.target.value)}/>
          <div className="ctr-incident-actions">
            <Button variant="primary" disabled={busy}
                    onClick={() => act('clear', `/api/containers/incidents/${inc.id}/clear`, { note: note.trim() || null })}>
              {busy ? 'clearing…' : 'clear'}
            </Button>
            <Button disabled={busy} onClick={() => setClearing(false)}>cancel</Button>
          </div>
        </div>
      )}
    </div>
  );
};

// "Rebuild the music player image": the core builds it again from its own
// recipe, the guard takes it as the image it expects, and every room's
// player is recreated on it (the way an update does). Shown only where it
// can run (status.mpd_rebuild); a tampering incident stays for the admin.
const CtrMpdRebuild = ({ rebuild, onRebuild, rebuilding }) => {
  const [asking, setAsking] = React.useState(false);
  if (!rebuild) return null;
  if (!rebuild.available) {
    return <div className="ctr-hint ctr-pad">music player image: can't be rebuilt from here · {rebuild.why}</div>;
  }
  if (!asking) {
    return (
      <div className="ctr-incident-actions ctr-pad">
        <Button disabled={rebuilding} onClick={() => setAsking(true)}>
          {rebuilding ? 'rebuilding the music player image…' : 'rebuild the music player image'}
        </Button>
      </div>
    );
  }
  return (
    <div className="ctr-clear ctr-pad">
      <div className="ctr-hint">
        Builds the music player image again on this box from Domovoi's own recipe (it downloads
        Debian's packages, a minute or so) and makes it the image the guard expects. Then every
        room's music player restarts on it, the way an update does: what is playing stops, and
        playlists and queues are kept. A tampering incident about the image stays open until you
        clear it.
      </div>
      <div className="ctr-incident-actions">
        <Button variant="primary" disabled={rebuilding}
                onClick={async () => { await onRebuild(); setAsking(false); }}>
          {rebuilding ? 'rebuilding…' : 'rebuild'}
        </Button>
        <Button disabled={rebuilding} onClick={() => setAsking(false)}>cancel</Button>
      </div>
    </div>
  );
};

const CtrStatus = ({ status, onCheck, checking, onRebuild, rebuilding }) => {
  const g = status && status.guard;
  if (!g) return null;
  const watched = Array.isArray(g.watched) ? g.watched : [];
  const holdsList = Object.entries(g.holds || {});
  const dockerWords = g.docker && g.docker.cli_missing ? 'docker is not installed'
    : (g.docker && g.docker.ok === false) ? `docker isn't answering${g.docker.error ? ` · ${g.docker.error}` : ''}`
      : 'docker answering';
  return (
    <Card title="what the guard watches"
          sub={g.checked_at ? `last check ${relTime(new Date(g.checked_at * 1000).toISOString())} · every ${Math.round(g.interval_sec || 30)}s · ${dockerWords}` : 'no check yet'}
          action={<Button onClick={onCheck} disabled={checking}>{checking ? 'checking…' : 'check now'}</Button>}>
      {g.hold && <div className="ctr-hint ctr-pad">waiting: {g.hold}</div>}
      {g.state === 'disabled' && <div className="ctr-hint ctr-pad">the container guard is turned off (CONTAINER_GUARD_ENABLED)</div>}
      <div className="ctr-watch">
        {watched.map((w) => {
          const o = w.observed || {};
          const tone = w.hold ? 'idle' : (o.status === 'running' ? 'ok'
            : (w.state === 'running' ? 'err' : 'idle'));
          return (
            <div key={w.name} className="ctr-watch-row">
              <StatusDot tone={tone}/>
              <span className="mono ctr-watch-name">{w.name}</span>
              <span className="ctr-watch-label">{w.label}</span>
              <span className="mono ctr-watch-meta">
                {w.state === 'running' ? 'must run' : w.state === 'oneshot' ? 'one-shot' : w.state === 'stopped' ? 'stopped on purpose' : 'known'}
                {' · '}{o.status || '—'}
                {' · '}{w.pin === 'digest' ? 'pinned by digest' : w.pin === 'id' ? 'built here'
                  : w.pin === 'repository' ? 'its repository' : w.pin === 'tag' ? 'tag only' : 'no image'}
                {w.hold ? ` · waiting: ${w.hold}` : ''}
              </span>
            </div>
          );
        })}
        {watched.length === 0 && <div className="ctr-hint ctr-pad">nothing to watch on this box</div>}
      </div>
      {holdsList.length > 0 && (
        <div className="ctr-hint ctr-pad">holding: {holdsList.map(([k, r]) => `${k} (${r})`).join(' · ')}</div>
      )}
      {(g.unrecognized || []).length > 0 && (
        <div className="ctr-hint ctr-pad">
          look like Domovoi's, claimed by nothing here (not watched, never an incident): {g.unrecognized.join(', ')}
        </div>
      )}
      {(g.failing_resolvers || []).length > 0 && (
        <div className="ctr-hint ctr-pad">
          not watched · resolver failing (the plugin can't say what it expects, so only its image's repository and settings are checked): {g.failing_resolvers.map((r) => `${r.slug}/${r.container}`).join(', ')}
        </div>
      )}
      {g.mpd_image && <div className="ctr-hint ctr-pad">music player image at this start: {g.mpd_image}</div>}
      <CtrMpdRebuild rebuild={status.mpd_rebuild} onRebuild={onRebuild} rebuilding={rebuilding}/>
      {(g.unwatched_plugins || []).length > 0 && (
        <div className="ctr-hint ctr-pad">
          plugins that run containers without declaring them, so they aren't watched: {g.unwatched_plugins.join(', ')}
        </div>
      )}
    </Card>
  );
};

/* The guard's state for Health: ONE read (the shared store, the banner's
 * too), the admin's status read, and the page's actions. Health renders
 * CtrOpenPart and CtrContainersPart from the same model. */
const useCtrModel = () => {
  const s = useContainerGuard();
  const shared = useSharedScreen();
  // A shared screen (the kitchen tablet) is a guest here whatever it holds:
  // the banner's "details" lead to the guest line, never to containers.
  const admin = useAdminSignedIn() && !shared;
  const [fire, toastNode] = useToast();
  const [status, setStatus] = React.useState(null);
  const [checking, setChecking] = React.useState(false);
  const [rebuilding, setRebuilding] = React.useState(false);

  const loadStatus = React.useCallback(async () => {
    if (!admin) { setStatus(null); return; }
    try { setStatus(await apiGet('/api/containers/status', { quiet: true })); }
    catch { setStatus(null); }
  }, [admin]);
  React.useEffect(() => { loadStatus(); }, [loadStatus]);
  useStateEvents(['containers.changed'], () => { loadStatus(); });

  const refresh = async () => {
    await ContainerGuardStore.refresh();
    await loadStatus();
  };
  const checkNow = async () => {
    setChecking(true);
    try {
      await apiPost('/api/containers/check', {});
      await refresh();
    } catch (e) {
      reportMutationFailure(fire, 'check', e);
    } finally {
      setChecking(false);
    }
  };

  const rebuildMpd = async () => {
    setRebuilding(true);
    try {
      const r = await apiPost('/api/containers/mpd-image/rebuild', {});
      const failed = ((r && r.rooms) || []).filter((x) => x.error);
      if (failed.length) {
        fire(`music player image rebuilt, but not every room restarted on it: ${failed.map((x) => `${x.room} (${x.error})`).join('; ')}`, { stay: true });
      } else {
        fire('music player image rebuilt; every room restarted on it');
      }
      await refresh();
    } catch (e) {
      reportMutationFailure(fire, 'rebuild', e);
    } finally {
      setRebuilding(false);
    }
  };

  const household = !shared && s.loaded && s.tier === 'household' && s.data;
  const guestView = s.loaded && (shared || s.tier === 'open');
  const attention = s.loaded && s.data && (s.tier === 'open' ? s.data.attention : !!(s.data.banner && s.data.banner.show));
  const incidents = household ? (s.data.incidents || []) : [];
  return {
    s, shared, admin, fire, toastNode, status, checking, rebuilding, checkNow, rebuildMpd, refresh,
    household: !!household, guestView: !!guestView, attention: !!attention,
    open: incidents.filter((i) => i.open),
    closed: incidents.filter((i) => !i.open),
    frozen: !!(household && s.data.frozen),
  };
};

/* The top of Health while anything is open: what the freeze holds, and
 * every open incident with its acknowledge / clear. Nothing otherwise. */
const CtrOpenPart = ({ m }) => {
  if (!m.household || !m.open.length) return null;
  return (
    <div className="ctr-open-part" data-settings-mark="incidents">
      {m.frozen && (
        <Card title="paused until an admin reviews" sub="everything else keeps working" padded>
          <ul className="ctr-freeze-list">
            {CTR_FREEZE_LIST.map((x) => <li key={x}>{x}</li>)}
          </ul>
        </Card>
      )}
      <Card title="containers that need attention" sub={`${m.open.length} open`}>
        {m.open.map((inc) => (
          <CtrIncident key={inc.id} inc={inc} admin={m.admin} onChanged={m.refresh} fire={m.fire}/>
        ))}
      </Card>
    </div>
  );
};

/* Health's Containers part, inside its card under the check row: the
 * guest line for a browser that can't see more, and the last day's
 * history. */
const CtrContainersPart = ({ m }) => {
  return (
    <>
      {!m.s.loaded && <div className="ctr-hint ctr-pad">reading…</div>}
      {m.guestView && (
        <div className="ctr-pad">
          <div className="ctr-guest">
            {m.attention ? CONTAINER_GUARD_GUEST_TEXT : 'nothing needs attention'}
          </div>
          {m.shared ? (
            <div className="ctr-hint">this is a shared screen · an admin sees the details on their own device</div>
          ) : (
            <>
              <div className="ctr-hint">pair this browser or sign in to see what Domovoi watches</div>
              <div style={{ marginTop: 10 }}>
                <Button onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</Button>
              </div>
            </>
          )}
        </div>
      )}
      {m.household && m.closed.length > 0 && (
        <div className="ctr-history">
          <div className="health-sub mono">last 24 hours · resolved and cleared</div>
          {m.closed.map((inc) => (
            <CtrIncident key={inc.id} inc={inc} admin={m.admin} onChanged={m.refresh} fire={m.fire}/>
          ))}
        </div>
      )}
    </>
  );
};

/* "Behind the scenes", under Health's Containers card and folded: what
 * the guard watches, check now, and the music player image's rebuild.
 * Admins only (the status read is theirs). */
const CtrBehindPart = ({ m }) => {
  const [open, setOpen] = React.useState(false);
  if (!m.admin) return null;
  return (
    <div className="health-behind" data-settings-mark="behind-the-scenes">
      <button type="button" className="health-disclose" aria-expanded={open}
              onClick={() => setOpen((o) => !o)}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14}/>
        behind the scenes
      </button>
      {open && <CtrStatus status={m.status} onCheck={m.checkNow} checking={m.checking}
                          onRebuild={m.rebuildMpd} rebuilding={m.rebuilding}/>}
    </div>
  );
};

Object.assign(window, { useCtrModel, CtrOpenPart, CtrContainersPart, CtrBehindPart });
