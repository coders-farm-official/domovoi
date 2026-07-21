/* Plugins admin page (design §3, §7.5).
 *
 * List installed plugins (status, version, publisher, permissions),
 * install from a zip upload or a GitHub URL via the TWO-PHASE flow —
 * stage → preview → confirm — where the confirm modal renders the
 * §7.5 trust statement UNSKIPPABLY (there is no way to confirm without
 * that screen; it is the only path to the confirm endpoint), plus
 * enable / disable / upgrade and uninstall with an explicit
 * keep-vs-purge choice that shows what purge will drop.
 *
 * All mutations proxy to the CORE process with the caller's Bearer
 * forwarded — an unauthenticated click gets a 401/403 and data.js pops
 * the admin login modal automatically.
 */

const PERMISSION_LABELS = {
  network: 'makes outbound network requests',
  subprocess: 'spawns external processes on your machine',
  hardware: 'touches USB / audio hardware',
  filesystem_outside_data: 'writes files outside its own data directory',
};

const STATUS_PILL = {
  ok:          { tone: 'ok',   label: 'ok' },
  degraded:    { tone: 'warn', label: 'degraded' },
  load_error:  { tone: 'err',  label: 'load error' },
  uninstalled: { tone: 'idle', label: 'uninstalled' },
};

/* ---- The §7.5 trust/confirm modal ---------------------------- */
/* Rendered after Phase A returns a preview; the ONLY affordance that
 * reaches Phase B. Shows the standing trust statement, permission
 * rows, free-text warnings, the pinned direct requirements AND the
 * resolved transitive tree, handlers + bands, publisher/license, and
 * (github installs) the source URL verbatim. */
const TrustConfirmModal = ({ stagedId, preview, sourceLabel, verb, onDone, onCancel, fire }) => {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const p = preview || {};
  const perms = p.permissions || {};
  const reqs = p.requirements || {};
  const confirm = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await apiPost(`/api/plugins/install/${stagedId}/confirm`);
      fire(`${verb} complete: ${p.name} ${p.version || ''}`);
      onDone(res);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="cal-modal-bg" onClick={() => !busy && onCancel()}>
      <div className="cal-modal" onClick={(e) => e.stopPropagation()}
           style={{ width: 560, maxWidth: '94vw', maxHeight: '86vh', overflow: 'auto' }}>
        <div className="cal-modal-head">
          <div className="ttl">{verb} “{p.name || 'plugin'}” {p.version || ''}</div>
          <IconButton name="x" onClick={onCancel}/>
        </div>
        <div className="cal-modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* The standing trust statement — always shown, unskippable. */}
          <div style={{ border: '1px solid var(--warn)', background: 'var(--err-soft)',
                        borderRadius: 'var(--r-sm)', padding: '10px 12px', fontSize: 13, lineHeight: 1.5 }}>
            <strong>This plugin runs with full access to your Domovoi server.</strong>{' '}
            It can read and modify your library, database, configuration, and anything
            else this machine can reach. Only install plugins from publishers you trust.
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 6, fontSize: 12 }}>
            <div className="label">publisher</div><div>{p.publisher || '—'}</div>
            <div className="label">license</div><div>{p.license || '—'}</div>
            {sourceLabel && (<><div className="label">source</div><div className="mono" style={{ wordBreak: 'break-all' }}>{sourceLabel}</div></>)}
            <div className="label">description</div><div>{p.description || '—'}</div>
          </div>

          {(Object.keys(perms).some(k => k !== 'warnings' && perms[k]) || (perms.warnings || []).length > 0) && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>what it declares it does</div>
              {Object.entries(PERMISSION_LABELS).map(([k, label]) => perms[k] && (
                <div key={k} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, padding: '3px 0' }}>
                  <Icon name="alert-triangle" size={13}/> {label}
                </div>
              ))}
              {(perms.warnings || []).map((w, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, padding: '3px 0', color: 'var(--warn)' }}>
                  <Icon name="alert-octagon" size={13}/> {w}
                </div>
              ))}
            </div>
          )}

          {(p.handlers || []).length > 0 && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>new voice commands</div>
              {(p.handlers || []).map((h) => (
                <div key={h.name} style={{ padding: '2px 0' }}>
                  {(h.corpus || []).length > 0 ? (
                    <>
                      {h.corpus.map((phrase) => (
                        <div key={phrase} style={{ fontSize: 12, padding: '1px 0' }}>
                          “{phrase}”
                        </div>
                      ))}
                      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', paddingTop: 2 }}>
                        {h.label || h.name} · band {h.band}
                      </div>
                    </>
                  ) : (
                    <div className="mono" style={{ fontSize: 12 }}>
                      {h.label || h.name} · band {h.band}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {((reqs.direct || []).length > 0 || (reqs.transitive || []).length > 0) && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>
                python dependencies it will install ({(reqs.direct || []).length} direct
                · {(reqs.transitive || []).length} resolved)
              </div>
              <div style={{ maxHeight: 140, overflow: 'auto', border: '1px solid var(--border-soft)',
                            borderRadius: 'var(--r-sm)', padding: '6px 10px' }}>
                {(reqs.direct || []).map((d, i) => (
                  <div key={`d${i}`} className="mono" style={{ fontSize: 11 }}>{d}</div>
                ))}
                {(reqs.transitive || []).map((t, i) => (
                  <div key={`t${i}`} className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
                    {t.name}=={t.version}{t.hashed === false ? '  (UNHASHED)' : ''}
                  </div>
                ))}
              </div>
            </div>
          )}

          {typeof p.migration_count === 'number' && p.migration_count > 0 && (
            <div className="meta">creates its own database schema · {p.migration_count} migration{p.migration_count === 1 ? '' : 's'}</div>
          )}
          {(p.capabilities || []).length > 0 && (
            <div className="meta">capabilities: {(p.capabilities || []).join(', ')}</div>
          )}
          {p.downgrade && (
            <div style={{ fontSize: 12, color: 'var(--err)' }}>
              This is a DOWNGRADE — it may reintroduce vulnerabilities fixed in the newer
              version, and migrations never run backwards.
            </div>
          )}
          {err && <div className="err">{err}</div>}
        </div>
        <div className="cal-modal-foot">
          <Button onClick={onCancel} disabled={busy}>cancel</Button>
          <Button variant="primary" icon="shield-check" onClick={confirm} disabled={busy}>
            {busy ? 'installing… (pip + migrations can take a while)' : `I trust this publisher — ${verb}`}
          </Button>
        </div>
      </div>
    </div>
  );
};

/* ---- Uninstall keep-vs-purge dialog --------------------------- */
const UninstallModal = ({ plugin, onClose, onDone, fire }) => {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const { data: purgeInfo } = useApiObject(`/api/plugins/${plugin.slug}/purge-preview`);
  const run = async (data) => {
    setBusy(true); setErr(null);
    try {
      await apiPost(`/api/plugins/${plugin.slug}/uninstall`, { data });
      fire(`uninstalled ${plugin.name}${data === 'purge' ? ' — data purged' : ' — data kept'}`);
      onDone();
    } catch (e) {
      setErr(String(e.message || e));
      setBusy(false);
    }
  };
  const tables = (purgeInfo && purgeInfo.tables) || [];
  return (
    <div className="cal-modal-bg" onClick={() => !busy && onClose()}>
      <div className="cal-modal" onClick={(e) => e.stopPropagation()} style={{ width: 460, maxWidth: '94vw' }}>
        <div className="cal-modal-head">
          <div className="ttl">uninstall “{plugin.name}”</div>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div className="cal-modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13 }}>
            Keep the plugin's data (its database schema and data directory) so a later
            reinstall picks up where it left off — or purge everything it stored.
          </div>
          {tables.length > 0 && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>purge would drop {purgeInfo.schema}</div>
              {tables.map((t) => (
                <div key={t.table} className="mono" style={{ fontSize: 12 }}>
                  {t.table} · {t.rows} row{t.rows === 1 ? '' : 's'}
                </div>
              ))}
            </div>
          )}
          {err && <div className="err">{err}</div>}
        </div>
        <div className="cal-modal-foot">
          <Button onClick={onClose} disabled={busy}>cancel</Button>
          <Button icon="archive" onClick={() => run('keep')} disabled={busy}>uninstall · keep data</Button>
          <Button variant="primary" icon="trash-2" onClick={() => run('purge')} disabled={busy}
                  style={{ background: 'var(--err)', borderColor: 'transparent' }}>
            uninstall · purge data
          </Button>
        </div>
      </div>
    </div>
  );
};

/* ---- Install / upgrade launcher ------------------------------- */
/* Phase A of the two-phase flow: POST the zip (multipart) or the
 * GitHub URL (JSON) to /api/plugins/install (or .../{slug}/upgrade),
 * then hand the returned {staged_id, preview} to TrustConfirmModal. */
const useInstallFlow = (fire, refresh) => {
  const [staged, setStaged] = React.useState(null);  // {stagedId, preview, sourceLabel, verb}
  const fileInputRef = React.useRef(null);
  const upgradeSlugRef = React.useRef(null);

  /* On an auth failure, wait for the login modal to succeed and retry
   * ONCE — the user's action resumes instead of silently dying after
   * they sign in (bearer lives in JS memory, so any page refresh drops
   * it and the next mutation 403s). */
  const _retryAfterLogin = async (e, retryFn) => {
    if (e.status !== 401 && e.status !== 403) return false;
    const ok = await Auth.ensureLoggedIn();
    if (ok) { await retryFn(); return true; }
    fire('sign-in cancelled — the install was not started');
    return true;
  };

  const stageZip = async (file, slug, retried = false) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const path = slug ? `/api/plugins/${slug}/upgrade` : '/api/plugins/install';
    try {
      fire('validating zip…');
      const res = await apiUpload(path, fd);
      setStaged({
        stagedId: res.staged_id, preview: res.preview,
        sourceLabel: file.name, verb: slug ? 'upgrade' : 'install',
      });
    } catch (e) {
      if (!retried && await _retryAfterLogin(e, () => stageZip(file, slug, true))) return;
      // Installing a zip whose slug is already installed: re-stage the
      // same upload as an UPGRADE of that slug instead of dead-ending.
      const errInfo = (((e.detail || {}).detail || {}).error) || {};
      const existingSlug = (errInfo.details || {}).slug;
      if (!slug && errInfo.code === 'slug_exists' && existingSlug) {
        fire(`${existingSlug} is already installed — staging as an upgrade`);
        return stageZip(file, existingSlug, retried);
      }
      fire(`validation failed: ${e.message}`);
    }
  };

  const stageGithub = async (url, slug, retried = false) => {
    const path = slug ? `/api/plugins/${slug}/upgrade` : '/api/plugins/install';
    try {
      fire('downloading + validating…');
      const res = await apiPost(path, { github_url: url });
      setStaged({
        stagedId: res.staged_id, preview: res.preview,
        sourceLabel: url, verb: slug ? 'upgrade' : 'install',
      });
    } catch (e) {
      if (!retried && await _retryAfterLogin(e, () => stageGithub(url, slug, true))) return;
      fire(`validation failed: ${e.message}`);
    }
  };

  const pickZip = (slug = null) => {
    upgradeSlugRef.current = slug;
    if (fileInputRef.current) fileInputRef.current.click();
  };

  const onFilePicked = (fileList) => {
    const file = fileList && fileList[0];
    if (file) stageZip(file, upgradeSlugRef.current);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const modal = staged && (
    <TrustConfirmModal
      stagedId={staged.stagedId} preview={staged.preview}
      sourceLabel={staged.sourceLabel} verb={staged.verb}
      fire={fire}
      onCancel={() => setStaged(null)}
      onDone={() => { setStaged(null); refresh(); }}/>
  );
  const fileInput = (
    <input ref={fileInputRef} type="file" accept=".zip,application/zip"
           style={{ display: 'none' }} onChange={(e) => onFilePicked(e.target.files)}/>
  );
  return { pickZip, stageGithub, modal, fileInput };
};

/* Live view of the browser-side plugin error pipeline (index.html's
   window.DomovoiPluginErrors) for one plugin. These are the failures the
   server can't see: the plugin's JSX failing to load/transform in this
   browser, or its page throwing while rendering. */
const useBrowserPluginErrors = (slug) => {
  const [, force] = React.useReducer((x) => x + 1, 0);
  React.useEffect(() => {
    const store = window.DomovoiPluginErrors;
    return store ? store.subscribe(force) : undefined;
  }, []);
  return (window.DomovoiPluginErrors && window.DomovoiPluginErrors.for(slug)) || [];
};

const PHASE_LABEL = { load: 'failed to load in your browser', render: 'crashed while rendering' };

/* ---- One installed-plugin row --------------------------------- */
const PluginRow = ({ p, onEnable, onDisable, onUninstall, onUpgradeZip, onUpgradeUrl }) => {
  const [open, setOpen] = React.useState(false);
  const [ghUrl, setGhUrl] = React.useState('');
  const pill = STATUS_PILL[p.status] || { tone: 'idle', label: p.status };
  const perms = p.permissions || {};
  const activePerms = Object.keys(PERMISSION_LABELS).filter((k) => perms[k]);
  const browserErrors = useBrowserPluginErrors(p.slug);
  return (
    <div style={{ borderBottom: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', cursor: 'pointer' }}
           onClick={() => setOpen(!open)}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14}/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>{p.name}</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>v{p.version}</span>
            {p.bundled && <Pill tone="idle">bundled</Pill>}
            {p.install_source === 'dev' && <Pill tone="warn">DEV</Pill>}
            <Pill tone={pill.tone}>{pill.label}</Pill>
            {!p.enabled && p.status !== 'uninstalled' && <Pill tone="idle">disabled</Pill>}
          </div>
          <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            {p.publisher || 'unknown publisher'} · {p.license || 'no license'}
            {activePerms.length > 0 && <> · {activePerms.join(', ')}</>}
          </div>
          {p.last_error && (
            <div className="mono" style={{ fontSize: 11, color: 'var(--err)', marginTop: 2 }}>
              server: {p.last_error}
            </div>
          )}
          {p.web_load_error && (
            <div className="mono" style={{ fontSize: 11, color: 'var(--err)', marginTop: 2 }}>
              server web module: {p.web_load_error}
            </div>
          )}
          {browserErrors.map((e, i) => (
            <div key={i} className="mono" style={{ fontSize: 11, color: 'var(--err)', marginTop: 2 }}>
              browser ({PHASE_LABEL[e.phase] || e.phase}): {e.message}{e.count > 1 ? ` ·×${e.count}` : ''}
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6 }} onClick={(e) => e.stopPropagation()}>
          {p.status === 'uninstalled' ? (
            <Button icon="rotate-ccw" onClick={() => onEnable(p)}>reinstall</Button>
          ) : p.enabled ? (
            <Button icon="pause" onClick={() => onDisable(p)}>disable</Button>
          ) : (
            <Button icon="play" onClick={() => onEnable(p)}>enable</Button>
          )}
          {p.status !== 'uninstalled' && (
            <Button icon="trash-2" onClick={() => onUninstall(p)}>uninstall</Button>
          )}
        </div>
      </div>
      {open && (
        <div style={{ padding: '0 16px 14px 42px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {p.description && <div style={{ fontSize: 12, color: 'var(--fg-muted)', maxWidth: 640 }}>{p.description}</div>}
          <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', rowGap: 4, fontSize: 12 }}>
            <div className="label">slug</div><div className="mono">{p.slug}</div>
            <div className="label">installed from</div>
            <div className="mono">{p.install_source}{p.source_ref ? ` · ${p.source_ref}` : ''}</div>
            {(p.provides || []).length > 0 && (<>
              <div className="label">provides</div><div className="mono">{p.provides.join(', ')}</div>
            </>)}
            {(p.handlers || []).length > 0 && (<>
              <div className="label">voice commands</div>
              <div className="mono">
                {p.handlers.map(h => {
                  const c = h.corpus || [];
                  if (c.length === 0) return `${h.name} (band ${h.band})`;
                  const more = c.length > 1 ? ` +${c.length - 1} more` : '';
                  return `“${c[0]}”${more} (${h.label || h.name})`;
                }).join(' · ')}
              </div>
            </>)}
            {(p.pages || []).length > 0 && (<>
              <div className="label">dashboard pages</div>
              <div className="mono">{p.pages.map(pg => `#${pg.route}`).join(', ')}</div>
            </>)}
            {(p.android_capabilities || []).length > 0 && (<>
              <div className="label">android</div>
              <div className="mono">{p.android_capabilities.join(', ')}</div>
            </>)}
          </div>
          {(perms.warnings || []).length > 0 && (
            <div>
              {(perms.warnings || []).map((w, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--warn)', padding: '2px 0' }}>
                  <Icon name="alert-octagon" size={13}/> {w}
                </div>
              ))}
            </div>
          )}
          {p.status !== 'uninstalled' && p.install_source !== 'dev' && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button icon="arrow-up-circle" onClick={() => onUpgradeZip(p.slug)}>upgrade from zip</Button>
              <input placeholder="https://github.com/org/repo[@ref]" value={ghUrl}
                     onChange={(e) => setGhUrl(e.target.value)}
                     style={{ font: 'inherit', fontSize: 12, height: 28, padding: '0 10px', width: 280,
                              borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                              background: 'var(--card)', color: 'var(--fg)' }}/>
              <Button icon="github" disabled={!ghUrl.trim()}
                      onClick={() => { onUpgradeUrl(p.slug, ghUrl.trim()); setGhUrl(''); }}>
                upgrade from GitHub
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/* ---- The page -------------------------------------------------- */
const PluginsPage = () => {
  const [fire, toastNode] = useToast();
  const { data, refresh } = useApiObject('/api/plugins', { eventTypes: ['plugins.changed'] });
  const plugins = (data && data.plugins) || [];
  const flow = useInstallFlow(fire, refresh);
  const [ghUrl, setGhUrl] = React.useState('');
  const [uninstalling, setUninstalling] = React.useState(null);

  const onEnable = async (p) => {
    try { await apiPost(`/api/plugins/${p.slug}/enable`); fire(`enabled ${p.name}`); refresh(); }
    catch (e) { fire(`enable failed: ${e.message}`); }
  };
  const onDisable = async (p) => {
    try { await apiPost(`/api/plugins/${p.slug}/disable`); fire(`disabled ${p.name}`); refresh(); }
    catch (e) { fire(`disable failed: ${e.message}`); }
  };

  return (
    <div className="page">
      <PageHeader
        title="Plugins"
        sub="voice commands, workers, dashboard pages and Android screens — installable, from publishers you choose to trust"
        actions={
          <>
            {flow.fileInput}
            <Button variant="primary" icon="upload" onClick={() => flow.pickZip(null)}>
              Install from zip
            </Button>
          </>
        }
      />

      <Card title="install from GitHub"
            sub="public repos only — the repo (or release tag) must contain a domovoi-plugin.toml at its root">
        <div style={{ padding: '12px 16px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input placeholder="https://github.com/org/repo[@ref]" value={ghUrl}
                 onChange={(e) => setGhUrl(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Enter' && ghUrl.trim()) { flow.stageGithub(ghUrl.trim(), null); setGhUrl(''); } }}
                 style={{ flex: '1 1 300px', maxWidth: 480, font: 'inherit', fontSize: 13, height: 30,
                          padding: '0 10px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)' }}/>
          <Button variant="primary" icon="github" disabled={!ghUrl.trim()}
                  onClick={() => { flow.stageGithub(ghUrl.trim(), null); setGhUrl(''); }}>
            fetch & preview
          </Button>
          <span className="meta">nothing runs until you confirm the trust screen</span>
        </div>
      </Card>

      <Card title="installed" sub={`${plugins.length} plugin${plugins.length === 1 ? '' : 's'} registered`}>
        {plugins.length === 0 ? (
          <Empty title="no plugins installed"
                 sub="install one from a zip or a GitHub URL — bundled plugins appear here automatically on first boot"/>
        ) : (
          <div>
            {plugins.map((p) => (
              <PluginRow key={p.slug} p={p}
                         onEnable={onEnable} onDisable={onDisable}
                         onUninstall={setUninstalling}
                         onUpgradeZip={(slug) => flow.pickZip(slug)}
                         onUpgradeUrl={(slug, url) => flow.stageGithub(url, slug)}/>
            ))}
          </div>
        )}
      </Card>

      <div className="meta" style={{ maxWidth: 680 }}>
        Plugins are ordinary Python running inside your Domovoi server — there is no
        sandbox. The admin password gates who can install; the trust screen tells you
        what you're agreeing to. Code changes to an already-loaded plugin need a core
        restart to take effect.
      </div>

      {flow.modal}
      {uninstalling && (
        <UninstallModal plugin={uninstalling} fire={fire}
                        onClose={() => setUninstalling(null)}
                        onDone={() => { setUninstalling(null); refresh(); }}/>
      )}
      {toastNode}
    </div>
  );
};

window.PluginsPage = PluginsPage;
