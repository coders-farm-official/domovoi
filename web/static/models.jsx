/* Models page — the model-management hub.
 *
 * One page, top-down:
 *   1. Hardware panel   — per-GPU VRAM/util/temp + CPU/RAM/disk. The
 *                         denominator for every fit badge.
 *   2. Active models    — Q&A / tool-routing / speech-to-text role slots,
 *                         each with the current model + a switch picker
 *                         (restart badge where the tier needs it).
 *   3. Installed        — on-disk Ollama models, loaded-in-VRAM marker, delete.
 *   4. Browse & install — curated catalog cards (fit badge + install) and a
 *                         pull-by-name box; installs run as background jobs
 *                         with a live progress bar over the model_jobs bus.
 *   5. STT catalog      — the Whisper size/compute set; select → whisper_model.
 *   6. TTS + wake       — folded-in summaries reusing the existing pages,
 *                         with links to their full management tabs.
 *
 * Fit badges are CONSERVATIVE, labeled estimates: model VRAM need vs. the
 * largest single GPU's free VRAM (a model loads on one GPU unless split).
 *
 * Conventions match the rest of the bundle: inline styles + CSS vars, the
 * data.js hooks, the components.jsx primitives, no emoji in chrome.
 */

/* ---- fit math -------------------------------------------------- */
/* All sizes in GB. `freeGb` = largest single GPU's free VRAM (or null when
 * no GPU is visible). `ramFreeGb` is the CPU-offload ceiling. Deliberately
 * cautious: "fits" leaves headroom for the KV cache / context growth. */
const _fitBadge = (estGb, freeGb, ramFreeGb) => {
  if (estGb == null) return { label: 'size unknown', tone: 'idle' };
  if (freeGb == null) return { label: `~${estGb} GB`, tone: 'idle' };
  if (estGb <= freeGb * 0.85) return { label: 'fits', tone: 'ok' };
  if (estGb <= freeGb) return { label: 'tight', tone: 'warn' };
  if (ramFreeGb != null && estGb <= freeGb + ramFreeGb)
    return { label: 'spills to CPU', tone: 'warn' };
  return { label: "won't fit", tone: 'err' };
};

const _gb = (bytes) => (bytes == null ? null : bytes / (1024 * 1024 * 1024));
const _fmtGb = (gb) => (gb == null ? '—' : `${gb.toFixed(1)} GB`);

/* A labeled fit pill + an "estimate" hint on hover. */
const FitBadge = ({ estGb, hw }) => {
  const freeGb = hw ? hw.largestFreeGb : null;
  const b = _fitBadge(estGb, freeGb, hw ? hw.ramFreeGb : null);
  return (
    <span title={freeGb != null
      ? `estimate: needs ~${estGb ?? '?'} GB vs ${freeGb.toFixed(1)} GB free on the largest GPU`
      : 'no GPU detected — VRAM fit unknown'}>
      <Pill tone={b.tone}>{b.label}</Pill>
    </span>
  );
};

/* ---- hardware panel ------------------------------------------- */

const _bar = (pct, tone) => (
  <div style={{ height: 6, borderRadius: 4, background: 'var(--border-soft)', overflow: 'hidden' }}>
    <div style={{ height: '100%', width: `${Math.max(0, Math.min(100, pct || 0))}%`,
                  background: `var(--${tone || 'brand'})`, transition: 'width .4s ease' }}/>
  </div>
);

const GpuCard = ({ g }) => {
  const usedGb = (g.mem_used_mb || 0) / 1024;
  const totalGb = (g.mem_total_mb || 0) / 1024;
  const memPct = totalGb ? (usedGb / totalGb) * 100 : 0;
  const hot = (g.temp_c || 0) >= 80;
  const busy = (g.util_pct || 0) >= 5;
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: 12,
                  background: 'var(--card)', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="cpu" size={15}/>
        <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.name}</span>
        <Pill tone={busy ? 'live' : 'idle'} live={busy}>{g.util_pct}%</Pill>
      </div>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11,
                      color: 'var(--fg-muted)', marginBottom: 4 }}>
          <span>VRAM</span>
          <span className="mono">{usedGb.toFixed(1)} / {totalGb.toFixed(1)} GB</span>
        </div>
        {_bar(memPct, memPct > 90 ? 'err' : memPct > 70 ? 'warn' : 'brand')}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--fg-muted)' }}>
        <span>free <span className="mono" style={{ color: 'var(--ok)' }}>{(totalGb - usedGb).toFixed(1)} GB</span></span>
        <span style={{ color: hot ? 'var(--err)' : 'var(--fg-muted)' }}>{g.temp_c}°C</span>
      </div>
    </div>
  );
};

const HardwarePanel = ({ data, loading }) => {
  const gpus = (data && data.gpus) || [];
  const cpu = data && data.cpu;
  const ram = data && data.ram;
  const disk = data && data.disk;
  return (
    <Card title="Hardware" sub="Live host readout — the denominator for every fit estimate below.">
      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {loading && !data ? (
          <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>reading hardware…</div>
        ) : gpus.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>
            No GPU detected (nvidia-smi unavailable or CPU-only host) — fit badges will show as unknown.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 10 }}>
            {gpus.map((g, i) => <GpuCard key={i} g={g}/>)}
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
          {cpu && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--fg-muted)' }}>
                <span>CPU{cpu.logical_cores ? ` · ${cpu.logical_cores} threads` : ''}</span>
                <span className="mono">{cpu.percent}%</span>
              </div>
              {_bar(cpu.percent, cpu.percent > 85 ? 'warn' : 'brand')}
            </div>
          )}
          {ram && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--fg-muted)' }}>
                <span>RAM</span>
                <span className="mono">{(ram.used_mb / 1024).toFixed(1)} / {(ram.total_mb / 1024).toFixed(0)} GB</span>
              </div>
              {_bar(ram.percent, ram.percent > 90 ? 'err' : ram.percent > 75 ? 'warn' : 'brand')}
            </div>
          )}
          {disk && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--fg-muted)' }}>
                <span>Model disk free</span>
                <span className="mono">{(disk.free_mb / 1024).toFixed(0)} GB free</span>
              </div>
              {_bar(disk.percent, disk.percent > 90 ? 'err' : disk.percent > 75 ? 'warn' : 'brand')}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
};

/* ---- active model rows ---------------------------------------- */

const ROLE_LABEL = { qa: 'Q&A', tool: 'Tool routing', stt: 'Speech-to-text' };
const ROLE_SUB = {
  qa: "conversational fallthrough — 'tell me a joke'",
  tool: 'routes voice commands to handlers',
  stt: 'Whisper transcription',
};

const ActiveRow = ({ r, installed, whisperNames, hw, catalogByName, onSwitch }) => {
  const [pick, setPick] = React.useState(r.model || '');
  React.useEffect(() => { setPick(r.model || ''); }, [r.model]);

  const options = r.role === 'stt'
    ? whisperNames
    : installed.map((m) => m.name);
  // Always include the current model even if it isn't in the option source
  // (e.g. active-but-not-installed, or a whisper size not in the catalog).
  const opts = Array.from(new Set([r.model, ...options].filter(Boolean)));

  const est = (() => {
    const c = catalogByName[r.model];
    if (c) return c.est_vram_gb;
    if (r.role !== 'stt') {
      const inst = installed.find((m) => m.name === r.model);
      return inst ? _gb(inst.size_bytes) : null;
    }
    return null;
  })();

  const changed = pick && pick !== r.model;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px',
                  borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
      <div style={{ minWidth: 130 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{ROLE_LABEL[r.role]}</div>
        <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{ROLE_SUB[r.role]}</div>
      </div>
      <div style={{ flex: 1, minWidth: 160, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="mono" style={{ fontSize: 13, fontWeight: 500 }}>{r.model || '—'}</span>
        {est != null && <FitBadge estGb={Math.round(est * 10) / 10} hw={hw}/>}
        {r.tier === 'restart' && <Pill tone="warn">restart to apply</Pill>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <select value={pick} onChange={(e) => setPick(e.target.value)}
                style={{ font: 'inherit', fontSize: 13, height: 30, padding: '0 8px',
                         borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                         background: 'var(--card)', color: 'var(--fg)', maxWidth: 220 }}>
          {opts.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <Button variant={changed ? 'primary' : 'secondary'} icon="check"
                disabled={!changed} onClick={() => onSwitch(r, pick)}>Switch</Button>
      </div>
    </div>
  );
};

/* ---- installed list ------------------------------------------- */

const InstalledRow = ({ m, hw, onDelete }) => {
  const gb = _gb(m.size_bytes);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px',
                  borderBottom: '1px solid var(--border-soft)' }}>
      <span className="mono" style={{ fontSize: 13, fontWeight: 500, flex: 1, minWidth: 0,
                                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {m.name}
      </span>
      {m.loaded && <Pill tone="live" live>loaded</Pill>}
      {m.quant && <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{m.quant}</span>}
      <span className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)', width: 74, textAlign: 'right' }}>
        {_fmtGb(gb)}
      </span>
      {gb != null && <FitBadge estGb={Math.round(gb * 10) / 10} hw={hw}/>}
      <IconButton name="trash-2" title="delete from disk" onClick={() => onDelete(m)}/>
    </div>
  );
};

/* ---- pull progress ------------------------------------------- */

const PullJob = ({ j, onCancel }) => {
  const active = j.status === 'pending' || j.status === 'running';
  const tone = j.status === 'done' ? 'ok' : j.status === 'failed' ? 'err'
    : j.status === 'cancelled' ? 'idle' : 'brand';
  return (
    <div style={{ padding: '9px 14px', borderBottom: '1px solid var(--border-soft)',
                  display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="mono" style={{ fontSize: 13, fontWeight: 500, flex: 1 }}>{j.model}</span>
        <Pill tone={tone} live={active}>{j.status}</Pill>
        {j.pct != null && <span className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{j.pct}%</span>}
        {active && <IconButton name="x" title="cancel pull" onClick={() => onCancel(j)}/>}
      </div>
      {active && _bar(j.pct, 'brand')}
      <div style={{ fontSize: 11, color: j.status === 'failed' ? 'var(--err)' : 'var(--fg-faint)' }}>
        {j.error || j.status_text || (active ? 'starting…' : '')}
      </div>
    </div>
  );
};

/* ---- catalog cards ------------------------------------------- */

const _ROLE_TAG = { qa: 'Q&A', tool: 'tool', both: 'Q&A · tool', embedding: 'embedding', stt: 'STT' };

const CatalogCard = ({ m, hw, installedNames, pulling, onInstall }) => {
  const isInstalled = installedNames.has(m.name);
  const isPulling = pulling.has(m.name);
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: 12,
                  background: 'var(--card)', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="mono" style={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0,
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {m.name}
        </span>
        <Pill tone="idle">{_ROLE_TAG[m.role] || m.role}</Pill>
      </div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.45, minHeight: 34 }}>{m.desc}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {m.params && <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{m.params}</span>}
        {m.quant && <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{m.quant}</span>}
        <FitBadge estGb={m.est_vram_gb} hw={hw}/>
      </div>
      <div style={{ marginTop: 2 }}>
        {isInstalled
          ? <Pill tone="ok">installed</Pill>
          : <Button variant="primary" icon="download" disabled={isPulling}
                    onClick={() => onInstall(m.name)}>{isPulling ? 'installing…' : 'Install'}</Button>}
      </div>
    </div>
  );
};

/* ---- STT catalog rows ---------------------------------------- */

const SttRow = ({ m, hw, active, onSelect }) => {
  const isActive = active === m.name;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px',
                  borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
      <span className="mono" style={{ fontSize: 13, fontWeight: 500, minWidth: 130 }}>{m.name}</span>
      <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{m.compute}</span>
      <span style={{ fontSize: 12, color: 'var(--fg-muted)', flex: 1, minWidth: 140 }}>{m.accuracy}</span>
      <FitBadge estGb={m.est_vram_gb} hw={hw}/>
      {isActive
        ? <Pill tone="live">active</Pill>
        : <Button variant="secondary" onClick={() => onSelect(m.name)}>Use (restart)</Button>}
    </div>
  );
};

/* ---- folded summaries: TTS voices + wake words --------------- */

const FoldedSummary = ({ icon, title, sub, count, countLabel, tab }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px',
                borderBottom: '1px solid var(--border-soft)' }}>
    <Icon name={icon} size={18}/>
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{sub}</div>
    </div>
    {count != null && (
      <span className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
        {count} {countLabel}
      </span>
    )}
    <Button variant="secondary" icon="external-link"
            onClick={() => { window.location.hash = '#settings'; }}>Manage</Button>
  </div>
);

/* ============================================================== */
/* Page                                                           */
/* ============================================================== */

/* Rendered as a tab on the Settings page (not a top-level nav route), so it
 * returns a bare fragment — the Settings shell supplies the .page wrapper and
 * the page header. */
const ModelsPanel = () => {
  const [fire, node] = useToast();

  const { data: hwData, loading: hwLoading, refresh: refreshHw } =
    useApiObject('/api/models/hardware');
  const { data: catalog } = useApiObject('/api/models/catalog');
  const { data: installedData, refresh: refreshInstalled } =
    useApiObject('/api/models/installed', { eventTypes: ['model_jobs.changed'] });
  const { data: activeData, refresh: refreshActive } = useApiObject('/api/models/active');
  const { items: jobs } = useApiList('/api/models/jobs', { eventTypes: ['model_jobs.changed'],
    pickItems: (d) => (d && d.jobs) || [] });

  // Re-poll hardware on a light cadence so VRAM/util stay live-ish.
  React.useEffect(() => {
    const t = setInterval(refreshHw, 5000);
    return () => clearInterval(t);
  }, [refreshHw]);

  // Derive the fit denominator: largest single GPU's free VRAM + a CPU
  // offload ceiling (free system RAM).
  const hw = React.useMemo(() => {
    if (!hwData) return null;
    const gpus = hwData.gpus || [];
    const largestFreeGb = gpus.length
      ? Math.max(...gpus.map((g) => (g.mem_free_mb || 0) / 1024))
      : null;
    const ram = hwData.ram;
    const ramFreeGb = ram ? (ram.total_mb - ram.used_mb) / 1024 : null;
    return { ...hwData, largestFreeGb, ramFreeGb };
  }, [hwData]);

  const installed = (installedData && installedData.installed) || [];
  const ollamaReachable = installedData ? installedData.ollama_reachable : true;
  const installedNames = new Set(installed.map((m) => m.name));
  const roles = (activeData && activeData.roles) || [];
  const catOllama = (catalog && catalog.ollama) || [];
  const catWhisper = (catalog && catalog.whisper) || [];
  const catalogByName = {};
  catOllama.forEach((m) => { catalogByName[m.name] = m; });

  const sttActive = (roles.find((r) => r.role === 'stt') || {}).model;
  const whisperNames = Array.from(new Set(catWhisper.map((m) => m.name)));

  // Models with a live/recent pull job, so catalog cards show "installing…".
  const pulling = new Set(jobs.filter((j) => j.status === 'pending' || j.status === 'running')
    .map((j) => j.model));

  const guard = async (fn, okMsg) => {
    try {
      const r = await fn();
      if (okMsg) fire(typeof okMsg === 'function' ? okMsg(r) : okMsg);
      return r;
    } catch (e) {
      fire(`failed: ${e.message || e}`);
    }
  };

  const switchModel = (r, model) =>
    guard(async () => {
      const res = await apiPost('/api/models/active', { role: r.role, model });
      await refreshActive();
      return res;
    }, (res) => {
      const restart = res && res.restart_required && res.restart_required.length;
      return restart ? `saved — restart the Domovoi server to apply ${model}` : `switched to ${model}`;
    });

  const install = (name) =>
    guard(async () => { await apiPost('/api/models/pull', { model: name }); }, `pulling ${name}…`);

  const cancel = (j) =>
    guard(async () => { await apiPost(`/api/models/pull/${j.id}/cancel`, {}); }, 'pull cancelled');

  const del = (m) => {
    if (!window.confirm(`Delete ${m.name} from disk? It can be re-pulled later.`)) return;
    return guard(async () => {
      await apiDelete(`/api/models/${encodeURIComponent(m.name)}`);
      await refreshInstalled();
    }, `deleted ${m.name}`);
  };

  const [pullName, setPullName] = React.useState('');
  const submitPull = (e) => {
    e?.preventDefault?.();
    const n = pullName.trim();
    if (!n) return;
    install(n);
    setPullName('');
  };

  return (
    <React.Fragment>
      <HardwarePanel data={hw} loading={hwLoading}/>

      <Card title="Active models" sub="One model per role. Switching writes config — Ollama applies instantly; Whisper needs a restart.">
        {roles.length === 0
          ? <div style={{ padding: 14, fontSize: 12, color: 'var(--fg-muted)' }}>
              {activeData === null ? 'domovoi unreachable — active models unavailable' : 'loading…'}
            </div>
          : roles.map((r) => (
              <ActiveRow key={r.role} r={r} installed={installed} whisperNames={whisperNames}
                         hw={hw} catalogByName={catalogByName} onSwitch={switchModel}/>
            ))}
      </Card>

      {jobs.length > 0 && (
        <Card title="Installs in progress" sub="Ollama pulls run in the background — you can leave this page.">
          {jobs.map((j) => <PullJob key={j.id} j={j} onCancel={cancel}/>)}
        </Card>
      )}

      <Card title={`Installed (${installed.length})`}
            sub="On-disk Ollama models. 'loaded' means it's resident in VRAM right now.">
        {!ollamaReachable ? (
          <Empty title="Ollama offline" sub="The local Ollama server isn't reachable — start it to list installed models."/>
        ) : installed.length === 0 ? (
          <Empty title="No models installed" sub="Install one from the catalog below."/>
        ) : (
          installed.map((m) => <InstalledRow key={m.name} m={m} hw={hw} onDelete={del}/>)
        )}
      </Card>

      <Card title="Browse & install"
            sub="Curated Ollama models with fit estimates. Or pull anything by name.">
        <form onSubmit={submitPull}
              style={{ display: 'flex', gap: 8, padding: '10px 14px', alignItems: 'center',
                       flexWrap: 'wrap', borderBottom: '1px solid var(--border-soft)' }}>
          <input value={pullName} onChange={(e) => setPullName(e.target.value)}
                 placeholder="Pull by name (e.g. qwen2.5:7b)"
                 style={{ font: 'inherit', fontSize: 13, padding: '7px 10px', borderRadius: 'var(--r-sm)',
                          border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--fg)',
                          flex: 1, minWidth: 220 }}/>
          <Button variant="primary" icon="download" type="submit">Install</Button>
        </form>
        <div style={{ padding: 14, display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: 10 }}>
          {catOllama.map((m) => (
            <CatalogCard key={m.name} m={m} hw={hw} installedNames={installedNames}
                         pulling={pulling} onInstall={install}/>
          ))}
        </div>
      </Card>

      <Card title="Speech-to-text (Whisper)"
            sub="Selecting a size writes whisper_model — a restart-tier change. int8 halves VRAM at near-identical accuracy.">
        {catWhisper.map((m, i) => (
          <SttRow key={`${m.name}-${m.compute}-${i}`} m={m} hw={hw} active={sttActive}
                  onSelect={(name) => switchModel({ role: 'stt' }, name)}/>
        ))}
      </Card>

      <Card title="Voices & wake words"
            sub="Managed in full on the Settings page — surfaced here so everything model-shaped is in one place.">
        <FoldedSummary icon="volume-2" title="TTS voices"
          sub="Edge (cloud) + Piper (local) voice registry — each satellite speaks in one." tab="voices"/>
        <FoldedSummary icon="mic" title="Wake words"
          sub="Custom openWakeWord models — record clips on a satellite, train, push." tab="wakewords"/>
      </Card>

      {node}
    </React.Fragment>
  );
};

window.ModelsPanel = ModelsPanel;
