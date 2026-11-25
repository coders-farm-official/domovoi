/* Offline kit (Settings → System → Offline kit; release 1.5.0).
 *
 * The server makes a USB stick image of everything an install would
 * download (domovoi/kit_builder/ on the core; docs/OFFLINE_KIT.md): the
 * household ticks what goes on it, sees the size and whether it fits its
 * stick, presses "Make the kit", watches one progress bar, then writes the
 * image with Raspberry Pi Imager (or downloads it). Admins only.
 *
 * Owner's defaults (2026-11-22): a Windows or Linux PC ticked, a Mac
 * unticked; models and wake words the household added itself are offered,
 * off by default and labelled "not checked against a release". Refusals
 * come from the server with one plain reason each; a server behind the
 * latest release gets a warning with "Make it anyway". The kit image is
 * deleted by itself after three days (and when a new kit starts).
 *
 * The job's state lives on the server, so a reload finds it still running.
 */

const KIT_GB = (n) => `${(Number(n || 0) / 1e9).toFixed(1)} GB`;

const kitQuery = (sel) => {
  const q = new URLSearchParams();
  q.set('targets', sel.targets.join(','));
  q.set('includes', sel.includes.join(','));
  if (sel.household.length) q.set('household', sel.household.join(','));
  if (sel.stick_gb) q.set('stick_gb', String(sel.stick_gb));
  return q.toString();
};

const kitRefusalText = (e) => {
  const d = e && e.detail && e.detail.detail;
  return (d && typeof d === 'object' && typeof d.message === 'string') ? d.message : apiErrorText(e, 300);
};

const KitTick = ({ checked, onChange, disabled, children, sub }) => (
  <label className="offline-kit-tick" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13,
                                               cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.6 : 1 }}>
    <input type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)}
           style={{ marginTop: 2 }}/>
    <span>
      {children}
      {sub && <span style={{ display: 'block', fontSize: 11, color: 'var(--fg-muted)' }}>{sub}</span>}
    </span>
  </label>
);

const KitProgress = ({ job }) => {
  const pct = job.bytes_todo ? Math.min(100, Math.round(100 * (job.bytes_done || 0) / job.bytes_todo)) : 0;
  const what = job.stage === 'download' ? (job.line || 'Downloading the parts')
    : job.stage === 'check' ? 'Checking the image'
    : job.stage === 'write' ? 'Making the kit' : 'Starting';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
           style={{ height: 6, borderRadius: 9999, background: 'var(--sunken)', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: 'var(--brand)', transition: 'width 180ms cubic-bezier(0.2,0,0,1)' }}/>
      </div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
        {what}… {job.bytes_todo ? `${KIT_GB(job.bytes_done)} of ${KIT_GB(job.bytes_todo)}` : ''}
        {job.cancelling ? ' · stopping' : ''}
      </div>
    </div>
  );
};

/* A kit carrying household items downloads only with the admin's Bearer
 * (the core refuses a plain link), so the card asks for a single-use
 * download ticket (5 minutes) and then lets the browser download the
 * image through it, straight to disk: never into the page's memory. */
const kitDownloadSignedIn = async (build) => {
  const t = await apiFetch(`/api/offline-kit/${build.id}/ticket`, { method: 'POST' });
  const a = document.createElement('a');
  a.href = t.url;
  a.download = build.image_name;
  document.body.appendChild(a);
  a.click();
  a.remove();
};

const KitReady = ({ build, onDelete }) => {
  const imager = `rpi-imager://?repo=${encodeURIComponent(build.os_list_url)}`;
  const days = Math.max(0, Math.round((build.expires * 1000 - Date.now()) / 86400000));
  const note = { fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 };
  const locked = !!build.needs_admin;
  const [fetching, setFetching] = React.useState(false);
  const [failed, setFailed] = React.useState(null);
  const download = async () => {
    setFetching(true);
    setFailed(null);
    try {
      await kitDownloadSignedIn(build);
    } catch (e) {
      setFailed(String((e && e.message) || e || 'the download failed'));
    } finally {
      setFetching(false);
    }
  };
  return (
    <div className="offline-kit-ready" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 13 }}>
        <b>Your offline kit is ready.</b> Domovoi {build.domovoi_version}, {KIT_GB(build.size)}.
      </div>
      {locked ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button variant="primary" icon="download" onClick={download} disabled={fetching}>
            {fetching ? 'starting the download…' : 'download the image'}
          </Button>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a className="btn btn-primary" href={imager} style={{ textDecoration: 'none' }}>
            <Icon name="usb" size={14}/>open in Raspberry Pi Imager
          </a>
          <a className="btn btn-secondary" href={build.image_url} download style={{ textDecoration: 'none' }}>
            <Icon name="download" size={14}/>download the image
          </a>
          <a className="btn btn-secondary" href={build.manifest_url} download style={{ textDecoration: 'none' }}>
            <Icon name="file" size={14}/>Imager file
          </a>
        </div>
      )}
      {locked && (
        <div style={note}>
          This kit carries items this house added, so it downloads only here, signed in as admin. A link or
          Raspberry Pi Imager can’t fetch it: save the image, then choose it in Imager under “Use custom”.
        </div>
      )}
      {failed && <div style={{ ...note, color: 'var(--danger)' }}>The download failed: {failed}</div>}
      <div style={note}>Everything on the stick will be replaced.</div>
      <div style={note}>Don’t sell this stick: some files on it may only be shared for free.</div>
      {build.household && build.household.length > 0 && (
        <div style={note}>Added by this house, not checked against a release: {build.household.join(', ')}.</div>
      )}
      {build.installers && Object.keys(build.installers).length > 0 && (
        <details style={note}>
          <summary>installer checksums</summary>
          <div style={{ marginTop: 4 }}>Compare them with the release page from any phone before you install.</div>
          {Object.entries(build.installers).map(([name, sha]) => (
            <div key={name} className="mono" style={{ fontSize: 11, wordBreak: 'break-all' }}>{name} {sha}</div>
          ))}
        </details>
      )}
      <div>
        <Button variant="secondary" icon="trash-2" onClick={() => onDelete(build.id)}>
          delete the kit
        </Button>
        <span style={{ ...note, marginLeft: 8 }}>
          frees {KIT_GB(build.size)}; deleted by itself {days > 0 ? `in ${days} day${days === 1 ? '' : 's'}` : 'today'}.
          On a Windows or Mac server, Domovoi’s disk doesn’t shrink by itself: delete the kit once the stick is written.
        </span>
      </div>
    </div>
  );
};

const OfflineKitCard = () => {
  const [sel, setSel] = React.useState({ targets: ['pc-x86_64'], includes: ['ai', 'vision', 'rooms'],
                                         household: [], stick_gb: 16 });
  const [st, setSt] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [fire, toastNode] = useToast();
  const seq = React.useRef(0);

  const load = React.useCallback(async (s) => {
    const n = ++seq.current;
    try {
      const doc = await apiGet(`/api/offline-kit?${kitQuery(s)}`);
      if (n === seq.current) { setSt(doc); setError(null); }
    } catch (e) {
      if (n === seq.current) setError(kitRefusalText(e));
    }
  }, []);
  React.useEffect(() => { load(sel); }, [sel, load]);

  const job = (st && st.job) || {};
  const running = !!job.running;
  React.useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => load(sel), 2000);
    return () => clearInterval(t);
  }, [running, sel, load]);

  const toggle = (key, id, on) => setSel((s) => ({
    ...s, [key]: on ? [...s[key].filter((x) => x !== id), id] : s[key].filter((x) => x !== id),
  }));

  const make = async (force) => {
    setBusy(true); setError(null);
    try { setSt(await apiPost('/api/offline-kit', { ...sel, force: !!force })); }
    catch (e) { setError(kitRefusalText(e)); }
    finally { setBusy(false); }
  };
  const stop = async () => {
    try { await apiPost('/api/offline-kit/cancel', {}); load(sel); } catch (e) { setError(kitRefusalText(e)); }
  };
  const remove = async (id) => {
    if (!await askConfirm('Delete the offline kit image from the server?')) return;
    try { setSt(await apiDelete(`/api/offline-kit/${id}`)); fire('the kit image is gone from the server'); }
    catch (e) { setError(kitRefusalText(e)); }
  };

  return (
    <Card title="Offline kit"
          sub="Set up Domovoi in a house with no internet. This copies the models, the phone app and the installers onto a USB stick.">
      {toastNode}
      <OfflineKitView st={st} sel={sel} error={error} busy={busy} onToggle={toggle}
                      onStick={(g) => setSel((s) => ({ ...s, stick_gb: g }))}
                      onMake={make} onStop={stop} onDelete={remove}/>
    </Card>
  );
};

/* The card's body for one server answer (st) and the boxes ticked (sel):
 * no state of its own, so a test renders every case from props. */
const OfflineKitView = ({ st, sel, error, busy, onToggle, onStick, onMake, onStop, onDelete }) => {
  const toggle = onToggle, make = onMake, stop = onStop, remove = onDelete;
  const job = (st && st.job) || {};
  const running = !!job.running;
  const note = { fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 };
  const plan = st && st.plan;
  const refusal = st && st.refusal && st.refusal.reason !== 'busy' ? st.refusal : null;
  const warning = st && st.warning;
  const build = st && st.builds && st.builds[0];
  const failed = !running && job.ok === false && job.message;
  const stick = plan && plan.stick;
  const about = plan && !plan.exact ? 'About ' : '';

  return (
      <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!st && !error && <div style={note}>asking the server…</div>}
        {error && <div role="alert" style={{ fontSize: 12, color: 'var(--err-ink)' }}>{error}</div>}
        {st && !plan && refusal && <div role="alert" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{refusal.message}</div>}

        {plan && !running && (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div className="label">for</div>
              {plan.targets.map((t) => (
                <KitTick key={t.id} checked={sel.targets.includes(t.id)} onChange={(on) => toggle('targets', t.id, on)}>
                  {t.label}
                </KitTick>
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div className="label">include</div>
              {plan.includes.map((i) => (
                <KitTick key={i.id} checked={sel.includes.includes(i.id)} onChange={(on) => toggle('includes', i.id, on)}>
                  {i.label} <span className="mono" style={{ color: 'var(--fg-muted)' }}>({KIT_GB(i.bytes)})</span>
                </KitTick>
              ))}
            </div>
            {plan.household.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div className="label">added by this house</div>
                {plan.household.map((h) => (
                  <KitTick key={h.id} checked={sel.household.includes(h.id)} onChange={(on) => toggle('household', h.id, on)}
                           sub="not checked against a release">
                    {h.kind === 'wake-word' ? `the wake word ${h.name}` : h.name}{' '}
                    <span className="mono" style={{ color: 'var(--fg-muted)' }}>({KIT_GB(h.bytes)})</span>
                  </KitTick>
                ))}
              </div>
            )}
            {plan.refused_models && plan.refused_models.length > 0 && (
              <div style={note}>
                Never on a kit: {plan.refused_models.map((m) => m.reason).join('; ')}.
              </div>
            )}
            <div style={{ fontSize: 13 }}>
              {about}<b>{KIT_GB(plan.total_bytes)}</b>.
              {stick && stick.needed_gb ? <> Needs a <b>{stick.needed_gb} GB</b> stick or bigger.</> : ' Bigger than any stick listed.'}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 13 }}>
              <label htmlFor="offline-kit-stick">My stick is</label>
              <select id="offline-kit-stick" value={sel.stick_gb || ''}
                      onChange={(e) => onStick(Number(e.target.value) || null)}
                      style={{ font: 'inherit', height: 30, padding: '0 8px', borderRadius: 'var(--r-sm)',
                               border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
                {plan.stick_sizes.map((g) => <option key={g} value={g}>{g} GB</option>)}
              </select>
              {stick && stick.fits === true && <span style={{ color: 'var(--ok-ink)' }}><Icon name="check" size={14}/> fits</span>}
            </div>
            {stick && stick.fits === false && (
              <div role="status" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{stick.message}</div>
            )}
            {plan.download_bytes > 0 && (
              <div style={note}>
                {KIT_GB(plan.download_bytes)} will be downloaded first ({plan.download_labels.join(', ')}).
              </div>
            )}
            {refusal && <div role="alert" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{refusal.message}</div>}
            {warning && !refusal && <div role="status" style={{ fontSize: 12, color: 'var(--warn-ink)' }}>{warning.message}</div>}
            {failed && job.reason !== 'cancelled' && <div role="alert" style={{ fontSize: 12, color: 'var(--err-ink)' }}>{job.message}</div>}
            {failed && job.reason === 'cancelled' && <div style={note}>{job.message}</div>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {warning && !refusal ? (
                <>
                  <a className="btn btn-primary" href="#settings/about" style={{ textDecoration: 'none' }}>update first</a>
                  <Button variant="secondary" icon="package" disabled={busy} onClick={() => make(true)}>
                    {busy ? 'starting…' : 'make it anyway'}
                  </Button>
                </>
              ) : (
                <Button variant="primary" icon="package" disabled={busy || !!refusal} onClick={() => make(false)}>
                  {busy ? 'starting…' : 'make the kit'}
                </Button>
              )}
            </div>
            <div style={note}>
              Don’t sell this stick: some files on it, like the Domovoi wake word, may only be shared for free.
            </div>
          </>
        )}

        {running && (
          <>
            <KitProgress job={job}/>
            <div><Button variant="secondary" icon="x" disabled={job.cancelling} onClick={stop}>stop</Button></div>
          </>
        )}

        {build && !running && <KitReady build={build} onDelete={remove}/>}
      </div>
  );
};

const OfflineKitPanel = () => {
  const admin = useAdminSignedIn();
  if (!admin) {
    return (
      <Card title="Offline kit" sub="Set up Domovoi in a house with no internet, from a USB stick.">
        <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--fg-muted)' }}>
          Admins only.{' '}
          <button type="button" className="home-link"
                  onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</button>
        </div>
      </Card>
    );
  }
  return <OfflineKitCard/>;
};

/* Update from an offline kit (About & updates; release 1.5.0, P5;
 * domovoi/kit_update.py on the core). The admin plugs the newer kit's
 * stick into this computer and chooses its folder. The page sends kit.json
 * (the server refuses an older or the same release, another machine, or a
 * disk without room, in one plain sentence), then each file the server
 * lacks in pieces of at most chunk_max bytes, each at the offset the server
 * already holds: a reload or a dropped connection picks up where it
 * stopped once the folder is chosen again. Then "Update now" checks the
 * release, moves to it and starts the update, with no internet. */
const KIT_CHUNK = 8 * 1024 * 1024;

const kitUpdateText = (e) => {
  const d = e && e.detail && e.detail.detail;
  return (d && typeof d === 'object' && typeof d.message === 'string') ? d.message : apiErrorText(e, 300);
};

/* The chosen folder's files by their path on the stick: the folder's own
 * name (the first part of webkitRelativePath) is dropped. */
const kitFilesByPath = (list) => {
  const out = {};
  for (const f of Array.from(list || [])) {
    const rel = (f.webkitRelativePath || f.name).split('/').slice(1).join('/') || f.name;
    out[rel] = f;
  }
  return out;
};

/* Send every file of the session the server doesn't hold yet. */
const kitSendFiles = async (session, files, onProgress, stopped) => {
  const chunk = Math.min(KIT_CHUNK, session.chunk_max || KIT_CHUNK);
  let sent = session.bytes_received || 0;
  for (const item of session.files) {
    let at = item.received;
    if (at >= item.size) continue;
    const file = files[item.path];
    if (!file) throw new Error(`The chosen folder has no ${item.path}. Choose the kit's own folder (the one with kit.json in it).`);
    if (file.size !== item.size) throw new Error(`${item.path} on the stick is not the size kit.json says. Make the stick again.`);
    while (at < item.size) {
      if (stopped()) return;
      const end = Math.min(item.size, at + chunk);
      const q = new URLSearchParams({ path: item.path, offset: String(at) });
      let r;
      try {
        r = await apiFetchRaw(`/api/offline-kit/update/${session.id}/file?${q}`, {
          method: 'PUT', body: file.slice(at, end), headers: { 'Content-Type': 'application/octet-stream' },
        });
      } catch (e) {
        const d = e && e.detail && e.detail.detail;
        // The server holds another amount: carry on from there.
        if (d && d.reason === 'offset' && typeof d.received === 'number') { sent += d.received - at; at = d.received; continue; }
        throw e;
      }
      const doc = await r.json();
      sent += doc.received - at;
      at = doc.received;
      onProgress(sent);
    }
  }
};

const OfflineKitUpdateBody = () => {
  const [st, setSt] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [busy, setBusy] = React.useState(null); // 'reading' | 'sending' | 'applying'
  const [sent, setSent] = React.useState(0);
  const inputRef = React.useRef(null);
  const stopRef = React.useRef(false);

  const load = React.useCallback(async () => {
    try { setSt(await apiGet('/api/offline-kit/update')); } catch (e) { setError(kitUpdateText(e)); }
  }, []);
  React.useEffect(() => { load(); return () => { stopRef.current = true; }; }, [load]);
  const session = st && st.session;
  const applying = session && session.state === 'applying';
  React.useEffect(() => {
    if (!applying) return undefined;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [applying, load]);

  const choose = async (list) => {
    const files = kitFilesByPath(list);
    if (inputRef.current) inputRef.current.value = '';
    setError(null);
    const kitJson = files['kit.json'];
    if (!kitJson) { setError('There is no kit.json in that folder. Choose the stick itself (DOMOVOIKIT), not a folder inside it.'); return; }
    stopRef.current = false;
    try {
      setBusy('reading');
      const view = await apiFetch('/api/offline-kit/update', { method: 'POST', body: await kitJson.text() });
      setSt(view);
      setBusy('sending');
      setSent(view.session.bytes_received || 0);
      await kitSendFiles(view.session, files, setSent, () => stopRef.current);
      await load();
    } catch (e) {
      setError(kitUpdateText(e));
      load();
    } finally {
      setBusy(null);
    }
  };
  const apply = async () => {
    setBusy('applying'); setError(null);
    try { setSt(await apiPost(`/api/offline-kit/update/${session.id}/apply`, {})); }
    catch (e) { setError(kitUpdateText(e)); load(); }
    finally { setBusy(null); }
  };
  const cancel = async () => {
    stopRef.current = true;
    try { setSt(await apiDelete('/api/offline-kit/update')); setError(null); }
    catch (e) { setError(kitUpdateText(e)); }
  };

  const note = { fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 };
  const total = session ? session.bytes_total : 0;
  const got = busy === 'sending' ? sent : (session ? session.bytes_received : 0);
  const pct = total ? Math.min(100, Math.round(100 * got / total)) : 0;
  const uploading = session && (session.state === 'uploading' || session.state === 'failed');
  const allHere = session && session.files_left === 0;
  return (
    <div className="offline-kit-update" style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
      <div style={note}>
        Plug the newer kit’s stick into this computer and choose its folder. Only what this server lacks is
        sent (often 0.5 to 1.5 GB), and nothing is downloaded from the internet.
      </div>
      <input ref={inputRef} type="file" multiple webkitdirectory="" directory="" style={{ display: 'none' }}
             onChange={(e) => choose(e.target.files)}/>
      {error && <div role="alert" style={{ color: 'var(--danger)', fontSize: 13 }}>{error}</div>}
      {session && session.state === 'done' && (
        <div>Updated to Domovoi {session.version} from the offline kit. The uploaded copy was removed.
          {session.apps && session.apps.kept ? ` The phone app is on the server (${session.apps.kept} file${session.apps.kept === 1 ? '' : 's'}).` : ''}
        </div>
      )}
      {session && session.state === 'refused' && session.error && <div role="alert">{session.error}</div>}
      {session && uploading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div>Domovoi {session.version} from the kit: {KIT_GB(got)} of {KIT_GB(total)}
            {session.files_left && busy !== 'sending' ? ` · ${session.files_left} file${session.files_left === 1 ? '' : 's'} still to send: choose the folder again to go on` : ''}
          </div>
          <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
               style={{ height: 6, borderRadius: 9999, background: 'var(--sunken)', overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: 'var(--brand)', transition: 'width 180ms cubic-bezier(0.2,0,0,1)' }}/>
          </div>
          {session.state === 'failed' && session.error && <div role="alert" style={note}>{session.error}</div>}
        </div>
      )}
      {applying && <div>Updating to Domovoi {session.version}… The dashboard goes away for a minute or two while Domovoi restarts.</div>}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {!applying && (
          <Button variant={allHere && uploading ? 'secondary' : 'primary'} icon="usb" disabled={!!busy}
                  onClick={() => inputRef.current && inputRef.current.click()}>
            {busy === 'reading' ? 'reading the kit…' : busy === 'sending' ? 'sending…' : 'choose the kit folder'}
          </Button>
        )}
        {uploading && allHere && !busy && (
          <Button variant="primary" icon="refresh-cw" onClick={apply}>update now</Button>
        )}
        {busy === 'applying' && <span style={note}>checking the release…</span>}
        {session && !applying && session.state !== 'done' && (
          <Button variant="secondary" icon="x" onClick={cancel}>cancel</Button>
        )}
      </div>
      {st && st.running_version && <div style={note}>This server runs Domovoi {st.running_version}. A kit can only update it to a newer release.</div>}
    </div>
  );
};

const OfflineKitUpdateCard = () => {
  const admin = useAdminSignedIn();
  return (
    <Card title="Update from an offline kit" sub="A house with no internet updates from a newer kit’s USB stick." padded>
      {admin ? <OfflineKitUpdateBody/> : (
        <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Admins only.</div>
      )}
    </Card>
  );
};
window.OfflineKitUpdateCard = OfflineKitUpdateCard;

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['offline-kit'] = { Panel: OfflineKitPanel };
