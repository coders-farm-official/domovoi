/* Settings → Chat & AI → Photos and videos (1.5.0, media understanding).
 *
 * What the house works out about its photos and videos, in the background
 * and voice first (domovoi/media_understanding/): the switches, progress
 * per kind, the estimate, "Paused for a voice answer" while it holds off,
 * Pause, Run now, Rescan, the per-video frame cap, and excluded folders.
 *
 * Tiers (owner answers, 2026-11-22): the household sees progress and may
 * ask for a rescan; every switch, Pause, Run now and the excluded folders
 * are admin. Faces and photo locations are off by default.
 *
 * Data:
 *   GET  /api/media-understanding               · state (household)
 *   PUT  /api/media-understanding/settings      · switches (admin)
 *   POST /api/media-understanding/pause|run-now · (admin)
 *   POST /api/media-understanding/rescan        · (household)
 *   POST/DELETE /api/media-understanding/excluded (admin)
 *   GET  /api/media/search?q=                    · search (household, 1.5.0 slice 2)
 * Live: `media_understanding.changed` on /ws/state.
 *
 * Search results carry words a model or OCR made from a picture (the
 * caption, tags, the text found in it). They are untrusted text: rendered
 * only as React text children (never HTML), and a caption is shown as
 * "Might show: …" because a small model sometimes sees things that aren't
 * there. The search box only shows once something has been understood
 * (no misleading controls); Media ▸ Photos (photos.jsx) is the full page.
 */

const MU_PHASES = {
  off: 'Off.',
  paused: 'Paused.',
  idle: 'Up to date.',
  working: 'Working on it.',
  waiting_for_window: 'Waiting for the night (new photos and videos are done during the day when the house is quiet).',
  waiting_for_memory: 'Waiting for enough free memory, so voice answers stay quick.',
};

const muCount = (n) => (typeof n === 'number' ? n.toLocaleString() : '0');

const MuProgressRow = ({ label, p }) => {
  const total = (p && p.total) || 0;
  const done = (p && p.done) || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
        <strong>{label}</strong>
        <span style={{ color: 'var(--fg-muted)' }}>
          {muCount(done)} of {muCount(total)} done
          {p && p.failed ? ` · ${muCount(p.failed)} couldn’t be read` : ''}
        </span>
      </div>
      <div role="progressbar" aria-label={`${label}: ${pct}%`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
           style={{ height: 6, borderRadius: 3, background: 'var(--border-soft)', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: 'var(--accent)' }}/>
      </div>
    </div>
  );
};

const MuSwitch = ({ checked, disabled, onChange, label, sub }) => (
  <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13, lineHeight: 1.5 }}>
    <input type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)}/>
    <span>
      <strong>{label}</strong>
      <span style={{ display: 'block', color: 'var(--fg-muted)', fontSize: 12 }}>{sub}</span>
    </span>
  </label>
);

const muWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
};

const muThumb = (hit) => withDeviceToken(
  `${API_BASE}/api/images/thumb?library_id=${encodeURIComponent(hit.library_id)}`
  + `&path=${encodeURIComponent(hit.path)}&size=s`);

/* One search hit. Every string from the server is a text child. */
const MuHit = ({ hit }) => (
  <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13 }}>
    {hit.kind === 'image'
      ? <img src={muThumb(hit)} alt="" loading="lazy" width={64} height={64}
             style={{ objectFit: 'cover', borderRadius: 6, background: 'var(--border-soft)', flex: '0 0 auto' }}/>
      : <div aria-hidden="true" style={{ width: 64, height: 64, borderRadius: 6, background: 'var(--border-soft)', flex: '0 0 auto' }}/>}
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
      <strong style={{ overflowWrap: 'anywhere' }}>{hit.name}</strong>
      <span style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
        {muWhen(hit.taken_at)}{hit.date_from === 'file' ? ' (the file’s date)' : ''}
      </span>
      {(hit.place || hit.near_home) && (
        <span style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
          {hit.place || ''}{hit.near_home ? `${hit.place ? ' · ' : ''}near home` : ''}
        </span>
      )}
      {hit.might_show && <span>Might show: {hit.might_show}</span>}
      {hit.tags && hit.tags.length > 0 && (
        <span style={{ color: 'var(--fg-muted)', fontSize: 12 }}>{hit.tags.join(', ')}</span>
      )}
      {hit.text && (
        <span style={{ fontSize: 12, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          Text in it: {hit.text}
        </span>
      )}
      {!hit.analysed && <span style={{ color: 'var(--fg-muted)', fontSize: 12 }}>Not looked at yet: found by its name.</span>}
    </div>
  </div>
);

const MuSearch = () => {
  const [q, setQ] = React.useState('');
  const [res, setRes] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState('');
  const run = async (e) => {
    if (e) e.preventDefault();
    if (!q.trim()) return;
    setBusy(true); setErr('');
    try {
      setRes(await apiGet(`/api/media/search?q=${encodeURIComponent(q.trim())}&limit=20`, { quiet: true }));
    } catch (x) {
      setErr((x && x.message) || 'the search didn’t work');
    } finally { setBusy(false); }
  };
  const items = (res && res.items) || [];
  return (
    <Card title="Search" sub="By what is in them and when: “dog at the beach”, “birthday cake 2023”, “snow last winter”.">
      <form onSubmit={run} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input aria-label="Search photos and videos" className="cal-inp" value={q} maxLength={300}
               onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 160 }}/>
        <button type="submit" className="btn" disabled={busy || !q.trim()}>Search</button>
      </form>
      <div role="status" style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
        {err && <div style={{ fontSize: 13, color: 'var(--danger, #b00)' }}>{err}</div>}
        {res && items.length === 0 && <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Nothing found.</div>}
        {items.map((h) => <MuHit key={h.id} hit={h}/>)}
        {items.length > 0 && (
          <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            Descriptions are a computer’s guess at what a picture shows, and are sometimes wrong.
          </div>
        )}
      </div>
    </Card>
  );
};

const MediaUnderstandingPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const { data, refresh } = useApiObject('/api/media-understanding',
                                         { quiet: true, eventTypes: ['media_understanding.changed'] });
  const [busy, setBusy] = React.useState(false);
  const [cap, setCap] = React.useState('');
  const [newLib, setNewLib] = React.useState('');
  const [newPrefix, setNewPrefix] = React.useState('');
  // The shared confirm dialog (components.jsx useDeleteConfirm).
  const facesOff = useDeleteConfirm(() => save({ faces: false, confirm_delete_faces: true }, 'faces off: every face deleted'));
  const placesOff = useDeleteConfirm(() => save({ places: false, confirm_delete_places: true }, 'photo locations off: every location deleted'));
  // Excluding a folder that has results deletes them: asked first, with
  // the count the server gave (its 409), then sent again with the yes.
  const excludeAsk = useDeleteConfirm(async (x) => {
    const ok = await call(() => apiPost('/api/media-understanding/excluded', { ...x, confirm_delete: true }),
                          'folder excluded: what was worked out about it deleted');
    if (ok) setNewPrefix('');
  });
  React.useEffect(() => {
    if (data && data.settings) setCap(data.settings.frame_cap == null ? '' : String(data.settings.frame_cap));
  }, [data && data.settings && data.settings.frame_cap]);

  const call = async (fn, said) => {
    setBusy(true);
    try {
      await fn();
      if (said) fire(said, { tone: 'ok' });
      refresh();
      return true;
    } catch (e) {
      fire(`not saved: ${(e && e.message) || 'the server said no'}`, { tone: 'warn' });
      return false;
    } finally { setBusy(false); }
  };
  const save = (changes, said) => call(() => apiPut('/api/media-understanding/settings', changes), said);

  if (!data) return <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>loading…</div>;
  const s = data.settings || {};
  const p = data.progress || {};
  const est = data.estimate || {};
  const libs = data.libraries || [];
  const libLabel = (id) => ((libs.find((l) => l.id === id) || {}).label || id);
  const libOption = (l) => `${l.label}${l.present === false ? ' (not here now)' : ''}`;
  const placeName = (x) => `${libLabel(x.library_id)}${x.prefix ? ` / ${x.prefix}` : ' (all of it)'}`;
  const exclude = async () => {
    const x = { library_id: newLib || libs[0].id, prefix: newPrefix.trim() };
    setBusy(true);
    try {
      await apiPost('/api/media-understanding/excluded', x);
      fire('folder excluded', { tone: 'ok' });
      setNewPrefix('');
      refresh();
    } catch (e) {
      const d = e && e.detail && e.detail.detail;
      if (e && e.status === 409 && d && typeof d.files === 'number') {
        excludeAsk.request(x, {
          title: `Exclude ${placeName(x)}?`, confirmLabel: 'Exclude and delete',
          body: (
            <div>
              Domovoi stops looking at it, and deletes what it worked out about {muCount(d.files)}
              {d.files === 1 ? ' photo or video' : ' photos and videos'} in it: descriptions, tags, text found, what is
              said, summaries, faces and places. Your files stay as they are. Including it again looks at it afresh.
            </div>
          ),
        });
      } else {
        fire(`not saved: ${apiErrorText(e)}`, { tone: 'warn' });
      }
    } finally { setBusy(false); }
  };
  const [capLo, capHi] = data.frame_cap_range || [3, 1000];
  const capN = Number(cap);
  const capOk = Number.isInteger(capN) && capN >= capLo && capN <= capHi;
  const left = ((p.photos && p.photos.left) || 0) + ((p.videos && p.videos.left) || 0);
  const locked = !admin || busy;

  const turnFacesOff = () => {
    if (!data.face_count) { save({ faces: false }, 'faces off'); return; }
    facesOff.request(null, {
      title: 'Turn faces off?', confirmLabel: 'Turn off and delete',
      body: (
        <>
          <div>This deletes every face and group: {muCount(data.face_count)} saved faces and every name on them. Turning it on again starts from zero, and this can’t be undone.</div>
          <div>People who asked to be forgotten stay forgotten: their forget list is kept, so they aren’t learned again if faces are turned back on.</div>
        </>
      ),
    });
  };

  const pl = data.places || {};
  const turnPlacesOff = () => {
    if (!pl.count) { save({ places: false }, 'photo locations off'); return; }
    placesOff.request(null, {
      title: 'Turn photo locations off?', confirmLabel: 'Turn off and delete',
      body: (
        <div>This deletes where {muCount(pl.count)} photos were taken: their positions and place names. Turning it on again reads them from the photos afresh.</div>
      ),
    });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toastNode}
      {facesOff.node}
      {placesOff.node}
      {excludeAsk.node}
      <div style={{ fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
        Domovoi can look at your photos and videos on this server, so you can find them by what is in them.
        {' '}Nothing leaves the house and nothing is written into your files. It works at night, and pauses whenever someone talks to Domovoi.
      </div>

      <Card title="Progress">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div role="status" style={{ fontSize: 13 }}>
            {p.waiting_for_voice ? 'Paused for a voice answer.' : (MU_PHASES[p.phase] || MU_PHASES.idle)}
            {s.enabled && left > 0 && est.nights > 0 && (
              <span style={{ color: 'var(--fg-muted)' }}>
                {' '}About {est.nights === 1 ? 'one night' : `${est.nights} nights`} left (an estimate{p.benchmark ? ' from this server’s measured speed' : ''}).
              </span>
            )}
          </div>
          <MuProgressRow label="Photos" p={p.photos}/>
          <MuProgressRow label="Videos" p={p.videos}/>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {admin && s.enabled && (s.paused
              ? <button type="button" className="btn" disabled={busy}
                        onClick={() => call(() => apiPost('/api/media-understanding/pause', { paused: false }), 'carrying on')}>Resume</button>
              : <button type="button" className="btn" disabled={busy}
                        onClick={() => call(() => apiPost('/api/media-understanding/pause', { paused: true }), 'paused')}>Pause</button>)}
            {admin && s.enabled && !s.paused && !s.run_now && left > 0 && (
              <button type="button" className="btn" disabled={busy}
                      onClick={() => call(() => apiPost('/api/media-understanding/run-now', {}), 'running now, until the backlog is done')}>Run now</button>
            )}
            {s.enabled && (
              <button type="button" className="btn" disabled={busy || p.rescan_requested}
                      onClick={() => call(() => apiPost('/api/media-understanding/rescan', {}), 'looking for new photos and videos')}>
                {p.rescan_requested ? 'Rescan asked' : 'Rescan'}
              </button>
            )}
          </div>
          {s.run_now && <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Running now, day and night, until the backlog is done (still pausing for voice).</div>}
        </div>
      </Card>

      {s.enabled && (((p.photos && p.photos.done) || 0) + ((p.videos && p.videos.done) || 0)) > 0 && <MuSearch/>}

      <Card title="Switches" sub={admin ? undefined : 'An admin can change these.'}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <MuSwitch checked={s.enabled} disabled={locked}
                    onChange={(on) => save({ enabled: on }, on ? 'understanding photos and videos' : 'understanding off')}
                    label="Understand photos and videos"
                    sub={data.ai_on
                      ? 'Descriptions, text in pictures, and what is said in videos.'
                      : 'AI features are off: text in pictures and what is said in videos still work; descriptions wait until AI is on.'}/>
          <MuSwitch checked={s.faces} disabled={locked || !s.enabled}
                    onChange={(on) => (on ? save({ faces: true }, 'faces on') : turnFacesOff())}
                    label="Recognize faces"
                    sub="Groups the same face across photos and videos so you can name people. Off by default. Turning it off deletes every face."/>
          <MuSwitch checked={s.places} disabled={locked || !s.enabled}
                    onChange={(on) => (on ? save({ places: true }, 'photo locations on') : turnPlacesOff())}
                    label="Use photo locations"
                    sub="Reads where a photo was taken from the photo itself and names the town on this server, so you can search “photos from Paris” or “at home”. Off by default. Turning it off deletes every location. Town names: GeoNames (CC BY 4.0)."/>
          {s.places && pl.problem && (
            <div role="status" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{pl.problem}</div>
          )}
        </div>
      </Card>

      <Card title="Frames per video" sub="How many moments of each video are looked at.">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="radio" name="mu-cap" checked={s.frame_cap_mode !== 'manual'} disabled={locked}
                   onChange={() => save({ frame_cap_mode: 'auto' }, 'frames per video: automatic')}/>
            <span><strong>Automatic</strong> <span style={{ color: 'var(--fg-muted)' }}>
              (from this server’s measured speed: more on a fast graphics card, fewer on a short clip)</span></span>
          </label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input type="radio" name="mu-cap" aria-label="At most this many" checked={s.frame_cap_mode === 'manual'}
                   disabled={locked || !capOk}
                   onChange={() => save({ frame_cap_mode: 'manual', frame_cap: capN }, `at most ${capN} frames per video`)}/>
            <label htmlFor="mu-cap-n"><strong>At most</strong></label>
            <input id="mu-cap-n" type="number" min={capLo} max={capHi} step={1} value={cap} disabled={locked}
                   className="cal-inp" style={{ width: 80, textAlign: 'right' }} aria-invalid={cap !== '' && !capOk}
                   onChange={(e) => setCap(e.target.value)}
                   onBlur={() => { if (capOk && s.frame_cap_mode === 'manual' && capN !== s.frame_cap) save({ frame_cap: capN }, `at most ${capN} frames per video`); }}/>
            <span>frames per video</span>
          </div>
        </div>
      </Card>

      <Card title="Excluded folders" sub="Never looked at. Any library or a folder in one; excluding it deletes what was worked out about it.">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
          {(data.excluded || []).length === 0 && <div style={{ color: 'var(--fg-muted)' }}>None.</div>}
          {(data.excluded || []).map((x) => (
            <div key={`${x.library_id}/${x.prefix}`} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{placeName(x)}</span>
              {admin && (
                <button type="button" className="btn" disabled={busy}
                        onClick={() => call(() => apiFetch(`/api/media-understanding/excluded?library_id=${encodeURIComponent(x.library_id)}&prefix=${encodeURIComponent(x.prefix)}`, { method: 'DELETE' }),
                                            'folder included again')}>Include again</button>
              )}
            </div>
          ))}
          {admin && libs.length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <select aria-label="Library" className="cal-inp" value={newLib || libs[0].id} disabled={busy}
                      style={{ maxWidth: '100%' }} onChange={(e) => setNewLib(e.target.value)}>
                {libs.map((l) => <option key={l.id} value={l.id}>{libOption(l)}</option>)}
              </select>
              <input aria-label="Folder inside it (empty for all of it)" className="cal-inp" placeholder="folder, e.g. Work/Receipts"
                     value={newPrefix} disabled={busy} onChange={(e) => setNewPrefix(e.target.value)} style={{ flex: 1, minWidth: 160 }}/>
              <button type="button" className="btn" disabled={busy} onClick={exclude}>Exclude</button>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['photos-and-videos'] = { Panel: MediaUnderstandingPanel };
