/* Music page — Library / Artists / Genres / Room queue / Playlists / Stats / Jobs tabs.
 * Provider-specific search & download UIs live on PLUGIN pages (design
 * §10.1) — this page stays generic: "Add music" enqueues rows in the
 * provider-agnostic media-acquisition queue, and the Jobs tab is a
 * small readout of that queue.
 * Now Playing strip · tabbed lower section · side drawer · search · load as you scroll
 *
 * Data sources:
 *   * GET /api/music/library                    — one page of tracks at a time
 *                                                 (data.js usePagedList: limit +
 *                                                 cursor, {total, items, has_more,
 *                                                 next_cursor}). The Library tab
 *                                                 drives q/source/sort/favorited;
 *                                                 q is debounced; every filter is
 *                                                 the server's.
 *   * GET /api/music/artists · /api/music/artist — the Artists tab (performers,
 *                                                 paged + searched by the server)
 *                                                 and one artist's albums + track ids;
 *                                                 their songs are GET /api/music/
 *                                                 library?artist=&album=&sort=album.
 *   * GET /api/music/genres · /api/music/genre  — the Genres tab (V050: genres from
 *                                                 the files' tags, with how far the
 *                                                 read has got) and one genre's
 *                                                 track ids; its songs are GET
 *                                                 /api/music/library?genre=&sort=artist.
 *   * GET /api/music/library/stats              — whole-library aggregates for the
 *                                                 Stats tab and the tab badge,
 *                                                 computed server-side so we never
 *                                                 iterate a huge in-memory list.
 *   * GET /api/acquisitions                     — generic acquisition queue rows
 *                                                 + fulfiller availability (Jobs).
 *   * GET /api/music/now-playing                — per-room MPD state.
 *   * GET /api/music/aliases/for-track/{id}     — the drawer's "also called" lists
 *                                                 (song / artists / album);
 *                                                 POST / DELETE /api/music/aliases
 *                                                 add or remove one name.
 *   * GET /api/music/aliases/status             — the Stats tab's names card.
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

/* The room set comes from the now-playing fetch — every room with an
 * mpd_rooms row shows up. Nothing is invented when that list is empty
 * or unreachable: an empty set is a real state ("no rooms provisioned
 * yet") that each consumer renders on its own, and a made-up default
 * room had the Room queue tab fetching /api/music/queue/kitchen on a
 * box with no rooms and reporting the 502 as an empty queue (F-020). */
const useKnownRooms = (nowPlaying) => (nowPlaying || []).map(np => np.room_id);

/* The play-in-room chip strip shared by the track and playlist drawers.
 * With no rooms it says so instead of offering a room that does not
 * exist; the caller disables its play button on `room == null`. */
const RoomChips = ({ rooms, room, onPick }) => {
  if (rooms.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
        no rooms set up yet — set up a room to play on a speaker
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {rooms.map(r => (
        <button key={r} onClick={() => onPick(r)}
          style={{ font: 'inherit', fontSize: 12, cursor: 'pointer',
                   padding: '4px 10px', borderRadius: 'var(--r-full)',
                   border: '1px solid var(--border)',
                   background: room === r ? 'var(--brand-soft)' : 'var(--card)',
                   color: room === r ? 'var(--brand-ink)' : 'var(--fg)' }}>{r}</button>
      ))}
    </div>
  );
};

/* The Library tab's list: one page of tracks at a time, loaded as the
 * table scrolls (data.js usePagedList + PagedListFooter). Owns the
 * q/source/sort/favorites state and debounces q; any change to them is a
 * new list from page one, filtered and sorted by the server over the WHOLE
 * library — never just the rows already loaded. */
const useLibraryPage = ({ pageSize = 50 } = {}) => {
  const [q, setQ] = React.useState('');
  const [source, setSource] = React.useState('all');
  const [sort, setSort] = React.useState('added_desc');
  const [favoritedOnly, setFavoritedOnlyRaw] = React.useState(false);
  const setFavoritedOnly = React.useCallback((v) => setFavoritedOnlyRaw(!!v), []);

  const qDebounced = useDebouncedValue(q, 250);

  const path = React.useMemo(() => {
    const params = new URLSearchParams();
    if (qDebounced) params.set('q', qDebounced);
    if (source && source !== 'all') params.set('source', source);
    if (favoritedOnly) params.set('favorited', 'true');
    params.set('sort', sort);
    return `/api/music/library?${params.toString()}`;
  }, [qDebounced, source, favoritedOnly, sort]);

  const list = usePagedList(path, { pageSize, eventTypes: ['library.indexer.changed'] });

  return {
    list, items: list.items, total: list.total ?? 0, loading: list.loading,
    refresh: list.reload,
    q, source, sort, favoritedOnly,
    setQ, setSource, setSort, setFavoritedOnly,
  };
};

/* ---- Now Playing card ------------------------------------- */
/* Seconds since this room's now-playing was read, ticking once a second
 * while it plays, so the elapsed time advances between pushes (the server
 * only says play / pause / skip / new song; the seconds between need a
 * local clock). Back to 0 whenever the room's state, song or elapsed time
 * changes: the server's elapsed_sec is canonical, the tick adds to it.
 * Per card, not per page: the page-wide tick re-drew the whole Music page
 * (every tab, a long room queue included) every second. */
const useRoomTick = (np) => {
  const [tick, setTick] = React.useState(0);
  const playing = !!(np && np.state === 'play' && np.song);
  const sig = `${roomPlaySig(np)}|${np ? np.elapsed_sec : ''}`;
  React.useEffect(() => { setTick(0); }, [sig]);
  React.useEffect(() => {
    if (!playing) return undefined;
    const i = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(i);
  }, [playing]);
  return tick;
};
const TickingNPCard = (props) => {
  const tick = useRoomTick(props.np);
  return <NPCard {...props} tick={tick}/>;
};

// `guest` (data.js isGuestViewer): a browser with no household credential.
// It may pause, resume and skip a room that is playing and change its
// queue (owner decision 2026-10-10), but not stop it, favorite for the
// household or start a quiet room, so those buttons are left off — a
// quiet room says pairing is how to start it — rather than offered and
// answered with a pairing prompt. `onQueue` opens the Room queue tab on
// this room.
const NPCard = ({ np, tick, guest, onPlayRandom, onPause, onResume, onSkip, onStop, onFavorite, onQueue }) => {
  const playing = np.state === 'play' && np.song;
  const paused = np.state === 'pause' && np.song;
  const songDur = np.song?.duration_sec ?? 0;
  const elapsed = (playing || paused) ? (np.elapsed_sec ?? 0) + (playing ? tick : 0) : 0;
  const progress = songDur > 0 ? Math.min(100, (elapsed / songDur) * 100) : 0;
  const sourceHref = webHref(np.source_url);  // http(s) only, else no pill
  // A library file the room plays has a cover; now-playing maps MPD's
  // current file to its track id. A stream, or nothing, keeps the plain tile.
  const coverSrc = (playing || paused) && np.track_id != null ? libraryCoverUrl(np.track_id) : null;
  return (
    <div className="card" style={{ padding: 0 }}>
      <div style={{ padding: '14px 16px', display: 'grid', gridTemplateColumns: '52px 1fr', gap: 12, alignItems: 'center' }}>
        <CoverArt src={coverSrc} size={52} icon={null}
                  background={(playing || paused) ? 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))' : 'var(--sunken)'}/>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <RoomChip name={np.room_id} online/>
            {playing && <Pill tone="live" live>live</Pill>}
            {paused && <Pill tone="idle">paused</Pill>}
            {/* Generic now-playing source pill (design §4.7/§10.1): a
                provider plugin's stamp supplies source + optional URL;
                the label stays provider-agnostic. */}
            {(playing || paused) && sourceHref && (
              <a href={sourceHref} target="_blank" rel="noopener noreferrer"
                 title={`open source${np.source ? ` (${np.source})` : ''}`}
                 style={{
                   display: 'inline-flex', alignItems: 'center', gap: 4,
                   fontSize: 11, lineHeight: 1, padding: '3px 8px',
                   borderRadius: 'var(--r-full)', textDecoration: 'none',
                   color: 'var(--brand-ink)', background: 'var(--brand-soft)',
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
              {/* Who queued this, when we know. Greyed and appended rather
                  than given its own line — "added by" is a footnote, not a
                  headline, and it must not reflow the card when absent. */}
              {np.added_by && (
                <span style={{ color: 'var(--fg-faint)', fontWeight: 400, fontSize: 11 }}>
                  {' · added by '}{np.added_by}
                </span>
              )}
            </div>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>nothing playing in {np.room_id}</div>
          )}
          {/* The room's current timed line (lyrics.jsx), household tier only. */}
          {(playing || paused) && typeof LyricsRoomLine === 'function' && <LyricsRoomLine np={np} tick={tick}/>}
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
            {!guest && <IconButton name="square" onClick={() => onStop(np.room_id)}/>}
            {!guest && (
              <IconButton name="heart" onClick={() => onFavorite(np.room_id)}
                          style={np.favorited ? { color: 'var(--brand-ink)' } : undefined}
                          title={np.favorited
                            ? 'already favorited'
                            : 'favorite (library track = save · external stream = fetch into library)'}/>
            )}
            {onQueue && (
              <Button icon="list-music" onClick={() => onQueue(np.room_id)}
                      title={`${np.room_id}'s queue`}>queue</Button>
            )}
          </div>
        </div>
      ) : guest ? (
        /* Starting a quiet room takes a paired phone. Said, with the way to
           pair one press away — not a button whose answer is a prompt. */
        <div style={{ padding: '0 16px 14px', display: 'flex', alignItems: 'center', gap: 8,
                      justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>connect this browser to start music here</span>
          <Button icon="link" onClick={() => {
            try { if (typeof ConnectAsk !== 'undefined') { ConnectAsk.open(); return; } } catch { /* below */ }
            try { Auth.requestPairing(); } catch {}
          }}>connect</Button>
        </div>
      ) : (
        <div style={{ padding: '0 16px 14px', display: 'flex', justifyContent: 'flex-end' }}>
          <Button icon="shuffle" onClick={() => onPlayRandom(np.room_id)}>play something</Button>
        </div>
      )}
    </div>
  );
};

/* ---- "Also called" (track drawer) -------------------------- */
/* Other names the household says for this song, each of its artists and
 * its album (V019 library_aliases, GET /api/music/aliases/for-track/{id}).
 * A thing can have ANY number of names (owner decision 2026-10-02), so
 * each target is an editable LIST: chips, an x on the ones this caller
 * may remove, and an "add a name…" field (Enter adds, Escape clears) —
 * never a single text box. A name means ONE thing household-wide: adding
 * one that already means something else asks "Replace it?" when this
 * caller may replace it (POST again with replace: true), else says only
 * an admin can. MusicBrainz names are muted. The lists are loaded once
 * per track and then kept from each write's own answer. */
const AlsoCalledTypeWord = { artist: 'artist', album: 'album', track: 'song' };

/* The drawer's targets, in order: the song, each performer (a whole
 * band-like credit first), the album. */
const AlsoCalledTargets = (view) => {
  if (!view || !view.track) return [];
  const rows = [{
    id: 'song', label: 'song', name: view.track.title || 'this song',
    aliases: view.track.aliases || [],
    target: { target_type: 'track', target_track_id: view.track.id },
  }];
  (view.artists || []).forEach((a) => rows.push({
    id: `artist:${a.key}`, label: a.credit ? 'whole credit' : 'artist', name: a.name,
    aliases: a.aliases || [],
    target: { target_type: 'artist', target_name: a.name },
  }));
  if (view.album) {
    const target = { target_type: 'album', target_name: view.album.name };
    if (view.album.artist_name) target.target_artist_name = view.album.artist_name;
    rows.push({ id: 'album', label: 'album', name: view.album.name, aliases: view.album.aliases || [], target });
  }
  return rows;
};

/* Put a saved alias on row `rowId` (and off every other row: a replaced
 * name moves). */
const AlsoCalledPut = (lists, rowId, alias) => {
  const next = {};
  Object.keys(lists).forEach((k) => { next[k] = (lists[k] || []).filter((a) => a.id !== alias.id); });
  next[rowId] = (next[rowId] || []).concat([alias]);
  return next;
};

const AlsoCalledDrop = (lists, aliasId) => {
  const next = {};
  Object.keys(lists).forEach((k) => { next[k] = (lists[k] || []).filter((a) => a.id !== aliasId); });
  return next;
};

const AlsoCalledChip = ({ alias, onRemove }) => {
  const mb = alias.source === 'musicbrainz';
  return (
    <span title={mb ? 'from MusicBrainz' : `added by ${alias.added_by}`}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 2, fontSize: 12, lineHeight: '18px',
                   padding: alias.can_remove ? '2px 2px 2px 10px' : '2px 10px', borderRadius: 'var(--r-full)',
                   border: '1px solid var(--border)',
                   background: mb ? 'transparent' : 'var(--brand-soft)',
                   color: mb ? 'var(--fg-muted)' : 'var(--brand-ink)' }}>
      {alias.alias}
      {alias.can_remove && (
        <button type="button" title={`remove '${alias.alias}'`} onClick={onRemove}
                style={{ font: 'inherit', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                         width: 18, height: 18, padding: 0, border: 0, borderRadius: 'var(--r-full)',
                         background: 'transparent', color: 'inherit', cursor: 'pointer' }}>
          <Icon name="x" size={12}/>
        </button>
      )}
    </span>
  );
};

const AlsoCalledSection = ({ track, fire }) => {
  const say = fire || (() => {});
  const [view, setView] = React.useState(null);
  const [lists, setLists] = React.useState({});
  const [drafts, setDrafts] = React.useState({});
  const [pending, setPending] = React.useState(null);   // { row, text, existing } — a replace question
  React.useEffect(() => {
    let live = true;
    setView(null); setLists({}); setDrafts({}); setPending(null);
    apiGet(`/api/music/aliases/for-track/${track.id}`, { quiet: true })
      .then((d) => {
        if (!live || !d || !d.track) return;
        const seeded = {};
        AlsoCalledTargets(d).forEach((r) => { seeded[r.id] = r.aliases; });
        setView(d); setLists(seeded);
      })
      .catch(() => { /* an older server without the route: no section */ });
    return () => { live = false; };
  }, [track.id]);
  if (!view) return null;
  const rows = AlsoCalledTargets(view);
  const setDraft = (rowId, value) => setDrafts((d) => Object.assign({}, d, { [rowId]: value }));
  const failed = (verb, e) => {
    if (typeof reportMutationFailure === 'function') reportMutationFailure(say, verb, e);
    else say(`${verb} failed`);
  };

  const add = async (row, text, replace) => {
    const alias = (text || '').trim();
    if (!alias) return;
    try {
      const r = await apiPost('/api/music/aliases', Object.assign({}, row.target, { alias, replace: !!replace }));
      if (!r || !r.alias) return;
      setLists((cur) => AlsoCalledPut(cur, row.id, r.alias));
      setDraft(row.id, '');
      const shadow = (r.shadows || [])[0];
      if (r.status === 'exists') say(`'${r.alias.alias}' already means ${row.name}`);
      else if (shadow) say(`'${r.alias.alias}' will now play ${row.name} instead of ${shadow.name}.`);
      else say(`'${r.alias.alias}' now also means ${row.name}`);
    } catch (e) {
      const d = e && e.detail && e.detail.detail;
      if (e && e.status === 409 && d && d.code === 'alias_taken') {
        if (d.can_replace) setPending({ row, text: alias, existing: d.existing || {} });
        else say('Only an admin can change a name someone else added.');
        return;
      }
      if (d && typeof d === 'object' && typeof d.message === 'string') { say(d.message); return; }
      failed('add name', e);
    }
  };

  const remove = async (row, alias) => {
    try {
      await apiDelete(`/api/music/aliases/${alias.id}`);
      setLists((cur) => AlsoCalledDrop(cur, alias.id));
      say(`removed '${alias.alias}'`);
    } catch (e) {
      if (e && e.status === 403 && !e.loginPrompted && !e.authCancelled) {
        say('Only an admin can remove a name someone else added.');
        return;
      }
      failed('remove name', e);
    }
  };

  const ex = pending ? pending.existing : null;
  return (
    <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)', display: 'grid', gap: 10 }}>
      <div className="label">also called</div>
      {rows.map((row) => {
        const draft = drafts[row.id] || '';
        return (
          <div key={row.id} style={{ display: 'grid', gap: 4 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'baseline', minWidth: 0 }}>
              <span style={{ fontSize: 11, color: 'var(--fg-faint)', flexShrink: 0 }}>{row.label}</span>
              <span style={{ fontSize: 12, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.name}</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              {(lists[row.id] || []).map((a) => (
                <AlsoCalledChip key={a.id} alias={a} onRemove={() => remove(row, a)}/>
              ))}
              <input name={`also-called-${row.id}`} value={draft} placeholder="add a name…" maxLength={120}
                     aria-label={`another name for ${row.name}`}
                     onChange={(e) => setDraft(row.id, e.target.value)}
                     onKeyDown={(e) => {
                       if (e.key === 'Enter') { e.preventDefault(); add(row, draft, false); }
                       if (e.key === 'Escape') setDraft(row.id, '');
                     }}
                     style={{ font: 'inherit', fontSize: 12, height: 24, padding: '0 8px', minWidth: 110, flex: '1 1 110px',
                              borderRadius: 'var(--r-full)', border: '1px dashed var(--border)',
                              background: 'var(--card)', color: 'var(--fg)' }}/>
            </div>
          </div>
        );
      })}
      {pending && (
        <DeleteConfirmDialog title={`Replace '${pending.text}'?`} icon="repeat" confirmLabel="Replace"
                             onCancel={() => setPending(null)}
                             onConfirm={() => { const p = pending; setPending(null); add(p.row, p.text, true); }}>
          <div>
            {`'${ex.alias || pending.text}' already means ${ex.target_name || 'something else'}`
              + ` (${AlsoCalledTypeWord[ex.target_type] || 'name'}). Replace it?`}
          </div>
          <div>{`It will mean ${pending.row.name} instead.`}</div>
        </DeleteConfirmDialog>
      )}
    </div>
  );
};

/* The Stats tab's card: how many names, and where the opt-in MusicBrainz
 * lookup is (GET /api/music/aliases/status). */
const AlsoCalledFetchLine = (f) => {
  const n = (x) => Number(x || 0).toLocaleString('en-US');
  if (!f || f.enabled === null || f.enabled === undefined) return 'MusicBrainz lookup: status unknown';
  if (f.enabled === false || f.state === 'off') return 'MusicBrainz lookup: off — Settings → Advanced → Library';
  if (f.state === 'offline') return 'MusicBrainz lookup: paused — offline';
  if (f.state === 'rate_limited') return 'MusicBrainz lookup: paused — MusicBrainz asked to slow down';
  if (f.state === 'error') return `MusicBrainz lookup: error${f.last_error ? ` — ${f.last_error}` : ''}`;
  if (f.state === 'done' || (f.artists_total > 0 && f.checked >= f.artists_total)) return 'MusicBrainz lookup: done';
  return `MusicBrainz lookup: checking — ${n(f.checked)} of ${n(f.artists_total)} artists`;
};

const AlsoCalledStat = () => {
  const { data } = useApiObject('/api/music/aliases/status', { quiet: true });
  if (!data) return null;
  const household = data.household || 0;
  const mb = data.musicbrainz || 0;
  return (
    <Stat label="also-called names" value={household + mb}
          sub={`${household} household · ${mb} from MusicBrainz · ${AlsoCalledFetchLine(data.fetch)}`}/>
  );
};

/* ---- Drawer (track detail) -------------------------------- */
const TRACK_TAG_FIELDS = ['title', 'artist', 'album'];
const _trackFieldStyle = {
  font: 'inherit', fontSize: 13, height: 28, padding: '0 8px', borderRadius: 'var(--r-sm)',
  border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)', width: '100%',
};

/* The tag fields whose trimmed form value differs from the row — what a
 * save PATCHes. An emptied field goes as null (clears the tag). */
const trackTagChanges = (form, track) => {
  const changes = {};
  for (const k of TRACK_TAG_FIELDS) {
    const next = (form[k] || '').trim() || null;
    if (next !== (track[k] || null)) changes[k] = next;
  }
  return changes;
};

/* A library track's source: the provider plugin's slug that stamped it, or
 * null for none. No source used to read "manual" in the drawer, the same
 * word as added-via's "manual" beside it, so every file the library found
 * on disk read "manual manual" (the app's trackSource, MusicBits.kt). */
const trackSource = (source) => (typeof source === 'string' && source.trim()) || null;

/* The track drawer. The header's title / artist / album flip into an
 * edit form (F-024: a mistagged track could never be fixed from the
 * dashboard); `onEdit(track, changes)` PATCHes only the changed tags. */
const Drawer = ({ track, rooms, onClose, onDelete, onEdit, onPlayInRoom, onQueueInRoom, onBrowserPlay, onQueueTrack, fire }) => {
  const [alsoFile, setAlsoFile] = React.useState(false);
  const [room, setRoom] = React.useState(rooms[0] || null);
  const [editing, setEditing] = React.useState(false);
  const [form, setForm] = React.useState({ title: '', artist: '', album: '' });
  const [saving, setSaving] = React.useState(false);
  React.useEffect(() => {
    setAlsoFile(false);
    setRoom(rooms[0] || null);
  }, [track?.id, rooms.join(',')]);
  React.useEffect(() => { setEditing(false); }, [track?.id]);
  if (!track) return null;
  const startEdit = () => {
    setForm({ title: track.title || '', artist: track.artist || '', album: track.album || '' });
    setEditing(true);
  };
  const changes = editing ? trackTagChanges(form, track) : {};
  const dirty = Object.keys(changes).length > 0;
  const saveEdit = async () => {
    if (!dirty) { setEditing(false); return; }
    setSaving(true);
    try {
      if (await onEdit(track, changes)) setEditing(false);
    } finally { setSaving(false); }
  };
  const tagInput = (k) => (
    <input key={k} value={form[k]} placeholder={k} style={_trackFieldStyle}
           onChange={e => setForm({ ...form, [k]: e.target.value })}
           onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') setEditing(false); }}/>
  );
  return (
    <>
      <div onClick={onClose}
           style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.16)',
                    backdropFilter: 'blur(2px)', zIndex: 40 }}/>
      <aside style={{ position: 'fixed', top: 0, right: 0, height: 'var(--app-h)', width: 420, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 41,
                      display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
        <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border)' }}>
          <div className="eyebrow">track · #{track.id}</div>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div style={{ padding: 16, display: 'flex', gap: 14, alignItems: 'center', borderBottom: '1px solid var(--border-soft)' }}>
          <CoverArt src={libraryCoverUrl(track.id)} size={72}/>
          {editing ? (
            <div style={{ minWidth: 0, flex: 1, display: 'grid', gap: 6 }}>
              {TRACK_TAG_FIELDS.map(tagInput)}
              <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                <Button icon="x" disabled={saving} onClick={() => setEditing(false)}>cancel</Button>
                <Button variant="primary" icon="check" disabled={saving || !dirty} onClick={saveEdit}>
                  {saving ? 'saving…' : 'save'}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{track.title || 'unknown title'}</div>
                <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>{track.artist || 'unknown artist'}</div>
                <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>{track.album || '—'}</div>
              </div>
              {onEdit && <IconButton name="pencil" title="edit tags" onClick={startEdit}/>}
            </>
          )}
        </div>

        {/* Label over value, two to a line; the path gets the full width.
            This is the one place added / via / source show — the library
            table leaves them out. */}
        <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '10px 12px', fontSize: 12 }}>
          {[
            ['duration',  <div className="mono">{fmtDur(track.duration_sec)}</div>],
            ['added',     <div className="mono">{relTime(track.added_at)}</div>],
            ['added via', <div><Pill tone={track.added_via === 'voice' ? 'live' : 'idle'}>{track.added_via || '—'}</Pill></div>],
            ['source',    trackSource(track.source)
                            ? <div><Pill tone="live">{trackSource(track.source)}</Pill></div>
                            : <div className="mono">—</div>],
            ['enriched',  <div className="mono">{track.enriched_at ? relTime(track.enriched_at) : '—'}</div>],
            ['source id', <div className="mono" style={{ color: 'var(--fg-muted)', wordBreak: 'break-all' }}>{track.source_id || '—'}</div>],
          ].map(([label, value]) => (
            <div key={label}>
              <div className="label" style={{ marginBottom: 2 }}>{label}</div>
              {value}
            </div>
          ))}
          <div style={{ gridColumn: '1 / -1' }}>
            <div className="label" style={{ marginBottom: 2 }}>path</div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', wordBreak: 'break-all' }}>{track.file_path}</div>
          </div>
        </div>

        {/* Other names for the song, its artists and its album — an
            editable list per target (V019; AlsoCalledSection above). */}
        <AlsoCalledSection track={track} fire={fire}/>

        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-soft)' }}>
          <div className="label" style={{ marginBottom: 6 }}>play in room</div>
          <RoomChips rooms={rooms} room={room} onPick={setRoom}/>
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <Button variant="primary" icon="play" disabled={!room}
                    onClick={() => room && onPlayInRoom(track, room)}>play in {room || 'a room'}</Button>
            {/* Appends to what the room is already playing, instead of
                replacing it — the one a guest may use while the room plays. */}
            {onQueueInRoom && (
              <Button icon="list-end" disabled={!room}
                      onClick={() => room && onQueueInRoom(track, room)}>add to {room || 'a room'}'s queue</Button>
            )}
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
                  style={{ background: 'var(--err-soft)', color: 'var(--err-ink)', borderColor: 'transparent' }}>
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
    list, items, total, loading,
    q, source, sort, favoritedOnly,
    setQ, setSource, setSort, setFavoritedOnly,
  } = lib;
  const filtered = !!q || source !== 'all' || favoritedOnly;
  // Up next: a song played from a searched or filtered library goes on with
  // the rest of what is shown; from the whole library, with songs like it.
  const rowContext = filtered
    ? { kind: 'ids', name: q ? `results for "${q}"` : (favoritedOnly ? 'favorites' : 'this list') }
    : null;

  const [selected, setSelected] = React.useState(() => new Set());
  const [bulkPid, setBulkPid] = React.useState('');
  const toggleSel = (id) => setSelected(prev => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  // "Select all" means every LOADED row — what is on screen, never the
  // rows further down that haven't been fetched. The checkbox says so.
  const allSelected = items.length > 0 && items.every(t => selected.has(t.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map(t => t.id)));
  // A new list (search, filter, sort) starts with nothing selected.
  React.useEffect(() => { setSelected(new Set()); }, [q, source, sort, favoritedOnly]);
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
        <table className="tbl lib-tbl">
          <thead><tr>
            <th style={{ width: 30 }}><input type="checkbox" checked={allSelected} onChange={toggleAll}
                title={`select the ${items.length} loaded track${items.length === 1 ? '' : 's'} (rows further down load as you scroll)`}/></th>
            <th style={{ width: 40 }}></th>
            <th style={{ width: '100%' }}>track</th>
            <th>duration</th>
            <th className="actions"></th>
          </tr></thead>
          <tbody>
            {items.map(t => (
              <tr key={t.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(t)}>
                <td onClick={e => e.stopPropagation()}><input type="checkbox" checked={selected.has(t.id)} onChange={() => toggleSel(t.id)}/></td>
                {/* On a phone the cover moves here, under the play button,
                    so the track cell keeps every pixel it had (styles.css,
                    "Library table"); the copy that isn't shown never loads. */}
                <td onClick={e => e.stopPropagation()}>
                  <span className="lib-play">
                    <span className="lib-cover-phone"><CoverArt src={libraryCoverUrl(t.id)} size={32} icon={null}/></span>
                    <IconButton name="play" onClick={() => onBrowserPlay(t, rowContext, items)} title="play in this browser"/>
                  </span>
                </td>
                {/* The cover, then title, artist and album stacked in one
                    cell so each gets the full column width. Added / via /
                    source live in the track drawer (row click or the info
                    button). */}
                <td style={{ paddingTop: 8, paddingBottom: 8 }}>
                  <div className="lib-track">
                    <span className="lib-cover-desk"><CoverArt src={libraryCoverUrl(t.id)} size={40}/></span>
                    <div className="lib-track-text">
                      <div style={{ fontWeight: 500 }}>{t.title || '—'}</div>
                      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{t.artist || '—'}</div>
                      <div style={{ fontSize: 12, color: 'var(--fg-faint)' }}>{t.album || '—'}</div>
                    </div>
                  </div>
                </td>
                <td className="mono">{fmtDur(t.duration_sec)}</td>
                <td className="actions" onClick={e => e.stopPropagation()}>
                  <div className="lib-acts">
                    <MusicSongMenu track={t} fire={fire}/>
                    <IconButton name="heart" onClick={() => onToggleFavorite(t)}
                                style={t.favorited ? { color: 'var(--brand-ink)' } : undefined}
                                title={t.favorited ? 'unfavorite' : 'favorite'}/>
                    <IconButton name="plus" onClick={() => onAddToPlaylist(t)}
                                title="add to playlist…"/>
                    <IconButton name="download" title="save to this device"
                                onClick={() => deviceDownload(`/api/music/library/${t.id}/audio?download=1`)}/>
                    <IconButton name="info" onClick={() => onSelect(t)} title="track details"/>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!showLoading && (
        <div style={{ borderTop: '1px solid var(--border-soft)' }}>
          <PagedListFooter list={list} noun="tracks"/>
        </div>
      )}
    </>
  );
};

/* ---- Artists tab ------------------------------------------ */
/* The library by performer (owner, 2026-11-11): GET /api/music/artists,
 * one page at a time like the Library tab, searched by the server (the
 * library search's rules: "ti" finds T.I.) and sorted by name or track
 * count. A credit naming several performers ("Jeezy, T.I.") counts for
 * each of them. A row opens ArtistDrawer. */
const ArtistsTab = ({ onOpen }) => {
  const [q, setQ] = React.useState('');
  const [sort, setSort] = React.useState('name');
  const qDebounced = useDebouncedValue(q, 250);
  const path = React.useMemo(() => {
    const params = new URLSearchParams();
    if (qDebounced) params.set('q', qDebounced);
    params.set('sort', sort);
    return `/api/music/artists?${params.toString()}`;
  }, [qDebounced, sort]);
  const list = usePagedList(path, {
    pageSize: 50, key: (a) => a.name, eventTypes: ['library.indexer.changed'],
  });
  const items = list.items || [];
  const total = list.total ?? 0;
  const showLoading = list.loading && items.length === 0;
  const showEmpty = !list.loading && items.length === 0;
  return (
    <>
      <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 360 }}>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="search artists…"
                 aria-label="search artists"
                 style={{ font: 'inherit', fontSize: 13, width: '100%', height: 30, padding: '0 10px 0 28px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <span style={{ position: 'absolute', left: 9, top: 8, color: 'var(--fg-subtle)', pointerEvents: 'none' }}>
            <Icon name="search" size={13}/>
          </span>
        </div>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--fg-muted)' }}>
          sort
          <select value={sort} onChange={e => setSort(e.target.value)}
            style={{ font: 'inherit', fontSize: 12, height: 26, padding: '0 8px', borderRadius: 'var(--r-sm)',
                     border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
            <option value="name">name</option>
            <option value="tracks">most tracks</option>
          </select>
        </label>
        <div style={{ marginLeft: 'auto' }} className="mono">
          <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{total} artist{total === 1 ? '' : 's'}</span>
        </div>
      </div>
      {showLoading ? (
        <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading artists…</div>
      ) : showEmpty ? (
        <Empty glyph="headphones" title={q ? 'no artists match' : 'no artists yet'}
               sub={q ? `q = “${q}”` : 'tracks with an artist tag show up here'}
               action={q ? <Button icon="x" onClick={() => setQ('')}>clear search</Button> : null}/>
      ) : (
        <div className="artist-list">
          {items.map((a, i) => (
            <button key={a.name} type="button" className="artist-row" onClick={() => onOpen(a)}
                    style={{ display: 'grid', gridTemplateColumns: '40px 1fr auto', gap: 12, alignItems: 'center',
                             width: '100%', padding: '8px 16px', font: 'inherit', textAlign: 'left',
                             background: 'transparent', color: 'var(--fg)', border: 0, cursor: 'pointer',
                             borderBottom: i === items.length - 1 ? 'none' : '1px solid var(--border-soft)' }}>
              <CoverArt src={a.cover_track_id != null ? libraryCoverUrl(a.cover_track_id) : null} size={40}/>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</div>
                <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                  {a.track_count} track{a.track_count === 1 ? '' : 's'}
                  {a.album_count ? ` · ${a.album_count} album${a.album_count === 1 ? '' : 's'}` : ''}
                </div>
              </div>
              <Icon name="chevron-right" size={14}/>
            </button>
          ))}
        </div>
      )}
      {!showLoading && (
        <div style={{ borderTop: '1px solid var(--border-soft)' }}>
          <PagedListFooter list={list} noun="artists"/>
        </div>
      )}
    </>
  );
};

/* A random order of `ids` (Fisher-Yates), for "shuffle". */
const shuffledIds = (ids) => {
  const out = [...ids];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
};

/* One artist (GET /api/music/artist): their albums as chips ("all songs"
 * first), the songs of the chosen one in album order (the library list,
 * ?artist=&album=&sort=album, loaded as it scrolls), and the same actions
 * as a playlist: play / shuffle / add to a room's queue, and play or queue
 * in this browser. A room gets every track id (the server's list, at
 * most ROOM_TRACK_CAP at once); this browser the songs loaded so far. */
const ROOM_TRACK_CAP = 500;
const ArtistDrawer = ({ artist, rooms, onClose, onPlayTracks, onQueueTracksInRoom,
                        onBrowserPlayTracks, onBrowserQueueTracks, onSelectTrack, onBrowserPlay, onQueueTrack, fire = null }) => {
  const [room, setRoom] = React.useState(rooms[0] || null);
  const [album, setAlbum] = React.useState(null);     // null = all songs; '' = no album
  React.useEffect(() => { setRoom(rooms[0] || null); }, [artist?.name, rooms.join(',')]);
  React.useEffect(() => { setAlbum(null); }, [artist?.name]);
  const name = artist ? artist.name : null;
  const detailPath = name
    ? `/api/music/artist?${new URLSearchParams(album == null ? { name } : { name, album }).toString()}`
    : null;
  const { data: detail } = useApiObject(detailPath, { eventTypes: ['library.indexer.changed'] });
  const songsPath = React.useMemo(() => {
    if (!name) return null;
    const p = new URLSearchParams({ artist: name, sort: 'album' });
    if (album != null) p.set('album', album);
    return `/api/music/library?${p.toString()}`;
  }, [name, album]);
  const songs = usePagedList(songsPath, { pageSize: 100, eventTypes: ['library.indexer.changed'] });
  if (!artist) return null;
  const ids = (detail && detail.track_ids) || [];
  const albums = (detail && detail.albums) || [];
  const what = album == null ? artist.name : `${album || 'songs with no album'} · ${artist.name}`;
  // Up next: what a song played from here goes on with.
  const context = album == null ? { kind: 'artist', ref: artist.name, name: artist.name }
    : album ? { kind: 'album', ref: `${artist.name}|${album}`, name: album }
      : { kind: 'ids', name: what };
  const chip = (key, label, count, active, onClick) => (
    <button key={key} type="button" onClick={onClick}
      style={{ font: 'inherit', fontSize: 12, cursor: 'pointer', padding: '4px 10px',
               borderRadius: 'var(--r-full)', border: '1px solid var(--border)',
               background: active ? 'var(--brand-soft)' : 'var(--card)',
               color: active ? 'var(--brand-ink)' : 'var(--fg)' }}>
      {label}{count != null ? <span className="mono" style={{ marginLeft: 6, fontSize: 11, color: 'var(--fg-muted)' }}>{count}</span> : null}
    </button>
  );
  return (
    <>
      <div onClick={onClose}
           style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.16)',
                    backdropFilter: 'blur(2px)', zIndex: 40 }}/>
      <aside role="dialog" aria-label={`artist ${artist.name}`}
             style={{ position: 'fixed', top: 0, right: 0, height: 'var(--app-h)', width: 460, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 41,
                      display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border)' }}>
          <div className="eyebrow">artist</div>
          <IconButton name="x" onClick={onClose} title="close"/>
        </div>
        <div style={{ padding: 16, display: 'flex', gap: 14, alignItems: 'center', borderBottom: '1px solid var(--border-soft)' }}>
          <CoverArt src={artist.cover_track_id != null ? libraryCoverUrl(artist.cover_track_id) : null} size={56}/>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{artist.name}</div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
              {artist.track_count} track{artist.track_count === 1 ? '' : 's'}
              {artist.album_count ? ` · ${artist.album_count} album${artist.album_count === 1 ? '' : 's'}` : ''}
            </div>
          </div>
        </div>

        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)' }}>
          <div className="label" style={{ marginBottom: 6 }}>albums</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {chip('*all', 'all songs', artist.track_count, album == null, () => setAlbum(null))}
            {albums.map(al => chip(`a:${al.name ?? ''}`, al.name || 'no album', al.track_count,
                                   album === (al.name ?? ''), () => setAlbum(al.name ?? '')))}
          </div>
        </div>

        <TrackSetActions rooms={rooms} room={room} onRoom={setRoom} ids={ids} songs={songs} what={what}
                         context={context}
                         onClose={onClose} onPlayTracks={onPlayTracks} onQueueTracksInRoom={onQueueTracksInRoom}
                         onBrowserPlayTracks={onBrowserPlayTracks} onBrowserQueueTracks={onBrowserQueueTracks}/>
        <TrackSetSongs fire={fire} songs={songs} onSelectTrack={onSelectTrack} onBrowserPlay={onBrowserPlay}
                       onQueueTrack={onQueueTrack} context={context}
                       sub={(t) => [t.album, t.artist !== artist.name ? t.artist : null].filter(Boolean).join(' · ')}/>
      </aside>
    </>
  );
};

/* The actions of a drawer of many songs (Artists, Genres): play / shuffle /
 * add to queue in a room, which gets every track id the server gave (`ids`,
 * at most ROOM_TRACK_CAP at once), and play / shuffle / queue in this
 * browser, which takes the songs loaded so far (`songs`, a paged list). */
const TrackSetActions = ({ rooms, room, onRoom, ids, songs, what, context = null, onClose, onPlayTracks,
                           onQueueTracksInRoom, onBrowserPlayTracks, onBrowserQueueTracks }) => {
  const loaded = songs.items || [];
  const roomIds = (shuffle) => (shuffle ? shuffledIds(ids) : ids).slice(0, ROOM_TRACK_CAP);
  const capNote = ids.length > ROOM_TRACK_CAP
    ? `a room takes ${ROOM_TRACK_CAP} at a time — the first ${ROOM_TRACK_CAP} (or a random ${ROOM_TRACK_CAP} when shuffled)`
    : null;
  return (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)' }}>
      <div className="label" style={{ marginBottom: 6 }}>play in room</div>
      <RoomChips rooms={rooms} room={room} onPick={onRoom}/>
      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <Button variant="primary" icon="play" disabled={!room || ids.length === 0}
                onClick={() => { if (room) { onPlayTracks(roomIds(false), room, what, false, context); onClose(); } }}>
          play
        </Button>
        <Button icon="shuffle" disabled={!room || ids.length === 0}
                onClick={() => { if (room) { onPlayTracks(roomIds(true), room, what, true, context); onClose(); } }}>
          shuffle
        </Button>
        <Button icon="list-plus" disabled={!room || ids.length === 0}
                onClick={() => { if (room) onQueueTracksInRoom(roomIds(false), room, what); }}>
          add to queue
        </Button>
      </div>
      {capNote && <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 8 }}>{capNote}</div>}
      <div className="label" style={{ margin: '12px 0 6px' }}>this browser</div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button icon="headphones" disabled={loaded.length === 0}
                onClick={() => onBrowserPlayTracks(loaded, what, context)}>play here</Button>
        <Button icon="shuffle" disabled={loaded.length === 0}
                onClick={() => onBrowserPlayTracks(shuffledIds(loaded), what, context)}>shuffle here</Button>
        <Button icon="list-plus" disabled={loaded.length === 0}
                onClick={() => onBrowserQueueTracks(loaded, what)}>queue here</Button>
      </div>
      {songs.hasMore && (
        <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 8 }}>
          this browser takes the {loaded.length} songs loaded so far — scroll the list for more
        </div>
      )}
    </div>
  );
};

/* The songs of such a drawer, loaded as they scroll: a tap opens the track
 * drawer, the buttons play or queue one in this browser. `sub(t)` is the
 * line under the title. Play starts Up next on `context`, the drawer's
 * list (the artist, album or genre; the loaded songs for an `ids` one). */
const TrackSetSongs = ({ songs, sub, onSelectTrack, onBrowserPlay, onQueueTrack, context = null, fire = null }) => {
  const loaded = songs.items || [];
  return (
    <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
      {songs.loading && loaded.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>
      ) : loaded.length === 0 ? (
        <Empty glyph="headphones" title="no songs" sub="nothing in the library for this choice"/>
      ) : (
        <div style={{ padding: '4px 0' }}>
          {loaded.map((t, i) => (
            <div key={t.id}
                 style={{ display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center', gap: 8,
                          padding: '8px 16px',
                          borderBottom: i === loaded.length - 1 ? 'none' : '1px solid var(--border-soft)' }}>
              <button type="button" onClick={() => onSelectTrack(t)}
                      style={{ minWidth: 0, font: 'inherit', textAlign: 'left', background: 'transparent',
                               border: 0, padding: 0, color: 'var(--fg)', cursor: 'pointer' }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {t.title || (t.file_path || '').split('/').pop()}
                </div>
                <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {sub(t) || '—'}
                </div>
              </button>
              <div style={{ display: 'flex', gap: 2 }}>
                <IconButton name="play" onClick={() => onBrowserPlay(t, context, loaded)} title="play in this browser"/>
                <MusicSongMenu track={t} fire={fire}/>
              </div>
            </div>
          ))}
          <PagedListFooter list={songs} noun="songs" compact/>
        </div>
      )}
    </div>
  );
};

/* ---- Genres tab (owner, 2026-11-12) ------------------------- */
/* The library by genre: GET /api/music/genres, one page at a time like
 * Artists, searched and sorted by the server. Genres come from the files'
 * own tags. For tracks that were in the library before genres were read,
 * the server reads them in the background; until it is done the tab says
 * so ("reading genres from your files… N of M") and re-reads itself every
 * GENRE_READ_POLL_MS, rather than passing a partial list off as the whole
 * one. Read tracks whose tags name no genre are one last row, "no genre",
 * so nothing is hidden. Songs song recognition matched get a genre from
 * MusicBrainz in the background; until they are asked, that row says how
 * many are waiting ("· 4 waiting for MusicBrainz"; the server sends 0 when
 * the lookup can't run). A row opens GenreDrawer. */
const GENRE_READ_POLL_MS = 10000;
const genreReadingLine = (page) => (page && page.reading
  ? `reading genres from your files… ${fmtCount(page.tags_read)} of ${fmtCount(page.tags_total)}`
  : null);
const GenresTab = ({ onOpen }) => {
  const [q, setQ] = React.useState('');
  const [sort, setSort] = React.useState('name');
  const qDebounced = useDebouncedValue(q, 250);
  const path = React.useMemo(() => {
    const params = new URLSearchParams();
    if (qDebounced) params.set('q', qDebounced);
    params.set('sort', sort);
    return `/api/music/genres?${params.toString()}`;
  }, [qDebounced, sort]);
  const list = usePagedList(path, {
    pageSize: 50, key: (g) => g.key, eventTypes: ['library.indexer.changed'],
  });
  const page = list.extra;
  const reading = !!(page && page.reading);
  const reload = list.reload;
  React.useEffect(() => {
    if (!reading) return undefined;
    const t = setInterval(() => reload({ quietRead: true }), GENRE_READ_POLL_MS);
    return () => clearInterval(t);
  }, [reading, reload]);
  const items = list.items || [];
  const total = list.total ?? 0;
  const untagged = (page && page.untagged_count) || 0;
  const mbPending = (page && page.musicbrainz_pending) || 0;
  const showLoading = list.loading && items.length === 0 && !page;
  const showNoGenre = !q && !list.hasMore && untagged > 0;
  const showEmpty = !list.loading && items.length === 0 && !showNoGenre;
  const readingLine = genreReadingLine(page);
  const row = (g, last, muted) => (
    <button key={`g:${g.key}`} type="button" className="artist-row" onClick={() => onOpen(g)}
            style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, alignItems: 'center',
                     width: '100%', padding: '10px 16px', font: 'inherit', textAlign: 'left',
                     background: 'transparent', color: muted ? 'var(--fg-muted)' : 'var(--fg)',
                     border: 0, cursor: 'pointer',
                     borderBottom: last ? 'none' : '1px solid var(--border-soft)' }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.name}</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
          {g.track_count} track{g.track_count === 1 ? '' : 's'}
          {g.note ? ` · ${g.note}` : ''}
        </div>
      </div>
      <Icon name="chevron-right" size={14}/>
    </button>
  );
  return (
    <>
      <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 360 }}>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="search genres…"
                 aria-label="search genres"
                 style={{ font: 'inherit', fontSize: 13, width: '100%', height: 30, padding: '0 10px 0 28px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <span style={{ position: 'absolute', left: 9, top: 8, color: 'var(--fg-subtle)', pointerEvents: 'none' }}>
            <Icon name="search" size={13}/>
          </span>
        </div>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--fg-muted)' }}>
          sort
          <select value={sort} onChange={e => setSort(e.target.value)}
            style={{ font: 'inherit', fontSize: 12, height: 26, padding: '0 8px', borderRadius: 'var(--r-sm)',
                     border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)' }}>
            <option value="name">name</option>
            <option value="tracks">most tracks</option>
          </select>
        </label>
        <div style={{ marginLeft: 'auto' }} className="mono">
          <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{total} genre{total === 1 ? '' : 's'}</span>
        </div>
      </div>
      {readingLine && (
        <div role="status" style={{ padding: '8px 16px', fontSize: 12, color: 'var(--fg-muted)',
                                    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                                    borderBottom: '1px solid var(--border-soft)' }}>
          <StatusDot tone="brand" live/>
          <span>{readingLine}</span>
          <span>· the list fills in as it goes</span>
        </div>
      )}
      {showLoading ? (
        <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading genres…</div>
      ) : showEmpty ? (
        <Empty glyph="headphones"
               title={q ? 'no genres match' : reading ? 'no genres found yet' : 'no genres yet'}
               sub={q ? `q = “${q}”` : reading ? 'genres show up here as your files are read'
                 : 'songs whose tags name a genre show up here'}
               action={q ? <Button icon="x" onClick={() => setQ('')}>clear search</Button> : null}/>
      ) : (
        <div className="artist-list">
          {items.map((g, i) => row(g, i === items.length - 1 && !showNoGenre, false))}
          {showNoGenre && row({ key: '', name: 'no genre', track_count: untagged,
                                note: mbPending > 0 ? `${fmtCount(mbPending)} waiting for MusicBrainz` : null }, true, true)}
        </div>
      )}
      {!showLoading && (
        <div style={{ borderTop: '1px solid var(--border-soft)' }}>
          <PagedListFooter list={list} noun="genres"/>
        </div>
      )}
    </>
  );
};

/* One genre (GET /api/music/genre): its songs by artist (the library list,
 * ?genre=&sort=artist, loaded as it scrolls) with the Artists drawer's
 * actions. `genre.key` '' is the tracks whose tags name no genre. */
const GenreDrawer = ({ genre, rooms, onClose, onPlayTracks, onQueueTracksInRoom,
                       onBrowserPlayTracks, onBrowserQueueTracks, onSelectTrack, onBrowserPlay, onQueueTrack, fire = null }) => {
  const [room, setRoom] = React.useState(rooms[0] || null);
  React.useEffect(() => { setRoom(rooms[0] || null); }, [genre?.key, rooms.join(',')]);
  const key = genre ? genre.key : null;
  const detailPath = key != null ? `/api/music/genre?${new URLSearchParams({ key }).toString()}` : null;
  const { data: detail } = useApiObject(detailPath, { eventTypes: ['library.indexer.changed'] });
  const songsPath = React.useMemo(() => (key == null ? null
    : `/api/music/library?${new URLSearchParams({ genre: key, sort: 'artist' }).toString()}`), [key]);
  const songs = usePagedList(songsPath, { pageSize: 100, eventTypes: ['library.indexer.changed'] });
  if (!genre) return null;
  const ids = (detail && detail.track_ids) || [];
  const count = detail ? detail.track_count : genre.track_count;
  // "no genre" ('') isn't a genre to draw from: its loaded songs are the list.
  const context = genre.key ? { kind: 'genre', ref: genre.key, name: genre.name } : { kind: 'ids', name: genre.name };
  return (
    <>
      <div onClick={onClose}
           style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.16)',
                    backdropFilter: 'blur(2px)', zIndex: 40 }}/>
      <aside role="dialog" aria-label={`genre ${genre.name}`}
             style={{ position: 'fixed', top: 0, right: 0, height: 'var(--app-h)', width: 460, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 41,
                      display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border)' }}>
          <div className="eyebrow">genre</div>
          <IconButton name="x" onClick={onClose} title="close"/>
        </div>
        <div style={{ padding: 16, borderBottom: '1px solid var(--border-soft)' }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{genre.name}</div>
          <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            {count} track{count === 1 ? '' : 's'}{genre.key === '' ? ' · their tags name no genre' : ''}
          </div>
        </div>
        <TrackSetActions rooms={rooms} room={room} onRoom={setRoom} ids={ids} songs={songs} what={genre.name}
                         context={context} onClose={onClose} onPlayTracks={onPlayTracks} onQueueTracksInRoom={onQueueTracksInRoom}
                         onBrowserPlayTracks={onBrowserPlayTracks} onBrowserQueueTracks={onBrowserQueueTracks}/>
        <TrackSetSongs fire={fire} songs={songs} onSelectTrack={onSelectTrack} onBrowserPlay={onBrowserPlay}
                       onQueueTrack={onQueueTrack} context={context}
                       sub={(t) => [t.artist, t.album].filter(Boolean).join(' · ')}/>
      </aside>
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
  // room_id only attributes the request in the intent log (nothing
  // plays) — with no rooms it is the web itself, not an invented room.
  const room = rooms[0] || 'web';
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
      reportMutationFailure(fire, 'add', e);
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
        <span className="meta" style={{ color: 'var(--warn-ink)' }}>
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
      {/* How far the house has got with its lyrics (lyrics.jsx). */}
      {typeof LyricsJobsCard === 'function' && <LyricsJobsCard/>}
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
                    {a.error && <div className="mono" style={{ fontSize: 11, color: 'var(--err-ink)', marginTop: 4 }}>{a.error}</div>}
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

/* ---- Room queue tab --------------------------------------- */
/*
 * The room's LIVE queue, editable: reorder by drag (a mouse) or by the
 * row's tap controls (move up / down, play next — a finger), drop an
 * entry, clear it.
 * Unlike the expanded player's queue (this browser's own queue) this is the queue the
 * room's speaker plays from — MPD's — so every client sees the same list and
 * edits land for everyone.
 *
 * Each row carries an "added by <device>" tag when we know who queued it.
 * Deliberately quiet: it lives on the secondary line, greyed, and simply
 * isn't rendered for entries with no record (voice commands, casts from
 * before devices had names). It is NEVER the reason a row is taller.
 *
 * An admin can block a device from editing a queue. The server is the
 * authority (every edit is re-checked), but the read tells us up front, so a
 * blocked device gets disabled controls and a plain explanation instead of a
 * 403 on first click.
 *
 * A GUEST — a phone with no household credential — may change the queue of
 * a room that is playing (owner decision 2026-10-10): reorder, play next,
 * remove (not the song playing now), add from a track's details. Never
 * clear, and a room that isn't playing reads as "isn't playing", with a
 * pair button, rather than a pairing prompt nobody asked for. The read says
 * who is asking (`guest`, `admin`); an admin gets a ban button on an entry
 * a guest added.
 */
/* The shared queue and song-row parts (components.jsx, P5), through
 * wrappers: this file is also drawn on its own (the test harnesses), where
 * they aren't loaded; there the parts are simply left out. */
const MusicSongMenu = (props) => (typeof SongRowMenu === 'function' ? <SongRowMenu {...props}/> : null);
const MusicQueueSec = ({ children }) => (typeof QueueSectionHead === 'function'
  ? <QueueSectionHead className="rq-sec">{children}</QueueSectionHead> : null);
const MusicQueueGrip = (props) => (typeof QueueGrip === 'function'
  ? <QueueGrip {...props}/> : <Icon name="grip-vertical" size={13}/>);

const fmtQueueDur = (sec) => {
  if (sec == null) return '—';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
};

/* A GUEST's presses never open the pair prompt (owner, 2026-10-10). What a
 * guest may do — change a playing room's queue, pause / resume / skip /
 * go back there — is decided by the server, and a refusal of something a
 * guest can't do (the room stopped meanwhile, an admin's ban, the
 * household switched guests off) is told in words where the button was,
 * like the idle room's panel, never by a modal the guest didn't ask for.
 * Pairing stays one press away on the panels that offer it. Outside data.js
 * (a test harness) this is plain apiPost. */
const guestSend = (path, body) => (typeof apiFetch === 'function'
  ? apiFetch(path, { method: 'POST', body: JSON.stringify(body || {}), noPrompt: true })
  : apiPost(path, body));

// How often a guest's Music page re-reads what the rooms are doing: it has
// no live socket to be told.
const GUEST_POLL_MS = 15000;

// What a guest is told when the server refused one of its presses.
const guestRefusalText = (verb, e) => {
  const status = e && e.status;
  if (status === 429) return `${verb}: too many changes from this phone — try again in a minute`;
  const nested = e && e.detail && e.detail.detail;
  const said = typeof nested === 'string' ? nested : null;
  if (status === 401 || (said && /x-device-token|admin session required/i.test(said))) {
    return `${verb}: guests can only change a room that's playing — connect this browser to do more`;
  }
  return `${verb}: ${said || apiErrorText(e)}`;
};

/* Where a tap moves an entry, by finger (phone browsers don't drag). `idx`
 * is the entry's place in the list, `cur` the playing entry's (or -1).
 * The move route takes the place the entry lands at after it has been
 * taken out, so "play next" from ABOVE the playing entry is `cur` itself
 * (everything after it shifts up one) and from below is `cur + 1`. */
const queueTapTarget = (kind, idx, cur, count) => {
  if (kind === 'up') return idx > 0 ? idx - 1 : null;
  if (kind === 'down') return idx < count - 1 ? idx + 1 : null;
  if (kind === 'next') {
    if (cur < 0 || idx === cur) return null;
    const to = idx < cur ? cur : cur + 1;
    return to === idx ? null : to;
  }
  return null;
};

// The Room queue tab's rows are drawn this many at a time ("show more").
const QUEUE_ROWS_STEP = 100;
// Songs played before the one playing that stay drawn above it.
const QUEUE_FOLD_AFTER = 20;
// The room this browser last picked on the Room queue tab.
const QUEUE_ROOM_KEY = 'domovoi.music.queueRoom';
// What makes a room's queue worth re-reading on a now-playing event: its
// state and its song (from the event's per-room value or a now-playing row).
const roomPlaySig = (np) => (np ? `${np.state || ''}|${np.song ? `${np.song.file || ''}|${np.song.title || ''}` : ''}` : '');

/* The Room queue tab. `room` is the Music page's (a room card's queue
 * button picks it); null falls back to a room that's playing.
 *
 * Up next (P4): the songs the room's Up next picked are marked (a sparkle
 * and why: "same artist", "90s", "from road trip") under an "up next"
 * head that holds the room's Autoplay switch; tap any song to play it now
 * (a real jump in the room's queue); "less like this" on a pick takes that
 * artist out of the mix; "save as playlist" keeps the queue. A guest gets
 * the jump, the moves and remove on a playing room — no switch, no "less
 * like this", no saving.
 *
 * The same rows as the player's queue (P5: components.jsx QueueGrip,
 * QueueSectionHead): "now playing", "next", then "up next"; the grip drags
 * or moves a song with the arrow keys. `embedded`: drawn inside the
 * player's queue panel aimed at this room (player.jsx PlayerQueueView),
 * whose head already picks the room, so no room chips. */
const QueueTab = ({ rooms, nowPlaying, npError, npLoading = false, fire, room: pickedRoom, onRoom,
                    embedded = false }) => {
  // Default to the room this browser looked at last (kept per browser), so
  // the queue is read at once rather than after now-playing has answered;
  // else a room that's actually playing — that's the queue you came here to
  // look at. A kept room that no longer exists is dropped once the rooms
  // are known.
  const playingRoom = (nowPlaying.find(np => np.state === 'play') || {}).room_id;
  // Rendered on its own (no `onRoom`), the tab keeps its own pick.
  const [ownRoom, setOwnRoom] = React.useState(null);
  const [kept] = React.useState(() => {
    try { return localStorage.getItem(QUEUE_ROOM_KEY) || null; } catch { return null; }
  });
  const roomsKnown = rooms.length > 0 || !npLoading;
  const keptRoom = kept && (!roomsKnown || rooms.includes(kept)) ? kept : null;
  const room = pickedRoom || ownRoom || keptRoom || playingRoom || rooms[0] || null;
  const setRoom = onRoom || setOwnRoom;
  const chosen = pickedRoom || ownRoom;
  React.useEffect(() => {
    if (!chosen) return;
    try { localStorage.setItem(QUEUE_ROOM_KEY, chosen); } catch { /* kept for the visit */ }
  }, [chosen]);

  const [order, setOrder] = React.useState(null);   // local drag order, or null
  const [busy, setBusy] = React.useState(false);
  const dragFrom = React.useRef(null);
  // Rows drawn: a whole playlist can be a room's queue (1,500 songs), and
  // drawing every one of them is what made the tab crawl on a phone.
  const [shown, setShown] = React.useState(QUEUE_ROWS_STEP);
  const [earlier, setEarlier] = React.useState(false);
  React.useEffect(() => { setShown(QUEUE_ROWS_STEP); setEarlier(false); }, [room]);

  const deviceId = DeviceIdentity.id();
  // Quiet: opening the tab, or switching rooms, is nobody pressing
  // anything. A refused read renders its own state; it never pops the pair
  // prompt (a guest's phone used to get one just for looking).
  const { data: answer, error, refresh } = useApiObject(
    room ? `/api/music/queue/${encodeURIComponent(room)}`
           + `?device_id=${encodeURIComponent(deviceId)}` : null,
    { quiet: true },
  );
  // Another room's answer (the read for a room just picked is still out)
  // is not this room's queue: it would show the old room's songs under the
  // new room's name.
  const queue = answer && (!answer.room_id || answer.room_id === room) ? answer : null;

  // Re-read when THIS room's song or state changed. The event carries
  // every room's now-playing (realtime.py `music.now_playing.changed`):
  // a song changing in another room used to re-read, and re-draw, this
  // room's whole queue. An event without the rooms re-reads, as before.
  const npNow = React.useRef(nowPlaying);
  npNow.current = nowPlaying;
  const seen = React.useRef(null);
  React.useEffect(() => { seen.current = null; }, [room]);
  useStateEvents(['music.now_playing.changed'], (ev) => {
    const all = ev && ev.data;
    if (!room || !all || typeof all !== 'object' || Array.isArray(all)) { refresh(); return; }
    if (seen.current === null) {
      const np = (npNow.current || []).find((x) => x && x.room_id === room);
      seen.current = roomPlaySig(np);
    }
    const now = roomPlaySig(all[room]);
    if (now === seen.current) return;
    seen.current = now;
    refresh();
  });

  // A guest's tab has no live socket to say the queue moved on: re-read it
  // on the same 15 s beat as the page's now-playing (MusicPage).
  const guestTab = !!queue?.guest;
  React.useEffect(() => {
    if (!guestTab) return undefined;
    const t = setInterval(() => {
      try { if (typeof document !== 'undefined' && document.hidden) return; } catch {}
      refresh();
    }, GUEST_POLL_MS);
    return () => clearInterval(t);
  }, [guestTab, room]);

  // Drop the optimistic order whenever the server's list changes underneath
  // us (a track advanced, or someone else edited) — otherwise a stale local
  // order would keep re-rendering over the truth.
  const serverKey = (queue?.items || []).map(i => i.song_id).join(',');
  React.useEffect(() => { setOrder(null); }, [serverKey, room]);

  // No rooms is three states: now-playing hasn't answered yet, it came
  // back empty (nothing provisioned) or it never came back at all.
  if (!room) {
    if (npLoading && !npError) {
      return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading rooms…</div>;
    }
    if (npError) {
      return <Empty glyph="headphones" title="rooms unavailable"
                    sub={`couldn't load now-playing: ${apiErrorText(npError)}`}/>;
    }
    return <Empty glyph="headphones" title="no rooms set up yet"
                  sub="set up a room to play music there"/>;
  }

  const items = order || queue?.items || [];
  const roomChoices = rooms.length ? rooms : [room];
  const editable = queue ? queue.editable !== false : true;
  // The server says who is asking (music_queue.py RoomQueue): a guest
  // never clears and never removes the song playing now; an admin may ban
  // the guest behind an entry a guest queued.
  const guest = !!queue?.guest;
  const admin = !!queue?.admin;
  const cur = items.findIndex(i => i.playing);
  // A guest's refusal (the room stopped meanwhile, a ban) is said in words
  // and the queue re-read — which then shows the "isn't playing" panel —
  // never a pairing prompt (guestSend).
  const send = (path, body) => (guest ? guestSend(path, body) : apiPost(path, body));
  const refused = (verb, e) => {
    if (!guest) { reportMutationFailure(fire, verb, e); return; }
    fire(guestRefusalText(verb, e));
    refresh();
  };

  const moveTo = async (from, toIdx) => {
    if (from == null || toIdx == null || from === toIdx || !editable || busy) return;
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(toIdx, 0, moved);
    setOrder(next);                       // optimistic
    try {
      await send(`/api/music/queue/${encodeURIComponent(room)}/move`, {
        song_id: moved.song_id, to_position: toIdx, device_id: deviceId,
      });
      refresh();
    } catch (e) {
      setOrder(null);                     // revert to the server's truth
      refused('move', e);
    }
  };

  const onDrop = (toIdx) => {
    const from = dragFrom.current;
    dragFrom.current = null;
    moveTo(from, toIdx);
  };

  const removeItem = async (item) => {
    setBusy(true);
    try {
      await send(`/api/music/queue/${encodeURIComponent(room)}/remove`, {
        song_ids: [item.song_id], device_id: deviceId,
      });
      fire(`removed "${item.title || 'track'}"`);
      refresh();
    } catch (e) {
      refused('remove', e);
    } finally { setBusy(false); }
  };

  // Tap a song: the room plays it now (POST …/play, a real jump).
  const playNow = async (item) => {
    if (!editable || item.playing || busy) return;
    setBusy(true);
    try {
      await roomPlayEntry(room, item.song_id, send);
      fire(`playing "${item.title || 'track'}" in ${room}`);
      refresh();
    } catch (e) {
      refused('play', e);
    } finally { setBusy(false); }
  };

  // The room's Up next: its Autoplay switch and "less like this" (both the
  // household's, never a guest's).
  const sessionId = queue?.session_id || null;
  const setAutoplay = async (on) => {
    if (!sessionId) return;
    setBusy(true);
    try {
      await apiPatch(`/api/music/up-next/sessions/${sessionId}`, { autoplay: !!on });
      fire(on ? `autoplay on in ${room}` : `autoplay off in ${room} — the queue stops at the end`);
      refresh();
    } catch (e) {
      reportMutationFailure(fire, 'autoplay', e);
    } finally { setBusy(false); }
  };
  const lessLike = async (item) => {
    if (!sessionId || item.up_next_seq == null) return;
    setBusy(true);
    try {
      const r = await apiPost(`/api/music/up-next/sessions/${sessionId}/feedback`,
        { seq: item.up_next_seq, kind: 'less_like_this' });
      const n = Math.max(0, ((r && r.removed) || []).length - 1);
      fire(`no more ${item.artist || 'of that'} in this mix${n ? ` (${n} more taken out)` : ''}`);
      refresh();
    } catch (e) {
      reportMutationFailure(fire, 'less like this', e);
    } finally { setBusy(false); }
  };

  const clearQueue = async () => {
    if (!window.confirm(`Clear ${room}'s queue and stop playback?`)) return;
    setBusy(true);
    try {
      await apiPost(`/api/music/queue/${encodeURIComponent(room)}/clear`,
                    { device_id: deviceId });
      fire(`cleared ${room}'s queue`);
      refresh();
    } catch (e) {
      reportMutationFailure(fire, 'clear', e);
    } finally { setBusy(false); }
  };

  // Admin only: ban the guest that queued this entry, from every room's
  // queue and transport (by the id it sent and its address; household
  // policy, not a lock — Settings → Phones & browsers lists and lifts bans).
  const banGuest = async (item) => {
    if (!window.confirm('Ban the guest that added this? They can no longer change '
      + 'the music in any room, as a guest, until you lift it in Settings → Phones & browsers.')) return;
    try {
      await apiPost('/api/music/guest-bans', {
        device_id: item.guest_device_id || null,
        source: item.guest_source || null,
      });
      fire('guest banned');
    } catch (e) {
      if (e && e.status === 409) fire('that guest is already banned');
      else reportMutationFailure(fire, 'ban', e);
    }
  };

  // The connect prompt (components.jsx ConnectAsk); the pair prompt where it isn't loaded.
  const pairPhone = () => {
    try { if (typeof ConnectAsk !== 'undefined') { ConnectAsk.open(); return; } } catch { /* below */ }
    try { Auth.requestPairing(); } catch {}
  };
  // The "up next" head: before the first pick still to come, or after the
  // list when the room's Up next has none waiting. Nothing without one.
  const pickRows = roomUpNextRows(items);
  const firstPick = pickRows.length ? pickRows[0].i : -1;
  const unLine = queue ? roomUpNextLine(queue, pickRows) : null;
  const unBasis = roomUpNextBasis(queue);
  const upNextHead = sessionId ? (
    <div className="rq-un-head" role="group" aria-label={`up next in ${room}`}>
      <span className="label">up next</span>
      {unBasis && <span className="rq-un-basis">{unBasis}</span>}
      <span style={{ flex: 1 }}/>
      {!guest && editable && (
        <label className="mp-un-auto" title="keep playing similar songs when the queue ends">
          <input type="checkbox" role="switch" checked={!!queue.autoplay} aria-checked={!!queue.autoplay}
                 disabled={busy} aria-label={`autoplay in ${room}: keep playing similar songs when the queue ends`}
                 onChange={(e) => setAutoplay(!!e.target.checked)}/>
          <span>autoplay</span>
        </label>
      )}
      {unLine && <div className="rq-un-line" role="status">{unLine}</div>}
    </div>
  ) : null;

  // Search the library and add to this room's queue: the tab's main
  // control, under the room chips. Shown only where adding works — the
  // queue has answered for this room and says this caller may change it
  // (a guest: while the room plays, unless banned; a household device:
  // unless an admin blocked it). Otherwise the box isn't drawn and the
  // queue's own words say why.
  const canAdd = !!queue && editable && typeof QueueSearchAdd === 'function';
  const addToRoom = async (tracks, next, label) => {
    const n = tracks.length;
    try {
      await send(roomQueuePath(room, '/add'), {
        track_ids: tracks.map((t) => t.id), device_id: deviceId, where: next ? 'next' : 'end',
      });
    } catch (e) {
      if (!guest) throw e;
      refresh();
      return guestRefusalText('add', e);
    }
    refresh();
    return `${next ? 'Playing next' : 'Added'} in ${room}: ${label}${n > 1 ? ` (${n} songs)` : ''}`;
  };

  // The rows drawn: QUEUE_ROWS_STEP at a time, "show more" for the rest.
  // Deep into a long queue the songs already played fold away (one press
  // shows them) so the song playing is near the top.
  const from = earlier || cur < QUEUE_FOLD_AFTER ? 0 : cur - 2;
  const to = Math.min(items.length, from + shown);
  const visible = items.slice(from, to);

  const tapButton = (name, title, to, from) => (
    <IconButton name={name} title={title} disabled={busy || to == null}
                onClick={() => moveTo(from, to)}/>
  );

  return (
    <div>
      <div className="rq-rooms" style={{ borderBottom: '1px solid var(--border-soft)' }}>
        {!embedded && <span className="label">room</span>}
        {!embedded && roomChoices.map(r => (
          <button key={r} className="rq-room" onClick={() => setRoom(r)}
                  style={{ background: room === r ? 'var(--brand-soft)' : 'var(--card)',
                           color: room === r ? 'var(--brand-ink)' : 'var(--fg)' }}>{r}</button>
        ))}
        <span style={{ flex: 1 }}/>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
          {items.length} track{items.length === 1 ? '' : 's'}
        </span>
        {items.length > 0 && editable && !guest && (
          <Button icon="trash-2" disabled={busy} onClick={clearQueue}>clear</Button>
        )}
      </div>

      {canAdd && (
        <div className="mp-q-save rq-add" key={`add-${room}`}>
          <QueueSearchAdd onAdd={addToRoom} idBase="rq-add" where={room}
                          ariaLabel={`search the library to add to ${room}'s queue`}/>
        </div>
      )}

      {!editable && queue?.blocked_reason && !(guest && items.length === 0) && (
        <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 8,
                      background: 'var(--warn-soft, var(--sunken))',
                      borderBottom: '1px solid var(--border-soft)' }}>
          <Icon name="lock" size={13}/>
          <span style={{ fontSize: 12, color: 'var(--fg)' }}>
            {queue.blocked_reason}. You can watch the queue but not change it.
          </span>
        </div>
      )}

      {editable && guest && items.length > 0 && (
        <div style={{ padding: '8px 16px', fontSize: 12, color: 'var(--fg-muted)',
                      borderBottom: '1px solid var(--border-soft)' }}>
          You're a guest: while {room} is playing you can play a song now, reorder,
          play next and remove songs, and add songs with the search above.
        </div>
      )}

      {!guest && items.length > 0 && typeof PlayerQueueSave === 'function' && (
        <div className="rq-save" style={{ padding: '6px 16px', borderBottom: '1px solid var(--border-soft)' }}>
          <PlayerQueueSave queue={[]} roomId={room}/>
        </div>
      )}

      {error && queue && (
        <div style={{ padding: '8px 16px', fontSize: 12, color: 'var(--err-ink)',
                      borderBottom: '1px solid var(--border-soft)' }}>
          couldn't refresh the queue — showing the last one loaded: {apiErrorText(error)}
        </div>
      )}

      {!queue && !error ? (
        <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading queue…</div>
      ) : error && !queue ? (
        /* A failed fetch is not an empty queue (F-020): say what failed
           and offer a retry instead of "cast from the Player tab". */
        <Empty glyph="headphones" title={`couldn't load ${room}'s queue`}
               sub={apiErrorText(error)}
               action={<Button icon="refresh-cw" onClick={refresh}>retry</Button>}/>
      ) : items.length === 0 && guest && !editable ? (
        /* A guest on a room that isn't playing (or a banned guest): the
           server sent no queue and said why. Pairing is the way in, so it
           is offered — on a press, never on its own. */
        <Empty glyph="headphones" title={`${room} isn't playing`}
               sub={queue?.blocked_reason || ''}
               action={<Button icon="link" onClick={pairPhone}>connect this browser</Button>}/>
      ) : items.length === 0 ? (
        <Empty glyph="headphones" title="queue is empty"
               sub={`cast from the player, or say "play something" in ${room}`}/>
      ) : (
        <div>
          {from > 0 && (
            <button type="button" className="rq-more" onClick={() => setEarlier(true)}>
              show {from} played song{from === 1 ? '' : 's'}
            </button>
          )}
          {visible.map((item, i) => {
            const idx = from + i;
            const canRemove = editable && !(guest && item.playing);
            const canBan = admin && (item.guest_device_id || item.guest_source);
            const pick = item.origin === 'auto' && idx > cur;
            const head = idx === firstPick ? upNextHead : null;
            // "now playing" over the song playing, "next" over what was
            // asked for after it (the player's queue has the same heads).
            const secHead = item.playing ? 'now playing'
              : (idx === cur + 1 && cur >= 0 && !pick ? 'next' : null);
            return (
              <React.Fragment key={item.song_id}>
              {head}
              {secHead && <MusicQueueSec>{secHead}</MusicQueueSec>}
              <div className="rq-row"
                   draggable={editable}
                   onDragStart={() => { dragFrom.current = idx; }}
                   onDragOver={(e) => { if (editable) e.preventDefault(); }}
                   onDrop={() => onDrop(idx)}
                   style={{ background: item.playing ? 'var(--brand-soft)' : 'transparent',
                            cursor: editable ? 'grab' : 'default' }}>
                <span className="rq-grip">
                  {editable
                    ? <MusicQueueGrip index={idx} count={items.length} disabled={busy}
                                 onMove={(f, t) => moveTo(f, t)} label={item.title || 'this song'}/>
                    : <Icon name="minus" size={13}/>}
                </span>
                <span className="rq-num">
                  {item.playing
                    ? <Icon name="volume-2" size={13} style={{ color: 'var(--brand-ink)' }}/>
                    : <span className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{idx + 1}</span>}
                </span>
                <div className="rq-main"
                     role={editable && !item.playing ? 'button' : undefined}
                     tabIndex={editable && !item.playing ? 0 : undefined}
                     title={editable && !item.playing ? `play now in ${room}` : undefined}
                     style={{ cursor: editable && !item.playing ? 'pointer' : undefined }}
                     onClick={() => playNow(item)}
                     onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); playNow(item); } }}>
                  <div style={{ fontSize: 13, fontWeight: item.playing ? 600 : 400,
                                overflow: 'hidden', textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap' }}>
                    {pick && <Icon name="sparkles" size={11} style={{ color: 'var(--fg-muted)', marginRight: 4 }}/>}
                    {item.title || item.file || 'unknown'}
                  </div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)',
                                                 overflow: 'hidden', textOverflow: 'ellipsis',
                                                 whiteSpace: 'nowrap' }}>
                    {item.artist || 'unknown artist'}
                    {item.origin === 'auto' && (item.reason_label || item.why) && (
                      <span className="rq-why"> · <UpNextWhy className="rq-why-chip" label={item.reason_label}
                                                              why={item.why}/></span>
                    )}
                    {item.added_by && (
                      <span style={{ color: 'var(--fg-faint)' }}> · added by {item.added_by}</span>
                    )}
                  </div>
                </div>
                <span className="rq-dur mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                  {fmtQueueDur(item.duration_sec)}
                </span>
                {/* Tap controls: what drag does, by finger. 44px on a phone. */}
                <span className="rq-ctrls">
                  {editable && (
                    <>
                      {cur >= 0 && !item.playing
                        && tapButton('list-start', 'play next', queueTapTarget('next', idx, cur, items.length), idx)}
                      {tapButton('chevron-up', 'move up', queueTapTarget('up', idx, cur, items.length), idx)}
                      {tapButton('chevron-down', 'move down', queueTapTarget('down', idx, cur, items.length), idx)}
                    </>
                  )}
                  {pick && editable && !guest && sessionId && (
                    <IconButton name="thumbs-down" disabled={busy}
                                title={`less like this: no more ${item.artist || 'of this artist'} in this mix`}
                                onClick={() => lessLike(item)}/>
                  )}
                  {canBan && (
                    <IconButton name="user-x" title="ban the guest that added this"
                                onClick={() => banGuest(item)}/>
                  )}
                  {canRemove && (
                    <IconButton name="x" title="remove from queue" disabled={busy}
                                onClick={() => removeItem(item)}/>
                  )}
                </span>
              </div>
              </React.Fragment>
            );
          })}
          {to < items.length && (
            <button type="button" className="rq-more"
                    onClick={() => setShown((n) => n + QUEUE_ROWS_STEP)}>
              show more ({items.length - to} more)
            </button>
          )}
          {(firstPick < 0 || firstPick >= to) && upNextHead}
        </div>
      )}
    </div>
  );
};

/* ---- Playlists tab ---------------------------------------- */
/* Wrapped to keep the JSX scannable: list view in the tab area,
 * details via PlaylistDrawer (similar pattern to the existing
 * track Drawer). Favorites is pinned at the top with a star icon
 * — it's a virtual playlist (id=0) the backend derives from
 * library_tracks.favorited and that can't be renamed or deleted. */
const EMPTY_PLAYLIST_FORM = { name: '', description: '', cover_color: '', cover_emoji: '' };
const _playlistFieldStyle = {
  font: 'inherit', fontSize: 13, height: 30, padding: '0 10px', borderRadius: 'var(--r-sm)',
  border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)',
};

/* What the four-field form posts: a trimmed name and the three optional
 * fields, empty ones sent as null so create and edit clear them alike. */
const playlistFields = (form) => {
  const opt = (v) => { const t = (v || '').trim(); return t ? t : null; };
  return {
    name: (form.name || '').trim(),
    description: opt(form.description),
    cover_color: opt(form.cover_color),
    cover_emoji: opt(form.cover_emoji),
  };
};

/* The playlist form — name, description, cover colour, emoji — used by
 * the Playlists tab's "new playlist" panel and the drawer's edit mode
 * (F-021: create used to take a name only, in a drawer off a library row). */
const PlaylistForm = ({ form, setForm, onSubmit, submitLabel = 'save', busy = false }) => {
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const canSubmit = !busy && !!(form.name || '').trim();
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <input value={form.name} onChange={set('name')} placeholder="name"
             onKeyDown={e => { if (e.key === 'Enter' && canSubmit) onSubmit(); }}
             style={_playlistFieldStyle}/>
      <input value={form.description} onChange={set('description')}
             placeholder="description (optional)" style={_playlistFieldStyle}/>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input type="color" value={/^#[0-9a-fA-F]{6}$/.test(form.cover_color) ? form.cover_color : '#7c5cff'}
               onChange={set('cover_color')} title="cover color"
               style={{ width: 34, height: 30, padding: 0, border: '1px solid var(--border)',
                        borderRadius: 'var(--r-sm)', background: 'var(--card)' }}/>
        <input value={form.cover_emoji} onChange={set('cover_emoji')} placeholder="emoji" maxLength={4}
               style={{ ..._playlistFieldStyle, fontSize: 16, width: 56, textAlign: 'center', padding: 0 }}/>
        <PlaylistCover playlist={{ ...form, is_virtual: false }} size={30}/>
        <div style={{ marginLeft: 'auto' }}>
          <Button variant="primary" icon={submitLabel === 'create' ? 'plus' : 'check'}
                  onClick={onSubmit} disabled={!canSubmit}>{busy ? `${submitLabel}…` : submitLabel}</Button>
        </div>
      </div>
    </div>
  );
};

/* Cover swatch: the playlist's colour behind its emoji or the fallback
 * icon. The colour used to tint only the fallback icon, so a playlist
 * with an emoji never showed it (F-021). `data-cover-color` carries the
 * value for tests and the DOM. */
const PlaylistCover = ({ playlist: p, size = 26 }) => (
  <div data-cover-color={p.cover_color || undefined}
       style={{ width: size, height: size, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                background: p.is_virtual
                  ? 'linear-gradient(135deg, oklch(0.86 0.06 75), oklch(0.62 0.14 50))'
                  : (p.cover_color || 'var(--sunken)'),
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
    {p.cover_emoji && !p.is_virtual
      ? <span style={{ fontSize: Math.round(size * 0.55), lineHeight: 1 }}>{p.cover_emoji}</span>
      : <Icon name={p.is_virtual ? 'star' : 'list-music'} size={Math.round(size * 0.5)}
              style={{ color: p.is_virtual ? 'var(--brand-fg)' : 'var(--fg-muted)' }}/>}
  </div>
);

const PlaylistsTab = ({ playlists, loading, onSelect, onPlay, onCreate, fire }) => {
  // "new playlist" opens the full form right here — the only create
  // used to be a name box in the add-to-playlist drawer off a library
  // row, unreachable with an empty library (F-021).
  const [creating, setCreating] = React.useState(false);
  const [form, setForm] = React.useState(EMPTY_PLAYLIST_FORM);
  const [busy, setBusy] = React.useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      if (await onCreate(form)) { setCreating(false); setForm(EMPTY_PLAYLIST_FORM); }
    } finally { setBusy(false); }
  };
  const toolbar = (
    <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 8,
                  borderBottom: '1px solid var(--border-soft)' }}>
      <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
        {playlists.length} playlist{playlists.length === 1 ? '' : 's'}
      </span>
      <span style={{ flex: 1 }}/>
      <Button variant={creating ? 'secondary' : 'primary'} icon={creating ? 'x' : 'plus'}
              onClick={() => setCreating(c => !c)}>
        {creating ? 'cancel' : 'new playlist'}
      </Button>
    </div>
  );
  const createPanel = creating && (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)', background: 'var(--sunken)' }}>
      <div className="label" style={{ marginBottom: 8 }}>new playlist</div>
      <PlaylistForm form={form} setForm={setForm} onSubmit={submit} submitLabel="create" busy={busy}/>
    </div>
  );
  if (loading && playlists.length === 0) {
    return <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading playlists…</div>;
  }
  if (playlists.length === 0) {
    return (
      <div>
        {toolbar}
        {createPanel}
        <Empty glyph="headphones" title="no playlists yet"
               sub={`create one above, click the + on a library row, or say "make a new playlist called X"`}/>
      </div>
    );
  }
  return (
    <div>
      {toolbar}
      {createPanel}
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
                <PlaylistCover playlist={p} size={26}/>
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
                <IconButton name="play" title="play" onClick={() => onPlay(p)}/>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

/* ---- Playlist drawer -------------------------------------- */
/* Shows tracks in playback order (insertion order for real
 * playlists, library added_at ASC for the Favorites virtual
 * playlist). Per-row remove icon; for Favorites that flips
 * library_tracks.favorited, for real playlists it deletes from
 * playlist_tracks (the backend handles the routing).
 *
 * "play here" / "shuffle here" play the whole list (the tracks route is
 * unpaged) in this browser. Footer holds Play / Shuffle buttons (both POST
 * /v1/admin/music/play-playlist with shuffle=false/true), Rename,
 * and Delete — the last two are disabled for Favorites. */
const PlaylistDrawer = ({ playlist, rooms, onClose, onPlay, onShuffle, onBrowserPlayTracks, onBrowserPlay, onRemoveTrack, onDelete, onEdit, onReorder, fire }) => {
  const [room, setRoom] = React.useState(rooms[0] || null);
  const [editing, setEditing] = React.useState(false);
  const [form, setForm] = React.useState({ name: '', description: '', cover_color: '', cover_emoji: '' });
  const [order, setOrder] = React.useState(null);   // local drag order, or null
  const dragFrom = React.useRef(null);
  React.useEffect(() => { setRoom(rooms[0] || null); }, [playlist?.id, rooms.join(',')]);
  React.useEffect(() => { setEditing(false); setOrder(null); }, [playlist?.id]);
  const { data: tracks, loading, refresh } = useApiObject(
    playlist ? `/api/playlists/${playlist.id}/tracks` : null,
    { eventTypes: ['playlists.changed', 'library.indexer.changed'] },
  );
  if (!playlist) return null;
  const items = order || tracks || [];
  // The header count follows the track list, not the playlist row
  // captured when the drawer opened: removing a track refreshes the list
  // (playlists.changed) but never that row, so it read "3 tracks" over
  // two (F-023). The row's count only fills in until the fetch lands.
  const trackCount = tracks ? items.length : playlist.track_count;
  // Up next: a song played from here goes on with the rest of the playlist
  // (Favorites, a virtual list, by its songs).
  const context = playlist.is_virtual ? { kind: 'ids', name: playlist.name }
    : { kind: 'playlist', ref: String(playlist.id), name: playlist.name };

  const startEdit = () => {
    setForm({
      name: playlist.name || '', description: playlist.description || '',
      cover_color: playlist.cover_color || '', cover_emoji: playlist.cover_emoji || '',
    });
    setEditing(true);
  };
  // The form closes only once the save landed: a refused or failed save
  // leaves the operator's typing where it was (the track drawer's rule).
  const saveEdit = async () => { if (await onEdit(playlist, playlistFields(form))) setEditing(false); };

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
      <aside style={{ position: 'fixed', top: 0, right: 0, height: 'var(--app-h)', width: 460, maxWidth: '100%',
                      background: 'var(--card)', borderLeft: '1px solid var(--border)',
                      boxShadow: 'var(--shadow-md)', zIndex: 41,
                      display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border)' }}>
          <div className="eyebrow">{playlist.is_virtual ? 'virtual' : 'playlist'} · #{playlist.id}</div>
          <IconButton name="x" onClick={onClose}/>
        </div>
        <div style={{ padding: 16, display: 'flex', gap: 14, alignItems: 'center', borderBottom: '1px solid var(--border-soft)' }}>
          <PlaylistCover playlist={playlist} size={56}/>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{playlist.name}</div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{trackCount} track{trackCount === 1 ? '' : 's'}</div>
            {playlist.description && <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 2 }}>{playlist.description}</div>}
          </div>
        </div>

        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)' }}>
          <div className="label" style={{ marginBottom: 6 }}>play in room</div>
          <RoomChips rooms={rooms} room={room} onPick={setRoom}/>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <Button variant="primary" icon="play" disabled={!room}
                    onClick={() => { if (room) { onPlay(playlist, room); onClose(); } }}>
              play
            </Button>
            <Button icon="shuffle" disabled={!room}
                    onClick={() => { if (room) { onShuffle(playlist, room); onClose(); } }}>
              shuffle
            </Button>
            {!playlist.is_virtual && (
              <Button icon="edit-2" onClick={() => (editing ? setEditing(false) : startEdit())}>
                {editing ? 'cancel' : 'edit'}
              </Button>
            )}
          </div>
          <div className="label" style={{ margin: '12px 0 6px' }}>this browser</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button icon="headphones" disabled={items.length === 0}
                    onClick={() => { onBrowserPlayTracks(items, playlist.name, context); onClose(); }}>play here</Button>
            <Button icon="shuffle" disabled={items.length === 0}
                    onClick={() => { onBrowserPlayTracks(shuffledIds(items), playlist.name, context); onClose(); }}>shuffle here</Button>
          </div>
          {editing && !playlist.is_virtual && (
            <div style={{ marginTop: 12 }}>
              <PlaylistForm form={form} setForm={setForm} onSubmit={saveEdit} submitLabel="save"/>
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
                     style={{ display: 'grid', gridTemplateColumns: '32px 1fr auto auto auto',
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
                  {onBrowserPlay && (
                    <IconButton name="play" onClick={() => onBrowserPlay(t, context, items)}
                                title="play in this browser" aria-label={`play ${t.title || 'this song'} in this browser`}/>
                  )}
                  <MusicSongMenu track={t} fire={fire}/>
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
                    style={{ background: 'var(--err-soft)', color: 'var(--err-ink)', borderColor: 'transparent' }}>
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
      const verb = p.is_virtual ? (inIt ? 'unfavorite' : 'favorite') : (inIt ? 'remove' : 'add');
      reportMutationFailure(fire, verb, e);
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
      reportMutationFailure(fire, 'create', e);
    }
  };

  return (
    <>
      <div onClick={onClose}
           style={{ position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.16)',
                    backdropFilter: 'blur(2px)', zIndex: 40 }}/>
      <aside style={{ position: 'fixed', top: 0, right: 0, height: 'var(--app-h)', width: 380, maxWidth: '100%',
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
                      style={inIt || p.is_virtual ? { color: 'var(--brand-ink)' } : { color: 'var(--fg-muted)' }}/>
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
  const enriched = stats.enriched_count;
  const minutes  = Math.round(totalDur / 60);
  // Both maps are open enums from the endpoint: added_via is whatever
  // stamped the row ('unknown' when nothing did, i.e. NULL) and source is
  // whatever provider plugin filled the library. Render every bucket,
  // biggest first — picking two keys by name hid an 11-track library
  // behind "added via voice 0 / 0 manual" (F-025).
  const buckets = (map) => Object.entries(map || {}).sort((a, b) => b[1] - a[1]);
  const byVia    = buckets(stats.by_added_via);
  const bySource = buckets(stats.by_source);
  return (
    <div style={{ padding: 16, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
      <Stat label="total tracks"     value={total}                sub="across all sources"/>
      <Stat label="total duration"   value={totalDur ? fmtBigDur(totalDur) : '—'}
            sub={totalDur ? `${minutes} minute${minutes === 1 ? '' : 's'}` : 'no durations indexed'}/>
      <Stat label="enriched"         value={total ? `${enriched} / ${total}` : '—'} sub={total ? `${total - enriched} pending` : ''}/>
      <BucketStat label="added via" buckets={byVia}/>
      <BucketStat label="by source" buckets={bySource}/>
      <AlsoCalledStat/>
    </div>
  );
};

/* One tile per open-enum map: the largest bucket is the headline, the
 * rest are listed underneath so no bucket is ever silently dropped. */
const BucketStat = ({ label, buckets }) => {
  if (buckets.length === 0) return <Stat label={label} value="—" sub="no tracks"/>;
  const [[topKey, topN], ...rest] = buckets;
  return (
    <Stat label={`${label} ${topKey}`} value={topN}
          sub={rest.length ? rest.map(([k, n]) => `${n} ${k}`).join(' · ') : 'all tracks'}/>
  );
};

/* ---- Page ------------------------------------------------- */
// The lower section's tabs, by address: #music/<id> (useRouteTab,
// components.jsx). The labels and counts are the page's `tabs` below.
const MUSIC_PAGE_TABS = [
  // No 'player' since 2026-11-12: #music/player opens the expanded player
  // over this page (player.jsx PLAYER_SHEET_ROUTE).
  { id: 'library' }, { id: 'artists' }, { id: 'genres' }, { id: 'queue' },
  { id: 'playlists' }, { id: 'stats' }, { id: 'jobs' },
];

const MusicPage = () => {
  // #music/queue opens Room queue, a tab click writes its address, Back
  // goes to the tab before.
  const [tab, setTab] = useRouteTab('music', MUSIC_PAGE_TABS, 'library');
  const [selected, setSelected] = React.useState(null);
  // The rooms strip is folded away until asked for (a simple page by
  // default); the choice is kept per browser, and the page works without it.
  const [roomsOpen, setRoomsOpen] = React.useState(() => {
    try { return localStorage.getItem('domovoi.music.roomsOpen') === '1'; } catch { return false; }
  });
  const toggleRooms = () => {
    const next = !roomsOpen;
    setRoomsOpen(next);
    try { localStorage.setItem('domovoi.music.roomsOpen', next ? '1' : '0'); } catch { /* kept for the visit */ }
  };
  // Playlists tab state: which playlist row's drawer is open, and
  // which library track triggered the "+ add to playlist" drawer.
  // Independent so a user can browse the playlist drawer without
  // closing the library add drawer (rare, but cleaner).
  const [openPlaylist, setOpenPlaylist] = React.useState(null);
  const [addToPlaylistTrack, setAddToPlaylistTrack] = React.useState(null);
  // Artists tab: the artist whose drawer is open.
  const [openArtist, setOpenArtist] = React.useState(null);
  // Genres tab: the genre whose drawer is open.
  const [openGenre, setOpenGenre] = React.useState(null);
  const [fire, toastNode] = useToast();

  // App-level browser playback context (docked mini-player lives in the
  // shell, its expanded view opens from the bar; here we drive it from
  // library rows). Degrades gracefully when the
  // provider isn't mounted (available === false).
  const player = usePlayback();
  // A song played here starts Up next (player.jsx playFromList): with the
  // list it was clicked in (`context`, `list`: the rest of that list comes
  // next), or on its own (songs like it from the whole library).
  const onBrowserPlay = (track, context = null, list = null) => {
    if (!player.available) { fire('browser player not available'); return; }
    // "Play here" plays here, casting or not: a room being cast to is
    // paused (player.jsx playItems), and the toast says which — once the
    // room has answered, so it never says "paused" over a room whose pause
    // didn't happen (the core's 502 for a player that never got it).
    const playing = `playing "${track.title || 'track'}" in this browser`;
    const onLeft = (room, paused) => fire(paused ? `${playing} · paused ${room}`
      : `${playing} · couldn't pause ${room}, it may still be playing`);
    const idx = Array.isArray(list) ? list.findIndex((t) => t && t.id === track.id) : -1;
    const left = typeof player.playFromList === 'function'
      ? player.playFromList(idx >= 0 ? list : [track], Math.max(0, idx), idx >= 0 ? context : null, { onLeft })
      : player.playItems([window.itemFromTrack(track)], 0, { onLeft });
    if (!left) fire(playing);
  };
  const onQueueTrack = (track) => {
    if (!player.available) { fire('browser player not available'); return; }
    player.enqueue([window.itemFromTrack(track)]);
    fire(`queued "${track.title || 'track'}"`);
  };
  // Many tracks at once (the Artists drawer's "play here" / "queue here").
  // A whole list played here, in the order given (shuffled or not): the
  // list is the queue; Up next carries on when it is nearly over.
  const onBrowserPlayTracks = (tracks, label, context = null) => {
    if (!player.available) { fire('browser player not available'); return; }
    if (!tracks.length) return;
    const playing = `playing ${label} in this browser`;
    const onLeft = (room, paused) => fire(paused ? `${playing} · paused ${room}`
      : `${playing} · couldn't pause ${room}, it may still be playing`);
    const left = typeof player.playList === 'function'
      ? player.playList(tracks, context || { kind: 'ids', name: label }, { onLeft })
      : player.playItems(tracks.map(t => window.itemFromTrack(t)), 0, { onLeft });
    if (!left) fire(playing);
  };
  const onBrowserQueueTracks = (tracks, label) => {
    if (!player.available) { fire('browser player not available'); return; }
    if (!tracks.length) return;
    player.enqueue(tracks.map(t => window.itemFromTrack(t)));
    fire(`queued ${tracks.length} song${tracks.length === 1 ? '' : 's'} · ${label}`);
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
  const { items: nowPlaying, error: npError, loading: npLoading, refresh: refreshNP } =
    useApiList('/api/music/now-playing', { eventTypes: ['music.now_playing.changed'] });
  // Playlists list — driven by /api/playlists which includes the
  // virtual Favorites row. Refetches on any playlists_changed event
  // (create/rename/delete + library favorite flips).
  const { items: playlists, loading: playlistsLoading, refresh: refreshPlaylists } =
    useApiList('/api/playlists', { eventTypes: ['playlists.changed'] });

  const rooms = useKnownRooms(nowPlaying);
  // data.js's; a page rendered without it (a test harness) draws what it
  // always drew.
  const guest = typeof useGuestViewer === 'function' ? useGuestViewer() : false;

  // The Room queue tab's room lives here, so a room card's queue button
  // (and Home's, through openRoomQueue) can open the tab on THAT room and
  // bring it into view — on a phone the tabs sit below every room card.
  const [queueRoom, setQueueRoom] = React.useState(null);
  const [queueAsk, setQueueAsk] = React.useState(0);
  const tabsRef = React.useRef(null);
  const showRoomQueue = React.useCallback((room) => {
    setQueueRoom(room);
    setTab('queue');
    setQueueAsk((n) => n + 1);
  }, []);
  React.useEffect(() => {
    if (!queueAsk) return undefined;
    const go = () => {
      try { if (tabsRef.current) tabsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch {}
    };
    go();
    // Again once the room's queue has arrived: the first scroll stops short
    // when the tab is still a one-line "loading queue…" and the page isn't
    // yet tall enough to bring the tabs to the top.
    const again = setTimeout(go, 450);
    return () => clearTimeout(again);
  }, [queueAsk]);
  React.useEffect(() => {
    if (typeof RoomQueueIntent === 'undefined') return undefined;
    const waiting = RoomQueueIntent.take();
    if (waiting) showRoomQueue(waiting);
    return RoomQueueIntent.subscribe((room) => { RoomQueueIntent.take(); showRoomQueue(room); });
  }, [showRoomQueue]);

  // A guest has no live socket (/ws/state takes a household credential),
  // so nothing pushes it a room starting, stopping or moving on: it
  // re-reads the open now-playing every 15 s while the page is visible.
  React.useEffect(() => {
    if (!guest) return undefined;
    const t = setInterval(() => {
      try { if (typeof document !== 'undefined' && document.hidden) return; } catch {}
      refreshNP();
    }, GUEST_POLL_MS);
    return () => clearInterval(t);
  }, [guest]);

  // ── Action handlers — all hit the real backend. ──────────────
  // Every mutation's catch reports through reportMutationFailure (data.js):
  // a refusal the pair / sign-in prompt owns stays quiet, a dismissed
  // prompt reads "play cancelled — this browser is not paired.", and a
  // real failure names the server's reason — never `${e.message}`, which
  // put `play failed: 401 Unauthorized: {"detail":…}` in the toast.
  //
  // One request per play in flight at a time (the radio Stations page's
  // rule). A tap that shows nothing for a round trip gets tapped again,
  // and every copy refused for want of a credential waits on the SAME
  // prompt: dismissing it toasted the refusal once per tap, and pairing
  // replayed every copy — two random tracks, or one playlist started
  // twice. A repeat of the same play (same room, same `what`) is dropped
  // until the first settles; a different choice for that room — another
  // track while MPD is still starting the first — goes through, rather
  // than vanishing behind a drawer that already closed, and so does
  // another room's play.
  const playsInFlight = React.useRef(new Set());
  // Rooms a guest may act on: playing, or paused on a song (the server's
  // rule, read off now-playing so a guest is told before anything is sent).
  const activeRooms = new Set(nowPlaying
    .filter(np => np.song && (np.state === 'play' || np.state === 'pause'))
    .map(np => np.room_id));
  const GUEST_START_HINT = 'starting music in a room takes a paired phone — '
    + 'a guest can change the queue of a room that is already playing';
  const playInRoom = async (room_id, what, verb, startedMsg, send) => {
    // Starting (or replacing) what a room plays is never a guest's: say so
    // here rather than send it and get the pairing prompt back.
    if (guest) { fire(GUEST_START_HINT); return; }
    const key = `${room_id}|${what}`;
    if (playsInFlight.current.has(key)) return;
    playsInFlight.current.add(key);
    fire(startedMsg);
    try { await send(); refreshNP(); }
    catch (e) { reportMutationFailure(fire, verb, e); }
    finally { playsInFlight.current.delete(key); }
  };
  // What every room start sends for its Up next: who is asking, whose
  // taste the mix follows, and this browser's Autoplay switch.
  const roomStartBody = (body) => Object.assign({ device_id: DeviceIdentity.id() }, body,
    typeof upNextListenerFields === 'function' ? upNextListenerFields() : {},
    typeof upNextAutoplayPref === 'function' && !upNextAutoplayPref() ? { autoplay: false } : {});
  const onPlayRandom = (room_id) => playInRoom(room_id, 'random', 'play', `shuffle requested in ${room_id}…`,
    () => apiPost('/api/music/play', { room_id, query: 'something random' }));
  const onPlayInRoom = (track, room_id) => {
    // Close the drawer immediately rather than after the API resolves
    // so the click feels responsive even when MPD takes a beat.
    // Failure surfaces as a toast either way.
    setSelected(null);
    // Direct-play by id: skips the router entirely, so no
    // conversation_log entry, and no fuzzy-match streaming-provider
    // fallthrough when ID3 tags don't line up with what MPD indexed.
    // The song starts the room's Up next (P4): songs like it follow it there.
    return playInRoom(room_id, `track ${track.id}`, 'play', `playing "${track.title || 'track'}" in ${room_id}…`,
      () => apiPost('/api/music/play-track', roomStartBody({ room_id, track_id: track.id })));
  };
  // Append to what the room is playing (the room queue), rather than
  // replacing it — the one way a guest adds a song (owner decision
  // 2026-10-10: a guest may change a PLAYING room's queue).
  const onQueueInRoom = async (track, room_id) => {
    if (guest && !activeRooms.has(room_id)) {
      fire(`${room_id} isn't playing — connect this browser to start music there`);
      return;
    }
    try {
      const body = { track_ids: [track.id], device_id: DeviceIdentity.id() };
      const path = `/api/music/queue/${encodeURIComponent(room_id)}/add`;
      await (guest ? guestSend(path, body) : apiPost(path, body));
      fire(`added "${track.title || 'track'}" to ${room_id}'s queue`);
      refreshNP();
    } catch (e) {
      if (guest) fire(guestRefusalText('add', e));
      else reportMutationFailure(fire, 'add', e);
    }
  };
  // The Artists drawer: a list of library tracks into a room — replacing
  // what it plays (play / shuffle, POST /api/music/play-tracks) or after
  // it (the room queue, as onQueueInRoom).
  // The list plays as it is; the room's Up next carries on with songs like
  // it when it is nearly over (P4: `context` names the list).
  const onPlayTracks = (track_ids, room_id, label, shuffle, context = null) => playInRoom(
    room_id, `tracks ${label}${shuffle ? ' shuffled' : ''}`, shuffle ? 'shuffle' : 'play',
    `${shuffle ? 'shuffling' : 'playing'} ${label} in ${room_id}…`,
    () => apiPost('/api/music/play-tracks', roomStartBody({
      room_id, track_ids, context: context && context.kind !== 'ids' ? context : { kind: 'ids', name: label, track_ids },
    })));
  const onQueueTracksInRoom = async (track_ids, room_id, label) => {
    if (guest && !activeRooms.has(room_id)) {
      fire(`${room_id} isn't playing — connect this browser to start music there`);
      return;
    }
    try {
      const body = { track_ids, device_id: DeviceIdentity.id() };
      const path = `/api/music/queue/${encodeURIComponent(room_id)}/add`;
      await (guest ? guestSend(path, body) : apiPost(path, body));
      fire(`added ${track_ids.length} song${track_ids.length === 1 ? '' : 's'} by ${label} to ${room_id}'s queue`);
      refreshNP();
    } catch (e) {
      if (guest) fire(guestRefusalText('add', e));
      else reportMutationFailure(fire, 'add', e);
    }
  };
  const transport = (verb) => async (room_id) => {
    try {
      const path = `/api/music/${verb}/${room_id}`;
      await (guest ? guestSend(path) : apiPost(path));
      refreshNP();
    } catch (e) {
      if (guest) { fire(guestRefusalText(verb, e)); refreshNP(); }
      else reportMutationFailure(fire, verb, e);
    }
  };
  const onPause = transport('pause');
  const onResume = transport('resume');
  const onSkip = transport('skip');
  const onStop = transport('stop');

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
      // Patch the loaded row in place (a re-read would refetch every page
      // scrolled through); under "favorites only" an unfavorited row
      // leaves the list. Now-playing too, so the NPCard's heart stays in
      // sync if this track is what some room is playing.
      if (!next && lib.favoritedOnly) lib.list.removeItem(track.id);
      else lib.list.updateItem(track.id, { favorited: next });
      refreshNP();
    } catch (e) {
      reportMutationFailure(fire, next ? 'favorite' : 'unfavorite', e);
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
      reportMutationFailure(fire, 'favorite', e);
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
      lib.list.removeItem(track.id);
      refreshStats();
    } catch (e) {
      // The login modal owns a 401 (data.js) — no raw toast behind it (F-006).
      reportMutationFailure(fire, 'delete', e);
    }
  };
  // Tag edits from the drawer: PATCH only what changed, keep the open
  // drawer on the returned row, refresh the list (and now-playing, in
  // case the edited track is what a room is playing).
  const onEditTrack = async (track, changes) => {
    try {
      const updated = await apiPatch(`/api/music/library/${track.id}`, changes);
      const next = { ...track, ...(updated || changes) };
      fire(`updated "${next.title || 'track'}"`);
      setSelected(next);
      lib.list.updateItem(track.id, next);
      refreshNP();
      return true;
    } catch (e) {
      // The drawer keeps the edit open on false, so the typing is kept.
      reportMutationFailure(fire, 'update', e, { kept: true });
      return false;
    }
  };
  const onCancelAcquisition = async (a) => {
    try { await apiDelete(`/api/music/acquisitions/${a.id}`); fire(`cancel sent for #${a.id}`); refreshAcquisitions(); }
    catch (e) { reportMutationFailure(fire, `cancel #${a.id}`, e); }
  };
  // ── Playlist action handlers ─────────────────────────────────
  const onPlayPlaylist = (playlist, room_id) => playInRoom(
    room_id, `playlist ${playlist.id}`, 'play', `playing ${playlist.name} in ${room_id}…`,
    () => apiPost('/api/music/play-playlist',
      { room_id, playlist_id: playlist.id, shuffle: false }));
  // The Playlists tab's row play button targets the first room; with none
  // provisioned it says so rather than posting to a room that isn't there.
  const onPlayPlaylistInFirstRoom = (playlist) => {
    if (rooms.length === 0) { fire('no rooms set up yet — set up a room to play on a speaker'); return; }
    return onPlayPlaylist(playlist, rooms[0]);
  };
  const onShufflePlaylist = (playlist, room_id) => playInRoom(
    room_id, `playlist ${playlist.id} shuffled`, 'shuffle', `shuffling ${playlist.name} in ${room_id}…`,
    () => apiPost('/api/music/play-playlist',
      { room_id, playlist_id: playlist.id, shuffle: true }));
  const onRemoveFromPlaylist = async (playlist, track) => {
    try {
      await apiDelete(`/api/playlists/${playlist.id}/tracks/${track.id}`);
      fire(`removed "${track.title || 'track'}" from ${playlist.name}`);
      refreshPlaylists();
    } catch (e) {
      reportMutationFailure(fire, 'remove', e);
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
      reportMutationFailure(fire, 'delete', e);
    }
  };
  const onCreatePlaylist = async (form) => {
    const fields = playlistFields(form);
    if (!fields.name) { fire('a playlist needs a name'); return false; }
    try {
      const created = await apiPost('/api/playlists', fields);
      fire(`created ${(created && created.name) || fields.name}`);
      refreshPlaylists();
      return true;
    } catch (e) {
      // The create form stays open on false, with the typing in it.
      reportMutationFailure(fire, 'create', e, { kept: true });
      return false;
    }
  };
  const onEditPlaylist = async (playlist, fields) => {
    try {
      await apiPatch(`/api/playlists/${playlist.id}`, fields);
      fire('playlist updated');
      refreshPlaylists();
      setOpenPlaylist({ ...playlist, ...fields });   // keep the drawer current
      return true;
    } catch (e) {
      reportMutationFailure(fire, 'update', e, { kept: true });
      return false;
    }
  };
  const onReorderPlaylist = async (playlist, track_ids) => {
    try {
      await apiPatch(`/api/playlists/${playlist.id}/order`, { track_ids });
      refreshPlaylists();
    } catch (e) {
      reportMutationFailure(fire, 'reorder', e);
      throw e;   // let the drawer revert its optimistic order
    }
  };
  const onBulkAddToPlaylist = async (playlistId, trackIds) => {
    let added = 0, dupes = 0, stopped = false;
    for (const tid of trackIds) {
      try {
        await apiPost(`/api/playlists/${playlistId}/tracks`, { track_id: tid });
        added++;
      } catch (e) {
        if (/already|409/.test(e.message)) { dupes++; continue; }
        reportMutationFailure(fire, 'add', e);
        // A refusal the prompt was shown for would be asked again for
        // every remaining track — one prompt per track — and an admin's
        // block on this device refuses every one of them the same way,
        // one identical toast per track. Stop at the first.
        if (e.authCancelled || isAuthFailure(e) || deviceBlockReason(e)) { stopped = true; break; }
      }
    }
    if (!stopped || added || dupes) {
      fire(`added ${added} track${added === 1 ? '' : 's'}${dupes ? `, ${dupes} already in` : ''}`);
    }
    refreshPlaylists();
  };

  const onRescan = async () => {
    try { await apiPost('/api/music/library/reindex'); fire('rescan started'); }
    catch (e) { reportMutationFailure(fire, 'rescan', e); }
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
      reportMutationFailure(fire, 'upload', e);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };
  const onEnrich = async () => {
    try {
      const r = await apiPost('/api/music/library/enrich');
      // The core starts a sweep only when it can do something; otherwise
      // it says why (queued: false) instead of pretending (fix B3).
      const notQueued = {
        disabled: 'song recognition is turned off on the server',
        offline: 'no internet right now — try again when it is back',
        no_provider: 'song recognition needs a free AcoustID key or the Shazam add-on',
        running: 'already identifying songs',
      };
      // The missing key has a home in the dashboard: say so with a way there
      // (Settings → Advanced, the AcoustID key field).
      if (r && r.queued === false && r.reason === 'no_provider') {
        fire(notQueued.no_provider, {
          stay: true, key: 'enrich-no-provider',
          actions: [{
            label: 'add the AcoustID key',
            title: 'Settings → Advanced → AcoustID key',
            onClick: () => { try { window.location.hash = ACOUSTID_KEY_ROUTE; } catch { /* no location */ } },
          }],
        });
        return;
      }
      fire(r && r.queued === false
        ? (notQueued[r.reason] || `enrich didn't start (${r.reason || 'unknown reason'})`)
        : 'enrich started');
    }
    catch (e) { reportMutationFailure(fire, 'enrich', e); }
  };

  const playingCount = nowPlaying.filter(n => n.state === 'play').length;
  const activeJobs = acquisitions.filter(a => a.status === 'claimed' || a.status === 'pending').length;

  const libraryTotal = stats?.total_tracks ?? null;
  // Data-driven source filter options (open enum, §6.4).
  const sourceOptions = Object.keys(stats?.by_source || {}).sort();
  const tabs = [
    { id: 'library',   label: 'Library',   count: libraryTotal ?? undefined },
    { id: 'artists',   label: 'Artists' },
    { id: 'genres',    label: 'Genres' },
    // Room queue: what the SPEAKERS play from, as opposed to the player's
    // queue (this browser's own, in the expanded player).
    { id: 'queue',     label: 'Room queue' },
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
            Empty if no mpd_rooms have been registered yet (first boot);
            a fetch failure says so instead of posing as first boot. */}
      {(() => {
        const playingCount = nowPlaying.filter(np => np.state === 'play').length;
        return (
          <div>
            <button type="button" className="rooms-toggle" onClick={toggleRooms}
                    aria-expanded={roomsOpen} aria-controls="music-rooms-strip"
                    style={{ background: 'none', border: 0, padding: '10px 4px', minHeight: 44,
                             color: 'var(--brand-ink)', font: 'inherit', cursor: 'pointer',
                             textDecoration: 'underline' }}>
              {roomsOpen ? 'Hide rooms'
                : playingCount > 0 ? `Show rooms (${playingCount} playing)` : 'Show rooms'}
            </button>
          </div>
        );
      })()}
      <div id="music-rooms-strip" hidden={!roomsOpen}>
      {roomsOpen && (nowPlaying.length === 0 ? (
        <Card>
          {npError ? (
            <Empty glyph="headphones" title="rooms unavailable"
                   sub={`couldn't load now-playing: ${apiErrorText(npError)}`}
                   action={<Button icon="refresh-cw" onClick={refreshNP}>retry</Button>}/>
          ) : (
            <Empty glyph="headphones" title="no rooms set up yet" sub="set up a room to play music there"/>
          )}
        </Card>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
          {nowPlaying.map(np => (
            <TickingNPCard key={np.room_id} np={np} guest={guest}
                    onPlayRandom={onPlayRandom}
                    onPause={onPause} onResume={onResume}
                    onSkip={onSkip} onStop={onStop}
                    onFavorite={onFavorite} onQueue={showRoomQueue}/>
          ))}
        </div>
      ))}
      </div>

      {/* [2] Lower tabbed section */}
      {/* A wrapper for the ref: Card forwards no extra props. */}
      <div ref={tabsRef} className={tab === 'queue' ? 'music-tabs-queue' : undefined}
           style={{ scrollMarginTop: 8 }}>
      <Card>
        <div style={{ padding: '0 8px' }}><Tabs tabs={tabs} value={tab} onChange={setTab}/></div>
        {tab === 'library'   && <LibraryTab   lib={lib} libraryTotal={libraryTotal} sourceOptions={sourceOptions} onSelect={setSelected} onToggleFavorite={onToggleFavorite} onAddToPlaylist={setAddToPlaylistTrack} playlists={playlists} onBulkAddToPlaylist={onBulkAddToPlaylist} onBrowserPlay={onBrowserPlay} onQueueTrack={onQueueTrack} onPlayNextTrack={onPlayNextTrack} fire={fire}/>}
        {tab === 'artists'   && <ArtistsTab onOpen={setOpenArtist}/>}
        {tab === 'genres'    && <GenresTab onOpen={setOpenGenre}/>}
        {tab === 'queue'     && <QueueTab rooms={rooms} nowPlaying={nowPlaying} npError={npError} npLoading={npLoading} fire={fire}
                                          room={queueRoom} onRoom={setQueueRoom}/>}
        {tab === 'playlists' && <PlaylistsTab playlists={playlists} loading={playlistsLoading} onSelect={setOpenPlaylist} onPlay={onPlayPlaylistInFirstRoom} onCreate={onCreatePlaylist} fire={fire}/>}
        {tab === 'stats'     && <StatsTab     stats={stats} loading={!stats}/>}
        {tab === 'jobs'      && <JobsTab jobs={acquisitions} availability={acqData} loading={acqLoading} rooms={rooms} onCancel={onCancelAcquisition} fire={fire} refresh={refreshAcquisitions}/>}
      </Card>
      </div>

      <Drawer track={selected} rooms={rooms} onClose={() => setSelected(null)}
              onDelete={onDelete} onEdit={onEditTrack} onPlayInRoom={onPlayInRoom}
              onQueueInRoom={onQueueInRoom}
              onBrowserPlay={onBrowserPlay} onQueueTrack={onQueueTrack} fire={fire}/>
      <PlaylistDrawer playlist={openPlaylist} rooms={rooms}
                      onClose={() => setOpenPlaylist(null)}
                      onPlay={onPlayPlaylist} onShuffle={onShufflePlaylist}
                      onBrowserPlayTracks={onBrowserPlayTracks} onBrowserPlay={onBrowserPlay}
                      onRemoveTrack={onRemoveFromPlaylist}
                      onDelete={onDeletePlaylist} onEdit={onEditPlaylist}
                      onReorder={onReorderPlaylist}
                      fire={fire}/>
      <ArtistDrawer artist={openArtist} rooms={rooms} fire={fire}
                    onClose={() => setOpenArtist(null)}
                    onPlayTracks={onPlayTracks} onQueueTracksInRoom={onQueueTracksInRoom}
                    onBrowserPlayTracks={onBrowserPlayTracks} onBrowserQueueTracks={onBrowserQueueTracks}
                    onSelectTrack={(t) => { setOpenArtist(null); setSelected(t); }}
                    onBrowserPlay={onBrowserPlay} onQueueTrack={onQueueTrack}/>
      <GenreDrawer genre={openGenre} rooms={rooms} fire={fire}
                   onClose={() => setOpenGenre(null)}
                   onPlayTracks={onPlayTracks} onQueueTracksInRoom={onQueueTracksInRoom}
                   onBrowserPlayTracks={onBrowserPlayTracks} onBrowserQueueTracks={onBrowserQueueTracks}
                   onSelectTrack={(t) => { setOpenGenre(null); setSelected(t); }}
                   onBrowserPlay={onBrowserPlay} onQueueTrack={onQueueTrack}/>
      <LibraryAddDrawer track={addToPlaylistTrack}
                        onClose={() => setAddToPlaylistTrack(null)}
                        onMutated={() => { lib.refresh(); refreshPlaylists(); refreshNP(); }}
                        fire={fire}/>
      {toastNode}
    </div>
  );
};

window.MusicPage = MusicPage;
