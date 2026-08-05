/* Homegrown spreadsheet editor (window.SheetEditor) — the Documents
 * library's editor for .xlsx / .csv, replacing the retired office engines.
 *
 * The grid is the vendored `x-spreadsheet` (MIT — /vendor/xspreadsheet),
 * which handles cell editing, basic styling and client-side formula
 * evaluation (=SUM(...), etc.). Persistence goes through the grid model
 * endpoints (GET/PUT /api/documents/sheet/{rel}): values as `v`, formula
 * strings as `f` — .xlsx keeps formulas as formulas via openpyxl, .csv
 * stores what you see. Export buttons hand off to /api/documents/export/
 * sheet (csv | xlsx).
 */

/* server grid rows[[{v,f}]] → x-spreadsheet data {rows:{r:{cells:{c:{text}}}}} */
const _gridToXs = (rows) => {
  const out = { rows: {} };
  rows.forEach((row, r) => {
    const cells = {};
    (row || []).forEach((cell, c) => {
      if (!cell) return;
      const text = cell.f != null && cell.f !== '' ? cell.f : cell.v;
      if (text != null && text !== '') cells[c] = { text: String(text) };
    });
    if (Object.keys(cells).length) out.rows[r] = { cells };
  });
  return out;
};

/* x-spreadsheet data → server grid (formula = text starting with '='). */
const _xsToGrid = (data) => {
  const rowsObj = (data && data.rows) || {};
  let maxRow = -1;
  Object.keys(rowsObj).forEach((k) => {
    const n = Number(k);
    if (Number.isFinite(n)) maxRow = Math.max(maxRow, n);
  });
  const grid = [];
  for (let r = 0; r <= maxRow; r++) {
    const cellsObj = (rowsObj[r] && rowsObj[r].cells) || {};
    let maxCol = -1;
    Object.keys(cellsObj).forEach((k) => {
      const n = Number(k);
      if (Number.isFinite(n)) maxCol = Math.max(maxCol, n);
    });
    const row = [];
    for (let c = 0; c <= maxCol; c++) {
      const t = cellsObj[c] && cellsObj[c].text != null ? String(cellsObj[c].text) : '';
      if (t === '') row.push(null);
      else if (t.startsWith('=')) row.push({ f: t });
      else row.push({ v: t });
    }
    grid.push(row);
  }
  return grid;
};

const SheetEditorOverlay = ({ rel_path, onClose, fire }) => {
  const [state, setState] = React.useState({ status: 'loading' });
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const holderRef = React.useRef(null);
  const sheetRef = React.useRef(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/documents/sheet/${encodeURIComponent(rel_path)}`,
          { cache: 'no-store', credentials: 'include' });
        if (r.status === 415) {
          if (!cancelled) setState({ status: 'unsupported' });
          return;
        }
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const j = await r.json();
        if (cancelled || !holderRef.current || !window.x_spreadsheet) {
          if (!cancelled) setState(window.x_spreadsheet ? { status: 'ready' }
            : { status: 'error', message: 'spreadsheet library missing' });
          return;
        }
        holderRef.current.innerHTML = '';
        const rect = holderRef.current.getBoundingClientRect();
        const sheet = new window.x_spreadsheet(holderRef.current, {
          mode: 'edit',
          showToolbar: true,
          showGrid: true,
          showBottomBar: false,
          view: {
            height: () => holderRef.current ? holderRef.current.clientHeight : rect.height,
            width: () => holderRef.current ? holderRef.current.clientWidth : rect.width,
          },
        });
        sheet.loadData(_gridToXs(j.rows || []));
        sheet.change(() => setDirty(true));
        sheetRef.current = sheet;
        setState({ status: 'ready' });
      } catch (e) {
        if (!cancelled) setState({ status: 'error', message: String(e.message || e) });
      }
    })();
    return () => { cancelled = true; sheetRef.current = null; };
  }, [rel_path]);

  const onSave = async () => {
    const sheet = sheetRef.current;
    if (!sheet) return;
    setSaving(true);
    try {
      // getData() returns one object per sheet tab; the editor uses tab 0.
      const data = Array.isArray(sheet.getData()) ? sheet.getData()[0] : sheet.getData();
      await apiFetch(`/api/documents/sheet/${encodeURIComponent(rel_path)}`, {
        method: 'PUT', body: JSON.stringify({ rows: _xsToGrid(data) }),
      });
      setDirty(false);
      fire && fire('Saved');
    } catch (e) {
      fire && fire(`Save failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setSaving(false); }
  };

  const onExport = async (fmt) => {
    if (dirty) await onSave();
    const a = document.createElement('a');
    a.href = `${API_BASE}/api/documents/export/sheet/${encodeURIComponent(rel_path)}?fmt=${fmt}`;
    a.download = '';
    document.body.appendChild(a); a.click(); a.remove();
  };

  const requestClose = () => {
    if (dirty && !window.confirm('You have unsaved changes. Discard them and close?')) return;
    onClose();
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'var(--bg)',
                  display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                    borderBottom: '1px solid var(--border)', background: 'var(--card)' }}>
        <Icon name="table" size={16}/>
        <strong style={{ fontSize: 14 }}>{rel_path}</strong>
        <Pill tone="idle">spreadsheet</Pill>
        {dirty && <span style={{ fontSize: 11, color: 'var(--warn)' }}>unsaved — click Save</span>}
        <span style={{ flex: 1 }}/>
        <Button icon="file-down" title="export as .csv" onClick={() => onExport('csv')}>csv</Button>
        <Button icon="file-down" title="export as .xlsx" onClick={() => onExport('xlsx')}>xlsx</Button>
        {state.status === 'ready' && (
          <Button variant="primary" icon="save" disabled={saving || !dirty} onClick={onSave}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        )}
        <Button icon="x" onClick={requestClose}>Close</Button>
      </div>

      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {state.status === 'loading' && (
          <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Loading…</div>
        )}
        {state.status === 'error' && (
          <div style={{ padding: 20, color: 'var(--err)', fontSize: 13 }}>
            Couldn’t load: {state.message}
          </div>
        )}
        {state.status === 'unsupported' && (
          <div style={{ padding: 24, color: 'var(--fg-muted)', fontSize: 13 }}>
            This format can’t be edited in the sheet editor (only .xlsx and .csv can) —
            download it instead.
          </div>
        )}
        {/* x-spreadsheet mounts into this holder imperatively. */}
        <div ref={holderRef} style={{ position: 'absolute', inset: 0 }}/>
      </div>
    </div>
  );
};

window.SheetEditor = SheetEditorOverlay;
