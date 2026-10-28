/* Spreadsheet editor (window.SheetEditor) — the Documents library's
 * editor for .xlsx / .csv.
 *
 * The grid is Univer (open-source core only, Apache-2.0), bundled once
 * into /vendor/univer (scripts/vendor/univer) and loaded the first time a
 * sheet is opened, so the dashboard's start-up doesn't pay for it. It
 * carries its own React; the dashboard's is never touched.
 *
 * Univer's own .xlsx import/export is in its paid tier, so the server does
 * the conversion (web/backend/api/sheet_workbook.py, openpyxl):
 *   GET  /api/documents/workbook/{rel}  → { workbook, version, editable, why, format }
 *   PUT  /api/documents/workbook/{rel}  ← { workbook, base_version, force, device_id }
 * A save writes only what changed into the file that's there, so what the
 * editor doesn't show (comments, validation, conditional formatting…)
 * survives. Saving is automatic, a moment after the last edit.
 *
 * One person edits at a time: there is no live co-editing of sheets yet.
 * POST /api/documents/presence/{rel} names anybody else who has the same
 * sheet open, and a save from a stale copy is refused (409) and offered
 * back as a plain choice, so nobody overwrites anybody without knowing.
 */

const SHEET_AUTOSAVE_MS = 1500;
const SHEET_PRESENCE_MS = 15000;

/* Load the vendored bundle once; resolves to window.DomovoiUniverLib. */
let _sheetUniverPromise = null;
const loadSheetUniver = () => {
  if (window.DomovoiUniverLib) return Promise.resolve(window.DomovoiUniverLib);
  if (_sheetUniverPromise) return _sheetUniverPromise;
  _sheetUniverPromise = new Promise((resolve, reject) => {
    if (!document.querySelector('link[data-univer-css]')) {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = '/vendor/univer/univer.css';
      css.setAttribute('data-univer-css', '1');
      document.head.appendChild(css);
    }
    const done = () => (window.DomovoiUniverLib ? resolve(window.DomovoiUniverLib)
      : reject(new Error('spreadsheet library missing')));
    window.addEventListener('domovoi-univer-ready', done, { once: true });
    const s = document.createElement('script');
    s.type = 'module';
    s.src = '/vendor/univer/univer.js';
    s.onerror = () => { _sheetUniverPromise = null; reject(new Error('spreadsheet library missing')); };
    document.head.appendChild(s);
  });
  return _sheetUniverPromise;
};

/* Univer's palette from the dashboard's tokens: its warm stone greys and
 * the amber accent instead of Univer's blue (colors_and_type.css --brand,
 * --brand-ink for anything drawn as text). */
const sheetDomovoiTheme = (lib) => {
  const base = lib.themes.orange;
  return Object.assign({}, base, {
    primary: {
      50: '#FEF8EC', 100: '#FDEFD3', 200: '#FADDA6', 300: '#F4C675', 400: '#EEB257',
      500: '#E8A33D', 600: '#98600C', 700: '#8F5A0B', 800: '#744B0E', 900: '#5C3C0E',
    },
  });
};

/* The fonts the font menu offers: the house registry (builder C's
 * window.DomovoiFonts) when it is there, else the two families the
 * dashboard itself ships plus the three every browser draws. */
const sheetFontList = () => {
  const reg = window.DomovoiFonts;
  const cat = (c) => (['sans-serif', 'serif', 'monospace', 'display', 'handwriting'].indexOf(c) >= 0
    ? c : (c === 'mono' ? 'monospace' : c === 'sans' ? 'sans-serif' : undefined));
  if (reg && typeof reg.list === 'function') {
    try {
      const fams = reg.list() || [];
      if (fams.length) {
        return fams.map((f) => ({ value: f.family, label: f.family, category: cat(f.category) }));
      }
    } catch (e) { /* fall through to the built-in list */ }
  }
  return [
    { value: 'Inter', label: 'Inter', category: 'sans-serif' },
    { value: 'Arial', label: 'Arial', category: 'sans-serif' },
    { value: 'Times New Roman', label: 'Times New Roman', category: 'serif' },
    { value: 'Courier New', label: 'Courier New', category: 'monospace' },
    { value: 'JetBrains Mono', label: 'JetBrains Mono', category: 'monospace' },
  ];
};

/* Ask the registry for every family a workbook uses, so the canvas draws
 * them rather than a fallback. Never waits more than 3 s. */
const sheetEnsureFonts = (families) => {
  const reg = window.DomovoiFonts;
  if (!reg || typeof reg.ensure !== 'function' || !families.length) return Promise.resolve();
  const all = Promise.all(families.map((f) => Promise.resolve()
    .then(() => reg.ensure(f)).catch(() => null)));
  return Promise.race([all, new Promise((r) => setTimeout(r, 3000))]);
};

const sheetFamiliesIn = (wb) => {
  const out = new Set();
  const add = (s) => { if (s && typeof s === 'object' && typeof s.ff === 'string' && s.ff) out.add(s.ff); };
  Object.values((wb && wb.styles) || {}).forEach(add);
  add(wb && wb.defaultStyle);
  return Array.from(out);
};

/* Toolbar and menu items a household sheet doesn't need, or that would
 * mislead: range protection (there is one editor), feature search,
 * background pictures, calculation settings, full screen (the editor
 * already is), the shortcut list. */
const SHEET_HIDDEN_MENUS = [
  'sheet.command.add-range-protection-from-toolbar',
  'sheet.contextMenu.permission',
  'sheet.command.add-range-protection-from-context-menu',
  'sheet.command.set-range-protection-from-context-menu',
  'sheet.command.delete-range-protection-from-context-menu',
  'sheet.command.view-sheet-permission-from-context-menu',
  'sheet.command.add-worksheet-protection',
  'sheet.command.add-range-protection-from-sheet-bar',
  'sheet.command.change-sheet-protection-from-sheet-bar',
  'sheet.command.delete-worksheet-protection-from-sheet-bar',
  'sheet.command.view-sheet-permission-from-sheet-bar',
  'ui.operation.open-feature-search',
  'sheet.menu.worksheet-background-image',
  'sheets-formula-ui.operation.calculation-options',
  'base-ui.operation.toggle-fullscreen',
  'base-ui.operation.toggle-shortcut-panel',
  // Ten function catalogues are a power user's menu; "common functions"
  // stays, and any function can still be typed (=PMT(...)).
  'formula-ui.operation.insert-function.financial',
  'formula-ui.operation.insert-function.logical',
  'formula-ui.operation.insert-function.text',
  'formula-ui.operation.insert-function.date',
  'formula-ui.operation.insert-function.lookup',
  'formula-ui.operation.insert-function.math',
  'formula-ui.operation.insert-function.statistical',
  'formula-ui.operation.insert-function.engineering',
  'formula-ui.operation.insert-function.information',
  'formula-ui.operation.insert-function.database',
];
/* A .csv keeps values only, so formatting controls would do nothing that
 * lasts: they are hidden for .csv files. */
const SHEET_FORMAT_MENUS = [
  'ui.operation.activate-format-painter', 'ui.command.clear-formatting',
  'sheet.command.set-range-font-family', 'sheet.command.set-range-fontsize',
  'sheet.command.set-range-font-increase', 'sheet.command.set-range-font-decrease',
  'sheet.command.set-range-bold', 'sheet.command.set-range-italic',
  'sheet.command.set-range-underline', 'sheet.command.set-range-stroke',
  'sheet.command.set-range-text-color', 'sheet.command.set-background-color',
  'sheet.command.set-border-basic', 'sheet.command.set-horizontal-text-align',
  'sheet.command.set-vertical-text-align', 'sheet.command.set-text-wrap',
  'sheet.command.set-shrink-to-fit', 'sheet.command.set-text-rotation',
  'sheet.command.add-worksheet-merge', 'sheet.operation.open.numfmt.panel',
  'sheet.command.numfmt.set.percent', 'sheet.command.numfmt.set.currency',
  'sheet.command.numfmt.add.decimal.command', 'sheet.command.numfmt.subtract.decimal.command',
];

const sheetMenuConfig = (csv) => {
  const cfg = {};
  SHEET_HIDDEN_MENUS.concat(csv ? SHEET_FORMAT_MENUS : []).forEach((id) => { cfg[id] = { hidden: true }; });
  return cfg;
};

/* Univer's snapshot, ready for the server: each sheet names the sheet of
 * the file it started as (`origin`), and a formula Univer keeps as a
 * shared-formula reference (`si` without `f`) is spelled out. Univer
 * stamps the theme's own text colour on every cell it edits; that is "no
 * colour", not a choice, so it isn't written into the file (in Excel it
 * would pin near-black text where the file meant "automatic"). */
const SHEET_THEME_TEXT = '#1C1917';   // sheetDomovoiTheme: orange theme gray.900
const sheetSnapshotForSave = (fWorkbook, origins) => {
  const snap = fWorkbook.save();
  const styles = {};
  Object.keys(snap.styles || {}).forEach((id) => {
    const st = snap.styles[id];
    // Only styles made in this session: the file's own ("st<n>", as the
    // server names them) say what the file says, even in that colour.
    if (st && st.cl && typeof st.cl.rgb === 'string' && !/^st\d+$/.test(id)
        && st.cl.rgb.toUpperCase() === SHEET_THEME_TEXT) {
      const copy = Object.assign({}, st);
      delete copy.cl;
      styles[id] = copy;
    } else {
      styles[id] = st;
    }
  });
  const out = { sheetOrder: snap.sheetOrder || [], styles, sheets: {} };
  (snap.sheetOrder || []).forEach((sid) => {
    const sheet = Object.assign({}, snap.sheets[sid]);
    sheet.origin = Object.prototype.hasOwnProperty.call(origins, sid) ? origins[sid] : null;
    const cells = sheet.cellData || {};
    let fws = null;
    Object.keys(cells).forEach((r) => {
      const row = cells[r] || {};
      Object.keys(row).forEach((c) => {
        let cell = row[c];
        if (cell && cell.si && !cell.f) {
          if (!fws) fws = fWorkbook.getSheetBySheetId(sid);
          const f = fws ? fws.getRange(Number(r), Number(c)).getFormula() : '';
          if (f) cell = Object.assign({}, cell, { f });
        }
        // A formula's last result is Univer's to recompute, not a change
        // to save (the server keeps formulas as formulas).
        if (cell && cell.f) {
          cell = Object.assign({}, cell);
          delete cell.v; delete cell.t; delete cell.si;
        }
        row[c] = cell;
      });
    });
    out.sheets[sid] = {
      id: sid, name: sheet.name, origin: sheet.origin, hidden: sheet.hidden || 0,
      tabColor: sheet.tabColor || null, cellData: cells, mergeData: sheet.mergeData || [],
      columnData: sheet.columnData || {}, rowData: sheet.rowData || {},
      freeze: sheet.freeze || null, showGridlines: sheet.showGridlines,
    };
  });
  return out;
};

const sheetIsDark = () => document.documentElement.getAttribute('data-theme') === 'dark';

/* `inApp`: opened by the phone app (sheet.html in a WebView), which has
 * its own download and print, and its own Back for Close. */
const SheetEditorOverlay = ({ rel_path, onClose, fire, blockedReason = null, inApp = false }) => {
  const [state, setState] = React.useState({ status: 'loading' });
  // 'saved' | 'dirty' | 'saving' | 'error' | 'conflict'
  const [saveState, setSaveState] = React.useState('saved');
  const dirty = saveState === 'dirty' || saveState === 'saving' || saveState === 'error'
    || saveState === 'conflict';
  /* The Files page already knows whether this device may write and
   * passes it in; a block applied while the editor is open still arrives
   * as a refusal, and that lands here rather than as a password prompt. */
  const [refused, setRefused] = React.useState(null);
  const blocked = refused || blockedReason;
  const [others, setOthers] = React.useState([]);
  const [reloadKey, setReloadKey] = React.useState(0);
  const holderRef = React.useRef(null);
  const apiRef = React.useRef(null);        // { univer, univerAPI }
  const metaRef = React.useRef({ version: null, origins: {}, lastSaved: null, format: 'xlsx' });
  const timerRef = React.useRef(null);
  const savingRef = React.useRef(false);
  const againRef = React.useRef(false);
  const blockedRef = React.useRef(blocked);
  blockedRef.current = blocked;
  const sessionRef = React.useRef(`s-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`);
  const enc = encodeURIComponent(rel_path);

  const onSave = async (opts) => {
    const force = !!(opts && opts.force);
    const api = apiRef.current;
    const meta = metaRef.current;
    if (!api || state.status !== 'ready' || !meta.editable) return true;
    if (blockedRef.current) return false;
    if (savingRef.current) { againRef.current = true; return false; }
    const fWorkbook = api.univerAPI.getActiveWorkbook();
    if (!fWorkbook) return false;
    const snap = sheetSnapshotForSave(fWorkbook, meta.origins);
    const text = JSON.stringify(snap);
    if (!force && text === meta.lastSaved) { setSaveState('saved'); return true; }
    savingRef.current = true;
    setSaveState('saving');
    try {
      const r = await apiFetch(`/api/documents/workbook/${enc}`, {
        method: 'PUT',
        body: JSON.stringify({
          workbook: snap, base_version: meta.version, force,
          device_id: (typeof DeviceIdentity !== 'undefined' && DeviceIdentity.id) ? DeviceIdentity.id() : null,
        }),
      });
      meta.version = r && r.version;
      // The file's sheets are now in this order: the next save's origins.
      const origins = {};
      snap.sheetOrder.forEach((sid, i) => { origins[sid] = `s${i}`; });
      meta.origins = origins;
      meta.lastSaved = JSON.stringify(sheetSnapshotForSave(fWorkbook, origins));
      meta.conflict = false;
      setRefused(null);
      setSaveState('saved');
      return true;
    } catch (e) {
      // Same contract as the doc editor. A per-device block first: it is
      // not a credential problem and no prompt can answer it.
      const why = deviceBlockReason(e);
      if (why) { setRefused(why); setSaveState('error'); return false; }
      const body = e && e.detail && e.detail.detail;
      if (e && e.status === 409 && body && body.reason === 'changed') {
        meta.conflict = true;
        setSaveState('conflict');
        return false;
      }
      // A dismissed sign-in reads as cancelled (the sheet is untouched),
      // and the prompt speaks for itself while it is up.
      const msg = mutationErrorText(e);
      if (msg && fire) fire(msg);
      setSaveState('error');
      return false;
    } finally {
      savingRef.current = false;
      if (againRef.current) {
        againRef.current = false;
        clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => onSaveRef.current(), SHEET_AUTOSAVE_MS);
      }
    }
  };
  const onSaveRef = React.useRef(onSave);
  onSaveRef.current = onSave;

  /* After any change Univer makes (an edit, but also the formula engine
   * filling in results), look once things settle: only a workbook that
   * really differs from the last save turns "Saved" into "Editing…" and
   * saves a moment later. */
  const checkRef = React.useRef(null);
  const scheduleSave = () => {
    clearTimeout(checkRef.current);
    checkRef.current = setTimeout(() => {
      const api = apiRef.current;
      const wb = api && api.univerAPI.getActiveWorkbook();
      if (!wb) return;
      const text = JSON.stringify(sheetSnapshotForSave(wb, metaRef.current.origins));
      if (text === metaRef.current.lastSaved) {
        setSaveState((st) => (st === 'dirty' ? 'saved' : st));
        return;
      }
      setSaveState((st) => (st === 'conflict' || st === 'saving' ? st : 'dirty'));
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        // In a conflict, nothing saves until the person picks a copy.
        if (metaRef.current.conflict) return;
        onSaveRef.current();
      }, SHEET_AUTOSAVE_MS);
    }, 300);
  };

  // Load the library and the workbook, then mount Univer.
  React.useEffect(() => {
    let cancelled = false;
    let disposeFns = [];
    (async () => {
      try {
        const [lib, r] = await Promise.all([
          loadSheetUniver(),
          fetch(`${API_BASE}/api/documents/workbook/${enc}`,
            { cache: 'no-store', credentials: 'include', headers: apiHeaders() }),
        ]);
        if (r.status === 415) {
          if (!cancelled) setState({ status: 'unsupported' });
          return;
        }
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const j = await r.json();
        if (cancelled || !holderRef.current) return;
        await sheetEnsureFonts(sheetFamiliesIn(j.workbook));
        if (cancelled || !holderRef.current) return;
        const csv = j.format === 'csv';
        const fonts = sheetFontList();
        const { univer, univerAPI } = lib.createUniver({
          locale: lib.LocaleType.EN_US,
          locales: { [lib.LocaleType.EN_US]: lib.locales['en-US'] },
          theme: sheetDomovoiTheme(lib),
          darkMode: sheetIsDark(),
          presets: [
            lib.UniverSheetsCorePreset({
              container: holderRef.current,
              header: true,
              toolbar: true,
              ribbonType: 'simple',
              formulaBar: true,
              contextMenu: true,
              disableAutoFocus: true,
              customFontFamily: { override: true, list: fonts },
              menu: sheetMenuConfig(csv),
              // =IMAGE() only for pictures inside the formula or on this
              // server: no outside requests (scripts/vendor/univer/safe_image.js).
              formula: { function: lib.formulaFunctions || [] },
              footer: {
                sheetBar: true, statisticBar: true, menus: false, zoomSlider: false,
                addSheetButtonConfig: { show: !csv },
              },
            }),
            lib.UniverSheetsSortPreset(),
            lib.UniverSheetsFilterPreset(),
          ],
        });
        apiRef.current = { univer, univerAPI };
        disposeFns.push(() => { try { univer.dispose(); } catch (e) { /* already gone */ } });
        const fWorkbook = univerAPI.createWorkbook(j.workbook);
        const origins = {};
        (j.workbook.sheetOrder || []).forEach((sid) => { origins[sid] = sid; });
        metaRef.current = {
          version: j.version, origins, format: j.format, editable: !!j.editable,
          why: j.why || null, truncated: j.truncated || [], lastSaved: null,
        };
        if (!j.editable) fWorkbook.setEditable(false);
        // What "nothing changed" looks like, in Univer's own spelling.
        metaRef.current.lastSaved = JSON.stringify(sheetSnapshotForSave(fWorkbook, origins));
        // Every edit is a mutation; selection and scrolling are not.
        const MUTATION = 2;
        const sub = fWorkbook.onCommandExecuted((cmd) => {
          if (!cmd || cmd.type !== MUTATION || !metaRef.current.editable) return;
          // The formula engine's own results are not edits.
          if (/^formula\.|calculation/.test(cmd.id || '')) return;
          scheduleSave();
        });
        disposeFns.push(() => sub && sub.dispose && sub.dispose());
        // A font picked from the menu: load it, then repaint with it.
        const fontSub = univerAPI.onCommandExecuted
          ? univerAPI.onCommandExecuted((cmd) => {
            if (cmd && cmd.id === 'sheet.command.set-range-font-family' && cmd.params && cmd.params.value) {
              sheetEnsureFonts([cmd.params.value]).then(() => {
                const ws = univerAPI.getActiveWorkbook() && univerAPI.getActiveWorkbook().getActiveSheet();
                if (ws && ws.refreshCanvas) ws.refreshCanvas();
              });
            }
          }) : null;
        disposeFns.push(() => fontSub && fontSub.dispose && fontSub.dispose());
        if (!cancelled) setState({ status: 'ready' });
      } catch (e) {
        if (!cancelled) setState({ status: 'error', message: String((e && e.message) || e) });
      }
    })();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
      clearTimeout(checkRef.current);
      disposeFns.forEach((fn) => fn());
      disposeFns = [];
      apiRef.current = null;
    };
  }, [rel_path, reloadKey]);

  // Dark mode follows the dashboard's theme switch live.
  React.useEffect(() => {
    const obs = new MutationObserver(() => {
      const api = apiRef.current;
      if (api && api.univerAPI.toggleDarkMode) api.univerAPI.toggleDarkMode(sheetIsDark());
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);

  // Who else has this sheet open (by device name).
  React.useEffect(() => {
    if (state.status !== 'ready') return undefined;
    let stopped = false;
    const ping = async (leaving) => {
      try {
        // The device is read from the request (this header, or the
        // registration cookie), not from the body.
        const dev = (typeof DeviceIdentity !== 'undefined' && DeviceIdentity.id) ? DeviceIdentity.id() : null;
        const r = await apiFetch(`/api/documents/presence/${enc}`, {
          method: 'POST', quiet: true, noPrompt: true,
          headers: dev ? { 'X-Device-Id': dev } : {},
          body: JSON.stringify({ session_id: sessionRef.current, leaving: !!leaving }),
        });
        if (!stopped && r && Array.isArray(r.others)) setOthers(r.others);
      } catch (e) { /* a courtesy: never in the way */ }
    };
    ping(false);
    const t = setInterval(() => ping(false), SHEET_PRESENCE_MS);
    return () => { stopped = true; clearInterval(t); ping(true); };
  }, [state.status, rel_path]);

  // Saving on the way out of the page, too.
  React.useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === 'hidden' && saveState === 'dirty') {
        clearTimeout(timerRef.current);
        onSaveRef.current();
      }
    };
    document.addEventListener('visibilitychange', onHide);
    return () => document.removeEventListener('visibilitychange', onHide);
  }, [saveState]);

  // The conflict's two answers: throw this copy away and open theirs, or
  // write this copy over theirs.
  const reloadTheirs = () => {
    metaRef.current.conflict = false;
    clearTimeout(timerRef.current);
    setSaveState('saved');
    setState({ status: 'loading' });
    setReloadKey((k) => k + 1);
  };
  const keepMine = () => onSave({ force: true });

  const onExport = async (fmt) => {
    // A refused or cancelled save must not be followed by a download of
    // the stale server-side file.
    if (dirty && !(await onSave())) return;
    const same = rel_path.toLowerCase().endsWith(`.${fmt}`);
    const a = document.createElement('a');
    a.href = withDeviceToken(same
      ? `${API_BASE}/api/documents/raw/${enc}`
      : `${API_BASE}/api/documents/export/sheet/${enc}?fmt=${fmt}`);
    a.download = '';
    document.body.appendChild(a); a.click(); a.remove();
  };

  const requestClose = async () => {
    // Autosave: an edit still waiting for its moment is saved now, and a
    // sheet with nothing left unsaved closes without asking.
    if (saveState !== 'conflict') {
      clearTimeout(timerRef.current);
      clearTimeout(checkRef.current);
      if (await onSave()) { onClose(); return; }
    }
    if (dirty && !window.confirm('You have unsaved changes. Discard them and close?')) return;
    onClose();
  };

  // The phone's Back button asks for Close (sheet.html / SheetWebEditor.kt).
  const requestCloseRef = React.useRef(requestClose);
  requestCloseRef.current = requestClose;
  React.useEffect(() => {
    const onAppClose = () => requestCloseRef.current();
    window.addEventListener('domovoi-sheet-close', onAppClose);
    return () => window.removeEventListener('domovoi-sheet-close', onAppClose);
  }, []);

  const meta = metaRef.current;
  const name = rel_path.replace(/^.*\//, '');
  const savedText = {
    saved: 'Saved', dirty: 'Editing…', saving: 'Saving…',
    error: 'Not saved', conflict: 'Not saved',
  }[saveState];

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'var(--bg)',
                  display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                    borderBottom: '1px solid var(--border)', background: 'var(--card)',
                    flexWrap: 'wrap' }}>
        <Icon name="table" size={16}/>
        <strong style={{ fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis',
                         whiteSpace: 'nowrap', maxWidth: '40vw' }}>{name}</strong>
        <Pill tone="idle">spreadsheet</Pill>
        {state.status === 'ready' && meta.editable && (
          <span aria-live="polite" style={{ fontSize: 11,
            color: saveState === 'saved' ? 'var(--fg-faint)'
              : (saveState === 'error' || saveState === 'conflict') ? 'var(--err-ink)' : 'var(--warn-ink)' }}>
            {savedText}
          </span>
        )}
        <span style={{ flex: 1 }}/>
        {!inApp && (
          <Button icon="file-down" title="download as .csv" onClick={() => onExport('csv')}>csv</Button>
        )}
        {!inApp && (
          <Button icon="file-down" title="download as .xlsx" onClick={() => onExport('xlsx')}>xlsx</Button>
        )}
        {!inApp && window.PrintFileButton && (
          <window.PrintFileButton libraryId="core:documents" path={rel_path} name={rel_path}
                                  title={dirty ? 'prints the last saved version' : 'print'}/>
        )}
        <Button icon="x" onClick={requestClose}>Close</Button>
      </div>

      <WriteBlockedNotice reason={blocked}>
        {dirty ? ' Your edits are still on screen — copy them out before closing.' : ''}
      </WriteBlockedNotice>

      {state.status === 'ready' && others.length > 0 && (
        <div role="status" style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '7px 14px',
                      borderBottom: '1px solid var(--border)', background: 'var(--warn-soft)',
                      color: 'var(--fg)', fontSize: 12 }}>
          <Icon name="users" size={13}/>
          <span>
            {others.join(', ')} {others.length === 1 ? 'has' : 'have'} this sheet open too.
            Edits aren’t shared live; if you both save, the second is asked which copy to keep.
          </span>
        </div>
      )}

      {saveState === 'conflict' && (
        <div role="alert" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
                      padding: '8px 14px', borderBottom: '1px solid var(--border)',
                      background: 'var(--err-soft)', color: 'var(--fg)', fontSize: 12 }}>
          <span>This sheet was saved from another device while you were editing.
            Your changes aren’t saved yet.</span>
          <span style={{ flex: 1 }}/>
          <Button onClick={reloadTheirs}>Load their version</Button>
          <Button variant="primary" onClick={keepMine}>Keep mine</Button>
        </div>
      )}

      {state.status === 'ready' && !meta.editable && meta.why && (
        <div style={{ padding: '7px 14px', borderBottom: '1px solid var(--border)',
                      background: 'var(--sunken)', color: 'var(--fg-muted)', fontSize: 12 }}>
          Read-only here: {meta.why}
        </div>
      )}
      {state.status === 'ready' && meta.editable && meta.truncated && meta.truncated.length > 0 && (
        <div style={{ padding: '7px 14px', borderBottom: '1px solid var(--border)',
                      background: 'var(--sunken)', color: 'var(--fg-muted)', fontSize: 12 }}>
          {meta.truncated.join(', ')}: only the first {meta.format === 'csv' ? 'rows and columns' : '5,000 rows and 100 columns'} are
          shown. The rest of the file is kept as it is.
        </div>
      )}

      <div key={reloadKey} style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {state.status === 'loading' && (
          <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Loading…</div>
        )}
        {state.status === 'error' && (
          <div style={{ padding: 20, color: 'var(--err-ink)', fontSize: 13 }}>
            Couldn’t load: {state.message}
          </div>
        )}
        {state.status === 'unsupported' && (
          <div style={{ padding: 24, color: 'var(--fg-muted)', fontSize: 13 }}>
            This format can’t be edited in the sheet editor (only .xlsx and .csv can) —
            download it instead.
          </div>
        )}
        {/* Univer mounts into this holder imperatively. */}
        <div ref={holderRef} className="domovoi-univer"
             style={{ position: 'absolute', inset: 0,
                      visibility: state.status === 'ready' ? 'visible' : 'hidden' }}/>
      </div>
    </div>
  );
};

window.SheetEditor = SheetEditorOverlay;
