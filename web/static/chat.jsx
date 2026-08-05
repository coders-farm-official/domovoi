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
 * surface; assistant text renders as plain pre-wrap (no markdown lib in
 * the no-build bundle).
 */

const chatUploadUrl = (token) => `${API_BASE}/api/chat/uploads/${token}`;

/* SSE reader for the send endpoint: fetch + ReadableStream, calling
 * onDelta(text) per chunk and resolving with the final done payload. */
const chatSendStream = async (threadId, body, onDelta) => {
  const r = await fetch(`${API_BASE}/api/chat/threads/${threadId}/messages`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(typeof Auth !== 'undefined' ? Auth.headers() : {}) },
    body: JSON.stringify(body),
  });
  if (!r.ok || !r.body) throw new Error(`${r.status} ${r.statusText}`);
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

const ChatMessage = ({ m }) => {
  const isUser = m.role === 'user';
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
        <div style={{ maxWidth: '76%', padding: '9px 13px', fontSize: 13, lineHeight: 1.55,
                      background: 'var(--card)', border: '1px solid var(--border)',
                      borderRadius: 'var(--r-md)', whiteSpace: 'pre-wrap',
                      overflowWrap: 'break-word' }}>
          {m.content}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 10, maxWidth: '86%' }}>
          <span style={{ flexShrink: 0, marginTop: 3 }}><DomovoiGlyph size={14}/></span>
          <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap',
                        overflowWrap: 'break-word', minWidth: 0 }}>
            {m.content}
            {m.pending && <span className="mono" style={{ color: 'var(--fg-faint)' }}>▍</span>}
            {m.error && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--err)', marginTop: 4 }}>
                {m.error}
              </div>
            )}
            {m.model && !m.pending && (
              <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', marginTop: 4 }}>
                {m.model}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

/* ─── Page ──────────────────────────────────────────────────────────────── */

const ChatPage = () => {
  const [fire, toastNode] = useToast();
  const { items: threads, refresh: refreshThreads } =
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

  const loadMessages = async (id) => {
    try {
      const r = await apiGet(`/api/chat/threads/${id}/messages`);
      setMessages(r.messages || []);
    } catch { setMessages([]); }
  };

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
      { id: `u-${Date.now()}`, role: 'user', content, images },
      { id: 'pending', role: 'assistant', content: '', pending: true },
    ]);
    try {
      const { done, errorDetail } = await chatSendStream(id, { content, images }, (delta) => {
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
              ? <div style={{ fontSize: 12, color: 'var(--fg-faint)', padding: 8 }}>no chats yet</div>
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
          ) : messages.map((m) => <ChatMessage key={m.id} m={m}/>)}
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
