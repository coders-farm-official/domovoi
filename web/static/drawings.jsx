/* Drawings page — Office Suite (filtered VIEW #3 over documents_dir).
 *
 * Same flat store as Documents/Spreadsheets; this view filters to
 * .excalidraw/.svg. UNLIKE the office engines, Excalidraw is an in-page
 * React library (no container, NO lock) — single-user, saves plain
 * JSON/SVG straight into documents_dir via /api/documents/drawings/*.
 *
 * NO-BUILD-STEP LOADING (validated 2026-07-01 — see INTEGRATION_office.md):
 *   Excalidraw dropped its UMD build at v0.18.0 (now ESM-only). The
 *   dashboard has no bundler (UMD + Babel-in-browser), so we pin the LAST
 *   UMD release, 0.17.6, whose dist/excalidraw.production.min.js exposes
 *   window.ExcalidrawLib and consumes the React/ReactDOM 18 UMD globals
 *   already present in index.html. Two shims are required:
 *     1. window.EXCALIDRAW_ASSET_PATH — where its fonts/worker assets load.
 *     2. window.process = { env: {} } — the UMD bundle reads process.env.*
 *        and throws a ReferenceError in-browser without it.
 *   (Vendored locally under /vendor/excalidraw/dist — same as React/Babel/
 *   lucide in index.html — so the dashboard makes zero external requests.)
 *
 * Data sources:
 *   * GET  /api/documents?kind=drawing        — list .excalidraw/.svg.
 *   * POST /api/documents/drawings/read        — load a scene for editing.
 *   * POST /api/documents/drawings/write       — save scene (JSON) / SVG.
 *   * POST /api/documents/create               — new blank .excalidraw.
 */

const EXCALIDRAW_VERSION = '0.17.6';
// Vendored locally (see web/static/vendor/excalidraw/dist) — served by the
// web backend's "/" StaticFiles mount so the whole dist/ (incl.
// excalidraw-assets/ fonts + locales lazy-loaded via EXCALIDRAW_ASSET_PATH)
// is reachable with zero external network requests. Trailing slash matters:
// Excalidraw concatenates asset filenames onto EXCALIDRAW_ASSET_PATH.
const EXCALIDRAW_ASSET_BASE = '/vendor/excalidraw/dist/';
const EXCALIDRAW_UMD = `${EXCALIDRAW_ASSET_BASE}excalidraw.production.min.js`;

/* Load the Excalidraw UMD once, applying the two required shims first. */
let _excalidrawPromise = null;
const loadExcalidraw = () => {
  if (_excalidrawPromise) return _excalidrawPromise;
  // Shims MUST be set before the bundle evaluates.
  window.EXCALIDRAW_ASSET_PATH = window.EXCALIDRAW_ASSET_PATH || EXCALIDRAW_ASSET_BASE;
  window.process = window.process || { env: { NODE_ENV: 'production' } };
  const sharedLoad = window.OfficeSuite && window.OfficeSuite.officeLoadScript;
  _excalidrawPromise = (sharedLoad
    ? sharedLoad(EXCALIDRAW_UMD)
    : new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = EXCALIDRAW_UMD;
        s.onload = () => resolve(); s.onerror = () => reject(new Error('excalidraw load failed'));
        document.head.appendChild(s);
      })
  ).then(() => window.ExcalidrawLib);
  return _excalidrawPromise;
};

const useExcalidraw = () => {
  const [lib, setLib] = React.useState(window.ExcalidrawLib || null);
  React.useEffect(() => {
    if (lib) return;
    let alive = true;
    loadExcalidraw().then(l => { if (alive) setLib(l); })
      .catch(e => console.error('Excalidraw failed to load:', e));
    return () => { alive = false; };
  }, []);
  return lib;
};

/* The canvas. `apiRef` receives the Excalidraw imperative API so the
 * page can pull scene data out at save time. */
const DrawingCanvas = ({ lib, initialData, apiRef }) => {
  const Excalidraw = lib && lib.Excalidraw;
  if (!Excalidraw) {
    return <div style={{ padding: 24, color: 'var(--fg-faint)' }}>Loading Excalidraw…</div>;
  }
  return (
    <div style={{ width: '100%', height: '100%' }}>
      {React.createElement(Excalidraw, {
        initialData: initialData || null,
        excalidrawAPI: (api) => { apiRef.current = api; },
      })}
    </div>
  );
};

/* Full-screen drawing editor overlay. */
const DrawingOverlay = ({ file, lib, onClose, onSaved, fire }) => {
  const apiRef = React.useRef(null);
  const [initialData, setInitialData] = React.useState(file.rel_path ? undefined : null);
  const [saving, setSaving] = React.useState(false);

  // Load an existing scene's JSON when editing a saved file.
  React.useEffect(() => {
    if (!file.rel_path || !file.rel_path.endsWith('.excalidraw')) {
      setInitialData(null);
      return;
    }
    apiPost('/api/documents/drawings/read', { rel_path: file.rel_path })
      .then(res => {
        try {
          const scene = JSON.parse(res.content);
          setInitialData({ elements: scene.elements || [], appState: scene.appState || {}, files: scene.files || {} });
        } catch { setInitialData(null); }
      })
      .catch(() => setInitialData(null));
  }, [file.rel_path]);

  const doSave = async (asSvg) => {
    const api = apiRef.current;
    if (!api || !lib) return;
    setSaving(true);
    try {
      const elements = api.getSceneElements();
      const appState = api.getAppState();
      const files = api.getFiles();
      let rel = file.rel_path;
      let content;
      if (asSvg) {
        const svg = await lib.exportToSvg({ elements, appState, files, exportPadding: 10 });
        content = new XMLSerializer().serializeToString(svg);
        rel = (rel || 'drawing').replace(/\.(excalidraw|svg)$/i, '') + '.svg';
        await apiPost('/api/documents/drawings/write', { rel_path: rel, content, fmt: 'svg' });
      } else {
        content = lib.serializeAsJSON(elements, appState, files, 'local');
        if (!rel) {
          const name = window.prompt('Save whiteboard as:', 'sketch');
          if (!name) { setSaving(false); return; }
          rel = name.replace(/\.excalidraw$/i, '') + '.excalidraw';
        }
        await apiPost('/api/documents/drawings/write', { rel_path: rel, content, fmt: 'excalidraw' });
      }
      fire('Saved');
      onSaved();
    } catch (e) {
      fire(`Save failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setSaving(false); }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'var(--bg)',
                  display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                    borderBottom: '1px solid var(--border)', background: 'var(--card)' }}>
        <Icon name="pen-tool" size={16}/>
        <strong style={{ fontSize: 14 }}>{file.rel_path || 'new whiteboard'}</strong>
        <span style={{ flex: 1 }}/>
        <Button icon="image" disabled={saving} onClick={() => doSave(true)}>Export SVG</Button>
        <Button variant="primary" icon="save" disabled={saving} onClick={() => doSave(false)}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
        <Button icon="x" onClick={onClose}>Close</Button>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <DrawingCanvas lib={lib} initialData={initialData} apiRef={apiRef}/>
      </div>
    </div>
  );
};

const DrawingRow = ({ doc, onOpen }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px',
                borderTop: '1px solid var(--border-soft)' }}>
    <Icon name={doc.ext === '.svg' ? 'image' : 'pen-tool'} size={18}/>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 500 }}>
        {doc.name}<span style={{ color: 'var(--fg-faint)' }}>{doc.ext}</span>
      </div>
      <div style={{ fontSize: 11, color: 'var(--fg-faint)' }} className="mono">
        {window.OfficeSuite ? window.OfficeSuite.fmtBytes(doc.size) : doc.size + ' B'}
        {' · '}{liveRelTime(new Date(doc.modified_at * 1000).toISOString())}
      </div>
    </div>
    {/* .excalidraw scenes are editable; .svg exports are view-only here */}
    <Button icon="edit-3" disabled={doc.ext === '.svg'}
            title={doc.ext === '.svg' ? 'SVG export — open the .excalidraw to edit' : 'edit'}
            onClick={() => onOpen(doc)}>
      Edit
    </Button>
  </div>
);

/* Expose the Excalidraw surface so the unified Documents page (documents.jsx)
 * can open/edit .excalidraw scenes inline — the same window.* contract
 * spreadsheets.jsx uses to borrow the office suite. drawings.jsx loads
 * AFTER documents.jsx, so DocumentsPage reads these at render time (both are
 * loaded before the app first renders). */
window.DrawingSuite = { useExcalidraw, DrawingOverlay, DrawingRow, loadExcalidraw };

const DrawingsPage = () => {
  const lib = useExcalidraw();
  const { items, loading, refresh } =
    useApiList('/api/documents?kind=drawing');
  const [editing, setEditing] = React.useState(null);   // {rel_path} | {} (new)
  const [fire, toastNode] = useToast();

  return (
    <div className="page">
      <PageHeader title="Drawings"
        sub="Whiteboards & diagrams — Excalidraw, saved into documents_dir"
        actions={<Button variant="primary" icon="plus" onClick={() => setEditing({})}>New whiteboard</Button>}/>

      <Card>
        {loading ? (
          <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Loading…</div>
        ) : items.length === 0 ? (
          <Empty title="no drawings yet" sub="click New whiteboard to sketch one"/>
        ) : (
          items.map(doc => <DrawingRow key={doc.rel_path} doc={doc} onOpen={setEditing}/>)
        )}
      </Card>

      {editing && (
        <DrawingOverlay file={editing} lib={lib}
                        onClose={() => setEditing(null)}
                        onSaved={() => { refresh(); }}
                        fire={fire}/>
      )}
      {toastNode}
    </div>
  );
};

window.DrawingsPage = DrawingsPage;
