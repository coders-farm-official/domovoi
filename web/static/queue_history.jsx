/* Settings → Household → Queue history (1.3.1, 2026-11-16).
 *
 * Looking back at what Up next picked, and why: every Up next session
 * still kept (a room's, a browser's, a phone's), newest first, and one
 * session's picks in order with the reason, the song each followed, and
 * how long it really played (from the listening history). A summary sits
 * on top: picks, played vs skipped, how many artists and how close two
 * picks of one artist came, the reason mix, the decades. Downloads as CSV
 * or JSON. Read-only: nothing here changes or deletes anything.
 *
 * Why here: Up next is set up next door in Music mix (the section links
 * here), it covers every room and phone rather than one player's queue,
 * and it is admin-only, like the other history sections.
 *
 * Addresses: #settings/queue-history (the sessions),
 * #settings/queue-history/<session id> (one session).
 *
 * Data (admin, web/backend/api/up_next_history.py):
 *   GET /api/music/up-next/history                      · sessions, paged
 *   GET /api/music/up-next/history/{id}                 · summary + picks, paged
 *   GET /api/music/up-next/history/{id}/download?format=csv|json
 */

const QH_API = '/api/music/up-next/history';
const QH_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const qhWhen = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined,
      { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch { return iso; }
};
const qhDay = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); } catch { return iso; }
};
const qhClock = (iso) => {
  if (!iso) return '';
  try { return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }); } catch { return ''; }
};
/* "12h 5m", "40m": how long a session ran (to its end, or its last activity). */
const qhMinutes = (min) => (min < 60 ? `${min}m` : `${Math.floor(min / 60)}h ${min % 60}m`);
const qhSpan = (from, to) => {
  if (!from || !to) return '';
  return qhMinutes(Math.max(0, Math.round((new Date(to) - new Date(from)) / 60000)));
};
const qhPlural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;
const qhWhere = (t) => (t && t.kind === 'room' ? `${t.name}` : `${(t && t.name) || 'a phone or browser'}`);
const qhSeed = (seed) => {
  if (!seed) return '—';
  const song = seed.title ? `${seed.title}${seed.artist ? ` — ${seed.artist}` : ''}` : '';
  const list = seed.list && seed.list.name ? `from ${seed.list.name}` : '';
  return [song, list].filter(Boolean).join(' · ') || '—';
};

/* Save a download with the admin's credentials (a plain link can't carry
 * the Bearer). The server names the file. */
const qhDownload = async (id, format, fire) => {
  try {
    const r = await apiFetchRaw(`${QH_API}/${encodeURIComponent(id)}/download?format=${format}`);
    if (!r.ok) throw new Error(`the server said ${r.status}`);
    const cd = r.headers.get('content-disposition') || '';
    const m = /filename="([^"]+)"/.exec(cd);
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = m ? m[1] : `up-next.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    if (!isAuthFailure(e)) fire(`couldn’t download: ${apiErrorText(e)}`, { tone: 'warn' });
  }
};

/* A list read page by page ("show more"), restarting when `path` changes. */
const useQhPages = (path, key) => {
  const [state, setState] = React.useState({ rows: [], body: null, error: null, loading: true, cursor: null });
  const load = React.useCallback(async (cursor) => {
    if (!path) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const sep = path.includes('?') ? '&' : '?';
      const body = await apiGet(`${path}${sep}limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
      setState((s) => ({
        rows: cursor ? s.rows.concat(body[key] || []) : (body[key] || []),
        body, error: null, loading: false, cursor: body.next_cursor || null,
      }));
    } catch (e) {
      setState((s) => ({ ...s, error: e, loading: false }));
    }
  }, [path, key]);
  React.useEffect(() => { load(null); }, [load]);
  return { ...state, more: () => load(state.cursor), reload: () => load(null) };
};

const QhNote = ({ children }) => (
  <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--fg-muted)' }}>{children}</div>
);

const QhSessions = () => {
  const { rows, body, error, loading, cursor, more, reload } = useQhPages(QH_API, 'sessions');
  const keptDays = (body && body.kept_days) || 30;
  const sub = `Every Up next still kept, newest first. A finished one is deleted ${keptDays} days after it ends.`;
  if (error) {
    return <Card title="Up next sessions" sub={sub}><QhNote>couldn’t load: {apiErrorText(error)}</QhNote></Card>;
  }
  return (
    <Card title="Up next sessions" sub={sub}
          action={<IconButton name="refresh-cw" title="refresh" onClick={reload}/>}>
      {!rows.length ? (
        loading ? <QhNote>loading…</QhNote>
          : <Empty title="no Up next yet" sub="Play a song in a room or on a phone, and the songs Up next adds show up here."/>
      ) : (
        <div className="qh-scroll">
          <table className="tbl qh-tbl">
            <thead><tr>
              <th>where</th><th>started</th><th>ran</th><th>started from</th>
              <th className="num">picks</th><th className="num">played</th><th className="num">skipped</th>
            </tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="qh-row" tabIndex={0} role="link"
                    aria-label={`${qhWhere(s.target)}, ${qhWhen(s.started_at)}`}
                    onClick={() => { window.location.hash = `settings/queue-history/${s.id}`; }}
                    onKeyDown={(e) => { if (e.key === 'Enter') window.location.hash = `settings/queue-history/${s.id}`; }}>
                  <td className="qh-nowrap">
                    {s.target.kind === 'room' ? <span className="room-chip">{s.target.name}</span> : s.target.name}
                    {s.person && <span className="qh-muted"> · {s.person.name}’s mix</span>}
                  </td>
                  <td className="mono">{qhWhen(s.started_at)}</td>
                  <td className="mono">
                    {s.ended_at ? qhSpan(s.started_at, s.ended_at) : <span>{qhSpan(s.started_at, s.last_active_at)} · still on</span>}
                  </td>
                  <td className="qh-clip">{qhSeed(s.seed)}</td>
                  <td className="num">{s.picks}</td>
                  <td className="num">{s.played}</td>
                  <td className="num">{s.skipped}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {cursor && (
        <div style={{ padding: '10px 16px' }}>
          <Button onClick={more} disabled={loading}>{loading ? 'loading…' : 'show more'}</Button>
        </div>
      )}
    </Card>
  );
};

/* "played 3:12", "skipped at 0:15", "removed", "not reached". */
const qhResult = (p) => {
  const l = p.listen;
  if (p.status === 'skipped') return l ? `skipped at ${fmtDur(l.played_sec)}` : 'skipped';
  if (p.status === 'played') return l ? `played ${fmtDur(l.played_sec)}` : 'played';
  return p.status_words || p.status;
};

const QhSummary = ({ s }) => {
  const rep = s.closest_repeat;
  const line = (label, value) => (
    <div className="qh-line"><div className="label">{label}</div><div>{value}</div></div>
  );
  return (
    <div className="qh-summary">
      {line('picks', <>
        {qhPlural(s.picks, 'pick')} · {s.played} played · {s.skipped} skipped
        {s.removed ? ` · ${s.removed} removed` : ''}{s.not_reached ? ` · ${s.not_reached} not reached` : ''}
        {s.listened_sec ? ` · ${qhMinutes(Math.round(s.listened_sec / 60))} listened` : ''}
      </>)}
      {line('artists', <>
        {qhPlural(s.distinct_artists, 'artist')}.{' '}
        {rep
          ? (rep.songs === 1
            ? `${rep.artist} came twice in a row (picks ${rep.from_seq} and ${rep.to_seq}).`
            : `The closest two picks of one artist were ${rep.songs} songs apart: ${rep.artist}, picks ${rep.from_seq} and ${rep.to_seq}.`)
          : 'No artist came twice.'}
      </>)}
      {line('why picked', s.reasons.length
        ? s.reasons.map((r) => `${r.words} ${r.count}`).join(' · ') : '—')}
      {line('decades', s.decades.length
        ? s.decades.map((d) => `${d.decade === 'unknown' ? 'year not known' : d.decade} ${d.count}`).join(' · ') : '—')}
    </div>
  );
};

const QhSession = ({ id, fire }) => {
  const { rows, body, error, loading, cursor, more } = useQhPages(`${QH_API}/${encodeURIComponent(id)}`, 'picks');
  const back = <a href="#settings/queue-history" className="home-link">← all sessions</a>;
  if (error) {
    return (
      <Card title="Up next session">
        <QhNote>{error.status === 404 ? 'This session isn’t kept any more.' : `couldn’t load: ${apiErrorText(error)}`} {back}</QhNote>
      </Card>
    );
  }
  if (!body) return <Card title="Up next session"><QhNote>loading…</QhNote></Card>;
  const sess = body.session;
  const title = `${qhWhere(sess.target)} · ${qhWhen(sess.started_at)}`;
  const ran = sess.ended_at
    ? `ran ${qhSpan(sess.started_at, sess.ended_at)}, ended ${qhWhen(sess.ended_at)}`
    : `still on, last active ${relTime(sess.last_active_at)}`;
  const sub = `${sess.mode_words} · started from ${qhSeed(sess.seed)}${sess.person ? ` · ${sess.person.name}’s mix` : ''} · ${ran}`;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>{back}</div>
      <Card title={title} sub={sub}>
        <div style={{ padding: '4px 16px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <QhSummary s={body.summary}/>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <Button icon="download" onClick={() => qhDownload(sess.id, 'csv', fire)}>download CSV</Button>
            <Button icon="download" onClick={() => qhDownload(sess.id, 'json', fire)}>download JSON</Button>
            <span className="qh-muted" style={{ fontSize: 12 }}>
              {sess.kept_until
                ? `Kept until ${qhDay(sess.kept_until)}, then deleted.`
                : 'Kept while it plays, and for 30 days after it ends.'}
            </span>
          </div>
        </div>
      </Card>
      <Card title="The picks, in order" sub="Why each song was picked, the song it followed, and how it went.">
        {!rows.length ? <Empty title="no picks" sub="This session hadn’t added any songs."/> : (
          <div className="qh-scroll">
            <table className="tbl qh-tbl">
              <thead><tr>
                <th className="num">#</th><th>song</th><th>year</th><th>why</th><th>after</th><th>how it went</th><th>when</th>
              </tr></thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.seq} className={p.status === 'removed' || p.status === 'queued' ? 'qh-faded' : ''}>
                    <td className="num mono">{p.seq}</td>
                    <td className="qh-clip">
                      <div>{p.title || '—'}</div>
                      <div className="qh-muted">{p.artist}</div>
                    </td>
                    <td className="mono">{p.year || '—'}</td>
                    <td className="qh-clip">
                      <div>{p.reason_label || p.reason}</div>
                      {p.why && <div className="qh-muted">{p.why}</div>}
                    </td>
                    <td className="qh-clip qh-muted">{p.seed ? `${p.seed.title || '—'}${p.seed.artist ? ` — ${p.seed.artist}` : ''}` : '—'}</td>
                    <td className="qh-nowrap">{qhResult(p)}</td>
                    <td className="mono">{p.listen ? qhClock(p.listen.started_at) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cursor && (
          <div style={{ padding: '10px 16px' }}>
            <Button onClick={more} disabled={loading}>{loading ? 'loading…' : `show more (${body.total - rows.length} left)`}</Button>
          </div>
        )}
      </Card>
    </div>
  );
};

const QueueHistoryPanel = ({ marks }) => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const id = (marks && marks[0] && QH_UUID.test(marks[0])) ? marks[0] : null;
  if (!admin) {
    return (
      <Card title="Queue history" sub="What Up next picked in each room and on each phone, and why.">
        <QhNote>
          Admins only.{' '}
          <button type="button" className="home-link"
                  onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</button>
        </QhNote>
      </Card>
    );
  }
  return (
    <div className="queue-history" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toastNode}
      {!id && (
        <div style={{ fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
          Open a session to see its picks in order. How Up next picks is set in <a href="#settings/music-mix">Music mix</a>.
        </div>
      )}
      {id ? <QhSession key={id} id={id} fire={fire}/> : <QhSessions/>}
    </div>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['queue-history'] = { Panel: QueueHistoryPanel };
