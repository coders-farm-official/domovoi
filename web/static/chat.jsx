/* Chat page — Claude-desktop-style threaded text chat, answered by the
 * local Ollama (web/backend/api/chat.py).
 *
 * Layout: thread list rail (collapsible on phones) + conversation pane +
 * composer. Sending POSTs the message and reads the reply as an SSE stream
 * (fetch + ReadableStream — the assistant bubble fills in live). Attach up
 * to 4 images per message; a message with images is answered by the vision
 * model (server-side switch), surfaced with a small pill in the composer.
 *
 * Design notes: the cat glyph marks assistant-attributed lines (one of its
 * three sanctioned homes); user bubbles sit right-aligned on the card
 * surface; assistant text renders as Markdown (chat_markdown.js: the
 * vendored marked, then the sanitiser), the user's own text as pre-wrap.
 */

/* An <img src> (and the new tab its link opens) cannot set a header, and a
 * chat image is read on the device tier like the thread it was sent in, so
 * the URL carries the household token as ?device_token= (withDeviceToken,
 * data.js) — the query the server accepts on reads and nowhere else. */
const chatUploadUrl = (token) => withDeviceToken(`${API_BASE}/api/chat/uploads/${token}`);

/* SSE reader for the send endpoint: fetch + ReadableStream, calling
 * onDelta(text) per chunk and resolving with the final done payload. */
const chatSendStream = async (threadId, body, onDelta) => {
  // Streams the reply, so it needs the Response itself rather than parsed
  // JSON — apiFetchRaw, not apiFetch. Going through the helper is what
  // gets an unpaired browser the "pair this browser" prompt and one
  // replay of the message, instead of a red bubble it can do nothing
  // about; a bare fetch() sends the same headers and none of that.
  const r = await apiFetchRaw(`/api/chat/threads/${threadId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.body) throw new Error(`${r.status} ${r.statusText}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let done = null;
  let errorDetail = null;
  for (;;) {
    const { value, done: eof } = await reader.read();
    if (eof) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = 'message';
      let data = '';
      frame.split('\n').forEach((line) => {
        if (line.startsWith('event: ')) event = line.slice(7).trim();
        else if (line.startsWith('data: ')) data += line.slice(6);
      });
      if (!data) continue;
      let payload;
      try { payload = JSON.parse(data); } catch { continue; }
      if (event === 'delta') onDelta(payload.text || '');
      else if (event === 'done') done = payload;
      else if (event === 'error') errorDetail = payload.detail || 'model error';
    }
  }
  return { done, errorDetail };
};

/* ─── Thread list rail ──────────────────────────────────────────────────── */

const ChatThreadRow = ({ t, active, onSelect, onDelete }) => (
  <div onClick={() => onSelect(t)}
       style={{ padding: '9px 12px', borderRadius: 'var(--r-sm)', cursor: 'pointer',
                background: active ? 'var(--brand-soft)' : 'transparent',
                display: 'flex', alignItems: 'center', gap: 8 }}>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: active ? 600 : 500, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {t.title || 'new chat'}
      </div>
      <div style={{ fontSize: 11, color: 'var(--fg-faint)', overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {t.last_snippet || 'no messages yet'}
      </div>
    </div>
    <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', flexShrink: 0 }}>
      {liveRelTime(t.updated_at)}
    </span>
    <IconButton name="trash-2" title="delete chat"
                onClick={(e) => { e.stopPropagation(); onDelete(t); }}/>
  </div>
);

/* ─── Messages ──────────────────────────────────────────────────────────── */

/* Copy a message. The dashboard is usually opened over plain http on the
 * LAN, where browsers withhold navigator.clipboard (secure contexts only),
 * so the old hidden-textarea copy is the fallback, not dead code. */
const chatCopyText = async (text) => {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* fall back below */ }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  document.body.removeChild(ta);
  return ok;
};

/* Who sent a user message, as the details show it. */
const chatDeviceLabel = (m) => {
  if (m.device) return m.device;
  const mine = typeof DeviceIdentity !== 'undefined' && m.device_id && m.device_id === DeviceIdentity.id();
  return mine ? 'this browser' : (m.device_name || m.device_id || null);
};

/* The copy / details menu, at the pointer (right-click or long press) or
 * under the "more" button. Closes on a click elsewhere, Escape or scroll. */
const ChatMessageMenu = ({ at, canCopy, onCopy, onDetails, onClose }) => {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const away = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    const key = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', key);
    window.addEventListener('scroll', onClose, true);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('pointerdown', away, true);
      document.removeEventListener('keydown', key);
      window.removeEventListener('scroll', onClose, true);
      window.removeEventListener('resize', onClose);
    };
  }, [onClose]);
  // Kept on screen: a press near the right or bottom edge opens inward.
  const left = Math.max(8, Math.min(at.x, window.innerWidth - 168));
  const top = Math.max(8, Math.min(at.y, window.innerHeight - 100));
  const item = (icon, label, run, disabled) => (
    <button type="button" className="chat-menu-item" disabled={disabled} role="menuitem"
            onClick={() => { onClose(); run(); }}>
      <Icon name={icon} size={14}/><span>{label}</span>
    </button>
  );
  return (
    <div ref={ref} className="chat-menu" role="menu" style={{ left, top }}>
      {item('copy', 'copy', onCopy, !canCopy)}
      {item('info', 'details', onDetails, false)}
    </div>
  );
};

const ChatDetailsDialog = ({ m, threadId, onCopy, onClose }) => {
  React.useEffect(() => {
    const key = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [onClose]);
  const rows = ChatDetails.rows({ ...m, device: chatDeviceLabel(m) }, threadId);
  return (
    <div className="chat-details-bg" onClick={onClose}>
      <div className="chat-details" role="dialog" aria-modal="true" aria-label="message details"
           onClick={(e) => e.stopPropagation()}>
        <div className="chat-details-head">
          <Icon name="info" size={16}/><strong>message details</strong>
          <span style={{ flex: 1 }}/>
          <IconButton name="x" title="close" onClick={onClose}/>
        </div>
        <dl className="chat-details-rows">
          {rows.map(([label, value]) => (
            <React.Fragment key={label}>
              <dt>{label}</dt>
              <dd className={label === 'error' ? 'err' : ''}>{value}</dd>
            </React.Fragment>
          ))}
        </dl>
        <div className="chat-details-foot">
          <Button icon="copy" onClick={onCopy}>copy text</Button>
          <Button variant="primary" onClick={onClose}>close</Button>
        </div>
      </div>
    </div>
  );
};

// A touch held this long opens the menu (iOS fires no contextmenu event).
const CHAT_LONG_PRESS_MS = 500;

const ChatMessage = ({ m, threadId, onCopy }) => {
  const isUser = m.role === 'user';
  const [menuAt, setMenuAt] = React.useState(null);
  const [details, setDetails] = React.useState(false);
  const press = React.useRef(null);
  const closeMenu = React.useCallback(() => setMenuAt(null), []);
  const closeDetails = React.useCallback(() => setDetails(false), []);

  // Right-click (and Android's long press, which arrives as the same
  // event). With text selected, the browser's own menu stays, so part of
  // a message can still be copied the ordinary way.
  const onContextMenu = (e) => {
    const sel = window.getSelection && String(window.getSelection());
    if (sel) return;
    e.preventDefault();
    setMenuAt({ x: e.clientX, y: e.clientY });
  };
  const cancelPress = () => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  };
  const onPointerDown = (e) => {
    if (e.pointerType !== 'touch') return;
    const { clientX: x, clientY: y } = e;
    cancelPress();
    press.current = { x, y, timer: setTimeout(() => { press.current = null; setMenuAt({ x, y }); }, CHAT_LONG_PRESS_MS) };
  };
  const onPointerMove = (e) => {
    const p = press.current;
    if (p && Math.hypot(e.clientX - p.x, e.clientY - p.y) > 10) cancelPress();
  };
  const bubbleEvents = {
    onContextMenu, onPointerDown, onPointerMove,
    onPointerUp: cancelPress, onPointerCancel: cancelPress, onPointerLeave: cancelPress,
  };
  const openFromButton = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    setMenuAt({ x: isUser ? r.right - 160 : r.left, y: r.bottom + 4 });
  };

  const stamp = ChatDetails.stamp(m.created_at);
  const line = [stamp, !isUser && !m.pending ? m.model : null].filter(Boolean).join(' · ');

  return (
    <div style={{ display: 'flex', flexDirection: 'column',
                  alignItems: isUser ? 'flex-end' : 'stretch', gap: 4 }}>
      {(m.images || []).length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap',
                      justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
          {m.images.map((img) => (
            <a key={img.token} href={chatUploadUrl(img.token)} target="_blank" rel="noopener">
              <img src={chatUploadUrl(img.token)} alt={img.name}
                   style={{ width: 96, height: 96, objectFit: 'cover',
                            borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }}/>
            </a>
          ))}
        </div>
      )}
      {isUser ? (
        <div {...bubbleEvents}
             style={{ maxWidth: '76%', padding: '9px 13px', fontSize: 13, lineHeight: 1.55,
                      background: 'var(--card)', border: '1px solid var(--border)',
                      borderRadius: 'var(--r-md)', whiteSpace: 'pre-wrap',
                      overflowWrap: 'break-word' }}>
          {m.content}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 10, maxWidth: '86%' }}>
          <span style={{ flexShrink: 0, marginTop: 3 }}><DomovoiGlyph size={14}/></span>
          <div {...bubbleEvents}
               style={{ fontSize: 13, lineHeight: 1.6, overflowWrap: 'break-word', minWidth: 0 }}>
            <div className="chat-md" dangerouslySetInnerHTML={{ __html: window.chatMarkdownHtml(m.content || '') }}/>
            {m.pending && <span className="mono" style={{ color: 'var(--fg-faint)' }}>▍</span>}
            {m.error && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--err)', marginTop: 4 }}>
                {m.error}
              </div>
            )}
          </div>
        </div>
      )}
      {!m.pending && (
        <div className={`chat-stamp${isUser ? ' mine' : ''}`}>
          {line && <span className="mono">{line}</span>}
          <button type="button" className="chat-more" title="message actions" aria-label="message actions"
                  aria-haspopup="menu" onClick={openFromButton}>
            <Icon name="ellipsis" size={14}/>
          </button>
        </div>
      )}
      {menuAt && (
        <ChatMessageMenu at={menuAt} canCopy={!!m.content} onClose={closeMenu}
                         onCopy={() => onCopy(m.content)} onDetails={() => setDetails(true)}/>
      )}
      {details && (
        <ChatDetailsDialog m={m} threadId={threadId} onClose={closeDetails}
                           onCopy={() => { onCopy(m.content); closeDetails(); }}/>
      )}
    </div>
  );
};

/* ─── Page ──────────────────────────────────────────────────────────────── */

const ChatPage = () => {
  const [fire, toastNode] = useToast();
  const copyMessage = async (text) => fire((await chatCopyText(text || '')) ? 'copied' : 'copy failed');
  const { items: threads, error: threadsError, refresh: refreshThreads } =
    useApiList('/api/chat/threads', { pickItems: (x) => x.threads, eventTypes: ['chat.changed'] });
  const { data: modelsInfo } = useApiObject('/api/chat/models');

  const [threadId, setThreadId] = React.useState(null);
  const [messages, setMessages] = React.useState([]);
  const [draft, setDraft] = React.useState('');
  const [attachments, setAttachments] = React.useState([]); // [{token, name}]
  const [sending, setSending] = React.useState(false);
  const [railOpen, setRailOpen] = React.useState(true);
  const scrollRef = React.useRef(null);
  const fileRef = React.useRef(null);

  // A thread's messages are read on the device tier: on a browser that is
  // not paired yet the read is refused, apiGet opens the pair modal, and
  // the refusal re-runs the read once a credential arrives. The thread
  // list is a useApiList and recovers on its own.
  const [messagesRefusal, setMessagesRefusal] = React.useState(null);
  const loadMessages = async (id) => {
    try {
      const r = await apiGet(`/api/chat/threads/${id}/messages`);
      setMessages(r.messages || []);
      setMessagesRefusal(null);
    } catch (e) {
      setMessages([]);
      setMessagesRefusal(e && (e.status === 401 || e.status === 403) ? e : null);
    }
  };
  const reloadMessages = React.useCallback(() => {
    if (threadId != null) loadMessages(threadId);
  }, [threadId]);
  useRetryAfterCredential(messagesRefusal, reloadMessages);

  const selectThread = (t) => {
    setThreadId(t.id);
    loadMessages(t.id);
  };

  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const newThread = async () => {
    try {
      const t = await apiPost('/api/chat/threads', {});
      refreshThreads();
      setThreadId(t.id);
      setMessages([]);
    } catch { fire('could not create chat'); }
  };

  const deleteThread = async (t) => {
    if (!window.confirm(`Delete "${t.title || 'new chat'}"? This can't be undone.`)) return;
    try {
      await apiDelete(`/api/chat/threads/${t.id}`);
      if (t.id === threadId) { setThreadId(null); setMessages([]); }
      refreshThreads();
    } catch { fire('delete failed'); }
  };

  const attach = async (files) => {
    for (const f of Array.from(files).slice(0, 4 - attachments.length)) {
      const form = new FormData();
      form.append('file', f);
      try {
        const up = await apiUpload('/api/chat/uploads', form);
        setAttachments((cur) => [...cur, up]);
      } catch (e) {
        fire(e.status === 415 ? 'unsupported image type' : 'upload failed');
      }
    }
  };

  const send = async () => {
    const content = draft.trim();
    if (!content || sending) return;
    let id = threadId;
    if (id == null) {
      try {
        const t = await apiPost('/api/chat/threads', {});
        id = t.id;
        setThreadId(id);
      } catch { fire('could not create chat'); return; }
    }
    const images = attachments;
    setDraft('');
    setAttachments([]);
    setSending(true);
    setMessages((cur) => [
      ...cur,
      { id: `u-${Date.now()}`, role: 'user', content, images,
        created_at: new Date().toISOString(), device: 'this browser' },
      { id: 'pending', role: 'assistant', content: '', pending: true },
    ]);
    try {
      // device_id: which install sent it, for the message's details (V017).
      const body = { content, images, device_id: DeviceIdentity.id() };
      const { done, errorDetail } = await chatSendStream(id, body, (delta) => {
        setMessages((cur) => cur.map((m) =>
          m.id === 'pending' ? { ...m, content: m.content + delta } : m));
      });
      setMessages((cur) => cur.map((m) =>
        m.id === 'pending'
          ? (done || { ...m, pending: false, error: errorDetail })
          : m));
      refreshThreads();
    } catch (e) {
      setMessages((cur) => cur.map((m) =>
        m.id === 'pending' ? { ...m, pending: false, error: String(e.message || e) } : m));
    }
    setSending(false);
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const visionPill = attachments.length > 0 && modelsInfo && (
    <Pill tone="idle">answers with {modelsInfo.vision_model}</Pill>
  );

  return (
    <div style={{ display: 'flex', gap: 16, height: 'calc(100vh - var(--topbar-h) - 48px)',
                  minHeight: 380 }}>
      {/* thread rail */}
      {railOpen && (
        <div style={{ width: 250, flexShrink: 0, display: 'flex', flexDirection: 'column',
                      gap: 8, minHeight: 0 }}>
          <Button variant="primary" icon="plus" onClick={newThread}>new chat</Button>
          <div style={{ flex: 1, overflowY: 'auto', display: 'flex',
                        flexDirection: 'column', gap: 2 }}>
            {threads.length === 0
              ? <div style={{ fontSize: 12, color: 'var(--fg-faint)', padding: 8 }}>
                  {threadsError && threadsError.deviceTokenRequired
                    ? 'paired devices only — pair this browser, or sign in, to read the chats'
                    : 'no chats yet'}
                </div>
              : threads.map((t) => (
                  <ChatThreadRow key={t.id} t={t} active={t.id === threadId}
                                 onSelect={selectThread} onDelete={deleteThread}/>
                ))}
          </div>
        </div>
      )}

      {/* conversation pane */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconButton name={railOpen ? 'panel-left-close' : 'panel-left-open'}
                      title={railOpen ? 'hide chats' : 'show chats'}
                      onClick={() => setRailOpen(!railOpen)}/>
          <div style={{ fontSize: 14, fontWeight: 600, flex: 1, minWidth: 0,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {(threads.find((t) => t.id === threadId) || {}).title || 'new chat'}
          </div>
          {modelsInfo && (
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
              {modelsInfo.default_model}
            </span>
          )}
        </div>

        <div ref={scrollRef}
             style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex',
                      flexDirection: 'column', gap: 14, padding: '4px 2px' }}>
          {threadId == null && messages.length === 0 ? (
            <Empty glyph="sleeping" title="ask anything"
                   sub="chats run on your own hardware — attach an image and the vision model reads it"/>
          ) : messages.map((m) => <ChatMessage key={m.id} m={m} threadId={threadId} onCopy={copyMessage}/>)}
        </div>

        {/* composer */}
        <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)',
                      background: 'var(--card)', boxShadow: 'var(--inner-highlight)',
                      padding: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {attachments.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              {attachments.map((a) => (
                <span key={a.token}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <img src={chatUploadUrl(a.token)} alt={a.name}
                       style={{ width: 44, height: 44, objectFit: 'cover',
                                borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }}/>
                  <IconButton name="x" title={`remove ${a.name}`}
                              onClick={() => setAttachments((cur) => cur.filter((x) => x.token !== a.token))}/>
                </span>
              ))}
              {visionPill}
            </div>
          )}
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={onKeyDown} rows={2}
                    placeholder="message the domovoi… (Enter to send, Shift+Enter for a new line)"
                    style={{ font: 'inherit', fontSize: 13, lineHeight: 1.5, resize: 'none',
                             border: 'none', outline: 'none', background: 'transparent',
                             color: 'var(--fg)' }}/>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input ref={fileRef} type="file" accept="image/*" multiple hidden
                   onChange={(e) => { attach(e.target.files); e.target.value = ''; }}/>
            <IconButton name="paperclip" title="attach images (up to 4)"
                        onClick={() => fileRef.current && fileRef.current.click()}/>
            <div style={{ flex: 1 }}/>
            <Button variant="primary" icon="send" onClick={send}
                    disabled={sending || !draft.trim()}>
              {sending ? 'thinking…' : 'send'}
            </Button>
          </div>
        </div>
      </div>
      {toastNode}
    </div>
  );
};

window.ChatPage = ChatPage;
