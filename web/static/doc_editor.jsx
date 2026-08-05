/* Homegrown markdown document editor (window.DocEditor) — the Documents
 * library's editor for .md files, replacing the retired office engines.
 *
 * Minimalist by design: a formatting toolbar that inserts markdown around
 * the selection, an edit / preview toggle (vendored `marked`), explicit
 * Save (PUT /api/documents/text/{rel}) with a dirty guard, and Export →
 * .docx (server-side python-docx via /api/documents/export/doc).
 *
 * Same overlay shell as TextEditorOverlay (files.jsx) so the two editors
 * feel like one surface.
 */

const _mdWrap = (textarea, before, after, placeholder) => {
  /* Wrap the current selection (or insert placeholder) and return the new
   * full value + selection range. */
  const { selectionStart: s, selectionEnd: e, value } = textarea;
  const sel = value.slice(s, e) || placeholder;
  const next = value.slice(0, s) + before + sel + after + value.slice(e);
  return { next, start: s + before.length, end: s + before.length + sel.length };
};

const _mdLinePrefix = (textarea, prefix) => {
  /* Prefix every line in the selection (list / heading / quote). */
  const { selectionStart: s, selectionEnd: e, value } = textarea;
  const lineStart = value.lastIndexOf('\n', s - 1) + 1;
  const block = value.slice(lineStart, e);
  const prefixed = block.split('\n').map((l) => prefix + l).join('\n');
  const next = value.slice(0, lineStart) + prefixed + value.slice(e);
  return { next, start: lineStart, end: lineStart + prefixed.length };
};

const DOC_TOOLBAR = [
  { icon: 'bold', title: 'bold', run: (ta) => _mdWrap(ta, '**', '**', 'bold') },
  { icon: 'italic', title: 'italic', run: (ta) => _mdWrap(ta, '*', '*', 'italic') },
  { icon: 'code', title: 'inline code', run: (ta) => _mdWrap(ta, '`', '`', 'code') },
  { icon: 'heading-1', title: 'heading', run: (ta) => _mdLinePrefix(ta, '# ') },
  { icon: 'heading-2', title: 'subheading', run: (ta) => _mdLinePrefix(ta, '## ') },
  { icon: 'list', title: 'bullet list', run: (ta) => _mdLinePrefix(ta, '- ') },
  { icon: 'list-ordered', title: 'numbered list', run: (ta) => _mdLinePrefix(ta, '1. ') },
  { icon: 'quote', title: 'quote', run: (ta) => _mdLinePrefix(ta, '> ') },
  { icon: 'link', title: 'link', run: (ta) => _mdWrap(ta, '[', '](url)', 'text') },
];

const DocEditorOverlay = ({ rel_path, onClose, fire }) => {
  const [state, setState] = React.useState({ status: 'loading' });
  const [text, setText] = React.useState('');
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [preview, setPreview] = React.useState(false);
  const taRef = React.useRef(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/documents/text/${encodeURIComponent(rel_path)}`,
          { cache: 'no-store', credentials: 'include' });
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const j = await r.json();
        if (!cancelled) { setText(j.text || ''); setState({ status: 'ready' }); }
      } catch (e) {
        if (!cancelled) setState({ status: 'error', message: String(e.message || e) });
      }
    })();
    return () => { cancelled = true; };
  }, [rel_path]);

  const applyTool = (tool) => {
    const ta = taRef.current;
    if (!ta) return;
    const { next, start, end } = tool.run(ta);
    setText(next);
    setDirty(true);
    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(start, end);
    });
  };

  const onSave = async () => {
    setSaving(true);
    try {
      await apiFetch(`/api/documents/text/${encodeURIComponent(rel_path)}`, {
        method: 'PUT', body: JSON.stringify({ text }),
      });
      setDirty(false);
      fire && fire('Saved');
    } catch (e) {
      fire && fire(`Save failed: ${String(e.message || e).slice(0, 80)}`);
    } finally { setSaving(false); }
  };

  const onExport = async () => {
    if (dirty) await onSave();
    const a = document.createElement('a');
    a.href = `${API_BASE}/api/documents/export/doc/${encodeURIComponent(rel_path)}?fmt=docx`;
    a.download = '';
    document.body.appendChild(a); a.click(); a.remove();
  };

  const requestClose = () => {
    if (dirty && !window.confirm('You have unsaved changes. Discard them and close?')) return;
    onClose();
  };

  const onKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); onSave(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'b') { e.preventDefault(); applyTool(DOC_TOOLBAR[0]); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'i') { e.preventDefault(); applyTool(DOC_TOOLBAR[1]); }
  };

  const previewHtml = React.useMemo(() => {
    if (!preview) return '';
    try { return window.marked ? window.marked.parse(text) : '<p>preview unavailable</p>'; }
    catch { return '<p>preview failed</p>'; }
  }, [preview, text]);

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'var(--bg)',
                  display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                    borderBottom: '1px solid var(--border)', background: 'var(--card)' }}>
        <Icon name="file-text" size={16}/>
        <strong style={{ fontSize: 14 }}>{rel_path}</strong>
        <Pill tone="idle">markdown</Pill>
        {dirty && <span style={{ fontSize: 11, color: 'var(--warn)' }}>unsaved — click Save</span>}
        <span style={{ flex: 1 }}/>
        <Button icon={preview ? 'edit' : 'eye'} onClick={() => setPreview(!preview)}>
          {preview ? 'Edit' : 'Preview'}
        </Button>
        <Button icon="file-down" title="export as .docx" onClick={onExport}>Export</Button>
        {state.status === 'ready' && (
          <Button variant="primary" icon="save" disabled={saving || !dirty} onClick={onSave}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        )}
        <Button icon="x" onClick={requestClose}>Close</Button>
      </div>

      {!preview && state.status === 'ready' && (
        <div style={{ display: 'flex', gap: 2, padding: '6px 14px',
                      borderBottom: '1px solid var(--border-soft)', background: 'var(--card)' }}>
          {DOC_TOOLBAR.map((t) => (
            <IconButton key={t.icon} name={t.icon} title={t.title}
                        onClick={() => applyTool(t)}/>
          ))}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {state.status === 'loading' && (
          <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Loading…</div>
        )}
        {state.status === 'error' && (
          <div style={{ padding: 20, color: 'var(--err)', fontSize: 13 }}>
            Couldn’t load: {state.message}
          </div>
        )}
        {state.status === 'ready' && (preview ? (
          <div className="doc-preview"
               style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '20px 24px',
                        maxWidth: 780, fontSize: 14, lineHeight: 1.65 }}
               dangerouslySetInnerHTML={{ __html: previewHtml }}/>
        ) : (
          <textarea
            ref={taRef}
            className="mono"
            value={text}
            spellCheck={false}
            onChange={(e) => { setText(e.target.value); setDirty(true); }}
            onKeyDown={onKeyDown}
            style={{ flex: 1, minHeight: 0, width: '100%', resize: 'none', border: 'none',
                     outline: 'none', padding: '14px 16px', fontSize: 13, lineHeight: 1.6,
                     background: 'var(--bg)', color: 'var(--fg)', tabSize: 4 }}
          />
        ))}
      </div>
    </div>
  );
};

window.DocEditor = DocEditorOverlay;
