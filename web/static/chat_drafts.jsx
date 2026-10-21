/* Drafts in home-server chats — the dashboard's half of
 * web/backend/api/chat_drafts.py (documents plan, D4 "write and save").
 *
 * "Write a cover letter for …" in a chat makes a draft: the reply row
 * carries `draft` ({id, version, kind, title, action, change}) and is drawn
 * as a draft card instead of text. Follow-ups ("shorter", "more formal")
 * make new versions of the same draft; every version is kept.
 *
 * This file draws those pieces for chat.jsx (window.ChatDrafts):
 *   * DraftCard — on the reply: kind · title, the version chip, the first
 *     lines, the one line of what changed, and open · save · download ·
 *     print; "print it" in the chat gets the same card with Print first;
 *   * Canvas — the document panel: beside the conversation on a wide
 *     screen (1,100 px and up), in place of the thread on a narrow one,
 *     with the composer left below it either way so "shorter" works while
 *     looking at the draft. A version picker (v1 · v2 · v3), the changes
 *     switch (added words in the soft amber wash, removed ones struck
 *     through, never green or red: those mean status here; on for a
 *     version this browser hasn't seen, off once it has), edit (saved as a
 *     version "edited by you"), save as .docx or PDF into Documents → From
 *     Chat with the ATS self-check's line, download, and print;
 *   * List — every chat's drafts, for the Chat page while the household's
 *     AI features are off: no drafting then, but saving still works.
 * The diff comes from the server (chat_drafts.py ``changes``), so there is
 * no second copy of it here. Every top-level name starts ChatDraft: page
 * scripts share one scope.
 */

const ChatDrafts = (() => {
  const KIND = { resume: 'resume', cover_letter: 'cover letter', letter: 'letter' };
  const FORMAT_LABEL = { docx: '.docx', pdf: 'PDF' };
  // The width from which the panel sits beside the conversation.
  const WIDE_MIN = 1100;

  const api = {
    get: (id) => apiGet(`/api/chat/drafts/${id}`),
    thread: (tid) => apiGet(`/api/chat/threads/${tid}/drafts`, { quiet: true }),
    all: () => apiGet('/api/chat/drafts'),
    edit: (id, text, base) => apiPost(`/api/chat/drafts/${id}/versions`, { text, base_version: base }),
    save: (id, version, format) => apiPost(`/api/chat/drafts/${id}/save`, {
      version, format, device_id: (typeof DeviceIdentity !== 'undefined' ? DeviceIdentity.id() : null) }),
    exportUrl: (id, version, format) => withDeviceToken(
      `${API_BASE}/api/chat/drafts/${id}/export?version=${version}&format=${format}`),
  };

  /* The newest version this browser has looked at, per draft (changes are
   * on for a version newer than that). Lost storage only means changes
   * show again. */
  const seenKey = (id) => `domovoi.draftSeen.${id}`;
  const seen = (id) => {
    try { return Number(window.localStorage.getItem(seenKey(id)) || 0); } catch { return 0; }
  };
  const markSeen = (id, v) => {
    try { if (v > seen(id)) window.localStorage.setItem(seenKey(id), String(v)); } catch { /* fine */ }
  };

  /* true while `ref`'s element is at least `px` wide. */
  const useWidthAtLeast = (ref, px) => {
    const [ok, setOk] = React.useState(() => (window.innerWidth || 1280) - 232 - 48 >= px);
    React.useLayoutEffect(() => {
      const el = ref.current;
      if (!el) return undefined;
      const measure = () => {
        const w = el.getBoundingClientRect().width;
        if (w > 0) setOk(w >= px);
      };
      measure();
      if (typeof ResizeObserver === 'undefined') {
        window.addEventListener('resize', measure);
        return () => window.removeEventListener('resize', measure);
      }
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      return () => ro.disconnect();
    }, [ref, px]);
    return ok;
  };

  const sourceLabel = (v) => {
    if (!v) return '';
    if (v.source === 'edited') return 'edited by you';
    if (v.source === 'first') return 'first draft';
    return `“${v.instruction || 'revised'}”`;
  };

  const saveErrorText = (e) => {
    const blocked = typeof deviceBlockReason === 'function' ? deviceBlockReason(e) : null;
    if (blocked) return `${blocked}. An admin lifts the block in Settings → Devices.`;
    return (typeof apiErrorText === 'function' ? apiErrorText(e, 0) : null) || 'save failed';
  };

  /* The added and removed words, as elements (never HTML). */
  const Changes = ({ ops }) => (
    <div className="chat-draft-changes">
      {(ops || []).map(([op, text], i) => (
        op === '+' ? <span key={i} className="chat-draft-add">{text}</span>
          : op === '-' ? <del key={i} className="chat-draft-del">{text}</del>
            : <span key={i}>{text}</span>
      ))}
    </div>
  );

  /* Saves a version's file on this device (the server names it). */
  const download = (id, version, format) => {
    const url = api.exportUrl(id, version, format);
    if (typeof deviceDownload === 'function') deviceDownload(url);
    else window.open(url, '_blank', 'noopener');
  };

  /* The draft card on the reply that made a version, or asked to print. */
  const DraftCard = ({ m, latest, onOpen, onSave, onPrint, canPrint }) => {
    const d = m.draft || {};
    const isPrint = d.action === 'print';
    const newer = latest && latest.version > d.version ? latest.version : null;
    const lines = latest && latest.version === d.version ? latest.first_lines : null;
    return (
      <div className="chat-draft-card" role="group" aria-label={`draft: ${d.title}`}>
        <div className="chat-draft-card-head">
          <Icon name={isPrint ? 'printer' : 'file-text'} size={14}/>
          <span className="chat-draft-kind">{KIND[d.kind] || 'draft'}</span>
          <span className="chat-draft-title" title={d.title}>{d.title}</span>
          <span className="chat-draft-chip mono">v{d.version}</span>
          {newer && <span className="chat-draft-newer mono">latest v{newer}</span>}
        </div>
        {lines && <div className="chat-draft-lines">{lines}</div>}
        {m.content && <div className="chat-draft-line">{m.content}</div>}
        <div className="chat-draft-actions">
          {isPrint && canPrint && (
            <Button variant="primary" icon="printer" onClick={() => onPrint(d.id, d.version)}>Print</Button>
          )}
          {isPrint && !canPrint && <span className="chat-draft-note">no printer is set up on this home</span>}
          <Button icon="panel-right-open" onClick={() => onOpen(d.id, d.version)}>open</Button>
          {!isPrint && <Button icon="save" onClick={() => onSave(d.id, d.version)}>save</Button>}
          {!isPrint && (
            <Button icon="download" title="download this version"
                    onClick={() => download(d.id, d.version, d.kind === 'letter' ? 'pdf' : 'docx')}>download</Button>
          )}
          {!isPrint && canPrint && (
            <Button icon="printer" onClick={() => onPrint(d.id, d.version)}>print</Button>
          )}
        </div>
      </div>
    );
  };

  /* The document panel. `reloadKey` changes when the chat wrote a version
   * of this draft: the panel reads it again and shows the newest. */
  const Canvas = ({ draftId, version: wantVersion, reloadKey, wide, onClose, onPrint, canPrint, fire,
                    readOnly = false, onVersion = null }) => {
    const [data, setData] = React.useState(null);
    const [error, setError] = React.useState(null);
    const [sel, setSel] = React.useState(null);
    const [showChanges, setShowChanges] = React.useState(false);
    const [editing, setEditing] = React.useState(null);     // the text being edited
    const [busy, setBusy] = React.useState(false);
    const [saved, setSaved] = React.useState(null);         // {name, check}

    const load = async (jumpTo) => {
      try {
        const r = await api.get(draftId);
        setData(r);
        setError(null);
        const vs = r.versions || [];
        const last = vs.length ? vs[vs.length - 1].version : 1;
        const pick = jumpTo && vs.some((v) => v.version === jumpTo) ? jumpTo : last;
        setSel(pick);
        setShowChanges(pick > 1 && pick > seen(draftId));
      } catch (e) {
        setError(e && e.status === 404 ? 'this draft is gone' : "couldn't read the draft");
      }
    };
    React.useEffect(() => { setEditing(null); setSaved(null); load(wantVersion); }, [draftId, reloadKey]);
    // The version on screen, remembered here and by the page (a panel that
    // moves between beside and in place opens where it was).
    React.useEffect(() => { if (sel) { markSeen(draftId, sel); if (onVersion) onVersion(sel); } }, [draftId, sel]);
    React.useEffect(() => {
      const key = (e) => { if (e.key === 'Escape' && !editing) onClose(); };
      document.addEventListener('keydown', key);
      return () => document.removeEventListener('keydown', key);
    }, [onClose, editing]);

    const versions = (data && data.versions) || [];
    const v = versions.find((x) => x.version === sel) || versions[versions.length - 1] || null;
    const latest = versions[versions.length - 1] || null;
    const draft = (data && data.draft) || {};
    const fmt = draft.format || 'docx';
    const other = fmt === 'docx' ? 'pdf' : 'docx';

    const save = async (format) => {
      if (!v) return;
      setBusy(true);
      try {
        const r = await api.save(draftId, v.version, format);
        setSaved(r);
        if (fire) fire(`saved to Documents → ${r.rel_path}`);
        load(v.version);
      } catch (e) {
        if (fire) fire(saveErrorText(e));
      } finally { setBusy(false); }
    };
    const saveEdit = async () => {
      if (editing == null || !latest) return;
      setBusy(true);
      try {
        const r = await api.edit(draftId, editing, latest.version);
        setEditing(null);
        if (fire) fire(r.unchanged ? 'no changes' : `saved as v${r.version.version} · edited by you`);
        load(r.version.version);
      } catch (e) {
        if (e && e.status === 409) {
          if (fire) fire('a newer version came in meanwhile; your text is still here');
          const r = await api.get(draftId).catch(() => null);
          if (r) setData(r);
        } else if (fire) fire(saveErrorText(e));
      } finally { setBusy(false); }
    };

    const head = (
      <div className="chat-canvas-head">
        <Icon name="file-text" size={16}/>
        <div className="chat-canvas-title" title={draft.title}>{draft.title || 'draft'}</div>
        <IconButton name="x" title="close the draft" aria-label="close the draft" onClick={onClose}/>
      </div>
    );
    if (error || !data) {
      return (
        <div className={`chat-canvas${wide ? ' wide' : ''}`} role="region" aria-label="draft">
          {head}
          <div className="chat-canvas-body chat-draft-note">{error || 'reading the draft…'}</div>
        </div>
      );
    }
    return (
      <div className={`chat-canvas${wide ? ' wide' : ''}`} role="region" aria-label={`draft: ${draft.title}`}>
        {head}
        <div className="chat-canvas-bar">
          <div className="chat-canvas-versions" role="tablist" aria-label="versions">
            {versions.map((x) => (
              <button key={x.version} type="button" role="tab" aria-selected={x.version === sel}
                      className={`chat-canvas-version mono${x.version === sel ? ' on' : ''}`}
                      title={`v${x.version} · ${sourceLabel(x)}`}
                      onClick={() => { setEditing(null); setSel(x.version);
                                       setShowChanges(x.version > 1 && x.version > seen(draftId)); }}>
                v{x.version}
              </button>
            ))}
          </div>
          <span className="chat-canvas-source">{sourceLabel(v)}</span>
          <span style={{ flex: 1 }}/>
          <label className={`chat-canvas-switch${v && v.changes ? '' : ' off'}`}>
            <input type="checkbox" checked={!!(showChanges && v && v.changes)} disabled={!v || !v.changes || editing != null}
                   onChange={(e) => setShowChanges(e.target.checked)}/>
            changes
          </label>
        </div>
        <div className="chat-canvas-tools">
          {!readOnly && editing == null && (
            <Button icon="pencil" disabled={busy || !latest}
                    title={v && latest && v.version !== latest.version ? `edit starting from v${v.version}` : 'edit'}
                    onClick={() => setEditing(v ? v.body : '')}>edit</Button>
          )}
          {editing != null && (
            <React.Fragment>
              <Button variant="primary" icon="check" disabled={busy || !editing.trim()} onClick={saveEdit}>
                save as v{(latest ? latest.version : 0) + 1}
              </Button>
              <Button icon="x" onClick={() => setEditing(null)}>cancel</Button>
            </React.Fragment>
          )}
          {editing == null && (
            <React.Fragment>
              <Button variant="primary" icon="save" disabled={busy} onClick={() => save(fmt)}
                      title={`save v${v ? v.version : ''} as ${FORMAT_LABEL[fmt]} in Documents → From Chat`}>
                save {FORMAT_LABEL[fmt]}
              </Button>
              <Button icon="save" disabled={busy} onClick={() => save(other)}>save {FORMAT_LABEL[other]}</Button>
              <Button icon="download" disabled={!v} title="download this version"
                      onClick={() => download(draftId, v.version, fmt)}>download</Button>
              {canPrint && (
                <Button icon="printer" disabled={!v} onClick={() => onPrint(draftId, v.version)}>print</Button>
              )}
            </React.Fragment>
          )}
        </div>
        {saved && (
          <div className={`chat-canvas-saved${saved.check && saved.check.ok ? '' : ' warn'}`} role="status">
            <span className="mono">{saved.rel_path}</span>
            <span>{saved.check ? saved.check.line : ''}</span>
          </div>
        )}
        <div className="chat-canvas-body">
          {editing != null ? (
            <textarea className="chat-canvas-edit mono" value={editing} aria-label="edit the draft"
                      onChange={(e) => setEditing(e.target.value)}/>
          ) : showChanges && v && v.changes ? (
            <Changes ops={v.changes}/>
          ) : (
            <div className="chat-md chat-canvas-doc"
                 dangerouslySetInnerHTML={{ __html: window.chatMarkdownHtml((v && v.body) || '') }}/>
          )}
        </div>
        {v && (v.saved || []).length > 0 && editing == null && (
          <div className="chat-canvas-files">
            {(v.saved || []).map((s, i) => (
              <div key={i} className="chat-canvas-file">
                <span className="mono">{s.rel_path}</span>
                <span className={s.check && s.check.ok ? '' : 'warn'}>{s.check ? s.check.line : ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  /* Every chat's drafts, for the Chat page with AI off: open one to save,
   * download or print it (no drafting, no composer). */
  const List = ({ onOpen }) => {
    const [items, setItems] = React.useState(null);
    React.useEffect(() => {
      api.all().then((r) => setItems(r.drafts || [])).catch(() => setItems([]));
    }, []);
    if (!items || items.length === 0) return null;
    return (
      <div className="chat-draft-list" aria-label="drafts">
        <div className="chat-draft-list-head">drafts from chats · saving still works</div>
        {items.map((d) => (
          <button key={d.id} type="button" className="chat-draft-list-row" onClick={() => onOpen(d.id)}>
            <Icon name="file-text" size={14}/>
            <span className="chat-draft-title">{d.title}</span>
            <span className="mono chat-draft-chip">v{d.version}</span>
          </button>
        ))}
      </div>
    );
  };

  return { api, KIND, WIDE_MIN, DraftCard, Canvas, Changes, List, useWidthAtLeast, seen, markSeen };
})();

window.ChatDrafts = ChatDrafts;
