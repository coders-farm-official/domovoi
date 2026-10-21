/* Chat page — Claude-desktop-style threaded text chat, answered by the
 * local Ollama (web/backend/api/chat.py).
 *
 * Layout: thread list rail (collapsible on phones) + conversation pane +
 * composer. Sending POSTs the message and reads the reply as an SSE stream
 * (fetch + ReadableStream — the assistant bubble fills in live). Attach up
 * to 4 images per message; a message with images is answered by the vision
 * model (server-side switch), surfaced with a small pill in the composer.
 * When that model isn't installed (/api/chat/models `vision_installed:
 * false`) the paperclip says so instead of opening the picker, and a
 * reply's error reads as a plain sentence (ChatDetails.errorView), with
 * Settings → Models one press away for an admin.
 *
 * Documents (PDF, Word, OpenDocument, Excel, CSV, text, markdown) ride the
 * same paperclip to /api/chat/documents (chat_documents.jsx, ChatDocs); a
 * thread with documents is answered from them, each reply with its sources
 * and notes. #chat/<id> opens a thread (the Documents page's "Ask about
 * this document" lands there). A summary of a long document is read in
 * parts by a job on the server (chat_doc_jobs.py): the reply's place shows
 * its progress with stop while the send streams; a thread opened while one
 * runs shows it from the message list's `jobs`, polled until it ends; a
 * stopped summary offers "carry on".
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

/* What a send says when the household's AI features are off. */
const CHAT_AI_OFF_TEXT = 'AI chat is turned off on your home server';

/* SSE reader for the send endpoint: fetch + ReadableStream, calling
 * onDelta(text) per chunk and resolving with the final done payload. A
 * long document's job (chat_doc_jobs.py) sends `job` and `progress`
 * events first: onJob(view) for each. */
const chatSendStream = async (threadId, body, onDelta, onStatus, onJob) => {
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
  // The household turned AI chat off (Settings → Models): the server
  // answers 409 with X-Domovoi-Refusal: ai-off before any reply; say so
  // plainly instead of "409 Conflict" or an empty bubble.
  if (r.status === 409 && r.headers && r.headers.get && r.headers.get('X-Domovoi-Refusal') === 'ai-off') {
    throw new Error(CHAT_AI_OFF_TEXT);
  }
  if (!r.body) throw new Error(`${r.status} ${r.statusText}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let done = null;
  let errorDetail = null;
  let errorCode = null;
  let errorModel = null;
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
      // A document turn: the reply is being checked against the document
      // and arrives whole, checked (chat_documents.py).
      else if (event === 'status') { if (onStatus) onStatus(payload.state || ''); }
      else if (event === 'job' || event === 'progress') { if (onJob) onJob(payload); }
      else if (event === 'done') done = payload;
      else if (event === 'error') {
        errorDetail = payload.detail || 'model error';
        errorCode = payload.code || null;
        errorModel = payload.model || null;
      }
    }
  }
  return { done, errorDetail, errorCode, errorModel };
};

/* "open Settings → Models", for an admin, where a model is the fix. */
const ChatModelsLink = () => (
  <a href="#settings/models" className="chat-note-link">
    open Settings → Models
  </a>
);

/* A reply that failed: the plain sentence (with a code) as a note, the
 * link for an admin when a model is the fix; an error the server never
 * coded (the browser's own, "Failed to fetch") stays a small red line. */
const ChatReplyError = ({ m, admin }) => {
  const err = ChatDetails.errorView(m);
  if (!err) return null;
  if (!err.code) {
    return (
      <div className="mono" style={{ fontSize: 11, color: 'var(--err-ink)', marginTop: 4 }}>{err.text}</div>
    );
  }
  return (
    <div className="chat-note" role="note">
      <div>{err.text}</div>
      {err.models && admin && <ChatModelsLink/>}
    </div>
  );
};

/* The composer's note when a picture can't be read here: why, what chat
 * takes, and Settings → Models for an admin. */
const ChatPictureNote = ({ model, admin, onClose }) => (
  <div className="chat-note info" role="note">
    <div className="chat-note-row">
      <Icon name="image-off" size={14}/>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div>{ChatDetails.visionMissingText(model)}</div>
        <div className="chat-note-sub">{ChatDetails.ACCEPTS}</div>
        {admin && <ChatModelsLink/>}
      </div>
      <IconButton name="x" title="close" onClick={onClose}/>
    </div>
  </div>
);

/* ─── Thread list rail ──────────────────────────────────────────────────── */

/* One chat in the rail: its title (the first words, then the model's, or
 * the name someone gave it), its last words (or, in a search, the words
 * around the match), and rename / archive / delete — the same actions as
 * the app's history list (assistant plan part 7). */
const ChatThreadRow = ({ t, active, onSelect, onDelete, onRename, onArchive }) => {
  const stop = (fn) => (e) => { e.stopPropagation(); fn(t); };
  const snippet = t.match_snippet || t.last_snippet || 'no messages yet';
  return (
    <div onClick={() => onSelect(t)}
         style={{ padding: '9px 12px', borderRadius: 'var(--r-sm)', cursor: 'pointer',
                  background: active ? 'var(--brand-soft)' : 'transparent',
                  display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: active ? 600 : 500, overflow: 'hidden',
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {t.title || 'new chat'}
        </div>
        <div style={{ fontSize: 11, color: active ? 'var(--fg-muted)' : 'var(--fg-faint)', overflow: 'hidden',
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {t.archived ? 'archived · ' : ''}{snippet}
        </div>
      </div>
      <span className="mono" style={{ fontSize: 10, color: active ? 'var(--fg-muted)' : 'var(--fg-faint)', flexShrink: 0 }}>
        {liveRelTime(t.updated_at)}
      </span>
      <IconButton name="pencil" title="rename chat" aria-label="rename chat" onClick={stop(onRename)}/>
      <IconButton name={t.archived ? 'archive-restore' : 'archive'}
                  title={t.archived ? 'move back to chats' : 'archive chat'}
                  aria-label={t.archived ? 'move back to chats' : 'archive chat'}
                  onClick={stop(onArchive)}/>
      <IconButton name="trash-2" title="delete chat" aria-label="delete chat" onClick={stop(onDelete)}/>
    </div>
  );
};

/* What the rail lists: a search across every chat (archived ones too),
 * the archived chats, or the chats. */
const chatRailPath = (q, archived) => {
  const query = (q || '').trim();
  if (query) return `/api/chat/threads?q=${encodeURIComponent(query)}`;
  return archived ? '/api/chat/threads?archived=true' : '/api/chat/threads';
};

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
    const opened = Date.now();
    const away = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    const key = (e) => { if (e.key === 'Escape') onClose(); };
    // A scroll that lands with the tap itself (the list settling, the
    // browser bringing the button into view) must not shut the menu it
    // just opened; a scroll after that means the reader moved on.
    const scrolled = () => { if (Date.now() - opened > 250) onClose(); };
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', key);
    window.addEventListener('scroll', scrolled, true);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('pointerdown', away, true);
      document.removeEventListener('keydown', key);
      window.removeEventListener('scroll', scrolled, true);
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

const ChatMessage = ({ m, threadId, onCopy, admin, stoppedJob, onCarryOn }) => {
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
  // Who Domovoi heard, on a message said into the app's chat mic.
  const speaker = !m.pending ? ChatDetails.speakerMark(m) : null;

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
      {(m.documents || []).map((d) => (
        <ChatDocs.DocChip key={d.token} doc={d} mine={isUser}/>
      ))}
      {isUser && !m.content && (m.documents || []).length > 0 ? null : isUser ? (
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
            {m.pending && m.status === 'checking' && !m.content && (
              <span className="mono chat-doc-checking">reading the passages and checking the answer…</span>
            )}
            {m.pending && m.job && !m.content && (
              <ChatDocs.JobProgress job={m.job} onStop={ChatDocs.isLive(m.job) ? ChatDocs.stopJob : null}/>
            )}
            {m.pending && !m.job && <span className="mono" style={{ color: 'var(--fg-faint)' }}>▍</span>}
            {!m.pending && <ChatDocs.DocAnswer doc={m.doc} stoppedJob={stoppedJob} onCarryOn={onCarryOn}/>}
            <ChatReplyError m={m} admin={admin}/>
          </div>
        </div>
      )}
      {speaker && (
        <div className={`chat-speaker${speaker.soft ? ' soft' : ''}`}
             title={speaker.description} aria-label={speaker.description}>
          <Icon name="mic" size={11}/>
          {speaker.name && <Avatar name={speaker.name}/>}
          <span>{speaker.label}</span>
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

/* Below this page width the chat list and the conversation take turns
 * instead of sitting side by side (styles.css .chat-page.narrow): the
 * 250 px list plus its 16 px gap would leave the conversation under
 * 400 px. A phone, a Fold's inner screen beside the sidebar (604 px) and
 * a phone on its side (532 px at 812) are all narrow. */
const CHAT_TWO_PANE_MIN = 666;

/* The page's width before it has one to measure (the first render): the
 * shell's 16 px phone padding at 760 px and below, else the 232 px sidebar
 * and 24 px padding. Measured for real before the first paint. */
const chatGuessNarrow = () => {
  const w = window.innerWidth || 1280;
  return (w <= 760 ? w - 32 : w - 232 - 48) < CHAT_TWO_PANE_MIN;
};

/* true while `ref`'s element is narrower than CHAT_TWO_PANE_MIN. */
const useChatNarrow = (ref) => {
  const [narrow, setNarrow] = React.useState(chatGuessNarrow);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => {
      const w = el.getBoundingClientRect().width;
      if (w > 0) setNarrow(w < CHAT_TWO_PANE_MIN);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return narrow;
};

/* Chat while the household's AI features are off (Settings → Models): the
 * page is off every launcher then (navItemsFor), and a link or an old
 * bookmark that still lands here says why instead of offering a composer
 * whose every send would be refused. */
const ChatAiOff = () => (
  <div style={{ padding: 24 }}>
    <Empty title="chat is off"
           sub="AI chat and questions are turned off for this home. Voice commands still work. An admin can turn AI on in Settings → Models."
           action={<a href="#settings/models" className="home-link">Settings → Models</a>}/>
  </div>
);

const ChatPage = () => {
  const ai = useAiFeatures();
  return ai ? <ChatPageMain/> : <ChatAiOff/>;
};

const ChatPageMain = () => {
  const [fire, toastNode] = useToast();
  const copyMessage = async (text) => fire((await chatCopyText(text || '')) ? 'copied' : 'copy failed');
  const { items: threads, error: threadsError, refresh: refreshThreads } =
    useApiList('/api/chat/threads', { pickItems: (x) => x.threads, eventTypes: ['chat.changed'] });
  // The rail: the chats, the archived ones, or a search across them all.
  const [railQuery, setRailQuery] = React.useState('');
  const [showArchived, setShowArchived] = React.useState(false);
  const railQ = useDebouncedValue(railQuery, 250);
  const railPath = chatRailPath(railQ, showArchived);
  const railList = useApiList(railPath, { pickItems: (x) => x.threads, eventTypes: ['chat.changed'] });
  const railItems = railPath === '/api/chat/threads' ? threads : railList.items;
  const refreshAll = () => { refreshThreads(); railList.refresh(); };
  const { data: modelsInfo, refresh: refreshModels } = useApiObject('/api/chat/models');
  const admin = useAdminSignedIn();
  // Pictures can't be read here (the vision model isn't installed): the
  // paperclip opens this note instead of the picker.
  const visionMissing = ChatDetails.visionMissing(modelsInfo);
  const [pictureNote, setPictureNote] = React.useState(false);

  const [threadId, setThreadId] = React.useState(null);
  const [messages, setMessages] = React.useState([]);
  const [draft, setDraft] = React.useState('');
  const [attachments, setAttachments] = React.useState([]); // [{token, name}]
  // Documents staged for the next message (chat_documents.jsx): their metadata.
  const [docAttachments, setDocAttachments] = React.useState([]);
  const [docBusy, setDocBusy] = React.useState(0);
  const [sending, setSending] = React.useState(false);
  const pageRef = React.useRef(null);
  const narrow = useChatNarrow(pageRef);
  // Narrow: the list or the conversation, and a chat page opens on the
  // conversation (the "ask anything" state and the composer) with the
  // list one press away. Wide: the list beside it, as before.
  const [railOpen, setRailOpen] = React.useState(() => !chatGuessNarrow());
  const wasNarrow = React.useRef(narrow);
  React.useEffect(() => {
    // Crossing the line (a Fold opened or closed, a phone turned): the
    // list goes beside the conversation, or out of its way.
    if (wasNarrow.current !== narrow) setRailOpen(!narrow);
    wasNarrow.current = narrow;
  }, [narrow]);
  const scrollRef = React.useRef(null);
  const fileRef = React.useRef(null);
  const inputRef = React.useRef(null);

  // A thread's messages are read on the device tier: on a browser that is
  // not paired yet the read is refused, apiGet opens the pair modal, and
  // the refusal re-runs the read once a credential arrives. The thread
  // list is a useApiList and recovers on its own.
  const [messagesRefusal, setMessagesRefusal] = React.useState(null);
  // The thread's long-document jobs (chat_doc_jobs.py): running ones show
  // their progress, stopped ones let their reply carry on.
  const [jobs, setJobs] = React.useState([]);
  const loadMessages = async (id) => {
    try {
      const r = await apiGet(`/api/chat/threads/${id}/messages`);
      setMessages(r.messages || []);
      setJobs(r.jobs || []);
      setMessagesRefusal(null);
    } catch (e) {
      setMessages([]);
      setJobs([]);
      setMessagesRefusal(e && (e.status === 401 || e.status === 403
        || (e.status === 429 && e.deviceTokenWait)) ? e : null);
    }
  };
  const reloadMessages = React.useCallback(() => {
    if (threadId != null) loadMessages(threadId);
  }, [threadId]);
  useRetryAfterCredential(messagesRefusal, reloadMessages);

  // A job running in this thread that this page didn't start (the page was
  // left and opened again, another device asked): its progress, read every
  // two seconds until it ends, then the thread again (its reply is in).
  const sendingRef = React.useRef(false);
  const liveJobs = jobs.filter((j) => ChatDocs.isLive(j));
  const liveKey = liveJobs.map((j) => j.id).join(',');
  React.useEffect(() => {
    if (!liveKey || threadId == null) return undefined;
    let gone = false;
    const tick = async () => {
      const next = [];
      let ended = false;
      for (const j of liveJobs) {
        try {
          const v = await apiGet(`/api/chat/doc-jobs/${j.id}`);
          next.push(v);
          if (!ChatDocs.isLive(v)) ended = true;
        } catch { ended = true; }
      }
      if (gone) return;
      if (ended) { if (!sendingRef.current) loadMessages(threadId); return; }
      setJobs((cur) => cur.map((j) => next.find((v) => v.id === j.id) || j));
    };
    const timer = setInterval(tick, 2000);
    return () => { gone = true; clearInterval(timer); };
  }, [liveKey, threadId]);
  const carryOn = async (job) => {
    const v = await ChatDocs.resumeJob(job);
    setJobs((cur) => [...cur.filter((j) => j.id !== v.id), v]);
  };
  const stoppedJobs = jobs.filter((j) => j.state === 'stopped' || j.state === 'failed');
  // The newest reply of a stopped job is the one that offers carry on.
  const carryOnAt = {};
  messages.forEach((m) => {
    const j = m.doc && m.doc.job;
    if (j && stoppedJobs.some((s) => s.id === j.id)) carryOnAt[j.id] = m.id;
  });

  // A chat the help panel started ("ask Domovoi", help.jsx): open it with
  // the question typed in, for the person to send (or change) themselves.
  React.useEffect(() => {
    const takeHelp = () => {
      const h = window.DomovoiHelpHandoff && window.DomovoiHelpHandoff.take();
      if (!h) return;
      setThreadId(h.threadId);
      loadMessages(h.threadId);
      setDraft(h.draft || '');
      refreshThreads();
      if (inputRef.current) inputRef.current.focus();
    };
    takeHelp();
    window.addEventListener('domovoi-help-handoff', takeHelp);
    return () => window.removeEventListener('domovoi-help-handoff', takeHelp);
  }, []);

  // Narrow, the list and the conversation take turns (useChatNarrow), so
  // opening a chat puts the list away; the panel button brings it back.
  const hideRailOnPhone = () => {
    if (narrow) setRailOpen(false);
  };
  const selectThread = (t) => {
    setThreadId(t.id);
    loadMessages(t.id);
    hideRailOnPhone();
  };

  // #chat/<id>: open that thread (the Documents page's "Ask about this
  // document" makes one and lands here). The address goes back to #chat.
  React.useEffect(() => {
    const follow = () => {
      const m = /^#?chat\/(\d+)$/.exec(String(window.location.hash || ''));
      if (!m) return;
      selectThread({ id: Number(m[1]) });
      try { window.history.replaceState(null, '', '#chat'); } catch { /* the address stays */ }
      refreshThreads();
    };
    follow();
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);

  // The thread keeps its newest line in view: on a new message, and when
  // the thread itself gets shorter or taller (the keyboard coming up, the
  // composer growing a line, a phone turning) while the reader was at the
  // bottom. Scrolled up to read, it stays where it is.
  const atBottom = React.useRef(true);
  const onThreadScroll = (e) => {
    const el = e.currentTarget;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };
  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    atBottom.current = true;
  }, [messages]);
  React.useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    atBottom.current = true;   // a thread that just came back opens at its end
    const ro = new ResizeObserver(() => { if (atBottom.current) el.scrollTop = el.scrollHeight; });
    ro.observe(el);
    return () => ro.disconnect();
  }, [narrow, railOpen]);

  // Narrow, the composer is one row and its text grows with what is typed,
  // to five lines (styles.css caps it at 140 px and it scrolls past that).
  React.useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = '';
    if (narrow) el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [draft, narrow, railOpen]);

  const newThread = async () => {
    try {
      const t = await apiPost('/api/chat/threads', {});
      refreshThreads();
      setThreadId(t.id);
      setMessages([]);
      hideRailOnPhone();
    } catch { fire('could not create chat'); }
  };

  // The household's chats: a delete is for everyone (the app says the same).
  const deleteThread = async (t) => {
    if (!window.confirm(`Delete "${t.title || 'new chat'}"? Deletes it for everyone in the house. `
      + "This can't be undone.")) return;
    try {
      await apiDelete(`/api/chat/threads/${t.id}`);
      if (t.id === threadId) { setThreadId(null); setMessages([]); }
      refreshAll();
    } catch { fire('delete failed'); }
  };

  // A name set here is the person's: the model's title never replaces it.
  const renameThread = async (t) => {
    const name = window.prompt('rename chat', t.title || '');
    if (name == null) return;
    const title = name.trim();
    if (!title || title === t.title) return;
    try {
      await apiPatch(`/api/chat/threads/${t.id}`, { title });
      refreshAll();
    } catch { fire('rename failed'); }
  };

  const archiveThread = async (t) => {
    try {
      await apiPatch(`/api/chat/threads/${t.id}`, { archived: !t.archived });
      fire(t.archived ? 'moved back to chats' : 'archived');
      refreshAll();
    } catch { fire(t.archived ? 'could not move it back' : 'archive failed'); }
  };

  const attach = async (files) => {
    const all = Array.from(files);
    const pictures = all.filter((f) => ChatDocs.isPicture(f));
    const documents = all.filter((f) => !ChatDocs.isPicture(f));
    // A picture with no vision model to read it: the note says why.
    if (pictures.length && visionMissing) {
      setPictureNote(true);
      refreshModels();
    }
    for (const f of visionMissing ? [] : pictures.slice(0, 4 - attachments.length)) {
      const form = new FormData();
      form.append('file', f);
      try {
        const up = await apiUpload('/api/chat/uploads', form);
        setAttachments((cur) => [...cur, up]);
      } catch (e) {
        // The server's reason in words (a HEIC photo: what to do instead).
        fire(ChatDetails.uploadRefusalText(e.status, e.detail) || 'upload failed');
      }
    }
    for (const f of documents.slice(0, 4 - docAttachments.length)) {
      setDocBusy((n) => n + 1);
      try {
        const meta = await ChatDocs.upload(f);
        setDocAttachments((cur) => [...cur, meta]);
      } catch (e) {
        // Every refusal is a sentence: an old .doc, a scan, a zip bomb.
        fire(ChatDocs.refusalText(e));
      } finally {
        setDocBusy((n) => n - 1);
      }
    }
  };

  const send = async () => {
    const content = draft.trim();
    const docs = docAttachments;
    if ((!content && docs.length === 0) || sending || docBusy) return;
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
    setDocAttachments([]);
    setSending(true);
    sendingRef.current = true;
    setMessages((cur) => [
      ...cur,
      { id: `u-${Date.now()}`, role: 'user', content, images, documents: docs,
        created_at: new Date().toISOString(), device: 'this browser' },
      { id: 'pending', role: 'assistant', content: '', pending: true },
    ]);
    try {
      // device_id: which install sent it, for the message's details (V017).
      const body = { content, images, documents: docs.map((d) => ({ token: d.token })),
                     device_id: DeviceIdentity.id() };
      const { done, errorDetail, errorCode, errorModel } = await chatSendStream(id, body, (delta) => {
        setMessages((cur) => cur.map((m) =>
          m.id === 'pending' ? { ...m, content: m.content + delta } : m));
      }, (state) => {
        setMessages((cur) => cur.map((m) => (m.id === 'pending' ? { ...m, status: state } : m)));
      }, (job) => {
        // A long document read in parts: the reply's place shows how far.
        setMessages((cur) => cur.map((m) => (m.id === 'pending' ? { ...m, job } : m)));
      });
      setMessages((cur) => cur.map((m) =>
        m.id === 'pending'
          ? (done || { ...m, pending: false, error: errorDetail, error_code: errorCode,
                       model: errorModel || m.model })
          : m));
      refreshThreads();
      // Refused for want of the vision model: re-read what's installed, so
      // the paperclip explains it next time instead of opening the picker.
      const code = (done && done.stats && done.stats.error_code) || errorCode;
      if (code === 'vision_model_missing') refreshModels();
    } catch (e) {
      setMessages((cur) => cur.map((m) =>
        m.id === 'pending' ? { ...m, pending: false, error: String(e.message || e) } : m));
    }
    setSending(false);
    sendingRef.current = false;
    // A stopped summary can carry on: the thread's jobs, read again.
    if (id != null) {
      try { setJobs((await apiGet(`/api/chat/threads/${id}/messages`)).jobs || []); } catch { /* next open */ }
    }
  };

  // A screenshot pasted into the box attaches like the paperclip's
  // pictures (help: "paste or attach a screenshot").
  const onPaste = (e) => {
    const files = Array.from((e.clipboardData && e.clipboardData.files) || [])
      .filter((f) => /^image\//.test(f.type));
    if (!files.length) return;
    e.preventDefault();
    if (visionMissing) { setPictureNote(true); refreshModels(); return; }
    attach(files);
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const visionPill = attachments.length > 0 && modelsInfo && !visionMissing && (
    <Pill tone="idle">answers with {modelsInfo.vision_model}</Pill>
  );

  // The paperclip: the picker, or — when a picture would be refused —
  // the note saying why (and a fresh read, in case an admin has since
  // installed the model; the note goes once the read says so).
  // Documents are read on the server whatever the vision model, so the
  // picker always opens; a picture is the one thing the note may stop.
  const pickPictures = () => {
    setPictureNote(false);
    if (fileRef.current) fileRef.current.click();
  };
  const showPictureNote = visionMissing && (pictureNote || attachments.length > 0);

  // Narrow: the list fills the page while it is open, the conversation
  // otherwise. Wide: the conversation always, the list beside it.
  const showPane = !narrow || !railOpen;

  const textarea = (
    <textarea ref={inputRef} className="chat-input" value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown} onPaste={onPaste} rows={narrow ? 1 : 2} aria-label="message"
              placeholder={narrow ? 'message the domovoi…'
                : 'message the domovoi… (Enter to send, Shift+Enter for a new line)'}/>
  );
  const clip = (
    <IconButton name="paperclip"
                title={visionMissing
                  ? "attach documents (pictures need the vision model, which isn't installed)"
                  : 'attach pictures or documents (up to 4 of each)'}
                aria-label="attach pictures or documents"
                onClick={pickPictures}/>
  );
  const sendButton = (
    <Button variant="primary" icon="send" className="btn btn-primary chat-send" onClick={send}
            disabled={sending || docBusy > 0 || (!draft.trim() && docAttachments.length === 0)}
            aria-label={sending ? 'thinking…' : 'send'}
            title={narrow ? (sending ? 'thinking…' : 'send') : undefined}>
      <span className="chat-send-label">{sending ? 'thinking…' : 'send'}</span>
    </Button>
  );

  return (
    <div ref={pageRef} className={`chat-page${narrow ? ' narrow' : ''}`}>
      {/* thread rail */}
      {railOpen && (
        <div className="chat-rail">
          {narrow && (
            <div className="chat-rail-head">
              <span style={{ flex: 1 }}>chats</span>
              <IconButton name="panel-left-close" title="back to the conversation"
                          aria-label="back to the conversation" onClick={() => setRailOpen(false)}/>
            </div>
          )}
          <Button variant="primary" icon="plus" className="btn btn-primary chat-new"
                  onClick={newThread}>new chat</Button>
          <div className="chat-rail-search">
            <input type="search" value={railQuery} onChange={(e) => setRailQuery(e.target.value)}
                   placeholder="search chats…" aria-label="search chats" maxLength={200}/>
            <span className="chat-rail-search-icon"><Icon name="search" size={13}/></span>
          </div>
          {showArchived && !railQ.trim() && (
            <div className="chat-rail-head">
              <IconButton name="arrow-left" title="back to chats" aria-label="back to chats"
                          onClick={() => setShowArchived(false)}/>
              <span style={{ flex: 1 }}>archived</span>
            </div>
          )}
          <div className="chat-rail-list">
            {railItems.length === 0
              ? <div style={{ fontSize: 12, color: 'var(--fg-faint)', padding: 8 }}>
                  {threadsError && threadsError.deviceTokenRequired
                    ? 'paired devices only — pair this browser, or sign in, to read the chats'
                    : railQ.trim() ? (railList.error ? "couldn't search" : 'no chats match')
                    : showArchived ? 'no archived chats' : 'no chats yet'}
                </div>
              : railItems.map((t) => (
                  <ChatThreadRow key={t.id} t={t} active={t.id === threadId}
                                 onSelect={selectThread} onDelete={deleteThread}
                                 onRename={renameThread} onArchive={archiveThread}/>
                ))}
            {!showArchived && !railQ.trim() && (
              <button type="button" className="chat-rail-archived" onClick={() => setShowArchived(true)}>
                <Icon name="archive" size={13}/> archived
              </button>
            )}
          </div>
        </div>
      )}

      {/* conversation pane */}
      {showPane && (
      <div className="chat-pane">
        <div className="chat-head">
          <IconButton name={railOpen ? 'panel-left-close' : 'panel-left-open'}
                      title={railOpen ? 'hide chats' : 'show chats'}
                      aria-label={railOpen ? 'hide chats' : 'show chats'}
                      onClick={() => setRailOpen(!railOpen)}/>
          <div className="chat-head-title">
            {(threads.find((t) => t.id === threadId) || {}).title || 'new chat'}
          </div>
          {modelsInfo && (
            <span className="mono chat-head-model">{modelsInfo.default_model}</span>
          )}
        </div>

        <div ref={scrollRef} className="chat-thread" onScroll={onThreadScroll}>
          {threadId == null && messages.length === 0 ? (
            <Empty glyph="sleeping" title="ask anything"
                   sub="chats run on your own hardware — attach a document to ask about it, or an image for the vision model"
                   action={narrow && threads.length > 0
                     ? <Button icon="panel-left-open" onClick={() => setRailOpen(true)}>
                         your chats ({threads.length})
                       </Button>
                     : null}/>
          ) : messages.map((m) => (
            <ChatMessage key={m.id} m={m} threadId={threadId} onCopy={copyMessage} admin={admin}
                         stoppedJob={m.doc && m.doc.job && carryOnAt[m.doc.job.id] === m.id
                           ? stoppedJobs.find((j) => j.id === m.doc.job.id) : null}
                         onCarryOn={carryOn}/>
          ))}
          {!sending && liveJobs.map((j) => (
            <div key={j.id} style={{ display: 'flex', gap: 10, maxWidth: '86%' }}>
              <span style={{ flexShrink: 0, marginTop: 3 }}><DomovoiGlyph size={14}/></span>
              <ChatDocs.JobProgress job={j} onStop={async (job) => {
                const v = await ChatDocs.stopJob(job);
                setJobs((cur) => cur.map((x) => (x.id === v.id ? v : x)));
                loadMessages(threadId);
              }}/>
            </div>
          ))}
        </div>

        {/* composer */}
        <div className="chat-composer">
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
          {(docAttachments.length > 0 || docBusy > 0) && (
            <div className="chat-doc-staged" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              {docAttachments.map((d) => (
                <ChatDocs.DocChip key={d.token} doc={d}
                                  onRemove={() => setDocAttachments((cur) => cur.filter((x) => x.token !== d.token))}/>
              ))}
              {docBusy > 0 && <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', alignSelf: 'center' }}>reading the document…</span>}
            </div>
          )}
          {showPictureNote && (
            <ChatPictureNote model={modelsInfo.vision_model} admin={admin}
                             onClose={() => setPictureNote(false)}/>
          )}
          <input ref={fileRef} type="file" accept={ChatDocs.ACCEPT} multiple hidden
                 onChange={(e) => { attach(e.target.files); e.target.value = ''; }}/>
          {narrow ? (
            <div className="chat-composer-row">{clip}{textarea}{sendButton}</div>
          ) : (
            <React.Fragment>
              {textarea}
              <div className="chat-composer-row">
                {clip}
                <div style={{ flex: 1 }}/>
                {sendButton}
              </div>
            </React.Fragment>
          )}
        </div>
      </div>
      )}
      {toastNode}
    </div>
  );
};

window.ChatPage = ChatPage;
