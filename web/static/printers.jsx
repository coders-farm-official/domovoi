/* Printers and scanners — Settings tab, the Print dialog and the Scan sheet.
 *
 * Domovoi prints to and scans from NETWORK printers only (AirPrint, IPP
 * Everywhere, Mopria, eSCL scanners); USB and driver-only printers are not
 * supported on any host. Everything here talks to the web backend's
 * /api/printers, /api/print and /api/scan routes (web/backend/api/printers.py),
 * which proxy the core.
 *
 * Globals this file adds (page scripts share ONE global scope, so every
 * top-level name is prefixed):
 *   PrintingAvailability, usePrintingAvailability, printingKindFor,
 *   printingErrorText, PrintFileButton, PrintDialog, ScanSheet,
 *   PrintingScanButton, PrintersPanel and their private Printing* / Printer*
 *   helpers.
 *
 * Strings that come from a printer (model, location, status message, supply
 * names) are data: React escapes them, and nothing here renders HTML.
 *
 * No object-rest destructuring (`{ a, ...rest }`) anywhere in this file: the
 * in-browser Babel compiles it to a top-level `const _excluded`, which
 * components.jsx already declares (domovoi/tests/test_web_script_scope.py).
 */

/* ---- Availability: is there a printer / scanner, what prints --------- */
/* One GET /api/printers shared by every Print and Scan button on the page
 * (a Files folder has a row per file), kept for a short while. Fails
 * closed: an error, a guest or a refusal is "no printer", so no button. */
const PRINTING_AVAIL_TTL_MS = 30000;
const PrintingAvailability = (() => {
  let data = null;      // null = not asked yet; false = failed; else the answer
  let at = 0;
  let pending = null;
  const subs = new Set();
  const notify = () => subs.forEach((f) => { try { f(); } catch { /* a gone row */ } });
  const set = (d) => { data = d && typeof d === 'object' ? d : false; at = Date.now(); notify(); };
  const load = (force) => {
    if (!force && data !== null && Date.now() - at < PRINTING_AVAIL_TTL_MS) return Promise.resolve(data);
    if (pending) return pending;
    pending = apiGet('/api/printers', { quiet: true })
      .then((d) => { set(d); return data; })
      .catch(() => { set(false); return data; })
      .finally(() => { pending = null; });
    return pending;
  };
  return {
    get: () => data,
    load,
    set,
    subscribe: (f) => { subs.add(f); return () => { subs.delete(f); }; },
  };
})();

const usePrintingAvailability = () => {
  const [, force] = React.useReducer((n) => n + 1, 0);
  React.useEffect(() => {
    const off = PrintingAvailability.subscribe(force);
    PrintingAvailability.load(false);
    return off;
  }, []);
  const d = PrintingAvailability.get();
  if (!d) return { hasPrinter: false, hasScanner: false, printable: null, data: null };
  return { hasPrinter: !!d.has_printer, hasScanner: !!d.has_scanner,
           printable: d.printable || null, data: d };
};

const printingExtOf = (name) => {
  const s = String(name || '');
  const slash = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
  const base = s.slice(slash + 1);
  const i = base.lastIndexOf('.');
  return i > 0 ? base.slice(i).toLowerCase() : '';
};

/* 'print' | 'pdf_first' | null for a file name, from the server's lists. */
const printingKindFor = (printable, name) => {
  if (!printable) return null;
  const ext = printingExtOf(name);
  if (!ext) return null;
  if ((printable.exts || []).includes(ext)) return 'print';
  if ((printable.pdf_first_exts || []).includes(ext)) return 'pdf_first';
  return null;
};

/* The server's own words for a refusal (its `detail`), or null when the
 * sign-in / pair prompt already owns the story. */
const printingErrorText = (e) => {
  const blocked = deviceBlockReason(e);
  if (blocked) return `${blocked}. An admin lifts the block in Settings → Devices.`;
  if (e && e.authCancelled) return e.deviceTokenRequired ? 'canceled — this browser is not paired' : 'canceled — not signed in';
  if (isAuthFailure(e)) return null;
  return apiErrorText(e, 0);
};

const PRINTING_JOB_ENDED = new Set(['done', 'failed', 'canceled']);
const printingPlural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;

/* What the person sees when a job ends ("Printed: Cover letter (2 pages)"). */
const printingJobEndText = (job) => {
  if (!job) return '';
  if (job.state === 'done') return `Printed: ${job.title} (${printingPlural(job.pages || 0, 'page')})`;
  if (job.state === 'canceled') return `Canceled: ${job.title}`;
  return job.error || `${job.printer_name || 'The printer'} stopped the job.`;
};

const PRINTING_SIDES_LABEL = {
  'one-sided': 'one-sided',
  'two-sided-long-edge': 'two-sided (long edge)',
  'two-sided-short-edge': 'two-sided (short edge)',
};
const PRINTING_COLOR_LABEL = { color: 'colour', monochrome: 'black and white' };
const PRINTING_SCAN_SOURCE_LABEL = {
  auto: 'automatic', platen: 'glass', adf: 'feeder', adf_duplex: 'feeder, both sides',
};
const PRINTING_SCAN_COLOR_LABEL = { color: 'colour', grey: 'grey' };
const PRINTING_TONE = { ok: 'ok', warn: 'warn', err: 'err', off: 'idle' };
const PRINTING_HEX = /^#[0-9a-f]{6}$/i;
const printingSafeHref = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);

/* ---- small shared pieces ------------------------------------------- */
const PrintingField = ({ label, children }) => (
  <div className="field" style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
    <label style={{ fontSize: 11, fontWeight: 500, color: 'var(--fg-muted)' }}>{label}</label>
    {children}
  </div>
);

const PrintingMuted = ({ children, mono = false, style = null }) => (
  <div className={mono ? 'mono' : undefined}
       style={{ fontSize: 11, color: 'var(--fg-faint)', lineHeight: 1.45, ...(style || {}) }}>
    {children}
  </div>
);

const PrintingThumbs = ({ thumbs }) => {
  if (!thumbs || !thumbs.length) return null;
  return (
    <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 4 }}>
      {thumbs.map((t) => (
        <div key={t.page} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, flexShrink: 0 }}>
          <img src={t.src} alt={`page ${t.page}`} draggable={false}
               style={{ height: 120, width: 'auto', maxWidth: 110, objectFit: 'contain',
                        border: '1px solid var(--border)', borderRadius: 'var(--r-sm)',
                        background: 'var(--sunken)' }}/>
          <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>{t.page}</span>
        </div>
      ))}
    </div>
  );
};

/* A blocking dialog over whatever opened it (styles.css .cal-modal, z 100:
 * above the editor overlays a Print button can sit in). */
const PrintingModal = ({ title, icon, onClose, foot, wide = false, children }) => {
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="cal-modal-bg" onClick={onClose}>
      <div className="cal-modal" role="dialog" aria-modal="true" aria-label={title}
           onClick={(e) => e.stopPropagation()} style={{ maxWidth: wide ? 600 : 480 }}>
        <div className="cal-modal-head">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            {icon && <Icon name={icon} size={16}/>}
            <span className="ttl" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
          </span>
          <IconButton name="x" title="close" aria-label="close" onClick={onClose}/>
        </div>
        <div className="cal-modal-body">{children}</div>
        {foot && <div className="cal-modal-foot" style={{ flexWrap: 'wrap' }}>{foot}</div>}
      </div>
    </div>
  );
};

const PrintingNotice = ({ tone = 'warn', children }) => (
  <div style={{ padding: '9px 11px', borderRadius: 'var(--r-md)', fontSize: 12, lineHeight: 1.5,
                background: tone === 'err' ? 'var(--err-soft)' : tone === 'ok' ? 'var(--ok-soft)' : 'var(--warn-soft)',
                color: 'var(--fg)', border: '1px solid var(--border)' }}>
    {children}
  </div>
);

/* ==================================================================== *
 *  Print dialog
 * ==================================================================== */
/* Opened with {library_id, path, name}. Preview first (debounced as the
 * options change), then Print; the job's progress line polls every 2 s
 * while the dialog shows it. Closing while the job runs hands the job to
 * onClose(job) so the caller can keep watching it and say how it ended. */
const PrintDialog = ({ library_id, path, name, onClose, fire }) => {
  const avail = usePrintingAvailability();
  const printers = ((avail.data && avail.data.printers) || []).filter((p) => p.can_print);
  const [printerId, setPrinterId] = React.useState(null);   // null = the server's pick
  const [pages, setPages] = React.useState('');
  const [copies, setCopies] = React.useState(1);
  const [sides, setSides] = React.useState(null);
  const [color, setColor] = React.useState(null);
  const [media, setMedia] = React.useState(null);
  const [preview, setPreview] = React.useState(null);
  const [previewErr, setPreviewErr] = React.useState(null);
  const [previewing, setPreviewing] = React.useState(true);
  const [submitting, setSubmitting] = React.useState(false);
  const [submitErr, setSubmitErr] = React.useState(null);
  const [job, setJob] = React.useState(null);
  const seq = React.useRef(0);

  const options = {};
  if (pages.trim()) options.pages = pages.trim();
  if (copies && copies !== 1) options.copies = copies;
  if (sides) options.sides = sides;
  if (color) options.color = color;
  if (media) options.media = media;
  const body = { library_id, path, printer_id: printerId, options };
  const key = JSON.stringify(body);
  const debKey = useDebouncedValue(key, 400);

  React.useEffect(() => {
    const mine = ++seq.current;
    setPreviewing(true);
    apiPost('/api/print/preview', JSON.parse(debKey))
      .then((p) => { if (mine === seq.current) { setPreview(p); setPreviewErr(null); } })
      .catch((e) => { if (mine === seq.current) { setPreview(null); setPreviewErr(printingErrorText(e)); } })
      .finally(() => { if (mine === seq.current) setPreviewing(false); });
  }, [debKey]);

  // The job's progress, every 2 s while this dialog shows it.
  const jobActive = !!job && !PRINTING_JOB_ENDED.has(job.state);
  React.useEffect(() => {
    if (!jobActive) return undefined;
    let stop = false;
    const t = setInterval(async () => {
      try {
        const j = await apiGet(`/api/print/jobs/${encodeURIComponent(job.id)}`, { quiet: true });
        if (!stop && j) setJob(j);
      } catch { /* the next tick tries again */ }
    }, 2000);
    return () => { stop = true; clearInterval(t); };
  }, [jobActive, job && job.id]);

  const ended = !!job && PRINTING_JOB_ENDED.has(job.state);
  const endedRef = React.useRef(null);
  React.useEffect(() => {
    if (ended && endedRef.current !== job.id) {
      endedRef.current = job.id;
      if (fire) fire(printingJobEndText(job));
    }
  }, [ended, job && job.id]);

  const choices = (preview && preview.choices)
    || ((printers.find((p) => p.id === (printerId != null ? printerId : (preview && preview.printer_id))) || {}).choices)
    || {};
  const resolved = (preview && preview.options) || {};
  const sidesList = choices.sides || [];
  const colorList = choices.color || [];
  const mediaList = choices.media || [];
  const copiesMax = choices.copies_max || 1;
  const shownPrinter = printerId != null ? printerId : (preview ? preview.printer_id : '');

  const pickPrinter = (v) => {
    setPrinterId(v === '' ? null : Number(v));
    // Another printer has its own choices: start from its defaults.
    setSides(null); setColor(null); setMedia(null);
  };

  const doPrint = async () => {
    setSubmitting(true); setSubmitErr(null);
    try {
      const j = await apiPost('/api/print/jobs', body);
      setJob(j);
    } catch (e) {
      setSubmitErr(printingErrorText(e));
    } finally { setSubmitting(false); }
  };
  const cancelJob = async () => {
    try {
      const j = await apiPost(`/api/print/jobs/${encodeURIComponent(job.id)}/cancel`, {});
      if (j) setJob(j);
    } catch (e) {
      const msg = printingErrorText(e);
      if (msg) setSubmitErr(msg);
    }
  };
  const close = () => onClose(jobActive ? job : null);

  const stale = previewing || debKey !== key;
  const canPrint = !!preview && !previewErr && !preview.over_limit && !stale && !submitting && !jobActive;
  const title = (preview && preview.title) || name || path;

  return (
    <PrintingModal title={`Print ${title}`} icon="printer" onClose={close} wide
      foot={<>
        <Button icon="x" onClick={close}>{jobActive ? 'Close' : 'Cancel'}</Button>
        <Button variant="primary" icon="printer" disabled={!canPrint} onClick={doPrint}>
          {submitting ? 'Sending…' : (ended ? 'Print again' : 'Print')}
        </Button>
      </>}>
      {printers.length > 1 && (
        <PrintingField label="printer">
          <select className="cal-inp" value={shownPrinter == null ? '' : String(shownPrinter)}
                  onChange={(e) => pickPrinter(e.target.value)}>
            {shownPrinter == null && <option value="">the usual printer</option>}
            {printers.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}{p.status && p.status.state && p.status.state !== 'ready' ? ` · ${p.status.state}` : ''}
              </option>
            ))}
          </select>
        </PrintingField>
      )}
      {printers.length <= 1 && preview && preview.printer_name && (
        <PrintingMuted>to {preview.printer_name}</PrintingMuted>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
        {(!preview || preview.pages_total > 1) && (
          <PrintingField label="pages">
            <input className="cal-inp" type="text" value={pages} placeholder="all (or 1-3, 5)"
                   onChange={(e) => setPages(e.target.value)}/>
          </PrintingField>
        )}
        {copiesMax > 1 && (
          <PrintingField label="copies">
            <input className="cal-inp" type="number" min={1} max={copiesMax} value={copies}
                   onChange={(e) => {
                     const n = Math.round(Number(e.target.value));
                     setCopies(Number.isFinite(n) ? Math.min(copiesMax, Math.max(1, n)) : 1);
                   }}/>
          </PrintingField>
        )}
        {sidesList.length > 1 && (
          <PrintingField label="two-sided">
            <select className="cal-inp" value={sides || resolved.sides || sidesList[0]}
                    onChange={(e) => setSides(e.target.value)}>
              {sidesList.map((s) => <option key={s} value={s}>{PRINTING_SIDES_LABEL[s] || s}</option>)}
            </select>
          </PrintingField>
        )}
        {colorList.length > 1 && (
          <PrintingField label="colour">
            <select className="cal-inp" value={color || resolved.color || colorList[0]}
                    onChange={(e) => setColor(e.target.value)}>
              {colorList.map((c) => <option key={c} value={c}>{PRINTING_COLOR_LABEL[c] || c}</option>)}
            </select>
          </PrintingField>
        )}
        {mediaList.length > 1 && (
          <PrintingField label="paper size">
            <select className="cal-inp" value={media || resolved.media || mediaList[0].id}
                    onChange={(e) => setMedia(e.target.value)}>
              {mediaList.map((m) => <option key={m.id} value={m.id}>{m.label || m.id}</option>)}
            </select>
          </PrintingField>
        )}
      </div>

      {previewErr ? (
        <PrintingNotice tone="err">{previewErr}</PrintingNotice>
      ) : !preview ? (
        <PrintingMuted>making the preview…</PrintingMuted>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, opacity: stale ? 0.6 : 1 }}>
          <PrintingThumbs thumbs={preview.thumbs}/>
          <div style={{ fontSize: 13 }}>{preview.summary}</div>
          {(preview.adjustments || []).map((a, i) => <PrintingNotice key={`a${i}`}>{a}</PrintingNotice>)}
          {(preview.notes || []).map((n, i) => <PrintingMuted key={`n${i}`}>{n}</PrintingMuted>)}
          {preview.over_limit && <PrintingNotice tone="err">{preview.over_limit}</PrintingNotice>}
        </div>
      )}

      {submitErr && <PrintingNotice tone="err">{submitErr}</PrintingNotice>}
      {job && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 13 }}>
          <StatusDot tone={job.state === 'done' ? 'ok' : job.state === 'failed' ? 'err'
                           : job.state === 'canceled' ? 'idle' : 'brand'} live={jobActive}/>
          <span>{ended ? printingJobEndText(job) : (job.progress || job.state)}</span>
          {jobActive && job.can_cancel && (
            <Button icon="x" onClick={cancelJob}>Cancel</Button>
          )}
        </div>
      )}
    </PrintingModal>
  );
};

/* The Print action for one file: a button, the "save it as PDF first" line,
 * or nothing — only with a saved printer and a printable kind of file.
 * A job still running when the dialog closes is watched here (every 5 s)
 * so its end still gets a toast. */
const PrintFileButton = ({ libraryId, path, name, title }) => {
  const avail = usePrintingAvailability();
  const [open, setOpen] = React.useState(false);
  const [bgJob, setBgJob] = React.useState(null);
  const [fire, toastNode] = useToast();
  React.useEffect(() => {
    if (!bgJob) return undefined;
    let stop = false;
    const t = setInterval(async () => {
      try {
        const j = await apiGet(`/api/print/jobs/${encodeURIComponent(bgJob.id)}`, { quiet: true });
        if (stop || !j) return;
        if (PRINTING_JOB_ENDED.has(j.state)) { fire(printingJobEndText(j)); setBgJob(null); }
      } catch { /* try again next tick */ }
    }, 5000);
    return () => { stop = true; clearInterval(t); };
  }, [bgJob && bgJob.id]);

  if (!avail.hasPrinter) return toastNode || null;
  const kind = printingKindFor(avail.printable, name || path);
  if (kind === 'pdf_first') {
    return (
      <span style={{ fontSize: 11, color: 'var(--fg-faint)', lineHeight: 1.35, maxWidth: 260 }}>
        {avail.printable.pdf_first_message}
      </span>
    );
  }
  if (kind !== 'print') return toastNode || null;
  return (
    <>
      <Button icon="printer" title={title || 'print'} onClick={() => setOpen(true)}>Print</Button>
      {open && (
        <PrintDialog library_id={libraryId} path={path} name={name} fire={fire}
                     onClose={(running) => { setOpen(false); if (running) setBgJob(running); }}/>
      )}
      {toastNode}
    </>
  );
};

/* ==================================================================== *
 *  Scan sheet
 * ==================================================================== */
const PRINTING_SCAN_BUSY = new Set(['scanning', 'saving']);

/* Open the saved scan the way the Documents page opens a PDF (a new tab
 * on the /raw endpoint, with the household token in the query). */
const printingOpenScan = (scan) => {
  if (!scan || !scan.path) return;
  if (scan.library_id === 'core:documents' && typeof openDocInNewTab === 'function') {
    openDocInNewTab(scan.path);
    return;
  }
  window.open(withDeviceToken(`${API_BASE}/api/files/download?library_id=${encodeURIComponent(scan.library_id || 'core:documents')}`
    + `&path=${encodeURIComponent(scan.path)}`), '_blank', 'noopener');
};

/* `onRead(scan)` is the seam for "What does it say?": the button shows
 * only when the scan is readable AND a caller passed a reader. */
const ScanSheet = ({ onClose, onDone, onRead, fire }) => {
  const avail = usePrintingAvailability();
  const scanners = ((avail.data && avail.data.printers) || []).filter((p) => p.can_scan);
  const [scannerId, setScannerId] = React.useState(null);
  const scanner = scanners.find((s) => s.id === scannerId) || scanners[0] || null;
  const caps = (scanner && scanner.scan) || {};
  const sources = caps.sources || [];
  const hasFeeder = sources.includes('adf') || sources.includes('adf_duplex');
  const sourceChoices = hasFeeder
    ? ['auto', ...sources.filter((s) => PRINTING_SCAN_SOURCE_LABEL[s])] : [];
  const colorChoices = (caps.color || []).filter((c) => PRINTING_SCAN_COLOR_LABEL[c]);
  const [source, setSource] = React.useState('auto');
  const [color, setColor] = React.useState(null);
  const [scan, setScan] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);

  const state = scan ? scan.state : null;
  const working = PRINTING_SCAN_BUSY.has(state);
  React.useEffect(() => {
    if (!scan || !working) return undefined;
    let stop = false;
    const t = setInterval(async () => {
      try {
        const s = await apiGet(`/api/scan/jobs/${encodeURIComponent(scan.id)}`, { quiet: true });
        if (!stop && s) setScan(s);
      } catch { /* next tick */ }
    }, 1500);
    return () => { stop = true; clearInterval(t); };
  }, [scan && scan.id, working]);

  const doneRef = React.useRef(null);
  React.useEffect(() => {
    if (state === 'done' && doneRef.current !== scan.id) {
      doneRef.current = scan.id;
      if (onDone) onDone(scan);
      if (fire && scan.name) fire(`Saved: ${scan.name}`);
    }
  }, [state, scan && scan.id]);

  const act = async (fn) => {
    setBusy(true); setErr(null);
    try { const s = await fn(); if (s) setScan(s); }
    catch (e) { const m = printingErrorText(e); if (m) setErr(m); }
    finally { setBusy(false); }
  };
  const start = () => act(() => {
    const b = { printer_id: scanner ? scanner.id : null,
                source: hasFeeder ? source : (sources.includes('platen') ? 'platen' : 'auto'),
                color: color || (colorChoices.includes('color') ? 'color' : (colorChoices[0] || 'color')) };
    if ((caps.resolutions || []).includes(300)) b.resolution = 300;
    return apiPost('/api/scan/jobs', b);
  });
  const next = () => act(() => apiPost(`/api/scan/jobs/${encodeURIComponent(scan.id)}/next`, {}));
  const finish = () => act(() => apiPost(`/api/scan/jobs/${encodeURIComponent(scan.id)}/finish`, {}));
  const cancel = () => act(() => apiPost(`/api/scan/jobs/${encodeURIComponent(scan.id)}/cancel`, {}));

  const open = !!scan && !['done', 'failed', 'canceled'].includes(state);
  const close = () => {
    if (open && scan.pages > 0
        && !window.confirm('Throw away the pages scanned so far? Nothing is saved until you press Done.')) return;
    if (open) apiPost(`/api/scan/jobs/${encodeURIComponent(scan.id)}/cancel`, {}).catch(() => {});
    onClose();
  };

  let foot;
  if (!scan || state === 'failed' || state === 'canceled') {
    foot = <>
      <Button icon="x" onClick={close}>Close</Button>
      <Button variant="primary" icon="scan-line" disabled={busy || !scanner} onClick={start}>
        {busy ? 'Scanning…' : (scan ? 'Scan again' : 'Scan')}
      </Button>
    </>;
  } else if (state === 'done') {
    foot = <>
      {scan.readable && onRead && <Button icon="text" onClick={() => onRead(scan)}>What does it say?</Button>}
      <Button icon="external-link" disabled={!scan.path} onClick={() => printingOpenScan(scan)}>Open</Button>
      <Button variant="primary" icon="check" onClick={onClose}>Close</Button>
    </>;
  } else {
    foot = <>
      <Button icon="x" disabled={busy} onClick={cancel}>Cancel</Button>
      {scan.can_scan_more && (
        <Button icon="plus" disabled={busy || working} onClick={next}>Scan another page</Button>
      )}
      <Button variant="primary" icon="check" disabled={busy || working || !scan.pages} onClick={finish}>Done</Button>
    </>;
  }

  return (
    <PrintingModal title="Scan" icon="scan-line" onClose={close} foot={foot} wide>
      {!scan && (
        <>
          {!scanner && <PrintingMuted>there's no scanner set up</PrintingMuted>}
          {scanners.length > 1 && (
            <PrintingField label="scanner">
              <select className="cal-inp" value={scanner ? String(scanner.id) : ''}
                      onChange={(e) => { setScannerId(Number(e.target.value)); setSource('auto'); setColor(null); }}>
                {scanners.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
              </select>
            </PrintingField>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
            {sourceChoices.length > 1 && (
              <PrintingField label="glass or feeder">
                <select className="cal-inp" value={source} onChange={(e) => setSource(e.target.value)}>
                  {sourceChoices.map((s) => <option key={s} value={s}>{PRINTING_SCAN_SOURCE_LABEL[s]}</option>)}
                </select>
              </PrintingField>
            )}
            {colorChoices.length > 1 && (
              <PrintingField label="colour">
                <select className="cal-inp" value={color || (colorChoices.includes('color') ? 'color' : colorChoices[0])}
                        onChange={(e) => setColor(e.target.value)}>
                  {colorChoices.map((c) => <option key={c} value={c}>{PRINTING_SCAN_COLOR_LABEL[c]}</option>)}
                </select>
              </PrintingField>
            )}
          </div>
          {source === 'auto' && hasFeeder && (
            <PrintingMuted>automatic: the feeder when there's paper in it, otherwise the glass</PrintingMuted>
          )}
        </>
      )}
      {scan && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, flexWrap: 'wrap' }}>
            <StatusDot tone={state === 'done' ? 'ok' : state === 'failed' ? 'err' : state === 'canceled' ? 'idle'
                             : working ? 'brand' : 'warn'} live={working}/>
            <span>{state === 'waiting' ? 'waiting for the next page' : state}</span>
            <span style={{ color: 'var(--fg-faint)' }}>· {printingPlural(scan.pages || 0, 'page')}</span>
            {scan.scanner_name && <span style={{ color: 'var(--fg-faint)' }}>· {scan.scanner_name}</span>}
          </div>
          <PrintingThumbs thumbs={scan.thumbs}/>
          {state === 'waiting' && scan.source_used === 'platen' && (
            <PrintingMuted>put the next page on the glass and press Scan another page, or Done to save</PrintingMuted>
          )}
          {state === 'done' && scan.path && (
            <PrintingNotice tone="ok">
              Saved to Documents: <span className="mono">{scan.path}</span>
            </PrintingNotice>
          )}
          {scan.incomplete && <PrintingNotice>Some pages may be missing from this scan.</PrintingNotice>}
          {scan.error && <PrintingNotice tone="err">{scan.error}</PrintingNotice>}
        </>
      )}
      {err && <PrintingNotice tone="err">{err}</PrintingNotice>}
    </PrintingModal>
  );
};

/* The Documents page header's Scan button: only with a saved scanner. */
const PrintingScanButton = ({ onDone, fire }) => {
  const avail = usePrintingAvailability();
  const [open, setOpen] = React.useState(false);
  if (!avail.hasScanner) return null;
  return (
    <>
      <Button icon="scan-line" onClick={() => setOpen(true)}>Scan</Button>
      {open && <ScanSheet fire={fire} onDone={onDone} onClose={() => setOpen(false)}/>}
    </>
  );
};

/* ==================================================================== *
 *  Settings → Printers and scanners
 * ==================================================================== */
const PrintingJobsList = ({ jobs, onCancel, showPrinter = false }) => {
  if (!jobs) return <PrintingMuted>loading…</PrintingMuted>;
  if (!jobs.length) return <PrintingMuted>no print jobs yet</PrintingMuted>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {jobs.map((j) => (
        <div key={j.id} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                                 padding: '6px 0', borderTop: '1px solid var(--border-soft)', fontSize: 12 }}>
          <StatusDot tone={j.state === 'done' ? 'ok' : j.state === 'failed' ? 'err'
                           : j.state === 'canceled' ? 'idle' : 'brand'}/>
          <span style={{ minWidth: 64, color: 'var(--fg-muted)' }}>{j.state}</span>
          <span style={{ flex: '1 1 140px', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {j.title}
          </span>
          {showPrinter && j.printer_name && <span style={{ color: 'var(--fg-faint)' }}>{j.printer_name}</span>}
          {j.who && <span style={{ color: 'var(--fg-faint)' }}>{j.who}</span>}
          <span className="mono" style={{ color: 'var(--fg-faint)' }} title={j.created_at || ''}>{relTime(j.created_at)}</span>
          {!PRINTING_JOB_ENDED.has(j.state) && j.progress && (
            <span style={{ color: 'var(--fg-muted)' }}>{j.progress}</span>
          )}
          {j.can_cancel && <Button icon="x" onClick={() => onCancel(j)}>Cancel</Button>}
          {j.error && <div style={{ flexBasis: '100%', color: 'var(--err-ink)', fontSize: 11 }}>{j.error}</div>}
        </div>
      ))}
    </div>
  );
};

/* Recent jobs, collapsed until opened (GET /api/print/jobs). */
const PrintingRecentJobs = ({ printerId, fire, label = 'recent jobs', extra = null, reloadKey = 0 }) => {
  const [open, setOpen] = React.useState(false);
  const [jobs, setJobs] = React.useState(null);
  const [scanLog, setScanLog] = React.useState([]);
  const load = React.useCallback(async () => {
    try {
      const q = printerId != null ? `printer_id=${encodeURIComponent(printerId)}&limit=20` : 'limit=20';
      const r = await apiGet(`/api/print/jobs?${q}`, { quiet: true });
      setJobs((r && r.jobs) || []);
      setScanLog((r && r.scans) || []);
    } catch (e) {
      setJobs([]);
      setScanLog([]);
      const m = printingErrorText(e);
      if (m) fire(m);
    }
  }, [printerId]);
  React.useEffect(() => { if (open) load(); }, [open, load, reloadKey]);
  const cancel = async (j) => {
    try { await apiPost(`/api/print/jobs/${encodeURIComponent(j.id)}/cancel`, {}); load(); }
    catch (e) { const m = printingErrorText(e); if (m) fire(m); }
  };
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <button type="button" onClick={() => setOpen((o) => !o)}
                style={{ font: 'inherit', fontSize: 12, background: 'transparent', border: 'none', padding: 0,
                         cursor: 'pointer', color: 'var(--fg-muted)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14}/>{label}
        </button>
        {open && <IconButton name="refresh-cw" title="refresh" aria-label="refresh" onClick={load}/>}
        <span style={{ flex: 1 }}/>
        {extra}
      </div>
      {open && <div style={{ marginTop: 6 }}><PrintingJobsList jobs={jobs} onCancel={cancel} showPrinter={printerId == null}/></div>}
      {open && scanLog.length > 0 && <PrintingScanLog scans={scanLog}/>}
    </div>
  );
};

/* Scans in the household's job log (the same 90 days). */
const PrintingScanLog = ({ scans }) => (
  <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
    {scans.map((s) => (
      <div key={s.id} style={{ fontSize: 12, color: 'var(--fg-muted)', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span>scan</span>
        <span>· {s.state}</span>
        <span>· {s.pages} page{s.pages === 1 ? '' : 's'}</span>
        {s.name && <span style={{ color: 'var(--fg)' }}>· {s.name}</span>}
        {s.who && <span>· {s.who}</span>}
        {s.error && <span style={{ color: 'var(--warn-ink, var(--warn))' }}>· {s.error}</span>}
      </div>
    ))}
  </div>
);

const PrinterSupplies = ({ printer }) => {
  if (!printer.supplies_reported) return <PrintingMuted>this printer doesn't report ink levels</PrintingMuted>;
  const list = printer.supplies || [];
  if (!list.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {list.map((s, i) => {
        const lvl = typeof s.level === 'number' ? Math.max(0, Math.min(100, s.level)) : null;
        const swatch = PRINTING_HEX.test(s.color || '') ? s.color : null;
        return (
          <div key={`${s.name}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, flexShrink: 0,
                           border: '1px solid var(--border)', background: swatch || 'transparent' }}/>
            <span style={{ flex: '0 1 150px', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {s.name}
            </span>
            {lvl == null ? (
              <span style={{ color: 'var(--fg-faint)', fontSize: 11 }}>level not reported</span>
            ) : (
              <>
                <span style={{ flex: '1 1 120px', height: 6, borderRadius: 'var(--r-full)', background: 'var(--sunken)',
                               border: '1px solid var(--border-soft)', overflow: 'hidden', minWidth: 60 }}>
                  <span style={{ display: 'block', height: '100%', width: `${lvl}%`,
                                 background: s.low ? 'var(--warn)' : 'var(--fg-muted)' }}/>
                </span>
                <span className="mono" style={{ fontSize: 11, color: s.low ? 'var(--warn-ink)' : 'var(--fg-muted)', minWidth: 34, textAlign: 'right' }}>
                  {lvl}%
                </span>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
};

/* "follow the printer / on / off" for a household per-printer switch. */
const PrintingTriSelect = ({ label, value, onChange, disabled }) => (
  <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--fg-muted)' }}>
    {label}
    <select className="cal-inp" style={{ padding: '4px 8px', fontSize: 12 }} disabled={disabled}
            value={value === true ? 'on' : value === false ? 'off' : ''}
            onChange={(e) => onChange(e.target.value === 'on' ? true : e.target.value === 'off' ? false : null)}>
      <option value="">follow the printer</option>
      <option value="on">on</option>
      <option value="off">off</option>
    </select>
  </label>
);

const PrinterCard = ({ printer, rooms, admin, fire, onChanged, onReplace }) => {
  const p = printer;
  const [busy, setBusy] = React.useState(null);
  const [renaming, setRenaming] = React.useState(null);   // the typed name, or null
  const [jobsKey, setJobsKey] = React.useState(0);
  const del = useDeleteConfirm(async () => {
    try {
      await apiDelete(`/api/printers/${encodeURIComponent(p.id)}`);
      fire(`removed ${p.name}`);
      onChanged();
    } catch (e) { const m = printingErrorText(e); if (m) fire(m); }
  });

  const run = async (what, fn, okText) => {
    setBusy(what);
    try {
      const r = await fn();
      if (r && r.id === p.id && r.status) onReplace(r);
      if (okText) fire(okText);
      return r;
    } catch (e) {
      const m = printingErrorText(e);
      if (m) fire(m);
      return null;
    } finally { setBusy(null); }
  };
  const patch = (b) => run('patch', () => apiPatch(`/api/printers/${encodeURIComponent(p.id)}`, b))
    .then((r) => { if (r) onChanged(); });
  const toggleRoom = (rid) => {
    const cur = p.rooms || [];
    patch({ rooms: cur.includes(rid) ? cur.filter((x) => x !== rid) : [...cur, rid] });
  };
  const testPage = () => run('test', () => apiPost(`/api/printers/${encodeURIComponent(p.id)}/test-page`, {}),
    `test page sent to ${p.name}`).then(() => { setJobsKey((k) => k + 1); onChanged(); });
  const identify = () => run('identify', () => apiPost(`/api/printers/${encodeURIComponent(p.id)}/identify`, {}),
    `${p.name} should beep or flash now`);
  const checkNow = () => run('refresh', () => apiPost(`/api/printers/${encodeURIComponent(p.id)}/refresh`, {}));
  const trust = () => run('trust', () => apiPost(`/api/printers/${encodeURIComponent(p.id)}/trust-certificate`, {}),
    `${p.name}'s new certificate is trusted`).then((r) => { if (r) onChanged(); });
  const saveName = () => {
    const n = (renaming || '').trim();
    if (!n || n === p.name) { setRenaming(null); return; }
    run('rename', () => apiPost(`/api/printers/${encodeURIComponent(p.id)}/rename`, { name: n }))
      .then((r) => { if (r) { setRenaming(null); onChanged(); } });
  };

  const st = p.status || {};
  const tone = PRINTING_TONE[st.tone] || 'idle';
  const adminHref = printingSafeHref(p.admin_url);
  const caps = [p.can_print && 'prints', p.can_scan && 'scans', p.color && 'colour', p.duplex && 'two-sided'].filter(Boolean);
  const aj = p.active_job;

  return (
    <div style={{ padding: 16, borderTop: '1px solid var(--border-soft)', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ paddingTop: 5 }}><StatusDot tone={tone} live={st.state === 'printing'}/></span>
        <div style={{ flex: '1 1 220px', minWidth: 0 }}>
          {renaming != null ? (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <input className="cal-inp" value={renaming} autoFocus maxLength={60}
                     onChange={(e) => setRenaming(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') saveName(); if (e.key === 'Escape') setRenaming(null); }}/>
              <Button variant="primary" icon="check" disabled={busy === 'rename'} onClick={saveName}>Save</Button>
              <Button icon="x" onClick={() => setRenaming(null)}>Cancel</Button>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 14 }}>{p.name}</strong>
              <span style={{ fontSize: 12, color: tone === 'err' ? 'var(--err-ink)' : tone === 'warn' ? 'var(--warn-ink)' : 'var(--fg-muted)' }}>
                {st.state || 'checking'}
              </span>
              {p.is_default && <span className="pill live">default</span>}
            </div>
          )}
          <PrintingMuted>
            {[p.make_and_model, p.location].filter(Boolean).join(' · ')}
            {p.address ? <> · <span className="mono">{p.address}</span></> : null}
          </PrintingMuted>
          {caps.length > 0 && <PrintingMuted>{caps.join(' · ')}</PrintingMuted>}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {p.can_print && (
            <Button icon="printer" disabled={busy === 'test'} onClick={testPage}>
              {busy === 'test' ? 'Sending…' : 'Test page'}
            </Button>
          )}
          {p.identify && (
            <Button icon="lightbulb" disabled={busy === 'identify'} onClick={identify}>Which one is it?</Button>
          )}
          <IconButton name="refresh-cw" title="check now" aria-label="check now" disabled={busy === 'refresh'} onClick={checkNow}/>
          {admin && renaming == null && (
            <Button icon="pencil" onClick={() => setRenaming(p.name)}>Rename</Button>
          )}
          {admin && (
            <Button icon="trash-2" onClick={() => del.request(p, {
              title: `Remove ${p.name}?`, confirmLabel: 'Remove',
              body: <div>Domovoi stops printing{p.can_scan ? ' and scanning' : ''} with it. You can add it again later.</div>,
            })}>Remove</Button>
          )}
        </div>
      </div>

      {p.needs_attention && (
        <PrintingNotice>
          <div>{p.needs_attention}</div>
          {admin ? (
            <div style={{ marginTop: 8 }}>
              <Button icon="shield-check" disabled={busy === 'trust'} onClick={trust}>Trust the new certificate</Button>
            </div>
          ) : <div style={{ marginTop: 4, color: 'var(--fg-muted)' }}>sign in as an admin to trust it</div>}
        </PrintingNotice>
      )}

      {(st.words || []).length > 0 && (
        <div style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {st.words.map((w, i) => <div key={i}>{w}</div>)}
        </div>
      )}
      {st.message && <PrintingMuted>printer says: {st.message}</PrintingMuted>}
      {aj && !PRINTING_JOB_ENDED.has(aj.state) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <StatusDot tone="brand" live/>
          <span>{aj.progress || aj.state}</span>
          <span style={{ color: 'var(--fg-faint)' }}>· {aj.title}</span>
        </div>
      )}

      {p.can_print && <PrinterSupplies printer={p}/>}

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px 16px', flexWrap: 'wrap' }}>
        {p.can_print && (
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
            <input type="checkbox" checked={!!p.is_default} disabled={busy === 'patch'}
                   onChange={(e) => patch({ is_default: e.target.checked })}/>
            Default for the house
          </label>
        )}
        {p.can_print && p.duplex && (
          <PrintingTriSelect label="Two-sided by default" value={p.two_sided_default} disabled={busy === 'patch'}
                             onChange={(v) => patch({ two_sided_default: v })}/>
        )}
        {p.can_print && p.color && (
          <PrintingTriSelect label="Colour by default" value={p.color_default} disabled={busy === 'patch'}
                             onChange={(v) => patch({ color_default: v })}/>
        )}
      </div>

      {p.can_print && rooms.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--fg-muted)', marginRight: 2 }}>Default in rooms</span>
          {rooms.map((r) => {
            const on = (p.rooms || []).includes(r.id);
            return (
              <button key={r.id} type="button" className="room-chip" aria-pressed={on} disabled={busy === 'patch'}
                      onClick={() => toggleRoom(r.id)}
                      style={{ cursor: 'pointer', font: 'inherit', fontSize: 11,
                               background: on ? 'var(--brand-soft)' : 'var(--sunken)',
                               borderColor: on ? 'var(--brand)' : 'var(--border)' }}>
                {on && <Icon name="check" size={12}/>}{(r.label || r.id).toLowerCase()}
              </button>
            );
          })}
        </div>
      )}

      {adminHref && (
        <div style={{ fontSize: 12 }}>
          <a href={adminHref} target="_blank" rel="noopener noreferrer"
             style={{ color: 'var(--brand-ink)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            printer's own page <Icon name="external-link" size={12}/>
          </a>
        </div>
      )}

      {p.can_print && <PrintingRecentJobs printerId={p.id} fire={fire} reloadKey={jobsKey}/>}
      {st.checked_at ? <PrintingMuted>checked {relTime(st.checked_at)}</PrintingMuted> : null}
      {del.node}
    </div>
  );
};

/* One "found on your network" row, with its inline Add form. */
const PrintingFoundRow = ({ row, rooms, onAdded, fire }) => {
  const [adding, setAdding] = React.useState(false);
  const [name, setName] = React.useState('');
  const [room, setRoom] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const caps = [row.prints && 'prints', row.scans && 'scans', row.color && 'colour', row.duplex && 'two-sided'].filter(Boolean);
  const roomLabel = (rooms.find((r) => r.id === room) || {}).label;
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const b = { key: row.key };
      if (name.trim()) b.name = name.trim();
      if (room) b.room_id = room;
      const p = await apiPost('/api/printers', b);
      setAdding(false);
      onAdded(p, row);
    } catch (e) {
      const m = printingErrorText(e);
      if (m) setErr(m);
    } finally { setBusy(false); }
  };
  return (
    <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border-soft)', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Icon name={row.prints ? 'printer' : 'scan-line'} size={16}/>
        <div style={{ flex: '1 1 200px', minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>{row.make_and_model || 'unnamed printer'}</div>
          <PrintingMuted>
            {row.location ? `${row.location} · ` : ''}<span className="mono">{row.address}</span>
            {row.via ? ` · by ${row.via}` : ''}
          </PrintingMuted>
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {caps.map((c) => <span key={c} className="pill idle">{c}</span>)}
        </div>
        {row.already_added != null ? (
          <span style={{ fontSize: 12, color: 'var(--ok-ink)' }}>added</span>
        ) : !adding ? (
          <Button variant="primary" icon="plus" onClick={() => setAdding(true)}>Add</Button>
        ) : null}
      </div>
      {adding && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <PrintingField label="name">
            <input className="cal-inp" value={name} maxLength={60}
                   placeholder={roomLabel ? `${roomLabel.toLowerCase()} printer` : (row.make_and_model || 'printer')}
                   onChange={(e) => setName(e.target.value)}/>
          </PrintingField>
          {rooms.length > 0 && (
            <PrintingField label="room (optional)">
              <select className="cal-inp" value={room} onChange={(e) => setRoom(e.target.value)}>
                <option value="">no room</option>
                {rooms.map((r) => <option key={r.id} value={r.id}>{(r.label || r.id).toLowerCase()}</option>)}
              </select>
            </PrintingField>
          )}
          <Button variant="primary" icon="check" disabled={busy} onClick={save}>{busy ? 'Adding…' : 'Save'}</Button>
          <Button icon="x" disabled={busy} onClick={() => { setAdding(false); setErr(null); }}>Cancel</Button>
        </div>
      )}
      {err && <PrintingNotice tone="err">{err}</PrintingNotice>}
    </div>
  );
};

/* Add by address: an IP, a name like printer.local, or a full ipp:// or
 * http://…/eSCL URL. */
const PrintingAddByAddress = ({ rooms, onAdded, onCancel }) => {
  const [address, setAddress] = React.useState('');
  const [name, setName] = React.useState('');
  const [room, setRoom] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const save = async () => {
    const a = address.trim();
    if (!a) return;
    setBusy(true); setErr(null);
    try {
      const b = { address: a };
      if (name.trim()) b.name = name.trim();
      if (room) b.room_id = room;
      const p = await apiPost('/api/printers', b);
      onAdded(p);
    } catch (e) {
      const m = printingErrorText(e);
      if (m) setErr(m);
    } finally { setBusy(false); }
  };
  return (
    <div style={{ padding: 16, borderTop: '1px solid var(--border-soft)', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={{ flex: '2 1 240px' }}>
          <PrintingField label="address">
            <input className="cal-inp mono" value={address} autoFocus
                   placeholder="192.168.1.50, printer.local or ipp://192.168.1.50/ipp/print"
                   onChange={(e) => setAddress(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') save(); }}/>
          </PrintingField>
        </div>
        <div style={{ flex: '1 1 150px' }}>
          <PrintingField label="name (optional)">
            <input className="cal-inp" value={name} maxLength={60} onChange={(e) => setName(e.target.value)}/>
          </PrintingField>
        </div>
        {rooms.length > 0 && (
          <PrintingField label="room (optional)">
            <select className="cal-inp" value={room} onChange={(e) => setRoom(e.target.value)}>
              <option value="">no room</option>
              {rooms.map((r) => <option key={r.id} value={r.id}>{(r.label || r.id).toLowerCase()}</option>)}
            </select>
          </PrintingField>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <Button variant="primary" icon="plus" disabled={busy || !address.trim()} onClick={save}>{busy ? 'Adding…' : 'Add'}</Button>
        <Button icon="x" disabled={busy} onClick={onCancel}>Cancel</Button>
      </div>
      {err && <PrintingNotice tone="err">{err}</PrintingNotice>}
    </div>
  );
};

/* "last look: found 2 printers — 2 by multicast, 0 by unicast, 0 by sweep;
 * 1 added by address", and any errors. Kept visible: it is how a household
 * tells whether multicast discovery works on its install. */
const PrintingDiagnostics = ({ d }) => {
  if (!d) return null;
  const when = d.ran_at ? ` ${relTime(d.ran_at)}` : '';
  return (
    <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border-soft)' }}>
      <PrintingMuted mono>
        last look{when}: found {printingPlural(d.found || 0, 'printer')} — {d.by_multicast || 0} by multicast,
        {' '}{d.by_unicast || 0} by unicast, {d.by_sweep || 0} by sweep; {d.typed || 0} added by address
      </PrintingMuted>
      {d.sweep === 'rate_limited' && (
        <PrintingMuted>
          the network sweep runs at most once a minute{d.sweep_retry_sec ? ` — try again in ${d.sweep_retry_sec}s` : ''}
        </PrintingMuted>
      )}
      {(d.errors || []).map((e, i) => <PrintingMuted key={i} mono>{e}</PrintingMuted>)}
    </div>
  );
};

const PRINTING_LIMIT_FIELDS = [
  ['max_pages_per_job', 'pages in one job'],
  ['max_copies', 'copies of one job'],
  ['max_sheets_per_job', 'sheets of paper in one job'],
  ['max_jobs_per_hour_per_device', 'jobs an hour from one device'],
  ['max_sheets_per_day', 'sheets of paper a day'],
  ['max_scan_pages', 'pages in one scan'],
];

const PrintingLimits = ({ initial, admin, fire }) => {
  const [limits, setLimits] = React.useState(initial || null);
  const [draft, setDraft] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    apiGet('/api/print/limits', { quiet: true }).then((l) => { if (l) setLimits(l); }).catch(() => {});
  }, []);
  if (!limits) return <PrintingMuted>loading the limits…</PrintingMuted>;
  const save = async () => {
    setBusy(true);
    try {
      const b = {};
      PRINTING_LIMIT_FIELDS.forEach(([k]) => {
        const n = Math.round(Number(draft[k]));
        if (Number.isFinite(n) && n !== limits[k]) b[k] = n;
      });
      const l = await apiFetch('/api/print/limits', { method: 'PUT', body: JSON.stringify(b) });
      if (l) setLimits(l);
      setDraft(null);
      fire('limits saved');
    } catch (e) { const m = printingErrorText(e); if (m) fire(m); }
    finally { setBusy(false); }
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10 }}>
        {PRINTING_LIMIT_FIELDS.map(([k, label]) => (
          <PrintingField key={k} label={label}>
            {draft ? (
              <input className="cal-inp mono" type="number" min={1} value={draft[k]}
                     onChange={(e) => setDraft(Object.assign({}, draft, { [k]: e.target.value }))}/>
            ) : (
              <span className="mono" style={{ fontSize: 13 }}>{limits[k] != null ? limits[k] : '—'}</span>
            )}
          </PrintingField>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {admin && !draft && (
          <Button icon="pencil" onClick={() => {
            const d = {}; PRINTING_LIMIT_FIELDS.forEach(([k]) => { d[k] = limits[k] != null ? String(limits[k]) : ''; });
            setDraft(d);
          }}>Change</Button>
        )}
        {admin && draft && <>
          <Button variant="primary" icon="check" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</Button>
          <Button icon="x" disabled={busy} onClick={() => setDraft(null)}>Cancel</Button>
        </>}
        {!admin && <PrintingMuted>an admin can change these</PrintingMuted>}
      </div>
    </div>
  );
};

const PRINTING_POLL_MS = 60000;

const PrintersPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const [data, setData] = React.useState(null);
  const [loadErr, setLoadErr] = React.useState(null);
  const [refusal, setRefusal] = React.useState(null);
  const [found, setFound] = React.useState(null);
  const [diag, setDiag] = React.useState(null);
  const [looking, setLooking] = React.useState(false);
  const [lookErr, setLookErr] = React.useState(null);
  const [typing, setTyping] = React.useState(false);
  const [justAdded, setJustAdded] = React.useState(null);
  const [historyKey, setHistoryKey] = React.useState(0);

  const load = React.useCallback(async () => {
    try {
      const d = await apiGet('/api/printers?refresh=1', { quiet: true });
      setData(d); setLoadErr(null); setRefusal(null);
      PrintingAvailability.set(d);
    } catch (e) {
      const refused = e && (e.status === 401 || e.status === 403);
      setRefusal(refused ? e : null);
      setLoadErr(refused ? null : (printingErrorText(e) || null));
    }
  }, []);

  // Read on open, then every 60 s while the tab is on screen.
  React.useEffect(() => {
    load();
    const t = setInterval(load, PRINTING_POLL_MS);
    return () => clearInterval(t);
  }, [load]);
  useRetryAfterCredential(refusal, load);

  const discover = React.useCallback(async (lookAgain) => {
    setLooking(true); setLookErr(null);
    try {
      // Opening the page looks quietly (no prompt for a browser not paired
      // yet); the buttons are a person asking, so they may prompt.
      const r = lookAgain
        ? await apiPost('/api/printers/discover', { look_again: true })
        : await apiFetch('/api/printers/discover', { method: 'POST', noPrompt: true,
                                                     body: JSON.stringify({ look_again: false }) });
      setFound((r && r.found) || []);
      setDiag((r && r.diagnostics) || null);
    } catch (e) {
      if (lookAgain) { const m = printingErrorText(e); if (m) setLookErr(m); }
    } finally { setLooking(false); }
  }, []);
  React.useEffect(() => { discover(false); }, [discover]);

  const onAdded = (p, row) => {
    if (row) setFound((cur) => (cur || []).map((f) => (f.key === row.key ? Object.assign({}, f, { already_added: p && p.id }) : f)));
    setTyping(false);
    setJustAdded(p || null);
    fire(`added ${(p && p.name) || 'the printer'}`);
    load();
  };
  const replace = (row) => setData((d) => (d ? Object.assign({}, d, {
    printers: (d.printers || []).map((x) => (x.id === row.id ? row : x)),
  }) : d));

  const clearHistory = useDeleteConfirm(async () => {
    try {
      const r = await apiDelete('/api/print/jobs');
      fire(`cleared ${printingPlural((r && r.cleared) || 0, 'job')}`);
      setHistoryKey((k) => k + 1);
    } catch (e) { const m = printingErrorText(e); if (m) fire(m); }
  });

  const testJustAdded = async () => {
    const p = justAdded;
    setJustAdded(null);
    try {
      await apiPost(`/api/printers/${encodeURIComponent(p.id)}/test-page`, {});
      fire(`test page sent to ${p.name}`);
      load();
    } catch (e) { const m = printingErrorText(e); if (m) fire(m); }
  };

  if (refusal && !data) {
    return <Card><PairedOnlyEmpty what="the printers"/>{toastNode}</Card>;
  }

  const printers = (data && data.printers) || [];
  const rooms = (data && data.rooms) || [];
  const diagnostics = diag || (data && data.discovery) || null;
  const foundRows = (found || []).slice().sort((a, b) => (a.already_added != null) - (b.already_added != null));
  const lookButtons = (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <Button icon="radar" disabled={looking} onClick={() => discover(true)}>
        {looking ? 'Looking…' : (found ? 'Look again' : 'Look for printers')}
      </Button>
      <Button icon="plus" onClick={() => setTyping(true)} disabled={typing}>Add by address</Button>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
      <PrintingMuted style={{ fontSize: 12 }}>
        Domovoi works with network printers and scanners: AirPrint, IPP Everywhere and Mopria, which most
        network printers from the last decade speak. USB printers aren't supported.
      </PrintingMuted>

      {justAdded && justAdded.can_print && (
        <PrintingNotice tone="ok">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ flex: 1 }}>Added {justAdded.name}.</span>
            <Button icon="printer" onClick={testJustAdded}>Print a test page</Button>
            <IconButton name="x" title="dismiss" aria-label="dismiss" onClick={() => setJustAdded(null)}/>
          </div>
        </PrintingNotice>
      )}

      <Card title="printers and scanners" action={printers.length > 0 ? lookButtons : null}>
        {loadErr && <div style={{ padding: 16 }}><PrintingNotice tone="err">{loadErr}</PrintingNotice></div>}
        {!data && !loadErr ? (
          <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Loading…</div>
        ) : printers.length === 0 ? (
          <Empty title="no printers yet" sub="look for one on your network, or add it by its address"
                 action={lookButtons}/>
        ) : (
          printers.map((p) => (
            <PrinterCard key={p.id} printer={p} rooms={rooms} admin={admin} fire={fire}
                         onChanged={load} onReplace={replace}/>
          ))
        )}
        {typing && (
          <PrintingAddByAddress rooms={rooms} onAdded={(p) => onAdded(p, null)} onCancel={() => setTyping(false)}/>
        )}
      </Card>

      <Card title="found on your network" sub={looking ? 'looking…' : undefined}>
        {lookErr && <div style={{ padding: 16 }}><PrintingNotice tone="err">{lookErr}</PrintingNotice></div>}
        {found == null ? (
          <div style={{ padding: 16, color: 'var(--fg-faint)', fontSize: 13 }}>{looking ? 'Looking…' : 'not looked yet'}</div>
        ) : foundRows.length === 0 ? (
          <div style={{ padding: 16, color: 'var(--fg-faint)', fontSize: 13 }}>
            nothing found — make sure the printer is switched on and on the same Wi-Fi, or add it by its address
          </div>
        ) : (
          foundRows.map((row) => (
            <PrintingFoundRow key={row.key} row={row} rooms={rooms} fire={fire} onAdded={onAdded}/>
          ))
        )}
        <PrintingDiagnostics d={diagnostics}/>
      </Card>

      <Card title="household">
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <PrintingRecentJobs printerId={null} fire={fire} label="recent jobs, all printers" reloadKey={historyKey}
              extra={admin ? (
                <Button icon="trash-2" onClick={() => clearHistory.request(null, {
                  title: 'Clear the print and scan history?', confirmLabel: 'Clear history',
                  body: <div>Every print and scan job in the list goes. Nothing that was printed or saved changes.</div>,
                })}>Clear history</Button>
              ) : null}/>
            <PrintingMuted>
              History is kept {(data && data.limits && data.limits.history_days) || 90} days.
            </PrintingMuted>
          </div>
          <div>
            <div className="label" style={{ marginBottom: 8 }}>limits</div>
            <PrintingLimits initial={data && data.limits} admin={admin} fire={fire}/>
          </div>
        </div>
      </Card>
      {clearHistory.node}
      {toastNode}
    </div>
  );
};

Object.assign(window, {
  PrintingAvailability, usePrintingAvailability, printingKindFor,
  PrintFileButton, PrintDialog, ScanSheet, PrintingScanButton, PrintersPanel,
});
