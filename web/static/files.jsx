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
 *     * GET  /api/files/browse      — one directory level inside a library,
 *                                     a page at a time as the list scrolls
 *                                     (data.js usePagedList); the name
 *                                     filter and the sort are the server's,
 *                                     over the whole folder.
 *     * GET  /api/files/download    — file (attachment) or dir (zip).
 *     * POST /api/files/upload      — upload into the current dir.
 *     * POST /api/files/delete      — delete files / (recursive) folders.
 *     * POST /api/files/move        — move files/folders (drag and drop).
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

/* Slim shared surface (drawings.jsx borrows these two). `fmtBytes` is the
 * shared-kit helper from components.jsx — not redeclared here, because page
 * scripts share one global scope and a duplicate top-level `const` is a
 * SyntaxError that silently turns this tab into the Music fallback. */
window.OfficeSuite = { officeLoadScript, fmtBytes };

/* ---- raw / text / download helpers ------------------------ */
/* Built on API_BASE, the server the switcher selected (FE-6). These URLs
 * carry THAT server's household token (withDeviceToken reads it per
 * server), so an origin-relative path sent one box's token to another:
 * the request went to whichever box served the page, and its access log
 * kept the selected box's credential. Both helpers are absolute when a
 * server is selected; apiFetch and fetch leave an absolute URL alone. */
const docRawUrl = (rel) => `${API_BASE}/api/documents/raw/${encodeURIComponent(rel)}`;
const docTextUrl = (rel) => `${API_BASE}/api/documents/text/${encodeURIComponent(rel)}`;
/* Opened/downloaded by the BROWSER, which can't set a header — the daily
 * read tier takes the household token in the query for exactly this. */
const openDocInNewTab = (rel) => window.open(withDeviceToken(docRawUrl(rel)), '_blank', 'noopener');
const downloadDoc = (rel) => {
  const a = document.createElement('a');
  a.href = withDeviceToken(docRawUrl(rel));
  a.download = rel;        // same-origin → forces a download despite inline C-D
  document.body.appendChild(a);
  a.click();
  a.remove();
};

/* Bulk download → server-built zip. Streams the response so we can report
 * real progress (download-zip sets Content-Length). `onProgress` gets a
 * 0..1 fraction, or null when the length is unknown. */
const downloadDocsZip = async (relPaths, onProgress) => {
  // Saving a document is device tier, but taking a zip of a whole
  // selection in one request is still the admin tier — so this goes
  // through apiFetchRaw: the same headers a plain apiFetch sends, the
  // same sign-in prompt and replay on a 403, and the streamed Response
  // handed back so the progress bar can still move.
  const r = await apiFetchRaw('/api/documents/download-zip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel_paths: relPaths }),
  });
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
const TextEditorOverlay = ({ rel_path, onClose, fire, blockedReason = null }) => {
  const [state, setState] = React.useState({ status: 'loading' });
  const [text, setText] = React.useState('');
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  /* Why this is BOTH a prop and a state. The Files page already knows
   * the answer — /api/files/browse returns it for this device — so the
   * Save button can be off before anybody presses it. But the block can
   * also be applied while the editor is open, and this overlay is
   * reachable from places that never browsed, so a refusal that arrives
   * anyway has to land here too rather than as a password prompt. */
  const [refused, setRefused] = React.useState(null);
  const blocked = refused || blockedReason;

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // no-store: always read the file fresh. Without this a browser can
        // serve a cached (often empty, just-created) copy on reopen, making a
        // successful save look like it was lost.
        const r = await fetch(docTextUrl(rel_path), {
          cache: 'no-store', credentials: 'include', headers: apiHeaders(),
        });
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

  /* Through apiFetch, never a bare fetch. Saving is DEVICE tier, so this
   * PUT carries the household token this browser stored, and a refused
   * write has to be able to ask for whatever it was refused for and then
   * replay — the dashboard holds its bearer in memory only, so a page
   * refresh leaves somebody who can still READ every file. apiFetch is
   * the single place that decides WHICH refusal deserves which answer:
   * the pairing prompt for a device-tier refusal, the sign-in prompt for
   * an admin-tier one, and for the per-device block no prompt at all,
   * because no credential the person can supply would help. A raw fetch
   * skipped all of it: one red PUT, no prompt, the typing lost. */
  const onSave = async () => {
    setSaving(true);
    try {
      await apiFetch(docTextUrl(rel_path), {
        method: 'PUT', body: JSON.stringify({ text }),
      });
      setDirty(false);
      setRefused(null);
      fire && fire('Saved');
    } catch (e) {
      // The block is not a passing error and not a credential problem:
      // it stays on screen beside Save until an admin lifts it.
      const why = deviceBlockReason(e);
      if (why) { setRefused(why); return; }
      const msg = mutationErrorText(e);
      if (msg && fire) fire(msg);
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
      {dirty && <span style={{ fontSize: 11, color: 'var(--warn-ink)' }}>unsaved — click Save</span>}
      <span style={{ flex: 1 }}/>
      {window.PrintFileButton && (
        <window.PrintFileButton libraryId="core:documents" path={rel_path} name={rel_path}
                                title={dirty ? 'prints the last saved version' : 'print'}/>
      )}
      {state.status === 'ready' && (
        <Button variant="primary" icon="save" disabled={saving || !dirty || !!blocked}
                title={blocked || undefined} onClick={onSave}>
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
      <WriteBlockedNotice reason={blocked}>
        {dirty ? ' Your changes are still here — copy them somewhere safe before closing.' : ''}
      </WriteBlockedNotice>
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
                  onOpenDoc, onOpenSheet, onOpenText, onOpenDrawing, onDelete,
                  onDragStartEntry }) => {
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

  // Layout in styles.css (.files-row): the actions take their own line on a phone.
  return (
    <div className="files-row" draggable={!!onDragStartEntry}
         onDragStart={(e) => onDragStartEntry
           && onDragStartEntry(e, { rel: doc.rel_path, name: doc.name + doc.ext,
                                    is_dir: false })}
         style={{ background: selected ? 'var(--brand-soft)' : 'transparent',
                  cursor: onDragStartEntry ? 'grab' : 'default' }}>
      <input type="checkbox" checked={selected} title="select"
             onChange={() => onToggleSelect(doc.rel_path)}
             style={{ cursor: 'pointer', width: 15, height: 15, flexShrink: 0 }}/>
      <Icon name={icon} size={18}/>
      <div className="files-row-main">
        <div className="files-row-name">
          {doc.name}<span style={{ color: 'var(--fg-faint)' }}>{doc.ext}</span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--fg-faint)' }} className="mono">
          {fmtBytes(doc.size)} · {liveRelTime(new Date(doc.modified_at * 1000).toISOString())}
        </div>
      </div>
      <div className="files-row-acts">
        <Button icon={primary.icon} disabled={primary.disabled} title={primary.title}
                onClick={primary.onClick}>{primary.label}</Button>
        {/* Print (printers.jsx): only with a saved printer and a printable
            kind of file; a Word file gets the "save it as PDF" line. */}
        {window.PrintFileButton && (
          <window.PrintFileButton libraryId="core:documents" path={doc.rel_path} name={doc.name + doc.ext}/>
        )}
        {cat !== 'download' && (
          <Button icon="download" title="download"
                  onClick={() => downloadDoc(doc.rel_path)}>Download</Button>
        )}
        <Button icon="trash-2" title="delete" onClick={() => onDelete(doc)}>Delete</Button>
        {/* A chat about this file (chat_documents.jsx): the kinds Chat reads. */}
        {window.ChatDocs && <window.ChatDocs.AskButton relPath={doc.rel_path} name={doc.name + doc.ext}/>}
      </div>
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

/* A button that opens a short menu of choices: "+ New" (Documents) and
 * Upload (Files… / Folder…). One component so both behave the same way:
 * a real menu button (aria-haspopup/expanded), focus moves into the menu
 * when it opens and back to the button when it closes, ↑/↓/Home/End move
 * between items, Esc or a press outside closes it. `pointerdown` rather
 * than `mousedown` for the outside press, so a tap on a phone closes it
 * the same way a click does. */
let filesMenuSeq = 0;
const FilesActionMenu = ({ label, icon, variant = 'secondary', disabled, title, items, onPick,
                           align = 'right' }) => {
  const [open, setOpen] = React.useState(false);
  const wrapRef = React.useRef(null);
  const btnRef = React.useRef(null);
  const itemRefs = React.useRef([]);
  const idRef = React.useRef(null);
  if (idRef.current == null) { filesMenuSeq += 1; idRef.current = `files-menu-${filesMenuSeq}`; }
  const menuId = idRef.current;
  const menuRef = React.useRef(null);
  // Anchored to the button's edge, then nudged back inside the window: on a
  // phone the Upload button sits near the left, and a right-anchored menu
  // hung off the screen there.
  const [shift, setShift] = React.useState(0);
  React.useLayoutEffect(() => {
    if (!open || !menuRef.current) { setShift(0); return; }
    const r = menuRef.current.getBoundingClientRect();
    const vw = document.documentElement.clientWidth || window.innerWidth;
    if (r.left < 8) setShift(8 - r.left);
    else if (r.right > vw - 8) setShift(vw - 8 - r.right);
  }, [open]);
  const close = (refocus) => {
    setOpen(false);
    if (refocus && btnRef.current) btnRef.current.focus();
  };
  React.useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(true); } };
    document.addEventListener('pointerdown', onDoc);
    document.addEventListener('keydown', onKey);
    // The first item takes focus, so the arrow keys work at once.
    const t = setTimeout(() => { if (itemRefs.current[0]) itemRefs.current[0].focus(); }, 0);
    return () => {
      clearTimeout(t);
      document.removeEventListener('pointerdown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const onMenuKey = (e) => {
    const els = itemRefs.current.filter(Boolean);
    const i = els.indexOf(document.activeElement);
    let next = null;
    if (e.key === 'ArrowDown') next = els[(i + 1) % els.length];
    else if (e.key === 'ArrowUp') next = els[(i - 1 + els.length) % els.length];
    else if (e.key === 'Home') next = els[0];
    else if (e.key === 'End') next = els[els.length - 1];
    else if (e.key === 'Tab') { setOpen(false); return; }
    if (next) { e.preventDefault(); next.focus(); }
  };
  const onBtnKey = (e) => {
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !disabled) { e.preventDefault(); setOpen(true); }
  };
  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      {/* A plain <button> with Button's classes: Button can't carry a ref,
          and the menu hands focus back to this on close. */}
      <button type="button" className={`btn btn-${variant}`} disabled={disabled} title={title}
              ref={btnRef} aria-haspopup="menu" aria-expanded={open}
              aria-controls={open ? menuId : undefined}
              onKeyDown={onBtnKey}
              onClick={() => setOpen((o) => !o)}>
        {icon && <Icon name={icon} size={14}/>}
        {label}
        <Icon name="chevron-down" size={13}/>
      </button>
      {open && (
        <div id={menuId} ref={menuRef} role="menu"
             aria-label={typeof label === 'string' ? label : undefined}
             onKeyDown={onMenuKey}
             style={{ position: 'absolute', [align]: 0, top: 'calc(100% + 6px)', zIndex: 50,
                      transform: shift ? `translateX(${shift}px)` : undefined,
                      minWidth: 220, maxWidth: 'calc(100vw - 32px)', background: 'var(--card)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--r-md)', boxShadow: 'var(--shadow-md), var(--inner-highlight)',
                      padding: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {items.map((it, i) => (
            <button key={it.key} role="menuitem" type="button" tabIndex={-1}
                    ref={(el) => { itemRefs.current[i] = el; }}
                    className="files-menu-item"
                    onClick={() => { close(true); onPick(it.key); }}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 10px',
                             minHeight: 40, background: 'transparent', border: 'none',
                             borderRadius: 'var(--r-sm)', cursor: 'pointer', textAlign: 'left',
                             font: 'inherit', color: 'var(--fg)' }}>
              <Icon name={it.icon} size={16}/>
              <span style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{it.label}</span>
                {it.hint && <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{it.hint}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const NewMenu = ({ disabled, onPick }) => (
  <FilesActionMenu label="New" icon="plus" variant="primary" disabled={disabled}
                   items={NEW_KINDS.map((it) => ({ ...it, key: it.kind }))} onPick={onPick}/>
);

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
/* ---- Drag and drop -------------------------------------------------
 * Moving things around is a drag: pick up a row (or a whole selection) and
 * drop it on a folder, a breadcrumb crumb, or another library's chip.
 *
 * Internal drags are tagged with a private MIME type. Everything droppable
 * checks for it before calling preventDefault, so a file dragged in from the
 * desktop is NOT mistaken for a move — that case is handled once, at the
 * browser card, as an upload into the current folder. Without that split, an
 * OS file drop would fall through to the browser's default and navigate the
 * dashboard away to the dropped file.
 */
const FILES_DRAG_MIME = 'application/x-domovoi-files';

const isInternalDrag = (e) => {
  const types = e.dataTransfer && e.dataTransfer.types;
  if (!types) return false;
  // DataTransfer.types is a DOMStringList in some browsers, not an Array.
  return Array.prototype.indexOf.call(types, FILES_DRAG_MIME) !== -1;
};

const hasOsFiles = (e) => {
  const types = e.dataTransfer && e.dataTransfer.types;
  if (!types) return false;
  return Array.prototype.indexOf.call(types, 'Files') !== -1;
};

/* Visual affordance for anything accepting a move. `active` comes from the
 * dragenter/leave pair, which is counted rather than toggled — moving over a
 * child element fires leave on the parent and would otherwise flicker. */
const useDropTarget = (accepts, onDropMove) => {
  const [active, setActive] = React.useState(false);
  const depth = React.useRef(0);
  const reset = () => { depth.current = 0; setActive(false); };
  return {
    active,
    props: {
      onDragEnter: (e) => {
        if (!accepts || !isInternalDrag(e)) return;
        depth.current += 1;
        setActive(true);
      },
      onDragOver: (e) => {
        if (!accepts || !isInternalDrag(e)) return;
        e.preventDefault();                       // "yes, you may drop here"
        e.dataTransfer.dropEffect = 'move';
      },
      onDragLeave: (e) => {
        if (!accepts || !isInternalDrag(e)) return;
        depth.current -= 1;
        if (depth.current <= 0) reset();
      },
      onDrop: (e) => {
        if (!accepts || !isInternalDrag(e)) return;
        e.preventDefault();
        e.stopPropagation();
        reset();
        onDropMove();
      },
    },
  };
};

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
  const r = await fetch(full, { credentials: 'include', headers: apiHeaders() });
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
/* A library chip is also a drop target: dropping on it moves into that
 * library's ROOT. Only editable libraries accept — a move deletes from the
 * source, so a read-only root can never be one, and a removable drive stays
 * copy-only through Import. */
const LibChip = ({ lib, active, onClick, onDropMove }) => {
  const accepts = !!lib.editable && !!onDropMove;
  const drop = useDropTarget(accepts, () => onDropMove(lib));
  const highlight = drop.active ? 'var(--brand)' : (active ? 'var(--brand)' : 'var(--border)');
  return (
    <button onClick={onClick} {...drop.props}
            title={lib.owner ? `${lib.kind} · ${lib.owner}` : lib.kind}
            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px',
                     border: `1px solid ${highlight}`,
                     background: (drop.active || active) ? 'var(--brand-soft)' : 'var(--card)',
                     color: 'var(--fg)', borderRadius: 'var(--r-md)', cursor: 'pointer',
                     font: 'inherit', fontSize: 13,
                     outline: drop.active ? '2px solid var(--brand)' : 'none',
                     outlineOffset: 1 }}>
      <Icon name={lib.icon || 'folder'} size={16}/>
      <span style={{ whiteSpace: 'nowrap' }}>{lib.label}</span>
      <Icon name={lib.kind_icon || 'folder'} size={12}/>
    </button>
  );
};

const LibrarySelector = ({ libraries, activeId, onSelect, onRefresh, onDropMove }) => (
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
                <LibChip key={l.id} lib={l} active={l.id === activeId}
                         onClick={() => onSelect(l)} onDropMove={onDropMove}/>
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
/* One crumb. Also a drop target, which is how you move something UP a level
 * — there's no ".." row, so without this the only way out of a folder would
 * be to cut-and-paste, which this page doesn't have. */
const Crumb = ({ label, path, bold, disabled, onNav, onDropMove }) => {
  const drop = useDropTarget(!!onDropMove, () => onDropMove(path));
  return (
    <button onClick={() => onNav(path)} disabled={disabled} {...drop.props}
            title={onDropMove ? `drop here to move into ${label}` : undefined}
            style={{ border: drop.active ? '1px solid var(--brand)' : '1px solid transparent',
                     borderRadius: 'var(--r-sm)', padding: drop.active ? '1px 5px' : '1px 5px',
                     background: drop.active ? 'var(--brand-soft)' : 'transparent',
                     cursor: disabled ? 'default' : 'pointer',
                     color: bold ? 'var(--fg)' : 'var(--brand-ink)', font: 'inherit',
                     fontWeight: bold ? 600 : 400 }}>
      {label}
    </button>
  );
};

const FilesBreadcrumb = ({ lib, segments, onNav, onDropMove }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap',
                fontSize: 12, color: 'var(--fg-muted)' }}>
    <Crumb label={lib ? lib.label : '—'} path="" bold={!segments.length}
           onNav={onNav} onDropMove={onDropMove}/>
    {segments.map((seg, i) => {
      const p = segments.slice(0, i + 1).join('/');
      const last = i === segments.length - 1;
      return (
        <React.Fragment key={p}>
          <span style={{ color: 'var(--fg-faint)' }}>/</span>
          {/* The last crumb IS the folder on screen — dropping into it is a
              no-op, so it isn't a target. */}
          <Crumb label={seg} path={p} bold={last} disabled={last}
                 onNav={onNav} onDropMove={last ? null : onDropMove}/>
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
                      importables, onOpen, onDownload, onDelete, onImport,
                      onDragStartEntry, onDropMove }) => {
  const isDir = entry.is_dir;
  // A folder accepts a drop; a file doesn't (there's nothing to drop it into).
  const drop = useDropTarget(isDir && !!onDropMove, () => onDropMove(entry));
  const iso = entry.mtime ? new Date(entry.mtime * 1000).toISOString() : null;
  // Images in ANY library open inline in a new tab via the generic
  // library-image serve (the Files tab owns image browsing).
  const openImage = () => window.open(withDeviceToken(
    `${API_BASE}/api/images/raw?library_id=${encodeURIComponent(libraryId)}`
    + `&path=${encodeURIComponent(entry.rel)}`), '_blank', 'noopener');
  // Layout in styles.css (.files-row): the actions take their own line on a phone.
  return (
    <div className="files-row" draggable={!!onDragStartEntry}
         onDragStart={(e) => onDragStartEntry && onDragStartEntry(e, entry)}
         {...drop.props}
         style={{ background: drop.active ? 'var(--brand-soft)'
                    : (selected ? 'var(--brand-soft)' : 'transparent'),
                  outline: drop.active ? '2px solid var(--brand)' : 'none',
                  outlineOffset: -2,
                  cursor: onDragStartEntry ? 'grab' : 'default' }}>
      <input type="checkbox" checked={selected} title="select"
             onChange={() => onToggleSelect(entry.rel)}
             style={{ cursor: 'pointer', width: 15, height: 15, flexShrink: 0 }}/>
      <Icon name={entryIcon(entry.kind)} size={18}/>
      <div className="files-row-main" style={{ cursor: isDir ? 'pointer' : 'default' }}
           onClick={isDir ? onOpen : undefined}>
        <div className="files-row-name">
          {entry.name}
        </div>
        <div style={{ fontSize: 11, color: 'var(--fg-faint)' }} className="mono">
          {isDir ? 'folder' : fmtBytes(entry.size)}{iso ? ` · ${liveRelTime(iso)}` : ''}
        </div>
      </div>
      <div className="files-row-acts">
        {isDir && (
          <Button icon="chevron-right" title="open folder" onClick={onOpen}>Open</Button>
        )}
        {!isDir && entry.kind === 'image' && (
          <Button icon="external-link" title="open in a new tab" onClick={openImage}>Open</Button>
        )}
        {!isDir && window.PrintFileButton && (
          <window.PrintFileButton libraryId={libraryId} path={entry.rel} name={entry.name}/>
        )}
        <Button icon="download" title="download" onClick={onDownload}>Download</Button>
        {removable && (
          <ImportMenu importables={importables} onPick={(tid) => onImport(entry, tid)}/>
        )}
        {editable && (
          <Button icon="trash-2" title="delete" onClick={onDelete}>Delete</Button>
        )}
        {!isDir && libraryId === 'core:documents' && window.ChatDocs && (
          <window.ChatDocs.AskButton relPath={entry.rel} name={entry.name}/>
        )}
      </div>
    </div>
  );
};

/* Files' delete confirmation: the shared DeleteConfirmDialog
 * (components.jsx) with the entry list in the body and a loud warning
 * when a folder — a recursive delete — is among them. */
const FilesDeleteConfirm = ({ state, busy, onCancel, onConfirm }) => {
  if (!state) return null;
  const { entries } = state;
  const folders = entries.filter((e) => e.is_dir);
  const recursive = folders.length > 0;
  const n = entries.length;
  return (
    <DeleteConfirmDialog title={`Delete ${n} item${n === 1 ? '' : 's'}?`} busy={busy}
                         confirmLabel={recursive ? 'Delete everything' : 'Delete'}
                         onCancel={onCancel} onConfirm={() => onConfirm(entries)}>
      {recursive ? (
        <div style={{ padding: '9px 11px', borderRadius: 'var(--r-md)',
                      background: 'var(--danger-soft)',
                      border: '1px solid var(--danger)', color: 'var(--fg)' }}>
          <strong>{folders.length} folder{folders.length === 1 ? '' : 's'}</strong>{' '}
          and everything inside {folders.length === 1 ? 'it' : 'them'} will be permanently
          deleted. This can’t be undone.
        </div>
      ) : <div>This can’t be undone.</div>}
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
    </DeleteConfirmDialog>
  );
};

/* ---- Uploading files and folders ------------------------------------
 * Upload → Files… / Folder…, or anything dropped on the page (files and
 * folders mixed), becomes ONE batch: a list of { file, rel } where `rel`
 * is the file's place inside what was picked ("Book Title/01.mp3"; a
 * plain file is just its name). The batch is sent one file per request to
 * POST /api/files/upload-stream, FILES_UPLOAD_PARALLEL at a time, the body
 * being the file itself, so the server streams it to disk (files.py) and
 * a multi-GB audiobook never sits in anyone's memory.
 *
 * Before anything is sent, /api/files/upload-check says which paths are
 * already there; when any are, the page asks ONCE for the whole batch:
 * keep both, replace, or skip. The batch ends with /api/files/upload-done,
 * which reindexes Music or Audiobooks once rather than once per file.
 *
 * What a dropped FOLDER looks like to a page: `dataTransfer.files` lists
 * it as a zero-byte, type-less File that cannot be read (sending it fails
 * the whole request), so folders are walked through
 * `DataTransferItem.webkitGetAsEntry()` instead. Those entries are only
 * valid during the drop event itself: collect them synchronously there,
 * walk them afterwards. `readEntries` answers in batches (100 at a time in
 * Chrome), so a reader is read until it answers an empty batch. */
const FILES_UPLOAD_JUNK = new Set(['desktop.ini', 'thumbs.db', '.ds_store']);
// files.py MAX_UPLOAD_FILE_BYTES — the server says the same thing if a
// bigger one gets there anyway.
const FILES_UPLOAD_MAX_BYTES = 8 * 1024 * 1024 * 1024;
const FILES_UPLOAD_PARALLEL = 3;
const FILES_UPLOAD_CHOICES = [
  { key: 'files',  label: 'Files…',  icon: 'file-up',   hint: 'one or more files' },
  { key: 'folder', label: 'Folder…', icon: 'folder-up', hint: 'a whole folder, with its subfolders' },
];
// Under files.py _MAX_CHECK_PATHS (5000), and small enough that long paths
// stay inside the 1 MiB JSON body every ordinary route takes.
const FILES_UPLOAD_CHECK_CHUNK = 2000;

const filesUploadBaseName = (rel) => String(rel).split('/').pop();
const isFilesUploadJunk = (rel) => FILES_UPLOAD_JUNK.has(filesUploadBaseName(rel).toLowerCase());
const fmtUploadGB = (n) => `${(n / 1024 ** 3).toFixed(1)} GB`;

const readAllDirEntries = (reader) => new Promise((resolve, reject) => {
  const out = [];
  const step = () => reader.readEntries((batch) => {
    if (!batch.length) { resolve(out); return; }
    for (const e of batch) out.push(e);
    step();
  }, reject);
  step();
});

const fileOfEntry = (entry) => new Promise((resolve, reject) => entry.file(resolve, reject));

/* FileSystemEntry[] → { items: [{file, rel}], unreadable: [reason] }. */
const walkDroppedEntries = async (entries) => {
  const items = [];
  const unreadable = [];
  const visit = async (entry, prefix) => {
    const rel = prefix + entry.name;
    if (entry.isFile) {
      try { items.push({ file: await fileOfEntry(entry), rel }); }
      catch { unreadable.push(`${rel}: couldn't be read from this computer`); }
    } else if (entry.isDirectory) {
      let kids;
      try { kids = await readAllDirEntries(entry.createReader()); }
      catch { unreadable.push(`${rel}/: the folder couldn't be opened`); return; }
      for (const kid of kids) {
        // eslint-disable-next-line no-await-in-loop
        await visit(kid, `${rel}/`);
      }
    }
  };
  for (const e of entries) {
    // eslint-disable-next-line no-await-in-loop
    await visit(e, '');
  }
  return { items, unreadable };
};

/* SYNCHRONOUS part of a drop: grab the entries (or, in a browser without
 * the entries API, the plain files) before the event ends. Returns a
 * promise of the batch. */
const collectDroppedUpload = (dt) => {
  const its = dt && dt.items ? Array.from(dt.items).filter((it) => it.kind === 'file') : [];
  const entries = its.map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry()
    : (it.getAsEntry ? it.getAsEntry() : null))).filter(Boolean);
  if (entries.length) return walkDroppedEntries(entries);
  // No entries API: a folder is the zero-byte, type-less, extensionless
  // File described above. Leave it out and say so, rather than letting it
  // fail every other file in the drop.
  const items = [];
  const unreadable = [];
  for (const f of Array.from((dt && dt.files) || [])) {
    if (f.size === 0 && !f.type && !/\.[^.]+$/.test(f.name)) {
      unreadable.push(`${f.name}: looks like a folder — this browser can't read dropped folders; use Upload → Folder…`);
    } else items.push({ file: f, rel: f.name });
  }
  return Promise.resolve({ items, unreadable });
};

/* An <input type=file> selection → the batch. With `webkitdirectory` each
 * File carries `webkitRelativePath` ("Book Title/01.mp3"). */
const pickedUploadItems = (fileList) => Array.from(fileList || []).map((f) => (
  { file: f, rel: f.webkitRelativePath || f.name }));

const uploadErrorDetail = (xhr) => {
  let body = null;
  try { body = JSON.parse(xhr.responseText); } catch { /* plain text */ }
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object' && d.detail) return String(d.detail);
  const txt = String(xhr.responseText || '').trim();
  return txt ? txt.slice(0, 160) : `${xhr.status} ${xhr.statusText || 'error'}`;
};

/* One file, streamed. XHR rather than fetch: only XHR reports upload
 * progress. Same headers apiFetch sends (credential, device id, the
 * preflight-forcing X-Requested-With). */
const sendUploadFile = ({ libraryId, path, item, onConflict, onProgress }) => {
  const xhr = new XMLHttpRequest();
  const q = new URLSearchParams({
    library_id: libraryId, path: path || '', rel_path: item.rel,
    device_id: DeviceIdentity.id(), on_conflict: onConflict, reindex: 'false',
  });
  const promise = new Promise((resolve, reject) => {
    xhr.open('POST', `${API_BASE}/api/files/upload-stream?${q.toString()}`);
    xhr.withCredentials = true;
    const h = apiHeaders();
    Object.keys(h).forEach((k) => { if (h[k] != null) xhr.setRequestHeader(k, h[k]); });
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({}); }
        return;
      }
      const err = new Error(uploadErrorDetail(xhr));
      err.status = xhr.status;
      reject(err);
    };
    xhr.onerror = () => reject(Object.assign(
      new Error('the connection to the server dropped'), { network: true }));
    xhr.onabort = () => reject(Object.assign(new Error('cancelled'), { aborted: true }));
    xhr.send(item.file);
  });
  return { promise, abort: () => { try { xhr.abort(); } catch { /* done already */ } } };
};

/* The queue. One batch at a time; `snap` is what the panel draws. */
const useFilesUploadQueue = ({ fire, onFinished }) => {
  const [snap, setSnap] = React.useState(null);
  const [ask, setAsk] = React.useState(null);      // { existing: [rel], total, resolve }
  const q = React.useRef(null);
  const timer = React.useRef(null);

  const publish = (now) => {
    const cur = q.current;
    if (!cur) { setSnap(null); return; }
    const draw = () => {
      timer.current = null;
      const c = q.current;
      if (!c) return;
      let bytesDone = c.bytesFinished;
      c.active.forEach((a) => { bytesDone += a.loaded; });
      setSnap({
        phase: c.phase, paused: c.paused, label: c.label,
        total: c.items.length, finished: c.finished, saved: c.saved.length,
        skipped: c.skipped.slice(), failed: c.failed.slice(), junk: c.junk.slice(),
        notSent: c.notSent, replaced: c.replaced,
        bytesTotal: c.bytesTotal, bytesDone,
        active: Array.from(c.active.values()).map((a) => ({ rel: a.rel, loaded: a.loaded, size: a.size })),
        reindex: c.reindex, fatal: c.fatal, cancelled: c.cancelled,
      });
    };
    if (now) { if (timer.current) clearTimeout(timer.current); draw(); return; }
    if (!timer.current) timer.current = setTimeout(draw, 150);
  };

  const running = !!(snap && (snap.phase === 'checking' || snap.phase === 'asking' || snap.phase === 'running'));

  // Leaving the page mid-upload loses what hasn't been sent: say so.
  React.useEffect(() => {
    if (!running) return undefined;
    const onBefore = (e) => { e.preventDefault(); e.returnValue = ''; return ''; };
    window.addEventListener('beforeunload', onBefore);
    return () => window.removeEventListener('beforeunload', onBefore);
  }, [running]);

  const finish = async (c) => {
    c.phase = 'finishing';
    c.notSent = c.items.length - c.finished;
    if (c.saved.length) {
      try {
        c.reindex = await apiPost('/api/files/upload-done',
          { library_id: c.libraryId, device_id: DeviceIdentity.id() });
      } catch (e) { c.reindex = { error: String((e && e.message) || e) }; }
    }
    c.phase = 'done';
    publish(true);
    const parts = [`${c.saved.length} file${c.saved.length === 1 ? '' : 's'} uploaded`];
    if (c.skipped.length) parts.push(`${c.skipped.length} skipped`);
    if (c.failed.length) parts.push(`${c.failed.length} failed`);
    if (c.cancelled && c.notSent) parts.push(`${c.notSent} not sent (cancelled)`);
    fire(parts.join(' · '));
    if (onFinished) onFinished(c);
  };

  const worker = async (c) => {
    for (;;) {
      if (c.cancelled) return;
      if (c.paused) {
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => { c.wake.push(r); });
        continue;
      }
      const i = c.next++;
      if (i >= c.items.length) return;
      const item = c.items[i];
      if (item.skip) {
        c.skipped.push(`${item.rel}: ${item.skip}`);
        c.finished += 1;
        publish();
        continue;
      }
      const a = { rel: item.rel, loaded: 0, size: item.file.size };
      const send = sendUploadFile({ libraryId: c.libraryId, path: c.path, item,
                                    onConflict: c.onConflict,
                                    onProgress: (n) => { a.loaded = n; publish(); } });
      a.abort = send.abort;
      c.active.set(i, a);
      publish();
      try {
        // eslint-disable-next-line no-await-in-loop
        const res = await send.promise;
        if (res && res.saved) { c.saved.push(res.saved); if (res.replaced) c.replaced += 1; }
        else c.skipped.push(`${item.rel}: ${(res && res.skipped) || 'not saved'}`);
        c.finished += 1;
      } catch (e) {
        if (e && e.aborted) { c.active.delete(i); publish(); return; }
        c.finished += 1;
        c.failed.push(`${item.rel}: ${e.message || e}`);
        // Refused for who we are (signed out, unpaired, blocked): every
        // other file would be refused the same way. Stop, and say why.
        if (e && (e.status === 401 || e.status === 403)) {
          c.fatal = e.message || 'the server refused the upload';
          c.cancelled = true;
          c.active.forEach((o, k) => { if (k !== i && o.abort) o.abort(); });
        }
      }
      c.active.delete(i);
      c.bytesFinished += item.file.size;
      publish();
    }
  };

  const start = async ({ items, unreadable = [], libraryId, path, label }) => {
    if (q.current && q.current.phase !== 'done') {
      fire('an upload is already running — wait for it, or cancel it first');
      return;
    }
    const junk = items.filter((it) => isFilesUploadJunk(it.rel)).map((it) => it.rel);
    const keep = items.filter((it) => !isFilesUploadJunk(it.rel));
    const c = {
      phase: 'checking', paused: false, cancelled: false, label, libraryId, path,
      items: keep, next: 0, finished: 0, saved: [], skipped: unreadable.slice(), failed: [],
      junk, notSent: 0, replaced: 0, bytesTotal: keep.reduce((s, it) => s + it.file.size, 0),
      bytesFinished: 0, active: new Map(), wake: [], reindex: null, fatal: null,
      onConflict: 'keep_both',
    };
    q.current = c;
    publish(true);
    if (!keep.length) { await finish(c); return; }
    keep.forEach((it) => {
      if (it.file.size > FILES_UPLOAD_MAX_BYTES) {
        it.skip = `${fmtUploadGB(it.file.size)} — one file can be at most ${fmtUploadGB(FILES_UPLOAD_MAX_BYTES)}`;
      }
    });
    // Which are already there, and which names the server won't take.
    const existing = [];
    try {
      for (let at = 0; at < keep.length; at += FILES_UPLOAD_CHECK_CHUNK) {
        const chunk = keep.slice(at, at + FILES_UPLOAD_CHECK_CHUNK);
        // eslint-disable-next-line no-await-in-loop
        const r = await apiPost('/api/files/upload-check', {
          library_id: libraryId, path: path || '', device_id: DeviceIdentity.id(),
          rel_paths: chunk.map((it) => it.rel),
        });
        const refused = r.refused || {};
        chunk.forEach((it) => { if (refused[it.rel] && !it.skip) it.skip = refused[it.rel]; });
        (r.existing || []).forEach((rel) => existing.push(rel));
      }
    } catch (e) {
      const msg = mutationErrorText(e, 'upload', { kept: false });
      if (msg) fire(msg);
      q.current = null; publish(true);
      return;
    }
    if (c.cancelled) { await finish(c); return; }
    if (existing.length) {
      c.phase = 'asking'; publish(true);
      const choice = await new Promise((resolve) => setAsk({ existing, total: keep.length, resolve }));
      setAsk(null);
      if (choice === 'cancel') { q.current = null; publish(true); return; }
      c.onConflict = choice;
      if (choice === 'skip') {
        const ex = new Set(existing);
        keep.forEach((it) => { if (ex.has(it.rel) && !it.skip) it.skip = 'already there'; });
      }
    }
    // Progress is over what will actually be sent.
    c.bytesTotal = keep.reduce((sum, it) => sum + (it.skip ? 0 : it.file.size), 0);
    c.phase = 'running'; publish(true);
    const n = Math.min(FILES_UPLOAD_PARALLEL, keep.length);
    await Promise.all(Array.from({ length: n }, () => worker(c)));
    await finish(c);
  };

  const pause = () => { const c = q.current; if (c && c.phase === 'running') { c.paused = true; publish(true); } };
  const resume = () => {
    const c = q.current;
    if (!c) return;
    c.paused = false;
    const w = c.wake; c.wake = [];
    w.forEach((r) => r());
    publish(true);
  };
  const cancel = () => {
    const c = q.current;
    if (!c || c.phase === 'done') return;
    c.cancelled = true;
    c.active.forEach((a) => { if (a.abort) a.abort(); });
    resume();
  };
  const dismiss = () => {
    if (q.current && q.current.phase !== 'done') return;
    q.current = null; publish(true);
  };
  return { snap, ask, running, start, pause, resume, cancel, dismiss };
};

/* What the batch's reindex did, in words — or what the person must do. */
const uploadReindexNote = (snap) => {
  const r = snap.reindex;
  if (!r || !snap.saved) return null;
  if (r.error) return `The library wasn't told about the new files (${r.error}). Rescan it to list them.`;
  if (r.reindex_kind === 'audiobooks') {
    return r.reindex_triggered ? 'Audiobooks has picked them up.'
      : 'Audiobooks didn’t reindex — press reindex on the Audiobooks page to list them.';
  }
  if (r.reindex_kind === 'music') {
    return r.reindex_triggered ? 'Music is reindexing — they appear there in a moment.'
      : 'Music hasn’t indexed them yet — an admin can press Rescan library on the Music page.';
  }
  return null;
};

const UploadBar = ({ frac, thin }) => (
  <div style={{ height: thin ? 3 : 6, background: 'var(--sunken)', borderRadius: 3, overflow: 'hidden' }}>
    <div style={{ height: '100%', width: `${Math.max(0, Math.min(1, frac)) * 100}%`,
                  background: 'var(--brand)', transition: 'width .15s ease' }}/>
  </div>
);

/* The batch on screen: overall and per-file progress while it runs, the
 * summary once it's done. */
const FilesUploadPanel = ({ up }) => {
  const s = up.snap;
  if (!s || s.phase === 'asking') return null;
  const done = s.phase === 'done';
  const frac = s.bytesTotal ? s.bytesDone / s.bytesTotal : (s.total ? s.finished / s.total : 1);
  const problems = [...s.failed, ...s.skipped];
  const note = done ? uploadReindexNote(s) : null;
  let headline;
  if (s.phase === 'checking') headline = `Getting ${s.total} file${s.total === 1 ? '' : 's'} ready…`;
  else if (!done) {
    headline = `${s.paused ? 'Paused' : 'Uploading'} — ${s.finished} of ${s.total}`
      + ` file${s.total === 1 ? '' : 's'} done · ${fmtBytes(s.bytesDone)} of ${fmtBytes(s.bytesTotal)}`;
  } else {
    headline = `${s.saved} file${s.saved === 1 ? '' : 's'} uploaded`
      + (s.replaced ? ` (${s.replaced} replaced)` : '')
      + (s.skipped.length ? `, ${s.skipped.length} skipped` : '')
      + (s.failed.length ? `, ${s.failed.length} failed` : '')
      + (s.notSent ? `, ${s.notSent} not sent` : '')
      + (s.cancelled && !s.fatal ? ' — cancelled' : '');
  }
  return (
    <div role="region" aria-label="Upload progress"
         style={{ marginBottom: 12, padding: '12px 14px', borderRadius: 'var(--r-md)',
                  border: '1px solid var(--border)', background: 'var(--card)',
                  display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Icon name={done ? (s.failed.length ? 'alert-triangle' : 'check-circle') : 'upload'} size={15}/>
        <strong role="status" aria-live="polite" style={{ fontSize: 13, flex: '1 1 220px' }}>{headline}</strong>
        <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>into {s.label}</span>
        {!done && s.phase === 'running' && (s.paused
          ? <Button icon="play" onClick={up.resume}>Resume</Button>
          : <Button icon="pause" onClick={up.pause}
                    title="finish the files on their way, then wait">Pause</Button>)}
        {!done && <Button icon="x" onClick={up.cancel}>Cancel</Button>}
        {done && <Button icon="x" onClick={up.dismiss}>Close</Button>}
      </div>
      {!done && <UploadBar frac={frac}/>}
      {!done && s.active.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {s.active.map((a) => (
            <div key={a.rel} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto',
                                      gap: '2px 10px', fontSize: 12 }}>
              <span className="mono" title={a.rel}
                    style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.rel}</span>
              <span style={{ color: 'var(--fg-muted)' }}>
                {a.size ? `${Math.round((a.loaded / a.size) * 100)}%` : '…'} of {fmtBytes(a.size)}
              </span>
              <div style={{ gridColumn: '1 / -1' }}><UploadBar frac={a.size ? a.loaded / a.size : 0} thin/></div>
            </div>
          ))}
        </div>
      )}
      {s.paused && !done && (
        <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
          Files already on their way finish; nothing new starts until you resume.
        </div>
      )}
      {done && s.fatal && (
        <div style={{ fontSize: 12, color: 'var(--danger)' }}>Stopped: {s.fatal}</div>
      )}
      {done && note && <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{note}</div>}
      {done && s.junk.length > 0 && (
        <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>
          Left out {s.junk.length} system file{s.junk.length === 1 ? '' : 's'}
          {' '}({Array.from(new Set(s.junk.map(filesUploadBaseName))).join(', ')}).
        </div>
      )}
      {done && problems.length > 0 && (
        <details open={problems.length <= 5}>
          <summary style={{ fontSize: 12, cursor: 'pointer' }}>
            {problems.length} not uploaded — why
          </summary>
          <div style={{ maxHeight: 180, overflowY: 'auto', marginTop: 6, display: 'flex',
                        flexDirection: 'column', gap: 3 }}>
            {problems.map((p, i) => (
              <div key={i} className="mono" style={{ fontSize: 11.5, color: 'var(--fg-muted)',
                                                     overflowWrap: 'anywhere' }}>{p}</div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
};

/* Asked once per batch when some of it is already there. */
const FilesConflictDialog = ({ ask }) => {
  const firstRef = React.useRef(null);
  React.useEffect(() => { if (firstRef.current) firstRef.current.focus(); }, [!!ask]);
  React.useEffect(() => {
    if (!ask) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') ask.resolve('cancel'); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [ask]);
  if (!ask) return null;
  const n = ask.existing.length;
  const all = n === ask.total;
  const title = all
    ? `${n === 1 ? 'This file is' : `All ${n} files are`} already here`
    : `${n} of ${ask.total} files are already here`;
  const choice = (key, icon, label, sub, primary) => (
    <button type="button" ref={primary ? firstRef : undefined}
            className={`btn ${primary ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => ask.resolve(key)}
            style={{ justifyContent: 'flex-start', height: 'auto', padding: '9px 12px', textAlign: 'left' }}>
      <Icon name={icon} size={15}/>
      <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 11.5, opacity: 0.8, fontWeight: 400 }}>{sub}</span>
      </span>
    </button>
  );
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 90, background: 'rgba(0,0,0,0.45)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
         onClick={() => ask.resolve('cancel')}>
      <div role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
           style={{ width: 'min(460px, 100%)', background: 'var(--card)',
                    border: '1px solid var(--border)', borderRadius: 'var(--r-lg)',
                    boxShadow: 'var(--shadow-md), var(--inner-highlight)', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '13px 16px',
                      borderBottom: '1px solid var(--border-soft)' }}>
          <Icon name="copy" size={16}/>
          <strong style={{ fontSize: 14 }}>{title}</strong>
        </div>
        <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8,
                      fontSize: 13, color: 'var(--fg-muted)' }}>
          <div style={{ maxHeight: 110, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
            {ask.existing.slice(0, 50).map((rel) => (
              <div key={rel} className="mono" style={{ fontSize: 11.5, overflowWrap: 'anywhere' }}>{rel}</div>
            ))}
            {n > 50 && <div style={{ fontSize: 11.5 }}>…and {n - 50} more</div>}
          </div>
          <div>What should happen to {n === 1 ? 'it' : 'them'}? This applies to the whole upload.</div>
          {choice('keep_both', 'copy', 'Keep both', 'the new one is saved as “name (1)”', true)}
          {choice('replace', 'refresh-cw', 'Replace', 'the file here is overwritten')}
          {choice('skip', 'skip-forward', 'Skip', 'upload only what isn’t here yet')}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '10px 16px',
                      borderTop: '1px solid var(--border-soft)' }}>
          <Button icon="x" onClick={() => ask.resolve('cancel')}>Cancel upload</Button>
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
  // The folder belongs to a library: switching libraries is the root of
  // the new one at once (no request for the old path inside the new one).
  const [loc, setLoc] = React.useState({ libId: null, path: '' });
  const path = loc.libId === activeId ? loc.path : '';
  const [filter, setFilter] = React.useState('');
  const debFilter = useDebouncedValue(filter, 250);
  const [sort, setSort] = React.useState('name');
  const [selected, setSelected] = React.useState(() => new Set());  // rels
  const [docRel, setDocRel] = React.useState(null);     // markdown editor
  const [sheetRel, setSheetRel] = React.useState(null); // spreadsheet editor
  const [textRel, setTextRel] = React.useState(null);   // text editor
  const [drawing, setDrawing] = React.useState(null);   // excalidraw: {rel_path}
  const [busy, setBusy] = React.useState(null);         // 'deleting'|'downloading'|'importing'|null
  const [dlPct, setDlPct] = React.useState(null);       // 0..1 or null
  const [confirmState, setConfirmState] = React.useState(null);   // {entries}
  const fileInputRef = React.useRef(null);
  const folderInputRef = React.useRef(null);
  // What's currently being dragged: {libraryId, paths, label}. A ref, not
  // state — it's read inside drop handlers and must never trigger a render
  // mid-drag (a re-render during a drag cancels it in some browsers).
  const dragPayload = React.useRef(null);
  const [fire, toastNode] = useToast();

  const activeLib = libraries.find((l) => l.id === activeId) || null;
  const importables = libraries.filter((l) => l.importable);
  const isRemovable = activeLib?.kind === 'removable';
  const isDocuments = activeLib?.id === 'core:documents';

  // The libraries are a device read: on a browser not paired yet it is
  // refused and the pair modal opens. Once a credential arrives (the
  // household token pasted there, or "sign in as an admin instead", which
  // pairs the browser itself) the read runs again (data.js
  // useRetryAfterCredential): the page used to keep "no libraries
  // available" until you left it and came back (F-052).
  const [libsRefusal, setLibsRefusal] = React.useState(null);
  const loadLibraries = React.useCallback(async () => {
    setLibLoading(true);
    try {
      const r = await apiGet('/api/files/libraries');
      const libs = r.libraries || [];
      setLibraries(libs);
      setActiveId((prev) => (prev && libs.some((l) => l.id === prev)) ? prev
        : (libs.length ? libs[0].id : null));
      setLibsRefusal(null);
    } catch (e) {
      setLibsRefusal(e && (e.status === 401 || e.status === 403
        || (e.status === 429 && e.deviceTokenWait)) ? e : null);
      // A refusal that opened the sign-in or pair prompt: the prompt says it.
      if (!(e && e.loginPrompted)) fire(`Couldn't load libraries: ${String(e.message || e).slice(0, 80)}`);
    } finally { setLibLoading(false); }
  }, []);

  React.useEffect(() => { loadLibraries(); }, [loadLibraries]);
  useRetryAfterCredential(libsRefusal, loadLibraries);

  // One folder, a page at a time (data.js usePagedList). device_id only
  // affects `writable` / `blocked_reason` in the answer: reading is never
  // blocked, but the page disables its own write controls from this rather
  // than discovering the 403 on first drop. The folder's own fields
  // (breadcrumb, editable, writable…) ride on every page; `view` is the
  // latest answer.
  const needle = debFilter.trim();
  // Read once: where storage is unavailable DeviceIdentity.id() mints a new
  // id per call, and a new id per render would be a new list per render.
  const deviceId = React.useRef(null);
  if (deviceId.current == null) deviceId.current = DeviceIdentity.id();
  const browsePath = activeId
    ? `/api/files/browse?library_id=${encodeURIComponent(activeId)}&path=${encodeURIComponent(path || '')}`
      + `&device_id=${encodeURIComponent(deviceId.current)}`
      + (needle ? `&q=${encodeURIComponent(needle)}` : '')
      + (sort !== 'name' ? `&sort=${encodeURIComponent(sort)}` : '')
    : null;
  // `files.changed` (the state socket) re-reads the folder on screen when a
  // library changed elsewhere: a phone uploaded into it, another browser
  // deleted, a plugin saved a picture (listing_cache tells).
  const list = usePagedList(browsePath, { pick: (x) => x && x.entries, key: (e) => e.rel,
                                          eventTypes: ['files.changed'] });
  const view = list.extra;
  const browseLoading = list.loading && list.items.length === 0;

  // A new folder, library, filter or sort is a new list: nothing selected.
  React.useEffect(() => { setSelected(new Set()); }, [browsePath]);

  // A folder that could not be opened says so; an ejected removable (410)
  // drops out of the registry.
  React.useEffect(() => {
    const e = list.error;
    if (!e || list.items.length) return;
    const msg = String(e.message || e);
    fire(`Couldn't open folder: ${msg.slice(0, 80)}`);
    if (e.status === 410 || msg.startsWith('410')) loadLibraries();
  }, [list.error]);

  // A filter typed in one library doesn't follow you into the next.
  React.useEffect(() => { setFilter(''); }, [activeId]);

  const navigate = (p) => { setFilter(''); setLoc({ libId: activeId, path: p || '' }); };
  // Re-read the folder (what is loaded of it), keeping the scroll position.
  const refresh = () => list.reload();
  const onSelectLibrary = (l) => { if (l.id !== activeId) setActiveId(l.id); };
  // Rescan: the library registry AND this folder from disk, past the
  // server's cached listing.
  const rescan = () => { loadLibraries(); list.reload({ fresh: true }); };

  const toggleSelect = (rel) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(rel) ? next.delete(rel) : next.add(rel);
    return next;
  });
  // Selection, "select all" and the bulk actions cover the LOADED rows —
  // what is on screen — never entries further down that haven't been
  // fetched; the checkbox's title says how many that is.
  const entries = list.items;
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
      const msg = mutationErrorText(e, 'Create', { kept: false });
      if (msg) fire(msg);
    }
  };

  // ── Upload (files and folders into the current directory) ──
  // Every way in — Upload → Files…, Upload → Folder…, a drop — becomes one
  // batch for the queue (useFilesUploadQueue, above), which keeps the
  // folders' structure under the folder on screen.
  const up = useFilesUploadQueue({ fire, onFinished: () => refresh() });
  const uploading = up.running;
  const uploadGate = () => {
    if (!activeLib?.editable) { fire('this library is read-only'); return false; }
    if (view?.writable === false) { fire(view.blocked_reason || 'this device is blocked from changing files'); return false; }
    return true;
  };
  const startUpload = ({ items, unreadable }) => {
    if (!items.length && !(unreadable && unreadable.length)) return;
    if (!uploadGate()) return;
    const where = [activeLib?.label || activeId, ...(path ? path.split('/') : [])].join(' / ');
    up.start({ items, unreadable, libraryId: activeId, path, label: where });
  };
  const onPicked = (input) => {
    const items = pickedUploadItems(input.files);
    input.value = '';
    startUpload({ items, unreadable: [] });
  };
  const onUploadPick = (key) => {
    const el = key === 'folder' ? folderInputRef.current : fileInputRef.current;
    if (el) el.click();
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
      // A 401 here already opened the login modal (data.js) — that prompt
      // owns the story; a raw status line behind it reads as a crash (F-006).
      if (!isAuthFailure(e)) fire(`delete failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setBusy(null); }
  };

  // ── Move (drag and drop) ──
  /* Dragging a row that's part of the selection drags the WHOLE selection —
   * that's the behavior every file manager has, and without it a multi-select
   * plus drag silently moves one file. */
  const onDragStartEntry = (e, entry) => {
    const selectedPaths = selected.has(entry.rel)
      ? Array.from(selected)
      : [entry.rel];
    dragPayload.current = {
      libraryId: activeId,
      paths: selectedPaths,
      label: selectedPaths.length === 1
        ? entry.name
        : `${selectedPaths.length} items`,
    };
    try {
      // The payload itself stays in the ref; this only TAGS the drag so drop
      // targets can tell an internal move from a file dragged off the desktop.
      e.dataTransfer.setData(FILES_DRAG_MIME, '1');
      e.dataTransfer.effectAllowed = 'move';
    } catch { /* older browsers: the ref is still authoritative */ }
  };

  const performMove = async (targetLibraryId, targetPath) => {
    const payload = dragPayload.current;
    dragPayload.current = null;
    if (!payload || !payload.paths.length) return;
    // Dropping a folder onto itself. The server refuses this too; catching it
    // here keeps the obvious mistake from reading like an error.
    if (payload.libraryId === targetLibraryId
        && payload.paths.some((rel) => rel === targetPath)) return;

    setBusy('moving');
    try {
      const res = await apiPost('/api/files/move', {
        source_library_id: payload.libraryId,
        paths: payload.paths,
        target_library_id: targetLibraryId,
        target_path: targetPath || '',
        device_id: DeviceIdentity.id(),
      });
      const okN = (res.moved || []).length;
      const skipN = (res.skipped || []).length;
      const failN = (res.failed || []).length;
      const parts = [];
      if (okN) parts.push(`moved ${okN} item${okN === 1 ? '' : 's'}`);
      if (skipN) parts.push(`${skipN} already there`);
      if (failN) parts.push(`${failN} failed`);
      if (res.reindex_triggered) parts.push('reindexing');
      fire(parts.join(' · ') || 'nothing to move');
      // Name the first real failure — "1 failed" with no reason is useless
      // when the cause is a collision the user can act on.
      if (failN) fire(String(res.failed[0]).slice(0, 120));
      clearSelection();
      refresh();
    } catch (e) {
      const msg = mutationErrorText(e, 'move', { kept: false });
      if (msg) fire(msg);
    } finally { setBusy(null); }
  };

  const onDropIntoEntry = (entry) => performMove(activeId, entry.rel);
  const onDropIntoPath = (p) => performMove(activeId, p);
  const onDropIntoLibrary = (targetLib) => performMove(targetLib.id, '');

  /* Files dragged in from the desktop are an UPLOAD, not a move. Internal
   * drags carry the private MIME type and are ignored here — folder rows,
   * crumbs and library chips handle those. */
  const onOsFileDrop = (e) => {
    if (isInternalDrag(e) || !hasOsFiles(e)) return;
    e.preventDefault();
    if (!uploadGate()) return;
    if (up.running) { fire('an upload is already running — wait for it, or cancel it first'); return; }
    // The entries must be taken NOW, inside the event; the walk is async.
    collectDroppedUpload(e.dataTransfer)
      .then(startUpload)
      .catch((err) => fire(`couldn't read what was dropped: ${String((err && err.message) || err).slice(0, 80)}`));
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
        device_id: DeviceIdentity.id(),
      });
      const parts = [`imported ${(res.copied || []).length} item${(res.copied || []).length === 1 ? '' : 's'}`];
      if (res.skipped?.length) parts.push(`${res.skipped.length} skipped`);
      if (res.reindex_triggered) parts.push('reindexing');
      fire(parts.join(' · '));
    } catch (e) {
      const msg = mutationErrorText(e, 'import', { kept: false });
      if (msg) fire(msg);
    } finally { setBusy(null); }
  };

  const DrawingOverlayCmp = window.DrawingSuite && window.DrawingSuite.DrawingOverlay;
  const nSel = selected.size;
  // Two different "no": the LIBRARY is read-only (editable=false), or THIS
  // DEVICE has been blocked by an admin (writable=false). Upload / move /
  // import need both; delete is admin-gated server-side and stays visible —
  // the login modal handles the 401.
  const blocked = view?.writable === false;
  // The server's own sentence for this device, handed to the editors so
  // they say the same thing this page does.
  const blockedReason = blocked
    ? (view.blocked_reason || 'this device is blocked from changing files') : null;
  const canWrite = !!view?.editable && !blocked;
  const canUpload = !!activeLib?.editable && !blocked;
  const uploadTitle = blockedReason
    || (canUpload ? 'upload into this folder' : 'this library is read-only');

  return (
    /* The desktop-file drop is handled ONCE, here on the page root: without
       it the browser's default would navigate the dashboard away to the
       dropped file. (Card renders a fixed <section> and forwards no extra
       props, so it can't carry these.) */
    <div className="page"
         onDragOver={(e) => { if (!isInternalDrag(e) && hasOsFiles(e)) e.preventDefault(); }}
         onDrop={onOsFileDrop}>
      <PageHeader title="Files"
        sub={"Browse every library — core media, plugins & removable drives."
             + " Drag rows onto a folder, a breadcrumb, or another library to move them;"
             + " drop files or whole folders from your computer to upload them here."}
        actions={<>
          {/* Two pickers behind one Upload menu: a browser's file dialog picks
              files OR (webkitdirectory) folders, never both at once. */}
          <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }}
                 data-testid="files-upload-input" tabIndex={-1} aria-hidden="true"
                 onChange={(e) => onPicked(e.target)}/>
          <input ref={folderInputRef} type="file" multiple webkitdirectory="" directory=""
                 style={{ display: 'none' }} data-testid="files-folder-input" tabIndex={-1}
                 aria-hidden="true" onChange={(e) => onPicked(e.target)}/>
          {/* Scan (printers.jsx): only with a saved scanner, and only where
              this device may write to Documents (a scan writes there); a
              finished scan lands in Documents, so the folder is read again. */}
          {isDocuments && canUpload && window.PrintingScanButton && (
            <window.PrintingScanButton fire={fire} onDone={() => refresh()}/>
          )}
          {isDocuments && <NewMenu disabled={!canUpload} onPick={onCreate}/>}
          <FilesActionMenu label={uploading ? 'Uploading…' : 'Upload'} icon="upload"
                           variant={isDocuments ? 'secondary' : 'primary'}
                           disabled={uploading || !canUpload} title={uploadTitle}
                           items={FILES_UPLOAD_CHOICES} onPick={onUploadPick}/>
        </>}/>

      <LibrarySelector libraries={libraries} activeId={activeId}
                       onSelect={onSelectLibrary} onRefresh={rescan}
                       onDropMove={canWrite ? onDropIntoLibrary : null}/>

      <FilesUploadPanel up={up}/>
      <FilesConflictDialog ask={up.ask}/>

      {blocked && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '10px 14px',
                      marginBottom: 12, borderRadius: 'var(--r-sm)',
                      border: '1px solid var(--border)', background: 'var(--sunken)',
                      fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
          <Icon name="lock" size={13}/>
          {/* Naming the affordances one by one was already a list that
              could go stale, and it did: saving a document is a write
              too, and once the block reaches the editors this banner
              understated it. Say what is and is not allowed instead. */}
          <span>
            {view.blocked_reason}. You can browse, open and download from this device,
            but not change anything here — no saving a document, uploading, moving or
            importing. An admin lifts the block in Settings → Phones & browsers.
          </span>
        </div>
      )}

      <Card>
        {/* Wraps: on a phone the filter, sort and count go to a second line
            rather than pushing the page sideways. */}
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 12px',
                      padding: '9px 14px', borderBottom: '1px solid var(--border-soft)' }}>
          {entries.length > 0 && (
            <input type="checkbox" checked={allSelected}
                   title={`select the ${entries.length} loaded item${entries.length === 1 ? '' : 's'}`
                          + (list.hasMore ? ' (more load as you scroll)' : '')}
                   ref={(el) => { if (el) el.indeterminate = nSel > 0 && !allSelected; }}
                   onChange={toggleSelectAll}
                   style={{ cursor: 'pointer', width: 15, height: 15, flexShrink: 0 }}/>
          )}
          <FilesBreadcrumb lib={activeLib}
                           segments={view?.breadcrumb || []} onNav={navigate}
                           onDropMove={canWrite ? onDropIntoPath : null}/>
          <span style={{ flex: 1 }}/>
          {nSel > 0 ? (
            <>
              <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                {nSel} selected{busy === 'moving' ? ' · moving…' : ''}
              </span>
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
            <>
              <input type="text" placeholder="filter this folder…" value={filter}
                     onChange={(e) => setFilter(e.target.value)} disabled={!activeLib}
                     style={{ font: 'inherit', fontSize: 12, height: 26, padding: '0 8px', width: 150,
                              borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                              background: 'var(--card)', color: 'var(--fg)' }}/>
              <select value={sort} onChange={(e) => setSort(e.target.value)} title="sort (folders first)"
                      disabled={!activeLib}
                      style={{ font: 'inherit', fontSize: 12, height: 26, padding: '0 6px',
                               borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                               background: 'var(--card)', color: 'var(--fg)' }}>
                <option value="name">name</option>
                <option value="newest">newest</option>
                <option value="oldest">oldest</option>
                <option value="largest">largest</option>
              </select>
              <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                {view && needle
                  ? `${fmtCount(view.total)} of ${fmtCount(view.folder_total)}`
                  : `${fmtCount(view ? view.total : entries.length)} item${(view ? view.total : entries.length) === 1 ? '' : 's'}`}
              </span>
            </>
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
        ) : entries.length === 0 && !list.error ? (
          needle
            ? <Empty title="nothing matches" sub={`filter = “${needle}”`}
                     action={<Button icon="x" onClick={() => setFilter('')}>clear filter</Button>}/>
            : <Empty title="empty folder"
                     sub={canUpload ? 'upload files or a folder with Upload (or drop them here), or go back up' : 'nothing here'}/>
        ) : (
          entries.map((entry) => {
            if (!entry.is_dir && isDocuments && view.doc_editing) {
              return (
                <DocRow key={entry.rel} doc={entryToDoc(entry)}
                        selected={selected.has(entry.rel)} onToggleSelect={toggleSelect}
                        onOpenDoc={setDocRel} onOpenSheet={setSheetRel}
                        onOpenText={setTextRel}
                        onOpenDrawing={(d) => setDrawing(d)}
                        onDragStartEntry={canWrite ? onDragStartEntry : null}
                        onDelete={(d) => requestDelete([entries.find((e) => e.rel === d.rel_path)])}/>
              );
            }
            return (
              <BrowserRow key={entry.rel} entry={entry} libraryId={activeId}
                          selected={selected.has(entry.rel)} onToggleSelect={toggleSelect}
                          editable={view.editable} removable={isRemovable && !blocked}
                          importables={importables}
                          onOpen={() => navigate(entry.rel)}
                          onDownload={() => downloadEntry(entry)}
                          onDelete={() => requestDelete([entry])}
                          onImport={doImport}
                          onDragStartEntry={canWrite ? onDragStartEntry : null}
                          onDropMove={canWrite ? onDropIntoEntry : null}/>
            );
          })
        )}
        {activeLib && <PagedListFooter list={list} noun="items" compact/>}
      </Card>

      {/* The page already asked the server whether THIS device may write
          (browse answers `writable` / `blocked_reason`), so an editor it
          opens can grey Save out rather than letting the person type for
          ten minutes and then be refused. The editors take the refusal
          on its own too — the block can arrive while one is open. */}
      {docRel && window.DocEditor && (
        <window.DocEditor rel_path={docRel} onClose={onCloseDoc} fire={fire}
                          blockedReason={blockedReason}/>
      )}
      {sheetRel && window.SheetEditor && (
        <window.SheetEditor rel_path={sheetRel} onClose={onCloseSheet} fire={fire}
                            blockedReason={blockedReason}/>
      )}
      {textRel && <TextEditorOverlay rel_path={textRel} onClose={onCloseText} fire={fire}
                                     blockedReason={blockedReason}/>}
      {drawing && DrawingOverlayCmp && (
        <DrawingOverlayCmp file={drawing} lib={lib}
                           onClose={() => setDrawing(null)}
                           blockedReason={blockedReason}
                           onSaved={() => refresh()} fire={fire}/>
      )}
      <FilesDeleteConfirm state={confirmState} busy={busy === 'deleting'}
                           onCancel={() => setConfirmState(null)}
                           onConfirm={performDelete}/>
      {toastNode}
    </div>
  );
};

window.FilesPage = FilesPage;
