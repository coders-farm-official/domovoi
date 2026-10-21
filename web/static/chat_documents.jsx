/* Documents in home-server chats — the dashboard's half of
 * web/backend/api/chat_documents.py (documents plan, D1 "read and ask").
 *
 * The chat's paperclip takes pictures and documents alike: a picture goes
 * to /api/chat/uploads as before, anything else to /api/chat/documents,
 * which reads it on the server and answers with what it is ("lease.pdf ·
 * 30 pages · about 9,800 words") or a sentence saying why it can't
 * (an old .doc, a zip bomb, a scan with no text). A thread with documents
 * is answered from them; each reply carries `doc`: the passages the app
 * gave the model (only those can be cited), whether one answered, and
 * notes ("I couldn't find that in the document" and what to try).
 *
 * This file draws those pieces for chat.jsx (window.ChatDocs):
 *   * DocChip — a document on the composer or on the message that
 *     attached it, with its notes (scanned pages, formulas, long);
 *   * DocAnswer — under a reply: the notes, then the sources as chips; a
 *     cited passage reads at full strength, one only read stays faint;
 *   * PassageSheet — a source opened: its label, the passage with the
 *     sentence that matched marked, and for a PDF "open page N", which
 *     opens the file in a new tab at #page=N;
 *   * JobProgress — a long document read in parts (chat_doc_jobs.py, D2):
 *     "reading part 3 of 8 · about 1 min left", a bar of the parts read,
 *     and stop (what was read is kept: "Summary of pages 1-9 (stopped)");
 *     a stopped reply offers "carry on" (DocAnswer's onCarryOn).
 * Every top-level name starts ChatDoc: page scripts share one scope.
 */

const ChatDocs = (() => {
  const PICTURE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.heic', '.heif', '.avif', '.jxl', '.tif', '.tiff'];
  // What the file picker offers (the server decides what it reads, and
  // says so in words for anything else).
  const ACCEPT = 'image/*,.pdf,.docx,.odt,.xlsx,.csv,.tsv,.txt,.md,.markdown,.doc,.rtf,.xls,.ods';

  const extOf = (name) => {
    const m = /\.[^./\\]+$/.exec(String(name || '').toLowerCase());
    return m ? m[0] : '';
  };
  const isPicture = (file) => (file && String(file.type || '').startsWith('image/'))
    || PICTURE_EXTS.includes(extOf(file && file.name));

  const fileUrl = (token, page) => withDeviceToken(`${API_BASE}/api/chat/documents/${token}/file`)
    + (page ? `#page=${page}` : '');

  /* The sentence a refused document came back with (every refusal has
   * one), else a plain fallback. */
  const refusalText = (e) => {
    const b = e && e.detail;
    if (b && typeof b.detail === 'string' && b.detail.trim()) return b.detail.trim();
    if (b && b.detail && typeof b.detail.detail === 'string') return b.detail.detail;
    if (e && e.status === 413) return 'That file is too large for the chat.';
    return "That document couldn't be attached.";
  };

  const upload = async (file) => {
    const form = new FormData();
    form.append('file', file);
    return apiUpload('/api/chat/documents', form);
  };

  const Notes = ({ notes }) => (notes || []).length ? (
    <div className="chat-doc-notes">
      {notes.map((n, i) => <div key={`${n.code}-${i}`} className="chat-doc-note">{n.text}</div>)}
    </div>
  ) : null;

  const DocChip = ({ doc, onRemove, mine }) => (
    <div className={`chat-doc-chip${mine ? ' mine' : ''}`}>
      <div className="chat-doc-chip-row">
        <Icon name={doc.kind === 'xlsx' || doc.kind === 'csv' ? 'file-spreadsheet' : 'file-text'} size={14}/>
        <span className="chat-doc-name" title={doc.name}>{doc.name}</span>
        {onRemove && <IconButton name="x" title={`remove ${doc.name}`} onClick={onRemove}/>}
      </div>
      {doc.summary && <div className="chat-doc-summary mono">{doc.summary.split(' · ').slice(1).join(' · ')}</div>}
      <Notes notes={doc.notes}/>
    </div>
  );

  /* The passage's text with the matched sentence marked (as elements,
   * never HTML: it is text from somebody's file). */
  const Marked = ({ text, match }) => {
    const t = String(text || '');
    const i = match ? t.indexOf(match) : -1;
    if (i < 0) return <span>{t}</span>;
    return (
      <span>{t.slice(0, i)}<mark className="chat-doc-mark">{match}</mark>{t.slice(i + match.length)}</span>
    );
  };

  const PassageSheet = ({ source, onClose }) => {
    React.useEffect(() => {
      const key = (e) => { if (e.key === 'Escape') onClose(); };
      document.addEventListener('keydown', key);
      return () => document.removeEventListener('keydown', key);
    }, [onClose]);
    const pdf = extOf(source.name) === '.pdf' && source.page;
    return (
      <div className="chat-details-bg" onClick={onClose}>
        <div className="chat-details chat-passage" role="dialog" aria-modal="true"
             aria-label={`${source.label} of ${source.name}`} onClick={(e) => e.stopPropagation()}>
          <div className="chat-details-head">
            <Icon name="file-text" size={16}/>
            <strong>{source.label}</strong>
            <span className="chat-passage-of mono">{source.name}</span>
            <span style={{ flex: 1 }}/>
            <IconButton name="x" title="close" onClick={onClose}/>
          </div>
          <div className="chat-passage-text"><Marked text={source.text} match={source.match}/></div>
          <div className="chat-details-foot">
            {!source.cited && <span className="chat-passage-why">read for this answer, not cited</span>}
            <span style={{ flex: 1 }}/>
            {pdf && (
              <a className="btn" href={fileUrl(source.token, source.page)} target="_blank" rel="noopener">
                <Icon name="external-link" size={14}/> open page {source.page}
              </a>
            )}
            <Button variant="primary" onClick={onClose}>close</Button>
          </div>
        </div>
      </div>
    );
  };

  // A job is live while it reads or writes the summary (chat_doc_jobs.LIVE).
  const LIVE = ['running', 'reducing'];
  const isLive = (job) => !!job && LIVE.includes(job.state);
  const stopJob = (job) => apiPost(`/api/chat/doc-jobs/${job.id}/stop`, {});
  const resumeJob = (job) => apiPost(`/api/chat/doc-jobs/${job.id}/resume`, {});

  /* The progress of a long document's job: the server's own line (it
   * words the time left from measured speeds), a bar of the parts read,
   * and stop. */
  const JobProgress = ({ job, onStop }) => {
    const [stopping, setStopping] = React.useState(false);
    if (!job) return null;
    const pct = job.parts ? Math.round((100 * (job.read || 0)) / job.parts) : 0;
    const stop = async () => {
      setStopping(true);
      try { await onStop(job); } catch { /* the next poll says how it stands */ }
      setStopping(false);
    };
    const line = job.line || `reading part ${job.part || 1} of ${job.parts || 1}`;
    return (
      <div className="chat-doc-job" role="status" aria-live="polite">
        <div className="chat-doc-job-row">
          <span className="mono chat-doc-job-line">{line}</span>
          {onStop && (
            <Button icon="square" disabled={stopping} onClick={stop}
                    title="stop reading; what was read so far is kept">
              {stopping ? 'stopping…' : 'stop'}
            </Button>
          )}
        </div>
        <div className="chat-doc-job-bar" aria-hidden="true"><span style={{ width: `${pct}%` }}/></div>
      </div>
    );
  };

  /* "carry on" under a stopped summary: the job reads the parts it hasn't
   * and writes the whole summary as a new reply. */
  const CarryOn = ({ job, onCarryOn }) => {
    const [busy, setBusy] = React.useState(false);
    const [why, setWhy] = React.useState(null);
    const go = async () => {
      setBusy(true);
      setWhy(null);
      try { await onCarryOn(job); } catch (e) { setWhy(refusalText(e)); }
      setBusy(false);
    };
    return (
      <div className="chat-doc-carry">
        <Button icon="play" disabled={busy} onClick={go}>
          {busy ? 'starting…' : `carry on (${job.parts - job.read} part${job.parts - job.read === 1 ? '' : 's'} left)`}
        </Button>
        {why && <span className="chat-doc-ask-why" role="status">{why}</span>}
      </div>
    );
  };

  /* A job a restart interrupted (review round 1): it waits to be asked,
   * "this summary was interrupted — carry on?". */
  const JobInterrupted = ({ job, onCarryOn }) => (
    <div className="chat-doc-job" role="status">
      <span className="mono chat-doc-job-line">{job.line || 'this summary was interrupted — carry on?'}</span>
      {job.state === 'interrupted' && <CarryOn job={job} onCarryOn={onCarryOn}/>}
    </div>
  );

  const DocAnswer = ({ doc, stoppedJob, onCarryOn }) => {
    const [open, setOpen] = React.useState(null);
    const close = React.useCallback(() => setOpen(null), []);
    if (!doc) return null;
    // Cited first; one chip per label (two passages of one page overlap
    // by a sentence: the cited one, else the best, stands for both).
    const seen = new Set();
    const sources = (doc.sources || []).slice().sort((a, b) => (b.cited ? 1 : 0) - (a.cited ? 1 : 0))
      .filter((s) => (seen.has(s.label) ? false : (seen.add(s.label), true)));
    return (
      <div className="chat-doc-answer">
        <Notes notes={doc.notes}/>
        {sources.length > 0 && (
          <div className="chat-doc-sources" aria-label="sources">
            <span className="chat-doc-sources-label">{doc.found ? 'sources' : 'read'}</span>
            {sources.map((s, i) => (
              <button key={`${s.token}-${s.label}-${i}`} type="button"
                      className={`chat-doc-source${s.cited ? ' cited' : ''}`}
                      title={s.cited ? `cited: ${s.label} of ${s.name}` : `read, not cited: ${s.label} of ${s.name}`}
                      onClick={() => setOpen(s)}>
                {s.label}
              </button>
            ))}
          </div>
        )}
        {stoppedJob && onCarryOn && <CarryOn job={stoppedJob} onCarryOn={onCarryOn}/>}
        {open && <PassageSheet source={open} onClose={close}/>}
      </div>
    );
  };

  // The kinds the server's chat reads (domovoi/doc_extract KIND_BY_EXT).
  const READS = ['.pdf', '.docx', '.odt', '.xlsx', '.csv', '.tsv', '.txt', '.text', '.md', '.markdown'];
  const alwaysOn = () => true;

  /* "Ask about this document" on a Documents-library row: a new chat about
   * that file (POST /api/chat/threads/from-document), opened at #chat/<id>.
   * Only for the kinds Chat reads, and only while the household's AI
   * features are on (useAiFeatures, components.jsx, where the server has
   * the switch). */
  const AskButton = ({ relPath, name }) => {
    const ai = (typeof useAiFeatures === 'function' ? useAiFeatures : alwaysOn)();
    const [busy, setBusy] = React.useState(false);
    const [why, setWhy] = React.useState(null);
    if (!ai || !READS.includes(extOf(name || relPath))) return null;
    const ask = async () => {
      setBusy(true);
      setWhy(null);
      try {
        const r = await apiPost('/api/chat/threads/from-document', { rel_path: relPath });
        window.location.hash = `chat/${r.thread.id}`;
      } catch (e) {
        setWhy(refusalText(e));
      } finally {
        setBusy(false);
      }
    };
    return (
      <React.Fragment>
        <Button icon="message-square" disabled={busy} title={why || 'start a chat about this document'}
                onClick={ask}>{busy ? 'reading…' : 'Ask'}</Button>
        {why && <span className="chat-doc-ask-why" role="status">{why}</span>}
      </React.Fragment>
    );
  };

  return { ACCEPT, READS, isPicture, upload, refusalText, fileUrl, DocChip, DocAnswer, PassageSheet,
           AskButton, extOf, JobProgress, JobInterrupted, isLive, stopJob, resumeJob };
})();

window.ChatDocs = ChatDocs;
