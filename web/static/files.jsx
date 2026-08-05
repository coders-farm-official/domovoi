/* Files page — a multi-library file browser over every root the dashboard
 * exposes (core media dirs, enabled-plugin media libraries, present
 * removable drives), driven by the generic /api/files/* surface.
 *
 * Documents-library editing is fully homegrown now — the former
 * OnlyOffice/Collabora iframe engines are retired. Rows inside
 * `core:documents` route their open-action BY TYPE (client-derived
 * `category`, mirroring the backend's _doc_category):
 *   * 'doc'      → markdown editor overlay (doc_editor.jsx · window.DocEditor)
 *   * 'sheet'    → spreadsheet editor overlay (sheet_editor.jsx · window.SheetEditor)
 *   * 'drawing'  → in-page Excalidraw editor (.excalidraw)
 *   * 'newtab'   → open the /raw endpoint in a NEW TAB (pdf/images)
 *   * 'download' → legacy office formats — save-to-device only
 *   * 'text'     → in-app text editor overlay (.txt/unknown)
 *
 * Loads BEFORE doc_editor.jsx / sheet_editor.jsx / drawings.jsx, which
 * register the editor overlays this page borrows. Babel-in-browser shares
 * one global scope; index.html fixes script-tag order.
 *
 * Data sources:
 *   Files browser (all libraries):
 *     * GET  /api/files/libraries   — the library registry (root_path stripped).
 *     * GET  /api/files/browse      — one directory level inside a library.
 *     * GET  /api/files/download    — file (attachment) or dir (zip).
 *     * POST /api/files/upload      — upload into the current dir.
 *     * POST /api/files/delete      — delete files / (recursive) folders.
 *     * POST /api/files/import      — copy from a removable drive into a library.
 *   Documents-library editing:
 *     * /api/documents/{text,sheet,raw,create,export} — homegrown editors.
 */

/* ---- shared: dynamic script loader (dedup by URL) ---------- */
const _officeScripts = {};
const officeLoadScript = (url) => {
  if (_officeScripts[url]) return _officeScripts[url];
  _officeScripts[url] = new Promise((resolve, reject) => {
    if (document.querySelector(`script[data-office="${url}"]`)) { resolve(); return; }
    const s = document.createElement('script');
    s.src = url; s.dataset.office = url;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`failed to load ${url}`));
    document.head.appendChild(s);
  });
  return _officeScripts[url];
};

const fmtBytes = (n) => {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

/* Slim shared surface (drawings.jsx borrows these two). */
window.OfficeSuite = { officeLoadScript, fmtBytes };

/* ---- raw / text / download helpers ------------------------ */
const docRawUrl = (rel) => `/api/documents/raw/${encodeURIComponent(rel)}`;
const docTextUrl = (rel) => `/api/documents/text/${encodeURIComponent(rel)}`;
const openDocInNewTab = (rel) => window.open(docRawUrl(rel), '_blank', 'noopener');
const downloadDoc = (rel) => {
  const a = document.createElement('a');
  a.href = docRawUrl(rel);
  a.download = rel;        // same-origin → forces a download despite inline C-D
  document.body.appendChild(a);
  a.click();
  a.remove();
};

/* Bulk download → server-built zip. Streams the response so we can report
 * real progress (download-zip sets Content-Length). `onProgress` gets a
 * 0..1 fraction, or null when the length is unknown. */
const downloadDocsZip = async (relPaths, onProgress) => {
  const r = await fetch('/api/documents/download-zip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel_paths: relPaths }),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const total = Number(r.headers.get('Content-Length')) || 0;
  const reader = r.body && r.body.getReader ? r.body.getReader() : null;
  let blob;
  if (reader) {
    const chunks = [];
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      if (onProgress) onProgress(total ? received / total : null);
    }
    blob = new Blob(chunks, { type: 'application/zip' });
  } else {
    blob = await r.blob();
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'documents.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};

/* ---- in-app text editor overlay --------------------------- */
const TextEditorOverlay = ({ rel_path, onClose, fire }) => {
  const [state, setState] = React.useState({ status: 'loading' });
  const [text, setText] = React.useState('');
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // no-store: always read the file fresh. Without this a browser can
        // serve a cached (often empty, just-created) copy on reopen, making a
        // successful save look like it was lost.
        const r = await fetch(docTextUrl(rel_path), { cache: 'no-store' });
        if (r.status === 415) {
          const j = await r.json().catch(() => ({}));
          if (!cancelled) setState({ status: 'unpreviewable', reason: j.reason || 'binary' });
          return;
        }
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const j = await r.json();
        if (!cancelled) { setText(j.text || ''); setState({ status: 'ready' }); }
      } catch (e) {
        if (!cancelled) setState({ status: 'error', message: String(e.message || e) });
      }
    })();
    return () => { cancelled = true; };
  }, [rel_path]);

  const onSave = async () => {
    setSaving(true);
    try {
      const r = await fetch(docTextUrl(rel_path), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      setDirty(false);
      fire && fire('Saved');
    } catch (e) {
      fire && fire(`Save failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setSaving(false); }
  };

  // This editor saves on the Save button, NOT automatically — so guard Close
  // when there are unsaved edits rather than dropping them silently.
  const requestClose = () => {
    if (dirty && !window.confirm('You have unsaved changes. Discard them and close?')) return;
    onClose();
  };

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                  borderBottom: '1px solid var(--border)', background: 'var(--card)' }}>
      <Icon name="file-text" size={16}/>
      <strong style={{ fontSize: 14 }}>{rel_path}</strong>
      <Pill tone="idle">text</Pill>
      {dirty && <span style={{ fontSize: 11, color: 'var(--warn)' }}>unsaved — click Save</span>}
      <span style={{ flex: 1 }}/>
      {state.status === 'ready' && (
        <Button variant="primary" icon="save" disabled={saving || !dirty} onClick={onSave}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      )}
      <Button icon="x" onClick={requestClose}>Close</Button>
    </div>
  );

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'var(--bg)',
                  display: 'flex', flexDirection: 'column' }}>
      {header}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {state.status === 'loading' && (
          <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Loading…</div>
        )}
        {state.status === 'error' && (
          <div style={{ padding: 20, color: 'var(--danger)', fontSize: 13 }}>
            Couldn’t load: {state.message}
          </div>
        )}
        {state.status === 'unpreviewable' && (
          <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 12,
                        color: 'var(--fg-muted)', fontSize: 13 }}>
            <div>
              Can’t preview <code className="mono">{rel_path}</code> as text
              {state.reason === 'too_large' ? ' — it’s too large.' : ' — it looks binary.'}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button icon="download" onClick={() => downloadDoc(rel_path)}>Download</Button>
              <Button icon="external-link" onClick={() => openDocInNewTab(rel_path)}>Open raw</Button>
            </div>
          </div>
        )}
        {state.status === 'ready' && (
          <textarea
            className="mono"
            value={text}
            spellCheck={false}
            onChange={(e) => { setText(e.target.value); setDirty(true); }}
            style={{ flex: 1, minHeight: 0, width: '100%', resize: 'none', border: 'none',
                     outline: 'none', padding: '14px 16px', fontSize: 13, lineHeight: 1.55,
                     background: 'var(--bg)', color: 'var(--fg)', tabSize: 4 }}
          />
        )}
      </div>
    </div>
  );
};

/* ---- one Documents-library file row: checkbox + Edit/Open · Download · Delete ----
 * Exactly one primary action, chosen by file TYPE:
 *   doc              → Edit in the markdown editor
 *   sheet            → Edit in the spreadsheet editor
 *   drawing          → Edit in Excalidraw
 *   text             → Edit in the in-app text editor
 *   newtab (pdf/img) → Open in a new tab
 *   download         → legacy office formats — Download is the action
 */
const DocRow = ({ doc, selected, onToggleSelect,
                  onOpenDoc, onOpenSheet, onOpenText, onOpenDrawing, onDelete }) => {
  const cat = doc.category || 'text';
  const icon = cat === 'newtab' ? (doc.ext === '.pdf' ? 'file-text' : 'image')
    : cat === 'drawing' ? 'pen-tool'
    : cat === 'sheet' ? 'table'
    : cat === 'doc' || cat === 'download' ? 'file-text' : 'file';

  let primary;
  if (cat === 'doc') {
    primary = { label: 'Edit', icon: 'edit', title: 'edit markdown',
      onClick: () => onOpenDoc(doc.rel_path) };
  } else if (cat === 'sheet') {
    primary = { label: 'Edit', icon: 'edit', title: 'edit spreadsheet',
      onClick: () => onOpenSheet(doc.rel_path) };
  } else if (cat === 'drawing') {
    primary = { label: 'Edit', icon: 'edit-3', onClick: () => onOpenDrawing(doc) };
  } else if (cat === 'text') {
    primary = { label: 'Edit', icon: 'edit', onClick: () => onOpenText(doc.rel_path) };
  } else if (cat === 'download') {
    primary = { label: 'Download', icon: 'download',
      title: 'legacy office format — download to edit elsewhere',
      onClick: () => downloadDoc(doc.rel_path) };
  } else {
    primary = { label: 'Open', icon: 'external-link', title: 'open in a new tab',
      onClick: () => openDocInNewTab(doc.rel_path) };
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px',
                  borderTop: '1px solid var(--border-soft)',
                  background: selected ? 'var(--brand-soft)' : 'transparent' }}>
      <input type="checkbox" checked={selected} title="select"
             onChange={() => onToggleSelect(doc.rel_path)}
             style={{ cursor: 'pointer', width: 15, height: 15, flexShrink: 0 }}/>
      <Icon name={icon} size={18}/>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap',
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {doc.name}<span style={{ color: 'var(--fg-faint)' }}>{doc.ext}</span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--fg-faint)' }} className="mono">
          {fmtBytes(doc.size)} · {liveRelTime(new Date(doc.modified_at * 1000).toISOString())}
        </div>
      </div>
      <Button icon={primary.icon} disabled={primary.disabled} title={primary.title}
              onClick={primary.onClick}>{primary.label}</Button>
      {cat !== 'download' && (
        <Button icon="download" title="download"
                onClick={() => downloadDoc(doc.rel_path)}>Download</Button>
      )}
      <Button icon="trash-2" title="delete" onClick={() => onDelete(doc)}>Delete</Button>
    </div>
  );
};

/* ---- "+ New" create menu (Documents library only) ---------- */
const NEW_KINDS = [
  { kind: 'doc',     label: 'Document',      icon: 'file-text', hint: 'Markdown document · .md' },
  { kind: 'sheet',   label: 'Spreadsheet',   icon: 'table',     hint: 'Workbook · .xlsx' },
  { kind: 'drawing', label: 'Drawing',       icon: 'edit-3',    hint: 'Excalidraw whiteboard' },
  { kind: 'text',    label: 'Text file',     icon: 'file',      hint: 'Plain text · name as typed' },
];

const NewMenu = ({ disabled, onPick }) => {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <Button variant="primary" icon="plus" disabled={disabled}
              onClick={() => setOpen((o) => !o)}>New</Button>
      {open && (
        <div style={{ position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 50,
                      minWidth: 240, background: 'var(--card)', border: '1px solid var(--border)',
                      borderRadius: 'var(--r-md)', boxShadow: 'var(--shadow-md), var(--inner-highlight)',
                      padding: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {NEW_KINDS.map((it) => (
            <button key={it.kind}
                    onClick={() => { setOpen(false); onPick(it.kind); }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px',
                             background: 'transparent', border: 'none', borderRadius: 'var(--r-sm)',
                             cursor: 'pointer', textAlign: 'left', font: 'inherit', color: 'var(--fg)' }}>
              <Icon name={it.icon} size={16}/>
              <span style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{it.label}</span>
                <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{it.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

/* ============================================================ *
 *  Files browser — the generic multi-library surface.
 * ============================================================ */

/* Client mirror of the backend _doc_category (documents.py). Drives the
 * per-row editing affordance for the Documents library only. */
const _FILES_OFFICE_WP = new Set(['.docx', '.doc', '.odt', '.rtf']);
const _FILES_SHEET_EDIT = new Set(['.xlsx', '.csv']);
const _FILES_IMAGE = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.avif', '.svg']);
const extOf = (name) => { const i = name.lastIndexOf('.'); return i >= 0 ? name.slice(i).toLowerCase() : ''; };
const docCategory = (ext) => {
  if (ext === '.md' || ext === '.markdown') return 'doc';
  if (_FILES_SHEET_EDIT.has(ext)) return 'sheet';
  if (_FILES_OFFICE_WP.has(ext) || ext === '.xls' || ext === '.ods' || ext === '.odg') return 'download';
  if (ext === '.excalidraw') return 'drawing';
  if (ext === '.pdf' || _FILES_IMAGE.has(ext)) return 'newtab';
  return 'text';
};

/* A browse entry → the `doc` shape DocRow expects (Documents library). */
const entryToDoc = (entry) => {
  const ext = extOf(entry.name);
  const base = ext ? entry.name.slice(0, -ext.length) : entry.name;
  return {
    rel_path: entry.rel, name: base, ext,
    size: entry.size, modified_at: entry.mtime,
    category: docCategory(ext), locked_by: entry.locked_by,
  };
};

/* Per-entry-kind lucide glyph for the generic (non-Documents) rows. */
const entryIcon = (kind) => ({
  folder: 'folder', audio: 'music', video: 'film', 'doc-office': 'file-text',
  'doc-text': 'file', image: 'image', pdf: 'file-text', other: 'file',
}[kind] || 'file');

/* Per-KIND library glyph fallback (the registry supplies `icon`, but keep a
 * sane default per kind). */
const LIBRARY_KIND_GROUPS = [
  ['core', 'Core'],
  ['plugin', 'Plugins'],
  ['removable', 'Removable'],
];

/* Streaming download that attaches the admin bearer (so it works even when
 * the dashboard points at a cross-origin Domovoi server, where <a download>
 * can't carry the header). `onProgress` gets a 0..1 fraction, or null when
 * the length is unknown. Falls back to the filename in Content-Disposition. */
const streamFileDownload = async (url, fallbackName, onProgress) => {
  const full = url.startsWith('http') ? url : `${API_BASE}${url}`;
  let hdrs = {};
  try { hdrs = (typeof Auth !== 'undefined' && Auth.headers && Auth.headers()) || {}; } catch {}
  const r = await fetch(full, { credentials: 'include', headers: hdrs });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const total = Number(r.headers.get('Content-Length')) || 0;
  let name = fallbackName;
  const cd = r.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)/i);
  if (m) { try { name = decodeURIComponent(m[1]); } catch { name = m[1]; } }
  const reader = r.body && r.body.getReader ? r.body.getReader() : null;
  let blob;
  if (reader) {
    const chunks = [];
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      if (onProgress) onProgress(total ? received / total : null);
    }
    blob = new Blob(chunks);
  } else {
    blob = await r.blob();
  }
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objUrl; a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objUrl);
};

/* Library selector chip — per-library glyph + a small kind badge. */
const LibChip = ({ lib, active, onClick }) => (
  <button onClick={onClick} title={lib.owner ? `${lib.kind} · ${lib.owner}` : lib.kind}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px',
                   border: `1px solid ${active ? 'var(--brand)' : 'var(--border)'}`,
                   background: active ? 'var(--brand-soft)' : 'var(--card)',
                   color: 'var(--fg)', borderRadius: 'var(--r-md)', cursor: 'pointer',
                   font: 'inherit', fontSize: 13 }}>
    <Icon name={lib.icon || 'folder'} size={16}/>
    <span style={{ whiteSpace: 'nowrap' }}>{lib.label}</span>
    <Icon name={lib.kind_icon || 'folder'} size={12}/>
  </button>
);

const LibrarySelector = ({ libraries, activeId, onSelect, onRefresh }) => (
  <Card>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px',
                  borderBottom: '1px solid var(--border-soft)' }}>
      <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>library</span>
      <span style={{ flex: 1 }}/>
      <Button icon="refresh-cw" title="rescan libraries & drives" onClick={onRefresh}>Rescan</Button>
    </div>
    <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {LIBRARY_KIND_GROUPS.map(([kind, label]) => {
        const libs = libraries.filter((l) => l.kind === kind);
        if (!libs.length) return null;
        return (
          <div key={kind}>
            <div style={{ fontSize: 11, textTransform: 'lowercase', color: 'var(--fg-faint)',
                          marginBottom: 6 }}>{label}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {libs.map((l) => (
                <LibChip key={l.id} lib={l} active={l.id === activeId} onClick={() => onSelect(l)}/>
              ))}
            </div>
          </div>
        );
      })}
      {libraries.length === 0 && (
        <div style={{ fontSize: 13, color: 'var(--fg-faint)', padding: '4px 2px' }}>
          no libraries available
        </div>
      )}
    </div>
  </Card>
);

/* Breadcrumb trail — the library label is the root crumb. */
const FilesBreadcrumb = ({ lib, segments, onNav }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
                fontSize: 12, color: 'var(--fg-muted)' }}>
    <button onClick={() => onNav('')}
            style={{ background: 'transparent', border: 'none', cursor: 'pointer',
                     color: segments.length ? 'var(--brand)' : 'var(--fg)', font: 'inherit',
                     padding: 0, fontWeight: 600 }}>
      {lib ? lib.label : '—'}
    </button>
    {segments.map((seg, i) => {
      const p = segments.slice(0, i + 1).join('/');
      const last = i === segments.length - 1;
      return (
        <React.Fragment key={p}>
          <span style={{ color: 'var(--fg-faint)' }}>/</span>
          <button onClick={() => onNav(p)} disabled={last}
                  style={{ background: 'transparent', border: 'none',
                           cursor: last ? 'default' : 'pointer',
                           color: last ? 'var(--fg)' : 'var(--brand)', font: 'inherit',
                           padding: 0, fontWeight: last ? 600 : 400 }}>
            {seg}
          </button>
        </React.Fragment>
      );
    })}
  </div>
);

/* "Import into…" menu — lists importable destination libraries. */
const ImportMenu = ({ importables, disabled, onPick }) => {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <Button icon="import" disabled={disabled || importables.length === 0}
              title={importables.length === 0 ? 'no importable libraries' : 'import into a library'}
              onClick={() => setOpen((o) => !o)}>Import</Button>
      {open && (
        <div style={{ position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 50,
                      minWidth: 220, background: 'var(--card)', border: '1px solid var(--border)',
                      borderRadius: 'var(--r-md)', boxShadow: 'var(--shadow-md), var(--inner-highlight)',
                      padding: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <div style={{ fontSize: 11, color: 'var(--fg-faint)', padding: '4px 8px' }}>
            import into…
          </div>
          {importables.map((l) => (
            <button key={l.id}
                    onClick={() => { setOpen(false); onPick(l.id); }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px',
                             background: 'transparent', border: 'none', borderRadius: 'var(--r-sm)',
                             cursor: 'pointer', textAlign: 'left', font: 'inherit', color: 'var(--fg)' }}>
              <Icon name={l.icon || 'folder'} size={16}/>
              <span style={{ fontSize: 13 }}>{l.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

/* One generic browser row: folder (navigates) or file. Handles selection,
 * Download, Delete (editable), and Import (removable source). Documents-
 * library FILE rows use DocRow instead (editing affordance). */
const BrowserRow = ({ entry, libraryId, selected, onToggleSelect, editable, removable,
                      importables, onOpen, onDownload, onDelete, onImport }) => {
  const isDir = entry.is_dir;
  const iso = entry.mtime ? new Date(entry.mtime * 1000).toISOString() : null;
  // Images in ANY library open inline in a new tab via the generic
  // library-image serve (the Files tab owns image browsing).
  const openImage = () => window.open(
    `${API_BASE}/api/images/raw?library_id=${encodeURIComponent(libraryId)}`
    + `&path=${encodeURIComponent(entry.rel)}`, '_blank', 'noopener');
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px',
                  borderTop: '1px solid var(--border-soft)',
                  background: selected ? 'var(--brand-soft)' : 'transparent' }}>
      <input type="checkbox" checked={selected} title="select"
             onChange={() => onToggleSelect(entry.rel)}
             style={{ cursor: 'pointer', width: 15, height: 15, flexShrink: 0 }}/>
      <Icon name={entryIcon(entry.kind)} size={18}/>
      <div style={{ flex: 1, minWidth: 0, cursor: isDir ? 'pointer' : 'default' }}
           onClick={isDir ? onOpen : undefined}>
        <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap',
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {entry.name}
        </div>
        <div style={{ fontSize: 11, color: 'var(--fg-faint)' }} className="mono">
          {isDir ? 'folder' : fmtBytes(entry.size)}{iso ? ` · ${liveRelTime(iso)}` : ''}
        </div>
      </div>
      {isDir && (
        <Button icon="chevron-right" title="open folder" onClick={onOpen}>Open</Button>
      )}
      {!isDir && entry.kind === 'image' && (
        <Button icon="external-link" title="open in a new tab" onClick={openImage}>Open</Button>
      )}
      <Button icon="download" title="download" onClick={onDownload}>Download</Button>
      {removable && (
        <ImportMenu importables={importables} onPick={(tid) => onImport(entry, tid)}/>
      )}
      {editable && (
        <Button icon="trash-2" title="delete" onClick={onDelete}>Delete</Button>
      )}
    </div>
  );
};

/* Delete confirmation dialog. A recursive (folder) delete gets a loud
 * warning; a files-only delete is a plain confirm. */
const DeleteConfirmDialog = ({ state, busy, onCancel, onConfirm }) => {
  if (!state) return null;
  const { entries } = state;
  const folders = entries.filter((e) => e.is_dir);
  const recursive = folders.length > 0;
  const n = entries.length;
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 90, background: 'rgba(0,0,0,0.45)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
         onClick={onCancel}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ width: 'min(460px, 100%)', background: 'var(--card)',
                    border: '1px solid var(--border)', borderRadius: 'var(--r-lg)',
                    boxShadow: 'var(--shadow-md), var(--inner-highlight)', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '13px 16px',
                      borderBottom: '1px solid var(--border-soft)' }}>
          <Icon name="trash-2" size={16}/>
          <strong style={{ fontSize: 14 }}>
            Delete {n} item{n === 1 ? '' : 's'}?
          </strong>
        </div>
        <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10,
                      fontSize: 13, color: 'var(--fg-muted)' }}>
          {recursive && (
            <div style={{ padding: '9px 11px', borderRadius: 'var(--r-md)',
                          background: 'var(--danger-soft, rgba(220,80,60,0.12))',
                          border: '1px solid var(--danger, #d8503c)', color: 'var(--fg)' }}>
              <strong>{folders.length} folder{folders.length === 1 ? '' : 's'}</strong>{' '}
              and everything inside {folders.length === 1 ? 'it' : 'them'} will be permanently
              deleted. This can’t be undone.
            </div>
          )}
          {!recursive && <div>This can’t be undone.</div>}
          <div style={{ maxHeight: 160, overflowY: 'auto', display: 'flex',
                        flexDirection: 'column', gap: 3 }}>
            {entries.map((e) => (
              <div key={e.rel} className="mono"
                   style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 7,
                            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                <Icon name={e.is_dir ? 'folder' : 'file'} size={13}/>{e.rel}
              </div>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 16px',
                      borderTop: '1px solid var(--border-soft)' }}>
          <Button icon="x" disabled={busy} onClick={onCancel}>Cancel</Button>
          <Button variant="primary" icon="trash-2" disabled={busy}
                  onClick={() => onConfirm(entries)}
                  style={{ background: 'var(--danger, #d8503c)', borderColor: 'var(--danger, #d8503c)',
                           color: '#fff' }}>
            {busy ? 'Deleting…' : (recursive ? 'Delete everything' : 'Delete')}
          </Button>
        </div>
      </div>
    </div>
  );
};

/* The Files page — library selector + breadcrumb folder browser + the
 * New(upload) / Download / Delete / Import controls. Documents-library rows
 * keep the exact office/text/drawing/newtab editing routing. */
const FilesPage = () => {
  // Excalidraw machinery lives in drawings.jsx; reuse it for .excalidraw rows.
  const useExcalidrawHook = window.DrawingSuite && window.DrawingSuite.useExcalidraw;
  const lib = useExcalidrawHook ? useExcalidrawHook() : null;

  const [libraries, setLibraries] = React.useState([]);
  const [libLoading, setLibLoading] = React.useState(true);
  const [activeId, setActiveId] = React.useState(null);
  const [view, setView] = React.useState(null);      // browse response
  const [path, setPath] = React.useState('');
  const [browseLoading, setBrowseLoading] = React.useState(false);
  const [selected, setSelected] = React.useState(() => new Set());  // rels
  const [docRel, setDocRel] = React.useState(null);     // markdown editor
  const [sheetRel, setSheetRel] = React.useState(null); // spreadsheet editor
  const [textRel, setTextRel] = React.useState(null);   // text editor
  const [drawing, setDrawing] = React.useState(null);   // excalidraw: {rel_path}
  const [uploading, setUploading] = React.useState(false);
  const [busy, setBusy] = React.useState(null);         // 'deleting'|'downloading'|'importing'|null
  const [dlPct, setDlPct] = React.useState(null);       // 0..1 or null
  const [confirmState, setConfirmState] = React.useState(null);   // {entries}
  const fileInputRef = React.useRef(null);
  const [fire, toastNode] = useToast();

  const activeLib = libraries.find((l) => l.id === activeId) || null;
  const importables = libraries.filter((l) => l.importable);
  const isRemovable = activeLib?.kind === 'removable';
  const isDocuments = activeLib?.id === 'core:documents';

  const loadLibraries = React.useCallback(async () => {
    setLibLoading(true);
    try {
      const r = await apiGet('/api/files/libraries');
      const libs = r.libraries || [];
      setLibraries(libs);
      setActiveId((prev) => (prev && libs.some((l) => l.id === prev)) ? prev
        : (libs.length ? libs[0].id : null));
    } catch (e) {
      fire(`Couldn't load libraries: ${String(e.message || e).slice(0, 80)}`);
    } finally { setLibLoading(false); }
  }, []);

  React.useEffect(() => { loadLibraries(); }, [loadLibraries]);

  const doBrowse = React.useCallback(async (libId, p) => {
    if (!libId) { setView(null); return; }
    setBrowseLoading(true);
    try {
      const r = await apiGet(
        `/api/files/browse?library_id=${encodeURIComponent(libId)}&path=${encodeURIComponent(p || '')}`);
      setView(r);
      setPath(r.path || '');
      setSelected(new Set());
    } catch (e) {
      const msg = String(e.message || e);
      fire(`Couldn't open folder: ${msg.slice(0, 80)}`);
      setView(null);
      // Ejected removable (410) → drop it and rescan.
      if (msg.startsWith('410')) loadLibraries();
    } finally { setBrowseLoading(false); }
  }, [loadLibraries]);

  // Browse the root whenever the active library changes.
  React.useEffect(() => { if (activeId) doBrowse(activeId, ''); }, [activeId]);

  const navigate = (p) => doBrowse(activeId, p);
  const refresh = () => doBrowse(activeId, path);
  const onSelectLibrary = (l) => { if (l.id !== activeId) setActiveId(l.id); };

  const toggleSelect = (rel) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(rel) ? next.delete(rel) : next.add(rel);
    return next;
  });
  const entries = view?.entries || [];
  const allSelected = entries.length > 0 && selected.size === entries.length;
  const toggleSelectAll = () =>
    setSelected(allSelected ? new Set() : new Set(entries.map((e) => e.rel)));
  const clearSelection = () => setSelected(new Set());
  const entriesFor = (rels) => entries.filter((e) => rels.has(e.rel));

  // ── Documents-library editing (homegrown overlays) ──
  const onCloseText = () => { setTextRel(null); refresh(); };
  const onCloseDoc = () => { setDocRel(null); refresh(); };
  const onCloseSheet = () => { setSheetRel(null); refresh(); };

  // "+ New" (Documents library) — create then open the right editor.
  const onCreate = async (kind) => {
    const name = window.prompt(`New ${kind === 'doc' ? 'document' : kind} name:`);
    if (!name) return;
    try {
      const row = await apiPost('/api/documents/create', { name, kind });
      refresh();
      if (row.category === 'doc') setDocRel(row.rel_path);
      else if (row.category === 'sheet') setSheetRel(row.rel_path);
      else if (row.category === 'drawing') setDrawing(row);
      else if (row.category === 'text') setTextRel(row.rel_path);
    } catch (e) {
      fire(`Create failed: ${String(e.message || e).slice(0, 80)}`);
    }
  };

  // ── New (upload into the current directory) ──
  const onUpload = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (!activeLib?.editable) { fire('this library is read-only'); return; }
    setUploading(true);
    fire(`uploading ${files.length} file${files.length === 1 ? '' : 's'}…`);
    try {
      const fd = new FormData();
      fd.append('library_id', activeId);
      fd.append('path', path);
      files.forEach((f) => fd.append('files', f));
      const res = await apiUpload('/api/files/upload', fd);
      const parts = [`uploaded ${res.saved.length} file${res.saved.length === 1 ? '' : 's'}`];
      if (res.skipped?.length) parts.push(`${res.skipped.length} skipped`);
      if (res.reindex_triggered) parts.push('reindexing');
      fire(parts.join(' · '));
      refresh();
    } catch (e) {
      fire(`upload failed: ${String(e.message || e).slice(0, 80)}`);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  // ── Download (file → attachment, folder → streamed zip) ──
  const downloadEntry = async (entry) => {
    const url = `/api/files/download?library_id=${encodeURIComponent(activeId)}`
      + `&path=${encodeURIComponent(entry.rel)}`;
    const fname = entry.is_dir ? `${entry.name}.zip` : entry.name;
    setBusy('downloading'); setDlPct(entry.is_dir ? null : 0);
    try {
      await streamFileDownload(url, fname, (p) => setDlPct(p));
    } catch (e) {
      fire(`download failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setBusy(null); setDlPct(null); }
  };
  const downloadSelected = async () => {
    const rels = Array.from(selected);
    const list = entriesFor(new Set(rels));
    if (!list.length) return;
    for (const entry of list) {
      // Sequential so the progress bar reflects one transfer at a time.
      // eslint-disable-next-line no-await-in-loop
      await downloadEntry(entry);
    }
  };

  // ── Delete (confirm dialog; recursive when any folder is selected) ──
  const requestDelete = (list) => { if (list.length) setConfirmState({ entries: list }); };
  const performDelete = async (list) => {
    const paths = list.map((e) => e.rel);
    const recursive = list.some((e) => e.is_dir);
    setBusy('deleting');
    try {
      const res = await apiPost('/api/files/delete',
        { library_id: activeId, paths, recursive });
      const okN = (res.deleted || []).length;
      const failN = (res.failed || []).length;
      fire(`deleted ${okN} item${okN === 1 ? '' : 's'}${failN ? ` · ${failN} failed` : ''}`);
      setConfirmState(null);
      clearSelection();
      refresh();
    } catch (e) {
      fire(`delete failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setBusy(null); }
  };

  // ── Import (removable source → an importable library) ──
  const doImport = async (entry, targetId) => {
    const target = libraries.find((l) => l.id === targetId);
    setBusy('importing');
    fire(`importing ${entry.name} → ${target?.label || targetId}…`);
    try {
      const res = await apiPost('/api/files/import', {
        source_library_id: activeId,
        source_path: entry.rel,
        target_library_id: targetId,
        target_path: '',
      });
      const parts = [`imported ${(res.copied || []).length} item${(res.copied || []).length === 1 ? '' : 's'}`];
      if (res.skipped?.length) parts.push(`${res.skipped.length} skipped`);
      if (res.reindex_triggered) parts.push('reindexing');
      fire(parts.join(' · '));
    } catch (e) {
      fire(`import failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setBusy(null); }
  };

  const DrawingOverlayCmp = window.DrawingSuite && window.DrawingSuite.DrawingOverlay;
  const nSel = selected.size;
  const canUpload = !!activeLib?.editable;

  return (
    <div className="page">
      <PageHeader title="Files"
        sub="Browse every library — core media, plugins & removable drives"
        actions={<>
          <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }}
                 onChange={(e) => onUpload(e.target.files)}/>
          {isDocuments && <NewMenu disabled={!canUpload} onPick={onCreate}/>}
          <Button variant={isDocuments ? 'secondary' : 'primary'} icon="upload"
                  disabled={uploading || !canUpload}
                  title={canUpload ? 'upload into this folder' : 'this library is read-only'}
                  onClick={() => fileInputRef.current && fileInputRef.current.click()}>
            {uploading ? 'Uploading…' : 'Upload'}
          </Button>
        </>}/>

      <LibrarySelector libraries={libraries} activeId={activeId}
                       onSelect={onSelectLibrary} onRefresh={loadLibraries}/>

      <Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 14px',
                      borderBottom: '1px solid var(--border-soft)' }}>
          {entries.length > 0 && (
            <input type="checkbox" checked={allSelected} title="select all"
                   ref={(el) => { if (el) el.indeterminate = nSel > 0 && !allSelected; }}
                   onChange={toggleSelectAll}
                   style={{ cursor: 'pointer', width: 15, height: 15, flexShrink: 0 }}/>
          )}
          <FilesBreadcrumb lib={activeLib}
                           segments={view?.breadcrumb || []} onNav={navigate}/>
          <span style={{ flex: 1 }}/>
          {nSel > 0 ? (
            <>
              <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{nSel} selected</span>
              <Button icon="download" disabled={busy != null} onClick={downloadSelected}>
                {busy === 'downloading' ? 'Downloading…' : 'Download'}
              </Button>
              {canUpload && (
                <Button icon="trash-2" disabled={busy != null}
                        onClick={() => requestDelete(entriesFor(selected))}>Delete</Button>
              )}
              <Button icon="x" onClick={clearSelection}>Clear</Button>
            </>
          ) : (
            <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
              {entries.length} item{entries.length === 1 ? '' : 's'}
            </span>
          )}
        </div>

        {busy === 'downloading' && (
          <div style={{ height: 3, background: 'var(--sunken)', overflow: 'hidden' }}>
            <div style={{ height: '100%',
                          width: dlPct != null ? `${Math.round(dlPct * 100)}%` : '100%',
                          opacity: dlPct != null ? 1 : 0.5,
                          background: 'var(--brand)', transition: 'width .15s ease' }}/>
          </div>
        )}

        {(libLoading || browseLoading) ? (
          <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Loading…</div>
        ) : !activeLib ? (
          <Empty title="no library selected" sub="pick a library above to browse it"/>
        ) : entries.length === 0 ? (
          <Empty title="empty folder"
                 sub={canUpload ? 'upload files with New, or go back up' : 'nothing here'}/>
        ) : (
          entries.map((entry) => {
            if (!entry.is_dir && isDocuments && view.doc_editing) {
              return (
                <DocRow key={entry.rel} doc={entryToDoc(entry)}
                        selected={selected.has(entry.rel)} onToggleSelect={toggleSelect}
                        onOpenDoc={setDocRel} onOpenSheet={setSheetRel}
                        onOpenText={setTextRel}
                        onOpenDrawing={(d) => setDrawing(d)}
                        onDelete={(d) => requestDelete([entries.find((e) => e.rel === d.rel_path)])}/>
              );
            }
            return (
              <BrowserRow key={entry.rel} entry={entry} libraryId={activeId}
                          selected={selected.has(entry.rel)} onToggleSelect={toggleSelect}
                          editable={view.editable} removable={isRemovable}
                          importables={importables}
                          onOpen={() => navigate(entry.rel)}
                          onDownload={() => downloadEntry(entry)}
                          onDelete={() => requestDelete([entry])}
                          onImport={doImport}/>
            );
          })
        )}
      </Card>

      {docRel && window.DocEditor && (
        <window.DocEditor rel_path={docRel} onClose={onCloseDoc} fire={fire}/>
      )}
      {sheetRel && window.SheetEditor && (
        <window.SheetEditor rel_path={sheetRel} onClose={onCloseSheet} fire={fire}/>
      )}
      {textRel && <TextEditorOverlay rel_path={textRel} onClose={onCloseText} fire={fire}/>}
      {drawing && DrawingOverlayCmp && (
        <DrawingOverlayCmp file={drawing} lib={lib}
                           onClose={() => setDrawing(null)}
                           onSaved={() => refresh()} fire={fire}/>
      )}
      <DeleteConfirmDialog state={confirmState} busy={busy === 'deleting'}
                           onCancel={() => setConfirmState(null)}
                           onConfirm={performDelete}/>
      {toastNode}
    </div>
  );
};

window.FilesPage = FilesPage;
