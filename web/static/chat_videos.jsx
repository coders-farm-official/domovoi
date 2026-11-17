/* Videos in home-server chats — the dashboard's half of
 * web/backend/api/chat_videos.py and the core's domovoi/chat_video.
 *
 * The chat's paperclip takes one short video a message (MP4, MOV or WebM,
 * up to 3 minutes and 2 GB) when the server can watch one
 * (/api/chat/models → video.available: AI features on and ffmpeg there);
 * otherwise videos aren't offered at all. A clip over 3 minutes is refused
 * here before anything is sent ("Videos up to 3 minutes can be attached"),
 * and the server checks again with ffprobe.
 *
 * The file goes up as the request body (XHR, for the progress bar). Sent
 * with a message, the server queues a job and the reply says so at once;
 * the attachment then shows "Watching your video… about 4 minutes left"
 * (polled from /api/chat/video-jobs/{id}), "paused while someone talks to
 * Domovoi" during a voice turn, and stop. When it ends, Domovoi's summary
 * is in the chat, and "what was kept" opens the transcript and what each
 * frame might show. The video itself is gone by then: only text is kept.
 *
 * This file draws those pieces for chat.jsx (window.ChatVideos). Every
 * top-level name starts ChatVideo: page scripts share one scope.
 */

const ChatVideos = (() => {
  const EXTS = ['.mp4', '.m4v', '.mov', '.webm'];

  const extOf = (name) => {
    const m = /\.[^./\\]+$/.exec(String(name || '').toLowerCase());
    return m ? m[0] : '';
  };
  const isVideo = (file) => (file && String(file.type || '').startsWith('video/'))
    || EXTS.includes(extOf(file && file.name));
  const available = (modelsInfo) => !!(modelsInfo && modelsInfo.video && modelsInfo.video.available);
  const isLive = (job) => !!job && (job.state === 'queued' || job.state === 'running');

  const clock = (s) => {
    const t = Math.max(0, Math.round(Number(s) || 0));
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
  };
  const sizeText = (bytes) => {
    const mb = (Number(bytes) || 0) / (1024 * 1024);
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
  };

  /* The clip's length, read by the browser before anything is sent (null
   * when it can't tell: the server's ffprobe decides then). */
  const durationOf = (file) => new Promise((resolve) => {
    let url = null;
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      if (url) URL.revokeObjectURL(url);
      resolve(v);
    };
    try {
      const v = document.createElement('video');
      v.preload = 'metadata';
      v.onloadedmetadata = () => finish(Number.isFinite(v.duration) ? v.duration : null);
      v.onerror = () => finish(null);
      url = URL.createObjectURL(file);
      v.src = url;
      setTimeout(() => finish(null), 5000);
    } catch { finish(null); }
  });

  /* The sentence a refused video came back with, else a plain fallback. */
  const refusalText = (e) => {
    const b = e && e.body;
    if (b && typeof b.detail === 'string' && b.detail.trim()) return b.detail.trim();
    if (e && e.status === 413) return 'Videos up to 2 GB can be attached.';
    if (e && e.status === 409) return CHAT_AI_OFF_TEXT;
    return (e && e.message) || "That video couldn't be attached.";
  };

  /* One video, streamed as the body. XHR rather than fetch: only XHR
   * reports upload progress. Same headers apiFetch sends. */
  const upload = (file, onProgress) => {
    const xhr = new XMLHttpRequest();
    const promise = new Promise((resolve, reject) => {
      const q = new URLSearchParams({ name: file.name || 'video.mp4' });
      xhr.open('POST', `${API_BASE}/api/chat/videos?${q.toString()}`);
      xhr.withCredentials = true;
      const h = apiHeaders();
      Object.keys(h).forEach((k) => { if (h[k] != null) xhr.setRequestHeader(k, h[k]); });
      xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        let body = null;
        try { body = JSON.parse(xhr.responseText); } catch { /* plain text */ }
        if (xhr.status >= 200 && xhr.status < 300) { resolve(body || {}); return; }
        const err = new Error((body && typeof body.detail === 'string' && body.detail) || `${xhr.status}`);
        err.status = xhr.status;
        err.body = body;
        reject(err);
      };
      xhr.onerror = () => reject(new Error('the connection to the server dropped'));
      xhr.onabort = () => reject(Object.assign(new Error('cancelled'), { aborted: true }));
      xhr.send(file);
    });
    return { promise, abort: () => { try { xhr.abort(); } catch { /* done already */ } } };
  };

  const getJob = (id) => apiGet(`/api/chat/video-jobs/${id}`);
  const stopJob = (job) => apiPost(`/api/chat/video-jobs/${job.id}/cancel`, {});

  /* A video on the composer: uploading (with its bar), or ready to send. */
  const StagedChip = ({ item, onRemove }) => (
    <div className="chat-doc-chip chat-video-chip">
      <div className="chat-doc-chip-row">
        <Icon name="film" size={14}/>
        <span className="chat-doc-name" title={item.name}>{item.name}</span>
        {onRemove && <IconButton name="x" title={`remove ${item.name}`} onClick={onRemove}/>}
      </div>
      {item.uploading ? (
        <React.Fragment>
          <div className="chat-doc-summary mono">sending… {Math.round((item.pct || 0) * 100)}%</div>
          <div className="chat-doc-job-bar" aria-hidden="true">
            <span style={{ width: `${Math.round((item.pct || 0) * 100)}%` }}/>
          </div>
        </React.Fragment>
      ) : (
        <div className="chat-doc-summary mono">
          {[clock(item.duration_s), sizeText(item.size),
            item.estimate_text ? `watching takes ${item.estimate_text}` : null].filter(Boolean).join(' · ')}
        </div>
      )}
    </div>
  );

  /* What was kept from a watched video: the summary, the transcript with
   * times, and what each frame might show. */
  const Kept = ({ job }) => {
    const [data, setData] = React.useState(null);
    const [failed, setFailed] = React.useState(false);
    React.useEffect(() => {
      let gone = false;
      apiGet(`/api/chat/video-jobs/${job.id}/results`)
        .then((r) => { if (!gone) setData(r); })
        .catch(() => { if (!gone) setFailed(true); });
      return () => { gone = true; };
    }, [job.id]);
    if (failed) return <div className="chat-doc-note">couldn't load what was kept</div>;
    if (!data) return <div className="chat-doc-note mono">loading…</div>;
    return (
      <div className="chat-video-kept">
        {data.transcript.length > 0 ? (
          <div>
            <div className="chat-video-kept-head">what was said</div>
            {data.transcript.map((s, i) => (
              <div key={i} className="chat-video-kept-line">
                <span className="mono">{clock(s.start_s)}</span><span>{s.text}</span>
              </div>
            ))}
          </div>
        ) : <div className="chat-doc-note">no speech was heard</div>}
        {data.frames.length > 0 && (
          <div>
            <div className="chat-video-kept-head">what the pictures might show</div>
            {data.frames.map((f, i) => (
              <div key={i} className="chat-video-kept-line">
                <span className="mono">{clock(f.at_s)}</span><span>might show: {f.might_show}</span>
              </div>
            ))}
            <div className="chat-doc-note">these are a small model's guesses, and can be wrong</div>
          </div>
        )}
        <div className="chat-doc-note">the video itself was deleted after it was watched</div>
      </div>
    );
  };

  /* A sent video: its progress while it is watched (with stop), then how
   * it ended, and what was kept. */
  const JobChip = ({ job, onStop }) => {
    const [stopping, setStopping] = React.useState(false);
    const [open, setOpen] = React.useState(false);
    if (!job) return null;
    const live = isLive(job);
    const p = job.progress || {};
    const units = (p.pieces || 0) + (p.frames || 0) + 1;
    const doneUnits = (p.pieces_done || 0) + (p.frames_done || 0);
    const pct = job.state === 'running' ? Math.min(95, Math.round((100 * doneUnits) / Math.max(units, 1))) : 0;
    const stop = async () => {
      setStopping(true);
      try { await onStop(job); } catch { /* the next poll says how it stands */ }
      setStopping(false);
    };
    return (
      <div className="chat-doc-chip mine chat-video-chip" role="status" aria-live="polite">
        <div className="chat-doc-chip-row">
          <Icon name="film" size={14}/>
          <span className="chat-doc-name" title={job.name}>{job.name}</span>
          <span className="mono chat-doc-summary">{clock(job.duration_s)}</span>
        </div>
        <div className="chat-doc-job-row">
          <span className="mono chat-doc-job-line">{job.line}</span>
          {live && onStop && !job.cancel_requested && (
            <Button icon="square" disabled={stopping} onClick={stop}
                    title="stop watching; the video is deleted">
              {stopping ? 'stopping…' : 'stop'}
            </Button>
          )}
          {job.state === 'done' && (
            <Button icon={open ? 'chevron-up' : 'chevron-down'} onClick={() => setOpen((o) => !o)}>
              what was kept
            </Button>
          )}
        </div>
        {job.state === 'running' && (
          <div className="chat-doc-job-bar" aria-hidden="true"><span style={{ width: `${pct}%` }}/></div>
        )}
        {job.state === 'failed' && job.error && <div className="chat-doc-note">{job.error}</div>}
        {open && <Kept job={job}/>}
      </div>
    );
  };

  return { isVideo, isLive, available, durationOf, upload, refusalText, getJob, stopJob, clock,
           StagedChip, JobChip };
})();

window.ChatVideos = ChatVideos;
