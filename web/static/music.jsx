/* Music page — Library / Player / Playlists / Stats / Jobs tabs.
 * Provider-specific search & download UIs live on PLUGIN pages (design
 * §10.1) — this page stays generic: "Add music" enqueues rows in the
 * provider-agnostic media-acquisition queue, and the Jobs tab is a
 * small readout of that queue.
 * Now Playing strip · tabbed lower section · side drawer · search · pagination
 *
 * Data sources:
 *   * GET /api/music/library                    — server-paginated track page.
 *                                                 Returns {total, items}. Library
 *                                                 tab drives q/source/sort/offset/
 *                                                 limit via URL; q is debounced.
 *   * GET /api/music/library/stats              — whole-library aggregates for the
 *                                                 Stats tab and the tab badge,
 *                                                 computed server-side so we never
 *                                                 iterate a huge in-memory list.
 *   * GET /api/acquisitions                     — generic acquisition queue rows
 *                                                 + fulfiller availability (Jobs).
 *   * GET /api/music/now-playing                — per-room MPD state.
 *   * /ws/state · `music.now_playing.changed`   — push refresh of NP strip.
 *   * /ws/state · `acquisitions.changed`        — push refresh of the Jobs tab.
 *   * /ws/state · `library.indexer.changed`     — refetch library page + stats
 *                                                 (an indexer sweep added rows,
 *                                                 e.g. after an upload / rescan).
 */

/* ---- helpers ----------------------------------------------- */
const truncMid = (s, max = 56) => s.length <= max ? s : s.slice(0, Math.floor(max/2)) + '…' + s.slice(-Math.floor(max/2));
const fmtBigDur = (sec) => {
  const h = Math.floor(sec / 3600); const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
};

/* Knob the room set comes from the now-playing fetch — every room
 * with an mpd_rooms row shows up. Falls back to a minimal default
 * so the play-in-room chips and the download modal still render
 * when the API is briefly unreachable. */
const useKnownRooms = (nowPlaying) => {
  const fromNP = (nowPlaying || []).map(np => np.room_id);
  if (fromNP.length) return fromNP;
  return ['kitchen'];
};

/* Server-paginated library page. Encapsulates q/source/sort/page
 * state, debounces q so each keystroke doesn't fire a request, and
 * builds the URL the backend's /api/music/library expects. Mutating
 * a filter always resets to page 0 so the user doesn't end up on a
 * page that no longer exists. Page is clamped back to 0 in an effect
 * if the current offset falls off the end of the filtered total
 * (e.g. user deleted enough rows from page 4 that page 4 is empty). */
const useLibraryPage = ({ pageSize = 12 } = {}) => {
  const [q, setQRaw] = React.useState('');
  const [source, setSourceRaw] = React.useState('all');
  const [sort, setSortRaw] = React.useState('added_desc');
  const [favoritedOnly, setFavoritedOnlyRaw] = React.useState(false);
  const [page, setPage] = React.useState(0);

  const setQ      = React.useCallback((v) => { setQRaw(v);      setPage(0); }, []);
  const setSource = React.useCallback((v) => { setSourceRaw(v); setPage(0); }, []);
  const setSort   = React.useCallback((v) => { setSortRaw(v);   setPage(0); }, []);
  const setFavoritedOnly = React.useCallback(
    (v) => { setFavoritedOnlyRaw(!!v); setPage(0); }, []);

  const qDebounced = useDebouncedValue(q, 250);

  const path = React.useMemo(() => {
    const params = new URLSearchParams();
    if (qDebounced) params.set('q', qDebounced);
    if (source && source !== 'all') params.set('source', source);
    if (favoritedOnly) params.set('favorited', 'true');
    params.set('sort', sort);
    params.set('limit', String(pageSize));
    params.set('offset', String(page * pageSize));
    return `/api/music/library?${params.toString()}`;
  }, [qDebounced, source, favoritedOnly, sort, pageSize, page]);

  const { data, loading, refresh } =
    useApiObject(path, { eventTypes: ['library.indexer.changed'] });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));

  React.useEffect(() => {
    if (total > 0 && page >= pages) setPage(pages - 1);
  }, [total, page, pages]);

  return {
    items, total, pages, loading, refresh,
    q, source, sort, favoritedOnly, page, pageSize,
    setQ, setSource, setSort, setFavoritedOnly, setPage,
  };
};

/* ---- Now Playing card ------------------------------------- */
const NPCard = ({ np, tick, onPlayRandom, onPause, onResume, onSkip, onStop, onFavorite }) => {
  const playing = np.state === 'play' && np.song;
  const paused = np.state === 'pause' && np.song;
  const songDur = np.song?.duration_sec ?? 0;
  const elapsed = (playing || paused) ? (np.elapsed_sec ?? 0) + (playing ? tick : 0) : 0;
  const progress = songDur > 0 ? Math.min(100, (elapsed / songDur) * 100) : 0;
  return (
    <div className="card" style={{ padding: 0 }}>
      <div style={{ padding: '14px 16px', display: 'grid', gridTemplateColumns: '52px 1fr', gap: 12, alignItems: 'center' }}>
        <div style={{ width: 52, height: 52, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                      background: (playing || paused) ? 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))' : 'var(--sunken)' }}/>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <RoomChip name={np.room_id} online/>
            {playing && <Pill tone="live" live>live</Pill>}
            {paused && <Pill tone="idle">paused</Pill>}
            {/* Generic now-playing source pill (design §4.7/§10.1): a
                provider plugin's stamp supplies source + optional URL;
                the label stays provider-agnostic. */}
            {(playing || paused) && np.source_url && (
              <a href={np.source_url} target="_blank" rel="noopener noreferrer"
                 title={`open source${np.source ? ` (${np.source})` : ''}`}
                 style={{
                   display: 'inline-flex', alignItems: 'center', gap: 4,
                   fontSize: 11, lineHeight: 1, padding: '3px 8px',
                   borderRadius: 'var(--r-full)', textDecoration: 'none',
                   color: 'var(--brand-press)', background: 'var(--brand-soft)',
                   border: '1px solid var(--border)',
                 }}>
                <Icon name="external-link" size={11}/>open source ↗
              </a>
            )}
            {(playing || paused) && !np.source_url && np.source && (
              <Pill tone="idle">{np.source}</Pill>
            )}
          </div>
          {(playing || paused) ? (
            <div style={{ fontSize: 14, fontWeight: 500, letterSpacing: '-0.005em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {np.song.title
                || (np.song.file?.startsWith('http') ? 'online stream' : np.song.file?.split('/').pop())
                || 'unknown'}
              {np.song.artist && <span style={{ color: 'var(--fg-muted)', fontWeight: 400 }}> · {np.song.artist}</span>}
            </div>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>nothing playing in {np.room_id}</div>
          )}
        </div>
      </div>
      {(playing || paused) ? (
        <div style={{ padding: '0 16px 12px' }}>
          <div style={{ height: 4, borderRadius: 2, background: 'var(--sunken)', overflow: 'hidden', marginBottom: 8 }}>
            <div style={{ height: '100%', width: `${progress}%`, background: 'var(--brand)' }}/>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', flex: 1 }}>
              {fmtDur(elapsed)} {songDur ? `/ ${fmtDur(songDur)}` : ''}
            </div>
            <IconButton name="skip-back" disabled/>
            <IconButton name={playing ? 'pause' : 'play'} onClick={() => playing ? onPause(np.room_id) : onResume(np.room_id)}/>
            <IconButton name="skip-forward" onClick={() => onSkip(np.room_id)}/>
            <IconButton name="square" onClick={() => onStop(np.room_id)}/>
            <IconButton name="heart" onClick={() => onFavorite(np.room_id)}
                        style={np.favorited ? { color: 'var(--brand)' } : undefined}
                        title={np.favorited
                          ? 'already favorited'
                          : 'favorite (library track = save · external stream = fetch into library)'}/>
          </div>
        </div>
      ) : (
        <div style={{ padding: '0 16px 14px', display: 'flex', justifyContent: 'flex-end' }}>
          <Button icon="shuffle" onClick={() => onPlayRandom(np.room_id)}>play something</Button>
        </div>
      )}
    </div>
  );
};

/* ---- Drawer (track detail) -------------------------------- */
const Drawer = ({ track, rooms, onClose, onDelete, onPlayInRoom, onBrowserPlay, onQueueTrack }) => {
  const [alsoFile, setAlsoFile] = React.useState(false);
  const [room, setRoom] = React.useState(rooms[0] || 'kitchen');
  React.useEffect(() => {
    setAlsoFile(false);
    setRoom(rooms[0] || 'kitchen');
  }, [track?.id, rooms.join(',')]);
  if (!track) return null;
  return (
    <>
      <div onClick={onClose}
           style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.16)',
                    backdropFilter: 'blur(2px)', zIndex: 40 }}/>
      <aside style={{ position: 'fixed', top: 0, right: 0, height: '100vh', width: 420, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 41,
                      display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border)' }}>
          <div className="eyebrow">track · #{track.id}</div>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div style={{ padding: 16, display: 'flex', gap: 14, alignItems: 'center', borderBottom: '1px solid var(--border-soft)' }}>
          <div style={{ width: 72, height: 72, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                        background: 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))' }}/>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{track.title || 'unknown title'}</div>
            <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>{track.artist || 'unknown artist'}</div>
            <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>{track.album || '—'}</div>
          </div>
        </div>

        <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 8, fontSize: 12 }}>
          <div className="label">duration</div>      <div className="mono">{fmtDur(track.duration_sec)}</div>
          <div className="label">source</div>        <div><Pill tone={track.source ? 'live' : 'idle'}>{track.source || 'manual'}</Pill></div>
          <div className="label">source id</div>     <div className="mono" style={{ color: 'var(--fg-muted)' }}>{track.source_id || '—'}</div>
          <div className="label">added</div>         <div className="mono">{relTime(track.added_at)}</div>
          <div className="label">added via</div>     <div><Pill tone={track.added_via === 'voice' ? 'live' : 'idle'}>{track.added_via}</Pill></div>
          <div className="label">enriched</div>      <div className="mono">{track.enriched_at ? relTime(track.enriched_at) : '—'}</div>
          <div className="label">path</div>          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', wordBreak: 'break-all' }}>{track.file_path}</div>
        </div>

        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)' }}>
          <div className="label" style={{ marginBottom: 6 }}>play in room</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {rooms.map(r => (
              <button key={r} onClick={() => setRoom(r)}
                style={{ font: 'inherit', fontSize: 12, cursor: 'pointer',
                         padding: '4px 10px', borderRadius: 'var(--r-full)',
                         border: '1px solid var(--border)',
                         background: room === r ? 'var(--brand-soft)' : 'var(--card)',
                         color: room === r ? 'var(--brand-press)' : 'var(--fg)' }}>{r}</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <Button variant="primary" icon="play" onClick={() => onPlayInRoom(track, room)}>play in {room}</Button>
            <Button icon="headphones" onClick={() => { onBrowserPlay && onBrowserPlay(track); }}>play here</Button>
            <Button icon="list-plus" onClick={() => { onQueueTrack && onQueueTrack(track); }}>queue</Button>
            <Button icon="download" title="save to this device"
                    onClick={() => deviceDownload(`/api/music/library/${track.id}/audio?download=1`)}>save</Button>
          </div>
        </div>

        <div style={{ marginTop: 'auto', padding: 16, borderTop: '1px solid var(--border)', background: 'var(--sunken)' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--fg-muted)', marginBottom: 10, cursor: 'pointer' }}>
            <input type="checkbox" checked={alsoFile} onChange={e => setAlsoFile(e.target.checked)}/>
            also delete file on disk
          </label>
          <Button variant="secondary" icon="trash-2" onClick={() => onDelete(track, alsoFile)}
                  style={{ background: 'var(--err-soft)', color: 'var(--err)', borderColor: 'transparent' }}>
            delete track
          </Button>
        </div>
      </aside>
    </>
  );
};

/* ---- Library tab ------------------------------------------ */
const LibraryTab = ({ lib, libraryTotal, sourceOptions, onSelect, onToggleFavorite, onAddToPlaylist, playlists, onBulkAddToPlaylist, onBrowserPlay, onQueueTrack, onPlayNextTrack, fire }) => {
  const {
    items, total, pages, loading,
    q, source, sort, favoritedOnly, page,
    setQ, setSource, setSort, setFavoritedOnly, setPage,
  } = lib;
  const filtered = !!q || source !== 'all' || favoritedOnly;

  const [selected, setSelected] = React.useState(() => new Set());
  const [bulkPid, setBulkPid] = React.useState('');
  const toggleSel = (id) => setSelected(prev => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  const allSelected = items.length > 0 && items.every(t => selected.has(t.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map(t => t.id)));
  const realPlaylists = (playlists || []).filter(p => !p.is_virtual);
  const doBulkAdd = async () => {
    if (!bulkPid || selected.size === 0) return;
    await onBulkAddToPlaylist(Number(bulkPid), [...selected]);
    setSelected(new Set());
  };

  const ctrl = (label, val, set, opts) => (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--fg-muted)' }}>
      {label}
      <select value={val} onChange={e => set(e.target.value)}
        style={{ font: 'inherit', fontSize: 12, height: 26, padding: '0 8px', borderRadius: 'var(--r-sm)',
                 border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
        {opts.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
      </select>
    </label>
  );

  const showLoading = loading && items.length === 0;
  const showEmpty   = !loading && items.length === 0;

  return (
    <>
      <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 360 }}>
          <input value={q} onChange={e => setQ(e.target.value)}
                 placeholder="search title, artist, album, path…"
                 style={{ font: 'inherit', fontSize: 13, width: '100%', height: 30, padding: '0 10px 0 28px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <span style={{ position: 'absolute', left: 9, top: 8, color: 'var(--fg-subtle)', pointerEvents: 'none' }}>
            <Icon name="search" size={13}/>
          </span>
        </div>
        {/* Source options are data-driven off /library/stats by_source —
            whatever provider plugins stamp into library_tracks.source
            shows up automatically (open enum, design §6.4). */}
        {ctrl('source', source, setSource, [
          { v: 'all', l: 'all' },
          ...(sourceOptions || []).map(s => ({ v: s, l: s })),
        ])}
        {ctrl('sort', sort, setSort, [
          {v:'added_desc',l:'newest'},{v:'added_asc',l:'oldest'},
          {v:'title',l:'title'},{v:'artist',l:'artist'},{v:'duration',l:'longest'}
        ])}
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                        fontSize: 11, color: 'var(--fg-muted)', cursor: 'pointer' }}>
          <input type="checkbox" checked={favoritedOnly}
                 onChange={e => setFavoritedOnly(e.target.checked)}/>
          favorites only
        </label>
        <div style={{ marginLeft: 'auto' }} className="mono">
          <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
            {filtered ? `${total} of ${libraryTotal ?? '—'}` : `${total} track${total === 1 ? '' : 's'}`}
          </span>
        </div>
      </div>

      {selected.size > 0 && (
        <div style={{ padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--border-soft)', background: 'var(--sunken)' }}>
          <span style={{ fontSize: 12, fontWeight: 500 }}>{selected.size} selected</span>
          <select value={bulkPid} onChange={e => setBulkPid(e.target.value)}
                  style={{ font: 'inherit', fontSize: 12, height: 28, padding: '0 8px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
            <option value="">add to playlist…</option>
            {realPlaylists.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <Button variant="primary" icon="plus" disabled={!bulkPid} onClick={doBulkAdd}>add {selected.size}</Button>
          <Button icon="x" onClick={() => setSelected(new Set())}>clear</Button>
        </div>
      )}

      {showLoading ? (
        <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading library…</div>
      ) : showEmpty ? (
        <Empty glyph="headphones"
               title={filtered ? 'no tracks match' : 'library is empty'}
               sub={q ? `q = “${q}”`
                       : favoritedOnly ? 'no favorited tracks yet'
                       : source !== 'all' ? `no ${source} tracks`
                       : 'upload files, or ask any room to add music to the library'}
               action={filtered
                 ? <Button icon="x" onClick={() => { setQ(''); setSource('all'); setFavoritedOnly(false); }}>clear filters</Button>
                 : null}/>
      ) : (
        <table className="tbl">
          <thead><tr>
            <th style={{ width: 30 }}><input type="checkbox" checked={allSelected} onChange={toggleAll} title="select all on this page"/></th>
            <th style={{ width: 40 }}></th>
            <th>title</th>
            <th>album</th>
            <th>added</th>
            <th>via</th>
            <th>source</th>
            <th className="num">duration</th>
            <th className="actions"></th>
          </tr></thead>
          <tbody>
            {items.map(t => (
              <tr key={t.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(t)}>
                <td onClick={e => e.stopPropagation()}><input type="checkbox" checked={selected.has(t.id)} onChange={() => toggleSel(t.id)}/></td>
                <td onClick={e => e.stopPropagation()}><IconButton name="play" onClick={() => onBrowserPlay(t)} title="play in this browser"/></td>
                <td>
                  <div style={{ fontWeight: 500 }}>{t.title || '—'}</div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{t.artist || '—'}</div>
                </td>
                <td style={{ color: 'var(--fg-muted)' }}>{t.album || '—'}</td>
                <td className="mono">{relTime(t.added_at)}</td>
                <td><Pill tone={t.added_via === 'voice' ? 'live' : 'idle'}>{t.added_via}</Pill></td>
                <td><Pill tone={t.source ? 'live' : 'idle'}>{t.source || 'manual'}</Pill></td>
                <td className="num mono">{fmtDur(t.duration_sec)}</td>
                <td className="actions" onClick={e => e.stopPropagation()}
                    style={{ whiteSpace: 'nowrap' }}>
                  <IconButton name="list-plus" onClick={() => onQueueTrack(t)}
                              title="add to browser queue"/>
                  <IconButton name="corner-down-right" onClick={() => onPlayNextTrack(t)}
                              title="play next in browser"/>
                  <IconButton name="heart" onClick={() => onToggleFavorite(t)}
                              style={t.favorited ? { color: 'var(--brand)' } : undefined}
                              title={t.favorited ? 'unfavorite' : 'favorite'}/>
                  <IconButton name="plus" onClick={() => onAddToPlaylist(t)}
                              title="add to playlist…"/>
                  <IconButton name="download" title="save to this device"
                              onClick={() => deviceDownload(`/api/music/library/${t.id}/audio?download=1`)}/>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 8, borderTop: '1px solid var(--border-soft)' }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>page {page + 1} of {pages}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <IconButton name="chevron-left"  onClick={() => setPage(Math.max(0, page - 1))}/>
          <IconButton name="chevron-right" onClick={() => setPage(Math.min(pages - 1, page + 1))}/>
        </div>
      </div>
    </>
  );
};

/* ---- Jobs tab (generic acquisition-queue readout) ---------- */
/* Vocabulary-clean by design (§4.8): rows are "acquisitions" — a
 * structured request to obtain media into the library. Provider
 * plugins render richer, provider-specific views on their own pages;
 * this readout is the lowest common denominator plus an "add music"
 * bar that enqueues by free-text query (open daily action) or by
 * exact URL (admin / allowlist gated — a 401/403 pops the login
 * modal via data.js). */
const _ACQ_STATUS_PILL = {
  pending:       { tone: 'idle', label: 'pending' },
  claimed:       { tone: 'live', label: 'fetching', live: true },
  done:          { tone: 'ok',   label: 'done' },
  failed:        { tone: 'err',  label: 'failed' },
  unfulfillable: { tone: 'warn', label: 'unfulfillable' },
  cancelled:     { tone: 'idle', label: 'cancelled' },
};

const AddMusicBar = ({ rooms, canFulfillQuery, fire, onQueued }) => {
  const [mode, setMode] = React.useState('query');    // 'query' | 'url'
  const [text, setText] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const room = rooms[0] || 'kitchen';
  const submit = async () => {
    const value = text.trim();
    if (!value) return;
    setBusy(true);
    try {
      const res = mode === 'query'
        ? await apiPost('/api/music/add-by-query', { room_id: room, query: value })
        : await apiPost('/api/music/add-by-url', { room_id: room, url: value });
      fire(res?.message || (res?.queued ? 'queued' : 'not queued'));
      setText('');
      onQueued && onQueued();
    } catch (e) {
      fire(`add failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ padding: '12px 16px', display: 'flex', gap: 10, alignItems: 'center', borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
      <select value={mode} onChange={e => setMode(e.target.value)}
              style={{ font: 'inherit', fontSize: 12, height: 30, padding: '0 8px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
        <option value="query">by search query</option>
        <option value="url">by exact URL</option>
      </select>
      <input value={text} onChange={e => setText(e.target.value)}
             onKeyDown={e => { if (e.key === 'Enter') submit(); }}
             placeholder={mode === 'query' ? 'artist and title…' : 'https://… (admin or allow-listed hosts)'}
             style={{ flex: '1 1 260px', maxWidth: 420, font: 'inherit', fontSize: 13, height: 30, padding: '0 10px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}/>
      <Button variant="primary" icon="plus" onClick={submit} disabled={busy || !text.trim()}>
        {busy ? 'queueing…' : 'add music'}
      </Button>
      {canFulfillQuery === false && (
        <span className="meta" style={{ color: 'var(--warn)' }}>
          no media provider installed — requests wait until one is
        </span>
      )}
    </div>
  );
};

const JobsTab = ({ jobs, availability, loading, rooms, onCancel, fire, refresh }) => {
  if (loading && jobs.length === 0) {
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading jobs…</div>;
  }
  return (
    <>
      <AddMusicBar rooms={rooms} fire={fire} onQueued={refresh}
                   canFulfillQuery={availability?.can_fulfill_query}/>
      {jobs.length === 0 ? (
        <Empty glyph="headphones" title="no acquisition jobs"
               sub='say "add … to my library" in any room, or use the add-music bar above'/>
      ) : (
        <table className="tbl">
          <thead><tr>
            <th>request</th><th>kind</th><th>status</th><th>requested by</th><th>requested</th><th className="actions"></th>
          </tr></thead>
          <tbody>
            {jobs.map(a => {
              const pill = _ACQ_STATUS_PILL[a.status] || { tone: 'idle', label: a.status };
              return (
                <tr key={a.id}>
                  <td className="mono" title={a.text} style={{ maxWidth: 320 }}>
                    <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{truncMid(a.text || '', 50)}</div>
                  </td>
                  <td><Pill tone="idle">{a.kind}</Pill></td>
                  <td>
                    <Pill tone={pill.tone} live={!!pill.live}>{pill.label}</Pill>
                    {a.error && <div className="mono" style={{ fontSize: 11, color: 'var(--err)', marginTop: 4 }}>{a.error}</div>}
                  </td>
                  <td className="mono" style={{ fontSize: 11 }}>{a.requested_by || '—'}</td>
                  <td className="mono">{relTime(a.requested_at)}</td>
                  <td className="actions">
                    {(a.status === 'pending' || a.status === 'claimed')
                      ? <Button icon="x" onClick={() => onCancel(a)}>cancel</Button>
                      : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
};

/* ---- Playlists tab ---------------------------------------- */
/* Wrapped to keep the JSX scannable: list view in the tab area,
 * details via PlaylistDrawer (similar pattern to the existing
 * track Drawer). Favorites is pinned at the top with a star icon
 * — it's a virtual playlist (id=0) the backend derives from
 * library_tracks.favorited and that can't be renamed or deleted. */
const PlaylistsTab = ({ playlists, loading, onSelect, onPlay, fire }) => {
  if (loading && playlists.length === 0) {
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading playlists…</div>;
  }
  if (playlists.length === 0) {
    return <Empty glyph="headphones" title="no playlists yet"
                  sub={`click the + on a library row to start one, or say "make a new playlist called X"`}/>;
  }
  return (
    <table className="tbl">
      <thead><tr>
        <th style={{ width: 40 }}></th>
        <th>name</th>
        <th className="num">tracks</th>
        <th>created</th>
        <th className="actions"></th>
      </tr></thead>
      <tbody>
        {playlists.map(p => (
          <tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(p)}>
            <td onClick={e => e.stopPropagation()}>
              {p.cover_emoji
                ? <span style={{ fontSize: 18, lineHeight: 1 }}>{p.cover_emoji}</span>
                : <Icon name={p.is_virtual ? 'star' : 'list-music'} size={14}
                        style={p.is_virtual ? { color: 'var(--brand)' }
                                            : (p.cover_color ? { color: p.cover_color } : undefined)}/>}
            </td>
            <td>
              <div style={{ fontWeight: 500 }}>{p.name}</div>
              {p.description
                ? <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{p.description}</div>
                : p.is_virtual && (
                  <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                    derived from your favorites
                  </div>
                )}
            </td>
            <td className="num mono">{p.track_count}</td>
            <td className="mono">{p.created_at ? relTime(p.created_at) : '—'}</td>
            <td className="actions" onClick={e => e.stopPropagation()}>
              <IconButton name="play" onClick={() => onPlay(p)}/>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

/* ---- Playlist drawer -------------------------------------- */
/* Shows tracks in playback order (insertion order for real
 * playlists, library added_at ASC for the Favorites virtual
 * playlist). Per-row remove icon; for Favorites that flips
 * library_tracks.favorited, for real playlists it deletes from
 * playlist_tracks (the backend handles the routing).
 *
 * Footer holds Play / Shuffle buttons (both POST
 * /v1/admin/music/play-playlist with shuffle=false/true), Rename,
 * and Delete — the last two are disabled for Favorites. */
const PlaylistDrawer = ({ playlist, rooms, onClose, onPlay, onShuffle, onRemoveTrack, onDelete, onEdit, onReorder, fire }) => {
  const [room, setRoom] = React.useState(rooms[0] || 'kitchen');
  const [editing, setEditing] = React.useState(false);
  const [form, setForm] = React.useState({ name: '', description: '', cover_color: '', cover_emoji: '' });
  const [order, setOrder] = React.useState(null);   // local drag order, or null
  const dragFrom = React.useRef(null);
  React.useEffect(() => { setRoom(rooms[0] || 'kitchen'); }, [playlist?.id, rooms.join(',')]);
  React.useEffect(() => { setEditing(false); setOrder(null); }, [playlist?.id]);
  const { data: tracks, loading, refresh } = useApiObject(
    playlist ? `/api/playlists/${playlist.id}/tracks` : null,
    { eventTypes: ['playlists.changed', 'library.indexer.changed'] },
  );
  if (!playlist) return null;
  const items = order || tracks || [];

  const startEdit = () => {
    setForm({
      name: playlist.name || '', description: playlist.description || '',
      cover_color: playlist.cover_color || '', cover_emoji: playlist.cover_emoji || '',
    });
    setEditing(true);
  };
  const saveEdit = async () => { await onEdit(playlist, form); setEditing(false); };

  // HTML5 drag-to-reorder. Works on a local copy; commits the new order on drop.
  const onDrop = (toIdx) => {
    const from = dragFrom.current;
    dragFrom.current = null;
    if (from == null || from === toIdx) return;
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(toIdx, 0, moved);
    setOrder(next);
    onReorder(playlist, next.map(t => t.id)).catch(() => setOrder(null));
  };
  return (
    <>
      <div onClick={onClose}
           style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.16)',
                    backdropFilter: 'blur(2px)', zIndex: 40 }}/>
      <aside style={{ position: 'fixed', top: 0, right: 0, height: '100vh', width: 460, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 41,
                      display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border)' }}>
          <div className="eyebrow">{playlist.is_virtual ? 'virtual' : 'playlist'} · #{playlist.id}</div>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div style={{ padding: 16, display: 'flex', gap: 14, alignItems: 'center', borderBottom: '1px solid var(--border-soft)' }}>
          <div style={{ width: 56, height: 56, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                        background: playlist.is_virtual
                          ? 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))'
                          : (playlist.cover_color || 'var(--sunken)'),
                        display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {playlist.cover_emoji && !playlist.is_virtual
              ? <span style={{ fontSize: 28, lineHeight: 1 }}>{playlist.cover_emoji}</span>
              : <Icon name={playlist.is_virtual ? 'star' : 'list-music'} size={22}
                      style={playlist.is_virtual ? { color: 'var(--card)' } : { color: 'var(--fg-muted)' }}/>}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{playlist.name}</div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{playlist.track_count} track{playlist.track_count === 1 ? '' : 's'}</div>
            {playlist.description && <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 2 }}>{playlist.description}</div>}
          </div>
        </div>

        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)' }}>
          <div className="label" style={{ marginBottom: 6 }}>play in room</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {rooms.map(r => (
              <button key={r} onClick={() => setRoom(r)}
                style={{ font: 'inherit', fontSize: 12, cursor: 'pointer',
                         padding: '4px 10px', borderRadius: 'var(--r-full)',
                         border: '1px solid var(--border)',
                         background: room === r ? 'var(--brand-soft)' : 'var(--card)',
                         color: room === r ? 'var(--brand-press)' : 'var(--fg)' }}>{r}</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <Button variant="primary" icon="play"
                    onClick={() => { onPlay(playlist, room); onClose(); }}>
              play
            </Button>
            <Button icon="shuffle"
                    onClick={() => { onShuffle(playlist, room); onClose(); }}>
              shuffle
            </Button>
            {!playlist.is_virtual && (
              <Button icon="edit-2" onClick={() => (editing ? setEditing(false) : startEdit())}>
                {editing ? 'cancel' : 'edit'}
              </Button>
            )}
          </div>
          {editing && !playlist.is_virtual && (
            <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
              <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                     placeholder="name"
                     style={{ font: 'inherit', fontSize: 13, height: 30, padding: '0 10px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}/>
              <input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })}
                     placeholder="description (optional)"
                     style={{ font: 'inherit', fontSize: 13, height: 30, padding: '0 10px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}/>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input type="color" value={/^#[0-9a-fA-F]{6}$/.test(form.cover_color) ? form.cover_color : '#7c5cff'}
                       onChange={e => setForm({ ...form, cover_color: e.target.value })}
                       title="cover color" style={{ width: 34, height: 30, padding: 0, border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', background: 'var(--card)' }}/>
                <input value={form.cover_emoji} onChange={e => setForm({ ...form, cover_emoji: e.target.value })}
                       placeholder="emoji" maxLength={4}
                       style={{ font: 'inherit', fontSize: 16, width: 56, height: 30, textAlign: 'center', padding: 0, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}/>
                <div style={{ marginLeft: 'auto' }}>
                  <Button variant="primary" icon="check" onClick={saveEdit} disabled={!form.name.trim()}>save</Button>
                </div>
              </div>
            </div>
          )}
        </div>

        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
          {loading && items.length === 0 ? (
            <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>
          ) : items.length === 0 ? (
            <Empty glyph="headphones" title="no tracks yet"
                   sub={playlist.is_virtual
                     ? 'favorite a track to add it here'
                     : 'click the + on a library row to add tracks'}/>
          ) : (
            <div style={{ padding: '4px 0' }}>
              {items.map((t, i) => (
                <div key={t.id}
                     draggable={!playlist.is_virtual}
                     onDragStart={() => { dragFrom.current = i; }}
                     onDragOver={e => e.preventDefault()}
                     onDrop={() => onDrop(i)}
                     style={{ display: 'grid', gridTemplateColumns: '32px 1fr 24px',
                              alignItems: 'center', gap: 8,
                              padding: '8px 16px',
                              cursor: playlist.is_virtual ? 'default' : 'grab',
                              borderBottom: i === items.length - 1 ? 'none' : '1px solid var(--border-soft)' }}>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)', textAlign: 'right' }}>{i + 1}</div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.title || (t.file_path || '').split('/').pop().split('\\').pop()}
                    </div>
                    <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.artist || '—'}
                    </div>
                  </div>
                  <IconButton name="x"
                              onClick={() => onRemoveTrack(playlist, t).then(refresh)}
                              title="remove from playlist"/>
                </div>
              ))}
            </div>
          )}
        </div>

        {!playlist.is_virtual && (
          <div style={{ padding: 16, borderTop: '1px solid var(--border)', background: 'var(--sunken)' }}>
            <Button variant="secondary" icon="trash-2"
                    onClick={() => onDelete(playlist)}
                    style={{ background: 'var(--err-soft)', color: 'var(--err)', borderColor: 'transparent' }}>
              delete playlist
            </Button>
          </div>
        )}
      </aside>
    </>
  );
};

/* ---- Library "add to playlist" drawer --------------------- */
/* Opens from the + icon in a library row. Lists every playlist
 * (Favorites pinned at top); click toggles add/remove for the
 * specific track. Footer has a "create new" inline input.
 * Auto-closes on any explicit add/remove action so the workflow
 * stays one-click. */
const LibraryAddDrawer = ({ track, onClose, fire, onMutated }) => {
  const [creating, setCreating] = React.useState('');
  const { data: memberships, refresh: refreshMembers } = useApiObject(
    track ? `/api/music/library/${track.id}/playlists` : null,
    { eventTypes: ['playlists.changed'] },
  );
  const { data: allPlaylists, refresh: refreshAll } = useApiObject(
    track ? '/api/playlists' : null,
    { eventTypes: ['playlists.changed'] },
  );
  if (!track) return null;
  const memberIds = new Set((memberships || []).map(p => p.id));
  const playlists = allPlaylists || [];

  const closeAndPing = () => { onMutated && onMutated(); onClose(); };

  const onToggle = async (p) => {
    const inIt = memberIds.has(p.id);
    try {
      if (p.is_virtual) {
        // Favorites toggle goes through the existing library PATCH.
        await apiPatch(`/api/music/library/${track.id}`, { favorited: !inIt });
        fire(inIt ? `unfavorited "${track.title || 'track'}"` : `favorited "${track.title || 'track'}"`);
      } else if (inIt) {
        await apiDelete(`/api/playlists/${p.id}/tracks/${track.id}`);
        fire(`removed from ${p.name}`);
      } else {
        await apiPost(`/api/playlists/${p.id}/tracks`, { track_id: track.id });
        fire(`added to ${p.name}`);
      }
      closeAndPing();
    } catch (e) {
      fire(`failed: ${e.message}`);
      refreshMembers(); refreshAll();
    }
  };

  const onCreate = async () => {
    const name = creating.trim();
    if (!name) return;
    try {
      const created = await apiPost('/api/playlists', { name });
      await apiPost(`/api/playlists/${created.id}/tracks`, { track_id: track.id });
      fire(`created ${name} · added "${track.title || 'track'}"`);
      closeAndPing();
    } catch (e) {
      fire(`create failed: ${e.message}`);
    }
  };

  return (
    <>
      <div onClick={onClose}
           style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.16)',
                    backdropFilter: 'blur(2px)', zIndex: 40 }}/>
      <aside style={{ position: 'fixed', top: 0, right: 0, height: '100vh', width: 380, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 41,
                      display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border)' }}>
          <div>
            <div className="eyebrow">add to playlist</div>
            <div style={{ fontSize: 13, fontWeight: 500, marginTop: 2 }}>{track.title || 'track'}</div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{track.artist || ''}</div>
          </div>
          <IconButton name="x" onClick={onClose}/>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
          {playlists.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>
              no playlists yet — create one below
            </div>
          ) : playlists.map((p, i) => {
            const inIt = memberIds.has(p.id);
            return (
              <button key={p.id} onClick={() => onToggle(p)}
                style={{ font: 'inherit', display: 'flex', alignItems: 'center', gap: 10,
                         width: '100%', padding: '10px 16px',
                         border: 'none', borderBottom: i === playlists.length - 1 ? 'none' : '1px solid var(--border-soft)',
                         background: inIt ? 'var(--brand-soft)' : 'var(--card)',
                         color: 'var(--fg)', cursor: 'pointer', textAlign: 'left' }}>
                <Icon name={p.is_virtual ? 'star' : (inIt ? 'check' : 'plus')} size={14}
                      style={inIt || p.is_virtual ? { color: 'var(--brand)' } : { color: 'var(--fg-muted)' }}/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{p.name}</div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                    {inIt ? 'in this playlist' : `${p.track_count} track${p.track_count === 1 ? '' : 's'}`}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        <div style={{ padding: 14, borderTop: '1px solid var(--border)', background: 'var(--sunken)' }}>
          <div className="label" style={{ marginBottom: 6 }}>create new playlist</div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input value={creating} onChange={e => setCreating(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter') onCreate(); }}
                   placeholder="playlist name…"
                   style={{ font: 'inherit', fontSize: 13, flex: 1, height: 30,
                            padding: '0 10px', borderRadius: 'var(--r-sm)',
                            border: '1px solid var(--border)',
                            background: 'var(--card)', color: 'var(--fg)' }}/>
            <Button variant="primary" icon="plus" onClick={onCreate}
                    disabled={!creating.trim()}>create</Button>
          </div>
        </div>
      </aside>
    </>
  );
};

/* ---- Stats tab ------------------------------------------- */
const StatsTab = ({ stats, loading }) => {
  if (loading && !stats) {
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading stats…</div>;
  }
  if (!stats) {
    return <Empty glyph="headphones" title="stats unavailable" sub="library/stats endpoint did not return"/>;
  }
  const total    = stats.total_tracks;
  const totalDur = stats.total_duration_sec;
  const byVia    = stats.by_added_via || {};
  const enriched = stats.enriched_count;
  return (
    <div style={{ padding: 16, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
      <Stat label="total tracks"     value={total}                sub="across all sources"/>
      <Stat label="total duration"   value={totalDur ? fmtBigDur(totalDur) : '—'}  sub={totalDur ? `${Math.round(totalDur/60)} minutes` : 'no durations indexed'}/>
      <Stat label="added via voice"  value={byVia.voice || 0}     sub={`${byVia.manual || 0} manual`}/>
      <Stat label="enriched"         value={total ? `${enriched} / ${total}` : '—'} sub={total ? `${total - enriched} pending` : ''}/>
    </div>
  );
};

/* ---- Page ------------------------------------------------- */
const MusicPage = () => {
  const [tab, setTab] = React.useState('library');
  const [selected, setSelected] = React.useState(null);
  // Playlists tab state: which playlist row's drawer is open, and
  // which library track triggered the "+ add to playlist" drawer.
  // Independent so a user can browse the playlist drawer without
  // closing the library add drawer (rare, but cleaner).
  const [openPlaylist, setOpenPlaylist] = React.useState(null);
  const [addToPlaylistTrack, setAddToPlaylistTrack] = React.useState(null);
  const [tick, setTick] = React.useState(0);
  const [fire, toastNode] = useToast();

  // App-level browser playback context (docked mini-player lives in the
  // shell; here we drive it from library rows and render the expanded
  // "now playing" view as the Player tab). Degrades gracefully when the
  // provider isn't mounted (available === false).
  const player = usePlayback();
  const onBrowserPlay = (track) => {
    if (!player.available) { fire('browser player not available'); return; }
    player.playItems([window.itemFromTrack(track)]);
    fire(`playing "${track.title || 'track'}" in this browser`);
  };
  const onQueueTrack = (track) => {
    if (!player.available) { fire('browser player not available'); return; }
    player.enqueue([window.itemFromTrack(track)]);
    fire(`queued "${track.title || 'track'}"`);
  };
  const onPlayNextTrack = (track) => {
    if (!player.available) { fire('browser player not available'); return; }
    player.playNext(window.itemFromTrack(track));
    fire(`playing next: "${track.title || 'track'}"`);
  };

  // Data fetches. Each subscribes to the WS event types that imply
  // "your snapshot is stale, refetch." Library page is server-paginated
  // (useLibraryPage encapsulates filter state + URL building) and the
  // whole-library aggregates live in a separate /stats endpoint — both
  // refetch on indexer progress, which is the one event that adds rows.
  const lib = useLibraryPage();
  const { data: stats, refresh: refreshStats } =
    useApiObject('/api/music/library/stats', { eventTypes: ['library.indexer.changed'] });
  const { data: acqData, loading: acqLoading, refresh: refreshAcquisitions } =
    useApiObject('/api/acquisitions?limit=100', { eventTypes: ['acquisitions.changed'] });
  const acquisitions = acqData?.acquisitions || [];
  const { items: nowPlaying, refresh: refreshNP } =
    useApiList('/api/music/now-playing', { eventTypes: ['music.now_playing.changed'] });
  // Playlists list — driven by /api/playlists which includes the
  // virtual Favorites row. Refetches on any playlists_changed event
  // (create/rename/delete + library favorite flips).
  const { items: playlists, loading: playlistsLoading, refresh: refreshPlaylists } =
    useApiList('/api/playlists', { eventTypes: ['playlists.changed'] });

  const rooms = useKnownRooms(nowPlaying);

  // Tick once a second so the elapsed-time display advances
  // between WS pushes (domovoi only emits on play/pause/skip
  // boundaries; the seconds in between need a local clock).
  React.useEffect(() => {
    const i = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(i);
  }, []);

  // Reset tick whenever the now-playing payload changes — the new
  // elapsed_sec from the server is canonical, the local tick is
  // additive to it.
  React.useEffect(() => { setTick(0); }, [JSON.stringify(nowPlaying)]);

  // ── Action handlers — all hit the real backend. ──────────────
  const onPlayRandom = async (room_id) => {
    fire(`shuffle requested in ${room_id}…`);
    try { await apiPost('/api/music/play', { room_id, query: 'something random' }); refreshNP(); }
    catch (e) { fire(`play failed: ${e.message}`); }
  };
  const onPlayInRoom = async (track, room_id) => {
    fire(`playing "${track.title || 'track'}" in ${room_id}…`);
    // Close the drawer immediately rather than after the API resolves
    // so the click feels responsive even when MPD takes a beat.
    // Failure surfaces as a toast either way.
    setSelected(null);
    // Direct-play by id: skips the router entirely, so no
    // conversation_log entry, and no fuzzy-match streaming-provider
    // fallthrough when ID3 tags don't line up with what MPD indexed.
    try { await apiPost('/api/music/play-track', { room_id, track_id: track.id }); refreshNP(); }
    catch (e) { fire(`play failed: ${e.message}`); }
  };
  const onPause = async (room_id) => { try { await apiPost(`/api/music/pause/${room_id}`); refreshNP(); } catch (e) { fire(`pause failed: ${e.message}`); } };
  const onResume = async (room_id) => { try { await apiPost(`/api/music/resume/${room_id}`); refreshNP(); } catch (e) { fire(`resume failed: ${e.message}`); } };
  const onSkip = async (room_id) => { try { await apiPost(`/api/music/skip/${room_id}`); refreshNP(); } catch (e) { fire(`skip failed: ${e.message}`); } };
  const onStop = async (room_id) => { try { await apiPost(`/api/music/stop/${room_id}`); refreshNP(); } catch (e) { fire(`stop failed: ${e.message}`); } };

  // Favorite whatever's currently playing in `room_id`. The backend
  // picks the right path (library row flip · generic acquisition
  // enqueue) based on the MPD currentsong; we just relay the kind to
  // the toast so the user sees the right confirmation.
  // Toggle the favorited flag on a specific library_tracks row. Used
  // by the heart icon in the Library table — mirrors the now-playing
  // card's heart visually but flips both directions (the NPCard's
  // is set-only because there's no "current track to unfavorite"
  // affordance there).
  const onToggleFavorite = async (track) => {
    const next = !track.favorited;
    try {
      await apiPatch(`/api/music/library/${track.id}`, { favorited: next });
      // Refresh both the library list (so the row's heart updates)
      // and now-playing (so the NPCard's heart stays in sync if this
      // track happens to be the one currently playing in some room).
      lib.refresh();
      refreshNP();
    } catch (e) {
      fire(`favorite failed: ${e.message}`);
    }
  };

  const onFavorite = async (room_id) => {
    try {
      const r = await apiPost(`/api/music/now-playing/${room_id}/favorite`);
      const label = r.title || 'track';
      if (r.already_favorited) {
        if (r.kind === 'library') fire(`${label} is already favorited`);
        else fire(`${label} is already in your library`);
      } else if (r.kind === 'library') {
        fire(`favorited ${label}`);
        lib.refresh();
      } else if (r.kind === 'acquisition') {
        // The core's message carries the graceful-absence copy when no
        // provider plugin is installed — relay it verbatim.
        fire(r.message || `queued ${label} for the library`);
        refreshAcquisitions();
      }
      // Pull a fresh now-playing snapshot so the heart's filled state
      // reflects the row we just flipped. The poll loop catches this
      // within ~1.5 s anyway, but the user just clicked — they expect
      // immediate feedback.
      refreshNP();
    } catch (e) {
      fire(`favorite failed: ${e.message}`);
    }
  };

  const onDelete = async (track, alsoFile) => {
    // File deletion is irreversible — confirm explicitly. Metadata-
    // only delete still nukes the row but the file stays put (and
    // the next library indexer sweep will resurrect the row), so
    // we don't bother gating that with a confirm.
    if (alsoFile) {
      const ok = window.confirm(
        `Permanently delete the file for "${track.title || 'track'}" `
        + `from disk? This can't be undone.\n\n${track.file_path || ''}`,
      );
      if (!ok) return;
    }
    try {
      const qs = alsoFile ? '?also_file=true' : '';
      await apiDelete(`/api/music/library/${track.id}${qs}`);
      fire(
        alsoFile
          ? `deleted "${track.title || 'track'}" · file removed from disk`
          : `removed "${track.title || 'track'}" from library (file kept; rescan will re-add)`,
      );
      setSelected(null);
      lib.refresh();
      refreshStats();
    } catch (e) {
      fire(`delete failed: ${e.message}`);
    }
  };
  const onCancelAcquisition = async (a) => {
    try { await apiDelete(`/api/music/acquisitions/${a.id}`); fire(`cancel sent for #${a.id}`); refreshAcquisitions(); }
    catch (e) { fire(`cancel failed: ${e.message}`); }
  };
  // ── Playlist action handlers ─────────────────────────────────
  const onPlayPlaylist = async (playlist, room_id) => {
    fire(`playing ${playlist.name} in ${room_id}…`);
    try {
      await apiPost('/api/music/play-playlist',
        { room_id, playlist_id: playlist.id, shuffle: false });
      refreshNP();
    } catch (e) { fire(`play failed: ${e.message}`); }
  };
  const onShufflePlaylist = async (playlist, room_id) => {
    fire(`shuffling ${playlist.name} in ${room_id}…`);
    try {
      await apiPost('/api/music/play-playlist',
        { room_id, playlist_id: playlist.id, shuffle: true });
      refreshNP();
    } catch (e) { fire(`shuffle failed: ${e.message}`); }
  };
  const onRemoveFromPlaylist = async (playlist, track) => {
    try {
      await apiDelete(`/api/playlists/${playlist.id}/tracks/${track.id}`);
      fire(`removed "${track.title || 'track'}" from ${playlist.name}`);
      refreshPlaylists();
    } catch (e) {
      fire(`remove failed: ${e.message}`);
    }
  };
  const onDeletePlaylist = async (playlist) => {
    if (!window.confirm(`Delete playlist "${playlist.name}"?`)) return;
    try {
      await apiDelete(`/api/playlists/${playlist.id}`);
      fire(`deleted ${playlist.name}`);
      setOpenPlaylist(null);
      refreshPlaylists();
    } catch (e) {
      fire(`delete failed: ${e.message}`);
    }
  };
  const onEditPlaylist = async (playlist, fields) => {
    try {
      await apiPatch(`/api/playlists/${playlist.id}`, fields);
      fire('playlist updated');
      refreshPlaylists();
      setOpenPlaylist({ ...playlist, ...fields });   // keep the drawer current
    } catch (e) {
      fire(`update failed: ${e.message}`);
    }
  };
  const onReorderPlaylist = async (playlist, track_ids) => {
    try {
      await apiPatch(`/api/playlists/${playlist.id}/order`, { track_ids });
      refreshPlaylists();
    } catch (e) {
      fire(`reorder failed: ${e.message}`);
      throw e;   // let the drawer revert its optimistic order
    }
  };
  const onBulkAddToPlaylist = async (playlistId, trackIds) => {
    let added = 0, dupes = 0;
    for (const tid of trackIds) {
      try {
        await apiPost(`/api/playlists/${playlistId}/tracks`, { track_id: tid });
        added++;
      } catch (e) {
        if (/already|409/.test(e.message)) dupes++;
        else fire(`add failed: ${e.message}`);
      }
    }
    fire(`added ${added} track${added === 1 ? '' : 's'}${dupes ? `, ${dupes} already in` : ''}`);
    refreshPlaylists();
  };

  const onRescan = async () => {
    try { await apiPost('/api/music/library/reindex'); fire('rescan started'); }
    catch (e) { fire(`rescan failed: ${e.message}`); }
  };

  // ── Upload audio into the library ───────────────────────────
  // Accepts one or many audio files and/or .zip archives. The backend
  // writes them under MUSIC_DIR/uploads/ then triggers a reindex (which
  // also tells each room's MPD to rescan, so they're playable). We don't
  // poll for the result: index_music_dir fires a `library_changed` NOTIFY
  // when rows land, which the library/stats hooks above subscribe to via
  // `library.indexer.changed` — so the new tracks appear the instant
  // indexing finishes, however long it takes.
  const fileInputRef = React.useRef(null);
  const [uploading, setUploading] = React.useState(false);
  const onUploadFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setUploading(true);
    fire(`uploading ${files.length} item${files.length === 1 ? '' : 's'}…`);
    try {
      const fd = new FormData();
      files.forEach(f => fd.append('files', f));
      const res = await apiUpload('/api/music/library/upload', fd);
      const parts = [`uploaded ${res.saved} track${res.saved === 1 ? '' : 's'}`];
      if (res.skipped?.length) parts.push(`${res.skipped.length} skipped`);
      parts.push(res.reindex_triggered ? 'indexing…' : 'domovoi offline — will index on next boot');
      fire(parts.join(' · '));
    } catch (e) {
      fire(`upload failed: ${e.message}`);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };
  const onEnrich = async () => {
    try { await apiPost('/api/music/library/enrich'); fire('enrich started'); }
    catch (e) { fire(`enrich failed: ${e.message}`); }
  };

  const playingCount = nowPlaying.filter(n => n.state === 'play').length;
  const activeJobs = acquisitions.filter(a => a.status === 'claimed' || a.status === 'pending').length;

  const libraryTotal = stats?.total_tracks ?? null;
  // Data-driven source filter options (open enum, §6.4).
  const sourceOptions = Object.keys(stats?.by_source || {}).sort();
  const tabs = [
    { id: 'library',   label: 'Library',   count: libraryTotal ?? undefined },
    { id: 'player',    label: 'Player' },
    { id: 'playlists', label: 'Playlists', count: playlists.length || undefined },
    { id: 'stats',     label: 'Stats' },
    { id: 'jobs',      label: 'Jobs',      count: activeJobs || undefined },
  ];

  return (
    <div className="page">
      <PageHeader
        title="Music"
        sub={`${libraryTotal ?? '—'} tracks · ${playingCount} room${playingCount === 1 ? '' : 's'} playing · ${activeJobs} job${activeJobs === 1 ? '' : 's'} active`}
        actions={
          <>
            <input ref={fileInputRef} type="file" multiple
                   accept=".mp3,.m4a,.mp4,.flac,.ogg,.oga,.opus,.wav,.wma,.aac,.alac,.zip,audio/*,application/zip"
                   style={{ display: 'none' }}
                   onChange={e => onUploadFiles(e.target.files)}/>
            <Button variant="primary" icon="upload" disabled={uploading}
                    onClick={() => fileInputRef.current && fileInputRef.current.click()}>
              {uploading ? 'Uploading…' : 'Upload'}
            </Button>
            <Button icon="refresh-cw" onClick={onRescan}>Rescan library</Button>
            <Button icon="sparkles"   onClick={onEnrich}>Enrich tags</Button>
          </>
        }
      />

      {/* [1] Now Playing strip — one card per provisioned room.
            Empty if no mpd_rooms have been registered yet (first boot). */}
      {nowPlaying.length === 0 ? (
        <Card><Empty glyph="headphones" title="no rooms provisioned yet" sub="connect a satellite to bring its room online"/></Card>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
          {nowPlaying.map(np => (
            <NPCard key={np.room_id} np={np} tick={tick}
                    onPlayRandom={onPlayRandom}
                    onPause={onPause} onResume={onResume}
                    onSkip={onSkip} onStop={onStop}
                    onFavorite={onFavorite}/>
          ))}
        </div>
      )}

      {/* [2] Lower tabbed section */}
      <Card>
        <div style={{ padding: '0 8px' }}><Tabs tabs={tabs} value={tab} onChange={setTab}/></div>
        {tab === 'library'   && <LibraryTab   lib={lib} libraryTotal={libraryTotal} sourceOptions={sourceOptions} onSelect={setSelected} onToggleFavorite={onToggleFavorite} onAddToPlaylist={setAddToPlaylistTrack} playlists={playlists} onBulkAddToPlaylist={onBulkAddToPlaylist} onBrowserPlay={onBrowserPlay} onQueueTrack={onQueueTrack} onPlayNextTrack={onPlayNextTrack} fire={fire}/>}
        {tab === 'player'    && <NowPlayingPanel/>}
        {tab === 'playlists' && <PlaylistsTab playlists={playlists} loading={playlistsLoading} onSelect={setOpenPlaylist} onPlay={(p) => onPlayPlaylist(p, rooms[0] || 'kitchen')} fire={fire}/>}
        {tab === 'stats'     && <StatsTab     stats={stats} loading={!stats}/>}
        {tab === 'jobs'      && <JobsTab jobs={acquisitions} availability={acqData} loading={acqLoading} rooms={rooms} onCancel={onCancelAcquisition} fire={fire} refresh={refreshAcquisitions}/>}
      </Card>

      <Drawer track={selected} rooms={rooms} onClose={() => setSelected(null)}
              onDelete={onDelete} onPlayInRoom={onPlayInRoom}
              onBrowserPlay={onBrowserPlay} onQueueTrack={onQueueTrack}/>
      <PlaylistDrawer playlist={openPlaylist} rooms={rooms}
                      onClose={() => setOpenPlaylist(null)}
                      onPlay={onPlayPlaylist} onShuffle={onShufflePlaylist}
                      onRemoveTrack={onRemoveFromPlaylist}
                      onDelete={onDeletePlaylist} onEdit={onEditPlaylist}
                      onReorder={onReorderPlaylist}
                      fire={fire}/>
      <LibraryAddDrawer track={addToPlaylistTrack}
                        onClose={() => setAddToPlaylistTrack(null)}
                        onMutated={() => { lib.refresh(); refreshPlaylists(); refreshNP(); }}
                        fire={fire}/>
      {toastNode}
    </div>
  );
};

window.MusicPage = MusicPage;
